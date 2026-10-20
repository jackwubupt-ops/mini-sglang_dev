"""Checkpoint: every FlashInfer operation Mini-SGLang uses with `--attention-backend fi` runs and
matches a plain PyTorch reference on this GPU (small shapes, < 1 GiB).

    scripts/run_offline.sh cp-flashinfer-<time> 6 -- python scripts/compat/flashinfer_check.py

Ops (called the way python/minisgl calls them): rmsnorm, fused_add_rmsnorm, silu_and_mul,
apply_rope_with_cos_sin_cache_inplace, sampling.softmax, sampling.sampling_from_probs,
BatchPrefillWithPagedKVCacheWrapper (fa2, causal, page_size 1, GQA), BatchDecodeWithPagedKVCacheWrapper
(fa2, with and without tensor cores). FlashInfer JIT-compiles each kernel on first use with the
toolkit in CUDA_HOME, so a FAIL usually carries a compiler error. Writes
$RUN_DIR/flashinfer_check.json; exit 1 if any op fails.
"""

from __future__ import annotations

import math
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from offline_common import run_dir, write_json_once  # noqa: E402

TOL = 3e-2  # bf16


def main() -> None:
    out = run_dir()
    import flashinfer
    import torch

    torch.manual_seed(0)
    dev, bf16 = "cuda", torch.bfloat16
    results = []

    def check(name, fn):
        try:
            err = fn()
            ok = err is None or err < TOL
            results.append({"op": name, "status": "PASS" if ok else "FAIL", "max_abs_err": err})
            print(f"{'PASS' if ok else 'FAIL'} {name}: max_abs_err={err}")
        except Exception as e:
            tb = traceback.format_exc()
            results.append({"op": name, "status": "FAIL", "error": repr(e)[:3000], "traceback": tb[-6000:]})
            print(f"FAIL {name}: {repr(e)[:800]}")

    def diff(a, b):
        return (a.float() - b.float()).abs().max().item()

    H, HKV, D, HID, EPS = 16, 8, 128, 1024, 1e-6  # Qwen3-0.6B shapes

    def ref_rms(x, w):
        xf = x.float()
        return (xf * torch.rsqrt(xf.pow(2).mean(-1, keepdim=True) + EPS) * w.float()).to(x.dtype)

    def t_rmsnorm():
        x, w = torch.randn(7, HID, device=dev, dtype=bf16), torch.randn(HID, device=dev, dtype=bf16)
        return diff(flashinfer.rmsnorm(x, w, EPS), ref_rms(x, w))

    def t_fused_add_rmsnorm():
        x, r = torch.randn(7, HID, device=dev, dtype=bf16), torch.randn(7, HID, device=dev, dtype=bf16)
        w = torch.randn(HID, device=dev, dtype=bf16)
        exp_r = (x.float() + r.float()).to(bf16)
        exp_x = ref_rms(exp_r, w)
        flashinfer.fused_add_rmsnorm(x, r, w, EPS)
        return max(diff(r, exp_r), diff(x, exp_x))

    def t_silu_and_mul():
        x = torch.randn(7, 2 * 3072, device=dev, dtype=bf16)
        a, b = x[:, :3072].float(), x[:, 3072:].float()
        return diff(flashinfer.silu_and_mul(x), (torch.nn.functional.silu(a) * b).to(bf16))

    def t_rope():
        n, max_pos = 9, 4096
        inv = 1.0 / (1e6 ** (torch.arange(0, D, 2, dtype=torch.float) / D))
        freqs = torch.einsum("i,j->ij", torch.arange(max_pos, dtype=torch.float), inv)
        cache = torch.cat((freqs.cos(), freqs.sin()), dim=-1).to(dev)
        pos = torch.randint(0, max_pos, (n,), device=dev)
        q = torch.randn(n, H * D, device=dev, dtype=bf16)
        k = torch.randn(n, HKV * D, device=dev, dtype=bf16)

        def ref(x, heads):
            x = x.view(n, heads, D).float()
            cos, sin = cache[pos, : D // 2][:, None, :], cache[pos, D // 2 :][:, None, :]
            x1, x2 = x[..., : D // 2], x[..., D // 2 :]
            return torch.cat((x1 * cos - x2 * sin, x2 * cos + x1 * sin), -1).view(n, heads * D).to(bf16)

        eq, ek = ref(q, H), ref(k, HKV)
        flashinfer.apply_rope_with_cos_sin_cache_inplace(
            positions=pos, query=q, key=k, head_size=D, cos_sin_cache=cache
        )
        return max(diff(q, eq), diff(k, ek))

    def t_softmax():
        logits = torch.randn(4, 151936, device=dev, dtype=torch.float32)
        temps = torch.tensor([0.5, 1.0, 0.7, 2.0], device=dev)
        got = flashinfer.sampling.softmax(logits, temps, enable_pdl=False)
        return diff(got, torch.softmax(logits / temps[:, None], dim=-1)) * 1e3  # probs are tiny

    def t_sampling_from_probs():
        idx = torch.tensor([3, 151000, 42, 7], device=dev)
        probs = torch.zeros(4, 151936, device=dev)
        probs[torch.arange(4), idx] = 1.0
        got = flashinfer.sampling.sampling_from_probs(probs)
        return 0.0 if torch.equal(got.long(), idx) else 1.0

    def paged(kv_lens):
        total = sum(kv_lens)
        k_cache = torch.randn(total + 8, 1, HKV, D, device=dev, dtype=bf16)
        v_cache = torch.randn(total + 8, 1, HKV, D, device=dev, dtype=bf16)
        indices = torch.randperm(total + 8, device=dev)[:total].to(torch.int32)
        indptr = torch.tensor([0] + list(torch.tensor(kv_lens).cumsum(0)), dtype=torch.int32)
        return k_cache, v_cache, indices, indptr

    def ref_attn(q, k, v, causal):  # q (Lq,H,D), k/v (Lk,HKV,D)
        k = k.float().repeat_interleave(H // HKV, dim=1)
        v = v.float().repeat_interleave(H // HKV, dim=1)
        s = torch.einsum("qhd,khd->hqk", q.float(), k) / math.sqrt(D)
        if causal:
            lq, lk = q.shape[0], k.shape[0]
            mask = torch.arange(lk)[None, :] > (torch.arange(lq)[:, None] + lk - lq)
            s = s.masked_fill(mask.to(dev)[None], float("-inf"))
        return torch.einsum("hqk,khd->qhd", torch.softmax(s, -1), v)

    ws = torch.empty(128 * 1024 * 1024, dtype=torch.uint8, device=dev)

    def t_prefill():
        q_lens, kv_lens = [5, 1, 9], [5, 7, 12]
        k_cache, v_cache, indices, kv_indptr = paged(kv_lens)
        qo_indptr = torch.tensor([0] + list(torch.tensor(q_lens).cumsum(0)), dtype=torch.int32)
        q = torch.randn(sum(q_lens), H, D, device=dev, dtype=bf16)
        w = flashinfer.BatchPrefillWithPagedKVCacheWrapper(ws, kv_layout="NHD", backend="fa2")
        w.plan(
            qo_indptr=qo_indptr, paged_kv_indptr=kv_indptr, paged_kv_indices=indices,
            paged_kv_last_page_len=torch.ones(len(q_lens), dtype=torch.int32),
            num_qo_heads=H, num_kv_heads=HKV, head_dim_qk=D, page_size=1, pos_encoding_mode="NONE",
            seq_lens=torch.tensor(kv_lens, dtype=torch.int32), q_data_type=bf16, kv_data_type=bf16, causal=True,
        )
        o = w.run(q=q, paged_kv_cache=(k_cache, v_cache))
        err = 0.0
        for i in range(len(q_lens)):
            sel = indices[kv_indptr[i] : kv_indptr[i + 1]].long()
            qi = q[qo_indptr[i] : qo_indptr[i + 1]]
            r = ref_attn(qi, k_cache[sel, 0], v_cache[sel, 0], causal=True)
            err = max(err, diff(o[qo_indptr[i] : qo_indptr[i + 1]], r))
        return err

    def t_decode(tensor_cores):
        def run():
            kv_lens = [3, 17, 64, 200]
            k_cache, v_cache, indices, kv_indptr = paged(kv_lens)
            q = torch.randn(len(kv_lens), H, D, device=dev, dtype=bf16)
            w = flashinfer.BatchDecodeWithPagedKVCacheWrapper(
                ws, use_tensor_cores=tensor_cores, kv_layout="NHD", backend="fa2"
            )
            w.plan(
                indptr=kv_indptr, indices=indices, last_page_len=torch.ones(len(kv_lens), dtype=torch.int32),
                num_qo_heads=H, num_kv_heads=HKV, head_dim=D, page_size=1, pos_encoding_mode="NONE",
                seq_lens=torch.tensor(kv_lens, dtype=torch.int32), data_type=bf16, q_data_type=bf16,
                kv_data_type=bf16,
            )
            o = w.run(q=q, paged_kv_cache=(k_cache, v_cache))
            err = 0.0
            for i in range(len(kv_lens)):
                sel = indices[kv_indptr[i] : kv_indptr[i + 1]].long()
                r = ref_attn(q[i : i + 1], k_cache[sel, 0], v_cache[sel, 0], causal=False)
                err = max(err, diff(o[i : i + 1], r))
            return err

        return run

    print(f"flashinfer {flashinfer.__version__}, torch {torch.__version__} (CUDA {torch.version.cuda})")
    for name, fn in [
        ("rmsnorm", t_rmsnorm),
        ("fused_add_rmsnorm", t_fused_add_rmsnorm),
        ("silu_and_mul", t_silu_and_mul),
        ("apply_rope_with_cos_sin_cache_inplace", t_rope),
        ("sampling.softmax (x1e3)", t_softmax),
        ("sampling.sampling_from_probs", t_sampling_from_probs),
        ("BatchPrefillWithPagedKVCacheWrapper fa2 causal", t_prefill),
        ("BatchDecodeWithPagedKVCacheWrapper fa2", t_decode(False)),
        ("BatchDecodeWithPagedKVCacheWrapper fa2 tensor cores", t_decode(True)),
    ]:
        check(name, fn)
        torch.cuda.synchronize()

    n_fail = sum(r["status"] != "PASS" for r in results)
    print("RESULT:", "all FlashInfer ops pass" if not n_fail else f"{n_fail} op(s) failed")
    write_json_once(
        out / "flashinfer_check.json",
        {"flashinfer": flashinfer.__version__, "torch": torch.__version__, "torch_cuda": torch.version.cuda,
         "results": results},
    )
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()

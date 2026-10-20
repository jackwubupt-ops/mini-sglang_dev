# Running Mini-SGLang on `ps` with driver 520.61.05 (CUDA 11.8): checkpointed compatibility plan

Status: **plan only, nothing executed.** Written 2026-10-20 from the code at `9a91cfa` and the
package indexes (PyPI, download.pytorch.org, flashinfer.ai, sgl-project wheel index) read from the Mac.
Goal: find out, cheaply and safely, whether Mini-SGLang can run on this server with reasonable
adjustments, before asking for a driver upgrade or renting another server.

Run the checkpoints (CP) in order. Each one says what it does, its risk, what to expect and how to
undo it. **STOP** means: do not continue, paste the output to Claude Code, decide together.

## What Mini-SGLang actually needs on the GPU (from the source)

| Component | Used for (our config: Qwen3 dense, `--attention-backend fi`, TP 1) | CUDA 11.8 situation |
| --- | --- | --- |
| PyTorch `<2.10` | everything | `torch 2.7.1+cu118` exists (newest CUDA 11.8 build); satisfies `<2.10`, no source change |
| FlashInfer `>=0.5.3` | attention (prefill/decode wrappers, `plan(seq_lens=..., head_dim_qk=...)`), sampling, RMSNorm, SiLU, RoPE | prebuilt CUDA 11.8 wheels stop at 0.2.0.post2, whose API is too old for this code. 0.5.3 compiles kernels on first use (JIT) with the local `nvcc`; whether that works with nvcc 11.8 is **unknown → CP5** |
| Mini-SGLang's own kernels (`python/minisgl/kernel/csrc`, ~2,000 lines) | KV-cache store, embedding index, NCCL wrapper (built even for TP 1), radix compare | compiled at runtime by tvm-ffi with **`-std=c++20`** and C++20 features (`std::source_location`, `std::span`, `concepts`/`requires`, `std::bit_cast`). **nvcc 11.8 has no C++20 mode** (added in CUDA 12.0) → expected **FAIL at CP3/CP6** |
| sgl_kernel `>=0.3.17.post1` | only the `fa` attention backend and MoE models | no CUDA 11.8 build after 0.1.6.post1; **not needed** for our config (imported lazily) → skipped |
| Triton | only MoE kernels | not needed for Qwen3 dense |
| quack-kernels | not imported anywhere | skipped |
| apache-tvm-ffi `>=0.1.4` | loads Mini-SGLang's kernels | CUDA-independent; uses `nvcc` from `CUDA_HOME` |

**CUDA 12 on this driver:** a CUDA 12.x runtime needs driver ≥ 525.60.13 (CUDA "minor version
compatibility" starts there), so 520.61.05 cannot run any CUDA 12 build. The only user-space route,
the CUDA forward-compatibility package (`cuda-compat`, a newer `libcuda.so` loaded from a private
directory), is officially limited to data-center GPUs; on GeForce cards it fails with error 804. CP2
tests this in five minutes without touching the system driver.

**Prediction (to be confirmed by the checkpoints):** CP2 fails with error 804 → no CUDA 12. CP3 shows
nvcc 11.8 rejects `-std=c++20` → unmodified Mini-SGLang cannot build its kernels on this server.
Continuing past CP3 is still useful: CP5 tells us whether FlashInfer works with CUDA 11.8, which
decides whether a source port (C++17 version of the ~2,000-line kernel directory on a separate
branch) would be enough, or whether a driver upgrade/another server is the only option.

## Rules for every checkpoint

- Everything goes into `/home/xwc/envs/minisgl` (a new Conda environment) and `/home/xwc/vllm/...`.
  Nothing touches the NVIDIA driver, system CUDA, Conda `base`, other users' files, or uses `sudo`.
- Network access uses your tunnel only per command (`HTTPS_PROXY=...` in front of `conda`,
  `--proxy` for `pip`); nothing is stored in any config file.
- Conda packages come from `conda-forge` and NVIDIA's channel with `--override-channels`, so the
  Anaconda `defaults` channel (and its terms-of-service prompt) is not used.
- GPU work only through `scripts/run_offline.sh` on **GPU 6** (the only GPU with no compute process
  in your nvidia-smi output; the script refuses if another user's process appears on it). All GPU
  tests here use less than 3 GiB for a few minutes.
- Disk: the root filesystem is 99% full and shared (54 GiB free). This plan uses about 8–10 GiB
  (Conda env with torch and the CUDA 11.8 compiler ~7 GiB, FlashInfer JIT cache < 1 GiB, model
  1.5 GiB at CP8). `pip` runs with `--no-cache-dir`.
- Keep one terminal for the whole session; the variables `PX` and `GPU` are set once.

---

## CP0. Policy and resources (read-only, no risk)

```bash
cd /home/xwc/vllm/mini-sglang_dev && scripts/pick_gpu.sh
```
Expected: `GPU 6 (8 MiB used)` (or similar) under "candidates". If GPU 6 is not listed, **STOP**
(someone is using it; pick another free GPU with me first).

```bash
head -n 4 /etc/os-release; ldd --version | head -n 1; gcc --version | head -n 1; g++ --version | head -n 1; echo "cores: $(nproc)"; uptime; df -h /home/xwc/vllm; du -sh /home/xwc/anaconda3/pkgs 2>/dev/null
```
What to check:
- glibc (`ldd`) **≥ 2.28** — torch 2.7.1 wheels are `manylinux_2_28`. Lower → **STOP**.
- gcc major version: nvcc 11.8 accepts host gcc **≤ 11**. If gcc is 12 or newer, CP3 adds gcc 11
  inside the environment (no system change).
- At least **25 GiB** free on `/home/xwc/vllm`, otherwise **STOP**.
- Load average well below the core count (shared host; JIT compiles use `MAX_JOBS=8`).

Rollback: nothing to undo.

## CP1. Which package servers are reachable, directly or through the tunnel (read-only)

```bash
for u in https://conda.anaconda.org/conda-forge/ https://conda.anaconda.org/nvidia/ https://pypi.org/simple/pip/ https://files.pythonhosted.org https://download.pytorch.org/whl/cu118/torch/ https://flashinfer.ai/whl/ https://huggingface.co/api/models/Qwen/Qwen3-0.6B; do d=$(curl -sS -o /dev/null -m 20 -w '%{http_code}' "$u" 2>&1 | tail -n 1); t=$(curl -sS -o /dev/null -m 20 -x http://127.0.0.1:17897 -w '%{http_code}' "$u" 2>&1 | tail -n 1); printf '%-55s direct=%-6s tunnel=%s\n' "$u" "$d" "$t"; done
```
`200/301/302` = reachable, `000` or an error = blocked.

Then set `PX` for this terminal, **one** of these two lines:
- all `direct=` values are 200/301/302 → use direct access:
```bash
PX=
```
- some `direct=` values fail but `tunnel=` works → use the tunnel (check it first; run
  `scripts/check_gh_tunnel.sh` again whenever you reconnect):
```bash
scripts/check_gh_tunnel.sh && PX=http://127.0.0.1:17897 && echo "using tunnel $PX"
```
If neither works for a server that a checkpoint needs, **STOP**.

Rollback: nothing to undo.

## CP2. Can CUDA 12 run on this driver through forward compatibility? (5 min, ~150 MB)

Creates a separate throw-away environment holding only NVIDIA's forward-compatibility `libcuda`,
then asks it to initialise. No GPU memory, no kernel, no context; the system driver is untouched.

```bash
if [ "$(id -un)" != xwc ]; then echo "STOP: not xwc"; elif [ -e /home/xwc/envs/cudacompat-probe ]; then echo "exists: /home/xwc/envs/cudacompat-probe (inspect it, not recreated)"; else env ${PX:+HTTPS_PROXY=$PX HTTP_PROXY=$PX} conda create -y -p /home/xwc/envs/cudacompat-probe -c conda-forge --override-channels "cuda-compat=12.4"; fi
```
```bash
cd /home/xwc/vllm/mini-sglang_dev && python3 scripts/compat/cuda_compat_probe.py && LIB=$(find /home/xwc/envs/cudacompat-probe -name 'libcuda.so.1' | head -n 1) && echo "compat lib: ${LIB:-not found}" && [ -n "$LIB" ] && python3 scripts/compat/cuda_compat_probe.py --libcuda "$LIB"
```
Expected:
- system driver: `version=11.8`, `cuInit: rc=0`, `RESULT: usable`;
- forward-compat library: `version=12.4`, then most likely `cuInit: rc=804 (COMPAT_NOT_SUPPORTED_ON_DEVICE)`,
  `RESULT: NOT usable` → CUDA 12 is impossible here without a driver upgrade; continue with CP3.
- If the second probe says `rc=0 ... usable` (unexpected): **STOP** and tell me — a CUDA 12 route
  (the normal dependency set, no source changes) would then be worth testing instead.

If conda reports that `cuda-compat=12.4` cannot be found, rerun the create command with
`cuda-compat` (no version) once; if that also fails, skip CP2 (record it) and continue with CP3.

Rollback (asks for confirmation; deletes only this probe environment):
```bash
conda env remove -p /home/xwc/envs/cudacompat-probe
```

## CP3. Environment + CUDA 11.8 compiler, and the C++20 gate (15 min, ~2 GiB)

Create the project environment (Python 3.12, as the plan requires) from conda-forge:
```bash
if [ "$(id -un)" != xwc ]; then echo "STOP: not xwc"; elif [ -e /home/xwc/envs/minisgl ]; then echo "exists: /home/xwc/envs/minisgl (inspect it, not recreated)"; else env ${PX:+HTTPS_PROXY=$PX HTTP_PROXY=$PX} conda create -y -p /home/xwc/envs/minisgl -c conda-forge --override-channels python=3.12; fi
```
```bash
conda activate /home/xwc/envs/minisgl && python -c "import sys; print(sys.prefix)"
```
Must print `/home/xwc/envs/minisgl`.

Add the CUDA 11.8 compiler, runtime headers, CUB/Thrust and cuRAND headers **inside this
environment only** (NVIDIA's channel, release label 11.8.0):
```bash
[ "${CONDA_PREFIX:-}" = /home/xwc/envs/minisgl ] && env ${PX:+HTTPS_PROXY=$PX HTTP_PROXY=$PX} conda install -y -p /home/xwc/envs/minisgl -c "nvidia/label/cuda-11.8.0" --override-channels cuda-nvcc cuda-cudart-dev cuda-cccl libcurand-dev cuda-nvrtc-dev cuda-profiler-api || echo "STOP: activate /home/xwc/envs/minisgl first, or the install failed"
```
Only if CP0 showed gcc 12 or newer, add gcc 11 to the environment and tell nvcc to use it:
```bash
[ "${CONDA_PREFIX:-}" = /home/xwc/envs/minisgl ] && env ${PX:+HTTPS_PROXY=$PX HTTP_PROXY=$PX} conda install -y -p /home/xwc/envs/minisgl -c conda-forge --override-channels "gxx_linux-64=11" "gcc_linux-64=11" && export NVCC_PREPEND_FLAGS="-ccbin $CONDA_PREFIX/bin/x86_64-conda-linux-gnu-g++" && echo "nvcc host compiler: $NVCC_PREPEND_FLAGS"
```

Session settings (repeat in every new terminal after `conda activate`):
```bash
cd /home/xwc/vllm/mini-sglang_dev && scripts/check_env.sh && source scripts/env.sh && export CUDA_HOME="$CONDA_PREFIX" FLASHINFER_CUDA_ARCH_LIST=8.6 TORCH_CUDA_ARCH_LIST=8.6 MAX_JOBS=8 && mkdir -p /home/xwc/vllm/cache /home/xwc/vllm/cu118 && "$CUDA_HOME/bin/nvcc" --version | tail -n 2
```
Expected: `ok: xwc, ...` and `Cuda compilation tools, release 11.8, V11.8.89`.

**The C++20 gate** — compile a two-line CUDA program with `-std=c++17` and with `-std=c++20`
(the flag Mini-SGLang passes to nvcc):
```bash
T=$(mktemp -d /home/xwc/vllm/cache/nvcc-probe.XXXX) && printf '#include <cstdio>\n__global__ void k() {}\nint main() { k<<<1, 1>>>(); std::printf("ok\\n"); }\n' > "$T/t.cu" && for s in c++17 c++20; do printf '%s: ' "$s"; "$CUDA_HOME/bin/nvcc" -std=$s -arch=sm_86 -o "$T/t_$s" "$T/t.cu" 2>&1 | head -n 2; [ -x "$T/t_$s" ] && echo "compiled"; done; echo "probe files: $T"
```
Expected: `c++17: compiled`; `c++20: nvcc fatal : Value 'c++20' is not defined for option 'std'`.
- If c++20 fails as expected: **STOP #1. Unmodified Mini-SGLang cannot build its own kernels on
  this server.** Tell me. If you then want to size the remaining risk (is a C++17 port enough?),
  continue with CP4–CP6 as diagnostics; they do not change the source.
- If c++20 compiles (unexpected): continue normally.

Rollback (asks for confirmation; deletes only this environment):
```bash
conda deactivate; conda env remove -p /home/xwc/envs/minisgl
```

## CP4. PyTorch 2.7.1 for CUDA 11.8, GPU check (10 min, ~5 GiB)

Pin torch so later installs can never replace it, then install it from PyTorch's CUDA 11.8 index:
```bash
[ -e /home/xwc/vllm/cu118/constraints.txt ] || printf 'torch==2.7.1+cu118\n' > /home/xwc/vllm/cu118/constraints.txt; cat /home/xwc/vllm/cu118/constraints.txt
```
```bash
[ "${CONDA_PREFIX:-}" = /home/xwc/envs/minisgl ] && python -m pip install --no-cache-dir ${PX:+--proxy "$PX"} --index-url https://download.pytorch.org/whl/cu118 "torch==2.7.1+cu118" || echo "STOP: env not active or install failed"
```
GPU check on GPU 6 (< 100 MiB, seconds):
```bash
GPU=6 && scripts/run_offline.sh cp4-torch-$(date +%Y%m%d-%H%M%S) "$GPU" -- python scripts/compat/torch_check.py
```
Expected: `torch: 2.7.1+cu118`, `torch_cuda: 11.8`, `cuda_available: True`, `device_count: 1`,
`device: NVIDIA GeForce RTX ...`, `capability: (8, 6)`, `status: PASS`, `exit code 0`.
Any other result: **STOP**.

Rollback: `python -m pip uninstall torch` (asks), or remove the whole environment (CP3 rollback).

## CP5. FlashInfer 0.5.3 with the CUDA 11.8 compiler (critical, 15–40 min of compiling)

Install (torch stays pinned by the constraints file):
```bash
[ "${CONDA_PREFIX:-}" = /home/xwc/envs/minisgl ] && python -m pip install --no-cache-dir ${PX:+--proxy "$PX"} -c /home/xwc/vllm/cu118/constraints.txt "flashinfer-python==0.5.3" "apache-tvm-ffi>=0.1.4,<0.2" && python -c "import torch, flashinfer; print('torch', torch.__version__, 'flashinfer', flashinfer.__version__)" || echo "STOP: install or import failed"
```
Expected: `torch 2.7.1+cu118 flashinfer 0.5.3`. If pip wants to change torch it refuses (constraint);
that is a **STOP** (FlashInfer would need a newer torch, i.e. CUDA 12).

Run every FlashInfer operation Mini-SGLang uses, against a PyTorch reference, on GPU 6 (< 1 GiB;
the first call of each op compiles its kernel, which is the actual test):
```bash
GPU=6 && env ${PX:+HTTPS_PROXY=$PX HTTP_PROXY=$PX} scripts/run_offline.sh cp5-flashinfer-$(date +%Y%m%d-%H%M%S) "$GPU" -- python scripts/compat/flashinfer_check.py
```
Expected: nine `PASS ...` lines and `RESULT: all FlashInfer ops pass`, or `FAIL ...` lines with
compiler errors. Any FAIL: **STOP #2** — with FlashInfer unusable under CUDA 11.8 there is no
reasonable adjustment left; the driver upgrade (or another server) is the answer.

Rollback: `python -m pip uninstall flashinfer-python apache-tvm-ffi` (asks); the compiled kernels sit in
`/home/xwc/vllm/cache/flashinfer` (see "Full rollback").

## CP6. Mini-SGLang's own kernels: exact list of what fails (CPU only, 5 min)

```bash
[ "${CONDA_PREFIX:-}" = /home/xwc/envs/minisgl ] && python -m pip install --no-cache-dir --no-deps -e . && python scripts/compat/jit_gate.py
```
(`--no-deps`: installs only the Mini-SGLang package link; no dependency is added or changed.)
Expected (prediction): `PASS radix.cpp`, `PASS tensor.cpp` (host compiler, if gcc ≥ 11),
`FAIL store.cu`, `FAIL index.cu`, `FAIL pynccl.cu` with `Value 'c++20' is not defined for option 'std'`.
`pynccl.cu` may additionally fail at link time (`-lnccl`: the PyTorch NCCL wheel ships only
`libnccl.so.2`); that would be a separate, small fix. This output is the scope of a possible C++17
port. Paste it to me.

Rollback: `python -m pip uninstall minisgl` (asks).

## CP7. Decision (with me)

| Outcome | Meaning | Recommendation |
| --- | --- | --- |
| CP2 `rc=0` | CUDA 12 works via forward compat | test the normal (CUDA 12) dependency set instead; no source change |
| CP5 FAIL | FlashInfer does not build with CUDA 11.8 | ask for a driver upgrade (≥ 535; ≥ 570 for CUDA 12.8) or use another server |
| CP5 PASS, CP6 FAIL (predicted) | only Mini-SGLang's ~2,000 lines of C++20 kernels block | choose: driver upgrade, **or** a C++17 port on a new branch `cu118-compat` (written and unit-tested by Claude Code locally; same kernel logic; `tests/kernel` must pass on GPU 6). Consequence for the plan: every arm then runs on `9a91cfa + port`, so `upstream` is no longer bit-identical to `9a91cfa` (PLAN 4.4, 5.1) and the plan needs an amendment |
| everything passes | Mini-SGLang runs unmodified | CP8 |

## CP8. Only after CP5 and CP6 pass: remaining packages, model, smoke tests

Remaining runtime packages (sgl_kernel and quack-kernels deliberately left out; `pip check` will
list them as missing — expected):
```bash
[ "${CONDA_PREFIX:-}" = /home/xwc/envs/minisgl ] && python -m pip install --no-cache-dir ${PX:+--proxy "$PX"} -c /home/xwc/vllm/cu118/constraints.txt "transformers>=4.56.0,<=4.57.3" accelerate msgpack pyzmq uvicorn fastapi prompt_toolkit openai modelscope pytest && python -m pip freeze > /home/xwc/vllm/cu118/requirements-cu118.lock && python -m pip check | head -n 20
```
Model (~1.5 GiB into `$HF_HOME`, set by `scripts/env.sh`):
```bash
env ${PX:+HTTPS_PROXY=$PX HTTP_PROXY=$PX} python -c "from huggingface_hub import snapshot_download; print(snapshot_download('Qwen/Qwen3-0.6B'))"
```
Kernel unit tests and the offline engine on GPU 6 (~3 GiB):
```bash
GPU=6 && scripts/run_offline.sh cp8-kernel-tests-$(date +%Y%m%d-%H%M%S) "$GPU" -- python -m pytest tests/kernel/test_index.py tests/kernel/test_store.py tests/kernel/test_tensor.py tests/core/test_cache_allocate.py -q -o addopts="" && scripts/run_offline.sh cp8-offline-smoke-$(date +%Y%m%d-%H%M%S) "$GPU" -- python scripts/compat/offline_smoke.py
```
Expected: tests pass; two short greedy completions and `RESULT: PASS`.
Server smoke (start, S1–S5, stop with confirmation), small KV cache:
```bash
GPU=6 && P=30000 && RID=cp8-serve-$(date +%Y%m%d-%H%M) && scripts/serve.sh "$RID" "$GPU" "$P" -- --model-path Qwen/Qwen3-0.6B --attention-backend fi --cache-type naive --page-size 1 --max-prefill-length 2048 --max-running-requests 16 --cuda-graph-max-bs 16 --num-pages 16384 && scripts/smoke.sh "$RID" "$GPU"
```
Expected: `ready: ...`, `S1/S2/S3 PASS`, confirm SIGTERM (`y` after checking all processes are
`xwc`), `S4 PASS`, `S5 PASS`, `smoke: all PASS`. If serve.sh reports a busy port, use `P=30010`.

## Full rollback (everything this plan created)

Each line shows what will be removed and asks before deleting; nothing outside these paths is touched.
```bash
conda deactivate 2>/dev/null; conda env remove -p /home/xwc/envs/minisgl; conda env remove -p /home/xwc/envs/cudacompat-probe
```
```bash
cd /home/xwc/vllm && ls -ld cache/* cu118 2>/dev/null && du -sh cache/* cu118 2>/dev/null; echo "Delete the directories listed above? Type DELETE:"; read -r a; [ "$a" = DELETE ] && rm -r -I cache/tvm-ffi cache/flashinfer cache/nvcc-probe.* cu118 || echo "nothing deleted"
```
(`cache/hf` holds the downloaded model; delete it the same way only if you no longer need it.)
The repository `/home/xwc/vllm/mini-sglang_dev` is not modified by any checkpoint except the
`pip install -e .` link in CP6, which `conda env remove` undoes.

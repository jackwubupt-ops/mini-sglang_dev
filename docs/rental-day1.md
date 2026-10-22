# Rented CUDA 12 GPU: requirements and day-1 runbook

Why a rented machine: `ps` has driver 520.61.05 (CUDA 11.8); the pinned dependencies need CUDA 12
(docs/cu118-compat-plan.md, PROGRESS 2026-10-20). Day 1 covers: environment, smoke test S1–S5,
golden outputs, and reproducers R4, R1, R5. Nothing here changes `python/minisgl` except the R5
logger on `instr`, exactly as on `ps`.

All scripts run with `MINISGL_HOST=rental` (scripts/host_profile.sh): the root is the directory
that contains the checkout, the owner is the current user, the Conda env is `<root>/envs/minisgl`.
The guards stay on (run directories never overwritten, GPU/port/disk checks, confirmed stops).
Claude Code still runs only on the Mac; nothing installs or starts an AI agent on the rental.

## 1. What to rent

| Item | Requirement | Why |
| --- | --- | --- |
| GPU | 1 × 24 GB, Ampere or Ada: **RTX 3090** (same class as `ps`), RTX 4090, A10 24 GB, L4, A5000 | PLAN's hypothesis is about one 24 GB GPU; `fi` backend, page size 1 |
| NVIDIA driver | **≥ 570** (`nvidia-smi` shows CUDA Version ≥ 12.8) | Dockerfile reference is CUDA 12.8.1; torch < 2.10 from PyPI is built for CUDA 12.8 |
| Image | Ubuntu 22.04/24.04 **CUDA 12.8 devel** image (has `nvcc`), e.g. `nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04` or the provider's "CUDA 12.8 devel / PyTorch" image | FlashInfer and Mini-SGLang compile kernels at first use with `nvcc` |
| CPU / RAM | ≥ 8 cores, ≥ 32 GB RAM | JIT compiling, tokenizer processes |
| Disk | ≥ 100 GB **persistent** data disk | env ~15 GB, models 1.5 GB (0.6B) + 8 GB (4B, later), runs |
| Access | SSH (user, host, port) and VS Code Remote-SSH | same workflow as `ps`; the tunnel below if GitHub/PyPI/HF are slow or blocked |
| Billing | by the hour; **shut down when idle** | day 1 needs about 2–4 GPU-hours [A] (first JIT compile is the slow part) |

Avoid: multi-tenant "shared GPU" offers, MIG slices, drivers < 570, images without `nvcc`.

## 2. [Win] SSH host entry (once)

Add to `%USERPROFILE%\.ssh\config`, with the three values from the provider's console:

```
Host minisgl-rent
    HostName <address from the provider>
    Port <port from the provider>
    User <user from the provider>
    RemoteForward 127.0.0.1:17897 127.0.0.1:7897
    ExitOnForwardFailure yes
    ServerAliveInterval 30
    ServerAliveCountMax 3
```
Connect with VS Code Remote-SSH to `minisgl-rent` and open a terminal there. The `RemoteForward`
line gives the rental the same Clash Verge tunnel as `ps` (keep Clash Verge running on Windows).

## 3. [rental] Day-1 commands, in order (bash, one terminal)

**D1. Root directory and read-only checks**
```bash
for d in /workspace /root/autodl-tmp; do [ -d "$d" ] && [ -w "$d" ] && R=$d && break; done; R=${R:-$HOME/work}; mkdir -p "$R" && echo "root: $R" && whoami && hostname && nvidia-smi && (nvcc --version || /usr/local/cuda/bin/nvcc --version) | tail -n 2; df -h "$R"; nproc; free -g | head -n 2; ldd --version | head -n 1; for t in git curl rsync ss ps setsid nohup tar sha256sum; do printf '%-9s %s\n' "$t" "$(command -v "$t" || echo MISSING)"; done
```
Expected: a writable root (`/workspace` or `/root/autodl-tmp` on most providers), the GPU table
with **CUDA Version ≥ 12.8** and no other process, `release 12.8` from nvcc, ≥ 100 GB free.
STOP if the driver is < 570 or there is no `nvcc`: change image/instance before paying for more time.

Only if D1 listed `MISSING` tools and you are root on the rental (single-tenant, so this is allowed
here, unlike on `ps`):
```bash
[ "$(id -u)" = 0 ] && apt-get update && apt-get install -y --no-install-recommends git curl rsync iproute2 procps util-linux ca-certificates || echo "not root: ask the provider for an image with these tools"
```

**D2. Which servers are reachable, directly or through the tunnel**
```bash
for u in https://github.com https://pypi.org/simple/pip/ https://files.pythonhosted.org https://download.pytorch.org/whl/ https://conda.anaconda.org/conda-forge/ https://huggingface.co/api/models/Qwen/Qwen3-0.6B; do d=$(curl -sS -o /dev/null -m 20 -w '%{http_code}' "$u" 2>&1 | tail -n 1); t=$(curl -sS -o /dev/null -m 20 -x http://127.0.0.1:17897 -w '%{http_code}' "$u" 2>&1 | tail -n 1); printf '%-50s direct=%-6s tunnel=%s\n' "$u" "$d" "$t"; done
```
Then set **one** of (all direct 200/301/302 → first line; otherwise second):
```bash
PX=
```
```bash
PX=http://127.0.0.1:17897; curl -sS -o /dev/null -I -m 20 -x "$PX" -w 'github via tunnel: %{http_code}\n' https://github.com
```

**D3. Clone the fork (`tooling`, plus local `upstream` and `instr`)**
```bash
if [ -e "$R/mini-sglang_dev" ]; then ls -ld "$R/mini-sglang_dev"; echo "STOP: exists; inspect it, do not clone again"; else git ${PX:+-c http.proxy=$PX} -c transfer.fsckObjects=true clone -b tooling https://github.com/jackwubupt-ops/mini-sglang_dev.git "$R/mini-sglang_dev" && cd "$R/mini-sglang_dev" && git branch --track upstream origin/upstream && git branch --track instr origin/instr && git for-each-ref --format='%(objectname) %(refname)' refs/heads/upstream refs/heads/tooling refs/heads/instr refs/tags/baseline-v1 && git diff --quiet 9a91cfa HEAD -- python/ pyproject.toml && echo "python/ is 9a91cfa"; fi
```
Expected: four refs matching the Mac (`git -C ~/mini-sglang for-each-ref ...`), `python/ is 9a91cfa`.

**D4. Miniforge (conda-forge only) and the Python 3.12 env, inside the root**
```bash
cd "$R" && if [ -e "$R/miniforge3" ]; then echo "miniforge exists"; else curl -fsSL ${PX:+-x $PX} -o Miniforge3-Linux-x86_64.sh https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh && curl -fsSL ${PX:+-x $PX} -o Miniforge3-Linux-x86_64.sh.sha256 https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh.sha256 && sha256sum -c Miniforge3-Linux-x86_64.sh.sha256 && bash Miniforge3-Linux-x86_64.sh -b -p "$R/miniforge3"; fi
```
(`-b` installs without touching `~/.bashrc`.)
```bash
source "$R/miniforge3/etc/profile.d/conda.sh" && if [ -e "$R/envs/minisgl" ]; then echo "env exists"; else env ${PX:+HTTPS_PROXY=$PX HTTP_PROXY=$PX} conda create -y -p "$R/envs/minisgl" -c conda-forge --override-channels python=3.12; fi && conda activate "$R/envs/minisgl" && python -c "import sys; print(sys.prefix)"
```

**D5. Session settings — run this block in every new terminal from now on**
```bash
export MINISGL_HOST=rental; for d in /workspace /root/autodl-tmp; do [ -d "$d" ] && [ -w "$d" ] && R=$d && break; done; R=${R:-$HOME/work}; source "$R/miniforge3/etc/profile.d/conda.sh" && conda activate "$R/envs/minisgl" && cd "$R/mini-sglang_dev" && scripts/check_env.sh && source scripts/env.sh && export CUDA_HOME=/usr/local/cuda && export PATH="$CUDA_HOME/bin:$PATH" FLASHINFER_CUDA_ARCH_LIST="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -n 1)" && echo "RUNS=$RUNS arch=$FLASHINFER_CUDA_ARCH_LIST" && nvcc --version | tail -n 1
```
Expected: `ok (rental): <user>, <root>/mini-sglang_dev, <root>/envs/minisgl`, `RUNS=<root>/runs`,
`arch=8.6` (3090/A10/A5000) or `8.9` (4090/L4). Set `PX` again (D2) in a new terminal.

**D6. Install the pinned project (as PLAN 4.5 step 4) and record the lock**
```bash
[ -z "$(git status --porcelain)" ] && [ "$(git branch --show-current)" = tooling ] && python -m pip install --no-cache-dir ${PX:+--proxy "$PX"} -e ".[dev]" torch-c-dlpack-ext && python -c "import torch, flashinfer; print('torch', torch.__version__, 'cuda', torch.version.cuda, 'gpu', torch.cuda.get_device_name(0)); print('flashinfer', flashinfer.__version__)" && (python -m pip freeze > "$RUNS/../requirements-rental.lock") && echo "lock: $(sha256sum "$RUNS/../requirements-rental.lock")" || echo "STOP: not clean/not on tooling, or install failed"
```
Expected: `torch 2.x.x+cu128 cuda 12.8 gpu NVIDIA GeForce RTX ...`, `flashinfer 0.x.x`.
(The `.[dev]` extra matches PLAN; sgl_kernel and quack-kernels are installed but not used by the
`fi` backend.)

**D7. GPU choice, environment record, model download**
```bash
scripts/pick_gpu.sh && GPU=0 && P=30000 && python scripts/capture_env.py --gpu "$GPU" --out "$RUNS/env-$(date +%Y%m%d-%H%M)/env.json" && env ${PX:+HTTPS_PROXY=$PX HTTP_PROXY=$PX} python -c "from huggingface_hub import snapshot_download; print(snapshot_download('Qwen/Qwen3-0.6B'))"
```
Expected: GPU 0 listed as a candidate, `wrote .../env.json`, a path under `$HF_HOME`.
`SFLAGS` for every server run today:
```bash
SFLAGS=(--model-path Qwen/Qwen3-0.6B --attention-backend fi --cache-type naive --page-size 1 --max-prefill-length 4096 --max-running-requests 64 --cuda-graph-max-bs 64 --num-pages 65536) && echo "${#SFLAGS[@]} flags set"
```

**D8. Smoke test S1–S5**
```bash
RID=smoke-$(date +%Y%m%d-%H%M) && scripts/serve.sh "$RID" "$GPU" "$P" -- "${SFLAGS[@]}" && scripts/smoke.sh "$RID" "$GPU"
```
Expected: `ready`, `Allocating 65536 tokens for KV cache`, S1–S3 PASS, answer `y` at the stop
prompt, S4 PASS, S5 PASS, `smoke: all PASS`. First start includes JIT compiling (minutes).
If it fails: `tail -n 60 "$RUNS/$RID/serve.log"`, then `scripts/stop_server.sh "$RID"`, and send
me the log lines.

**D9. Golden outputs twice (engine code = 9a91cfa)**
```bash
git diff --quiet 9a91cfa HEAD -- python/ pyproject.toml && GA=golden-a-$(date +%Y%m%d-%H%M%S) && scripts/run_offline.sh "$GA" "$GPU" -- python scripts/golden_outputs.py --num-pages 65536 --cuda-graph-max-bs 64 && GB=golden-b-$(date +%Y%m%d-%H%M%S) && scripts/run_offline.sh "$GB" "$GPU" -- python scripts/golden_outputs.py --num-pages 65536 --cuda-graph-max-bs 64 && python scripts/compare_outputs.py "$RUNS/$GA/outputs.json" "$RUNS/$GB/outputs.json"
```
Expected: `IDENTICAL: 64 requests`.

**D10. R4 (H4): overlap on, then off**
```bash
scripts/run_offline.sh r4-on-$(date +%Y%m%d-%H%M%S) "$GPU" -- python scripts/repro/r4_offline.py --num-pages 65536 --cuda-graph-max-bs 64 && MINISGL_DISABLE_OVERLAP_SCHEDULING=1 scripts/run_offline.sh r4-off-$(date +%Y%m%d-%H%M%S) "$GPU" -- python scripts/repro/r4_offline.py --num-pages 65536 --cuda-graph-max-bs 64
```

**D11. R1 (H1): overlap on, then off**
```bash
RID=r1-on-$(date +%Y%m%d-%H%M) && scripts/serve.sh "$RID" "$GPU" "$P" -- "${SFLAGS[@]}" && python scripts/repro/r1_client.py --run-id "$RID"; scripts/stop_server.sh "$RID"
```
```bash
RID=r1-off-$(date +%Y%m%d-%H%M) && MINISGL_DISABLE_OVERLAP_SCHEDULING=1 scripts/serve.sh "$RID" "$GPU" "$P" -- "${SFLAGS[@]}" && python scripts/repro/r1_client.py --run-id "$RID"; scripts/stop_server.sh "$RID"
```
Expected: event counts per `max_tokens` (`[1] [2] [8] [64]` = H1 not reproduced; `[1] [1] [7] [63]` = H1).

**D12. R5 (H5) on `instr`, then back to `tooling`**
```bash
[ -z "$(git status --porcelain)" ] && git switch instr && git diff --stat tooling instr -- python/ && RID=r5-$(date +%Y%m%d-%H%M) && MINISGL_DEBUG_DETOK_MAP=1 scripts/serve.sh "$RID" "$GPU" "$P" -- "${SFLAGS[@]}" && python scripts/repro/r5_client.py --run-id "$RID"; grep MINISGL_DEBUG_DETOK_MAP "$RUNS/$RID/serve.log" | tail -n 3; scripts/stop_server.sh "$RID"; python -m pytest tests/tooling -q -o addopts=""; [ -z "$(git status --porcelain)" ] && git switch tooling && git branch --show-current
```
Expected: `env.py` and `tokenizer/server.py` in the diff, `all 200 requests returned`, the last
map-size lines, the stop prompt, tooling tests pass (including the logger test), `tooling`.

**D13. Pack the results (never overwrites)**
```bash
mkdir -p "$R/outbox" && O="$R/outbox/day1-$(date +%Y%m%d-%H%M%S).tar.gz" && [ ! -e "$O" ] && tar -czf "$O" -C "$R" runs requirements-rental.lock && (cd "$R/outbox" && sha256sum "$(basename "$O")" > "$(basename "$O").sha256") && ls -l "$O" "$O.sha256"
```

## 4. Results back to the Mac, then stop paying

[Win] PowerShell (replace the file name with the one D13 printed):
```powershell
. { $ErrorActionPreference = 'Stop'
  $Name = 'day1-<stamp>.tar.gz'
  $In = Join-Path $env:USERPROFILE 'mini-sglang-transfer\inbox'; New-Item -ItemType Directory -Force -Path $In | Out-Null
  if (Test-Path (Join-Path $In $Name)) { throw "already in $In" }
  $Root = ssh minisgl-rent 'for d in /workspace /root/autodl-tmp; do [ -d "$d" ] && [ -w "$d" ] && echo "$d" && exit; done; echo "$HOME/work"'
  scp "minisgl-rent:$Root/outbox/$Name" "minisgl-rent:$Root/outbox/$Name.sha256" $In; if ($LASTEXITCODE -ne 0) { throw 'scp failed' }
  $Want = ((Get-Content (Join-Path $In "$Name.sha256")) -split '\s+')[0]
  $Got = (Get-FileHash -Algorithm SHA256 (Join-Path $In $Name)).Hash.ToLower()
  if ($Want -ne $Got) { throw 'sha256 mismatch' }; "ok: $In\$Name"
}
```
Move both files to the Mac's `~/Downloads`, then [Mac]:
```bash
cd ~/Downloads && shasum -a 256 -c day1-*.tar.gz.sha256 && mkdir -p ~/mini-sglang/rental && tar -xzkf day1-*.tar.gz -C ~/mini-sglang/rental && ls ~/mini-sglang/rental/runs
```
Then tell Claude Code; it runs the R1/R4/R5 judges and records everything.

**Only after the archive is verified on the Mac**, stop (or release) the instance in the
provider's console. Releasing usually deletes the data disk; stopping keeps it but may still bill
for storage.

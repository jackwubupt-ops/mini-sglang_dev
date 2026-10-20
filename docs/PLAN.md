# Mini-SGLang Mixed-Batch Scheduling: Consolidated Implementation Plan

Version 1.1 (final) · 2026-10-20 · Pinned baseline: sgl-project/mini-sglang `9a91cfa` · Execution host: shared server `ps` (account `xwc`)

## 0. How to use this document

**This is the single authoritative plan.** It replaces the 2026-10-19 project plan, the 2026-10-20 implementation manual, the Technical Clarifications document with its Amendment 1, and version 1.0 of this plan. Nothing in those earlier documents applies unless it is repeated here. Version 1.1 differs from 1.0 only in adopting the Mini-SGLang Shared Server Usage Policy (§4): no Docker, no `sudo`, no AI agent on the server, guarded commands, and shared-GPU measurement rules.

**Evidence labels used throughout:**

| Label | Meaning |
| --- | --- |
| [V] | Verified by reading source or git objects at `9a91cfa` (or the named PR head / FlashInfer tag) on 2026-10-20 |
| [H] | Hypothesis about a defect. It becomes a confirmed defect only when its reproducer fails on the `upstream` branch |
| [A] | Assumption or estimate (capacity, timing, budget) to be replaced by a measurement |

**Nothing in this plan has been executed on a GPU.** Every performance statement is a hypothesis; every correctness test is unexecuted.

**Server policy comes first.** All GPU work runs on the shared server `ps` under account `xwc`, following the administrator's usage policy summarised in §4.1. Where this plan and that policy differ, the policy wins.

**Using it with Claude Code.** Claude Code (like Codex) runs **only on your local machine**, never on the server, and its conversation files stay local. Commit the Markdown copy of this plan as `docs/PLAN.md` in your fork; §17 has the `CLAUDE.md` rules, the Week 1 local prompt and the server checklist. Line numbers below are at `9a91cfa` under `python/minisgl/` unless stated otherwise.

**Command safety.** Every Linux command in this plan carries a guardrail: it checks the user, path, branch or ownership first, prefers read-only or non-destructive forms, and asks for confirmation before anything destructive. No command uses `sudo`, `rm -rf`, `pkill` or `killall`.

## 1. Summary

**Goal.** Replace Mini-SGLang's prefill-first, phase-exclusive scheduler with decode-first mixed prefill+decode batching under one token budget (the policy of vLLM V1's `Scheduler.schedule()`), and prove what it changes with a complete measure → change → verify loop.

**Problem [V].** `Scheduler._schedule_next_batch` (`scheduler/scheduler.py:219`) returns `prefill_manager.schedule_next_batch(B) or decode_manager.schedule_next_batch()`. Whenever any prefill is admissible, no decode runs, so a long prompt (⌈L / B⌉ chunk steps) pauses every running stream. Whether this produces a measurable tail-latency problem on one 24 GB GPU is hypothesis A-stall, decided at the Week-4 gate (§13).

**Contribution, in order of certainty:**

1. A repaired, verified baseline: reproducers for five suspected overlap-scheduling defects (H1–H5, §3), fixes only for those confirmed, and structural KV-integrity checks.
2. Measurement infrastructure: a fixed benchmark client and a cross-process token trace that separates GPU time from CPU pipeline delay.
3. The mixed-batch policy (behind a flag), with kernel-level and model-level correctness tests.
4. A pre-registered paired evaluation against the repaired baseline, with upstream PR #155 and a simple "prefill-streak cap" as comparison arms.

**Prior art to credit [V]:** open, unmerged PRs #155 (mixed batching, no tests), #165 (radix `check_integrity`, no tests), #172 (detokenizer fix, with test), and #97 (optimistic admission with retraction). §16 maps each against this plan.

**Decision structure.** Weeks 1–5 build no scheduling policy. They establish the environment, run the reproducers, repair the baseline, calibrate measurement, and end with a written Go / Go-reduced / No-Go decision (§13). Weeks 6–13 implement and evaluate the outcome (§14) or the fallback (§15).

## 2. Verified facts that shape the design

| # | Fact [V] | Location | Consequence in this plan |
| --- | --- | --- | --- |
| F1 | `_process_last_data` decides prefill caching with `elif batch.is_prefill:`; under overlap, `Req` lengths have already advanced by the next step | `scheduler.py:163` | Per-request phase snapshot (C6) |
| F2 | `forward_batch` calls `req.complete_one()` on the CPU at launch; `finished = not req.can_decode` is evaluated one step later | `engine.py:200`, `scheduler.py:153` | Hypothesis H1 |
| F3 | `copy_done` is recorded inside `forward_batch` (`engine.py:205`), before `_forward` writes sampled tokens to `token_pool` (`scheduler.py:231`); the engine stream waits on the scheduler stream (`scheduler.py:102`), never the reverse | as listed | Hypothesis H2; event-ordered slot reuse (C3b) |
| F4 | `TableManager.allocate()` is `self._free_slots.pop()` and `free()` appends: the last freed slot is reused first (LIFO) | `table.py:17–20` | Raises H2 exposure |
| F5 | Aborts are handled at the start of the loop (`_process_one_msg`, `scheduler.py:190–195`) and free immediately; aborted requests never enter `finished_reqs` | as listed | Hypothesis H3 |
| F6 | `RadixPrefixCache.check_integrity()` and `NaivePrefixCache.check_integrity()` are `pass`; `CacheManager.check_integrity()` (`cache.py:81`) compares counters only and runs only from `run_when_idle()` (`scheduler.py:78`) | `radix_cache.py:187` | Structural checker (§6) |
| F7 | `max_extend_tokens` (budget B) is also `max_forward_len`, which sizes the PyNCCL buffer | `engine.py:124`, `scheduler/config.py:37` | Startup rule `max_running_req + P_min ≤ B` (§8) |
| F8 | `HybridBackend` routes by `batch.is_prefill`, so a mixed batch would go to the decode backend | `attention/base.py:49, 53` | Route by `is_decode` (C7) |
| F9 | `GraphRunner.can_use_cuda_graph` is `batch.is_decode and batch.size <= max_graph_bs`; `--cuda-graph-max-bs 0` disables graphs | `engine/graph.py:149`, `_determine_cuda_graph_bs` | Mixed steps run eager; graph factorial (§12) |
| F10 | Admission reserves `extend_len + max_tokens` plus `inflight_tokens`; `PrefillManager.schedule_next_batch` stops at the first request that does not fit (`else: break`) | `prefill.py:39, 147` | W2 derived from measured capacity (§10) |
| F11 | Benchmark client: first timestamp taken after `create(...)` returns; pooled gaps labelled TPOT; the final `finish_reason` chunk is timestamped; `input_length_override` is not read by the server | `benchmark/client.py:223, 236` | Client fixes (C1) |
| F12 | Every ack becomes exactly one SSE event; streaming responses carry `"id": "cmpl-{uid}"`; acks after the first `finished` are dropped by the API server | `server/api_server.py` (`listen`, `wait_for_ack`, `stream_chat_completions`) | Token accounting (§7) |
| F13 | With `MINISGL_DISABLE_OVERLAP_SCHEDULING=1`, `run_forever` runs `normal_loop` inside the engine stream context and processes each step immediately | `scheduler.py:121–126` | Overlap-off fallback has none of H1–H4 |
| F14 | `fi` backend asserts `page_size == 1`; `auto` picks `fa,fi` on Hopper and `fi` on Ampere/Ada | `attention/fi.py` `FIMetadata`, `engine.py` `_adjust_config` | Always pass `--attention-backend fi --page-size 1` |

## 3. Hypothesis register

**Status of every row: not yet run.** Reproducers are specified in §5.3. A hypothesis that is not reproduced keeps its reproducer as a regression test, and its fix is not applied to the baseline.

| ID | Hypothesis on `upstream` | Mechanism (from [V] facts) | Reproducer | Week |
| --- | --- | --- | --- | --- |
| H1 | With overlap on, length-terminated streaming requests deliver `max_tokens − 1` tokens | F2 + F12: `finished` turns true one token early; later acks are dropped | R1 | 1 |
| H2 | A reused table slot's prompt token can be overwritten by the previous owner's in-flight sampled-token write | F3 + F4: unordered writes to the same `token_pool` row on two streams | R2 | 2 |
| H3 | Aborting a request whose step is in flight can free its table slot and radix lock twice | F5: abort frees; `_process_last_data` may then free or `cache_req` again | R3 | 2 |
| H4 | With overlap on, offline outputs of EOS-terminated requests carry one extra token after EOS | The EOS-finished request is already in the next step; its token is appended and replied with `finished=False` | R4 | 1 |
| H5 | The detokenizer leaks one `decode_map` entry per EOS-terminated request | The H4 reply re-creates `decode_map[uid]` after deletion; it is never finished | R5 | 1 |
| A-stall | Under W2, prefill-only runs produce a large, scheduler-caused excess stall at Qwen3-4B on the server's GPU | §1 Problem | Gate G1–G2 | 4 |
| A-fi | FlashInfer's prefill wrapper is correct for 1-token rows in a ragged batch on the target GPU | Docstring of `causal=True` (§9) | K0–K3 | 3 |

## 4. Environment and Week 1 setup

### 4.1 Supported flags at `9a91cfa` [V]

| Use in scripts | Also accepted | How | Default |
| --- | --- | --- | --- |
| `--model-path` | `--model` | Explicit alias | required |
| `--attention-backend` | `--attn` | Explicit alias; values `fi`, `fa`, `trtllm` or `prefill,decode` pairs | `auto` |
| `--cache-type` | `--cache` | Argparse prefix abbreviation only (not an alias) | `radix` |
| `--max-prefill-length` | `--max-extend-length` | Explicit alias; sets B (`max_extend_tokens`) | 8192 |
| `--cuda-graph-max-bs` | `--graph` | Explicit alias; `0` disables graphs | auto |
| `--shell-mode` | `--shell` | Prefix abbreviation | off |
| `--max-running-requests`, `--memory-ratio`, `--num-pages`, `--page-size`, `--port` | — | — | 256, 0.9, auto, 1, 1919 |

Always use the full names in scripts; abbreviations break silently if a new flag is added.

### 4.2 Server usage policy (binding)

Source: Mini-SGLang Shared Server Usage Policy, administrator update of 2026-10-20.

| Allowed | Not allowed without prior approval |
| --- | --- |
| Project directories and environments under `/home/xwc` | Touching other users' files, directories or processes |
| VS Code Remote-SSH to view and edit your own remote project and run tests | Installing or running Codex or Claude Code on the server, or syncing their session files to it |
| Writing code locally (VS Code + Claude Code or Codex) and syncing through the fork `jackwubupt-ops/mini-sglang_dev` | Committing `.env` files, API keys, SSH keys, tokens, agent sessions or sensitive logs |
| Read-only inspection (`git status`, `pwd`, `nvidia-smi`) | Interfering with other GPU workloads, exhausting resources others need, bypassing a job scheduler |
| Dependencies inside a personal environment | `sudo`, system-wide packages, changes to shared Conda `base`, drivers or CUDA |
| Stopping only processes you started, after verifying their PIDs | `killall` / `pkill`, stopping other users' jobs |
| Caches, logs and results in your own directory, watching disk usage | Deleting shared caches, overwriting others' files, careless `rm -rf` / `chmod -R` |

There are no special GPU restrictions, but that is not exclusive access: check usage before every GPU job and follow any job scheduler that exists.

**Consequences for this plan:**

- **No Docker.** The repository `Dockerfile` is used only as the reference for versions; the environment is a personal Conda prefix environment.
- **No GPU clock locking** (it needs `sudo`). Clocks, temperature and power are logged instead and enter the run-validity rule (§10.2).
- **KV size is fixed with `--num-pages`**, never derived from `--memory-ratio`. `Engine._determine_num_pages` sizes the cache from the free memory at startup when no override is given (`engine.py:158–161`) [V]. On a shared GPU that both takes memory others may need and changes N_kv from run to run.
- **CUDA-graph sizes are fixed** with `--cuda-graph-max-bs`, because the default also depends on free memory (`_determine_cuda_graph_bs`) [V].
- **Servers are stopped by process group**, because `launch_server` starts non-daemon child processes (`server/launch.py`) [V].
- **Ports are checked first**: the server listens on `--port` and uses `--port + 1` for its distributed init (`server/args.py`, `distributed_addr`) [V].

### 4.3 Environment specification

| Component | Requirement | Source |
| --- | --- | --- |
| Host | Shared server `ps`, account `xwc`, home `/home/xwc` | Policy |
| Repository | `/home/xwc/my_projects/mini-sglang_dev`, clone of `jackwubupt-ops/mini-sglang_dev` | Policy |
| Python environment | Conda prefix environment `/home/xwc/envs/minisgl`, Python 3.12; never `base` | Policy; Python version from `pyproject.toml` and Dockerfile [V] |
| CUDA toolkit (for JIT kernels) | An `nvcc` matching the driver: use the system one if `nvcc --version` ≤ the driver's CUDA version; otherwise install `cuda-toolkit` from the `nvidia` Conda channel **into the personal environment** (12.8 if the driver supports it) | README rule "toolkit must match driver" [V] |
| torch | `< 2.10.0`, wheel built for the chosen CUDA version | `pyproject.toml` [V] |
| transformers | `>= 4.56.0, <= 4.57.3` | [V] |
| flashinfer-python | `>= 0.5.3`, unpinned upstream (latest stable tag v0.7.2): **pin on day 1** | [V] |
| sgl_kernel | `>= 0.3.17.post1` | [V] |
| apache-tvm-ffi | `>= 0.1.4`; also install `torch-c-dlpack-ext`, as the Dockerfile does | [V] |
| Caches | `HF_HOME`, `TVM_FFI_CACHE_DIR`, `FLASHINFER_WORKSPACE_BASE` (variable names from the Dockerfile [V]) under `/home/xwc/my_projects/cache` | Policy |
| Run data | `/home/xwc/my_projects/runs/<run_id>/` (raw logs, traces); small summaries, gate files and a `results/manifest.csv` of sha256 checksums are committed to the repo | Policy (no large logs in Git) |

Exact wheel versions could not be resolved while writing (no package-index access). Freeze them on day 1 with the guarded command in §4.5 and commit the lock from your local machine. Every run records the lock hash, the Git SHA, the GPU index and model, the driver and CUDA versions.

**Hardware.** Whatever GPU the server provides; record its model and memory in `results/env.json`. All runs of one comparison use the same GPU index on `ps`.

### 4.4 Guard and helper scripts (written locally on `tooling`, run on the server)

Two small files carry the guardrails. `scripts/check_env.sh` is executed (never sourced), so a failed check stops a script without closing your terminal; `scripts/env.sh` only exports variables.

```bash
#!/usr/bin/env bash
# scripts/check_env.sh -- exits non-zero unless user, directory and environment are right
PROJ=/home/xwc/my_projects/mini-sglang_dev
ENVP=/home/xwc/envs/minisgl
[ "$(id -un)" = "xwc" ] || { echo "STOP: run as xwc, not $(id -un)"; exit 1; }
case "$(pwd -P)" in "$PROJ"|"$PROJ"/*) ;; *) echo "STOP: cd into $PROJ first"; exit 1;; esac
[ "${CONDA_PREFIX:-}" = "$ENVP" ] || { echo "STOP: conda activate $ENVP (never base)"; exit 1; }
echo "ok: xwc, $PROJ, $ENVP"
```

```bash
# scripts/env.sh -- source after check_env.sh succeeds; exports only
export HF_HOME=/home/xwc/my_projects/cache/hf
export TVM_FFI_CACHE_DIR=/home/xwc/my_projects/cache/tvm-ffi
export FLASHINFER_WORKSPACE_BASE=/home/xwc/my_projects/cache/flashinfer
export RUNS=/home/xwc/my_projects/runs
```

Interactive use: `scripts/check_env.sh && source scripts/env.sh`. Every server script begins with `set -euo pipefail`, runs `check_env.sh`, then sources `env.sh`.

| Script | Does | Guardrails |
| --- | --- | --- |
| `scripts/pick_gpu.sh` | Prints each GPU's index, model, memory used/total, utilisation and its compute processes (`nvidia-smi --query-gpu=...`, `--query-compute-apps=...`); lists GPUs with no compute process and < 1 GiB used | Read-only; never sets or kills anything |
| `scripts/serve.sh RUN_ID GPU PORT -- <server flags>` | Starts one server under `setsid nohup` with `CUDA_VISIBLE_DEVICES=GPU`, logs to `$RUNS/RUN_ID/`, writes `server.pgid` and a pre-run GPU snapshot; starts a 5 s `nvidia-smi` logger (clocks, temperature, power, memory, processes) in the same process group | Refuses if `$RUNS/RUN_ID` exists, if `GPU` has another user's compute process, if `PORT` or `PORT+1` is in use (`ss -ltn`), if free disk under `/home/xwc` is < 20 GiB, if `sinfo`/`squeue` exist (use the scheduler instead), or if `--num-pages` or `--cuda-graph-max-bs` is missing |
| `scripts/stop_server.sh RUN_ID` | Stops that run's process group with `SIGTERM`, waits up to 60 s, reports survivors | Reads the PGID from `$RUNS/RUN_ID/server.pgid`; lists `ps -o user=,pid=,args= -g PGID`; aborts unless every process belongs to `xwc` and the group leader's command contains `minisgl`; asks for confirmation; never sends `SIGKILL` without a second confirmation |
| `scripts/run_offline.sh RUN_ID GPU -- <command>` | Runs one offline GPU job (golden outputs, R2, R4, ID tests, K/M tests, `pytest` on GPU) under `setsid` with `CUDA_VISIBLE_DEVICES=GPU`, logging to `$RUNS/RUN_ID/` with the same 5 s GPU logger and a `job.pgid` file | Same GPU, disk, scheduler and run-directory checks as `serve.sh`; offline engines must be given `num_page_override` and `cuda_graph_max_bs` explicitly; stopped only through `stop_server.sh RUN_ID` (which also accepts `job.pgid`) |
| `scripts/run_matrix.py` | Runs comparisons through `serve.sh` / `stop_server.sh` (§11.2) | Refuses without a passing `results/gates/G6.json` for the current commit (§13.1); checks the GPU before every run |

**Running "upstream" code on the server.** The `tooling` branch is cut from `9a91cfa` and changes nothing under `python/` or `pyproject.toml`. Reproducers and golden outputs for `upstream` therefore run from a `tooling` checkout after verifying `git diff --quiet 9a91cfa HEAD -- python/ pyproject.toml`; the scripts are then present while the engine code is exactly `9a91cfa`.

### 4.5 Week 1 setup on the server (VS Code Remote-SSH terminal)

1. **Read-only checks:**

```bash
whoami; hostname; pwd; echo "$HOME"; ls -ld "$HOME"
nvidia-smi
command -v sinfo; command -v squeue     # only checks whether Slurm is present
command -v nvcc && nvcc --version
df -h /home/xwc
```

If `sinfo` or `squeue` exist, stop and adapt §4.4 to the scheduler before any GPU job.

2. **Clone the fork once** (no second fork, no second clone):

```bash
if [ "$(id -un)" = "xwc" ]; then
  mkdir -p /home/xwc/my_projects && cd /home/xwc/my_projects && pwd
  if [ -e mini-sglang_dev ]; then ls -ld mini-sglang_dev; echo "exists: inspect it, do not clone again"
  else git clone https://github.com/jackwubupt-ops/mini-sglang_dev.git; fi
else echo "STOP: not xwc"; fi
```

3. **Create the personal environment** (never `base`):

```bash
if [ "$(id -un)" != "xwc" ]; then echo "STOP: not xwc"
elif [ -e /home/xwc/envs/minisgl ]; then echo "env exists: inspect it, do not recreate"
else conda create -y -p /home/xwc/envs/minisgl python=3.12; fi
conda activate /home/xwc/envs/minisgl
python -c "import sys; print(sys.prefix)"     # must print /home/xwc/envs/minisgl
```

If step 1 showed no `nvcc` compatible with the driver, install the toolkit into this environment only: `conda install -y -p /home/xwc/envs/minisgl -c nvidia cuda-toolkit=<version the driver supports, 12.8 if possible>`.

4. **Check out `tooling` and install inside the environment** (on your local machine first: fetch `9a91cfa` from `sgl-project/mini-sglang`, create `upstream` and `tooling` from it, tag `baseline-v1`, push all three to the fork):

```bash
cd /home/xwc/my_projects/mini-sglang_dev && scripts_ok=0
if [ -n "$(git status --porcelain)" ]; then echo "STOP: uncommitted changes on the server"
else
  git fetch origin && git switch tooling && git pull --ff-only origin tooling
  if git diff --quiet 9a91cfa HEAD -- python/ pyproject.toml && scripts/check_env.sh; then scripts_ok=1; fi
fi
if [ "$scripts_ok" = 1 ]; then
  source scripts/env.sh
  python -m pip install -e ".[dev]" torch-c-dlpack-ext
  if [ -e results/requirements.lock ]; then echo "lock exists: not overwritten"
  else python -m pip freeze > results/requirements.lock; fi
fi
```

If torch's default wheel does not match the chosen CUDA version, reinstall torch from the matching PyTorch index inside the same environment before the project install. Commit the lock locally (copy it down with the `rsync` command below), never from the server.

5. **Smoke test** (`scripts/smoke.sh`, checks S1–S5, pass conditions in brackets). Pick a GPU with `scripts/pick_gpu.sh` and a free port P (P and P+1 unused), then:

```bash
scripts/check_env.sh && source scripts/env.sh && \
scripts/serve.sh smoke-$(date +%Y%m%d-%H%M) <GPU> <P> -- \
  --model-path Qwen/Qwen3-0.6B --attention-backend fi --cache-type naive --page-size 1 \
  --max-prefill-length 4096 --max-running-requests 64 --cuda-graph-max-bs 64 --num-pages 65536
```

- **S1** `curl -sf localhost:<P>/v1/models` [lists the model].
- **S2** Non-streaming request [returns text]: `curl -s localhost:<P>/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"Qwen/Qwen3-0.6B","messages":[{"role":"user","content":"Count to five."}],"max_tokens":16,"temperature":0}'`
- **S3** Same with `"stream":true` [ends with `data: [DONE]`].
- **S4** `scripts/stop_server.sh <RUN_ID>` [no process of the group left; the GPU memory is released in `nvidia-smi`].
- **S5** Existing tests on the chosen GPU [all pass]: `scripts/run_offline.sh tests-$(date +%Y%m%d-%H%M) <GPU> -- python -m pytest tests/core/test_cache_allocate.py tests/kernel/test_index.py tests/kernel/test_store.py tests/kernel/test_tensor.py tests/misc -q`. Skip `tests/core/test_scheduler.py` (manual script for Llama-3.1-8B) and `tests/kernel/test_comm.py` (multi-GPU).

**Sizing `--num-pages`.** With page size 1, pages = tokens. Choose N_kv so that weights + N_kv × (KV bytes per token) + about 3 GiB for graphs and activations stays below the chosen GPU's free memory minus a 4 GiB margin. Qwen3-4B needs 144 KiB per token [V-derived from its config] and about 8 GiB of weights; Qwen3-0.6B about 112 KiB per token [A]. Example: N_kv = 65,536 for Qwen3-4B needs roughly 8 + 9 + 3 = 20 GiB. The startup line `Allocating N tokens for KV cache` (`engine.py`) [V] confirms the value.

**Golden outputs (Week 1).** Offline `LLM` on `upstream`, Qwen3-0.6B, greedy, 64 fixed prompts (8 of 1–16 tokens, 32 of 100–2,000, 16 of 4k–8k, 8 longer than B), `ignore_eos=True`, fixed `max_tokens` per prompt, `num_page_override` and `cuda_graph_max_bs` set explicitly, run through `scripts/run_offline.sh`. Save `tests/golden/upstream.json`. Run twice; pass: bitwise identical. Reproducers R1, R4 and R5 (§5.3) also run in Week 1.

**Moving results.** Pull run data to your local machine with a non-destructive copy run **locally**: `rsync -av --ignore-existing xwc@ps:/home/xwc/my_projects/runs/ ./runs/` (no `--delete`).

## 5. Branches, baseline isolation and the overlap lifecycle

### 5.1 Branches

| Branch | Contents | Used for |
| --- | --- | --- |
| `upstream` | `9a91cfa`, tag `baseline-v1`, never modified | Reference for reproducers R1–R5 and bug impact; run on the server through `tooling` (§4.4) |
| `instr` | `upstream` + C1 (tracer, client fixes, debug checks), all off by default | Observing reproducers; must be behaviour-neutral (ID1) |
| `baseline-fixed` | `instr` + fixes for **confirmed** hypotheses only (C2, C3a, C3b per the rule below) + PR #172 + PR #165 (cherry-picked, authorship preserved) + C4 | **Reference arm for every performance comparison** |
| `mixed` | `baseline-fixed` + C5–C8 (`--schedule-policy`, default `prefill_first`) | Treatment arms: `mixed`, `decode_first`, `streak_cap` |
| `pr155` | `baseline-fixed` + PR #155 (`2862051`); conflict resolution with C2 recorded in `docs/pr155-merge.md` | Comparison arm |

**Repository.** All branches live in your fork `jackwubupt-ops/mini-sglang_dev`. `9a91cfa` is fetched from `sgl-project/mini-sglang` on your local machine and pushed to the fork. Code is written and committed locally, pushed, and pulled on the server with `git pull --ff-only` after checking a clean tree. The policy's `dev` branch is the integration branch: it tracks whichever of `instr`, `baseline-fixed` or `mixed` is current, and the server only ever fast-forwards.

**Tooling branch.** Scripts, configs, pre-registration files and results live on a `tooling` branch cut from `upstream` with no changes under `python/minisgl/`; it is merged into `instr` and every later branch.

**Fix-inclusion rule:** C2 enters `baseline-fixed` if H1 is confirmed; C3a if any of H3, H4, H5 is confirmed; C3b if H2 is confirmed. The three PR cherry-picks apply cleanly to `9a91cfa` in the order #172, #165, #155 [V, tested].

### 5.2 What "identical" means

| Comparison | Precondition | Claim | Test |
| --- | --- | --- | --- |
| `upstream` vs `instr` | Debug flags off | Same step sequence and same tokens | ID1 |
| `upstream` vs `baseline-fixed`, offline, `ignore_eos=True` | `max_running_req` ≥ concurrent requests + 2, so slot quarantine never delays admission | Same step sequence `[(uid, extend_len)…]` ⇒ bitwise-identical token ids | ID2 |
| Same, `ignore_eos=False` | As above | Identical up to and including termination; `upstream` may have exactly one extra trailing token per EOS-terminated request (H4) and nothing else | ID3 |
| Same, online streaming | — | Token counts differ by exactly the H1 effect; text of the shared prefix identical | R1 |
| `mixed --schedule-policy prefill_first` vs `baseline-fixed` | Same code path | Bitwise identical; then all policy arms run from one binary | ID2 on `mixed` |

- **ID1:** run `upstream` and `instr` (flags off) twice each. Pass: identical token ids across all four runs; step sequence in `instr` identical with tracing on and off.
- **ID2:** dump the step sequence and token ids from `upstream` (via a throwaway hook on a scratch copy) and `baseline-fixed`. Pass: both equal. If step sequences differ, the test is inconclusive (not failed): rerun with a larger `max_running_req`.
- **ID3:** prompts that terminate early ("Answer with one word: yes or no."), `max_tokens=64`. Pass: `baseline-fixed` output equals `upstream` output with at most its final token removed; `baseline-fixed` overlap on equals overlap off.

Performance results are never compared against `upstream`. Bug-impact numbers come only from `upstream` vs `baseline-fixed` and are reported separately.

### 5.3 Reproducers (run on `upstream` / `instr` before any fix)

Each records **confirmed** or **not reproduced** (never "absent") in `results/repro/<ID>.json`; raw logs stay in its run directory under `/home/xwc/my_projects/runs/` and are listed in `results/manifest.csv`.

| ID | Setup | Observation | Confirmed if |
| --- | --- | --- | --- |
| R1 (H1) | Online, Qwen3-0.6B, 32 streaming requests, `ignore_eos=true`, `max_tokens ∈ {1, 2, 8, 64}`; overlap on, then `MINISGL_DISABLE_OVERLAP_SCHEDULING=1`. No code change needed | SSE events per request with `finish_reason == null` (one per delivered ack, F12) | On: T − 1 events for every T ≥ 2; off: T |
| R2 (H2) | `instr`, offline `LLM`, `max_running_req=2`, alternating A (64-token prompt, `max_tokens=16`, `ignore_eos=true`) and B (1,024-token prompt); 2,000 requests; `MINISGL_DEBUG_CHECK_POOL=1` compares `token_pool[slot, :len]` with host `input_ids` when B's prefill result is processed | Mismatch count and positions; count of immediate slot reuses. If R1 is not confirmed, rerun with A using `ignore_eos=false` and early-terminating prompts | ≥ 1 mismatch at the previous owner's write position (64 + 16 − 1 = 79) |
| R3 (H3) | Online, `--cache-type radix`, `--max-running-requests 4`; clients disconnect (a) immediately after sending, (b) after the first chunk; `max_tokens=4`; 2,000 requests; debug assertion that `TableManager._free_slots` has no duplicates | Duplicate slot; `assert node.ref_count >= 0` in `lock_handle` [V]; integrity failure at idle | Any of the three |
| R4 (H4) | Offline, `ignore_eos=false`, 64 early-terminating prompts, overlap on vs off | Per-request outputs | On-output = off-output + exactly one trailing token for each EOS-terminated request |
| R5 (H5) | Online, 200 EOS-terminated requests, then idle; `instr` logs `len(DetokenizeManager.decode_map)` every 5 s | Map size at idle | > 0 and proportional to the number of EOS-terminated requests |

**Acceptance on `baseline-fixed`:** R1 gives T events with overlap on; R2 shows 0 mismatches in ≥ 10,000 immediate reuses; R3 completes with no duplicate, no assertion and a clean integrity check; R4 outputs equal overlap-off outputs; R5 map size is 0 at idle.

### 5.4 Resource analysis under overlap

**When a request is still referenced by an in-flight step [V].** Each `overlap_loop` iteration: receive messages (aborts handled here) → schedule and launch step k → `_process_last_data(k − 1)`. A request finished by EOS while processing step n is always inside step n+1. Under H1, so are length-terminated requests. An abort for a running request lands while its latest step is in flight.

| Resource | How in-flight step n+1 uses it [V] | Next owner's access [V] | Ordered? | Verdict |
| --- | --- | --- | --- | --- |
| `token_pool[slot]` | Engine stream: input gather (`scheduler.py:229`), sampled-token scatter at `device_len` or −1 (`:231`, `_make_write_tuple`) | Prompt copy in `PrefillAdder._try_allocate_one` / `_add_one_req` on the scheduler stream, `non_blocking` | **No** (F3) | H2: event-ordered reuse (C3b) |
| `page_table[slot]` | Read by GPU ops issued on the scheduler stream in `_prepare_batch` (`out_loc`, FlashInfer `indices`) before the engine stream is released | Cached-prefix copy and `_write_page_table`, also on the scheduler stream, later | Yes, same stream | Safe |
| KV pages | Written through `out_loc` on the engine stream; pages may already be free or in the radix tree | Writes and reads by later steps on the same engine stream | Yes, same stream | Safe |
| Table-slot index (CPU free list) | — | Abort path can free twice (H3) | n/a | Once-only retirement (C3a) |
| Radix handle locks (CPU) | — | Abort frees and unlocks; `_process_last_data` may `cache_req` or free again (H3) | n/a | Once-only retirement (C3a) |
| Host `input_ids`, replies | — | EOS-finished request gets an extra append and reply (H4, H5) | n/a | Skip retired requests (C3a) |

### 5.5 Lifecycle fixes (applied per the inclusion rule)

**C2 — finish detection (H1).** At `scheduler.py:153`: `finished = len(req.input_ids) >= req.max_device_len`, EOS rule unchanged (same rule as PR #155; credit it).

**C3a — once-only retirement (H3, H4, H5).**

1. One function `_retire(req)` used by the finish path in `_process_last_data` and the abort path in `_process_one_msg`: if `req` is already retired, return; otherwise remove it from the decode manager, `cache_req(req, finished=True)`, hand its slot to C3b (or free it directly if C3b is not included), and add `req` to `self.retired`.
2. `_process_last_data` skips any `req in self.retired` before appending a token or replying.
3. Abort of a pending request that was never scheduled frees immediately (nothing in flight).

**C3b — event-ordered slot reuse (H2).**

1. In `_forward`, right after `self.token_pool[output_mapping] = ...` (`scheduler.py:231`), record `self.last_write_event` on the engine stream.
2. Retired slots go to `slot_quarantine` as `(table_idx, self.last_write_event)`.
3. At the start of `_schedule_next_batch`, release each quarantined slot whose event `query()` is true. Prune the matching `self.retired` entries at the same time.

```python
# sketch, scheduler.py (C3a + C3b)
def _retire(self, req: Req) -> None:
    if req in self.retired:
        return
    self.decode_manager.remove_req(req)
    self.cache_manager.cache_req(req, finished=True)
    self.slot_quarantine.append((req.table_idx, self.last_write_event, req))
    self.retired.add(req)

def _release_quarantined_slots(self) -> None:
    still = []
    for idx, ev, req in self.slot_quarantine:
        if ev is None or ev.query():
            self.table_manager.free(idx)
            self.retired.discard(req)
        else:
            still.append((idx, ev, req))
    self.slot_quarantine = still
```

The integrity checker (§6) counts quarantined slots as owned.

## 6. KV-cache integrity checking

**Facts:** F6. PR #165 (`c5d10f1`) replaces the radix no-op with a tree walk that checks I1, I2 and duplicate page indices within the tree, plus cycle detection; it has no tests [V].

**Checker `tests/support/kv_integrity.py`** (builds on #165; debug-only behind `MINISGL_DEBUG_INTEGRITY=1`):

| # | Invariant | Derived from [V] | Covered by #165 |
| --- | --- | --- | --- |
| I1 | Σ length of non-root nodes = `evictable_size + protected_size` | Counter updates in `insert_prefix`, `evict`, `lock_handle` | Yes |
| I2 | `evictable_size` = Σ length with `ref_count == 0`; `protected_size` = Σ with `ref_count > 0` | `lock_handle` 0 ↔ 1 transitions | Yes |
| I3 | Every non-root node: `ref_count ≥ Σ children's ref_count` | Locking increments the node and all ancestors | No |
| I4 | `len(key) == len(value) == length`; `parent.children[key_fn(key)] is node` | `set_key_value`, `set_parent`, `split_at` | No |
| I5 | Page ownership is a partition across free slots, tree values, live requests (`page_table[slot, handle.cached_len : device_len]`) and quarantine | `allocate_paged`, `cache_req`, `_free` | Within-tree duplicates only |
| I6 | At idle: all non-root `ref_count == 0`, `protected_size == 0`, every table slot free, quarantine empty | Every lock unlocked, every slot freed | No |

**Use:** correctness and stress runs use `--cache-type radix`; performance runs use `naive`. Stress runs call I1–I4 every 1,000 steps and I5–I6 at forced quiesce points (stop admitting, drain, check, resume). Never enable in benchmark runs.

**Acceptance:** seeded-bug tests (skip one unlock; free one page twice; corrupt one child key) each make the intended invariant fail; all invariants pass on `baseline-fixed` through the 30-minute stress run (G6d).

## 7. Measurement: benchmark client and token tracing

### 7.1 Client fixes (C1) [V, F11]

- TTFT starts at `t_send`, taken before `client.chat.completions.create(...)`.
- Parse the scheduler uid from each chunk's `id` (`cmpl-{uid}`).
- Count and timestamp only events with `finish_reason == null`; the final `"stop"` event is not a token.
- Report separately: ITL (all gaps, pooled per run), per-request TPOT = (E2E − TTFT) / (tokens − 1), per-request max gap.
- Build exact-length prompts with `generate_prompt(tokenizer, n)`; verify lengths from the scheduler's `UserMsg` log (inputs pass through the chat template).
- Save raw per-request timestamps as JSON for every run.

### 7.2 Units at each hop [V]

| Hop | Unit | Breaks a 1:1 token mapping when |
| --- | --- | --- |
| Scheduler `_process_last_data` | One `DetokenizeMsg` per request per processed step; none for `ChunkedReq` | Post-finish messages (H1, H4) |
| Tokenizer server, `DetokenizeManager.detokenize` | One string per message; batches whatever is queued | Empty string for EOS or an incomplete UTF-8 piece; wrong text when one batch holds several messages for one uid (fixed by PR #172), count unchanged |
| API `listen()` | Acks for known uids only | Acks after the first `finished` are dropped |
| `stream_chat_completions` | One SSE event per ack; then one `finish_reason: "stop"` event with no token; then `[DONE]` | The final event is not a token |
| Client SDK | One chunk per event | TCP coalescing can give several events near-identical timestamps |

**Counting rule.** For request u, the k-th event with `finish_reason == null` is the k-th ack yielded by the API server and the k-th scheduler message for u. Scheduler messages after u's first `finished` are logged as `undelivered` and excluded from ITL. Text is never used to count.

### 7.3 Trace points (C1, enabled by `MINISGL_TRACE_DIR`, always a run directory under `/home/xwc/my_projects/runs/`)

| Timestamp | Where | Class |
| --- | --- | --- |
| `gpu_start`, `gpu_end` per step | CUDA events on the engine stream: after `wait_stream`; after the `token_pool` write | **GPU-derived**, converted to host time by calibration |
| `t_ready`, `was_ready` | `_process_last_data`: `copy_done.query()`, then `copy_done.synchronize()`, then stamp | CPU observation; equals D2H completion only if `was_ready` is false |
| `t_sched_send` | after `self.send_result(reply)` | CPU observation |
| `t_detok_in`, `t_detok_out` | `tokenizer/server.py` after `recv_listener.get()` and after `send_frontend.put(...)` | CPU observation |
| `t_api_in`, `t_api_yield` | `listen()` when appending; `stream_chat_completions` before `yield` | CPU observation (not wire time) |
| `t_send`, `t_client_recv[k]` | client | CPU observation in another process |

All host timestamps use `time.monotonic_ns()` (`CLOCK_MONOTONIC`, shared across processes on one Linux host). Client and server run on the same host for diagnostic runs. Each process writes JSONL to its own file; merge offline on `(uid, k)`.

**GPU-to-host calibration.** At startup and every 60 s: record E0 on the engine stream, `E0.synchronize()`, read `t0 = time.monotonic_ns()`. Then `host_time(E) = t0 + E0.elapsed_time(E) × 10⁶` ns.

**Gap attribution:** client gap = Δ`gpu_end` (scheduler/GPU) + Δ(`t_api_yield` − `gpu_end`) (CPU pipeline) + Δ(`t_client_recv` − `t_api_yield`) (transport and client). The stall hypothesis concerns only the first term.

### 7.4 Step log and admission counters (C1)

Per step: step id, phase, uids and `extend_len` per request, `prefill_flags`, `sched_cpu_ms`, `gpu_start`, `gpu_end`. Per request: `t_arrival` (`UserMsg` received), `t_admit` (first chunk scheduled). Counters for why `PrefillAdder.try_add_one` returned `None`: token budget, KV reservation, no table slot.

### 7.5 Tests

| ID | Test | Pass |
| --- | --- | --- |
| T1 | Join completeness on a W2 run (`baseline-fixed`) | For 100% of requests, null-`finish_reason` events = API yields = scheduler messages up to the first `finished`; 0 `undelivered` |
| T2 | Calibration: `torch.cuda._sleep` kernel between two events; host-time difference vs `elapsed_time` | \|error\| < 100 µs; drift < 50 µs per minute |
| T3 | Coalescing: client gaps vs `t_api_yield` gaps | If > 1% of client gaps are < 50 µs while server gaps are not, server-egress gaps become the primary signal (recorded in `prereg/*.yaml` before confirmatory runs) |
| T4 | Overhead: tracing on vs off, A/A protocol (§11) | Primary metric and throughput differences inside the A/A noise bound |

## 8. Mixed-batch design

### 8.1 Algorithm

Each step:

1. D = all requests in `DecodeManager.running_reqs`, sorted by uid (one token each). `DecodeManager.schedule_next_batch()` mutates nothing [V].
2. R = B − |D|. If R > 0, `PrefillManager.schedule_next_batch(R)` admits chunks. Its KV reservation starts at `decode_manager.inflight_tokens` and is unchanged, so a mixed step admits a subset of what the baseline would admit [V-derived].
3. Emit one batch: pure decode, pure prefill, or mixed (decode requests first, then prefill requests), with `prefill_flags` fixed at construction.

**Budget rule.** `max_running_req + P_min ≤ B`, checked at startup when `--schedule-policy mixed` is selected; then |D| ≤ `max_running_req` and R ≥ P_min always. P_min = 256 by default (`--min-prefill-tokens`). Decode is never split across steps; runtime raising (as PR #155 does) is rejected.

**Disjointness [V-derived].** `running_reqs` holds only requests with `can_decode`, never a `ChunkedReq`; a request leaves `pending_list` when its last chunk is scheduled and joins `running_reqs` in that step's `_forward`. A duplicate-uid assertion stays on anyway.

### 8.2 Commit list (all lines at `9a91cfa`)

| # | Change | Location | Detail |
| --- | --- | --- | --- |
| C1 | Tracing, client fixes, debug checks | `benchmark/client.py:223, 236`; `scheduler/scheduler.py:143, 167, 229–231`; `tokenizer/server.py`; `server/api_server.py`; `env.py` | §7; env vars `TRACE_DIR`, `DEBUG_CHECK_POOL`, `DEBUG_INTEGRITY`, `DEBUG_DETOK_MAP`, `DEBUG_ALL_LOGITS` added to `EnvClassSingleton` (read with the `MINISGL_` prefix) |
| C2 | Finish detection | `scheduler/scheduler.py:153` | §5.5 |
| C3a | Once-only retirement | `scheduler/scheduler.py:147–166, 190–201` | §5.5 |
| C3b | Event-ordered slot reuse | `scheduler/scheduler.py:219, 231`; `scheduler/table.py:17–20` | §5.5 |
| C4 | Integrity checker | `tests/support/kv_integrity.py`; hook at `scheduler/scheduler.py:78` | §6 |
| C5 | Policy flag, no behaviour change | `scheduler/config.py:16–17`; `server/args.py` near 206 | `--schedule-policy {prefill_first, decode_first, mixed, streak_cap}`, `--min-prefill-tokens`, `--max-prefill-streak`; startup validation: `mixed` requires `fi`, TP = 1 and the budget rule |
| C6 | Per-request phase snapshot | `core.py:74, 84–90`; `scheduler/scheduler.py:163` | `phase` adds `"mixed"`; `prefill_flags` tuple; `elif batch.prefill_flags[i]:`; `is_prefill`/`is_decode` stay strict |
| C7 | Mixed policy | `scheduler/scheduler.py:219` | `_schedule_mixed_batch` per §8.1 |
| | | `layers/embedding.py:92` | `if not batch.is_decode:` (gather last positions) |
| | | `attention/fi.py:203, 224` | Choose the `cu_seqlens_q` branch by phase (`is_decode` → `arange`; pure prefill without cache hit → `cu_seqlens_k`; else explicit cumsum); extract `_plan_kwargs(metadata)` from `_initialize_metadata_once` (pure refactor, used by K1) |
| | | `attention/base.py:49, 53` | `self.decode_backend if batch.is_decode else self.prefill_backend` |
| | | `attention/trtllm.py:60` | Raise on mixed batches |
| | | `engine/graph.py:160` | Assert mixed batches are never padded (no other change; F9) |
| C8 | Streak-cap policy | `scheduler/scheduler.py:219` | After K consecutive prefill-only steps while `decode_manager.runnable`, schedule decode |
| C9 (optional) | Per-request prefill cap | `scheduler/prefill.py:73` | `chunk_size = min(self.token_budget, remain_len, cap)` |

```python
# sketch, scheduler.py (C7)
def _schedule_mixed_batch(self) -> Batch | None:
    decode = self.decode_manager.schedule_next_batch()          # sorted by uid
    n_dec = decode.size if decode else 0
    remaining = self.prefill_budget - n_dec                      # >= P_min by startup rule
    prefill = self.prefill_manager.schedule_next_batch(remaining)
    if prefill is None:
        return decode
    if decode is None:
        return prefill
    reqs = decode.reqs + prefill.reqs
    assert len({r.uid for r in reqs}) == len(reqs)
    assert sum(r.extend_len for r in reqs) <= self.prefill_budget
    return Batch(reqs=reqs, phase="mixed",
                 prefill_flags=(False,) * n_dec + (True,) * prefill.size)
```

### 8.3 Invariants and their tests

| # | Invariant | Test |
| --- | --- | --- |
| V1 | Each request at most once per batch, in exactly one phase | CPU unit test `tests/core/test_mixed_policy.py` |
| V2 | Input tokens per forward ≤ B; prefill share ≥ P_min whenever prefill is pending | Unit test + runtime assertion |
| V3 | KV reservation never exceeded; decode pages always allocatable | Stress run (G6d), `allocate_paged` never asserts |
| V4 | Resources retired exactly once; no slot reused while referenced in flight | R2, R3 on the branch under test |
| V5 | Exactly `max_tokens` tokens with `ignore_eos`, overlap on and off | R1 on the branch under test |
| V6 | I1–I6 hold at every check point | §6 |
| V7 | `prefill_first` on `mixed` reproduces `baseline-fixed` bitwise | ID2 |
| V8 | Startup rejects `mixed` with `trtllm`, TP > 1, or `max_running_req + P_min > B` | Unit test |

## 9. Attention and model correctness

### 9.1 FlashInfer facts [V]

- `pyproject.toml` allows any `flashinfer-python >= 0.5.3`; the latest stable tag is v0.7.2. The version you get depends on install date, so pin it (§4.2).
- The prefill `plan` keywords used in `attention/fi.py` (`qo_indptr`, `paged_kv_indptr`, `paged_kv_indices`, `paged_kv_last_page_len`, `num_qo_heads`, `num_kv_heads`, `head_dim_qk`, `page_size`, `pos_encoding_mode`, `seq_lens`, `q_data_type`, `kv_data_type`, `non_blocking`, `causal`) exist in v0.5.3 and v0.7.2. The constructor's `backend` argument, private `_int_workspace_buffer` and `_backend`, and `CUDAGraphBatchDecodeWithPagedKVCacheWrapper` exist in both.
- In v0.7.2 `BatchDecodeWithPagedKVCacheWrapper.plan` takes `**kwargs` and forwards them to an internal planner that still accepts the deprecated `data_type` that `fi.py` passes. A signature check cannot detect a removed keyword; only a call can.
- Both versions document `causal=True` as `tril(..., diagonal = kv_len − qo_len)`: aligned to the end of each request's KV, as a 1-token decode query needs.
- Mixed decode rows use the prefill wrapper; pure decode rows use the decode wrapper (tensor cores when GQA ≥ 4). Expect bf16-level differences, hence tolerance-based tests.

### 9.2 Kernel-level tests (`tests/gpu/test_fi_kernel.py`, gate G5, Week 3)

| ID | What | Pass |
| --- | --- | --- |
| K0 | `flashinfer.__version__` equals the lock; the private attributes exist; both wrappers' `plan()` are **called** with the exact production keyword sets on a 2-request batch | All true; calls succeed |
| K1 | Mixed ragged batch vs fp32 reference and vs separate batches (code below); after C7 the test takes its `plan` keywords from `FlashInferBackend._plan_kwargs` | 5 seeds × two shapes (32/8 heads; 16/8 heads): max error ≤ 2e-2 and mixed error ≤ 1.5 × separate error + 1e-3 |
| K2 | Decode rows of the mixed run vs `BatchDecodeWithPagedKVCacheWrapper` built as `fi.py` builds it | Max difference ≤ 2e-2; recorded in `results/attn_check.json` |
| K3 | Same mixed batch planned and run twice | Bitwise identical |

```python
import math, pytest, torch, flashinfer

H_Q, H_KV, D = 32, 8, 128                     # Qwen3-4B shape (GQA 4); repeat with 16/8
DEV, DT = "cuda", torch.bfloat16

def reference(q, k, v, qo_len, kv_len):        # fp32, causal aligned to the KV end
    q, k, v = q.float(), k.float(), v.float()
    k = k.repeat_interleave(H_Q // H_KV, 1); v = v.repeat_interleave(H_Q // H_KV, 1)
    s = torch.einsum("qhd,khd->hqk", q, k) / math.sqrt(D)
    i = torch.arange(qo_len, device=DEV)[:, None]; j = torch.arange(kv_len, device=DEV)[None, :]
    s = s.masked_fill(j > i + (kv_len - qo_len), float("-inf"))
    return torch.einsum("hqk,khd->qhd", s.softmax(-1), v)

def run_prefill(reqs, k_cache, v_cache, pages):
    ws = torch.empty(128 << 20, dtype=torch.uint8, device=DEV)
    w = flashinfer.BatchPrefillWithPagedKVCacheWrapper(ws, kv_layout="NHD", backend="fa2")
    qo = torch.tensor([0] + [r["q"].shape[0] for r in reqs]).cumsum(0).int()
    kv = torch.tensor([0] + [r["kv_len"] for r in reqs]).cumsum(0).int()
    idx = torch.cat([pages[r["off"]: r["off"] + r["kv_len"]] for r in reqs])
    w.plan(qo_indptr=qo, paged_kv_indptr=kv, paged_kv_indices=idx,
           paged_kv_last_page_len=torch.ones(len(reqs), dtype=torch.int32),
           num_qo_heads=H_Q, num_kv_heads=H_KV, head_dim_qk=D, page_size=1,
           causal=True, q_data_type=DT, kv_data_type=DT,
           seq_lens=torch.tensor([r["kv_len"] for r in reqs], dtype=torch.int32))
    out = w.run(torch.cat([r["q"] for r in reqs]),
                paged_kv_cache=(k_cache.view(-1, 1, H_KV, D), v_cache.view(-1, 1, H_KV, D)))
    return out.split([r["q"].shape[0] for r in reqs])

@pytest.mark.gpu
@pytest.mark.parametrize("seed", range(5))
def test_mixed_matches_reference_and_separate(seed):
    g = torch.Generator(device=DEV).manual_seed(seed)
    shapes = [(1, 37), (1, 1500), (1, 4096), (1, 2), (1, 900), (1, 3000),   # decode rows
              (512, 512), (300, 2348), (1, 1), (7, 107)]                     # prefill rows
    n = sum(kv for _, kv in shapes)
    pages = torch.randperm(n + 64, generator=g, device=DEV)[:n].int()        # scattered pages
    k_cache = torch.randn(n + 64, H_KV, D, device=DEV, dtype=DT, generator=g)
    v_cache = torch.randn_like(k_cache)
    reqs, off = [], 0
    for qlen, kvlen in shapes:
        reqs.append({"q": torch.randn(qlen, H_Q, D, device=DEV, dtype=DT, generator=g),
                     "kv_len": kvlen, "off": off}); off += kvlen
    mixed = run_prefill(reqs, k_cache, v_cache, pages)
    sep = (run_prefill(reqs[:6], k_cache, v_cache, pages)
           + run_prefill(reqs[6:], k_cache, v_cache, pages))
    for r, o_mix, o_sep in zip(reqs, mixed, sep):
        p = pages[r["off"]: r["off"] + r["kv_len"]].long()
        ref = reference(r["q"], k_cache[p], v_cache[p], r["q"].shape[0], r["kv_len"])
        e_mix = (o_mix.float() - ref).abs().max().item()
        e_sep = (o_sep.float() - ref).abs().max().item()
        assert e_mix <= 2e-2, (r["kv_len"], e_mix)
        assert e_mix <= 1.5 * e_sep + 1e-3
```

### 9.3 Model-level tests (`tests/gpu/test_model_mixed.py`, Weeks 6–8, need C7)

| ID | What | Pass |
| --- | --- | --- |
| M1 Argmax path | Offline `LLM`, 64 prompts, greedy, `prefill_first` vs `mixed` at B = 512. Hook in `engine/sample.py` records top-1 id and top-1/top-2 margin per (uid, position). A/A reference: `prefill_first` vs `prefill_first` with a different `max_running_req` | Step log shows each compared request in ≥ 1 mixed step (else vacuous). At every cross-policy first divergence, baseline margin ≤ 99th percentile of A/A divergence margins. Report identical-sequence fractions |
| M2 Teacher-forced prefill rows | 16 sequences (prompt + 256 baseline tokens) sent as prompts with `max_tokens=1` while 32 other requests decode; `MINISGL_DEBUG_ALL_LOGITS` returns log-probs for every prompt position | Per-position \|Δ log-prob\| between policies ≤ maximum A/A \|Δ\| from two `prefill_first` runs with different B |
| M3 External sanity | 8 short prompts vs Hugging Face `transformers` greedy (bf16) | Any divergence at a margin below the M1 A/A threshold |

Kernel tests prove the attention op for a batch shape; model tests prove the engine's inputs and outputs around it (positions, `cu_seqlens_q`, LM-head gather, sampler, phase bookkeeping). Neither substitutes for the other. No correctness claim rests on matching output text.

## 10. Workloads

| ID | Shape | Arrivals | Supports | Does not support |
| --- | --- | --- | --- | --- |
| W0 | 256 requests at t = 0, input 100–1,024, output 100–1,024, `ignore_eos`, greedy | Offline | Maximum throughput (output tokens/s); graph-cost check (§12) | Latency |
| W1 | Input log-uniform 256–8,192, output 128–512 | Open-loop Poisson, fixed schedule file | Latency at matched arrival rate; sustainable throughput (§11.5) | — |
| W2 | N_bg closed-loop background streams + long prompts on a fixed timetable | Hybrid | Tail latency under interference (confirmatory) | Any throughput claim |
| W2-noLong | W2 without the long prompts | Closed loop | Gate control | — |
| W3 | First 1,000 requests of the Qwen trace (`benchmark/online/bench_qwen.py`), time-scaled | Open-loop trace | Exploratory latency (Optional tier) | Confirmatory claims |

### 10.1 Deriving W2 from measured capacity (Week 3)

Starting values: N_bg = 32, L_s = 512, O_s = 256, L_l = 16,384, O_l = 64, Δ = 4 s, long prompts at t = 30 s + Δ·i, 150 s runs, analysis window 30–150 s, B = 4,096, `max_running_req` = 64, `--cuda-graph-max-bs 64`, N_kv fixed with `--num-pages` (§4.5).

1. Fix N_kv with `--num-pages` for the model in use (§4.5 sizing rule) and confirm it in the startup log. The same N_kv is used by every arm and every run.
2. Pilot each arm for 60 s (`--purpose pilot`). From the step log, take the longest long-prompt residence time R_long (admission to finish) and compute m_max = ⌈R_long / Δ⌉ + 1, using the larger of the two arms.
3. Require (page size 1, so the `(page_size − 1) × n` term of `inflight_tokens` is 0 [V]):

```latex
N_{bg}(L_s + O_s) + m_{max}(L_l + O_l) \le 0.9\,N_{kv}, \quad N_{bg} + m_{max} \le \text{max\_running\_req}, \quad \text{max\_running\_req} + P_{min} \le B
```

4. If violated, reduce O_s (not below 128), then N_bg (not below 16), then increase Δ. `scripts/derive_w2.py` writes `configs/w2.yaml` (N_kv, R_long, m_max, chosen values) and fails if any inequality is violated. Both arms use the same file.

Orientation: the starting values reserve 32 × 768 + 2 × 16,448 = 57,472 tokens, which fits 0.9 × 65,536 = 58,982 only if m_max ≤ 2; the pilot decides.

### 10.2 Run validity rule (pre-registered, all arms)

A run is valid only if, in the analysis window: 0 KV-reservation rejections and 0 table-slot rejections (§7.4 counters); every long prompt admitted within one scheduler step of arrival; decode batch size within [N_bg − 2, N_bg + m_max] in ≥ 95% of steps; at least one background gap recorded; **no other user's compute process on the selected GPU** in the pre-run snapshot, the post-run snapshot or any 5 s sample; host 1-minute load average below half the CPU count throughout. An invalid run is rerun once (after waiting for the GPU to be free; never by disturbing another job). Two invalid runs in one comparison mean W2 is re-derived and the comparison restarts.

## 11. Statistical protocol

### 11.1 Primary metric

Excess stall time per stream-minute, per run, over the background streams only (long-prompt requests excluded):

```latex
S = \frac{1}{N_{bg} \cdot T_{min}} \sum_{\text{background streams}} \; \sum_{\text{gaps } g} \max(0,\; g - \tau)
```

in ms per stream-minute. S = 0 is a valid value; no decision uses a ratio. τ = 3 × the median GPU time of a pure-decode step (`gpu_end − gpu_start`), measured in a W2-noLong pilot on `baseline-fixed` and written in milliseconds into `prereg/gate.yaml` before the gate study. The gap source (client or server-egress, per T3) is fixed in the same file.

**Secondary metrics (reported, never gating):** exceedance rate (fraction of gaps > τ) as an absolute difference in percentage points; median per-request max gap; p99 ITL per run; TTFT p90 per run; output tokens/s.

### 11.2 ABBA design (every comparison)

- **Arms:** X = reference (`baseline-fixed`, or `mixed --schedule-policy prefill_first`), Y = treatment; same binary where possible.
- **Blocks:** b = 1…8. Odd blocks run X, Y, Y, X; even blocks Y, X, X, Y. The four runs of a block share schedule seed s_b (listed in `prereg/*.yaml`).
- **Each run:** fresh server started with `scripts/serve.sh` and stopped with `scripts/stop_server.sh`, same GPU index and same `--num-pages` for all runs of a comparison, warm-up batch excluded, metrics on the analysis window only.
- **Paired difference:** d_b = mean(Y runs in b) − mean(X runs in b). A linear drift within a block cancels exactly.
- **Inference:** mean of d_b with a 95% t-interval (7 df) and a bootstrap interval over blocks; exact sign-flip permutation p over the 8 blocks (smallest two-sided p = 2/256 ≈ 0.008). Gaps or requests are never resampled across runs.
- **A/A:** 6 blocks with X = Y = `baseline-fixed` on W2. Its interval must contain 0, else stop and fix the setup. The noise bound used below is max |d_b| over the A/A blocks.

### 11.3 Confirmatory test (one)

Y = `mixed`, X = `prefill_first` (same binary), workload W2, metric S. Claim an improvement only if the upper bound of the 95% interval of mean d is below −max(Δ_practical, A/A noise bound) and the permutation p < 0.01. Δ_practical = 50 ms per stream-minute, fixed in `prereg/confirmatory.yaml` before the first confirmatory run. Every other comparison is exploratory and labelled so.

### 11.4 Rules

Pre-registration files are committed, and their hashes recorded, before the data they judge. No optional stopping. Report absolute values next to every relative change, A/A noise, per-block differences as a dot plot, and the hardware/model/workload string ("RTX 4090, Qwen3-4B, W2").

### 11.5 Throughput claims

Only from W0 (offline, ABBA, 8 blocks) and W1 sustainable rate: for each rate in {75, 90, 100, 110}% of the baseline's saturation rate, run ABBA with 8 blocks; the sustainable rate is the highest rate at which an arm meets the SLOs (TTFT ≤ 2 s and per-request max gap ≤ 200 ms for 99% of requests) in ≥ 7 of 8 blocks. Below saturation both arms complete the same offered load, so equal tokens/s is a sanity check, not a result.

## 12. CUDA-graph isolation

**Facts:** F9. Mixed steps run eager and move decode rows from the decode wrapper to the prefill wrapper; pure-decode steps still replay graphs.

| | Graphs on | Graphs off (`--cuda-graph-max-bs 0`) |
| --- | --- | --- |
| `prefill_first` | P0-on | P0-off |
| `mixed` | P2-on | P2-off |

Contrasts (each an 8-block ABBA comparison on W2): policy effect without graphs = P2-off − P0-off; deployed effect = P2-on − P0-on. Graph value at baseline and the interaction are computed from the same runs.

**Step accounting.** Per run: share of steps and decode tokens in mixed, graph-decode and eager-prefill steps; GPU time per class. Fit t_mixed ≈ a + b·P + c·D and compare c with the per-token cost of a graph decode step at the same batch size; the fitted model must predict the measured throughput change. Offline microbenchmarks (Week 4): decode step time for batch sizes {1, 8, 32, 64} with and without graphs; marginal cost of D decode rows added to a 2,048-token prefill chunk.

**Acceptance thresholds (fixed in `prereg/confirmatory.yaml`):** W0 throughput loss: the 95% interval lies entirely below 3%; W1 sustainable rate not lower than the baseline's; short-prompt-only W1: no significant change. If exceeded, mitigate in order: mix only when pending prefill tokens exceed a floor, then raise P_min; report the trade-off either way.

## 13. Weeks 1–5 and the decision gate

| Week | Deliverables | Acceptance (measurable) |
| --- | --- | --- |
| 1 | Server read-only checks; clone under `/home/xwc/my_projects/`; personal environment `/home/xwc/envs/minisgl`; `requirements.lock`, `env.json`; `scripts/check_env.sh`, `env.sh`, `pick_gpu.sh`, `serve.sh`, `stop_server.sh`, `smoke.sh`; `--num-pages` sizing for both models; golden outputs on `upstream`; R1 and R4 run; minimal `instr` hook for R5 and R5 run; code-reading notes (`notes/architecture.md`) | Smoke checks S1–S5 pass, including a clean stop with no leftover processes; golden outputs deterministic; R1, R4, R5 statuses recorded with raw logs |
| 2 | `instr` complete (C1); R2 and R3 run; fixes for confirmed H1–H5 per the inclusion rule, each with its failing reproducer committed first; PR #172 and #165 cherry-picked; ID1 | ID1 passes; T1 passes on a short run with the W2 starting values; reproducers of confirmed hypotheses pass on `baseline-fixed` |
| 3 | C4 checker with seeded bugs; K0–K3 on the target GPU (G5); ID2, ID3; 30-minute stress (G6d); `results/gates/G6.json`; W2 derivation (`configs/w2.yaml`); τ pilot; A/A (6 blocks); T2–T4; `prereg/gate.yaml` committed | G6 passes (or Stop-and-Fix starts); G5 recorded; A/A interval contains 0 |
| 4 | Gate study: W2 vs W2-noLong on `baseline-fixed`, 8 ABBA blocks (32 runs); step-cost fit; microbenchmarks; one Nsight Systems capture if `nsys` is already available to `xwc` (nothing is installed system-wide); diagnosis memo; `docs/gate-week4.md` | Every gate criterion evaluated with value, interval, threshold and raw-file path; decision committed |
| 5 | Buffer: Stop-and-Fix if G6 failed (gate study then runs in Week 5); otherwise start C5–C6 | — |

### 13.1 Gate criteria (thresholds committed in `prereg/gate.yaml` before the gate study)

| ID | Question | Pass rule |
| --- | --- | --- |
| G1 | Is there a stall? | With d_b = S(W2) − S(W2-noLong): lower bound of the 95% t-interval of mean d > max(Δ_practical = 50 ms per stream-minute, A/A noise bound), and permutation p < 0.01. If every run in both arms has S = 0, G1 fails |
| G2 | Is it the scheduler? | ≥ 80% of the excess stall time S in W2 runs comes from gaps whose scheduler term (Δ`gpu_end`) is ≥ 50% of the gap and during which only prefill-only steps excluding that request completed |
| G3 | Can an improvement be measured? | A/A interval contains 0, and the A/A noise bound < 25% of the observed mean d |
| G4 | Is mixing worth building? | From the step-cost fit, predicted median per-request max gap under mixing ≤ 70% of the observed baseline value at W2 settings |
| G5 | Is the kernel path correct? | K0–K3 pass on the target GPU with the pinned FlashInfer version |
| G6 | Is the baseline sound? | G6a: R1–R5 statuses recorded on `upstream`. G6b: R1–R5 pass on `baseline-fixed`. G6c: ID1–ID3 pass. G6d: 30-minute stress (radix cache, aborts, EOS and length termination, `--max-running-requests 4`, integrity checks every 1,000 steps and at idle) has zero failures. G6e: existing tests plus new CPU unit tests green |

**Order and enforcement.** G6 is evaluated first. `scripts/gates.py` writes `results/gates/G6.json` with the commit hash; `run_matrix.py` refuses any performance run unless that file exists, all checks passed, and the hash equals the checkout under test. Exceptions: runs tagged `--purpose reproducer` (on `upstream` / `instr`) and `--purpose pilot` (W2 derivation, τ), whose results are never reported.

### 13.2 Decisions

| Outcome | Condition | Action |
| --- | --- | --- |
| **Stop-and-Fix (baseline)** | G6 fails | No performance runs. Fix on a branch off `baseline-fixed`, failing test first; rerun all of G6 and the A/A calibration (τ and noise may move). Time box 5 working days; then run every arm with `MINISGL_DISABLE_OVERLAP_SCHEDULING=1` (F13), rerun G6, document the limitation and continue. If G6 still fails with overlap off, switch to Fallback F (§15) |
| **Stop-and-Fix (setup)** | G3 fails | Reduce noise without privileges (wait for an idle GPU and a quiet host, stop only your own background processes, longer windows), rerun A/A and the gate study before deciding |
| **Go** | G1–G6 pass | §14 with `mixed` as the primary treatment |
| **Go-reduced** | G1–G3 and G6 pass; G4 or G5 fails | §14 with `streak_cap` as the primary treatment; `mixed` becomes an exploratory arm with its limitation documented |
| **No-Go** | G1 or G2 fails | Fallback F (§15) |

`docs/gate-week4.md` records: date, commit hash, prereg hash, one row per criterion (value, interval, threshold, pass/fail, raw-file path), the decision and what would change it. Write the reasoning yourself.

## 14. Weeks 6–13 after Go or Go-reduced

| Week | Work | Acceptance |
| --- | --- | --- |
| 6–7 | C5–C8 on `mixed`; CPU unit tests (V1, V2, V8); integration with overlap on and off; R1–R3 and stress rerun on `mixed` | V1–V8 pass; ID2 on `mixed` |
| 8 | M1–M3; `pr155` branch built and passing R1–R5 and stress (record any failure as a finding); `prereg/confirmatory.yaml` committed | M1–M3 pass; pre-registration hash recorded |
| 9 | Confirmatory W2 comparison (§11.3); exploratory W2 comparisons: `pr155` vs `baseline-fixed`, `streak_cap` vs `prefill_first` (8 blocks each) | All runs valid under §10.2 |
| 10 | Graph factorial (§12); W0 and W1 throughput (§11.5) | Thresholds evaluated |
| 11 | Budget sweep B ∈ {1,024, 2,048, 4,096, 8,192} × {`prefill_first`, `mixed`}, 4 blocks each (exploratory frontier: TTFT p90 vs S); failure cases (below) | Frontier figure regenerates from raw data |
| 12 | README, `docs/design.md`, `docs/bugs.md`, 4–6 page report, CI for CPU tests, upstream PRs for confirmed fixes and tests | A second person reproduces one figure inside its interval |
| 13 | Slack; interview preparation | — |

**Failure cases (exploratory, 2 blocks each):** short prompts only (< 512 tokens); W0 offline; concurrency 1; saturated decode with short inputs; `--max-running-requests 200` with B = 1,024; one 32k prompt among short ones; Qwen3-0.6B CPU-bound steps (`sched_cpu_ms` vs `gpu_ms`); KV pressure via `--num-pages`. Each gets a post-mortem: symptom, mechanism from the trace, fixable or not.

## 15. Fallback F (after No-Go, or G6 unrecoverable with overlap)

Optimistic admission with retraction is out of scope: a safe version needs retirement of in-flight victims, recomputation, victim policy and thrashing control. For scale, PR #97 (`a115a17`) changes 13 files (+389/−98) and still notes "todo: skip retracted requests for overlap schedule" [V].

| Part | Content | Status at the gate |
| --- | --- | --- |
| F1 | Reproducers and fixes for confirmed H1–H5 | Done in Weeks 1–3 |
| F2 | Integrity checker (§6), offered upstream with tests for #165 | Done in Week 3 |
| F3 | Tracer and fixed client (§7) | Done in Week 2 |
| F4 | `streak_cap` policy (C8), evaluated with §11 on W2, frontier over K | New, ~20 lines + tests |
| F5 | Admission headroom study: KV reserved vs used on an EOS-terminated workload under `--num-pages` pressure | Logging and analysis only |

## 16. Upstream overlap and contribution claims

| PR [V] | Head, date | Content | Tests |
| --- | --- | --- | --- |
| #155 | `2862051`, 2026-10-07 | Mixed batching with validated `req_phases`; host-length finish rule; skips `finished_reqs`; enabled by default for `FlashInferBackend` with TP = 1; runtime `ValueError` when decode tokens exceed B | None |
| #165 | `c5d10f1`, 2026-10-13 | `RadixPrefixCache.check_integrity` for Issue #149 | None |
| #172 | `82bb455`, 2026-10-19 | Detokenizer handles several messages for one uid in one batch | Yes |

None is merged (`main` is `9a91cfa`) [V]. Re-fetch the three heads at the start of each week; if one merges or changes, rebase `baseline-fixed` and rerun G6 before further performance runs.

| Planned work | Covered upstream | What remains yours |
| --- | --- | --- |
| H1 fix | #155's host-length rule | Reproducer R1; standalone tested fix (credit #155) |
| H4, H5 | #155's `finished_reqs` skip covers the next step only | R4, R5; abort coverage via C3a |
| H2 | Nothing | R2 and C3b |
| H3 | Nothing | R3 and C3a |
| KV integrity | #165: I1, I2, within-tree duplicates | Tests for #165; I3–I6; stress-time invocation |
| Streaming text | #172 | Event accounting and `undelivered` counting |
| Tracing and client | Nothing | All of §7 |
| Mixed policy | #155 | Startup validation, P_min, retirement-safe lifecycle, K/M tests, evaluation |
| Evaluation | Nothing | All of §11–§14 |

The README credits #155, #165 and #172, and every claimed contribution maps to the right-hand column.

## 17. Working with Claude Code under the server policy

**Where it runs.** Claude Code runs only on your local machine, in a local clone of `jackwubupt-ops/mini-sglang_dev`. It is never installed or launched on `ps`, it does not open SSH sessions to `ps`, and its conversation files stay local. Like Codex, it sends code context to its service, so the repository must never contain secrets.

**Division of work:**

| Step | Where | Who |
| --- | --- | --- |
| Read code, write scripts, tests and fixes; review the diff; commit | Local clone | Claude Code, reviewed by you |
| Check for secrets before every push (`git diff --cached`, `.gitignore` covers `.env`, keys, tokens, agent session folders, `runs/`, caches, weights) | Local | You |
| Push the branch to the fork | Local | You |
| `git pull --ff-only` after checking a clean tree; run scripts in the Remote-SSH terminal | `ps` | You |
| Copy run data down with the local `rsync --ignore-existing` command (§4.5) | Local | You |
| Analyse raw data, update `results/manifest.csv`, summaries and `docs/PROGRESS.md` | Local | Claude Code, reviewed by you |

**Files that carry state between sessions:** `CLAUDE.md` (rules), `docs/PLAN.md` (this plan), `docs/PROGRESS.md` (dated log), `docs/DECISIONS.md` (your reasoning), `docs/bugs.md`, `prereg/*.yaml`, `results/` (small artefacts and `manifest.csv` only; raw run data stays in `runs/`, outside Git).

**`CLAUDE.md` ground rules (paste and adapt):**

```markdown
# Mini-SGLang mixed-batch project
Plan: docs/PLAN.md is authoritative. Read the section named in each session prompt.
Pinned baseline: branch `upstream` at 9a91cfa (tag baseline-v1). Never modify it.
Execution: you run locally only. Never run commands on the server `ps`, never open SSH,
never add instructions that install or launch an AI agent on the server.
Server scripts you write must follow the Shared Server Usage Policy (PLAN 4.2):
- start with `set -euo pipefail`, run scripts/check_env.sh, then source scripts/env.sh;
- no sudo, no system-wide installs, no changes to Conda base, drivers or CUDA;
- never `rm -rf`, `chmod -R`, `pkill` or `killall`; stop only our own process group via scripts/stop_server.sh;
- every GPU job goes through scripts/serve.sh or scripts/run_offline.sh, which check GPU usage, ports and disk;
- always pass --num-pages and --cuda-graph-max-bs (num_page_override / cuda_graph_max_bs for the offline LLM);
- every destructive step needs an explicit confirmation prompt; prefer read-only or non-destructive alternatives.
Every Linux command you give me must include a guardrail (user/path/branch/ownership check or confirmation).
Data rules:
- Never report a performance number not produced by our scripts; every number points to a run in results/manifest.csv.
- A suspected defect (H1-H5) is "confirmed" only after its reproducer fails on `upstream`; commit the failing reproducer before any fix.
- Performance runs only through scripts/run_matrix.py, which refuses without a passing results/gates/G6.json for the current commit.
- prereg/*.yaml thresholds are committed before the data they judge and never edited afterwards.
- Never commit .env files, keys, tokens, agent session files, raw logs, caches or model weights.
Git: small commits, descriptive messages, never force-push; default behaviour stays prefill_first unless --schedule-policy says otherwise.
If the same step fails twice, stop and report what was tried. End every session by appending to docs/PROGRESS.md.
```

**Week 1, local Claude Code session prompt (paste as one message):**

```text
Session W1-local: tooling for environment setup and baseline validation. Read docs/PLAN.md sections 0, 4, 5 and 17.
You run locally only; do not run anything on the server and do not modify python/minisgl/.
1. Add sgl-project/mini-sglang as a remote, fetch 9a91cfa, create branches `upstream` and `tooling` at it, tag baseline-v1.
2. On `tooling`, write scripts/check_env.sh, scripts/env.sh, scripts/pick_gpu.sh, scripts/serve.sh, scripts/stop_server.sh,
   scripts/run_offline.sh, scripts/smoke.sh and scripts/capture_env.py exactly to PLAN 4.4/4.5, including every listed guardrail.
   Add a .gitignore for .env, keys, tokens, agent session folders, runs/, caches and weights.
3. Write the golden-output script and reproducer scripts R1 and R4 (PLAN 4.5, 5.3); each writes only under $RUNS/<run_id>/.
4. On a new `instr` branch from `tooling`, add only the decode_map size logger for R5 (behind MINISGL_DEBUG_DETOK_MAP) and the R5 script.
5. Run shellcheck and a dry-run mode for each shell script locally; show me the diff and the exact server commands I should run, in order,
   each with its guardrail and expected output.
6. Append a dated entry to docs/PROGRESS.md. Do not push; I will review and push.
```

**Week 1, server checklist (you, in the Remote-SSH terminal):** run §4.5 steps 1–5 in order; then the golden-output script twice and R4 through `scripts/run_offline.sh`, and R1 and R5 through `scripts/serve.sh` / `scripts/stop_server.sh`, each on a GPU that `scripts/pick_gpu.sh` shows as free. Before each GPU job, tick the policy checklist: no scheduler or scheduler followed; GPU checked; isolated repository and environment; log, cache and output paths under `/home/xwc`; Git SHA, environment and parameters recorded; you know the run's PGID.

Later sessions follow the same pattern: a local prompt naming the PLAN section, branch and acceptance criteria, and a server checklist that you execute.

## 18. Budget and scope

### 18.1 GPU and time budget [A]

Per-run assumption: ~4 minutes per online run (1–1.5 min start-up and graph capture for Qwen3-4B, 150 s workload); ~2 minutes per offline run. Replace with Week 1–2 measurements.

| Block | Runs | GPU-hours |
| --- | --- | --- |
| Weeks 1–5: setup, reproducers, development, stress | — | 30–40 |
| A/A (6 blocks), gate study (8 blocks), pilots | ~66 | ~4.5 |
| Kernel tests, microbenchmarks, Nsight | — | ~3 |
| Weeks 6–8: integration and model tests | — | 35–45 |
| Confirmatory + two exploratory W2 comparisons (8 blocks each) | 96 | ~6.5 |
| Graph factorial (2 contrasts × 8 blocks) | 64 | ~4.5 |
| W0 (8 blocks) and W1 sustainable rate (4 rates × 8 blocks) | 160 | ~9.5 |
| Budget sweep and failure cases | 128 | ~9 |
| Reruns and debugging of the evaluation | — | ~20 |
| **Total** | | **≈ 120–145** (optional vLLM comparison and W3: +10) |

GPU time runs on the shared server, so there is no rental cost, but waiting for an idle GPU adds wall-clock time; plan confirmatory blocks for quiet periods. Your time: about 200–250 hours over 13 weeks at 15–20 hours per week.

### 18.2 Scope tiers

| Tier | Items |
| --- | --- |
| MVP, gate-independent | Environment and smoke test; R1–R5; fixes for confirmed hypotheses; PR #172 and #165 integration; C4 checker; C1 tracing and client; K0–K3; A/A; gate study and decision record |
| MVP after Go / Go-reduced | C5–C8; V1–V8 and M1–M3; confirmatory W2 test; `pr155` and `streak_cap` arms; graph factorial; W0/W1 throughput; budget sweep; failure cases; README, design doc, report |
| Optional | W3 exploratory; C9 per-request cap; vLLM `--max-num-batched-tokens` comparison on the same W2 file; Hopper `fa,fi` routing if an H100 is available on `ps`; upstream PRs beyond the confirmed fixes |
| Out of scope | Optimistic admission with retraction; splitting decode across steps; TP > 1, TRT-LLM, MoE; CUDA graphs for mixed batches; new attention kernels; prefix-cache-heavy performance claims |

**Rules across all tiers:** no performance number without a raw file behind it; no correctness claim from matching text; every threshold committed before the data it judges; no defect called confirmed without a failing reproducer on `upstream`.

## 19. Sources

- Mini-SGLang Shared Server Usage Policy (administrator; update of 2026-10-20), binding for all server work
- sgl-project/mini-sglang at `9a91cfa` (main on 2026-10-20): `python/minisgl/{core.py, env.py, scheduler/*, engine/*, attention/{__init__,base,fi,trtllm}.py, layers/embedding.py, kvcache/*, tokenizer/{server,detokenize}.py, server/{args,api_server,launch}.py, benchmark/client.py, llm/llm.py}`, `Dockerfile`, `pyproject.toml`, `tests/`, `benchmark/` — https://github.com/sgl-project/mini-sglang
- PR #155 head `2862051`, PR #165 head `c5d10f1`, PR #172 head `82bb455`, PR #97 head `a115a17`, each fetched from `refs/pull/*/head` and diffed against `9a91cfa`; Issue #149 text not read (API access refused), the no-op confirmed in source
- flashinfer-ai/flashinfer tags v0.5.3 and v0.7.2: `flashinfer/prefill.py`, `flashinfer/decode.py` — https://github.com/flashinfer-ai/flashinfer
- Qwen3-4B attention shape (36 layers, 32 query heads, 8 KV heads, head_dim 128) from a Qwen3-4B-based config (codefuse-ai/F2LLM-4B); confirm against the official Qwen/Qwen3-4B config
- Sarathi-Serve (arXiv:2403.02310) for chunked prefill with decode piggybacking; vLLM V1 `vllm/v1/core/sched/scheduler.py` for the decode-first token-budget policy

# Week 1 server runbook (you, in the VS Code Remote-SSH terminal on `ps`)

Follows PLAN 4.5 and 17. Claude Code wrote these scripts locally; it never runs anything on `ps`.
Replace `<GPU>` with an index that `scripts/pick_gpu.sh` lists as a candidate, and `<P>` with a
port where both P and P+1 are free (serve.sh checks both). Use the same `<GPU>` for every run.
Before each GPU job: no scheduler (or follow it), GPU checked, own repo/env, paths under
`/home/xwc`, SHA recorded (the scripts write `run_meta.txt`), and you know the run's PGID
(printed by the script; stop only with `scripts/stop_server.sh RUN_ID`).

Every script supports `DRY_RUN=1` (checks and prints, launches nothing). Try it first, e.g.
`DRY_RUN=1 scripts/serve.sh test-x <GPU> <P> -- --model-path Qwen/Qwen3-0.6B --num-pages 65536 --cuda-graph-max-bs 64`.

## 0. Local, before the server (you)

Review, check for secrets, then push the three branches and the tag to the fork:

```bash
git -C ~/mini-sglang diff 9a91cfa tooling --stat && git -C ~/mini-sglang diff tooling instr --stat
```
```bash
git -C ~/mini-sglang push fork upstream tooling instr baseline-v1
```

Expected: `upstream` is `9a91cfa`; `tooling` changes nothing under `python/`; `instr` adds only
the R5 logger under `python/`.

## 1. Read-only checks

```bash
whoami; hostname; pwd; echo "$HOME"; ls -ld "$HOME"
nvidia-smi
command -v sinfo; command -v squeue
command -v nvcc && nvcc --version
df -h /home/xwc
```

Expected: `xwc`, `ps`, `/home/xwc` owned by `xwc`; no output from the `sinfo`/`squeue` line
(if there is any, stop: the scripts refuse to launch and must be adapted to the scheduler).
Note the driver's CUDA version from `nvidia-smi` and the `nvcc` version.

## 2-4. Clone, environment, install

Run PLAN 4.5 steps 2, 3 and 4 exactly as written there. Expected at the end of step 4:
`ok: xwc, /home/xwc/my_projects/mini-sglang_dev, /home/xwc/envs/minisgl`, a successful
`pip install -e ".[dev]" torch-c-dlpack-ext`, and a new `results/requirements.lock`
(copy it down in step 11 and commit it locally; never commit on the server).

Then make every script executable check pass and record the environment (no GPU used):

```bash
cd /home/xwc/my_projects/mini-sglang_dev && scripts/check_env.sh && source scripts/env.sh && \
  scripts/pick_gpu.sh && \
  python scripts/capture_env.py --gpu <GPU> --out "$RUNS/env-$(date +%Y%m%d-%H%M)/env.json"
```

Expected: the GPU table, a candidate list, `wrote .../env.json` (refuses if the file exists).

## 5. Smoke test S1-S5

```bash
scripts/check_env.sh && source scripts/env.sh && RID=smoke-$(date +%Y%m%d-%H%M) && \
scripts/serve.sh "$RID" <GPU> <P> -- \
  --model-path Qwen/Qwen3-0.6B --attention-backend fi --cache-type naive --page-size 1 \
  --max-prefill-length 4096 --max-running-requests 64 --cuda-graph-max-bs 64 --num-pages 65536 && \
scripts/smoke.sh "$RID" <GPU>
```

Expected: `ready: run smoke-..., PGID ..., port <P>`, the `Allocating 65536 tokens for KV cache`
line, then `S1 PASS`, `S2 PASS`, `S3 PASS`; smoke.sh then lists the process group and asks
`Send SIGTERM to process group ...? [y/N]` (answer `y` after checking every line is `xwc`),
`S4 PASS`, then the tests run on the same GPU and `S5 PASS`, `smoke: all PASS`.
If serve.sh times out, read `$RUNS/$RID/serve.log`, then `scripts/stop_server.sh "$RID"`.

Also run the tooling tests on the server (CPU only, includes the torch-dependent logger test
once you are on `instr` in step 10):

```bash
scripts/check_env.sh && python -m pytest tests/tooling -q -o addopts=""
```

## 6. Golden outputs, twice (upstream engine code via `tooling`)

```bash
scripts/check_env.sh && source scripts/env.sh && \
git diff --quiet 9a91cfa HEAD -- python/ pyproject.toml && echo "python/ is 9a91cfa" && \
for k in a b; do
  scripts/run_offline.sh golden-$k-$(date +%Y%m%d-%H%M%S) <GPU> -- \
    python scripts/golden_outputs.py --num-pages 65536 --cuda-graph-max-bs 64 || break
done
```
```bash
python scripts/compare_outputs.py "$RUNS"/golden-a-*/outputs.json "$RUNS"/golden-b-*/outputs.json
```

Expected: `python/ is 9a91cfa`, two `finished: ... exit code 0`, then `IDENTICAL: 64 requests`.
(If you ran golden more than once, name the two directories explicitly instead of the globs.)
`meta.length_mismatch` in each outputs.json lists requests that returned fewer than
`max_tokens` tokens; a non-empty list is itself evidence for H1 and goes in PROGRESS.

## 7. R4 (H4), overlap on then off

```bash
scripts/check_env.sh && source scripts/env.sh && \
scripts/run_offline.sh r4-on-$(date +%Y%m%d-%H%M%S) <GPU> -- \
  python scripts/repro/r4_offline.py --num-pages 65536 --cuda-graph-max-bs 64 && \
MINISGL_DISABLE_OVERLAP_SCHEDULING=1 scripts/run_offline.sh r4-off-$(date +%Y%m%d-%H%M%S) <GPU> -- \
  python scripts/repro/r4_offline.py --num-pages 65536 --cuda-graph-max-bs 64
```

Expected: two `exit code 0`; `run_meta.txt` of the second shows
`MINISGL_DISABLE_OVERLAP_SCHEDULING=1`. Judged locally (step 11).

## 8. R1 (H1), overlap on, then off

```bash
scripts/check_env.sh && source scripts/env.sh && RID=r1-on-$(date +%Y%m%d-%H%M) && \
scripts/serve.sh "$RID" <GPU> <P> -- \
  --model-path Qwen/Qwen3-0.6B --attention-backend fi --cache-type naive --page-size 1 \
  --max-prefill-length 4096 --max-running-requests 64 --cuda-graph-max-bs 64 --num-pages 65536 && \
python scripts/repro/r1_client.py --run-id "$RID"; scripts/stop_server.sh "$RID"
```

Then the same block with `RID=r1-off-...` and `MINISGL_DISABLE_OVERLAP_SCHEDULING=1` in front of
`scripts/serve.sh`. Expected: the client prints the events per request for each `max_tokens`
(`[1]`, `[2]`, `[8]`, `[64]` if H1 does not hold; `[1]`, `[7]`, `[63]` style counts if it does),
then stop_server asks for confirmation.

## 9. (reserved: R2 and R3 are Week 2)

## 10. R5 (H5) on `instr`

```bash
cd /home/xwc/my_projects/mini-sglang_dev && \
if [ -n "$(git status --porcelain)" ]; then echo "STOP: uncommitted changes on the server"; else
  git fetch origin && git switch instr && git pull --ff-only origin instr && \
  scripts/check_env.sh && git diff --stat origin/tooling HEAD -- python/; fi
```
```bash
scripts/check_env.sh && source scripts/env.sh && RID=r5-$(date +%Y%m%d-%H%M) && \
MINISGL_DEBUG_DETOK_MAP=1 scripts/serve.sh "$RID" <GPU> <P> -- \
  --model-path Qwen/Qwen3-0.6B --attention-backend fi --cache-type naive --page-size 1 \
  --max-prefill-length 4096 --max-running-requests 64 --cuda-graph-max-bs 64 --num-pages 65536 && \
python scripts/repro/r5_client.py --run-id "$RID"; \
grep MINISGL_DEBUG_DETOK_MAP "$RUNS/$RID/serve.log" | tail -n 3; scripts/stop_server.sh "$RID"
```

Expected: `all 200 requests returned; idle for 30 s ...`, the last three map-size lines
(about 200 if H5 holds, 0 if not), then the stop confirmation. Switch back to `tooling`
afterwards with the same clean-tree guard.

## 11. Copy down and judge (locally, on your Mac)

```bash
mkdir -p ~/mini-sglang/runs && rsync -av --ignore-existing xwc@ps:/home/xwc/my_projects/runs/ ~/mini-sglang/runs/
```
```bash
mkdir -p ~/mini-sglang/results && rsync -av --ignore-existing xwc@ps:/home/xwc/my_projects/mini-sglang_dev/results/requirements.lock ~/mini-sglang/results/
```
```bash
cd ~/mini-sglang && python3 scripts/repro/r1_judge.py runs/r1-on-*/r1_client.json runs/r1-off-*/r1_client.json
```
```bash
cd ~/mini-sglang && python3 scripts/repro/r4_judge.py runs/r4-on-*/outputs.json runs/r4-off-*/outputs.json
```
```bash
cd ~/mini-sglang && python3 scripts/repro/r5_judge.py runs/r5-*
```

`runs/` is git-ignored. The judges need Python >= 3.9, refuse to overwrite an existing
`results/repro/<ID>.json`, and print `confirmed`, `not reproduced` or `inconclusive`.
Then copy one golden `outputs.json` to `tests/golden/upstream.json` and `results/requirements.lock`
from the server copy, and ask Claude Code to update `results/manifest.csv` and `docs/PROGRESS.md`.

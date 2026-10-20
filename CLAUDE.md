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

Local checks (no GPU): `shellcheck -x scripts/*.sh`; `DRY_RUN=1 scripts/<script>.sh ...` checks and prints
without launching or killing anything; `python -m pytest tests/tooling -o addopts=""` (the repo's pytest
addopts need pytest-cov). The local Mac has no Linux tools (setsid, ss, nvidia-smi), so real launches
are only exercised on the server.

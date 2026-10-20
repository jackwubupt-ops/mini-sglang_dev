# Progress log

## 2026-10-20 — Session W1-local (Claude Code, local only)

Done (nothing run on `ps`, nothing pushed):
- Branches `upstream` and `tooling` at `9a91cfa`, tag `baseline-v1`; `instr` from `tooling`.
  `git diff --quiet 9a91cfa tooling -- python/ pyproject.toml` passes.
- `tooling`: docs/PLAN.md (v1.1), CLAUDE.md, .gitignore for secrets/agent sessions/runs/caches/weights;
  scripts check_env.sh, env.sh, lib_guard.sh, minisgl_group.sh, serve.sh, run_offline.sh,
  stop_server.sh, pick_gpu.sh, smoke.sh, capture_env.py; golden_outputs.py + compare_outputs.py;
  reproducers R1 (r1_client.py, r1_judge.py) and R4 (r4_offline.py, r4_judge.py);
  docs/week1-server.md (ordered server commands with guardrails and expected output).
- `instr`: MINISGL_DEBUG_DETOK_MAP decode_map size logger (off by default) and R5
  (r5_client.py, r5_judge.py).

Verified locally:
- shellcheck -x scripts/*.sh: clean.
- DRY_RUN=1 with stubbed nvidia-smi/ss/sinfo: serve.sh refuses missing --num-pages, --port or
  abbreviated flags, a GPU with another user's process, a missing GPU, busy PORT or PORT+1,
  a bad or existing RUN_ID, an installed scheduler.
- End to end on macOS with stubs (python os.setsid for setsid, fake server): serve.sh starts a
  group and waits for readiness; stop_server.sh lists it, aborts on "n", stops it on "y",
  refuses a group led by another program; run_offline.sh records exit codes 0 and 3.
- tests/tooling: 15 passed, 1 skipped (logger test needs torch; runs on the server).

Not verified: anything on Linux or a GPU (setsid, ss, df, nvidia-smi formats, the engine itself).
No hypothesis has been run; H1-H5 all remain [H].

Judgement calls to review:
- R5 "proportional" is judged as idle map size within 0.5x-1.5x of EOS-terminated requests.
- Golden B = 8192 (default); "longer than B" prompts are B+1..B+4096 tokens; cache naive.
- R1/R4/R5 count a request as EOS-terminated from the token/event count, not a server flag.

Next: you review and push (docs/week1-server.md step 0), then run steps 1-10 on `ps`.

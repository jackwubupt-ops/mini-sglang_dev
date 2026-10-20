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

## 2026-10-20 — Server path correction

The administrator allocated `/home/xwc/vllm` as the project root on `ps`; it replaces PLAN's
`/home/xwc/my_projects` (policy wins over the plan, PLAN 0). Repo `/home/xwc/vllm/mini-sglang_dev`,
`RUNS=/home/xwc/vllm/runs`, caches `/home/xwc/vllm/cache/*`, disk check on `/home/xwc/vllm`.
The Conda env stays at `/home/xwc/envs/minisgl`. check_env.sh now compares physical paths (symlink-safe).
docs/PLAN.md is unchanged; read its `/home/xwc/my_projects` as `/home/xwc/vllm`.
Verified locally: shellcheck clean, dry-run refusals unchanged, tests/tooling 12 passed (tooling branch).

## 2026-10-20 — Windows-mediated deployment (ps has no GitHub access)

Reported by the user: on `ps`, `git clone` from GitHub fails (`gnutls_handshake() failed`) and
`curl -I` gets `200 Connection established` then `unexpected eof`; cause unconfirmed.
Changes (tooling, merged into instr):
- docs/week1-server.md rewritten: Mac push → Windows bare mirror → `git bundle` + sha256 + refs file
  → scp to /home/xwc/vllm/transfer → verified first clone on ps; results return as a tar.gz + sha256
  from /home/xwc/vllm/outbox through Windows to the Mac (extracted with `tar -k`, never overwriting).
  Added a read-only connectivity check for Conda, PyPI and Hugging Face, and conditional fallbacks:
  wheelhouse built on Windows (uv resolve for Linux, pip download) and model copy from Windows.
- scripts/sync_from_bundle.sh: later updates on ps (sha256, refs file, git bundle verify, fsck,
  clean tree, upstream/baseline-v1 at 9a91cfa, fast-forward only, tags never moved).
- smoke.sh reads --model-path from run_meta.txt (so a local model directory works).
Verified locally: shellcheck clean; bundle flow simulated with a bare "fork" and mirror — first
clone, fast-forward update, refusal of a corrupted bundle, a rewritten (non-ff) branch and a path
outside transfer/; tests/tooling 12 passed. Not verified: every PowerShell block (no PowerShell
here), anything on ps, the wheelhouse and model fallbacks.
Open question: whether ps can reach Conda/PyPI/Hugging Face (runbook step 1 decides).

## 2026-10-20 — GitHub through the SSH RemoteForward tunnel (preferred), bundles as fallback

User set up `RemoteForward 127.0.0.1:17897 127.0.0.1:7897` (Windows Clash Verge mixed port) on the
Remote-SSH host; `curl -I -x http://127.0.0.1:17897 https://github.com` works on ps, plain `git clone`
still fails. Changes (tooling, merged into instr):
- scripts/check_gh_tunnel.sh (read-only): listener on 127.0.0.1:17897 must belong to the user's own
  sshd/sshd-session process, GitHub must answer through the proxy, `git ls-remote` must work.
- scripts/sync_from_github.sh: tunnel check, origin == fork URL, clean tree, fetch with
  `-c http.proxy=...` (fsck on), baseline-v1 fetched without overwrite, fast-forward only for
  upstream/tooling/instr, upstream and baseline-v1 must be 9a91cfa; DRY_RUN=1 checks only.
- docs/week1-server.md: new section G (G0 Windows SSH config, G1 inline tunnel check, G2 first clone
  through the tunnel, G3 updates, one-off `pull --ff-only` with guards, adding `origin` to a bundle
  checkout); T and step 2 marked as fallback. CLAUDE.md deployment rule rewritten accordingly.
- Found uncommitted changes in the working tree that reverted the bundle commit (f3fdcd5) and deleted
  scripts/sync_from_bundle.sh; no session was running. Kept as `git stash` entry
  "uncommitted revert of bundle deploy" (not applied, not dropped) for the user to review.
Verified locally (stubs for ss/ps/curl/id, a bare repo standing in for the fork via url.insteadOf):
shellcheck clean; sync_from_github.sh fast-forwards, is idempotent, and refuses a rewritten branch, a
moved baseline-v1 tag, a dirty tree, a wrong or missing origin, a missing listener, another user's
listener and a dead proxy; the inline G1 check gives the same verdicts; all 38 runbook bash blocks parse.
Not verified: anything on ps (real ss -p output for the sshd listener, the proxy, the G2 clone).

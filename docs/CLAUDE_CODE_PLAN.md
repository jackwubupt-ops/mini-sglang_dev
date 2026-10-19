# Claude Code execution plan (corrected)

2026-10-19 · @jacky

This tab turns milestones E1–E6 from the main plan into Claude Code sessions. It covers where to run Claude Code, the files that carry state between sessions, a session-by-session schedule, and the first prompt. Repo facts were checked against mini-sglang at commit 9a91cfa on 2026-10-19. Claude Code behaviour can change between versions, so check the docs if something doesn't match.

## Errata applied in this version

| # | Was | Problem (verified in repo) | Fix |
|---|-----|----------------------------|-----|
| 1 | Raw JSON/JSONL under `results/`, `results/env.json`, golden files, step-log JSONL | `.gitignore` ignores `*.json` and `*.jsonl` everywhere, so none of these would be committed | **Done on `dev`:** un-ignore rules for `results/` and `tests/golden/`, plus `logs/` ignored |
| 2 | `docs/PLAN.md` committed in setup (on `main`), then `dev` created from `baseline` | `dev` would not contain `docs/PLAN.md`, so `@docs/PLAN.md` fails in every later session | **Done:** `dev` (based on `9a91cfa`) is pushed to the fork; commit PLAN.md on `dev` |
| 3 | `pytest tests/core tests/misc` treated as a real test suite | `tests/core/test_scheduler.py` is a script (`main()` loads gated `meta-llama/Llama-3.1-8B-Instruct`), not a pytest test. `tests/misc/test_serialize.py` uses `@call_if_main()`, which runs the function at import time and replaces it with `None`, so pytest collects 0 tests there. Only `tests/core/test_cache_allocate.py` (6 tests) really runs | The S0 prompt says this; the expected count is about 6. Golden-output and integration tests from S1/S7+ become the real regression suite |
| 4 | "dev extras, following the README" | README only shows `uv pip install -e .` | Explicit command `uv pip install -e ".[dev]"`. dev provides `pytest-cov`, which `pyproject` addopts (`--cov`) require |
| 5 | "Wait until it is ready" | No `/health` endpoint. Default port is 1919 | Poll `GET http://127.0.0.1:1919/v1/models` |
| 6 | Fork and clone | The current local clone's `origin` is upstream `sgl-project/mini-sglang` | Fork is `jackwubupt-ops/mini-sglang_dev`. On the box, clone it as `origin`, add upstream as `upstream`, work on `dev`. Never push to upstream |
| 7 | JIT fallback "use the Dockerfile" | Dockerfile pins CUDA 12.8.1 / Ubuntu 24.04 / Py 3.12 and also installs `torch-c-dlpack-ext` | Mention both in the prompt so the fallback is concrete |
| 8 | Golden outputs "identical" in S7 | Greedy outputs are only bit-identical when batch composition is the same (FP non-associativity) | Golden files are produced and checked with requests sent **one at a time** (bs=1). Cross-batch comparisons belong to S11 margins |
| 9 | Calendar | S6 (weeks 3–4), S7–S10 (4–7) and S11–S12 (6–8) overlap, but S11 depends on S10 | Calendar made sequential (below) |
| 10 | Rhythm step 4 only names `/code-review` | Project is run with the superpowers plugin | Superpowers skill mapping added (below) |

Verified as correct: commit `9a91cfa` exists; flags `--attention-backend fi` (alias `--attn`) and `--cache-type naive` (choices `naive`/`radix`) exist; `Scheduler.overlap_loop`, `Scheduler._process_last_data`, `Req.cached_len/device_len/max_device_len` and the `extend_len` property exist; `check_integrity` exists on cache managers; env vars use the `MINISGL_` prefix via `python/minisgl/env.py`, so `MINISGL_STEP_LOG` must be added to `EnvClassSingleton`. The S2 TTFT bug is real: in `python/minisgl/benchmark/client.py`, `tics[0]` is taken *after* `await client.chat.completions.create(...)` returns, so queueing/prefill time before the stream opens is excluded from TTFT, and "TPOT" is actually pooled inter-chunk gaps (ITL).

## Claude Code terms you will use

| What you want | Claude Code name | How |
|---|---|---|
| Standing project instructions | CLAUDE.md (repo root) | `/init` drafts one; `/memory` edits it; `/context` confirms it loaded |
| Explore and plan without edits | Plan mode | Shift+Tab until the status bar shows plan mode, or `claude --permission-mode plan`; Ctrl+G opens the plan in your editor |
| Let Claude work without asking each time | Auto mode | A classifier blocks risky actions. Manual mode asks before every edit and command. `/permissions` pre-approves specific commands. Check which mode your version starts in |
| Stop and redirect | Esc | Context is kept |
| Undo | Checkpoints | Esc Esc or `/rewind`. Covers Claude's file edits only, not shell side effects; git is your real safety net |
| Fresh or smaller context | `/clear`, `/compact <focus>`, `/btw` | `/clear` between tasks; `/btw` asks a side question without adding it to context |
| Continue later | Sessions | `/rename <name>`, then `claude --continue` or `claude --resume` |
| Research or review in a separate context | Subagents | Say "use a subagent to…"; `/code-review` reviews the current diff |
| Reusable workflow | Skills | `.claude/skills/<name>/SKILL.md`, invoked as `/<name>` |
| Must-run automation | Hooks | Scripts at fixed points, e.g. a Stop hook that runs tests; `/hooks` lists them |
| Keep going until a check passes | `/goal` | A separate evaluator re-checks the condition after each turn |
| Scripted, non-interactive run | `claude -p "…"` | For batch jobs and CI |

### Superpowers mapping

Install the superpowers plugin on the GPU box too. Its skills fire automatically, and you can also name them.

| Plan step | Superpowers skill |
|---|---|
| Plan-mode exploration before engine changes (S2, S3, S7–S10) | `superpowers:brainstorming` → `superpowers:writing-plans` |
| Implementing an approved plan | `superpowers:executing-plans` (inline) or `superpowers:subagent-driven-development` |
| New timing code, budget split, mixed batches | `superpowers:test-driven-development`. In S8 **you** write the failing test and the function |
| "Same step fails twice → stop" | `superpowers:systematic-debugging` |
| "Require evidence, not claims" | `superpowers:verification-before-completion` |
| End-of-session review | `superpowers:requesting-code-review` (with the invariant list) or `/code-review`, then `superpowers:receiving-code-review` |
| S15 fresh-clone reproduction | `superpowers:using-git-worktrees` |
| Branch wrap-up | `superpowers:finishing-a-development-branch` |

Superpowers writes design specs and plans under `docs/superpowers/`. Keep them; they are good evidence that the design was your own and reached independently.

## Where to run it

The experiments need the GPU, so Claude Code must run on the GPU machine. Mini-SGLang is Linux-only (README); nothing runs on macOS.

1. Recommended: install the Claude Code CLI on the rented Linux GPU box, SSH in, and run `claude` inside tmux in the repo.
2. GUI alternative: Claude Code Desktop SSH session, or VS Code Remote-SSH with the Claude Code extension.
3. Claude Code on the web: no GPU. Use it only for reading code, CPU-only unit tests, and docs or plots from committed results.

On a disposable box with nothing private on it, auto mode is reasonable. Commit before each risky step, keep tokens (HF, GitHub) in env vars, and shut the box down between sessions.

## One-time setup (you, about an hour)

1. Rent the GPU box (24 GB Ampere/Ada, Ubuntu 22.04/24.04 with an NVIDIA driver that supports CUDA ≥ 12.8). Install git, tmux, uv, gh, and a CUDA toolkit that matches the driver (the JIT kernels need `nvcc`).
2. Fork already exists: `jackwubupt-ops/mini-sglang_dev`, with branch `dev` (gitignore fix + this plan as `docs/CLAUDE_CODE_PLAN.md`). On the box: `gh repo clone jackwubupt-ops/mini-sglang_dev mini-sglang && cd mini-sglang && git switch dev && git remote add upstream https://github.com/sgl-project/mini-sglang.git`.
3. Export the main tab of this doc as Markdown to `docs/PLAN.md` on `dev`. Prompt 0 commits it.
4. Install Claude Code (and the superpowers plugin), run `claude` in the repo root and sign in.
5. Paste Prompt 0.
6. Review the CLAUDE.md draft, approve it, and let it commit.

## Files that carry state between sessions

| File | Purpose | Who writes it |
|---|---|---|
| `CLAUDE.md` | Commands, ground rules, gotchas; loaded every session; under ~60 lines | Claude drafts, you approve |
| `docs/PLAN.md` | The engineering plan; referenced as `@docs/PLAN.md` | Exported from this doc, committed on `dev` |
| `docs/PROGRESS.md` | Dated log: done, commands, problems, next step | Claude appends every session |
| `docs/DECISIONS.md` | Each design decision and why | You write the reasoning |
| `results/` | Raw JSON per run plus `env.json`; never edited by hand. **Un-ignored in `.gitignore`** | Scripts only |
| `tests/golden/` | Greedy bs=1 outputs for 64 prompts. **Un-ignored** | Script from S1 |
| `.claude/skills/bench/SKILL.md` | Add at end of E2 | Claude, from the working command |
| `.claude/agents/invariant-reviewer.md` | Add at E4: subagent that checks a diff against the seven invariants | Claude drafts, you edit |

## Rhythm for every session

1. Start clean: new `claude` or `/clear`, then `/rename <session name>`.
2. For sessions that touch engine code, use plan mode (or let `superpowers:brainstorming`/`writing-plans` run); read and edit the plan; approve before implementation.
3. Require evidence: test output, the command run, and the path of any raw results file.
4. Before ending: `/code-review` or `superpowers:requesting-code-review` against the invariants in PLAN.md Phase 4. Then commit and append to `docs/PROGRESS.md`.
5. Read every diff yourself and write 3–5 sentences explaining it.
6. If you corrected the same mistake twice, `/clear` and rewrite the prompt.
7. Run long benchmarks as background processes writing to `logs/`; check them later.

## Session schedule

| # | Session | Milestone | Mode | Goal | Done when | You do yourself |
|---|---|---|---|---|---|---|
| S0 | setup | E1 | auto | Install, smoke test, tests, env capture, PROGRESS.md, CLAUDE.md, gitignore fix | All steps green or blockers named | Approve CLAUDE.md |
| S1 | tour | E1 | plan | Guided trace of one request; golden greedy bs=1 outputs for 64 prompts | notes/architecture.md, golden files committed (check `git ls-files tests/golden`) | Draw the lifecycle diagram from memory |
| S2 | bench-fix | E2 | plan → auto | TTFT measured from request send (before `create()`); separate ITL and per-request TPOT; raw per-request JSON | Client reports TTFT, ITL, TPOT, max gap; unit test for the timing code | Check the fix against the client code |
| S3 | step-log | E2 | plan → auto | `MINISGL_STEP_LOG` (added to `EnvClassSingleton` in `env.py`) JSONL with CUDA-event GPU time | Logging-off overhead within noise; fields documented | — |
| S4 | workloads | E2 | auto | W0–W3 generators, `scripts/run_matrix.py`, `configs/*.yaml`, fixed seeds | One command reproduces one run | Choose the SLOs for goodput |
| S5 | baseline | E2 | auto, background | Find saturation rate; 5 interleaved runs per workload and load | Reruns inside recorded CIs | Sanity-check the numbers |
| S6 | diagnose | E3 gate | plan | Join step log with client gaps; control runs; one Nsight trace; step-cost fit; memo | Memo with two figures and a go/pivot verdict | You make the go/pivot call |
| S7 | policy-flag | E4 c1 | plan → auto | `--schedule-policy` plumbing, no behaviour change | bs=1 golden outputs identical; all tests pass | — |
| S8 | decode-first | E4 c3 | plan → auto | Decode-first separate batches (ablation P1) | Unit tests pass | Hand-write the budget-split function and its test (TDD), then ask for review |
| S9 | mixed-no-overlap | E4 c4 | plan → auto | Mixed batches with `MINISGL_DISABLE_OVERLAP_SCHEDULING=1`: LM-head slicing, FlashInfer routing, CUDA-graph exclusion | Integration test passes with overlap off | Write the control-flow section of docs/design.md |
| S10 | mixed-overlap | E4 c5 | plan → auto | Overlap on: per-request phase snapshot (note `_process_last_data` runs one step late and skips `ChunkedReq`), invariant fixes | Integration passes with overlap on and off; `check_integrity` passes | Explain the hardest invariant in DECISIONS.md |
| S11 | correctness | E5 | auto | Determinism, cross-policy margins, teacher-forced check, edge cases | Consistency report written | Review every divergence case |
| S12 | stress | E5 | auto + `/goal` | 30-min stress run with aborts, `--cache-type radix` | Integrity passes; no leaked pages or slots | — |
| S13 | matrix | E6 | auto, background | Full matrix and ablations | Raw results complete for every cell | — |
| S14 | analysis | E6 | auto | Plots, frontier chart, CIs, step-cost fit, report draft | Every figure regenerates from raw results | You write the conclusions |
| S15 | polish | E6 | auto | README, design doc, troubleshooting; fresh-clone reproduction in a new worktree or box | Someone else reproduces one figure | — |
| R2 | pr-review | after S10 | plan | Now read upstream PR #155 and compare with your implementation | Written review | Write the comparison |
| R1 | vllm-compare | optional | auto | vLLM budget sweep on same GPU and W2 request file | Side-by-side frontier chart | Map each invariant to vLLM code |

Rough calendar at 15–20 h/week, sequential: S0–S1 week 1; S2–S5 weeks 2–3; S6 week 4; S7–S10 weeks 5–7; S11–S12 week 8; S13–S15 weeks 9–10. Some sessions will take two sittings.

## Prompt 0: first session

Use a normal (auto mode) session. Paste as one message.

```
You're helping me build a portfolio project on Mini-SGLang, a compact LLM serving engine. This repo is my fork jackwubupt-ops/mini-sglang_dev (origin = my fork, upstream = sgl-project/mini-sglang); I work on branch `dev`. The engineering plan is in docs/PLAN.md (uncommitted for now); the session plan is docs/CLAUDE_CODE_PLAN.md. For this session, read only its Summary, "The engineering problem", "Phase 1" and "Phase 2" sections.

This is Session 0: environment setup only (milestone E1). Do not modify anything under python/minisgl/ in this session.

Do these in order, and show me the command and its key output for each:

1. Baseline pin and branches. Confirm I'm on `dev` and that `git merge-base dev 9a91cfa` is 9a91cfa, and that `git diff 9a91cfa dev -- python/` is empty. Create branch `baseline` and tag `baseline-v1` at 9a91cfa if they don't exist. Commit docs/PLAN.md on `dev`. Don't push yet.
2. Environment. Report OS, GPU model and memory, driver version, `nvcc --version`, Python version, and whether the CUDA toolkit is compatible with the driver. If something is missing, tell me what to install; ask before using sudo.
3. Install. `uv venv --python=3.12 && source .venv/bin/activate && uv pip install -e ".[dev]"`. Report torch, flashinfer-python and sgl_kernel versions.
4. Smoke test. Start `python -m minisgl --model Qwen/Qwen3-0.6B --attention-backend fi --cache-type naive` as a background process logging to logs/server.log (default port 1919). There is no /health endpoint: poll GET http://127.0.0.1:1919/v1/models until it answers. Send one streaming and one non-streaming /v1/chat/completions request with temperature 0, then shut the server down cleanly and confirm no GPU processes remain. If the JIT kernels fail to build, stop and propose using the repo's Dockerfile (CUDA 12.8.1, Ubuntu 24.04, also installs torch-c-dlpack-ext) instead of changing CUDA versions.
5. Tests. Run `pytest tests/core tests/misc -q` and report the counts. Expect only about 6 collected tests: tests/core/test_scheduler.py is a script (its main() needs gated Llama-3.1-8B) and tests/misc/test_serialize.py runs at import time through @call_if_main(), so pytest collects nothing from it. Confirm this, don't fix failures, summarise each one.
6. Git-ignore check. .gitignore on `dev` already ignores logs/ and un-ignores JSON/JSONL under results/ and tests/golden/. Verify with `git check-ignore -v --no-index results/env.json logs/server.log` (first must NOT be ignored, second must be). Report only; edit only if the check fails.
7. Env capture. Write scripts/capture_env.py that writes results/env.json with GPU name and memory, driver, CUDA toolkit, torch (and torch.version.cuda), flashinfer-python and sgl_kernel versions (via importlib.metadata), CPU model, Python version and git commit. Run it.
8. Progress log. Create docs/PROGRESS.md with a dated Session 0 entry: what worked, exact commands, versions, problems hit, and open questions.
9. CLAUDE.md. Draft a CLAUDE.md for this repo, under 60 lines: the setup, run, test and benchmark commands you verified; the ground rules below; and gotchas you hit (include the test-collection and gitignore facts above). Show me the draft and wait for my OK before writing the file.
10. Commit steps 7–9 on `dev` as separate commits with clear messages. Then push `dev` to origin (my fork) only.

Ground rules for the whole project (these go into CLAUDE.md):
- Never write a performance number that wasn't produced by a command in this repo; every number in docs must point to a file under results/.
- Default scheduling behaviour must stay unchanged unless a flag enables the new policy.
- Don't open or fetch upstream PR #155 (sgl-project/mini-sglang, mixed batch) or its diff until I say so. I want an independent design.
- Small commits with descriptive messages; never force-push; never push to upstream; never commit model weights; raw results only under results/.
- If the same step fails twice, stop and tell me what you tried instead of working around it.
- End every session by appending to docs/PROGRESS.md.

Finish with a checklist of steps 1–10 with status, any blockers, and the three files I should read first next session.
```

## Prompt 1: codebase tour

Switch to plan mode before sending.

```
Session 1: codebase tour (milestone E1). First read the latest entry in @docs/PROGRESS.md, then the "Request lifecycle" and "Prioritized reading order" parts of @docs/PLAN.md.

Walk me through one /v1/chat/completions request from the API server (python/minisgl/server/api_server.py) to the detokenizer (python/minisgl/tokenizer/detokenize.py), one hop at a time, citing file and function for each hop. After each hop, stop and ask me one question to check I understood it before continuing.

Then explain Scheduler.overlap_loop: what step n+1 overlaps with, and why _process_last_data handles results one step late (and why it skips ChunkedReq). Then trace the Req length fields in python/minisgl/core.py (cached_len, device_len, max_device_len, and the extend_len property) for a 10,000-token prompt with --max-prefill-length 4096, step by step until its first decode step.

After the tour, propose (don't run yet) a script that saves greedy outputs for 64 fixed prompts as golden files under tests/golden/, sending requests one at a time so batch composition can't change the results, and an outline for notes/architecture.md that I will write myself.
```

## Template for later sessions

```
Session <n>: <name> (milestone <E#>; see the "<section>" section of @docs/PLAN.md).
First read the latest entry in @docs/PROGRESS.md.

Goal: <one sentence>.
In scope: <files and functions>. Out of scope: <list>.
Done means: <checks that must pass, with the exact command>.

Explore, then propose a plan that names the files you'll change, the invariants it touches, and the tests you'll add (write tests first). Wait for my approval before editing.
When finished: show the test output; use a subagent to review the diff against the invariants in PLAN.md Phase 4 and fix only correctness gaps; commit; append to docs/PROGRESS.md.
```

## Keeping the work yours

- Write the reasoning yourself: DECISIONS.md, the design doc's control-flow section, and the report's conclusions.
- Hand-write one core piece: the decode/prefill budget split and its unit test in S8.
- Make the diagnosis call yourself in S6 before asking Claude's opinion.
- Be open about the tool: say you used Claude Code (with superpowers) for implementation and tooling, and how you verified it: tests, invariants, reruns and diff reviews.

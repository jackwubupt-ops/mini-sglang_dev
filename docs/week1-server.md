# Week 1 server runbook — GitHub through the SSH tunnel, bundles as fallback

Follows PLAN 4.5 and 17, adapted to the facts of 2026-10-20: the project root on `ps` is
`/home/xwc/vllm`, and **direct HTTPS from `ps` to GitHub fails** (`gnutls_handshake() failed`).
The user's Windows machine now carries GitHub traffic for `ps` through an SSH RemoteForward:

```
ps:127.0.0.1:17897 --(SSH RemoteForward, VS Code Remote-SSH session)--> Windows 127.0.0.1:7897 (Clash Verge mixed port)
```

**Preferred:** `ps` clones and fetches the fork through that tunnel (section G), with the proxy
given on every command (`git -c http.proxy=http://127.0.0.1:17897 ...`).
**Fallback:** git bundles built on Windows and copied with scp (sections T and 2), used whenever
the tunnel check (G1) fails.

Rules:
- Every GitHub HTTPS command on `ps` passes `-c http.proxy=http://127.0.0.1:17897` explicitly.
  Plain `git clone/fetch/pull` does **not** use the tunnel. Never store the proxy with
  `git config` (global, system or repo) or in shell startup files.
- Never disable TLS verification (`http.sslVerify`, `GIT_SSL_NO_VERIFY`, `curl -k`).
- Verify the tunnel (G1) before relying on it. If it fails, stop and report the output. Do not
  retry direct GitHub access; use the bundle fallback if you need code before the tunnel is fixed.
- Only fast-forward (`pull --ff-only`, `merge --ff-only`). Never `pull --force`, `reset --hard`,
  `checkout -f`, `clean`, or anything else that overwrites uncommitted work. Commit nothing on `ps`.
- No AI agent and no agent session files on `ps`. Never touch other users' files. Nothing on
  `ps` overwrites an existing file (transfers are timestamped and checked first).
- The tunnel port 127.0.0.1:17897 is reachable by every local user of `ps` while you are
  connected. G1 checks that your own sshd holds it. TLS still protects the content.

Notation: **[Mac]** zsh on your Mac, **[Win]** Windows PowerShell (5.1 or 7), **[ps]** bash in the
VS Code Remote-SSH terminal on `ps` as `xwc`. PowerShell blocks are wrapped in `. { ... }` so an
error stops the whole block while variables like `$T` stay set for the next block; keep one
PowerShell window open for a whole transfer. Keep one `ps` terminal open too (variables
`GPU`, `P`, `MODEL`, `SFLAGS` are set once and reused).

**Order:** 0 → 1 → G1 → G2 (if G1 fails: T → 2) → 3 → 4 → 5 … 11. Later code updates: G3
(fallback: T2–T4 + `scripts/sync_from_bundle.sh`).

---

## 0. [Mac] Review, push, record the SHAs

```bash
git -C ~/mini-sglang diff 9a91cfa tooling --stat -- python/ && git -C ~/mini-sglang diff tooling instr --stat -- python/
```
Expected: the first prints nothing (`tooling` leaves the engine untouched); the second lists only
`python/minisgl/env.py` and `python/minisgl/tokenizer/server.py`.

```bash
git -C ~/mini-sglang push fork upstream tooling instr baseline-v1
```
```bash
git -C ~/mini-sglang for-each-ref --format='%(objectname) %(refname)' refs/heads/upstream refs/heads/tooling refs/heads/instr refs/tags/baseline-v1
```
Keep this output: `ps` (G2 or G3; fallback: Windows T2 and `ps` step 2) prints the same four lines, and they must match.

---

## G. [ps] GitHub through the SSH RemoteForward tunnel (preferred)

**G0. [Win] SSH host entry** (in `%USERPROFILE%\.ssh\config`, the host VS Code Remote-SSH uses):

```
RemoteForward 127.0.0.1:17897 127.0.0.1:7897
ExitOnForwardFailure yes
ServerAliveInterval 30
ServerAliveCountMax 3
```
Clash Verge must be running with its mixed port on `127.0.0.1:7897`. After changing the config,
close and reopen the Remote-SSH window. With `ExitOnForwardFailure yes` the connection fails
(instead of silently connecting without the tunnel) if 17897 is already taken on `ps`.

**G1. Verify the tunnel** (read-only; run it at the start of every session that needs GitHub).
This inline check is the same as `scripts/check_gh_tunnel.sh`, which you can use once the repo
exists:

```bash
if [ "$(id -un)" != xwc ]; then echo "STOP: not xwc"; else L=$(ss -ltnpH 'sport = :17897' | head -n 1); TP=$(sed -n 's/.*pid=\([0-9]*\).*/\1/p' <<< "$L" | head -n 1); echo "listener: ${L:-none}"; if [ -z "$TP" ] || [ "$(ps -o user= -p "$TP" | tr -d ' ')" != xwc ] || ! ps -o comm= -p "$TP" | grep -qx 'sshd\|sshd-session'; then echo "STOP: 127.0.0.1:17897 is not held by your sshd; reconnect Remote-SSH, do not use the port"; elif ! curl -sS -o /dev/null -I --connect-timeout 10 --max-time 20 -x http://127.0.0.1:17897 -w 'github via tunnel: %{http_code}\n' https://github.com; then echo "STOP: proxy not answering; check Clash Verge on Windows"; elif ! git -c http.proxy=http://127.0.0.1:17897 ls-remote https://github.com/jackwubupt-ops/mini-sglang_dev.git HEAD; then echo "STOP: git ls-remote through the tunnel failed"; else echo "tunnel ok"; fi; fi
```
Expected: a `LISTEN ... 127.0.0.1:17897 ... users:(("sshd",pid=...))` line (or `sshd-session`),
`github via tunnel: 200`, one `<sha> HEAD` line, `tunnel ok`. Any `STOP`: report the output to
me and use the bundle fallback (T, 2) if you need code now. Do not retry plain `git clone`.

**G2. First checkout through the tunnel** (once; only after G1 printed `tunnel ok`):

```bash
if [ "$(id -un)" != xwc ] || [ ! -O /home/xwc/vllm ]; then echo "STOP: not xwc or /home/xwc/vllm not ours"; elif [ -e /home/xwc/vllm/mini-sglang_dev ]; then ls -ld /home/xwc/vllm/mini-sglang_dev; echo "STOP: repo exists; inspect it, then update with scripts/sync_from_github.sh"; else git -c http.proxy=http://127.0.0.1:17897 -c transfer.fsckObjects=true clone -b tooling https://github.com/jackwubupt-ops/mini-sglang_dev.git /home/xwc/vllm/mini-sglang_dev && cd /home/xwc/vllm/mini-sglang_dev && git branch --track upstream origin/upstream && git branch --track instr origin/instr && git -c http.proxy=http://127.0.0.1:17897 fetch --no-tags origin 'refs/tags/baseline-v1:refs/tags/baseline-v1' && { git config --get http.proxy || echo "no stored proxy (good)"; } && [ "$(git rev-parse upstream)" = 9a91cfafe754aa85daee49998176275667eb58f2 ] && [ "$(git rev-parse 'baseline-v1^{commit}')" = 9a91cfafe754aa85daee49998176275667eb58f2 ] && echo "upstream and baseline-v1 at 9a91cfa" && git for-each-ref --format='%(objectname) %(refname)' refs/heads/upstream refs/heads/tooling refs/heads/instr refs/tags/baseline-v1 && git status --short --branch; fi
```
Expected: the clone, `no stored proxy (good)`,
`upstream and baseline-v1 at 9a91cfa`, four lines that match the Mac output of step 0, and
`## tooling...origin/tooling`. Then continue at step 3.

**G3. Later updates** (after you push from the Mac). Run from the repo with the env active. It
re-runs G1, checks that `origin` is the fork and the tree is clean, fetches through the tunnel,
fast-forwards `upstream`, `tooling` and `instr` only, and refuses if baseline-v1 would move:

```bash
cd /home/xwc/vllm/mini-sglang_dev && DRY_RUN=1 scripts/sync_from_github.sh && scripts/sync_from_github.sh
```

For a one-off update of the current branch only, use the same guards by hand:

```bash
cd /home/xwc/vllm/mini-sglang_dev && if [ -n "$(git status --porcelain)" ]; then echo "STOP: uncommitted changes"; elif [ "$(git config --get remote.origin.url)" != https://github.com/jackwubupt-ops/mini-sglang_dev.git ]; then echo "STOP: origin is not the fork"; elif scripts/check_gh_tunnel.sh; then git branch --show-current && git -c http.proxy=http://127.0.0.1:17897 pull --ff-only; fi
```

**If the repo came from a bundle** (it has the remote `bundle` and no `origin`), add the fork
once, without changing anything else, then use G3:

```bash
cd /home/xwc/vllm/mini-sglang_dev && if git remote | grep -qx origin; then echo "origin exists: $(git config --get remote.origin.url)"; else git remote add origin https://github.com/jackwubupt-ops/mini-sglang_dev.git && git remote -v; fi
```

---

## T. Fallback [Win]: build a verified bundle and copy it to `ps`

Use T only when G1 fails. Run T0 and T1 once, then T2–T4 for every transfer (first checkout and every later update).

**T0. Tools and SSH host.** Use the same SSH host you use for VS Code Remote-SSH (an alias from
`~\.ssh\config`, or `xwc@<server address>`).

```powershell
. { $ErrorActionPreference = 'Stop'
  $H = 'xwc@ps'      # <- change to your Remote-SSH host alias if it differs
  $T = Join-Path $env:USERPROFILE 'mini-sglang-transfer'
  git --version; ssh -V; scp 2>&1 | Select-Object -First 1; tar --version | Select-Object -First 1
  ssh $H 'echo user=$(id -un) host=$(hostname); ls -ld /home/xwc/vllm'
}
```
Expected: git, OpenSSH and tar versions; `user=xwc`, and `/home/xwc/vllm` owned by `xwc`.
If `git` is missing, install Git for Windows first. Re-run the first three lines (`$H`, `$T`) in any
new PowerShell window before T2–T4.

**T1. One-time bare mirror of the fork on Windows** (never overwrites an existing one):

```powershell
. { $ErrorActionPreference = 'Stop'
  New-Item -ItemType Directory -Force -Path $T | Out-Null
  $M = Join-Path $T 'mini-sglang_dev.git'
  if (Test-Path $M) { "mirror exists: $M (T2 updates it)" }
  else { git clone --mirror https://github.com/jackwubupt-ops/mini-sglang_dev.git $M
         if ($LASTEXITCODE -ne 0) { throw 'clone failed' } }
}
```

**T2. Update the mirror and check the refs against the Mac:**

```powershell
. { $ErrorActionPreference = 'Stop'
  $M = Join-Path $T 'mini-sglang_dev.git'
  git -C $M fetch origin; if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
  $Refs = git -C $M for-each-ref --format='%(objectname) %(refname)' refs/heads/upstream refs/heads/tooling refs/heads/instr refs/tags/baseline-v1
  $Refs
  if (@($Refs).Count -ne 4) { throw 'expected 4 refs (upstream, tooling, instr, baseline-v1); push them from the Mac' }
  if ((git -C $M rev-parse upstream) -ne '9a91cfafe754aa85daee49998176275667eb58f2') { throw 'upstream is not 9a91cfa' }
  if ((git -C $M rev-parse 'baseline-v1^{commit}') -ne '9a91cfafe754aa85daee49998176275667eb58f2') { throw 'baseline-v1 is not 9a91cfa' }
  'refs ok: compare the four lines above with the Mac output of step 0'
}
```

**T3. Create the bundle, its sha256 and its refs file** (LF line endings, readable by `sha256sum`):

```powershell
. { $ErrorActionPreference = 'Stop'
  $M = Join-Path $T 'mini-sglang_dev.git'
  $Name = 'mini-sglang_dev-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.bundle'
  $B = Join-Path $T $Name
  git -C $M bundle create $B upstream tooling instr baseline-v1; if ($LASTEXITCODE -ne 0) { throw 'bundle create failed' }
  git -C $M bundle verify $B; if ($LASTEXITCODE -ne 0) { throw 'bundle verify failed' }
  $Refs = git -C $M for-each-ref --format='%(objectname) %(refname)' refs/heads/upstream refs/heads/tooling refs/heads/instr refs/tags/baseline-v1
  $Heads = git bundle list-heads $B
  if (Compare-Object @($Refs | Sort-Object) @($Heads | Sort-Object)) { throw 'bundle heads differ from mirror refs' }
  $Hash = (Get-FileHash -Algorithm SHA256 $B).Hash.ToLower()
  [IO.File]::WriteAllText("$B.sha256", "$Hash  $Name`n")
  [IO.File]::WriteAllText("$B.refs", (($Refs | Sort-Object) -join "`n") + "`n")
  "bundle: $B"; "sha256: $Hash"
}
```

**T4. Copy it to `ps`** (refuses if a file with that name already exists there):

```powershell
. { $ErrorActionPreference = 'Stop'
  ssh $H 'set -e; [ $(id -un) = xwc ]; [ -O /home/xwc/vllm ]; mkdir -p /home/xwc/vllm/transfer; [ -O /home/xwc/vllm/transfer ]; ls -ld /home/xwc/vllm/transfer'
  if ($LASTEXITCODE -ne 0) { throw 'STOP: remote user or /home/xwc/vllm ownership check failed' }
  ssh $H "test ! -e /home/xwc/vllm/transfer/$Name"
  if ($LASTEXITCODE -ne 0) { throw "STOP: /home/xwc/vllm/transfer/$Name already exists" }
  scp $B "$B.sha256" "$B.refs" "${H}:/home/xwc/vllm/transfer/"
  if ($LASTEXITCODE -ne 0) { throw 'scp failed' }
  ssh $H "cd /home/xwc/vllm/transfer && sha256sum -c $Name.sha256"
  if ($LASTEXITCODE -ne 0) { throw 'sha256 mismatch on ps' }
  "on ps: /home/xwc/vllm/transfer/$Name"
}
```
Expected last lines: `mini-sglang_dev-<stamp>.bundle: OK` and the path to use in step 2 (first
time) or in `scripts/sync_from_bundle.sh` (later updates).

---

## 1. [ps] Read-only checks, including what the server can reach

```bash
whoami; hostname; pwd; echo "$HOME"; ls -ld "$HOME" /home/xwc/vllm; nvidia-smi; command -v sinfo; command -v squeue; command -v nvcc && nvcc --version; df -h /home/xwc /home/xwc/vllm
```
Expected: `xwc`, `ps`, `/home/xwc` and `/home/xwc/vllm` owned by `xwc`; no output from the
`sinfo`/`squeue` checks (if there is, stop: the scripts refuse to launch under a scheduler).

GitHub is not the only network dependency: Conda, pip and the model download need
`repo.anaconda.com`/`conda.anaconda.org`, `pypi.org`/`files.pythonhosted.org` (or a configured
mirror) and `huggingface.co`. Check them read-only (proxy credentials are redacted):

```bash
for u in https://repo.anaconda.com https://conda.anaconda.org https://pypi.org/simple/pip/ https://files.pythonhosted.org https://huggingface.co/api/models/Qwen/Qwen3-0.6B; do printf '%-55s ' "$u"; curl -sS -o /dev/null -m 20 -w '%{http_code}\n' "$u" 2>&1 | tail -n 1; done; env | grep -i '_proxy=' | sed -E 's#//[^@/]*@#//<redacted>@#'; (conda config --show channels; python3 -m pip config list) 2>/dev/null
```
A `200` (or `301/302`) means reachable; `000` or a TLS error means blocked. Use the results:
Conda blocked → stop at step 3 and tell me; pip blocked → use step 4b; Hugging Face blocked →
use step 4c.

## 2. Fallback [ps]: first checkout from the bundle (once; skip if G2 succeeded)

Set `B` to the path T4 printed, then:

```bash
B=/home/xwc/vllm/transfer/mini-sglang_dev-<stamp>.bundle
```
```bash
if [ "$(id -un)" != xwc ] || [ ! -O /home/xwc/vllm ]; then echo "STOP: not xwc or /home/xwc/vllm not ours"; elif [ -e /home/xwc/vllm/mini-sglang_dev ]; then echo "STOP: repo exists; update with scripts/sync_from_bundle.sh instead"; elif [ ! -f "$B" ] || [ ! -O "$B" ]; then echo "STOP: $B missing or not ours"; else cd /home/xwc/vllm/transfer && sha256sum -c "$(basename "$B").sha256" && diff <(git bundle list-heads "$B" | sort) <(sort "$B.refs") && git -c transfer.fsckObjects=true clone -b tooling "$B" /home/xwc/vllm/mini-sglang_dev && cd /home/xwc/vllm/mini-sglang_dev && git remote rename origin bundle && git config transfer.fsckObjects true && git branch upstream bundle/upstream && git branch instr bundle/instr && git for-each-ref --format='%(objectname) %(refname)' refs/heads refs/tags && git status --short --branch; fi
```
Expected: `...bundle: OK`, no diff output, the clone, then four lines identical to the Mac output of
step 0 and `## tooling...bundle/tooling`. The checkout has no GitHub remote, so an accidental
`git pull` fails harmlessly.

**Later updates** (after a new T2–T4 transfer), from inside the repo with the env active:

```bash
cd /home/xwc/vllm/mini-sglang_dev && DRY_RUN=1 scripts/sync_from_bundle.sh /home/xwc/vllm/transfer/mini-sglang_dev-<stamp>.bundle && scripts/sync_from_bundle.sh /home/xwc/vllm/transfer/mini-sglang_dev-<stamp>.bundle
```
It checks sha256, the refs file, `git bundle verify`, a clean tree and `upstream`/`baseline-v1`
at 9a91cfa, then moves branches by fast-forward only and prints the refs to compare with the Mac.

## 3. [ps] Personal environment (never `base`)

```bash
if [ "$(id -un)" != "xwc" ]; then echo "STOP: not xwc"; elif [ -e /home/xwc/envs/minisgl ]; then echo "env exists: inspect it, do not recreate"; else conda create -y -p /home/xwc/envs/minisgl python=3.12; fi
```
```bash
conda activate /home/xwc/envs/minisgl && python -c "import sys; print(sys.prefix)"
```
Must print `/home/xwc/envs/minisgl`. If `conda create` fails with a network/TLS error, stop and
send me the output (do not disable SSL verification). Only if step 1 found no `nvcc` matching the
driver, add the toolkit to this env (`<VER>` = the driver's CUDA version or lower, 12.8 if possible):

```bash
[ "${CONDA_PREFIX:-}" = /home/xwc/envs/minisgl ] && conda install -y -p /home/xwc/envs/minisgl -c nvidia cuda-toolkit=<VER> || echo "STOP: activate /home/xwc/envs/minisgl first"
```

## 4. [ps] Install (pip online)

Use this if step 1 showed PyPI (or a configured mirror) reachable; otherwise go to 4b.

```bash
cd /home/xwc/vllm/mini-sglang_dev && if [ -n "$(git status --porcelain)" ]; then echo "STOP: uncommitted changes"; elif [ "$(git branch --show-current)" != tooling ]; then echo "STOP: not on tooling"; elif ! git diff --quiet 9a91cfa HEAD -- python/ pyproject.toml; then echo "STOP: python/ differs from 9a91cfa"; elif scripts/check_env.sh; then source scripts/env.sh && python -m pip install -e ".[dev]" torch-c-dlpack-ext && if [ -e results/requirements.lock ]; then echo "lock exists: not overwritten"; else python -m pip freeze > results/requirements.lock; fi; fi
```
Expected: `ok: xwc, /home/xwc/vllm/mini-sglang_dev, /home/xwc/envs/minisgl`, a successful
install, and `results/requirements.lock` (it comes back to the Mac in step 11; never commit on `ps`).

### 4b. Only if PyPI is blocked: wheelhouse built on Windows (untested)

`pip download --platform` evaluates environment markers for Windows and would drop torch's
Linux-only CUDA dependencies, so resolve for Linux with `uv` first. [Win]:

```powershell
. { $ErrorActionPreference = 'Stop'
  $W = Join-Path $T ('wheelhouse-' + (Get-Date -Format 'yyyyMMdd-HHmmss')); New-Item -ItemType Directory -Path $W | Out-Null
  py -m pip install --user uv; if ($LASTEXITCODE -ne 0) { throw 'uv install failed' }
  git -C (Join-Path $T 'mini-sglang_dev.git') show tooling:pyproject.toml | Set-Content -Encoding ascii (Join-Path $W 'pyproject.toml')
  Push-Location $W
  py -m uv pip compile pyproject.toml --extra dev --python-version 3.12 --python-platform x86_64-manylinux_2_28 -o requirements-linux.txt
  if ($LASTEXITCODE -ne 0) { Pop-Location; throw 'resolve failed' }
  Add-Content requirements-linux.txt "torch-c-dlpack-ext`nsetuptools`nwheel"
  py -m pip download --no-deps -r requirements-linux.txt -d wheels --only-binary=:all: --python-version 3.12 --implementation cp --abi cp312 --abi abi3 --abi none --platform manylinux_2_28_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 --platform linux_x86_64 --platform any
  if ($LASTEXITCODE -ne 0) { Pop-Location; throw 'download failed (a package may have no Linux wheel)' }
  Pop-Location
  $Name = (Split-Path $W -Leaf) + '.tar'; $A = Join-Path $T $Name
  tar -cf $A -C $T (Split-Path $W -Leaf)
  $Hash = (Get-FileHash -Algorithm SHA256 $A).Hash.ToLower(); [IO.File]::WriteAllText("$A.sha256", "$Hash  $Name`n")
  ssh $H "test ! -e /home/xwc/vllm/transfer/$Name"; if ($LASTEXITCODE -ne 0) { throw 'exists on ps' }
  scp $A "$A.sha256" "${H}:/home/xwc/vllm/transfer/"; if ($LASTEXITCODE -ne 0) { throw 'scp failed' }
  "on ps: /home/xwc/vllm/transfer/$Name"
}
```
[ps] (set `WH` to the printed path):

```bash
WH=/home/xwc/vllm/transfer/wheelhouse-<stamp>.tar
```
```bash
cd /home/xwc/vllm/transfer && sha256sum -c "$(basename "$WH").sha256" && D=/home/xwc/vllm/$(basename "$WH" .tar) && if [ -e "$D" ]; then echo "STOP: $D exists"; else tar -xf "$WH" -C /home/xwc/vllm && cd /home/xwc/vllm/mini-sglang_dev && scripts/check_env.sh && source scripts/env.sh && python -m pip install --no-index --find-links "$D/wheels" -e ".[dev]" torch-c-dlpack-ext && if [ -e results/requirements.lock ]; then echo "lock exists: not overwritten"; else python -m pip freeze > results/requirements.lock; fi; fi
```
FlashInfer and TVM-FFI compile kernels at first use (needs `nvcc`, step 3) and FlashInfer may try to
download prebuilt cubins; if the smoke log shows a download error, send it to me.

### 4c. Only if Hugging Face is blocked: copy the model from Windows

[Win] (needs Python on Windows; installs `huggingface_hub` for your Windows user only):

```powershell
. { $ErrorActionPreference = 'Stop'
  $Mdl = Join-Path $T 'models'; New-Item -ItemType Directory -Force -Path $Mdl | Out-Null
  if (Test-Path (Join-Path $Mdl 'Qwen3-0.6B')) { throw "already downloaded: $Mdl\Qwen3-0.6B (inspect it)" }
  py -m pip install --user huggingface_hub; if ($LASTEXITCODE -ne 0) { throw 'install failed' }
  py -c "from huggingface_hub import snapshot_download; snapshot_download('Qwen/Qwen3-0.6B', local_dir=r'$Mdl\Qwen3-0.6B')"
  if ($LASTEXITCODE -ne 0) { throw 'download failed' }
  $Name = 'Qwen3-0.6B-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.tar'; $A = Join-Path $T $Name
  tar -cf $A --exclude .cache -C $Mdl Qwen3-0.6B
  $Hash = (Get-FileHash -Algorithm SHA256 $A).Hash.ToLower(); [IO.File]::WriteAllText("$A.sha256", "$Hash  $Name`n")
  ssh $H "test ! -e /home/xwc/vllm/transfer/$Name"; if ($LASTEXITCODE -ne 0) { throw 'exists on ps' }
  scp $A "$A.sha256" "${H}:/home/xwc/vllm/transfer/"; if ($LASTEXITCODE -ne 0) { throw 'scp failed' }
  "on ps: /home/xwc/vllm/transfer/$Name"
}
```
[ps]:

```bash
MA=/home/xwc/vllm/transfer/Qwen3-0.6B-<stamp>.tar
```
```bash
cd /home/xwc/vllm/transfer && sha256sum -c "$(basename "$MA").sha256" && mkdir -p /home/xwc/vllm/models && if [ -e /home/xwc/vllm/models/Qwen3-0.6B ]; then echo "STOP: /home/xwc/vllm/models/Qwen3-0.6B exists"; else tar -xf "$MA" -C /home/xwc/vllm/models && ls -l /home/xwc/vllm/models/Qwen3-0.6B; fi
```
Then use `MODEL=/home/xwc/vllm/models/Qwen3-0.6B` in step 5.

## 5. [ps] Session variables, GPU choice, environment record

```bash
cd /home/xwc/vllm/mini-sglang_dev && scripts/check_env.sh && source scripts/env.sh && scripts/pick_gpu.sh
```
Pick a candidate GPU and a port P with P and P+1 free (e.g. 30000). `MODEL` is the hub id, or the
local directory from 4c:

```bash
GPU=<index>; P=30000; MODEL=Qwen/Qwen3-0.6B; SFLAGS=(--model-path "$MODEL" --attention-backend fi --cache-type naive --page-size 1 --max-prefill-length 4096 --max-running-requests 64 --cuda-graph-max-bs 64 --num-pages 65536); [[ "$GPU" =~ ^[0-9]+$ ]] && [ -z "$(ss -ltnH "sport = :$P")$(ss -ltnH "sport = :$((P+1))")" ] && echo "GPU=$GPU P=$P MODEL=$MODEL ok" || echo "STOP: bad GPU index or port in use"
```
```bash
scripts/check_env.sh && source scripts/env.sh && python scripts/capture_env.py --gpu "$GPU" --out "$RUNS/env-$(date +%Y%m%d-%H%M)/env.json"
```

## 6. [ps] Smoke test S1-S5

```bash
scripts/check_env.sh && source scripts/env.sh && RID=smoke-$(date +%Y%m%d-%H%M) && scripts/serve.sh "$RID" "$GPU" "$P" -- "${SFLAGS[@]}" && scripts/smoke.sh "$RID" "$GPU"
```
Expected: `ready: ...`, `Allocating 65536 tokens for KV cache`, `S1/S2/S3 PASS`; at the SIGTERM
prompt check every listed process is `xwc`, answer `y`; then `S4 PASS`, `S5 PASS`,
`smoke: all PASS`. If the server did not come up:

```bash
scripts/check_env.sh && source scripts/env.sh && tail -n 50 "$RUNS/$RID/serve.log" && scripts/stop_server.sh "$RID"
```
```bash
scripts/check_env.sh && python -m pytest tests/tooling -q -o addopts=""
```

## 7. [ps] Golden outputs, twice

```bash
scripts/check_env.sh && source scripts/env.sh && git diff --quiet 9a91cfa HEAD -- python/ pyproject.toml && echo "python/ is 9a91cfa" && GA=golden-a-$(date +%Y%m%d-%H%M%S) && scripts/run_offline.sh "$GA" "$GPU" -- python scripts/golden_outputs.py --model-path "$MODEL" --num-pages 65536 --cuda-graph-max-bs 64 && GB=golden-b-$(date +%Y%m%d-%H%M%S) && scripts/run_offline.sh "$GB" "$GPU" -- python scripts/golden_outputs.py --model-path "$MODEL" --num-pages 65536 --cuda-graph-max-bs 64
```
```bash
scripts/check_env.sh && source scripts/env.sh && python scripts/compare_outputs.py "$RUNS/$GA/outputs.json" "$RUNS/$GB/outputs.json"
```
Expected: `IDENTICAL: 64 requests`. A non-empty `meta.length_mismatch` in outputs.json is H1
evidence; note it.

## 8. [ps] R4 (H4), overlap on then off

```bash
scripts/check_env.sh && source scripts/env.sh && scripts/run_offline.sh r4-on-$(date +%Y%m%d-%H%M%S) "$GPU" -- python scripts/repro/r4_offline.py --model-path "$MODEL" --num-pages 65536 --cuda-graph-max-bs 64 && MINISGL_DISABLE_OVERLAP_SCHEDULING=1 scripts/run_offline.sh r4-off-$(date +%Y%m%d-%H%M%S) "$GPU" -- python scripts/repro/r4_offline.py --model-path "$MODEL" --num-pages 65536 --cuda-graph-max-bs 64
```

## 9. [ps] R1 (H1), overlap on then off

```bash
scripts/check_env.sh && source scripts/env.sh && RID=r1-on-$(date +%Y%m%d-%H%M) && scripts/serve.sh "$RID" "$GPU" "$P" -- "${SFLAGS[@]}" && python scripts/repro/r1_client.py --run-id "$RID"; scripts/stop_server.sh "$RID"
```
```bash
scripts/check_env.sh && source scripts/env.sh && RID=r1-off-$(date +%Y%m%d-%H%M) && MINISGL_DISABLE_OVERLAP_SCHEDULING=1 scripts/serve.sh "$RID" "$GPU" "$P" -- "${SFLAGS[@]}" && python scripts/repro/r1_client.py --run-id "$RID"; scripts/stop_server.sh "$RID"
```

## 10. [ps] R5 (H5) on `instr`, then back to `tooling`

```bash
cd /home/xwc/vllm/mini-sglang_dev && if [ -n "$(git status --porcelain)" ]; then echo "STOP: uncommitted changes"; else git switch instr && scripts/check_env.sh && git diff --stat tooling instr -- python/; fi
```
Expected: only `env.py` and `tokenizer/server.py` (the editable install picks up the switch).

```bash
scripts/check_env.sh && source scripts/env.sh && RID=r5-$(date +%Y%m%d-%H%M) && MINISGL_DEBUG_DETOK_MAP=1 scripts/serve.sh "$RID" "$GPU" "$P" -- "${SFLAGS[@]}" && python scripts/repro/r5_client.py --run-id "$RID"; grep MINISGL_DEBUG_DETOK_MAP "$RUNS/$RID/serve.log" | tail -n 3; scripts/stop_server.sh "$RID"
```
```bash
scripts/check_env.sh && python -m pytest tests/tooling -q -o addopts=""
```
```bash
cd /home/xwc/vllm/mini-sglang_dev && if [ -n "$(git status --porcelain)" ]; then echo "STOP: uncommitted changes"; else git switch tooling && git branch --show-current; fi
```

## 11. Results back to the Mac, through Windows

[ps] Pack the Week 1 runs and the lock file (refuses to overwrite):

```bash
cd /home/xwc/vllm && [ "$(id -un)" = xwc ] && mkdir -p outbox && O=outbox/week1-$(date +%Y%m%d-%H%M%S).tar.gz && [ ! -e "$O" ] && ls -d runs/*/ && tar -czf "$O" -C /home/xwc/vllm runs -C /home/xwc/vllm/mini-sglang_dev results/requirements.lock && (cd outbox && sha256sum "$(basename "$O")" > "$(basename "$O").sha256") && ls -l "$O" "$O.sha256"
```
[Win] Fetch and verify (set `$Name` to the archive name printed above):

```powershell
. { $ErrorActionPreference = 'Stop'
  $Name = 'week1-<stamp>.tar.gz'
  $In = Join-Path $T 'inbox'; New-Item -ItemType Directory -Force -Path $In | Out-Null
  if (Test-Path (Join-Path $In $Name)) { throw "already in $In" }
  scp "${H}:/home/xwc/vllm/outbox/$Name" "${H}:/home/xwc/vllm/outbox/$Name.sha256" $In; if ($LASTEXITCODE -ne 0) { throw 'scp failed' }
  $Want = ((Get-Content (Join-Path $In "$Name.sha256")) -split '\s+')[0]
  $Got = (Get-FileHash -Algorithm SHA256 (Join-Path $In $Name)).Hash.ToLower()
  if ($Want -ne $Got) { throw 'sha256 mismatch' }; "ok: $In\$Name"
}
```
Move `$Name` and `$Name.sha256` to the Mac by your usual method (shared folder, OneDrive, USB),
into `~/Downloads`. [Mac] verify and extract without overwriting anything (`-k`):

```bash
cd ~/Downloads && shasum -a 256 -c week1-<stamp>.tar.gz.sha256 && tar -xzkf week1-<stamp>.tar.gz -C ~/mini-sglang && ls ~/mini-sglang/runs
```
Then ask Claude Code to run the R1, R4 and R5 judges, copy a golden output to
`tests/golden/upstream.json`, and update `results/manifest.csv` and `docs/PROGRESS.md`.

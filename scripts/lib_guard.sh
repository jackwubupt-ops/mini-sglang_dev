# shellcheck shell=bash
# scripts/lib_guard.sh -- shared pre-launch guardrails for serve.sh and run_offline.sh.
# Sourced only by those scripts, after `set -euo pipefail`, check_env.sh and env.sh.

OWNER=xwc
MIN_FREE_GIB=20
DRY_RUN=${DRY_RUN:-0}

die() { echo "REFUSED: $*" >&2; exit 1; }

# Prints the command in dry-run mode; runs it otherwise.
run() {
  if [ "$DRY_RUN" = 1 ]; then echo "DRY-RUN: would run: $*"; else "$@"; fi
}

have() { command -v "$1" >/dev/null 2>&1; }

guard_run_id() {
  local run_id=$1
  [[ "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "RUN_ID '$run_id' must match [A-Za-z0-9][A-Za-z0-9._-]*"
  [ -n "${RUNS:-}" ] || die "RUNS is not set; source scripts/env.sh"
  [ ! -e "$RUNS/$run_id" ] || die "$RUNS/$run_id already exists; choose a new RUN_ID (runs are never overwritten)"
}

guard_scheduler() {
  if have sinfo || have squeue; then
    die "a job scheduler (sinfo/squeue) is installed: submit through it instead (PLAN 4.5 step 1)"
  fi
}

guard_disk() {
  local avail_kib
  avail_kib=$(df -Pk /home/xwc 2>/dev/null | awk 'NR==2 {print $4}') || true
  if [ -z "$avail_kib" ]; then
    [ "$DRY_RUN" = 1 ] && { echo "DRY-RUN: /home/xwc not found, skipping disk check"; return; }
    die "cannot read free space of /home/xwc"
  fi
  [ "$avail_kib" -ge $((MIN_FREE_GIB * 1024 * 1024)) ] \
    || die "less than ${MIN_FREE_GIB} GiB free under /home/xwc ($((avail_kib / 1024 / 1024)) GiB)"
}

# Refuses if GPU index $1 does not exist or runs a compute process of another user.
guard_gpu() {
  local gpu=$1 uuid line pid user
  [[ "$gpu" =~ ^[0-9]+$ ]] || die "GPU must be a single nvidia-smi index, got '$gpu'"
  if ! have nvidia-smi; then
    [ "$DRY_RUN" = 1 ] && { echo "DRY-RUN: nvidia-smi not found, skipping GPU check"; return; }
    die "nvidia-smi not found"
  fi
  uuid=$(nvidia-smi --query-gpu=index,uuid --format=csv,noheader | awk -F', ' -v g="$gpu" '$1 == g {print $2}')
  [ -n "$uuid" ] || die "GPU $gpu does not exist (see scripts/pick_gpu.sh)"
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    pid=${line%%,*}
    user=$(ps -o user= -p "$pid" 2>/dev/null | tr -d ' ') || true
    if [ "$user" != "$OWNER" ]; then
      die "GPU $gpu runs a compute process of another user (pid $pid, user '${user:-unknown}'); pick another GPU"
    fi
    echo "warning: GPU $gpu already runs our own process pid $pid; results from this run may be invalid (PLAN 10.2)"
  done < <(nvidia-smi --query-compute-apps=pid,gpu_uuid --format=csv,noheader | awk -F', ' -v u="$uuid" '$2 == u {print $1 ","}')
}

guard_port() {
  local port=$1 p
  [[ "$port" =~ ^[0-9]+$ ]] && [ "$port" -ge 1024 ] && [ "$port" -le 65534 ] || die "PORT must be 1024..65534"
  if ! have ss; then
    [ "$DRY_RUN" = 1 ] && { echo "DRY-RUN: ss not found, skipping port check"; return; }
    die "ss not found"
  fi
  for p in "$port" $((port + 1)); do
    if [ -n "$(ss -ltnH "sport = :$p")" ]; then die "port $p is in use (the server needs PORT and PORT+1)"; fi
  done
}

# Writes a pre/post-run snapshot: GPU state, compute processes, host load.
snapshot() {
  local out=$1 gpu=$2
  {
    echo "# $(date -Is) user=$(id -un) host=$(hostname) nproc=$(nproc) loadavg=$(cut -d' ' -f1-3 /proc/loadavg)"
    nvidia-smi --query-gpu=index,uuid,name,driver_version,memory.used,memory.total,utilization.gpu,clocks.sm,clocks.mem,temperature.gpu,power.draw --format=csv -i "$gpu"
    nvidia-smi --query-compute-apps=pid,gpu_uuid,used_memory,process_name --format=csv
  } > "$out" 2>&1 || true
}

# Starts "$@" in a new session (its own process group) led by scripts/minisgl_group.sh, which
# also runs the 5 s GPU logger. $1 = run dir, $2 = GPU, $3 = pgid file name, $4 = kind
# (serve | run-offline, also the log name), rest = command. stop_server.sh checks the leader.
launch_group() {
  local run_dir=$1 gpu=$2 pgid_file=$3 kind=$4
  shift 4
  if [ "$DRY_RUN" = 1 ]; then
    echo "DRY-RUN: would run: setsid nohup bash scripts/minisgl_group.sh $kind $run_dir $gpu $pgid_file $* > $run_dir/$kind.log"
    return
  fi
  setsid nohup bash scripts/minisgl_group.sh "$kind" "$run_dir" "$gpu" "$pgid_file" "$@" \
    > "$run_dir/$kind.log" 2>&1 < /dev/null &
  for _ in $(seq 1 50); do
    [ -s "$run_dir/$pgid_file" ] && break
    sleep 0.1
  done
  [ -s "$run_dir/$pgid_file" ] || die "process group did not start; see $run_dir"
  echo "started: PGID $(cat "$run_dir/$pgid_file"), log $run_dir/$kind.log"
}

# Records how the run was started: Git state, MINISGL_* variables and the command.
write_run_meta() {
  local run_dir=$1 gpu=$2
  shift 2
  {
    echo "date: $(date -Is)"
    echo "user: $(id -un)"
    echo "host: $(hostname)"
    echo "gpu_index: $gpu"
    echo "git_sha: $(git rev-parse HEAD)"
    echo "git_branch: $(git branch --show-current)"
    echo "git_dirty: $([ -n "$(git status --porcelain)" ] && echo yes || echo no)"
    echo "python_diff_vs_9a91cfa: $(git diff --quiet 9a91cfa HEAD -- python/ pyproject.toml && echo none || echo yes)"
    echo "env:"
    env | grep -E '^(MINISGL_|CUDA_DEVICE_ORDER=|LOG_LEVEL=)' | sed 's/^/  /' || true
    printf 'command:'; printf ' %q' "$@"; echo
  } > "$run_dir/run_meta.txt"
}

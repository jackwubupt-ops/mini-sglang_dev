#!/usr/bin/env bash
# scripts/serve.sh RUN_ID GPU PORT -- <server flags>
# Starts one Mini-SGLang server in its own process group on nvidia-smi GPU index GPU,
# logging to $RUNS/RUN_ID/. Stop it only with scripts/stop_server.sh RUN_ID.
# Environment variables such as MINISGL_DISABLE_OVERLAP_SCHEDULING=1 are passed through
# and recorded in run_meta.txt. DRY_RUN=1 checks and prints, but launches nothing.
set -euo pipefail
cd "$(dirname "$0")/.."
scripts/check_env.sh
# shellcheck source=scripts/env.sh
source scripts/env.sh
# shellcheck source=scripts/lib_guard.sh
source scripts/lib_guard.sh

usage() { echo "usage: $0 RUN_ID GPU PORT -- <server flags>" >&2; exit 2; }
[ $# -ge 4 ] && [ "$4" = "--" ] || usage
RUN_ID=$1 GPU=$2 PORT=$3
shift 4
READY_TIMEOUT=${READY_TIMEOUT:-900}

has_flag() { local f=$1; shift; local a; for a in "$@"; do [ "$a" = "$f" ] && return 0; done; return 1; }
has_flag --num-pages "$@" || die "--num-pages is required (KV size must not depend on free memory, PLAN 4.2)"
has_flag --cuda-graph-max-bs "$@" || die "--cuda-graph-max-bs is required (PLAN 4.2)"
has_flag --model-path "$@" || die "--model-path is required (use full flag names, PLAN 4.1)"
for bad in --port --model --graph --attn --max-extend-length --cache --shell; do
  has_flag "$bad" "$@" && die "do not pass $bad: serve.sh sets --port; use full flag names (PLAN 4.1)"
done

guard_run_id "$RUN_ID"
guard_scheduler
guard_disk
guard_gpu "$GPU"
guard_port "$PORT"

RUN_DIR=$RUNS/$RUN_ID
CMD=(python -m minisgl "$@" --port "$PORT")
if [ "$DRY_RUN" = 1 ]; then
  echo "DRY-RUN: would create $RUN_DIR, write run_meta.txt and pre_snapshot.txt"
  launch_group "$RUN_DIR" "$GPU" server.pgid serve "${CMD[@]}"
  echo "DRY-RUN: would wait up to ${READY_TIMEOUT}s for http://127.0.0.1:$PORT/v1/models"
  exit 0
fi

mkdir -p "$RUN_DIR"
write_run_meta "$RUN_DIR" "$GPU" "${CMD[@]}"
echo "$PORT" > "$RUN_DIR/port"
snapshot "$RUN_DIR/pre_snapshot.txt" "$GPU"
launch_group "$RUN_DIR" "$GPU" server.pgid serve "${CMD[@]}"

PGID=$(cat "$RUN_DIR/server.pgid")
echo "waiting up to ${READY_TIMEOUT}s for the server on port $PORT ..."
for _ in $(seq 1 $((READY_TIMEOUT / 5))); do
  if curl -sf "http://127.0.0.1:$PORT/v1/models" > /dev/null; then
    echo "ready: run $RUN_ID, PGID $PGID, port $PORT, GPU $GPU"
    grep -m1 "Allocating" "$RUN_DIR/serve.log" || true
    exit 0
  fi
  if [ -z "$(ps -o pid= -g "$PGID" 2>/dev/null)" ]; then
    echo "FAILED: the server exited during start-up; last log lines:" >&2
    tail -n 30 "$RUN_DIR/serve.log" >&2
    exit 1
  fi
  sleep 5
done
echo "TIMEOUT: server not ready after ${READY_TIMEOUT}s; it is still running." >&2
echo "Inspect $RUN_DIR/serve.log, then stop it with: scripts/stop_server.sh $RUN_ID" >&2
exit 1

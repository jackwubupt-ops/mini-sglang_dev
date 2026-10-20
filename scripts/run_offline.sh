#!/usr/bin/env bash
# scripts/run_offline.sh RUN_ID GPU -- <command>
# Runs one offline GPU job (golden outputs, reproducers, GPU pytest) in its own process group
# on nvidia-smi GPU index GPU, logging to $RUNS/RUN_ID/, and waits for it to finish.
# Ctrl-C here does not reach the job (it has its own session); stop it with
# scripts/stop_server.sh RUN_ID. Offline engines must get --num-pages and --cuda-graph-max-bs
# (num_page_override / cuda_graph_max_bs); our Python scripts require both.
# The job can read its run directory from $RUN_DIR. DRY_RUN=1 checks and prints only.
set -euo pipefail
cd "$(dirname "$0")/.."
scripts/check_env.sh
# shellcheck source=scripts/env.sh
source scripts/env.sh
# shellcheck source=scripts/lib_guard.sh
source scripts/lib_guard.sh

usage() { echo "usage: $0 RUN_ID GPU -- <command>" >&2; exit 2; }
[ $# -ge 4 ] && [ "$3" = "--" ] || usage
RUN_ID=$1 GPU=$2
shift 3

guard_run_id "$RUN_ID"
guard_scheduler
guard_disk
guard_gpu "$GPU"

RUN_DIR=$RUNS/$RUN_ID
export RUN_DIR
if [ "$DRY_RUN" = 1 ]; then
  echo "DRY-RUN: would create $RUN_DIR, write run_meta.txt and pre_snapshot.txt"
  launch_group "$RUN_DIR" "$GPU" job.pgid run-offline "$@"
  echo "DRY-RUN: would wait for the job and write post_snapshot.txt"
  exit 0
fi

mkdir -p "$RUN_DIR"
write_run_meta "$RUN_DIR" "$GPU" "$@"
snapshot "$RUN_DIR/pre_snapshot.txt" "$GPU"
launch_group "$RUN_DIR" "$GPU" job.pgid run-offline "$@"
PGID=$(cat "$RUN_DIR/job.pgid")
echo "running; follow with: tail -f $RUN_DIR/run-offline.log   (stop: scripts/stop_server.sh $RUN_ID)"
while [ -n "$(ps -o pid= -g "$PGID" 2>/dev/null)" ]; do sleep 5; done
snapshot "$RUN_DIR/post_snapshot.txt" "$GPU"
RC=$(cat "$RUN_DIR/exit_code" 2>/dev/null || echo "unknown (stopped?)")
echo "finished: run $RUN_ID, exit code $RC, log $RUN_DIR/run-offline.log"
[ "$RC" = 0 ]

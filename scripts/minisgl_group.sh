#!/usr/bin/env bash
# scripts/minisgl_group.sh KIND RUN_DIR GPU PGID_FILE <command...>
# Process-group leader started by serve.sh / run_offline.sh under setsid; never run it directly.
# Records its PGID and start time, runs a 5 s GPU logger in the same group, runs the command
# with CUDA_VISIBLE_DEVICES=GPU, records the exit code and stops its own logger.
# stop_server.sh checks that a group leader runs this file (its name contains "minisgl").
set -uo pipefail
[ $# -ge 5 ] || { echo "usage: $0 KIND RUN_DIR GPU PGID_FILE <command...>" >&2; exit 2; }
_kind=$1 run_dir=$2 gpu=$3 pgid_file=$4
shift 4
ps -o pgid= -p $$ | tr -d ' ' > "$run_dir/$pgid_file"
ps -o lstart= -p $$ > "$run_dir/$pgid_file.lstart"
(
  while true; do
    nvidia-smi --query-gpu=timestamp,index,clocks.sm,clocks.mem,temperature.gpu,power.draw,memory.used,utilization.gpu \
      --format=csv,noheader -i "$gpu" >> "$run_dir/gpu.csv" 2>&1
    nvidia-smi --query-compute-apps=timestamp,pid,gpu_uuid,used_memory --format=csv,noheader >> "$run_dir/gpu_procs.csv" 2>&1
    sleep 5
  done
) &
logger_pid=$!
rc=0
CUDA_VISIBLE_DEVICES=$gpu "$@" || rc=$?
echo "$rc" > "$run_dir/exit_code"
kill "$logger_pid" 2>/dev/null || true
exit "$rc"

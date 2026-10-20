#!/usr/bin/env bash
# scripts/pick_gpu.sh -- read-only: shows every GPU and its compute processes (with owners)
# and lists the GPUs that have no compute process and less than 1 GiB in use.
# Never sets, reserves or kills anything.
set -euo pipefail
command -v nvidia-smi > /dev/null || { echo "nvidia-smi not found" >&2; exit 1; }

echo "== GPUs (nvidia-smi index; scripts use CUDA_DEVICE_ORDER=PCI_BUS_ID so indices match)"
nvidia-smi --query-gpu=index,uuid,name,memory.used,memory.total,utilization.gpu,temperature.gpu --format=csv

echo "== compute processes"
procs=$(nvidia-smi --query-compute-apps=gpu_uuid,pid,used_memory,process_name --format=csv,noheader)
if [ -z "$procs" ]; then
  echo "(none)"
else
  while IFS=', ' read -r uuid pid mem _; do
    user=$(ps -o user= -p "$pid" 2>/dev/null | tr -d ' ') || true
    idx=$(nvidia-smi --query-gpu=index,uuid --format=csv,noheader | awk -F', ' -v u="$uuid" '$2 == u {print $1}')
    echo "gpu $idx  pid $pid  user ${user:-unknown}  mem $mem MiB"
  done <<< "${procs// MiB/}"
fi

echo "== candidates (no compute process, < 1024 MiB used)"
busy=$(echo "$procs" | awk -F', ' 'NF {print $1}' | sort -u)
nvidia-smi --query-gpu=index,uuid,memory.used --format=csv,noheader,nounits | while IFS=', ' read -r idx uuid used; do
  if [ "$used" -lt 1024 ] && ! grep -qx "$uuid" <<< "$busy"; then echo "GPU $idx (${used} MiB used)"; fi
done
command -v sinfo > /dev/null || command -v squeue > /dev/null && echo "NOTE: a job scheduler is installed; use it (PLAN 4.5)."
exit 0

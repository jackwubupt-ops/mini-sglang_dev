#!/usr/bin/env bash
# scripts/stop_server.sh RUN_ID
# Stops the process group started by serve.sh (server.pgid) or run_offline.sh (job.pgid)
# for that run with SIGTERM, after checking that every process in it belongs to xwc and that
# the group leader is scripts/minisgl_group.sh started by us. Asks before sending anything; SIGKILL
# needs a second, separate confirmation. DRY_RUN=1 checks and prints, but sends nothing.
set -euo pipefail
cd "$(dirname "$0")/.."
scripts/check_env.sh
# shellcheck source=scripts/env.sh
source scripts/env.sh
DRY_RUN=${DRY_RUN:-0}
OWNER=$MINISGL_OWNER   # from host_profile.sh via env.sh

die() { echo "REFUSED: $*" >&2; exit 1; }
[ $# -eq 1 ] || { echo "usage: $0 RUN_ID" >&2; exit 2; }
RUN_ID=$1
[[ "$RUN_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "bad RUN_ID '$RUN_ID'"
RUN_DIR=$RUNS/$RUN_ID
[ -d "$RUN_DIR" ] || die "$RUN_DIR does not exist"

PGID_FILE=
for f in server.pgid job.pgid; do [ -s "$RUN_DIR/$f" ] && PGID_FILE=$RUN_DIR/$f; done
[ -n "$PGID_FILE" ] || die "no server.pgid or job.pgid in $RUN_DIR"
PGID=$(tr -d ' \n' < "$PGID_FILE")
[[ "$PGID" =~ ^[0-9]+$ ]] && [ "$PGID" -gt 1 ] || die "bad PGID '$PGID' in $PGID_FILE"
[ "$PGID" != "$(ps -o pgid= -p $$ | tr -d ' ')" ] || die "PGID $PGID is this shell's own group"

members=$(ps -o user=,pid=,args= -g "$PGID" 2>/dev/null || true)
if [ -z "$members" ]; then
  echo "nothing to stop: no process left in group $PGID (run $RUN_ID)"
  exit 0
fi
echo "process group $PGID of run $RUN_ID:"
echo "$members"

# every member must be ours
foreign=$(echo "$members" | awk -v o="$OWNER" '$1 != o')
[ -z "$foreign" ] || die "group $PGID contains processes not owned by $OWNER:
$foreign"
# the leader must be our wrapper and the same process we started (PIDs can be reused)
leader_args=$(ps -o args= -p "$PGID" 2>/dev/null || true)
[ -n "$leader_args" ] || die "group leader $PGID has exited; inspect the members above by hand"
case "$leader_args" in "bash scripts/minisgl_group.sh "*) ;; *) die "group leader is not scripts/minisgl_group.sh: $leader_args";; esac
if [ -s "$PGID_FILE.lstart" ]; then
  [ "$(ps -o lstart= -p "$PGID")" = "$(cat "$PGID_FILE.lstart")" ] \
    || die "leader start time differs from $PGID_FILE.lstart: PID was reused"
fi

if [ "$DRY_RUN" = 1 ]; then
  echo "DRY-RUN: would ask for confirmation, then: kill -TERM -- -$PGID, wait up to 60 s"
  exit 0
fi
read -r -p "Send SIGTERM to process group $PGID? [y/N] " ans
[ "$ans" = y ] || [ "$ans" = Y ] || { echo "aborted, nothing sent"; exit 1; }
kill -TERM -- "-$PGID"
for _ in $(seq 1 60); do
  [ -z "$(ps -o pid= -g "$PGID" 2>/dev/null)" ] && break
  sleep 1
done
survivors=$(ps -o user=,pid=,args= -g "$PGID" 2>/dev/null || true)
if [ -n "$survivors" ]; then
  echo "still running after 60 s:"
  echo "$survivors"
  read -r -p "Type KILL to send SIGKILL to group $PGID (anything else leaves it running): " ans
  if [ "$ans" = KILL ]; then
    kill -KILL -- "-$PGID"
    sleep 2
  else
    echo "left running; re-run this script later"
    exit 1
  fi
fi
if [ -z "$(ps -o pid= -g "$PGID" 2>/dev/null)" ]; then
  echo "stopped: no process left in group $PGID"
else
  echo "WARNING: processes remain in group $PGID" >&2
fi
GPU=$(awk '/^gpu_index:/ {print $2}' "$RUN_DIR/run_meta.txt" 2>/dev/null || true)
if [ -n "$GPU" ] && [ ! -e "$RUN_DIR/post_snapshot.txt" ]; then
  sleep 3
  nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv -i "$GPU" | tee "$RUN_DIR/post_snapshot.txt"
  nvidia-smi --query-compute-apps=pid,gpu_uuid,used_memory,process_name --format=csv >> "$RUN_DIR/post_snapshot.txt"
fi

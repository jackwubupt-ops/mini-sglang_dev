#!/usr/bin/env bash
# scripts/check_env.sh -- exits non-zero unless user, directory and environment are right.
# Execute it (never source it), so a failed check stops a script without closing the terminal.
# DRY_RUN=1 reports every failed check as "would STOP" and exits 0; dry runs never launch,
# kill or write anything, so this is only for testing the scripts away from the server.
PROJ=/home/xwc/vllm/mini-sglang_dev   # project root /home/xwc/vllm (allocated by the admin)
ENVP=/home/xwc/envs/minisgl
fail=0
stop() {
  if [ "${DRY_RUN:-0}" = 1 ]; then echo "DRY-RUN: would STOP: $1"; fail=1
  else echo "STOP: $1"; exit 1; fi
}
[ "$(id -un)" = "xwc" ] || stop "run as xwc, not $(id -un)"
# compare physical paths, in case /home/xwc/vllm is a symlink
PROJ_REAL=$(cd "$PROJ" 2>/dev/null && pwd -P) || PROJ_REAL=$PROJ
case "$(pwd -P)" in "$PROJ_REAL"|"$PROJ_REAL"/*) ;; *) stop "cd into $PROJ first";; esac
[ "${CONDA_PREFIX:-}" = "$ENVP" ] || stop "conda activate $ENVP (never base)"
if [ "$fail" = 0 ]; then echo "ok: xwc, $PROJ, $ENVP"; else echo "DRY-RUN: continuing despite failed checks"; fi

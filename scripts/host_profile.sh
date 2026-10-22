# shellcheck shell=bash
# scripts/host_profile.sh -- which machine this checkout runs on. Sourced (never executed) by
# check_env.sh, env.sh, lib_guard.sh, stop_server.sh, check_gh_tunnel.sh and sync_from_bundle.sh.
#   MINISGL_HOST=ps (default): the shared server ps; user xwc, root /home/xwc/vllm,
#     repo /home/xwc/vllm/mini-sglang_dev, Conda env /home/xwc/envs/minisgl (Shared Server Usage Policy).
#   MINISGL_HOST=rental: a rented single-tenant GPU machine; the root is the directory containing
#     this checkout, the owner is the current user, the Conda env is <root>/envs/minisgl.
#     Refused on ps, so the shared-server guards can never be relaxed there by mistake.
# Sets MINISGL_OWNER, MINISGL_ROOT, MINISGL_PROJ, MINISGL_ENV (read by the scripts that source it).
# shellcheck disable=SC2034,SC2317
_minisgl_repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
case "${MINISGL_HOST:-ps}" in
  ps)
    MINISGL_OWNER=xwc
    MINISGL_ROOT=/home/xwc/vllm
    MINISGL_PROJ=/home/xwc/vllm/mini-sglang_dev
    MINISGL_ENV=/home/xwc/envs/minisgl
    ;;
  rental)
    if [ "$(hostname)" = ps ]; then
      echo "STOP: MINISGL_HOST=rental is not allowed on ps; unset it" >&2
      return 1 2> /dev/null || exit 1
    fi
    MINISGL_OWNER=$(id -un)
    MINISGL_PROJ=$_minisgl_repo
    MINISGL_ROOT=$(dirname "$_minisgl_repo")
    MINISGL_ENV=$MINISGL_ROOT/envs/minisgl
    ;;
  *)
    echo "STOP: unknown MINISGL_HOST='${MINISGL_HOST}' (use ps or rental)" >&2
    return 1 2> /dev/null || exit 1
    ;;
esac
MINISGL_HOST=${MINISGL_HOST:-ps}
export MINISGL_HOST

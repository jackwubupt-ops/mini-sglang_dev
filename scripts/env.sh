# shellcheck shell=bash
# scripts/env.sh -- source after check_env.sh succeeds; exports only
# shellcheck source=scripts/host_profile.sh
source "$(dirname "${BASH_SOURCE[0]}")/host_profile.sh" || return 1
export HF_HOME=$MINISGL_ROOT/cache/hf
export TVM_FFI_CACHE_DIR=$MINISGL_ROOT/cache/tvm-ffi
export FLASHINFER_WORKSPACE_BASE=$MINISGL_ROOT/cache/flashinfer
export RUNS=$MINISGL_ROOT/runs
# GPU indices given to our scripts are nvidia-smi indices; make CUDA number GPUs the same way.
export CUDA_DEVICE_ORDER=PCI_BUS_ID

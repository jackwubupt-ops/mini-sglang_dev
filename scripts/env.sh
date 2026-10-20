# shellcheck shell=bash
# scripts/env.sh -- source after check_env.sh succeeds; exports only
export HF_HOME=/home/xwc/my_projects/cache/hf
export TVM_FFI_CACHE_DIR=/home/xwc/my_projects/cache/tvm-ffi
export FLASHINFER_WORKSPACE_BASE=/home/xwc/my_projects/cache/flashinfer
export RUNS=/home/xwc/my_projects/runs
# GPU indices given to our scripts are nvidia-smi indices; make CUDA number GPUs the same way.
export CUDA_DEVICE_ORDER=PCI_BUS_ID

# shellcheck shell=bash
# scripts/env.sh -- source after check_env.sh succeeds; exports only
export HF_HOME=/home/xwc/vllm/cache/hf
export TVM_FFI_CACHE_DIR=/home/xwc/vllm/cache/tvm-ffi
export FLASHINFER_WORKSPACE_BASE=/home/xwc/vllm/cache/flashinfer
export RUNS=/home/xwc/vllm/runs
# GPU indices given to our scripts are nvidia-smi indices; make CUDA number GPUs the same way.
export CUDA_DEVICE_ORDER=PCI_BUS_ID

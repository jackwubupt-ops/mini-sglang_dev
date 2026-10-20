"""Checkpoint: can Mini-SGLang's own C++/CUDA kernels be compiled with the installed toolkit?

    python scripts/compat/jit_gate.py

Compile-and-load only (no GPU memory, no kernel launch), through the same tvm-ffi loaders the
engine uses (python/minisgl/kernel). Builds into $TVM_FFI_CACHE_DIR. Reports, per module, PASS or
FAIL with the first compiler error lines. Exit 0 only if every module passes.
Modules: store.cu / index.cu (JIT, nvcc), pynccl.cu (AOT, nvcc + -lnccl), radix.cpp / tensor.cpp
(AOT, host C++ only).
"""

from __future__ import annotations

import re
import sys
import traceback


def first_errors(text: str, n: int = 8) -> str:
    lines = [ln for ln in text.splitlines() if re.search(r"error|fatal|unsupported|not defined", ln, re.I)]
    return "\n".join(lines[:n]) or text[-1500:]


def main() -> None:
    from minisgl.kernel.index import _jit_index_module
    from minisgl.kernel.pynccl import _load_nccl_module
    from minisgl.kernel.radix import _load_radix_module
    from minisgl.kernel.store import _jit_store_module
    from minisgl.kernel.tensor import _load_test_tensor_module

    # element sizes in bytes of one row: Qwen3-0.6B KV row (8 heads x 128 x bf16) and hidden row
    modules = [
        ("radix.cpp (host C++20)", _load_radix_module),
        ("tensor.cpp (host C++20)", _load_test_tensor_module),
        ("store.cu (nvcc JIT)", lambda: _jit_store_module(2048)),
        ("index.cu (nvcc JIT)", lambda: _jit_index_module(2048)),
        ("pynccl.cu (nvcc AOT, -lnccl)", _load_nccl_module),
    ]
    failed = 0
    for name, load in modules:
        try:
            load()
            print(f"PASS {name}")
        except Exception as e:  # report every module, do not stop at the first
            failed += 1
            msg = "".join(traceback.format_exception_only(type(e), e))
            print(f"FAIL {name}\n{first_errors(str(e) + msg)}\n")
    print("RESULT:", "all modules compile" if not failed else f"{failed} module(s) failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

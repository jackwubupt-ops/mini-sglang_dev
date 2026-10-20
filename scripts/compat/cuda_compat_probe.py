"""Probe whether a libcuda.so can initialise on this machine (CUDA forward-compatibility test).

    python scripts/compat/cuda_compat_probe.py                     # the system driver
    python scripts/compat/cuda_compat_probe.py --libcuda /home/xwc/envs/cudacompat-probe/cuda-compat/libcuda.so.1

Calls only cuInit(0) and cuDriverGetVersion through ctypes: no context, no memory, no kernel.
Error 804 (CUDA_ERROR_COMPAT_NOT_SUPPORTED_ON_DEVICE) means the forward-compatibility driver
refuses this GPU (expected for GeForce cards).
"""

from __future__ import annotations

import argparse
import ctypes

NAMES = {0: "CUDA_SUCCESS", 3: "NOT_INITIALIZED", 35: "INSUFFICIENT_DRIVER", 100: "NO_DEVICE",
         803: "SYSTEM_DRIVER_MISMATCH", 804: "COMPAT_NOT_SUPPORTED_ON_DEVICE"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--libcuda", default="libcuda.so.1")
    args = parser.parse_args()
    try:
        lib = ctypes.CDLL(args.libcuda)
        lib.cuDriverGetVersion, lib.cuInit  # noqa: B018 -- fail early if symbols are missing
    except (OSError, AttributeError) as e:
        print(f"libcuda: {args.libcuda}\nRESULT: cannot load ({e})")
        return
    version = ctypes.c_int(0)
    rc_ver = lib.cuDriverGetVersion(ctypes.byref(version))
    rc_init = lib.cuInit(0)
    v = version.value
    print(f"libcuda: {args.libcuda}")
    print(f"cuDriverGetVersion: rc={rc_ver} version={v // 1000}.{(v % 1000) // 10}")
    print(f"cuInit: rc={rc_init} ({NAMES.get(rc_init, 'see CUDA driver API error codes')})")
    print("RESULT:", "usable" if rc_init == 0 else "NOT usable")


if __name__ == "__main__":
    main()

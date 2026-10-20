"""Checkpoint: PyTorch sees the GPU and runs a bf16 matmul correctly (tiny, < 100 MiB).

    scripts/run_offline.sh cp-torch-<time> 6 -- python scripts/compat/torch_check.py

Writes $RUN_DIR/torch_check.json; exit 1 on any failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from offline_common import run_dir, write_json_once  # noqa: E402


def main() -> None:
    out = run_dir()
    import torch

    info = {
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "cudnn": torch.backends.cudnn.version(),
        "cuda_available": torch.cuda.is_available(),
    }
    ok = info["cuda_available"]
    if ok:
        info["device_count"] = torch.cuda.device_count()
        info["device"] = torch.cuda.get_device_name(0)
        info["capability"] = torch.cuda.get_device_capability(0)
        a = torch.randn(512, 512, device="cuda", dtype=torch.bfloat16)
        b = torch.randn(512, 512, device="cuda", dtype=torch.bfloat16)
        err = ((a @ b).float() - a.float() @ b.float()).abs().max().item()
        info["bf16_matmul_max_abs_err"] = err
        ok = info["device_count"] == 1 and err < 1.0
        torch.cuda.synchronize()
    info["status"] = "PASS" if ok else "FAIL"
    for k, v in info.items():
        print(f"{k}: {v}")
    write_json_once(out / "torch_check.json", info)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

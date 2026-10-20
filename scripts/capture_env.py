"""Record the software and hardware environment of a run as JSON.

    python scripts/capture_env.py --gpu GPU --out $RUNS/<run_id>/env.json

Writes only to --out, which must not exist. Never allocates GPU memory: torch is imported
but CUDA is not initialised; GPU facts come from nvidia-smi.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as md
import json
import os
import platform
import socket
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGES = [
    "torch",
    "flashinfer-python",
    "sgl_kernel",
    "sgl-kernel",
    "transformers",
    "apache-tvm-ffi",
    "torch-c-dlpack-ext",
    "triton",
    "quack-kernels",
    "minisgl",
]


def sh(*cmd: str) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=REPO)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def gpu_info(gpu: int | None) -> dict:
    fields = "index,uuid,name,driver_version,memory.total,pci.bus_id,clocks.max.sm,power.limit"
    rows = sh("nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader")
    gpus = []
    for line in (rows or "").splitlines():
        values = [v.strip() for v in line.split(",")]
        gpus.append(dict(zip(fields.split(","), values)))
    header = sh("nvidia-smi") or ""
    cuda_driver = None
    for token in header.split("|"):
        if "CUDA Version" in token:
            cuda_driver = token.split("CUDA Version:")[-1].strip()
    chosen = [g for g in gpus if gpu is not None and g.get("index") == str(gpu)]
    return {"chosen": chosen[0] if chosen else None, "all": gpus, "driver_cuda_version": cuda_driver}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu", type=int, default=None, help="nvidia-smi index used by the run")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        sys.exit(f"REFUSED: {args.out} exists; env files are never overwritten")

    versions = {}
    for name in PACKAGES:
        try:
            versions[name] = md.version(name)
        except md.PackageNotFoundError:
            pass
    torch_info = {}
    try:
        import torch

        torch_info = {"version": torch.__version__, "cuda": torch.version.cuda}
    except ImportError:
        pass

    env = {
        "host": socket.gethostname(),
        "user": os.environ.get("USER"),
        "python": sys.version,
        "python_prefix": sys.prefix,
        "platform": platform.platform(),
        "git": {
            "sha": sh("git", "rev-parse", "HEAD"),
            "branch": sh("git", "branch", "--show-current"),
            "dirty": bool(sh("git", "status", "--porcelain")),
            "python_identical_to_9a91cfa": subprocess.run(
                ["git", "diff", "--quiet", "9a91cfa", "HEAD", "--", "python/", "pyproject.toml"],
                cwd=REPO,
            ).returncode
            == 0,
        },
        "requirements_lock_sha256": sha256(REPO / "results" / "requirements.lock"),
        "packages": versions,
        "torch": torch_info,
        "nvcc": sh("nvcc", "--version"),
        "gpu": gpu_info(args.gpu),
        "env_vars": {
            k: v
            for k, v in os.environ.items()
            if k.startswith("MINISGL_")
            or k
            in (
                "CUDA_VISIBLE_DEVICES",
                "CUDA_DEVICE_ORDER",
                "HF_HOME",
                "TVM_FFI_CACHE_DIR",
                "FLASHINFER_WORKSPACE_BASE",
            )
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "x") as f:
        json.dump(env, f, indent=2)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

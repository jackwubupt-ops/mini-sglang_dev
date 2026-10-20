"""Shared helpers for offline GPU scripts (golden outputs, R4, later R2 and ID tests).

Every offline script:
  * runs under scripts/run_offline.sh, which exports RUN_DIR=$RUNS/<run_id>;
  * writes only inside RUN_DIR (refuses anything else and never overwrites);
  * passes num_page_override and cuda_graph_max_bs explicitly (PLAN 4.2).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parent.parent


def add_engine_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model-path", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--num-pages", type=int, required=True, help="num_page_override")
    parser.add_argument("--cuda-graph-max-bs", type=int, required=True, help="0 disables graphs")
    parser.add_argument("--max-prefill-length", type=int, default=8192, help="budget B")
    parser.add_argument("--max-running-requests", type=int, default=256)
    parser.add_argument("--cache-type", default="naive", choices=["naive", "radix"])
    parser.add_argument("--attention-backend", default="fi")
    parser.add_argument("--page-size", type=int, default=1)


def engine_kwargs(args: argparse.Namespace) -> Dict[str, Any]:
    return dict(
        num_page_override=args.num_pages,
        cuda_graph_max_bs=args.cuda_graph_max_bs,
        max_extend_tokens=args.max_prefill_length,
        max_running_req=args.max_running_requests,
        cache_type=args.cache_type,
        attention_backend=args.attention_backend,
        page_size=args.page_size,
    )


def run_dir() -> Path:
    """The run directory created by run_offline.sh; refuses to run without it."""
    runs, rd = os.environ.get("RUNS"), os.environ.get("RUN_DIR")
    if not runs or not rd:
        sys.exit("REFUSED: run through scripts/run_offline.sh (RUNS and RUN_DIR must be set)")
    path = Path(rd).resolve()
    if path.parent != Path(runs).resolve() or not path.is_dir():
        sys.exit(f"REFUSED: RUN_DIR {rd} is not a directory directly under RUNS={runs}")
    return path


def write_json_once(path: Path, data: Any) -> None:
    with open(path, "x") as f:
        json.dump(data, f, indent=1)
    print(f"wrote {path}")


def overlap_enabled() -> bool:
    from minisgl.env import ENV

    return not bool(ENV.DISABLE_OVERLAP_SCHEDULING)


def tokens_sha256(ids: List[int]) -> str:
    return hashlib.sha256(json.dumps(ids).encode()).hexdigest()


def run_meta(args: argparse.Namespace) -> Dict[str, Any]:
    def git(*a: str) -> str:
        return subprocess.run(["git", *a], capture_output=True, text=True, cwd=REPO).stdout.strip()

    return {
        "git_sha": git("rev-parse", "HEAD"),
        "git_branch": git("branch", "--show-current"),
        "git_dirty": bool(git("status", "--porcelain")),
        "overlap": overlap_enabled(),
        "argv": sys.argv,
        "args": vars(args),
    }


def generate(llm, prompts, sampling_params) -> List[List[int]]:
    outputs = llm.generate(prompts, sampling_params)
    return [list(map(int, o["token_ids"])) for o in outputs]

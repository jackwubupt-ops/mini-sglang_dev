"""Reproducer R1 for H1 (PLAN 5.3): streamed token count with overlap on and off.

Client only; no GPU. Start the server first (Qwen3-0.6B), once per mode:
  scripts/serve.sh r1-on-<time> GPU P -- --model-path Qwen/Qwen3-0.6B ... (see docs/PROGRESS.md)
  MINISGL_DISABLE_OVERLAP_SCHEDULING=1 scripts/serve.sh r1-off-<time> GPU P -- ...
then, in the same checked environment:
  python scripts/repro/r1_client.py --run-id r1-on-<time>
32 streaming requests, ignore_eos=true, max_tokens in {1, 2, 8, 64} (8 each). Writes
$RUNS/<run-id>/r1_client.json with the SSE events per request. Judge locally with r1_judge.py.
"""

from __future__ import annotations

import argparse

from sse_client import overlap_from_run_meta, run_all, server_run_dir, write_once

MAX_TOKENS = [1, 2, 8, 64]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="serve.sh RUN_ID of the running server")
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()
    rd = server_run_dir(args.run_id)
    port = int((rd / "port").read_text())
    jobs = [
        {
            "i": i,
            "content": f"Write a long story about the number {i}.",
            "max_tokens": MAX_TOKENS[i % 4],
            "ignore_eos": True,
        }
        for i in range(32)
    ]
    results = run_all(port, jobs, args.concurrency)
    write_once(
        rd / "r1_client.json",
        {"run_id": args.run_id, "overlap": overlap_from_run_meta(rd), "results": results},
    )
    for t in MAX_TOKENS:
        counts = sorted({r.get("null_events") for r in results if r["max_tokens"] == t}, key=str)
        print(f"max_tokens={t}: null-finish events per request {counts}")


if __name__ == "__main__":
    main()

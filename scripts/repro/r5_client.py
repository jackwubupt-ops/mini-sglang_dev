"""Reproducer R5 for H5 (PLAN 5.3): detokenizer decode_map leak per EOS-terminated request.

Needs the `instr` branch. Client only; no GPU. Start the server with the map logger on:
  MINISGL_DEBUG_DETOK_MAP=1 scripts/serve.sh r5-<time> GPU P -- --model-path Qwen/Qwen3-0.6B ...
then:
  python scripts/repro/r5_client.py --run-id r5-<time>
Sends 200 streaming yes/no requests (ignore_eos=false, max_tokens=64), then stays idle for
--idle seconds so the 5 s logger records the map size at idle. Writes
$RUNS/<run-id>/r5_client.json. Stop the server afterwards, then judge locally with
r5_judge.py (it reads serve.log from the same run directory).
"""

from __future__ import annotations

import argparse
import time

from sse_client import overlap_from_run_meta, run_all, server_run_dir, write_once

MAX_TOKENS = 64


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="serve.sh RUN_ID of the running server")
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--idle", type=float, default=30.0)
    args = parser.parse_args()
    rd = server_run_dir(args.run_id)
    port = int((rd / "port").read_text())
    jobs = [
        {
            "i": i,
            "content": f"Answer with one word: yes or no. Is {i} an even number?",
            "max_tokens": MAX_TOKENS,
            "ignore_eos": False,
        }
        for i in range(args.n)
    ]
    t_start = time.time()
    results = run_all(port, jobs, args.concurrency)
    t_idle = time.time()
    print(f"all {args.n} requests returned; idle for {args.idle:.0f} s ...")
    time.sleep(args.idle)
    write_once(
        rd / "r5_client.json",
        {
            "run_id": args.run_id,
            "overlap": overlap_from_run_meta(rd),
            "t_start": t_start,
            "t_idle": t_idle,
            "t_end": time.time(),
            "results": [{k: r.get(k) for k in ("i", "max_tokens", "null_events", "done", "error")} for r in results],
        },
    )


if __name__ == "__main__":
    main()

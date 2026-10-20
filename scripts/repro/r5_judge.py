"""Judge R5 (H5) from one r5 run directory; run locally after copying the run down.

    python scripts/repro/r5_judge.py runs/<r5-run> [--out results/repro/R5.json]

Reads r5_client.json and the MINISGL_DEBUG_DETOK_MAP lines in serve.log. The idle map size is
the last logged size between t_idle + 6 s and t_end (at least one full 5 s logger tick after
the last reply). A request counts as EOS-terminated if it streamed fewer than max_tokens - 1
events (robust to H1, which removes one event from length-terminated requests).
confirmed      : idle size > 0 and within [0.5, 1.5] x the number of EOS-terminated requests.
not reproduced : idle size == 0.
inconclusive   : anything else (no samples, client errors, size not proportional).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

LINE = re.compile(r"MINISGL_DEBUG_DETOK_MAP t=([0-9.]+) size=(\d+)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--out", type=Path, default=Path("results/repro/R5.json"))
    args = parser.parse_args()
    client_path, log_path = args.run_dir / "r5_client.json", args.run_dir / "serve.log"
    client = json.loads(client_path.read_text())
    samples = [(float(t), int(s)) for t, s in LINE.findall(log_path.read_text(errors="replace"))]
    idle = [s for t, s in samples if client["t_idle"] + 6 <= t <= client["t_end"]]
    errors = [r for r in client["results"] if r.get("error") or not r.get("done")]
    n_eos = sum(1 for r in client["results"] if not r.get("error") and r["null_events"] < r["max_tokens"] - 1)
    idle_size = idle[-1] if idle else None

    if idle_size is None or errors:
        status = "inconclusive"
    elif idle_size == 0:
        status = "not reproduced"
    elif n_eos and 0.5 * n_eos <= idle_size <= 1.5 * n_eos:
        status = "confirmed"
    else:
        status = "inconclusive"
    verdict = {
        "id": "R5",
        "hypothesis": "H5",
        "status": status,
        "overlap": client["overlap"],
        "n_requests": len(client["results"]),
        "n_eos_terminated": n_eos,
        "idle_map_size": idle_size,
        "idle_samples": idle,
        "max_map_size": max((s for _, s in samples), default=None),
        "client_errors": len(errors),
        "inputs": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (client_path, log_path)},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "x") as f:
        json.dump(verdict, f, indent=1)
    print(f"R5: {status} (idle map size {idle_size}, {n_eos} EOS-terminated); wrote {args.out}")


if __name__ == "__main__":
    main()

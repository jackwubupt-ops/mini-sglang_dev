"""Judge R1 (H1) from two r1_client.py results; run locally after copying the runs down.

    python scripts/repro/r1_judge.py runs/<r1-on>/r1_client.json runs/<r1-off>/r1_client.json \
        [--out results/repro/R1.json]

confirmed      : overlap on gives T - 1 events for every request with T >= 2, and overlap off
                 gives T events for every request (PLAN 5.3).
not reproduced : both modes give T events for every request.
inconclusive   : anything else (errors, missing [DONE], mixed counts); details are recorded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("on", type=Path)
    parser.add_argument("off", type=Path)
    parser.add_argument("--out", type=Path, default=Path("results/repro/R1.json"))
    args = parser.parse_args()
    on, off = json.loads(args.on.read_text()), json.loads(args.off.read_text())
    if on["overlap"] is not True or off["overlap"] is not False:
        raise SystemExit("REFUSED: first file must be overlap on, second overlap off")

    def table(d):
        return [
            {"i": r["i"], "T": r["max_tokens"], "events": r.get("null_events"), "error": r.get("error"),
             "done": r.get("done")}
            for r in d["results"]
        ]

    t_on, t_off = table(on), table(off)
    broken = [r for r in t_on + t_off if r["error"] or not r["done"]]
    off_ok = all(r["events"] == r["T"] for r in t_off)
    on_minus_one = all(r["events"] == r["T"] - 1 for r in t_on if r["T"] >= 2)
    on_equal = all(r["events"] == r["T"] for r in t_on)
    if not broken and off_ok and on_minus_one:
        status = "confirmed"
    elif not broken and off_ok and on_equal:
        status = "not reproduced"
    else:
        status = "inconclusive"
    verdict = {
        "id": "R1",
        "hypothesis": "H1",
        "status": status,
        "on": t_on,
        "off": t_off,
        "broken_requests": broken,
        "inputs": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.on, args.off)},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "x") as f:
        json.dump(verdict, f, indent=1)
    print(f"R1: {status}; wrote {args.out}")


if __name__ == "__main__":
    main()

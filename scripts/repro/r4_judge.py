"""Judge R4 (H4) from two r4_offline.py runs; run locally after copying the runs down.

    python scripts/repro/r4_judge.py runs/<r4-on>/outputs.json runs/<r4-off>/outputs.json \
        [--out results/repro/R4.json]

A request is EOS-terminated if its overlap-off output is shorter than max_tokens.
confirmed      : every EOS-terminated request's overlap-on output equals the overlap-off
                 output plus exactly one trailing token, and nothing else differs (PLAN 5.3).
not reproduced : every overlap-on output equals its overlap-off output.
inconclusive   : anything else, or no EOS-terminated request (details are recorded).
Length-terminated differences are reported separately (they would point at H1, not H4).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("on", type=Path)
    parser.add_argument("off", type=Path)
    parser.add_argument("--out", type=Path, default=Path("results/repro/R4.json"))
    args = parser.parse_args()
    on, off = json.loads(args.on.read_text()), json.loads(args.off.read_text())
    if on["meta"]["overlap"] is not True or off["meta"]["overlap"] is not False:
        raise SystemExit("REFUSED: first file must be overlap on, second overlap off (see meta.overlap)")
    if on["meta"]["git_sha"] != off["meta"]["git_sha"]:
        raise SystemExit("REFUSED: the two runs come from different commits")

    eos_plus_one, eos_equal, eos_other, len_diff = [], [], [], []
    n_eos = 0
    if len(on["results"]) != len(off["results"]):
        raise SystemExit("REFUSED: the two runs have different numbers of requests")
    for a, b in zip(on["results"], off["results"]):
        assert a["prompt_sha256"] == b["prompt_sha256"], "prompt sets differ"
        ta, tb = a["token_ids"], b["token_ids"]
        if len(tb) < b["max_tokens"]:
            n_eos += 1
            if ta == tb:
                eos_equal.append(a["i"])
            elif len(ta) == len(tb) + 1 and ta[:-1] == tb:
                eos_plus_one.append({"i": a["i"], "extra_token": ta[-1]})
            else:
                eos_other.append({"i": a["i"], "on_len": len(ta), "off_len": len(tb)})
        elif ta != tb:
            len_diff.append({"i": a["i"], "on_len": len(ta), "off_len": len(tb)})

    if n_eos and len(eos_plus_one) == n_eos and not eos_other:
        status = "confirmed"
    elif n_eos and len(eos_equal) == n_eos and not len_diff:
        status = "not reproduced"
    else:
        status = "inconclusive"
    verdict = {
        "id": "R4",
        "hypothesis": "H4",
        "status": status,
        "n_requests": len(on["results"]),
        "n_eos_terminated": n_eos,
        "eos_plus_one": eos_plus_one,
        "eos_equal": eos_equal,
        "eos_other_difference": eos_other,
        "length_terminated_differences": len_diff,
        "inputs": {
            str(args.on): {"sha256": sha256(args.on), "git_sha": on["meta"]["git_sha"]},
            str(args.off): {"sha256": sha256(args.off), "git_sha": off["meta"]["git_sha"]},
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "x") as f:
        json.dump(verdict, f, indent=1)
    print(f"R4: {status} ({len(eos_plus_one)}/{n_eos} EOS-terminated requests +1 token); wrote {args.out}")


if __name__ == "__main__":
    main()

"""Compare two outputs.json files token by token (golden runs, ID tests).

    python scripts/compare_outputs.py A/outputs.json B/outputs.json

Exit 0 only if both have the same prompts (by sha256) and bitwise-identical token ids.
Read-only; prints the first differing position of every differing request.
"""

from __future__ import annotations

import json
import sys


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    a, b = (json.load(open(p)) for p in sys.argv[1:])
    ra, rb = a["results"], b["results"]
    if len(ra) != len(rb):
        sys.exit(f"DIFFERENT: {len(ra)} vs {len(rb)} requests")
    n_diff = 0
    for x, y in zip(ra, rb):
        if x["prompt_sha256"] != y["prompt_sha256"]:
            sys.exit(f"DIFFERENT PROMPTS at request {x['i']}: the prompt sets do not match")
        tx, ty = x["token_ids"], y["token_ids"]
        if tx != ty:
            n_diff += 1
            k = next((j for j, (p, q) in enumerate(zip(tx, ty)) if p != q), min(len(tx), len(ty)))
            print(f"request {x['i']}: first difference at output position {k} (lengths {len(tx)} vs {len(ty)})")
    for key in ("git_sha", "overlap"):
        if a["meta"].get(key) != b["meta"].get(key):
            print(f"note: meta.{key} differs: {a['meta'].get(key)} vs {b['meta'].get(key)}")
    if n_diff:
        print(f"DIFFERENT: {n_diff} of {len(ra)} requests")
        sys.exit(1)
    print(f"IDENTICAL: {len(ra)} requests, {sum(len(r['token_ids']) for r in ra)} tokens")


if __name__ == "__main__":
    main()

"""Golden outputs (PLAN 4.5): offline LLM, greedy, 64 fixed prompts, ignore_eos=True.

Prompt set (fixed seed, token ids built from synthetic text so prompts share no prefix):
  8 of 1-16 tokens, 32 of 100-2,000, 16 of 4,097-8,192, 8 longer than B (B+1 .. B+4,096),
  each with a fixed max_tokens from {16, 32, 64, 128}.

Run twice through run_offline.sh; pass if compare_outputs.py reports bitwise-identical ids:
  scripts/run_offline.sh golden-a-<time> GPU -- python scripts/golden_outputs.py \
      --num-pages 65536 --cuda-graph-max-bs 64
Writes $RUN_DIR/outputs.json. After both runs pass, copy one locally to tests/golden/upstream.json.
"""

from __future__ import annotations

import argparse
import random
from typing import List, Tuple

from offline_common import (
    add_engine_args,
    engine_kwargs,
    generate,
    run_dir,
    run_meta,
    tokens_sha256,
    write_json_once,
)

SEED = 20261020
WORDS = (
    "the of and to in is was for on that with as by at from it his an were are which this be "
    "or has had first one their its new after but who not they have her she two been other when "
    "there all during into school time may years more most only over city some world would where "
    "later up such used many can state about national out known university united then made "
    "river music system house north south east west water light paper number model engine table "
    "garden window market station village bridge mountain island forest summer winter morning "
    "evening history science language family country library teacher student question answer"
).split()


def prompt_spec(budget: int) -> List[Tuple[int, int]]:
    """[(prompt_len, max_tokens)] in a fixed order."""
    rng = random.Random(SEED)
    lens = (
        [rng.randint(1, 16) for _ in range(8)]
        + [rng.randint(100, 2000) for _ in range(32)]
        + [rng.randint(4097, 8192) for _ in range(16)]
        + [rng.randint(budget + 1, budget + 4096) for _ in range(8)]
    )
    return [(n, rng.choice([16, 32, 64, 128])) for n in lens]


def build_prompt(tokenizer, rng: random.Random, length: int) -> List[int]:
    ids: List[int] = []
    while len(ids) < length:
        text = " ".join(rng.choice(WORDS) for _ in range(256))
        ids.extend(tokenizer.encode(" " + text, add_special_tokens=False))
    return ids[:length]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_engine_args(parser)
    args = parser.parse_args()
    out_dir = run_dir()

    import torch
    from minisgl.core import SamplingParams
    from minisgl.llm import LLM

    llm = LLM(args.model_path, dtype=torch.bfloat16, **engine_kwargs(args))
    spec = prompt_spec(args.max_prefill_length)
    rng = random.Random(SEED + 1)
    prompts = [build_prompt(llm.tokenizer, rng, n) for n, _ in spec]
    params = [SamplingParams(temperature=0.0, ignore_eos=True, max_tokens=m) for _, m in spec]
    outputs = generate(llm, prompts, params)

    results = [
        {
            "i": i,
            "prompt_len": len(p),
            "prompt_sha256": tokens_sha256(p),
            "max_tokens": m,
            "token_ids": o,
        }
        for i, (p, (_, m), o) in enumerate(zip(prompts, spec, outputs))
    ]
    bad = [r["i"] for r in results if len(r["token_ids"]) != r["max_tokens"]]
    meta = run_meta(args) | {"seed": SEED, "n_prompts": len(results), "length_mismatch": bad}
    write_json_once(out_dir / "outputs.json", {"meta": meta, "results": results})
    if bad:
        print(f"note: {len(bad)} requests returned != max_tokens tokens: {bad}")


if __name__ == "__main__":
    main()

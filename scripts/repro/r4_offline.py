"""Reproducer R4 for H4 (PLAN 5.3): extra token after EOS with overlap scheduling.

Offline LLM, greedy, ignore_eos=False, 64 early-terminating prompts, max_tokens=64.
Run twice through run_offline.sh, once with overlap on and once with it off:
  scripts/run_offline.sh r4-on-<time>  GPU -- python scripts/repro/r4_offline.py \
      --num-pages 65536 --cuda-graph-max-bs 64
  MINISGL_DISABLE_OVERLAP_SCHEDULING=1 scripts/run_offline.sh r4-off-<time> GPU -- \
      python scripts/repro/r4_offline.py --num-pages 65536 --cuda-graph-max-bs 64
Each writes $RUN_DIR/outputs.json (meta.overlap records the mode). Judge locally with
scripts/repro/r4_judge.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from offline_common import (  # noqa: E402
    add_engine_args,
    engine_kwargs,
    generate,
    run_dir,
    run_meta,
    tokens_sha256,
    write_json_once,
)

MAX_TOKENS = 64
SUBJECTS = [
    "the sky blue on a clear day", "fire cold", "water wet", "a cat a mammal", "the sun a planet",
    "ice hot", "a square round", "Paris in France", "a fish able to fly by default",
    "two larger than one", "snow white", "a week seven days long",
]


def questions() -> List[str]:
    qs = [f"Answer with one word: yes or no. Is {s}?" for s in SUBJECTS]
    qs += [f"Answer with one word: yes or no. Is {n} an even number?" for n in range(1, 27)]
    qs += [f"Answer with one word: yes or no. Is {n} greater than 50?" for n in range(10, 140, 5)][:26]
    assert len(qs) == 64, len(qs)
    return qs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_engine_args(parser)
    args = parser.parse_args()
    out_dir = run_dir()

    import torch
    from minisgl.core import SamplingParams
    from minisgl.llm import LLM

    llm = LLM(args.model_path, dtype=torch.bfloat16, **engine_kwargs(args))
    prompts = [
        llm.tokenizer.apply_chat_template(
            [{"role": "user", "content": q}],
            add_generation_prompt=True,
            enable_thinking=False,
            tokenize=True,
        )
        for q in questions()
    ]
    params = SamplingParams(temperature=0.0, ignore_eos=False, max_tokens=MAX_TOKENS)
    outputs = generate(llm, prompts, params)
    results = [
        {"i": i, "prompt_sha256": tokens_sha256(p), "max_tokens": MAX_TOKENS, "token_ids": o}
        for i, (p, o) in enumerate(zip(prompts, outputs))
    ]
    meta = run_meta(args) | {"eos_token_id": llm.eos_token_id}
    write_json_once(out_dir / "outputs.json", {"meta": meta, "results": results})


if __name__ == "__main__":
    main()

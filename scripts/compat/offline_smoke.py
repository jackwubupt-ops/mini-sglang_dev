"""Checkpoint: minimal end-to-end run of the offline engine (Qwen3-0.6B, 2 greedy prompts).

    scripts/run_offline.sh cp-smoke-<time> 6 -- python scripts/compat/offline_smoke.py

Small, fixed KV cache (--num-pages 16384) and CUDA graphs up to batch 8, so it needs ~3 GiB.
Writes $RUN_DIR/offline_smoke.json; exit 1 if any output is empty.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from offline_common import add_engine_args, engine_kwargs, run_dir, run_meta, write_json_once  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_engine_args(parser)
    parser.set_defaults(num_pages=16384, cuda_graph_max_bs=8, max_running_requests=8, max_prefill_length=2048)
    for a in parser._actions:  # defaults above replace the "required" engine flags
        if a.dest in ("num_pages", "cuda_graph_max_bs"):
            a.required = False
    args = parser.parse_args()
    out = run_dir()

    import torch
    from minisgl.core import SamplingParams
    from minisgl.llm import LLM

    llm = LLM(args.model_path, dtype=torch.bfloat16, **engine_kwargs(args))
    prompts = ["The capital of France is", "Count from one to five:"]
    outs = llm.generate(prompts, SamplingParams(temperature=0.0, max_tokens=16, ignore_eos=True))
    ok = all(len(o["token_ids"]) > 0 for o in outs)
    for p, o in zip(prompts, outs):
        print(f"{p!r} -> {o['text']!r} ({len(o['token_ids'])} tokens)")
    print("RESULT:", "PASS" if ok else "FAIL")
    write_json_once(out / "offline_smoke.json", {"meta": run_meta(args), "prompts": prompts, "outputs": outs})
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

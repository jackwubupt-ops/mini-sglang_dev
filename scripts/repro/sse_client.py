"""Minimal stdlib streaming client for /v1/chat/completions, shared by online reproducers.

Counts SSE events per request (F12: every ack becomes exactly one event with
finish_reason == null; the server then sends one finish_reason == "stop" event and [DONE]).
Also helps reproducers find the server run directory created by serve.sh.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

MODEL = "Qwen/Qwen3-0.6B"


def server_run_dir(run_id: str) -> Path:
    runs = os.environ.get("RUNS")
    if not runs:
        sys.exit("REFUSED: RUNS is not set; scripts/check_env.sh && source scripts/env.sh")
    path = (Path(runs) / run_id).resolve()
    if path.parent != Path(runs).resolve() or not (path / "port").is_file():
        sys.exit(f"REFUSED: {path} is not a serve.sh run directory (no port file)")
    return path


def overlap_from_run_meta(run_dir: Path) -> bool:
    meta = (run_dir / "run_meta.txt").read_text()
    for line in meta.splitlines():
        line = line.strip()
        if line.startswith("MINISGL_DISABLE_OVERLAP_SCHEDULING="):
            return line.split("=", 1)[1].lower() not in ("1", "true", "yes")
    return True


def stream_one(port: int, content: str, max_tokens: int, ignore_eos: bool, timeout: float = 600) -> Dict[str, Any]:
    body = json.dumps(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "ignore_eos": ignore_eos,
            "stream": True,
        }
    )
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    t0 = time.time()
    conn.request("POST", "/v1/chat/completions", body, {"Content-Type": "application/json"})
    resp = conn.getresponse()
    if resp.status != 200:
        return {"error": f"HTTP {resp.status}: {resp.read()[:200]!r}"}
    null_events, stop_events, done, text, ids = 0, 0, False, "", set()
    for raw in resp:
        line = raw.decode().strip()
        if not line.startswith("data: "):
            continue
        data = line[len("data: "):]
        if data == "[DONE]":
            done = True
            break
        chunk = json.loads(data)
        ids.add(chunk.get("id"))
        choice = chunk["choices"][0]
        if choice["finish_reason"] is None:
            null_events += 1
            text += choice["delta"].get("content", "")
        else:
            stop_events += 1
    conn.close()
    return {
        "null_events": null_events,
        "stop_events": stop_events,
        "done": done,
        "ids": sorted(ids),
        "text": text,
        "seconds": round(time.time() - t0, 3),
    }


def run_all(port: int, jobs: List[Dict[str, Any]], concurrency: int) -> List[Dict[str, Any]]:
    def one(job: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return job | stream_one(port, job["content"], job["max_tokens"], job["ignore_eos"])
        except Exception as e:  # recorded, not raised: one failed request must not hide others
            return job | {"error": repr(e)}

    with ThreadPoolExecutor(concurrency) as pool:
        return list(pool.map(one, jobs))


def write_once(path: Path, data: Any) -> None:
    with open(path, "x") as f:
        json.dump(data, f, indent=1)
    print(f"wrote {path}")

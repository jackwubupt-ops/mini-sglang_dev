"""CPU-only tests for the Week 1 tooling (judges, output comparison, SSE client)."""

from __future__ import annotations

import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "repro"))
import sse_client  # noqa: E402


def _run(script: str, *args, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / script), *map(str, args)],
        capture_output=True, text=True, check=check,
    )


def _outputs(path: Path, token_lists, overlap: bool, max_tokens=64, sha="abc") -> Path:
    results = [
        {"i": i, "prompt_sha256": f"p{i}", "max_tokens": max_tokens, "token_ids": t}
        for i, t in enumerate(token_lists)
    ]
    path.write_text(json.dumps({"meta": {"overlap": overlap, "git_sha": sha}, "results": results}))
    return path


# ---- compare_outputs.py

def test_compare_identical_and_different(tmp_path):
    a = _outputs(tmp_path / "a.json", [[1, 2], [3]], True)
    b = _outputs(tmp_path / "b.json", [[1, 2], [3]], True)
    c = _outputs(tmp_path / "c.json", [[1, 9], [3]], True)
    assert _run("compare_outputs.py", a, b).returncode == 0
    r = _run("compare_outputs.py", a, c)
    assert r.returncode == 1 and "request 0: first difference at output position 1" in r.stdout


# ---- r4_judge.py

@pytest.mark.parametrize(
    "on, status",
    [
        ([[5, 6, 7], [8, 9], list(range(64))], "confirmed"),
        ([[5, 6], [8], list(range(64))], "not reproduced"),
        ([[5, 6, 7], [8], list(range(64))], "inconclusive"),
    ],
)
def test_r4_judge(tmp_path, on, status):
    off = _outputs(tmp_path / "off.json", [[5, 6], [8], list(range(64))], False)
    on_p = _outputs(tmp_path / "on.json", on, True)
    out = tmp_path / "R4.json"
    r = _run("repro/r4_judge.py", on_p, off, "--out", out)
    assert r.returncode == 0, r.stderr
    v = json.loads(out.read_text())
    assert v["status"] == status and v["n_eos_terminated"] == 2
    assert _run("repro/r4_judge.py", on_p, off, "--out", out).returncode != 0  # never overwrites


def test_r4_judge_refuses_swapped_modes(tmp_path):
    off = _outputs(tmp_path / "off.json", [[1]], False)
    on = _outputs(tmp_path / "on.json", [[1]], True)
    assert _run("repro/r4_judge.py", off, on, "--out", tmp_path / "x.json").returncode != 0


# ---- r1_judge.py

def _r1(path: Path, overlap: bool, events_for):
    results = [
        {"i": i, "max_tokens": t, "null_events": events_for(t), "done": True}
        for i, t in enumerate([1, 2, 8, 64] * 8)
    ]
    path.write_text(json.dumps({"overlap": overlap, "results": results}))
    return path


@pytest.mark.parametrize(
    "on_events, status",
    [
        (lambda t: max(t - 1, 1), "confirmed"),
        (lambda t: t, "not reproduced"),
        (lambda t: t + 1, "inconclusive"),
    ],
)
def test_r1_judge(tmp_path, on_events, status):
    off = _r1(tmp_path / "off.json", False, lambda t: t)
    on = _r1(tmp_path / "on.json", True, on_events)
    out = tmp_path / "R1.json"
    assert _run("repro/r1_judge.py", on, off, "--out", out).returncode == 0
    assert json.loads(out.read_text())["status"] == status


# ---- sse_client.py against a fake server that mimics api_server.stream_chat_completions

class _FakeSSE(http.server.BaseHTTPRequestHandler):
    drop_last = True  # emulate H1: one ack fewer than max_tokens

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        n = body["max_tokens"] - (1 if self.drop_last and body["max_tokens"] >= 2 else 0)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for k in range(n):
            delta = {"content": f"t{k} "} | ({"role": "assistant"} if k == 0 else {})
            chunk = {"id": "cmpl-7", "choices": [{"delta": delta, "index": 0, "finish_reason": None}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        end = {"id": "cmpl-7", "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]}
        self.wfile.write(f"data: {json.dumps(end)}\n\n".encode() + b"data: [DONE]\n\n")

    def log_message(self, *args):
        pass


def test_sse_client_counts_events():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeSSE)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        jobs = [{"i": i, "content": "x", "max_tokens": t, "ignore_eos": True} for i, t in enumerate([1, 2, 8])]
        res = sse_client.run_all(port, jobs, concurrency=3)
    finally:
        server.shutdown()
    assert [r["null_events"] for r in res] == [1, 1, 7]
    assert all(r["done"] and r["stop_events"] == 1 and r["ids"] == ["cmpl-7"] for r in res)
    assert res[2]["text"].startswith("t0 t1")


def test_overlap_from_run_meta(tmp_path):
    (tmp_path / "run_meta.txt").write_text("env:\n  MINISGL_DISABLE_OVERLAP_SCHEDULING=1\n")
    assert sse_client.overlap_from_run_meta(tmp_path) is False
    (tmp_path / "run_meta.txt").write_text("env:\n  CUDA_DEVICE_ORDER=PCI_BUS_ID\n")
    assert sse_client.overlap_from_run_meta(tmp_path) is True


def test_golden_prompt_spec_is_fixed():
    sys.path.insert(0, str(REPO / "scripts"))
    from golden_outputs import prompt_spec

    spec = prompt_spec(8192)
    assert spec == prompt_spec(8192) and len(spec) == 64
    lens = [n for n, _ in spec]
    assert all(1 <= n <= 16 for n in lens[:8]) and all(100 <= n <= 2000 for n in lens[8:40])
    assert all(4097 <= n <= 8192 for n in lens[40:56]) and all(n > 8192 for n in lens[56:])


def test_r4_has_64_questions():
    sys.path.insert(0, str(REPO / "scripts" / "repro"))
    from r4_offline import questions

    qs = questions()
    assert len(qs) == 64 and len(set(qs)) == 64

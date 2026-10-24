#!/usr/bin/env python3
"""End-to-end: does ctxprobe record what a harness actually sends?"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ctxprobe  # noqa: E402
import fake_upstream  # noqa: E402


def payload(stream: bool = False, extra_message: bool = False):
    messages = [{"role": "user", "content": "hello, fix the failing test"}]
    if extra_message:
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "x" * 4000}
                ],
            }
        )
    return {
        "model": "claude-sonnet-4-5",
        "max_tokens": 128,
        "stream": stream,
        "system": [{"type": "text", "text": "You are a coding agent. Today is 2026-10-24."}],
        "tools": [
            {"name": "Read", "description": "read a file", "input_schema": {"type": "object"}},
            {"name": "Edit", "description": "edit a file", "input_schema": {"type": "object"}},
        ],
        "messages": messages,
    }


class ProxyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.session_dir = Path(cls.tmp.name)
        cls.session_id = "test-session"

        cls.upstream, upstream_port = fake_upstream.start()
        cls.proxy = ThreadingHTTPServer(("127.0.0.1", 0), ctxprobe.CtxProbeHandler)
        ctxprobe.CtxProbeHandler.upstream_base = f"http://127.0.0.1:{upstream_port}"
        ctxprobe.CtxProbeHandler.recorder = ctxprobe.Recorder(
            cls.session_dir, cls.session_id, False
        )
        cls.port = cls.proxy.server_address[1]
        threading.Thread(target=cls.proxy.serve_forever, daemon=True).start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.proxy.shutdown()
        cls.upstream.shutdown()
        cls.tmp.cleanup()

    def post(self, body: dict) -> str:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/messages",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer sk-test"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode()

    def records(self) -> list[dict]:
        path = self.session_dir / f"{self.session_id}.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def test_non_stream_roundtrip(self):
        out = json.loads(self.post(payload()))
        self.assertEqual(out["usage"]["input_tokens"], 1200)
        rec = self.records()[-1]
        self.assertEqual(rec["provider"], "anthropic")
        self.assertEqual(rec["usage"]["input"], 1200)
        self.assertEqual(rec["usage"]["cache_read"], 800)
        self.assertFalse(rec["usage"]["estimated"])
        self.assertEqual(rec["req"]["n_tools"], 2)
        self.assertGreater(rec["req"]["schema_tokens"], 0)
        self.assertEqual(len(rec["req"]["prefix_hash"]), 12)

    def test_stream_roundtrip_captures_usage(self):
        body = self.post(payload(stream=True))
        self.assertIn("content_block_delta", body)
        rec = self.records()[-1]
        self.assertTrue(rec["stream"])
        self.assertEqual(rec["usage"]["input"], 1200)
        self.assertEqual(rec["usage"]["output"], 42)

    def test_prefix_is_stable_across_turns(self):
        self.post(payload())
        self.post(payload(extra_message=True))
        a, b = self.records()[-2:]
        self.assertEqual(a["req"]["prefix_hash"], b["req"]["prefix_hash"])

    def test_tool_result_is_captured(self):
        self.post(payload(extra_message=True))
        rec = self.records()[-1]
        kinds = [m["k"] for m in rec["req"]["messages"]]
        self.assertIn("tool_result", kinds)
        big = max(rec["req"]["messages"], key=lambda m: m["c"])
        self.assertGreater(big["c"], 3000)

    def test_volatile_date_does_not_break_prefix(self):
        """A date in the system prompt must not change the prefix hash."""
        first = payload()
        first["system"] = [{"type": "text", "text": "You are an agent. Today is 2026-10-24."}]
        second = payload()
        second["system"] = [{"type": "text", "text": "You are an agent. Today is 2026-10-25."}]
        self.post(first)
        self.post(second)
        a, b = self.records()[-2:]
        self.assertEqual(a["req"]["prefix_hash"], b["req"]["prefix_hash"])

    def test_changed_tool_list_breaks_prefix(self):
        self.post(payload())
        changed = payload()
        changed["tools"] = changed["tools"][:1]
        self.post(changed)
        a, b = self.records()[-2:]
        self.assertNotEqual(a["req"]["prefix_hash"], b["req"]["prefix_hash"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

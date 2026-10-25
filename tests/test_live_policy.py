#!/usr/bin/env python3
"""Regression tests for the explicit live request-policy gateway."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ctxprobe  # noqa: E402
import live_policy  # noqa: E402


class CaptureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    last_body = b""
    response_body = b'{"ok":true}'

    def log_message(self, *_args):
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        type(self).last_body = self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.response_body)))
        self.end_headers()
        self.wfile.write(self.response_body)


def request_body(messages: list[dict]) -> dict:
    return {
        "model": "claude-sonnet-4-5",
        "system": "You are an agent. Today is 2026-10-26.",
        "tools": [
            {"name": "Read", "description": "read files on 2026-10-26"},
            {"name": "Bash", "description": "run commands"},
        ],
        "messages": messages,
    }


class LivePolicyUnitTest(unittest.TestCase):
    def test_truncate_changes_only_tool_result_content(self):
        body = request_body(
            [
                {"role": "user", "content": "keep this prompt"},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "content": "x" * 1000,
                        }
                    ],
                },
            ]
        )
        out = live_policy.make("truncate:20").transform(body, "anthropic")
        self.assertEqual(out["messages"][0], body["messages"][0])
        result = out["messages"][1]["content"][0]["content"]
        self.assertIn("truncated", result)
        self.assertLess(len(result), 1000)

    def test_dedupe_tracks_repeats_across_requests(self):
        policy = live_policy.make("dedupe-reads")
        first = request_body(
            [{"role": "tool", "tool_call_id": "a", "content": "same output"}]
        )
        second = request_body(
            [{"role": "tool", "tool_call_id": "b", "content": "same output"}]
        )
        self.assertEqual(policy.transform(first, "openai")["messages"][0]["content"], "same output")
        reduced = policy.transform(second, "openai")["messages"][0]["content"]
        self.assertIn("identical tool result", reduced)

    def test_cache_stable_masks_prefix_but_not_user_content(self):
        body = request_body([{"role": "user", "content": "Today is 2026-10-26 in the task."}])
        body["system"] = "You are an agent. Current unix time: 12345."
        out = live_policy.make("cache-stable").transform(body, "anthropic")
        self.assertIn("<VOLATILE>", out["system"])
        self.assertIn("<DATE>", out["tools"][0]["description"])
        self.assertEqual(out["messages"][0]["content"], body["messages"][0]["content"])

    def test_schema_gating_never_sends_an_empty_toolset(self):
        body = request_body([{"role": "user", "content": "hello"}])
        out = live_policy.make("schema-gating:Missing").transform(body, "anthropic")
        self.assertEqual(out["tools"], body["tools"])

    def test_schema_gating_adapts_after_observing_a_tool_call(self):
        policy = live_policy.make("schema-gating")
        body = request_body([{"role": "user", "content": "hello"}])
        self.assertEqual(len(policy.transform(body, "anthropic")["tools"]), 2)
        policy.observe(
            {"blocks": [{"type": "tool_use", "name": "Read"}]}, "anthropic"
        )
        out = policy.transform(body, "anthropic")
        self.assertEqual([tool["name"] for tool in out["tools"]], ["Read"])

    def test_unsupported_policy_has_actionable_error(self):
        with self.assertRaises(live_policy.LivePolicyError) as ctx:
            live_policy.make("compact-at:40")
        self.assertIn("replay.py compare", str(ctx.exception))


class LivePolicyProxyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
        self.proxy = ThreadingHTTPServer(("127.0.0.1", 0), ctxprobe.CtxProbeHandler)
        ctxprobe.CtxProbeHandler.upstream_base = (
            f"http://127.0.0.1:{self.upstream.server_address[1]}"
        )
        ctxprobe.CtxProbeHandler.recorder = ctxprobe.Recorder(self.root, "live", False)
        ctxprobe.CtxProbeHandler.request_policy = live_policy.make("truncate:20")
        threading.Thread(target=self.upstream.serve_forever, daemon=True).start()
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()

    def tearDown(self):
        self.proxy.shutdown()
        self.proxy.server_close()
        self.upstream.shutdown()
        self.upstream.server_close()
        ctxprobe.CtxProbeHandler.upstream_base = ""
        ctxprobe.CtxProbeHandler.recorder = None
        ctxprobe.CtxProbeHandler.request_policy = None
        self.tmp.cleanup()

    def test_live_gateway_reduces_request_and_preserves_response(self):
        body = request_body(
            [
                {"role": "user", "content": "fix the test"},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "content": "pytest output\n" * 500,
                        }
                    ],
                },
            ]
        )
        original = json.dumps(body, ensure_ascii=False).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.proxy.server_address[1]}/v1/messages",
            data=original,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            self.assertEqual(response.read(), CaptureHandler.response_body)

        time.sleep(0.1)
        forwarded = json.loads(CaptureHandler.last_body)
        content = forwarded["messages"][1]["content"][0]["content"]
        self.assertIn("truncated", content)
        self.assertLess(len(CaptureHandler.last_body), len(original))
        records = [
            json.loads(line)
            for line in (self.root / "live.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
        self.assertEqual(records[-1]["req"]["messages"][2]["k"], "tool_result")
        self.assertLess(records[-1]["req"]["messages"][2]["c"], 7000)


if __name__ == "__main__":
    unittest.main(verbosity=2)

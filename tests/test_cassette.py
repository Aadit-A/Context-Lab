#!/usr/bin/env python3
"""Cassette building: record with --cassette, then build a replay fixture."""

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

import cassette as cassette_mod  # noqa: E402
import ctxprobe  # noqa: E402
import fake_upstream  # noqa: E402

SCRIPT = [
    {
        "content": [{"type": "text", "text": "Reading the file."}],
        "stop_reason": "tool_use",
        "blocks": [
            {"type": "tool_use", "id": "tu_a", "name": "Read", "input": {"file_path": "x.py"}}
        ],
    },
    {
        "content": [{"type": "text", "text": "Running tests."}],
        "stop_reason": "tool_use",
        "blocks": [
            {"type": "tool_use", "id": "tu_b", "name": "Bash", "input": {"command": "pytest -q"}}
        ],
    },
    {
        "content": [{"type": "text", "text": "Done."}],
        "stop_reason": "end_turn",
        "blocks": [],
    },
]

TOOLS = [
    {"name": "Read", "description": "read", "input_schema": {"type": "object"}},
    {"name": "Bash", "description": "run", "input_schema": {"type": "object"}},
    {"name": "Grep", "description": "search", "input_schema": {"type": "object"}},  # never used
]


def tool_result(uid: str, content: str) -> dict:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": uid, "content": content}],
    }


def assistant(blocks: list[dict]) -> dict:
    return {"role": "assistant", "content": blocks}


class CassetteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.session_dir = root
        cls.upstream, upstream_port = fake_upstream.start(mode="realistic", script=SCRIPT)

        object_store = root / "s.cassette"
        ctxprobe.CtxProbeHandler.upstream_base = f"http://127.0.0.1:{upstream_port}"
        ctxprobe.CtxProbeHandler.recorder = ctxprobe.Recorder(
            root, "s", False, object_store, True
        )
        cls.proxy = ThreadingHTTPServer(("127.0.0.1", 0), ctxprobe.CtxProbeHandler)
        cls.port = cls.proxy.server_address[1]
        threading.Thread(target=cls.proxy.serve_forever, daemon=True).start()
        time.sleep(0.2)

        task = [{"role": "user", "content": "fix the failing test in x.py"}]
        history = [
            task,
            task
            + [
                assistant(
                    [{"type": "text", "text": "Reading the file."},
                     {"type": "tool_use", "id": "tu_a", "name": "Read",
                      "input": {"file_path": "x.py"}}]
                ),
                tool_result("tu_a", "def add(a, b):\n    return a - b\n" * 40),
            ],
            None,  # filled below
        ]
        history[2] = history[1] + [
            assistant(
                [{"type": "text", "text": "Running tests."},
                 {"type": "tool_use", "id": "tu_b", "name": "Bash",
                  "input": {"command": "pytest -q"}}]
            ),
            tool_result("tu_b", "1 failed, 41 passed"),
        ]
        for messages in history:
            cls.post(messages)
        time.sleep(0.2)

        cls.session_path = root / "s.jsonl"
        cls.cassette = cassette_mod.build(cls.session_path)

    @classmethod
    def tearDownClass(cls):
        cls.proxy.shutdown()
        cls.upstream.shutdown()
        cls.tmp.cleanup()

    @classmethod
    def post(cls, messages: list[dict]) -> dict:
        body = {
            "model": "claude-sonnet-4-5",
            "max_tokens": 128,
            "system": "You are a coding agent. Today is 2026-10-24.",
            "tools": TOOLS,
            "messages": messages,
        }
        req = urllib.request.Request(
            f"http://127.0.0.1:{cls.port}/v1/messages",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    # ------------------------------------------------------------------ #
    def test_session_recorded_with_responses(self):
        turns = [
            json.loads(line)
            for line in self.session_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(turns), 3)
        for turn in turns:
            self.assertIn("resp", turn)
            self.assertTrue(turn["resp"]["blocks"])
        self.assertEqual(turns[0]["resp"]["stop_reason"], "tool_use")
        self.assertEqual(turns[-1]["resp"]["stop_reason"], "end_turn")

    def test_cassette_structure(self):
        cas = self.cassette
        self.assertEqual(cas["n_turns"], 3)
        self.assertEqual(cas["provider"], "anthropic")
        self.assertTrue(cas["system"])
        self.assertEqual(len(cas["tools"]), 3)
        self.assertTrue(cas["objects"])
        # every message block resolves to stored content
        for turn in cas["turns"]:
            for m in turn["messages"]:
                self.assertIn(m["h"], cas["objects"])

    def test_tool_pairs_and_lookup(self):
        store = cassette_mod.ToolResultStore(self.cassette)
        self.assertEqual(store.pairs, 2)
        self.assertEqual(store.unpaired, 0)
        self.assertEqual(store.used_tools, {"Read", "Bash"})
        content, match = store.lookup("Read", {"file_path": "x.py"})
        self.assertEqual(match, "exact")
        self.assertIn("def add", content)
        content, match = store.lookup("Bash", {"command": "pytest -q"})
        self.assertEqual(match, "exact")
        self.assertEqual(content, "1 failed, 41 passed")

    def test_approximate_lookup_by_target(self):
        store = cassette_mod.ToolResultStore(self.cassette)
        content, match = store.lookup("Read", {"file_path": "x.py", "offset": 10})
        self.assertEqual(match, "approx")
        self.assertTrue(content)

    def test_miss_is_reported(self):
        store = cassette_mod.ToolResultStore(self.cassette)
        content, match = store.lookup("Grep", {"pattern": "add"})
        self.assertIsNone(content)
        self.assertEqual(match, "miss")

    def test_build_fails_clearly_without_responses(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "noresp.jsonl"
            p.write_text(
                json.dumps({"turn": 1, "usage": {}, "req": {"messages": []}}) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit) as ctx:
                cassette_mod.build(p)
            self.assertIn("no recorded response", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)

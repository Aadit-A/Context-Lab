#!/usr/bin/env python3
"""Tests for the replay engine: policies, simulation, and the live decision check."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_upstream  # noqa: E402
import replay  # noqa: E402

BIG = "tests/test_billing.py::test_x PASSED\n" * 120  # ~4,000 chars


def tool_use(path: str) -> dict:
    return {"type": "tool_use", "id": "t", "name": "Read", "input": {"path": path}}


def tool_result(text: str) -> dict:
    return {"type": "tool_result", "tool_use_id": "t", "content": text}


def build_cassette(directory: Path) -> list[dict]:
    base = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 256,
        "system": [{"type": "text", "text": "You are an agent. Today is 2026-10-24."}],
        "tools": [
            {"name": "Read", "description": "read a file", "input_schema": {"type": "object"}},
            {"name": "Edit", "description": "edit a file", "input_schema": {"type": "object"}},
            {"name": "Bash", "description": "run a command", "input_schema": {"type": "object"}},
        ],
    }
    m1 = [{"role": "user", "content": "fix the failing test"}]
    m2 = m1 + [{"role": "assistant", "content": [tool_use("src/billing.py")]},
               {"role": "user", "content": [tool_result(BIG)]}]
    m3 = m2 + [{"role": "assistant", "content": [tool_use("src/billing.py")]},
               {"role": "user", "content": [tool_result(BIG)]}]
    m4 = m3 + [{"role": "assistant", "content": [{"type": "text", "text": "done"}]}]

    bodies = []
    for i, msgs in enumerate([m1, m2, m3, m4], start=1):
        body = json.loads(json.dumps(base))
        body["messages"] = msgs
        bodies.append(body)
        (directory / f"{i:04d}.json").write_text(json.dumps(body), encoding="utf-8")
    return bodies


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.bodies = build_cassette(self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_loads_in_order(self):
        loaded = replay.load_cassette(self.dir)
        self.assertEqual(len(loaded), 4)
        self.assertEqual(loaded[0]["messages"][0]["role"], "user")
        self.assertEqual(replay.infer_provider(loaded[0]), "anthropic")

    def test_baseline_changes_nothing(self):
        rows = replay.simulate(self.bodies, ["baseline"])
        self.assertEqual(rows[0]["delta_tokens"], 0)
        self.assertEqual(rows[0]["turns_changed"], 0)

    def test_dedupe_removes_the_repeat(self):
        rows = replay.simulate(self.bodies, ["dedupe-reads"])
        self.assertLess(rows[0]["tokens_after"], rows[0]["tokens_before"])
        self.assertGreaterEqual(rows[0]["turns_changed"], 1)

    def test_truncate_is_gentler_than_elide(self):
        trunc = replay.simulate(self.bodies, ["truncate:200"])[0]
        self.assertLess(trunc["tokens_after"], trunc["tokens_before"])
        self.assertLess(trunc["delta_pct"], 0)

    def test_truncate_keeps_message_structure(self):
        out, _, _ = replay.apply_policy(self.bodies, "truncate:100")
        # same number of messages, same block types, shorter payloads
        self.assertEqual(len(out[1]["messages"]), len(self.bodies[1]["messages"]))
        block = out[1]["messages"][2]["content"][0]
        self.assertEqual(block["type"], "tool_result")
        self.assertIn("truncated", block["content"])
        self.assertLess(len(block["content"]), len(BIG))

    def test_dedupe_preserves_tool_result_blocks(self):
        out, _, _ = replay.apply_policy(self.bodies, "dedupe-reads")
        block = out[2]["messages"][2]["content"][0]
        self.assertEqual(block["type"], "tool_result")
        self.assertIn("identical tool result", block["content"])

    def test_elide_starts_with_a_plain_user_turn(self):
        out, _, _ = replay.apply_policy(self.bodies, "elide:2")
        first = out[3]["messages"][0]
        self.assertEqual(first["role"], "user")
        self.assertIsInstance(first["content"], str)

    def test_schema_keep_reduces_tools(self):
        out, _, _ = replay.apply_policy(self.bodies, "schema-keep:1")
        self.assertEqual(len(out[0]["tools"]), 1)
        self.assertEqual(out[0]["tools"][0]["name"], "Read")  # most used in the cassette

    def test_strip_volatile_masks_the_date(self):
        out, _, _ = replay.apply_policy(self.bodies, "strip-volatile")
        text = out[0]["system"][0]["text"]
        self.assertIn("<DATE>", text)
        self.assertNotIn("2026-10-24", text)

    def test_unknown_policy_is_rejected(self):
        with self.assertRaises(SystemExit):
            replay.apply_policy(self.bodies, "nope:1")

    def test_simulate_orders_by_nothing_but_reports_all(self):
        rows = replay.simulate(self.bodies, ["baseline", "dedupe-reads", "truncate:200"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["policy"], "baseline")


class LiveReplayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.bodies = build_cassette(self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, mode: str) -> dict:
        server, port = fake_upstream.start(0, mode=mode)
        try:
            return replay.run_live(
                self.bodies,
                "dedupe-reads",
                f"http://127.0.0.1:{port}",
                "test-key",
                "anthropic",
                max_turns=3,
            )
        finally:
            server.shutdown()

    def test_agreement_when_model_decisions_are_unchanged(self):
        result = self.run_with("tool")
        self.assertEqual(result["turns"], 3)
        self.assertGreater(result["input_tokens"], 0)
        self.assertGreater(result["cost_usd"], 0)
        self.assertEqual(result["diverged"], 0)
        self.assertEqual(result["compared"], 2)  # last turn has no follow-on decision

    def test_divergence_is_detected(self):
        result = self.run_with("tool_alt")
        self.assertEqual(result["diverged"], 2)
        self.assertEqual(result["divergence_rate"], 1.0)

    def test_details_are_recorded_per_turn(self):
        result = self.run_with("tool")
        self.assertEqual(len(result["details"]), 3)
        self.assertIn("actual_calls", result["details"][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Unit tests for the attribution engine and the report renderer."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import analyze  # noqa: E402
import report  # noqa: E402


def msg(role: str, h: str, chars: int, kind: str = "text", preview: str = "") -> dict:
    return {
        "role": role,
        "h": h,
        "c": chars,
        "t": max(1, chars // 4),
        "k": kind,
        "p": preview,
    }


def build_session(turns: int = 6) -> list[dict]:
    out = []
    for i in range(turns):
        prefix = "aaaa" if i < 3 else "bbbb"
        messages = [
            msg("system", "sys1", 800, "system", "You are a coding agent."),
            msg("user", "u1", 400, "text", "fix the failing test"),
        ]
        # a tool result that repeats on turn 4
        messages.append(
            msg(
                "user",
                "tr-shared" if i in (1, 4) else f"tr-{i}",
                8000,
                "tool_result",
                "pytest output: 42 failed",
            )
        )
        if i >= 2:
            messages.append(msg("assistant", f"a-{i}", 600, "text", "editing file"))

        out.append(
            {
                "v": 1,
                "session": "synthetic",
                "turn": i + 1,
                "ts": 1_700_000_000 + i * 30,
                "provider": "anthropic",
                "model": "claude-sonnet-4-5",
                "path": "/v1/messages",
                "stream": False,
                "status": 200,
                "latency_ms": 900,
                "usage": {
                    "input": 2000,
                    "output": 120,
                    "cache_read": 0 if i == 3 else 6000,
                    "cache_creation": 0,
                    "estimated": False,
                },
                "req": {
                    "n_messages": len(messages),
                    "system_chars": 800,
                    "schema_chars": 4000,
                    "schema_tokens": 1000,
                    "n_tools": 4,
                    "tool_names": ["Read", "Edit", "Bash", "Grep"],
                    "prefix_hash": prefix,
                    "messages": messages,
                },
            }
        )
    return out


class AnalyzeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.turns = build_session()
        cls.result = analyze.analyse(cls.turns, analyze.DEFAULT_PRICES)

    def test_totals(self):
        self.assertEqual(self.result["turns"], 6)
        self.assertGreater(self.result["cost_usd"], 0)
        self.assertAlmostEqual(
            sum(self.result["buckets_usd"].values()), self.result["cost_usd"], places=2
        )

    def test_detects_prefix_break(self):
        self.assertEqual(len(self.result["prefix_breaks"]), 1)
        self.assertEqual(self.result["prefix_breaks"][0]["turn"], 4)

    def test_detects_redundant_read(self):
        # one repeat of a 2,000-token result out of 12,000 tool-result tokens
        self.assertAlmostEqual(self.result["metrics"]["rdr"], 1 / 6, places=3)
        self.assertEqual(self.result["redundant_reads"][0]["times"], 2)

    def test_cache_hit_ratio(self):
        chr_ = self.result["metrics"]["chr"]
        self.assertGreater(chr_, 0.5)
        self.assertLess(chr_, 1.0)

    def test_schema_tax(self):
        self.assertGreater(self.result["metrics"]["st"], 0.05)

    def test_recommendations_are_ranked_and_priced(self):
        recs = self.result["recommendations"]
        self.assertTrue(recs)
        savings = [r["est_saving_usd"] for r in recs]
        self.assertEqual(savings, sorted(savings, reverse=True))
        self.assertTrue(all(r["est_saving_usd"] >= 0 for r in recs))

    def test_terminal_rendering(self):
        text = analyze.render_terminal(self.result)
        self.assertIn("CONTEXT AUDIT", text)
        self.assertIn("cache hit rate", text)
        self.assertIn("TOP FIXES", text)

    def test_html_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "r.html"
            report.write_html(self.result, out)
            doc = out.read_text(encoding="utf-8")
            self.assertIn("<!doctype html>", doc)
            self.assertIn("Ranked fixes", doc)
            self.assertNotIn("Traceback", doc)

    def test_load_session_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "s.jsonl"
            p.write_text(
                "\n".join(json.dumps(t) for t in self.turns), encoding="utf-8"
            )
            loaded = analyze.load_session(p)
            self.assertEqual(len(loaded), 6)
            self.assertEqual(loaded[0]["session"], "synthetic")

    def test_empty_session_is_rejected(self):
        with self.assertRaises(SystemExit):
            analyze.analyse([], analyze.DEFAULT_PRICES)


if __name__ == "__main__":
    unittest.main(verbosity=2)


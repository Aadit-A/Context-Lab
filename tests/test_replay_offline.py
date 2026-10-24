#!/usr/bin/env python3
"""Offline counterfactual comparison: does each policy move the right number?"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import policies as policies_mod  # noqa: E402
import replay  # noqa: E402
from test_cassette import CassetteTest  # noqa: E402


class OfflineCompareTest(CassetteTest):
    """Inherits the recorded cassette from CassetteTest."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.result = replay.compare(
            cls.cassette,
            ["baseline", "dedupe-reads", "schema-gating", "cache-stable", "truncate-200"],
        )

    def test_baseline_reproduces_recorded_cost(self):
        drift = self.result["baseline_drift_pct"]
        self.assertIsNotNone(drift)
        self.assertLess(abs(drift), 15.0, f"model drift too large: {drift}%")

    def test_calibration_is_anchored(self):
        self.assertTrue(self.result["calibration"]["anchored"])
        self.assertGreater(self.result["calibration"]["scale"], 0.5)
        self.assertLess(self.result["calibration"]["scale"], 2.0)

    def test_policies_reduce_tokens(self):
        p = self.result["policies"]
        base = p["baseline"]["tokens_in"]
        self.assertLess(p["truncate-200"]["tokens_in"], base * 0.9)
        self.assertLess(p["dedupe-reads"]["tokens_in"], base)

    def test_cache_stable_improves_cache_not_tokens(self):
        p = self.result["policies"]
        base = p["baseline"]
        cs = p["cache-stable"]
        # same information, so token count barely moves...
        self.assertAlmostEqual(cs["tokens_in"], base["tokens_in"], delta=base["tokens_in"] * 0.05)
        # ...but the cache finally works, so cost drops
        self.assertGreater(cs["cache_pct"], base["cache_pct"] + 0.3)
        self.assertLess(cs["cost_usd"], base["cost_usd"] * 0.75)

    def test_trajectory_labels_are_honest(self):
        p = self.result["policies"]
        self.assertTrue(p["baseline"]["preserves_trajectory"])
        self.assertTrue(p["schema-gating"]["preserves_trajectory"])
        self.assertFalse(p["truncate-200"]["preserves_trajectory"])
        self.assertFalse(p["dedupe-reads"]["preserves_trajectory"])

    def test_deterministic(self):
        again = replay.compare(
            self.cassette,
            ["baseline", "truncate-200"],
        )
        self.assertEqual(
            again["policies"]["truncate-200"]["cost_usd"],
            self.result["policies"]["truncate-200"]["cost_usd"],
        )

    def test_compaction_triggers_below_window(self):
        small = replay.compare(
            self.cassette, ["baseline", "compact-at-40"], window=3000
        )
        self.assertLess(
            small["policies"]["compact-at-40"]["tokens_in"],
            small["policies"]["baseline"]["tokens_in"] * 0.9,
        )

    def test_compaction_does_not_trigger_above_window(self):
        big = replay.compare(
            self.cassette, ["baseline", "compact-at-90"], window=1_000_000
        )
        self.assertEqual(
            big["policies"]["compact-at-90"]["tokens_in"],
            big["policies"]["baseline"]["tokens_in"],
        )


class PolicyUnitTest(unittest.TestCase):
    def test_schema_gating_removes_unused_tools(self):
        policy = policies_mod.get("schema-gating")
        tools = [
            {"name": "Read", "description": "x"},
            {"name": "Grep", "description": "y"},
        ]
        gated = policy.transform_tools(tools, used={"Read"})
        self.assertEqual([t["name"] for t in gated], ["Read"])

    def test_schema_gating_never_empties_the_toolset(self):
        policy = policies_mod.get("schema-gating")
        tools = [{"name": "Read"}]
        gated = policy.transform_tools(tools, used=set())
        self.assertEqual(gated, tools)

    def test_truncate_is_content_addressed(self):
        policy = policies_mod.get("truncate-40")
        block = {"kind": "tool_result", "content": "x" * 4000, "h": "abc"}
        seen: set[str] = set()
        a = policy.transform_block(dict(block), seen)
        b = policy.transform_block(dict(block), seen)
        self.assertEqual(a["content"], b["content"])
        self.assertIn("truncated", a["content"])

    def test_dedupe_replaces_only_repeats(self):
        policy = policies_mod.get("dedupe-reads")
        first = policy.transform_block({"kind": "tool_result", "content": "f", "h": "h1"}, set())
        second = policy.transform_block({"kind": "tool_result", "content": "f", "h": "h1"}, set())
        self.assertEqual(first["content"], "f")
        self.assertIn("identical tool result", second["content"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

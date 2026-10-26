#!/usr/bin/env python3
"""Regression tests for autotune: ladder, budget selection, config, guard."""

from __future__ import annotations

import copy
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import autotune  # noqa: E402
import live_policy  # noqa: E402
import replay as replay_mod  # noqa: E402

DEMO_CASSETTE = ROOT / "examples" / "demo.cassette.json"
SAMPLE_SESSION = ROOT / "examples" / "sample-session.jsonl"


class LadderTest(unittest.TestCase):
    def test_ladder_is_safety_ordered_with_explicit_specs(self) -> None:
        specs = autotune.ladder_specs(200)
        self.assertEqual(
            specs,
            [
                (0, "baseline"),
                (1, "cache-stable"),
                (2, "truncate:200"),
                (3, "dedupe-reads"),
                (4, "schema-gating"),
            ],
        )

    def test_tier_caps(self) -> None:
        self.assertEqual(autotune.MAX_TIER["safe"], 1)
        self.assertEqual(autotune.MAX_TIER["balanced"], 3)
        self.assertEqual(autotune.MAX_TIER["aggressive"], 4)

    def test_step_down_policy_walks_the_ladder(self) -> None:
        self.assertEqual(autotune.step_down_policy("dedupe-reads"), (2, "truncate:400"))
        self.assertEqual(autotune.step_down_policy("truncate:200"), (1, "cache-stable"))
        self.assertEqual(autotune.step_down_policy("cache-stable"), (0, "baseline"))
        self.assertEqual(autotune.step_down_policy("baseline"), (0, "baseline"))

    def test_step_down_rejects_offline_policies_by_landing_on_baseline(self) -> None:
        self.assertEqual(autotune.step_down_policy("elide-20"), (0, "baseline"))


class BudgetTest(unittest.TestCase):
    def test_absolute_tokens(self) -> None:
        self.assertEqual(autotune.parse_budget("10000", 36296), 10000)

    def test_percentage_of_baseline(self) -> None:
        self.assertEqual(autotune.parse_budget("50%", 36296), 18148)

    def test_rejects_garbage_and_nonpositive(self) -> None:
        for bad in ("lots", "0", "-5", "0%", "150%", "x%"):
            with self.assertRaises(autotune.AutotuneError):
                autotune.parse_budget(bad, 1000)


class CalibratedPlanTest(unittest.TestCase):
    def plan(self, budget: str, **kw: object) -> dict:
        opts: dict[str, object] = dict(
            session=None,
            cassette=DEMO_CASSETTE,
            budget=budget,
            budget_usd=None,
            max_tier="aggressive",
            truncate_n=400,
        )
        opts.update(kw)
        return autotune.build_plan(**opts)  # type: ignore[arg-type]

    def test_mode_and_rows(self) -> None:
        plan = self.plan("8000")
        self.assertEqual(plan["mode"], "calibrated")
        self.assertEqual([t["tier"] for t in plan["tiers"]], [0, 1, 2, 3, 4])
        baseline = plan["tiers"][0]
        self.assertEqual(baseline["tokens_in"], plan["baseline_tokens_in"])
        self.assertTrue(plan["tiers"][1]["preserves_trajectory"])
        self.assertFalse(plan["tiers"][3]["preserves_trajectory"])

    def test_selects_lowest_fitting_tier(self) -> None:
        # truncate:400 projects ~9.5k and dedupe-reads ~6.1k on this fixture,
        # so an 8k budget must skip truncate and land on dedupe-reads.
        plan = self.plan("8000")
        sel = plan["selection"]
        self.assertIsNotNone(sel)
        self.assertEqual(sel["policy"], "dedupe-reads")
        self.assertLessEqual(sel["tokens_in"], 8000)
        self.assertGreater(sel["headroom"], 0)

    def test_baseline_budget_selects_tier_zero(self) -> None:
        plan = self.plan("999999")
        self.assertEqual(plan["selection"]["tier"], 0)
        self.assertEqual(plan["selection"]["policy"], "baseline")

    def test_unreachable_budget_reports_best_and_floor(self) -> None:
        plan = self.plan("3000")
        self.assertIsNone(plan["selection"])
        self.assertTrue(plan["unreachable"])
        self.assertEqual(plan["best_within_cap"]["policy"], "dedupe-reads")

    def test_max_tier_cap_forces_unreachable_rather_than_risk(self) -> None:
        plan = self.plan("3000", max_tier="safe")
        self.assertIsNone(plan["selection"])
        self.assertTrue(plan["unreachable"])
        self.assertLessEqual(plan["best_within_cap"]["tier"], 1)

    def test_high_drift_withholds_selection(self) -> None:
        cas = replay_mod.load_cassette_file(DEMO_CASSETTE)
        with tempfile.TemporaryDirectory() as tmp:
            for turn in cas["turns"]:
                u = turn.get("usage") or {}
                for key in ("input", "cache_read", "cache_creation"):
                    if u.get(key):
                        u[key] = int(u[key]) * 5
            path = Path(tmp) / "skewed.cassette.json"
            path.write_text(json.dumps(cas), encoding="utf-8")
            plan = autotune.build_plan(
                session=None,
                cassette=path,
                budget="8000",
                budget_usd=None,
                max_tier="aggressive",
                truncate_n=400,
            )
        self.assertGreater(plan["drift_pct"], autotune.DRIFT_LIMIT)
        self.assertIsNone(plan["selection"])
        self.assertFalse(plan["unreachable"])
        self.assertIn("drift", plan["why_unselected"])


class EstimatePlanTest(unittest.TestCase):
    def test_estimate_mode_is_labelled_and_never_inflates(self) -> None:
        plan = autotune.build_plan(
            session=SAMPLE_SESSION,
            cassette=None,
            budget=None,
            budget_usd=None,
            max_tier="aggressive",
            truncate_n=400,
        )
        self.assertEqual(plan["mode"], "estimate")
        self.assertIsNone(plan["drift_pct"])
        self.assertIsNone(plan["selection"])
        for row in plan["tiers"]:
            self.assertLessEqual(row["tokens_in"], plan["baseline_tokens_in"])
        # cache-stable moves cost, not tokens
        self.assertEqual(plan["tiers"][1]["tokens_in"], plan["baseline_tokens_in"])
        self.assertLess(plan["tiers"][1]["cost_usd"], plan["baseline_cost_usd"])

    def test_render_is_ascii(self) -> None:
        plan = autotune.build_plan(
            session=SAMPLE_SESSION,
            cassette=None,
            budget="50%",
            budget_usd=None,
            max_tier="aggressive",
            truncate_n=400,
        )
        text = autotune.render_plan(plan)
        text.encode("ascii")
        self.assertIn("AUTOTUNE PLAN", text)


class ConfigLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = Path(self._tmp.name) / "autotune.json"
        self._env = os.environ.get("CTXPROBE_AUTOTUNE_CONFIG")
        os.environ["CTXPROBE_AUTOTUNE_CONFIG"] = str(self.config)

    def tearDown(self) -> None:
        if self._env is None:
            os.environ.pop("CTXPROBE_AUTOTUNE_CONFIG", None)
        else:
            os.environ["CTXPROBE_AUTOTUNE_CONFIG"] = self._env
        self._tmp.cleanup()

    def test_enable_status_disable_and_auto_resolution(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(autotune.main(["enable", "truncate:200", "--budget", "8000"]), 0)
        cfg = autotune.load_config()
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["policy"], "truncate:200")
        self.assertEqual(autotune.resolve_policy(), "truncate:200")
        self.assertEqual(live_policy.make("auto").spec, "truncate:200")

        with redirect_stdout(io.StringIO()):
            self.assertEqual(autotune.main(["status"]), 0)
            self.assertEqual(autotune.main(["disable"]), 0)
        self.assertFalse(autotune.load_config()["enabled"])
        with self.assertRaises(live_policy.LivePolicyError):
            live_policy.make("auto")

    def test_enable_rejects_offline_policies(self) -> None:
        err = io.StringIO()
        import contextlib

        with redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(autotune.main(["enable", "elide-20"]), 2)
        self.assertFalse(self.config.exists())

    def test_auto_without_config_names_the_fix(self) -> None:
        with self.assertRaises(live_policy.LivePolicyError) as ctx:
            live_policy.make("auto")
        self.assertIn("autotune.py plan", str(ctx.exception))

    def test_auto_rejects_configured_offline_policy(self) -> None:
        autotune.save_config({"enabled": True, "policy": "compact-at:60"})
        with self.assertRaises(autotune.AutotuneError):
            autotune.resolve_policy()


class GuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.config = self.dir / "autotune.json"
        self.csv = self.dir / "bench.csv"
        header = "ts,name,model,turns,input_tokens,output_tokens,cost_usd,solved,duration_s\n"

        def row(ts: str, tokens: int, solved: int) -> str:
            return f"{ts},fix-billing,m,8,{tokens},2000,0.02,{solved},55\n"

        self.csv.write_text(
            header + row("2026-10-26T10:00:00", 36299, 1) + row("2026-10-26T11:00:00", 6599, 0),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_regression_steps_down_and_persists(self) -> None:
        autotune.save_config({"enabled": True, "policy": "dedupe-reads"}, self.config)
        out = io.StringIO()
        with redirect_stdout(out):
            code = autotune.guard(self.csv, "fix-billing", self.config)
        self.assertEqual(code, 1)
        cfg = autotune.load_config(self.config)
        self.assertEqual(cfg["policy"], "truncate:400")
        self.assertEqual(cfg["guard"]["event"], "regression")
        self.assertIn("REGRESSION", out.getvalue())

    def test_regression_without_policy_reports_and_keeps_config(self) -> None:
        autotune.save_config({"enabled": False, "policy": "dedupe-reads"}, self.config)
        with redirect_stdout(io.StringIO()) as out:
            code = autotune.guard(self.csv, "fix-billing", self.config)
        self.assertEqual(code, 1)
        self.assertIn("not policy-caused", out.getvalue())
        self.assertEqual(autotune.load_config(self.config)["policy"], "dedupe-reads")

    def test_healthy_run_is_ok(self) -> None:
        with self.csv.open("a", encoding="utf-8") as fh:
            fh.write("2026-10-26T12:00:00,fix-billing,m,8,6400,2000,0.02,1,50\n")
        autotune.save_config({"enabled": True, "policy": "truncate:400"}, self.config)
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(autotune.guard(self.csv, "fix-billing", self.config), 0)
        self.assertIn("ok", out.getvalue())
        self.assertEqual(autotune.load_config(self.config)["policy"], "truncate:400")

    def test_token_regression_warns_without_stepping_down(self) -> None:
        with self.csv.open("a", encoding="utf-8") as fh:
            fh.write("2026-10-26T12:00:00,fix-billing,m,8,40000,2000,0.1,1,50\n")
        autotune.save_config({"enabled": True, "policy": "truncate:400"}, self.config)
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(autotune.guard(self.csv, "fix-billing", self.config), 0)
        self.assertIn("WARNING", out.getvalue())
        self.assertEqual(autotune.load_config(self.config)["policy"], "truncate:400")

    def test_single_run_is_a_no_op(self) -> None:
        self.csv.write_text(
            "ts,name,model,turns,input_tokens,output_tokens,cost_usd,solved,duration_s\n"
            "2026-10-26T10:00:00,fix-billing,m,8,100,2,0.01,1,5\n",
            encoding="utf-8",
        )
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(autotune.guard(self.csv, "fix-billing", self.config), 0)
        self.assertIn("fewer than two", out.getvalue())


if __name__ == "__main__":
    unittest.main()

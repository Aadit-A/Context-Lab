#!/usr/bin/env python3
"""
autotune - pick, persist, and guard a live context policy against a budget.

    autotune.py plan <session.jsonl> --budget 100000
    autotune.py plan --latest --budget 50% --max-tier balanced
    autotune.py plan --cassette <session>.cassette.json --budget 8000
    autotune.py enable truncate:200 --budget 100000
    autotune.py status
    autotune.py disable
    autotune.py guard --name fix-billing --csv bench.csv

`plan` never changes anything: it projects a safety-ordered ladder of live
policies against one recorded session and picks the lowest tier that fits the
budget. `enable` persists that choice to a local config file, and
`wrap.py --policy auto` reads it back, so the reduction stays in force without
retyping the spec. `guard` watches bench.py results and steps back DOWN the
ladder when a cheaper run stops solving the task.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:  # survive python -P / safe_path environments
    sys.path.insert(0, str(HERE))

import analyze as analyze_mod  # noqa: E402
import policies as policies_mod  # noqa: E402
import replay as replay_mod  # noqa: E402

# Risk-ordered, lowest first. The order is the product decision autotune makes:
# tier 1 never changes content the model reads, tiers 2-4 progressively do.
TIER_SPECS = [
    (0, "baseline"),
    (1, "cache-stable"),
    (2, "truncate"),
    (3, "dedupe-reads"),
    (4, "schema-gating"),
]
TIER_NAMES = {name: tier for tier, name in TIER_SPECS}
MAX_TIER = {"safe": 1, "balanced": 3, "aggressive": 4}
TIER_NOTES = {
    0: "no change; measurement only",
    1: "prefix only; content the model reads is unchanged",
    2: "oversized tool results capped at head+tail",
    3: "exact repeated tool results replaced by a marker",
    4: "tool schemas the session never called are dropped",
}
DEFAULT_TRUNCATE = 400
DRIFT_LIMIT = 15.0

# Estimate-mode multipliers, mirroring the ones analyze.py already uses for its
# ranked fixes. Documented in references/autotune.md; never presented as
# measurements.
MARKER_OVERHEAD_TOKENS = 10
DEDUPE_KEEP = 0.1
SCHEMA_KEEP = 0.5
ASSUMED_CACHE_HIT = 0.9


class AutotuneError(ValueError):
    """Raised for user-facing autotune problems (bad budget, missing config)."""


# --------------------------------------------------------------------------- #
# config file
# --------------------------------------------------------------------------- #
def config_path() -> Path:
    override = os.environ.get("CTXPROBE_AUTOTUNE_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".ctxprobe" / "autotune.json"


def load_config(path: str | Path | None = None) -> dict[str, Any] | None:
    p = Path(path).expanduser() if path else config_path()
    if not p.exists():
        return None
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AutotuneError(f"autotune config is not valid JSON ({p}): {exc}") from None
    if not isinstance(cfg, dict):
        raise AutotuneError(f"autotune config must be a JSON object ({p})")
    return cfg


def save_config(cfg: dict[str, Any], path: str | Path | None = None) -> Path:
    p = Path(path).expanduser() if path else config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return p


def resolve_policy(path: str | Path | None = None) -> str:
    """The spec `--policy auto` expands to; raises if nothing is persisted."""
    import live_policy as live_policy_mod  # local import: live_policy imports this module lazily too

    cfg = load_config(path)
    if cfg is None:
        raise AutotuneError(
            "no autotune policy is configured. Run `autotune.py plan <session> "
            "--budget N`, then `autotune.py enable <policy>`."
        )
    if not cfg.get("enabled"):
        last = cfg.get("policy") or "none"
        raise AutotuneError(
            f"autotune is disabled (last policy: {last}). "
            "Re-enable with: autotune.py enable <policy>"
        )
    spec = str(cfg.get("policy") or "")
    if not spec:
        raise AutotuneError("autotune config has no policy. Run: autotune.py enable <policy>")
    try:
        live_policy_mod.LivePolicy(spec)
    except ValueError as exc:
        raise AutotuneError(f"configured policy is not a live policy ({spec}): {exc}") from None
    return spec


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #
def ladder_specs(truncate_n: int) -> list[tuple[int, str]]:
    return [
        (0, "baseline"),
        (1, "cache-stable"),
        (2, f"truncate:{max(1, truncate_n)}"),
        (3, "dedupe-reads"),
        (4, "schema-gating"),
    ]


def parse_budget(raw: str, baseline_tokens: int) -> int:
    text = raw.strip()
    if text.endswith("%"):
        try:
            pct = float(text[:-1])
        except ValueError:
            raise AutotuneError(f"budget percentage is not a number: {raw}") from None
        if pct <= 0 or pct > 100:
            raise AutotuneError(f"budget percentage must be in (0, 100]: {raw}")
        return max(1, round(baseline_tokens * pct / 100))
    try:
        n = int(text)
    except ValueError:
        raise AutotuneError(f"budget must be a token count or a percentage like 50%: {raw}") from None
    if n <= 0:
        raise AutotuneError(f"budget must be positive: {raw}")
    return n


def find_cassette(session: Path) -> Path | None:
    candidate = session.with_suffix(".cassette.json")
    return candidate if candidate.exists() else None


def calibrated_plan(
    cassette_path: Path, specs: list[tuple[int, str]]
) -> dict[str, Any]:
    cas = replay_mod.load_cassette_file(cassette_path)
    result = replay_mod.compare(cas, [spec for _, spec in specs])
    tiers = []
    for tier, spec in specs:
        row = result["policies"][spec]
        tiers.append(
            {
                "tier": tier,
                "policy": spec,
                "tokens_in": row["tokens_in"],
                "cost_usd": row["cost_usd"],
                "delta_pct": row["delta_pct"],
                "preserves_trajectory": row["preserves_trajectory"],
                "note": TIER_NOTES[tier],
            }
        )
    return {
        "mode": "calibrated",
        "cassette": str(cassette_path),
        "n_turns": result["n_turns"],
        "model": result["model"],
        "baseline_tokens_in": result["recorded_tokens_in"],
        "baseline_cost_usd": result["policies"][specs[0][1]]["cost_usd"],
        "drift_pct": result["baseline_drift_pct"],
        "tiers": tiers,
    }


def estimate_plan(
    turns: list[dict[str, Any]], specs: list[tuple[int, str]], truncate_n: int
) -> dict[str, Any]:
    stats = analyze_mod.analyse(turns, analyze_mod.DEFAULT_PRICES)
    baseline_in = stats["tokens"]["input"]
    price = stats["price_used"]
    in_cost = baseline_in * price["in"] / 1e6
    out_cost = stats["tokens"]["output"] * price["out"] / 1e6

    cut = 0
    for turn in turns:
        for msg in (turn.get("req") or {}).get("messages") or []:
            if msg.get("k") == "tool_result":
                t = int(msg.get("t") or 0)
                cut += max(0, t - (truncate_n + MARKER_OVERHEAD_TOKENS))
    dedupe_save = int(stats["tokens"]["redundant"] * (1 - DEDUPE_KEEP))
    schema_save = int(stats["tokens"]["schema"] * (1 - SCHEMA_KEEP))
    savings = {
        "baseline": 0,
        "cache-stable": 0,
        "truncate": cut,
        "dedupe-reads": dedupe_save,
        "schema-gating": schema_save,
    }

    tiers = []
    for tier, spec in specs:
        name = policies_mod.parse(spec)[0]
        tokens_in = max(0, baseline_in - savings[name])
        if name == "cache-stable":
            # cache-stable moves cost, not tokens; assume the provider then
            # hits the same cache share replay.py models for stable prefixes.
            cost = (
                in_cost * ((1 - ASSUMED_CACHE_HIT) + ASSUMED_CACHE_HIT * price["cache_read_mult"])
                + out_cost
            )
        else:
            share = tokens_in / max(1, baseline_in)
            cost = stats["cost_usd"] * share
        delta = (cost - stats["cost_usd"]) / max(stats["cost_usd"], 1e-9) * 100
        tiers.append(
            {
                "tier": tier,
                "policy": spec,
                "tokens_in": tokens_in,
                "cost_usd": round(cost, 4),
                "delta_pct": round(delta, 1),
                "preserves_trajectory": policies_mod.get(spec).preserves_trajectory,
                "note": TIER_NOTES[tier],
            }
        )
    return {
        "mode": "estimate",
        "cassette": None,
        "n_turns": stats["turns"],
        "model": stats["model"],
        "baseline_tokens_in": baseline_in,
        "baseline_cost_usd": stats["cost_usd"],
        "drift_pct": None,
        "tiers": tiers,
    }


def build_plan(
    *,
    session: Path | None,
    cassette: Path | None,
    budget: str | None,
    budget_usd: float | None,
    max_tier: str,
    truncate_n: int,
) -> dict[str, Any]:
    specs = ladder_specs(truncate_n)
    cap = MAX_TIER[max_tier]

    if cassette is None and session is not None:
        cassette = find_cassette(session)

    if cassette is not None:
        plan = calibrated_plan(cassette, specs)
    elif session is not None:
        plan = estimate_plan(analyze_mod.load_session(session), specs, truncate_n)
    else:
        raise AutotuneError("no session or cassette to plan against")

    plan["source"] = str(session) if session else None
    plan["max_tier"] = max_tier
    plan["budget_usd"] = budget_usd

    budget_tokens = None
    if budget is not None:
        budget_tokens = parse_budget(budget, plan["baseline_tokens_in"])
    plan["budget_tokens"] = budget_tokens

    selection = None
    unreachable = False
    if budget_tokens is None:
        plan["why_unselected"] = "no budget given; ladder is informational. Pass --budget to select."
    elif plan["mode"] == "calibrated" and (plan["drift_pct"] or 0) > DRIFT_LIMIT:
        plan["why_unselected"] = (
            f"baseline drift {plan['drift_pct']}% exceeds {DRIFT_LIMIT:.0f}%; the cassette "
            "projection is not trustworthy on this session. Re-capture or plan without "
            "--cassette for a labelled estimate."
        )
    else:
        for row in plan["tiers"]:
            if row["tier"] > cap:
                break
            if row["tokens_in"] > budget_tokens:
                continue
            if budget_usd is not None and row["cost_usd"] > budget_usd:
                continue
            selection = dict(row, headroom=budget_tokens - row["tokens_in"])
            break
        if selection is None:
            reachable = [r for r in plan["tiers"] if r["tier"] <= cap]
            best = min(reachable, key=lambda r: r["tokens_in"]) if reachable else None
            plan["best_within_cap"] = best
            unreachable = True
    plan["selection"] = selection
    plan["unreachable"] = unreachable

    next_steps = []
    if selection is not None:
        enable = f"python scripts/autotune.py enable {selection['policy']}"
        if budget_tokens is not None:
            enable += f" --budget {budget_tokens}"
        next_steps = [
            enable,
            "python scripts/wrap.py --policy auto -- <agent command>",
            "python scripts/bench.py --name <task> --test '<test cmd>' --policy auto -- <agent command>",
            "python scripts/autotune.py guard --name <task>",
        ]
    plan["next"] = next_steps
    return plan


# --------------------------------------------------------------------------- #
# guard
# --------------------------------------------------------------------------- #
def read_bench_rows(csv_path: Path, name: str) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        return [row for row in csv.DictReader(fh) if row.get("name") == name]


def step_down_policy(spec: str) -> tuple[int, str]:
    """One step down the ladder from `spec`; baseline steps to baseline."""
    name, _arg = policies_mod.parse(spec)
    tier = TIER_NAMES.get(name, 0)
    lower = max(0, tier - 1)
    return ladder_specs(DEFAULT_TRUNCATE)[lower]


def guard(csv_path: Path, name: str, config: str | Path | None = None) -> int:
    rows = read_bench_rows(csv_path, name)
    if len(rows) < 2:
        print(f"guard: fewer than two bench runs named '{name}' in {csv_path}; nothing to compare.")
        return 0
    last, prev = rows[-1], rows[-2]
    cfg = load_config(config)
    current = str(cfg.get("policy") or "") if cfg and cfg.get("enabled") else ""

    if prev.get("solved") == "1" and last.get("solved") == "0":
        if current and current != "baseline":
            tier, spec = step_down_policy(current)
            cfg["policy"] = spec
            cfg["enabled"] = True
            cfg["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            cfg["guard"] = {
                "event": "regression",
                "bench_name": name,
                "from": current,
                "to": spec,
            }
            save_config(cfg, config)
            print(
                f"guard: REGRESSION - '{name}' solved on the previous run and fails now.\n"
                f"  autotune stepped down: {current} -> {spec} (tier {tier}).\n"
                f"  Re-run the same task before trusting any number from these runs."
            )
        else:
            print(
                f"guard: REGRESSION - '{name}' solved on the previous run and fails now.\n"
                "  No enabled policy to step down from; the failure is not policy-caused.\n"
                "  Investigate the task before re-running."
            )
        return 1

    pin, pout = int(last.get("input_tokens") or 0), int(prev.get("input_tokens") or 0)
    if pin > pout * 1.05:
        print(
            f"guard: WARNING - input tokens rose {pout:,} -> {pin:,} for '{name}'.\n"
            "  The enabled policy is not helping on this task; re-run `autotune.py plan`."
        )
        return 0

    print(f"guard: ok - '{name}' still solves, {pin:,} input tokens (previous {pout:,}).")
    return 0


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def render_plan(plan: dict[str, Any]) -> str:
    lines: list[str] = []
    add = lines.append
    mode_note = (
        f"calibrated (cassette {plan['cassette']}, drift {plan['drift_pct']}%)"
        if plan["mode"] == "calibrated"
        else "estimate (no cassette; heuristic, labelled as such)"
    )
    add("")
    add(
        f"AUTOTUNE PLAN - {plan['n_turns']} turns - {plan['model']} - "
        f"{plan['baseline_tokens_in']:,} baseline input tokens"
    )
    add(f"mode: {mode_note}   max tier: {plan['max_tier']}")
    add("-" * 78)
    add(f"{'tier':>4}  {'policy':<18}{'projected in':>13}{'delta':>8}{'cost':>8}  trajectory")
    for row in plan["tiers"]:
        capped = " (above cap)" if row["tier"] > MAX_TIER[plan["max_tier"]] else ""
        traj = "preserved" if row["preserves_trajectory"] else "AT RISK"
        add(
            f"{row['tier']:>4}  {row['policy'] + capped:<18}{row['tokens_in']:>13,}"
            f"{row['delta_pct']:>7.0f}%{row['cost_usd']:>8.2f}  {traj}"
        )
        add(f"      {row['note']}")
    add("-" * 78)

    sel = plan.get("selection")
    if plan.get("budget_tokens") is not None:
        add(f"budget: {plan['budget_tokens']:,} input tokens per session")
    if sel is not None:
        add(
            f"selection: {sel['policy']} (tier {sel['tier']}) - "
            f"projects {sel['tokens_in']:,} in, {sel['headroom']:,} under budget"
        )
    elif plan.get("unreachable"):
        best = plan.get("best_within_cap")
        if best:
            deficit = best["tokens_in"] - plan["budget_tokens"]
            add(
                f"selection: none - best tier within cap is {best['policy']} at "
                f"{best['tokens_in']:,} tokens, {deficit:,} over budget."
            )
        else:
            add("selection: none - no tiers within the cap.")
        add(
            "  Request shaping cannot reach this budget. Non-policy fixes (compact\n"
            "  earlier, drop unused MCP servers, cheaper exploration model) are in\n"
            "  references/playbook.md; relax --max-tier if you accept more risk."
        )
    else:
        add(f"selection: withheld - {plan.get('why_unselected', 'no budget given')}")
    if plan["next"]:
        add("next:")
        for step in plan["next"]:
            add(f"  {step}")
    add("")
    return analyze_mod.ascii_safe("\n".join(lines))


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Pick, persist, and guard a live context policy against a budget."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="project the policy ladder against one session")
    p_plan.add_argument("session", nargs="?", help="path to a .jsonl session file")
    p_plan.add_argument("--latest", action="store_true", help="use the newest session")
    p_plan.add_argument("--home", default=str(Path.home() / ".ctxprobe" / "sessions"))
    p_plan.add_argument("--cassette", default="", help="cassette path (default: <session>.cassette.json)")
    p_plan.add_argument("--budget", default="", help="max projected input tokens, or N%% of baseline")
    p_plan.add_argument("--budget-usd", type=float, default=None, help="optional cost cap")
    p_plan.add_argument(
        "--max-tier",
        default="aggressive",
        choices=sorted(MAX_TIER),
        help="safety ceiling on the ladder (safe <= cache-stable, balanced <= dedupe-reads)",
    )
    p_plan.add_argument("--truncate", type=int, default=DEFAULT_TRUNCATE, help="token cap for the truncate tier")
    p_plan.add_argument("--config", default="", help="config path for the suggested enable command")
    p_plan.add_argument("--json", action="store_true")

    p_enable = sub.add_parser("enable", help="persist a live policy for --policy auto")
    p_enable.add_argument("policy", help="live policy spec, e.g. truncate:200")
    p_enable.add_argument("--budget", default="", help="record the budget the policy was chosen for")
    p_enable.add_argument("--config", default="")

    p_disable = sub.add_parser("disable", help="stop auto-applying the persisted policy")
    p_disable.add_argument("--config", default="")

    p_status = sub.add_parser("status", help="show the persisted policy")
    p_status.add_argument("--config", default="")
    p_status.add_argument("--json", action="store_true")

    p_guard = sub.add_parser("guard", help="check bench results and step down on regression")
    p_guard.add_argument("--name", required=True, help="bench task name")
    p_guard.add_argument("--csv", default="bench.csv")
    p_guard.add_argument("--config", default="")

    args = ap.parse_args(argv)

    try:
        if args.cmd == "plan":
            session: Path | None = None
            if args.session:
                session = Path(args.session).expanduser()
            elif args.latest:
                session = analyze_mod.latest_session(Path(args.home).expanduser())
            elif not args.cassette:
                ap.error("plan needs a session path, --latest, or --cassette")
            cassette = Path(args.cassette).expanduser() if args.cassette else None
            plan = build_plan(
                session=session,
                cassette=cassette,
                budget=args.budget or None,
                budget_usd=args.budget_usd,
                max_tier=args.max_tier,
                truncate_n=args.truncate,
            )
            print(json.dumps(plan, indent=2) if args.json else render_plan(plan))
            return 0

        if args.cmd == "enable":
            import live_policy as live_policy_mod

            try:
                live_policy_mod.LivePolicy(args.policy)
            except ValueError as exc:
                print(f"cannot enable: {exc}", file=sys.stderr)
                return 2
            existing = load_config(args.config or None) or {}
            cfg = {
                "enabled": True,
                "policy": args.policy,
                "budget_tokens": int(args.budget) if args.budget.isdigit() else None,
                "created": existing.get("created") or time.strftime("%Y-%m-%dT%H:%M:%S"),
                "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "guard": existing.get("guard"),
            }
            path = save_config(cfg, args.config or None)
            print(f"enabled: {args.policy}")
            print(f"config: {path}")
            print("future runs: python scripts/wrap.py --policy auto -- <agent command>")
            return 0

        if args.cmd == "disable":
            cfg = load_config(args.config or None) or {}
            cfg["enabled"] = False
            cfg["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            path = save_config(cfg, args.config or None)
            print(f"disabled (last policy: {cfg.get('policy') or 'none'})")
            print(f"config: {path}")
            return 0

        if args.cmd == "status":
            cfg = load_config(args.config or None)
            if args.json:
                print(json.dumps(cfg, indent=2))
                return 0
            if cfg is None:
                print("autotune: not configured.")
                print("start with: python scripts/autotune.py plan <session> --budget N")
                return 0
            state = "enabled" if cfg.get("enabled") else "disabled"
            budget = cfg.get("budget_tokens")
            budget_note = f", budget {budget:,} tokens" if budget else ""
            print(f"autotune: {state} - policy {cfg.get('policy') or 'none'}{budget_note}")
            if cfg.get("guard"):
                g = cfg["guard"]
                print(f"  last guard event: {g.get('event')} ({g.get('from')} -> {g.get('to')})")
            print("runs use it via: python scripts/wrap.py --policy auto -- <agent command>")
            return 0

        # guard
        return guard(Path(args.csv).expanduser(), args.name, args.config or None)

    except AutotuneError as exc:
        print(f"autotune: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

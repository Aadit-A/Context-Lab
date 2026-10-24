#!/usr/bin/env python3
"""
replay - compare context policies on the same recorded session.

    replay.py compare <session.jsonl> [--window N]   # projected, offline
    replay.py run     <raw_dir> --policy ...         # live, measures decisions

`compare` reconstructs each request from the cassette's content-addressed
objects, applies a policy, and re-measures against recorded usage. It is
anchored: the token model is calibrated so the untouched session reproduces what
the provider actually charged, and every projection inherits that scale.

`run` re-sends transformed requests turn by turn and checks whether the model
still makes the same tool calls. A saving that changes decisions is not a saving
until the task is re-run in the harness.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:  # survive python -P / safe_path environments
    sys.path.insert(0, str(HERE))

import analyze as analyze_mod  # noqa: E402
import cassette as cassette_mod  # noqa: E402
import policies as policies_mod  # noqa: E402


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --------------------------------------------------------------------------- #
# raw body <-> block bridge
# --------------------------------------------------------------------------- #
def body_to_blocks(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a provider request into cassette-shaped blocks."""
    blocks: list[dict[str, Any]] = []
    system = body.get("system")
    if isinstance(system, str):
        blocks.append({"role": "system", "kind": "system", "content": system})
    elif isinstance(system, list):
        text = " ".join(
            b.get("text", "") for b in system if isinstance(b, dict) and b.get("text")
        )
        if text:
            blocks.append({"role": "system", "kind": "system", "content": text})

    for msg in body.get("messages") or []:
        role = msg.get("role", "user")
        content = msg.get("content")
        if role == "tool" or msg.get("tool_call_id"):
            # OpenAI tool result: payload on the message, not in a block.
            text = content if isinstance(content, str) else canon(content or "")
            blocks.append(
                {
                    "role": role,
                    "kind": "tool_result",
                    "content": text,
                    "rid": msg.get("tool_call_id"),
                }
            )
            continue
        if isinstance(content, str):
            blocks.append({"role": role, "kind": "text", "content": content})
            continue
        for b in content or []:
            if not isinstance(b, dict):
                continue
            kind = b.get("type", "text")
            if kind == "tool_result":
                inner = b.get("content")
                text = inner if isinstance(inner, str) else canon(inner)
                blocks.append(
                    {
                        "role": role,
                        "kind": "tool_result",
                        "content": text,
                        "rid": b.get("tool_use_id"),
                    }
                )
            elif kind == "tool_use":
                blocks.append(
                    {
                        "role": role,
                        "kind": "tool_use",
                        "content": canon(b.get("input") or {}),
                        "n": b.get("name"),
                        "id": b.get("id"),
                    }
                )
            else:
                blocks.append(
                    {"role": role, "kind": "text", "content": b.get("text", "")}
                )

    for block in blocks:
        block["h"] = policies_mod.digest(block["content"])
    return blocks


def blocks_to_body(
    blocks: list[dict[str, Any]], template: dict[str, Any]
) -> dict[str, Any]:
    body = {k: v for k, v in template.items() if k not in ("messages", "system")}
    system_blocks = [b for b in blocks if b.get("kind") == "system"]
    if "system" in template:
        joined = "\n".join(b["content"] for b in system_blocks)
        body["system"] = (
            [{"type": "text", "text": joined}] if isinstance(template["system"], list) else joined
        )

    messages: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("kind") == "system":
            continue
        kind = block.get("kind")
        if kind == "tool_use":
            try:
                payload = json.loads(block["content"])
            except json.JSONDecodeError:
                payload = {"raw": block["content"]}
            piece = {
                "type": "tool_use",
                "id": block.get("id") or "toolu_x",
                "name": block.get("n") or "",
                "input": payload,
            }
        elif kind == "tool_result":
            piece = {
                "type": "tool_result",
                "tool_use_id": block.get("rid") or "toolu_x",
                "content": block["content"],
            }
        else:
            piece = {"type": "text", "text": block["content"]}

        role = block.get("role", "user")
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].append(piece)
        else:
            messages.append({"role": role, "content": [piece]})
    for msg in messages:
        if len(msg["content"]) == 1 and msg["content"][0].get("type") == "text":
            msg["content"] = msg["content"][0]["text"]
    body["messages"] = messages
    return body


def estimate_body(body: dict[str, Any]) -> int:
    chars = len(canon(body.get("system") or ""))
    chars += len(canon(body.get("tools") or []))
    chars += len(canon(body.get("messages") or []))
    return max(1, round(chars / 4))


# --------------------------------------------------------------------------- #
# raw cassettes
# --------------------------------------------------------------------------- #
def load_cassette(raw_dir: Path) -> list[dict[str, Any]]:
    files = sorted(raw_dir.glob("*.json"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0)
    if not files:
        raise SystemExit(f"no raw requests in {raw_dir}. Re-capture with --record-raw.")
    bodies = []
    for path in files:
        try:
            bodies.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return bodies


def infer_provider(body: dict[str, Any]) -> str:
    return "anthropic" if "system" in body else "openai"


def apply_policy(
    bodies: list[dict[str, Any]], spec: str
) -> tuple[list[dict[str, Any]], int, int]:
    """Apply a policy across a raw cassette. Returns (bodies, changed, tokens removed)."""
    policy = policies_mod.get(spec)
    policy.reset()
    before_total = after_total = 0
    changed = 0
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Which tools the session actually calls, so schema gating has something to
    # gate on even on the first turn.
    used_all: set[str] = set()
    for body in bodies:
        used_all |= policies_mod.usage_from_blocks(body_to_blocks(body))

    for body in bodies:
        blocks = body_to_blocks(body)
        body_blocks = [b for b in blocks if b.get("kind") != "system"]
        system_blocks = [b for b in blocks if b.get("kind") == "system"]
        original_tools = body.get("tools") or []
        before = estimate_body(body)

        new_blocks, new_tools = policies_mod.apply_to_blocks(
            body_blocks, policy, tools=original_tools, used=used_all, seen=seen
        )
        new_system = [policy.transform_block(dict(b), seen) for b in system_blocks]

        untouched = (
            new_blocks == body_blocks
            and new_tools == original_tools
            and new_system == system_blocks
        )
        if untouched:
            after_total += before
            out.append(body)
            continue

        new_body = blocks_to_body(new_system + new_blocks, body)
        if new_tools != original_tools:
            new_body["tools"] = new_tools

        after = estimate_body(new_body)
        before_total += before
        after_total += after
        changed += 1
        out.append(new_body)

    return out, changed, max(0, before_total - after_total)


def simulate(bodies: list[dict[str, Any]], specs: list[str]) -> list[dict[str, Any]]:
    base_tokens = sum(estimate_body(b) for b in bodies)
    rows = []
    for spec in specs:
        policy = policies_mod.get(spec)
        _, changed, removed = apply_policy(bodies, spec)
        rows.append(
            {
                "policy": spec,
                "name": policy.name,
                "tokens_before": base_tokens,
                "tokens_after": base_tokens - removed,
                "delta_tokens": -removed,
                "delta_pct": round(-removed / max(base_tokens, 1) * 100, 1),
                "turns_changed": changed,
                "turns": len(bodies),
                "preserves_trajectory": policy.preserves_trajectory,
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# object cassettes
# --------------------------------------------------------------------------- #
def cassette_blocks(cas: dict[str, Any], turn_index: int) -> list[dict[str, Any]]:
    objects = cas["objects"]
    blocks = []
    for msg in cas["turns"][turn_index]["messages"]:
        blocks.append(
            {
                "role": msg.get("role", "user"),
                "kind": msg.get("k", "text"),
                "content": objects.get(msg.get("h"), ""),
                "h": msg.get("h", ""),
                "n": msg.get("n"),
                "id": msg.get("id"),
                "rid": msg.get("rid"),
            }
        )
    return blocks


def block_tokens(blocks: list[dict[str, Any]], tools: list[dict[str, Any]], system: str) -> int:
    chars = len(system) + len(canon(tools))
    chars += sum(len(b.get("content") or "") + 24 for b in blocks)
    return max(1, round(chars / 4))


def recorded_input(turn: dict[str, Any]) -> int:
    u = turn.get("usage") or {}
    return int(u.get("input") or 0) + int(u.get("cache_read") or 0) + int(u.get("cache_creation") or 0)


def recorded_output(turn: dict[str, Any]) -> int:
    return int((turn.get("usage") or {}).get("output") or 0)


def compare(
    cas: dict[str, Any], specs: list[str], window: int | None = None
) -> dict[str, Any]:
    """Project each policy against the cassette, calibrated to recorded usage."""
    turns = cas["turns"]
    per_turn_blocks = [cassette_blocks(cas, i) for i in range(len(turns))]
    per_turn_used = [policies_mod.usage_from_blocks(b) for b in per_turn_blocks]
    recorded_in = [recorded_input(t) for t in turns]
    recorded_out = [recorded_output(t) for t in turns]

    baseline_est = [
        block_tokens([b for b in blocks if b["kind"] != "system"], cas["tools"], cas["system"])
        for blocks in per_turn_blocks
    ]

    rec_total = sum(recorded_in)
    est_total = sum(baseline_est)
    raw_scale = rec_total / max(est_total, 1)
    scale = min(1.95, max(0.55, raw_scale))

    errors = [
        abs(e * scale - r) / max(r, 1) * 100
        for e, r in zip(baseline_est, recorded_in)
        if r > 0
    ]
    drift = round(statistics.median(errors), 2) if errors else 0.0

    base_cache = sum(int((t.get("usage") or {}).get("cache_read") or 0) for t in turns) / max(
        1, rec_total
    )
    out_tokens = sum(recorded_out)
    price = analyze_mod.price_for(cas["model"], cas["provider"], analyze_mod.DEFAULT_PRICES)

    results: dict[str, Any] = {}
    baseline_cost: float | None = None

    for spec in specs:
        policy = policies_mod.get(spec)
        policy.reset()
        seen: set[str] = set()
        tokens = 0.0
        changed = 0

        for i, blocks in enumerate(per_turn_blocks):
            body_blocks = [b for b in blocks if b["kind"] != "system"]
            new_blocks, new_tools = policies_mod.apply_to_blocks(
                body_blocks,
                policy,
                tools=cas["tools"],
                used=per_turn_used[i],
                window=window,
                seen=seen,
            )
            est = block_tokens(new_blocks, new_tools, cas["system"])
            tokens += recorded_in[i] * (est / max(baseline_est[i], 1))
            if est < baseline_est[i] or len(new_blocks) != len(body_blocks) or new_tools != cas["tools"]:
                changed += 1

        cache = 0.9 if policy.stable_prefix else base_cache
        cost = (
            tokens * (1 - cache) * price["in"] / 1e6
            + tokens * cache * price["in"] * price["cache_read_mult"] / 1e6
            + out_tokens * price["out"] / 1e6
        )
        if baseline_cost is None:
            baseline_cost = cost

        results[spec] = {
            "tokens_in": int(round(tokens)),
            "tokens_out": out_tokens,
            "cache_pct": round(cache, 3),
            "cost_usd": round(cost, 4),
            "preserves_trajectory": policy.preserves_trajectory,
            "turns_changed": changed,
            "delta_pct": round((cost - baseline_cost) / baseline_cost * 100, 1)
            if baseline_cost
            else 0.0,
        }

    return {
        "n_turns": len(turns),
        "model": cas["model"],
        "recorded_tokens_in": rec_total,
        "estimated_tokens_in": est_total,
        "baseline_drift_pct": drift,
        "calibration": {"scale": round(scale, 3), "anchored": 0.25 <= raw_scale <= 4.0},
        "policies": results,
    }


# --------------------------------------------------------------------------- #
# live replay
# --------------------------------------------------------------------------- #
def recorded_tool_calls(body: dict[str, Any]) -> list[tuple[str, str]]:
    for msg in reversed(body.get("messages") or []):
        if msg.get("role") != "assistant":
            continue
        calls: list[tuple[str, str]] = []
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    calls.append((block.get("name", ""), canon(block.get("input"))))
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            calls.append((fn.get("name", ""), canon(fn.get("arguments"))))
        return calls
    return []


def response_tool_calls(payload: dict[str, Any], provider: str) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    if provider == "anthropic":
        for block in payload.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append((block.get("name", ""), canon(block.get("input"))))
    else:
        for choice in payload.get("choices") or []:
            for tc in (choice.get("message") or {}).get("tool_calls") or []:
                fn = tc.get("function", {})
                calls.append((fn.get("name", ""), canon(fn.get("arguments"))))
    return calls


def post(body: dict[str, Any], upstream: str, provider: str, api_key: str, timeout: int = 300):
    path = "/v1/messages" if provider == "anthropic" else "/v1/chat/completions"
    req = urllib.request.Request(
        upstream.rstrip("/") + path, data=json.dumps(body).encode(), method="POST"
    )
    req.add_header("Content-Type", "application/json")
    if provider == "anthropic":
        req.add_header("x-api-key", api_key)
        req.add_header("anthropic-version", "2023-06-01")
    else:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"upstream {exc.code}: {exc.read().decode('utf-8', 'replace')[:400]}")


def run_live(
    bodies: list[dict[str, Any]],
    spec: str,
    upstream: str,
    api_key: str,
    provider: str,
    max_turns: int,
) -> dict[str, Any]:
    transformed, changed, _ = apply_policy(bodies, spec)
    limit = min(len(transformed), max_turns)

    total_in = total_out = total_cached = 0
    cost = 0.0
    diverged = 0
    compared = 0
    details = []

    for i in range(limit):
        body = transformed[i]
        recorded_next = recorded_tool_calls(bodies[i + 1]) if i + 1 < len(bodies) else []

        payload = post(body, upstream, provider, api_key)
        usage = payload.get("usage") or ((payload.get("message") or {}).get("usage") or {})
        pin = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        pout = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        cached = int(
            usage.get("cache_read_input_tokens")
            or (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            or 0
        )

        total_in += pin
        total_out += pout
        total_cached += cached
        price = analyze_mod.price_for(body.get("model", ""), provider, analyze_mod.DEFAULT_PRICES)
        cost += (
            max(0, pin - cached) * price["in"] / 1e6
            + cached * price["in"] * price["cache_read_mult"] / 1e6
            + pout * price["out"] / 1e6
        )

        actual = response_tool_calls(payload, provider)
        if recorded_next:
            compared += 1
            if sorted(actual) != sorted(recorded_next):
                diverged += 1
        details.append(
            {
                "turn": i + 1,
                "in": pin,
                "out": pout,
                "cached": cached,
                "recorded_calls": [c[0] for c in recorded_next],
                "actual_calls": [c[0] for c in actual],
                "diverged": bool(recorded_next and sorted(actual) != sorted(recorded_next)),
            }
        )

    return {
        "policy": spec,
        "turns": limit,
        "turns_changed": changed,
        "input_tokens": total_in,
        "output_tokens": total_out,
        "cached_tokens": total_cached,
        "chr": round(total_cached / max(1, total_in), 3),
        "cost_usd": round(cost, 4),
        "diverged": diverged,
        "compared": compared,
        "divergence_rate": round(diverged / compared, 3) if compared else None,
        "details": details,
    }


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def render_simulate(rows: list[dict[str, Any]]) -> str:
    lines = ["", f"{'policy':<22}{'tokens':>10}{'delta':>9}{'changed':>10}", "-" * 52]
    for r in rows:
        delta = f"{r['delta_pct']:+.1f}%" if r["policy"] != "baseline" else "-"
        lines.append(
            f"{r['policy']:<22}{r['tokens_after']:>10,}{delta:>9}"
            f"{r['turns_changed']:>6}/{r['turns']:<3}"
        )
    lines += [
        "-" * 52,
        "Projected only: this estimates cost, not correctness.",
        "Run `replay.py run` to see whether the model's decisions change.",
        "",
    ]
    return "\n".join(lines)


def render_compare(result: dict[str, Any]) -> str:
    pol = result["policies"]
    base = pol.get("baseline") or next(iter(pol.values()))
    lines = [
        "",
        f"policy comparison  -  {result['n_turns']} turns  -  {result['model']}",
        f"calibrated against {result['recorded_tokens_in']:,} recorded input tokens "
        f"(scale {result['calibration']['scale']}, drift {result['baseline_drift_pct']}%)",
        "-" * 74,
        f"{'policy':<20}{'tokens in':>11}{'cost':>9}{'delta':>8}{'cache':>7}  trajectory",
        "-" * 74,
    ]
    for spec, row in pol.items():
        delta = "-" if spec.startswith("baseline") else f"{row['delta_pct']:+.0f}%"
        traj = "preserved" if row["preserves_trajectory"] else "AT RISK"
        lines.append(
            f"{spec:<20}{row['tokens_in']:>11,}{row['cost_usd']:>9.2f}{delta:>8}"
            f"{row['cache_pct']:>7.0%}  {traj}"
        )
    lines += [
        "-" * 74,
        f"baseline: ${base['cost_usd']:.2f} across {result['n_turns']} turns",
        "",
        "Projection only. `trajectory` is the policy's own claim; `replay.py run`",
        "tests it against a live model before you trust the saving.",
        "",
    ]
    return "\n".join(lines)


def render_live(result: dict[str, Any]) -> str:
    rate = result["divergence_rate"]
    lines = [
        "",
        f"policy {result['policy']}  -  {result['turns']} turns replayed live",
        "-" * 64,
        f"  input tokens       {result['input_tokens']:>10,}",
        f"  output tokens      {result['output_tokens']:>10,}",
        f"  cache hit ratio    {result['chr']:>10.0%}",
        f"  cost               ${result['cost_usd']:>9,.2f}",
        f"  decisions changed  {result['diverged']:>10}/{result['compared']}",
    ]
    if rate is not None:
        lines.append(f"  divergence rate    {rate:>10.0%}")
    lines.append("-" * 64)
    if rate:
        for d in result["details"]:
            if d["diverged"]:
                lines.append(f"    turn {d['turn']:>3}: {d['recorded_calls']} -> {d['actual_calls']}")
        lines += [
            "",
            "  A policy that changes decisions must be judged on success, not just",
            "  cost. Re-run the task in the harness before adopting it.",
        ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
def load_cassette_file(path: Path) -> dict[str, Any]:
    """Accept either a session log or a pre-built .cassette.json."""
    path = Path(path).expanduser()
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    return cassette_mod.build(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Replay a recorded session under a context policy.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="show what a cassette contains")
    p_list.add_argument("cassette")

    p_sim = sub.add_parser("simulate", help="estimate token deltas from raw bodies")
    p_sim.add_argument("cassette")
    p_sim.add_argument("--policies", default="baseline,dedupe-reads,truncate:800")
    p_sim.add_argument("--json", action="store_true")

    p_cmp = sub.add_parser("compare", help="calibrated comparison from a cassette")
    p_cmp.add_argument("session", help="session.jsonl or built cassette.cassette.json")
    p_cmp.add_argument(
        "--policies", default="baseline,cache-stable,dedupe-reads,schema-gating,truncate-200"
    )
    p_cmp.add_argument("--window", type=int, default=None, help="context window (characters)")
    p_cmp.add_argument("--json", action="store_true")

    p_build = sub.add_parser("build", help="build a portable cassette from a session")
    p_build.add_argument("session")
    p_build.add_argument("--out", default="", help="output path (default: <session>.cassette.json)")

    p_run = sub.add_parser("run", help="replay live and measure decision changes")
    p_run.add_argument("cassette")
    p_run.add_argument("--policy", default="baseline")
    p_run.add_argument("--upstream", default="https://api.anthropic.com")
    p_run.add_argument("--provider", default="", choices=["", "anthropic", "openai"])
    p_run.add_argument("--api-key", default="")
    p_run.add_argument("--max-turns", type=int, default=5)
    p_run.add_argument("--yes", action="store_true")
    p_run.add_argument("--out", default="")

    args = ap.parse_args(argv)

    if args.cmd == "list":
        path = Path(args.cassette).expanduser()
        if path.is_dir():
            bodies = load_cassette(path)
            print(f"raw cassette  {path}")
            print(f"turns         {len(bodies)}")
            print(f"provider      {infer_provider(bodies[0]) if bodies else 'unknown'}")
        else:
            cas = load_cassette_file(path)
            store = cassette_mod.ToolResultStore(cas)
            print(f"cassette      {path}")
            print(f"turns         {cas['n_turns']}")
            print(f"provider      {cas['provider']}")
            print(f"model         {cas['model']}")
            print(f"tool pairs    {store.pairs} ({store.unpaired} unpaired)")
            print(f"tools used    {', '.join(sorted(store.used_tools)) or 'none'}")
        return 0

    if args.cmd == "simulate":
        bodies = load_cassette(Path(args.cassette).expanduser())
        specs = [s.strip() for s in args.policies.split(",") if s.strip()]
        rows = simulate(bodies, specs)
        print(json.dumps(rows, indent=2) if args.json else render_simulate(rows))
        return 0

    if args.cmd == "build":
        cas = cassette_mod.build(Path(args.session).expanduser())
        out = Path(args.out).expanduser() if args.out else Path(args.session).expanduser().with_suffix(".cassette.json")
        out.write_text(json.dumps(cas, ensure_ascii=False), encoding="utf-8")
        print(f"cassette written to {out} ({cas['n_turns']} turns, {len(cas['objects'])} objects)")
        return 0

    if args.cmd == "compare":
        cas = load_cassette_file(Path(args.session))
        specs = [s.strip() for s in args.policies.split(",") if s.strip()]
        if args.window is None and any(policies_mod.parse(s)[0] == "compact-at" for s in specs):
            print(
                "note: no --window given, so compact-at policies did not compact; "
                "their rows show the uncompacted session. Pass --window <characters> "
                "(context limit / 4) to model a real limit."
            )
        result = compare(cas, specs, window=args.window)
        print(json.dumps(result, indent=2) if args.json else render_compare(result))
        return 0

    bodies = load_cassette(Path(args.cassette).expanduser())
    provider = args.provider or infer_provider(bodies[0])
    api_key = args.api_key or os.environ.get(
        "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY", ""
    )
    if not api_key:
        print("no API key. Set ANTHROPIC_API_KEY / OPENAI_API_KEY, or pass --api-key.", file=sys.stderr)
        return 2

    preview = simulate(bodies, [args.policy])[0]
    est = preview["tokens_after"] / max(1, min(len(bodies), args.max_turns)) * 0.000003
    if est > 0.5 and not args.yes:
        print(f"this will replay up to {args.max_turns} turns (~${est:.2f}). Re-run with --yes.")
        return 3

    result = run_live(bodies, args.policy, args.upstream, api_key, provider, args.max_turns)
    print(render_live(result))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
analyze - turn a ctxprobe session into metrics, a cost breakdown, and a
ranked list of fixes with estimated savings.

    python scripts/analyze.py ~/.ctxprobe/sessions/20261024-1402.jsonl
    python scripts/analyze.py --latest
    python scripts/analyze.py --latest --json
    python scripts/analyze.py --latest --html out.html
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:  # survive python -P / safe_path environments
    sys.path.insert(0, str(HERE))

DEFAULT_PRICES = {
    "anthropic": {
        "in": 3.0,
        "out": 15.0,
        "cache_read_mult": 0.1,
        "cache_write_mult": 1.25,
    },
    "openai": {"in": 1.25, "out": 10.0, "cache_read_mult": 0.1, "cache_write_mult": 1.0},
    "_default": {"in": 3.0, "out": 15.0, "cache_read_mult": 0.1, "cache_write_mult": 1.25},
}

MODEL_HINTS = [
    ("opus", "anthropic", 15.0, 75.0),
    ("sonnet", "anthropic", 3.0, 15.0),
    ("haiku", "anthropic", 0.8, 4.0),
    ("gpt-5", "openai", 1.25, 10.0),
    ("gpt-4.1", "openai", 2.0, 8.0),
    ("o4", "openai", 1.1, 4.4),
]


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #
def load_session(path: Path) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                turns.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return turns


def latest_session(root: Path) -> Path:
    files = sorted(root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise SystemExit(f"no sessions found in {root}")
    return files[-1]


def price_for(model: str, provider: str, prices: dict[str, Any]) -> dict[str, float]:
    low = (model or "").lower()
    for hint, prov, pin, pout in MODEL_HINTS:
        if hint in low:
            base = dict(prices.get(prov, DEFAULT_PRICES["_default"]))
            base["in"], base["out"] = pin, pout
            return base
    return dict(prices.get(provider, prices["_default"]))


# --------------------------------------------------------------------------- #
# core analysis
# --------------------------------------------------------------------------- #
def total_input(turn: dict[str, Any]) -> int:
    u = turn.get("usage") or {}
    inp = int(u.get("input") or 0)
    if turn.get("provider") == "anthropic":
        return inp + int(u.get("cache_read") or 0) + int(u.get("cache_creation") or 0)
    return inp


def turn_cost(turn: dict[str, Any], price: dict[str, float]) -> float:
    u = turn.get("usage") or {}
    inp = int(u.get("input") or 0)
    out = int(u.get("output") or 0)
    cr = int(u.get("cache_read") or 0)
    cc = int(u.get("cache_creation") or 0)
    pin, pout = price["in"] / 1e6, price["out"] / 1e6
    if turn.get("provider") == "anthropic":
        return inp * pin + cr * pin * price["cache_read_mult"] + cc * pin * price[
            "cache_write_mult"
        ] + out * pout
    uncached = max(0, inp - cr)
    return uncached * pin + cr * pin * price["cache_read_mult"] + out * pout


def analyse(turns: list[dict[str, Any]], prices: dict[str, Any]) -> dict[str, Any]:
    if not turns:
        raise SystemExit("session is empty")

    provider = turns[0].get("provider", "anthropic")
    model = turns[0].get("model", "unknown")
    price = price_for(model, provider, prices)

    buckets = {
        "carried_history": 0.0,
        "schemas": 0.0,
        "tool_results": 0.0,
        "new_text": 0.0,
        "output": 0.0,
    }
    tokens = {
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_creation": 0,
        "schema": 0,
        "tool_result": 0,
        "redundant": 0,
    }
    prefix_breaks: list[dict[str, Any]] = []
    seen_hashes: dict[str, int] = {}
    redundant_detail: dict[str, dict[str, Any]] = {}
    compaction_turns: list[int] = []
    offenders: list[dict[str, Any]] = []

    prev_messages: list[dict[str, Any]] = []
    prev_prefix = None
    prev_n = 0

    for idx, turn in enumerate(turns):
        u = turn.get("usage") or {}
        req = turn.get("req") or {}
        msgs: list[dict[str, Any]] = req.get("messages") or []
        schema_tokens = int(req.get("schema_tokens") or 0)
        tin = total_input(turn)
        tout = int(u.get("output") or 0)
        cr = int(u.get("cache_read") or 0)
        cc = int(u.get("cache_creation") or 0)

        tokens["input"] += tin
        tokens["output"] += tout
        tokens["cache_read"] += cr
        tokens["cache_creation"] += cc
        tokens["schema"] += schema_tokens

        prev_counts = Counter(m["h"] for m in prev_messages)
        new_msgs = []
        for m in msgs:
            h = m.get("h")
            if prev_counts.get(h, 0) > 0:
                prev_counts[h] -= 1  # matched an identical carried message
            else:
                new_msgs.append(m)  # genuinely new, including extra copies
        new_tokens = sum(int(m.get("t") or 0) for m in new_msgs)

        carried = max(0, tin - new_tokens - schema_tokens)
        buckets["carried_history"] += carried * price["in"] / 1e6
        buckets["schemas"] += schema_tokens * price["in"] / 1e6
        buckets["output"] += tout * price["out"] / 1e6

        for m in new_msgs:
            kind = m.get("k")
            cost = int(m.get("t") or 0) * price["in"] / 1e6
            if kind == "tool_result":
                buckets["tool_results"] += cost
                tokens["tool_result"] += int(m.get("t") or 0)
                h = m.get("h")
                if h in seen_hashes:
                    tokens["redundant"] += int(m.get("t") or 0)
                    key = h
                    rec = redundant_detail.setdefault(
                        key, {"tokens": 0, "times": 1, "preview": m.get("p", "")}
                    )
                    rec["tokens"] += int(m.get("t") or 0)
                    rec["times"] += 1
                else:
                    seen_hashes[h] = idx
                if int(m.get("t") or 0) > 1500:
                    offenders.append(
                        {
                            "turn": idx + 1,
                            "tokens": int(m.get("t") or 0),
                            "remaining": len(turns) - idx,
                            "preview": m.get("p", ""),
                            "tool": m.get("n"),
                        }
                    )
            else:
                buckets["new_text"] += cost

        # prefix / cache stability
        ph = req.get("prefix_hash")
        if prev_prefix is not None and ph != prev_prefix:
            prefix_breaks.append(
                {
                    "turn": idx + 1,
                    "model_changed": turn.get("model") != turns[idx - 1].get("model"),
                    "tools_changed": (req.get("n_tools") or 0)
                    != (turns[idx - 1].get("req", {}).get("n_tools") or 0),
                }
            )
        prev_prefix = ph

        # compaction heuristic: message count collapses while context is large
        if prev_n and len(msgs) < prev_n * 0.6 and tin > 20000:
            compaction_turns.append(idx)
        prev_n = len(msgs)
        prev_messages = msgs

    cost_total = sum(buckets.values())
    tin_all = max(1, tokens["input"])
    chr_ = tokens["cache_read"] / tin_all
    psi = 1 - (len(prefix_breaks) / max(1, len(turns) - 1))
    rdr = tokens["redundant"] / max(1, tokens["tool_result"])
    st = tokens["schema"] / tin_all
    compaction_output = sum(
        int((turns[i].get("usage") or {}).get("output") or 0) for i in compaction_turns
    )
    ct = compaction_output / max(1, tokens["output"])

    recs = _recommendations(
        turns=turns,
        tokens=tokens,
        buckets=buckets,
        price=price,
        prefix_breaks=prefix_breaks,
        redundant_detail=redundant_detail,
        offenders=offenders,
        chr_=chr_,
        psi=psi,
        rdr=rdr,
        st=st,
        ct=ct,
        cost_total=cost_total,
    )

    return {
        "session": turns[0].get("session", "unknown"),
        "provider": provider,
        "model": model,
        "turns": len(turns),
        "duration_s": round(turns[-1]["ts"] - turns[0]["ts"], 1)
        if turns[-1].get("ts") and turns[0].get("ts")
        else 0,
        "cost_usd": round(cost_total, 4),
        "tokens": tokens,
        "buckets_usd": {k: round(v, 4) for k, v in buckets.items()},
        "buckets_pct": {
            k: (round(v / cost_total * 100, 1) if cost_total else 0.0)
            for k, v in buckets.items()
        },
        "metrics": {
            "chr": round(chr_, 4),
            "psi": round(psi, 4),
            "rdr": round(rdr, 4),
            "st": round(st, 4),
            "ct": round(ct, 4),
            "tokens_per_turn": round(tin_all / max(1, len(turns))),
        },
        "prefix_breaks": prefix_breaks,
        "compaction_turns": compaction_turns,
        "redundant_reads": sorted(
            redundant_detail.values(), key=lambda r: -r["tokens"]
        )[:10],
        "offenders": sorted(offenders, key=lambda o: -o["tokens"])[:10],
        "recommendations": recs,
        "price_used": price,
    }


def _recommendations(
    *,
    turns: list[dict[str, Any]],
    tokens: dict[str, int],
    buckets: dict[str, float],
    price: dict[str, float],
    prefix_breaks: list[dict[str, Any]],
    redundant_detail: dict[str, dict[str, Any]],
    offenders: list[dict[str, Any]],
    chr_: float,
    psi: float,
    rdr: float,
    st: float,
    ct: float,
    cost_total: float,
) -> list[dict[str, Any]]:
    pin = price["in"] / 1e6
    n_turns = max(1, len(turns))
    recs: list[dict[str, Any]] = []

    if prefix_breaks:
        avg_ctx = tokens["input"] / n_turns
        save = len(prefix_breaks) * avg_ctx * pin * (1 - price["cache_read_mult"]) * 0.75
        reasons = set()
        for b in prefix_breaks:
            if b.get("model_changed"):
                reasons.add("model switch")
            if b.get("tools_changed"):
                reasons.add("tool list change")
        why = ", ".join(sorted(reasons)) or "system prompt / dynamic text"
        recs.append(
            {
                "id": "cache-breaks",
                "title": f"Stop {len(prefix_breaks)} prefix change(s) that reset the prompt cache",
                "detail": (
                    f"Cache stability (PSI) is {psi:.0%}. Each break re-uploads the whole "
                    f"context at full price. Likely cause here: {why}. Move dates, cwd and "
                    f"any per-run identifiers out of the system prompt and tool definitions."
                ),
                "est_saving_usd": round(save, 2),
                "effort": "low",
                "confidence": "high",
            }
        )

    if st > 0.08 or tokens["schema"] * pin > 0.5:
        save = tokens["schema"] * pin * 0.5
        recs.append(
            {
                "id": "schema-tax",
                "title": f"Cut tool/MCP schema overhead ({st:.0%} of all input tokens)",
                "detail": (
                    "Schema tax is charged every turn. Drop MCP servers you did not use in "
                    "the last week, prefer a tool-search / lazy-loading setup, and keep tool "
                    "descriptions short."
                ),
                "est_saving_usd": round(save, 2),
                "effort": "low",
                "confidence": "high",
            }
        )

    if rdr > 0.05 and redundant_detail:
        save = 0.0
        for rec in redundant_detail.values():
            save += rec["tokens"] * pin * min(rec["times"], 8) * 0.8
        top = max(redundant_detail.values(), key=lambda r: r["tokens"])
        repeats = sum(max(1, r["times"]) - 1 for r in redundant_detail.values())
        recs.append(
            {
                "id": "redundant-reads",
                "title": f"{repeats} repeat(s) of unchanged content "
                f"({rdr:.0%} of tool-result tokens)",
                "detail": (
                    "The agent read the same bytes again. Largest repeat: "
                    f"~{top['tokens']} tokens, {top['times']}x. Teach the harness to say "
                    "'already read, unchanged' via a content-addressed cache, and prefer "
                    "re-running the test over re-reading the file after an edit."
                ),
                "est_saving_usd": round(save, 2),
                "effort": "medium",
                "confidence": "medium",
            }
        )

    if offenders:
        big = offenders[0]
        save = sum(
            o["tokens"] * pin * min(o["remaining"], 40) * 0.8 for o in offenders[:5]
        )
        recs.append(
            {
                "id": "output-shaping",
                "title": f"Shape command output (largest single result: ~{big['tokens']} tokens)",
                "detail": (
                    "Pipe builds and test runs through head/tail or quiet flags "
                    "(pytest -q --tb=line, --silent, git diff --stat). One verbose log is "
                    "re-sent on every later turn."
                ),
                "est_saving_usd": round(save, 2),
                "effort": "low",
                "confidence": "high",
            }
        )

    if ct > 0.08 or (compaction_like(turns) and chr_ < 0.5):
        recs.append(
            {
                "id": "compaction",
                "title": f"Compact earlier (compaction is {ct:.0%} of output tokens)",
                "detail": (
                    "Compacting near the window limit forces a full-context summary and "
                    "leaves no room to work. Compact at 40-60% instead. Verify with a "
                    "before/after replay before changing a team default."
                ),
                "est_saving_usd": round(cost_total * 0.06, 2),
                "effort": "low",
                "confidence": "low",
            }
        )

    output_share = buckets["output"] / max(cost_total, 1e-9)
    if output_share > 0.3:
        recs.append(
            {
                "id": "verbose-output",
                "title": f"Output is {output_share:.0%} of spend",
                "detail": (
                    "Output tokens are priced 3-8x higher than input. Ask for terse "
                    "responses, no restating of files, and diffs instead of whole-file writes."
                ),
                "est_saving_usd": round(buckets["output"] * 0.35, 2),
                "effort": "low",
                "confidence": "medium",
            }
        )

    recs.sort(key=lambda r: -r["est_saving_usd"])
    for rec in recs:
        rec["est_saving_pct"] = (
            round(rec["est_saving_usd"] / cost_total * 100, 1) if cost_total else 0.0
        )
    return recs


def compaction_like(turns: Iterable[dict[str, Any]]) -> bool:
    n = 0
    prev = 0
    for t in turns:
        cur = int((t.get("req") or {}).get("n_messages") or 0)
        if prev and cur < prev * 0.6:
            n += 1
        prev = cur
    return n > 0


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def bar(pct: float, width: int = 18) -> str:
    filled = int(round(max(0.0, min(100.0, pct)) / 100 * width))
    return "#" * filled + "." * (width - filled)


def ascii_safe(text: str) -> str:
    """Windows consoles are not UTF-8 by default; keep terminal output ASCII."""
    return text.encode("ascii", "backslashreplace").decode("ascii")


def render_terminal(r: dict[str, Any], top: int = 5) -> str:
    b = r["buckets_usd"]
    p = r["buckets_pct"]
    m = r["metrics"]
    lines: list[str] = []
    add = lines.append

    add("")
    add(f"CONTEXT AUDIT - session {r['session']} - {r['turns']} turns - "
        f"${r['cost_usd']:.2f} - {r['model']}")
    add("-" * 74)
    labels = [
        ("carried_history", "carried history"),
        ("schemas", "tool schemas / MCP"),
        ("output", "model output"),
        ("tool_results", "command + file output"),
        ("new_text", "new prompts"),
    ]
    for key, label in labels:
        add(f"  {label:<24} ${b[key]:>7.2f}  {p[key]:>5.1f}%  {bar(p[key])}")
    add("-" * 74)

    def flag(value: float, good: float, bad: float, higher_better: bool) -> str:
        if higher_better:
            return "ok" if value >= good else ("warn" if value >= bad else "BAD")
        return "ok" if value <= good else ("warn" if value <= bad else "BAD")

    add(
        f"  cache hit rate (CHR)     {m['chr']:>6.0%}   target >75%    "
        f"[{flag(m['chr'], 0.75, 0.4, True)}]"
    )
    add(
        f"  prefix stability (PSI)   {m['psi']:>6.0%}   target >90%    "
        f"[{flag(m['psi'], 0.9, 0.7, True)}]"
    )
    add(
        f"  redundant reads (RDR)    {m['rdr']:>6.0%}   target <10%    "
        f"[{flag(m['rdr'], 0.1, 0.25, False)}]"
    )
    add(
        f"  schema tax (ST)          {m['st']:>6.0%}   target <10%    "
        f"[{flag(m['st'], 0.1, 0.2, False)}]"
    )
    add(
        f"  compaction tax (CT)      {m['ct']:>6.0%}   target <8%     "
        f"[{flag(m['ct'], 0.08, 0.15, False)}]"
    )
    add(f"  tokens per turn          {m['tokens_per_turn']:>7,}")

    if r["recommendations"]:
        add("-" * 74)
        add("TOP FIXES (ranked by estimated saving)")
        for i, rec in enumerate(r["recommendations"][:top], 1):
            add(
                f"  {i}. {rec['title']}  "
                f"[-${rec['est_saving_usd']:.2f} / {rec['est_saving_pct']:.0f}%]"
            )
            for chunk in wrap(rec["detail"], 66):
                add(f"     {chunk}")
    if r["offenders"]:
        add("-" * 74)
        add("LARGEST SINGLE TOOL RESULTS")
        for off in r["offenders"][:3]:
            add(f"  turn {off['turn']:>3}  ~{off['tokens']:>6,} tok  {off['preview'][:54]}")
    add("")
    add("  Estimates are derived from this session's own usage, not from benchmarks.")
    add("  Re-measure after each change; keep what moves, revert what doesn't.")
    add("")
    return ascii_safe("\n".join(lines))


def wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Analyse a ctxprobe session.")
    ap.add_argument("session", nargs="?", help="path to a .jsonl session file")
    ap.add_argument("--latest", action="store_true", help="use the newest session")
    ap.add_argument(
        "--home",
        default=str(Path.home() / ".ctxprobe" / "sessions"),
        help="session directory",
    )
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ap.add_argument("--html", help="write a self-contained HTML report")
    ap.add_argument("--prices", help="JSON file overriding the price table")
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args(argv)

    root = Path(args.home).expanduser()
    if args.session:
        path = Path(args.session).expanduser()
    elif args.latest:
        path = latest_session(root)
    else:
        ap.error("pass a session path or --latest")

    prices = DEFAULT_PRICES
    if args.prices:
        prices = json.loads(Path(args.prices).read_text(encoding="utf-8"))

    result = analyse(load_session(path), prices)
    result["source"] = str(path)

    if args.html:
        from report import write_html  # local import keeps analyze dependency-free

        write_html(result, Path(args.html))
        if not args.json:
            print(f"report written to {args.html}")

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_terminal(result, args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())

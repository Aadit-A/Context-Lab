#!/usr/bin/env python3
"""Render a ctxprobe analysis as a single self-contained HTML file."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # sibling module; report.py is only ever run from the scripts directory
    from ctxprobe import SCHEMA_VERSION, VERSION
except ImportError:  # pragma: no cover - direct execution from elsewhere
    VERSION, SCHEMA_VERSION = "unknown", "?"

CSS = """
:root{--bg:#0b0e14;--panel:#121722;--line:#1f2733;--fg:#e6edf3;--dim:#8b98a9;
--accent:#7ee787;--warn:#e3b341;--bad:#ff7b72;--bar:#2f81f7}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.wrap{max-width:900px;margin:0 auto;padding:40px 24px 80px}
h1{font-size:20px;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:12px;margin-bottom:28px}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:28px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
.card .k{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
.card .v{font-size:22px;font-weight:600;margin-top:6px}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:18px 20px;margin-bottom:16px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);
margin:0 0 14px;font-weight:600}
.row{display:grid;grid-template-columns:200px 70px 46px 1fr;gap:10px;align-items:center;
padding:5px 0;font-size:13px}
.row .lbl{color:var(--fg)}
.row .amt{text-align:right;color:var(--dim)}
.row .pct{text-align:right;color:var(--dim);font-size:12px}
.track{background:#0d1420;border-radius:4px;height:9px;overflow:hidden}
.fill{height:100%;background:var(--bar);border-radius:4px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:500;font-size:11px;text-transform:uppercase;
letter-spacing:.05em}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.chip{display:inline-block;padding:1px 7px;border-radius:20px;font-size:11px;border:1px solid}
.ok{color:var(--accent);border-color:#1c3a26;background:#0d1f13}
.warn{color:var(--warn);border-color:#3d3218;background:#1f1a0c}
.bad{color:var(--bad);border-color:#4a1f1c;background:#210f0e}
.fix{padding:14px 0;border-bottom:1px solid var(--line)}
.fix:last-child{border-bottom:none}
.fix .t{font-weight:600;margin-bottom:4px}
.fix .d{color:var(--dim);font-size:12.5px}
.fix .s{color:var(--accent);font-size:12px;margin-top:6px}
.off{color:var(--dim);font-size:12px;padding:4px 0;border-bottom:1px solid var(--line)}
.note{color:var(--dim);font-size:11.5px;margin-top:22px;line-height:1.6}
code{background:#0d1420;padding:1px 5px;border-radius:4px;font-size:12px}
@media(max-width:640px){.cards{grid-template-columns:repeat(2,1fr)}
.row{grid-template-columns:130px 60px 40px 1fr}}
"""


def esc(v: Any) -> str:
    return html.escape(str(v))


def chip(value: float, good: float, bad: float, higher_better: bool) -> str:
    if higher_better:
        cls = "ok" if value >= good else ("warn" if value >= bad else "bad")
    else:
        cls = "ok" if value <= good else ("warn" if value <= bad else "bad")
    return f'<span class="chip {cls}">{cls.upper()}</span>'


def write_html(result: dict[str, Any], path: Path) -> None:
    b = result["buckets_usd"]
    p = result["buckets_pct"]
    m = result["metrics"]

    order = [
        ("carried_history", "carried history"),
        ("schemas", "tool schemas / MCP"),
        ("output", "model output"),
        ("tool_results", "command + file output"),
        ("new_text", "new prompts"),
    ]

    rows = "\n".join(
        f'<div class="row"><span class="lbl">{label}</span>'
        f'<span class="amt">${b[key]:,.2f}</span>'
        f'<span class="pct">{p[key]:.0f}%</span>'
        f'<span class="track"><span class="fill" style="width:{min(100, p[key]):.1f}%"></span></span></div>'
        for key, label in order
    )

    metrics_rows = "\n".join(
        [
            f"<tr><td>Cache hit ratio</td><td class='num'>{m['chr']:.0%}</td>"
            f"<td>target &gt; 75%</td><td>{chip(m['chr'], .75, .4, True)}</td></tr>",
            f"<tr><td>Prefix stability</td><td class='num'>{m['psi']:.0%}</td>"
            f"<td>target &gt; 90%</td><td>{chip(m['psi'], .9, .7, True)}</td></tr>",
            f"<tr><td>Redundant reads</td><td class='num'>{m['rdr']:.0%}</td>"
            f"<td>target &lt; 10%</td><td>{chip(m['rdr'], .1, .25, False)}</td></tr>",
            f"<tr><td>Schema tax</td><td class='num'>{m['st']:.0%}</td>"
            f"<td>target &lt; 10%</td><td>{chip(m['st'], .1, .2, False)}</td></tr>",
            f"<tr><td>Compaction tax</td><td class='num'>{m['ct']:.0%}</td>"
            f"<td>target &lt; 8%</td><td>{chip(m['ct'], .08, .15, False)}</td></tr>",
            f"<tr><td>Tokens per turn</td><td class='num'>{m['tokens_per_turn']:,}</td>"
            f"<td>&mdash;</td><td></td></tr>",
        ]
    )

    fixes = "\n".join(
        f'<div class="fix"><div class="t">{i}. {esc(r["title"])}</div>'
        f'<div class="d">{esc(r["detail"])}</div>'
        f'<div class="s">est. -${r["est_saving_usd"]:,.2f} '
        f'({r["est_saving_pct"]:.0f}% of this session) &middot; '
        f'effort {esc(r["effort"])} &middot; confidence {esc(r["confidence"])}</div></div>'
        for i, r in enumerate(result["recommendations"], 1)
    ) or '<div class="d">No issues above threshold. This session is close to optimal.</div>'

    offenders = "\n".join(
        f'<div class="off">turn {o["turn"]} &middot; ~{o["tokens"]:,} tokens &middot; '
        f'{esc(o["preview"][:90])}</div>'
        for o in result["offenders"][:5]
    ) or '<div class="d">None above 1,500 tokens.</div>'

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Context audit - {esc(result['session'])}</title>
<style>{CSS}</style></head><body><div class="wrap">
<h1>Context audit</h1>
<div class="sub">{esc(result['session'])} &middot; {esc(result['model'])} &middot;
{result['turns']} turns &middot; generated {generated}</div>

<div class="cards">
  <div class="card"><div class="k">Spend</div><div class="v">${result['cost_usd']:,.2f}</div></div>
  <div class="card"><div class="k">Cache hit</div><div class="v">{m['chr']:.0%}</div></div>
  <div class="card"><div class="k">Redundant</div><div class="v">{m['rdr']:.0%}</div></div>
  <div class="card"><div class="k">Recoverable</div><div class="v">
    ${sum(r['est_saving_usd'] for r in result['recommendations'][:3]):,.2f}</div></div>
</div>

<section><h2>Where the money went</h2>{rows}</section>

<section><h2>Metrics</h2><table>
<thead><tr><th>Metric</th><th>Value</th><th>Target</th><th></th></tr></thead>
<tbody>{metrics_rows}</tbody></table></section>

<section><h2>Ranked fixes</h2>{fixes}</section>

<section><h2>Largest tool results</h2>{offenders}</section>

<div class="note">
Estimates are derived from this session's own usage and a dated price table, not from
third-party benchmarks. Treat them as hypotheses: change one thing, re-run the same
task, keep the change only if tokens-to-green goes down.<br><br>
Input tokens: {result['tokens']['input']:,} &middot;
cache read: {result['tokens']['cache_read']:,} &middot;
cache write: {result['tokens']['cache_creation']:,} &middot;
output: {result['tokens']['output']:,}<br>
Schema tokens charged: {result['tokens']['schema']:,} &middot;
tool-result tokens: {result['tokens']['tool_result']:,} &middot;
redundant: {result['tokens']['redundant']:,}<br><br>
Generated by <code>ctxprobe</code> v{VERSION} &middot; schema v{SCHEMA_VERSION}
</div>
</div></body></html>"""

    path.write_text(doc, encoding="utf-8")


if __name__ == "__main__":
    import sys

    print("usage: analyze.py <session.jsonl> --html out.html", file=sys.stderr)

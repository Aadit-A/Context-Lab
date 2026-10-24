# AGENTS.md

Instructions for any coding agent working in this repository.

## What this project is

A local measurement tool for LLM token usage. Three Python scripts, standard
library only. Read `SKILL.md` for the user-facing procedure; read
`references/metrics.md` before changing any metric.

## Non-negotiables

1. **Never add a third-party dependency.** The zero-install property is the
   product. If a change needs a package, it is the wrong change.
2. **Never send user data off the machine.** No telemetry, no analytics, no
   remote logging. This includes tests and examples.
3. **Never weaken the privacy defaults.** Previews stay at 100 characters, API
   keys stay redacted, `--record-raw` stays opt-in.
4. **A metric change is a breaking change.** Bump `SCHEMA_VERSION` and note it in
   CHANGELOG.md.
5. **The proxy must not alter traffic.** Byte-for-byte passthrough, including
   streaming responses.

## Commands

```bash
python -m unittest discover -s tests        # all tests must pass
python scripts/analyze.py examples/sample-session.jsonl   # visual smoke test
python scripts/install.py --target dir --dest /tmp/x --dry-run
```

## Style

- Python 3.9-compatible syntax where practical; `from __future__ import annotations` at the top.
- Type hints on public functions.
- No comments that restate the code. Comment the *why*.
- Terminal output is ASCII only — Windows consoles are not UTF-8 by default.

## Where things live

| Path | Owns |
|---|---|
| `scripts/ctxprobe.py` | capture, proxying, usage parsing |
| `scripts/analyze.py` | attribution, metrics, recommendations |
| `scripts/report.py` | HTML rendering only |
| `scripts/bench.py` | tokens-to-green loop |
| `references/` | definitions and per-harness wiring — keep in sync with code |

If you change a formula in `analyze.py`, change the matching line in
`references/metrics.md` in the same commit.

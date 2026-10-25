# AGENTS.md

Instructions for any coding agent working in this repository.

## What this project is

A local toolkit for measuring and reducing LLM token usage. It is standard
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
5. **The default recorder must not alter traffic.** `ctxprobe serve` is
   byte-for-byte passthrough, including streaming responses. Request changes
   are available only through the explicit `--policy` live gateway; responses
   remain untouched.

## Commands

```bash
python -m unittest discover -s tests        # all tests must pass
python scripts/analyze.py examples/sample-session.jsonl   # visual smoke test
python scripts/install.py --target dir --dest .audit/install-check --dry-run
```

## Style

- Python 3.9-compatible syntax where practical; `from __future__ import annotations` at the top.
- Type hints on public functions.
- No comments that restate the code. Comment the *why*.
- Terminal output is ASCII only — Windows consoles are not UTF-8 by default.

## Where things live

| Path | Owns |
|---|---|
| `scripts/ctxprobe.py` | capture, proxying, usage and response parsing |
| `scripts/live_policy.py` | explicit request-context reductions for live runs |
| `scripts/cassette.py` | object store, tool-call pairing, request reconstruction |
| `scripts/policies.py` | context transformations and their claims |
| `scripts/replay.py` | calibration, projection, live divergence checking |
| `scripts/analyze.py` | attribution, metrics, recommendations |
| `scripts/report.py` | HTML rendering only |
| `scripts/bench.py` | tokens-to-green loop |
| `references/` | definitions and per-harness wiring — keep in sync with code |

If you change a formula in `analyze.py`, change the matching line in
`references/metrics.md` in the same commit.

# Contributing

The most valuable contribution right now is **not a feature**. It is a
verification:

> "I ran Context Lab against <harness> version <x> on <OS>, here is the session
> file and what broke."

## Verification reports

Open an issue with:

1. Harness name, version, and OS.
2. How you pointed it at the proxy (env var, config key, flag).
3. `python scripts/analyze.py --latest --json` output, with anything sensitive removed.
4. Whether streaming worked.

If it works, also open a PR adding your row to the table in
`references/harnesses.md`. That file is how the next person saves an afternoon.

## Development

```bash
git clone <repo> && cd context-lab
python -m unittest discover -s tests
```

Requirements: Python 3.9+, no dependencies, ever.

### Before you open a PR

- [ ] Tests pass.
- [ ] A new metric has: a formula in `references/metrics.md`, a healthy range, and a test.
- [ ] A new recommendation has: an `est_saving_usd` formula and a confidence label.
- [ ] Terminal output is ASCII (Windows).
- [ ] No new dependency in `requirements.txt` — there is no `requirements.txt`.

### Commit style

```
ctxprobe: handle gzip-encoded upstream responses
analyze: fix redundant-read detection for repeated tool results
docs: add OmniRush compatibility test
```

Prefix with the file or area, imperative mood, no trailing period.

## What will be rejected

- Telemetry or "anonymous usage stats".
- A hosted mode.
- Provider-specific hacks that break the passthrough guarantee.
- Rewrites of `analyze.py` into a framework. It is 500 lines on purpose.

## Ideas that are wanted

1. **Replay harness** — re-run a recorded session under a different context
   policy against the same tool outputs. This is the roadmap's headline item.
2. **Log adapters** — parse harness-native logs when they contain usage blocks.
3. **More policies** — schema gating, retrieval budgets, compaction scheduling.
4. **A leaderboard** — same model, same tasks, several strategies, reproducible.

## License

By contributing you agree your work is licensed under MIT.

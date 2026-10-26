# Impact

## Real-provider validation

Real-provider validation was attempted on **October 26, 2026** from:

- **OS:** `Windows-11-10.0.26200-SP0`
- **Python:** `3.14.6`
- **Context Lab:** `0.3.0`
- **Harness:** Claude Code `2.1.301`
- **Task:** an isolated scratch-repository task that asked Claude Code to fix a
  failing Python unittest and run it again.

No real token, cache, cost, task-success, or policy-delta statistic was
available. Every attempt failed before the provider returned a model response,
so **real impact is NOT MEASURED**.

## Attempts

### Attempt 1 — existing Claude configuration

Command:

```bash
python "C:\PROJECTS\Context Lab\scripts\wrap.py" \
  --cassette \
  --session real-baseline \
  --home "C:\Users\aadit\AppData\Local\Temp\ctxlab-real-20261026\baseline" \
  -- claude -p "In this scratch repository, run the unittest in test_calc.py and fix the implementation so it passes. Keep the change minimal, then run the test again." \
  --output-format json \
  --dangerously-skip-permissions
```

Result: Claude Code selected `google/gemma-4-26b-a4b-it:free`; the provider
returned HTTP `429` with `unrecognized_model`. No request was recorded by
Context Lab.

### Attempt 2 — explicit Anthropic model with the existing configuration

Command:

```bash
python "C:\PROJECTS\Context Lab\scripts\wrap.py" \
  --cassette \
  --session real-baseline-sonnet \
  --home "C:\Users\aadit\AppData\Local\Temp\ctxlab-real-20261026\baseline-sonnet" \
  -- claude -p "In this scratch repository, run the unittest in test_calc.py and fix the implementation so it passes. Keep the change minimal, then run the test again." \
  --model claude-sonnet-4-5 \
  --output-format json \
  --dangerously-skip-permissions
```

Result: the existing configuration routed the request to OpenRouter. OpenRouter
returned HTTP `402`: the request allowed up to `32,000` output tokens while the
account could afford `2,565`. No request was recorded by Context Lab.

### Attempt 3 — temporary first-party Anthropic settings

The temporary settings file contained only the first-party endpoint and model;
it contained no credential:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
    "ANTHROPIC_MODEL": "claude-sonnet-4-5"
  }
}
```

Command:

```bash
python "C:\PROJECTS\Context Lab\scripts\wrap.py" \
  --cassette \
  --session real-baseline-firstparty \
  --home "C:\Users\aadit\AppData\Local\Temp\ctxlab-real-20261026\baseline-firstparty" \
  -- claude -p "In this scratch repository, run the unittest in test_calc.py and fix the implementation so it passes. Keep the change minimal, then run the test again." \
  --settings "C:\Users\aadit\AppData\Local\Temp\ctxlab-real-20261026\first-party-settings.json" \
  --model claude-sonnet-4-5 \
  --output-format json \
  --dangerously-skip-permissions
```

Result: Claude Code returned HTTP `401 Not logged in` for the first-party
endpoint. No request was recorded by Context Lab.

## What is measured

| Statistic | Result |
|---|---|
| Real provider requests reaching `ctxprobe` | **0** |
| Provider-reported input tokens | **NOT MEASURED** |
| Provider-reported output tokens | **NOT MEASURED** |
| Provider-reported cache tokens | **NOT MEASURED** |
| Real baseline task success | **NOT MEASURED** |
| Real policy comparison | **NOT MEASURED** |
| Real savings percentage | **NOT MEASURED** |

The failed attempts are configuration/credit failures, not evidence that the
policies save or cost tokens.

## Reproduce after configuring a real provider

Configure Claude Code with either a working first-party Anthropic login/API key
or a funded OpenRouter account, then run the same isolated task through a
baseline capture and one policy capture:

```bash
python "C:\PROJECTS\Context Lab\scripts\wrap.py" \
  --cassette \
  --session real-baseline \
  --home "C:\Users\aadit\AppData\Local\Temp\ctxlab-real-20261026\baseline" \
  -- claude -p "In this scratch repository, run the unittest in test_calc.py and fix the implementation so it passes. Keep the change minimal, then run the test again." \
  --output-format json

python "C:\PROJECTS\Context Lab\scripts\wrap.py" \
  --cassette \
  --session real-policy \
  --home "C:\Users\aadit\AppData\Local\Temp\ctxlab-real-20261026\policy" \
  --policy cache-stable \
  -- claude -p "In this scratch repository, run the unittest in test_calc.py and fix the implementation so it passes. Keep the change minimal, then run the test again." \
  --output-format json
```

Then inspect both real sessions:

```bash
python scripts/analyze.py "C:\Users\aadit\AppData\Local\Temp\ctxlab-real-20261026\baseline\real-baseline.jsonl" --json
python scripts/analyze.py "C:\Users\aadit\AppData\Local\Temp\ctxlab-real-20261026\policy\real-policy.jsonl" --json
```

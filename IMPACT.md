# Impact

## Real Claude Code validation

Measured on **October 27, 2026** using:

- **OS:** `Windows-11-10.0.26200-SP0`
- **Python:** `3.14.6`
- **Context Lab:** `0.3.0`
- **Claude Code:** `2.1.310`
- **Model:** `claude-sonnet-4-5`
- **Task:** fix `calc.py` so the existing `test_calc.py` unittest passes,
  without changing the test file
- **Runs:** two fresh isolated scratch directories, baseline and
  `cache-stable`
- **Provider path:** the configured working Claude Code gateway, reached through
  the local Context Lab proxy; credentials were not recorded

Both runs completed the same task successfully. The final unittest passed in
each scratch directory, and every captured provider response returned HTTP 200
with provider-reported usage (`estimated: false`).

## Measured results

| Metric | Baseline | `cache-stable` | Change |
|---|---:|---:|---:|
| Turns captured | 5 | 5 | 0 |
| Input tokens | 129,829 | 129,326 | **-503 (-0.39%)** |
| Output tokens | 337 | 336 | **-1 (-0.30%)** |
| Cache-read tokens | 0 | 0 | 0 |
| Cache-creation tokens | 0 | 0 | 0 |
| Claude Code reported cost | $0.394689 | $0.393018 | **-$0.001671 (-0.42%)** |
| Final task test | PASS | PASS | preserved |

The token reduction is measured from the provider responses captured by
Context Lab. The cost values are the `total_cost_usd` values reported by Claude
Code. No cache savings are claimed because the provider reported zero
cache-read and cache-creation tokens in both runs.

## Reproduction

Run the same task through two isolated directories, using a working Claude Code
configuration and the explicit policy on the second run:

```bash
python scripts/wrap.py --cassette \
  --session real-baseline \
  --home <baseline-session-dir> \
  -- claude -p "Fix the failing test in test_calc.py by changing only calc.py. Run the test before and after the fix. Do not modify the test file." \
  --model claude-sonnet-4-5 --output-format json

python scripts/wrap.py --cassette --policy cache-stable \
  --session real-cache-stable \
  --home <policy-session-dir> \
  -- claude -p "Fix the failing test in test_calc.py by changing only calc.py. Run the test before and after the fix. Do not modify the test file." \
  --model claude-sonnet-4-5 --output-format json
```

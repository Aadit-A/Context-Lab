# Impact

## What was measured

- **Machine/OS:** `Windows-11-10.0.26200-SP0`, measured with
  `python -c "import datetime,platform; print(platform.platform()); print(platform.python_version()); print(datetime.date.today().isoformat())"`.
- **Python:** 3.14.6, measured by `python --version`.
- **Date:** October 26, 2026.
- **Regression suite:** 83 tests passed with `python -m unittest discover -s tests`.
- **Fixture A:** the bundled `examples/demo_agent.py`, an 8-turn
  `claude-sonnet-4-5`-shaped session against `tests/fake_upstream.py` in
  `realistic` mode on localhost. It deliberately repeats a file result, sends
  a large test result, and changes a volatile prefix value.
- **Interventions:** the same demo task was run five times: baseline,
  `cache-stable`, `schema-gating:Read`, `dedupe-reads`, and `truncate:200`.
  Every run completed the demo process with exit code 0 and printed its normal
  completion line. No task test was executed, so this is not a correctness
  result.
- **Token provenance:** the local scripted provider reported usage, but it
  derives `input_tokens` from serialized request bytes at roughly 4 characters
  per token. Token counts are therefore **measured provider responses from a
  synthetic provider**, not real-provider billing. Dollar figures use the
  dated local price table and are indicative.
- **Live replay against a real provider:** **NOT MEASURED**. No API key was
  used and the audit budget was $0.

## Headline

- **Measured (synthetic provider):** `truncate:200` reduced input from **36,299
  to 6,225 tokens (-82.9%)** and indicative cost from **$0.1090 to $0.0188
  (-82.8%)** on this 8-turn demo capture. The policy is marked trajectory-risky;
  the run did not prove task correctness.
- **Measured (synthetic provider):** `dedupe-reads` reduced input from **36,299
  to 6,599 tokens (-81.8%)** and reduced RDR from **50.0% to 1.3%** on the
  same demo task. It is also trajectory-risky because old tool results are
  replaced with a marker.
- **Measured / projected:** `cache-stable` changed PSI from **0% to 100%** in
  the live capture. The local provider reported no cache reads, so its real
  cache-dollar benefit was **NOT MEASURED**; the committed cassette projects
  **-80.9% cost** when a 90% cache hit is available.

## Method

The following commands produced the numbers above. The fake provider never
contacts the network; the five wrapped runs used only localhost.

```bash
python --version
python -c "import datetime,platform; print(platform.platform()); print(platform.python_version()); print(datetime.date.today().isoformat())"
python -m unittest discover -s tests
python scripts/analyze.py examples/sample-session.jsonl
python scripts/replay.py compare examples/demo.cassette.json --json
```

In a second terminal, start the no-key provider:

```bash
python tests/fake_upstream.py 8899 realistic
```

Then run the same demo once per policy. The session directory is scratch data
and is ignored by git:

```bash
python scripts/wrap.py --cassette --upstream http://127.0.0.1:8899 \
  --session impact-baseline --home .audit/impact-20261026 --no-analyze -- \
  python examples/demo_agent.py
python scripts/wrap.py --cassette --upstream http://127.0.0.1:8899 \
  --session impact-cache --home .audit/impact-20261026 --no-analyze \
  --policy cache-stable -- python examples/demo_agent.py
python scripts/wrap.py --cassette --upstream http://127.0.0.1:8899 \
  --session impact-schema --home .audit/impact-20261026 --no-analyze \
  --policy schema-gating:Read -- python examples/demo_agent.py
python scripts/wrap.py --cassette --upstream http://127.0.0.1:8899 \
  --session impact-dedupe --home .audit/impact-20261026 --no-analyze \
  --policy dedupe-reads -- python examples/demo_agent.py
python scripts/wrap.py --cassette --upstream http://127.0.0.1:8899 \
  --session impact-truncate --home .audit/impact-20261026 --no-analyze \
  --policy truncate:200 -- python examples/demo_agent.py
```

The live table was generated from those JSONL files with:

```bash
python -c "import json,sys; from pathlib import Path; sys.path.insert(0,'scripts'); import analyze; root=Path('.audit/impact-20261026'); out=[]; names=['baseline','cache','schema','dedupe','truncate'];
for name in names:
 p=root/f'impact-{name}.jsonl'; r=analyze.analyse(analyze.load_session(p), analyze.DEFAULT_PRICES); out.append({'session':name,'turns':r['turns'],'input_tokens':r['tokens']['input'],'output_tokens':r['tokens']['output'],'cost_usd':r['cost_usd'],'chr':r['metrics']['chr'],'psi':r['metrics']['psi'],'rdr':r['metrics']['rdr'],'st':r['metrics']['st'],'prefix_breaks':len(r['prefix_breaks'])});
print(json.dumps(out))"
```

## Results

### Live intervention table — measured on the synthetic provider

The token and cost columns are measured from each provider response. The
percentages are relative to the baseline row. `trajectory` is a safety label,
not a success measurement.

| Policy | Turns | Input tokens | Delta | Indicative cost | Delta | PSI | RDR | ST | Trajectory |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| baseline | 8 | 36,299 | — | $0.1090 | — | 0% | 50.0% | 2.5% | not tested |
| cache-stable | 8 | 36,093 | -0.6% | $0.1084 | -0.6% | 100% | 50.0% | 2.5% | prefix-only |
| schema-gating:Read | 8 | 35,370 | -2.6% | $0.1062 | -2.6% | 0% | 50.0% | 0.5% | at risk |
| dedupe-reads | 8 | 6,599 | -81.8% | $0.0199 | -81.7% | 0% | 1.3% | 13.8% | at risk |
| truncate:200 | 8 | 6,225 | -82.9% | $0.0188 | -82.8% | 0% | 50.0% | 14.6% | at risk |

### Offline calibrated projection — projected, not re-sent to a live model

Command: `python scripts/replay.py compare examples/demo.cassette.json --json`.
The baseline reproduced **36,296 recorded input tokens** with **0.39% drift**
and a calibration scale of **1.057**. The other rows inherit that scale.

| Policy | Projected input tokens | Projected cost | Delta cost | Cache | Trajectory claim |
|---|---:|---:|---:|---:|---|
| baseline | 36,296 | $0.1090 | — | 0% | preserved |
| cache-stable | 36,296 | $0.0208 | -80.9% | 90% | preserved |
| dedupe-reads | 6,058 | $0.0183 | -83.2% | 0% | at risk |
| schema-gating | 36,296 | $0.1090 | 0.0% | 0% | preserved |
| truncate-200 | 5,646 | $0.0171 | -84.4% | 0% | at risk |

## Attribution

The largest measured session was the baseline row in Fixture A:

- carried history: **75.8%** of indicative spend;
- command and file results: **21.2%**;
- tool schemas: **2.5%** *(schema tokens are estimated at 4 characters/token)*;
- new prompts: **0.3%**;
- model output: **0.1%**.

Its metrics were CHR **0%**, PSI **0%**, RDR **50.0%**, ST **2.5%**, and CT
**0%**. The biggest single cause was **repeated context: the demo carried a
2,853-token test result and a 998-token file result into later turns**. The
live policies fix two different causes: `truncate:200` cuts the oversized
result, while `dedupe-reads` replaces exact repeats.

## What this does not prove

- **Task success.** The demo process exited successfully, but it is not a real
  coding task and no test suite was scored. `dedupe-reads`, `truncate:200`, and
  an explicit schema allowlist can change what the model sees. No policy is
  proven correct by a token reduction alone.
- **Generality.** These are five runs of one synthetic 8-turn session on one
  machine. They are not a claim about typical Claude Code, Codex, Cline,
  OpenCode, or other harness sessions.
- **Provider billing or cache economics.** The local provider derives usage from
  request bytes and reports zero cache reads. Real-provider billing, cache TTL,
  and cache pricing were **NOT MEASURED**.
- **All policy types live.** `elide` and `compact-at` remain offline projections;
  they need a full conversation policy and can break tool-call structure.
- **No universal automatic fix.** The installed skill can apply one explicit
  request policy and measure it. It cannot safely choose an MCP allowlist,
  compaction threshold, or provider model for an arbitrary harness without
  user context.

## Reproduce

No API key and no third-party packages are required:

```bash
python -m unittest discover -s tests
python scripts/replay.py compare examples/demo.cassette.json

# terminal 1
python tests/fake_upstream.py 8899 realistic

# terminal 2
python scripts/wrap.py --upstream http://127.0.0.1:8899 \
  --policy truncate:200 -- python examples/demo_agent.py
```

The final command should print an 8-turn audit with a substantially smaller
input total than the unmodified `wrap.py` run. Keep the policy only after
re-running the same real task with `bench.py --policy` and confirming that the
task still passes.

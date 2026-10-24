# Impact

## What was measured

- **Machine/OS:** Windows 11 Pro (10.0.26200), Git Bash shell
- **Python:** 3.14.6
- **Date:** 2026-10-24
- **Fixture A — 8 turns, `claude-sonnet-4-5`:** the bundled demo agent
  (`examples/demo_agent.py`, which deliberately contains a volatile system
  prompt, two uncalled tools, one large test output, and one repeated read)
  against `tests/fake_upstream.py` in `realistic` mode on localhost.
- **Fixture B — 2 turns, `claude-sonnet-4-5`:** a real harness, Claude Code
  2.1.301 (`claude -p`), pointed at the same scripted local provider via
  `--settings`, solving "fix the failing test" in a scratch repo.
- **Token-count provenance for both fixtures:** the scripted provider derives
  usage from request bytes (4 chars/token). Token totals are therefore
  **estimated**, not provider-billed. Dollar figures use the dated price table
  in `references/prices.json` and are indicative only.
- Phase 4b (live replay against a real provider) was **not run**: no API key on
  this machine and the audit budget is $0.

## Headline

- Two independent captures of the same 8-turn session recorded **identical
  input token totals (36,299) and identical policy projections** — 0.00%
  difference between runs, calibration drift 0.4% *(estimated tokens,
  projected deltas)*.
- On a real Claude Code 2.1.301 one-question session, **26,855 of 38,031 input
  tokens per turn (71%) were tool-schema tokens**, before the agent did any
  work *(estimated, 4 chars/token)*.
- Offline policy projection on Fixture A: **dedupe-reads −83% input tokens,
  truncate-200 −84%, cache-stable −81% cost** on the same session
  *(projected; the first two are labelled trajectory AT RISK)*.

## Method

```bash
# baseline
python -m unittest discover -s tests
python scripts/analyze.py examples/sample-session.jsonl
python scripts/replay.py compare examples/demo.cassette.json

# fixture A - determinism (fake upstream on 8899, realistic mode)
python tests/fake_upstream.py 8899 realistic &
python scripts/wrap.py --cassette --upstream http://127.0.0.1:8899 \
    --session audit-a  --home ./.audit -- python examples/demo_agent.py
python scripts/wrap.py --cassette --upstream http://127.0.0.1:8899 \
    --session audit-a2 --home ./.audit -- python examples/demo_agent.py
python scripts/replay.py build .audit/audit-a.jsonl
python scripts/replay.py build .audit/audit-a2.jsonl
python scripts/replay.py compare .audit/audit-a.cassette.json  --window 3000 --json
python scripts/replay.py compare .audit/audit-a2.cassette.json --window 3000 --json

# fixture B - real harness at $0 (dummy key; scripted provider behind the proxy)
printf '{"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:18888", \
"ANTHROPIC_API_KEY": "dummy-local", "ANTHROPIC_AUTH_TOKEN": ""}}' > override.json
python scripts/wrap.py --cassette --upstream http://127.0.0.1:8899 \
    --session audit-b --home ./.audit --port 18888 -- \
    claude --settings <abs path>/override.json --model claude-sonnet-4-5 \
    -p "run the test in test_calc.py and fix calc.py so it passes" \
    --dangerously-skip-permissions
python scripts/replay.py build .audit/audit-b.jsonl
python scripts/replay.py compare .audit/audit-b.cassette.json --json
```

## Results

Fixture A — 8 turns, drift 0.4%, scale 1.057, two captures agreeing exactly:

| Policy | Tokens in | Δ vs baseline | Cost | Δ cost | Cache % | Trajectory |
|---|---:|---:|---:|---:|---:|---|
| baseline | 36,299 | — | $0.109 | — | 0% | preserved |
| cache-stable | 36,299 | ±0% | $0.021 | −81% | 90% | preserved |
| dedupe-reads | 6,062 | −83% | $0.018 | −83% | 0% | at risk |
| schema-gating | 36,299 | ±0% | $0.109 | ±0% | 0% | preserved |
| truncate-200 | 5,649 | −84% | $0.017 | −84% | 0% | at risk |

Fixture B — 2 turns, drift 0.0%, scale 1.009. The scripted provider answered
plain text, so Claude Code called no tools: dedupe/truncate/schema-gating had
nothing to act on (+0%), which the tool reports honestly rather than
inventing a saving. cache-stable −81% cost *(all projected)*.

## Attribution

Largest session measured: Fixture B (real Claude Code request shapes).

- tool schemas / MCP: **70.6%** of spend
- carried history: 15.2%
- new prompts: 14.2%
- command + file output: 0.0%
- model output: 0.0%
- CHR 0% (scripted provider reports no cache), PSI 100%, RDR 0%, ST 71%, CT 0%

The biggest single cause, in one sentence: **Claude Code ships 31 tool
definitions (~26,855 estimated tokens) on every request, so a one-question
session pays a 71% schema tax before any work happens.**

For Fixture A (the deliberately wasteful demo agent): carried history 75.8%,
tool results 21.2%, schema 2.5%, with PSI 0% (volatile system prompt) and RDR
50% (repeated read) — each planted problem was detected and named.

## What this does not prove

- **Task success.** No policy was replayed against a live model (Phase 4b
  needs an API key and spends money), so "trajectory: preserved / AT RISK"
  remains each policy's own claim, not an observation. A −83% token saving
  that changes the agent's decisions is a hypothesis, not a win.
- **Generality.** Two fixtures on one machine: a synthetic 8-turn agent and a
  2-turn scripted exchange with a real harness. Nothing here is a claim about
  "typical" sessions, other harnesses, or other models.
- **Provider-billed tokens.** Both fixtures used a scripted local provider
  that derives usage from request bytes; real-provider calibration drift was
  NOT MEASURED. The committed `examples/demo.cassette.json` reproduces on this
  machine to within 3 tokens (36,296 vs 36,299 recorded).
- **Dollar figures.** Prices come from a dated table (`references/prices.json`)
  and flat-rate subscriptions do not bill per token at all.

## Reproduce

Five minutes, no API key, no dependencies:

```bash
cd "C:\PROJECTS\Context Lab"          # any path with a space in it works
python -m unittest discover -s tests  # 74 tests
python tests/fake_upstream.py 8899 realistic &   # or run it in a second shell
python scripts/wrap.py --cassette --upstream http://127.0.0.1:8899 \
    --session audit-a --home ./.audit -- python examples/demo_agent.py
python scripts/replay.py build .audit/audit-a.jsonl
python scripts/replay.py compare .audit/audit-a.cassette.json --window 3000
python scripts/replay.py compare examples/demo.cassette.json
```

Expected: the audit names the four planted problems; the compare table shows
the Fixture A rows above; the two `compare` outputs agree to within a few
tokens. Machine-readable copies: `results/impact.json`.

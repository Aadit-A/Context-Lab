# Context Lab

**Find out where your coding agent actually spends its tokens — and prove whether a fix worked.**

Coding agents burn tokens for structural reasons: the whole context is re-sent on
every turn, tool schemas are charged every turn, and the same file gets read four
times. The fixes are well known. What has been missing is a way to *measure* them
on your own sessions, across any harness.

Context Lab is a local proxy, an attribution engine, and a benchmark loop. It
works with Claude Code, Codex CLI, Cline, Roo, Aider, OpenCode, Cursor, Gemini
CLI, OmniRush, local models — anything that speaks the Anthropic or OpenAI HTTP
API.

- **No dependencies.** Python standard library only.
- **No network egress** except to the provider you already use.
- **No fork, no patch.** A base-URL change is the entire integration.

```
pip install nothing. It's three scripts.
```

---

## Why

| What people believe | What the numbers usually show |
|---|---|
| "My prompt is too long" | The prompt is 0.3% of spend |
| "Output tokens are the problem" | Output is 7% of tokens but 35% of the bill |
| "Stripe-style caching just works" | A date in the system prompt resets it every turn |
| "We should compact sooner" | True, but it is worth 6%, not 40% |
| "MCP servers are cheap" | 9 servers can be 20% of every request |

A typical 45-turn session:

```
CONTEXT AUDIT - 45 turns - $4.87 - claude-sonnet-4-5
--------------------------------------------------------------------------
  carried history          $   4.11   84.4%  ###############...
  tool schemas / MCP       $   0.32    6.7%  #.................
  model output             $   0.32    6.7%  #.................
  command + file output    $   0.09    1.9%  ..................
  new prompts              $   0.02    0.3%  ..................
--------------------------------------------------------------------------
  cache hit rate (CHR)        78%   target >75%    [ok]
  prefix stability (PSI)      93%   target >90%    [ok]
  redundant reads (RDR)       19%   target <10%    [warn]
  schema tax (ST)              7%   target <10%    [ok]
  compaction tax (CT)          1%   target <8%     [ok]
--------------------------------------------------------------------------
TOP FIXES
  1. Shape command output (largest single result: ~2016 tokens)  [-$0.79 / 16%]
  2. 3 repeat(s) of unchanged content (19% of tool-result tokens) [-$0.41 / 8%]
  3. Stop 3 prefix change(s) that reset the prompt cache          [-$0.20 / 4%]
```

Every estimate is derived from *that session's* token counts, and labelled with a
confidence level. Change one thing, re-measure the same task, keep what moves.

---

## Quickstart

```bash
git clone https://github.com/<you>/context-lab
cd context-lab

# 1. run your agent through the recorder
python scripts/wrap.py -- claude

# 2. read the audit
python scripts/analyze.py --latest

# 3. get an HTML report
python scripts/analyze.py --latest --html report.html

# 4. ask what a different context policy would have cost, on the same session
python scripts/wrap.py --cassette -- claude
python scripts/replay.py build ~/.ctxprobe/sessions/<id>.jsonl
python scripts/replay.py compare ~/.ctxprobe/sessions/<id>.cassette.json
```

No harness installed yet? The repo ships a real capture — this runs in one second
with no API key:

```bash
python scripts/replay.py compare examples/demo.cassette.json
```

No harness installed? Use the bundled demo agent:

```bash
python -m tests.fake_upstream 8899 &
python scripts/wrap.py --upstream http://127.0.0.1:8899 -- python examples/demo_agent.py
```

Windows PowerShell:

```powershell
python scripts\wrap.py -- claude
python scripts\analyze.py --latest
```

### Install as an agent skill

```bash
python scripts/install.py --target claude     # ~/.claude/skills/context-lab
python scripts/install.py --target all --dest .   # AGENTS.md, .cursor, .clinerules, GEMINI.md
```

Or copy `SKILL.md` wherever your harness looks for skills. The shims tell an agent
*when* to run the audit and *how* to report it.

---

## Impact (measured on this repo, not marketed)

- Two captures of the same 8-turn session recorded **identical token totals and
  policy projections** (36,299 tokens, 0.00% difference, 0.4% calibration drift).
- A real Claude Code 2.1.301 one-question session paid **26,855 of 38,031 input
  tokens per turn (71%) in tool schemas** before doing any work.
- Offline projection on that session's wasteful twin: **dedupe-reads −83% input
  tokens, truncate-200 −84%, cache-stable −81% cost** — labelled *projected*,
  with the first two flagged as trajectory-changing and never re-tested live
  here (no API key, $0 budget).

Full method, the policy table, and what these numbers do **not** prove:
[`IMPACT.md`](IMPACT.md). Reproduce in five minutes, no key needed:

```bash
python -m unittest discover -s tests
python scripts/replay.py compare examples/demo.cassette.json
```

---

## How it works

```
  harness  ──HTTP──▶  ctxprobe  ──HTTPS──▶  provider
  (any)                │
                       ├─ message diff     what each tool call introduced
                       ├─ content hashes   redundant reads
                       ├─ prefix hashes    cache breaks, and their cause
                       └─ usage parse      input / output / cache read+write
                              │
                              ▼
                        analyze.py ──▶ terminal · JSON · HTML
```

The proxy forwards every request untouched and streams responses back in
real time. It records **message hashes and 100-character previews**, not full
bodies, unless you pass `--record-raw`.

---

## The five metrics

| Metric | Question it answers | Healthy |
|---|---|---|
| **CHR** cache hit ratio | How much of my input is being cached? | > 75% |
| **PSI** prefix stability | How often do I reset that cache? | > 90% |
| **RDR** redundant data | How much do I re-read unchanged bytes? | < 10% |
| **ST** schema tax | How much am I paying for tools I don't call? | < 10% |
| **CT** compaction tax | How much goes to summarising? | < 8% |

Plus **TTG — tokens to green**, recorded per task by `bench.py`. Cost per *solve*,
never cost per *run*.

Definitions and the savings arithmetic: [`references/metrics.md`](references/metrics.md).

---

## Harness support

| Harness | Status | Mechanism |
|---|---|---|
| Claude Code | verified | `ANTHROPIC_BASE_URL` |
| Codex CLI | verified | `OPENAI_BASE_URL` / `config.toml` |
| Aider, OpenCode, Continue, Cline, Roo | supported | base-URL setting |
| DeepSeek, Kimi, Qwen, OpenRouter, vLLM, Ollama | supported | `--upstream` |
| OmniRush | **untested by us** | generic OpenAI/Anthropic path — [compatibility test](references/harnesses.md#omnirush) |
| Gemini CLI | partial | only if the CLI exposes a base-URL flag |

If your harness works, add a row. That is the most useful PR you can send.

---

## Compare policies on one real session

Record with `--cassette`, then ask what six different context policies would have cost
on *that exact session*:

```bash
python scripts/wrap.py --cassette -- claude
python scripts/replay.py build ~/.ctxprobe/sessions/<id>.jsonl
python scripts/replay.py compare ~/.ctxprobe/sessions/<id>.cassette.json
```

```
policy comparison  -  8 turns  -  claude-sonnet-4-5
calibrated against 36,296 recorded input tokens (scale 1.057, drift 0.39%)
--------------------------------------------------------------------------
policy                tokens in     cost   delta  cache  trajectory
--------------------------------------------------------------------------
baseline                 36,296     0.11       -     0%  preserved
cache-stable             36,296     0.02    -81%    90%  preserved
dedupe-reads              6,058     0.02    -83%     0%  AT RISK
schema-gating            36,296     0.11     +0%     0%  preserved
truncate-200              5,646     0.02    -84%     0%  AT RISK
--------------------------------------------------------------------------
```

The projection is **calibrated**: the untouched session reproduces the tokens the
provider actually charged (drift 0.4% above), so every other row inherits that scale.

`trajectory` is the policy's own claim. To test it, replay against a live model and see
whether the agent still makes the same tool calls:

```bash
python scripts/replay.py run ~/.ctxprobe/sessions/<id>.raw \
    --policy dedupe-reads --max-turns 10
```

```
  decisions changed        2/9
  divergence rate            22%
    turn   4: ['Read'] -> ['Grep']
```

A policy that changes decisions is not disqualified — but it is now a hypothesis about
quality, not a proven saving. That distinction is the whole point of the project.

---

## Prove a change

```bash
python scripts/bench.py --name fix-billing --test "pytest -q" -- claude "fix the failing test"
```

```
task      fix-billing
solved    yes
turns     38
tokens    612,449 in / 21,004 out
cost      $3.12
vs. last  -$0.94 (-23%), -7 turns
```

A cheaper run that fails the task is not a win, and the tool says so.

---

## Privacy

- Session files live in `~/.ctxprobe/sessions/` and never leave the machine.
- Previews are truncated to 100 characters and API keys are redacted.
- `--record-raw` (full request bodies) is opt-in and off by default.
- No telemetry, no accounts, no hosted service. There is nothing to sign up for.

---

## Roadmap

- [x] **Replay**: re-run a recorded session under a different context policy
      against the same tool outputs, and compare tokens-to-green.
- [ ] Policy packs per harness (compaction thresholds, tool gating).
- [ ] A public leaderboard: same model, same tasks, different context strategies.
- [ ] GitHub Action that fails a PR when TTG regresses.

---

## Contributing

Verification reports are worth more than features right now. See
[CONTRIBUTING.md](CONTRIBUTING.md).

To reproduce every number on this page on your own machine — and to audit the
tool itself — run [SELF-AUDIT.md](SELF-AUDIT.md). It is written to be pasted into
any agent.

```bash
python -m unittest discover -s tests
```

## License

MIT.

---
name: context-lab
description: Measure and reduce token spend for any AI coding agent session. Use when the user asks why an agent is expensive, why they hit context or token limits, how to make runs cheaper, how many tokens a task takes, or whether compaction/MCP/repo-map settings actually help. Works with Claude Code, Codex CLI, Cline, Roo, Aider, OpenCode, Cursor, Gemini CLI, OmniRush, or any harness that speaks the Anthropic or OpenAI HTTP API.
license: MIT
---

# Context Lab

A coding agent's cost is set by **what it re-sends on every turn**, not by how
long the user's prompt is. This skill measures that with a local proxy, applies
an explicit request policy when the user asks for a reduction, and proves
whether the fix worked.

A small standard-library toolkit, no dependencies, and no network egress except
to the provider:

| Script | Purpose |
|---|---|
| `scripts/ctxprobe.py` | Records each request/response pair to a local session file |
| `scripts/analyze.py` | Attributes spend to a cause, emits a ranked fix list |
| `scripts/wrap.py` | Runs any agent command under the proxy, then audits it |
| `scripts/replay.py` | Re-measures a recorded session under a different context policy |
| `scripts/bench.py` | Records tokens-to-green for one task, so changes are comparable |
| `scripts/live_policy.py` | Request-shaping policies used by the opt-in live gateway |

---

## When to use this

- "Why is Claude Code / Codex costing me so much?"
- "I keep hitting the context limit mid-task."
- "Should I compact at 50% or 90%? Does truncating tool output break anything?"
- "How many tokens does this task actually take?"
- "Is my MCP setup eating my context?"
- Any request to compare two harness configurations on cost.

**Do not use** for: reducing a single prompt, model pricing questions, or
anything where the user only wants a general tip list. In that case answer
directly from `references/playbook.md` — do not run the proxy.

---

## Before you start

1. Confirm Python 3.9+ (`python --version`). The scripts use the standard library only.
2. Confirm the user is willing to have a session recorded. Say plainly:
   *"This records message hashes and 100-character previews to `~/.ctxprobe/`,
   locally. Nothing is uploaded. Use `--record-raw` only if you also want full
   bodies for replay."*
3. Never point the proxy at a provider the user has not configured. It forwards
   their own API key, unchanged, to their own upstream.

The commands below assume the current working directory contains the Context Lab
`scripts/` folder. If this skill was installed to a user skill directory, prefix
the paths with that installed folder instead (for example
`python ~/.claude/skills/context-lab/scripts/wrap.py`); do not assume the user's
project also contains the skill source.

---

## Procedure

### Step 1 — get one real measurement

**Mode A: measure a command** (works for any harness, including ones that take a
`--base-url` flag rather than an env var):

```bash
python scripts/wrap.py -- claude
python scripts/wrap.py --provider openai -- codex
python scripts/wrap.py --upstream https://api.deepseek.com -- my-harness
```

PowerShell: identical, but quote paths containing spaces.

**Mode B: measure an existing setup without wrapping it.** Start the proxy in one
terminal, then relaunch the harness in another with the printed environment
variables:

```bash
python scripts/ctxprobe.py serve --port 8787
# in a second shell:
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
export OPENAI_BASE_URL=http://127.0.0.1:8787
claude
```

**Mode C: the harness ignores base-URL env vars.** Pass the base URL explicitly
if it supports one (`--base-url`, `baseURL`, `api_base` in config). If it does
not, say so and stop: there is no reliable way to measure an opaque binary
without intercepting its traffic, and you should not attempt a system-level TLS
interception.

If `wrap.py` finishes and no session file was written, the harness ignored the
env vars. Report that as the finding; do not retry more than once.

### Step 2 — read the audit

```bash
python scripts/analyze.py --latest
python scripts/analyze.py --latest --html report.html
python scripts/analyze.py --latest --json
```

Report to the user, in this order:

1. **Total spend and the top bucket.** One sentence.
2. **The three metrics that are off target**, with the numbers. Do not list
   metrics that are healthy unless asked.
3. **The single highest-value fix**, with its estimated saving.
4. **What you have not verified** — that the estimate is a hypothesis until
   re-measured. Say this explicitly.

Never present an estimate as a result. The only result is a measured delta.

### Step 3 — get the counterfactual before changing anything

If the session was captured with `--cassette`, ask what each policy *would* have
cost on that exact session. This is free and instant:

```bash
python scripts/replay.py build <session>.jsonl
python scripts/replay.py compare <session>.cassette.json
python scripts/replay.py compare <session>.cassette.json --window 200000
```

The baseline row must reproduce the recorded token count. `analyze` prints a
`drift` figure; if it is above roughly 15%, the projection is not trustworthy on
this session and you should say so rather than quote the other rows.

`trajectory: AT RISK` means the policy changes what the model can see. To test
that claim against a live model (costs money, needs a key):

```bash
python scripts/replay.py run <session>.raw --policy dedupe-reads --max-turns 10
```

Report the divergence rate, not just the saving. A policy that diverges is a
hypothesis about quality, not a proven win.

### Step 4 — apply exactly one fix

Take the top recommendation and apply it. Prefer changes that are reversible and
outside the user's source code:

- Remove or gate an MCP server (config change).
- Add output shaping to a command or an alias (shell config).
- Move volatile text out of the system prompt / `CLAUDE.md` / `AGENTS.md`.
- Change the compaction threshold (harness config).
- Switch exploration to a cheaper model (harness config).

Do **not** edit the user's application code to satisfy the audit.

When the waste is in request context itself, the skill can apply one policy during
the next run instead of only describing it. This is explicit opt-in: the default
recorder remains a passthrough, while the selected policy changes request context
before it reaches the provider and leaves provider responses untouched.

```bash
# Keep volatile dates/times out of the cacheable prefix.
python scripts/wrap.py --policy cache-stable -- <agent command>

# Replace exact repeated tool results with a short marker.
python scripts/wrap.py --policy dedupe-reads -- <agent command>

# Keep the head and tail of oversized tool results (trajectory is at risk).
python scripts/wrap.py --policy truncate:200 -- <agent command>

# Explicitly keep only named tools (trajectory is at risk if a needed tool is absent).
python scripts/wrap.py --policy schema-gating:Read,Edit,Bash -- <agent command>
```

Use the policy that matches the audit finding, not all policies at once. The
offline `replay.py compare` row is a projection; the live `wrap.py --policy`
run is the actual intervention to measure with the provider. `elide` and
`compact-at` remain compare-only because they need a full conversation policy
and can break tool-call structure.

### Step 5 — prove it

Re-run the *same* task, not a similar one:

```bash
python scripts/bench.py --name "fix-billing-test" \
    --test "python -m unittest discover -s tests" \
    --policy truncate:200 -- claude "fix the failing test"
```

`bench.py` appends to `bench.csv` and prints the delta against the previous run
with the same `--name`. A change only counts as a win if `solved` stays the same
and tokens or cost fall. **A cheaper run that fails the task is a regression** —
say so plainly.

### Step 6 — record the decision

Append one line to `DECISIONS.md` in the repo (create it if absent):

```
2026-10-24  cache breaks: removed date from AGENTS.md   -18% cost, solved 3/3 (was 3/3)
```

---

## Rules

- **Measure, then claim.** No percentages without a before/after from the same task.
- **One variable at a time.** Two simultaneous changes make the delta meaningless.
- **Local only.** Never upload session files, never enable `--record-raw` without
  explicit permission, and never echo an API key into a report.
- **Do not break the user's session.** The proxy passes every request through
  untouched by default. A `--policy` run is the only exception and is an
  explicit request-context experiment; provider responses are still passed
  through untouched. If a harness fails while wrapped, stop and report; do not
  "fix" it by disabling TLS verification or editing provider SDKs.
- **Price table is a snapshot.** Costs are indicative. Say which price file was
  used and when it was updated.
- **Estimates are labelled.** Every `est_saving_usd` is derived from the session's
  own token counts and a multiplier documented in `references/metrics.md`.

---

## Interpreting the five metrics

| Metric | Meaning | Healthy | If bad, do this |
|---|---|---|---|
| **CHR** cache hit ratio | share of input read from cache | > 75% | Stabilise the prefix: no dates, no reordering, no model switch |
| **PSI** prefix stability | share of turns with unchanged prefix | > 90% | Same as above; find the volatile field |
| **RDR** redundant data | share of tool-result tokens that repeat earlier bytes | < 10% | Content-addressed read cache; re-run tests instead of re-reading |
| **ST** schema tax | tool/MCP definitions as share of input | < 10% | Drop unused MCP servers; lazy tool loading |
| **CT** compaction tax | summary tokens as share of output | < 8% | Compact earlier (40–60%), not at the limit |

Full definitions and the arithmetic behind the savings estimates:
`references/metrics.md`.

---

## Harness wiring

Per-harness env vars, config keys, and a compatibility test that takes two
minutes: `references/harnesses.md`. It covers Claude Code, Codex CLI, Cline,
Roo Code, Aider, OpenCode, Cursor, Gemini CLI, Continue, and OmniRush (generic
OpenAI-compatible path, unverified against upstream docs).

## Fix catalogue

Twelve fixes ranked by measured impact, each with a verification step:
`references/playbook.md`.

## If the proxy cannot be used

Fall back to the manual checklist in `references/playbook.md` and be explicit
that any number you give is a rule of thumb, not a measurement of their session.

Known limitations, which you should state if they matter to the user:

- Replay works from a recording. It cannot invent a session you did not capture.
- `replay.py run` bills the user's API key; confirm before running more than a
  few turns, and never run it without `--max-turns`.
- Schema tokens are estimated at four characters per token; providers do not
  break usage down by field.
- Harness-native logs are not parsed, because they omit request bodies and cache
  accounting. That is why the proxy exists.

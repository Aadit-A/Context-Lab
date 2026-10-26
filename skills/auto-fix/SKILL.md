---
name: context-lab-auto-fix
description: Router sub-skill that decides which Context Lab fix to apply. Use when the user wants token usage reduced but names neither a specific fix nor a budget - "reduce my token usage", "fix my context", "make my agent cheaper", "this session is expensive, fix it". Measures once, maps the top audit finding to exactly one live policy or config change, then applies and verifies it. Hands off to context-lab-budget-guard when the user states a cap.
license: MIT
---

# Auto-Fix (router)

One measurement, one decision, one fix, one verification. This sub-skill is
the dispatcher for the Context Lab family: it reads the audit and decides
which fix (or which sibling skill) applies, so the user does not have to name
it themselves.

The commands assume the current working directory contains the Context Lab
`scripts/` folder; otherwise prefix them with the installed skill folder.

## Routing rules, in order

1. The user states a cap or asks for set-and-forget ("under 200k tokens",
   "halve my spend", "stop asking me") -> use **context-lab-budget-guard**;
   do not pick a policy by hand.
2. The user asks a question only ("why is this expensive?") -> main
   context-lab skill, steps 1-2 (measure and report), then stop.
3. Otherwise -> the procedure below.

## Procedure

### Step 1 - measure once

```bash
python scripts/wrap.py --cassette -- <agent command>
```

Use `--cassette` when practical: it unlocks calibrated projections and the
used-tool list for schema decisions.

### Step 2 - read the audit as data

```bash
python scripts/analyze.py --latest --json
```

Take `recommendations[0]` (the top-ranked fix) and route it through the
decision table below. Do not skip ahead: applying fix #3 while #1 is unaddressed
makes the delta meaningless.

### Step 3 - the decision table

| Audit finding (`id`) | Decision | Apply |
|---|---|---|
| `cache-breaks` | live policy, tier 1, content-preserving | `wrap.py --policy cache-stable` |
| `output-shaping` | prefer a shell-side fix (alias piping through `head`/`tail`, quieter test flags); if the user cannot change the command, live policy tier 2 | `wrap.py --policy truncate:N` with N about half the largest offender's tokens (clamp 100-800) |
| `redundant-reads` | live policy, tier 3, at risk | `wrap.py --policy dedupe-reads` |
| `schema-tax` | prefer the config fix: drop unused MCP servers / lazy tool loading. Only if the user declines config changes: tier 4 with an explicit allowlist | `wrap.py --policy schema-gating:Tool1,Tool2` (names from `replay.py list <cassette>`) |
| `compaction` | NOT a live policy; change the compaction threshold in harness config | `references/playbook.md` |
| `verbose-output` | NOT a live policy; add a terse-response instruction to `CLAUDE.md`/`AGENTS.md` or the harness setting | `references/playbook.md` |
| (no recommendations) | session is healthy | say so and stop |

### Step 4 - apply exactly one fix

- Config fixes (playbook rows, schema-tax first choice): make that single
  change and nothing else.
- Live policies: run the wrapped command with the chosen policy. If the user
  also wants it to persist across runs, enable it once and switch to auto:

```bash
python scripts/autotune.py enable <policy>
python scripts/wrap.py --policy auto -- <agent command>
```

For tier 2+ policies, say what the policy removes before enabling.

### Step 5 - prove it on the same task

```bash
python scripts/bench.py --name <task> --test "<test cmd>" --policy auto -- <agent command>
python scripts/autotune.py guard --name <task>
```

A cheaper run that fails the task is a regression: say so, drop the policy
(`autotune.py disable` if it was persisted), and re-plan.

### Step 6 - record and, only if asked, iterate

Append one line to `DECISIONS.md`. If the user wants more reduction, loop back
to step 1 with the next-ranked finding - one change per measured iteration,
never two at once.

## Rules

- One variable at a time; the router picks exactly one fix per iteration.
- Estimates (`est_saving_usd`) are hypotheses; report measured deltas as the
  only results.
- Never edit the user's application code to satisfy an audit.
- If a needed tool was gated away or needed output was truncated, that is a
  failure of the fix, not of the task; revert and pick the safer tier.

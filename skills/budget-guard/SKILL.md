---
name: context-lab-budget-guard
description: Keep an agent's token usage under a stated maximum by selecting, persisting, and auto-applying exactly one Context Lab policy, with a regression guard that steps down if the task stops passing. Use when the user gives a token or cost cap ("keep sessions under 200k tokens", "cut input by half", "make it a set-and-forget cost reducer") or asks for automatic policy selection by risk level (safe / balanced / aggressive). Requires the main context-lab skill's scripts.
license: MIT
---

# Budget Guard

The main context-lab skill measures and recommends. This sub-skill closes the
loop: given the user's maximum token usage, it picks the least invasive live
policy that fits, persists the choice, and auto-applies it on every later
wrapped run until a bench regression says to stop.

Set-and-forget has one gate and one guard:

- **Gate:** tiers 2+ change what the model reads. Confirm the tier with the
  user once, at enable time. That confirmation is what makes everything after
  it automatic.
- **Guard:** `autotune.py guard` watches bench runs and steps back down the
  ladder the moment a cheaper run stops solving the task. A cheaper run that
  fails the task is a regression, never a win.

The commands assume the current working directory contains the Context Lab
`scripts/` folder; otherwise prefix them with the installed skill folder.

## When to use

- "Keep my sessions under N tokens."
- "Halve my token spend." (a percentage budget: `--budget 50%`)
- "Just make it cheaper and stop asking me." (pick a risk ceiling:
  `--max-tier safe|balanced|aggressive`)

Do not use when the user names a specific fix (apply it directly per the main
skill), or when no session has been recorded yet: autotune plans against a
capture, it cannot invent one.

## Procedure

### Step 1 - capture one representative session

```bash
python scripts/wrap.py --cassette -- <agent command>
python scripts/replay.py build ~/.ctxprobe/sessions/<session>.jsonl
```

The cassette makes the plan *calibrated* (projected from the user's own
recorded turns, with a drift check). Without one, autotune falls back to a
labelled *estimate* from the session's metrics. Prefer the cassette.

### Step 2 - plan the ladder against the budget

```bash
python scripts/autotune.py plan --latest --budget 100000
python scripts/autotune.py plan --latest --budget 50%            # relative
python scripts/autotune.py plan --latest --budget 8000 --max-tier balanced
```

The ladder is safety-ordered; selection takes the **lowest tier that fits**:

| Tier | Policy | What changes | Trajectory |
|---|---|---|---|
| 0 | baseline | nothing (measurement only) | preserved |
| 1 | cache-stable | volatile dates/ids masked in the prefix | preserved |
| 2 | truncate:N | oversized tool results capped at head+tail | at risk |
| 3 | dedupe-reads | exact repeated tool results replaced by a marker | at risk |
| 4 | schema-gating | schemas for tools the session never called are dropped | at risk |

`--max-tier` caps the risk: safe (<= 1), balanced (<= 3), aggressive (<= 4).

Read the plan to the user: the projected tokens per tier, the selection, and
whether the mode is calibrated or estimate. If the plan reports the budget
**unreachable**, say so plainly and route to `references/playbook.md`
(compact earlier, drop unused MCP servers, cheaper exploration model);
request shaping alone does not hit every budget.

### Step 3 - enable (the consent gate)

If the selection is tier 2 or above, state what the policy removes and get a
yes before enabling. Tier 1 (cache-stable) and tier 0 need no extra consent.

```bash
python scripts/autotune.py enable <selected policy> --budget <budget>
```

This writes `~/.ctxprobe/autotune.json` (local file, nothing uploaded).

### Step 4 - run set-and-forget

```bash
python scripts/wrap.py --policy auto -- <agent command>
```

`auto` resolves to the persisted policy at proxy start and prints it. It stays
in force for every later wrapped run until disabled or stepped down. The
recorder without `--policy` remains pure passthrough; responses are never
rewritten either way.

### Step 5 - prove once, then let the guard watch

```bash
python scripts/bench.py --name <task> --test "<test cmd>" --policy auto -- <agent command>
python scripts/autotune.py guard --name <task>
```

`guard` compares the last two bench runs of the task: if the task regressed
under the policy, autotune steps down one tier, persists the safer policy, and
says so. Run bench (any cadence the user wants) after that to keep the guard
fed; the guard only reacts to bench evidence.

### Step 6 - record the decision

Append one line to `DECISIONS.md`:

```
2026-10-26  budget 100k: enabled truncate:400 via autotune   -74% projected, bench pending
```

Update the line when the bench delta lands.

## Rules

- The budget is per recorded session shape. If the user's task mix changes
  (bigger repo, new MCP servers), re-run plan on a fresh capture.
- Projections are labelled projections. The only result is the measured
  before/after from bench on the same task.
- `cache-stable` lowers cost, not token counts; under a token budget it will
  usually be skipped by selection. Say this if the user expected it.
- One policy at a time. Never stack tiers manually; the ladder exists so
  automation does not have to.
- To stop: `python scripts/autotune.py disable`. To inspect:
  `python scripts/autotune.py status`.

## Limitations to state when they matter

- The persisted policy applies to wrapped runs (`wrap.py`/`bench.py`/`ctxprobe
  serve --policy auto`). Harnesses cannot be forced into the proxy from here.
- The guard reacts only to bench runs; it does not watch sessions silently.
- Estimate mode without a cassette is a rule of thumb from the session's own
  metrics, clearly labelled; build the cassette before trusting the ladder.

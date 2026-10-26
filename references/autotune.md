# Autotune: budget-driven policy selection

`scripts/autotune.py` turns "apply one matching policy" into "keep my usage
under N automatically". It never changes traffic by itself: it *selects* a
live policy, *persists* the choice to a local config, and only `--policy auto`
reads it back. The default recorder stays byte-for-byte passthrough
(non-negotiable 5 in `AGENTS.md`), and provider responses are never rewritten.

## The ladder

Policies are risk-ordered. Selection picks the **lowest tier that fits the
budget**, never the biggest saving.

| Tier | Policy | Changes what the model reads | Trajectory claim |
|---|---|---|---|
| 0 | `baseline` | nothing | preserved |
| 1 | `cache-stable` | nothing (volatile dates/ids masked) | preserved |
| 2 | `truncate:N` | oversized tool results capped head+tail | at risk |
| 3 | `dedupe-reads` | exact repeated tool results replaced by a marker | at risk |
| 4 | `schema-gating` | schemas of never-called tools dropped | at risk |

Ordering rationale: tier 1 is the only policy that provably preserves content.
`truncate` sits below `dedupe-reads` because its effect is bounded and
predictable (every result keeps a head and a tail of fixed size), while
`dedupe-reads` removes a payload the model may want to re-check. Schema
gating is last because a wrongly-absent tool can block a task outright.

`--max-tier` caps the risk the automation may take:

| Cap | Highest tier reachable | Meaning |
|---|---|---|
| `safe` | 1 | content the model reads never changes |
| `balanced` | 3 | tool-result content may change |
| `aggressive` | 4 | tool availability may change |

If the budget cannot be met within the cap, autotune reports `unreachable`
with the best reachable projection and points at `references/playbook.md`
(compact earlier, drop unused MCP servers, cheaper exploration model) instead
of exceeding the cap. Request shaping alone does not hit every budget.

## Budgets

`--budget` accepts an absolute token count (`100000`) or a percentage of the
baseline (`50%`). The budget constrains **projected input tokens for the
recorded session**; `--budget-usd` optionally adds a cost cap. Sessions differ,
so the budget is a target against the captured session's shape, not a
hard limit enforced on future runs - only measurement (bench) can prove the
real number.

## Projection modes

- **calibrated** (default when `<session>.cassette.json` exists or
  `--cassette` is given): `replay.py compare` projects each tier from the
  user's own recorded turns, calibrated so the baseline reproduces recorded
  usage. If baseline drift exceeds 15%, selection is withheld (the projection
  is not trustworthy), matching the main skill's rule.
- **estimate** (no cassette): per-tier reductions are derived from the
  session's own `analyze.py` numbers using fixed multipliers. These are
  heuristics, labelled as such in every output:
  - `truncate:N` - each tool-result message's tokens become `min(t, N + 10)`
    (10 tokens approximates the truncation marker).
  - `dedupe-reads` - redundant tokens (RDR numerator) are reduced by 90%,
    mirroring analyze's repeat-saving multiplier.
  - `schema-gating` - schema tokens are halved, mirroring analyze's
    schema-tax multiplier.
  - `cache-stable` - tokens unchanged; cost recomputed at an assumed 90%
    cache hit, the same share `replay.py compare` models for stable prefixes.

Estimates are never results. They rank the ladder; only a bench run measures.

## Config and `--policy auto`

`autotune.py enable <spec> --budget N` writes `~/.ctxprobe/autotune.json`
(override path with `CTXPROBE_AUTOTUNE_CONFIG`):

```json
{
  "enabled": true,
  "policy": "truncate:200",
  "budget_tokens": 100000,
  "created": "...",
  "updated": "...",
  "guard": { "event": "regression", "from": "dedupe-reads", "to": "truncate:400" }
}
```

`wrap.py --policy auto`, `bench.py --policy auto`, and
`ctxprobe serve --policy auto` expand `auto` to the persisted policy at proxy
startup (the resolved spec is printed). Missing or disabled config is an
error with the fix in the message - `auto` never silently degrades to a
different policy. `autotune.py disable` stops auto-application; the last
policy is kept in the file for the audit trail.

## The guard

`autotune.py guard --name <task> --csv bench.csv` compares the last two bench
runs of a task:

- previous run solved, latest does not: **regression**. If a policy is
  enabled, autotune steps one tier down, persists the safer policy, records
  the event in the config, and exits 1. This is the safety catch that makes
  set-and-forget acceptable: a cheaper run that fails the task automatically
  loses the policy that caused it.
- input tokens rose more than 5% with the task still passing: warning to
  re-plan (exit 0).
- otherwise: ok.

The guard reacts only to bench evidence. It does not watch sessions silently;
"set and forget" still means one verification run per task.

## Sub-skills

- `skills/budget-guard/SKILL.md` (`context-lab-budget-guard`) - the
  procedure for users who state a maximum.
- `skills/auto-fix/SKILL.md` (`context-lab-auto-fix`) - the router for users
  who state no fix and no budget; maps audit findings to exactly one action.

`install.py` installs them beside the main skill for the `claude` and
`claude-project` targets. See `skills/README.md`.

## Non-claims

- A projection, calibrated or estimated, is a hypothesis until the same task
  is re-run under the policy.
- The guard steps down on *evidence of regression*; absence of a bench run is
  absence of evidence, not evidence of safety.
- Autotune chooses among the four live policies only. It does not tune MCP
  allowlists, compaction thresholds, or models - those need user context and
  live in `references/playbook.md`.

# Context Lab skill family

The root `SKILL.md` is the main skill: measure, attribute, compare, apply one
fix, prove it. The sub-skills in this folder are narrower entry points for
users who want the loop shortened; each defers to the main skill's rules.

| Skill | Use when | What it automates |
|---|---|---|
| `context-lab` (root) | the user asks why a session is expensive, or wants a fix chosen by hand | nothing; it is the procedure and the source of truth |
| `context-lab-budget-guard` | the user states a maximum (tokens, cost, or a percentage) or asks for set-and-forget reduction | policy selection against the budget, persistence (`autotune.py enable`), auto-application (`--policy auto`), regression step-down (`autotune.py guard`) |
| `context-lab-auto-fix` | the user wants usage reduced but names no fix and no budget | routing: audit finding -> exactly one live policy or config change, then verification |

`install.py` installs the sub-skills beside the main skill (as
`context-lab-budget-guard` and `context-lab-auto-fix`) for the
`claude` and `claude-project` targets. The sub-skills have no scripts of their
own; they drive `scripts/autotune.py`, `scripts/wrap.py`, `scripts/analyze.py`,
and `scripts/bench.py` from the main skill.

Design notes for the ladder, tier caps, budget semantics, and the guard are in
`references/autotune.md`.

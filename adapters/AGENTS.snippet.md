<!-- context-lab:start -->
## Context Lab - token efficiency

When the user asks why an agent session is expensive, why they hit token limits,
or how to spend less per task, use the Context Lab skill.

Quick procedure:
1. `python scripts/wrap.py -- <agent command>` to record a real session.
2. `python scripts/analyze.py --latest` for the cost breakdown and ranked fixes.
3. Apply exactly one matching live policy, if request context is the cause:
   `python scripts/wrap.py --policy <policy> -- <agent command>`.
4. Re-run the *same* task and compare tokens-to-green with
   `python scripts/bench.py --name <task> --test "<test cmd>" --policy <policy> -- <agent command>`.

Rules: never send session data off the machine; never change the user's code
without asking; live policies are opt-in and responses are not rewritten; report
measured deltas, never estimated ones, as results.
Full details: SKILL.md in this skill folder.
<!-- context-lab:end -->

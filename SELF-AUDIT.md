# Context Lab — self-audit prompt

**How to use:** open the repository at `C:\PROJECTS\Context Lab` in your agent
(Claude Code, Codex CLI, Cline, OmniRush, or anything with shell access) and paste
everything below the line. The agent will test the tool against itself, produce
measured numbers, hunt for user-facing bugs, and leave the repo ready to publish.

**Time:** 60–120 minutes. **Cost:** $0 unless it reaches Phase 4b (live replay),
which is explicitly optional.

---

## Your mission

You are auditing **Context Lab**, a tool that measures and reduces token spend for
AI coding agents. Your job is to produce four things:

1. **Evidence** — numbers, measured on this machine, with the commands that
   produced them.
2. **An impact section** for the GitHub page that a sceptical engineer would
   believe, including what the tool does *not* prove.
3. **Real bug fixes** — failures an ordinary user would actually hit, each with a
   regression test.
4. **A short report** of what you found, what you fixed, and what you could not
   measure.

You are not marketing this. A modest, reproducible number is worth more than an
impressive one you cannot defend.

---

# 1. Context

## 1.1 What the project is

Coding agents (Claude Code, Codex CLI, Cline, Roo, Aider, OpenCode, OmniRush, and
anything OpenAI/Anthropic-compatible) spend most of their tokens re-sending the
same context on every turn. The fixes are known — filter command output, cut MCP
schemas, stop re-reading files, stabilise the prompt prefix so caching works — but
nobody can **measure** them on their own sessions, because harnesses either expose
only a total cost or nothing at all.

Context Lab is a local proxy plus an attribution engine plus a replay bench. It:

- records what a harness actually sends and receives, per turn;
- attributes spend to a cause (carried history, schemas, tool results, output,
  new prompts);
- computes five metrics and a ranked fix list;
- replays a recorded session under different context policies and reports both
  the projected saving **and** whether the model's decisions changed.

## 1.2 Why we built it

Three blind spots motivated it, and they are still the argument:

1. **Harness logs lie by omission.** They contain token totals but not request
   bodies, tool schemas, or cache accounting, so a log-based tool cannot see a
   cache break or a true prefix. And half the ecosystem has no logs at all.
2. **Nobody attributes cost to a cause.** Every tool says "this output was large".
   None says "these re-reads cost $0.94 and produced nothing new".
3. **No counterfactual.** The field argues about compaction thresholds and
   truncation with anecdotes. You could not take *your* session and ask "what if
   I had compacted at 50%?"

## 1.3 How it works

```
harness ──HTTP──▶ ctxprobe ──HTTPS──▶ provider
                    │
                    ├─ per-message diff   what each tool call introduced
                    ├─ content hashes     redundant reads
                    ├─ prefix hashes      cache breaks, with the cause
                    └─ usage parse        input / output / cache read+write
                          │
                          ▼
              analyze ──▶ terminal · JSON · HTML
                          │
                          ▼
              cassette ──▶ replay ──▶ projected cost + decision divergence
```

**Capture.** The harness is pointed at `http://127.0.0.1:8787` via
`ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL`. The proxy forwards every request
untouched, streams the response back in real time, and records message hashes,
sizes, prefixes, and usage. With `--cassette`, content is written to a
content-addressed object store so a session can be re-measured offline.

**Attribution.** Turn N's request is diffed against turn N−1 as a multiset, so a
genuinely new message is separated from a repeated one. Costs are split into five
buckets and priced with a dated table.

**Replay.** A policy transforms blocks, tools, or messages. The projection is
**calibrated**: the untouched session must reproduce the tokens the provider
actually charged, and that scale is applied to every other policy. `replay run`
goes further and re-sends transformed requests, comparing the tool calls the model
returns against the ones recorded in the next turn — the only way to know whether
a saving changed behaviour.

## 1.4 Repository map

| Path | Responsibility |
|---|---|
| `scripts/ctxprobe.py` | proxy, recorder, SSE/JSON usage and response parsing |
| `scripts/cassette.py` | object store, tool-call pairing, request reconstruction |
| `scripts/policies.py` | `Policy` protocol, seven policies, trajectory claims |
| `scripts/replay.py` | calibration, projection, live divergence, CLI |
| `scripts/analyze.py` | attribution, five metrics, recommendations |
| `scripts/report.py` | self-contained HTML |
| `scripts/wrap.py` | run any command under the proxy, then audit |
| `scripts/bench.py` | tokens-to-green per named task |
| `scripts/install.py` | writes skill + shims for each harness |
| `SKILL.md` | agent-facing procedure (the "skill" itself) |
| `references/` | metrics, harness wiring, fix catalogue, replay design, prices |
| `tests/` | 60 tests, stdlib `unittest`, no network |
| `examples/` | demo agent, sample session, sample report |

## 1.5 CLI surface

```bash
python scripts/ctxprobe.py serve --port 8787 [--cassette] [--record-raw] [--upstream URL]
python scripts/wrap.py [--cassette] [--html out.html] -- <agent command>
python scripts/analyze.py --latest [--json] [--html out.html]
python scripts/replay.py build <session>.jsonl
python scripts/replay.py compare <session>.cassette.json [--window N] [--json]
python scripts/replay.py run <raw_dir> --policy P --max-turns N [--yes]
python scripts/bench.py --name T --test "pytest -q" -- <agent command>
python scripts/install.py --target all --dest .
```

## 1.6 Current state (v0.2.0)

Shipped and tested: capture, streaming passthrough, attribution, five metrics,
HTML report, cassette + calibrated compare, live divergence check, tokens-to-green
bench, installer shims, 60 tests.

Known limitations, already documented — **do not "fix" these by pretending**:
summarising compaction (we elide, so the saving is an upper bound); replay is
Anthropic-shaped (OpenAI bodies are converted, not native); no harness-log parser;
schema token counts are estimated at 4 chars/token.

---

# 2. Hard rules

1. **No number without a command.** Every figure in your output must be traceable
   to something you ran on this machine, and you must show the command.
2. **Label every number** as `measured` (provider-reported), `projected`
   (calibrated estimate), or `estimated` (heuristic). Never blur them.
3. **Never invent a benchmark result**, a competitor's number, or a speed-up you
   did not observe. If you cannot measure it, write `NOT MEASURED` and why.
4. **No new dependencies.** Standard library only, Python 3.9+. The zero-install
   property is the product.
5. **No telemetry, no network calls** beyond a provider the user configured.
6. **Do not delete or weaken existing tests.** You may add tests freely. If a test
   is wrong, fix it and say so in the commit message.
7. **Keep diffs minimal.** No rewrites of `analyze.py` into a framework, no
   architectural churn. A bug fix is a bug fix.
8. **Do not run anything that costs money** unless you reach Phase 4b and the user
   has an API key; even then, cap at `--max-turns 5`.
9. **Never commit** session data, `~/.ctxprobe`, `bench.csv`, or anything
   containing a key or a customer name.

---

# 3. Phase 1 — baseline

Run these and record the output verbatim. If any fails, you have found bug #0.

```bash
python --version
cd "C:\PROJECTS\Context Lab"      # adjust for your OS
python -m unittest discover -s tests -v
python scripts/analyze.py examples/sample-session.jsonl
python scripts/replay.py compare examples/demo.cassette.json
```

Expected: 60 tests pass; the audit prints five buckets and five metrics; the
baseline row reproduces the recorded token count with drift under ~15%.

Note: `examples/sample-session.jsonl` predates cassettes and has no object store,
so `compare` on it should fail with a clear "re-capture with --cassette" message.
That is correct behaviour, and it is matrix case #5 — do not "fix" it by
inventing empty objects. `examples/demo.cassette.json` is a real capture and must
work.

Also verify the skill is loadable as a skill: `SKILL.md` must have YAML
frontmatter with `name` and `description`, and every path it references must exist.

---

# 4. Phase 2 — measure the impact

You need three fixtures. Run what you can, and say plainly what you skipped.

## Fixture A — deterministic, no API key

```bash
python tests/fake_upstream.py 8899 realistic &        # or: start /b ... / PowerShell Start-Process
python scripts/wrap.py --cassette --upstream http://127.0.0.1:8899 \
    --session audit-a --home ./.audit -- python examples/demo_agent.py
python scripts/wrap.py --cassette --upstream http://127.0.0.1:8899 \
    --session audit-a2 --home ./.audit -- python examples/demo_agent.py
python scripts/replay.py build .audit/audit-a.jsonl
python scripts/replay.py compare .audit/audit-a.cassette.json --window 3000
```

Purpose: prove the pipeline is **deterministic**. The two captures' token totals
and policy deltas must agree within 1%. If they do not, that is a bug.

## Fixture B — a real harness session (preferred, if available)

If a harness is installed and you can set a base URL (see
`references/harnesses.md`, including the OmniRush two-minute test):

```bash
python scripts/wrap.py --cassette --session audit-b -- claude     # or codex, aider, ...
python scripts/analyze.py --latest
python scripts/replay.py build .audit/audit-b.jsonl   # adjust path
python scripts/replay.py compare .audit/audit-b.cassette.json
```

Run one small, real task — ask the agent to fix a trivial failing test in a scratch
repo. Do not use the user's production repo.

Report: total spend, the bucket split, CHR/PSI/RDR/ST/CT, the top three fixes with
their estimated savings, and the calibration drift. If drift is above 15%, say the
projection is unreliable on this session and quote only measured numbers.

## Fixture C — policy comparison (the headline table)

Across Fixtures A and B, produce one table:

| Policy | Tokens in | Δ vs baseline | Cost | Δ cost | Cache % | Trajectory |
|---|---|---|---|---|---|---|
| baseline | | — | | — | | preserved |
| cache-stable | | | | | | |
| dedupe-reads | | | | | | at risk |
| schema-gating | | | | | | |
| truncate-200 | | | | | | at risk |

State `n` (number of turns behind each row) and the drift for that session.

## Phase 4b — optional live check (costs money)

Only if the user supplies a key and explicitly agrees:

```bash
python scripts/replay.py run .audit/audit-b.raw --policy dedupe-reads --max-turns 5 --yes
```

Report the divergence rate. If it is non-zero, name the turns that diverged and
say what that implies: the saving is real for tokens, unproven for quality.

## What you must NOT claim

- That it reduces cost by a specific percentage **in general**. Only on the
  sessions you measured.
- That a policy preserves correctness. You only know whether decisions changed,
  and only if you ran 4b.
- Any comparison to another tool's benchmark. You may cite a tool's *published*
  claim only with its URL and the word "self-reported".

---

# 5. Phase 3 — bug hunt

Work through this matrix. For each: reproduce, record, decide, fix if real.

| # | Scenario | How to trigger | Correct behaviour |
|---|---|---|---|
| 1 | Path with spaces | run every command from `C:\PROJECTS\Context Lab` | works; no unquoted-path failures |
| 2 | Port already in use | occupy 8787, then `wrap.py` | clear error, or auto-picks another port |
| 3 | Harness ignores env var | point at a binary that never calls the proxy | says so plainly, suggests `--upstream` / flag; no silent empty report |
| 4 | No API key | `replay.py run` without env | exits 2 with a useful message |
| 5 | Missing `--cassette` | capture without it, then run `compare` | error names the flag to re-capture with |
| 6 | Corrupt/partial JSONL | truncate a line mid-write, then analyze | skips the bad line, does not crash |
| 7 | Empty session | start proxy, make no calls, analyze | friendly message, non-zero exit |
| 8 | Streaming response | real streaming call through the proxy | bytes arrive incrementally; usage parsed from SSE |
| 9 | Upstream 401/429/500 | fake upstream returns an error | error surfaces to the harness unchanged, recorded as a turn |
| 10 | OpenAI-shaped body | POST `/v1/chat/completions` | provider detected, usage parsed, no crash |
| 11 | Unicode in content | emoji/CJK in a prompt | no encoding crash on Windows console or in the HTML |
| 12 | Very large session | >20 MB of JSONL | analyze completes in reasonable time and memory |
| 13 | Read-only home dir | no permission to write `~/.ctxprobe` | clear message suggesting `--home` |
| 14 | Ctrl-C mid-stream | interrupt during a response | proxy exits cleanly, session file still valid |
| 15 | Two sessions at once | two proxies, two harnesses | files do not interleave or clobber |
| 16 | Policy on empty toolset | `schema-gating` with no tools | returns tools unchanged, never empties them |
| 17 | `--window` omitted | `compare` with `compact-at-40` and no window | no compaction, no crash, documented |
| 18 | Windows console encoding | run on cp1252 console | ASCII-only terminal output; no `UnicodeEncodeError` |

Add any other failure you hit while doing Phase 2 — real use finds more than this
list.

**Severity rule:** fix anything that (a) crashes, (b) silently produces a wrong
number, or (c) makes a first-time user unable to get a first result. Cosmetic
issues: note them, do not gold-plate.

---

# 6. Phase 4 — fix what is real

For each fix:

1. Write the failing test **first** in `tests/`, in the existing style
   (`unittest`, no network, `tempfile` for any file work).
2. Make the minimal change.
3. Run the full suite: `python -m unittest discover -s tests`.
4. If behaviour changes, note it in `CHANGELOG.md` under an `Unreleased` heading.

Rules for fixes:

- A wrong number is worse than a crash. If attribution is wrong, fix it even
  though nothing appears broken.
- Do not add a dependency to fix an encoding or path issue; the stdlib has what
  you need.
- Do not silence an error to make a test pass. If a case is genuinely unsupported,
  fail loudly with a message that tells the user what to do instead.

---

# 7. Phase 5 — write the impact artifacts

Create `IMPACT.md` in the repo root with exactly these sections:

```markdown
# Impact

## What was measured
Machine, OS, Python, date, harness and version (or "bundled demo agent"),
number of turns, model.

## Headline
Three bullets maximum. Each with a number, a unit, and a label
(measured / projected / estimated).

## Method
The commands, in order, that reproduce every number above.

## Results
The policy table from Fixture C.

## Attribution
The bucket split and the five metrics for the largest session measured.
What the biggest single cause was, in one sentence.

## What this does not prove
At least three honest bullets: task success, generality, and anything
you could not run.

## Reproduce
A copy-paste block that a stranger can run in under five minutes.
```

Then add a short **Impact** section to `README.md` that links to `IMPACT.md` and
carries only the three headline bullets plus the reproduction command. Do not paste
the whole table into the README.

Also write `results/impact.json` — machine-readable, one object per fixture:

```json
{"fixture": "A", "turns": 8, "measured": {...}, "projected": {...}, "drift_pct": 0.4}
```

Commit the raw session files only if they contain no customer data and no keys.

---

# 8. Phase 6 — commit and report

One commit per concern, not one giant commit. Suggested:

```
fix(analyze): handle truncated session lines
test(analyze): regression for partial JSONL
docs: add measured impact for 2026-10-24
```

Then report back in this shape:

```
## Baseline
tests: X passed / Y failed
version: ...

## Impact (measured)
fixture A: ... turns, ... tokens, drift ...%
fixture B: ...
policy table: (paste)

## Bugs
| # | severity | found | fixed | test |
...

## Not fixed
...and why (out of scope / needs a key / cosmetic)

## What remains to build
...

## Files changed
...
```

---

# 9. Definition of done

- [ ] `python -m unittest discover -s tests` passes, and the count only went up.
- [ ] `IMPACT.md` exists; every number in it came from a command you ran.
- [ ] README links to it and does not overstate.
- [ ] Every fixed bug has a regression test.
- [ ] No new dependency; `requirements.txt` still does not exist.
- [ ] No secrets, sessions, or `bench.csv` committed.
- [ ] `CHANGELOG.md` updated if behaviour changed.
- [ ] You can state, in one sentence, what this tool demonstrably does and what it
      demonstrably does not.

---

# 10. What remains to build — scope fence

Wanted, in priority order. **Only touch these if they block a real user today.**

1. **Summarising compaction.** `compact-at` currently elides, which makes its
   saving an upper bound. A real implementation sends the dropped span to the
   model and keeps the summary.
2. **Native OpenAI replay.** `compare` reconstructs Anthropic-shaped requests.
   OpenAI bodies round-trip approximately.
3. **Harness-log adapters.** When a harness's own log contains usage, parse it, so
   the tool works without relaunching under a proxy.
4. **A GitHub Action** that fails a PR when tokens-to-green regresses on a named
   task.
5. **Verified harness matrix.** Each row needs a real session and a version stamp.

**Out of scope for this audit:** the public leaderboard, a hosted service, a GUI,
fine-tuning, multi-agent orchestration, or any attempt to increase stars. Do not
start them.

---

# 11. What not to do

- Do not fabricate or round up a number to make the project look better.
- Do not compare against another project's benchmark, or borrow their marketing
  claim.
- Do not "improve" the metrics so the dashboard looks good. If CHR is 41%, report
  41% and say why.
- Do not rename things, restructure directories, or reformat unrelated files.
- Do not weaken a test to make your change pass.
- Do not upload anything. There is no telemetry and there must not be.

---

# 12. Tone for the README

Write for a sceptical senior engineer who has read too many "10x your tokens"
posts. Short sentences. Concrete numbers. State the limitation before they think
of it. Never use the word "revolutionary". If a claim would embarrass you in a
review, cut it.

---

*If you finish early, do not add features. Re-run the full pipeline from a clean
clone and fix whatever breaks, because that is what the first user will hit.*

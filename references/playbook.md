# Fix catalogue

Twelve changes, ranked by impact per hour of effort. Each one is independently
verifiable: apply it, re-run the *same* task, compare with `bench.py`.

| # | Fix | Typical impact | Effort | Risk |
|---|---|---|---|---|
| 1 | Filter command output | 15–40% of session cost | 5 min | Low |
| 2 | Stabilise the prefix | 20–60% when CHR is low | 10 min | Low |
| 3 | Cut MCP servers | 5–20% | 5 min | Low |
| 4 | Read ranges, not files | 5–15% | habit | Low |
| 5 | Don't re-read after an edit | 5–15% | habit | Low |
| 6 | Plan before executing | 10–25% | habit | Low |
| 7 | Compact at 40–60% | 5–15% | 1 min | Medium |
| 8 | Explore with a cheap model | 10–30% | 5 min | Medium |
| 9 | Diff edits, not rewrites | 5–15% of output | habit | Low |
| 10 | Terse output mode | 5–20% of output | 2 min | Low |
| 11 | Subagents for exploration | 10–20% | 10 min | Medium |
| 12 | Batch API for non-interactive work | 50% on those calls | 30 min | Low |

The percentages are planning ranges from the mechanism, not benchmark results.
Measure your own.

Context Lab can apply the selected request-context fix during a real run instead
of only estimating it offline. Use one policy at a time:

```bash
python scripts/wrap.py --policy truncate:200 -- <agent command>
python scripts/wrap.py --policy dedupe-reads -- <agent command>
python scripts/wrap.py --policy cache-stable -- <agent command>
```

These are opt-in interventions. The normal recorder remains passthrough, and
the provider response is not rewritten. Measure the same task with
`bench.py --policy` before keeping a change.

---

## 1. Filter command output

**What:** never let raw build or test output reach the model.

```bash
pytest -q --tb=line          # instead of full tracebacks
npm install --silent
git diff --stat              # before any full diff
cargo test 2>&1 | tail -40
```

Or put a filter in front of every command with a wrapper like `rtk`, or a shell
function that pipes through `head -60`.

**Why:** output tokens are priced highest, and one verbose log is re-sent on every
subsequent turn. A 4,000-line install log is not 4,000 tokens once; it is 4,000
tokens times the number of turns left in the session.

**Verify:** RDR and the "largest single tool results" table should both fall.

---

## 2. Stabilise the prefix

**What:** everything before the first user message must be byte-identical across
turns.

- Remove dates, timestamps, cwd, git branch, request ids, and "current time" from
  the system prompt and from `CLAUDE.md` / `AGENTS.md`.
- Do not edit those files mid-session.
- Do not reorder tools between turns.
- Do not switch models mid-session unless you accept a cache reset.

**Why:** a cache read is about a tenth the price. One volatile field can cost you
90% of the available saving.

**Verify:** PSI should approach 100% and CHR should jump. If CHR stays low while
PSI is high, the cache is expiring on TTL, not being invalidated.

---

## 3. Cut MCP servers

**What:** keep two. Delete anything you have not called in a week.

**Why:** tool schemas are charged on every request whether used or not. Community
measurements put a heavy MCP setup at 40–70k tokens of overhead; lazy tool loading
or tool search can cut that by 80–95%.

**Verify:** ST should drop below 10%.

---

## 4. Read ranges, not files

**What:**

```bash
rg -n "function_name" src/        # locate first
# then read ~60 lines around the hit
```

Never `cat` a file over ~300 lines. Never read a file you have already read in
the same session unless it changed.

**Why:** whole-file reads are the second-largest tool-result bucket in most
sessions, and the model rarely needs more than the relevant function.

**Verify:** tool-result tokens per turn should fall; the task should still pass.
If success drops, your retrieval is too aggressive — widen the range.

---

## 5. Don't re-read after an edit

**What:** after an edit, run the test or the type checker. Do not read the file
back to "confirm".

**Why:** the edit already told you what changed. A confirmation read is pure
redundancy and it enters the context permanently.

**Verify:** RDR should drop under 10%. This is the single most mechanical win in
most sessions.

---

## 6. Plan before executing

**What:** produce a short plan, get it approved, then execute against it.

**Why:** history is re-sent every turn, so cost grows super-linearly with turn
count. Exploration that wanders produces turns that produce nothing. A plan
typically removes 20–40% of turns.

**Verify:** turns and TTG both fall on the same task.

---

## 7. Compact at 40–60%

**What:** compact well before the window is full, not when the harness forces it.

**Why:** compacting at the limit makes the summary itself a near-full-context
call, and leaves almost no room to work afterwards, which causes immediate
re-compaction. Compacting at 50% keeps the summary cheap and the working room
large.

**Risk:** compaction is lossy. On tasks with many small constraints, compacting
early can drop a requirement. Measure success rate, not just cost.

**Verify:** CT should fall and success should hold.

---

## 8. Explore with a cheap model

**What:** file search, summarisation, and "where is this defined" go to the cheap
model; planning and review go to the strong one.

**Why:** exploration is high-volume, low-difficulty work. Routing it typically
takes 60–80% of the turns at 5–15% of the price.

**Risk:** a weak model's mistake in exploration poisons everything downstream.
Keep the strong model for the plan and the final diff.

**Verify:** same task, same success, lower cost. If success drops, the routing
boundary is wrong.

---

## 9. Diff edits, not rewrites

**What:** emit a patch (unique-string replacement or unified diff), never a whole
file.

**Why:** output tokens are the expensive kind, and a 400-line rewrite of a 3-line
change is 130x the necessary output.

**Verify:** output tokens per edit should fall by an order of magnitude.

---

## 10. Terse output mode

**What:** instruct the agent to skip preamble, avoid restating the plan, and not
narrate before tool calls.

**Why:** output is 5–15% of tokens but 30–50% of the bill. Pleasantries are the
most expensive prose in the session.

**Risk:** too terse and you lose the reasoning that catches mistakes. Keep
reasoning, cut filler.

**Verify:** output share of cost should drop; success should hold.

---

## 11. Subagents for exploration

**What:** send open-ended search to a subagent, require a summary of 20 lines or
fewer back.

**Why:** the subagent's transcript stays out of the parent context. Only the
conclusion is charged to the main thread.

**Risk:** subagents can miss context the parent had. Give them the question, not
the history.

**Verify:** parent-session tool-result tokens should fall sharply.

---

## 12. Batch API for non-interactive work

**What:** anything that does not need a response now (bulk refactors, doc
generation, nightly analysis) goes through a batch endpoint.

**Why:** typically half price, and it composes with every fix above.

**Verify:** cost per item on the batch job, compared with the interactive path.

---

## The five-minute triage

If you only have five minutes:

1. `analyze.py --latest` and read CHR. If it is under 60%, do fix 2.
2. If RDR is over 15%, do fix 5.
3. If ST is over 10%, do fix 3.

Those three account for most of the recoverable spend in a typical session, and
none of them touch the user's code.

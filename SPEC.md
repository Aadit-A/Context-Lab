# Context Lab
### An open, harness-agnostic way to measure and cut agent token burn
*Project spec v0.1 · 24 Oct 2026 · MIT · target: a 6–8 week v0.1*

> **Implementation status (v0.1.0):** capture, attribution, the five metrics, the
> HTML report, and the tokens-to-green loop are shipped and covered by tests.
> The replay/counterfactual engine in §4.2 is designed but **not implemented** —
> it is the headline roadmap item, and until it exists every savings figure is an
> estimate rather than an experiment. See `CHANGELOG.md`.

---

## 1. TL;DR

Coding agents burn tokens for structural reasons, not because anyone writes long prompts. The fixes are known but **unmeasured**: nobody can tell you whether compacting at 40% beats compacting at 85%, because no public tool can replay the *same session* under two context policies and compare.

**Context Lab is two artifacts:**

1. **`ctxprobe`** — a local proxy that sits between any harness (Claude Code, Codex CLI, Cline, Aider, OpenCode, DeepSeek-based harnesses) and any provider, attributing every token to a cause: schemas, re-reads, oversized tool output, reasoning, compaction tax, cache misses.
2. **`ctx-replay`** — a cassette recorder/replayer that takes a captured session, replays the same tool outputs deterministically, and runs it under N different context policies to produce a **tokens-to-green** comparison.

The contribution isn't the dashboard. It's the **metric definitions, the replay protocol, and a public dataset** — a Lighthouse score for agent context.

---

## 2. Where the tokens actually go

Seven buckets. The important thing is that six of them **compound**, because the context is re-sent on every turn.

| Bucket | Typical share of spend | Why it compounds |
|---|---|---|
| **Re-sent history** | 40–65% | Every turn re-uploads the whole conversation. Turns × context = quadratic growth |
| **Tool schemas / MCP** | 5–25% | Fixed prefix, but charged on every single turn. Community reports of 40–70k tokens of schema overhead |
| **File reads** | 10–25% | Whole-file reads, plus 20–40% re-reads of files already in context |
| **Command output** | 5–20% | `npm install` at 4,000 lines, full test logs, giant diffs. Worst signal-to-token ratio of anything |
| **Reasoning tokens** | 5–15% | Billed at output rates, 3–8× input price |
| **Failed attempts** | 5–15% | The failure transcript stays in context and is re-sent forever |
| **Compaction** | 3–10% | Each summary is a full-context call, and the summary is lossy, causing re-work |

**The two multipliers that decide everything:**

- **Cache hit rate.** Cached input is ~10× cheaper than uncached. A session with a 90% hit rate costs roughly a tenth of the same session with 0%. Anything that changes the prefix — a timestamp in the system prompt, a re-ordered tool list, a mid-session model switch, an injected "current date" — silently resets it.
- **Turns.** Because history is re-sent, cutting turn count by 30% cuts total input by far more than 30%. This is why plan-then-execute beats explore-then-fix.

> **The counter-intuitive part:** output tokens are usually only 5–15% of the count but 30–50% of the bill, because they're priced 3–8× higher. Anyone optimising only "input tokens" is optimising the wrong half of the invoice.

---

## 3. What already exists (and why it isn't enough)

| Layer | Tools | What it does | Blind spot |
|---|---|---|---|
| **Provider caching** | Anthropic prompt caching, DeepSeek disk cache, OpenAI auto-cache | 50–90% discount on repeated prefix | Invisible to the user; no diagnosis when it breaks |
| **Harness compaction** | Claude Code `/compact`, Codex CLI local summarisation | Fits long sessions in the window | Threshold is a guess; lossy; no A/B evidence |
| **Output filtering** | [`rtk`](https://github.com/rtk-ai/rtk) (60–90% reduction on shell output), `caveman` (~75% off output prose) | Shrinks tool results before they hit context | Per-command heuristics; no attribution or proof of task impact |
| **Retrieval / repo maps** | [`code-review-graph`](https://github.com/phuryn/claude-usage) (6.8× on reviews, up to 49× on daily tasks), Aider repo map, Serena LSP | Sends only relevant symbols | Adds an index to maintain; retrieval misses hurt success rate |
| **MCP slimming** | Tool Search, progressive disclosure, [agentgateway](https://www.solo.io/blog/keeping-context-and-tokens-low-with-progressive-disclosure) (91% schema cut) | Loads tool schemas lazily | Only solves the schema bucket |
| **Usage dashboards** | `ccusage`, `claude-usage`, `claude-usage-monitor`, [`tokscale`](https://github.com/junhoyeo/tokscale) | Totals, burn rate, per-model cost | Post-hoc. Answers "how much", never "why" |
| **Log-based profilers** | [`tokenscope`](https://github.com/yaswanthme007/tokenscope) | Parses session logs, flags repeated content and bloated results | Logs lack request bodies, tool schemas, and cache accounting — so it can't see cache breaks or true prefixes |
| **Cost-per-task benchmarks** | [Arize](https://arize.com/blog/cost-per-successful-task-ai-model-benchmark) (10 models, 2,400 runs), [whatllm](https://whatllm.org/blog/coding-model-cost-per-solved-task) | Compare **models** on $/solved task | Compares models, not **context strategies**. Nobody publishes "same model, same task, 8 policies" |

### The three structural blind spots

1. **No causal attribution.** Every tool tells you *what* was large. None tells you *what it cost you* — e.g. "these 14 re-reads cost $3.10 and produced zero new information."
2. **No cross-harness truth.** Claude Code has JSONL logs; Codex CLI and most open harnesses don't. Any log-based tool is single-ecosystem by construction. The proxy layer is the only neutral vantage point.
3. **No counterfactual.** The field argues about compaction thresholds, repo maps, and output truncation using anecdotes and vendor benchmarks. There is no way to take *your* session and ask "what if I had compacted at 50%?"

**That third one is the whole project.**

---

## 4. The idea

### 4.1 `ctxprobe` — capture and attribute

A single binary (or `npx`) that runs a local HTTP proxy and a terminal UI.

```bash
$ ctxprobe audit -- claude          # launches the harness with the proxy injected
$ ctxprobe audit -- codex --model gpt-5
$ ctxprobe report ./sessions/2026-10-24T14-02.jsonl
```

**How it hooks in** (no patching, no forks):

| Harness | Mechanism |
|---|---|
| Claude Code | `ANTHROPIC_BASE_URL=http://127.0.0.1:8787` |
| Codex CLI / OpenAI-compatible | `OPENAI_BASE_URL` |
| DeepSeek, Kimi, Qwen, OpenRouter, local vLLM | same OpenAI-compatible override |
| Aider / Cline / OpenCode | provider base-URL setting |
| Anything else | `HTTP_PROXY` fallback |

**What it records per turn:** the full request body (messages, tool definitions, model, temperature), the response usage block (`input`, `output`, `cache_read`, `cache_creation`), wall-clock timing, and a hash of each message segment.

**What it computes** — five attribution passes:

1. **Segment diff.** Compare turn N's messages to turn N−1's. Everything unchanged is "carried"; everything new is "delta". You now know exactly how many tokens each tool call *introduced*.
2. **Content hashing.** Hash every file-read and command-output result. A repeat hash = a **redundant read** with a known dollar cost.
3. **Prefix stability.** Hash the normalised static prefix (system prompt + tool schemas, minus volatile fields). If it changes mid-session, mark the **cache break** and name the cause: new tool, timestamp, reordered list, model switch.
4. **Compaction detection.** Spot summarisation requests (long input, short output, meta prompt). Attribute the summary cost and the tokens it later forced the agent to re-derive.
5. **Outcome tagging.** Read the harness's own signals (test runs, git state at exit) to label each turn sequence as progress or thrash.

**Output:** a terminal table, an HTML report, and JSON on a published schema.

```
CONTEXT AUDIT — session 2026-10-24 14:02 — 187 turns — $14.83

  carried history        $6.10   41%   ████████████████
  tool schemas (MCP)     $2.94   20%   ████████
  command output         $2.11   14%   ██████
  file reads             $1.88   13%   █████
  reasoning              $1.02    7%   ███
  compaction             $0.61    4%   ██
  retries / thrash       $0.17    1%   █

  cache hit rate  41%  (target >75%)   ← $3.20 recoverable
  redundant reads 23 files, $0.94     ← 12 of them after an edit
  cache breaks    4                    ← all caused by "current_date" in system prompt

TOP FIXES
  1. Move the date out of the system prompt      est. −$3.20  (cache break × 4)
  2. Drop 2 of 4 MCP servers (unused in 30d)     est. −$1.90  (schemas)
  3. Pipe build logs through head -40            est. −$1.10  (3 commands = 61% of output)
```

Every recommendation carries an **estimated dollar delta derived from the session itself**, not from a blog post. That's the difference between this and a dashboard.

### 4.2 `ctx-replay` — the counterfactual

This is the part nobody has built.

**Cassette format.** A recorded session becomes a deterministic fixture: for each turn, the request fingerprint, the tool calls the model made, and the tool *results* (byte-exact). Replay never executes tools — it serves recorded results. No repo mutation, no flaky network, no side effects.

```
session.cassette/
  meta.json          # model, harness, task description, initial commit
  turns/0001.json    # request fingerprint, tool_calls[], tool_results[], usage
  workspace.diff     # final repo state, for scoring
```

**Policy plugin API.** A policy is ~40 lines:

```ts
export const policy: Policy = {
  name: "truncate-tool-output-60",
  transform(ctx) {
    return ctx.mapToolResults(r =>
      r.tokens > 60 ? headTail(r.content, 60) : r
    );
  }
};
```

**Shipped policies for v0.1:** `baseline`, `truncate-tool-output-{40,60,200}`, `dedupe-reads` (elide re-reads of unchanged files), `schema-gating` (only the tools the task needs), `compact-at-{40,70,90}%`, `repo-map-first`.

**Scoring.** Replay the policy, let the model run against recorded results, apply its edits to a scratch copy of the repo, run the task's tests. Report `solved?`, `tokens`, `cost`, `turns`, `CHR`.

```bash
$ ctx-replay run ./cassette --policies baseline,dedupe-reads,compact-at-40 --repeat 3

policy                solved   tokens     cost    turns   CHR    Δ cost
baseline               3/3     812k      $9.10    171    41%      —
dedupe-reads           3/3     604k      $6.72    158    68%    −26%
compact-at-40          3/3     655k      $7.44    164    63%    −18%
schema-gating          3/3     590k      $6.55    155    71%    −28%
truncate-60            2/3     498k      $5.61    149    70%    −38%  ← breaks the build
```

That last row is the entire point. Truncation looks like the biggest win until you notice it costs you a solve. **No existing tool can show you that.**

---

## 5. The metric spec (the actual contribution)

Publish these as a versioned spec with reference implementations. Adoption of the *vocabulary* outlives any tool.

| Metric | Definition | Healthy range |
|---|---|---|
| **CHR** — cache hit ratio | `cache_read / (cache_read + cache_creation + uncached)` | > 0.75 |
| **PSI** — prefix stability index | share of consecutive turn pairs with an identical normalised prefix hash | > 0.90 |
| **RDR** — redundant data ratio | tokens from tool results whose content hash already appeared unchanged / total tool-result tokens | < 0.10 |
| **ST** — schema tax | tool-definition tokens × turns / total input tokens | < 0.10 |
| **CT** — compaction tax | tokens spent generating summaries + tokens re-derived after a lossy summary / total | < 0.08 |
| **SDR** — signal density ratio | tokens in messages immediately preceding a state change / total tokens | > 0.30 |
| **TTG** — tokens to green | total tokens from task start to first passing test | task-dependent; the north star |
| **CPS** — cost per solve | total cost including failed attempts ÷ tasks solved | task-dependent |

Two rules make these honest: **always report CHR alongside TTG** (otherwise a cheap cache-hungry policy looks good while being fragile), and **always report CPS, not cost per run** (a policy that fails cheaply is not a win).

---

## 6. Architecture

```
┌─────────────┐    HTTP     ┌──────────────────┐   HTTPS   ┌──────────┐
│  harness    │ ──────────▶ │     ctxprobe     │ ────────▶ │ provider │
│ (any)       │ ◀────────── │  proxy + capture │ ◀──────── │  API     │
└─────────────┘             └────────┬─────────┘           └──────────┘
                                     │
                          ┌──────────▼──────────┐
                          │  attribution engine │  diff · hash · prefix · compaction
                          └──────────┬──────────┘
                     ┌───────────────┼────────────────┐
                     ▼               ▼                ▼
              session.jsonl    report.html      cassette/
                     │                                │
                     └──────────▶ ctx-replay ◀────────┘
                                   policies → scores
```

- **Language:** TypeScript, one process, zero native deps. `npx ctxprobe` has to be a one-liner or nobody tries it.
- **Storage:** append-only JSONL, one directory per session, `~/.ctxprobe/`.
- **Privacy:** local only, no telemetry, redaction on by default (env values, secrets, absolute home paths). A `--share` flag produces a sanitised cassette for the public dataset.
- **Cost model:** per-provider price table in a JSON file the community can PR. Publish the date of the price snapshot, always.
- **Extensibility:** policies and providers are plugins; the report schema is versioned (`schema/v0.1.json`).

---

## 7. Scope for v0.1 (6–8 weeks)

| Week | Deliverable | Done when |
|---|---|---|
| 1 | Proxy + passthrough for Anthropic and OpenAI-compatible APIs | `ctxprobe audit -- claude` captures a real session end to end |
| 2 | Segment diff + content hashing + terminal report | The table in §4.1 prints real numbers on a real session |
| 3 | Prefix/cache analysis + recommendation engine | Cache breaks are detected and named correctly on 5 hand-made cases |
| 4 | Cassette recorder + deterministic replayer | A recorded session replays with byte-identical tool results |
| 5 | 4 policies + scoring harness (apply diff → run tests) | `baseline` reproduces the original cost within 5% |
| 6 | 3 reference tasks + `dedupe-reads` / `schema-gating` policies | The §4.2 table is reproducible on a clean machine |
| 7–8 | Spec doc, dataset release, blog post, CI | A stranger can run it from the README in under 5 minutes |

**Explicit non-goals for v0.1:** no hosted service, no team dashboards, no fine-tuning, no multi-agent orchestration, no GUI beyond the static HTML report.

**Success looks like:** 300+ GitHub stars, 3 external policy PRs, and at least one "we changed our default compaction threshold because of this" issue.

---

## 8. The benchmark protocol

The dataset is what makes this citable. Rules:

1. **Fixed task corpus.** Start with 25 real, public issues across 5 repos (TypeScript, Python, Go, Rust, Java) each with a verifiable test. Deliberately include 5 tasks known to need long context.
2. **Same model, same seed.** Temperature 0 where supported; 3 repeats; report median and spread. Never compare across models in the headline table.
3. **Cost from a dated price snapshot**, and report CHR separately so a reader can reprice for their own situation.
4. **Publish failures.** A policy that solves 24/25 for 40% less is more interesting than one that solves 25/25 and costs the same.
5. **Reproducibility.** Every number in the leaderboard comes with a cassette + commit SHA + policy version. If it can't be replayed, it doesn't ship.
6. **Declare the threats:** model nondeterminism, cassette drift when the repo moves, cache pricing differences, and the fact that a recorded session already reflects one model's choices (replay measures *context policy*, not *agent skill*).

---

## 9. How this gets adopted

- **The one-liner is the ad.** `npx ctxprobe audit -- claude` produces a shareable report. People post their own numbers; the tool spreads itself.
- **Lead with a claim, not a feature.** Publish: *"We replayed 200 real sessions under 6 context policies. Schema-gating saves 28% with no success loss; truncating tool output at 60 tokens silently fails 1 in 3 tasks."* That sentence is the paper, the README, and the HN post.
- **Name the metrics.** If CHR/PSI/TTG get picked up by two other tools, you've won regardless of your repo's star count.
- **Neutrality is the moat.** You're not selling a harness, a model, or a proxy. You're the measurement layer every side can cite.
- **Land one integration.** A PR that adds a `--ctxprobe` flag to an existing harness, or a policy pack for Cline/OpenCode, buys more credibility than 50 blog posts.

---

## 10. Why this might fail, and what kills it

| Risk | Mitigation |
|---|---|
| Providers ship first-party attribution (Anthropic OTel, OpenAI tracing) | Stay cross-vendor and own the *counterfactual*; providers will never benchmark a competitor's harness |
| Harnesses absorb instrumentation | Your value is the neutral comparison and the public dataset, not the plumbing |
| Proxy adds latency or breaks streaming | Pass-through with zero buffering; SSE must stream unmodified |
| Cassettes rot as repos move | Pin commits; store the workspace diff; treat cassettes as fixtures with a version |
| Replay isn't faithful (recorded tool results differ from live) | Validate first: baseline replay must reproduce original cost within 5%, or the number is meaningless |
| Scope creep into a SaaS | Hard rule: no hosted anything until v1.0 |

---

## 11. If you only want to fix your own bill this week

No new tool required:

1. **Measure first.** Install a usage monitor (`ccusage`, `claude-usage-monitor`) so you know your baseline cost per session.
2. **Filter command output.** Install `rtk` (or alias `npm install` / `pytest` / `git diff` through `head`). This is the cheapest, largest single win.
3. **Cut MCP servers to 2.** Remove any you haven't used in a week. Schema tax is charged every turn, forever.
4. **Stabilise the prefix.** No timestamps or dynamic content in `CLAUDE.md`/`AGENTS.md`/system prompt. Fixed tool ordering. Don't switch models mid-session without expecting a cache reset.
5. **Read less.** `rg -n` before `read`; read line ranges; never `cat` a 500-line file; never re-read after an edit — re-run the test instead.
6. **Shape the output.** `pytest -q --tb=line`, `git diff --stat` before `git diff`, `npm install --silent`.
7. **Delegate exploration to a subagent** that returns a 20-line summary, not a transcript.
8. **Plan first, then execute.** Fewer turns is worth more than shorter prompts, because history is resent.
9. **Compact early** (at 40–60%, not 90%). Compaction near the limit forces a huge summary call and leaves no room to work.
10. **Use a cheap model for search and a strong model for reasoning.** Route, don't upgrade wholesale.

Each of these is measurable with §5's metrics — which is exactly the loop the project exists to close.

---

*Sources for the landscape in §3 are linked inline. Numbers attributed to third-party tools (rtk 60–90%, code-review-graph 6.8–49×, agentgateway 91%, Arize 2,400 runs) are the maintainers' own published claims, not independently verified here.*

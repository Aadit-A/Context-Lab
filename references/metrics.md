# Metrics

Five numbers, one north star. Every one is computed from a recorded session by
`scripts/analyze.py`; none of them are vendor-reported.

---

## 1. CHR — cache hit ratio

```
CHR = cache_read_tokens / (input_tokens + cache_read_tokens + cache_creation_tokens)
```

Anthropic reports non-cached input, cache writes, and cache reads separately.
OpenAI reports `prompt_tokens` with `prompt_tokens_details.cached_tokens` inside
it, so the denominator is just `prompt_tokens`.

**Why it dominates:** a cache read is roughly a tenth of the input price. Moving
CHR from 40% to 85% on a long session cuts input cost by about 80%.

**Target:** > 75%. Below 40% on a session longer than ten turns, something is
resetting the prefix on nearly every request.

**Typical causes of a low CHR**

| Cause | Symptom |
|---|---|
| A date, timestamp, or request id in the system prompt | CHR collapses to near zero on every turn |
| Tool list rebuilt per turn, or ordered non-deterministically | Prefix hash changes without any user action |
| Mid-session model switch | Provider has a separate cache per model |
| `CLAUDE.md` / `AGENTS.md` edited during the session | Every edit invalidates from that point |
| Cache TTL expired between turns | Hit rate falls only on long gaps |

---

## 2. PSI — prefix stability index

```
PSI = 1 - (turns where the normalised prefix hash changed) / (turns - 1)
```

The prefix is the system prompt plus the tool definitions, normalised before
hashing: ISO dates are masked to `<DATE>`, tool order is preserved (reordering is
a real cache breaker, so it must not be masked). OpenAI-shaped requests carry
the system prompt as a leading `system` message; it counts as part of the prefix.

**Target:** > 90%. `analyze.py` reports the likely cause of each break — model
change, tool-list change, or other.

PSI is the diagnostic; CHR is the symptom. A high PSI with a low CHR means the
prefix is stable but the cache is not being used — usually the provider's cache
TTL, or a request field like `metadata` that the SDK fills in.

---

## 3. RDR — redundant data ratio

```
RDR = tool-result tokens whose content hash already appeared unchanged
      / total tool-result tokens
```

A re-read is not free even when the file is unchanged: those bytes are inserted
again and re-sent on every later turn.

**Target:** < 10%. Above 25% is common in unoptimised sessions and is the single
most mechanical fix available.

Note on what counts: a second identical copy in a later turn is *new* messages
but *redundant content*. The analyser compares multisets, so a genuine re-read is
counted, while content that is merely carried forward is not.

---

## 4. ST — schema tax

```
ST = (tool-definition tokens per turn x turns) / total input tokens
```

Tool definitions are charged on every request whether or not the tool is called.
Nine MCP servers with verbose schemas commonly reach 10–25% of total input.

**Target:** < 10%. The estimate behind the fix (`est_saving_usd`) assumes you can
halve it, which lazy tool loading or dropping two servers usually achieves.

Schema tokens are estimated at 4 characters per token, because providers do not
break usage down by field. Treat ST as a ratio, not an exact count.

---

## 5. CT — compaction tax

```
CT = output tokens spent on summarisation turns / total output tokens
```

Compaction is detected heuristically: the message count collapses by more than
40% while the context is still large. Not every such turn is a compaction, so CT
is the least precise metric here.

**Target:** < 8%. High CT usually means compacting too late, which forces a huge
summary call and leaves almost no working room afterwards.

---

## North star: TTG — tokens to green

```
TTG = every token from task start until the test suite passes
```

A single `analyze.py` run cannot know this; `scripts/bench.py` records it per
named task and prints the delta against the previous run. Always pair TTG with
the solved flag. **Cost per solve, never cost per run** — a policy that fails
cheaply is not cheap.

---

## How the savings estimates are calculated

These are hypotheses, not measurements. Each one is derived from the session's
own counts and a documented multiplier.

| Recommendation | Formula | Multiplier rationale |
|---|---|---|
| Cache breaks | `breaks x avg_context x input_price x (1 - cache_read_mult) x 0.75` | Assumes 75% of the re-upload would otherwise have been cached |
| Schema tax | `schema_tokens x input_price x 0.5` | Assumes a 50% schema reduction |
| Redundant reads | `sum(tokens x min(times, 8) x input_price x 0.8)` | Caps runaway repeats at 8 occurrences |
| Output shaping | `sum(tokens x min(remaining_turns, 40) x input_price x 0.8)` | Assumes the result would have been re-sent to the end of the session |
| Compaction | `6% of total session cost` | Low confidence, flagged as such in the output |
| Verbose output | `35% of output cost` | Empirical, not measured per session |

Every number is presented with a confidence label. Treat `low` as "worth trying",
not "expected saving".

---

## Price table

Costs use `references/prices.json`, keyed by model-family substring. The snapshot
date is in the file. Update it when prices change; the token counts are
unaffected, so historical comparisons stay valid.

---

## Known limitations (v0.1)

1. **Schema tokens are estimated**, not reported by the provider.
2. **Compaction detection is heuristic.** A large context that shrinks for other
   reasons can be misread.
3. **Cache accounting differs by provider.** Anthropic is exact; OpenAI exposes
   only cached tokens; other OpenAI-compatible providers often report nothing, in
   which case usage is flagged `estimated: true`.
4. **Replay is not shipped yet.** Comparing two policies on the *same* recorded
   tool outputs is the roadmap item that turns these estimates into experiments.
5. **No log-based mode.** Harness logs omit request bodies and cache
   accounting, which is why the proxy exists.

# Changelog

All notable changes. Format loosely follows Keep a Changelog; versions follow
semver once 1.0 ships.

## [0.2.0] - 2026-10-24

Replay. Projections become experiments.

### Added
- `cassette.py` — content-addressed object store, response capture, and a
  `ToolResultStore` with exact / approx / miss lookup by tool call.
- `policies.py` — a `Policy` protocol (`transform_block`, `transform_tools`,
  `transform_messages`) with six policies: baseline, cache-stable,
  dedupe-reads, schema-gating, truncate-N, elide-N, compact-at-N.
- `replay.py compare` — calibrated projection. The untouched session reproduces
  the tokens the provider actually charged, and the reported drift says how
  close that is; every other policy inherits the same scale.
- `replay.py run` — live replay that reports how often a policy changes the
  model's tool calls, so a saving can be separated from a behaviour change.
- `replay.py build` — writes a portable `<session>.cassette.json`.
- `ctxprobe serve --cassette` — records response blocks and content objects.

### Changed
- Session schema v2: turns may carry `resp` and `req.tools`.
- Policies operate on cassette blocks, not provider payloads, so they run
  offline and are unit-testable without a network.

### Notes
- `compact-at` measures the window in characters (the estimator's native unit);
  divide a provider token limit by about four.
- Compaction simulation elides rather than summarises, so its saving is an upper
  bound.

## [0.1.0] - 2026-10-24

First working release.

### Added
- `ctxprobe.py` — stdlib HTTP proxy that records requests from any
  Anthropic/OpenAI-compatible harness. Streaming passthrough with usage
  extraction from SSE events.
- `analyze.py` — attribution into five buckets (carried history, schemas, tool
  results, new prompts, output), five metrics (CHR, PSI, RDR, ST, CT), and a
  ranked fix list with confidence labels.
- `report.py` — self-contained HTML report.
- `wrap.py` — run any command under the proxy and audit it in one step.
- `bench.py` — tokens-to-green recording with per-task deltas.
- `install.py` — installs the skill and writes shims for Claude Code,
  AGENTS.md, Cursor, Cline, and Gemini CLI.
- `SKILL.md` — agent-facing procedure, for any harness that supports skills or
  reads project instruction files.
- Reference docs: metric definitions, harness wiring, 12-item fix catalogue.
- 16 tests covering proxy round-trip, streaming, prefix masking, attribution,
  redundancy detection, rendering.

### Known limitations
- Schema tokens are estimated at 4 characters per token.
- Compaction detection is heuristic.
- No replay yet — policy comparison is still an estimate, not an experiment.
- OmniRush wiring is untested against vendor documentation.

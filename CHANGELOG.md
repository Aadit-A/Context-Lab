# Changelog

All notable changes. Format loosely follows Keep a Changelog; versions follow
semver once 1.0 ships.

## [Unreleased]

### Added
- `scripts/autotune.py`: budget-driven automation over the live policies.
  `plan` projects a safety-ordered ladder (baseline, cache-stable, truncate,
  dedupe-reads, schema-gating) against one recorded session — calibrated via
  the cassette when present, labelled estimate otherwise — and selects the
  lowest tier fitting `--budget` (tokens, `N%`, optional `--budget-usd`,
  `--max-tier safe|balanced|aggressive` risk cap). `enable`/`disable`/`status`
  persist the choice to `~/.ctxprobe/autotune.json`; `guard` steps the policy
  back down the ladder when a `bench.py` run regresses on the same task.
- `--policy auto` on `wrap.py`, `bench.py`, and `ctxprobe serve`: expands to
  the persisted autotune policy at proxy start. Still explicit opt-in — the
  default recorder remains byte-for-byte passthrough and responses are never
  rewritten.
- Sub-skills: `skills/budget-guard` (context-lab-budget-guard; use when the
  user states a max token usage or asks for set-and-forget reduction) and
  `skills/auto-fix` (context-lab-auto-fix; router mapping the top audit
  finding to exactly one policy or config change). `install.py` installs them
  beside the main skill for the `claude` and `claude-project` targets.
- `references/autotune.md` documents the ladder order, tier caps, budget
  semantics, estimate multipliers, and guard behavior.
- `tests/test_autotune.py` — 24 tests covering ladder order, budget parsing,
  tier selection (fit, floor, cap, drift-withholding), config lifecycle,
  `auto` resolution, and guard step-down.

## [0.3.0] - 2026-10-26

Findings of the 2026-10-24 self-audit (`SELF-AUDIT.md`) plus the v0.3.0 live
policy gateway. Session schema v2 is unchanged and no metric formula moved, so
`SCHEMA_VERSION` stays 2. Old session files remain readable.

### Added
- An explicit live policy gateway: `wrap.py --policy` / `ctxprobe serve
  --policy` can reduce repeated tool results, truncate oversized tool output,
  stabilise volatile prefix text, or apply an explicit tool allowlist before a
  request reaches the provider. The default recorder remains passthrough and
  provider responses are never rewritten.
- `bench.py --policy` so a live context change can be measured against the same
  task instead of stopping at an offline projection.

### Fixed
- `install.py` now stages a skill copy before moving it into place, so
  `--target all --dest .` and other destinations inside the source tree do not
  recurse into themselves or copy audit session scratch data.
- `ctxprobe serve` no longer silently co-binds an occupied port on Windows
  (SO_REUSEADDR allowed a second listener to steal traffic); it now exits with
  the port number and a hint. An unwritable `--session-dir` also produces a
  one-line error instead of a traceback.
- Streamed responses are decoded with an incremental UTF-8 decoder. A multibyte
  character split across read chunks was previously corrupted in the recorded
  response blocks (and therefore in cassette objects).
- OpenAI-shaped bodies: `role: "tool"` messages are recorded as `tool_result`
  blocks, so their tokens land in the "command + file output" bucket and RDR can
  see repeats (they were previously counted as plain text). A leading `system`
  message is now part of the prefix hash, so PSI can observe OpenAI cache
  breaks. `replay.py`'s raw-body parser classifies both the same way.
- `analyze.py` terminal output is ASCII-only again; previews containing CJK or
  emoji no longer crash a cp1252 console or a redirect.
- `replay.py compare` now says so when `compact-at-*` rows did not compact
  because no `--window` was given, instead of silently showing +0%.
- `install.py --target all` stays inside `--dest` (it used to write to
  `~/.claude/skills/` as a side effect). User-level install is unchanged and
  explicit: `--target claude`.
- HTML report footer states the real generator version instead of a hardcoded
  `v0.1.0 / schema v1`.

### Audit artifacts
- `tests/test_selfaudit.py` — 14 regression tests covering every fix above.
- `IMPACT.md` and `results/impact.json` — measured numbers from the audit, with
  provenance labels and explicit non-claims.

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
- Offline projections are calibrated estimates; live trajectory quality still
  needs a real task and, for `replay.py run`, a provider key.
- OmniRush wiring is untested against vendor documentation.

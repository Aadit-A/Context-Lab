# Changelog

All notable changes. Format loosely follows Keep a Changelog; versions follow
semver once 1.0 ships.

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

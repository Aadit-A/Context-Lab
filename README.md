# Context Lab

**Measure where an AI coding agent spends tokens, apply a targeted context fix,
and verify whether the same task got cheaper.**

Context Lab is a local, standard-library-only toolkit for Claude Code, Codex CLI,
Cline, Roo, Aider, OpenCode, Cursor, Gemini CLI, OmniRush, local models, and
other Anthropic- or OpenAI-compatible harnesses.

It answers three questions:

1. **Where did this session spend tokens?**
2. **What would a different context policy have cost on this exact session?**
3. **Can I apply one fix and measure the same task again?**

## At a glance

- **Local only:** no telemetry, hosted service, account, or data upload.
- **No dependencies:** Python standard library only; Python 3.9+.
- **Harness-agnostic:** redirect a provider base URL; do not fork or patch the harness.
- **Evidence-first:** every recommendation is tied to the recorded session.
- **Opt-in remediation:** apply `cache-stable`, `dedupe-reads`, `truncate:N`, or
  `schema-gating` during a real wrapped run.

## What it does

### Capture

`ctxprobe.py` is a local HTTP recorder. It forwards requests to the provider,
streams responses back to the harness, and records per-turn metadata:

- message hashes, sizes, roles, and 100-character previews;
- tool definitions and prefix hashes;
- provider usage: input, output, cache read, and cache write;
- response blocks when cassette capture is enabled.

The default recorder does not rewrite request or response traffic. Full request
bodies are stored only when `--record-raw` is explicitly enabled.

### Attribute

`analyze.py` separates spend into carried history, tool schemas, tool results,
new text, and model output. It also reports:

- **CHR** — cache hit ratio;
- **PSI** — prefix stability index;
- **RDR** — redundant data ratio;
- **ST** — schema tax;
- **CT** — compaction tax;
- ranked fixes with session-derived estimated savings.

### Compare

`cassette.py` and `replay.py` turn a captured session into an offline fixture.
`replay.py compare` applies policies to the same recorded messages and calibrates
the projection against the provider usage in the capture.

### Apply and verify

`wrap.py --policy` is the explicit live intervention. It changes selected request
context before the provider sees it, but still passes provider responses through.
Use `bench.py --policy` to compare tokens-to-green on the same task.

## Quickstart

```bash
git clone https://github.com/Aadit-A/Context-Lab.git
cd Context-Lab

# Record a real harness session.
python scripts/wrap.py -- claude

# Read the terminal audit, JSON, or HTML report.
python scripts/analyze.py --latest
python scripts/analyze.py --latest --json
python scripts/analyze.py --latest --html report.html
```

For a cassette and offline policy comparison:

```bash
python scripts/wrap.py --cassette -- claude
python scripts/replay.py build ~/.ctxprobe/sessions/<session>.jsonl
python scripts/replay.py compare ~/.ctxprobe/sessions/<session>.cassette.json
```

### Apply a fix

Choose one policy that matches the audit. Do not stack policies until the first
change has been measured.

```bash
# Remove volatile dates, times, and labelled run identifiers from the prefix.
python scripts/wrap.py --policy cache-stable -- claude

# Replace exact repeated tool results with a short marker.
python scripts/wrap.py --policy dedupe-reads -- claude

# Keep the head and tail of oversized tool results.
python scripts/wrap.py --policy truncate:200 -- claude

# Keep only explicitly named tools. Treat this as trajectory-risky.
python scripts/wrap.py --policy schema-gating:Read,Edit,Bash -- claude
```

Prove the result on the same task:

```bash
python scripts/bench.py --name fix-billing \
  --test "python -m unittest discover -s tests" \
  --policy truncate:200 -- claude "fix the failing test"
```

`dedupe-reads`, `truncate:N`, and an explicit schema allowlist can change what a
model sees. A cheaper run is not a win if the task no longer passes.

### Set-and-forget budgets (autotune)

State a maximum and let the toolkit pick, persist, and guard the policy:

```bash
python scripts/autotune.py plan --latest --budget 100000   # or 50%, or --max-tier safe
python scripts/autotune.py enable truncate:200 --budget 100000
python scripts/wrap.py --policy auto -- claude             # persists across runs
python scripts/bench.py --name fix-billing --test "..." --policy auto -- claude "fix the tests"
python scripts/autotune.py guard --name fix-billing        # steps DOWN on regression
```

`plan` projects a safety-ordered policy ladder against the recorded session
(calibrated when a cassette exists, labelled estimate otherwise) and selects
the lowest tier that fits the budget. `guard` watches bench results: if a
cheaper run stops solving the task, the persisted policy automatically steps
down one tier. Design: [`references/autotune.md`](references/autotune.md).

Two sub-skills automate the decision itself: **context-lab-budget-guard**
(user states a cap) and **context-lab-auto-fix** (router: audit finding to
exactly one fix). See [`skills/README.md`](skills/README.md); `install.py`
ships them beside the main skill.

## No-key demo

The repository includes a fake localhost provider and a demo agent. No API key or
external network access is required.

Terminal 1:

```bash
python tests/fake_upstream.py 8899 realistic
```

Terminal 2:

```bash
python scripts/wrap.py \
  --upstream http://127.0.0.1:8899 \
  --policy truncate:200 \
  -- python examples/demo_agent.py
```

For the committed offline capture:

```bash
python scripts/replay.py compare examples/demo.cassette.json
```

## Measured impact

Real-provider validation was attempted with Claude Code `2.1.301` on an isolated
scratch task. The configured OpenRouter route rejected the request for an
unrecognized model/insufficient credits, and a temporary first-party Anthropic
route returned `401 Not logged in` before any model request reached Context Lab.
Therefore real token usage, task success, and savings are **NOT MEASURED**.

Full commands, tables, attribution, and non-claims: [`IMPACT.md`](IMPACT.md).

## How it works

```text
harness ──HTTP──▶ ctxprobe ──HTTPS──▶ provider
                    │
                    ├─ message diff       carried vs. new context
                    ├─ content hashes      redundant reads
                    ├─ prefix hashes       cache breaks and likely causes
                    └─ usage parsing       input / output / cache accounting
                           │
                           ▼
                    analyze ──▶ terminal · JSON · HTML
                           │
                           ▼
                    cassette ─▶ replay ─▶ projection + divergence check
```

The normal recorder is passthrough. The live gateway is enabled only by an
explicit `--policy` argument.

## Metrics

| Metric | Question | Healthy target |
|---|---|---:|
| **CHR** | How much input did the provider read from cache? | `> 75%` |
| **PSI** | How often did the cacheable prefix stay stable? | `> 90%` |
| **RDR** | How much tool-result content was repeated? | `< 10%` |
| **ST** | How much input was tool-schema overhead? | `< 10%` |
| **CT** | How much output went to compaction? | `< 8%` |
| **TTG** | How many tokens until the task first passes? | task-specific |

Definitions and savings arithmetic live in
[`references/metrics.md`](references/metrics.md).

## Harness support

| Harness | Status | Integration |
|---|---|---|
| Claude Code | verified | `ANTHROPIC_BASE_URL` |
| Codex CLI | verified | `OPENAI_BASE_URL` or `config.toml` |
| Aider | supported | `--openai-api-base` or env var |
| Cline / Roo Code / Continue | supported | provider base-URL setting |
| OpenCode | supported | `provider.<id>.options.baseURL` |
| Cursor | version-dependent | OpenAI base-URL override |
| Gemini CLI | partial | only when a base-URL option is available |
| OmniRush | unverified | generic OpenAI/Anthropic path |
| OpenAI-compatible providers | supported | `--upstream` or `OPENAI_BASE_URL` |

Per-harness instructions and the compatibility test are in
[`references/harnesses.md`](references/harnesses.md).

## Install as an agent skill

```bash
# User-level Claude skill.
python scripts/install.py --target claude

# Project-local skill plus instruction-file shims.
python scripts/install.py --target all --dest .
```

The installed skill tells an agent when to measure, how to choose a policy, and
how to report a measured before/after result. See [`SKILL.md`](SKILL.md).

## Privacy and safety

- Session files stay under `~/.ctxprobe/` unless `--home` is supplied.
- No telemetry, hosted mode, account, or analytics.
- Previews are limited to 100 characters and secrets are redacted.
- `--record-raw` is opt-in and can contain sensitive request bodies.
- Live policies are explicit; trajectory-risky policies must be verified with the
  same task before adoption.

## Repository layout

| Path | Purpose |
|---|---|
| `scripts/ctxprobe.py` | Local proxy, capture, usage parsing, response recording |
| `scripts/live_policy.py` | Explicit live request-context reductions |
| `scripts/analyze.py` | Attribution, metrics, recommendations |
| `scripts/cassette.py` | Content-addressed cassette storage |
| `scripts/policies.py` | Offline policy definitions |
| `scripts/replay.py` | Calibrated comparison and live divergence checks |
| `scripts/wrap.py` | Run a harness under the recorder |
| `scripts/bench.py` | Tokens-to-green comparison |
| `scripts/autotune.py` | Budget-driven policy selection, persistence, regression guard |
| `scripts/install.py` | Install the skill and harness shims |
| `skills/` | Sub-skills: budget-guard, auto-fix (router) |
| `references/` | Metrics, harnesses, policies, prices, replay notes |
| `tests/` | Standard-library regression and integration tests |
| `examples/` | No-key demo agent and committed cassette |

## Development

```bash
python -m unittest discover -s tests
python scripts/analyze.py examples/sample-session.jsonl
python scripts/replay.py compare examples/demo.cassette.json
python scripts/install.py --target dir --dest .audit/install-check --dry-run
```

The project intentionally has no third-party requirements. If `pytest` is not
installed, use the standard-library `unittest` command above.

More detail:

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — verification and pull requests;
- [`SELF-AUDIT.md`](SELF-AUDIT.md) — full self-audit procedure;
- [`SPEC.md`](SPEC.md) — design and metric rationale;
- [`CHANGELOG.md`](CHANGELOG.md) — release history.

## License

MIT.

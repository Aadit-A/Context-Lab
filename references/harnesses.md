# Harness wiring

The proxy is plain HTTP on localhost. Anything that lets you change the provider
base URL works. Anything that does not, cannot be measured this way — and no
amount of config fiddling will change that.

General rule: set the base URL to `http://127.0.0.1:8787` **with no path suffix**.
The proxy forwards the incoming path unchanged, so `/v1/messages` from the SDK
arrives as `/v1/messages` at the provider.

---

## Quick reference

| Harness | Mechanism | Set this |
|---|---|---|
| **Claude Code** | env var | `ANTHROPIC_BASE_URL` |
| **Codex CLI** | env var or `~/.codex/config.toml` | `OPENAI_BASE_URL`, or `model_providers.<id>.base_url` |
| **Cline** | settings UI | API Provider → Base URL |
| **Roo Code** | settings UI | Provider → Base URL |
| **Aider** | flag or env | `--openai-api-base`, `OPENAI_API_BASE`, `ANTHROPIC_API_BASE` |
| **OpenCode** | `opencode.json` | `provider.<id>.options.baseURL` |
| **Continue** | `config.json` | `models[].apiBase` |
| **Cursor** | settings | Override OpenAI base URL (version-dependent) |
| **Gemini CLI** | — | No standard base-URL variable; use `wrap.py` only if the CLI exposes one |
| **OmniRush** | generic | Try `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL`; see below |
| **Any OpenAI-compatible** | env var | `OPENAI_BASE_URL` |

---

## Claude Code

```bash
python scripts/ctxprobe.py serve --port 8787
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
claude
```

```powershell
python scripts\ctxprobe.py serve --port 8787
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:8787"
claude
```

Or in one shot: `python scripts/wrap.py -- claude`.

Notes:
- Claude Code sends `/v1/messages`; streaming must pass through unmodified, which
  the proxy does by re-chunking the upstream stream.
- A flat-rate plan (Pro/Max) does not bill per token, but the token counts still
  determine how fast you hit the limit. The audit is still useful; ignore the
  dollar column.

## Codex CLI

```bash
python scripts/ctxprobe.py serve --port 8787
export OPENAI_BASE_URL=http://127.0.0.1:8787
codex
```

If your build ignores the env var, add a provider in `~/.codex/config.toml`:

```toml
[model_providers.ctxprobe]
name = "ctxprobe"
base_url = "http://127.0.0.1:8787/v1"
env_key = "OPENAI_API_KEY"
```

Then run `codex -c model_provider=ctxprobe`.

## Aider

```bash
python scripts/wrap.py --provider openai -- aider --model gpt-5
```

or manually:

```bash
aider --openai-api-base http://127.0.0.1:8787/v1
```

Aider's repo map means its input tokens are already lower than most harnesses;
the interesting metric for Aider is usually RDR and output share.

## Cline / Roo Code / Continue

These are editor extensions, so `wrap.py` cannot launch them. Start the proxy,
then point the extension's base URL at `http://127.0.0.1:8787` (or `.../v1`
depending on the field's placeholder). Send one message, then:

```bash
python scripts/analyze.py --latest
```

## OmniRush

I could not find public documentation for OmniRush while building this, so the
wiring below is the **generic OpenAI/Anthropic-compatible path**, not a verified
integration. Treat it as a hypothesis with a two-minute test.

Try in this order:

1. **Environment variables** — most harnesses built on the OpenAI or Anthropic
   SDKs honour these without any code change:

   ```bash
   python scripts/wrap.py --provider openai -- omnirush
   python scripts/wrap.py --provider anthropic -- omnirush
   ```

   Whichever produces a session file with more than zero turns is the right one.

2. **An explicit base-URL flag** — check `omnirush --help` for `--base-url`,
   `--api-base`, `--endpoint`, or a config file key (`baseURL`, `apiBase`,
   `base_url`). If one exists, run the proxy with `--no-run` and point the flag
   at it.

3. **A config file** — if OmniRush stores provider settings in JSON/TOML/YAML,
   set the base URL there and restart it.

### The two-minute compatibility test

```bash
# terminal 1
python scripts/ctxprobe.py serve --port 8787 --session omnirush-test

# terminal 2 - launch OmniRush however you normally do, with the base URL set
# send one short message, then stop it

# terminal 3
python scripts/analyze.py --latest
```

Pass criteria:

| Check | Expected |
|---|---|
| Session file exists | `~/.ctxprobe/sessions/omnirush-test.jsonl` |
| Turns recorded | ≥ 1 per model call |
| `usage.input` > 0 | yes |
| `usage.estimated` false | true if the provider returns usage |
| Streaming works | responses arrive incrementally, not in one blob at the end |

If turns are zero, the harness is not honouring the base URL — check whether it
resolves the endpoint at start-up and caches it, or whether it appends its own
path (`/v1` + `/v1/chat/completions` shows up as a 404 in `--verbose`).

If streaming arrives in one blob, the harness may be requesting non-streaming
responses; that is fine for measurement, just slower to read.

**When it works, open a PR adding a verified row to this table.** That is the
contribution the project actually needs.

---

## Providers other than Anthropic and OpenAI

DeepSeek, Moonshot, Qwen, OpenRouter, Groq, Together, Fireworks, and local
servers (vLLM, Ollama, LM Studio) are all OpenAI-compatible:

```bash
python scripts/wrap.py --upstream https://api.deepseek.com -- your-agent
```

Two caveats:

- **Cache accounting is often missing** from these providers. You will see
  `estimated: true` and a CHR of 0 that means "not reported", not "no caching".
  DeepSeek does cache on disk; it just does not always surface it per request.
- **Prices differ wildly.** Copy `references/prices.json` and edit it before
  trusting the dollar figures. Token counts remain valid either way.

---

## What cannot be measured

- Harnesses that hard-code the provider endpoint and offer no override.
- Traffic you cannot redirect without a TLS-intercepting proxy. Do not do this.
- Requests made by a browser extension directly from a vendor's server.

In those cases, use the manual checklist in `playbook.md` and say clearly that
the numbers are rules of thumb.

# Adapters

Pre-written shims for harnesses that read project instruction files instead of
a formal skill directory.

`scripts/install.py` generates these and inserts them between markers, so you
normally do not need to copy anything by hand:

```bash
python scripts/install.py --target all --dest .
```

Use the files here when you would rather paste them yourself, or when you are
wiring a harness we have not covered.

| File | Goes to | Harnesses |
|---|---|---|
| `AGENTS.snippet.md` | `AGENTS.md` | Codex CLI, OpenCode, Amp, and most agent frameworks |
| `GEMINI.snippet.md` | `GEMINI.md` | Gemini CLI |
| `cursor.mdc` | `.cursor/rules/context-lab.mdc` | Cursor |
| `clinerules.md` | `.clinerules/context-lab.md` | Cline, Roo Code |

None of these contain the analysis logic. They only tell the agent *when* to run
the scripts and *how* to report results — the reasoning lives in `SKILL.md`.

For a harness we have not listed, the generic version is:

```markdown
For token or cost questions, read ./context-lab/SKILL.md and follow it.
```

# Replay

Replay answers one question: **would a different context policy have been cheaper
on this exact session?** It does not need the repo, the harness, or the network.

```
recording ──▶ cassette ──▶ policy ──▶ projection ──▶ (optional) live check
```

---

## 1. What a cassette contains

`ctxprobe serve --cassette` writes, per turn:

| Field | Meaning |
|---|---|
| `req.messages[]` | role, kind, content hash, size, tool name, tool ids |
| `req.tools` | the full tool definitions for that request |
| `req.prefix_hash` | hash of the normalised system + tools prefix |
| `usage` | input, output, cache read, cache write |
| `resp.blocks[]` | what the model actually returned, by content hash |

Plus an object store: `objects/<hash>.txt`, one file per unique block of content.
No raw request bodies are needed beyond the first capture, and no API key is
needed to compare.

`replay.py build <session>.jsonl` flattens all of this into a single portable
JSON file. `replay.py compare` accepts either form.

---

## 2. The policies

A policy implements up to three hooks:

```python
class Policy:
    name = "my-policy"
    preserves_trajectory = True   # claim: same decisions
    stable_prefix = False         # claim: prefix is byte-stable

    def transform_block(self, block, seen): ...      # one message block
    def transform_tools(self, tools, used): ...      # tool definitions
    def transform_messages(self, messages, ctx): ... # compaction only
```

| Policy | Hooks used | Changes | Claim |
|---|---|---|---|
| `baseline` | — | nothing | preserves trajectory |
| `cache-stable` | block, tools | strips dates/times | preserves trajectory, stabilises prefix |
| `dedupe-reads` | block | elides repeated tool output | **at risk** |
| `schema-gating` | tools | drops tools never called | preserves trajectory |
| `truncate-N` | block | head/tail to N tokens | **at risk** |
| `elide-N` | messages | keeps task + last N | **at risk** |
| `compact-at-N` | messages | compacts over N% of window | **at risk** |

Rules a policy must not break:

- never produce a body the provider rejects (pair `tool_use` with `tool_result`,
  start with a user turn, never an empty tool list);
- never drop the original task message;
- be deterministic given the same input and a `reset()` state.

---

## 3. Calibration

A raw character count is a bad token estimate. So the projection is **anchored**:

```
scale = recorded_input_tokens / estimated_input_tokens
projected_tokens(policy) = recorded_tokens × (policy_estimate / baseline_estimate)
```

The baseline row therefore reproduces what the provider charged, and every other
row is a *relative* change applied to real numbers. `compare` reports the residual
per-turn drift after scaling; above roughly 15%, distrust the row.

This matters because relative savings survive a bad absolute estimator, while
absolute savings do not.

---

## 4. Live replay and divergence

`replay.py run` re-sends each transformed request to a real provider and compares
the tool calls it gets back with the ones recorded in the next turn of the
cassette. Those recorded calls are the model's original decision, so agreement is
evidence that the policy did not change behaviour.

```
decisions changed 2/9 — turn 4: ['Read'] -> ['Grep']
```

Divergence is not automatically bad. A cheaper run that takes a different path
and still solves the task is a win; a cheaper run that silently skips the file it
needed is a regression. Replay tells you which turns to look at; only running the
task tells you the outcome.

Cost control: `--max-turns` defaults to 5, and anything estimated above $0.50
requires `--yes`.

---

## 5. Adding a policy

1. Subclass `Policy` in `scripts/policies.py`, set `name` and the two claims.
2. Register it in `REGISTRY`.
3. Add a test in `tests/test_replay_offline.py` that asserts the transformation
   and its claim.
4. Update the table above.

Keep policies small. A policy that both truncates and elides cannot tell you which
of the two caused the change.

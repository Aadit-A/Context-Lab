#!/usr/bin/env python3
"""
policies - context transformations, expressed over cassette blocks.

A policy transforms three things:

  transform_block(block, seen)      one message block, by kind
  transform_tools(tools, used)      the tool definitions for one request
  transform_messages(messages, ctx) the whole conversation (compaction only)

Blocks are cassette-shaped: {"role", "kind", "content", "h"}. Policies never see
an API client, so they can run offline against a recording.

Each policy declares two hypotheses, which `replay run` can falsify:

  preserves_trajectory  the model should still make the same decisions
  stable_prefix         the request prefix should be byte-stable, so the
                        provider's prompt cache can actually be used
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

DATE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?\b"
)
TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s?(?:AM|PM|am|pm)?\b")


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


class Policy:
    name = "policy"
    preserves_trajectory = True
    stable_prefix = False

    def reset(self) -> None:
        """Clear any state so a run is reproducible."""

    def transform_block(self, block: dict[str, Any], seen: set[str]) -> dict[str, Any]:
        return block

    def transform_tools(
        self, tools: list[dict[str, Any]], used: set[str] | None = None
    ) -> list[dict[str, Any]]:
        return tools

    def transform_messages(
        self, messages: list[dict[str, Any]], ctx: dict[str, Any]
    ) -> list[dict[str, Any]]:
        return messages


class Baseline(Policy):
    name = "baseline"


class CacheStable(Policy):
    """Strip dates and clock times so the prefix stops changing between turns.

    This is the only policy that changes the cache rather than the content, so
    its token count barely moves while its cost can fall by more than half.
    """

    name = "cache-stable"
    stable_prefix = True

    def transform_block(self, block, seen):
        if isinstance(block.get("content"), str):
            block["content"] = TIME_RE.sub(
                "<TIME>", DATE_RE.sub("<DATE>", block["content"])
            )
        return block

    def transform_tools(self, tools, used=None):
        for tool in tools:
            if isinstance(tool.get("description"), str):
                tool["description"] = DATE_RE.sub("<DATE>", tool["description"])
        return tools


class DedupeReads(Policy):
    """Elide a tool result whose bytes already appeared in the conversation.

    The block stays (a tool_result must exist for every tool_use); only the
    payload is replaced. Marked as trajectory-changing because the model can no
    longer re-derive details it had not yet used.
    """

    name = "dedupe-reads"
    preserves_trajectory = False

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def reset(self) -> None:
        self._seen = set()

    def transform_block(self, block, seen):
        if block.get("kind") != "tool_result":
            return block
        content = block.get("content") or ""
        key = block.get("h") or digest(content)
        if key in self._seen or key in seen:
            block["content"] = (
                f"[context-lab] identical tool result elided "
                f"({len(content)} chars, seen earlier)"
            )
        else:
            self._seen.add(key)
            seen.add(key)
        return block


class Truncate(Policy):
    """Head/tail truncate every tool result to N tokens (4 chars ≈ 1 token)."""

    name = "truncate"
    preserves_trajectory = False

    def __init__(self, tokens: int = 400) -> None:
        self.tokens = max(1, tokens)

    def transform_block(self, block, seen):
        if block.get("kind") != "tool_result":
            return block
        content = block.get("content") or ""
        limit = self.tokens * 4
        if len(content) <= limit:
            return block
        keep = max(20, limit // 2)
        block["content"] = (
            f"{content[:keep]}\n...[context-lab] truncated "
            f"{len(content) - keep * 2} chars...\n{content[-keep:]}"
        )
        return block


class SchemaGating(Policy):
    """Drop tool definitions that were never called in the recorded session."""

    name = "schema-gating"

    def __init__(self, keep: int | None = None) -> None:
        self.keep = keep

    def transform_tools(self, tools, used=None):
        used = used or set()
        if not tools or not used:
            return tools
        kept = [t for t in tools if t.get("name") in used]
        if not kept:
            return tools  # never hand the model an empty toolset
        if self.keep:
            kept = kept[: self.keep]
        return kept


class Elide(Policy):
    """Keep the task statement plus the last N messages."""

    name = "elide"
    preserves_trajectory = False

    def __init__(self, keep: int = 20) -> None:
        self.keep = keep

    def transform_messages(self, messages, ctx):
        if len(messages) <= self.keep:
            return messages
        head = messages[0] if messages[0].get("role") == "user" else None
        trimmed = messages[-self.keep :]
        while trimmed and (
            trimmed[0].get("kind") == "tool_result" or trimmed[0].get("role") != "user"
        ):
            trimmed = trimmed[1:]
        if head is not None and (not trimmed or trimmed[0] is not head):
            trimmed = [head] + trimmed
        return trimmed


class CompactAt(Policy):
    """Compact once the conversation exceeds `pct`% of the window.

    `window` is measured in characters -- the estimator's native unit -- because
    that is what replay can measure exactly. Divide a provider's token limit by
    about four to get a comparable number.
    """

    name = "compact-at"
    preserves_trajectory = False

    def __init__(self, pct: int = 80) -> None:
        self.pct = max(1, min(99, pct))

    def transform_messages(self, messages, ctx):
        window = ctx.get("window")
        if not window:
            return messages
        size = sum(len(m.get("content") or "") for m in messages)
        if size <= window * self.pct / 100:
            return messages
        keep = max(2, len(messages) // 3)
        elided = Elide(keep)
        return elided.transform_messages(messages, ctx)


REGISTRY: dict[str, Any] = {
    "baseline": Baseline,
    "cache-stable": CacheStable,
    "dedupe-reads": DedupeReads,
    "schema-gating": SchemaGating,
    "truncate": Truncate,
    "elide": Elide,
    "compact-at": CompactAt,
}

ALIASES = {"schema-keep": "schema-gating", "strip-volatile": "cache-stable"}


def parse(spec: str) -> tuple[str, str]:
    """Accept `name`, `name:arg` and `name-arg`."""
    spec = spec.strip()
    if ":" in spec:
        name, arg = spec.split(":", 1)
        return ALIASES.get(name, name), arg.strip()
    if spec in REGISTRY or spec in ALIASES:
        return ALIASES.get(spec, spec), ""
    head, _, tail = spec.rpartition("-")
    if tail.isdigit() and (head in REGISTRY or head in ALIASES):
        return ALIASES.get(head, head), tail
    return ALIASES.get(spec, spec), ""


def get(spec: str) -> Policy:
    name, arg = parse(spec)
    if name not in REGISTRY:
        raise SystemExit(
            f"unknown policy '{spec}'. Available: {', '.join(sorted(REGISTRY))}"
        )
    cls = REGISTRY[name]
    if name in ("truncate", "elide", "compact-at", "schema-gating"):
        if arg:
            return cls(int(arg))
        return cls()
    return cls()


# --------------------------------------------------------------------------- #
# applying a policy to a conversation
# --------------------------------------------------------------------------- #
def apply_to_blocks(
    messages: list[dict[str, Any]],
    policy: Policy,
    tools: list[dict[str, Any]] | None = None,
    used: set[str] | None = None,
    window: int | None = None,
    seen: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Transform a conversation and its tool definitions. Returns (messages, tools)."""
    seen = seen if seen is not None else set()
    out: list[dict[str, Any]] = []
    for block in messages:
        copy = dict(block)
        out.append(policy.transform_block(copy, seen))
    out = policy.transform_messages(out, {"window": window})
    tools_out = policy.transform_tools([dict(t) for t in (tools or [])], used or set())
    return out, tools_out


def usage_from_blocks(messages: Iterable[dict[str, Any]]) -> set[str]:
    used = set()
    for block in messages:
        if block.get("kind") == "tool_use" and block.get("n"):
            used.add(block["n"])
    return used

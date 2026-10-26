#!/usr/bin/env python3
"""Opt-in request policies for reducing context before it reaches a provider.

The normal ctxprobe path remains a byte-preserving recorder.  This module is
used only when a caller explicitly passes ``--policy`` to wrap.py or serve.
Responses are never rewritten; only selected request context is changed.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Callable, Iterator

from policies import DATE_RE, TIME_RE, digest, parse


VOLATILE_RE = re.compile(
    r"\b(?:unix\s+time|timestamp|request\s+id|run\s+id|process\s+id)"
    r"\s*[:=]\s*[A-Za-z0-9._-]+",
    re.IGNORECASE,
)


class LivePolicyError(ValueError):
    """Raised when a policy cannot safely be applied to a live request."""


LIVE_POLICIES = {
    "baseline",
    "cache-stable",
    "dedupe-reads",
    "schema-gating",
    "truncate",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _mask(text: str) -> str:
    text = DATE_RE.sub("<DATE>", text)
    text = TIME_RE.sub("<TIME>", text)
    return VOLATILE_RE.sub("<VOLATILE>", text)


def _map_strings(value: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [_map_strings(item, fn) for item in value]
    if isinstance(value, dict):
        return {key: _map_strings(item, fn) for key, item in value.items()}
    return value


def _map_descriptions(value: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(value, list):
        return [_map_descriptions(item, fn) for item in value]
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            out[key] = fn(item) if key == "description" and isinstance(item, str) else _map_descriptions(item, fn)
        return out
    return value


def _tool_result_slots(body: dict[str, Any]) -> Iterator[tuple[dict[str, Any], str]]:
    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        if message.get("role") in ("tool", "function") or message.get("tool_call_id"):
            if "content" in message:
                yield message, "content"
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("tool_result", "function_result"):
                if "content" in block:
                    yield block, "content"


def _content_text(value: Any) -> str:
    return value if isinstance(value, str) else _canonical(value)


def _replace_content(original: Any, text: str) -> Any:
    if isinstance(original, str):
        return text
    if isinstance(original, list):
        return [{"type": "text", "text": text}]
    return text


def _truncate(text: str, tokens: int) -> str:
    limit = max(1, tokens) * 4
    if len(text) <= limit:
        return text
    keep = max(1, limit // 2)
    removed = max(0, len(text) - keep * 2)
    return (
        f"{text[:keep]}\n...[context-lab] truncated {removed} chars...\n"
        f"{text[-keep:]}"
    )


class LivePolicy:
    """Transform JSON request bodies while keeping their provider shape."""

    def __init__(self, spec: str) -> None:
        self.spec = spec
        self.name, self.arg = parse(spec)
        if self.name not in LIVE_POLICIES:
            supported = ", ".join(sorted(LIVE_POLICIES))
            raise LivePolicyError(
                f"policy '{spec}' is offline-only or unknown. Live policies: {supported}. "
                "Use replay.py compare for elide/compact-at projections."
            )
        if self.name == "truncate":
            try:
                self.tokens = max(1, int(self.arg or "400"))
            except ValueError:
                raise LivePolicyError(f"truncate policy needs an integer token limit: {spec}") from None
        else:
            self.tokens = 0
        self.allowed_tools = {
            part.strip() for part in self.arg.split(",") if part.strip()
        } if self.name == "schema-gating" and self.arg else set()
        self.seen_results: set[str] = set()
        self.used_tools: set[str] = set()

    def transform(self, body: dict[str, Any], provider: str) -> dict[str, Any]:
        out = copy.deepcopy(body)
        if self.name == "baseline":
            return out
        if self.name == "cache-stable":
            return self._cache_stable(out)
        if self.name == "schema-gating":
            return self._schema_gate(out)
        if self.name in ("dedupe-reads", "truncate"):
            return self._tool_results(out)
        return out

    def observe(self, response: dict[str, Any], provider: str) -> None:
        if self.name != "schema-gating":
            return
        for block in response.get("blocks") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name"):
                self.used_tools.add(str(block["name"]))

    def _cache_stable(self, body: dict[str, Any]) -> dict[str, Any]:
        if "system" in body:
            body["system"] = _map_strings(body["system"], _mask)
        for message in body.get("messages") or []:
            if isinstance(message, dict) and message.get("role") == "system":
                message["content"] = _map_strings(message.get("content"), _mask)
        if "tools" in body:
            body["tools"] = _map_descriptions(body["tools"], _mask)
        return body

    def _schema_gate(self, body: dict[str, Any]) -> dict[str, Any]:
        tools = body.get("tools") or []
        wanted = self.allowed_tools or self.used_tools
        if not tools or not wanted:
            return body
        kept = [tool for tool in tools if isinstance(tool, dict) and tool.get("name") in wanted]
        if kept:
            body["tools"] = kept
        return body

    def _tool_results(self, body: dict[str, Any]) -> dict[str, Any]:
        for container, key in _tool_result_slots(body):
            original = container.get(key)
            text = _content_text(original)
            if self.name == "dedupe-reads":
                marker = digest(text)
                if marker in self.seen_results:
                    container[key] = _replace_content(
                        original,
                        f"[context-lab] identical tool result elided ({len(text)} chars, seen earlier)",
                    )
                else:
                    self.seen_results.add(marker)
            else:
                container[key] = _replace_content(original, _truncate(text, self.tokens))
        return body


def make(spec: str) -> LivePolicy:
    """Build an explicit live policy, with a user-facing validation error."""
    if spec.strip() == "auto":
        import autotune  # lazy: the passthrough path never reads the config file

        try:
            spec = autotune.resolve_policy()
        except autotune.AutotuneError as exc:
            raise LivePolicyError(str(exc)) from None
    return LivePolicy(spec)

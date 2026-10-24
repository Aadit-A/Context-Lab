#!/usr/bin/env python3
"""
cassette - turn a recorded session into a replayable fixture.

A session written with `ctxprobe serve --cassette` has:
  * per-turn request metadata (message hashes, roles, sizes)
  * per-turn response blocks
  * a content-addressed object store, one file per unique block

`build()` joins them into one dict that replay can reason about, without the
original repo, the original harness, or any network access.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def object_dir_for(session_path: Path) -> Path:
    return session_path.with_suffix(".cassette") / "objects"


def load_objects(directory: Path) -> dict[str, str]:
    objects: dict[str, str] = {}
    if not directory.exists():
        return objects
    for path in directory.glob("*.txt"):
        objects[path.stem] = path.read_text(encoding="utf-8")
    return objects


def build(session_path: Path, object_dir: Path | None = None) -> dict[str, Any]:
    """Join a session log and its object store into a cassette."""
    session_path = Path(session_path).expanduser()
    if not session_path.exists():
        raise SystemExit(f"no session at {session_path}")

    turns: list[dict[str, Any]] = []
    for line in session_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            turns.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not turns:
        raise SystemExit(f"session {session_path.name} is empty")

    for turn in turns:
        if not turn.get("resp"):
            raise SystemExit(
                f"no recorded response for turn {turn.get('turn')} in {session_path.name}. "
                "Re-capture with `ctxprobe serve --cassette`."
            )

    objects = load_objects(object_dir or object_dir_for(session_path))
    if not objects:
        raise SystemExit(
            f"no content objects next to {session_path.name}. "
            "Capture with `ctxprobe serve --cassette` so replay has the bytes."
        )

    first = turns[0]
    system = ""
    for msg in first.get("req", {}).get("messages", []):
        if msg.get("k") == "system":
            system = objects.get(msg.get("h"), "")

    return {
        "n_turns": len(turns),
        "provider": first.get("provider", "anthropic"),
        "model": first.get("model", "unknown"),
        "system": system,
        "tools": (first.get("req", {}).get("tools") or []),
        "objects": objects,
        "turns": [
            {
                "messages": t.get("req", {}).get("messages", []),
                "usage": t.get("usage", {}),
                "resp": t.get("resp", {}),
                "prefix_hash": t.get("req", {}).get("prefix_hash", ""),
            }
            for t in turns
        ],
    }


def restore_block(block: dict[str, Any], objects: dict[str, str]) -> dict[str, Any]:
    """Rebuild an Anthropic-shaped content block from a recorded message."""
    text = objects.get(block.get("h"), "")
    kind = block.get("k")
    if kind == "tool_use":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {"raw": text}
        return {
            "type": "tool_use",
            "id": block.get("id", ""),
            "name": block.get("n", ""),
            "input": payload,
        }
    if kind == "tool_result":
        return {
            "type": "tool_result",
            "tool_use_id": block.get("rid", ""),
            "content": text,
        }
    if kind in ("text", "system", "empty"):
        return {"type": "text", "text": text}
    return {"type": "text", "text": text}


def to_request(
    cassette: dict[str, Any], turn_index: int
) -> dict[str, Any]:
    """Build a provider-shaped request body for one turn."""
    objects = cassette["objects"]
    messages: list[dict[str, Any]] = []

    for block in cassette["turns"][turn_index]["messages"]:
        if block.get("k") == "system":
            continue
        restored = restore_block(block, objects)
        role = block.get("role", "user")
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].append(restored)
        else:
            messages.append({"role": role, "content": [restored]})

    body: dict[str, Any] = {
        "model": cassette["model"],
        "messages": messages,
        "tools": cassette["tools"],
    }
    if cassette["system"]:
        body["system"] = cassette["system"]
    return body


class ToolResultStore:
    """Index of tool calls and the results they produced, for offline replay.

    `lookup` returns (content, match) where match is "exact", "approx" or "miss".
    Approximate matching lets a policy that rewrites a file path still find the
    recorded output, at the cost of a weaker guarantee.
    """

    TARGET_KEYS = ("file_path", "path", "command", "pattern", "url", "query", "cmd")

    def __init__(self, cassette: dict[str, Any]) -> None:
        objects = cassette["objects"]
        self.objects = objects
        self.by_id: dict[str, dict[str, Any]] = {}
        self.results: dict[str, str] = {}
        self.pairs = 0
        self.unpaired = 0
        self.used_tools: set[str] = set()
        self._calls: list[tuple[str, dict[str, Any], str]] = []

        pending: dict[str, tuple[str, dict[str, Any]]] = {}
        paired_ids: set[str] = set()

        for turn in cassette["turns"]:
            for msg in turn["messages"]:
                if msg.get("k") == "tool_use":
                    ident = msg.get("id") or f"noid-{len(pending)}"
                    try:
                        payload = json.loads(objects.get(msg.get("h"), "{}"))
                    except json.JSONDecodeError:
                        payload = {}
                    pending[ident] = (msg.get("n", ""), payload)
                elif msg.get("k") == "tool_result":
                    rid = msg.get("rid")
                    content = objects.get(msg.get("h"), "")
                    if rid in pending:
                        name, payload = pending.pop(rid)
                        self.results[rid] = content
                        if rid not in paired_ids:
                            paired_ids.add(rid)
                            self.pairs += 1
                            self.used_tools.add(name)
                            self._calls.append((name, payload, content))
                    else:
                        self.unpaired += 1

        self.unpaired += len(pending)

    def lookup(self, name: str, tool_input: dict[str, Any]) -> tuple[str | None, str]:
        target = canon(tool_input)
        for call_name, payload, content in self._calls:
            if call_name == name and canon(payload) == target:
                return content, "exact"

        wanted = {k: tool_input.get(k) for k in self.TARGET_KEYS if tool_input.get(k)}
        if wanted:
            for call_name, payload, content in self._calls:
                if call_name != name:
                    continue
                shared = {k: payload.get(k) for k in wanted if payload.get(k) == wanted[k]}
                if shared:
                    return content, "approx"
        return None, "miss"

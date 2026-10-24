#!/usr/bin/env python3
"""
A pretend coding agent, used to demo and test ctxprobe without installing a
real harness.

    python examples/demo_agent.py

It behaves like a three-turn agent session: a small prompt, a big tool result,
then a re-read of the same bytes (the classic redundant read).
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

BASE = os.environ.get("ANTHROPIC_BASE_URL", "http://127.0.0.1:8787")
BIG = Path(__file__).resolve().parent.parent / "tests" / "big_output.txt"

TOOLS = [
    {"name": "Read", "description": "Read a file from disk.", "input_schema": {"type": "object"}},
    {"name": "Edit", "description": "Replace a string in a file.", "input_schema": {"type": "object"}},
    {"name": "Bash", "description": "Run a shell command.", "input_schema": {"type": "object"}},
]


def call(messages: list[dict], label: str) -> None:
    body = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 256,
        "system": [{"type": "text", "text": "You are a coding agent. Today is 2026-10-24."}],
        "tools": TOOLS,
        "messages": messages,
    }
    req = urllib.request.Request(
        f"{BASE}/v1/messages",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-api-key": "demo"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    usage = data.get("usage", {})
    print(
        f"{label:<28} in={usage.get('input_tokens', 0):>6} "
        f"cached={usage.get('cache_read_input_tokens', 0):>6} "
        f"out={usage.get('output_tokens', 0):>4}"
    )


def main() -> None:
    noise = (BIG.read_text(encoding="utf-8") if BIG.exists() else "1 failed, 41 passed\n" * 300)

    history: list[dict] = [
        {"role": "user", "content": "The suite fails on test_parse. Find out why."}
    ]
    call(history, "turn 1: initial")

    history.append({"role": "assistant", "content": [{"type": "text", "text": "Running tests."}]})
    history.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": noise,
                }
            ],
        }
    )
    call(history, "turn 2: big test output")

    history.append({"role": "assistant", "content": [{"type": "text", "text": "Re-reading src."}]})
    history.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t2",
                    "content": noise,
                }
            ],
        }
    )
    call(history, "turn 3: same bytes again")


if __name__ == "__main__":
    main()

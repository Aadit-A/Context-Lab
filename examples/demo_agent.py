#!/usr/bin/env python3
"""
A pretend coding agent, used to demo and test ctxprobe without installing a
real harness.

    python tests/fake_upstream.py 8899 --mode realistic &
    python scripts/wrap.py --cassette --upstream http://127.0.0.1:8899 \
        --session demo -- python examples/demo_agent.py

It deliberately contains the four classic token problems so the audit and the
replay policies have something to find:

  - a volatile timestamp in the system prompt (breaks the prefix every turn)
  - two tools that are defined but never called (schema tax)
  - one large test output (worth truncating)
  - one repeated identical read (redundant data)
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

BASE = os.environ.get("ANTHROPIC_BASE_URL", "http://127.0.0.1:8787")
HERE = Path(__file__).resolve().parent.parent
BIG = HERE / "tests" / "big_output.txt"

TOOLS = [
    {"name": "Read", "description": "Read a file from disk.", "input_schema": {"type": "object"}},
    {"name": "Edit", "description": "Replace a string in a file.", "input_schema": {"type": "object"}},
    {"name": "Bash", "description": "Run a shell command.", "input_schema": {"type": "object"}},
    # defined but never called - schema tax
    {"name": "Grep", "description": "Search file contents with a regex.", "input_schema": {"type": "object"}},
    {"name": "Write", "description": "Write a file to disk.", "input_schema": {"type": "object"}},
]

FILE_CONTENT = "src/billing.py\n" + "\n".join(
    f"def fn_{n}(x): return x + {n}" for n in range(140)
)
TEST_OUTPUT = (BIG.read_text(encoding="utf-8") if BIG.exists() else "1 failed\n" * 200)


def call(messages: list[dict], label: str) -> dict:
    body = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 1024,
        "system": f"You are a coding agent. Current unix time: {os.getpid() + len(messages)}",
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
        return json.loads(resp.read())


def tool_result(uid: str, content: str) -> dict:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": uid, "content": content}],
    }


def main() -> None:
    history: list[dict] = [
        {"role": "user", "content": "The billing suite fails. Find out why and fix it."}
    ]

    # two full cycles of the scripted provider: Read -> Bash -> Edit -> answer
    for cycle in range(2):
        base = len(history)
        r = call(history, f"turn {len(history) + 1}")
        history.append({"role": "assistant", "content": r["content"]})
        history.append(tool_result("tu_1", FILE_CONTENT))          # read the file

        r = call(history, f"turn {len(history) + 1}")
        history.append({"role": "assistant", "content": r["content"]})
        history.append(tool_result("tu_2", TEST_OUTPUT))           # big test output

        r = call(history, f"turn {len(history) + 1}")
        history.append({"role": "assistant", "content": r["content"]})
        history.append(
            tool_result("tu_3", "Edit applied to src/billing.py.")
        )

        r = call(history, f"turn {len(history) + 1}")
        history.append({"role": "assistant", "content": r["content"]})

    print(f"demo session complete: {len(history)} messages across 8 model calls")


if __name__ == "__main__":
    main()

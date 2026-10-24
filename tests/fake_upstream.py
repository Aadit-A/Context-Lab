#!/usr/bin/env python3
"""A tiny stand-in for an LLM provider, used by the test suite.

Modes:
  text       - fixed usage, short text answer (default)
  tool       - fixed usage, tool_use for src/billing.py
  tool_alt   - fixed usage, tool_use for a different file (tests divergence)
  realistic  - usage derived from the request size, optional scripted replies
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

FIXED_USAGE = {
    "input_tokens": 1200,
    "output_tokens": 64,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 800,
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    mode = "text"
    script: list[dict[str, Any]] = []
    cursor = 0
    lock = threading.Lock()

    def log_message(self, *_args):
        pass

    # -- response shaping ------------------------------------------------ #
    def _scripted(self) -> dict[str, Any] | None:
        cls = type(self)
        if not cls.script:
            return None
        with cls.lock:
            entry = cls.script[min(cls.cursor, len(cls.script) - 1)]
            cls.cursor += 1
        return entry

    def _blocks(self, entry: dict[str, Any] | None) -> list[dict[str, Any]]:
        if entry is not None:
            blocks = list(entry.get("blocks") or [])
            return blocks
        if self.mode == "tool":
            return [{"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"path": "src/billing.py"}}]
        if self.mode == "tool_alt":
            return [{"type": "tool_use", "id": "toolu_2", "name": "Read", "input": {"path": "src/other.py"}}]
        return [{"type": "text", "text": "ok"}]

    def _usage(self, body: bytes, out_chars: int) -> dict[str, int]:
        if self.mode == "realistic":
            return {
                "input_tokens": max(1, round(len(body) / 4)),
                "output_tokens": max(1, round(out_chars / 4)),
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            }
        return dict(FIXED_USAGE)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) or b"{}"
        payload = json.loads(raw)

        entry = self._scripted()
        blocks = self._blocks(entry)
        texts = [b for b in blocks if b.get("type") == "text"]
        if entry is not None and entry.get("content"):
            texts = [t for t in entry["content"] if isinstance(t, dict)]
        out_chars = sum(len(b.get("text", "")) for b in texts)

        stop_reason = (entry or {}).get("stop_reason") or (
            "tool_use" if any(b.get("type") == "tool_use" for b in blocks) else "end_turn"
        )

        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            usage = self._usage(raw, out_chars)
            events = [
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_1",
                        "usage": {**usage, "output_tokens": 1},
                        "content": texts,
                    },
                },
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}},
                {"type": "message_delta", "delta": {"stop_reason": stop_reason},
                 "usage": {"output_tokens": usage["output_tokens"] if self.mode == "realistic" else 42}},
            ]
            for ev in events:
                data = f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode()
                self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            return

        response = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": payload.get("model", "claude-sonnet-4"),
            "content": texts + [b for b in blocks if b.get("type") != "text"],
            "stop_reason": stop_reason,
            "usage": self._usage(raw, out_chars),
        }
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start(port: int = 0, mode: str = "text", script: list[dict[str, Any]] | None = None):
    handler = type(
        "BoundHandler",
        (Handler,),
        {"mode": mode, "script": list(script or []), "cursor": 0},
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, httpd.server_address[1]


if __name__ == "__main__":
    mode = sys.argv[2] if len(sys.argv) > 2 else "text"
    srv, port = start(int(sys.argv[1]) if len(sys.argv) > 1 else 8899, mode)
    print(f"fake upstream on http://127.0.0.1:{port} (mode={mode})")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()

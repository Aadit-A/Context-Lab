#!/usr/bin/env python3
"""A tiny stand-in for an LLM provider, used by the test suite."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USAGE = {
    "input_tokens": 1200,
    "output_tokens": 64,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 800,
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")

        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            events = [
                {
                    "type": "message_start",
                    "message": {"id": "msg_1", "usage": {**USAGE, "output_tokens": 1}},
                },
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}},
                {"type": "message_delta", "usage": {"output_tokens": 42}},
            ]
            for ev in events:
                data = f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode()
                self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            return

        body = json.dumps(
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": payload.get("model", "claude-sonnet-4"),
                "content": [{"type": "text", "text": "ok"}],
                "usage": USAGE,
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start(port: int = 0) -> tuple[ThreadingHTTPServer, int]:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, httpd.server_address[1]


if __name__ == "__main__":
    srv, port = start(int(sys.argv[1]) if len(sys.argv) > 1 else 8899)
    print(f"fake upstream on http://127.0.0.1:{port}")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()

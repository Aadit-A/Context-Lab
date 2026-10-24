#!/usr/bin/env python3
"""
ctxprobe - a local, stdlib-only proxy that records what an AI coding agent
actually sends to an LLM provider, and what it costs.

Point any harness at it:

    python scripts/ctxprobe.py serve --port 8787
    ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude
    OPENAI_BASE_URL=http://127.0.0.1:8787 codex

No dependencies. No network egress except to the provider you configure.
Records message hashes and short previews, never full payloads, unless
--record-raw is passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

VERSION = "0.1.0"
SCHEMA_VERSION = 1

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "accept-encoding",
    "content-encoding",
}

DATE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?\b"
)
SECRET_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_\-]{12,}|sk-ant-[A-Za-z0-9_\-]{12,}|ghp_[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9._\-]{16,})"
)

DEFAULT_UPSTREAM = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(text: str, n: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:n]


def mask_dates(text: str) -> str:
    return DATE_RE.sub("<DATE>", text)


def redact(text: str) -> str:
    return SECRET_RE.sub("<REDACTED>", text)


def preview(text: str, n: int = 140) -> str:
    text = redact(text).replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:n] + ("..." if len(text) > n else "")


def est_tokens(text: str) -> int:
    """Rough fallback only. Provider-reported usage always wins."""
    return max(1, round(len(text) / 4))


def normalise_content(content: Any) -> list[dict[str, Any]]:
    """Normalise Anthropic and OpenAI message content into our block model."""
    blocks: list[dict[str, Any]] = []
    if content is None:
        return blocks
    if isinstance(content, str):
        return [{"kind": "text", "text": content}]
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        return [{"kind": "other", "text": canon(content)}]

    for item in content:
        if isinstance(item, str):
            blocks.append({"kind": "text", "text": item})
            continue
        if not isinstance(item, dict):
            blocks.append({"kind": "other", "text": canon(item)})
            continue
        btype = item.get("type") or ("text" if "text" in item else "other")
        if btype == "text":
            blocks.append({"kind": "text", "text": str(item.get("text", ""))})
        elif btype in ("tool_use", "function_call"):
            blocks.append(
                {
                    "kind": "tool_use",
                    "tool": item.get("name") or item.get("function", {}).get("name"),
                    "text": canon(item.get("input") or item.get("arguments") or {}),
                }
            )
        elif btype in ("tool_result", "function_result"):
            inner = item.get("content", item.get("output", ""))
            if not isinstance(inner, str):
                inner = canon(inner)
            blocks.append(
                {"kind": "tool_result", "tool": item.get("name"), "text": inner}
            )
        elif btype in ("image", "image_url", "input_image"):
            blocks.append({"kind": "image", "text": "<image>"})
        else:
            blocks.append({"kind": "other", "text": canon(item)})
    return blocks


def summarise_messages(payload: dict[str, Any], provider: str) -> tuple[list[dict], int]:
    """Return compact per-message records plus the system-prompt char count."""
    out: list[dict[str, Any]] = []
    system_chars = 0

    system = payload.get("system")
    if system:
        text = system if isinstance(system, str) else canon(system)
        system_chars = len(text)
        out.append(
            {
                "role": "system",
                "h": sha(mask_dates(text)),
                "c": len(text),
                "t": est_tokens(text),
                "k": "system",
                "p": preview(text, 80),
            }
        )

    for msg in payload.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "?")
        blocks = normalise_content(msg.get("content"))

        # OpenAI style tool calls live outside content
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            blocks.append(
                {
                    "kind": "tool_use",
                    "tool": fn.get("name"),
                    "text": canon(fn.get("arguments", "")),
                }
            )

        if not blocks:
            out.append(
                {"role": role, "h": sha(canon(msg)), "c": 2, "t": 1, "k": "empty", "p": ""}
            )
            continue

        for b in blocks:
            text = b["text"]
            out.append(
                {
                    "role": role,
                    "h": sha(text),
                    "c": len(text),
                    "t": est_tokens(text),
                    "k": b["kind"],
                    "n": b.get("tool"),
                    "p": preview(text, 100),
                }
            )
    return out, system_chars


def guess_provider(path: str, payload: dict[str, Any]) -> str:
    if "/messages" in path:
        return "anthropic"
    if payload.get("system") is not None and payload.get("messages") is not None:
        return "anthropic"
    return "openai"


def extract_usage(obj: dict[str, Any], provider: str) -> dict[str, Any]:
    """Read provider usage blocks into a common shape."""
    u = obj.get("usage") or obj.get("message", {}).get("usage") or {}
    if not isinstance(u, dict) or not u:
        return {}
    if provider == "anthropic":
        inp = int(u.get("input_tokens") or 0)
        out = int(u.get("output_tokens") or 0)
        cr = int(u.get("cache_read_input_tokens") or 0)
        cc = int(u.get("cache_creation_input_tokens") or 0)
    else:
        inp = int(u.get("prompt_tokens") or 0)
        out = int(u.get("completion_tokens") or 0)
        details = u.get("prompt_tokens_details") or {}
        cr = int(details.get("cached_tokens") or u.get("cached_tokens") or 0)
        cc = 0
    if not (inp or out or cr or cc):
        return {}
    return {
        "input": inp,
        "output": out,
        "cache_read": cr,
        "cache_creation": cc,
        "estimated": False,
    }


class UsageSink:
    """Accumulates usage from a full JSON body or from an SSE stream."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.input = 0
        self.output = 0
        self.cache_read = 0
        self.cache_creation = 0
        self.seen = False
        self._buf = ""

    def _absorb(self, obj: dict[str, Any]) -> None:
        u = extract_usage(obj, self.provider)
        if not u:
            return
        self.seen = True
        self.input = max(self.input, u["input"])
        self.output = max(self.output, u["output"])
        self.cache_read = max(self.cache_read, u["cache_read"])
        self.cache_creation = max(self.cache_creation, u["cache_creation"])

    def feed_json(self, body: bytes) -> None:
        try:
            self._absorb(json.loads(body.decode("utf-8", "replace")))
        except Exception:
            pass

    def feed_stream_bytes(self, chunk: bytes) -> None:
        self._buf += chunk.decode("utf-8", "replace")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                self._absorb(json.loads(data))
            except Exception:
                continue

    def result(self) -> dict[str, Any]:
        if not self.seen:
            return {
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_creation": 0,
                "estimated": True,
            }
        return {
            "input": self.input,
            "output": self.output,
            "cache_read": self.cache_read,
            "cache_creation": self.cache_creation,
            "estimated": False,
        }


class Recorder:
    def __init__(self, session_dir: Path, session_id: str, record_raw: bool) -> None:
        self.dir = session_dir
        self.session_id = session_id
        self.record_raw = record_raw
        self.lock = threading.Lock()
        self.turn = 0
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{session_id}.jsonl"
        self.raw_dir = self.dir / f"{session_id}.raw"
        if record_raw:
            self.raw_dir.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any], raw_body: bytes | None) -> None:
        with self.lock:
            self.turn += 1
            record["turn"] = self.turn
            record["session"] = self.session_id
            record["v"] = SCHEMA_VERSION
            if raw_body is not None:
                (self.raw_dir / f"{self.turn:04d}.json").write_bytes(raw_body)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #
# proxy
# --------------------------------------------------------------------------- #
class CtxProbeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"ctxprobe/{VERSION}"

    # injected by server
    upstream_base: str = ""
    recorder: Recorder | None = None
    verbose: bool = False

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D102
        if self.verbose:
            sys.stderr.write("[ctxprobe] " + (fmt % args) + "\n")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in ("/health", "/ctxprobe/health"):
            body = json.dumps({"ok": True, "version": VERSION}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._forward(b"")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""
        self._forward(body)

    # -- core ------------------------------------------------------------- #
    def _forward(self, body: bytes) -> None:
        started = time.time()
        payload: dict[str, Any] = {}
        if body:
            try:
                payload = json.loads(body.decode("utf-8", "replace"))
            except Exception:
                payload = {}

        provider = guess_provider(self.path, payload)
        upstream = (self.upstream_base or DEFAULT_UPSTREAM[provider]).rstrip("/")
        url = upstream + self.path

        req = urllib.request.Request(url, data=body if body else None, method=self.command)
        for key, value in self.headers.items():
            if key.lower() in HOP_BY_HOP:
                continue
            req.add_header(key, value)
        req.add_header("Accept-Encoding", "identity")

        stream = bool(payload.get("stream")) or "text/event-stream" in (
            self.headers.get("accept") or ""
        )

        try:
            resp = urllib.request.urlopen(req, timeout=900)
            status = resp.status
            headers = resp.headers
            stream = stream or "text/event-stream" in (headers.get("Content-Type") or "")
        except urllib.error.HTTPError as exc:
            resp = exc
            status = exc.code
            headers = exc.headers
        except Exception as exc:  # connection failure
            msg = json.dumps(
                {"type": "error", "error": {"type": "ctxprobe_upstream", "message": str(exc)}}
            ).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return

        sink = UsageSink(provider)

        if stream:
            self.send_response(status)
            for key, value in headers.items():
                if key.lower() in HOP_BY_HOP:
                    continue
                self.send_header(key, value)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                while True:
                    chunk = resp.read(2048)
                    if not chunk:
                        break
                    sink.feed_stream_bytes(chunk)
                    self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            raw = resp.read()
            sink.feed_json(raw)
            self.send_response(status)
            for key, value in headers.items():
                if key.lower() in HOP_BY_HOP:
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        if self.recorder is not None and body:
            self._record(payload, provider, sink, status, started, body, stream)

    def _record(
        self,
        payload: dict[str, Any],
        provider: str,
        sink: UsageSink,
        status: int,
        started: float,
        raw: bytes,
        stream: bool,
    ) -> None:
        try:
            messages, system_chars = summarise_messages(payload, provider)
            tools = payload.get("tools") or []
            tools_json = mask_dates(canon(tools)) if tools else ""
            system = payload.get("system")
            system_text = system if isinstance(system, str) else canon(system or "")
            prefix_hash = sha(
                mask_dates(canon({"system": system_text, "tools": tools})), 12
            )

            usage = sink.result()
            if usage["estimated"]:
                input_chars = system_chars + sum(m["c"] for m in messages)
                usage["input"] = est_tokens("x" * input_chars)
                usage["output"] = 0

            record = {
                "ts": round(started, 3),
                "provider": provider,
                "model": payload.get("model", "unknown"),
                "path": self.path,
                "stream": stream,
                "status": status,
                "latency_ms": int((time.time() - started) * 1000),
                "usage": usage,
                "req": {
                    "n_messages": len(messages),
                    "system_chars": system_chars,
                    "schema_chars": len(tools_json),
                    "schema_tokens": est_tokens(tools_json) if tools_json else 0,
                    "n_tools": len(tools),
                    "tool_names": [t.get("name") for t in tools if isinstance(t, dict)][:40],
                    "prefix_hash": prefix_hash,
                    "messages": messages,
                },
            }
            self.recorder.write(record, raw if self.recorder.record_raw else None)
        except Exception as exc:  # never break the user's session
            sys.stderr.write(f"[ctxprobe] record error: {exc}\n")


def serve(args: argparse.Namespace) -> None:
    session_dir = Path(args.session_dir).expanduser()
    session_id = args.session or time.strftime("%Y%m%d-%H%M%S")

    provider = args.provider
    if args.upstream:
        upstream = args.upstream
    elif provider in DEFAULT_UPSTREAM:
        upstream = DEFAULT_UPSTREAM[provider]
    else:
        upstream = DEFAULT_UPSTREAM["anthropic"]

    recorder = Recorder(session_dir, session_id, args.record_raw)
    CtxProbeHandler.upstream_base = upstream
    CtxProbeHandler.recorder = recorder
    CtxProbeHandler.verbose = args.verbose

    httpd = ThreadingHTTPServer((args.host, args.port), CtxProbeHandler)
    httpd.daemon_threads = True

    base = f"http://{args.host}:{args.port}"
    print(f"ctxprobe {VERSION} listening on {base}")
    print(f"  upstream : {upstream}")
    print(f"  session  : {session_id}")
    print(f"  log      : {recorder.path}")
    print()
    print("  bash/zsh :")
    print(f"    export ANTHROPIC_BASE_URL={base}")
    print(f"    export OPENAI_BASE_URL={base}")
    print("  powershell:")
    print(f'    $env:ANTHROPIC_BASE_URL="{base}"')
    print(f'    $env:OPENAI_BASE_URL="{base}"')
    print()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nctxprobe stopped")
    finally:
        httpd.server_close()
        print(f"session written to {recorder.path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ctxprobe",
        description="Record and attribute token usage for any AI coding agent.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="start the recording proxy")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument(
        "--provider",
        default="auto",
        choices=["auto", "anthropic", "openai"],
        help="which upstream to use when --upstream is not set",
    )
    s.add_argument("--upstream", default="", help="full upstream base URL")
    s.add_argument("--session", default="", help="session id (default: timestamp)")
    s.add_argument(
        "--session-dir",
        default=os.environ.get("CTXPROBE_HOME", str(Path.home() / ".ctxprobe" / "sessions")),
    )
    s.add_argument("--record-raw", action="store_true", help="store full request bodies")
    s.add_argument("--verbose", action="store_true")
    s.set_defaults(func=serve)

    v = sub.add_parser("version", help="print version")
    v.set_defaults(func=lambda _a: print(VERSION) or 0)

    args = parser.parse_args(argv)
    if args.cmd == "serve" and args.provider == "auto":
        args.provider = "anthropic"
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())

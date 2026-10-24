#!/usr/bin/env python3
"""Regressions found by the 2026-10-24 self-audit (SELF-AUDIT.md Phase 3)."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import analyze  # noqa: E402
import ctxprobe  # noqa: E402
import replay  # noqa: E402
import report  # noqa: E402


# --------------------------------------------------------------------------- #
# 1. a second ctxprobe must refuse an occupied port (silent on Windows before)
# --------------------------------------------------------------------------- #
class OccupiedPortTest(unittest.TestCase):
    def test_second_listener_on_same_port_is_refused(self):
        blocker = ThreadingHTTPServer(("127.0.0.1", 0), lambda *_: None)  # noqa: N802
        port = blocker.server_address[1]
        try:
            with self.assertRaises(OSError):
                ctxprobe.ProbeServer(("127.0.0.1", port), ctxprobe.CtxProbeHandler)
        finally:
            blocker.server_close()

    def test_unwritable_session_dir_fails_with_a_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            not_a_dir = Path(tmp) / "file.jsonl"
            not_a_dir.write_text("x", encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "ctxprobe.py"),
                    "serve",
                    "--port",
                    "0",
                    "--session-dir",
                    str(not_a_dir),
                    "--session",
                    "x",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--session-dir", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


# --------------------------------------------------------------------------- #
# 2. streamed multibyte UTF-8 must survive a chunk boundary
# --------------------------------------------------------------------------- #
class StreamDecodingTest(unittest.TestCase):
    def test_multibyte_char_split_across_chunks_is_intact(self):
        text = "\u4e16\u754c\U0001F680 ok"
        event = (
            "data: "
            + json.dumps(
                {
                    "type": "message_start",
                    "message": {
                        "content": [{"type": "text", "text": text}],
                        "usage": {"input_tokens": 42, "output_tokens": 1},
                    },
                },
                ensure_ascii=False,
            )
            + "\n\n"
        ).encode("utf-8")
        cut = event.index(bytes([0xE4])) + 1  # inside the first CJK character
        self.assertNotEqual(cut, 0)

        split = ctxprobe.UsageSink("anthropic")
        split.feed_stream_bytes(event[:cut])
        split.feed_stream_bytes(event[cut:])
        whole = ctxprobe.UsageSink("anthropic")
        whole.feed_stream_bytes(event)

        self.assertEqual(split.response()["blocks"], whole.response()["blocks"])
        self.assertEqual(split.response()["blocks"][0]["text"], text)


# --------------------------------------------------------------------------- #
# 3. OpenAI-shaped bodies: tool results and the system prefix
# --------------------------------------------------------------------------- #
OPENAI_BODY = {
    "model": "gpt-5",
    "messages": [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "fix the failing test"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "run_tests", "arguments": "{\"cmd\": \"pytest -q\"}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "1 failed"},
    ],
}


class OpenAIShapeTest(unittest.TestCase):
    def test_tool_message_is_recorded_as_tool_result(self):
        records, _, _ = ctxprobe.summarise_messages(OPENAI_BODY, "openai")
        tool_results = [r for r in records if r["k"] == "tool_result"]
        self.assertEqual(len(tool_results), 1)
        self.assertEqual(tool_results[0]["rid"], "call_1")
        self.assertEqual(tool_results[0]["role"], "tool")
        self.assertIn("1 failed", tool_results[0]["p"])

    def test_tool_message_attributed_to_tool_result_bucket(self):
        big_result = "1 failed, 41 passed\n" * 3000  # ~9k tokens, visible in USD
        body = json.loads(json.dumps(OPENAI_BODY))
        body["messages"][3]["content"] = big_result
        turns = []
        for i, n_messages in enumerate((3, 4)):
            msgs, _, _ = ctxprobe.summarise_messages(
                {"messages": body["messages"][:n_messages]}, "openai"
            )
            turns.append(
                {
                    "v": 2,
                    "session": "oa",
                    "turn": i + 1,
                    "ts": float(i),
                    "provider": "openai",
                    "model": "gpt-5",
                    "path": "/v1/chat/completions",
                    "stream": False,
                    "status": 200,
                    "latency_ms": 1,
                    "usage": {"input": 900, "output": 5, "cache_read": 0, "cache_creation": 0, "estimated": False},
                    "req": {
                        "n_messages": len(msgs),
                        "system_chars": 0,
                        "schema_chars": 0,
                        "schema_tokens": 0,
                        "n_tools": 0,
                        "tool_names": [],
                        "prefix_hash": "ph",
                        "messages": msgs,
                    },
                }
            )
        result = analyze.analyse(turns, analyze.DEFAULT_PRICES)
        self.assertGreater(result["buckets_usd"]["tool_results"], 0)

    def test_raw_replay_blocks_see_openai_tool_results(self):
        blocks = replay.body_to_blocks(OPENAI_BODY)
        kinds = [b["kind"] for b in blocks]
        self.assertIn("tool_result", kinds)
        tr = next(b for b in blocks if b["kind"] == "tool_result")
        self.assertEqual(tr["rid"], "call_1")

    def test_volatile_openai_system_message_breaks_prefix_hash(self):
        a = json.loads(json.dumps(OPENAI_BODY))
        b = json.loads(json.dumps(OPENAI_BODY))
        b["messages"][0]["content"] = "You are a sailing instructor."
        rec_a, _, sys_a = ctxprobe.summarise_messages(a, "openai")
        rec_b, _, sys_b = ctxprobe.summarise_messages(b, "openai")
        ha = ctxprobe.prefix_hash_for(sys_a, a.get("tools"))
        hb = ctxprobe.prefix_hash_for(sys_b, b.get("tools"))
        self.assertNotEqual(ha, hb)

    def test_stable_openai_system_message_keeps_prefix_hash(self):
        body2 = json.loads(json.dumps(OPENAI_BODY))
        body2["messages"].append({"role": "user", "content": "and now?"})
        _, _, sys_a = ctxprobe.summarise_messages(OPENAI_BODY, "openai")
        _, _, sys_b = ctxprobe.summarise_messages(body2, "openai")
        self.assertEqual(
            ctxprobe.prefix_hash_for(sys_a, OPENAI_BODY.get("tools")),
            ctxprobe.prefix_hash_for(sys_b, body2.get("tools")),
        )


# --------------------------------------------------------------------------- #
# 4. terminal output must be ASCII (Windows consoles are not UTF-8 by default)
# --------------------------------------------------------------------------- #
ASCII_RESULT = {
    "session": "ascii",
    "provider": "anthropic",
    "model": "claude-sonnet-4-5",
    "turns": 2,
    "duration_s": 1.0,
    "cost_usd": 1.0,
    "buckets_usd": {"carried_history": 0.5, "schemas": 0.1, "tool_results": 0.2, "new_text": 0.1, "output": 0.1},
    "buckets_pct": {"carried_history": 50, "schemas": 10, "tool_results": 20, "new_text": 10, "output": 10},
    "metrics": {"chr": 0.5, "psi": 0.9, "rdr": 0.1, "st": 0.1, "ct": 0.0, "tokens_per_turn": 1000},
    "prefix_breaks": [],
    "compaction_turns": [],
    "redundant_reads": [],
    "offenders": [
        {"turn": 1, "tokens": 4000, "remaining": 1, "preview": "pytest \u4e16\u754c \U0001F680 failed", "tool": "Bash"}
    ],
    "recommendations": [],
    "price_used": {},
    "tokens": {
        "input": 1, "output": 1, "cache_read": 0, "cache_creation": 0,
        "schema": 0, "tool_result": 0, "redundant": 0,
    },
}


class AsciiRenderTest(unittest.TestCase):
    def test_terminal_render_is_ascii_only(self):
        text = analyze.render_terminal(ASCII_RESULT)
        text.encode("ascii")  # must not raise

    def test_terminal_render_survives_cp1252_stream(self):
        buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        buf.write(analyze.render_terminal(ASCII_RESULT))
        buf.flush()  # must not raise


# --------------------------------------------------------------------------- #
# 5. `install --target all` must stay inside --dest
# --------------------------------------------------------------------------- #
class InstallScopeTest(unittest.TestCase):
    def test_all_targets_stay_inside_dest(self):
        import install  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = install.main(["--target", "all", "--dest", tmp, "--dry-run"])
            self.assertEqual(rc, 0)
            self.assertNotIn(str(Path.home() / ".claude"), out.getvalue())
            self.assertIn(tmp, out.getvalue())


# --------------------------------------------------------------------------- #
# 6. compact-at without --window must say it did nothing
# --------------------------------------------------------------------------- #
class InertCompactionNoteTest(unittest.TestCase):
    def test_compare_explains_inert_compaction(self):
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "replay.py"),
                "compare",
                str(ROOT / "examples" / "demo.cassette.json"),
                "--policies",
                "baseline,compact-at-40",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--window", proc.stdout)

    def test_compare_with_window_needs_no_note(self):
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "replay.py"),
                "compare",
                str(ROOT / "examples" / "demo.cassette.json"),
                "--policies",
                "baseline,compact-at-40",
                "--window",
                "3000",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0)
        note = [l for l in proc.stdout.splitlines() if "did not compact" in l]
        self.assertEqual(note, [])


# --------------------------------------------------------------------------- #
# 7. the HTML report must state the real generator version
# --------------------------------------------------------------------------- #
class ReportVersionTest(unittest.TestCase):
    def test_html_footer_carries_current_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "r.html"
            report.write_html(ASCII_RESULT, out)
            doc = out.read_text(encoding="utf-8")
        self.assertIn(f"v{ctxprobe.VERSION}", doc)
        self.assertIn(f"schema v{ctxprobe.SCHEMA_VERSION}", doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)

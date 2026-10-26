#!/usr/bin/env python3
"""
wrap - run any agent CLI under ctxprobe and audit it automatically.

    python scripts/wrap.py -- claude
    python scripts/wrap.py --provider openai -- codex
    python scripts/wrap.py --upstream https://api.deepseek.com -- my-agent

Any harness that respects ANTHROPIC_BASE_URL / OPENAI_BASE_URL works with no
changes. For harnesses that take an explicit --base-url flag, pass it yourself
and point it at the printed URL (use --no-run to just start the proxy).
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

# Make sibling scripts importable no matter how this file was launched.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_ready(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Run an agent command under ctxprobe, then report.",
        epilog="Everything after -- is the command to run.",
    )
    ap.add_argument("--provider", default="anthropic", choices=["anthropic", "openai"])
    ap.add_argument("--upstream", default="", help="override the provider base URL")
    ap.add_argument("--session", default="")
    ap.add_argument("--home", default=str(Path.home() / ".ctxprobe" / "sessions"))
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--html", default="", help="write an HTML report here")
    ap.add_argument("--cassette", action="store_true", help="record responses + objects for replay")
    ap.add_argument(
        "--policy",
        default="",
        help=(
            "opt-in live request policy: cache-stable, dedupe-reads, "
            "schema-gating[:Tool,...], truncate:N, or auto (the policy "
            "persisted by autotune.py enable)"
        ),
    )
    ap.add_argument("--no-analyze", action="store_true")
    ap.add_argument("--no-run", action="store_true", help="start the proxy and wait")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)

    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd and not args.no_run:
        ap.error("no command given. Use: wrap.py -- <your agent command>")
    if args.policy:
        from live_policy import make

        try:
            make(args.policy)
        except ValueError as exc:
            print(f"invalid --policy: {exc}", file=sys.stderr)
            return 2

    port = args.port or free_port()
    session = args.session or time.strftime("%Y%m%d-%H%M%S")
    base = f"http://127.0.0.1:{port}"

    proxy_cmd = [
        PY,
        str(HERE / "ctxprobe.py"),
        "serve",
        "--port",
        str(port),
        "--session",
        session,
        "--session-dir",
        args.home,
        "--provider",
        args.provider,
    ]
    if args.upstream:
        proxy_cmd += ["--upstream", args.upstream]
    if args.cassette:
        proxy_cmd += ["--cassette"]
    if args.policy:
        proxy_cmd += ["--policy", args.policy]

    proxy = subprocess.Popen(proxy_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_ready(port):
            print("ctxprobe failed to start", file=sys.stderr)
            return 2

        env = dict(os.environ)
        env.update(
            {
                "ANTHROPIC_BASE_URL": base,
                "OPENAI_BASE_URL": base,
                "OPENAI_API_BASE": base,
                "CTXPROBE_SESSION": session,
                "CTXPROBE_HOME": args.home,
            }
        )

        if args.no_run:
            print(f"ctxprobe listening on {base} (session {session})")
            print("Press Ctrl+C to stop.")
            try:
                while proxy.poll() is None:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
            return 0

        policy_note = f" policy={args.policy}" if args.policy else ""
        print(f"[ctxprobe] session {session} -> {base}{policy_note}", file=sys.stderr)
        try:
            code = subprocess.call(cmd, env=env)
        except FileNotFoundError:
            print(f"command not found: {cmd[0]}", file=sys.stderr)
            return 127
    finally:
        proxy.terminate()
        try:
            proxy.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proxy.kill()

    session_file = Path(args.home).expanduser() / f"{session}.jsonl"

    if args.no_analyze:
        if args.cassette and session_file.exists():
            print(
                f"\ncassette objects: {Path(args.home).expanduser() / (session + '.cassette')}\n"
                f"build a cassette: python scripts/replay.py build {session_file}"
            )
        return code
    if not session_file.exists():
        print(
            "\nno requests were recorded. Check that the harness honours the base-URL "
            "environment variable, or pass --upstream explicitly.",
            file=sys.stderr,
        )
        return code

    print()
    import analyze as analyze_mod  # noqa: PLC0415  (sibling script)

    extra = ["--html", args.html] if args.html else []
    analyze_mod.main([str(session_file), *extra])
    if args.cassette:
        print(
            f"\nnext: python scripts/replay.py build {session_file} && "
            f"python scripts/replay.py compare {session_file.with_suffix('.cassette.json')}"
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())

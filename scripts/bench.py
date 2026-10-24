#!/usr/bin/env python3
"""
bench - record tokens-to-green for a task, so a change can be proved rather
than believed.

    python scripts/bench.py --name fix-imports --test "pytest -q" -- claude "fix the tests"

Appends a row to bench.csv and prints the delta against the previous run with
the same --name.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from wrap import free_port, wait_ready  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Measure tokens-to-green for one task.")
    ap.add_argument("--name", required=True, help="task id, reused across runs")
    ap.add_argument("--test", required=True, help="command that proves the task is done")
    ap.add_argument("--provider", default="anthropic", choices=["anthropic", "openai"])
    ap.add_argument("--upstream", default="")
    ap.add_argument("--home", default=str(Path.home() / ".ctxprobe" / "sessions"))
    ap.add_argument("--csv", default="bench.csv")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)

    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd:
        ap.error("no agent command given. Use: bench.py --test '...' -- <command>")

    port = free_port()
    session = f"bench-{args.name}-{time.strftime('%Y%m%d-%H%M%S')}"
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

    base = f"http://127.0.0.1:{port}"
    started = time.time()
    proxy = subprocess.Popen(proxy_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_ready(port):
            print("ctxprobe failed to start", file=sys.stderr)
            return 2
        env = dict(os.environ)
        env.update({"ANTHROPIC_BASE_URL": base, "OPENAI_BASE_URL": base, "OPENAI_API_BASE": base})
        subprocess.call(cmd, env=env)
    finally:
        proxy.terminate()
        try:
            proxy.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proxy.kill()

    solved = subprocess.call(args.test, shell=True) == 0
    duration = round(time.time() - started, 1)

    session_file = Path(args.home).expanduser() / f"{session}.jsonl"
    stats = {"turns": 0, "input": 0, "output": 0, "cost": 0.0, "model": "unknown"}
    if session_file.exists():
        import analyze as analyze_mod

        result = analyze_mod.analyse(analyze_mod.load_session(session_file), analyze_mod.DEFAULT_PRICES)
        stats = {
            "turns": result["turns"],
            "input": result["tokens"]["input"],
            "output": result["tokens"]["output"],
            "cost": result["cost_usd"],
            "model": result["model"],
        }

    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "name": args.name,
        "model": stats["model"],
        "turns": stats["turns"],
        "input_tokens": stats["input"],
        "output_tokens": stats["output"],
        "cost_usd": round(stats["cost"], 4),
        "solved": int(solved),
        "duration_s": duration,
    }

    path = Path(args.csv)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(row))
        if new_file:
            writer.writeheader()
        writer.writerow(row)

    prev = previous_run(path, args.name)
    print()
    print(f"task      {args.name}")
    print(f"model     {row['model']}")
    print(f"solved    {'yes' if solved else 'NO'}")
    print(f"turns     {row['turns']}")
    print(f"tokens    {row['input']:,} in / {row['output']:,} out")
    print(f"cost      ${row['cost_usd']:.2f}")
    if prev:
        dc = row["cost_usd"] - float(prev["cost_usd"])
        dt = row["turns"] - int(prev["turns"])
        sign = "+" if dc >= 0 else ""
        print(
            f"vs. last  {sign}${dc:.2f} ({sign}{dc / max(float(prev['cost_usd']), 0.01) * 100:.0f}%), "
            f"{dt:+d} turns"
        )
        if int(prev["solved"]) and not solved:
            print("warning   previous run solved this task; this one did not. Not a win.")
    print(f"logged to {path}")
    return 0


def previous_run(path: Path, name: str) -> dict[str, str] | None:
    if not path.exists():
        return None
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    matches = [r for r in rows if r["name"] == name]
    return matches[-2] if len(matches) >= 2 else None


if __name__ == "__main__":
    raise SystemExit(main())

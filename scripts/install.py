#!/usr/bin/env python3
"""
install - put Context Lab where a given harness will find it.

    python scripts/install.py --target claude          # ~/.claude/skills/context-lab
    python scripts/install.py --target all --dest .   # project-local shims
    python scripts/install.py --target agents --dest . --dry-run

Shims are inserted between markers, so re-running is safe and never
clobbers hand-written instructions.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # skill root
START = "<!-- context-lab:start -->"
END = "<!-- context-lab:end -->"

SHIM = """{start}
## Context Lab - token efficiency

When the user asks why an agent session is expensive, why they hit token limits,
or how to spend less per task, use the Context Lab skill.

Quick procedure:
1. `python scripts/wrap.py -- <agent command>` to record a real session.
2. `python scripts/analyze.py --latest` for the cost breakdown and ranked fixes.
3. Apply exactly one matching live policy, if request context is the cause:
   `python scripts/wrap.py --policy <policy> -- <agent command>`.
4. Re-run the *same* task and compare tokens-to-green with
   `python scripts/bench.py --name <task> --test "<test cmd>" --policy <policy> -- <agent command>`.

Rules: never send session data off the machine; never change the user's code
without asking; live policies are opt-in and responses are not rewritten; report
measured deltas, never estimated ones, as results.
Full details: SKILL.md in this skill folder.
{end}
"""

CURSOR_RULE = """---
description: Diagnose and reduce agent token usage with Context Lab
globs:
alwaysApply: false
---

""" + SHIM


def block(text: str) -> str:
    return text.format(start=START, end=END)


def upsert(path: Path, text: str, dry: bool) -> str:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    payload = block(text)
    if START in existing and END in existing:
        head, rest = existing.split(START, 1)
        _, tail = rest.split(END, 1)
        new = head + payload + tail
        action = "updated"
    else:
        sep = "" if not existing or existing.endswith("\n") else "\n"
        new = existing + sep + ("\n" if existing else "") + payload + "\n"
        action = "appended"
    if not dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
    return action


def copy_skill(dest: Path, dry: bool) -> None:
    if dest == HERE:
        raise SystemExit("refusing to install the skill over its source directory")
    if dest.exists():
        shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
    if not dry:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Stage beside the source first. Otherwise `--dest .` causes copytree
        # to discover the destination it is creating and recurse into itself.
        with tempfile.TemporaryDirectory(prefix="context-lab-install-", dir=str(HERE.parent)) as tmp:
            staged = Path(tmp) / "context-lab"
            shutil.copytree(
                HERE,
                staged,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", ".git", ".audit", ".ctxprobe", "bench.csv"
                ),
            )
            shutil.move(str(staged), str(dest))


def copy_subskills(skills_root: Path, dry: bool) -> None:
    """Install each skills/<name> sub-skill as context-lab-<name> beside the main skill."""
    src_root = HERE / "skills"
    if not src_root.is_dir():
        return
    for src in sorted(p for p in src_root.iterdir() if p.is_dir()):
        out = skills_root / f"context-lab-{src.name}"
        if not dry:
            if out.exists():
                shutil.rmtree(out) if out.is_dir() else out.unlink()
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, out)
        print(f"sub-skill         -> {out}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Install Context Lab into a harness.")
    ap.add_argument(
        "--target",
        default="all",
        choices=["all", "claude", "claude-project", "agents", "cursor", "cline", "gemini", "dir"],
    )
    ap.add_argument("--dest", default=".", help="project root for shims")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    dest = Path(args.dest).expanduser().resolve()
    # `all` stays inside --dest; installing to the user's home (~/.claude) is
    # an explicit `--target claude` decision, never a side effect.
    targets = (
        ["claude-project", "agents", "cursor", "cline", "gemini"]
        if args.target == "all"
        else [args.target]
    )

    for target in targets:
        if target == "claude":
            out = Path.home() / ".claude" / "skills" / "context-lab"
            copy_skill(out, args.dry_run)
            print(f"claude (user)     -> {out}")
            copy_subskills(out.parent, args.dry_run)
        elif target == "claude-project":
            out = dest / ".claude" / "skills" / "context-lab"
            copy_skill(out, args.dry_run)
            print(f"claude (project)  -> {out}")
            copy_subskills(out.parent, args.dry_run)
        elif target == "dir":
            out = dest / "context-lab"
            copy_skill(out, args.dry_run)
            print(f"skill folder      -> {out}")
        elif target == "agents":
            p = dest / "AGENTS.md"
            print(f"AGENTS.md         -> {upsert(p, SHIM, args.dry_run)} {p}")
        elif target == "cursor":
            p = dest / ".cursor" / "rules" / "context-lab.mdc"
            if not args.dry_run:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(block(CURSOR_RULE), encoding="utf-8")
            print(f"cursor rule       -> {p}")
        elif target == "cline":
            p = dest / ".clinerules" / "context-lab.md"
            if not args.dry_run:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(block(SHIM), encoding="utf-8")
            print(f"cline rule        -> {p}")
        elif target == "gemini":
            p = dest / "GEMINI.md"
            print(f"GEMINI.md         -> {upsert(p, SHIM, args.dry_run)} {p}")

    if args.dry_run:
        print("\n(dry run - nothing written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

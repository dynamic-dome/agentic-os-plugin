#!/usr/bin/env python3
"""Nightly headless consolidation over all projects (T-028 E, the V4 trigger).

    python nightly_consolidate.py [--root ~ ...] [--max-depth 4] [--max-age-days 14]
        [--min-age-minutes 120] [--central-dir ~/AI] [--home ~] [--deny ~/ich ...]
        [--report ~/AI/.agent-memory/metrics/nightly-consolidation.jsonl]
        [--kill-switch ~/AI/.agent-memory/nightly-consolidation.off] [--dry-run]

Finds every project with un-wrapped work (`.agent-memory/working/dirty-*.json`, dirty, not just
wrap-up's own tail writes), newest dirty file younger than --max-age-days, and runs
`wrapup_core.py apply --headless` for each - oldest activity first, so the most recently active
project ends on top of the central handoff (cap 5). One JSONL report line per run.
Kill switch: if the --kill-switch file exists nothing runs (report line `killed`).
Older dirty sessions stay untouched (they keep showing as RECOVERY; bulk-consolidating months-old
work would flood the handoff and the wiki).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from wrapup_core import _is_tail  # noqa: E402

SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "AppData", "_archived", "worktrees",
             ".cache", "site-packages", "dist", "build", ".next", ".pytest_cache", ".codegraph"}
HOME = os.path.expanduser("~")


def _default_report():
    return os.environ.get("NIGHTLY_CONSOLIDATION_REPORT") or os.path.join(
        HOME, "AI", ".agent-memory", "metrics", "nightly-consolidation.jsonl")


def _default_kill_switch():
    return os.path.join(HOME, "AI", ".agent-memory", "nightly-consolidation.off")


def _denied(path, deny):
    p = os.path.normcase(os.path.abspath(path))
    return any(p == d or p.startswith(d + os.sep) for d in deny)


def discover(roots, max_depth, deny):
    """Project roots (parent of .agent-memory with a working/ dir), scandir walk, depth-limited."""
    deny = [os.path.normcase(os.path.abspath(d)) for d in deny]
    found, stack = [], [(os.path.abspath(r), 0) for r in roots]
    seen = set()
    while stack:
        path, depth = stack.pop()
        key = os.path.normcase(path)
        if key in seen or _denied(path, deny):
            continue
        seen.add(key)
        try:
            entries = list(os.scandir(path))
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if entry.name == ".agent-memory":
                if os.path.isdir(os.path.join(entry.path, "working")):
                    found.append(path)
            elif depth < max_depth and entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                stack.append((entry.path, depth + 1))
    return sorted(set(found))


def newest_real_dirty(mem):
    """mtime of the newest dirty file holding real (non-tail) work, or None. Unreadable -> treated as work."""
    work = os.path.join(mem, "working")
    newest = None
    try:
        names = os.listdir(work)
    except OSError:
        return None
    for name in names:
        if not (name.startswith("dirty-") and name.endswith(".json")):
            continue
        path = os.path.join(work, name)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not (isinstance(data, dict) and data.get("dirty")) or _is_tail(data):
                continue
        except (OSError, ValueError, UnicodeDecodeError):
            pass  # the core refuses unreadable evidence with exit 2 - surface that, do not hide it
        mtime = os.path.getmtime(path)
        newest = mtime if newest is None or mtime > newest else newest
    return newest


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="T-028 nightly headless consolidation")
    ap.add_argument("--root", action="append", default=None)
    ap.add_argument("--max-depth", type=int, default=4)
    ap.add_argument("--max-age-days", type=float, default=14)
    ap.add_argument("--min-age-minutes", type=int, default=120)
    ap.add_argument("--central-dir", default=os.path.join(HOME, "AI"))
    ap.add_argument("--home", default=HOME)
    ap.add_argument("--deny", action="append", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument("--kill-switch", default=None)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    roots = args.root or [HOME]
    deny = args.deny if args.deny is not None else [os.path.join(HOME, "ich")]
    report = args.report or _default_report()
    kill = args.kill_switch or _default_kill_switch()
    started = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    result = {"ts": started, "dry_run": args.dry_run, "projects": [], "skipped": {"stale": 0, "clean": 0}}

    if os.path.exists(kill):
        result["status"] = "killed"
    else:
        now = _dt.datetime.now().timestamp()
        candidates = []
        for proj in discover(roots, args.max_depth, deny):
            newest = newest_real_dirty(os.path.join(proj, ".agent-memory"))
            if newest is None:
                result["skipped"]["clean"] += 1
            elif now - newest > args.max_age_days * 86400:
                result["skipped"]["stale"] += 1
            else:
                candidates.append((newest, proj))
        for _, proj in sorted(candidates):
            cmd = [sys.executable, os.path.join(HERE, "wrapup_core.py"), "apply", "--headless",
                   "--mem", os.path.join(proj, ".agent-memory"), "--central-dir", args.central_dir,
                   "--home", args.home, "--min-age-minutes", str(args.min_age_minutes)]
            if args.dry_run:
                cmd.append("--dry-run")
            entry = {"project_root": proj}
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=args.timeout, stdin=subprocess.DEVNULL)
                out = json.loads(p.stdout) if p.stdout.strip() else {}
                entry.update({"rc": p.returncode, "status": out.get("status") or out.get("error", "no output"),
                              "iterations": out.get("iterations"), "handoff": out.get("handoff"),
                              "wiki_note": out.get("wiki_note")})
            except subprocess.TimeoutExpired:
                entry.update({"rc": None, "status": f"failed(timeout {args.timeout}s)"})
            except (OSError, ValueError) as exc:
                entry.update({"rc": None, "status": f"failed({type(exc).__name__})"})
            result["projects"].append(entry)
        result["status"] = "done"

    try:
        os.makedirs(os.path.dirname(os.path.abspath(report)), exist_ok=True)
        with open(report, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(result, ensure_ascii=False) + "\n")
    except OSError as exc:
        result["report_error"] = type(exc).__name__
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

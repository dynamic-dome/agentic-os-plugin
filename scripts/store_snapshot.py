#!/usr/bin/env python3
"""store_snapshot.py — byte copy of a memory store before a bulk mutation (5.3.0).

Taken automatically by the restore paths (apply_wrapup `restore` section,
extract_patterns --restore-archive) and on demand from the CLI. identity/ is
never copied (personal data has no business in a backup folder), neither are
working/ (session scratch) and metrics/.

Location: $AGENTIC_OS_SNAPSHOT_DIR, default ~/.agentic-os/snapshots, one folder
per store (slug of the store's absolute path), one sub-folder per snapshot.
Outside the store on purpose: a snapshot inside .agent-memory would be committed
by the project and read by indexers.

Retention: the newest `keep` snapshots, plus the newest one of each day for
the last 30 days. Older ones are deleted - they are copies, never the store.

Usage: python store_snapshot.py <mem-dir> [--keep 10]
Prints JSON {"ok", "snapshot", "files", "bytes", "removed"}; exit 0 / 1 usage / 2 io.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import sys

KEEP = 10
DAILY_DAYS = 30
SKIP_DIRS = {"identity", "working", "metrics", "snapshots"}


def snapshot_root() -> str:
    return os.environ.get("AGENTIC_OS_SNAPSHOT_DIR") or os.path.join(
        os.path.expanduser("~"), ".agentic-os", "snapshots")


def store_slug(mem: str) -> str:
    path = os.path.normcase(os.path.abspath(mem))
    return re.sub(r"[^A-Za-z0-9]+", "-", path).strip("-")[-120:]


def take(mem: str, keep: int = KEEP) -> dict:
    base = os.path.join(snapshot_root(), store_slug(mem))
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = os.path.join(base, stamp)
    files = size = 0
    for cur, dirs, names in os.walk(mem):
        rel_dir = os.path.relpath(cur, mem)
        top = rel_dir.split(os.sep)[0]
        if top in SKIP_DIRS:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not (rel_dir == "." and d in SKIP_DIRS)]
        for n in names:
            if not (n.endswith(".json") or n.endswith(".md")):
                continue
            src = os.path.join(cur, n)
            tgt = os.path.join(dest, rel_dir, n)
            os.makedirs(os.path.dirname(tgt), exist_ok=True)
            shutil.copy2(src, tgt)
            files += 1
            size += os.path.getsize(src)
    removed = prune(base, keep)
    return {"ok": True, "snapshot": dest, "files": files, "bytes": size, "removed": removed}


def prune(base: str, keep: int) -> list:
    snaps = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))
    keepset = set(snaps[-keep:]) if keep > 0 else set()
    cutoff = (_dt.date.today() - _dt.timedelta(days=DAILY_DAYS)).strftime("%Y%m%d")
    per_day: dict = {}
    for s in snaps:
        per_day[s[:8]] = s  # sorted -> the last one per day wins
    keepset |= {s for day, s in per_day.items() if day >= cutoff}
    removed = []
    for s in snaps:
        if s not in keepset:
            shutil.rmtree(os.path.join(base, s), ignore_errors=True)
            removed.append(s)
    return removed


def main(argv) -> int:
    ap = argparse.ArgumentParser(prog="store_snapshot.py")
    ap.add_argument("mem")
    ap.add_argument("--keep", type=int, default=KEEP)
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 1
    if not os.path.isdir(a.mem):
        print(json.dumps({"ok": False, "error": f"memory dir not found: {a.mem}"}))
        return 1
    try:
        print(json.dumps(take(a.mem, a.keep), ensure_ascii=False))
    except OSError as e:
        print(json.dumps({"ok": False, "error": f"io error: {e}"}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

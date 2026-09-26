#!/usr/bin/env python3
"""Read-then-write guard for cross-project write surfaces (T-19, membrain D9).

The central handoff and the status board are shared between parallel sessions
and agents. wrap-up Step 7.6 reads them, composes new content, then writes —
a parallel session writing in between silently loses its block. This guard
makes the read-modify-write cycle explicit:

  snapshot <file>... --state <state.json>   right after reading the surface
  check    <file>... --state <state.json>   immediately before writing it

Exit codes:
  0   content unchanged since snapshot (write is safe)
  20  DRIFT — file changed/appeared/vanished since snapshot: re-read, merge,
      re-snapshot, then write
  21  no usable snapshot for this file (read-then-write violated, or state
      file missing/corrupt) — read the file and snapshot first
  2   usage error

Content is compared by sha256; mtime is recorded for diagnostics only, so a
touch without a content change is NOT drift. State writes are atomic
(tmp + os.replace). stdlib only; no fail-soft on check — a guard that always
exits 0 guards nothing.
"""
import argparse
import hashlib
import json
import os
import sys
import time


def norm(path):
    return os.path.normcase(os.path.abspath(path))


def file_state(path):
    """(sha256-hex | None, mtime | None) — None means: file absent."""
    try:
        with open(path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        return digest, os.path.getmtime(path)
    except OSError:
        return None, None


def load_state(state_path):
    """Snapshot dict or None when missing/corrupt (callers decide severity)."""
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def save_state(state_path, data):
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, state_path)


def cmd_snapshot(files, state_path):
    data = load_state(state_path) or {}
    for path in files:
        digest, mtime = file_state(path)
        data[norm(path)] = {
            "sha256": digest,
            "mtime": mtime,
            "snapshotted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    save_state(state_path, data)
    print(f"snapshot: {len(files)} file(s) -> {state_path}")
    return 0


def cmd_check(files, state_path):
    data = load_state(state_path)
    if data is None:
        print(f"NO-SNAPSHOT: state file missing/corrupt: {state_path}", file=sys.stderr)
        return 21
    worst = 0
    for path in files:
        entry = data.get(norm(path))
        if entry is None:
            print(f"NO-SNAPSHOT: {path} was never snapshotted — read it first",
                  file=sys.stderr)
            worst = max(worst, 21)
            continue
        digest, _mtime = file_state(path)
        if digest == entry.get("sha256"):
            print(f"clean: {path}")
            continue
        was = entry.get("sha256") or "absent"
        now = digest or "absent"
        print(f"DRIFT: {path} changed since snapshot "
              f"({entry.get('snapshotted_at')}): {was[:12]} -> {now[:12]} — "
              f"re-read, merge, re-snapshot before writing")
        worst = max(worst, 20)
    return worst


def main(argv):
    parser = argparse.ArgumentParser(
        prog="handoff_write_guard.py",
        description="Read-then-write guard for shared handoff files.")
    parser.add_argument("command", choices=["snapshot", "check"])
    parser.add_argument("files", nargs="+", help="guarded file path(s)")
    parser.add_argument("--state", required=True, help="snapshot state JSON")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    if args.command == "snapshot":
        return cmd_snapshot(args.files, args.state)
    return cmd_check(args.files, args.state)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""gc_dirty_markers.py — deterministic garbage collection for orphaned dirty-*.json
recovery markers in .agent-memory/working/.

The PostToolUse dirty-tracker writes one dirty-<session>.json per session; wrap-up
only resets its OWN session's marker, so markers from crashed / parallel / other
sessions accumulate indefinitely (they cost 0 tokens — preprocess_state.py excludes
working/ — but they are disk cruft and noise for recovery-detection).

A marker is GC-eligible when, in precedence order:
  1. mtime within 30 min                       -> KEEP (running/parallel session)
  2. consolidated (dirty==false OR consolidated_at set) -> REMOVE
  3. updated < consolidation-marker.last_wrapup (a later wrap-up ran, so the work is
     consolidated in git/native memory)         -> REMOVE
  4. otherwise (un-consolidated, no later wrap-up) -> KEEP (real recovery candidate)

Rule 3 uses strict `<` to match session-bootstrap's Recovery-Detection, which keeps
full recovery unless the wrap-up is strictly NEWER than the marker's `updated`.

Timestamps are compared as real timezone-aware instants (not string prefixes), so a
DST change or a mixed tz-offset / naive pair can never mis-order them. A naive value
(e.g. consolidation-marker.last_wrapup) is interpreted as local time. An unparseable
or non-string value yields None and simply disables rule 3 for that marker — it is
never a reason to delete.

Default is dry-run (lists candidates, deletes nothing). Pass --apply to delete.
Called by /agentic-os:maintain; safe to run from a hook.

Usage: python gc_dirty_markers.py [path-to-.agent-memory] [--apply]
Exit 0 always (fail-soft); a parse/type error on one marker skips that marker only.
"""
import json
import os
import sys
import time
from datetime import datetime

PROTECT_WINDOW_S = 30 * 60  # <30 min mtime = likely a live/parallel session


def _parse_dt(value):
    """Parse an ISO-8601 string to a timezone-aware datetime, or None. A naive value
    (no offset) is read as local time; an invalid/non-string value returns None so it
    can never satisfy the delete rule."""
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # naive -> interpret as local wall-clock (DST-correct via the OS tz db)
        dt = dt.astimezone()
    return dt


def _last_wrapup(mem):
    try:
        with open(os.path.join(mem, "consolidation-marker.json"), encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    return _parse_dt(obj.get("last_wrapup"))


def _eligible(path, marker_wrapup, now):
    """Return (remove: bool, reason: str). Reads fresh from disk each call so it can be
    re-invoked immediately before delete to shrink the TOCTOU window."""
    # (1) protection: fresh mtime => running/parallel session
    try:
        if now - os.path.getmtime(path) < PROTECT_WINDOW_S:
            return False, "protected (<30min mtime)"
    except OSError:
        return False, "stat-failed"
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return False, "unparseable"  # never delete what we cannot read
    if not isinstance(obj, dict):
        return False, "not-an-object"  # corrupt shape -> never delete
    # (2) cleanly consolidated
    if obj.get("dirty") is False or obj.get("consolidated_at"):
        return True, "consolidated"
    # (3) a later wrap-up ran strictly after this session's last write
    updated = _parse_dt(obj.get("updated"))
    if marker_wrapup and updated and updated < marker_wrapup:
        return True, "superseded by later wrap-up"
    # (4) un-consolidated, no later wrap-up -> real recovery candidate
    return False, "un-consolidated (keep for recovery)"


def gc(mem, apply=False):
    working = os.path.join(mem, "working")
    if not os.path.isdir(working):
        return 0
    marker_wrapup = _last_wrapup(mem)
    removed = 0
    for name in sorted(os.listdir(working)):
        if not (name.startswith("dirty-") and name.endswith(".json")):
            continue
        path = os.path.join(working, name)
        remove, reason = _eligible(path, marker_wrapup, time.time())
        if not remove:
            continue
        if apply:
            # TOCTOU guard: re-verify with a fresh read/stat right before deleting, so a
            # parallel session that just re-dirtied this path is not clobbered.
            remove2, _ = _eligible(path, marker_wrapup, time.time())
            if not remove2:
                print(f"skipped (re-dirtied before delete): {name}")
                continue
            try:
                os.remove(path)
            except OSError as e:
                print(f"skip {name}: {e}")
                continue
            print(f"removed: {name} ({reason})")
        else:
            print(f"would remove: {name} ({reason})")
        removed += 1
    tail = "removed" if apply else "GC-eligible"
    print(f"{removed} orphaned dirty marker(s) {tail}.")
    return removed


def main(argv):
    args = [a for a in argv[1:] if a != "--apply"]
    apply = "--apply" in argv[1:]
    mem = args[0] if args else ".agent-memory"
    gc(mem, apply=apply)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

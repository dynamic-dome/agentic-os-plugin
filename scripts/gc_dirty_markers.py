#!/usr/bin/env python3
"""gc_dirty_markers.py — the only deleter of dirty-*.json markers in .agent-memory/working/.

The PostToolUse tracker writes one dirty-<session>.json per session. Since Lebenszyklus
Phase 2 Teil 2 (design A+, D-024) the marker also carries the session's harvest state
(scripts/harvest_state.py), so it may disappear only once that state says "closed":

  1. mtime within 30 min                         -> KEEP (running/parallel session)
  2. unreadable or not an object                 -> KEEP (never delete what we cannot read)
  3. harvest_state.marker_state(d) == "closed"   -> REMOVE, after one evidence line in
     metrics/harvest-closures.jsonl
  4. everything else                             -> KEEP: dirty work, and every pending
     session - harvested headless, consolidated by ANOTHER session's wrap-up, foreign-only,
     a legacy "wrap-up" stamp without a closure (until the backfill closes it), or a
     closure whose snapshot no longer matches because the session went on working.

The old rules "consolidated -> remove" and "a later wrap-up ran -> remove" are gone: they
deleted the last trace of sessions nobody had harvested (the silent loss of 07.-10.10.).

Evidence (append-only, evaluation only - Phase 4/6/7, never read for a decision): sid,
closed_by, closed_at, run, edits (own + foreign), transcript_path, transcript_sha256,
removed_at. Written BEFORE the delete; if it cannot be written, the marker stays. A delete
that fails after the line was written may leave a duplicate line next run - readers key on
(sid, closed_at).

--apply takes the store lock per marker and only for re-read, snapshot check, evidence
append and delete - the transcript hash is computed before, because the tracker waits only
LOCK_TIMEOUT_S (2 s) for the lock before it writes unlocked. A busy lock skips that marker.
Default is a dry run. Called by the daily run (memory_hygiene.py --apply) and /agentic-os:maintain
(preview). Exit 0 always (fail-soft).

Usage: python gc_dirty_markers.py [path-to-.agent-memory] [--apply] [--lock-timeout S]
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import harvest_state as hs  # noqa: E402
import store_lock  # noqa: E402

PROTECT_WINDOW_S = 30 * 60  # <30 min mtime = likely a live/parallel session
EVIDENCE = os.path.join("metrics", "harvest-closures.jsonl")


def _eligible(path, now):
    """(remove, reason, marker). Reads fresh from disk on every call."""
    try:
        if now - os.path.getmtime(path) < PROTECT_WINDOW_S:
            return False, "protected (<30min mtime)", None
    except OSError:
        return False, "stat-failed", None
    try:
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return False, "unparseable", None
    if not isinstance(d, dict):
        return False, "not-an-object", None
    state = hs.marker_state(d)
    if state == "closed":
        return True, f"closed ({d['harvest']['closed_by']})", d
    return False, state, d


def _sha256(path):
    if not isinstance(path, str) or not path:
        return None
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _evidence(d):
    h = d["harvest"]
    edits = hs.edit_count(d)
    tp = d.get("transcript_path") if isinstance(d.get("transcript_path"), str) else None
    return {"sid": str(d.get("session_id") or ""), "closed_by": h.get("closed_by"), "closed_at": h.get("closed_at"),
            "run": h.get("run"), "edits": edits, "transcript_path": tp, "transcript_sha256": _sha256(tp),
            "removed_at": datetime.now().astimezone().isoformat(timespec="seconds")}


def _append_evidence(mem, record):
    path = os.path.join(mem, EVIDENCE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _delete_tomb(tomb):
    os.remove(tomb)


def _restore(tomb, path):
    """Put a renamed marker back unless the tracker already created a fresh one."""
    try:
        if not os.path.exists(path):
            os.replace(tomb, path)
        else:
            os.remove(tomb)  # the fresh marker carries the session on; the tomb held a closed state
    except OSError:
        pass


def _remove_locked(mem, path, d, record, lock_timeout):
    """Under the store lock: still closed with the same snapshot -> rename, verify the renamed
    bytes, evidence, delete. The rename closes the window in which the tracker (which writes
    unlocked after its 2 s timeout) could land a write between the re-read and the delete:
    such a write ends up in the renamed file, fails the verify and is put back.
    Returns None on success, else the reason the marker stays."""
    try:
        with store_lock.store_lock(mem, timeout=lock_timeout):
            remove, reason, _ = _eligible(path, time.time())
            if not remove:  # the snapshot is compared on the renamed bytes below
                return f"changed before delete ({reason})"
            tomb = f"{path}.gc-{os.getpid()}"
            try:
                os.replace(path, tomb)
            except OSError as e:
                return str(e)
            try:
                with open(tomb, encoding="utf-8-sig") as f:
                    d3 = json.load(f)
            except (OSError, ValueError):
                d3 = None
            if not isinstance(d3, dict) or hs.marker_state(d3) != "closed" or hs.snapshot(d3) != hs.snapshot(d):
                _restore(tomb, path)
                return "changed during delete - restored"
            try:
                _append_evidence(mem, record)
            except OSError as e:
                _restore(tomb, path)
                return f"evidence not written ({type(e).__name__}) - marker kept"
            try:
                _delete_tomb(tomb)
            except OSError as e:  # Windows: another handle is open - never strand it as .gc-*
                _restore(tomb, path)
                return f"tomb not deleted ({type(e).__name__}) - restored, next run retries"
    except store_lock.LockTimeout:
        return "store lock busy - marker kept"
    return None


def gc(mem, apply=False, lock_timeout=store_lock.TIMEOUT_SECONDS):
    working = os.path.join(mem, "working")
    if not os.path.isdir(working):
        return 0
    removed = 0
    kept = {}
    for name in sorted(os.listdir(working)):
        if not (name.startswith("dirty-") and name.endswith(".json")):
            continue
        path = os.path.join(working, name)
        remove, reason, d = _eligible(path, time.time())
        if not remove:
            key = reason.split(" ")[0]
            kept[key] = kept.get(key, 0) + 1
            continue
        if not apply:
            print(f"would remove: {name} ({reason})")
            removed += 1
            continue
        why = _remove_locked(mem, path, d, _evidence(d), lock_timeout)  # hash outside the lock
        if why:
            print(f"skip {name}: {why}")
            kept["skipped"] = kept.get("skipped", 0) + 1
            continue
        print(f"removed: {name} ({reason})")
        removed += 1
    tail = "removed" if apply else "GC-eligible"
    kept_txt = ", ".join(f"{k} {v}" for k, v in sorted(kept.items())) or "none"
    print(f"{removed} closed marker(s) {tail}; kept: {kept_txt}.")
    return removed


def main(argv):
    ap = argparse.ArgumentParser(description="Delete closed dirty markers (harvest_state), with evidence.")
    ap.add_argument("mem", nargs="?", default=".agent-memory")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--lock-timeout", type=float, default=store_lock.TIMEOUT_SECONDS)
    args = ap.parse_args(argv[1:])
    gc(args.mem, apply=args.apply, lock_timeout=args.lock_timeout)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

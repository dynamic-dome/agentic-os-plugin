#!/usr/bin/env python3
"""Tests for scripts/gc_dirty_markers.py — orphaned dirty-marker garbage collection.

Rule under test (precedence):
  1. mtime within 30 min  -> KEEP (running/parallel session, never touch)
  2. consolidated (dirty==false OR consolidated_at set) -> GC
  3. updated <= marker.last_wrapup (a later wrap-up ran) -> GC
  4. otherwise (un-consolidated, no later wrap-up) -> KEEP

Run: python tests/test-gc-dirty-markers.py   (exit 0 = pass, 1 = fail)
"""
import json
import os
import subprocess
import sys
import tempfile
import time

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "gc_dirty_markers.py")

FAILURES = []


def _check(cond, msg):
    if cond:
        print(f"  PASS: {msg}")
    else:
        print(f"  FAIL: {msg}")
        FAILURES.append(msg)


def _write_dirty(working, sid, *, dirty, updated, consolidated_at=None, mtime_age_s=None):
    path = os.path.join(working, f"dirty-{sid}.json")
    obj = {
        "session_id": sid,
        "agent": "claude",
        "dirty": dirty,
        "updated": updated,
        "touched_files": ["x.md"],
        "write_count": 1,
        "consolidated_at": consolidated_at,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    if mtime_age_s is not None:
        t = time.time() - mtime_age_s
        os.utime(path, (t, t))
    return path


def _run(mem, apply=False):
    cmd = [sys.executable, SCRIPT, mem]
    if apply:
        cmd.append("--apply")
    return subprocess.run(cmd, capture_output=True, text=True)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        mem = os.path.join(tmp, ".agent-memory")
        working = os.path.join(mem, "working")
        os.makedirs(working)
        # marker: last wrap-up at 04:12
        with open(os.path.join(mem, "consolidation-marker.json"), "w", encoding="utf-8") as f:
            json.dump({"last_wrapup": "2026-07-18T04:12:02"}, f)

        DAY = 86400
        # (1) consolidated cleanly, old -> GC
        _write_dirty(working, "consol", dirty=False, updated="2026-07-18T03:12:03+02:00",
                     consolidated_at="2026-07-18T04:12:02", mtime_age_s=2 * DAY)
        # (2) still dirty but a later wrap-up ran (updated < last_wrapup) -> GC
        _write_dirty(working, "olddirty", dirty=True, updated="2026-07-18T02:19:00+02:00",
                     mtime_age_s=2 * DAY)
        # (3) dirty, updated AFTER last_wrapup -> un-consolidated, KEEP
        _write_dirty(working, "newdirty", dirty=True, updated="2026-07-18T07:42:00+02:00",
                     mtime_age_s=2 * DAY)
        # (4) dirty, fresh mtime (5 min) -> protected running/parallel, KEEP
        _write_dirty(working, "running", dirty=True, updated="2026-07-18T08:18:00+02:00",
                     mtime_age_s=5 * 60)

        # dry-run must not delete anything
        r = _run(mem, apply=False)
        _check(r.returncode == 0, f"dry-run exits 0 (got {r.returncode}, stderr={r.stderr[:200]})")
        _check(os.path.exists(os.path.join(working, "dirty-consol.json")),
               "dry-run keeps files on disk")
        out = r.stdout
        _check("consol" in out and "olddirty" in out, "dry-run lists both GC candidates")
        _check("newdirty" not in out, "dry-run does NOT list un-consolidated newer session")
        _check("running" not in out, "dry-run does NOT list fresh (protected) session")

        # apply deletes exactly the two candidates
        r = _run(mem, apply=True)
        _check(r.returncode == 0, f"apply exits 0 (got {r.returncode})")
        _check(not os.path.exists(os.path.join(working, "dirty-consol.json")),
               "apply deletes consolidated marker")
        _check(not os.path.exists(os.path.join(working, "dirty-olddirty.json")),
               "apply deletes old-dirty marker (later wrap-up ran)")
        _check(os.path.exists(os.path.join(working, "dirty-newdirty.json")),
               "apply KEEPS un-consolidated newer session")
        _check(os.path.exists(os.path.join(working, "dirty-running.json")),
               "apply KEEPS fresh (protected) session")

        # no marker present -> only mtime>7d + consolidated qualify, never crash
        mem2 = os.path.join(tmp, "mem2")
        w2 = os.path.join(mem2, "working")
        os.makedirs(w2)
        _write_dirty(w2, "nomarkfresh", dirty=True, updated="2026-07-18T02:00:00+02:00",
                     mtime_age_s=2 * DAY)
        r = _run(mem2, apply=True)
        _check(r.returncode == 0, "no-marker run exits 0")
        _check(os.path.exists(os.path.join(w2, "dirty-nomarkfresh.json")),
               "no marker + dirty + <7d -> KEEP (nothing proves consolidation)")

        # --- Codex-hardening cases ---
        DAY2 = 86400
        # #1/#2: an unparseable last_wrapup must NOT enable rule-3 deletion
        mem3 = os.path.join(tmp, "mem3")
        w3 = os.path.join(mem3, "working")
        os.makedirs(w3)
        with open(os.path.join(mem3, "consolidation-marker.json"), "w", encoding="utf-8") as f:
            json.dump({"last_wrapup": "9999-99-99T99:99:99"}, f)
        _write_dirty(w3, "invalidmark", dirty=True, updated="2026-07-18T07:42:00+02:00",
                     mtime_age_s=2 * DAY2)
        r = _run(mem3, apply=True)
        _check(r.returncode == 0, "#2 invalid last_wrapup: exit 0 (fail-soft)")
        _check(os.path.exists(os.path.join(w3, "dirty-invalidmark.json")),
               "#2 invalid last_wrapup does NOT delete an un-consolidated marker")

        # #4: non-dict JSON (marker=[] and dirty=[]) must not crash, must not delete
        mem4 = os.path.join(tmp, "mem4")
        w4 = os.path.join(mem4, "working")
        os.makedirs(w4)
        with open(os.path.join(mem4, "consolidation-marker.json"), "w", encoding="utf-8") as f:
            json.dump([], f)
        p_list = os.path.join(w4, "dirty-listshape.json")
        with open(p_list, "w", encoding="utf-8") as f:
            json.dump([], f)
        t = time.time() - 2 * DAY2
        os.utime(p_list, (t, t))
        r = _run(mem4, apply=True)
        _check(r.returncode == 0, "#4 non-dict JSON: exit 0 (no AttributeError)")
        _check(os.path.exists(p_list), "#4 list-shaped dirty json is NOT deleted")

        # #8: equality boundary — updated == last_wrapup (strict <) -> KEEP.
        # Both naive (no offset) so astimezone() normalises them equally, tz-independent.
        mem5 = os.path.join(tmp, "mem5")
        w5 = os.path.join(mem5, "working")
        os.makedirs(w5)
        with open(os.path.join(mem5, "consolidation-marker.json"), "w", encoding="utf-8") as f:
            json.dump({"last_wrapup": "2026-07-18T04:12:02"}, f)
        _write_dirty(w5, "equal", dirty=True, updated="2026-07-18T04:12:02",
                     mtime_age_s=2 * DAY2)
        r = _run(mem5, apply=True)
        _check(os.path.exists(os.path.join(w5, "dirty-equal.json")),
               "#8 updated == last_wrapup -> KEEP (strict <, parity with bootstrap)")

    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S)")
        return 1
    print("\nALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

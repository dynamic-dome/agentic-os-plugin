#!/usr/bin/env python3
"""Tests for scripts/gc_dirty_markers.py — dirty-marker garbage collection.

Rule under test (Lebenszyklus Phase 2 Teil 2, Scheibe 7, design A+ / D-024):
  1. mtime within 30 min                         -> KEEP (running/parallel session)
  2. unreadable or not an object                 -> KEEP (never delete what we cannot read)
  3. harvest_state.marker_state(d) == "closed"   -> REMOVE, after one evidence line in
     metrics/harvest-closures.jsonl (sid, closed_by, closed_at, run, edits,
     transcript_path, transcript_sha256)
  4. everything else (dirty, pending: headless, foreign wrap-up, foreign-only,
     legacy "wrap-up" stamps without a closure, reopened closures) -> KEEP
--apply runs under the store lock; a busy lock skips the run (exit 0, nothing removed).

Every path is a temp path. Run: python tests/test-gc-dirty-markers.py   (exit 0 = pass)
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "gc_dirty_markers.py")
_spec = importlib.util.spec_from_file_location("harvest_state_gc", os.path.join(HERE, "..", "scripts", "harvest_state.py"))
hs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hs)
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import store_lock  # noqa: E402

FAILURES = []
DAY = 86400


def _check(cond, msg):
    if cond:
        print(f"  PASS: {msg}")
    else:
        print(f"  FAIL: {msg}")
        FAILURES.append(msg)


def _write(working, sid, *, mtime_age_s=2 * DAY, closed_by=None, raw=None, **fields):
    path = os.path.join(working, f"dirty-{sid}.json")
    if raw is not None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(raw)
    else:
        d = {"session_id": sid, "agent": "claude", "dirty": False, "updated": "2026-10-09T23:00:00+02:00",
             "touched_files": ["x.md"], "write_count": 2}
        d.update(fields)
        if closed_by:
            d["harvest"] = hs.make_closure(d, closed_by, "nightly", "2026-10-10T03:00:00+02:00")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f)
    t = time.time() - mtime_age_s
    os.utime(path, (t, t))
    return path


def _run(mem, *extra):
    return subprocess.run([sys.executable, SCRIPT, mem, *extra], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _evidence(mem):
    p = os.path.join(mem, "metrics", "harvest-closures.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(ln) for ln in open(p, encoding="utf-8") if ln.strip()]


def main():
    with tempfile.TemporaryDirectory() as tmp:
        mem = os.path.join(tmp, ".agent-memory")
        w = os.path.join(mem, "working")
        os.makedirs(w)
        tr = os.path.join(tmp, "closed.jsonl")
        with open(tr, "w", encoding="utf-8") as f:
            f.write('{"type": "user"}\n')
        sha = hashlib.sha256(open(tr, "rb").read()).hexdigest()
        # a later wrap-up used to delete superseded markers - the consolidation marker no longer matters
        with open(os.path.join(mem, "consolidation-marker.json"), "w", encoding="utf-8") as f:
            json.dump({"last_wrapup": "2026-10-11T04:12:02", "mode": "wrap-up"}, f)

        _write(w, "closed", closed_by="wrapup_im_transkript", transcript_path=tr, foreign_write_count=1,
               foreign_touched={"C:/o": ["C:/o/a.py"]})
        _write(w, "closedwrapup", closed_by="wrap-up", consolidated_by="wrap-up", consolidated_at="x")
        _write(w, "dirty", dirty=True)
        _write(w, "headless", consolidated_by="headless", consolidated_at="2026-10-10T03:00:00+02:00")
        _write(w, "legacy", consolidated_by="wrap-up", consolidated_at="2026-09-01T03:00:00+02:00")
        _write(w, "superseded", dirty=True, updated="2026-10-01T10:00:00+02:00")
        _write(w, "foreignonly", updated=None, foreign_touched={"C:/o": ["C:/o/a.py"]})
        reopened = _write(w, "reopened", closed_by="trivial")
        d = json.load(open(reopened, encoding="utf-8"))
        d["write_count"] = 3  # new work after the closure
        json.dump(d, open(reopened, "w", encoding="utf-8"))
        os.utime(reopened, (time.time() - 2 * DAY, time.time() - 2 * DAY))
        _write(w, "fresh", closed_by="trivial", mtime_age_s=5 * 60)
        _write(w, "listshape", raw="[]")
        _write(w, "broken", raw="{kaputt")

        r = _run(mem)
        _check(r.returncode == 0, f"dry-run exits 0 ({r.returncode}, {r.stderr[:200]})")
        listed = {ln.split("dirty-")[1].split(".json")[0] for ln in r.stdout.splitlines() if ln.startswith("would remove")}
        _check(listed == {"closed", "closedwrapup"}, f"dry-run lists exactly the closed markers {sorted(listed)}")
        _check(len(os.listdir(w)) == 11 and not _evidence(mem), "dry-run deletes nothing and writes no evidence")

        r = _run(mem, "--apply")
        left = sorted(n[len("dirty-"):-len(".json")] for n in os.listdir(w) if n.startswith("dirty-"))
        _check(r.returncode == 0 and left == ["broken", "dirty", "foreignonly", "fresh", "headless", "legacy",
                                             "listshape", "reopened", "superseded"],
               f"apply removes exactly the closed markers, keeps the rest {left}")
        ev = {e["sid"]: e for e in _evidence(mem)}
        _check(set(ev) == {"closed", "closedwrapup"}, f"one evidence line per removed marker {sorted(ev)}")
        e = ev.get("closed", {})
        _check(e.get("closed_by") == "wrapup_im_transkript" and e.get("run") == "nightly"
               and e.get("closed_at") == "2026-10-10T03:00:00+02:00" and e.get("edits") == 3
               and e.get("transcript_path") == tr and e.get("transcript_sha256") == sha and e.get("removed_at"),
               f"evidence carries closure, edits (own+foreign), transcript path and hash {e}")
        _check(ev.get("closedwrapup", {}).get("transcript_sha256") is None,
               "no transcript recorded -> hash null, never guessed")
        _check("removed: dirty-closed.json" in r.stdout and "orphaned" not in r.stdout
               and "1 kept" not in r.stdout and "2 closed marker(s) removed" in r.stdout,
               f"summary line counts removed markers ({r.stdout.strip().splitlines()[-1:]})")
        r = _run(mem, "--apply")
        _check(len(_evidence(mem)) == 2 and "0 closed marker(s) removed" in r.stdout,
               "second run: nothing to remove, no duplicate evidence")

        # busy store lock: skip the whole run, delete nothing
        _write(w, "closed2", closed_by="trivial")
        with store_lock.store_lock(mem, timeout=5):
            r = _run(mem, "--apply", "--lock-timeout", "0.3")
        _check(r.returncode == 0 and os.path.exists(os.path.join(w, "dirty-closed2.json"))
               and "store lock busy" in r.stdout, f"busy store lock -> skipped, nothing removed ({r.stdout.strip()[:120]})")

        # evidence cannot be written -> never delete without it
        os.makedirs(os.path.join(mem, "metrics"), exist_ok=True)
        ev_path = os.path.join(mem, "metrics", "harvest-closures.jsonl")
        os.remove(ev_path)
        os.makedirs(ev_path)  # a directory where the file should be: every append fails
        r = _run(mem, "--apply")
        _check(os.path.exists(os.path.join(w, "dirty-closed2.json")) and r.returncode == 0,
               f"evidence write fails -> marker kept ({r.stdout.strip()[:160]})")

    # the session writes again between the decision and the lock (here: while the evidence
    # is computed) -> the re-read under the lock sees a new snapshot and keeps the marker
    _gspec = importlib.util.spec_from_file_location("gc_inproc", SCRIPT)
    gcm = importlib.util.module_from_spec(_gspec)
    _gspec.loader.exec_module(gcm)
    with tempfile.TemporaryDirectory() as tmp:
        mem = os.path.join(tmp, ".agent-memory")
        w = os.path.join(mem, "working")
        os.makedirs(w)
        path = _write(w, "racy", closed_by="trivial")
        real_evidence = gcm._evidence

        def evidence_while_tracker_writes(d):
            m = json.load(open(path, encoding="utf-8"))
            m.update(dirty=True, write_count=3, updated="2026-10-11T12:00:00+02:00")
            json.dump(m, open(path, "w", encoding="utf-8"))
            os.utime(path, (time.time() - 2 * DAY, time.time() - 2 * DAY))
            return real_evidence(d)

        gcm._evidence = evidence_while_tracker_writes
        gcm.gc(mem, apply=True)
        _check(os.path.exists(path) and json.load(open(path, encoding="utf-8")).get("dirty") is True
               and not _evidence(mem), "re-dirtied between decision and lock -> kept, no evidence")

        # another run re-closed the session in between: still closed, but the evidence
        # computed before would describe the OLD closure -> keep, next run writes the right one
        path = _write(w, "reclosed", closed_by="trivial")

        def evidence_while_reclosed(d):
            m = json.load(open(path, encoding="utf-8"))
            m.update(write_count=5, updated="2026-10-11T12:00:00+02:00")
            m["harvest"] = hs.make_closure(m, "native_note", "nightly", "2026-10-11T13:00:00+02:00")
            json.dump(m, open(path, "w", encoding="utf-8"))
            os.utime(path, (time.time() - 2 * DAY, time.time() - 2 * DAY))
            return real_evidence(d)

        gcm._evidence = evidence_while_reclosed
        gcm.gc(mem, apply=True)
        _check(os.path.exists(path) and not _evidence(mem),
               "re-closed with a new snapshot between decision and lock -> kept, no stale evidence")

        # the file turned unreadable in between -> keep it, never crash
        path = _write(w, "corrupted", closed_by="trivial")

        def evidence_while_corrupted(d):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{halb")
            os.utime(path, (time.time() - 2 * DAY, time.time() - 2 * DAY))
            return real_evidence(d)

        gcm._evidence = evidence_while_corrupted
        try:
            gcm.gc(mem, apply=True)
            crashed = False
        except Exception:
            crashed = True
        sids = [e["sid"] for e in _evidence(mem)]
        _check(not crashed and os.path.exists(path) and "corrupted" not in sids,
               f"unreadable between decision and lock -> kept, no crash {sids}")
        _check(sids == ["reclosed"], "the re-closed marker goes in the next run")
        _check(_evidence(mem) and _evidence(mem)[0].get("closed_by") == "native_note",
               "evidence describes the new closure (native_note), not the old one")
        gcm._evidence = real_evidence

    # H2: the tracker writes unlocked (after its 2 s timeout) right after the GC's locked
    # re-read - the GC renames first and verifies the renamed bytes, so the new write survives
    with tempfile.TemporaryDirectory() as tmp:
        mem = os.path.join(tmp, ".agent-memory")
        w = os.path.join(mem, "working")
        os.makedirs(w)
        path = _write(w, "unlocked", closed_by="trivial")
        real_eligible = gcm._eligible
        calls = {"n": 0}

        def eligible_then_tracker_writes(p, now):
            res = real_eligible(p, now)
            calls["n"] += 1
            if calls["n"] == 2:  # the re-read under the lock
                m = json.load(open(path, encoding="utf-8"))
                m.update(dirty=True, write_count=3, updated="2026-10-11T12:00:00+02:00")
                json.dump(m, open(path, "w", encoding="utf-8"))
            return res

        gcm._eligible = eligible_then_tracker_writes
        gcm.gc(mem, apply=True)
        gcm._eligible = real_eligible
        _check(os.path.exists(path) and json.load(open(path, encoding="utf-8")).get("write_count") == 3
               and not _evidence(mem) and not [n for n in os.listdir(w) if ".gc-" in n],
               "unlocked tracker write after the re-read survives (rename, verify, restore), no evidence, no tombstone")

    # R2: deleting the verified tomb fails (Windows: another handle is open) -> the marker goes
    # back to its name, the next run retries; never a stranded .gc file
    with tempfile.TemporaryDirectory() as tmp:
        mem = os.path.join(tmp, ".agent-memory")
        w = os.path.join(mem, "working")
        os.makedirs(w)
        path = _write(w, "locked", closed_by="trivial")
        real_delete = gcm._delete_tomb

        def delete_fails(tomb):
            raise PermissionError(32, "in use")

        gcm._delete_tomb = delete_fails
        gcm.gc(mem, apply=True)
        gcm._delete_tomb = real_delete
        _check(os.path.exists(path) and not [n for n in os.listdir(w) if ".gc-" in n],
               "tomb delete fails -> marker restored under its name, no stranded tomb")
        gcm.gc(mem, apply=True)
        _check(not os.path.exists(path) and [e["sid"] for e in _evidence(mem)] == ["locked", "locked"],
               "next run removes it; the duplicate evidence line is keyed by (sid, closed_at)")

    # missing working/ -> exit 0, nothing to do
    with tempfile.TemporaryDirectory() as tmp:
        r = _run(os.path.join(tmp, ".agent-memory"), "--apply")
        _check(r.returncode == 0, "no working/ dir -> exit 0")

    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S)")
        return 1
    print("\nALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Tests for the 5.3.0 write basis (T-030): what stays of the old restore test after 6.0.0.

Covers: store lock, row validation, case-insensitive ownership, learnings
dedup in code (Step 3a), review_sweep --charges and store_snapshot.py.
The restore path itself (restore_plan.py, plan section `restore`) is gone;
tests/test-apply-wrapup.py pins the refusal.

Every case runs against throwaway dirs under tempfile.mkdtemp(); snapshots go
to a temp dir via AGENTIC_OS_SNAPSHOT_DIR. The real stores are never touched.

Exit codes: 0 = all pass, 1 = failures found.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
APPLY = os.path.join(SCRIPTS, "apply_wrapup.py")
SWEEP = os.path.join(SCRIPTS, "review_sweep.py")
SNAPSHOT = os.path.join(SCRIPTS, "store_snapshot.py")
sys.path.insert(0, SCRIPTS)

from _isolated_home import process_env  # noqa: E402

# Subprozesse laufen mit frischem Temp-Home statt mit dem echten Home (P0, tests/_isolated_home.py)
ENV, ISO_HOME = process_env(PYTHONIOENCODING="utf-8")
SNAP_DIR = ENV["AGENTIC_OS_SNAPSHOT_DIR"]  # liegt im Temp-Home, wird mit ihm entfernt

TESTS = PASSED = ERRORS = 0


def check(cond, msg):
    global TESTS, PASSED, ERRORS
    TESTS += 1
    if cond:
        PASSED += 1
        print(f"  PASS: {msg}")
    else:
        ERRORS += 1
        print(f"  FAIL: {msg}")


def put(mem, rel, data):
    path = os.path.join(mem, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def load(mem, rel):
    with open(os.path.join(mem, rel), encoding="utf-8-sig") as fh:
        return json.load(fh)


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def learning(lid, text, **kw):
    row = {"id": lid, "date": "2026-07-01", "text": text, "importance": 3, "tags": ["t"],
           "layer": "short-term", "superseded_by": None, "last_relevant": "2026-07-01"}
    row.update(kw)
    return row


def run(args, stdin=None):
    proc = subprocess.run([sys.executable] + args, input=stdin, capture_output=True,
                          text=True, encoding="utf-8", env=ENV)
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        out = {"_raw": proc.stdout, "_err": proc.stderr}
    return proc.returncode, out


def apply(mem, plan, *extra):
    return run([APPLY, mem] + list(extra), json.dumps(plan))


def make_store():
    """A store with live learnings and an archive file next to them."""
    mem = tempfile.mkdtemp(prefix="writebasis-test-")
    put(mem, "learnings/learnings.json", [
        learning("L10", "Live Eintrag zehn ueber Windows Pfade und Encoding"),
        learning("L11", "Live Eintrag elf ueber Git Hooks und pre-commit Laufzeit"),
        learning("L12", "Live Eintrag zwoelf ueber Atlas Index Rebuild nach Store Aenderung"),
    ])
    put(mem, "learnings/learnings-archive-2026-07.json", [
        learning("L1", "Archiv Eintrag eins: Subprocess unter Windows braucht shutil which"),
    ])
    os.makedirs(os.path.join(mem, "working"), exist_ok=True)
    return mem


# ============================================================ 1. store lock
print("--- store lock")
import store_lock  # noqa: E402

mem = tempfile.mkdtemp(prefix="lock-test-")
with store_lock.store_lock(mem):
    check(os.path.exists(os.path.join(mem, "working", "store.lock")), "lock file exists while held")
    t0 = time.time()
    try:
        with store_lock.store_lock(mem, timeout=0.5):
            check(False, "second acquire must time out while the first holds the lock")
    except store_lock.LockTimeout:
        check(time.time() - t0 >= 0.4, "second acquire waits, then raises LockTimeout")
t0 = time.time()
with store_lock.store_lock(mem, timeout=0.5):
    check(time.time() - t0 < 0.3, "after release the lock is free at once (OS lock, file may stay)")
check(issubclass(store_lock.LockTimeout, OSError), "LockTimeout is an OSError (writers report it as io error, exit 2)")

# a leftover lock file (crashed holder) never blocks: the OS lock died with the process
lock = os.path.join(mem, "working", "store.lock")
with open(lock, "w") as fh:
    fh.write("99999 crashed")
t0 = time.time()
with store_lock.store_lock(mem, timeout=1):
    check(time.time() - t0 < 0.3, "a leftover lock file without a live holder does not block")
# a holder that is KILLED while holding releases the lock (no stale logic, nothing deleted)
HOLD = ("import sys, time; sys.path.insert(0, sys.argv[1]); import store_lock\n"
        "with store_lock.store_lock(sys.argv[2]):\n"
        "    print('held', flush=True); time.sleep(60)\n")
holder = subprocess.Popen([sys.executable, "-c", HOLD, SCRIPTS, mem], stdout=subprocess.PIPE, text=True, env=ENV)
holder.stdout.readline()
try:
    with store_lock.store_lock(mem, timeout=0.3):
        check(False, "a live holder in another process blocks")
except store_lock.LockTimeout:
    check(True, "a live holder in another process blocks")
holder.kill()
holder.wait()
with store_lock.store_lock(mem, timeout=2):
    check(True, "a killed holder's lock is released by the OS")

# a held lock blocks a real apply run -> exit 2, nothing written
mem = make_store()
before = sha(os.path.join(mem, "learnings/learnings.json"))
with store_lock.store_lock(mem):
    rc, out = run([APPLY, mem, "--lock-timeout", "0.5"],
                  json.dumps({"learnings": [{"text": "neu", "importance": 3}]}))
check(rc == 2 and "lock" in str(out.get("error", "")).lower(),
      f"apply_wrapup waits for the store lock and fails visibly (rc={rc}, {out.get('error')})")
check(sha(os.path.join(mem, "learnings/learnings.json")) == before, "... and writes nothing")

# parallel writers: no lost update, no duplicate id
mem = make_store()
procs = []
WORDS = ["Anker", "Bruecke", "Chargen", "Daemon", "Encoding", "Fixture", "Gate", "Hook"]
for i in range(8):
    # distinct texts: Jaccard >= 0.6 would (rightly) make them duplicates
    plan = json.dumps({"learnings": [{"text": f"{WORDS[i]} {WORDS[i]}-{i} braucht {WORDS[(i + 3) % 8]}x{i}",
                                      "importance": 3}]})
    p = subprocess.Popen([sys.executable, APPLY, mem], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, encoding="utf-8", env=ENV)
    procs.append((p, plan))
for p, plan in procs:
    p.stdin.write(plan)
    p.stdin.close()
for p, _ in procs:
    p.wait(timeout=60)
rows = load(mem, "learnings/learnings.json")
ids = [r["id"] for r in rows]
check(len(rows) == 3 + 8 and len(ids) == len(set(ids)),
      f"8 parallel apply runs: all 8 rows persisted, ids unique ({len(rows)} rows, {len(set(ids))} ids)")

# ============================================================ 2. ownership + validation
print("--- ownership and row validation")
import apply_wrapup as aw  # noqa: E402

for rel in ("learnings/learnings.json", "Learnings/Learnings.json", "learnings/../learnings/learnings.json"):
    try:
        aw._p("/tmp/x", rel)
        check(False, f"generic write path refuses {rel}")
    except aw.PlanError:
        check(True, f"generic write path refuses {rel} (owned by the learnings applier)")
for rel in ("Iterations/Errors.json", "PATTERNS/patterns.json", "Identity/Soul.md"):
    try:
        aw._p("/tmp/x", rel)
        check(False, f"case variant {rel} is refused")
    except aw.PlanError:
        check(True, f"case variant {rel} is refused")

for bad in (0, 6, "x", None, True):
    mem = make_store()
    before = sha(os.path.join(mem, "learnings/learnings.json"))
    rc, out = apply(mem, {"learnings": [{"text": "Neues Learning mit kaputter importance", "importance": bad}]})
    check(rc == 2 and sha(os.path.join(mem, "learnings/learnings.json")) == before,
          f"importance {bad!r} is rejected before the first byte (rc={rc})")
for field, val in (("tags", "kein-array"), ("derived_from", "kein-array"),
                   ("derived_from", [f"x{i}" for i in range(31)])):
    mem = make_store()
    rc, out = apply(mem, {"learnings": [{"text": "Neues Learning mit kaputtem Feld", "importance": 3, field: val}]})
    check(rc == 2, f"{field}={str(val)[:20]!r} is rejected (rc={rc})")

# legacy rows with None importance must NOT block a new plan
mem = make_store()
rows = load(mem, "learnings/learnings.json")
rows.append(learning("L13", "Altbestand ohne importance", importance=None))
put(mem, "learnings/learnings.json", rows)
rc, out = apply(mem, {"learnings": [{"text": "Ganz neues gueltiges Learning ueber Locks", "importance": 4}]})
check(rc == 0 and out["tally"]["learnings_added"] == 1,
      f"a legacy row with importance None does not block a new plan (rc={rc})")
rc, out = run([APPLY, mem, "--lint"])
check(rc == 0 and any("L13" in str(x) for x in out.get("lint", [])),
      "--lint reports the legacy row instead (report only)")

# ============================================================ 3. step 3a in code
print("--- learnings dedup in code (Step 3a)")
mem = make_store()
rc, out = apply(mem, {"date": "2026-10-08", "learnings": [
    {"text": "Live Eintrag zehn ueber Windows Pfade und das Encoding", "importance": 3},  # >= 0.6
    {"text": "Git Hooks brauchen pre-commit Messung der Laufzeit bei grossen Repos", "importance": 3},
]}, "--dry-run")
nd = out.get("near_duplicates") or {}
check(rc == 0 and out["tally"]["learnings_skipped_duplicate"] == 1,
      f"Jaccard >= 0.6 to a live entry is a duplicate, decided in code ({out.get('tally', {}).get('learnings_skipped_duplicate')})")
check(any(h["id"] == "L11" for hits in nd.values() for h in hits),
      f"dry-run lists near duplicates (0.2..0.6) for the model's judgement ({nd})")
rc, out = apply(mem, {"date": "2026-10-08", "learnings": [
    {"text": "Live Eintrag zehn ueber Windows Pfade und das Encoding", "importance": 3}]})
check(load(mem, "learnings/learnings.json")[0]["last_relevant"] == "2026-10-08",
      "the matched live entry gets last_relevant = plan date")
rc, out = apply(mem, {"date": "2026-10-09", "learnings": [
    {"text": "Git Hooks brauchen Messung", "importance": 3, "duplicate_of": "L11"}]})
rows = load(mem, "learnings/learnings.json")
check(rc == 0 and out["tally"]["learnings_added"] == 0
      and next(r for r in rows if r["id"] == "L11")["last_relevant"] == "2026-10-09",
      "duplicate_of: the model's verdict touches last_relevant and adds nothing")
rc, out = apply(mem, {"learnings": [{"text": "x y z", "importance": 3, "duplicate_of": "L404"}]})
check(rc == 2, "duplicate_of pointing at an unknown id is rejected")

# ============================================================ 7. review_sweep --charges
print("--- review_sweep --charges")
mem = make_store()
rows = load(mem, "learnings/learnings.json")
rows.append(learning("L60", "Mit Termin", review_after="2026-11-05"))
rows.append(learning("L61", "Zurueckgezogen", bridge_status="retired"))
rows.append(learning("L62", "Frueher zurueckgeholt", derived_from=["restored:learnings-archive-2026-07"]))
rows.append(learning("L63", "Abgeloest mit Beleg", superseded_by="fixed:5.0.1"))
put(mem, "learnings/learnings.json", rows)
rc, out = run([SWEEP, mem, "--charges", "--json"])
ch = {c["name"]: c for c in out.get("charges", [])}
restored = [n for n in ch if n.startswith("restored:")]
check(rc == 0 and len(restored) == 1 and ch[restored[0]]["ids"] == ["L62"],
      f"charge restored:<store> holds the row carrying restored: provenance ({ {k: v['ids'] for k, v in ch.items()} })")
check(sorted(ch.get("ohne-termin", {}).get("ids", [])) == ["L10", "L11", "L12"],
      "charge ohne-termin holds the live rows without review_after")
check(ch.get("due:2026-11", {}).get("ids") == ["L60"], "dated rows are charged by month")
check(not any(i in c["ids"] for c in ch.values() for i in ("L61", "L63")), "retired and superseded rows are in no charge")

# ============================================================ 8. store_snapshot.py
print("--- store_snapshot.py")
mem = make_store()
os.makedirs(os.path.join(mem, "identity"), exist_ok=True)
with open(os.path.join(mem, "identity", "user.md"), "w") as fh:
    fh.write("privat")
rc, out = run([SNAPSHOT, mem, "--keep", "2"])
snap = out.get("snapshot", "")
check(rc == 0 and os.path.isfile(os.path.join(snap, "learnings", "learnings.json")), f"snapshot taken ({rc}, {out})")
check(not os.path.exists(os.path.join(snap, "identity")), "identity is never snapshotted")
check(sha(os.path.join(snap, "learnings", "learnings-archive-2026-07.json"))
      == sha(os.path.join(mem, "learnings", "learnings-archive-2026-07.json")), "snapshot is byte-identical")
for _ in range(3):
    time.sleep(1.1)
    rc, out = run([SNAPSHOT, mem, "--keep", "2"])
parent = os.path.dirname(out["snapshot"])
check(len(os.listdir(parent)) == 2, f"retention keeps the newest {2} ({len(os.listdir(parent))})")

# ============================================================ 9. verifier regressions (54fd025)
print("--- verifier regressions")
# --render-learnings is a write: it waits for the lock
mem = make_store()
with store_lock.store_lock(mem):
    rc, out = run([APPLY, mem, "--render-learnings", "--lock-timeout", "0.3"])
check(rc == 2 and not os.path.exists(os.path.join(mem, "learnings", "learnings.md")),
      f"--render-learnings respects the store lock (rc={rc})")
# an invalid plan date is rejected before the first write
mem = make_store()
rc, out = apply(mem, {"date": "07.10.2026", "iterations": [{"type": "feature", "title": "x"}],
                      "learnings": [{"text": "Ein Learning mit kaputtem Plandatum", "importance": 3}]})
check(rc == 2 and not os.path.exists(os.path.join(mem, "iterations", "iteration-log.md")),
      f"non-ISO plan date: exit 2 before the first write (rc={rc})")

print(f"=== Results: {PASSED}/{TESTS} passed, {ERRORS} failures ===")
sys.exit(1 if ERRORS else 0)

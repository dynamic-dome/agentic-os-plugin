#!/usr/bin/env python3
"""Tests for the 5.3.0 write basis and archive restore (T-030, plan phase 1).

Covers: store lock, row validation, case-insensitive ownership, learnings
dedup in code (Step 3a), restore_plan.py (read-only report), the apply_wrapup
`restore` section, extract_patterns --restore-archive, review_sweep --charges
and store_snapshot.py.

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
RESTORE_PLAN = os.path.join(SCRIPTS, "restore_plan.py")
EXTRACT = os.path.join(SCRIPTS, "extract_patterns.py")
SWEEP = os.path.join(SCRIPTS, "review_sweep.py")
SNAPSHOT = os.path.join(SCRIPTS, "store_snapshot.py")
sys.path.insert(0, SCRIPTS)

SNAP_DIR = tempfile.mkdtemp(prefix="restore-test-snaps-")
ENV = dict(os.environ, AGENTIC_OS_SNAPSHOT_DIR=SNAP_DIR, PYTHONIOENCODING="utf-8")

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
    """A store whose archives hold every restore class the plan names."""
    mem = tempfile.mkdtemp(prefix="restore-test-")
    put(mem, "learnings/learnings.json", [
        learning("L10", "Live Eintrag zehn ueber Windows Pfade und Encoding"),
        learning("L11", "Live Eintrag elf ueber Git Hooks und pre-commit Laufzeit"),
        learning("L12", "Live Eintrag zwoelf ueber Atlas Index Rebuild nach Store Aenderung"),
    ])
    put(mem, "learnings/learnings-archive-2026-07.json", [
        learning("L1", "Archiv Kandidat eins: Subprocess unter Windows braucht shutil which"),
        learning("L2", "Archiv Kandidat zwei mit Float importance", importance=0.7),
        learning("L3", "live eintrag zehn ueber windows pfade und encoding"),          # dup text, other id
        learning("L4", "Live Eintrag elf ueber Git Hooks und pre-commit Laufzeit messen"),  # near dup (>= 0.8) of L11
        learning("L5", "Archiv Kandidat fuenf zeigt auf Nichts", superseded_by="L99"),  # dangling
        learning("L6", "Archiv Codex Tipp abgelehnt", bridge_status="rejected", source_agent="codex"),
        learning("L7", "Archiv Kandidat sieben ohne importance", importance=None),     # invalid
    ])
    # the misnamed family (maintain wrote it this way in 2026-08)
    put(mem, "learnings/learnings.json-archive-2026-08.json", [
        learning("L12", "Ganz anderer Text unter einer live vergebenen ID"),           # id collision
        learning("L8", "Archiv Kandidat acht abgeloest mit Beleg", superseded_by="fixed:5.0.1"),
    ])
    put(mem, "patterns/patterns.json", [
        {"id": "P007", "type": "pattern", "description": "live", "confidence": 0.5,
         "occurrences": 3, "last_seen": "2026-09-01", "evidence": []}])
    put(mem, "patterns/patterns-archive-2026-10.json", [
        {"id": "P008", "type": "pattern", "description": "ready", "promotion_status": "ready",
         "confidence": 0.85, "occurrences": 5, "last_seen": "2026-08-01", "evidence": ["err-1"],
         "archive_reason": "stale", "archived_at": "2026-10-06"},
        {"id": "P009", "type": "pattern", "description": "skill", "skill_candidate": True,
         "confidence": 0.4, "occurrences": 2, "last_seen": "2026-08-01", "evidence": []},
        {"id": "P010", "type": "pattern", "description": "low", "confidence": 0.3,
         "occurrences": 1, "last_seen": "2026-05-01", "evidence": []},
        {"id": "P011", "type": "pattern", "description": "referenced", "confidence": 0.5,
         "occurrences": 2, "last_seen": "2026-05-01", "evidence": []},
    ])
    # a live learning cites P011 -> protection class
    rows = load(mem, "learnings/learnings.json")
    rows[0]["derived_from"] = ["P011"]
    put(mem, "learnings/learnings.json", rows)
    os.makedirs(os.path.join(mem, "working"), exist_ok=True)
    return mem


def archive_hashes(mem):
    out = {}
    for fam in ("learnings", "patterns"):
        folder = os.path.join(mem, fam)
        for fn in sorted(os.listdir(folder)):
            if "archive" in fn:
                out[fn] = sha(os.path.join(folder, fn))
    return out


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
holder = subprocess.Popen([sys.executable, "-c", HOLD, SCRIPTS, mem], stdout=subprocess.PIPE, text=True)
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

# ============================================================ 4. restore_plan.py
print("--- restore_plan.py (read-only)")
mem = make_store()
hashes = archive_hashes(mem)
live_before = sha(os.path.join(mem, "learnings/learnings.json"))
rc, plan = run([RESTORE_PLAN, mem])
rep = plan.get("report", {}).get("learnings", {})
rows = plan.get("restore", {}).get("learnings", [])
ids = {r["id"] for r in rows}
check(rc == 0 and plan.get("ok"), f"restore_plan runs (rc={rc})")
check(archive_hashes(mem) == hashes and sha(os.path.join(mem, "learnings/learnings.json")) == live_before,
      "restore_plan writes nothing (archive + live hashes unchanged)")
check({"L1", "L2", "L4", "L8", "L12"} <= ids, f"candidates incl. near dup, collision, superseded-with-proof ({sorted(ids)})")
check("L3" not in ids and any(d["id"] == "L3" for d in rep.get("duplicate", [])),
      "same text under another live id -> duplicate, not restored")
check(any(d["id"] == "L4" and d["of"] == "L11" for d in rep.get("near_duplicate", [])),
      "near duplicate is restored but reported with its live twin")
check("L5" not in ids and any(d["id"] == "L5" for d in rep.get("dangling_superseded", [])),
      "dangling superseded_by goes to the report, the row is held back")
check("L6" not in ids and any(d["id"] == "L6" for d in rep.get("codex_excluded", [])),
      "codex rejected/retired stay in the archive")
check("L7" not in ids and any(d["id"] == "L7" for d in rep.get("invalid", [])),
      "an invalid row is dropped with a report line, the store is not blocked")
l2 = next(r for r in rows if r["id"] == "L2")
check(l2["importance"] == 4 and any("importance 0.7" in d for d in l2["derived_from"]),
      f"float importance 0.7 -> 4, original kept in derived_from ({l2.get('importance')}, {l2.get('derived_from')})")
check(all(any(str(d).startswith("restored:") for d in r["derived_from"]) for r in rows),
      "every restored row carries restored:<file> provenance")
check(all("review_after" not in r for r in rows), "no artificial review_after")
check(any(d["id"] == "L12" for d in rep.get("id_collision", [])), "id collision is reported")
pat = plan.get("report", {}).get("patterns", {})
check(sorted(pat.get("protected", [])) == ["P008", "P009", "P011"] and pat.get("report_only") == ["P010"],
      f"pattern protection class: ready / skill_candidate / referenced ({pat})")
check(sorted(plan.get("pattern_ids", [])) == ["P008", "P009", "P011"], "pattern_ids is the restore input")
rc, plan_skip = run([RESTORE_PLAN, mem, "--skip-ids", "L4"])
check("L4" not in {r["id"] for r in plan_skip["restore"]["learnings"]}, "--skip-ids deselects a row (owner gate)")

# ============================================================ 5. restore section
print("--- apply_wrapup restore section")
rc, out = apply(mem, plan, "--dry-run")
check(rc == 0 and sha(os.path.join(mem, "learnings/learnings.json")) == live_before,
      f"restore dry-run writes nothing (rc={rc})")
rc, out = apply(mem, plan)
rows = load(mem, "learnings/learnings.json")
by_id = {r["id"]: r for r in rows}
check(rc == 0 and out["tally"]["restored"] == 5, f"5 rows restored ({out.get('tally', {}).get('restored')}, {out.get('error')})")
check("L1" in by_id and by_id["L1"]["text"].startswith("Archiv Kandidat eins"), "free archive id is kept (citations stay valid)")
moved = [r for r in rows if any(d == "legacy:L12" for d in (r.get("derived_from") or []))]
check(len(moved) == 1 and moved[0]["id"] not in ("L12",) and by_id["L12"]["text"].startswith("Live Eintrag zwoelf"),
      f"id collision: new id + legacy:L12, the live L12 untouched ({[m['id'] for m in moved]})")
all_ids = [r["id"] for r in rows]
check(len(all_ids) == len(set(all_ids)), "ids unique after restore")
check(archive_hashes(mem) == hashes, "archives are never modified")
md = open(os.path.join(mem, "learnings/learnings.md"), encoding="utf-8").read()
check("Archiv Kandidat eins" in md, "learnings.md re-rendered")
snaps = [d for _r, ds, _f in os.walk(SNAP_DIR) for d in ds]
check(len(snaps) >= 1, "a snapshot was taken before the restore write")
before = sha(os.path.join(mem, "learnings/learnings.json"))
rc, out = apply(mem, plan)
check(rc == 0 and out["tally"]["restored"] == 0 and sha(os.path.join(mem, "learnings/learnings.json")) == before,
      "second run changes nothing (idempotent)")
# a broken row inside an otherwise valid restore is dropped, not fatal
mem2 = make_store()
rc, plan2 = run([RESTORE_PLAN, mem2])
plan2["restore"]["learnings"].append(learning("L50", "kaputt", importance=9, derived_from=["restored:x"]))
rc, out = apply(mem2, plan2)
check(rc == 0 and out["tally"]["restored"] == 5 and any("L50" in str(x) for x in out["tally"].get("restore_dropped", [])),
      "an invalid restore row is dropped with a report, the rest applies")

# ============================================================ 6. patterns --restore-archive
print("--- extract_patterns --restore-archive")
mem = make_store()
hashes = archive_hashes(mem)
rc, out = run([EXTRACT, mem, "--restore-archive", "--ids", "P008,P009"])
pats = load(mem, "patterns/patterns.json")
p8 = next((p for p in pats if p["id"] == "P008"), {})
check(rc == 0 and [p["id"] for p in pats] == ["P007", "P008", "P009"], f"protected patterns restored ({rc}, {out.get('error')})")
check(p8.get("confidence") == 0.85 and p8.get("occurrences") == 5 and p8.get("last_seen") == "2026-08-01",
      "numeric fields unchanged (D-013)")
check("archive_reason" not in p8 and "archived_at" not in p8 and p8.get("restored_from"),
      "archive stamps dropped, restored_from recorded")
check(archive_hashes(mem) == hashes, "pattern archives unmodified")
rc, out = run([EXTRACT, mem, "--restore-archive", "--ids", "P008"])
check(rc == 0 and len(load(mem, "patterns/patterns.json")) == 3, "restoring a live id again is a no-op")
rc, out = run([EXTRACT, mem, "--restore-archive", "--ids", "P404"])
check(rc == 2, "an unknown id is rejected")
rc, out = run([EXTRACT, mem, "--refresh"])
md = open(os.path.join(mem, "patterns/patterns.md"), encoding="utf-8").read()
check(rc == 0 and "P008" in md, "patterns.md shows the restored pattern")

# ============================================================ 7. review_sweep --charges
print("--- review_sweep --charges")
mem = make_store()
rc, plan = run([RESTORE_PLAN, mem])
apply(mem, plan)
rows = load(mem, "learnings/learnings.json")
rows.append(learning("L60", "Mit Termin", review_after="2026-11-05"))
rows.append(learning("L61", "Zurueckgezogen", bridge_status="retired"))
put(mem, "learnings/learnings.json", rows)
rc, out = run([SWEEP, mem, "--charges", "--json"])
ch = {c["name"]: c for c in out.get("charges", [])}
restored = [n for n in ch if n.startswith("restored:")]
check(rc == 0 and len(restored) == 1 and ch[restored[0]]["count"] == 4,
      f"charge restored:<store> holds the 5 restored rows minus L8, superseded with proof ({ {k: v['count'] for k, v in ch.items()} })")
check(ch.get("ohne-termin", {}).get("count") == 3, "charge ohne-termin holds the live rows without review_after")
check(ch.get("due:2026-11", {}).get("count") == 1, "dated rows are charged by month")
check(not any("L61" in c["ids"] for c in ch.values()), "retired rows are in no charge")

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

# ============================================================ 9. Codex verifier round 1 (54fd025)
print("--- verifier regressions")
# --render-learnings is a write: it waits for the lock
mem = make_store()
with store_lock.store_lock(mem):
    rc, out = run([APPLY, mem, "--render-learnings", "--lock-timeout", "0.3"])
check(rc == 2 and not os.path.exists(os.path.join(mem, "learnings", "learnings.md")),
      f"--render-learnings respects the store lock (rc={rc})")
# an unreadable archive stops a restore plan BEFORE iterations are written
mem = make_store()
rc, plan = run([RESTORE_PLAN, mem])
with open(os.path.join(mem, "learnings", "learnings-archive-2026-09.json"), "w") as fh:
    fh.write("{kaputt")
plan["iterations"] = [{"type": "feature", "title": "Darf nicht geschrieben werden"}]
rc, out = apply(mem, plan)
check(rc == 2 and not os.path.exists(os.path.join(mem, "iterations", "iteration-log.md")),
      f"restore + broken archive: exit 2 before the iteration log is written (rc={rc})")
# an invalid plan date is rejected before the first write
mem = make_store()
rc, out = apply(mem, {"date": "07.10.2026", "iterations": [{"type": "feature", "title": "x"}],
                      "learnings": [{"text": "Ein Learning mit kaputtem Plandatum", "importance": 3}]})
check(rc == 2 and not os.path.exists(os.path.join(mem, "iterations", "iteration-log.md")),
      f"non-ISO plan date: exit 2 before the first write (rc={rc})")
# superseded_by: a dropped successor drops its predecessor; a renumbered one is followed
mem = make_store()
rc, out = apply(mem, {"restore": {"learnings": [
    learning("L30", "Vorgaenger zeigt auf verworfene Zeile", superseded_by="L31", derived_from=["restored:t"]),
    learning("L31", "Verworfen wegen importance", importance=7, derived_from=["restored:t"]),
    learning("L32", "Vorgaenger zeigt auf kollidierende Zeile", superseded_by="L12", derived_from=["restored:t"]),
    learning("L12", "Kollidierender Nachfolger mit anderem Text", derived_from=["restored:t"]),
]}})
rows = {r["id"]: r for r in load(mem, "learnings/learnings.json")}
moved = next((r for r in rows.values() if "legacy:L12" in (r.get("derived_from") or [])), {})
dropped = {d["id"] for d in out.get("tally", {}).get("restore_dropped", [])}
check(rc == 0 and {"L30", "L31"} <= dropped and "L30" not in rows,
      f"a row pointing at a dropped successor is dropped too ({sorted(dropped)})")
check(rows.get("L32", {}).get("superseded_by") == moved.get("id") and moved.get("id") not in (None, "L12"),
      f"a pointer at a renumbered successor follows the new id ({rows.get('L32', {}).get('superseded_by')} -> {moved.get('id')})")
# round 2: a dropped successor whose id is ALSO live must not resolve to the foreign live row
mem = make_store()
rc, out = apply(mem, {"restore": {"learnings": [
    learning("L40", "Zeigt auf archivierte L12", superseded_by="L12", derived_from=["restored:t"]),
    learning("L12", "Archivierte L12 kaputt", importance=None, derived_from=["restored:t"]),
]}})
rows = {r["id"]: r for r in load(mem, "learnings/learnings.json")}
check(rc == 0 and "L40" not in rows, "dropped colliding successor: predecessor dropped, not re-pointed to live L12")

# a no-op restore takes no snapshot; the snapshot holds the PRE-restore bytes
snapdir = tempfile.mkdtemp(prefix="restore-test-snap2-")
env2 = dict(ENV, AGENTIC_OS_SNAPSHOT_DIR=snapdir)
mem = make_store()
pre = sha(os.path.join(mem, "learnings", "learnings.json"))
rc, plan = run([RESTORE_PLAN, mem])
subprocess.run([sys.executable, APPLY, mem], input=json.dumps(plan), capture_output=True, text=True,
               encoding="utf-8", env=env2)
snaps = [os.path.join(b, d) for b, ds, _f in os.walk(snapdir) for d in ds if d[:8].isdigit()]
check(len(snaps) == 1 and sha(os.path.join(snaps[0], "learnings", "learnings.json")) == pre,
      "the snapshot holds learnings.json as it was BEFORE the restore")
subprocess.run([sys.executable, APPLY, mem], input=json.dumps(plan), capture_output=True, text=True,
               encoding="utf-8", env=env2)
snaps2 = [d for b, ds, _f in os.walk(snapdir) for d in ds if d[:8].isdigit()]
check(len(snaps2) == 1, f"a second (no-op) restore takes no snapshot ({len(snaps2)})")

print(f"=== Results: {PASSED}/{TESTS} passed, {ERRORS} failures ===")
sys.exit(1 if ERRORS else 0)

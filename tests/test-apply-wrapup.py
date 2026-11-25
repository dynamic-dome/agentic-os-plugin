#!/usr/bin/env python3
"""Tests for scripts/apply_wrapup.py — batch writer for the wrap-up skill.

Every case runs against a throwaway memory dir under tempfile.mkdtemp().
The real .agent-memory is never touched.

Exit codes: 0 = all pass, 1 = failures found.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "apply_wrapup.py")

from _isolated_home import process_env  # noqa: E402

# Subprozesse laufen mit frischem Temp-Home statt mit dem echten Home (P0, tests/_isolated_home.py)
ENV, ISO_HOME = process_env()

TESTS = PASSED = ERRORS = 0


def pass_(msg):
    global TESTS, PASSED
    TESTS += 1
    PASSED += 1
    print(f"  PASS: {msg}")


def fail(msg):
    global TESTS, ERRORS
    TESTS += 1
    ERRORS += 1
    print(f"  FAIL: {msg}")


def check(cond, msg):
    pass_(msg) if cond else fail(msg)


def make_mem() -> str:
    mem = tempfile.mkdtemp(prefix="wrapup-test-")
    for d in ("learnings", "working", "identity", "context"):
        os.makedirs(os.path.join(mem, d), exist_ok=True)
    put(mem, "learnings/learnings.json", [{
        "id": "L1", "date": "2026-01-01", "text": "Bestehendes Learning",
        "importance": 4, "tags": ["x"], "layer": "short-term",
        "superseded_by": None, "last_relevant": "2026-01-01",
    }])
    put(mem, "working/user-candidates.json", [
        {"id": "UC1", "key": "reif", "observation": "Reifer Kandidat", "status": "inferred",
         "signal_type": "preference", "confidence": 0.7, "occurrences": 2,
         "evidence": ["s1"], "first_seen": "2026-07-01", "last_seen": "2026-07-01",
         "trust_source": "conversation"},
        {"id": "UC2", "key": "unreif", "observation": "Unreifer Kandidat", "status": "inferred",
         "signal_type": "preference", "confidence": 0.3, "occurrences": 1,
         "evidence": ["s1"], "first_seen": "2026-07-01", "last_seen": "2026-07-01",
         "trust_source": "conversation"},
    ])
    put(mem, "context/open-tasks.json", [{
        "id": "T-001", "title": "Alte Aufgabe", "status": "open", "created": "2026-07-01",
        "updated": "2026-07-01", "resolution": None, "source": "x", "cross_project": False,
    }])
    put(mem, "identity/user-changelog.json", [])
    write(mem, "identity/user.md",
          "# User Profile\n\n## Preferences\n\n- Handgeschriebene Zeile\n\n"
          "## Work Style\n\n## Known Corrections\n")
    # Fixtures in the REAL on-disk shapes (2026-07-27 drift audit): errors use
    # "err-00n" and decisions "D-00n", NOT the "E{n}"/"D{n}" the old SKILL.md
    # templates claimed. The id format is therefore detected, never assumed.
    put(mem, "iterations/errors.json", [{
        "id": "err-001", "date": "2026-07-01", "category": "import",
        "tags": ["python", "import-error", "circular-import"],
        "trigger": "pytest", "problem": "Zirkulaerer Import", "root_cause": "Modul A imports B",
        "fix": "Lazy import", "severity": "major", "occurrences": 1,
        "recurrence_dates": [], "last_seen": "2026-07-01",
    }])
    write(mem, "iterations/iteration-log.md",
          "# Iteration Log\n\n## 2026-07-01 — feature: Etwas Altes\n"
          "- **Type:** feature\n- **Tags:** alt, bestand\n- **Summary:** Vorher da.\n")
    put(mem, "context/decisions.json", [{
        "id": "D-001", "date": "2026-07-01", "type": "architecture-decision",
        "title": "Alte Entscheidung", "status": "active", "context": "c",
        "options_considered": [], "decision": "d", "consequences": "k",
        "supersedes": None, "tags": ["alt"],
    }])
    put(mem, "working/current-session.json", {
        "session_start": "2026-07-27T09:00:00", "errors_this_session": [],
        "learnings_draft": [],
    })
    # Files owned by OTHER skills / other scripts - must stay byte-identical.
    put(mem, "patterns/patterns.json", [{"id": "P001"}])
    write(mem, "patterns/patterns.md", "# Pattern Catalog\n")
    write(mem, "identity/soul.md", "# Soul\n\n- unantastbar\n")
    put(mem, "working/dirty-sess-A.json", {
        "session_id": "sess-A", "agent": "main", "dirty": True,
        "started": "2026-07-27T10:00:00", "updated": "2026-07-27T11:00:00",
        "touched_files": ["a.py", "b.py"], "write_count": 2,
        "consolidated_at": None, "consolidated_by": None,
    })
    return mem


def put(mem, rel, data):
    write(mem, rel, json.dumps(data, indent=2) + "\n")


def write(mem, rel, text):
    path = os.path.join(mem, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def read(mem, rel):
    with open(os.path.join(mem, rel), encoding="utf-8") as fh:
        return fh.read()


def load(mem, rel):
    return json.loads(read(mem, rel))


LEARNINGS_MD_HEADER = "*Auto-generated from learnings.json — do not edit directly.*"


def snapshot(mem):
    files = {}
    for base, _dirs, names in os.walk(mem):
        for n in names:
            p = os.path.join(base, n)
            rel = os.path.relpath(p, mem).replace(os.sep, "/")
            if rel == "working/store.lock":
                continue  # OS-lock handle file (5.3.0): infrastructure, not store data
            with open(p, "rb") as fh:
                files[rel] = fh.read()
    return files


def run(mem, plan, *extra):
    proc = subprocess.run(
        [sys.executable, SCRIPT, mem, "--session-id", "sess-A", *extra],
        input=json.dumps(plan), capture_output=True, text=True, encoding="utf-8", env=ENV,
    )
    try:
        return proc.returncode, json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.returncode, {"_stdout": proc.stdout, "_stderr": proc.stderr}


print("=== apply_wrapup tests ===")

if not os.path.isfile(SCRIPT):
    fail("scripts/apply_wrapup.py missing")
    print(f"=== Results: {PASSED}/{TESTS} passed, {ERRORS} failures ===")
    sys.exit(1)
pass_("script exists")

# --- 1. learnings: append, dedup, projection --------------------------------
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27", "learnings": [
    {"text": "Neues Learning", "importance": 5, "tags": ["t"]},
    {"text": "  bestehendes   LEARNING ", "importance": 3},  # >= 3: since 5.11.0 lower ones are not stored at all
]})
check(rc == 0, "exit 0 on a valid plan")
check(out["tally"]["learnings_added"] == 1, "new learning appended")
check(out["tally"]["learnings_skipped_duplicate"] == 1,
      "duplicate learning skipped (whitespace/case-insensitive)")
rows = load(mem, "learnings/learnings.json")
new = [r for r in rows if r["id"] == "L2"][0]
check(new["review_after"] == "2026-10-25", "review_after = date + 90 days")
check(new["layer"] == "short-term" and new["superseded_by"] is None,
      "learning defaults match the schema")
md = read(mem, "learnings/learnings.md")
check("L2" in md and "L1" in md, "learnings.md is a full projection of learnings.json")
check(md.index("## Importance 5") < md.index("## Importance 4"),
      "learnings.md sorted by importance descending")
check(md.startswith("# Learnings\n\n" + LEARNINGS_MD_HEADER + "\n"),
      "learnings.md opens with the header maintain 5.2 checks for")
check(new["bridge_status"] == "candidate",
      "importance >= 4 derives bridge_status=candidate (Step 3d.1)")

# --- 1-summary. optional short form for the projections (additive field) ------
mem = make_mem()
rc, out = run(mem, {"date": "2026-10-01", "learnings": [
    {"text": "Langer Kontext zuerst, dann die eigentliche Regel am Ende.", "importance": 4,
     "summary": "  Regel:\n immer  zuerst messen. "},
    {"text": "Ohne Kurzfassung", "importance": 3},
    {"text": "Zu lange Kurzfassung", "importance": 3, "summary": "s" * 151},
]})
rows = {r["id"]: r for r in load(mem, "learnings/learnings.json")}
check(rc == 0 and rows["L2"].get("summary") == "Regel: immer zuerst messen.",
      f"summary stored whitespace-collapsed ({rows.get('L2', {}).get('summary')!r})")
check("summary" not in rows["L3"], "no summary in the plan -> no field (additive, never backfilled)")
check(rows["L4"].get("summary") == "s" * 151
      and any("L4" in w and "summary" in w for w in out["tally"]["warnings"]),
      f"overlong summary kept but warned, run not aborted ({out['tally']['warnings']})")

# --- 1a. --render-learnings: maintain 5.2 regenerates without a plan ----------
mem = make_mem()
write(mem, "learnings/learnings.md", "# Learnings\n\n- handgebaut, ohne Kopf\n")
before = snapshot(mem)
proc = subprocess.run([sys.executable, SCRIPT, mem, "--render-learnings"],
                      input="", capture_output=True, text=True, encoding="utf-8", env=ENV)
md = read(mem, "learnings/learnings.md")
check(proc.returncode == 0 and md.startswith("# Learnings\n\n" + LEARNINGS_MD_HEADER + "\n")
      and "**L1**" in md and "handgebaut" not in md,
      f"--render-learnings rebuilds learnings.md from learnings.json (rc={proc.returncode}, {proc.stderr.strip()[:120]})")
after = snapshot(mem)
check({k: v for k, v in after.items() if k != "learnings/learnings.md"}
      == {k: v for k, v in before.items() if k != "learnings/learnings.md"},
      "--render-learnings touches nothing but learnings.md (no marker, no dirty flags)")
# A render-only run must not quarantine the source or render an empty projection
# (Codex verifier P1): corrupt or missing learnings.json -> exit 2, store unchanged.
for case, setup_src in (("corrupt", lambda m: write(m, "learnings/learnings.json", "{oops")),
                        ("missing", lambda m: os.remove(os.path.join(m, "learnings", "learnings.json"))),
                        ("non-object rows", lambda m: write(m, "learnings/learnings.json", "[1]"))):
    mem = make_mem()
    setup_src(mem)
    before = snapshot(mem)
    proc = subprocess.run([sys.executable, SCRIPT, mem, "--render-learnings"],
                          input="", capture_output=True, text=True, encoding="utf-8", env=ENV)
    check(proc.returncode == 2 and snapshot(mem) == before,
          f"--render-learnings with {case} learnings.json: exit 2, nothing renamed or written (rc={proc.returncode})")

# --- 1b. bridge_status: derivation + trust boundary --------------------------
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27", "learnings": [
    {"text": "Unwichtiges Detail", "importance": 3},
    {"text": "Schmuggelversuch", "importance": 3, "bridge_status": "approved"},
]})
rows = load(mem, "learnings/learnings.json")
low = [r for r in rows if r["text"] == "Unwichtiges Detail"][0]
check("bridge_status" not in low,
      "importance < 4 gets NO bridge_status field (additive, never backfill)")
smuggled = [r for r in rows if r["text"] == "Schmuggelversuch"][0]
check("bridge_status" not in smuggled,
      "plan-supplied bridge_status is ignored (gate bypass defense)")
check(out["tally"]["bridge_candidates"] == [],
      "tally lists no bridge candidates when none exist store-wide")
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27", "learnings": [
    {"text": "Wichtige Erkenntnis", "importance": 4},
]})
check(out["tally"]["bridge_candidates"] == ["L2"],
      "tally lists store-wide candidates for Step 3d.2")

# Codex review 2026-07-27: importance:"high" raised an uncaught ValueError
# mid-run (traceback instead of the fail-soft JSON contract).
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27", "learnings": [
    {"text": "Kaputte Importance", "importance": "high"},
]})
check(rc == 2 and out.get("ok") is False and "importance" in out.get("error", ""),
      "non-numeric importance rejects the plan as JSON, not a traceback")
check(load(mem, "learnings/learnings.json") == [
    {"id": "L1", "date": "2026-01-01", "text": "Bestehendes Learning",
     "importance": 4, "tags": ["x"], "layer": "short-term",
     "superseded_by": None, "last_relevant": "2026-01-01"}],
      "rejected importance plan writes nothing")
rows = load(mem, "learnings/learnings.json")
old = [r for r in rows if r["id"] == "L1"][0]
check("bridge_status" not in old,
      "pre-existing imp-4 entry without the field is never backfilled")

# --- 1c. bridge_decisions: delegated gate (5.7.0, Owner-Entscheid 2026-10-16) --
def bridge_mem():
    mem = make_mem()
    put(mem, "learnings/learnings.json", [
        {"id": "L1", "date": "2026-01-01", "text": "Alt ohne Feld", "importance": 4, "tags": [],
         "layer": "short-term", "superseded_by": None, "last_relevant": "2026-01-01"},
        {"id": "L2", "date": "2026-10-01", "text": "Kandidat A", "importance": 4, "tags": [],
         "layer": "short-term", "superseded_by": None, "last_relevant": "2026-10-01",
         "bridge_status": "candidate"},
        {"id": "L3", "date": "2026-10-01", "text": "Kandidat B", "importance": 5, "tags": [],
         "layer": "short-term", "superseded_by": None, "last_relevant": "2026-10-01",
         "bridge_status": "candidate"},
        {"id": "L4", "date": "2026-10-01", "text": "Zurueckgezogen", "importance": 2, "tags": [],
         "layer": "short-term", "superseded_by": None, "last_relevant": "2026-10-01",
         "bridge_status": "retired", "source_agent": "codex"},
        {"id": "L5", "date": "2026-10-01", "text": "Abgeloest", "importance": 4, "tags": [],
         "layer": "short-term", "superseded_by": "L3", "last_relevant": "2026-10-01",
         "bridge_status": "candidate"},
        {"id": "L6", "date": "2026-10-01", "kind": "principle", "text": "Regel → x.md#y",
         "summary": "Regel", "anchor": "x.md#y", "importance": 4, "tags": [], "layer": "long-term",
         "superseded_by": None, "last_relevant": "2026-10-01", "derived_from": ["L2"],
         "bridge_status": "approved"},
    ])
    return mem


# P1/B2: every owner-facing proposal carries `plain` (plain German, <= 600 characters).
PLAIN = "Das gilt in jedem Projekt und jeder Sitzung, nicht nur hier."
DECIDE = {"date": "2026-10-16", "bridge_decisions": [
    {"id": "L2", "status": "approved", "note": "Gilt  projektuebergreifend,\n  jede Session.", "plain": PLAIN},
    {"id": "L3", "status": "rejected", "note": "Nur fuer dieses Repo relevant.",
     "plain": "Das betrifft nur dieses eine Projekt."},
]}
mem = bridge_mem()
rc, out = run(mem, DECIDE)
by_id = {r["id"]: r for r in load(mem, "learnings/learnings.json")}
check(rc == 0 and by_id["L2"]["bridge_status"] == "approved" and by_id["L3"]["bridge_status"] == "rejected",
      f"bridge_decisions sets approved/rejected (rc={rc}, {out.get('error')})")
check(by_id["L2"].get("bridge_note") == "2026-10-16 agent: Gilt projektuebergreifend, jede Session."
      and by_id["L3"].get("bridge_note") == "2026-10-16 agent: Nur fuer dieses Repo relevant.",
      f"the reason lands on the object as one line '<date> <by>: <note>' ({by_id['L2'].get('bridge_note')!r})")
check(by_id["L2"].get("bridge_plain") == PLAIN and by_id["L3"].get("bridge_plain") == "Das betrifft nur dieses eine Projekt.",
      f"the plain-language text lands in its own field bridge_plain ({by_id['L2'].get('bridge_plain')!r})")
t = out.get("tally", {})
check(t.get("bridge_approved") == ["L2"] and t.get("bridge_rejected") == ["L3"]
      and t.get("bridge_status_changed") == 2,
      f"tally reports the decisions and the status changes ({t.get('bridge_approved')}, {t.get('bridge_status_changed')})")
check(t.get("bridge_candidates") == [],
      "tally.bridge_candidates is recomputed AFTER the decisions (superseded L5 is not live)")
check(out.get("bridge_status_line", "").startswith("Bridge: 1 approved, 1 rejected"),
      f"a bridge status line is returned for the mandatory report ({out.get('bridge_status_line')!r})")
check(by_id["L4"]["bridge_status"] == "retired" and by_id["L6"]["bridge_status"] == "approved"
      and "bridge_status" not in by_id["L1"],
      "rows not named in the plan are untouched")
before = snapshot(mem)
rc, out = run(mem, DECIDE)
check(rc == 0 and snapshot(mem) == before and out["tally"]["bridge_status_changed"] == 0,
      "a second identical run changes nothing (idempotent)")
# Codex F1: the same verdict and reason on a LATER day is the same decision - the
# original decision date must survive, nothing is rewritten.
rc, out = run(mem, {**DECIDE, "date": "2026-10-20"})
check(rc == 0 and snapshot(mem) == before,
      "the same verdict + reason on a later day rewrites nothing (decision date kept)")
# owner override = rollback: the same section, `by: owner`, no special path
rc, out = run(mem, {"date": "2026-10-17", "bridge_decisions": [
    {"id": "L2", "status": "rejected", "note": "Dominic: doch nicht", "by": "owner", "plain": PLAIN}]})
row = {r["id"]: r for r in load(mem, "learnings/learnings.json")}["L2"]
check(rc == 0 and row["bridge_status"] == "rejected" and row["bridge_note"] == "2026-10-17 owner: Dominic: doch nicht"
      and out["tally"]["bridge_status_changed"] == 1,
      "an earlier decision can be turned back (owner override), marked 'owner'")
# note-only change: written, but no status change -> projections not needed
rc, out = run(mem, {"date": "2026-10-17", "bridge_decisions": [
    {"id": "L3", "status": "rejected", "note": "Neu begruendet", "plain": "Das betrifft nur dieses eine Projekt."}]})
row = {r["id"]: r for r in load(mem, "learnings/learnings.json")}["L3"]
check(rc == 0 and row["bridge_note"] == "2026-10-17 agent: Neu begruendet" and out["tally"]["bridge_status_changed"] == 0,
      "a new reason for the same status updates the note, bridge_status_changed stays 0")
# dry run: full tally, no byte
mem = bridge_mem()
before = snapshot(mem)
rc, out = run(mem, DECIDE, "--dry-run")
check(rc == 0 and snapshot(mem) == before and out["tally"]["bridge_status_changed"] == 2,
      "--dry-run reports the decisions without writing")
# no section -> no status line (the night run and /agentic-os:log stay silent)
rc, out = run(bridge_mem(), {"date": "2026-10-16"})
check(rc == 0 and "bridge_status_line" not in out and out["tally"]["bridge_candidates"] == ["L2", "L3"],
      "without the section nothing is decided and no bridge line is returned")

# every malformed decision rejects the WHOLE plan before the first byte
BAD = [
    ("missing note", {"id": "L2", "status": "approved"}),
    ("blank note", {"id": "L2", "status": "approved", "note": "  \n "}),
    ("non-string note", {"id": "L2", "status": "approved", "note": 5}),
    ("status candidate", {"id": "L2", "status": "candidate", "note": "x"}),
    ("status retired", {"id": "L2", "status": "retired", "note": "x"}),
    ("unknown id", {"id": "L99", "status": "approved", "note": "x"}),
    ("id without bridge_status", {"id": "L1", "status": "approved", "note": "x"}),
    ("retired row", {"id": "L4", "status": "approved", "note": "x"}),
    ("superseded row", {"id": "L5", "status": "approved", "note": "x"}),
    ("principle pointer", {"id": "L6", "status": "rejected", "note": "x"}),
    ("unknown decider", {"id": "L2", "status": "approved", "note": "x", "by": "codex"}),
    ("not an object", "L2"),
]
for case, item in BAD:
    if isinstance(item, dict):
        item = {"plain": PLAIN, **item}  # each case must fail for the reason it names, not for a missing plain
    mem = bridge_mem()
    before = snapshot(mem)
    rc, out = run(mem, {"date": "2026-10-16", "iterations": [{"title": "Vorher geschrieben?"}],
                        "bridge_decisions": [item]})
    check(rc == 2 and out.get("ok") is False and snapshot(mem) == before,
          f"bridge_decisions with {case}: plan rejected, nothing written (rc={rc}, {out.get('error')})")
mem = bridge_mem()
before = snapshot(mem)
rc, out = run(mem, {"date": "2026-10-16", "bridge_decisions": [
    {"id": "L2", "status": "approved", "note": "a"}, {"id": "L2", "status": "rejected", "note": "b"}]})
check(rc == 2 and snapshot(mem) == before, "the same id twice in one plan is rejected")
rc, out = run(mem, {"date": "2026-10-16", "bridge_decisions": {"L2": "approved"}})
check(rc == 2 and snapshot(mem) == before, "bridge_decisions must be a list")

# --- 5. other skills' files are untouchable ---------------------------------
mem = make_mem()
before = {f: read(mem, f) for f in ("iterations/errors.json", "patterns/patterns.json",
                                    "context/decisions.json", "identity/soul.md")}
run(mem, {"date": "2026-07-27", "learnings": [{"text": "irgendwas", "importance": 3}],
          "soul_candidates": [{"proposal": "Ein Vorschlag", "evidence": ["e"]}]})
check(all(read(mem, f) == b for f, b in before.items()),
      "errors.json / patterns.json / decisions.json / soul.md stay byte-identical")
check(os.path.exists(os.path.join(mem, "identity/soul-candidates.md")),
      "soul proposals go to soul-candidates.md, never soul.md")

# --- 6. open tasks -----------------------------------------------------------
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27", "open_tasks": {
    "add": [{"title": "Neue Aufgabe"}, {"title": "Alte Aufgabe"}], "close": ["T-001"]}})
tasks = {t["title"]: t for t in load(mem, "context/open-tasks.json")}
check(out["tally"]["tasks_added"] == 1 and out["tally"]["tasks_skipped_duplicate"] == 1,
      "duplicate open task title is skipped")
check(tasks["Alte Aufgabe"]["status"] == "closed", "close list closes the task")
check(tasks["Neue Aufgabe"]["id"] == "T-002", "new task id keeps the T-00n format")

# --- 7. consolidation marker + dirty reset ----------------------------------
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27", "consolidate": True, "iterations_logged": 3})
marker = load(mem, "consolidation-marker.json")
check(marker["consolidated_sessions"] == ["sess-A"], "marker records the consolidated session")
check(marker["touched_files_seen"] == 2, "marker counts touched files from the dirty file")
check(marker["iterations_logged"] == 3, "marker carries iterations_logged from the plan")
d = load(mem, "working/dirty-sess-A.json")
check(d["dirty"] is False and d["consolidated_by"] == "wrap-up", "dirty flag flipped, file kept")
check(d["last_consolidated_by"] == "wrap-up" and marker.get("mode") == "wrap-up",
      "interactive run: dirty file and marker name the wrap-up mode")
# 5.14.0 Codex V2-01b: a valid JSON marker whose touched_files is not a list never breaks the consolidation
mem = make_mem()
broken = load(mem, "working/dirty-sess-A.json")
broken["touched_files"] = 7
put(mem, "working/dirty-sess-A.json", broken)
rc, out = run(mem, {"date": "2026-07-27", "consolidate": True})
check(rc == 0 and load(mem, "consolidation-marker.json")["touched_files_seen"] == 0
      and load(mem, "working/dirty-sess-A.json")["dirty"] is False,
      "touched_files not a list: consolidation runs, counts 0 files")

# --- 7b. headless consolidation says so (Lebenszyklus Phase 2, slice 1) -----
# The night run used to stamp "wrap-up" although no wrap-up ran; GC and readers
# could not tell a judged session from a mechanically harvested one.
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("apply_wrapup_by", SCRIPT)
_aw = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_aw)
mem = make_mem()
_aw.apply_consolidation(mem, {"consolidate": True}, "headless-2026-10-11", False, [],
                        {"iterations_logged": 0, "learnings_added": 0}, by="headless")
d = load(mem, "working/dirty-sess-A.json")
marker = load(mem, "consolidation-marker.json")
check(d["dirty"] is False and d["consolidated_by"] == "headless" and d["last_consolidated_by"] == "headless",
      "headless consolidation stamps 'headless', not 'wrap-up'")
check(marker.get("mode") == "headless", "marker names the headless mode")
check("harvest" not in d, "headless consolidation never closes the harvest")

# --- 7c. a judged wrap-up closes ONLY its own session (D-024) ----------------
# Another session's dirty file is consolidated (its git work is harvested) but its
# conversation was never judged - it stays pending until an E7 rule closes it.
_hs_spec = _ilu.spec_from_file_location("harvest_state_t", os.path.join(os.path.dirname(SCRIPT), "harvest_state.py"))
_hs = _ilu.module_from_spec(_hs_spec)
_hs_spec.loader.exec_module(_hs)
mem = make_mem()
put(mem, "working/dirty-sess-B.json", {
    "session_id": "sess-B", "agent": "main", "dirty": True,
    "started": "2026-07-26T10:00:00", "updated": "2026-07-26T11:00:00",
    "touched_files": ["c.py"], "write_count": 1, "consolidated_at": None, "consolidated_by": None,
})
rc, out = run(mem, {"date": "2026-07-27", "consolidate": True})
own, other = load(mem, "working/dirty-sess-A.json"), load(mem, "working/dirty-sess-B.json")
check(rc == 0 and (own.get("harvest") or {}).get("closed_by") == "wrap-up"
      and own["harvest"].get("run") == "wrap-up" and _hs.marker_state(own) == "closed",
      "own session: judged wrap-up closes the harvest (snapshot valid)")
check(other["dirty"] is False and "harvest" not in other and _hs.marker_state(other) == "pending",
      "foreign session: consolidated, but the harvest stays pending")
mem = make_mem()
_aw.apply_consolidation(mem, {"consolidate": True}, "sess-A", False, [],
                        {"iterations_logged": 0, "learnings_added": 0}, by="headless")
check("harvest" not in load(mem, "working/dirty-sess-A.json"),
      "headless never closes, even when handed the session's real id")
mem = make_mem()
try:
    _aw.apply_consolidation(mem, {"consolidate": True}, "x", False, [],
                            {"iterations_logged": 0, "learnings_added": 0}, by="nightly")
    fail("unknown consolidation mode must raise")
except _aw.PlanError:
    pass_("unknown consolidation mode is a PlanError")
check(not os.path.exists(os.path.join(mem, "consolidation-marker.json"))
      and load(mem, "working/dirty-sess-A.json")["dirty"] is True,
      "unknown mode: nothing written before the refusal")

# --- 7c. AI workspace: the local summary IS the central handoff (DCO #9433) ---
# Overwriting it dropped the other projects' handoff blocks (2026-09-24). The
# central block arrives via Step 7.6a / write_central (prepend + demote) instead.
HANDOFF = ("# Letzte Session\n\n*Datum: 2026-10-10 15:30*\n*Projekt: projB*\n\n- fremder Block B\n\n---\n\n"
           "# Vorherige Session (2026-10-09 projC, erhalten)\n\n*Projekt: projC*\n\n- fremder Block C\n")
SUMMARY_PLAN = {"date": "2026-10-11", "session_summary": {"what_was_done": ["x"], "statistics": {}}}
central = tempfile.mkdtemp(prefix="wrapup-central-")
mem = os.path.join(central, ".agent-memory")
shutil.copytree(make_mem(), mem)
write(mem, "session-summary.md", HANDOFF)
rc, out = run(mem, SUMMARY_PLAN, "--central-dir", central)
check(rc == 0 and read(mem, "session-summary.md") == HANDOFF,
      "store == central: the central handoff stays byte-identical")
check(any("central handoff" in w for w in out.get("tally", {}).get("warnings", [])),
      "store == central: the skipped summary is reported, never silent")
mem = make_mem()
write(mem, "session-summary.md", HANDOFF)
rc, out = run(mem, SUMMARY_PLAN, "--central-dir", tempfile.mkdtemp(prefix="wrapup-central-"))
check(rc == 0 and read(mem, "session-summary.md").startswith("# Last Session"),
      "control: an ordinary store still gets its local summary")
# the comparison is a path test (case, '..', slash mix), never a string or prefix test
central = tempfile.mkdtemp(prefix="wrapup-central-")
mem = os.path.join(central, ".agent-memory")
shutil.copytree(make_mem(), mem)
write(mem, "session-summary.md", HANDOFF)
variant = os.path.join(central, "x", "..").replace("\\", "/")
if os.name == "nt":
    variant = variant.upper()
rc, out = run(mem, SUMMARY_PLAN, "--central-dir", variant)
check(rc == 0 and read(mem, "session-summary.md") == HANDOFF,
      "store == central written differently (case, '..', '/'): still protected")
nested = os.path.join(central, "projX", ".agent-memory")  # a project store BELOW the central dir
shutil.copytree(make_mem(), nested)
write(nested, "session-summary.md", HANDOFF)
rc, out = run(nested, SUMMARY_PLAN, "--central-dir", central)
check(rc == 0 and read(nested, "session-summary.md").startswith("# Last Session"),
      "control: a store below the central dir is not the central handoff")
# the default (~/AI, no --central-dir) is what the skill uses; prove it on a fake home
fake_home = tempfile.mkdtemp(prefix="wrapup-home-")
mem = os.path.join(fake_home, "AI", ".agent-memory")
shutil.copytree(make_mem(), mem)
write(mem, "session-summary.md", HANDOFF)
env = dict(ENV, USERPROFILE=fake_home, HOME=fake_home)
proc = subprocess.run([sys.executable, SCRIPT, mem, "--session-id", "sess-A"], input=json.dumps(SUMMARY_PLAN),
                      capture_output=True, text=True, encoding="utf-8", env=env)
check(proc.returncode == 0 and read(mem, "session-summary.md") == HANDOFF,
      "default central dir (~/AI) protects the central handoff without --central-dir")
other = os.path.join(fake_home, "proj", ".agent-memory")  # central summary exists, this store is another one
shutil.copytree(make_mem(), other)
write(other, "session-summary.md", HANDOFF)
proc = subprocess.run([sys.executable, SCRIPT, other, "--session-id", "sess-A"], input=json.dumps(SUMMARY_PLAN),
                      capture_output=True, text=True, encoding="utf-8", env=env)
check(proc.returncode == 0 and read(other, "session-summary.md").startswith("# Last Session"),
      "control: an existing central handoff does not stop other stores from writing")

# --- 8. failure => no marker, honest dirty state ----------------------------
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27",
                    "learnings": [{"text": "", "importance": 3}], "consolidate": True})
check(rc == 2, "malformed plan exits 2")
check(out.get("ok") is False, "malformed plan reports ok=false")
check(not os.path.exists(os.path.join(mem, "consolidation-marker.json")),
      "no consolidation marker written when a step failed")
check(load(mem, "working/dirty-sess-A.json")["dirty"] is True,
      "dirty state stays honest after a failure")

# --- 9. dry-run writes nothing ----------------------------------------------
mem = make_mem()
before = read(mem, "learnings/learnings.json")
rc, out = run(mem, {"date": "2026-07-27", "learnings": [{"text": "Nur ein Test", "importance": 3}],
                    "consolidate": True}, "--dry-run")
check(rc == 0 and out["dry_run"] is True, "dry-run exits 0 and reports dry_run=true")
check(read(mem, "learnings/learnings.json") == before, "dry-run leaves files unchanged")
check(out["tally"]["learnings_added"] == 1, "dry-run still reports what it would do")

# --- 9b. restore was removed in 6.0.0: the section is refused before any other check ---
mem = make_mem()
before_snap = snapshot(mem)
proc = subprocess.run(
    [sys.executable, SCRIPT, mem, "--session-id", "sess-A"],
    input=json.dumps({"date": "2026-07-27", "restore": {"learnings": []}}),
    capture_output=True, text=True, encoding="utf-8", env=ENV)
check(proc.returncode == 2 and "removed in 6.0.0" in (proc.stdout + proc.stderr),
      f"test_restore_section_refused: plan with 'restore' exits 2 and says 'removed in 6.0.0' "
      f"(rc={proc.returncode}, {(proc.stdout + proc.stderr).strip()[:120]})")
check(snapshot(mem) == before_snap, "test_restore_section_refused: the store is byte-identical afterwards")
# also refused ahead of every other check: a plan that is otherwise invalid still names the removal
proc = subprocess.run(
    [sys.executable, SCRIPT, mem, "--session-id", "sess-A"],
    input=json.dumps({"date": "kaputt", "iterations": [{"summary": "ohne Titel"}], "restore": {}}),
    capture_output=True, text=True, encoding="utf-8", env=ENV)
check(proc.returncode == 2 and "removed in 6.0.0" in (proc.stdout + proc.stderr),
      "'restore' is refused first, before the date and iteration checks")

# --- 11. an unreadable store file aborts the run, it is never emptied --------
# 5.2.1: the old quarantine contract renamed the file to .corrupt.bak
# - even on --dry-run - and then wrote a store holding only the new rows, ids
# restarting at L1, exit 0, no warning. A BOM alone was enough to trigger it.
mem = make_mem()
write(mem, "learnings/learnings.json", "{ this is not json")
before = snapshot(mem)
rc, out = run(mem, {"date": "2026-07-27", "learnings": [{"text": "Nach Korruption", "importance": 3}]})
check(rc == 2, f"corrupt store file aborts the run with exit 2 (rc={rc})")
check(snapshot(mem) == before, "corrupt store file: nothing renamed, nothing written")
check(out.get("ok") is False and "learnings/learnings.json" in str(out.get("error", "")),
      f"the error names the unreadable file ({str(out.get('error', ''))[:120]})")
# Appliers run in sequence - the check has to happen before the FIRST write,
# not when the learnings applier finally gets to its file.
rc, out = run(mem, {"date": "2026-07-27",
                    "iterations": [{"type": "fix", "title": "Vor der Korruption", "outcome": "ok"}],
                    "learnings": [{"text": "Nach Korruption", "importance": 3}]})
check(rc == 2 and snapshot(mem) == before,
      "a corrupt store file stops the run before the iteration log is written")
rc, out = run(mem, {"date": "2026-07-27", "learnings": [{"text": "Nach Korruption", "importance": 3}]},
              "--dry-run")
check(rc == 2 and snapshot(mem) == before, "dry-run on a corrupt store file changes nothing either")

# --- 11b. a UTF-8 BOM is not corruption --------------------------------------
BOM_ROWS = [{"id": "L1", "date": "2026-01-01", "text": "Altbestand mit BOM", "importance": 4,
             "tags": ["x"], "layer": "short-term", "superseded_by": None, "last_relevant": "2026-01-01"}]
mem = make_mem()
with open(os.path.join(mem, "learnings", "learnings.json"), "wb") as fh:
    fh.write(b"\xef\xbb\xbf" + json.dumps(BOM_ROWS, ensure_ascii=False).encode("utf-8"))
before = snapshot(mem)
rc, out = run(mem, {"date": "2026-07-27", "learnings": [{"text": "Neu nach BOM", "importance": 3}]},
              "--dry-run")
check(rc == 0 and snapshot(mem) == before, "dry-run on a BOM store leaves every byte in place")
rc, out = run(mem, {"date": "2026-07-27", "learnings": [{"text": "Neu nach BOM", "importance": 3}]})
rows = load(mem, "learnings/learnings.json")
check(rc == 0 and [r["id"] for r in rows] == ["L1", "L2"]
      and rows[0]["text"] == "Altbestand mit BOM",
      f"BOM store keeps its rows and continues the sequence (ids={[r['id'] for r in rows]})")
check(not os.path.exists(os.path.join(mem, "learnings/learnings.json.corrupt.bak")),
      "BOM store is not quarantined")

# --- 11c. ids in archive files are taken ------------------------------------
# maintain moved rows to *-archive-*.json; re-issuing their ids makes two records
# share one id once the archive is restored, and the Atlas rebuild aborts on
# duplicate ids. All three on-disk spellings count: {stem}-archive-*.json,
# {stem}-archive.json and the misnamed {name}-archive-*.json.
mem = make_mem()  # live: L1
put(mem, "learnings/learnings-archive-2026-07.json",
    [{"id": "L2", "text": "a"}, {"id": "L5", "text": "b"}])
put(mem, "learnings/learnings.json-archive-2026-08.json", [{"id": "L7", "text": "c"}])
rc, out = run(mem, {"date": "2026-07-27", "learnings": [{"text": "Nach Archiv", "importance": 3}]})
check(rc == 0 and out["tally"]["learning_ids"] == ["L8"],
      f"new learning skips ids held by archives (got {out['tally'].get('learning_ids')})")
put(mem, "context/decisions-archive.json", [{"id": "D-030", "title": "alt"}])
put(mem, "context/decisions.json", [{"id": "D-002", "title": "live", "status": "active"}])
rc, out = run(mem, {"date": "2026-07-27", "decisions": [
    {"type": "architecture", "title": "Nach Decision-Archiv", "context": "c", "decision": "d"}]})
check(rc == 0 and out["tally"]["decision_ids"] == ["D-031"],
      f"new decision skips ids held by an undated archive (got {out['tally'].get('decision_ids')})")

# --- 11d. the preflight covers everything a plan will read (review of 5.2.1) --
# Each case: a plan whose FIRST applier would write the iteration log, and a
# problem that only a later applier used to discover -> exit 2, zero bytes changed.
ITER = [{"type": "fix", "title": "Vor dem Abbruch", "outcome": "ok"}]
cases = [
    ("dict-wrapped learnings.json",
     lambda m: put(m, "learnings/learnings.json", {"learnings": BOM_ROWS}),
     {"iterations": ITER, "learnings": [{"text": "x", "importance": 3}]}),
    ("learnings.json holding JSON null (Codex verifier 5.2.1)",
     lambda m: write(m, "learnings/learnings.json", "null"),
     {"iterations": ITER, "learnings": [{"text": "x", "importance": 3}]}),
    ("decisions.json with an object at top level",
     lambda m: put(m, "context/decisions.json", {}),
     {"iterations": ITER, "decisions": [{"type": "architecture", "title": "t", "context": "c", "decision": "d"}]}),
    ("unreadable dirty file on a consolidating plan",
     lambda m: write(m, "working/dirty-other.json", "{ kaputt"),
     {"iterations": ITER, "consolidate": True}),
    ("unreadable learnings archive on a plan that assigns learning ids",
     lambda m: write(m, "learnings/learnings-archive-2026-07.json", '[{"id": "L9", "text": "abgeschn'),
     {"iterations": ITER, "learnings": [{"text": "x", "importance": 3}]}),
    ("iteration log that is not UTF-8",
     lambda m: open(os.path.join(m, "iterations", "iteration-log.md"), "wb").write(
         "# Iteration Log\n\n## 2026-01-01 — fix: alt\n".encode("cp1252")),
     {"iterations": ITER}),
]
for name, setup, plan in cases:
    mem = make_mem()
    setup(mem)
    before = snapshot(mem)
    rc, out = run(mem, dict(plan, date="2026-07-27"))
    check(rc == 2 and out.get("ok") is False and snapshot(mem) == before,
          f"preflight: {name} -> exit 2 with a JSON error, nothing written (rc={rc}, "
          f"{str(out.get('error') or out.get('_stderr', ''))[:90]})")
# An unreadable archive only matters where an id is assigned.
mem = make_mem()
write(mem, "learnings/learnings-archive-2026-07.json", '[{"id": "L9", "text": "abgeschn')
rc, out = run(mem, {"date": "2026-07-27", "session_summary": {"what_was_done": ["nur Summary"], "statistics": {}}})
check(rc == 0, f"a plan that assigns no learning id is not blocked by a broken learnings archive (rc={rc})")

# --- 13. corrupt foreign-owned file is not quarantined ----------------------
mem = make_mem()
# The guard sits on mutation, not on reading - so the corrupt branch is the
# only path that can rename a foreign-owned file. Make it corrupt to reach it.
write(mem, "iterations/errors.json", "{ not json at all")
before = read(mem, "iterations/errors.json")
import importlib.util
spec = importlib.util.spec_from_file_location("apply_wrapup", SCRIPT)
aw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aw)
try:
    aw.load_json(mem, "iterations/errors.json", None)
    fail("loading a corrupt foreign-owned file must raise")
except aw.PlanError:
    pass_("a corrupt foreign-owned file raises PlanError")
check(read(mem, "iterations/errors.json") == before,
      "corrupt foreign-owned file stays untouched")
check(not os.path.exists(os.path.join(mem, "iterations/errors.json.corrupt.bak")),
      "no .corrupt.bak created for a foreign-owned file")

# --- 14. IO failure reports JSON and skips consolidation --------------------
mem = make_mem()
os.makedirs(os.path.join(mem, "session-summary.md"))  # a dir where a file must go
rc, out = run(mem, {"date": "2026-07-27",
                    "session_summary": {"what_was_done": ["x"], "statistics": {}},
                    "consolidate": True})
check(rc == 2, "IO failure exits 2, not 1")
check(out.get("ok") is False and "io error" in out.get("error", ""),
      "IO failure is reported as JSON, not a traceback")
check(not os.path.exists(os.path.join(mem, "consolidation-marker.json")),
      "no consolidation marker after an IO failure")
check(load(mem, "working/dirty-sess-A.json")["dirty"] is True,
      "dirty state stays honest after an IO failure")

# --- 15. iterations: real markdown shape, appended ---------------------------
# T-015 delegation rebuild: wrap-up no longer injects the iteration-logger body.
# The format is now FIXED here instead of being re-interpreted per run (the old
# SKILL.md template "## Iteration #{n}" was never actually followed on disk).
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27", "iterations": [{
    "type": "feature", "title": "Batch-Writer erweitert",
    "tags": ["wrap-up", "python"], "files_changed": ["scripts/apply_wrapup.py"],
    "summary": "Iterationen laufen jetzt ueber den Schreibplan.",
    "confidence": 5, "tests": "passed (60/60)", "commits": "abc1234",
}]})
log = read(mem, "iterations/iteration-log.md")
check(rc == 0, "plan with iterations applies cleanly")
check("## 2026-07-27 — feature: Batch-Writer erweitert" in log,
      "iteration header uses the real on-disk shape (date — type: title)")
check("- **Type:** feature" in log and "- **Tags:** wrap-up, python" in log,
      "iteration renders Type/Tags bullet fields")
check("- **Files changed:** scripts/apply_wrapup.py" in log and "- **Confidence:** 5/5" in log,
      "iteration renders Files changed and Confidence")
check(log.startswith("# Iteration Log") and "Etwas Altes" in log,
      "existing log content is preserved (append, never overwrite)")
check(out["tally"]["iterations_logged"] == 1, "tally counts logged iterations")

# --- 18. iteration dedup: same header is not written twice -------------------
mem = make_mem()
it = {"type": "feature", "title": "Etwas Altes", "tags": ["a"], "summary": "s"}
run(mem, {"date": "2026-07-01", "iterations": [it]})
rc, out = run(mem, {"date": "2026-07-01", "iterations": [it]})
check(read(mem, "iterations/iteration-log.md").count("feature: Etwas Altes") == 1,
      "identical iteration header is skipped (idempotent re-run)")
check(out["tally"]["iterations_skipped_duplicate"] == 1, "tally reports the skipped duplicate")

# --- 19. decisions: detected id format, append-only --------------------------
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27", "decisions": [{
    "type": "architecture-decision", "title": "Neue Entscheidung",
    "context": "warum", "decision": "was", "consequences": "folgen",
    "options_considered": [{"option": "A", "pros": ["p"], "cons": ["c"]}],
    "tags": ["architecture"],
}]})
decs = load(mem, "context/decisions.json")
check(rc == 0 and len(decs) == 2, "decision appended")
check(decs[-1]["id"] == "D-002", "decision id follows the DETECTED format (D-00n)")
check(decs[-1]["status"] == "active" and decs[-1]["date"] == "2026-07-27",
      "new decision is active and dated")
check(decs[0]["title"] == "Alte Entscheidung" and decs[0]["status"] == "active",
      "existing decisions are never rewritten")
check(out["tally"]["decisions_added"] == 1, "tally counts decisions")

# --- 20. supersedes flips the old record ------------------------------------
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27", "decisions": [{
    "type": "architecture-decision", "title": "Loest ab", "context": "c",
    "decision": "d", "consequences": "k", "supersedes": "D-001",
}]})
decs = {d["id"]: d for d in load(mem, "context/decisions.json")}
check(decs["D-001"]["status"] == "superseded", "superseded decision is flipped, not deleted")
check(decs["D-002"]["supersedes"] == "D-001", "new decision records what it supersedes")
check(out["tally"]["decisions_superseded"] == 1, "tally counts supersessions")

# --- 21. unknown supersedes target is a plan error --------------------------
mem = make_mem()
before_log = read(mem, "iterations/iteration-log.md")
rc, out = run(mem, {"date": "2026-07-27",
                    "iterations": [{"type": "feature", "title": "Wird verworfen",
                                    "tags": ["a"], "summary": "s"}],
                    "decisions": [{"type": "architecture-decision", "title": "Kaputt",
                                   "context": "c", "decision": "d", "consequences": "k",
                                   "supersedes": "D-999"}],
                    "consolidate": True})
check(rc == 2 and "D-999" in out.get("error", ""),
      "supersedes pointing at an unknown decision is rejected")
check(not os.path.exists(os.path.join(mem, "consolidation-marker.json")),
      "rejected decision plan leaves no consolidation marker")

# --- 22. legacy id formats still work ---------------------------------------
mem = make_mem()
put(mem, "context/decisions.json", [{"id": "D1", "title": "Legacy", "status": "active"}])
rc, out = run(mem, {"date": "2026-07-27",
                    "decisions": [{"type": "constraint-update", "title": "N", "context": "c",
                                   "decision": "d", "consequences": "k"}]})
check(load(mem, "context/decisions.json")[-1]["id"] == "D2",
      "legacy D{n} decision format is continued, not broken")

# --- 23. pattern files stay out of this script ------------------------------
# Patterns data belongs to the removed pattern mechanics (6.0.0): nothing writes it, so the
# guard must refuse them even though decisions are writable.
mem = make_mem()
before = {f: read(mem, f) for f in ("patterns/patterns.json", "patterns/patterns.md",
                                    "identity/soul.md")}
rc, out = run(mem, {"date": "2026-07-27",
                    "iterations": [{"type": "feature", "title": "T", "tags": ["a"], "summary": "s"}],
                    "decisions": [{"type": "constraint-update", "title": "D", "context": "c",
                                   "decision": "d", "consequences": "k"}]})
check(rc == 0 and all(read(mem, f) == b for f, b in before.items()),
      "patterns.json / patterns.md / soul.md stay byte-identical")

# --- 24. the generic write path cannot reach an applier-owned file ----------
spec = importlib.util.spec_from_file_location("apply_wrapup2", SCRIPT)
aw2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aw2)
mem = make_mem()
try:
    aw2.write_atomic(mem, "iterations/iteration-log.md", "gekapert", False, [])
    fail("generic write path must not reach an applier-owned file")
except aw2.PlanError:
    pass_("applier-owned file is refused on the generic write path (via= required)")
write(mem, "context/decisions.json", "{ kaputt")
for via in (None, "decisions"):
    try:
        aw2.load_json(mem, "context/decisions.json", None, via=via)
        fail(f"corrupt applier-owned file must raise (via={via})")
    except aw2.PlanError:
        pass_(f"corrupt applier-owned file raises PlanError (via={via})")
check(read(mem, "context/decisions.json") == "{ kaputt"
      and not os.path.exists(os.path.join(mem, "context/decisions.json.corrupt.bak")),
      "not even the owning applier renames or empties a corrupt file")

# === Codex verifier findings on commit 1e5c504 (2026-07-27) ==================

# --- 26. the ownership guard survives path traversal ------------------------
# "iterations/../iterations/errors.json" is not in APPLIER_OWNED as a raw
# string, but resolves to a protected file - the generic writer accepted it.
spec = importlib.util.spec_from_file_location("apply_wrapup3", SCRIPT)
aw3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aw3)
mem = make_mem()
before = read(mem, "iterations/errors.json")
for sneaky in ("iterations/../iterations/errors.json",
               "iterations\\errors.json",
               "./iterations/errors.json",
               "patterns/../patterns/patterns.json"):
    try:
        aw3.write_atomic(mem, sneaky, "GEKAPERT", False, [])
        fail(f"guard bypassed via {sneaky}")
    except aw3.PlanError:
        pass_(f"guard holds for {sneaky}")
check(read(mem, "iterations/errors.json") == before,
      "no traversal variant reached the protected file")
try:
    aw3._p(mem, "../../outside.json")
    fail("path escaping the memory dir must be refused")
except aw3.PlanError:
    pass_("path escaping the memory dir is refused")

# --- 27. the consolidation marker is written LAST ---------------------------
# It was published before the dirty flags were reset, so a failure while
# resetting them left exit 2 WITH a marker - the opposite of the contract.
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27", "consolidate": True})
files = out["files_written"]
check("consolidation-marker.json" in files and files[-1] == "consolidation-marker.json",
      "consolidation marker is the very last write of the run")

# --- 28. a corrupt dirty file must not be swallowed -------------------------
# It was quarantined, skipped, and the marker written anyway: the only record
# of un-consolidated work disappeared and the run reported success.
mem = make_mem()
write(mem, "working/dirty-sess-A.json", "{ kaputt")
rc, out = run(mem, {"date": "2026-07-27", "consolidate": True})
check(rc == 2, "a corrupt dirty file fails the run instead of passing silently")
check(not os.path.exists(os.path.join(mem, "consolidation-marker.json")),
      "no marker is written when a dirty file could not be read")
check(os.path.exists(os.path.join(mem, "working/dirty-sess-A.json")),
      "the corrupt dirty file stays in place as evidence")

# --- 29. a decision that supersedes must not be dropped as a title duplicate -
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27", "decisions": [{
    "type": "architecture-decision", "title": "Alte Entscheidung",
    "context": "neu bewertet", "decision": "anders", "consequences": "k",
    "supersedes": "D-001"}]})
decs = {d["id"]: d for d in load(mem, "context/decisions.json")}
check(out["tally"]["decisions_added"] == 1,
      "a superseding decision is recorded even when it reuses the old title")
check(decs["D-001"]["status"] == "superseded", "the superseded record is still flipped")
# ...but it must stay idempotent: identity is (title, supersedes), not title
# alone. Skipping the check entirely for superseding decisions appended the
# same record on every re-run (found by the smoke run, not by the suite).
rc, out = run(mem, {"date": "2026-07-27", "decisions": [{
    "type": "architecture-decision", "title": "Alte Entscheidung",
    "context": "neu bewertet", "decision": "anders", "consequences": "k",
    "supersedes": "D-001"}]})
check(out["tally"]["decisions_added"] == 0 and out["tally"]["decisions_skipped_duplicate"] == 1,
      "re-applying the same superseding decision does not append it twice")
check(len(load(mem, "context/decisions.json")) == 2, "decisions.json did not grow on the re-run")

# --- 30. the whole plan is validated before the first write -----------------
mem = make_mem()
before_log = read(mem, "iterations/iteration-log.md")
rc, out = run(mem, {"date": "2026-07-27",
                    "iterations": [{"type": "feature", "title": "Wird verworfen",
                                    "tags": ["a"], "summary": "s"}],
                    "learnings": [{"text": "", "importance": 3}]})
check(rc == 2, "an empty learning still rejects the plan")
check(read(mem, "iterations/iteration-log.md") == before_log,
      "a plan rejected on a LATER section leaves no half-written iteration log")

# --- 31. id sequence: a tie in frequency must not let an outlier win --------
mem = make_mem()
put(mem, "context/decisions.json", [{"id": "D-001", "title": "a", "status": "active"},
                                    {"id": "G-900", "title": "b", "status": "active"}])
rc, out = run(mem, {"date": "2026-07-27", "decisions": [{
    "type": "constraint-update", "title": "Neu", "context": "c",
    "decision": "d", "consequences": "k"}]})
check(load(mem, "context/decisions.json")[-1]["id"] == "D-002",
      "on a frequency tie the canonical prefix wins, not the foreign one")

# --- 32. iterations-only plan never touches the identity queue (/agentic-os:log) --
mem = make_mem()
before_user = read(mem, "identity/user.md")
before_queue = read(mem, "working/user-candidates.json")
rc, out = run(mem, {"date": "2026-07-27", "iterations": [{
    "type": "feature", "title": "Nur loggen", "tags": ["a", "b"], "summary": "s"}]})
check(rc == 0 and out["tally"]["iterations_logged"] == 1, "iterations-only plan applies")
check(read(mem, "identity/user.md") == before_user
      and read(mem, "working/user-candidates.json") == before_queue,
      "iterations-only plan leaves user.md and the candidate queue untouched")
# 6.0.0: the identity harvest is gone - a consolidating plan no longer re-reviews or promotes the queue
mem = make_mem()
before_user = read(mem, "identity/user.md")
before_queue = read(mem, "working/user-candidates.json")
rc, out = run(mem, {"date": "2026-07-27", "consolidate": True})
check(rc == 0 and read(mem, "identity/user.md") == before_user
      and read(mem, "working/user-candidates.json") == before_queue
      and not os.path.exists(os.path.join(mem, "identity", "briefing-lines.txt")),
      "a consolidating plan leaves user.md, the queue and the projection untouched (no promotion in 6.0.0)")
# 6.0.0: the queue is frozen data - only a plan that reads it (identity_expire, corrections.profile_candidates)
# preflights it, so a broken queue no longer blocks an unrelated plan
mem = make_mem()
write(mem, "working/user-candidates.json", "{ kaputt")
rc, out = run(mem, {"date": "2026-07-27", "iterations": [{"type": "fix", "title": "Trotz kaputter Queue"}]})
check(rc == 0 and "Trotz kaputter Queue" in read(mem, "iterations/iteration-log.md")
      and read(mem, "working/user-candidates.json") == "{ kaputt",
      f"a broken user-candidates.json does not stop an iterations-only plan and stays untouched "
      f"(rc={rc}, {out.get('error')})")
for name, extra in (("identity_expire", {"identity_expire": {"older_than_days": 30, "reason": "r"}}),
                    ("corrections.profile_candidates",
                     {"corrections": {"profile_candidates": [{"id": "UC1", "observation": "x", "reason": "r"}]}})):
    mem = make_mem()
    write(mem, "working/user-candidates.json", "{ kaputt")
    before_log = os.path.exists(os.path.join(mem, "iterations", "iteration-log.md"))
    rc, out = run(mem, {"date": "2026-07-27", "iterations": [{"type": "fix", "title": "Nie geschrieben"}], **extra})
    check(rc == 2 and "user-candidates.json" in str(out.get("error"))
          and os.path.exists(os.path.join(mem, "iterations", "iteration-log.md")) == before_log,
          f"a plan with {name} still stops on a broken queue before the first byte (rc={rc}, {out.get('error')})")

# --- 33. plan via stdin survives a cp1252 console (Windows heredoc path) ----------
# The skill pipes the plan as a heredoc. On Windows sys.stdin defaults to cp1252,
# so every "—" in the plan became "â€”" on disk (L57/L59/L60 membrain, L177-L179 DCO).
mem = make_mem()
env = dict(ENV, PYTHONIOENCODING="cp1252")
proc = subprocess.run(
    [sys.executable, SCRIPT, mem, "--session-id", "sess-A"],
    input=json.dumps({"date": "2026-09-09", "learnings": [
        {"text": "Gedankenstrich — bleibt — erhalten", "importance": 3}]},
        ensure_ascii=False).encode("utf-8"),
    capture_output=True, env=env)
texts = [e["text"] for e in load(mem, "learnings/learnings.json")]
check(proc.returncode == 0 and "Gedankenstrich — bleibt — erhalten" in texts,
      f"stdin plan is decoded as UTF-8 regardless of console encoding (got {texts[-1:]!r})")


# --- 35. 6.0.0: old plan sections are ignored and counted, never refused ----
def sha(mem_, rel):
    import hashlib
    path = os.path.join(mem_, rel)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# test_iteration_errors_werden_ignoriert
mem = make_mem()
errors_sha = sha(mem, "iterations/errors.json")
rc, out = run(mem, {"date": "2026-07-27", "iterations": [{
    "type": "bugfix", "title": "Ein Fehler", "tags": ["x", "y"], "summary": "s",
    "errors": [{"category": "runtime", "tags": ["node", "timeout"], "problem": "Hing",
                "root_cause": "Kein Timeout gesetzt", "fix": "Timeout ergaenzt", "severity": "major"}]}]})
log = read(mem, "iterations/iteration-log.md")
check(rc == 0 and "## 2026-07-27 — bugfix: Ein Fehler" in log and "**Errors:**" not in log,
      f"test_iteration_errors_werden_ignoriert: iteration-log.md gets the block, without an Errors field "
      f"(rc={rc}, {out.get('error')})")
check(sha(mem, "iterations/errors.json") == errors_sha and "iterations/errors.json" not in out.get("files_written", []),
      "test_iteration_errors_werden_ignoriert: errors.json sha256 unchanged, not in files_written")
check(out.get("tally", {}).get("ignored_sections") == ["errors"],
      f"test_iteration_errors_werden_ignoriert: tally.ignored_sections == ['errors'] "
      f"({out.get('tally', {}).get('ignored_sections')})")
check(out.get("ignored_status_line") == "Ignoriert (6.0.0): errors",
      f"test_iteration_errors_werden_ignoriert: status line names the ignored key ({out.get('ignored_status_line')!r})")
check(load(mem, "working/current-session.json")["errors_this_session"] == [],
      "test_iteration_errors_werden_ignoriert: no error id lands in working/current-session.json")

# test_errors_json_bleibt_liegen
mem = make_mem()
put(mem, "iterations/errors.json", [{"id": f"err-00{i}", "category": "x", "tags": ["a", "b"]} for i in (1, 2, 3)])
errors_bytes = read(mem, "iterations/errors.json")
rc, out = run(mem, {"date": "2026-07-27", "consolidate": True, "iterations": [
    {"type": "bugfix", "title": "Mit Fehlern", "tags": ["a", "b"], "summary": "s",
     "errors": [{"category": "x", "tags": ["a", "b"], "problem": "p"}]}]})
check(rc == 0 and os.path.exists(os.path.join(mem, "iterations", "errors.json"))
      and len(load(mem, "iterations/errors.json")) == 3 and read(mem, "iterations/errors.json") == errors_bytes,
      f"test_errors_json_bleibt_liegen: errors.json keeps its 3 entries byte for byte (rc={rc}, {out.get('error')})")

# a broken errors.json no longer blocks anything: nothing reads it
mem = make_mem()
write(mem, "iterations/errors.json", "{ kaputt")
rc, out = run(mem, {"date": "2026-07-27", "iterations": [{"type": "fix", "title": "Trotz kaputter errors.json"}]})
check(rc == 0 and "Trotz kaputter errors.json" in read(mem, "iterations/iteration-log.md")
      and read(mem, "iterations/errors.json") == "{ kaputt",
      f"an unreadable errors.json does not stop an iterations plan and stays untouched (rc={rc}, {out.get('error')})")

# the generic write path and the iterations applier both refuse errors.json (frozen data since 6.0.0)
spec = importlib.util.spec_from_file_location("apply_wrapup4", SCRIPT)
aw4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aw4)
mem = make_mem()
for via in (None, "iterations"):
    try:
        aw4.write_atomic(mem, "iterations/errors.json", "[]", False, [], via=via)
        fail(f"errors.json must be refused (via={via})")
    except aw4.PlanError:
        pass_(f"errors.json is refused on every write path (via={via})")

# user_candidates: ignored (even malformed ones), the identity files stay byte-identical, nothing is promoted
mem = make_mem()
before = snapshot(mem)
rc, out = run(mem, {"date": "2026-07-27", "user_candidates": [
    {"key": "neu", "observation": "Neue Beobachtung", "signal_type": "workflow", "confirmed": True, "quote": 5}]})
after = snapshot(mem)
check(rc == 0 and all(after.get(f) == before.get(f) for f in
                      ("identity/user.md", "identity/user-changelog.json", "working/user-candidates.json"))
      and out.get("tally", {}).get("ignored_sections") == ["user_candidates"]
      and out.get("ignored_status_line") == "Ignoriert (6.0.0): user_candidates",
      f"user_candidates are ignored, identity files byte-identical, counted "
      f"(rc={rc}, {out.get('error')}, {out.get('tally', {}).get('ignored_sections')})")
check("identity_status_line" not in out, "the old identity status line is gone")
rc, out = run(make_mem(), {"date": "2026-07-27", "user_candidates": [{"key": "k"}],
                           "iterations": [{"title": "x", "errors": [{"problem": "q"}]}, {"title": "y", "errors": [{"problem": "p"}]}]})
check(rc == 0 and out.get("tally", {}).get("ignored_sections") == ["errors", "user_candidates"]
      and out.get("ignored_status_line") == "Ignoriert (6.0.0): errors, user_candidates",
      f"several ignored sections: sorted, without duplicates ({out.get('tally', {}).get('ignored_sections')})")

# test_leere_alte_sektionen_zaehlen_nicht: an empty list carries nothing, so nothing was ignored
rc, out = run(make_mem(), {"date": "2026-07-27", "user_candidates": [],
                           "iterations": [{"title": "x", "errors": []}, {"title": "y"}]})
check(rc == 0 and out.get("tally", {}).get("ignored_sections") == [] and "ignored_status_line" not in out,
      f"test_leere_alte_sektionen_zaehlen_nicht: empty user_candidates / errors lists are not counted "
      f"({out.get('tally', {}).get('ignored_sections')})")
rc, out = run(make_mem(), {"date": "2026-07-27", "user_candidates": [],
                           "iterations": [{"title": "x", "errors": []}, {"title": "y", "errors": [{"problem": "p"}]}]})
check(rc == 0 and out.get("tally", {}).get("ignored_sections") == ["errors"],
      f"only the non-empty old section is counted ({out.get('tally', {}).get('ignored_sections')})")

# nothing ignored: the field is there and empty, no status line
rc, out = run(make_mem(), {"date": "2026-07-27", "learnings": [{"text": "Normal", "importance": 3}]})
check(rc == 0 and out.get("tally", {}).get("ignored_sections") == [] and "ignored_status_line" not in out,
      f"a plan without old sections: ignored_sections == [] and no status line "
      f"({out.get('tally', {}).get('ignored_sections')})")
rc, out = run(make_mem(), {"date": "2026-07-27", "user_candidates": [{"key": "k"}]}, "--dry-run")
check(rc == 0 and out.get("tally", {}).get("ignored_sections") == ["user_candidates"],
      "--dry-run counts the ignored sections too")

for tmp in []:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"=== Results: {PASSED}/{TESTS} passed, {ERRORS} failures ===")
sys.exit(1 if ERRORS else 0)

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


def run(mem, plan, *extra):
    proc = subprocess.run(
        [sys.executable, SCRIPT, mem, "--session-id", "sess-A", *extra],
        input=json.dumps(plan), capture_output=True, text=True, encoding="utf-8",
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
    {"text": "  bestehendes   LEARNING ", "importance": 2},
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
check(new["bridge_status"] == "candidate",
      "importance >= 4 derives bridge_status=candidate (Step 3d.1)")

# --- 1b. bridge_status: derivation + trust boundary --------------------------
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27", "learnings": [
    {"text": "Unwichtiges Detail", "importance": 3},
    {"text": "Schmuggelversuch", "importance": 2, "bridge_status": "approved"},
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

# --- 2. trust boundary + mood block -----------------------------------------
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27", "user_candidates": [
    {"key": "aus-web", "observation": "Von einer Webseite", "trust_source": "web"},
    {"key": "laune", "observation": "War genervt", "signal_type": "mood", "confirmed": True},
]})
check(out["tally"]["candidates_rejected_trust"] == 1,
      "trust_source != conversation is rejected (poisoning defense)")
keys = {c["key"] for c in load(mem, "working/user-candidates.json")}
check("aus-web" not in keys, "rejected candidate never reaches the queue")
laune_id = [c["id"] for c in load(mem, "working/user-candidates.json") if c["key"] == "laune"][0]
check(laune_id not in out["tally"]["promoted_ids"],
      "signal_type=mood is never promoted, even when confirmed")
laune = [c for c in load(mem, "working/user-candidates.json") if c["key"] == "laune"][0]
check(laune["status"] == "confirmed", "mood candidate is still recorded, just not promoted")

# --- 3. promotion rule + changelog ordering ---------------------------------
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27", "user_candidates": []})
promoted = {c["key"]: c["status"] for c in load(mem, "working/user-candidates.json")}
check(promoted["reif"] == "promoted",
      "inferred + occurrences>=2 + confidence>=0.6 promotes")
check(promoted["unreif"] == "inferred",
      "inferred + occurrences=1 + low confidence does NOT promote")
check(out["tally"]["candidates_promoted"] == 1, "full queue re-review runs without new input")
log = load(mem, "identity/user-changelog.json")
check(len(log) == 1 and log[0]["candidate_id"] == "UC1", "changelog entry written for promotion")
check(log[0]["field"] == "user.md/Preferences", "changelog names the target section")
user_md = read(mem, "identity/user.md")
check("Handgeschriebene Zeile" in user_md, "pre-existing user.md content is preserved")
check("Reifer Kandidat" in user_md, "promoted candidate lands in user.md")
check(user_md.count("## Preferences") == 1, "user.md sections are not duplicated")

# --- 4. occurrence increment escalates status -------------------------------
mem = make_mem()
run(mem, {"date": "2026-07-27", "user_candidates": [
    {"key": "unreif", "observation": "Unreifer Kandidat", "signal_type": "preference"},
]})
q = {c["key"]: c for c in load(mem, "working/user-candidates.json")}
check(q["unreif"]["occurrences"] == 2, "repeat observation increments occurrences")
check(q["unreif"]["status"] == "promoted",
      "repeated 2x -> confirmed -> promoted in the same run")

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

# --- 10. status line reports measured numbers -------------------------------
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27", "user_candidates": [
    {"key": "neu", "observation": "Neue Beobachtung", "signal_type": "workflow"}]})
line = out["identity_status_line"]
check(line.startswith("Identity: ") and "user.md promotet" in line,
      "identity status line matches the mandatory Step 6.5 format")
check("1 → user.md promotet" in line, "status line numbers come from the applied writes")

# --- 11. corrupt JSON is quarantined, not fatal -----------------------------
mem = make_mem()
write(mem, "learnings/learnings.json", "{ this is not json")
rc, out = run(mem, {"date": "2026-07-27", "learnings": [{"text": "Nach Korruption", "importance": 3}]})
check(rc == 0, "corrupt input file does not abort the run")
check(os.path.exists(os.path.join(mem, "learnings/learnings.json.corrupt.bak")),
      "corrupt file quarantined as .corrupt.bak")
check(load(mem, "learnings/learnings.json")[0]["id"] == "L1",
      "fresh file created after quarantine")

# --- 12. trust boundary also guards the full-queue re-review ----------------
# Codex verifier finding 2026-07-27 (MAJOR): trust_source was only checked when
# enqueuing a new observation. A poisoned row already on disk - written by an
# older version, another code path or by hand - was promoted into user.md by
# the Step 6.3 full-queue re-review.
mem = make_mem()
queue = load(mem, "working/user-candidates.json")
queue.append({
    "id": "UC9", "key": "vergiftet", "observation": "Aus einer Webseite geerbt",
    "status": "confirmed", "signal_type": "preference", "confidence": 0.9,
    "occurrences": 5, "evidence": ["web"], "first_seen": "2026-07-01",
    "last_seen": "2026-07-01", "trust_source": "web",
})
put(mem, "working/user-candidates.json", queue)
rc, out = run(mem, {"date": "2026-07-27", "user_candidates": []})
check(out["tally"]["promotion_blocked_trust"] == 1,
      "queue row with foreign trust_source is blocked at promotion time")
check("UC9" not in out["tally"]["promoted_ids"], "poisoned queue row is not promoted")
check("Aus einer Webseite geerbt" not in read(mem, "identity/user.md"),
      "poisoned observation never reaches user.md")
check("UC9" not in json.dumps(load(mem, "identity/user-changelog.json")),
      "poisoned observation never reaches the changelog")
check(out["tally"]["candidates_promoted"] == 1,
      "legitimate candidates in the same queue still promote")

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
    fail("quarantining a foreign-owned file must be refused")
except aw.PlanError:
    pass_("quarantine of a foreign-owned file is refused (routed through _p())")
check(read(mem, "iterations/errors.json") == before,
      "foreign-owned file untouched even on the quarantine path")
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

# --- 16. errors from an iteration: detected id format + working memory -------
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27", "iterations": [{
    "type": "bugfix", "title": "Ein Fehler", "tags": ["x", "y"],
    "summary": "s", "errors": [{
        "category": "runtime", "tags": ["node", "timeout"], "problem": "Hing",
        "root_cause": "Kein Timeout gesetzt", "fix": "Timeout ergaenzt", "severity": "major",
    }],
}]})
errs = load(mem, "iterations/errors.json")
check(len(errs) == 2 and errs[-1]["id"] == "err-002",
      "new error id follows the DETECTED format (err-00n), not the template E{n}")
check(errs[-1]["occurrences"] == 1 and errs[-1]["recurrence_dates"] == [],
      "new error entry carries the full schema")
check("- **Errors:** err-002" in read(mem, "iterations/iteration-log.md"),
      "iteration block references the error id it produced")
cs = load(mem, "working/current-session.json")
check("err-002" in cs["errors_this_session"], "error id lands in working/current-session.json")
check(out["tally"]["errors_added"] == 1, "tally counts new errors")

# --- 17. recurrence: same category + 2 overlapping tags ----------------------
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27", "iterations": [{
    "type": "bugfix", "title": "Schon wieder", "tags": ["x", "y"], "summary": "s",
    "errors": [{
        "category": "import", "tags": ["python", "circular-import", "neu"],
        "problem": "Wieder zirkulaer", "root_cause": "gleiche Ursache",
        "fix": "wieder lazy", "severity": "major",
    }],
}]})
errs = load(mem, "iterations/errors.json")
check(len(errs) == 1, "recurrence does NOT create a second error entry")
check(errs[0]["occurrences"] == 2 and "2026-07-27" in errs[0]["recurrence_dates"],
      "recurrence increments occurrences and records the date")
check(errs[0]["last_seen"] == "2026-07-27", "recurrence updates last_seen")
check("(Recurrence of err-001)" in read(mem, "iterations/iteration-log.md"),
      "iteration block marks the recurrence")
check(out["tally"]["errors_recurred"] == 1 and out["tally"]["errors_added"] == 0,
      "tally separates recurrences from new errors")

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
put(mem, "iterations/errors.json", [{"id": "E1", "category": "x", "tags": ["a"]}])
put(mem, "context/decisions.json", [{"id": "D1", "title": "Legacy", "status": "active"}])
rc, out = run(mem, {"date": "2026-07-27",
                    "iterations": [{"type": "bugfix", "title": "L", "tags": ["q"], "summary": "s",
                                    "errors": [{"category": "runtime", "tags": ["z"],
                                                "problem": "p", "root_cause": "r",
                                                "fix": "f", "severity": "minor"}]}],
                    "decisions": [{"type": "constraint-update", "title": "N", "context": "c",
                                   "decision": "d", "consequences": "k"}]})
check(load(mem, "iterations/errors.json")[-1]["id"] == "E2",
      "legacy E{n} error format is continued, not broken")
check(load(mem, "context/decisions.json")[-1]["id"] == "D2",
      "legacy D{n} decision format is continued, not broken")

# --- 23. pattern files stay out of this script ------------------------------
# Patterns are owned by scripts/extract_patterns.py - a different script, so the
# guard must refuse them even though errors/decisions are now writable.
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
write(mem, "context/decisions.json", "{ kaputt")  # only the corrupt branch mutates
try:
    aw2.load_json(mem, "context/decisions.json", None)
    fail("generic quarantine path must not reach an applier-owned file")
except aw2.PlanError:
    pass_("applier-owned file is refused on the generic quarantine path")
check(read(mem, "context/decisions.json") == "{ kaputt",
      "applier-owned file untouched on the generic quarantine path")
check(aw2.load_json(mem, "context/decisions.json", None, via="decisions") is None
      and os.path.exists(os.path.join(mem, "context/decisions.json.corrupt.bak")),
      "the owning applier CAN quarantine its own corrupt file")

# === Codex verifier findings on commit 1e5c504 (2026-07-27) ==================

# --- 25. re-running the SAME plan must not inflate error occurrences --------
# The header dedup ran AFTER the errors were processed, so a repeated plan
# skipped the iteration but counted its error as a recurrence every time:
# reproduced occurrences 2 -> 3 -> 4 over three identical runs. Test 18 missed
# it because its iteration carried no errors.
mem = make_mem()
it_with_err = {"type": "bugfix", "title": "Gleiche Iteration", "tags": ["a", "b"],
               "summary": "s", "errors": [{"category": "import",
               "tags": ["python", "circular-import", "neu"], "problem": "p",
               "root_cause": "r", "fix": "f", "severity": "major"}]}
plan_rep = {"date": "2026-07-27", "iterations": [it_with_err]}
run(mem, plan_rep)
occ_after_first = load(mem, "iterations/errors.json")[0]["occurrences"]
rc, out = run(mem, plan_rep)
occ_after_second = load(mem, "iterations/errors.json")[0]["occurrences"]
check(occ_after_second == occ_after_first,
      f"repeated plan does not re-count the recurrence (was {occ_after_first}, now {occ_after_second})")
check(out["tally"]["errors_recurred"] == 0 and out["tally"]["iterations_skipped_duplicate"] == 1,
      "a skipped duplicate iteration reports no error work at all")

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
check(out["tally"]["candidates_promoted"] == 0
      and read(mem, "identity/user.md") == before_user
      and read(mem, "working/user-candidates.json") == before_queue,
      "iterations-only plan leaves user.md and the candidate queue untouched (identity growth is wrap-up's)")
mem = make_mem()
rc, out = run(mem, {"date": "2026-07-27", "consolidate": True})
check(out["tally"]["candidates_promoted"] == 1,
      "a consolidating plan without a user_candidates key still re-reviews the full queue")

# --- 33. plan via stdin survives a cp1252 console (Windows heredoc path) ----------
# The skill pipes the plan as a heredoc. On Windows sys.stdin defaults to cp1252,
# so every "—" in the plan became "â€”" on disk (L57/L59/L60 membrain, L177-L179 DCO).
mem = make_mem()
env = dict(os.environ, PYTHONIOENCODING="cp1252")
proc = subprocess.run(
    [sys.executable, SCRIPT, mem, "--session-id", "sess-A"],
    input=json.dumps({"date": "2026-09-09", "learnings": [
        {"text": "Gedankenstrich — bleibt — erhalten", "importance": 3}]},
        ensure_ascii=False).encode("utf-8"),
    capture_output=True, env=env)
texts = [e["text"] for e in load(mem, "learnings/learnings.json")]
check(proc.returncode == 0 and "Gedankenstrich — bleibt — erhalten" in texts,
      f"stdin plan is decoded as UTF-8 regardless of console encoding (got {texts[-1:]!r})")

# --- 34. promotion never duplicates a line that already sits in user.md ----------
# 2026-07-27 (agentic-os store): UC1-UC3 were promoted a second time as plain
# duplicates of lines already present. Guard: id already cited OR Jaccard >= 0.6.
mem = make_mem()
write(mem, "identity/user.md", "# User Profile\n\n## Preferences\n\n"
      "- **Ground-Truth vor Aktion** — Behauptungen aus Reports gegen die Live-Realitaet pruefen, nicht der Buchhaltung trauen. [UC2, confirmed]\n"
      "- Codex-Verifier nach jeder substanziellen Aenderung anbieten (UC7, 2026-07-01)\n\n## Work Style\n\n- x\n")
put(mem, "working/user-candidates.json", [
    {"id": "UC2", "observation": "Ground-Truth vor Aktion: Behauptungen aus Reports gegen die Live-Realitaet pruefen, nicht der Buchhaltung trauen",
     "signal_type": "preference", "status": "confirmed", "occurrences": 3, "confidence": 0.9, "trust_source": "conversation", "evidence": ["e"]},
    {"id": "UC9", "observation": "Codex-Verifier nach jeder substanziellen Aenderung anbieten",
     "signal_type": "preference", "status": "confirmed", "occurrences": 2, "confidence": 0.8, "trust_source": "conversation", "evidence": ["e"]},
    {"id": "UC10", "observation": "Bevorzugt Deutsch in der Kommunikation und Englisch in Code und Dateinamen",
     "signal_type": "communication", "status": "confirmed", "occurrences": 2, "confidence": 0.8, "trust_source": "conversation", "evidence": ["e"]}])
rc, out = run(mem, {"date": "2026-09-09", "consolidate": True})
umd = read(mem, "identity/user.md")
check(rc == 0 and out["tally"]["promotion_skipped_duplicate"] == 2 and out["tally"]["candidates_promoted"] == 1,
      f"duplicate candidates (id cited / near-identical text) are skipped, the new one promoted ({out.get('tally')})")
check(umd.count("Ground-Truth vor Aktion") == 1 and umd.count("Codex-Verifier") == 1 and "Deutsch in der Kommunikation" in umd,
      "user.md gains exactly one new line and no duplicates")
queue = load(mem, "working/user-candidates.json")
check(all(c["status"] == "promoted" for c in queue)
      and [c["status_after_promotion"] for c in queue if c["id"] != "UC10"] == ["duplicate_of_existing"] * 2,
      "skipped duplicates leave the queue as promoted/duplicate_of_existing (no eternal re-review)")
changelog = load(mem, "identity/user-changelog.json")
check([e["candidate_id"] for e in changelog] == ["UC2", "UC9", "UC10"]
      and [e["field"] for e in changelog] == ["user.md/skipped-duplicate", "user.md/skipped-duplicate", "user.md/Preferences"]
      and changelog[0]["old_value"].startswith("- **Ground-Truth vor Aktion**"),
      "skipped duplicates leave an audit entry naming the matched line; the real promotion is logged as before")
# a genuinely different preference on the same topic must still be promoted (threshold 0.8, not 0.6)
mem = make_mem()
write(mem, "identity/user.md", "# User Profile\n\n## Preferences\n\n- Vor jedem Deployment alle Tests ausfuehren (UC7, 2026-07-01)\n\n## Work Style\n\n- x\n")
put(mem, "working/user-candidates.json", [
    {"id": "UC11", "observation": "Vor jedem Deployment nur die betroffenen Module testen, die volle Suite erst vor dem Release",
     "signal_type": "preference", "status": "confirmed", "occurrences": 2, "confidence": 0.8, "trust_source": "conversation", "evidence": ["e"]}])
rc, out = run(mem, {"date": "2026-09-09", "consolidate": True})
check(rc == 0 and out["tally"]["candidates_promoted"] == 1 and out["tally"]["promotion_skipped_duplicate"] == 0,
      "a related but different preference is promoted, not swallowed as duplicate")

for tmp in []:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"=== Results: {PASSED}/{TESTS} passed, {ERRORS} failures ===")
sys.exit(1 if ERRORS else 0)

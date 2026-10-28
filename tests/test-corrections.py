#!/usr/bin/env python3
"""Tests for the correction path of apply_wrapup.py (DCO #9393).

Plan section `corrections` retitles tasks and corrects learning texts WITH
provenance (who, when, why, what it was). Also covers the deterministic
mojibake repair (`--propose-encoding-corrections`) that feeds it.

Throwaway dirs only; the real stores are never touched.
Exit codes: 0 = all pass, 1 = failures found.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "apply_wrapup.py")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from _isolated_home import process_env  # noqa: E402

# Subprozesse laufen mit frischem Temp-Home statt mit dem echten Home (P0, tests/_isolated_home.py)
ENV, ISO_HOME = process_env(PYTHONIOENCODING="utf-8")

TESTS = PASSED = ERRORS = 0
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


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
        fh.write(data if isinstance(data, str) else json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def get(mem, rel):
    with open(os.path.join(mem, rel), encoding="utf-8") as fh:
        return fh.read()


def getj(mem, rel):
    return json.loads(get(mem, rel))


def moji(s: str) -> str:
    """What a UTF-8 text looks like after being read as cp1252 (the #9393 damage)."""
    return s.encode("utf-8").decode("cp1252")


def learning(lid, text, **kw):
    row = {"id": lid, "date": "2026-09-08", "text": text, "importance": 4, "tags": ["x"],
           "layer": "short-term", "superseded_by": None, "last_relevant": "2026-09-08"}
    row.update(kw)
    return row


def task(tid, title, status="open"):
    return {"id": tid, "title": title, "status": status, "created": "2026-09-08",
            "updated": "2026-09-08", "resolution": None, "source": "wrap-up", "cross_project": False}


LONG = ("Cloudflare-Pages-Builds der Hauptseite scheitern still, wenn der Token abläuft; "
        "ein lokaler Build bleibt grün und prüft darum nichts.")


def make_mem():
    mem = tempfile.mkdtemp(prefix="corr-test-")
    put(mem, "learnings/learnings.json", [
        learning("L1", moji(LONG), bridge_status="approved"),
        learning("L2", "Unverändertes Learning zur Kontrolle der Bytes.", bridge_status="approved"),
        {"id": "L3", "date": "2026-09-08", "kind": "principle", "text": "Regel → a.md#x", "summary": "Regel",
         "anchor": "a.md#x", "importance": 3, "tags": [], "layer": "long-term", "superseded_by": None,
         "last_relevant": "2026-09-08", "derived_from": ["L2"], "bridge_status": "approved"},
    ])
    put(mem, "context/open-tasks.json", [
        task("T-001", moji("Fünf Fragen: Owner-Redigat der Copy — Frage 4")),
        task("T-002", "Zweite Aufgabe bleibt unberührt"),
        task("T-003", moji("Messung für Fünf Fragen"), status="closed"),
    ])
    put(mem, "iterations/iteration-log.md", "# Iteration Log\n")
    put(mem, "iterations/errors.json", [])
    put(mem, "context/decisions.json", [])
    return mem


def run(mem, plan, *extra):
    p = subprocess.run([sys.executable, SCRIPT, mem, "--session-id", "s-test", *extra],
                       input=json.dumps(plan, ensure_ascii=False), capture_output=True,
                       encoding="utf-8", errors="replace", env=ENV)
    try:
        out = json.loads(p.stdout)
    except ValueError:
        out = {"_raw": p.stdout, "_err": p.stderr}
    return p.returncode, out


def snapshot(mem):
    return {rel: get(mem, rel) for rel in
            ("learnings/learnings.json", "context/open-tasks.json", "iterations/iteration-log.md")}


FIXED_TITLE = "Fünf Fragen: Owner-Redigat der Copy — Frage 4"


DD_ROW = {
    "id": "D1", "date": "2026-09-08", "type": "design", "title": moji("Besucherführung: Belege werden eingeklappt statt gelöscht"),
    "status": "active", "context": moji("Owner-Beobachtung: Besucher scrollen die Home nie; kürzte aber Belege weg."),
    "options_considered": [
        {"option": "Codex-Branch komplett mergen", "pros": ["schnell"], "cons": [moji("Belege gelöscht"), "Owner-Copy gekürzt"]},
        {"option": moji("Fünf Fragen als Lesehilfe"), "pros": [moji("nichts gelöscht")], "cons": ["ein Klick mehr"]}],
    "decision": "Option 2 – sauber gespeichert, nie kaputt gewesen: Löschungen nicht übernommen.",
    "consequences": "Neue Struktur-Tests pinnen das Budget.", "supersedes": None, "tags": ["ux"],
    "wiki_ref": "wiki/entities/x.md", "promoted_at": "2026-09-08"}
DD_FIX = {
    "id": "D1", "title": "Besucherführung: Belege werden eingeklappt statt gelöscht",
    "context": "Owner-Beobachtung: Besucher scrollen die Home nie; kürzte aber Belege weg.",
    "options_considered": [
        {"option": "Codex-Branch komplett mergen", "pros": ["schnell"], "cons": ["Belege gelöscht", "Owner-Copy gekürzt"]},
        {"option": "Fünf Fragen als Lesehilfe", "pros": ["nichts gelöscht"], "cons": ["ein Klick mehr"]}]}
UM_GOOD = "- Explizite Regel: Subagenten-Modell nach Aufgabenkomplexität wählen (UC31, 2026-09-07)"
UM_BAD = moji(UM_GOOD)


def make_dd_mem():
    mem = make_mem()
    other = dict(DD_ROW, id="D2", title="Zweite Entscheidung bleibt unberührt", context="Sauberer Text über Löschungen.",
                 options_considered=[], decision="Nichts zu tun.", wiki_ref=None, promoted_at=None)
    put(mem, "context/decisions.json", [DD_ROW, other])
    put(mem, "identity/user.md", "# User Profile\n\n## Preferences\n\n- Erste Zeile ist sauber, mit Umlaut ä.\n"
        + UM_BAD + "\n\n## Work Style\n\n- Noch eine Zeile.\n")
    put(mem, "identity/user-changelog.json", [{"ts": "2026-09-07T10:00:00+02:00", "field": "user.md/Preferences",
                                               "old_value": None, "new_value": "x", "candidate_id": "UC1", "evidence": []}])
    return mem


def main():
    print("== corrections: tasks")
    mem = make_mem()
    code, out = run(mem, {"date": "2026-10-24", "corrections": {"tasks": [
        {"id": "T-001", "title": FIXED_TITLE, "reason": "Encoding-Reparatur (cp1252 statt UTF-8)", "by": "agent"}]}})
    check(code == 0 and out.get("ok"), f"retitle accepted (exit {code})")
    rows = {r["id"]: r for r in getj(mem, "context/open-tasks.json")}
    check(rows["T-001"]["title"] == FIXED_TITLE, "title replaced")
    check(rows["T-001"]["updated"] == "2026-10-24", "updated stamped")
    prov = (rows["T-001"].get("corrections") or [None])[-1]
    check(isinstance(prov, dict) and prov.get("by") == "agent" and prov.get("date") == "2026-10-24"
          and "Encoding" in prov.get("reason", "") and prov.get("was", "").startswith("F"),
          "provenance holds who/when/why/was")
    check(prov and prov.get("field") == "title" and prov.get("session") == "s-test", "provenance names field and session")
    check(rows["T-002"].get("corrections") is None and rows["T-002"]["title"] == "Zweite Aufgabe bleibt unberührt",
          "other tasks untouched")
    check(out["tally"]["tasks_retitled"] == 1, "tally counts retitled task")

    print("== corrections: idempotent + closed task")
    code, out = run(mem, {"date": "2026-10-25", "corrections": {"tasks": [
        {"id": "T-001", "title": FIXED_TITLE, "reason": "nochmal"}]}})
    rows = {r["id"]: r for r in getj(mem, "context/open-tasks.json")}
    check(code == 0 and out["tally"]["corrections_skipped_unchanged"] == 1 and len(rows["T-001"]["corrections"]) == 1,
          "same title again: no-op, no second provenance entry")
    code, out = run(mem, {"date": "2026-10-25", "corrections": {"tasks": [
        {"id": "T-003", "title": "Messung für Fünf Fragen", "reason": "Encoding", "by": "owner"}]}})
    rows = {r["id"]: r for r in getj(mem, "context/open-tasks.json")}
    check(code == 0 and rows["T-003"]["status"] == "closed" and rows["T-003"]["corrections"][-1]["by"] == "owner",
          "closed task can be retitled, status kept, by=owner recorded")

    print("== corrections: learnings")
    mem = make_mem()
    code, out = run(mem, {"date": "2026-10-24", "corrections": {"learnings": [
        {"id": "L1", "text": LONG, "reason": "Encoding-Reparatur"}]}})
    check(code == 0 and out.get("ok"), f"learning correction accepted (exit {code}: {out.get('error')})")
    rows = {r["id"]: r for r in getj(mem, "learnings/learnings.json")}
    check(rows["L1"]["text"] == LONG, "text replaced")
    check(rows["L1"]["bridge_status"] == "approved", "bridge_status kept")
    prov = (rows["L1"].get("corrections") or [None])[-1]
    check(isinstance(prov, dict) and prov.get("field") == "text" and prov.get("was") == moji(LONG)
          and prov.get("by") == "agent" and prov.get("date") == "2026-10-24", "provenance keeps the old text")
    check(LONG in get(mem, "learnings/learnings.md"), "learnings.md regenerated from the corrected store")
    check(out["tally"]["learnings_corrected"] == 1 and out["tally"]["corrections_projection_needed"] is True,
          "tally: corrected + projections needed (approved row)")
    check(rows["L2"] == getj(make_mem(), "learnings/learnings.json")[1], "other learning byte-equal in content")

    print("== corrections: refusals leave every file untouched")
    cases = {
        "unknown id": {"learnings": [{"id": "L99", "text": LONG, "reason": "x"}]},
        "no reason": {"tasks": [{"id": "T-001", "title": FIXED_TITLE}]},
        "blank reason": {"tasks": [{"id": "T-001", "title": FIXED_TITLE, "reason": "  "}]},
        "empty title": {"tasks": [{"id": "T-001", "title": " ", "reason": "x"}]},
        "bad by": {"tasks": [{"id": "T-001", "title": FIXED_TITLE, "reason": "x", "by": "codex"}]},
        "principle row": {"learnings": [{"id": "L3", "text": "Regel", "reason": "x"}]},
        "meaning change": {"learnings": [{"id": "L2", "text": "Völlig anderer Inhalt ohne Bezug zur Vorlage hier.",
                                            "reason": "x"}]},
        "twice in plan": {"tasks": [{"id": "T-001", "title": FIXED_TITLE, "reason": "x"},
                                    {"id": "T-001", "title": FIXED_TITLE + "!", "reason": "x"}]},
        "title = other open task": {"tasks": [{"id": "T-001", "title": "Zweite Aufgabe bleibt unberührt",
                                                 "reason": "x"}]},
        "not an object": {"tasks": ["T-001"]},
    }
    for name, corr in cases.items():
        mem = make_mem()
        before = snapshot(mem)
        plan = {"date": "2026-10-24", "iterations": [{"title": "darf nicht geschrieben werden"}],
                "corrections": corr}
        code, out = run(mem, plan)
        check(code == 2 and not out.get("ok") and snapshot(mem) == before, f"refused before the first byte: {name}")

    print("== corrections: ambiguous id")
    mem = make_mem()
    rows = getj(mem, "context/open-tasks.json")
    rows.append(task("T-001", "Zweites T-001"))
    put(mem, "context/open-tasks.json", rows)
    before = snapshot(mem)
    code, out = run(mem, {"corrections": {"tasks": [{"id": "T-001", "title": FIXED_TITLE, "reason": "x"}]}})
    check(code == 2 and "ambiguous" in json.dumps(out) and snapshot(mem) == before, "duplicate id is refused, not guessed")

    print("== corrections: dry run")
    mem = make_mem()
    before = snapshot(mem)
    code, out = run(mem, {"corrections": {"tasks": [{"id": "T-001", "title": FIXED_TITLE, "reason": "x"}]}},
                    "--dry-run")
    check(code == 0 and out["tally"]["tasks_retitled"] == 1 and snapshot(mem) == before, "dry run counts, writes nothing")

    print("== corrections: store ownership unchanged")
    mem = make_mem()
    code, out = run(mem, {"corrections": {"learnings": [{"id": "L1", "text": LONG, "reason": "x"}]}})
    check(code == 0, "learnings.json is only reachable through the applier (no generic-path regression)")

    print("== mojibake helpers")
    import apply_wrapup as aw
    check(aw.repair_mojibake(moji("Fünf — grün … Äpfel §")) == "Fünf — grün … Äpfel §", "repair: full string")
    check(aw.repair_mojibake("gemischt: schon gut ü und " + moji("kaputtes ü")) == "gemischt: schon gut ü und kaputtes ü",
          "repair: mixed string, only the damaged run changes")
    check(aw.repair_mojibake("Ganz normaler Text mit ä ö ü ß und — Strich") == "Ganz normaler Text mit ä ö ü ß und — Strich",
          "repair: clean text is a fixpoint")
    check(aw.looks_mojibake(moji("Fünf")) and not aw.looks_mojibake("Fünf"), "detector")
    check(aw.repair_mojibake(moji("Emoji 😀 ok")) == "Emoji 😀 ok", "repair: 4-byte sequence")

    print("== --propose-encoding-corrections")
    mem = make_mem()
    before = snapshot(mem)
    p = subprocess.run([sys.executable, SCRIPT, mem, "--propose-encoding-corrections"], capture_output=True,
                       encoding="utf-8", errors="replace", env=ENV)
    proposal = json.loads(p.stdout)
    corr = proposal.get("corrections", {})
    check(p.returncode == 0 and snapshot(mem) == before, "read-only")
    check(sorted(t["id"] for t in corr.get("tasks", [])) == ["T-001", "T-003"], "proposes exactly the damaged tasks")
    check([l["id"] for l in corr.get("learnings", [])] == ["L1"], "proposes exactly the damaged learning (not the principle)")
    code, out = run(mem, proposal)
    check(code == 0 and out["tally"]["tasks_retitled"] == 2 and out["tally"]["learnings_corrected"] == 1,
          "the proposal applies as it is")
    p = subprocess.run([sys.executable, SCRIPT, mem, "--propose-encoding-corrections"], capture_output=True,
                       encoding="utf-8", errors="replace", env=ENV)
    check(json.loads(p.stdout).get("corrections") == {"tasks": [], "learnings": [], "decisions": [], "user_md": []}, "after applying nothing is left")
    check(getj(mem, "context/open-tasks.json")[0]["title"] == FIXED_TITLE, "task title repaired end to end")

    print("== corrections: decisions (DCO #10057 (2))")
    mem = make_dd_mem()
    other_before = json.dumps(getj(mem, "context/decisions.json")[1], sort_keys=True)
    plan = {"date": "2026-10-25", "corrections": {"decisions": [dict(DD_FIX, reason="Encoding-Reparatur (cp1252 statt UTF-8)")]}}
    code, out = run(mem, plan)
    d1 = getj(mem, "context/decisions.json")[0]
    check(code == 0 and out.get("ok"), f"decision correction accepted (exit {code}, {out.get('error', '')})")
    check(d1["title"] == DD_FIX["title"] and d1["context"] == DD_FIX["context"]
          and d1["options_considered"] == DD_FIX["options_considered"], "title, context and nested options_considered repaired")
    check(d1["decision"] == DD_ROW["decision"] and d1["status"] == "active" and d1["wiki_ref"] == "wiki/entities/x.md",
          "untouched fields (decision, status, wiki_ref) stay as they were")
    stamps = {c["field"]: c for c in d1.get("corrections", [])}
    check(set(stamps) == {"title", "context", "options_considered"} and stamps["title"]["was"] == DD_ROW["title"]
          and stamps["title"]["date"] == "2026-10-25" and stamps["title"]["by"] == "agent",
          "every changed field is stamped with the old value, date and decider")
    check(json.dumps(getj(mem, "context/decisions.json")[1], sort_keys=True) == other_before, "the other decision is untouched")
    check(out["tally"]["decisions_corrected"] == 1 and out["tally"]["correction_ids"] == ["D1"], "tally counts the decision")
    check(any("wiki" in w.lower() for w in out["tally"]["warnings"]), "warning: the promoted wiki copy is not repaired here")
    code, out = run(mem, plan)
    check(code == 0 and out["tally"]["decisions_corrected"] == 0 and out["tally"]["corrections_skipped_unchanged"] == 1
          and len(getj(mem, "context/decisions.json")[0]["corrections"]) == 3, "second run is a no-op (idempotent, no new stamps)")

    print("== corrections: decisions refusals leave the file untouched")
    mem = make_dd_mem()
    snap = get(mem, "context/decisions.json")
    bad = [
        ("different statement", {"id": "D1", "title": "Komplett anderer Titel ohne Bezug zum alten Text", "reason": "x"}),
        ("unknown id", {"id": "D99", "title": DD_FIX["title"], "reason": "x"}),
        ("no reason", {"id": "D1", "title": DD_FIX["title"]}),
        ("unknown field", {"id": "D1", "status": "superseded", "reason": "x"}),
        ("options shape changed", {"id": "D1", "options_considered": DD_FIX["options_considered"][:1], "reason": "x"}),
        ("options keys changed", {"id": "D1", "reason": "x", "options_considered": [
            {"option": DD_FIX["options_considered"][0]["option"], "extra": 1}, DD_FIX["options_considered"][1]]}),
        ("empty text", {"id": "D1", "decision": "  ", "reason": "x"}),
    ]
    for label, item in bad:
        code, out = run(mem, {"corrections": {"decisions": [item]}})
        check(code == 2 and get(mem, "context/decisions.json") == snap, f"{label}: plan rejected, store untouched")

    print("== corrections: user.md lines are encoding repair only (DCO #10057 (2))")
    mem = make_dd_mem()
    um_before = get(mem, "identity/user.md")
    good = {"old": UM_BAD, "new": UM_GOOD, "reason": "Encoding-Reparatur (cp1252 statt UTF-8)"}
    for label, item in [
        ("a rewording that is not a pure encoding repair", dict(good, new=UM_GOOD.replace("Explizite Regel", "Optionale Regel"))),
        ("a line that does not exist", dict(good, old="- Diese Zeile gibt es nicht")),
        ("a multi-line replacement", dict(good, new=UM_GOOD + "\n- eingeschmuggelte Zeile")),
    ]:
        code, out = run(mem, {"corrections": {"user_md": [item]}})
        check(code == 2 and get(mem, "identity/user.md") == um_before, f"{label}: rejected, user.md untouched")
    code, out = run(mem, {"date": "2026-10-25", "corrections": {"user_md": [good]}})
    um_after = get(mem, "identity/user.md")
    check(code == 0 and um_after == um_before.replace(UM_BAD, UM_GOOD), "the damaged line is replaced, every other byte stays")
    check(out["tally"]["user_lines_corrected"] == 1, "tally counts the user.md line")
    log = getj(mem, "identity/user-changelog.json")
    check(len(log) == 2 and log[-1]["field"] == "user.md/encoding-correction" and log[-1]["old_value"] == UM_BAD
          and log[-1]["new_value"] == UM_GOOD and log[-1]["reason"].startswith("Encoding"), "audit entry in user-changelog.json")
    code, out = run(mem, {"date": "2026-10-26", "corrections": {"user_md": [good]}})
    check(code == 0 and get(mem, "identity/user.md") == um_after and len(getj(mem, "identity/user-changelog.json")) == 2
          and out["tally"]["corrections_skipped_unchanged"] == 1, "second run is a no-op (no second audit entry)")
    dry_mem = make_dd_mem()
    code, out = run(dry_mem, {"corrections": {"user_md": [good]}}, "--dry-run")
    check(code == 0 and get(dry_mem, "identity/user.md") == um_before, "dry run changes nothing")

    print("== --propose-encoding-corrections covers decisions and user.md")
    mem = make_dd_mem()
    before = (get(mem, "context/decisions.json"), get(mem, "identity/user.md"))
    p = subprocess.run([sys.executable, SCRIPT, mem, "--propose-encoding-corrections"], capture_output=True,
                       encoding="utf-8", errors="replace", env=ENV)
    proposal = json.loads(p.stdout)
    corr = proposal["corrections"]
    check((get(mem, "context/decisions.json"), get(mem, "identity/user.md")) == before, "read-only")
    check([d["id"] for d in corr["decisions"]] == ["D1"] and set(corr["decisions"][0]) == {"id", "title", "context", "options_considered", "reason"},
          "proposes exactly the damaged decision and only its damaged fields")
    check(corr["user_md"] == [{"old": UM_BAD, "new": UM_GOOD, "reason": corr["user_md"][0]["reason"]}], "proposes exactly the damaged user.md line")
    code, out = run(mem, proposal)
    check(code == 0 and out["tally"]["decisions_corrected"] == 1 and out["tally"]["user_lines_corrected"] == 1, "the proposal applies as it is")
    check(getj(mem, "context/decisions.json")[0]["options_considered"] == DD_FIX["options_considered"]
          and UM_GOOD in get(mem, "identity/user.md") and UM_BAD not in get(mem, "identity/user.md"), "stores repaired end to end")
    p = subprocess.run([sys.executable, SCRIPT, mem, "--propose-encoding-corrections"], capture_output=True,
                       encoding="utf-8", errors="replace", env=ENV)
    check(json.loads(p.stdout)["corrections"] == {"tasks": [], "learnings": [], "decisions": [], "user_md": []},
          "after applying nothing is left (stamps keep the old text in 'was' but are not proposed again)")

    print("== write-time warning")
    mem = make_mem()
    code, out = run(mem, {"learnings": [{"text": moji("Neues Learning mit kaputtem Zeichen ü und ö"), "importance": 2}],
                          "open_tasks": {"add": [{"title": moji("Neue Aufgabe für später")}]}})
    warns = " ".join(out.get("tally", {}).get("warnings", []))
    check(code == 0 and "Mojibake" in warns and warns.count("Mojibake") >= 2,
          "a new learning/task with mojibake is written but warned about")

    print(f"=== Results: {PASSED}/{TESTS} passed, {ERRORS} failures ===")
    return 1 if ERRORS else 0


if __name__ == "__main__":
    sys.exit(main())

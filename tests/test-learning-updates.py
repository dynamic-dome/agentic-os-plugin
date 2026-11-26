#!/usr/bin/env python3
"""Tests for the plan section `learning_updates` and the Pflegelauf stamp (6.0.0).

Covers apply_wrapup.py: superseded_by set / cleared, review_after, the update_log
entry, the protection rules (rule_class, pointer rows, pointer members, chains,
self reference, cycles in one plan, unknown ids, reason, both fields), the stamp
working/pflegelauf.json with id_keys counted like count_ids in
memory-thresholds.sh, and --dry-run writing nothing.

Every refusal is checked byte-for-byte: a rejected plan writes nothing, not the
store and not the iteration log a plan section before it would write (L94).

Throwaway dirs only; the real stores are never touched.
Exit codes: 0 = all pass, 1 = failures found.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
APPLY = os.path.join(SCRIPTS, "apply_wrapup.py")
sys.path.insert(0, SCRIPTS)
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


def put(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(data if isinstance(data, str) else json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def row(lid, text, imp=3, date="2026-08-01", **kw):
    r = {"id": lid, "date": date, "text": text, "importance": imp, "tags": [], "layer": "short-term",
         "superseded_by": None, "last_relevant": date}
    r.update(kw)
    return r


def run(args, stdin=None):
    p = subprocess.run([sys.executable] + args, input=stdin, capture_output=True, text=True,
                       encoding="utf-8", env=ENV)
    try:
        return p.returncode, json.loads(p.stdout)
    except ValueError:
        return p.returncode, {"_raw": p.stdout, "_err": p.stderr}


def setup():
    """L1..L4 plain, L5 superseded by L4, L6 rule_class, L7 a pointer over L8, L8 its member,
    L9 bridge_status rejected, L10 bridge_status retired."""
    tmp = tempfile.mkdtemp(prefix="lupd-test-")
    mem = os.path.join(tmp, ".agent-memory")
    put(os.path.join(mem, "learnings", "learnings.json"), [
        row("L1", "Ein Learning, das doppelt ist", bridge_status="approved"),
        row("L2", "Das stärkere Learning zum selben Befund", 4, bridge_status="approved"),
        row("L3", "Ein drittes Learning", bridge_status="approved"),
        row("L4", "Ein viertes Learning", bridge_status="approved"),
        row("L5", "Schon überholt", superseded_by="L4", bridge_status="approved"),
        row("L6", "Eine Regel zur Verfassung", 5, rule_class=True,
            release={"status": "needs_explanation"}),
        row("L7", "Regelzeiger → rules.md#^r-x", 4, kind="principle", summary="Regelzeiger",
            anchor="rules.md#^r-x", derived_from=["L8"], bridge_status="approved"),
        row("L8", "Mitglied eines Zeigers", 4, bridge_status="approved"),
        row("L9", "Vom Bridge-Gate abgelehnt", bridge_status="rejected"),
        row("L10", "Aus der Bridge genommen", bridge_status="retired"),
    ])
    return tmp, mem


def learn_path(mem):
    return os.path.join(mem, "learnings", "learnings.json")


def stamp_path(mem):
    return os.path.join(mem, "working", "pflegelauf.json")


def rows_of(mem):
    with open(learn_path(mem), encoding="utf-8") as fh:
        return json.load(fh)


def by(mem, lid):
    return next((r for r in rows_of(mem) if r.get("id") == lid), None)


def apply(mem, plan, extra=()):
    return run([APPLY, mem] + list(extra), json.dumps(dict({"date": "2026-11-16"}, **plan)))


def setup_feedback():
    """setup() plus L11 approved feedback (an owner correction shown in MEMORY.md) and L12 rejected feedback."""
    tmp, mem = setup()
    rows = rows_of(mem) + [row("L11", "Owner-Korrektur: nie X tun", 4, kind="feedback", bridge_status="approved"),
                           row("L12", "Abgelehntes Feedback", 3, kind="feedback", bridge_status="rejected")]
    put(learn_path(mem), rows)
    return tmp, mem


def refused(plan, why, expect=None, make=None):
    """L94: a rejected plan writes nothing - not the store, not the iteration log written before it,
    not the stamp."""
    _, mem = (make or setup)()
    before = sha(learn_path(mem))
    plan = dict({"pflegelauf_stamp": True}, **plan, iterations=[{"title": "darf nicht geschrieben werden"}])
    rc, out = apply(mem, plan)
    check(rc == 2 and sha(learn_path(mem)) == before
          and not os.path.exists(os.path.join(mem, "iterations", "iteration-log.md"))
          and not os.path.exists(stamp_path(mem))
          and (expect is None or expect in str(out.get("error"))),
          f"refused before the first byte: {why} ({rc}, {str(out.get('error'))[:120]})")
    return out


# ============================================================ writes
def test_superseded_wird_gesetzt():
    print("--- test_superseded_wird_gesetzt")
    _, mem = setup()
    text_before = by(mem, "L1")["text"]
    rc, out = apply(mem, {"learning_updates": [{"id": "L1", "superseded_by": "L2", "reason": "doppelt: L2 sagt es genauer"}]})
    r = by(mem, "L1")
    check(rc == 0 and r.get("superseded_by") == "L2", f"L1.superseded_by == L2 ({rc}, {out.get('error')})")
    log = r.get("update_log") or [{}]
    check(log[-1] == {"date": "2026-11-16", "reason": "doppelt: L2 sagt es genauer", "by": "pflegelauf",
                      "field": "superseded_by", "old": None, "new": "L2"},
          f"update_log entry with date, reason, by, field, old, new ({log[-1]})")
    check(r.get("text") == text_before, "text unchanged")
    check((out.get("tally") or {}).get("learning_updates") == {"superseded": 1, "cleared": 0, "review_after": 0,
                                                                "obsolete": 0},
          f"tally learning_updates ({(out.get('tally') or {}).get('learning_updates')})")
    md_path = os.path.join(mem, "learnings", "learnings.md")
    md = open(md_path, encoding="utf-8").read() if os.path.exists(md_path) else ""
    check("**L1**" not in md and "**L2**" in md, "learnings.md projection no longer lists the superseded row")


def test_zuruecknehmen():
    print("--- test_zuruecknehmen")
    _, mem = setup()
    rc, out = apply(mem, {"learning_updates": [{"id": "L5", "superseded_by": None, "reason": "Fehlurteil zurückgenommen"}]})
    r = by(mem, "L5")
    check(rc == 0 and "superseded_by" in r and r["superseded_by"] is None,
          f"L5.superseded_by is None ({rc}, {out.get('error')}, {r.get('superseded_by')!r})")
    check((out.get("tally") or {}).get("learning_updates", {}).get("cleared") == 1, "tally cleared == 1")
    entry = (r.get("update_log") or [{}])[-1]
    check(entry.get("reason") == "Fehlurteil zurückgenommen", "update_log carries the reason")
    check(entry.get("field") == "superseded_by" and entry.get("old") == "L4" and "new" in entry and entry["new"] is None,
          f"update_log of a clear: field superseded_by, old L4, new null ({entry})")


def test_review_after():
    print("--- test_review_after")
    _, mem = setup()
    rc, out = apply(mem, {"learning_updates": [{"id": "L3", "review_after": "2027-05-16", "reason": "bleibt: trägt noch"}]})
    r = by(mem, "L3")
    check(rc == 0 and r.get("review_after") == "2027-05-16", f"L3.review_after set ({rc}, {out.get('error')})")
    check(r.get("superseded_by") is None, "review_after leaves superseded_by alone")
    check((out.get("tally") or {}).get("learning_updates", {}).get("review_after") == 1, "tally review_after == 1")
    entry = (r.get("update_log") or [{}])[-1]
    check(entry.get("field") == "review_after" and "old" in entry and entry["old"] is None
          and entry.get("new") == "2027-05-16", f"update_log of review_after: field, old null, new date ({entry})")
    rc, out = apply(mem, {"learning_updates": [{"id": "L3", "review_after": "2027-11-16", "reason": "bleibt wieder"}]})
    entry = (by(mem, "L3").get("update_log") or [{}])[-1]
    check(rc == 0 and entry.get("old") == "2027-05-16" and entry.get("new") == "2027-11-16",
          f"a second review_after logs the previous date as old ({entry})")


def test_bleibt_kommt_wieder():
    """I1: a row judged 'bleibt' stays out while its review date lies ahead and comes back once it has passed -
    in source 2 (review_sweep --candidates-json) and source 3 (learnings_lifecycle propose --skip-reviewed)."""
    print("--- test_bleibt_kommt_wieder")
    tmp, mem = setup()
    rules = os.path.join(tmp, "rules.md")
    put(rules, "# Regeln\n\n## Ein drittes Learning\n\nEin drittes Learning als Regel.\n")
    rc, out = apply(mem, {"learning_updates": [{"id": "L3", "review_after": "2027-05-16", "reason": "bleibt"}],
                          "pflegelauf_stamp": True})
    check(rc == 0 and by(mem, "L3").get("review_after") == "2027-05-16", f"bleibt written ({rc}, {out.get('error')})")
    sweep = os.path.join(SCRIPTS, "review_sweep.py")
    life = os.path.join(SCRIPTS, "learnings_lifecycle.py")

    def sources(today):
        p = subprocess.run([sys.executable, sweep, mem, "--candidates-json", "--today", today], capture_output=True,
                           text=True, encoding="utf-8", env=ENV)
        due = {c["id"] for c in json.loads(p.stdout or "[]")}
        _, prop = run([life, "propose", mem, "--catalog", rules, "--min-importance", "1", "--min-age-days", "0",
                       "--today", today, "--skip-reviewed"])
        return due, {x["id"] for x in prop.get("proposals", [])}

    due, prop = sources("2026-11-17")
    check("L3" not in due and "L3" not in prop, f"next day: L3 is out of source 2 and source 3 ({sorted(due)}, {sorted(prop)})")
    due, prop = sources("2027-05-17")
    check("L3" in due, f"review date passed: L3 is due again in source 2 ({sorted(due)})")
    check("L3" in prop, f"review date passed: L3 comes back in source 3 despite its pflegelauf entry ({sorted(prop)})")


def test_unveraendert_schreibt_nichts():
    print("--- test_unveraendert_schreibt_nichts")
    _, mem = setup()
    before = sha(learn_path(mem))
    rc, out = apply(mem, {"learning_updates": [{"id": "L5", "superseded_by": "L4", "reason": "schon so"},
                                               {"id": "L1", "superseded_by": None, "reason": "ist schon live"}]})
    t = out.get("tally") or {}
    check(rc == 0 and sha(learn_path(mem)) == before and t.get("learning_updates_unchanged") == 2
          and t.get("learning_updates") == {"superseded": 0, "cleared": 0, "review_after": 0, "obsolete": 0},
          f"an update that changes nothing writes nothing and counts as unchanged ({rc}, {t.get('learning_updates_unchanged')})")


# ============================================================ F1: verdict "gegenstandslos" (obsolete)
OBSOLETE_VALUE = "obsolete:pflegelauf-2026-11-16"


def test_gegenstandslos_wird_gesetzt():
    """F1: {"id", "obsolete": true, "reason"} sets superseded_by = obsolete:pflegelauf-<plan date>; text and row stay."""
    print("--- test_gegenstandslos_wird_gesetzt")
    _, mem = setup()
    text_before = by(mem, "L3")["text"]
    rc, out = apply(mem, {"learning_updates": [{"id": "L3", "obsolete": True,
                                                "reason": "beschreibt den Pattern-Sync, der in 6.0.0 abgebaut ist"}]})
    r = by(mem, "L3")
    check(rc == 0 and r is not None and r.get("superseded_by") == OBSOLETE_VALUE,
          f"L3.superseded_by == {OBSOLETE_VALUE} ({rc}, {out.get('error')}, {(r or {}).get('superseded_by')!r})")
    check(r.get("text") == text_before and len(rows_of(mem)) == 10, "text unchanged, no row removed (D-021)")
    entry = (r.get("update_log") or [{}])[-1]
    check(entry == {"date": "2026-11-16", "reason": "beschreibt den Pattern-Sync, der in 6.0.0 abgebaut ist",
                    "by": "pflegelauf", "field": "superseded_by", "old": None, "new": OBSOLETE_VALUE},
          f"update_log entry with date, reason, by, field, old, new ({entry})")
    check((out.get("tally") or {}).get("learning_updates") == {"superseded": 0, "cleared": 0, "review_after": 0,
                                                                "obsolete": 1},
          f"tally learning_updates.obsolete == 1 ({(out.get('tally') or {}).get('learning_updates')})")
    md_path = os.path.join(mem, "learnings", "learnings.md")
    md = open(md_path, encoding="utf-8").read() if os.path.exists(md_path) else ""
    check("**L3**" not in md and "**L4**" in md, "learnings.md projection no longer lists the obsolete row")
    rc, out = run([APPLY, mem, "--lint"])
    lint_ids = [x.get("id") for x in (out.get("lint") or [])]
    check(rc == 0 and "L3" not in lint_ids, f"--lint accepts the obsolete:pflegelauf- prefix ({rc}, {lint_ids})")
    sweep = subprocess.run([sys.executable, os.path.join(SCRIPTS, "review_sweep.py"), mem, "--candidates-json",
                            "--today", "2026-11-17"], capture_output=True, text=True, encoding="utf-8", env=ENV)
    due = {c["id"] for c in json.loads(sweep.stdout or "[]")}
    check("L3" not in due and "L4" in due, f"review_sweep --candidates-json leaves the obsolete row out ({sorted(due)})")
    # the value carries the PLAN date, not today's (L1: L4 is L5's live successor and stays protected)
    rc, out = apply(mem, {"date": "2026-10-05", "learning_updates": [{"id": "L1", "obsolete": True, "reason": "weg"}]})
    check(rc == 0 and by(mem, "L1").get("superseded_by") == "obsolete:pflegelauf-2026-10-05",
          f"obsolete value uses the plan date ({rc}, {by(mem, 'L1').get('superseded_by')!r})")
    # way back unchanged: superseded_by null clears it
    rc, out = apply(mem, {"learning_updates": [{"id": "L3", "superseded_by": None, "reason": "doch noch gültig"}]})
    r = by(mem, "L3")
    entry = (r.get("update_log") or [{}])[-1]
    check(rc == 0 and r.get("superseded_by") is None and entry.get("old") == OBSOLETE_VALUE and entry.get("new") is None
          and (out.get("tally") or {}).get("learning_updates", {}).get("cleared") == 1,
          f"superseded_by null takes 'gegenstandslos' back ({rc}, {out.get('error')}, {entry})")


def test_gegenstandslos_feedback():
    """R24: an approved feedback row (owner correction in MEMORY.md) cannot go 'gegenstandslos' - no successor would
    carry its content; superseded_by with a successor stays allowed, and a rejected feedback row may go."""
    print("--- test_gegenstandslos_feedback")
    refused({"learning_updates": [{"id": "L11", "obsolete": True, "reason": "weg"}]},
            "obsolete on approved feedback", expect="approved feedback", make=setup_feedback)
    _, mem = setup_feedback()
    rc, out = apply(mem, {"learning_updates": [{"id": "L12", "obsolete": True, "reason": "Mechanik weg"}]})
    check(rc == 0 and by(mem, "L12").get("superseded_by") == OBSOLETE_VALUE,
          f"obsolete on rejected feedback is allowed ({rc}, {out.get('error')})")
    # Codex 25.11.: a successor that is no approved feedback itself would drop the correction from MEMORY.md
    refused({"learning_updates": [{"id": "L11", "superseded_by": "L2", "reason": "L2 sagt es genauer"}]},
            "approved feedback superseded by a plain learning", expect="approved feedback", make=setup_feedback)
    _, mem = setup_feedback()
    rows = rows_of(mem) + [row("L13", "Owner-Korrektur, genauer: nie X tun, weil Y", 4, kind="feedback",
                               bridge_status="approved")]
    put(learn_path(mem), rows)
    rc, out = apply(mem, {"learning_updates": [{"id": "L11", "superseded_by": "L13", "reason": "L13 sagt es genauer"}]})
    check(rc == 0 and by(mem, "L11").get("superseded_by") == "L13",
          f"superseded_by on approved feedback with an approved feedback successor stays allowed ({rc}, {out.get('error')})")


def setup_regel_tag():
    """setup() plus L13: a legacy rule learning marked only by its tag (no rule_class flag, before the 5.8.0 gate)."""
    tmp, mem = setup()
    put(learn_path(mem), rows_of(mem) + [row("L13", "Alte Regel nur per Tag", 4, tags=["Regel"])])
    return tmp, mem


def test_regel_tag_geschuetzt():
    """Codex 25.11.: the schema marks a rule learning by flag OR tag; the run must protect a tag-only row too."""
    print("--- test_regel_tag_geschuetzt")
    refused({"learning_updates": [{"id": "L13", "obsolete": True, "reason": "weg"}]},
            "obsolete on a tag-only rule learning", expect="rule-class", make=setup_regel_tag)
    refused({"learning_updates": [{"id": "L13", "superseded_by": "L2", "reason": "doppelt"}]},
            "superseded_by on a tag-only rule learning", expect="rule-class", make=setup_regel_tag)
    refused({"learning_updates": [{"id": "L1", "superseded_by": "L13", "reason": "doppelt"}]},
            "a tag-only rule learning as target is unreleased", expect="unreleased", make=setup_regel_tag)
    _, mem = setup_regel_tag()
    rc, out = apply(mem, {"learning_updates": [{"id": "L13", "review_after": "2027-05-16", "reason": "bleibt"}]})
    check(rc == 0 and by(mem, "L13").get("review_after") == "2027-05-16", f"review_after stays allowed ({rc}, {out.get('error')})")


def test_regelquelle_bleibt_sichtbar():
    """6.0.2 (Task-10a review): 'gegenstandslos' on the derived_from source of a rule-class learning is refused -
    for a rule proposal of the same plan and for a live rule-class row of an earlier chunk."""
    print("--- test_regelquelle_bleibt_sichtbar")
    vorschlag = {"text": "Globale Regel aus L3", "summary": "Globale Regel aus L3", "importance": 4,
                 "tags": ["pflegelauf", "global"], "derived_from": ["L3"], "rule_class": True}
    refused({"learnings": [vorschlag], "learning_updates": [{"id": "L3", "obsolete": True, "reason": "weg"}]},
            "obsolete on the source of a rule proposal in the same plan", expect="derived_from")
    refused({"learnings": [dict(vorschlag, rule_class=False, tags=["regel"])],
             "learning_updates": [{"id": "L3", "obsolete": True, "reason": "weg"}]},
            "obsolete on the source of a tag-only rule proposal", expect="derived_from")

    def setup_regel_mit_quelle():
        tmp, mem = setup()
        put(learn_path(mem), rows_of(mem) + [row("L13", "Regel aus L3", 5, rule_class=True, derived_from=["L3"],
                                                 release={"status": "needs_explanation"})])
        return tmp, mem
    refused({"learning_updates": [{"id": "L3", "obsolete": True, "reason": "weg"}]},
            "obsolete on the source of a live rule-class row (earlier chunk)", expect="derived_from",
            make=setup_regel_mit_quelle)
    # what stays allowed: a plain learning as proposal source, and doppelt/überholt with a live successor
    _, mem = setup()
    rc, out = apply(mem, {"learnings": [dict(vorschlag, rule_class=False, tags=["pflegelauf"])],
                          "learning_updates": [{"id": "L3", "obsolete": True, "reason": "weg"}]})
    check(rc == 0, f"obsolete next to a plain (non-rule) learning stays allowed ({rc}, {out.get('error')})")
    _, mem = setup_regel_mit_quelle()
    rc, out = apply(mem, {"learning_updates": [{"id": "L3", "superseded_by": "L2", "reason": "doppelt"}]})
    check(rc == 0 and by(mem, "L3").get("superseded_by") == "L2",
          f"superseded_by on a rule source with a live successor stays allowed ({rc}, {out.get('error')})")


def test_nachfolger_bleibt_lebendig():
    """Codex 25.11.: chunk 1 writes L1 -> L2; a later chunk must not hide L2, or L1 points at nothing live."""
    print("--- test_nachfolger_bleibt_lebendig")

    def setup_kette():
        tmp, mem = setup()
        rc, _ = apply(mem, {"learning_updates": [{"id": "L1", "superseded_by": "L2", "reason": "doppelt"}]})
        assert rc == 0
        return tmp, mem
    refused({"learning_updates": [{"id": "L2", "obsolete": True, "reason": "weg"}]},
            "obsolete on the live successor of L1", expect="successor", make=setup_kette)
    refused({"learning_updates": [{"id": "L2", "superseded_by": "L3", "reason": "doppelt"}]},
            "superseding the live successor of L1 (chain)", expect="successor", make=setup_kette)
    _, mem = setup_kette()
    rc, out = apply(mem, {"learning_updates": [{"id": "L1", "superseded_by": "L3", "reason": "neu zugeordnet"},
                                               {"id": "L2", "obsolete": True, "reason": "weg"}]})
    check(rc == 0 and by(mem, "L1").get("superseded_by") == "L3" and by(mem, "L2").get("superseded_by") == OBSOLETE_VALUE,
          f"re-pointing L1 in the same plan frees L2 ({rc}, {out.get('error')})")
    _, mem = setup_kette()
    rc, out = apply(mem, {"learning_updates": [{"id": "L2", "review_after": "2027-05-16", "reason": "bleibt"}]})
    check(rc == 0, f"review_after on a successor stays allowed ({rc}, {out.get('error')})")
    # Codex 25.11. (Nachpruefung): only re-pointing or clearing L1 frees L2 - a review date on L1 keeps L1 -> L2
    refused({"learning_updates": [{"id": "L1", "review_after": "2027-05-16", "reason": "bleibt"},
                                  {"id": "L2", "obsolete": True, "reason": "weg"}]},
            "a review date on L1 does not free its successor L2", expect="successor", make=setup_kette)
    _, mem = setup_kette()
    rc, out = apply(mem, {"learning_updates": [{"id": "L1", "superseded_by": None, "reason": "doch eigenstaendig"},
                                               {"id": "L2", "obsolete": True, "reason": "weg"}]})
    check(rc == 0 and by(mem, "L1").get("superseded_by") is None,
          f"clearing L1 in the same plan frees L2 ({rc}, {out.get('error')})")


def test_gegenstandslos_verweigert():
    """F1: the same protections as superseded_by - rule_class, pointer, pointer member (also of the same plan),
    a row that is already superseded; and exactly one of superseded_by | review_after | obsolete."""
    print("--- test_gegenstandslos_verweigert")
    refused({"learning_updates": [{"id": "L6", "obsolete": True, "reason": "weg"}]},
            "obsolete on a rule_class learning", expect="rule-class learning")
    refused({"learning_updates": [{"id": "L7", "obsolete": True, "reason": "weg"}]},
            "obsolete on a pointer row", expect="pointer row")
    refused({"learning_updates": [{"id": "L8", "obsolete": True, "reason": "weg"}]},
            "obsolete on a pointer member", expect="member of a rule pointer")
    rules = os.path.join(tempfile.mkdtemp(prefix="lupd-rules-"), "rules.md")
    put(rules, "# Regeln\n\n## Abschnitt\nText\n")
    refused({"principles": [{"summary": "Regel", "anchor": f"{rules}#Abschnitt", "members": ["L3"],
                             "plain": "Eine Regel in einfachen Worten."}],
             "learning_updates": [{"id": "L3", "obsolete": True, "reason": "weg"}]},
            "obsolete on a member of a pointer written by the same plan", expect="member of a rule pointer")
    refused({"learning_updates": [{"id": "L5", "obsolete": True, "reason": "weg"}]},
            "obsolete on a row that is already superseded", expect="already superseded")
    refused({"learning_updates": [{"id": "L1", "obsolete": True, "superseded_by": "L2", "reason": "x"}]},
            "obsolete and superseded_by in one entry", expect="exactly one of")
    refused({"learning_updates": [{"id": "L1", "obsolete": True, "review_after": "2027-05-16", "reason": "x"}]},
            "obsolete and review_after in one entry", expect="exactly one of")
    refused({"learning_updates": [{"id": "L1", "obsolete": False, "reason": "x"}]},
            "obsolete false", expect="obsolete must be true")
    refused({"learning_updates": [{"id": "L1", "obsolete": "ja", "reason": "x"}]},
            "obsolete not a bool", expect="obsolete must be true")
    refused({"learning_updates": [{"id": "L1", "obsolete": True}]}, "obsolete without reason", expect="reason")
    # a target the same plan declares obsolete is no live successor (no chains)
    refused({"learning_updates": [{"id": "L2", "obsolete": True, "reason": "weg"},
                                  {"id": "L1", "superseded_by": "L2", "reason": "doppelt"}]},
            "target declared obsolete by the same plan", expect="obsolete by the same plan")


# ============================================================ protection rules
def test_rule_class_verweigert():
    print("--- test_rule_class_verweigert")
    refused({"learning_updates": [{"id": "L6", "superseded_by": "L2", "reason": "doppelt"}]}, "rule_class learning")
    refused({"learning_updates": [{"id": "L6", "superseded_by": None, "reason": "zurück"}]},
            "rule_class learning, clearing too", expect="rule-class learning")


def test_principle_verweigert():
    print("--- test_principle_verweigert")
    refused({"learning_updates": [{"id": "L7", "superseded_by": "L2", "reason": "doppelt"}]}, "pointer row")
    refused({"learning_updates": [{"id": "L7", "superseded_by": None, "reason": "zurück"}]},
            "pointer row, clearing too", expect="pointer row")


def test_zeiger_mitglied_verweigert():
    print("--- test_zeiger_mitglied_verweigert")
    refused({"learning_updates": [{"id": "L8", "superseded_by": "L2", "reason": "doppelt"}]}, "member of a live pointer")
    refused({"learning_updates": [{"id": "L8", "superseded_by": None, "reason": "zurück"}]},
            "member of a live pointer, clearing too", expect="member of a rule pointer")
    # a member the same plan's `principles` adds is a member as well
    rules = os.path.join(tempfile.mkdtemp(prefix="lupd-rules-"), "rules.md")
    put(rules, "# Regeln\n\n## Abschnitt\nText\n")
    refused({"principles": [{"summary": "Regel", "anchor": f"{rules}#Abschnitt", "members": ["L3"],
                             "plain": "Eine Regel in einfachen Worten."}],
             "learning_updates": [{"id": "L3", "superseded_by": "L2", "reason": "doppelt"}]},
            "member of a pointer written by the same plan")


def test_review_after_auf_geschuetzten_zeilen():
    """R13 (spec 5): the protections hold for superseded_by only; review_after on these rows is allowed."""
    print("--- test_review_after_auf_geschuetzten_zeilen")
    for lid, what in (("L6", "rule_class row"), ("L7", "pointer row"), ("L8", "pointer member")):
        _, mem = setup()
        rc, out = apply(mem, {"learning_updates": [{"id": lid, "review_after": "2027-05-16", "reason": "bleibt"}]})
        r = by(mem, lid)
        check(rc == 0 and r.get("review_after") == "2027-05-16" and r.get("superseded_by") is None,
              f"review_after on a {what} is set ({rc}, {out.get('error')})")


def test_ziel_geschuetzt_verweigert():
    """R14: a target must be a live, plain, released learning the same plan does not retire."""
    print("--- test_ziel_geschuetzt_verweigert")
    refused({"learning_updates": [{"id": "L1", "superseded_by": "L7", "reason": "doppelt"}]},
            "target is a pointer row", expect="target L7 is a rule pointer")
    refused({"learning_updates": [{"id": "L1", "superseded_by": "L9", "reason": "doppelt"}]},
            "target bridge_status rejected", expect="bridge_status 'rejected'")
    refused({"learning_updates": [{"id": "L1", "superseded_by": "L10", "reason": "doppelt"}]},
            "target bridge_status retired", expect="bridge_status 'retired'")
    refused({"learning_updates": [{"id": "L1", "superseded_by": "L6", "reason": "doppelt"}]},
            "target is an unreleased rule-class learning", expect="unreleased rule-class")
    refused({"principle_retire": [{"ids": ["L7"], "reason": "x"}],
             "learning_updates": [{"id": "L1", "superseded_by": "L7", "reason": "doppelt"}]},
            "target retired by principle_retire in the same plan", expect="principle_retire in the same plan")


def test_kette_verweigert():
    print("--- test_kette_verweigert")
    refused({"learning_updates": [{"id": "L1", "superseded_by": "L5", "reason": "doppelt"}]}, "target already superseded")
    refused({"learning_updates": [{"id": "L2", "superseded_by": "L3", "reason": "doppelt"},
                                  {"id": "L1", "superseded_by": "L2", "reason": "doppelt"}]},
            "target superseded by the same plan")


def test_selbstbezug_verweigert():
    print("--- test_selbstbezug_verweigert")
    refused({"learning_updates": [{"id": "L1", "superseded_by": "L1", "reason": "doppelt"}]}, "self reference")


def test_kreis_im_plan_verweigert():
    print("--- test_kreis_im_plan_verweigert")
    refused({"learning_updates": [{"id": "L1", "superseded_by": "L2", "reason": "doppelt"},
                                  {"id": "L2", "superseded_by": "L1", "reason": "doppelt"}]}, "A->B and B->A in one plan")


def test_unbekannte_id_verweigert():
    print("--- test_unbekannte_id_verweigert")
    refused({"learning_updates": [{"id": "L404", "superseded_by": "L2", "reason": "doppelt"}]}, "unknown id")
    refused({"learning_updates": [{"id": "L1", "superseded_by": "L404", "reason": "doppelt"}]}, "unknown target")
    refused({"learning_updates": [{"id": "dome-dynamics/L1", "superseded_by": "L2", "reason": "doppelt"}]},
            "qualified id")
    refused({"learning_updates": [{"id": "L1", "superseded_by": "obsolete:weg", "reason": "doppelt"}]},
            "target is no id (proof prefix)")


def test_reason_fehlt_verweigert():
    print("--- test_reason_fehlt_verweigert")
    refused({"learning_updates": [{"id": "L1", "superseded_by": "L2"}]}, "reason missing")
    refused({"learning_updates": [{"id": "L1", "superseded_by": "L2", "reason": "   "}]}, "reason blank")
    refused({"learning_updates": [{"id": "L1", "superseded_by": "L2", "reason": 7}]}, "reason not a string")
    refused({"learning_updates": [{"id": "L1", "superseded_by": "L2", "reason": "x" * 301}]}, "reason > 300 characters")


def test_beides_gesetzt_verweigert():
    print("--- test_beides_gesetzt_verweigert")
    refused({"learning_updates": [{"id": "L1", "superseded_by": "L2", "review_after": "2027-05-16", "reason": "x"}]},
            "superseded_by and review_after together")
    refused({"learning_updates": [{"id": "L1", "reason": "x"}]}, "neither superseded_by nor review_after")
    refused({"learning_updates": [{"id": "L1", "review_after": "2027-5-16", "reason": "x"}]}, "review_after not YYYY-MM-DD")
    refused({"learning_updates": [{"id": "L1", "review_after": "2027-02-30", "reason": "x"}]}, "review_after no real date")


def test_form_verweigert():
    print("--- test_form_verweigert")
    refused({"learning_updates": {"id": "L1"}}, "section is not a list")
    refused({"learning_updates": [{"id": "L1", "review_after": "2027-05-16", "reason": "x", "text": "neu"}]},
            "unknown field (text is never changed here)")
    refused({"learning_updates": [{"id": "L1", "review_after": "2027-05-16", "reason": "x"},
                                  {"id": "L1", "review_after": "2027-06-16", "reason": "y"}]}, "same id twice")
    refused({"learning_updates": [], "pflegelauf_stamp": "ja"}, "pflegelauf_stamp not a bool")


# ============================================================ stamp
def test_stempel():
    print("--- test_stempel")
    _, mem = setup()
    rc, out = apply(mem, {"learning_updates": [{"id": "L1", "superseded_by": "L2", "reason": "doppelt"}],
                          "pflegelauf_stamp": True})
    with open(learn_path(mem), encoding="utf-8-sig") as fh:
        text = fh.read()
    stamp = json.load(open(stamp_path(mem), encoding="utf-8")) if os.path.exists(stamp_path(mem)) else {}
    check(rc == 0 and stamp == {"date": "2026-11-16", "id_keys": text.count('"id"'), "zaehlung": 2},
          f"stamp = date + id_keys counted on the written file ({rc}, {stamp}, {text.count(chr(34) + 'id' + chr(34))})")
    check(any("pflegelauf.json" in str(f) for f in out.get("files_written", [])), "files_written names the stamp")
    # a maintenance run that found nothing still resets the counter
    _, mem = setup()
    before = sha(learn_path(mem))
    rc, out = apply(mem, {"pflegelauf_stamp": True})
    with open(learn_path(mem), encoding="utf-8-sig") as fh:
        text = fh.read()
    stamp = json.load(open(stamp_path(mem), encoding="utf-8")) if os.path.exists(stamp_path(mem)) else {}
    check(rc == 0 and sha(learn_path(mem)) == before and stamp.get("id_keys") == text.count('"id"') == 10,
          f"stamp-only plan: store untouched, stamp counts the current file ({rc}, {stamp})")
    # 6.0.2 (Codex): a learning dated on/after the run's start date (written by another session during a run of
    # several days) stays out of the base, so the next trigger counts it as new; a tag "id" is no key
    _, mem = setup()
    put(learn_path(mem), rows_of(mem) + [row("L11", "Kam waehrend des Laufs", date="2026-11-16"),
                                         row("L12", "Nach Mitternacht", date="2026-11-17T00:30"),
                                         row("L13", "Tag heisst id", tags=["id"]),
                                         row("L14", "Kein Datum", date="kein Datum")])
    rc, out = apply(mem, {"pflegelauf_stamp": True})
    stamp = json.load(open(stamp_path(mem), encoding="utf-8")) if os.path.exists(stamp_path(mem)) else {}
    check(rc == 0 and stamp.get("id_keys") == 12,
          f"base = rows dated before 2026-11-16 (10 + L13) plus L14 without a readable date ({rc}, {stamp})")
    # Codex 6.0.2 re-check: the stamp counts the file text the trigger counts - also "id" at the start of a line
    _, mem = setup()
    raw = "[\n" + ",\n".join('{\n"id"\n: "L%d",\n"date": "2026-08-01",\n"text": "t"\n}' % i for i in range(1, 8)) + "\n]\n"
    with open(learn_path(mem), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(raw)
    rc, out = apply(mem, {"pflegelauf_stamp": True})
    stamp = json.load(open(stamp_path(mem), encoding="utf-8")) if os.path.exists(stamp_path(mem)) else {}
    check(rc == 0 and stamp.get("id_keys") == 7 and stamp.get("zaehlung") == 2,
          f"unindented file, 'id' and colon on two lines: 7 rows counted ({rc}, {stamp})")
    # no stamp unless asked for
    _, mem = setup()
    apply(mem, {"learning_updates": [{"id": "L3", "review_after": "2027-05-16", "reason": "bleibt"}]})
    check(not os.path.exists(stamp_path(mem)), "no pflegelauf_stamp -> no stamp file")


def test_dry_run_schreibt_nichts():
    print("--- test_dry_run_schreibt_nichts")
    _, mem = setup()
    before = sha(learn_path(mem))
    rc, out = apply(mem, {"learning_updates": [{"id": "L1", "superseded_by": "L2", "reason": "doppelt"}],
                          "pflegelauf_stamp": True}, extra=["--dry-run"])
    check(rc == 0 and sha(learn_path(mem)) == before and not os.path.exists(stamp_path(mem))
          and not os.path.exists(os.path.join(mem, "learnings", "learnings.md")),
          f"--dry-run writes nothing, stamp included ({rc}, {out.get('error')})")
    check((out.get("tally") or {}).get("learning_updates", {}).get("superseded") == 1, "dry run still reports the tally")


for fn in (test_superseded_wird_gesetzt, test_zuruecknehmen, test_review_after, test_bleibt_kommt_wieder, test_unveraendert_schreibt_nichts,
           test_gegenstandslos_wird_gesetzt, test_gegenstandslos_verweigert, test_gegenstandslos_feedback,
           test_regel_tag_geschuetzt, test_regelquelle_bleibt_sichtbar, test_nachfolger_bleibt_lebendig,
           test_rule_class_verweigert, test_principle_verweigert, test_zeiger_mitglied_verweigert,
           test_review_after_auf_geschuetzten_zeilen, test_ziel_geschuetzt_verweigert,
           test_kette_verweigert, test_selbstbezug_verweigert, test_kreis_im_plan_verweigert,
           test_unbekannte_id_verweigert, test_reason_fehlt_verweigert, test_beides_gesetzt_verweigert,
           test_form_verweigert, test_stempel, test_dry_run_schreibt_nichts):
    fn()

print(f"=== Results: {PASSED}/{TESTS} passed, {ERRORS} failures ===")
sys.exit(1 if ERRORS else 0)

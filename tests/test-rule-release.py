#!/usr/bin/env python3
"""Tests for the learnings mixed form (DCO #9869, owner decision F4, 2026-10-22).

Normal learnings: the agent curates itself (bridge_decisions, one line each).
Rule-class learnings (they change a rule or the constitution): a second model
explains them (Erklaerbogen), and only the owner releases them - after he could
put the learning in two sentences of his own. Mechanics follow the plugin
convention: a mark in the store + a read-only queue/sheet, no live model call.

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
APPLY = os.path.join(ROOT, "scripts", "apply_wrapup.py")
RELEASE = os.path.join(ROOT, "scripts", "learning_release.py")
BRIDGE = os.path.join(ROOT, "scripts", "bridge_projection.py")
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


def getj(mem, rel):
    with open(os.path.join(mem, rel), encoding="utf-8") as fh:
        return json.load(fh)


def make_mem():
    mem = tempfile.mkdtemp(prefix="rel-test-")
    put(mem, "learnings/learnings.json", [{
        "id": "L1", "date": "2026-01-01", "text": "Normales Learning zum Vergleich", "importance": 4,
        "tags": ["x"], "layer": "short-term", "superseded_by": None, "last_relevant": "2026-01-01",
        "bridge_status": "candidate"}])
    put(mem, "iterations/iteration-log.md", "# Iteration Log\n")
    put(mem, "iterations/errors.json", [])
    put(mem, "context/decisions.json", [])
    return mem


def run(mem, plan, *extra):
    p = subprocess.run([sys.executable, APPLY, mem, "--session-id", "s-test", *extra],
                       input=json.dumps(plan, ensure_ascii=False), capture_output=True,
                       encoding="utf-8", errors="replace", env=ENV)
    try:
        out = json.loads(p.stdout)
    except ValueError:
        out = {"_raw": p.stdout, "_err": p.stderr}
    return p.returncode, out


def rows(mem):
    return {r["id"]: r for r in getj(mem, "learnings/learnings.json")}


RULE_TEXT = "Bei Reversiblem und Internem entscheidet der Agent selbst und meldet danach eine Zeile."
PLAIN = "Das ist eine Regel der Klasse Verfassung und braucht deshalb den Weg ueber die Zweitmodell-Erklaerung."  # P1: Klartext an jeder Owner-Vorlage
EXPL = {"id": "L2", "explained_by": "codex-gpt-5", "plain": "Der Agent darf kleine, umkehrbare Dinge ohne Rueckfrage "
        "erledigen und muss sie nur melden. Grosse oder unumkehrbare Dinge bleiben beim Owner.",
        "risk": "Der Agent koennte etwas fuer reversibel halten, das es nicht ist.", "verdict": "confirms",
        "affects": ["regeln/gemeinsam.md#delegierte-entscheidungen"]}
OWNER = {"id": "L2", "by": "owner", "two_sentences": "Claude darf Kleinkram selbst entscheiden und sagt mir danach Bescheid. "
         "Alles, was sich schwer zurueckdrehen laesst, fragt er vorher."}


def with_rule(mem=None, **extra):
    mem = mem or make_mem()
    lrn = {"text": RULE_TEXT, "importance": 2, "tags": ["workflow"], "rule_class": True}
    lrn.update(extra)
    code, out = run(mem, {"date": "2026-10-24", "learnings": [lrn]})
    assert code == 0, out
    return mem, out


def main():
    print("== marking: rule-class goes into the gate, never straight to the bridge")
    mem, out = with_rule()
    r = rows(mem)["L2"]
    check(r.get("rule_class") is True and r.get("release") == {"status": "needs_explanation"},
          "rule_class learning gets release.status = needs_explanation")
    check(r.get("bridge_status") == "candidate", "...and is a bridge candidate although importance is 2")
    check(out["tally"]["bridge_candidates"] == ["L1"], "...but is NOT offered to the agent in tally.bridge_candidates")
    check(out["tally"]["rule_class_open"] == {"needs_explanation": ["L2"], "explained": []},
          "tally.rule_class_open lists it")
    check("Regel-Learnings" in out.get("rule_class_status_line", ""), "result carries the report line")

    mem, out = with_rule(make_mem(), rule_class=False, tags=["Verfassung"])
    check(rows(mem)["L2"].get("rule_class") is True, "tag 'Verfassung' marks the class even without the flag")
    mem, out = with_rule(make_mem(), rule_class=False, tags=["x"], importance=4)
    check("rule_class" not in rows(mem)["L2"] and rows(mem)["L2"]["bridge_status"] == "candidate",
          "a normal learning is untouched by the gate")
    code, out = run(make_mem(), {"learnings": [{"text": "Ein Learning mit schlechtem Flag", "rule_class": "ja"}]})
    check(code == 2, "rule_class must be a JSON bool")

    print("== the agent cannot approve a rule-class learning")
    mem, _ = with_rule()
    code, out = run(mem, {"bridge_decisions": [{"id": "L2", "status": "approved", "note": "sieht gut aus", "plain": PLAIN}]})
    check(code == 2 and "rule" in json.dumps(out).lower(), "bridge_decisions approved on rule-class: plan rejected")
    code, out = run(mem, {"bridge_decisions": [{"id": "L2", "status": "approved", "note": "ich bin Owner", "by": "owner", "plain": PLAIN}]})
    check(code == 2, "...also with by=owner (release goes only through rule_releases)")
    code, out = run(mem, {"bridge_decisions": [{"id": "L2", "status": "rejected", "note": "unnoetig", "plain": PLAIN}]})
    check(code == 0 and rows(mem)["L2"]["bridge_status"] == "rejected" and rows(mem)["L2"]["release"]["status"] == "rejected",
          "rejecting is always allowed and closes the gate state")

    print("== second model explains (Erklaerbogen)")
    mem, _ = with_rule()
    code, out = run(mem, {"date": "2026-10-24", "rule_explanations": [EXPL]})
    r = rows(mem)["L2"]
    check(code == 0 and r["release"]["status"] == "explained" and r["release"]["explained_by"] == "codex-gpt-5"
          and r["release"]["explained_at"] == "2026-10-24", "explanation stored with model and date")
    check(r["release"]["sheet"]["verdict"] == "confirms" and r["release"]["sheet"]["plain"].startswith("Der Agent darf"),
          "sheet fields kept")
    check(r["bridge_status"] == "candidate", "explained is still not approved")
    check(out["tally"]["rule_class_open"] == {"needs_explanation": [], "explained": ["L2"]}, "queue moved to 'explained'")
    bad = {
        "unknown id": dict(EXPL, id="L99"),
        "not rule-class": dict(EXPL, id="L1"),
        "no model": {k: v for k, v in EXPL.items() if k != "explained_by"},
        "blank plain": dict(EXPL, plain=" "),
        "no risk": {k: v for k, v in EXPL.items() if k != "risk"},
        "bad verdict": dict(EXPL, verdict="gut"),
    }
    for name, e in bad.items():
        m, _ = with_rule()
        before = json.dumps(getj(m, "learnings/learnings.json"))
        code, out = run(m, {"rule_explanations": [e]})
        check(code == 2 and json.dumps(getj(m, "learnings/learnings.json")) == before, f"explanation refused: {name}")

    print("== owner release needs two sentences of his own words")
    refusals = {
        "not explained yet": (None, OWNER),
        "by agent": (EXPL, dict(OWNER, by="agent")),
        "one sentence": (EXPL, dict(OWNER, two_sentences="Das ist in Ordnung so wie es dasteht.")),
        "four sentences": (EXPL, dict(OWNER, two_sentences="Eins ist da. Zwei ist da. Drei ist da. Vier ist da.")),
        "copied from the sheet": (EXPL, dict(OWNER, two_sentences=EXPL["plain"])),
        "fragments": (EXPL, dict(OWNER, two_sentences="Ja. Gut.")),
        "missing": (EXPL, {k: v for k, v in OWNER.items() if k != "two_sentences"}),
    }
    for name, (expl, rel) in refusals.items():
        m, _ = with_rule()
        if expl:
            run(m, {"rule_explanations": [expl]})
        before = json.dumps(getj(m, "learnings/learnings.json"))
        code, out = run(m, {"rule_releases": [rel]})
        check(code == 2 and json.dumps(getj(m, "learnings/learnings.json")) == before, f"release refused: {name}")

    print("== release: approved only now, and visible to the projections")
    mem, _ = with_rule()
    agents = os.path.join(os.path.dirname(mem), "AGENTS-" + os.path.basename(mem) + ".md")
    subprocess.run([sys.executable, BRIDGE, mem, "--agents-md", agents], capture_output=True, env=ENV)
    check(not os.path.exists(agents) or "L2" not in open(agents, encoding="utf-8").read(),
          "before the release the rule is NOT in the AGENTS.md block")
    run(mem, {"date": "2026-10-24", "rule_explanations": [EXPL]})
    code, out = run(mem, {"date": "2026-10-25", "rule_releases": [OWNER]})
    r = rows(mem)["L2"]
    check(code == 0 and r["release"]["status"] == "released" and r["release"]["released_by"] == "owner"
          and r["release"]["released_at"] == "2026-10-25" and r["release"]["two_sentences"] == OWNER["two_sentences"],
          "release stored with owner, date and his two sentences")
    check(r["bridge_status"] == "approved" and r["bridge_note"].startswith("2026-10-25 owner:"), "bridge approved, note names the owner")
    check(out["tally"]["bridge_status_changed"] == 1 and out["tally"]["bridge_approved"] == ["L2"],
          "tally asks for the projections (bridge_status_changed)")
    subprocess.run([sys.executable, BRIDGE, mem, "--agents-md", agents], capture_output=True, env=ENV)
    check("[L2]" in open(agents, encoding="utf-8").read(), "after the release the rule is in the AGENTS.md block")
    code, out = run(mem, {"date": "2026-10-26", "rule_releases": [OWNER]})
    check(code == 2, "a released rule cannot be released again (state is not 'explained')")

    print("== owner can reject at any stage")
    mem, _ = with_rule()
    code, out = run(mem, {"rule_releases": [{"id": "L2", "by": "owner", "status": "rejected", "note": "verstehe ich nicht"}]})
    r = rows(mem)["L2"]
    check(code == 0 and r["bridge_status"] == "rejected" and r["release"]["status"] == "rejected", "rejection needs no explanation first")

    print("== correcting a rule's wording restarts the gate")
    mem, _ = with_rule()
    run(mem, {"rule_explanations": [EXPL]})
    run(mem, {"rule_releases": [OWNER]})
    code, out = run(mem, {"date": "2026-10-27", "corrections": {"learnings": [
        {"id": "L2", "text": RULE_TEXT + " Schwer Umkehrbares bleibt Owner-Gate.", "reason": "Ergaenzung"}]}})
    r = rows(mem)["L2"]
    check(code == 0 and r["release"]["status"] == "needs_explanation" and r["bridge_status"] == "candidate",
          "released rule + changed text: back to needs_explanation, out of the bridge")
    check(out["tally"]["bridge_status_changed"] == 1 and out["tally"]["corrections_projection_needed"] is False,
          "the demotion is reported so the projections get re-run")

    print("== a principle pointer cannot route a rule-class learning around the gate (DCO #10057 (1))")
    anchor_dir = tempfile.mkdtemp(prefix="rel-anchor-")
    rules_md = os.path.join(anchor_dir, "regeln.md")
    put(anchor_dir, "regeln.md", "# Regeln\n\n## Delegation\nReversibles entscheidet der Agent.\n\n## Zweiter Abschnitt\nText.\n")
    # D11: a pointer card carries `plain`; without it the rejections below would not be about the gate
    princ = {"summary": "Reversibles selbst entscheiden, danach melden", "anchor": f"{rules_md}#Delegation",
             "members": ["L2"], "plain": "Kleine, umkehrbare Dinge entscheidet der Agent allein und meldet es danach."}

    def pointers(m):
        return [r for r in getj(m, "learnings/learnings.json") if r.get("kind") == "principle"]

    mem, _ = with_rule()
    code, out = run(mem, {"date": "2026-10-24", "principles": [princ]})
    check(code == 2 and not pointers(mem), "needs_explanation member: principle plan rejected, no pointer written")
    check("rule_releases" in json.dumps(out) or "rule-class" in json.dumps(out), "...and the refusal names the gate")
    run(mem, {"date": "2026-10-24", "rule_explanations": [EXPL]})
    code, out = run(mem, {"date": "2026-10-24", "principles": [princ]})
    check(code == 2 and not pointers(mem), "explained (owner has not released): still rejected")
    code, out = run(mem, {"date": "2026-10-24", "principles": [dict(princ, members=["L1", "L2"])]})
    check(code == 2 and not pointers(mem), "a rule-class member hidden among normal members: rejected")
    code, out = run(mem, {"date": "2026-10-24", "principles": [dict(princ, members=["L1"])]})
    check(code == 0 and len(pointers(mem)) == 1, "control: a pointer over normal learnings still works")
    code, out = run(mem, {"date": "2026-10-24", "principles": [dict(princ, members=["L2"])]})
    check(code == 2 and pointers(mem)[0]["derived_from"] == ["L1"],
          "merging the rule-class member into an existing pointer: rejected, pointer unchanged")
    run(mem, {"date": "2026-10-25", "rule_releases": [OWNER]})
    code, out = run(mem, {"date": "2026-10-25", "principles": [dict(princ, members=["L2"])]})
    check(code == 0 and pointers(mem)[0]["derived_from"] == ["L1", "L2"],
          "after the owner's release the member may be folded into the pointer")

    mem, _ = with_rule()
    rows_now = getj(mem, "learnings/learnings.json")
    rows_now.append({"id": "L9", "date": "2026-10-24", "kind": "principle", "text": "x", "summary": "Hand gebauter Zeiger",
                     "anchor": f"{rules_md}#Delegation", "importance": 3, "tags": [], "layer": "long-term",
                     "superseded_by": None, "last_relevant": "2026-10-24", "derived_from": ["L2"],
                     "bridge_status": "approved"})
    put(mem, "learnings/learnings.json", rows_now)
    agents = os.path.join(mem, "AGENTS.md")
    memmd = os.path.join(mem, "MEMORY.md")
    subprocess.run([sys.executable, BRIDGE, mem, "--agents-md", agents], capture_output=True, env=ENV)
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "memory_index_projection.py"), mem,
                    "--memory-md", memmd], capture_output=True, env=ENV)
    seen = "".join(open(f, encoding="utf-8").read() for f in (agents, memmd) if os.path.exists(f))
    check("Hand gebauter Zeiger" not in seen and "[L9]" not in seen,
          "projection guard: a hand-written pointer over an unreleased rule-class member is not projected")

    print("== 'by: owner' is described as the honor system it is (DCO #10057 (3))")
    # CLAUDE.md is not part of the public export (release/freigabe.txt): skip its checks there.
    has_claude_md = os.path.exists(os.path.join(ROOT, "CLAUDE.md"))
    for rel in ("skills/wrap-up/SKILL.md", "skills/wrap-up/references/wrapup-schemas.md", "CLAUDE.md"):
        if rel == "CLAUDE.md" and not has_claude_md:
            print("  SKIP: CLAUDE.md not found (public export)")
            continue
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            check("honor system" in fh.read().lower(), f"{rel} calls 'by: owner' an honor system / self-commitment")
    if has_claude_md:
        with open(os.path.join(ROOT, "CLAUDE.md"), encoding="utf-8") as fh:
            claude_md = fh.read()
        check("not a lock the script enforces" in claude_md, "CLAUDE.md: 'never on the agent's say-so' is stated as a commitment, not a technical lock (DCO #10062 (3))")
    import re as _re
    with open(os.path.join(ROOT, "scripts", "projection_text.py"), encoding="utf-8") as fh:
        glued = [i for i, ln in enumerate(fh, 1) if _re.search(r"\S {10,}\S", ln) and "#" not in ln]
    check(not glued, f"projection_text.py: no code line with a run of 10+ spaces (merge accident, DCO #10062 (2)): {glued}")
    mem, _ = with_rule()
    run(mem, {"rule_explanations": [EXPL]})
    code, out = run(mem, {"rule_releases": [dict(OWNER, by="agent")]})
    check(code == 2 and "self-declaration" in json.dumps(out), "the refusal says the field is a self-declaration the script cannot verify")

    print("== learning_release.py (read-only queue + sheet)")
    mem, _ = with_rule()
    snap = json.dumps(getj(mem, "learnings/learnings.json"))
    p = subprocess.run([sys.executable, RELEASE, "list", mem, "--json"], capture_output=True, encoding="utf-8",
                       errors="replace", env=ENV)
    lst = json.loads(p.stdout)
    check(p.returncode == 0 and lst["needs_explanation"][0]["id"] == "L2" and lst["explained"] == [], "list shows the queue")
    p = subprocess.run([sys.executable, RELEASE, "sheet", mem, "L2"], capture_output=True, encoding="utf-8",
                       errors="replace", env=ENV)
    sheet = p.stdout
    check(p.returncode == 0 and "L2" in sheet and RULE_TEXT in sheet, "sheet quotes id and full text")
    check("Zwei Sätze" in sheet and "rule_releases" in sheet and "rule_explanations" in sheet,
          "sheet has the two-sentence field and both plan snippets")
    check("anderes Modell" in sheet or "Zweitmodell" in sheet, "sheet addresses the second model")
    p = subprocess.run([sys.executable, RELEASE, "sheet", mem, "L1"], capture_output=True, encoding="utf-8",
                       errors="replace", env=ENV)
    check(p.returncode == 2, "sheet for a normal learning is refused")
    check(json.dumps(getj(mem, "learnings/learnings.json")) == snap, "read-only: store unchanged")

    print(f"=== Results: {PASSED}/{TESTS} passed, {ERRORS} failures ===")
    return 1 if ERRORS else 0


if __name__ == "__main__":
    sys.exit(main())

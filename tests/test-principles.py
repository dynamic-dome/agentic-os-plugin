#!/usr/bin/env python3
"""Tests for the condensation path (T-031, plan phase 5 shortened, D-021 (3)+(4)).

Covers: the `principles` plan section of apply_wrapup.py (pointer rows), anchor
resolution, the MEMORY.md projection with the block at the START (pointers
first, members folded, codex capped, importance before date) and
learnings_lifecycle.py propose/report (read-only).

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
PROJ = os.path.join(SCRIPTS, "memory_index_projection.py")
LIFE = os.path.join(SCRIPTS, "learnings_lifecycle.py")
sys.path.insert(0, SCRIPTS)
from _isolated_home import process_env  # noqa: E402

# Subprozesse laufen mit frischem Temp-Home statt mit dem echten Home (P0, tests/_isolated_home.py)
ENV, ISO_HOME = process_env(PYTHONIOENCODING="utf-8")
BEGIN = "<!-- bridge:claude-native:begin"
END = "<!-- bridge:claude-native:end -->"
# D11: every pointer card carries `plain` (plain German for the owner, 1..600 characters), stored on the pointer
PLAIN = "In Windows-Skripten immer den Programmpfad vorher suchen und die Textcodierung ausdruecklich auf UTF-8 setzen."

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


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


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
    tmp = tempfile.mkdtemp(prefix="princ-test-")
    mem = os.path.join(tmp, ".agent-memory")
    rules = os.path.join(tmp, "rules.md")
    put(rules, "# Regeln\n\n## Windows-Subprocess\n- shutil.which vor run\n- Popen text=True ist cp1252\n\n"
               "## Verifikation vor Aktion\nExit 0 ist kein Beweis.\n\n- Regel mit Block ^r-trust\n")
    put(os.path.join(mem, "learnings", "learnings.json"), [
        row("L1", "subprocess.run findet npm nicht ohne shutil.which unter Windows", 5, bridge_status="approved"),
        row("L2", "Popen mit text=True dekodiert unter Windows als cp1252, explizit utf-8 setzen", 4,
            bridge_status="approved"),
        row("L3", "Exit-Code 0 eines Checkers bewies nichts, die Evidenzzeilen zeigten den Fehler", 4,
            date="2026-09-01", bridge_status="approved"),
        row("L4", "Codex Tipp eins", 2, bridge_status="approved", source_agent="codex"),
        row("L5", "Codex Tipp zwei", 2, bridge_status="approved", source_agent="codex"),
        row("L6", "Codex Tipp drei", 2, bridge_status="approved", source_agent="codex"),
        row("L7", "Codex Tipp vier", 2, date="2026-09-30", bridge_status="approved", source_agent="codex"),
        row("L8", "Ein unwichtiges neues Learning", 1, date="2026-10-01", bridge_status="approved"),
    ])
    return tmp, mem, rules


# ============================================================ 1. anchor resolution
print("--- anchors")
import projection_text as pt  # noqa: E402

tmp, mem, rules = setup()
check(pt.anchor_ok(f"{rules}#Windows-Subprocess"), "heading anchor resolves")
check(pt.anchor_ok(f"{rules}#^r-trust"), "block-id anchor resolves")
check(not pt.anchor_ok(f"{rules}#Gibt es nicht"), "missing heading -> not ok")
check(not pt.anchor_ok(os.path.join(tmp, "fehlt.md") + "#X"), "missing file -> not ok")
check(pt.anchor_ok(rules), "file-only anchor resolves")

# ============================================================ 2. principles section
print("--- principles plan section")
members_before = json.load(open(os.path.join(mem, "learnings", "learnings.json"), encoding="utf-8"))
plan = {"date": "2026-10-08", "principles": [
    {"summary": "Windows-Subprocess: Binary per shutil.which aufloesen, Encoding explizit utf-8.",
     "anchor": f"{rules}#Windows-Subprocess", "members": ["L1", "L2"], "tags": ["windows"], "plain": PLAIN}]}
rc, out = run([APPLY, mem], json.dumps(plan))
rows = json.load(open(os.path.join(mem, "learnings", "learnings.json"), encoding="utf-8"))
ptr = [r for r in rows if r.get("kind") == "principle"]
check(rc == 0 and len(ptr) == 1, f"one pointer row written ({rc}, {out.get('error')})")
p = ptr[0] if ptr else {}
check(p.get("id") == "L9" and p.get("anchor", "").endswith("#Windows-Subprocess")
      and p.get("derived_from") == ["L1", "L2"] and p.get("importance") == 5,
      f"pointer: next L-id, anchor, members in derived_from, importance = max member ({p})")
check("review_after" not in p and p.get("summary", "").startswith("Windows-Subprocess"),
      "pointer: summary set, no review_after")
check(p.get("plain") == PLAIN, f"pointer: the plain-language text is stored on the pointer ({p.get('plain')!r})")
check([r for r in rows if r["id"] in ("L1", "L2")] == [r for r in members_before if r["id"] in ("L1", "L2")],
      "members stay unchanged (compared with the rows before the write)")
rc, out = run([APPLY, mem], json.dumps({"date": "2026-10-09", "principles": [
    {"summary": "Windows-Subprocess: Binary per shutil.which aufloesen, Encoding explizit utf-8.",
     "anchor": f"{rules}#Windows-Subprocess", "members": ["L2", "L3"], "plain": PLAIN + " Neu gefasst."}]}))
rows = json.load(open(os.path.join(mem, "learnings", "learnings.json"), encoding="utf-8"))
ptr = [r for r in rows if r.get("kind") == "principle"]
check(rc == 0 and len(ptr) == 1 and ptr[0]["derived_from"] == ["L1", "L2", "L3"]
      and out["tally"]["principles_updated"] == 1,
      "same anchor again: members merged into the existing pointer, no second row")
check(ptr[0].get("plain") == PLAIN + " Neu gefasst.", "same anchor again: the plain-language text is updated with the summary")
for bad, why in (({"summary": "x", "anchor": f"{rules}#Windows-Subprocess", "members": ["L404"], "plain": PLAIN}, "unknown local member"),
                 ({"summary": "x", "anchor": "", "members": ["L1"], "plain": PLAIN}, "empty anchor"),
                 ({"summary": "", "anchor": f"{rules}#Windows-Subprocess", "members": ["L1"], "plain": PLAIN}, "empty summary"),
                 ({"summary": "x", "anchor": f"{rules}#Windows-Subprocess", "members": [], "plain": PLAIN}, "no members"),
                 ({"summary": "x", "anchor": f"{rules}#Nicht da", "members": ["L1"], "plain": PLAIN}, "anchor does not resolve")):
    before = sha(os.path.join(mem, "learnings", "learnings.json"))
    rc, out = run([APPLY, mem], json.dumps({"principles": [bad]}))
    check(rc == 2 and sha(os.path.join(mem, "learnings", "learnings.json")) == before,
          f"rejected before the first byte: {why}")
# round 2: superseded member refused; merged member cap checked before the first byte
rows = json.load(open(os.path.join(mem, "learnings", "learnings.json"), encoding="utf-8"))
rows.append(row("L50", "abgeloest", 4, superseded_by="fixed:x"))
rows += [row(f"L{100 + i}", f"Fuellzeile {i} mit eigenem Text {i * 13}", 3) for i in range(30)]
put(os.path.join(mem, "learnings", "learnings.json"), rows)
before = sha(os.path.join(mem, "learnings", "learnings.json"))
rc, out = run([APPLY, mem], json.dumps({"principles": [
    {"summary": "x", "anchor": f"{rules}#Windows-Subprocess", "members": ["L50"], "plain": PLAIN}]}))
check(rc == 2, "a superseded learning is refused as member")
rc, out = run([APPLY, mem], json.dumps({"iterations": [{"type": "feature", "title": "darf nicht"}],
                                        "principles": [{"summary": "x", "anchor": f"{rules}#Windows-Subprocess",
                                                        "members": [f"L{100 + i}" for i in range(28)], "plain": PLAIN}]}))
check(rc == 2 and not os.path.exists(os.path.join(mem, "iterations", "iteration-log.md"))
      and sha(os.path.join(mem, "learnings", "learnings.json")) == before,
      "merge over 30 members is refused before the iteration log is written")
# Qualified ids (store/Lnn, native:) skip the rule_class check of the local lookup, so they are
# refused until a cross-repo lookup exists (DCO #10062 (1), owner delegation 2026-10-25).
for qid in ("dome-dynamics/L31", "native:dco/x.md:ab12cd34", "L1:x", "nummerlos"):
    before = sha(os.path.join(mem, "learnings", "learnings.json"))
    rc, out = run([APPLY, mem], json.dumps({"principles": [
        {"summary": "Trust", "anchor": f"{rules}#^r-trust", "members": ["L1", qid], "plain": PLAIN}]}))
    err = str(out.get("error") or "")
    check(rc == 2 and "qualified" in err and qid in err and "rule_class" in err
          and sha(os.path.join(mem, "learnings", "learnings.json")) == before,
          f"qualified member {qid!r} refused with a clear message, before the first byte ({rc}, {err[:90]})")

# D11: no plain, no pointer - the owner reads the card in plain words, so the card must carry them
tmp_p, mem_p, rules_p = setup()
learn_p = os.path.join(mem_p, "learnings", "learnings.json")
good = {"summary": "Windows-Subprocess", "anchor": f"{rules_p}#Windows-Subprocess", "members": ["L1", "L2"], "plain": PLAIN}
for label, entry in (("plain fehlt", {k: v for k, v in good.items() if k != "plain"}), ("plain leer", dict(good, plain="")),
                     ("plain nur Whitespace", dict(good, plain=" \n ")), ("plain kein String", dict(good, plain=7)),
                     ("plain zu lang (601 Zeichen)", dict(good, plain="x" * 601)),
                     ("plain zu lang (Woerter, nach dem Zusammenfassen)", dict(good, plain="wort " * 130))):
    before = sha(learn_p)
    rc, out = run([APPLY, mem_p], json.dumps({"iterations": [{"title": "darf nicht geschrieben werden"}], "principles": [entry]}))
    check(rc == 2 and "plain" in str(out.get("error")).lower() and sha(learn_p) == before
          and not os.path.exists(os.path.join(mem_p, "iterations", "iteration-log.md")),
          f"principles, {label}: plan rejected before the first byte ({rc}, {str(out.get('error'))[:80]})")
rc, out = run([APPLY, mem_p], json.dumps({"principles": [dict(good, plain="y" * 600)]}))
check(rc == 0, f"principles: exactly 600 characters of plain are accepted ({rc}, {out.get('error')})")
rows_p = json.load(open(learn_p, encoding="utf-8"))
check([r.get("plain") for r in rows_p if r.get("kind") == "principle"] == ["y" * 600],
      "principles: plain is stored on the pointer row")
check(all("plain" not in r for r in rows_p if r.get("kind") != "principle"), "principles: the member rows get no plain")
# the entry is checked as a whole: the second of two entries without plain still rejects the first one
tmp_p, mem_p, rules_p = setup()
learn_p = os.path.join(mem_p, "learnings", "learnings.json")
before = sha(learn_p)
rc, out = run([APPLY, mem_p], json.dumps({"principles": [
    {"summary": "A", "anchor": f"{rules_p}#Windows-Subprocess", "members": ["L1"], "plain": PLAIN},
    {"summary": "B", "anchor": f"{rules_p}#Verifikation vor Aktion", "members": ["L3"]}]}))
check(rc == 2 and sha(learn_p) == before, "principles: one entry without plain rejects the whole plan, the valid one is not written either")

# ============================================================ 3. projection
print("--- MEMORY.md projection (block first)")
md = os.path.join(tmp, "memory", "MEMORY.md")
put(md, "# Memory Index\n\n- [Handnotiz](a.md) — bleibt\n")
p = subprocess.run([sys.executable, PROJ, mem, "--memory-md", md], capture_output=True, text=True,
                   encoding="utf-8", env=ENV)
text = read(md)
lines = text.split("\n")
check(p.returncode == 0 and lines[0].startswith(BEGIN), f"block starts the file ({lines[:1]})")
check(text.rstrip().endswith("- [Handnotiz](a.md) — bleibt"), "handwritten lines follow, unchanged")
blk = text[:text.index(END)]
i_ptr = blk.find("[L9]")
i_l3 = blk.find("[L3]")
check(0 < i_ptr < blk.find("[L8]") and "→" in blk[i_ptr:i_ptr + 200], "pointer line comes first with its anchor")
check("+3 Belege" in blk[i_ptr:blk.index("\n", i_ptr)], "approved members are folded into '+n Belege'")
check(pt.pointer_line({"id": "L1", "summary": "x", "anchor": "a.md"}, 1).endswith("(+1 Beleg)"),
      "singular: '+1 Beleg'")
check("[L1]" not in blk and "[L2]" not in blk and i_l3 == -1, "folded members are not listed again")
check(sum(1 for x in ("[L4]", "[L5]", "[L6]", "[L7]") if x in blk) == 3, "at most 3 codex tips")
check(blk.find("[L7]") < blk.find("[L8]") or "[L8]" not in blk,
      "importance before date: imp 2 codex tip before the newer imp 1 entry")
before = read(md)
subprocess.run([sys.executable, PROJ, mem, "--memory-md", md], capture_output=True, env=ENV)
check(read(md) == before, "second run: byte-identical (no blank line growth at the top)")
# legacy file with the block at the END moves to the top, hand lines keep their bytes
put(md, "# Memory Index\n\n- [Hand](h.md) — x\n\n" + BEGIN + " alt -->\nalter inhalt\n" + END + "\n")
subprocess.run([sys.executable, PROJ, mem, "--memory-md", md], capture_output=True, env=ENV)
text = read(md)
check(text.startswith(BEGIN) and text.count(BEGIN) == 1 and "alter inhalt" not in text
      and text.rstrip().endswith("- [Hand](h.md) — x"), "a legacy block at the end is moved to the top")
# lost anchor is flagged, not hidden
put(rules, "# Regeln\n\nalles umgebaut\n")
subprocess.run([sys.executable, PROJ, mem, "--memory-md", md], capture_output=True, env=ENV)
check("(Anker fehlt — prüfen)" in read(md), "a lost anchor is flagged in the pointer line")

# round 2: folding only for SHOWN pointers; line limit with a long anchor; '#' in a file name
import memory_index_projection as mip  # noqa: E402
# P3-A: the cap goes by live members - pointers 0..10 hold two (not approved) members, the twelfth only M11
ptrs = [{"id": f"L{900 + i}", "kind": "principle", "summary": f"Regel {i}", "anchor": rules,
         "derived_from": [f"M{i}", f"N{i}"] if i < 11 else ["M11"], "importance": 3, "date": "2026-10-01"}
        for i in range(12)]
appr = [{"id": "M11", "text": "Mitglied des elften Zeigers", "importance": 3, "date": "2026-10-01",
         "bridge_status": "approved"}]
appr += [{"id": f"{x}{i}", "text": f"Mitglied {x}{i}", "importance": 3, "date": "2026-10-01"}
         for i in range(11) for x in ("M", "N")]
put(os.path.join(tmp, "m2", ".agent-memory", "learnings", "learnings.json"), ptrs + appr)
_p, shown = mip.load_entries(os.path.join(tmp, "m2", ".agent-memory"))
check(any(e["id"] == "M11" for e in shown), "a member of a pointer beyond the display cap stays visible")
long_anchor = os.path.join(tmp, "x" * 190 + ".md")
check(len(pt.pointer_line({"id": "L1", "summary": "kurz", "anchor": long_anchor}, 3, False)) <= 200,
      "pointer line <= 200 chars even with a long anchor")
hashfile = os.path.join(tmp, "rules#v2.md")
put(hashfile, "# R\n")
check(pt.anchor_ok(hashfile), "a file name containing '#' resolves as a file anchor")
# a legacy block in the MIDDLE: hand lines after it keep their bytes incl. trailing blank lines
md2 = os.path.join(tmp, "memory", "MEMORY2.md")
put(md2, "# Index\n\n" + BEGIN + " alt -->\nalt\n" + END + "\n- [nach](n.md) — danach\n\n\n")
subprocess.run([sys.executable, PROJ, mem, "--memory-md", md2], capture_output=True, env=ENV)
t2 = read(md2)
check(t2.startswith(BEGIN) and t2.endswith("# Index\n- [nach](n.md) — danach\n\n\n"),
      f"middle block: rest byte-identical incl. trailing blank lines ({t2[-60:]!r})")
# AGENTS.md: pointer members are not listed twice
import bridge_projection as bp  # noqa: E402
put(os.path.join(tmp, "m3", ".agent-memory", "learnings", "learnings.json"), [
    {"id": "L1", "text": "Mitglied", "importance": 4, "date": "2026-10-01", "bridge_status": "approved"},
    {"id": "L9", "kind": "principle", "summary": "Regel", "text": "Regel -> a", "anchor": rules,
     "derived_from": ["L1"], "importance": 4, "date": "2026-10-02", "bridge_status": "approved"}])
check([e["id"] for e in bp.shown(os.path.join(tmp, "m3", ".agent-memory"))] == ["L9"],
      "AGENTS.md projection folds pointer members (the pointer is shown, its member is not)")

# ============================================================ 4. lifecycle propose / report
print("--- learnings_lifecycle.py")
tmp, mem, rules = setup()
learn = os.path.join(mem, "learnings", "learnings.json")
before = sha(learn)
rc, out = run([LIFE, "propose", mem, "--catalog", rules, "--min-importance", "4", "--min-age-days", "0",
               "--today", "2026-10-08"])
props = {x["id"]: x for x in out.get("proposals", [])}
check(rc == 0 and sha(learn) == before, f"propose is read-only ({rc}, {out.get('_err', '')[:200]})")
check(set(props) == {"L1", "L2", "L3"}, f"eligible: importance >= 4, not codex, no pointer member ({sorted(props)})")
top = props.get("L1", {}).get("matches", [{}])[0]
check(top.get("anchor", "").endswith("#Windows-Subprocess") and top.get("score", 0) > 0,
      f"L1 matches the Windows-Subprocess section ({top})")
check(props.get("L3", {}).get("matches", [{}])[0].get("anchor", "").endswith("#Verifikation vor Aktion"),
      "L3 matches the verification section")
run([APPLY, mem], json.dumps({"principles": [{"summary": "Windows", "anchor": f"{rules}#Windows-Subprocess",
                                               "members": ["L1"], "plain": PLAIN}]}))
rc, out = run([LIFE, "propose", mem, "--catalog", rules, "--min-importance", "4", "--min-age-days", "0"])
check("L1" not in {x["id"] for x in out.get("proposals", [])}, "a pointer member is not proposed again")
rc, out = run([LIFE, "report", mem, "--min-importance", "4", "--min-age-days", "0"])
check(rc == 0 and out.get("eligible") == 3 and out.get("anchored") == 1 and out.get("pointers") == 1,
      f"report counts eligible / anchored / pointers ({out})")
check(out.get("lost_anchors") == [], f"report: no lost anchor while the rule section exists ({out.get('lost_anchors')})")
put(rules, "# Regeln\n\nalles umgebaut\n")
rc, out = run([LIFE, "report", mem, "--min-importance", "4", "--min-age-days", "0"])
check(rc == 0 and out.get("lost_anchors") == ["L9"], f"report: the daily anchor check names a lost anchor ({out})")
rc, out = run([LIFE, "propose", mem, "--catalog", rules, "--min-age-days", "30", "--today", "2026-08-15"])
check(rc == 0 and not out.get("proposals"), "min-age-days keeps young learnings out")

# ============================================================ 5. writer safeguards (P3-A)
print("--- writer safeguards (P3-A)")


def rows_of(mem):
    return json.load(open(os.path.join(mem, "learnings", "learnings.json"), encoding="utf-8"))


def by(mem, lid):
    return next((r for r in rows_of(mem) if r.get("id") == lid), None)


def refused(mem, plan, why, extra=()):
    """L94: a rejected plan writes nothing - not the store, not the iteration log written before it."""
    learn = os.path.join(mem, "learnings", "learnings.json")
    before = sha(learn)
    plan = dict(plan, iterations=[{"title": "darf nicht geschrieben werden"}])
    rc, out = run([APPLY, mem] + list(extra), json.dumps(plan))
    check(rc == 2 and sha(learn) == before and not os.path.exists(os.path.join(mem, "iterations", "iteration-log.md")),
          f"refused before the first byte: {why} ({rc}, {str(out.get('error'))[:110]})")
    return out


def written(out):
    """files_written names learnings.json: an identical rewrite keeps the hash, so the hash alone proves nothing."""
    return any("learnings" in str(f) for f in out.get("files_written", []))


def card(rules, frag, members, **kw):
    return dict({"summary": f"Regel {frag}", "anchor": f"{rules}#{frag}", "members": members, "plain": PLAIN}, **kw)


# 5a. idempotency: the same pointer sent again changes nothing
tmp, mem, rules = setup()
learn = os.path.join(mem, "learnings", "learnings.json")
first = card(rules, "Windows-Subprocess", ["L1", "L2"], tags=["windows"])
rc, out = run([APPLY, mem], json.dumps({"date": "2026-10-08", "principles": [first]}))
before = sha(learn)
rc, out = run([APPLY, mem], json.dumps({"date": "2026-11-01", "principles": [first]}))
check(rc == 0 and sha(learn) == before and out["tally"]["principles_updated"] == 0 and not written(out)
      and out["tally"]["principles_unchanged"] == 1 and by(mem, "L9")["last_relevant"] == "2026-10-08",
      f"idempotent: unchanged pointer again -> no last_relevant, not counted, file untouched ({out.get('tally', {}).get('principles_unchanged')})")
rc, out = run([APPLY, mem], json.dumps({"date": "2026-11-02", "principles": [dict(first, members=["L3"])]}))
p9 = by(mem, "L9")
check(rc == 0 and out["tally"]["principles_updated"] == 1 and p9["derived_from"] == ["L1", "L2", "L3"]
      and p9["last_relevant"] == "2026-11-02" and p9.get("merges", [{}])[-1].get("added_members") == ["L3"],
      f"a real change still merges, counts and leaves a merges[] entry ({p9.get('merges')})")

# 5b. rid: tags - merge only within one rule; tags are merged
tmp, mem, rules = setup()
rc, out = run([APPLY, mem], json.dumps({"principles": [card(rules, "Windows-Subprocess", ["L1"], tags=["rid:R03"])]}))
refused(mem, {"principles": [card(rules, "Windows-Subprocess", ["L2"], tags=["rid:R39"])]},
        "a different rid: on the same anchor (rules would be mixed)")
refused(mem, {"principles": [card(rules, "Verifikation vor Aktion", ["L3"], tags=["rid:R25", "rid:R09"])]},
        "two rid: tags on one pointer")
rc, out = run([APPLY, mem], json.dumps({"principles": [card(rules, "Windows-Subprocess", ["L2"],
                                                             tags=["rid:R03", "condense:2026-11-03"])]}))
p9 = by(mem, "L9")
check(rc == 0 and p9["derived_from"] == ["L1", "L2"] and p9["tags"] == ["rid:R03", "condense:2026-11-03"],
      f"same rid: merges, tags are merged ({p9.get('tags')})")
tmp2, mem2, rules2 = setup()
run([APPLY, mem2], json.dumps({"principles": [card(rules2, "Windows-Subprocess", ["L1"])]}))
rc, out = run([APPLY, mem2], json.dumps({"principles": [card(rules2, "Windows-Subprocess", ["L2"], tags=["rid:R03"])]}))
check(rc == 0 and by(mem2, "L9")["tags"] == ["rid:R03"], "a legacy pointer without rid: adopts the rule id")

# 5c. held_rules in ~/.claude/agentic-os.local.json (rid or anchor)
tmp, mem, rules = setup()
put(os.path.join(ISO_HOME, ".claude", "agentic-os.local.json"),
    {"held_rules": ["R11", f"{rules}#Verifikation vor Aktion", 7]})
try:
    refused(mem, {"principles": [card(rules, "Windows-Subprocess", ["L1"], tags=["rid:R11"])]}, "held rule id")
    refused(mem, {"principles": [card(rules, "Verifikation vor Aktion", ["L3"])]}, "held anchor")
    rc, out = run([APPLY, mem], json.dumps({"principles": [card(rules, "Windows-Subprocess", ["L1"], tags=["rid:R03"])]}))
    check(rc == 0, f"a rule not on the hold list passes ({out.get('error')})")
finally:
    os.remove(os.path.join(ISO_HOME, ".claude", "agentic-os.local.json"))

# 5d. member_sha: checked under the lock, one changed member rejects the whole plan
import apply_wrapup as aw  # noqa: E402
tmp, mem, rules = setup()
l1, l2 = by(mem, "L1"), by(mem, "L2")
good = card(rules, "Windows-Subprocess", ["L1", "L2"], member_sha={"L1": aw.member_sha(l1), "L2": aw.member_sha(l2)[:12]})
stale = dict(good, member_sha={"L1": aw.member_sha(l1), "L2": aw.member_sha(dict(l2, text="alter Wortlaut"))})
refused(mem, {"principles": [card(rules, "Verifikation vor Aktion", ["L3"]), stale]}, "member_sha of a changed member")
refused(mem, {"principles": [dict(good, member_sha={"L1": aw.member_sha(l1)})]}, "member_sha missing a member")
refused(mem, {"principles": [dict(good, member_sha={"L1": "xyz", "L2": aw.member_sha(l2)})]}, "member_sha not hex")
rc, out = run([APPLY, mem], json.dumps({"principles": [good]}))
check(rc == 0 and by(mem, "L9") is not None, f"matching member_sha (full or 12-char prefix) is accepted ({out.get('error')})")

# 5e. principle_retire by ids
tmp, mem, rules = setup()
learn = os.path.join(mem, "learnings", "learnings.json")
run([APPLY, mem], json.dumps({"principles": [card(rules, "Windows-Subprocess", ["L1", "L2"])]}))
for bad, why in (({"ids": ["L404"], "reason": "x"}, "unknown id"), ({"ids": ["L1"], "reason": "x"}, "id is no pointer"),
                 ({"ids": ["L9"], "tag": "condense:2026-11-01", "reason": "x"}, "ids and tag"),
                 ({"reason": "x"}, "neither ids nor tag"), ({"ids": ["L9"], "reason": " "}, "empty reason"),
                 ({"tag": "windows", "reason": "x"}, "tag is no condense:<date> tag")):
    refused(mem, {"principle_retire": [bad]}, f"principle_retire, {why}")
refused(mem, {"principle_retire": [{"ids": ["L9"], "reason": "x"}],
              "principles": [card(rules, "Verifikation vor Aktion", ["L3"])]}, "principles and principle_retire in one plan")
rc, out = run([APPLY, mem], json.dumps({"principle_retire": [{"ids": ["L9"], "reason": "falscher  Anker"}]}))
check(rc == 0 and by(mem, "L9")["superseded_by"] == "obsolete:falscher Anker" and out["tally"]["principles_retired"] == ["L9"]
      and by(mem, "L1") == [r for r in rows_of(mem) if r["id"] == "L1"][0],
      f"retire by id: superseded_by = obsolete:<reason>, the row stays (D-021) ({out.get('error')})")
md = os.path.join(tmp, "memory", "MEMORY.md")
subprocess.run([sys.executable, PROJ, mem, "--memory-md", md], capture_output=True, env=ENV)
check("[L9]" not in read(md) and "[L1]" in read(md), "retired pointer leaves the block, its members are listed again")
before = sha(learn)
rc, out = run([APPLY, mem], json.dumps({"principle_retire": [{"ids": ["L9"], "reason": "falscher Anker"}]}))
check(rc == 0 and sha(learn) == before and out["tally"]["principle_retire_skipped"] == 1 and not written(out),
      "retire by id a second time: skipped, nothing written")

# 5f. principle_retire by tag: created pointers are retired, merged ones restored as they were
tmp, mem, rules = setup()
learn = os.path.join(mem, "learnings", "learnings.json")
run([APPLY, mem], json.dumps({"date": "2026-10-01", "principles": [card(rules, "Windows-Subprocess", ["L1"], tags=["windows"])]}))
old = by(mem, "L9")
T = "condense:2026-11-05"
rc, out = run([APPLY, mem], json.dumps({"date": "2026-11-05", "principles": [
    card(rules, "Windows-Subprocess", ["L2"], tags=[T], summary="Neu gefasst", plain=PLAIN + " Neu."),
    card(rules, "Verifikation vor Aktion", ["L3"], tags=[T])]}))
check(rc == 0 and by(mem, "L9")["derived_from"] == ["L1", "L2"] and by(mem, "L10") is not None,
      f"condense run: one merge, one new pointer ({out.get('error')})")
rc, out = run([APPLY, mem], json.dumps({"principle_retire": [{"tag": T, "reason": "Lauf zurueck"}]}))
p9, p10 = by(mem, "L9"), by(mem, "L10")
check(rc == 0 and out["tally"]["principles_retired"] == ["L10"] and out["tally"]["principles_unmerged"] == ["L9"],
      f"retire by tag: created -> retired, merged -> unmerged ({out.get('tally', {}).get('principles_retired')}, {out.get('error')})")
check(p10["superseded_by"] == "obsolete:Lauf zurueck", "retire by tag: the created pointer is superseded")
check(all(p9.get(k) == old.get(k) for k in ("derived_from", "summary", "plain", "tags", "text", "importance"))
      and not p9.get("superseded_by") and p9.get("merges") == []
      and p9.get("unmerges", [{}])[0].get("removed_members") == ["L2"],
      f"retire by tag: the merged pointer is back as it was, with an unmerges audit ({p9})")
before = sha(learn)
rc, out = run([APPLY, mem], json.dumps({"principle_retire": [{"tag": T, "reason": "Lauf zurueck"}]}))
check(rc == 0 and sha(learn) == before and not written(out), "retire by tag a second time: nothing written")
# a newer change under another tag on top blocks the unmerge
tmp, mem, rules = setup()
run([APPLY, mem], json.dumps({"principles": [card(rules, "Windows-Subprocess", ["L1"])]}))
run([APPLY, mem], json.dumps({"principles": [card(rules, "Windows-Subprocess", ["L2"], tags=["condense:2026-11-05"])]}))
run([APPLY, mem], json.dumps({"principles": [card(rules, "Windows-Subprocess", ["L3"], tags=["condense:2026-11-06"])]}))
refused(mem, {"principle_retire": [{"tag": "condense:2026-11-05", "reason": "x"}]}, "unmerge under a newer foreign merge")
rc, out = run([APPLY, mem], json.dumps({"principle_retire": [{"tag": "condense:2026-11-06", "reason": "x"}]}))
check(rc == 0 and by(mem, "L9")["derived_from"] == ["L1", "L2"], "the newest merge can be taken back first")

# 5g. --snapshot: under the lock, after the plan check
tmp, mem, rules = setup()
snaps = ENV["AGENTIC_OS_SNAPSHOT_DIR"]   # the subprocesses' snapshot dir, inside the temp home


def snap_count():
    return sum(len(fs) for _d, _s, fs in os.walk(snaps))


n0 = snap_count()
refused(mem, {"principles": [card(rules, "Gibt es nicht", ["L1"])]}, "--snapshot with a rejected plan", ["--snapshot"])
check(snap_count() == n0, "--snapshot: a rejected plan takes no snapshot")
rc, out = run([APPLY, mem, "--snapshot", "--dry-run"], json.dumps({"principles": [card(rules, "Windows-Subprocess", ["L1"])]}))
check(rc == 0 and snap_count() == n0 and "snapshot" not in out, "--snapshot --dry-run: no snapshot")
rc, out = run([APPLY, mem, "--snapshot"], json.dumps({"principles": [card(rules, "Windows-Subprocess", ["L1"])]}))
check(rc == 0 and os.path.isfile(os.path.join(str(out.get("snapshot")), "learnings", "learnings.json"))
      and str(out.get("snapshot")).startswith(snaps),
      f"--snapshot: byte copy before the write, path in the result ({out.get('snapshot')})")
snap_rows = json.load(open(os.path.join(out["snapshot"], "learnings", "learnings.json"), encoding="utf-8"))
check(not any(r.get("kind") == "principle" for r in snap_rows), "--snapshot: the copy is the state before the write")

# 5h. --lint reports pointer defects
tmp, mem, rules = setup()
rows = rows_of(mem)
rows += [row("L60", "abgeloest", 3, superseded_by="fixed:x"),
         {"id": "L61", "kind": "principle", "date": "2026-10-01", "text": "a", "summary": "a", "anchor": rules,
          "importance": 3, "tags": [], "superseded_by": None, "derived_from": ["L1", "dco/L5", "L404", "L60", "L62"]},
         {"id": "L62", "kind": "principle", "date": "2026-10-01", "text": "b", "summary": "b", "anchor": rules,
          "importance": 3, "tags": [], "superseded_by": None, "derived_from": ["L2"]}]
put(os.path.join(mem, "learnings", "learnings.json"), rows)
rc, out = run([APPLY, mem, "--lint"])
errs = {x["id"]: " | ".join(x["errors"]) for x in out.get("lint", [])}
e61 = errs.get("L61", "")
check(rc == 0 and "anchor without fragment" in e61 and "'dco/L5' is not a local id" in e61
      and "L404 is not in this store" in e61 and "L60 is not live" in e61 and "L62 is itself a pointer" in e61
      and "anchor shared with L62" in e61 and "anchor shared with L61" in errs.get("L62", ""),
      f"--lint: fragment, foreign/missing/dead/pointer member, shared anchor ({errs})")

# 5i. Codex-Verifier P3-A (2026-10-29), each red against the first version
print("--- Codex-Verifier P3-A")
# P3-A-01: created and merged under another tag in ONE plan - retiring the second tag only undoes its merge
tmp, mem, rules = setup()
rc, out = run([APPLY, mem], json.dumps({"principles": [
    card(rules, "Windows-Subprocess", ["L1"], tags=["rid:R03", "condense:A"], summary="Fassung A"),
    card(rules, "Windows-Subprocess", ["L2"], tags=["rid:R03", "condense:B"], summary="Fassung B")]}))
rc, out = run([APPLY, mem], json.dumps({"principle_retire": [{"tag": "condense:B", "reason": "zurueck"}]}))
p9 = by(mem, "L9") or {}
check(rc == 0 and not p9.get("superseded_by") and p9.get("derived_from") == ["L1"] and p9.get("summary") == "Fassung A"
      and p9.get("tags") == ["rid:R03", "condense:A"],
      f"P3-A-01 same-plan merge under tag B: retire B restores the pointer as created under A ({rc}, {out.get('error')}, {p9})")
tmp, mem, rules = setup()
run([APPLY, mem], json.dumps({"principles": [
    card(rules, "Windows-Subprocess", ["L1"], tags=["condense:A"]), card(rules, "Windows-Subprocess", ["L2"], tags=["condense:B"])]}))
refused(mem, {"principle_retire": [{"tag": "condense:A", "reason": "x"}]},
        "P3-A-01 retire of the creating tag under a same-plan merge of another tag")

# P3-A-02: restore in the same plan adds rows validate_principles / principle_retire never saw
tmp, mem, rules = setup()
restore = {"source": "test", "learnings": [row("L70", "zurueckgeholt", 3)]}
refused(mem, {"restore": restore, "principles": [card(rules, "Windows-Subprocess", ["L1"])]},
        "P3-A-02 restore and principles in one plan")
tmp, mem, rules = setup()
run([APPLY, mem], json.dumps({"principles": [card(rules, "Windows-Subprocess", ["L1"])]}))
refused(mem, {"restore": restore, "principle_retire": [{"ids": ["L9"], "reason": "x"}]},
        "P3-A-02 restore and principle_retire in one plan")

# P3-A-03: a held rid on the TARGET pointer also holds a card without rid:
tmp, mem, rules = setup()
run([APPLY, mem], json.dumps({"principles": [card(rules, "Windows-Subprocess", ["L1"], tags=["rid:R11"])]}))
put(os.path.join(ISO_HOME, ".claude", "agentic-os.local.json"), {"held_rules": ["R11"]})
try:
    refused(mem, {"principles": [card(rules, "Windows-Subprocess", ["L2"])]},
            "P3-A-03 card without rid: into a pointer whose rid is held")
finally:
    os.remove(os.path.join(ISO_HOME, ".claude", "agentic-os.local.json"))

# P3-A-04: a correction in the same plan resets a member's release after validate_principles saw it released
tmp, mem, rules = setup()
rows = rows_of(mem)
for r in rows:
    if r["id"] == "L1":
        r.update(rule_class=True, release={"status": "released", "released_at": "2026-10-01"})
put(os.path.join(mem, "learnings", "learnings.json"), rows)
refused(mem, {"corrections": {"learnings": [{"id": "L1", "reason": "Tippfehler",
                                             "text": "subprocess.run findet npm nicht ohne shutil.which() unter Windows"}]},
              "principles": [card(rules, "Windows-Subprocess", ["L1"])]},
        "P3-A-04 correction of a member and principles in one plan")
# Codex round 2: the id with spaces (the corrections applier strips it) and a member the pointer already holds
refused(mem, {"corrections": {"learnings": [{"id": " L1 ", "reason": "Tippfehler",
                                             "text": "subprocess.run findet npm nicht ohne shutil.which() unter Windows"}]},
              "principles": [card(rules, "Windows-Subprocess", ["L1"])]},
        "P3-A-04-R2-01 correction id with spaces")
run([APPLY, mem], json.dumps({"principles": [card(rules, "Windows-Subprocess", ["L1"])]}))
check(by(mem, "L9") is not None and by(mem, "L9")["derived_from"] == ["L1"], "P3-A-04-R2-02 setup: pointer L9 over L1")
refused(mem, {"corrections": {"learnings": [{"id": "L1", "reason": "Tippfehler",
                                             "text": "subprocess.run findet npm nicht ohne shutil.which() unter Windows"}]},
              "principles": [card(rules, "Windows-Subprocess", ["L2"], summary="neu gefasst")]},
        "P3-A-04-R2-02 correction of a member the target pointer already holds")

print(f"=== Results: {PASSED}/{TESTS} passed, {ERRORS} failures ===")
sys.exit(1 if ERRORS else 0)

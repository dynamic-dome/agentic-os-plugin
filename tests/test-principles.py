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
ENV = dict(os.environ, PYTHONIOENCODING="utf-8",
           AGENTIC_OS_SNAPSHOT_DIR=tempfile.mkdtemp(prefix="princ-snaps-"))
BEGIN = "<!-- bridge:claude-native:begin"
END = "<!-- bridge:claude-native:end -->"

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
     "anchor": f"{rules}#Windows-Subprocess", "members": ["L1", "L2"], "tags": ["windows"]}]}
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
check([r for r in rows if r["id"] in ("L1", "L2")] == [r for r in members_before if r["id"] in ("L1", "L2")],
      "members stay unchanged (compared with the rows before the write)")
rc, out = run([APPLY, mem], json.dumps({"date": "2026-10-09", "principles": [
    {"summary": "Windows-Subprocess: Binary per shutil.which aufloesen, Encoding explizit utf-8.",
     "anchor": f"{rules}#Windows-Subprocess", "members": ["L2", "L3"]}]}))
rows = json.load(open(os.path.join(mem, "learnings", "learnings.json"), encoding="utf-8"))
ptr = [r for r in rows if r.get("kind") == "principle"]
check(rc == 0 and len(ptr) == 1 and ptr[0]["derived_from"] == ["L1", "L2", "L3"]
      and out["tally"]["principles_updated"] == 1,
      "same anchor again: members merged into the existing pointer, no second row")
for bad, why in (({"summary": "x", "anchor": f"{rules}#Windows-Subprocess", "members": ["L404"]}, "unknown local member"),
                 ({"summary": "x", "anchor": "", "members": ["L1"]}, "empty anchor"),
                 ({"summary": "", "anchor": f"{rules}#Windows-Subprocess", "members": ["L1"]}, "empty summary"),
                 ({"summary": "x", "anchor": f"{rules}#Windows-Subprocess", "members": []}, "no members"),
                 ({"summary": "x", "anchor": f"{rules}#Nicht da", "members": ["L1"]}, "anchor does not resolve")):
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
    {"summary": "x", "anchor": f"{rules}#Windows-Subprocess", "members": ["L50"]}]}))
check(rc == 2, "a superseded learning is refused as member")
rc, out = run([APPLY, mem], json.dumps({"iterations": [{"type": "feature", "title": "darf nicht"}],
                                        "principles": [{"summary": "x", "anchor": f"{rules}#Windows-Subprocess",
                                                        "members": [f"L{100 + i}" for i in range(28)]}]}))
check(rc == 2 and not os.path.exists(os.path.join(mem, "iterations", "iteration-log.md"))
      and sha(os.path.join(mem, "learnings", "learnings.json")) == before,
      "merge over 30 members is refused before the iteration log is written")
rc, out = run([APPLY, mem], json.dumps({"principles": [
    {"summary": "Trust", "anchor": f"{rules}#^r-trust", "members": ["dome-dynamics/L31", "native:dco/x.md:ab12cd34"]}]}))
check(rc == 0, "qualified members (store/Lnn, native:) are accepted without a local lookup")

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
ptrs = [{"id": f"L{900 + i}", "kind": "principle", "summary": f"Regel {i}", "anchor": rules,
         "derived_from": [f"M{i}"], "importance": 3, "date": "2026-10-01"} for i in range(12)]
appr = [{"id": "M11", "text": "Mitglied des elften Zeigers", "importance": 3, "date": "2026-10-01",
         "bridge_status": "approved"}]
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
check([e["id"] for e in bp.load_approved(os.path.join(tmp, "m3", ".agent-memory"))] == ["L9"],
      "AGENTS.md projection folds pointer members")

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
                                               "members": ["L1"]}]}))
rc, out = run([LIFE, "propose", mem, "--catalog", rules, "--min-importance", "4", "--min-age-days", "0"])
check("L1" not in {x["id"] for x in out.get("proposals", [])}, "a pointer member is not proposed again")
rc, out = run([LIFE, "report", mem, "--min-importance", "4", "--min-age-days", "0"])
check(rc == 0 and out.get("eligible") == 3 and out.get("anchored") == 1 and out.get("pointers") == 1,
      f"report counts eligible / anchored / pointers ({out})")
rc, out = run([LIFE, "propose", mem, "--catalog", rules, "--min-age-days", "30", "--today", "2026-08-15"])
check(rc == 0 and not out.get("proposals"), "min-age-days keeps young learnings out")

print(f"=== Results: {PASSED}/{TESTS} passed, {ERRORS} failures ===")
sys.exit(1 if ERRORS else 0)

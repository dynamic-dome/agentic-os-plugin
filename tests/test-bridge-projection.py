#!/usr/bin/env python3
"""Tests for scripts/bridge_projection.py (T-14 Claude->Codex bridge projection).

Renders approved bridge learnings into a managed block in AGENTS.md.
Run: python tests/test-bridge-projection.py  (exit 0 = pass)
"""
import json
import os
import subprocess
import sys
import tempfile

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PLUGIN_ROOT, "scripts", "bridge_projection.py")

FAILURES = []
BEGIN = "<!-- bridge:begin"
END = "<!-- bridge:end -->"


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS: {name}")
    else:
        print(f"  FAIL: {name} {detail}")
        FAILURES.append(name)


def run(args, cwd):
    return subprocess.run(
        [sys.executable, SCRIPT] + args,
        capture_output=True, encoding="utf-8", errors="replace", cwd=cwd,
    )


def learning(lid, date, text, importance=4, bridge=None, superseded=None):
    entry = {
        "id": lid, "date": date, "text": text, "importance": importance,
        "tags": ["bridge"], "layer": "short-term",
        "superseded_by": superseded, "last_relevant": date,
    }
    if bridge is not None:
        entry["bridge_status"] = bridge
    return entry


def task(tid, title, status="open"):
    return {"id": tid, "title": title, "status": status, "created": "2026-07-17"}


def write_tasks(mem, tasks):
    os.makedirs(os.path.join(mem, "context"), exist_ok=True)
    with open(os.path.join(mem, "context", "open-tasks.json"), "w",
              encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False)


def setup(tmp, learnings, agents_body=None):
    mem = os.path.join(tmp, ".agent-memory")
    os.makedirs(os.path.join(mem, "learnings"), exist_ok=True)
    with open(os.path.join(mem, "learnings", "learnings.json"), "w",
              encoding="utf-8") as f:
        json.dump(learnings, f, ensure_ascii=False)
    agents = os.path.join(tmp, "AGENTS.md")
    if agents_body is not None:
        with open(agents, "w", encoding="utf-8") as f:
            f.write(agents_body)
    return mem, agents


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def pointer(pid, members, anchor, date="2026-10-07", importance=4):
    """A rule pointer row (kind "principle", 5.4.0) as apply_wrapup writes it."""
    return {"id": pid, "date": date, "kind": "principle", "text": f"Regel {pid} -> {anchor}",
            "summary": f"Regel {pid}", "anchor": anchor, "importance": importance, "tags": [],
            "layer": "long-term", "superseded_by": None, "last_relevant": date,
            "derived_from": list(members), "bridge_status": "approved"}


def block_lines(content):
    body = content[content.index(BEGIN):content.index(END)]
    return [l for l in body.split("\n") if l.startswith("- [")]


def pointer_tests():
    """P3-A: pointers get their own section and cap; only members of SHOWN pointers are folded."""
    print("--- rule pointers (P3-A)")
    with tempfile.TemporaryDirectory() as tmp:
        rules = os.path.join(tmp, "regeln.md")
        with open(rules, "w", encoding="utf-8") as f:
            f.write("# Regeln\n" + "".join(f"- Regel {i} ^r-r{i}\n" for i in range(20)))

        # 1. more than 10 pointers: 10 shown by relevance, overflow line, members of hidden ones stay visible
        rows, ptrs = [], []
        for i in range(12):
            n = 12 - i   # P0 has 12 members ... P11 has 1: relevance = number of live members
            members = [f"L{i}x{k}" for k in range(n)]
            rows += [learning(m, "2026-09-01", f"Mitglied {m} von Regel {i}", bridge="approved") for m in members]
            ptrs.append(pointer(f"P{i}", members, f"{rules}#^r-r{i}", importance=5 if i == 11 else 3))
        mem, agents = setup(tmp, rows + ptrs, "# AGENTS\n")
        p = run([mem, "--agents-md", agents], cwd=tmp)
        content = read(agents)
        lines = block_lines(content)
        shown = [l.split("]")[0][3:] for l in lines if "★" in l]
        check("pointers: 10 shown under their own cap", len(shown) == 10, str(shown))
        check("pointers: ordered by live members, not by importance (P11 has importance 5, 1 member)",
              shown == [f"P{i}" for i in range(10)], str(shown))
        check("pointers: overflow line names the 2 hidden pointers",
              "(2 weitere Regel-Zeiger: .agent-memory/learnings/learnings.json)" in content, content[-400:])
        check("pointers: line carries anchor and '+n Belege'",
              any(l.startswith("- [P0] ★ ") and "→ regeln.md#^r-r0" in l and "(+12 Belege)" in l for l in lines),
              str(lines[:2]))
        listed = {l.split("]")[0][3:] for l in lines if "★" not in l}
        check("pointers: members of shown pointers are folded",
              not any(m.startswith(("L0x", "L9x")) for m in listed), str(sorted(listed)))
        check("pointers: members of the hidden pointers stay visible",
              listed == {"L10x0", "L10x1", "L11x0"}, str(sorted(listed)))
        check("pointers: exit 0 and the cap is reported", p.returncode == 0 and "pointers capped at 10" in p.stdout,
              p.stdout + p.stderr[:200])
        sys.path.insert(0, os.path.join(PLUGIN_ROOT, "scripts"))
        import bridge_projection as bp_mod
        rendered = [l.split("]")[0][3:] for l in lines]
        check("shown() is exactly the rendered selection, in block order (Codex P3-A-05)",
              [e["id"] for e in bp_mod.shown(mem)] == rendered, f"{[e['id'] for e in bp_mod.shown(mem)]} vs {rendered}")

    # 2. several pointers at CAP=6: pointers do not eat the learnings' cap
    with tempfile.TemporaryDirectory() as tmp:
        rules = os.path.join(tmp, "regeln.md")
        with open(rules, "w", encoding="utf-8") as f:
            f.write("# Regeln\n")
        ptrs = [pointer(f"P{i}", [f"M{i}"], rules) for i in range(8)]
        members = [learning(f"M{i}", "2026-09-01", f"Mitglied {i}", bridge="approved") for i in range(8)]
        plain = [learning(f"L{i}", f"2026-09-{10 + i:02d}", f"Einzel-Learning {i}", bridge="approved") for i in range(7)]
        mem, agents = setup(tmp, ptrs + members + plain, "# AGENTS\n")
        run([mem, "--agents-md", agents], cwd=tmp)
        content = read(agents)
        lines = block_lines(content)
        check("CAP=6: all 8 pointers shown", sum(1 for l in lines if "★" in l) == 8, str(lines))
        check("CAP=6: still 6 plain learnings plus the overflow line",
              sum(1 for l in lines if "★" not in l) == 6 and "(1 ältere: learnings.json)" in content, str(lines))
        check("CAP=6: no member listed next to its shown pointer", not any(l.startswith("- [M") for l in lines),
              str(lines))
        check("CAP=6: pointers come before the plain learnings",
              max(i for i, l in enumerate(lines) if "★" in l) < min(i for i, l in enumerate(lines) if "★" not in l))
        before = content
        p = run([mem, "--agents-md", agents], cwd=tmp)
        check("pointers: second run byte-identical", read(agents) == before and "up to date" in p.stdout, p.stdout)

    # 3. one UID in two rules
    with tempfile.TemporaryDirectory() as tmp:
        rules = os.path.join(tmp, "regeln.md")
        with open(rules, "w", encoding="utf-8") as f:
            f.write("# Regeln\n")
        shared = learning("L1", "2026-09-01", "gehoert zu zwei Regeln", bridge="approved")
        mem, agents = setup(tmp, [shared, learning("L2", "2026-09-02", "nur Regel A", bridge="approved"),
                                  pointer("PA", ["L1", "L2"], rules), pointer("PB", ["L1"], rules)], "# AGENTS\n")
        run([mem, "--agents-md", agents], cwd=tmp)
        lines = block_lines(read(agents))
        check("UID in two shown rules: not listed itself, both pointers count it",
              not any(l.startswith("- [L1]") for l in lines)
              and any(l.startswith("- [PA]") and "(+2 Belege)" in l for l in lines)
              and any(l.startswith("- [PB]") and "(+1 Beleg)" in l for l in lines), str(lines))
    sys.path.insert(0, os.path.join(PLUGIN_ROOT, "scripts"))
    import bridge_projection as bp
    import projection_text as pt
    rules = os.path.join(PLUGIN_ROOT, "README.md")
    by_id = {f"M{i}": learning(f"M{i}", "2026-09-01", "x", bridge="approved") for i in range(12)}
    ptrs = [pointer(f"P{i}", [f"M{i}"], rules) for i in range(11)] + [pointer("PX", ["M11"], rules, date="2026-01-01")]
    ptrs[0]["derived_from"] = ["M0", "M11"]   # M11 is in P0 (2 members, ranks first) and in PX
    shown, folded, hidden = pt.select_pointers(ptrs, by_id, 10, files=set())
    check("UID in a shown and a hidden rule: folded once by the shown one",
          "M11" in folded and "PX" not in [p["id"] for p in shown] and hidden == 2, str([p["id"] for p in shown]))

    # 4. always-loaded rule files rank last; cut by the cap, their members stay folded silently
    loaded = {os.path.normcase(os.path.abspath(rules))}
    other = os.path.join(PLUGIN_ROOT, "CLAUDE.md")
    ptrs = [pointer("PL", ["M0", "M1", "M2"], rules)] + [pointer(f"Q{i}", [f"M{3 + i}"], other) for i in range(2)]
    shown, folded, hidden = pt.select_pointers(ptrs, by_id, 2, files=loaded)
    check("always-loaded: ranks last despite most members, its members stay folded",
          sorted(p["id"] for p in shown) == ["Q0", "Q1"] and {"M0", "M1", "M2"} <= folded and hidden == 1,
          f"{[p['id'] for p in shown]} {sorted(folded)}")
    shown, folded, hidden = pt.select_pointers(ptrs, by_id, 5, files=loaded)
    check("always-loaded: shown last when the cap leaves room", [p["id"] for p in shown][-1] == "PL" and len(shown) == 3,
          str([p["id"] for p in shown]))
    check("shown(): the exact rendered selection, pointers first",
          callable(getattr(bp, "shown", None)))


def main():
    print("=== bridge_projection.py tests ===")
    check("script exists", os.path.isfile(SCRIPT))
    if not os.path.isfile(SCRIPT):
        print("=== 1 failure (script missing) ===")
        return 1

    foreign = "# AGENTS.md\n\n## Projekt\nFremder Inhalt bleibt.\n"

    # 1. keine approved -> AGENTS.md byte-identisch, exit 0
    with tempfile.TemporaryDirectory() as tmp:
        mem, agents = setup(tmp, [learning("L1", "2026-07-16", "kein flag")],
                            foreign)
        p = run([mem, "--agents-md", agents], cwd=tmp)
        check("no approved: exit 0", p.returncode == 0, f"rc={p.returncode} err={p.stderr[:200]}")
        check("no approved: file untouched", read(agents) == foreign)

    # 2. approved -> Block angehaengt, Fremdinhalt byte-identisch davor
    with tempfile.TemporaryDirectory() as tmp:
        mem, agents = setup(tmp, [
            learning("L1", "2026-07-15", "altes learning", bridge="approved"),
            learning("L2", "2026-07-16", "neues learning", bridge="approved"),
            learning("L3", "2026-07-16", "nur candidate", bridge="candidate"),
        ], foreign)
        p = run([mem, "--agents-md", agents], cwd=tmp)
        content = read(agents)
        check("approved: exit 0", p.returncode == 0, f"rc={p.returncode} err={p.stderr[:200]}")
        check("approved: foreign prefix preserved", content.startswith(foreign))
        check("approved: block present", BEGIN in content and END in content)
        check("approved: entries rendered", "[L1]" in content and "[L2]" in content)
        check("candidate excluded", "[L3]" not in content)
        check("newest first", content.index("[L2]") < content.index("[L1]"))

        # 3. Idempotenz: zweiter Lauf -> identische Datei
        run([mem, "--agents-md", agents], cwd=tmp)
        check("idempotent", read(agents) == content)

        # 4. Update: Text aendern -> Block aktualisiert, genau EIN Block
        store = json.load(open(os.path.join(mem, "learnings", "learnings.json"),
                               encoding="utf-8"))
        store[1]["text"] = "neues learning ueberarbeitet"
        with open(os.path.join(mem, "learnings", "learnings.json"), "w",
                  encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False)
        run([mem, "--agents-md", agents], cwd=tmp)
        content = read(agents)
        check("update: new text rendered", "ueberarbeitet" in content)
        check("update: single block", content.count(BEGIN) == 1)

        # 5. Widerruf: alle Flags weg -> Block entfernt, Fremdinhalt bleibt
        for e in store:
            e.pop("bridge_status", None)
        with open(os.path.join(mem, "learnings", "learnings.json"), "w",
                  encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False)
        run([mem, "--agents-md", agents], cwd=tmp)
        check("revoke: block removed", BEGIN not in read(agents))
        check("revoke: foreign preserved", read(agents).startswith("# AGENTS.md"))

    # 6. Cap 6 + sichtbarer Ueberhang
    with tempfile.TemporaryDirectory() as tmp:
        many = [learning(f"L{i}", f"2026-07-{i:02d}", f"text {i}",
                         bridge="approved") for i in range(1, 13)]
        mem, agents = setup(tmp, many, foreign)
        run([mem, "--agents-md", agents], cwd=tmp)
        content = read(agents)
        block = content[content.index(BEGIN):content.index(END)]
        check("cap: 6 entries", block.count("- [L") == 6, f"n={block.count('- [L')}")
        check("cap: overflow visible", "6 ältere" in block, block[-200:])
        check("cap: newest kept", "[L12]" in block and "[L6]" not in block)

    # 6b. Kurzfassung wie in MEMORY.md (projection_text): Wortgrenze, <= 150 Zeichen Text,
    #     jede Zeile <= 200, Verweis auf den Volltext; kurzer Text bleibt
    with tempfile.TemporaryDirectory() as tmp:
        long_text = " ".join(f"wort{i:03d}" for i in range(80))
        mem, agents = setup(tmp, [
            learning("L1", "2026-07-16", long_text, bridge="approved"),
            learning("L2", "2026-07-17", "kurz", bridge="approved"),
        ], foreign)
        run([mem, "--agents-md", agents], cwd=tmp)
        block = read(agents)
        l1 = [ln for ln in block.splitlines() if ln.startswith("- [L1]")][0]
        summary = l1.split(") ", 1)[-1]
        check("short form: cut at a word boundary with ellipsis, <= 150 chars",
              summary.endswith("…") and len(summary) <= 150
              and summary[:-1].rstrip().split(" ")[-1] in long_text.split(" "), f"{len(summary)}: {summary}")
        check("short form: short text intact", "- [L2] (2026-07-17) kurz" in block)
        check("short form: pointer to the full text",
              "Volltext per ID: .agent-memory/learnings/learnings.json" in block)

    # 6c. Grenze: 150 bleibt ganz, 151 (ohne Leerzeichen) -> 149 + Ellipse; Umbrueche kollabiert
    with tempfile.TemporaryDirectory() as tmp:
        mem, agents = setup(tmp, [
            learning("L1", "2026-07-16", "y" * 150, bridge="approved"),
            learning("L2", "2026-07-17", "z" * 151, bridge="approved"),
            learning("L3", "2026-07-15", "erste Zeile\nzweite   Zeile", bridge="approved"),
        ], foreign)
        write_tasks(mem, [task("T-1", "Titel " * 60)])
        run([mem, "--agents-md", agents], cwd=tmp)
        content = read(agents)
        block = content[content.index(BEGIN):]
        l1 = [ln for ln in block.splitlines() if ln.startswith("- [L1]")][0]
        l2 = [ln for ln in block.splitlines() if ln.startswith("- [L2]")][0]
        check("short form: exactly 150 intact", l1.endswith("y" * 150) and "…" not in l1)
        check("short form: 151 -> 149 + ellipsis",
              l2.endswith("z" * 149 + "…") and not l2.endswith("z" * 150 + "…"))
        check("short form: newlines collapsed", "- [L3] (2026-07-15) erste Zeile zweite Zeile" in block)
        check("every block line <= 200 chars (learnings and task titles)",
              all(len(ln) <= 200 for ln in block.splitlines()),
              str([len(ln) for ln in block.splitlines() if len(ln) > 200]))

    # 6c2. ein sehr langes project_id haelt die Task-Ueberschrift trotzdem <= 200
    with tempfile.TemporaryDirectory() as tmp:
        mem, agents = setup(tmp, [], foreign)
        with open(os.path.join(mem, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"project_id": "p" * 250}, f)
        write_tasks(mem, [task("T-1", "kurz")])
        run([mem, "--agents-md", agents], cwd=tmp)
        content = read(agents)
        block = content[content.index(BEGIN):]
        check("long project_id: task heading <= 200 chars",
              all(len(ln) <= 200 for ln in block.splitlines()),
              str([len(ln) for ln in block.splitlines() if len(ln) > 200]))

    # 6d. summary-Feld schlaegt den Schnitt des Volltexts (gleiche Regel wie MEMORY.md)
    with tempfile.TemporaryDirectory() as tmp:
        e = learning("L1", "2026-07-16", "Kontext " * 40 + "Schluss.", bridge="approved")
        e["summary"] = "Kernaussage fuer Codex."
        mem, agents = setup(tmp, [e], foreign)
        run([mem, "--agents-md", agents], cwd=tmp)
        check("summary used in AGENTS.md", "- [L1] (2026-07-16) Kernaussage fuer Codex." in read(agents))

    # 7. superseded approved wird ausgeschlossen
    with tempfile.TemporaryDirectory() as tmp:
        mem, agents = setup(tmp, [
            learning("L1", "2026-07-16", "ersetzt", bridge="approved",
                     superseded="L2"),
            learning("L2", "2026-07-16", "ersatz", bridge="approved"),
        ], foreign)
        run([mem, "--agents-md", agents], cwd=tmp)
        content = read(agents)
        check("superseded excluded", "[L1]" not in content and "[L2]" in content)

    # 8. AGENTS.md fehlt + approved -> Datei mit Block angelegt
    with tempfile.TemporaryDirectory() as tmp:
        mem, agents = setup(tmp, [learning("L1", "2026-07-16", "t",
                                           bridge="approved")])
        p = run([mem, "--agents-md", agents], cwd=tmp)
        check("create: exit 0", p.returncode == 0, f"rc={p.returncode} err={p.stderr[:200]}")
        check("create: block present", BEGIN in read(agents))

    # 9. learnings.json fehlt -> No-op exit 0; invalid -> exit 1
    with tempfile.TemporaryDirectory() as tmp:
        mem = os.path.join(tmp, ".agent-memory")
        os.makedirs(os.path.join(mem, "learnings"))
        agents = os.path.join(tmp, "AGENTS.md")
        p = run([mem, "--agents-md", agents], cwd=tmp)
        check("missing store: exit 0", p.returncode == 0, f"rc={p.returncode}")
        check("missing store: no file created", not os.path.exists(agents))
        with open(os.path.join(mem, "learnings", "learnings.json"), "w",
                  encoding="utf-8") as f:
            f.write("{ not json")
        p = run([mem, "--agents-md", agents], cwd=tmp)
        check("invalid store: exit 1", p.returncode == 1, f"rc={p.returncode}")

    # 10. Usage-Fehler
    with tempfile.TemporaryDirectory() as tmp:
        p = run([], cwd=tmp)
        check("usage error: exit 2", p.returncode == 2, f"rc={p.returncode}")

    # 11. Nur offene Tasks, keine approved Learnings -> Block mit Task-Sektion
    with tempfile.TemporaryDirectory() as tmp:
        mem, agents = setup(tmp, [learning("L1", "2026-07-16", "kein flag")],
                            foreign)
        write_tasks(mem, [task("T-25", "Sichtpruefung PK1/PK4"),
                          task("T-9", "erledigt", "done")])
        p = run([mem, "--agents-md", agents], cwd=tmp)
        content = read(agents)
        check("tasks-only: exit 0", p.returncode == 0,
              f"rc={p.returncode} err={p.stderr[:200]}")
        check("tasks-only: block present", BEGIN in content and END in content)
        check("tasks-only: open task rendered",
              "[T-25]" in content and "Sichtpruefung" in content)
        check("tasks-only: done task filtered", "[T-9]" not in content)
        check("tasks-only: foreign preserved", content.startswith(foreign))

    # 12. Tasks + Learnings -> beide Sektionen, Tasks zuerst, ein Block
    with tempfile.TemporaryDirectory() as tmp:
        mem, agents = setup(tmp, [learning("L1", "2026-07-16", "lern",
                                           bridge="approved")], foreign)
        write_tasks(mem, [task("T-25", "offene sache")])
        run([mem, "--agents-md", agents], cwd=tmp)
        content = read(agents)
        check("both: task section present", "Offene Tasks" in content)
        check("both: learning section present", "Learnings von Claude" in content)
        check("both: tasks before learnings",
              content.index("[T-25]") < content.index("[L1]"))
        check("both: single block", content.count(BEGIN) == 1)

    # 13. Task-Cap 5 + sichtbarer Ueberhang
    with tempfile.TemporaryDirectory() as tmp:
        mem, agents = setup(tmp, [learning("L1", "2026-07-16", "x")], foreign)
        write_tasks(mem, [task(f"T-{i}", f"task {i}") for i in range(1, 8)])
        run([mem, "--agents-md", agents], cwd=tmp)
        block = read(agents)
        block = block[block.index(BEGIN):block.index(END)]
        check("task-cap: 5 tasks", block.count("- [T-") == 5,
              f"n={block.count('- [T-')}")
        check("task-cap: overflow visible", "2 weitere" in block)

    # 14. korrupte open-tasks.json -> fail-soft: Learnings trotzdem, exit 0
    with tempfile.TemporaryDirectory() as tmp:
        mem, agents = setup(tmp, [learning("L1", "2026-07-16", "lern",
                                           bridge="approved")], foreign)
        os.makedirs(os.path.join(mem, "context"))
        with open(os.path.join(mem, "context", "open-tasks.json"), "w",
                  encoding="utf-8") as f:
            f.write("{ kaputt")
        p = run([mem, "--agents-md", agents], cwd=tmp)
        content = read(agents)
        check("corrupt-tasks: exit 0", p.returncode == 0, f"rc={p.returncode}")
        check("corrupt-tasks: learnings still rendered", "[L1]" in content)
        check("corrupt-tasks: no task section", "Offene Tasks" not in content)

    # 15. Idempotenz mit Tasks
    with tempfile.TemporaryDirectory() as tmp:
        mem, agents = setup(tmp, [learning("L1", "2026-07-16", "x",
                                           bridge="approved")], foreign)
        write_tasks(mem, [task("T-25", "sache")])
        run([mem, "--agents-md", agents], cwd=tmp)
        c1 = read(agents)
        run([mem, "--agents-md", agents], cwd=tmp)
        check("tasks idempotent", read(agents) == c1)

    # 16. Tasks weg + keine Learnings -> Block entfernt, Fremdinhalt bleibt
    with tempfile.TemporaryDirectory() as tmp:
        mem, agents = setup(tmp, [learning("L1", "2026-07-16", "x")], foreign)
        write_tasks(mem, [task("T-25", "sache")])
        run([mem, "--agents-md", agents], cwd=tmp)
        check("pre: block present", BEGIN in read(agents))
        write_tasks(mem, [])
        run([mem, "--agents-md", agents], cwd=tmp)
        check("empty-tasks-no-learn: block removed", BEGIN not in read(agents))
        check("empty-tasks-no-learn: foreign preserved",
              read(agents).startswith(foreign))

    # 17. Task-Label aus config.json project_id, nie hart kodiert (T-014)
    with tempfile.TemporaryDirectory() as tmp:
        mem, agents = setup(tmp, [learning("L1", "2026-07-16", "x")], foreign)
        write_tasks(mem, [task("T-25", "sache")])
        with open(os.path.join(mem, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"project_id": "fooproj"}, f)
        run([mem, "--agents-md", agents], cwd=tmp)
        content = read(agents)
        check("label: from config project_id", "Offene Tasks (fooproj)" in content)
        check("label: membrain not hard-coded", "(membrain)" not in content)

    # 18. Label-Fallback ohne config.json -> Projektordner-Name, exit 0
    with tempfile.TemporaryDirectory() as tmp:
        mem, agents = setup(tmp, [learning("L1", "2026-07-16", "x")], foreign)
        write_tasks(mem, [task("T-25", "sache")])
        p = run([mem, "--agents-md", agents], cwd=tmp)
        content = read(agents)
        expected = os.path.basename(os.path.abspath(tmp))
        check("label-fallback: exit 0", p.returncode == 0, f"rc={p.returncode}")
        check("label-fallback: project dir name",
              f"Offene Tasks ({expected})" in content, f"expected ({expected})")

    # 19. codex-sourced approved learnings must NOT be projected back to Codex (loop guard)
    with tempfile.TemporaryDirectory() as tmp:
        rows = [learning("L1", "2026-09-01", "Claude insight", bridge="approved")]
        codex_row = learning("L2", "2026-09-02", "Codex tip echoed", bridge="approved")
        codex_row["source_agent"] = "codex"
        rows.append(codex_row)
        mem, agents = setup(tmp, rows, agents_body="# Rules\n")
        p = run([mem, "--agents-md", agents], cwd=tmp)
        txt = open(agents, encoding="utf-8").read()
        check("loop guard: claude learning projected", "[L1]" in txt, txt)
        check("loop guard: codex learning excluded", "[L2]" not in txt and "Codex tip echoed" not in txt, txt)
        check("loop guard: count line says 1 approved", "1 approved" in p.stdout, p.stdout)

    # 20. bridge:begin marker carries the generation date (DCO-8974: Staleness
    # sichtbar machen) and stays idempotent within the same day (test 3/15).
    with tempfile.TemporaryDirectory() as tmp:
        import datetime as _dt
        mem, agents = setup(tmp, [learning("L1", "2026-07-16", "x", bridge="approved")], foreign)
        run([mem, "--agents-md", agents], cwd=tmp)
        content = read(agents)
        today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
        begin_line = next(ln for ln in content.splitlines() if ln.startswith(BEGIN))
        check("marker: generation date present", today in begin_line, begin_line)
        run([mem, "--agents-md", agents], cwd=tmp)
        check("marker: idempotent same-day re-run", read(agents) == content)

    # 21. Zeilenenden der vorhandenen AGENTS.md bleiben erhalten (CRLF bleibt CRLF)
    with tempfile.TemporaryDirectory() as tmp:
        mem, agents = setup(tmp, [learning("L1", "2026-07-16", "kurz", bridge="approved")], "")
        crlf = "# AGENTS.md\r\n\r\nFremder Inhalt.\r\n"
        with open(agents, "wb") as f:
            f.write(crlf.encode("utf-8"))
        run([mem, "--agents-md", agents], cwd=tmp)
        with open(agents, "rb") as f:
            data = f.read()
        check("CRLF AGENTS.md stays CRLF, foreign part byte-identical",
              data.count(b"\n") == data.count(b"\r\n") and data.startswith(crlf.encode("utf-8"))
              and b"[L1]" in data, repr(data[-80:]))
        run([mem, "--agents-md", agents], cwd=tmp)
        with open(agents, "rb") as f:
            check("CRLF AGENTS.md: second run byte-identical (no growing blank lines)", f.read() == data)
        mixed = "# Kopf\nNotiz\r\n"
        with open(agents, "wb") as f:
            f.write(mixed.encode("utf-8"))
        run([mem, "--agents-md", agents], cwd=tmp)
        with open(agents, "rb") as f:
            data = f.read()
        check("mixed line endings: foreign part byte-identical", data.startswith(mixed.encode("utf-8")),
              repr(data[:40]))

    # 22. DCO #9695: a UTF-8 BOM (PowerShell Set-Content -Encoding utf8) is not corruption -
    # learnings.json, open-tasks.json and config.json with BOM project exactly like without.
    outs = {}
    for label, prefix in (("plain", b""), ("bom", b"\xef\xbb\xbf")):
        with tempfile.TemporaryDirectory() as tmp:
            mem, agents = setup(tmp, [learning("L1", "2026-07-16", "bom learning", bridge="approved")], "# AGENTS\n")
            write_tasks(mem, [task("T1", "bom task")])
            with open(os.path.join(mem, "config.json"), "w", encoding="utf-8") as f:
                json.dump({"project_id": "bomproj"}, f)
            for rel in (("learnings", "learnings.json"), ("context", "open-tasks.json"), ("config.json",)):
                path = os.path.join(mem, *rel)
                with open(path, "rb") as f:
                    raw = f.read()
                with open(path, "wb") as f:
                    f.write(prefix + raw)
            p = run([mem, "--agents-md", agents], cwd=tmp)
            content = read(agents)
            outs[label] = (p.returncode, content)
            check(f"BOM twin ({label}): exit 0, learning + task + label projected",
                  p.returncode == 0 and "[L1]" in content and "[T1]" in content
                  and "Offene Tasks (bomproj)" in content,
                  f"rc={p.returncode} err={p.stderr[:200]} {content[-200:]!r}")
    check("BOM store: AGENTS.md identical to the BOM-less result", outs["bom"] == outs["plain"])

    pointer_tests()

    n = len(FAILURES)
    print(f"=== {n} failure(s) ===" if n else "=== all tests passed ===")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())

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

    # 6b. Text-Cap 220: langer Text wird mit Ellipse gekuerzt, kurzer bleibt
    with tempfile.TemporaryDirectory() as tmp:
        long_text = "x" * 300
        mem, agents = setup(tmp, [
            learning("L1", "2026-07-16", long_text, bridge="approved"),
            learning("L2", "2026-07-17", "kurz", bridge="approved"),
        ], foreign)
        run([mem, "--agents-md", agents], cwd=tmp)
        block = read(agents)
        l1 = [ln for ln in block.splitlines() if ln.startswith("- [L1]")][0]
        check("text-cap: truncated with ellipsis",
              l1.endswith("…") and len(l1) < 260, f"len={len(l1)}")
        check("text-cap: short text intact", "- [L2] (2026-07-17) kurz" in block)

    # 6c. Text-Cap Grenze: 220 bleibt ganz, 221 wird zu 219 Zeichen + Ellipse (= 220)
    with tempfile.TemporaryDirectory() as tmp:
        mem, agents = setup(tmp, [
            learning("L1", "2026-07-16", "y" * 220, bridge="approved"),
            learning("L2", "2026-07-17", "z" * 221, bridge="approved"),
        ], foreign)
        run([mem, "--agents-md", agents], cwd=tmp)
        block = read(agents)
        l1 = [ln for ln in block.splitlines() if ln.startswith("- [L1]")][0]
        l2 = [ln for ln in block.splitlines() if ln.startswith("- [L2]")][0]
        check("text-cap: exactly 220 intact", l1.endswith("y" * 220) and "…" not in l1)
        check("text-cap: 221 -> 219 + ellipsis",
              l2.endswith("z" * 219 + "…") and not l2.endswith("z" * 220 + "…"))

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

    n = len(FAILURES)
    print(f"=== {n} failure(s) ===" if n else "=== all tests passed ===")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())

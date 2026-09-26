#!/usr/bin/env python3
"""Tests for scripts/memory_index_projection.py (E2 hub -> Claude MEMORY.md block).
Run: python tests/test-memory-index-projection.py  (exit 0 = pass)"""
import json
import os
import subprocess
import sys
import tempfile

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PLUGIN_ROOT, "scripts", "memory_index_projection.py")
FAILURES = []
BEGIN = "<!-- bridge:claude-native:begin"
END = "<!-- bridge:claude-native:end -->"


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS: {name}")
    else:
        print(f"  FAIL: {name} {detail}")
        FAILURES.append(name)


def run(args, cwd):
    return subprocess.run([sys.executable, SCRIPT] + args, capture_output=True,
                          encoding="utf-8", errors="replace", cwd=cwd)


def learning(lid, date, text, importance=3, bridge="approved", kind=None, agent=None, superseded=None):
    e = {"id": lid, "date": date, "text": text, "importance": importance, "tags": [],
         "layer": "short-term", "superseded_by": superseded, "last_relevant": date, "bridge_status": bridge}
    if kind:
        e["kind"] = kind
    if agent:
        e["source_agent"] = agent
    return e


def setup(tmp, learnings, memory_body=None):
    mem = os.path.join(tmp, ".agent-memory")
    os.makedirs(os.path.join(mem, "learnings"), exist_ok=True)
    with open(os.path.join(mem, "learnings", "learnings.json"), "w", encoding="utf-8") as f:
        json.dump(learnings, f, ensure_ascii=False)
    md = os.path.join(tmp, "memory", "MEMORY.md")
    if memory_body is not None:
        os.makedirs(os.path.dirname(md), exist_ok=True)
        with open(md, "w", encoding="utf-8") as f:
            f.write(memory_body)
    return mem, md


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def main():
    print("=== memory_index_projection.py tests ===")
    check("script exists", os.path.isfile(SCRIPT))
    if not os.path.isfile(SCRIPT):
        print("=== 1 failure (script missing) ===")
        return 1

    hand = "- [pi-agent-ordner](pi-agent-ordner.md) — handwritten line\n- [x](x.md) — keep me\n"

    # 1. order, codex marker, block appended after handwritten lines, byte-identical outside
    with tempfile.TemporaryDirectory() as tmp:
        rows = [learning("L1", "2026-08-01", "Older learning", importance=5),
                learning("L2", "2026-08-20", "Newer learning"),
                learning("L3", "2026-07-01", "A feedback rule", kind="feedback"),
                learning("L4", "2026-08-10", "Codex tip", agent="codex", kind="learning"),
                learning("L5", "2026-08-30", "Candidate only", bridge="candidate"),
                learning("L6", "2026-08-30", "Superseded", superseded="L2")]
        mem, md = setup(tmp, rows, memory_body=hand)
        p = run([mem, "--memory-md", md], cwd=tmp)
        check("exit 0", p.returncode == 0, p.stderr[:300])
        txt = read(md)
        check("handwritten intact", txt.startswith(hand), txt[:120])
        body = txt[txt.index(BEGIN):]
        lines = [l for l in body.split("\n") if l.startswith("- [")]
        check("feedback first, then date desc", [l[3:5] for l in lines] == ["L3", "L2", "L4", "L1"], str(lines))
        check("codex marker in line", "(2026-08-10, codex) Codex tip" in txt, txt)
        check("candidate + superseded excluded", "L5" not in txt and "L6" not in txt)
        check("heading present", "## Bridge: Learnings + Feedback (learnings.json, kuratiert)" in txt)
        check("ends with END marker", txt.rstrip().endswith(END))
        # idempotent
        before = txt
        p = run([mem, "--memory-md", md], cwd=tmp)
        check("second run byte-identical", read(md) == before and "up to date" in p.stdout, p.stdout)

    # 2. cap 20 + overflow line
    with tempfile.TemporaryDirectory() as tmp:
        rows = [learning(f"L{i}", f"2026-08-{(i % 28) + 1:02d}", f"Text {i}") for i in range(1, 26)]
        mem, md = setup(tmp, rows, memory_body="")
        run([mem, "--memory-md", md], cwd=tmp)
        txt = read(md)
        check("cap 20 lines", sum(1 for l in txt.split("\n") if l.startswith("- [")) == 20)
        check("overflow line", "(5 weitere approved: .agent-memory/learnings/learnings.json)" in txt, txt[-200:])

    # 3. no MEMORY.md -> created with block only; 0 approved -> block removed, file untouched otherwise
    with tempfile.TemporaryDirectory() as tmp:
        mem, md = setup(tmp, [learning("L1", "2026-08-01", "Only one")])
        p = run([mem, "--memory-md", md], cwd=tmp)
        check("missing file created", os.path.isfile(md) and read(md).startswith(BEGIN))
        with open(os.path.join(mem, "learnings", "learnings.json"), "w", encoding="utf-8") as f:
            json.dump([learning("L1", "2026-08-01", "Only one", bridge="candidate")], f)
        run([mem, "--memory-md", md], cwd=tmp)
        check("0 approved -> block removed", BEGIN not in read(md), read(md))
        hand_only = "- [a](a.md) — a\n"
        with open(md, "w", encoding="utf-8") as f:
            f.write(hand_only)
        p = run([mem, "--memory-md", md], cwd=tmp)
        check("0 approved + no block -> untouched", read(md) == hand_only and "nothing to do" in p.stdout, p.stdout)

    # 4. --project-root derives the native path
    with tempfile.TemporaryDirectory() as tmp:
        home = os.path.join(tmp, "home")
        root = os.path.join(home, "dynamic_central_orchestrator")
        os.makedirs(root)
        mem, _ = setup(tmp, [learning("L1", "2026-08-01", "Derived path")])
        p = run([mem, "--project-root", root, "--home", home], cwd=tmp)
        import re
        expected = os.path.join(home, ".claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(root)), "memory", "MEMORY.md")
        check("project-root -> hashed native path", os.path.isfile(expected), expected + "\n" + p.stdout + p.stderr)
        check("underscore hashed to hyphen", "dynamic-central-orchestrator" in expected)

    # 5. invalid store -> exit 1; usage -> exit 2
    with tempfile.TemporaryDirectory() as tmp:
        mem, md = setup(tmp, [], memory_body="")
        with open(os.path.join(mem, "learnings", "learnings.json"), "w", encoding="utf-8") as f:
            f.write("{oops")
        check("invalid json exit 1", run([mem, "--memory-md", md], cwd=tmp).returncode == 1)
        check("usage exit 2", run([mem], cwd=tmp).returncode == 2)

    # 6. --print-native-dir prints the dirname of the derived native MEMORY.md path
    with tempfile.TemporaryDirectory() as tmp:
        p = run(["--print-native-dir", tmp, "--home", tmp], cwd=tmp)
        check("print-native-dir", p.returncode == 0 and p.stdout.strip().endswith(os.path.join("memory")), p.stdout)

    n = len(FAILURES)
    print(f"=== {n} failure{'s' if n != 1 else ''} ===")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

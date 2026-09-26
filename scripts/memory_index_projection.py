#!/usr/bin/env python3
"""E2: hub (learnings.json) -> managed block in Claude Code's native MEMORY.md.

MEMORY.md is the ONLY native-memory file Claude loads at session start, so
approved learnings + feedback rendered here are always present for Claude —
including source_agent=codex entries (that is the Codex->Claude half of the
bridge). Everything outside the markers (handwritten index lines) stays
byte-identical. learnings.json stays canonical; regenerate any time.

Usage:
  python memory_index_projection.py <mem-dir> --memory-md <path>
  python memory_index_projection.py <mem-dir> --project-root <dir> [--home <dir>]
  python memory_index_projection.py --print-native-dir <project-root> [--home <dir>]
Exit codes: 0 ok (also no-op) · 1 learnings.json unreadable · 2 usage error.

`--print-native-dir` is a separate, side-effect-free lookup: it prints the
native memory DIRECTORY (not the MEMORY.md file path) derived from the same
hashing rule as `native_memory_md()` and exits 0 — no learnings.json read, no
projection, no write.
"""
import argparse
import json
import os
import re
import sys

BEGIN = ("<!-- bridge:claude-native:begin — generiert von agentic-os "
         "memory_index_projection, NICHT von Hand editieren -->")
END = "<!-- bridge:claude-native:end -->"
BEGIN_PREFIX = "<!-- bridge:claude-native:begin"
HEADING = "## Bridge: Learnings + Feedback (learnings.json, kuratiert)"
CAP = 20


def native_memory_md(project_root, home=None):
    """Same hashing rule as membrain/scripts/native_paths.py: every char outside
    [A-Za-z0-9] of the absolute project path becomes '-'."""
    home = home or os.path.expanduser("~")
    hashed = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(project_root))
    return os.path.join(home, ".claude", "projects", hashed, "memory", "MEMORY.md")


def load_approved(mem_dir):
    path = os.path.join(mem_dir, "learnings", "learnings.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"memory-index: learnings.json unreadable: {exc}", file=sys.stderr)
        return None
    entries = data if isinstance(data, list) else data.get("learnings", [])
    approved = [e for e in entries if isinstance(e, dict)
                and e.get("bridge_status") == "approved" and not e.get("superseded_by")]
    approved.sort(key=lambda e: (e.get("kind", "learning") == "feedback",
                                 str(e.get("date", "")), int(e.get("importance", 0))), reverse=True)
    return approved


def render_block(approved):
    lines = [BEGIN, HEADING]
    for e in approved[:CAP]:
        src = ", codex" if e.get("source_agent", "claude") == "codex" else ""
        lines.append(f"- [{e.get('id')}] ({e.get('date')}{src}) {e.get('text')}")
    overflow = len(approved) - CAP
    if overflow > 0:
        lines.append(f"({overflow} weitere approved: .agent-memory/learnings/learnings.json)")
    lines.append(END)
    return "\n".join(lines) + "\n"


def strip_block(text):
    out, inside, found = [], False, False
    for line in text.split("\n"):
        if not inside and line.startswith(BEGIN_PREFIX):
            inside, found = True, True
            continue
        if inside:
            if line.strip() == END:
                inside = False
            continue
        out.append(line)
    if not found:
        return text
    result = "\n".join(out)
    while result.endswith("\n\n"):
        result = result[:-1]
    return result


def write_atomic(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def main(argv):
    if argv[:1] == ["--print-native-dir"]:
        home = argv[argv.index("--home") + 1] if "--home" in argv else None
        print(os.path.dirname(native_memory_md(argv[1], home)))
        return 0
    ap = argparse.ArgumentParser(prog="memory_index_projection.py")
    ap.add_argument("mem_dir")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--memory-md")
    g.add_argument("--project-root")
    ap.add_argument("--home")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    target = a.memory_md or native_memory_md(a.project_root, a.home)

    approved = load_approved(a.mem_dir)
    if approved is None:
        return 1
    exists = os.path.isfile(target)
    current = ""
    if exists:
        with open(target, "r", encoding="utf-8") as f:
            current = f.read()

    if not approved:
        if exists and BEGIN_PREFIX in current:
            write_atomic(target, strip_block(current))
            print(f"memory-index: 0 approved — block removed from {target}")
        else:
            print("memory-index: no approved learnings, nothing to do")
        return 0

    block = render_block(approved)
    base = strip_block(current) if exists else ""
    if base and not base.endswith("\n"):
        base += "\n"
    new = base + ("\n" if base else "") + block
    if exists and new == current:
        print(f"memory-index: {len(approved)} approved — block up to date")
        return 0
    write_atomic(target, new)
    cap = f" (capped at {CAP})" if len(approved) > CAP else ""
    print(f"memory-index: {len(approved)} approved -> {target}{cap}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""E2: hub (learnings.json) -> managed block in Claude Code's native MEMORY.md.

MEMORY.md is the ONLY native-memory file Claude loads at session start, so
approved learnings + feedback rendered here are always present for Claude —
including source_agent=codex entries (that is the Codex->Claude half of the
bridge). Each entry is a short form (~150 chars, line <= 200) behind its [id];
the full text stays in learnings.json. Everything outside the markers
(handwritten index lines) stays byte-identical. learnings.json stays canonical;
regenerate any time.

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

# Load-limit rule (200 lines / 25 000 bytes) has one home: the native audit.
from native_memory_audit import count_lines, load_level

BEGIN = ("<!-- bridge:claude-native:begin — generiert von agentic-os "
         "memory_index_projection, NICHT von Hand editieren -->")
END = "<!-- bridge:claude-native:end -->"
BEGIN_PREFIX = "<!-- bridge:claude-native:begin"
HEADING = "## Bridge: Learnings + Feedback (learnings.json, kuratiert)"
POINTER = "Kurzfassungen — Volltext per ID: .agent-memory/learnings/learnings.json"
CAP = 20
# MEMORY.md is loaded into every session, so CAP bounds the count and these two bound
# the length. The native memory-index guard sees Write/Edit tool calls only, never
# this script — the line limit has to be enforced here.
SUMMARY_CHARS = 150
LINE_LIMIT = 200
ELLIPSIS = "…"


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


def shorten(text, limit):
    """Whitespace-collapsed text, cut to <= limit chars at a word boundary + ellipsis."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    head = text[:limit - len(ELLIPSIS)]
    cut = head.rfind(" ")
    if cut > 0:
        head = head[:cut]
    return head.rstrip(" ,;:-—") + ELLIPSIS


def render_line(e):
    src = ", codex" if e.get("source_agent", "claude") == "codex" else ""
    prefix = f"- [{e.get('id')}] ({e.get('date')}{src}) "
    room = max(LINE_LIMIT - len(prefix), len(ELLIPSIS))
    line = prefix + shorten(e.get("text"), min(SUMMARY_CHARS, room))
    # an absurdly long id/date alone could still exceed the limit: hard cap
    return line if len(line) <= LINE_LIMIT else line[:LINE_LIMIT - len(ELLIPSIS)] + ELLIPSIS


def render_block(approved):
    lines = [BEGIN, HEADING, POINTER]
    for e in approved[:CAP]:
        lines.append(render_line(e))
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


def load_report(text):
    """Load share of the MEMORY.md as written -> (suffix for the status line, warning|None).
    The block sits at the END of the file, so it is the first part Claude Code cuts off."""
    data = text.encode("utf-8")
    pct, limit, level = load_level(len(data), count_lines(data))
    warning = None
    if level == "truncated":
        warning = (f"memory-index: WARNUNG MEMORY.md liegt bei {pct} % der Ladegrenze "
                   f"(200 Zeilen / 25 000 Bytes, Engpass {limit}) — der Bruecken-Block am Ende "
                   f"wird nicht vollstaendig geladen; handgeschriebene Index-Zeilen kuerzen")
    return f" · Ladegrenze {pct} % {limit} ({level})", warning


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
            stripped = strip_block(current)
            write_atomic(target, stripped)
            load, warning = load_report(stripped)
            print(f"memory-index: 0 approved — block removed from {target}{load}")
        else:
            load, warning = load_report(current) if exists else ("", None)
            print(f"memory-index: no approved learnings, nothing to do{load}")
        if warning:
            print(warning)
        return 0

    block = render_block(approved)
    base = strip_block(current) if exists else ""
    if base and not base.endswith("\n"):
        base += "\n"
    new = base + ("\n" if base else "") + block
    load, warning = load_report(new)
    if exists and new == current:
        print(f"memory-index: {len(approved)} approved — block up to date{load}")
    else:
        write_atomic(target, new)
        cap = f" (capped at {CAP})" if len(approved) > CAP else ""
        print(f"memory-index: {len(approved)} approved -> {target}{cap}{load}")
    if warning:
        print(warning)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

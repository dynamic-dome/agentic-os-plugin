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
import tempfile

# Load-limit rule (200 lines / 25 000 bytes) has one home: the native audit;
# the short-form rule (150 / 200 chars) one in projection_text (shared with AGENTS.md).
from native_memory_audit import count_lines, load_level
from projection_text import BOM, POINTER, anchor_ok, entry_line, pointer_line, read_raw, with_eol
from projection_text import strip_block as _strip_block

BEGIN = ("<!-- bridge:claude-native:begin — generiert von agentic-os "
         "memory_index_projection, NICHT von Hand editieren -->")
END = "<!-- bridge:claude-native:end -->"
BEGIN_PREFIX = "<!-- bridge:claude-native:begin"
HEADING = "## Bridge: Learnings + Feedback (learnings.json, kuratiert)"
CAP = 20
POINTER_CAP = 10   # rule pointers (kind "principle") shown first, D-021 (4)
CODEX_CAP = 3      # E14: codex tips (importance 2, fan-out) took half of the block
# MEMORY.md is loaded into every session: CAP bounds the count, projection_text the
# length. The native memory-index guard sees Write/Edit tool calls only, never this
# script — the line limit has to be enforced here.


def native_memory_md(project_root, home=None):
    """Same hashing rule as membrain/scripts/native_paths.py: every char outside
    [A-Za-z0-9] of the absolute project path becomes '-'."""
    home = home or os.path.expanduser("~")
    hashed = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(project_root))
    return os.path.join(home, ".claude", "projects", hashed, "memory", "MEMORY.md")


def load_entries(mem_dir):
    """-> (pointers, approved) or None when learnings.json is unreadable."""
    path = os.path.join(mem_dir, "learnings", "learnings.json")
    if not os.path.isfile(path):
        return [], []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"memory-index: learnings.json unreadable: {exc}", file=sys.stderr)
        return None
    entries = [e for e in (data if isinstance(data, list) else data.get("learnings", []))
               if isinstance(e, dict) and not e.get("superseded_by")]
    pointers = [e for e in entries if e.get("kind") == "principle"]
    pointers.sort(key=lambda e: (_imp(e), str(e.get("date", ""))), reverse=True)
    # fold only members of pointers that are actually SHOWN (Codex round 2)
    folded = {m for p in pointers[:POINTER_CAP] for m in (p.get("derived_from") or []) if isinstance(m, str)}
    approved = [e for e in entries if e.get("kind") != "principle"
                and e.get("bridge_status") == "approved" and str(e.get("id")) not in folded]
    # E14: importance before date (the 20 newest used to win, half of them codex tips)
    approved.sort(key=lambda e: (_imp(e), e.get("kind", "learning") == "feedback",
                                 str(e.get("date", ""))), reverse=True)
    out, codex = [], 0
    for e in approved:
        if e.get("source_agent") == "codex":
            codex += 1
            if codex > CODEX_CAP:
                continue
        out.append(e)
    return pointers, out


def _imp(e):
    v = e.get("importance", 0)
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def load_approved(mem_dir):
    """Back-compat for callers that only need the listed entries."""
    loaded = load_entries(mem_dir)
    return None if loaded is None else loaded[1]


def render_block(approved, pointers=()):
    lines = [BEGIN, HEADING, POINTER]
    for p in list(pointers)[:POINTER_CAP]:
        members = [m for m in (p.get("derived_from") or []) if isinstance(m, str)]
        lines.append(pointer_line(p, len(members), anchor_ok(p.get("anchor"))))
    for e in approved[:CAP]:
        lines.append(entry_line(e))
    overflow = len(approved) - CAP
    if overflow > 0:
        lines.append(f"({overflow} weitere approved: .agent-memory/learnings/learnings.json)")
    lines.append(END)
    return "\n".join(lines) + "\n"


def strip_block(text):
    """Remove the managed block (shared rule in projection_text). A block at the
    START also takes the one separator line the projection put after it."""
    if text.split("\n", 1)[0].startswith(BEGIN_PREFIX):
        lines = text.split("\n")
        end = next((i for i, line in enumerate(lines) if line.strip() == END), None)
        if end is not None:
            rest = lines[end + 1:]
            if rest and rest[0].strip() == "":
                rest = rest[1:]
            return "\n".join(rest)
    return _strip_block(text, BEGIN_PREFIX, END)


def assemble_top(bom, base, block, eol):
    """BOM + block + one separator + the untouched rest (E14: Claude Code loads the
    first 200 lines / 25 000 bytes, so the block must never be the part cut off)."""
    out = with_eol(block, eol)
    if base:
        out += eol + base
    return (BOM if bom else "") + out


def load_report(text):
    """Load share of the MEMORY.md as written -> (suffix for the status line, warning|None).
    Since 5.4.0 the block sits at the START; a truncated file now loses hand lines."""
    data = text.encode("utf-8")
    pct, limit, level = load_level(len(data), count_lines(data))
    warning = None
    if level == "truncated":
        warning = (f"memory-index: WARNUNG MEMORY.md liegt bei {pct} % der Ladegrenze "
                   f"(200 Zeilen / 25 000 Bytes, Engpass {limit}) — die letzten Index-Zeilen "
                   f"werden nicht geladen; handgeschriebene Index-Zeilen kuerzen")
    return f" · Ladegrenze {pct} % {limit} ({level})", warning


def write_atomic(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # mkstemp, not path + ".tmp" (5.3.0): two runs at once shared one temp
    # file name and could publish each other's half-written text.
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


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

    loaded = load_entries(a.mem_dir)
    if loaded is None:
        return 1
    pointers, approved = loaded
    exists = os.path.isfile(target)
    bom, current, eol = read_raw(target) if exists else (False, "", "\n")
    on_disk = (BOM if bom else "") + current

    if not approved and not pointers:
        if exists and BEGIN_PREFIX in current:
            stripped = (BOM if bom else "") + strip_block(current)
            write_atomic(target, stripped)
            load, warning = load_report(stripped)
            print(f"memory-index: 0 approved — block removed from {target}{load}")
        else:
            load, warning = load_report(on_disk) if exists else ("", None)
            print(f"memory-index: no approved learnings, nothing to do{load}")
        if warning:
            print(warning)
        return 0

    new = assemble_top(bom, strip_block(current) if exists else "", render_block(approved, pointers), eol)
    load, warning = load_report(new)
    if exists and new == on_disk:
        print(f"memory-index: {len(pointers)} pointers, {len(approved)} approved — block up to date{load}")
    else:
        write_atomic(target, new)
        cap = f" (capped at {CAP})" if len(approved) > CAP else ""
        print(f"memory-index: {len(pointers)} pointers, {len(approved)} approved -> {target}{cap}{load}")
    if warning:
        print(warning)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

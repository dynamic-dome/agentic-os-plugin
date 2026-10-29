#!/usr/bin/env python3
"""Claude->Codex bridge projection (T-14 + T-25, membrain membridge.md §3.3).

Renders a managed block into the project's AGENTS.md so standalone Codex
sessions get it as INJECTED context (not a pointer they must read via a tool).
Two sections: open/blocked tasks (T-25 — Codex TUI ignores SessionStart-hook
additionalContext, but reads AGENTS.md) and bridge_status=approved learnings
(T-14). learnings.json / open-tasks.json stay canonical; the block is a
projection — regenerate any time, never edit by hand. Read-modify-write happens
inside ONE process with an atomic replace, so there is no agent-level race
window (T-19 class).

Usage:
  python bridge_projection.py <mem-dir> --agents-md <path>

Exit codes: 0 ok (also no-op) · 1 learnings.json unreadable · 2 usage error.
Tasks are optional context: a missing/corrupt open-tasks.json is fail-soft
(logged to stderr, tasks skipped), never exit 1.
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

# Same short-form rule as the MEMORY.md block (150 chars, line <= 200).
from projection_text import (gated_pointer, BOM, LINE_LIMIT, POINTER, anchor_ok, assemble, bounded_line, entry_line,
                             pointer_line, pointer_overflow, read_raw, select_pointers, shorten)
from projection_text import strip_block as _strip_block

END = "<!-- bridge:end -->"
BEGIN_PREFIX = "<!-- bridge:begin"


def begin_line(today=None):
    """DCO-8974: render the generation date into the marker so a stale block
    (learnings.json/open-tasks.json changed but the projection was never
    re-run) is visible in the AGENTS.md diff instead of silently drifting.
    Date-only (not a full timestamp) so two runs on the same day stay
    idempotent (tests 3/15)."""
    day = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (f"{BEGIN_PREFIX} — generiert von agentic-os bridge_projection am "
            f"{day}, NICHT von Hand editieren -->")
CAP = 6
POINTER_CAP = 10   # rule pointers have their own cap (P3-A): they used to share CAP with the learnings
TASK_CAP = 5


def _imp(e):
    v = e.get("importance", 0)
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def load_entries(mem_dir):
    """-> (pointers, approved) or None when the store is invalid. `pointers` are the approved, live
    rule pointers (kind "principle", 5.4.0) in render order (relevance, P3-A), complete - the block
    shows the first POINTER_CAP. `approved` are the other approved learnings, newest first, without
    the members of SHOWN pointers: a member of a pointer the cap leaves out stays visible itself
    (P3-A - before, every pointer folded its members while only 6 lines were shown)."""
    path = os.path.join(mem_dir, "learnings", "learnings.json")
    if not os.path.isfile(path):
        return [], []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:  # a BOM is not corruption
            data = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"bridge: learnings.json unreadable: {exc}", file=sys.stderr)
        return None
    entries = [e for e in (data if isinstance(data, list) else data.get("learnings", [])) if isinstance(e, dict)]
    by_id = {str(e.get("id")): e for e in entries}

    def projectable(e):
        return (e.get("bridge_status") == "approved" and not e.get("superseded_by")
                and e.get("source_agent", "claude") != "codex")   # loop guard (hub spec E3)

    # a pointer over an unreleased rule-class learning is not projected (gate, DCO #10057)
    pointers = [e for e in entries if e.get("kind") == "principle" and projectable(e)
                and not gated_pointer(e, by_id)]
    shown_ptrs, folded, _hidden = select_pointers(pointers, by_id, POINTER_CAP)
    pointers = shown_ptrs + [p for p in pointers if p not in shown_ptrs]
    approved = [e for e in entries if e.get("kind") != "principle" and projectable(e)
                and str(e.get("id")) not in folded]
    approved.sort(key=lambda e: (str(e.get("date", "")), _imp(e)), reverse=True)
    return pointers, approved


def load_approved(mem_dir):
    """Approved, non-superseded learnings (no pointers), newest first. None = store invalid."""
    loaded = load_entries(mem_dir)
    return None if loaded is None else loaded[1]


def shown(mem_dir):
    """The rows the block renders, in block order (pointers first), or None when the store is
    invalid. One selection for the projection and for measurements (erreichbarkeit_eval K4)."""
    loaded = load_entries(mem_dir)
    if loaded is None:
        return None
    pointers, approved = loaded
    return list(pointers[:POINTER_CAP]) + list(approved[:CAP])


def load_open_tasks(mem_dir):
    """Open/blocked tasks, order preserved. Fail-soft: tasks are optional
    context, an unreadable/missing file must NEVER kill the projection (unlike
    learnings, which are canonical) -> return [] on any problem."""
    path = os.path.join(mem_dir, "context", "open-tasks.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:  # a BOM is not corruption
            data = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"bridge: open-tasks.json unreadable, tasks skipped: {exc}",
              file=sys.stderr)
        return []
    tasks = data if isinstance(data, list) else data.get("tasks", [])
    return [t for t in tasks if isinstance(t, dict)
            and t.get("status") in ("open", "blocked")]


def project_label(mem_dir):
    """Project label for the task heading: config.json project_id, falling back
    to the project root's directory name. Fail-soft — a missing/corrupt config
    must never kill the projection (T-014: the old hard-coded '(membrain)' put
    the wrong label into every other project's AGENTS.md)."""
    path = os.path.join(mem_dir, "config.json")
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            pid = json.load(f).get("project_id", "")
        if isinstance(pid, str) and pid.strip():
            return pid.strip()
    except (OSError, ValueError):
        pass
    return os.path.basename(os.path.dirname(os.path.abspath(mem_dir)))


def render_block(approved, tasks, label, pointers=()):
    lines = [begin_line()]
    if tasks:
        heading = "## Bridge: Offene Tasks ({})"
        lines.append(heading.format(shorten(label, LINE_LIMIT - len(heading.format("")))))
        for t in tasks[:TASK_CAP]:
            lines.append(bounded_line(f"- [{t.get('id', '?')}] ", t.get("title", ""), limit=200))
        extra = len(tasks) - TASK_CAP
        if extra > 0:
            lines.append(f"({extra} weitere: context/open-tasks.json)")
    pointers = list(pointers)
    if approved or pointers:
        lines.append("## Bridge: Learnings von Claude (kuratiert)")
        lines.append(POINTER)
        for p in pointers[:POINTER_CAP]:
            members = [m for m in (p.get("derived_from") or []) if isinstance(m, str)]
            lines.append(pointer_line(p, len(members), anchor_ok(p.get("anchor"))))
        if len(pointers) > POINTER_CAP:
            lines.append(pointer_overflow(len(pointers) - POINTER_CAP))
        for e in approved[:CAP]:
            lines.append(entry_line(e))
        overflow = len(approved) - CAP
        if overflow > 0:
            lines.append(f"({overflow} ältere: learnings.json)")
    lines.append(END)
    return "\n".join(lines) + "\n"


def strip_block(text):
    """Remove an existing managed block; returns text unchanged if absent."""
    return _strip_block(text, BEGIN_PREFIX, END)


def write_atomic(path, text):
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
    parser = argparse.ArgumentParser(prog="bridge_projection.py")
    parser.add_argument("mem_dir")
    parser.add_argument("--agents-md", required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2

    loaded = load_entries(args.mem_dir)
    if loaded is None:
        return 1
    pointers, approved = loaded
    tasks = load_open_tasks(args.mem_dir)

    exists = os.path.isfile(args.agents_md)
    bom, current, eol = read_raw(args.agents_md) if exists else (False, "", "\n")
    on_disk = (BOM if bom else "") + current

    if not approved and not pointers and not tasks:
        if exists and BEGIN_PREFIX in current:
            write_atomic(args.agents_md, (BOM if bom else "") + strip_block(current))
            print(f"bridge: 0 approved/0 tasks — block removed from "
                  f"{args.agents_md}")
        else:
            print("bridge: no approved learnings or open tasks, nothing to do")
        return 0

    block = render_block(approved, tasks, project_label(args.mem_dir), pointers)
    new = assemble(bom, strip_block(current) if exists else "", block, eol)
    if exists and new == on_disk:
        print(f"bridge: {len(approved)} approved, {len(tasks)} tasks — "
              f"block up to date")
        return 0
    write_atomic(args.agents_md, new)
    caps = []
    if len(pointers) > POINTER_CAP:
        caps.append(f"pointers capped at {POINTER_CAP}")
    if len(approved) > CAP:
        caps.append(f"learnings capped at {CAP}")
    if len(tasks) > TASK_CAP:
        caps.append(f"tasks capped at {TASK_CAP}")
    suffix = f" ({', '.join(caps)})" if caps else ""
    print(f"bridge: {len(approved)} approved, {len(tasks)} tasks -> "
          f"{args.agents_md}{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

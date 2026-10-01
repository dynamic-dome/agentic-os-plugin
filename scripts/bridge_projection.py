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
from datetime import datetime, timezone

# Same short-form rule as the MEMORY.md block (150 chars, line <= 200).
from projection_text import (BOM, LINE_LIMIT, POINTER, assemble, bounded_line, entry_line,
                             read_raw, shorten)
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
TASK_CAP = 5


def load_approved(mem_dir):
    """Approved, non-superseded learnings, newest first. None = store invalid."""
    path = os.path.join(mem_dir, "learnings", "learnings.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"bridge: learnings.json unreadable: {exc}", file=sys.stderr)
        return None
    entries = data if isinstance(data, list) else data.get("learnings", [])
    approved = [e for e in entries if isinstance(e, dict)
                and e.get("bridge_status") == "approved"
                and not e.get("superseded_by")
                and e.get("source_agent", "claude") != "codex"]   # loop guard (hub spec E3)
    approved.sort(key=lambda e: (str(e.get("date", "")),
                                 int(e.get("importance", 0))), reverse=True)
    return approved


def load_open_tasks(mem_dir):
    """Open/blocked tasks, order preserved. Fail-soft: tasks are optional
    context, an unreadable/missing file must NEVER kill the projection (unlike
    learnings, which are canonical) -> return [] on any problem."""
    path = os.path.join(mem_dir, "context", "open-tasks.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
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
        with open(path, "r", encoding="utf-8") as f:
            pid = json.load(f).get("project_id", "")
        if isinstance(pid, str) and pid.strip():
            return pid.strip()
    except (OSError, ValueError):
        pass
    return os.path.basename(os.path.dirname(os.path.abspath(mem_dir)))


def render_block(approved, tasks, label):
    lines = [begin_line()]
    if tasks:
        heading = "## Bridge: Offene Tasks ({})"
        lines.append(heading.format(shorten(label, LINE_LIMIT - len(heading.format("")))))
        for t in tasks[:TASK_CAP]:
            lines.append(bounded_line(f"- [{t.get('id', '?')}] ", t.get("title", ""), limit=200))
        extra = len(tasks) - TASK_CAP
        if extra > 0:
            lines.append(f"({extra} weitere: context/open-tasks.json)")
    if approved:
        lines.append("## Bridge: Learnings von Claude (kuratiert)")
        lines.append(POINTER)
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
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def main(argv):
    parser = argparse.ArgumentParser(prog="bridge_projection.py")
    parser.add_argument("mem_dir")
    parser.add_argument("--agents-md", required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2

    approved = load_approved(args.mem_dir)
    if approved is None:
        return 1
    tasks = load_open_tasks(args.mem_dir)

    exists = os.path.isfile(args.agents_md)
    bom, current, eol = read_raw(args.agents_md) if exists else (False, "", "\n")
    on_disk = (BOM if bom else "") + current

    if not approved and not tasks:
        if exists and BEGIN_PREFIX in current:
            write_atomic(args.agents_md, (BOM if bom else "") + strip_block(current))
            print(f"bridge: 0 approved/0 tasks — block removed from "
                  f"{args.agents_md}")
        else:
            print("bridge: no approved learnings or open tasks, nothing to do")
        return 0

    block = render_block(approved, tasks, project_label(args.mem_dir))
    new = assemble(bom, strip_block(current) if exists else "", block, eol)
    if exists and new == on_disk:
        print(f"bridge: {len(approved)} approved, {len(tasks)} tasks — "
              f"block up to date")
        return 0
    write_atomic(args.agents_md, new)
    caps = []
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

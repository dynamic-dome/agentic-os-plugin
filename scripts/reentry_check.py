#!/usr/bin/env python3
"""Stand-Abgleich: the last session summary against git (T-088, REGELKREISE L1).

Read-only. Counts the commits since the date line of session-summary.md and lists open tasks
whose id a commit subject names - the briefing may be older than the repo.

    python reentry_check.py <memory_dir> <project_root> [--json]

Without --json only the line is printed (empty when nothing differs). Exit is always 0: the
SessionStart hook must never fail because of this check. Any error yields an empty line.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

# *Date: 2026-11-12 14:32* (store), *Datum: ...* (central handoff), plus the variants found in real
# stores: **Date:** 2026-..., a date without time, a trailing note like "(abends)".
DATE_RE = re.compile(r"^\*{1,2}(?:Date|Datum):?\*{0,2}:?\s*(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?")
TASK_RE = re.compile(r"\bT-\d+\b")
# A task is open unless it carries one of the statuses that end it - the same set the briefing uses
# (preprocess_state.CLOSED_TASK_STATUSES; a test keeps both equal). Kept here so the hook check stays
# standalone; in_progress and unknown statuses count as open (DCO #10461).
CLOSED_TASK_STATUSES = frozenset({"done", "closed", "cancelled", "deferred"})


def _summary_date(mem: str) -> str | None:
    with open(os.path.join(mem, "session-summary.md"), encoding="utf-8-sig") as fh:
        for _, line in zip(range(10), fh):
            m = DATE_RE.match(line.rstrip("\r\n"))
            if m:
                return f"{m.group(1)} {m.group(2)}" if m.group(2) else m.group(1)
    return None


def _since(date: str) -> str:
    """A date without time counts from the end of that day: no false hits from the same day."""
    return date if " " in date else f"{date} 23:59"


def _open_task_ids(mem: str) -> set[str]:
    path = os.path.join(mem, "context", "open-tasks.json")
    if not os.path.isfile(path):
        return set()
    with open(path, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    tasks = data.get("tasks", []) if isinstance(data, dict) else data
    return {str(t.get("id")) for t in tasks
            if isinstance(t, dict) and t.get("status") not in CLOSED_TASK_STATUSES and t.get("id")}


def _subjects_since(root: str, since: str) -> list[str]:
    git = shutil.which("git")
    if not git or not os.path.exists(os.path.join(root, ".git")):
        return []
    # Commits that only touch .agent-memory are wrap-up's own memory commit, not new work.
    cp = subprocess.run([git, "-C", root, "log", f"--since={_since(since)}", "--format=%s",
                         "--", ".", ":(exclude).agent-memory"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5)
    if cp.returncode != 0:   # e.g. a repo without commits
        return []
    return [s for s in cp.stdout.splitlines() if s.strip()]


def _line(n: int, date: str, ids: list[str]) -> str:
    parts = []
    if n:
        parts.append(f"{n} Commits seit der letzten Zusammenfassung ({date})")
    if ids:
        parts.append(f"offen, aber in Commits genannt: {', '.join(ids)}")
    return "Stand-Abgleich: " + "; ".join(parts) if parts else ""


def check(mem: str, project_root: str) -> dict:
    result = {"summary_date": None, "commits_since": 0, "open_tasks_in_commits": [], "line": ""}
    try:
        date = _summary_date(mem)
        result["summary_date"] = date
        if date is None:
            return result
        subjects = _subjects_since(project_root, date)
        named = {tid for s in subjects for tid in TASK_RE.findall(s)}
        ids = sorted(named & _open_task_ids(mem), key=lambda t: int(t.split("-")[1]))
        result["commits_since"] = len(subjects)
        result["open_tasks_in_commits"] = ids
        result["line"] = _line(len(subjects), date, ids)
    except Exception:
        result["line"] = ""
    return result


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        args = [a for a in argv if a != "--json"]
        if len(args) != 2:
            return 0
        result = check(args[0], args[1])
        if "--json" in argv:
            print(json.dumps(result, ensure_ascii=False))
        elif result["line"]:
            print(result["line"])
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

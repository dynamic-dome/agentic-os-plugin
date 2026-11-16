#!/usr/bin/env python3
"""Tests for scripts/reentry_check.py (T-088, REGELKREISE L1): briefing against git.

Every case runs in throwaway dirs under tempfile.mkdtemp() (a fake project with a git repo and
a .agent-memory). Real stores are never touched.

Exit codes: 0 = all pass, 1 = failures found.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "reentry_check.py")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolated_home import process_env  # noqa: E402

ENV, ISO_HOME = process_env()
GIT = shutil.which("git")

TESTS = PASSED = ERRORS = 0


def check(cond, msg):
    global TESTS, PASSED, ERRORS
    TESTS += 1
    if cond:
        PASSED += 1
        print(f"  PASS: {msg}")
    else:
        ERRORS += 1
        print(f"  FAIL: {msg}")


def put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def git(repo, *args):
    subprocess.run([GIT, "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True, text=True, encoding="utf-8", env=ENV)


CREATED: list[str] = []  # every throwaway project, removed when the run ends (DCO #10461)


def project(summary_head="*Date: 2026-01-01 10:00*", commits=(), tasks=None, init_git=True):
    proj = tempfile.mkdtemp(prefix="reentry-proj-")
    CREATED.append(proj)
    if init_git:
        git(proj, "init", "-q")
        for i, msg in enumerate(commits):
            put(os.path.join(proj, f"f{i}.txt"), msg)
            git(proj, "add", f"f{i}.txt")
            git(proj, "commit", "-q", "-m", msg)
    mem = os.path.join(proj, ".agent-memory")
    put(os.path.join(mem, "session-summary.md"), f"# Last Session\n\n{summary_head}\n*Agent: Claude Code*\n")
    put(os.path.join(mem, "context", "open-tasks.json"), json.dumps({"tasks": tasks or []}))
    return proj, mem


def run_cli(mem, proj, *extra):
    return subprocess.run([sys.executable, SCRIPT, mem, proj, *extra], capture_output=True,
                          text=True, encoding="utf-8", env=ENV)


def main():
    import reentry_check as rc

    print("Case 1: two commits after the summary date")
    proj, mem = project(commits=("feat: a", "fix: b"))
    r = rc.check(mem, proj)
    check(r["summary_date"] == "2026-01-01 10:00", "summary_date parsed")
    check(r["commits_since"] == 2, f"commits_since == 2 (got {r['commits_since']})")
    check(r["line"].startswith("Stand-Abgleich: 2 Commits seit der letzten Zusammenfassung (2026-01-01 10:00)"),
          f"line text frozen (got {r['line']!r})")
    check("offen, aber in Commits genannt" not in r["line"], "empty task part is left out")

    print("Case 2: open task named in a commit message")
    tasks = [{"id": "T-005", "status": "open"}, {"id": "T-006", "status": "blocked"},
             {"id": "T-007", "status": "done"}, {"id": "T-008", "status": "in_progress"},
             {"id": "T-009", "status": "closed"}, {"id": "T-010", "status": "cancelled"},
             {"id": "T-011", "status": "deferred"},
             # Codex V1-06 (5.14.1): an unknown status and a missing status count as open as well
             {"id": "T-012", "status": "custom_pending"}, {"id": "T-013"}]
    proj, mem = project(commits=("fix: T-005 erledigt", "chore: T-007 nachgezogen", "feat: T-006",
                                 "wip: T-008", "fix: T-009 T-010 T-011", "wip: T-012 T-013"), tasks=tasks)
    r = rc.check(mem, proj)
    check(r["open_tasks_in_commits"] == ["T-005", "T-006", "T-008", "T-012", "T-013"],
          f"open, blocked, in_progress, unknown and missing status listed, closed ones not "
          f"(got {r['open_tasks_in_commits']})")
    check(r["line"].endswith("; offen, aber in Commits genannt: T-005, T-006, T-008, T-012, T-013"),
          f"task part in line (got {r['line']!r})")
    import preprocess_state
    check(rc.CLOSED_TASK_STATUSES == preprocess_state.CLOSED_TASK_STATUSES,
          "closed statuses are the same as the briefing's (preprocess_state)")

    print("Case 3: closed task is not listed")
    proj, mem = project(commits=("fix: T-005 erledigt",), tasks=[{"id": "T-005", "status": "done"}])
    r = rc.check(mem, proj)
    check(r["open_tasks_in_commits"] == [], "closed T-005 not listed")
    check(r["commits_since"] == 1, "commit still counted")

    print("Case 4: *Datum:* (central handoff) is recognized too")
    proj, mem = project(summary_head="*Datum: 2026-01-01 10:00*", commits=("feat: a",))
    r = rc.check(mem, proj)
    check(r["summary_date"] == "2026-01-01 10:00" and r["commits_since"] == 1, "Datum line parsed")

    print("Case 5: no commits after the date")
    proj, mem = project(summary_head="*Date: 2099-01-01 10:00*", commits=("feat: a",))
    r = rc.check(mem, proj)
    check(r["commits_since"] == 0 and r["line"] == "", f"line empty (got {r['line']!r})")

    print("Case 6: no ground - folder without .git, summary without date, repo without commits")
    proj, mem = project(commits=(), init_git=False)
    check(rc.check(mem, proj)["line"] == "", "folder without .git -> empty line")
    cp = run_cli(mem, proj)
    check(cp.returncode == 0 and cp.stdout.strip() == "", f"CLI exit 0, no output (rc={cp.returncode})")
    proj, mem = project(summary_head="*Agent: x*", commits=("feat: a",))
    check(rc.check(mem, proj)["line"] == "", "summary without date line -> empty line")
    proj, mem = project(commits=())
    check(rc.check(mem, proj)["line"] == "", "repo without commits -> empty line")
    cp = run_cli(os.path.join(proj, "does-not-exist"), proj)
    check(cp.returncode == 0 and cp.stdout.strip() == "", "missing memory dir -> CLI exit 0, no output")

    print("Case 6b: other date formats in real stores (review T-088)")
    proj, mem = project(summary_head="**Date:** 2026-01-01 10:00", commits=("feat: a",))
    r = rc.check(mem, proj)
    check(r["summary_date"] == "2026-01-01 10:00" and r["commits_since"] == 1, f"**Date:** parsed (got {r['summary_date']!r})")
    proj, mem = project(summary_head="*Date: 2026-01-01*", commits=("feat: a",))
    r = rc.check(mem, proj)
    check(r["summary_date"] == "2026-01-01" and r["commits_since"] == 1, f"date without time parsed (got {r['summary_date']!r})")
    check(r["line"].startswith("Stand-Abgleich: 1 Commits seit der letzten Zusammenfassung (2026-01-01)"), "date-only line text")
    proj, mem = project(summary_head="*Datum: 2026-01-01 (abends)*", commits=("feat: a",))
    check(rc.check(mem, proj)["summary_date"] == "2026-01-01", "Datum with a trailing note parsed")
    check(rc._since("2026-01-01") == "2026-01-01 23:59", "date only counts from the end of that day")
    # 5.14.0 Codex V1-03: the day boundary must reach git, not only the helper - a commit late on the summary day
    # (23:30, before the 23:59 boundary) does not count, a commit made now does
    proj, mem = project(summary_head="*Date: 2026-01-01*", commits=())
    put(os.path.join(proj, "late.txt"), "late")
    git(proj, "add", "late.txt")
    dated = dict(ENV, GIT_AUTHOR_DATE="2026-01-01T23:30:00", GIT_COMMITTER_DATE="2026-01-01T23:30:00")
    subprocess.run([GIT, "-C", proj, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "late"],
                   check=True, capture_output=True, text=True, encoding="utf-8", env=dated)
    put(os.path.join(proj, "now.txt"), "now")
    git(proj, "add", "now.txt")
    git(proj, "commit", "-q", "-m", "now")
    r = rc.check(mem, proj)
    check(r["commits_since"] == 1, f"same-day commit before the day boundary not counted (got {r['commits_since']})")
    # 5.14.0 Codex V2-01: git fills a date without time with the current clock time, so the commit check above
    # cannot catch a dropped boundary between 23:30 and 23:59 - check the argument that reaches git directly
    seen, real_run = [], rc.subprocess.run
    def spy(cmd, *a, **kw):
        seen.append(list(cmd))
        return real_run(cmd, *a, **kw)
    rc.subprocess.run = spy
    try:
        rc.check(mem, proj)
    finally:
        rc.subprocess.run = real_run
    check(any("--since=2026-01-01 23:59" in c for c in seen), f"git gets the day boundary (got {seen})")

    print("Case 6c: commits that only touch .agent-memory do not count (wrap-up's memory commit)")
    proj, mem = project(commits=("feat: code",))
    put(os.path.join(mem, "learnings", "learnings.json"), "[]")
    git(proj, "add", "-f", ".agent-memory")
    git(proj, "commit", "-q", "-m", "chore(memory): session")
    r = rc.check(mem, proj)
    check(r["commits_since"] == 1, f"memory-only commit excluded (got {r['commits_since']})")

    print("Case 7: CLI prints the line, --json the dict")
    proj, mem = project(commits=("feat: a",))
    cp = run_cli(mem, proj)
    check(cp.returncode == 0 and cp.stdout.strip().startswith("Stand-Abgleich: 1 Commits"), f"CLI line (got {cp.stdout!r})")
    cp = run_cli(mem, proj, "--json")
    try:
        data = json.loads(cp.stdout)
    except ValueError:
        data = {}
    check(data.get("commits_since") == 1 and set(data) == {"summary_date", "commits_since", "open_tasks_in_commits", "line"},
          "CLI --json gives the four keys")

    print(f"\n{PASSED}/{TESTS} passed, {ERRORS} failed")
    return 1 if ERRORS else 0


def _remove(path):
    """git writes its objects read-only; on Windows rmtree fails on them unless the bit is cleared."""
    for root, _dirs, files in os.walk(path):
        for name in files:
            os.chmod(os.path.join(root, name), stat.S_IWRITE)
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    try:
        rc_main = main()
    finally:
        for path in CREATED:
            _remove(path)
    sys.exit(rc_main)

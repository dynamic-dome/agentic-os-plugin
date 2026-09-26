"""Iteration harvest without a model (wrap-up Step 1.5, headless path of the V3 spec §4.2).

Two mechanical sources, no judgment:
  - commits since `since` (git log), type from the Conventional-Commit prefix
  - per dirty session: touched files under the project that no commit covers -> one
    `config` iteration marked `recovered_from: <sid>`
The result uses the iteration-plan schema of apply_wrapup.py (wrapup-schemas.md §Write plan).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

PREFIX_RE = re.compile(r"^(feat|fix|refactor|docs|test|chore)(\([^)]*\))?!?:\s*(.+)$", re.I)
TYPE_BY_PREFIX = {"feat": "feature", "fix": "fix", "refactor": "refactor", "docs": "docs",
                  "test": "test", "chore": "chore"}
MAX_FILES = 20
MEM_DIRNAME = ".agent-memory"
# Above this many commits the harvest groups by type: one iteration per type instead of one per
# commit. Real-data dry run 2026-09-24: DCO had 145 un-wrapped commits - 145 mechanical log entries
# would drown the iteration log and feed spurious clusters to extract_patterns.
GROUP_ABOVE = 8
MAX_SUBJECTS = 15
MAX_TITLE = 160      # real DCO commit subjects run to several hundred characters
MAX_SUMMARY = 700


def _cut(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _rel(project_root: str, path: str) -> str | None:
    """Project-relative forward-slash path, or None when outside the project or inside .agent-memory."""
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(project_root))
    except ValueError:  # different drive on Windows
        return None
    rel = rel.replace("\\", "/")
    if rel.startswith("../") or rel == ".." or rel.split("/", 1)[0] == MEM_DIRNAME:
        return None
    return rel


def _commits(project_root: str, since: str) -> list[dict]:
    git = shutil.which("git")
    if not git or not os.path.isdir(os.path.join(project_root, ".git")):
        return []
    try:
        out = subprocess.run([git, "-C", project_root, "log", f"--since={since}", "--no-merges",
                              "--pretty=format:%x1e%h%x1f%s", "--name-only"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    commits = []
    for chunk in out.stdout.split("\x1e"):
        if "\x1f" not in chunk:
            continue
        head, _, rest = chunk.partition("\n")
        sha, _, subject = head.partition("\x1f")
        files = [f.strip().replace("\\", "/") for f in rest.splitlines() if f.strip()]
        commits.append({"sha": sha.strip(), "subject": subject.strip(), "files": files})
    return list(reversed(commits))  # oldest first, like the log reads


def _dirty_sessions(mem_dir: str) -> list[dict]:
    work = os.path.join(mem_dir, "working")
    out = []
    if not os.path.isdir(work):
        return out
    for name in sorted(os.listdir(work)):
        if not (name.startswith("dirty-") and name.endswith(".json")):
            continue
        try:
            with open(os.path.join(work, name), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue  # unreadable evidence is apply_consolidation's call (it refuses), not ours
        if isinstance(data, dict) and data.get("dirty"):
            out.append(data)
    return out


def harvest(project_root: str, mem_dir: str, since: str) -> list[dict]:
    """Iterations for the headless plan: commits since `since`, then uncommitted dirty work."""
    iterations, committed, parsed = [], set(), []
    for c in _commits(project_root, since):
        m = PREFIX_RE.match(c["subject"])
        itype = TYPE_BY_PREFIX[m.group(1).lower()] if m else "feature"
        title = _cut((m.group(3) if m else c["subject"]).strip(), MAX_TITLE)
        if not title:
            continue
        committed.update(os.path.normcase(f) for f in c["files"])
        parsed.append((itype, title, c))
    if len(parsed) <= GROUP_ABOVE:
        for itype, title, c in parsed:
            iterations.append({"type": itype, "title": title, "tags": ["headless", itype],
                               "files_changed": c["files"][:MAX_FILES], "commits": c["sha"],
                               "summary": "Harvested headless from git log (no judge)."})
    else:
        groups = {}
        for itype, title, c in parsed:
            groups.setdefault(itype, []).append((title, c))
        for itype, items in groups.items():
            files = []
            for _, c in items:
                files += [f for f in c["files"] if f not in files]
            subjects = "; ".join(_cut(t, 100) for t, _ in items[-MAX_SUBJECTS:])
            iterations.append({"type": itype, "title": f"{len(items)} {itype}-Commits (headless gesammelt)",
                               "tags": ["headless", itype, "grouped"], "files_changed": files[:MAX_FILES],
                               "commits": f"{items[0][1]['sha']}..{items[-1][1]['sha']}",
                               "summary": _cut(f"Neueste Betreffzeilen: {subjects}", MAX_SUMMARY)})
    for sess in _dirty_sessions(mem_dir):
        files = []
        for path in sess.get("touched_files") or []:
            rel = _rel(project_root, str(path))
            if rel and os.path.normcase(rel) not in committed and rel not in files:
                files.append(rel)
        if files:
            sid = str(sess.get("session_id") or "unknown")
            iterations.append({"type": "config", "title": f"Uncommitted changes ({len(files)} files)",
                               "tags": ["headless", "uncommitted"], "files_changed": files[:MAX_FILES],
                               "recovered_from": sid,
                               "summary": f"Harvested headless from working/dirty-{sid}.json (no judge)."})
    return iterations

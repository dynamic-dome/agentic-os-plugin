#!/usr/bin/env python3
"""Stage-0 deterministic preprocessing for memory skills (agentic-os v4.7.0).

Gathers all mechanical session facts WITHOUT any model call and emits one
normalized JSON state object on stdout (spec memospartoken.md section 7.1).
Consumers: wrap-up Step 0, session-bootstrap fast path.

Contract (must never break):
- Fail-soft: any error -> empty fields / stderr warning, ALWAYS exit 0.
- Only mechanical facts (paths, counts, hashes) — no LLM, no content analysis.
- Hash writes are atomic (tmp + os.replace).
- stdlib only; subprocess guarded by shutil.which() (Windows rule).

Usage:
  python preprocess_state.py [mem-dir] [--session-id SID] [--write-hash]
  -h/--help prints usage; every other invocation prints the JSON state object.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

# Files whose content defines the memory state hash (relative to mem dir).
# Deliberately excludes working/ (scratch) and metrics/ (traces).
STATE_FILES = [
    "session-summary.md",
    "context/open-tasks.json",
    "context/project-context.md",
    "context/decisions.json",
    "learnings/learnings.json",
    "learnings/learnings.md",
    "patterns/patterns.json",
    "iterations/iteration-log.md",
    "iterations/errors.json",
    "identity/user.md",
]

HASH_FILE = os.path.join("working", "state-hash")


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def compute_state_hash(mem):
    h = hashlib.sha256()
    for rel in STATE_FILES:
        p = os.path.join(mem, rel)
        if os.path.isfile(p):
            try:
                with open(p, "rb") as f:
                    h.update(rel.encode("utf-8") + b"\0" + f.read() + b"\0")
            except OSError:
                continue
    return h.hexdigest()


def collect_changed_files(mem, session_id):
    changed = []
    workdir = os.path.join(mem, "working")
    if not os.path.isdir(workdir):
        return changed
    try:
        names = sorted(os.listdir(workdir))
    except OSError:
        return changed
    for name in names:
        if not (name.startswith("dirty-") and name.endswith(".json")):
            continue
        if session_id and name != f"dirty-{session_id}.json":
            continue
        try:
            data = json.loads(read_text(os.path.join(workdir, name)) or "")
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and data.get("dirty"):
            touched = data.get("touched") or data.get("touched_files") or []
            if isinstance(touched, list):
                for t in touched:
                    if isinstance(t, str) and t not in changed:
                        changed.append(t)
    return changed


def git_diff_summary():
    git = shutil.which("git")
    if not git:
        return ""
    try:
        # NOTE: runs in the process cwd (callers invoke from the project root);
        # no cwd= arg on purpose — the mem dir may live outside the git repo.
        proc = subprocess.run(
            [git, "diff", "--stat", "HEAD"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=15,
        )
        if proc.returncode != 0:
            return ""
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        return lines[-1].strip() if lines else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def threshold_events(mem):
    bash = shutil.which("bash")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory-thresholds.sh")
    if not bash or not os.path.isfile(script):
        return []
    try:
        proc = subprocess.run(
            [bash, script, mem],
            capture_output=True, encoding="utf-8", errors="replace", timeout=15,
        )
        if proc.returncode == 10:
            return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        return []
    except (OSError, subprocess.SubprocessError):
        return []


# P0dd only: pattern ids are zero-padded (P004..P012); "P100" in prose is a GPU, not a ref.
PATTERN_REF = re.compile(r"\b(G-pattern-\d{3}|P0\d{2})\b")


def _rows(mem, rel, key):
    text = read_text(os.path.join(mem, rel))
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get(key, [])
    else:
        return None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else None


def dangling_pattern_refs(mem):
    """Learnings citing a pattern id that patterns.json does not hold (L5/L10/L12
    pointed at P006 for months; nothing surfaced it)."""
    patterns = _rows(mem, "patterns/patterns.json", "patterns")
    learnings = _rows(mem, "learnings/learnings.json", "learnings")
    if patterns is None or learnings is None:
        return []
    known = {str(p.get("id", "")) for p in patterns}
    errors = []
    for l in learnings:
        refs = set(PATTERN_REF.findall(str(l.get("text", ""))))
        for d in l.get("derived_from") or []:
            if isinstance(d, str) and PATTERN_REF.fullmatch(d):
                refs.add(d)
        for ref in sorted(refs - known):
            errors.append(f"learnings.json: {l.get('id')} references unknown pattern {ref}")
    return errors


def validation_errors(mem):
    try:
        errors = dangling_pattern_refs(mem)
    except Exception as e:  # fail-soft: a validator must never take the state with it
        errors = [f"pattern-ref check failed: {e}"]
    for root, dirs, files in os.walk(mem):
        # metrics traces are append-only JSONL, not JSON documents
        dirs[:] = [d for d in dirs if d not in ("metrics",)]
        for name in files:
            if not name.endswith(".json"):
                continue
            p = os.path.join(root, name)
            text = read_text(p)
            if text is None:
                continue
            try:
                json.loads(text)
            except ValueError as e:
                rel = os.path.relpath(p, mem).replace(os.sep, "/")
                errors.append(f"{rel}: {e}")
    return errors


def open_tasks(mem):
    text = read_text(os.path.join(mem, "context", "open-tasks.json"))
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    tasks = data if isinstance(data, list) else data.get("tasks", [])
    out = []
    if isinstance(tasks, list):
        for t in tasks:
            if isinstance(t, dict) and t.get("status") != "done":
                out.append({"id": t.get("id", ""), "title": t.get("title", ""),
                            "status": t.get("status", "")})
    return out


def main():
    # Windows: piped stdout defaults to the legacy codepage (cp1252) — a single
    # non-Latin-1 char in state would crash print(); force UTF-8 (fail-soft rule).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser()
    parser.add_argument("mem", nargs="?", default=".agent-memory")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--write-hash", action="store_true")
    try:
        args = parser.parse_args()
    except SystemExit as e:
        if not e.code:  # -h/--help: argparse printed help; keep stdout pure
            return 0
        # fail-soft contract: malformed argv must still yield JSON + exit 0
        print(json.dumps({
            "session_id": "", "changed_files": [], "git_diff_summary": "",
            "threshold_events": [], "validation_errors": [], "open_tasks": [],
            "previous_state_hash": "", "current_state_hash": "",
        }, ensure_ascii=False))
        return 0
    mem = args.mem

    state = {
        "session_id": args.session_id,
        "changed_files": [],
        "git_diff_summary": "",
        "threshold_events": [],
        "validation_errors": [],
        "open_tasks": [],
        "previous_state_hash": "",
        "current_state_hash": "",
    }
    try:
        state["changed_files"] = collect_changed_files(mem, args.session_id)
        state["git_diff_summary"] = git_diff_summary()
        state["threshold_events"] = threshold_events(mem)
        state["validation_errors"] = validation_errors(mem)
        state["open_tasks"] = open_tasks(mem)
        prev = read_text(os.path.join(mem, HASH_FILE))
        state["previous_state_hash"] = (prev or "").strip()
        state["current_state_hash"] = compute_state_hash(mem)

        if args.write_hash:
            workdir = os.path.join(mem, "working")
            os.makedirs(workdir, exist_ok=True)
            tmp = os.path.join(workdir, "state-hash.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(state["current_state_hash"])
            os.replace(tmp, os.path.join(mem, HASH_FILE))
    except Exception as e:  # fail-soft: never block real work
        print(f"preprocess_state: degraded ({e})", file=sys.stderr)

    print(json.dumps(state, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

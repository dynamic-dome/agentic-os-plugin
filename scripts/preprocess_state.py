#!/usr/bin/env python3
"""Stage-0 deterministic preprocessing for memory skills (agentic-os v4.7.0).

Gathers all mechanical session facts WITHOUT any model call and emits one
normalized JSON state object on stdout (spec memospartoken.md section 7.1).
Consumers: wrap-up Step 0, session-bootstrap fast path.

Contract (must never break):
- Fail-soft: any error -> empty fields / stderr warning, ALWAYS exit 0.
- Only mechanical facts (paths, counts, hashes, the keys of the existing user candidates) — no LLM,
  no content analysis. The candidate keys (`user_candidates`) are listed only with --session-id.
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

import identity_store  # P2: where the profile lives (central or project)

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


def _nested_store_guard(mem_dir):
    """True if writing under mem_dir would nest a store inside .agent-memory.

    Mirrors cost-trace.sh's guard (b34d055, DCO-9192): resolve mem_dir to an
    absolute path and check its PARENT for an .agent-memory path segment — a
    relative default (".agent-memory") combined with a cwd that already sits
    inside a store (e.g. a caller cd'd into .agent-memory/working) would
    otherwise create .agent-memory/.agent-memory/working.
    """
    parent = os.path.dirname(os.path.abspath(mem_dir))
    segments = re.split(r"[\\/]+", parent)
    return ".agent-memory" in segments


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:  # a BOM is not corruption
            return f.read()
    except OSError:
        return None


def compute_state_hash(mem, central=None):
    """sha256 over STATE_FILES of the store; with an active central profile (P2) its profile.md is appended, so
    a profile change is a state change. Without one the value is exactly the project-only hash."""
    h = hashlib.sha256()
    for rel in STATE_FILES:
        p = os.path.join(mem, rel)
        if os.path.isfile(p):
            try:
                with open(p, "rb") as f:
                    h.update(rel.encode("utf-8") + b"\0" + f.read() + b"\0")
            except OSError:
                continue
    if central:
        try:
            with open(os.path.join(central, identity_store.PROFILE_MD), "rb") as f:
                h.update(b"central:" + identity_store.PROFILE_MD.encode("utf-8") + b"\0" + f.read() + b"\0")
        except OSError:
            pass
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


# P1/B2: the model must see the keys that already stand in working/user-candidates.json, otherwise it
# invents a fresh key for every repeat of an observation and the repeat path (occurrences++, the second
# mention confirms) never fires - 264 open candidates in the stores are that failure. The list is capped
# so the state stays small; it does NOT enter the state hash (working/ is scratch by design). It is given only
# with --session-id (D10): wrap-up passes its session, the session-bootstrap call does not, and a start must not
# pay ~7 KB of context for keys it never reads. `user_candidates_total` is always the real count.
CANDIDATE_CAP = 40
OBSERVATION_CHARS = 80


def _open_candidate(row):
    """A candidate that can still progress: not promoted yet, and not a mood (moods are never promoted)."""
    return row.get("status") != "promoted" and row.get("signal_type") != "mood"


def candidate_keys(mem, central=None):
    """(entries, total): up to CANDIDATE_CAP candidates as {key, observation (<= 80 chars), status,
    occurrences}. Open ones first, inside each group the most recently seen first (ties: later in the
    file first). `total` counts every usable row, so the model can tell the list was cut. With an active central
    profile (P2) the keys come from its queue - that is where wrap-up enqueues."""
    if central:
        rows = _rows(central, identity_store.PROFILE_QUEUE, "candidates")
        if rows is None and os.path.isfile(os.path.join(central, identity_store.PROFILE_QUEUE)):
            print(f"preprocess_state: central queue {identity_store.PROFILE_QUEUE} is unreadable - no candidate "
                  f"keys (fail-soft)", file=sys.stderr)
    else:
        rows = _rows(mem, os.path.join("working", "user-candidates.json"), "candidates")
    if not rows:
        return [], 0
    usable = [(i, r) for i, r in enumerate(rows) if isinstance(r.get("key"), str) and r["key"].strip()]
    usable.sort(key=lambda t: (str(t[1].get("last_seen") or ""), t[0]), reverse=True)
    usable.sort(key=lambda t: 0 if _open_candidate(t[1]) else 1)  # stable: open ones first
    out = []
    for _i, r in usable[:CANDIDATE_CAP]:
        obs = " ".join(str(r.get("observation") or "").split())
        if len(obs) > OBSERVATION_CHARS:
            obs = obs[:OBSERVATION_CHARS - 1] + "…"
        try:
            occ = int(r.get("occurrences", 1))
        except (TypeError, ValueError):
            occ = 1
        out.append({"key": r["key"], "observation": obs, "status": str(r.get("status") or ""), "occurrences": occ})
    return out, len(usable)


def _promotable(row) -> bool:
    """The promotion gate of apply_wrapup.promote_candidates (wrap-up Step 6.3), read only."""
    if row.get("status") == "promoted" or row.get("signal_type") == "mood" or row.get("trust_source") != "conversation":
        return False
    try:
        occ, conf = int(row.get("occurrences", 1)), float(row.get("confidence", 0.0))
    except (TypeError, ValueError):
        return False
    return row.get("status") == "confirmed" or (row.get("status") == "inferred" and occ >= 2 and conf >= 0.6)


def identity_summary(mem, central=None):
    """What session-bootstrap's gates 6.5(b)/(c) need, from the queue wrap-up writes to: the central queue when the
    central profile is active, else the project's own. The bootstrap never reads that queue itself (P2: 357 KB)."""
    rows = (_rows(central, identity_store.PROFILE_QUEUE, "candidates") if central
            else _rows(mem, os.path.join("working", "user-candidates.json"), "candidates")) or []
    prom = [r for r in rows if _promotable(r)]
    seen = [str(r.get("last_seen")) for r in rows if r.get("last_seen")]
    return {"target": "central" if central else "project", "promotable": len(prom),
            "promotable_example": f"{prom[0].get('id')}: {prom[0].get('key')}" if prom else "",
            "last_seen": max(seen) if seen else ""}


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


# Statuses that end a task. apply_open_tasks closes with "closed", older stores use
# "done", and real stores also carry "cancelled" and "deferred" (5.2.1 review: the
# night run listed all of them as open items). Unknown statuses stay visible.
CLOSED_TASK_STATUSES = frozenset({"done", "closed", "cancelled", "deferred"})


def open_tasks(mem):
    text = read_text(os.path.join(mem, "context", "open-tasks.json"))
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    tasks = data if isinstance(data, list) else data.get("tasks", [])
    rows = [t for t in tasks if isinstance(t, dict) and t.get("status") not in CLOSED_TASK_STATUSES] \
        if isinstance(tasks, list) else []
    # Blocked first, then the most recently touched - consumers take the head of this list
    # (night-run board "Next", bootstrap Atlas query); file order put the oldest task first
    # (DCO #9711). Same rule as the session-start.sh briefing.
    rows.sort(key=lambda t: str(t.get("updated") or t.get("created") or ""), reverse=True)
    rows.sort(key=lambda t: 0 if t.get("status") == "blocked" else 1)
    return [{"id": t.get("id", ""), "title": t.get("title", ""), "status": t.get("status", "")} for t in rows]


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
    parser.add_argument("--identity-store", default=None,
                        help="central identity store (P2); else $AGENTIC_OS_IDENTITY_STORE, else ~/.agent-memory - "
                             "read only, used when its identity/profile.md exists")
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
            "user_candidates": [], "user_candidates_total": 0, "identity": {},
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
        "user_candidates": [],
        "user_candidates_total": 0,
        "identity": {},
    }
    try:
        state["changed_files"] = collect_changed_files(mem, args.session_id)
        state["git_diff_summary"] = git_diff_summary()
        state["threshold_events"] = threshold_events(mem)
        state["validation_errors"] = validation_errors(mem)
        state["open_tasks"] = open_tasks(mem)
        # read-only: no temp-store guard here (identity_store.select_paths guards writers)
        store, _source = identity_store.resolve_store(args.identity_store, os.environ)
        central = store if identity_store.central_active(store) else None
        entries, state["user_candidates_total"] = candidate_keys(mem, central)
        state["identity"] = identity_summary(mem, central)
        if args.session_id:  # D10: only wrap-up (it names its session) uses the keys; the bootstrap call must not pay for them
            state["user_candidates"] = entries
        prev = read_text(os.path.join(mem, HASH_FILE))
        state["previous_state_hash"] = (prev or "").strip()
        state["current_state_hash"] = compute_state_hash(mem, central)

        if args.write_hash:
            if _nested_store_guard(mem):
                print(f"preprocess_state: refusing to create a store inside "
                      f".agent-memory ({os.path.abspath(mem)}) — skipping "
                      f"--write-hash (nested-store guard)", file=sys.stderr)
            else:
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

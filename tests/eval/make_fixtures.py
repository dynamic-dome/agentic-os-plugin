#!/usr/bin/env python3
"""Generate the committed eval fixtures for the skill-redesign harness (T-35).

Each fixture is a minimal-but-REAL .agent-memory store that genuinely triggers a
specific bootstrap gate when the deterministic scripts run against it. Fixtures
are committed static data; re-run this ONLY on purpose (content drift) so the
fast-path hash stays correct-by-construction.

    python tests/eval/make_fixtures.py

Design rationale: memevalharness.md (membrain). mtimes are NOT set here (git
cannot carry them) — the runner (eval_signals.py) backdates the recovery marker
when it stages a fixture into a temp dir.
"""
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FIXROOT = os.path.join(HERE, "fixtures")

# Mirror of preprocess_state.py STATE_FILES — the files whose content defines the
# state hash. Kept in sync manually; test_fixtures_state_files_in_sync guards it.
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


def w(mem, rel, content):
    p = os.path.join(mem, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if isinstance(content, (dict, list)):
        content = json.dumps(content, indent=1, ensure_ascii=False)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def base_store(mem, summary="# Last Session\n\n*Date: 2026-07-01*\n\nBaseline.\n"):
    """Write the common core files every store needs (valid, parseable)."""
    w(mem, "session-summary.md", summary)
    w(mem, "context/open-tasks.json", [
        {"id": "T-1", "title": "Demo open task", "status": "open"},
    ])
    w(mem, "context/project-context.md", "# Project Context\n\nStack: python.\n")
    w(mem, "context/decisions.json", [])
    w(mem, "learnings/learnings.json", [])
    w(mem, "learnings/learnings.md", "# Learnings\n")
    w(mem, "patterns/patterns.json", [])
    w(mem, "iterations/iteration-log.md", "# Iteration Log\n")
    w(mem, "iterations/errors.json", [])
    w(mem, "identity/user.md", "# User\n\nPrefers German.\n")
    w(mem, "identity/soul.md", "# Soul\n\nBe concise.\n")
    # empty soul-candidate stub (identity fixture overrides this)
    w(mem, "identity/soul-candidates.md", "*Keine offenen Kandidaten.*\n")


def compute_hash(mem):
    h = hashlib.sha256()
    for rel in STATE_FILES:
        p = os.path.join(mem, rel)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                h.update(rel.encode("utf-8") + b"\0" + f.read() + b"\0")
    return h.hexdigest()


def build_fresh(mem):
    """Freshly initialised store, no prior hash -> fast path must be FALSE."""
    base_store(mem)
    # deliberately NO working/state-hash -> previous_state_hash empty


def build_fast_path(mem):
    """Prior hash matches current content, no dirty markers -> fast path TRUE."""
    base_store(mem)
    w(mem, "working/state-hash", compute_hash(mem))


def build_recovery(mem):
    """Stale un-consolidated CODEX dirty marker (>30min set by runner).

    Must: survive gc (not consolidated, no later wrapup) AND make changed_files
    non-empty in preprocess (dirty:true) -> fast path FALSE + RECOVERY surfaced.
    """
    base_store(mem)
    w(mem, "working/state-hash", compute_hash(mem))  # hash matches...
    # ...but a dirty:true marker forces changed_files non-empty -> NOT fast path
    w(mem, "working/dirty-019f73ae-crash-codex.json", {
        "session_id": "019f73ae-crash-codex",
        "agent": "codex",
        "dirty": True,
        "started": "2026-07-01T07:00:00+02:00",
        "updated": "2026-07-01T07:42:00+02:00",
        "touched_files": ["some/file.py", "other/file.md"],
        "write_count": 4,
    })
    # no consolidation-marker newer than the dirty marker -> gc must KEEP it


def build_identity(mem):
    """Promotable user-candidate + open soul-candidate -> identity gates fire."""
    base_store(mem)
    # open soul candidate (not the empty stub)
    w(mem, "identity/soul-candidates.md",
      "# Soul Candidates\n\n- Candidate: always cite sources.\n")
    # promotable user candidate (inferred, occurrences>=2, confidence>=0.6)
    w(mem, "working/user-candidates.json", [
        {"id": "uc-1", "key": "editor", "value": "neovim",
         "status": "inferred", "occurrences": 3, "confidence": 0.8,
         "last_seen": "2026-07-01"},
    ])
    # no state-hash -> not fast path (identity gate must run in full path)


def build_conflict(mem):
    """Contradictory active records + escalation trigger.

    The escalation/conflict gate is body-interpreted (checked by gate_linkage +
    capture_protocol), not by a single script signal; the fixture provides the
    conflicting state for the capture run.
    """
    base_store(mem, summary="# Last Session\n\n*Date: 2026-07-01*\n\n"
               "Conflict: open-task T-2 says done AND open.\n")
    # contradictory open-tasks: same id twice with opposing status
    w(mem, "context/open-tasks.json", [
        {"id": "T-2", "title": "Ship X", "status": "open"},
        {"id": "T-2", "title": "Ship X", "status": "done"},
    ])
    # a foreign un-consolidated marker to also exercise recovery+conflict overlap
    w(mem, "working/dirty-ffffffff-foreign.json", {
        "session_id": "ffffffff-foreign", "agent": "claude", "dirty": True,
        "started": "2026-07-01T06:00:00+02:00",
        "updated": "2026-07-01T06:10:00+02:00",
        "touched_files": ["x.md"], "write_count": 1,
    })


BUILDERS = {
    "fresh": build_fresh,
    "fast-path": build_fast_path,
    "recovery": build_recovery,
    "identity": build_identity,
    "conflict": build_conflict,
}


def main():
    import shutil
    for name, builder in BUILDERS.items():
        mem = os.path.join(FIXROOT, name)
        if os.path.isdir(mem):
            shutil.rmtree(mem)
        os.makedirs(mem)
        builder(mem)
        print(f"built fixture: {name}")


if __name__ == "__main__":
    main()

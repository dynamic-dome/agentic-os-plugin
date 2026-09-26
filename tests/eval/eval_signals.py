#!/usr/bin/env python3
"""Schicht 1 (deterministic) of the skill-redesign eval harness (T-35).

For each fixture scenario: stage it into a temp dir, apply scenario-specific
mtime setup (backdate crash markers so protection comes from the consolidation
LOGIC, not the 30-min mtime grace), run the REAL memory scripts, and assert the
gate-triggering signals bootstrap depends on.

This is a PLUMBING check — it proves the fixtures still trigger the right script
signals. It does NOT prove the (redesigned) skill body reacts to them; that is
gate_linkage.py (static) plus the optional capture_protocol.md (behavioral).

    python tests/eval/eval_signals.py           # exit 0 = all pass, 1 = failure

Design: memevalharness.md (membrain).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPTS = os.path.join(PLUGIN_ROOT, "scripts")
FIXROOT = os.path.join(HERE, "fixtures")

TESTS = 0
FAILS = 0


def check(cond, label):
    global TESTS, FAILS
    TESTS += 1
    if cond:
        print(f"  PASS: {label}")
    else:
        FAILS += 1
        print(f"  FAIL: {label}")


def stage(scenario, backdate_markers=False):
    """Copy a fixture into a fresh temp dir; optionally backdate dirty markers."""
    dst = tempfile.mkdtemp(prefix=f"eval-{scenario}-")
    shutil.copytree(os.path.join(FIXROOT, scenario), dst, dirs_exist_ok=True)
    if backdate_markers:
        workdir = os.path.join(dst, "working")
        old = time.time() - 3600  # 60 min ago -> outside the 30-min mtime grace
        for name in os.listdir(workdir):
            if name.startswith("dirty-") and name.endswith(".json"):
                p = os.path.join(workdir, name)
                os.utime(p, (old, old))
    return dst


def run_preprocess(mem):
    """Return the parsed preprocess_state.py state object (no --session-id)."""
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "preprocess_state.py"), mem],
        capture_output=True, encoding="utf-8", errors="replace", timeout=30,
    )
    return json.loads(proc.stdout)


def fast_path(state):
    ph, ch = state["previous_state_hash"], state["current_state_hash"]
    return ph != "" and ph == ch and not state["changed_files"]


def gc_eligible_count(mem):
    """Run gc_dirty_markers.py dry-run; return number of GC-eligible markers."""
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "gc_dirty_markers.py"), mem],
        capture_output=True, encoding="utf-8", errors="replace", timeout=30,
    )
    for line in proc.stdout.splitlines():
        line = line.strip()
        # gc prints e.g. "0 orphaned dirty marker(s) GC-eligible."
        if "GC-eligible" in line:
            head = line.split()[0]
            try:
                return int(head)
            except ValueError:
                return -1
    # No summary line: gc stays silent when there is no working/ dir at all.
    # exit 0 + no line = nothing to collect = 0 eligible; non-zero = real error.
    return 0 if proc.returncode == 0 else -1


def read_json(mem, rel):
    with open(os.path.join(mem, rel), "r", encoding="utf-8") as f:
        return json.load(f)


def read_text(mem, rel):
    with open(os.path.join(mem, rel), "r", encoding="utf-8") as f:
        return f.read()


def promotable(cands):
    """wrap-up promotion gate: confirmed, OR inferred & occ>=2 & confidence>=0.6."""
    for c in cands:
        if not isinstance(c, dict):
            continue
        if c.get("status") == "confirmed":
            return True
        if (c.get("status") == "inferred" and c.get("occurrences", 0) >= 2
                and c.get("confidence", 0) >= 0.6):
            return True
    return False


def has_contradictory_tasks(tasks):
    """Same task id appearing with conflicting status (e.g. open AND done)."""
    seen = {}
    for t in tasks:
        if not isinstance(t, dict):
            continue
        tid = t.get("id")
        st = t.get("status")
        if tid in seen and seen[tid] != st:
            return True
        seen[tid] = st
    return False


# --- scenario assertions ---------------------------------------------------

def test_fresh():
    print("-- fresh --")
    mem = stage("fresh")
    st = run_preprocess(mem)
    check(fast_path(st) is False, "fresh: fast path is FALSE (no prior hash)")
    check(st["changed_files"] == [], "fresh: no changed files")
    check(gc_eligible_count(mem) == 0, "fresh: gc finds 0 eligible markers")


def test_fast_path():
    print("-- fast-path --")
    mem = stage("fast-path")
    st = run_preprocess(mem)
    check(fast_path(st) is True, "fast-path: fast path is TRUE (hash match, no dirty)")
    check(st["changed_files"] == [], "fast-path: no changed files")


def test_recovery():
    print("-- recovery --")
    mem = stage("recovery", backdate_markers=True)  # simulate a >30min-old crash
    st = run_preprocess(mem)
    check(fast_path(st) is False,
          "recovery: fast path is FALSE despite hash match (dirty marker present)")
    check(len(st["changed_files"]) >= 1, "recovery: changed files non-empty")
    # the un-consolidated marker must SURVIVE gc (protection = logic, not mtime)
    check(gc_eligible_count(mem) == 0,
          "recovery: backdated un-consolidated codex marker survives gc")
    markers = [n for n in os.listdir(os.path.join(mem, "working"))
               if n.startswith("dirty-")]
    check(len(markers) == 1, "recovery: exactly one dirty marker present")
    data = read_json(mem, os.path.join("working", markers[0]))
    check(data.get("agent") == "codex" and data.get("dirty") is True,
          "recovery: marker is an un-consolidated codex session")


def test_hash_present_but_unequal():
    """Negative case the fixtures otherwise miss: a prior hash EXISTS but differs
    from the current content -> fast path must be FALSE. Guards a loosened '=='."""
    print("-- hash present but unequal --")
    mem = stage("fast-path")  # ships a matching state-hash
    # mutate a STATE_FILE so current hash diverges from the recorded one
    with open(os.path.join(mem, "session-summary.md"), "a", encoding="utf-8") as f:
        f.write("\nmutated after hash was written.\n")
    st = run_preprocess(mem)
    check(st["previous_state_hash"] != "", "unequal: a prior hash is present")
    check(st["previous_state_hash"] != st["current_state_hash"],
          "unequal: prior hash differs from current")
    check(fast_path(st) is False, "unequal: fast path is FALSE on hash mismatch")


def test_empty_touched_marker():
    """Characterization (Codex review): a dirty:true marker with EMPTY touched
    yields no changed_files, so the fast-path signal alone would not flag it —
    but the marker itself stays present, so recovery-detect (which keys off the
    marker's existence, not its touched list) still surfaces it. Locks this
    interaction so a future change to either path is noticed."""
    print("-- empty-touched marker --")
    mem = stage("fast-path")
    with open(os.path.join(mem, "working", "dirty-empty-sess.json"),
              "w", encoding="utf-8") as f:
        json.dump({"session_id": "empty-sess", "agent": "codex", "dirty": True,
                   "touched_files": [], "write_count": 2}, f)
    st = run_preprocess(mem)
    check(st["changed_files"] == [],
          "empty-touched: no changed_files from an empty touched list")
    markers = [n for n in os.listdir(os.path.join(mem, "working"))
               if n.startswith("dirty-")]
    check("dirty-empty-sess.json" in markers,
          "empty-touched: marker still present for recovery-detect")


def test_identity():
    print("-- identity --")
    mem = stage("identity")
    st = run_preprocess(mem)
    check(fast_path(st) is False, "identity: fast path is FALSE (full path runs)")
    cands = read_json(mem, os.path.join("working", "user-candidates.json"))
    check(promotable(cands), "identity: a promotable user-candidate exists")
    soul = read_text(mem, os.path.join("identity", "soul-candidates.md"))
    check("Keine offenen Kandidaten" not in soul,
          "identity: soul-candidates is a real (non-stub) candidate")


def test_conflict():
    print("-- conflict --")
    mem = stage("conflict", backdate_markers=True)
    st = run_preprocess(mem)
    check(fast_path(st) is False, "conflict: fast path is FALSE")
    tasks = read_json(mem, os.path.join("context", "open-tasks.json"))
    check(has_contradictory_tasks(tasks),
          "conflict: open-tasks carries a contradictory (open AND done) record")


def test_state_files_in_sync():
    """make_fixtures.STATE_FILES must mirror preprocess_state.STATE_FILES.

    A drift here silently invalidates the fast-path fixture hash (they would hash
    different file sets), so guard it explicitly.
    """
    print("-- state-files sync --")
    sys.path.insert(0, SCRIPTS)
    sys.path.insert(0, HERE)
    import importlib
    pp = importlib.import_module("preprocess_state")
    mf = importlib.import_module("make_fixtures")
    check(pp.STATE_FILES == mf.STATE_FILES,
          "make_fixtures STATE_FILES matches preprocess_state STATE_FILES")


def main():
    print("=== Eval Harness — Schicht 1 (deterministic script signals) ===")
    for t in (test_state_files_in_sync, test_fresh, test_fast_path,
              test_hash_present_but_unequal, test_empty_touched_marker,
              test_recovery, test_identity, test_conflict):
        t()
    print(f"\n{TESTS - FAILS}/{TESTS} checks passed.")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Tests for scripts/handoff_write_guard.py (T-19 read-then-write guard).

Guards the cross-project write surfaces (central handoff, status board) against
parallel-session drift: snapshot at read time, check immediately before write.
Run: python tests/test-handoff-write-guard.py  (exit 0 = pass)
"""
import json
import os
import subprocess
import sys
import tempfile

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PLUGIN_ROOT, "scripts", "handoff_write_guard.py")

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS: {name}")
    else:
        print(f"  FAIL: {name} {detail}")
        FAILURES.append(name)


def run(args, cwd):
    proc = subprocess.run(
        [sys.executable, SCRIPT] + args,
        capture_output=True, encoding="utf-8", errors="replace", cwd=cwd,
    )
    return proc


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    print("=== handoff_write_guard.py tests ===")
    check("script exists", os.path.isfile(SCRIPT))
    if not os.path.isfile(SCRIPT):
        print("=== 1 failure (script missing) ===")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        state = os.path.join(tmp, "guard-state.json")
        handoff = os.path.join(tmp, "session-summary.md")
        board = os.path.join(tmp, "cross-project-status.md")
        write(handoff, "# Letzte Session\nblock A\n")
        write(board, "# Cross-Project Status Board\n## membrain\n- State: x\n")

        # 1. snapshot both files -> exit 0, state file valid JSON with sha256
        p = run(["snapshot", handoff, board, "--state", state], cwd=tmp)
        check("snapshot exits 0", p.returncode == 0, f"rc={p.returncode} err={p.stderr[:200]}")
        try:
            st = json.load(open(state, encoding="utf-8"))
            check("state file valid JSON", True)
        except Exception as e:
            check("state file valid JSON", False, str(e))
            st = {}
        norm = {os.path.normcase(os.path.abspath(k)) for k in st}
        check("state holds both files",
              os.path.normcase(handoff) in norm and os.path.normcase(board) in norm,
              f"keys={list(st)}")
        entry = st.get(next((k for k in st if os.path.normcase(k) == os.path.normcase(handoff)), ""), {})
        check("entry has sha256", bool(entry.get("sha256")))
        check("entry has mtime", entry.get("mtime") is not None)

        # 2. unchanged file -> check exits 0
        p = run(["check", handoff, "--state", state], cwd=tmp)
        check("unchanged: check exits 0", p.returncode == 0, f"rc={p.returncode} err={p.stderr[:200]}")

        # 3. touch without content change (mtime drifts, hash equal) -> still exit 0
        os.utime(handoff, None)
        p = run(["check", handoff, "--state", state], cwd=tmp)
        check("touch-only: check exits 0", p.returncode == 0, f"rc={p.returncode}")

        # 4. PARALLEL-WRITE FIXTURE: another session prepends its block after our read
        write(handoff, "# Letzte Session\nblock B (fremde Session)\n---\n"
                       "# Vorherige Session (block A, erhalten)\n")
        p = run(["check", handoff, "--state", state], cwd=tmp)
        check("parallel write: check exits 20 (drift)", p.returncode == 20,
              f"rc={p.returncode} out={p.stdout[:200]}")
        check("drift names the file", handoff in (p.stdout + p.stderr))

        # 5. drift on ONE file must not poison the other -> board still clean
        p = run(["check", board, "--state", state], cwd=tmp)
        check("other file unaffected: check exits 0", p.returncode == 0, f"rc={p.returncode}")

        # 6. re-read + merge flow: fresh snapshot after re-read -> check clean again
        p = run(["snapshot", handoff, "--state", state], cwd=tmp)
        check("re-snapshot exits 0", p.returncode == 0)
        p = run(["check", handoff, "--state", state], cwd=tmp)
        check("after re-snapshot: check exits 0", p.returncode == 0, f"rc={p.returncode}")

        # 7. file deleted after snapshot -> drift
        os.remove(board)
        p = run(["check", board, "--state", state], cwd=tmp)
        check("deleted after snapshot: exit 20", p.returncode == 20, f"rc={p.returncode}")

        # 8. file absent at snapshot time, created before check -> drift
        newfile = os.path.join(tmp, "new-surface.md")
        p = run(["snapshot", newfile, "--state", state], cwd=tmp)
        check("snapshot of missing file exits 0", p.returncode == 0, f"rc={p.returncode}")
        write(newfile, "created by another session\n")
        p = run(["check", newfile, "--state", state], cwd=tmp)
        check("created after snapshot: exit 20", p.returncode == 20, f"rc={p.returncode}")

        # 9. missing file at snapshot AND at check -> exit 0 (both agree: absent)
        ghost = os.path.join(tmp, "ghost.md")
        run(["snapshot", ghost, "--state", state], cwd=tmp)
        p = run(["check", ghost, "--state", state], cwd=tmp)
        check("absent-absent: check exits 0", p.returncode == 0, f"rc={p.returncode}")

        # 10. check WITHOUT prior snapshot -> exit 21 (read-then-write violated)
        orphan = os.path.join(tmp, "orphan.md")
        write(orphan, "x\n")
        p = run(["check", orphan, "--state", state], cwd=tmp)
        check("no snapshot: check exits 21", p.returncode == 21, f"rc={p.returncode}")

        # 11. missing/corrupt state file -> check exits 21, snapshot recreates it
        bad_state = os.path.join(tmp, "bad-state.json")
        write(bad_state, "{ not json")
        p = run(["check", handoff, "--state", bad_state], cwd=tmp)
        check("corrupt state: check exits 21", p.returncode == 21, f"rc={p.returncode}")
        p = run(["snapshot", handoff, "--state", bad_state], cwd=tmp)
        check("corrupt state: snapshot recovers (exit 0)", p.returncode == 0,
              f"rc={p.returncode} err={p.stderr[:200]}")

        # 12. usage errors -> exit 2
        p = run(["check", "--state", state], cwd=tmp)
        check("no files: exit 2", p.returncode == 2, f"rc={p.returncode}")
        p = run(["frobnicate", handoff, "--state", state], cwd=tmp)
        check("unknown subcommand: exit 2", p.returncode == 2, f"rc={p.returncode}")

    n = len(FAILURES)
    print(f"=== {n} failure(s) ===" if n else "=== all tests passed ===")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())

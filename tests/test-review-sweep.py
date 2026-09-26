#!/usr/bin/env python3
"""Tests for scripts/review_sweep.py (decay report, never mutates).
Run: python tests/test-review-sweep.py  (exit 0 = pass)"""
import json
import os
import subprocess
import sys
import tempfile
import time

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PLUGIN_ROOT, "scripts", "review_sweep.py")
FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS: {name}")
    else:
        print(f"  FAIL: {name} {detail}")
        FAILURES.append(name)


def run(args, cwd):
    return subprocess.run([sys.executable, SCRIPT] + args, capture_output=True,
                          encoding="utf-8", errors="replace", cwd=cwd)


def write(path, text, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    if mtime:
        os.utime(path, (mtime, mtime))


def main():
    print("=== review_sweep.py tests ===")
    check("script exists", os.path.isfile(SCRIPT))
    if not os.path.isfile(SCRIPT):
        print("=== 1 failure (script missing) ===")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        mem = os.path.join(tmp, ".agent-memory")
        rows = [
            {"id": "L1", "date": "2026-05-01", "text": "due one", "review_after": "2026-08-01", "superseded_by": None},
            {"id": "L2", "date": "2026-05-01", "text": "due but superseded", "review_after": "2026-08-01", "superseded_by": "L3"},
            {"id": "L3", "date": "2026-08-20", "text": "fresh", "review_after": "2026-11-18", "superseded_by": None},
            {"id": "L4", "date": "2026-07-01", "text": "old candidate", "bridge_status": "candidate", "superseded_by": None},
            {"id": "L5", "date": "2026-09-01", "text": "new candidate", "bridge_status": "candidate", "superseded_by": None},
            {"id": "L6", "date": "2026-05-01", "text": "retired", "review_after": "2026-08-01", "status": "retired", "superseded_by": None},
        ]
        write(os.path.join(mem, "learnings", "learnings.json"), json.dumps(rows))
        native = os.path.join(tmp, "memory")
        old = time.time() - 200 * 86400
        write(os.path.join(native, "MEMORY.md"), "- [k](kept.md) — x\n")
        write(os.path.join(native, "kept.md"), "k", mtime=old)
        write(os.path.join(native, "orphan-old.md"), "o", mtime=old)
        write(os.path.join(native, "orphan-new.md"), "n")
        write(os.path.join(native, "_archive", "gone.md"), "g", mtime=old)
        report = os.path.join(tmp, "report.md")
        before = json.dumps(rows)
        p = run([mem, "--native-memory", native, "--report", report, "--today", "2026-09-08"], cwd=tmp)
        check("exit 0", p.returncode == 0, p.stderr[:300])
        check("summary line", p.stdout.strip() == "review-sweep: due=1 native_stale=1 candidates_stale=1", p.stdout)
        rep = open(report, encoding="utf-8").read()
        check("due lists L1 only", "[L1]" in rep and "[L2]" not in rep and "[L6]" not in rep, rep)
        check("native lists orphan-old only", "orphan-old.md" in rep and "orphan-new.md" not in rep and "kept.md" not in rep and "gone.md" not in rep, rep)
        check("candidates lists L4 only", "[L4]" in rep and "[L5]" not in rep, rep)
        check("store untouched", open(os.path.join(mem, "learnings", "learnings.json"), encoding="utf-8").read() == before)
        p = run([mem, "--today", "2026-09-08"], cwd=tmp)
        check("no native dir -> native_stale=0", "native_stale=0" in p.stdout, p.stdout)
        write(os.path.join(mem, "learnings", "learnings.json"), "{bad")
        check("invalid store exit 1", run([mem], cwd=tmp).returncode == 1)
    n = len(FAILURES)
    print(f"=== {n} failure{'s' if n != 1 else ''} ===")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

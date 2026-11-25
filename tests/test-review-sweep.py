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


def snapshot_tree(root):
    out = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(base, name)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = f.read()
    return out


def test_candidates_json():
    """6.0.0 Pflegelauf: --candidates-json lists the 'faellig' candidates (due review_after or none), read-only."""
    print("-- --candidates-json (Pflegelauf candidate source) --")
    with tempfile.TemporaryDirectory() as tmp:
        mem = os.path.join(tmp, ".agent-memory")
        rows = [
            {"id": "L1", "date": "2026-05-01", "text": "due in august", "review_after": "2026-08-01", "superseded_by": None},
            {"id": "L2", "date": "2026-05-01", "text": "due in september", "review_after": "2026-09-01", "superseded_by": None},
            {"id": "L3", "date": "2026-05-01", "text": "no review date", "superseded_by": None},
            {"id": "L4", "date": "2026-05-01", "text": "not due yet", "review_after": "2026-12-01", "superseded_by": None},
            # review_after == today is not due yet (same strict comparison as the sweep's due list)
            {"id": "L11", "date": "2026-05-01", "text": "due tomorrow", "review_after": "2026-09-08", "superseded_by": None},
            {"id": "L5", "date": "2026-05-01", "text": "superseded", "superseded_by": "L3"},
            {"id": "L6", "date": "2026-05-01", "text": "owner veto", "bridge_status": "rejected",
             "bridge_note": "2026-10-16 owner: nein", "superseded_by": None},
            # a live pointer and its member are anchored already: the run never judges them (no superseded_by
            # on them anyway, the applier refuses it) - without this a second run would show them again
            {"id": "L7", "date": "2026-05-01", "kind": "principle", "text": "rule pointer", "derived_from": ["L8"],
             "superseded_by": None},
            {"id": "L8", "date": "2026-05-01", "text": "member of a live pointer", "superseded_by": None},
            # a retired pointer anchors nothing: its member is a candidate like any other row
            {"id": "L9", "date": "2026-05-01", "kind": "principle", "text": "retired pointer", "derived_from": ["L10"],
             "superseded_by": "obsolete:gone"},
            {"id": "L10", "date": "2026-05-01", "text": "member of a retired pointer", "review_after": "2026-07-15",
             "superseded_by": None},
        ]
        write(os.path.join(mem, "learnings", "learnings.json"), json.dumps(rows))
        before = snapshot_tree(tmp)
        report = os.path.join(mem, "working", "review-sweep.md")
        p = run([mem, "--candidates-json", "--today", "2026-09-08", "--report", report], cwd=tmp)
        check("candidates-json: exit 0", p.returncode == 0, p.stderr[:300])
        try:
            got = json.loads(p.stdout)
        except ValueError:
            got = None
        check("candidates-json: stdout is one JSON list", isinstance(got, list), p.stdout[:300])
        got = got if isinstance(got, list) else []
        check("candidates-json: two due, one without date -> three entries in store order",
              got[:3] == [{"id": "L1", "reason": "due:2026-08"}, {"id": "L2", "reason": "due:2026-09"},
                          {"id": "L3", "reason": "ohne-termin"}], got)
        ids = [c.get("id") for c in got]
        check("candidates-json: not due (also review_after == today), superseded and owner veto are no candidates",
              not {"L4", "L11", "L5", "L6"} & set(ids), ids)
        check("candidates-json: live pointer and its member are no candidates", not {"L7", "L8"} & set(ids), ids)
        check("candidates-json: member of a retired pointer is a candidate (due:2026-07)",
              {"id": "L10", "reason": "due:2026-07"} in got and "L9" not in ids, got)
        check("candidates-json: exactly four entries", len(got) == 4, got)
        check("candidates-json: nothing written (no report, store bytes unchanged)",
              snapshot_tree(tmp) == before and not os.path.exists(report))


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
            {"id": "L6", "date": "2026-05-01", "text": "legacy status retired", "review_after": "2026-08-01", "status": "retired", "superseded_by": None},
            # 5.2.1: the real field is bridge_status (review_sweep checked a 'status' nobody writes)
            {"id": "L7", "date": "2026-05-01", "text": "bridge retired", "review_after": "2026-08-01", "bridge_status": "retired", "superseded_by": None},
            {"id": "L8", "date": "2026-05-01", "text": "owner veto", "review_after": "2026-08-01", "bridge_status": "rejected", "superseded_by": None},
            {"id": "L9", "date": "2026-05-01", "text": "approved and due", "review_after": "2026-08-01", "bridge_status": "approved", "superseded_by": None},
            # 5.7.0 (Codex F2): an agent's bridge rejection only keeps the row out of the
            # projections - the learning itself stays up for review; an owner's stays a veto.
            {"id": "L10", "date": "2026-05-01", "text": "agent kept out of bridge", "review_after": "2026-08-01", "bridge_status": "rejected",
             "bridge_note": "2026-10-16 agent: nicht in jeder Session", "superseded_by": None},
            {"id": "L11", "date": "2026-05-01", "text": "owner bridge veto", "review_after": "2026-08-01", "bridge_status": "rejected",
             "bridge_note": "2026-10-16 owner: nein", "superseded_by": None},
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
        check("summary line", p.stdout.strip() == "review-sweep: due=3 native_stale=1 candidates_stale=1", p.stdout)
        rep = open(report, encoding="utf-8").read()
        check("due lists L1 and L9 only", "[L1]" in rep and "[L9]" in rep and "[L2]" not in rep and "[L6]" not in rep, rep)
        check("bridge_status retired/rejected are not due", "[L7]" not in rep and "[L8]" not in rep, rep)
        check("an agent's bridge rejection stays due, an owner's does not", "[L10]" in rep and "[L11]" not in rep, rep)
        check("footer names no field that does not exist", "status: retired" not in rep, rep[-300:])
        check("6.0.0 footer: the Pflegelauf is the write path, no 'until a write path exists'",
              "/agentic-os:maintain" in rep and "Schreibweg" not in rep, rep[-300:])
        check("native lists orphan-old only", "orphan-old.md" in rep and "orphan-new.md" not in rep and "kept.md" not in rep and "gone.md" not in rep, rep)
        check("candidates lists L4 only", "[L4]" in rep and "[L5]" not in rep, rep)
        check("store untouched", open(os.path.join(mem, "learnings", "learnings.json"), encoding="utf-8").read() == before)
        p = run([mem, "--today", "2026-09-08"], cwd=tmp)
        check("no native dir -> native_stale=0", "native_stale=0" in p.stdout, p.stdout)
        # DCO #9695: a UTF-8 BOM (PowerShell Set-Content -Encoding utf8) is not corruption -
        # BOM'd learnings.json and BOM'd MEMORY.md read exactly like the BOM-less files.
        report_nobom = open(report, encoding="utf-8").read()
        with open(os.path.join(mem, "learnings", "learnings.json"), "wb") as f:
            f.write(b"\xef\xbb\xbf" + json.dumps(rows).encode("utf-8"))
        with open(os.path.join(native, "MEMORY.md"), "wb") as f:
            f.write(b"\xef\xbb\xbf" + "- [k](kept.md) — x\n".encode("utf-8"))
        os.utime(os.path.join(native, "kept.md"), (old, old))
        report_bom = os.path.join(tmp, "report-bom.md")
        p = run([mem, "--native-memory", native, "--report", report_bom, "--today", "2026-09-08"], cwd=tmp)
        check("BOM store: exit 0 and same summary as without BOM",
              p.returncode == 0 and p.stdout.strip() == "review-sweep: due=3 native_stale=1 candidates_stale=1",
              p.stdout + p.stderr[:300])
        check("BOM store: report identical to the BOM-less run",
              open(report_bom, encoding="utf-8").read() == report_nobom)
        write(os.path.join(mem, "learnings", "learnings.json"), "{bad")
        check("invalid store exit 1", run([mem], cwd=tmp).returncode == 1)
        check("invalid store: --candidates-json exit 1", run([mem, "--candidates-json"], cwd=tmp).returncode == 1)
    test_candidates_json()
    n = len(FAILURES)
    print(f"=== {n} failure{'s' if n != 1 else ''} ===")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

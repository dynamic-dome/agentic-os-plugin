#!/usr/bin/env python3
"""Tests for scripts/harvest_backfill.py - the one-time harvest-state backfill (Phase 2, Scheibe 8, D-024).

Contract under test:
  - only clean markers stamped "wrap-up" are candidates; dirty, closed, headless-stamped and
    unstamped markers are never touched.
  - a night-run victim (sid named in the iteration log, or consolidated_at inside a real night
    run of this project) closes only by a real wrap-up in its transcript (recorded path or
    found by sid) or a native note - never "trivial" or "transcript_fehlt"; else it stays pending.
  - every other "wrap-up" marker closes as "legacy" (run "backfill").
  - the default is a dry run that writes nothing; --apply is idempotent.

Every path is a temp path - no test touches a real store or ~/.claude.
Run: python tests/test-harvest-backfill.py   (exit 0 = pass, 1 = fail)
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)
_spec = importlib.util.spec_from_file_location("harvest_backfill", os.path.join(SCRIPTS, "harvest_backfill.py"))
hb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hb)
hs = hb.hs

FAILURES = []
NIGHT = "2026-09-30T03:00:02+02:00"
SLASH = "<command-message>agentic-os:wrap-up</command-message>\n<command-name>/agentic-os:wrap-up</command-name>"


def _check(cond, msg):
    if cond:
        print(f"  PASS: {msg}")
    else:
        print(f"  FAIL: {msg}")
        FAILURES.append(msg)


def _marker(sid, **kw):
    d = {"session_id": sid, "agent": "claude", "dirty": False, "updated": "2026-09-29T22:00:00+02:00",
         "touched_files": ["a.py"], "write_count": 4, "consolidated_by": "wrap-up",
         "last_consolidated_by": "wrap-up", "consolidated_at": "2026-09-29T23:00:00+02:00"}
    d.update(kw)
    return d


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def _transcript(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def _user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def _tool(name):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": name, "input": {}}]}}


def _hashes(root):
    out = {}
    for dp, _, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            out[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out


def _fixture(tmp):
    """One project with every marker class, a night-run report and a native root."""
    proj = os.path.join(tmp, "home", "proj")
    mem = os.path.join(proj, ".agent-memory")
    work = os.path.join(mem, "working")
    native = os.path.join(tmp, "native")
    markers = {
        "legacy1": _marker("legacy1"),
        "dirty1": _marker("dirty1", dirty=True),
        "headless1": _marker("headless1", consolidated_by="headless", last_consolidated_by="headless"),
        "unstamped1": _marker("unstamped1", consolidated_by=None, last_consolidated_by=None),
        "vlog_open": _marker("vlog_open"),                                   # named in the log, no evidence
        "vnight_open": _marker("vnight_open", consolidated_at="2026-09-30T03:00:06+02:00"),
        "vnight_edge": _marker("vnight_edge", consolidated_at="2026-09-30T03:30:03+02:00"),  # 1801 s -> legacy
        "vwrap": _marker("vwrap", consolidated_at="2026-09-30T03:00:06+02:00"),      # transcript found by sid
        "vnative": _marker("vnative"),                                       # in the log + native note
        "vtrivial": _marker("vtrivial", consolidated_at="2026-09-30T03:00:06+02:00", write_count=1),
        "vgone": _marker("vgone", consolidated_at="2026-09-30T03:00:06+02:00",
                         transcript_path=os.path.join(tmp, "gibtsnicht.jsonl")),
    }
    closed = _marker("closed1")
    closed["harvest"] = hs.make_closure(closed, "trivial", "nightly", "2026-10-01T03:00:00+02:00")
    markers["closed1"] = closed
    for sid, d in markers.items():
        _write_json(os.path.join(work, f"dirty-{sid}.json"), d)
    os.makedirs(os.path.join(mem, "iterations"))
    with open(os.path.join(mem, "iterations", "iteration-log.md"), "w", encoding="utf-8") as fh:
        for sid in ("vlog_open", "vnative"):
            fh.write(f"## x (recovered from session {sid})\n- **Summary:** Harvested headless from working/dirty-{sid}.json (no judge).\n\n")
    report = os.path.join(tmp, "nightly.jsonl")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": NIGHT, "dry_run": False, "projects": [{"project_root": proj, "status": "consolidated"}]}) + "\n")
        fh.write(json.dumps({"ts": "2026-09-29T22:59:00+02:00", "dry_run": True,
                             "projects": [{"project_root": proj, "status": "consolidated"}]}) + "\n")
    _transcript(os.path.join(native, "C--proj", "vwrap.jsonl"), [_user(SLASH), _tool("Edit")])
    _transcript(os.path.join(native, "C--proj", "vtrivial.jsonl"), [_user("hallo"), _tool("Edit")])
    os.makedirs(os.path.join(native, "C--proj", "memory"))
    with open(os.path.join(native, "C--proj", "memory", "note.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: x\noriginSessionId: vnative\n---\nbody\n")
    return proj, mem, report, native


def main():
    with tempfile.TemporaryDirectory() as tmp:
        proj, mem, report, native = _fixture(tmp)

        print("=== inputs ===")
        runs = hb.night_runs(report)
        key = os.path.normcase(os.path.normpath(proj))
        _check(list(runs) == [key] and len(runs[key]) == 1, "night_runs: only the real run, dry run ignored")
        _check(hb.victim_sids(mem) == {"vlog_open", "vnative"}, "victim_sids from 'Harvested headless from working/dirty-<sid>.json'")
        tindex = hb.transcript_index(native)
        _check(set(tindex) == {"vwrap", "vtrivial"}, "transcript_index: <root>/<project>/<sid>.jsonl, memory/ not a transcript")
        native_sids = hs.native_session_ids(native)

        print("=== classify ===")
        victims = hb.victim_sids(mem)
        want = {
            "legacy1": ("legacy", "legacy"), "dirty1": ("dirty", None), "closed1": ("closed", None),
            "headless1": ("headless", None), "unstamped1": ("unstamped", None),
            "vlog_open": ("victim_open", None), "vnight_open": ("victim_open", None),
            "vnight_edge": ("legacy", "legacy"), "vwrap": ("victim_closed", "wrapup_im_transkript"),
            "vnative": ("victim_closed", "native_note"), "vtrivial": ("victim_open", None),
            "vgone": ("victim_open", None),
        }
        notes = {"vlog_open": "named in the log, no evidence -> stays pending",
                 "vnight_open": "consolidated inside the night run -> victim, pending",
                 "vnight_edge": "consolidated 1801 s after the run start -> not a victim",
                 "vwrap": "victim with a real wrap-up in the transcript found by sid -> closes",
                 "vnative": "victim named by a native note -> closes",
                 "vtrivial": "victim with < 3 edits -> never 'trivial' (old tracker data)",
                 "vgone": "victim whose recorded transcript is gone -> never 'transcript_fehlt'"}
        for sid, exp in want.items():
            with open(os.path.join(mem, "working", f"dirty-{sid}.json"), encoding="utf-8") as fh:
                d = json.load(fh)
            got = hb.classify(d, sid, victims, runs[key], tindex, native_sids)
            _check(got == exp, f"{sid}: {notes.get(sid, exp[0])} (got {got})")

        print("=== dry run writes nothing ===")
        before = _hashes(tmp)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = hb.main(["--root", os.path.join(tmp, "home"), "--report", report, "--native-root", native])
        out = json.loads(buf.getvalue())
        _check(rc == 0 and _hashes(tmp) == before, "dry run: exit 0, every byte unchanged")
        _check(out["apply"] is False and out["totals"].get("closed_now") == 4 and out["totals"].get("closed") == 1,
               f"dry run reports 4 closures (legacy1, vnight_edge, vwrap, vnative): {out['totals']}")
        _check(out["totals"].get("victim_open") == 4 and out["totals"].get("dirty") == 1,
               "dry run counts the classes")

        print("=== apply ===")
        with contextlib.redirect_stdout(io.StringIO()):
            hb.main(["--root", os.path.join(tmp, "home"), "--report", report, "--native-root", native, "--apply"])

        def state(sid):
            with open(os.path.join(mem, "working", f"dirty-{sid}.json"), encoding="utf-8") as fh:
                d = json.load(fh)
            return hs.marker_state(d), (d.get("harvest") or {}).get("closed_by"), (d.get("harvest") or {}).get("run")

        _check(state("legacy1") == ("closed", "legacy", "backfill"), "apply: legacy closure with run 'backfill'")
        _check(state("vwrap") == ("closed", "wrapup_im_transkript", "backfill"), "apply: victim closed by its transcript")
        _check(state("vnative")[:2] == ("closed", "native_note"), "apply: victim closed by its native note")
        _check(all(state(s)[0] == "pending" for s in ("vlog_open", "vnight_open", "vtrivial", "vgone",
                                                    "headless1", "unstamped1")), "apply: open victims and others stay pending")
        _check(state("dirty1")[0] == "dirty" and state("closed1")[1] == "trivial", "apply: dirty and closed untouched")
        after = _hashes(tmp)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            hb.main(["--root", os.path.join(tmp, "home"), "--report", report, "--native-root", native, "--apply"])
        _check(_hashes(tmp) == after and json.loads(buf.getvalue())["totals"].get("closed_now") == 0
               and json.loads(buf.getvalue())["totals"].get("closed") == 5,
               "second apply: nothing left to close (5 counted as closed), every byte unchanged")

    print("=== refused write is reported, not counted ===")
    with tempfile.TemporaryDirectory() as tmp:
        proj, mem, report, native = _fixture(tmp)
        real = hs.close_marker
        hs.close_marker = lambda *a, **k: False  # changed since read / locked
        try:
            res = hb.backfill_store(mem, [], {}, set(), True, "2026-10-12T10:00:00+02:00")
        finally:
            hs.close_marker = real
        _check(res["closed"] == [] and "legacy1" in res["refused"], "close_marker refuses -> listed under refused")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

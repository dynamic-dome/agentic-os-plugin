#!/usr/bin/env python3
"""Tests for scripts/harvest_state.py — the harvest state inside the dirty marker
(Lebenszyklus Phase 2, Teil 2, Scheibe 5; design A+, D-024).

Contract under test:
  - marker_state: dirty work wins; a closure counts only while its snapshot
    (write_count, updated, foreign_updated) still matches; otherwise pending.
  - last_activity: newest of updated / foreign_updated, mtime only as fallback.
  - is_quiet: a session_end block proves the end only when ended_at >= the last
    activity; otherwise 12 h without activity (transcript mtime counts as activity).
  - close_reason: the four E7 rules over gathered facts, each with its guard.
  - gather_facts: a wrap-up counts only as a real invocation (slash command in a
    user message or the Skill tool), never as text inside a tool result.
  - close_marker: writes under the store lock, refuses a changed snapshot, is idempotent.

Every path is a temp path - no test touches a real store or ~/.claude.
Run: python tests/test-harvest-state.py   (exit 0 = pass, 1 = fail)
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "harvest_state.py")
_spec = importlib.util.spec_from_file_location("harvest_state", SCRIPT)
hs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hs)

FAILURES = []


def _check(cond, msg):
    if cond:
        print(f"  PASS: {msg}")
    else:
        print(f"  FAIL: {msg}")
        FAILURES.append(msg)


def _ts(epoch):
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def _marker(**kw):
    d = {"session_id": "s1", "agent": "claude", "dirty": False, "started": "2026-10-01T10:00:00+02:00",
         "updated": "2026-10-01T11:00:00+02:00", "touched_files": ["a.py"], "write_count": 1}
    d.update(kw)
    return d


def _closed(d, by="trivial"):
    d = dict(d)
    d["harvest"] = {"closed_by": by, "closed_at": "2026-10-02T03:00:00+02:00", "run": "nightly",
                    "snap": hs.snapshot(d)}
    return d


def _transcript(path, records):
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def _user(text, ts="2026-10-01T10:00:00Z"):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}}


def _tool(name, inp=None, ts="2026-10-01T10:01:00Z"):
    return {"type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "content": [{"type": "tool_use", "name": name, "input": inp or {}}]}}


def _tool_result(text):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": text}]}}


SLASH = "<command-message>agentic-os:wrap-up</command-message>\n<command-name>/agentic-os:wrap-up</command-name>"


def main():
    print("=== marker_state ===")
    _check(hs.marker_state(_marker(dirty=True)) == "dirty", "dirty marker -> dirty")
    _check(hs.marker_state(_marker(consolidated_by="headless", consolidated_at="x")) == "pending",
           "headless-consolidated, no closure -> pending")
    _check(hs.marker_state(_closed(_marker())) == "closed", "valid closure -> closed")
    m = _closed(_marker())
    m["write_count"] = 2
    _check(hs.marker_state(m) == "pending", "closure + new own write (write_count changed) -> pending")
    m = _closed(_marker())
    m["foreign_updated"] = "2026-10-03T09:00:00+02:00"
    _check(hs.marker_state(m) == "pending", "closure + new foreign write (foreign_updated changed) -> pending")
    m = _closed(_marker(foreign_updated="2026-10-01T11:00:00+02:00", foreign_write_count=1))
    m["foreign_write_count"] = 2  # a second foreign write within the same second: foreign_updated unchanged
    _check(hs.marker_state(m) == "pending", "closure + foreign write in the same second (only the count moved) -> pending")
    m = _closed(_marker())
    m.update(dirty=True, updated="2026-10-03T09:00:00+02:00", write_count=2)
    _check(hs.marker_state(m) == "dirty", "closure + new own work that re-dirtied -> dirty")
    m = _closed(_marker())
    m["dirty"] = True
    _check(hs.marker_state(m) == "dirty", "dirty wins even over a matching snapshot")
    _check(hs.marker_state(_marker(harvest="kaputt")) == "pending", "corrupt harvest field -> pending, never closed")
    m = _marker()
    m["harvest"] = {"snap": hs.snapshot(m)}
    _check(hs.marker_state(m) == "pending", "harvest without closed_by -> pending")

    print("=== is_tail / closable (Codex round 2) ===")
    lc = {"dirty": True, "last_consolidated_at": "2026-10-01T10:30:00+02:00"}
    _check(hs.is_tail(lc) is False, "no writes_since_consolidation recorded -> not a tail (no default 0)")
    _check(hs.is_tail(dict(lc, writes_since_consolidation=2)) is True,
           "writes recorded, no headless stamp -> tail (same rule as session-start.sh)")
    _check(hs.closable(dict(lc, writes_since_consolidation=2)) is False,
           "a dirty tail without an explicit judged wrap-up stamp is not closable")
    _check(hs.closable(dict(lc, writes_since_consolidation=2, last_consolidated_by="wrap-up")) is True,
           "a dirty tail of a judged wrap-up is closable")
    _check(hs.is_tail(dict(lc, writes_since_consolidation="kaputt")) is False, "corrupt counter -> not a tail")

    print("=== last_activity ===")
    a = hs.last_activity(_marker(updated="2026-10-01T11:00:00+02:00", foreign_updated="2026-10-01T12:00:00+02:00"))
    b = hs.last_activity(_marker(updated="2026-10-01T12:00:00+02:00"))
    _check(a is not None and a == b, "foreign_updated newer than updated wins")
    _check(hs.last_activity({"dirty": True}, fallback_mtime=123.0) == 123.0, "no timestamps -> mtime fallback")
    _check(hs.last_activity({"dirty": True}) is None, "no timestamps, no fallback -> None")
    _check(hs.last_activity(_marker(updated="kaputt"), fallback_mtime=5.0) == 5.0,
           "unparseable updated -> mtime fallback")
    naive = hs.last_activity(_marker(updated="2026-10-01T11:00:00"))
    aware = hs.last_activity(_marker(updated=datetime(2026, 10, 1, 11, 0).astimezone().isoformat()))
    _check(naive == aware, "naive timestamp is read as local time")

    print("=== is_quiet ===")
    now = time.time()
    recent = _ts(now - 3600)
    old = _ts(now - 13 * 3600)
    _check(hs.is_quiet(_marker(updated=old), now) is True, "13 h without activity -> quiet")
    _check(hs.is_quiet(_marker(updated=recent), now) is False, "1 h ago -> not quiet")
    _check(hs.is_quiet(_marker(updated=recent, session_end={"ended_at": _ts(now - 1800)}), now) is True,
           "session_end after the last activity proves the end")
    _check(hs.is_quiet(_marker(updated=_ts(now - 1800), session_end={"ended_at": recent}), now) is False,
           "session_end BEFORE the last activity (session continued) proves nothing")
    _check(hs.is_quiet(_marker(updated=recent, foreign_updated=_ts(now - 600),
                               session_end={"ended_at": _ts(now - 1800)}), now) is False,
           "a foreign write after session_end also means the session continued")
    _check(hs.is_quiet(_marker(updated=recent, session_end={"ended_at": _ts(now - 1800)}), now,
                       transcript_mtime=now - 60) is False,
           "transcript written long after session_end (resumed, chat only) proves nothing")
    _check(hs.is_quiet(_marker(updated=recent, session_end={"ended_at": _ts(now - 1800)}), now,
                       transcript_mtime=now - 1790) is True,
           "transcript flushed right after session_end still counts as ended")
    _check(hs.is_quiet(_marker(updated=old), now, transcript_mtime=now - 600) is False,
           "fresh transcript mtime counts as activity")
    _check(hs.is_quiet({"dirty": True}, now) is False, "no activity information -> never quiet")

    print("=== close_reason ===")
    tp = {"transcript_path": "C:/t/s1.jsonl"}
    no_facts = {"transcript_exists": True, "writes_after_wrapup": None, "native_note": False}
    _check(hs.close_reason(_marker(**tp, write_count=9), dict(no_facts, writes_after_wrapup=2)) == "wrapup_im_transkript",
           "wrap-up in transcript, 2 writes after -> wrapup_im_transkript")
    _check(hs.close_reason(_marker(**tp, write_count=9), dict(no_facts, writes_after_wrapup=hs.TAIL_WRITES_MAX + 1)) is None,
           "wrap-up followed by real work (> tail) -> not closed by that rule")
    _check(hs.close_reason(_marker(**tp, write_count=9), dict(no_facts, native_note=True)) == "native_note",
           "native note with originSessionId -> native_note")
    _check(hs.close_reason(_marker(**tp, write_count=2), no_facts) == "trivial", "2 edits with tracker data -> trivial")
    _check(hs.close_reason(_marker(write_count=1), no_facts) is None,
           "trivial needs transcript_path (tracker data), never from an old marker")
    _check(hs.close_reason(_marker(**tp, write_count=1, foreign_touched={"C:/o": ["C:/o/a.py"]}), no_facts) is None,
           "foreign paths without foreign_write_count -> edits unknown, not trivial")
    _check(hs.close_reason(_marker(**tp, write_count=1, foreign_touched={"C:/o": ["C:/o/a.py"]},
                                   foreign_write_count=2), no_facts) is None,
           "1 own + 2 foreign edits = 3 -> not trivial")
    _check(hs.close_reason(_marker(**tp, write_count=0, dirty=False, foreign_touched={"C:/o": ["C:/o/a.py"]},
                                   foreign_write_count=1), no_facts) == "trivial",
           "foreign-only with 1 counted edit -> trivial")
    _check(hs.close_reason(_marker(**tp, write_count=9), dict(no_facts, transcript_exists=False)) == "transcript_fehlt",
           "recorded transcript missing -> transcript_fehlt")
    _check(hs.close_reason(_marker(**tp, write_count=9, agent="codex"), dict(no_facts, transcript_exists=False)) is None,
           "transcript_fehlt never for codex")
    _check(hs.close_reason(_marker(write_count=9), dict(no_facts, transcript_exists=None)) is None,
           "no recorded transcript_path -> never transcript_fehlt")
    _check(hs.close_reason(_marker(**tp, write_count=9), no_facts) is None, "busy session, no rule -> open")

    print("=== gather_facts ===")
    with tempfile.TemporaryDirectory() as root:
        tr = os.path.join(root, "s1.jsonl")
        _transcript(tr, [_user("hallo"), _tool("Edit"), _user(SLASH), _tool("Bash"), _tool("Write"), _tool("Edit")])
        native = os.path.join(root, "projects")
        os.makedirs(os.path.join(native, "p", "memory"))
        f = hs.gather_facts(_marker(transcript_path=tr), native_root=native)
        _check(f["transcript_exists"] is True and f["writes_after_wrapup"] == 2,
               "slash wrap-up found, 2 writes after it (Bash not counted)")
        _check(f["native_note"] is False, "no native note -> False")

        tr5 = os.path.join(root, "s5.jsonl")
        _transcript(tr5, [_user("<command-name>/agentic-os:wrap-up</command-name>\n<command-message>agentic-os:wrap-up</command-message>"),
                          _tool("Edit")])
        _check(hs.gather_facts(_marker(transcript_path=tr5), native_root=native)["writes_after_wrapup"] == 1,
               "older tag order (command-name first) is still a real invocation")

        tr2 = os.path.join(root, "s2.jsonl")
        _transcript(tr2, [_user("x"), _tool("Edit"), _tool("Skill", {"skill": "agentic-os:wrap-up"}), _tool("Edit")])
        _check(hs.gather_facts(_marker(transcript_path=tr2), native_root=native)["writes_after_wrapup"] == 1,
               "Skill-tool wrap-up counts as invocation")

        tr3 = os.path.join(root, "s3.jsonl")
        _transcript(tr3, [_user("x"), _tool("Edit"), _tool_result("log: " + SLASH), _user("text mentions " + "/agentic-os:wrap-up"),
                          _user("so sieht der Aufruf aus: " + SLASH),
                          _user("<command-name>/agentic-os:status</command-name>\nund spaeter: " + SLASH)])
        _check(hs.gather_facts(_marker(transcript_path=tr3), native_root=native)["writes_after_wrapup"] is None,
               "wrap-up text inside a tool result or prose is NOT an invocation")

        tr4 = os.path.join(root, "s4.jsonl")
        with open(tr4, "w", encoding="utf-8") as fh:
            fh.write("{kaputt\n" + json.dumps(_user(SLASH)) + "\n")
        _check(hs.gather_facts(_marker(transcript_path=tr4), native_root=native)["writes_after_wrapup"] == 0,
               "a broken JSONL line is skipped, the rest is read")

        f = hs.gather_facts(_marker(transcript_path=os.path.join(root, "weg.jsonl")), native_root=native)
        _check(f["transcript_exists"] is False and f["writes_after_wrapup"] is None, "missing transcript -> exists False")
        _check(hs.gather_facts(_marker(), native_root=native)["transcript_exists"] is None,
               "no transcript_path -> exists None (unknown, not missing)")

        with open(os.path.join(native, "p", "memory", "n.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: x\nmetadata:\n  originSessionId: s1\n---\nbody\n")
        _check(hs.gather_facts(_marker(), native_root=native)["native_note"] is True, "originSessionId match -> True")
        _check(hs.gather_facts(_marker(session_id="s"), native_root=native)["native_note"] is False,
               "sid prefix is no match (s vs s1)")

    print("=== close_marker ===")
    with tempfile.TemporaryDirectory() as root:
        mem = os.path.join(root, ".agent-memory")
        work = os.path.join(mem, "working")
        os.makedirs(work)
        path = os.path.join(work, "dirty-s1.json")
        m = _marker(dirty=True, write_count=2, transcript_path="C:/t/s1.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(m, fh)
        before = open(path, "rb").read()
        _check(hs.close_marker(mem, "dirty-s1.json", "trivial", run="nightly", expected_snap=hs.snapshot(m)) is False
               and open(path, "rb").read() == before,
               "un-consolidated work (dirty, no wrap-up tail) is never closed - consolidate first")
        tail = _marker(dirty=True, write_count=4, session_id="tail", last_consolidated_at="2026-10-01T10:30:00+02:00",
                       last_consolidated_by="wrap-up", writes_since_consolidation=2)
        with open(os.path.join(work, "dirty-tail.json"), "w", encoding="utf-8") as fh:
            json.dump(tail, fh)
        _check(hs.close_marker(mem, "dirty-tail.json", "wrapup_im_transkript", run="nightly",
                               expected_snap=hs.snapshot(tail)) is True,
               "a judged wrap-up's tail (<= 5 writes after it) may be closed although dirty")
        headless_tail = dict(tail, session_id="htail", last_consolidated_by="headless")
        with open(os.path.join(work, "dirty-htail.json"), "w", encoding="utf-8") as fh:
            json.dump(headless_tail, fh)
        _check(hs.close_marker(mem, "dirty-htail.json", "trivial", run="nightly",
                               expected_snap=hs.snapshot(headless_tail)) is False,
               "writes after a HEADLESS stamp are real work, never a tail")
        m = _marker(dirty=False, write_count=2, transcript_path="C:/t/s1.jsonl", consolidated_by="headless")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(m, fh)
        snap = hs.snapshot(m)
        ok = hs.close_marker(mem, "dirty-s1.json", "trivial", run="nightly", expected_snap=snap)
        d = json.load(open(path, encoding="utf-8"))
        _check(ok is True and d["dirty"] is False and d["harvest"]["closed_by"] == "trivial"
               and d["harvest"]["run"] == "nightly" and d["harvest"]["snap"] == snap,
               "close_marker closes a pending marker with harvest and snapshot")
        _check(hs.marker_state(d) == "closed", "closed marker reads as closed")
        before = open(path, "rb").read()
        _check(hs.close_marker(mem, "dirty-s1.json", "trivial", run="nightly", expected_snap=snap) is False
               and open(path, "rb").read() == before, "closing twice is a no-op (byte-identical)")

        m2 = _marker(dirty=False, write_count=2, session_id="s2", consolidated_by="headless")  # closable: only the snapshot decides
        p2 = os.path.join(work, "dirty-s2.json")
        with open(p2, "w", encoding="utf-8") as fh:
            json.dump(m2, fh)
        stale_snap = hs.snapshot(m2)
        m2["write_count"] = 3  # the session wrote again between gather and close
        with open(p2, "w", encoding="utf-8") as fh:
            json.dump(m2, fh)
        before = open(p2, "rb").read()
        _check(hs.close_marker(mem, "dirty-s2.json", "trivial", run="nightly", expected_snap=stale_snap) is False
               and open(p2, "rb").read() == before, "changed snapshot since gather -> refused, file untouched")
        _check(hs.close_marker(mem, "dirty-weg.json", "trivial", run="nightly", expected_snap={}) is False,
               "missing marker -> False")
        try:
            hs.close_marker(mem, "dirty-s2.json", "geraten", run="nightly", expected_snap=hs.snapshot(m2))
            _check(False, "unknown closed_by must raise")
        except ValueError:
            _check(True, "unknown closed_by raises ValueError")

    print("=== close_pass (Scheibe 6) ===")
    with tempfile.TemporaryDirectory() as root:
        mem = os.path.join(root, ".agent-memory")
        work = os.path.join(mem, "working")
        native = os.path.join(root, "projects")
        os.makedirs(work)
        os.makedirs(os.path.join(native, "p", "memory"))
        now = time.time()

        def put(sid, **kw):
            d = _marker(session_id=sid, **kw)
            with open(os.path.join(work, f"dirty-{sid}.json"), "w", encoding="utf-8") as fh:
                json.dump(d, fh)
            return d

        def load(sid):
            return json.load(open(os.path.join(work, f"dirty-{sid}.json"), encoding="utf-8"))

        tr = os.path.join(root, "w.jsonl")
        _transcript(tr, [_user("x"), _tool("Edit"), _tool("Edit"), _tool("Edit"), _user(SLASH), _tool("Write")])
        old = _ts(now - 13 * 3600)
        put("wrapped", updated=old, write_count=9, consolidated_by="headless", transcript_path=tr)
        put("busy", updated=old, write_count=9, transcript_path=tr.replace("w.jsonl", "none-but-exists.jsonl"))
        open(os.path.join(root, "none-but-exists.jsonl"), "w").close()
        for t in (tr, os.path.join(root, "none-but-exists.jsonl")):  # a fresh transcript would mean "still active"
            os.utime(t, (now - 13 * 3600, now - 13 * 3600))
        put("young", updated=_ts(now - 3600), write_count=1, transcript_path=os.path.join(root, "weg.jsonl"))
        put("ancient", updated=_ts(now - 20 * 86400), write_count=9)
        put("done", updated=old)
        put("dirtywrapped", updated=old, dirty=True, write_count=9, transcript_path=tr)
        done = load("done")
        done["harvest"] = hs.make_closure(done, "trivial", "nightly")
        with open(os.path.join(work, "dirty-done.json"), "w", encoding="utf-8") as fh:
            json.dump(done, fh)
        with open(os.path.join(work, "dirty-kaputt.json"), "w", encoding="utf-8") as fh:
            fh.write("{kaputt")

        before = {n: open(os.path.join(work, n), "rb").read() for n in os.listdir(work)}
        dry = hs.close_pass(mem, run="nightly", now=now, native_root=native, dry=True)
        _check(sorted((c["sid"], c["closed_by"]) for c in dry["closed"]) == [("wrapped", "wrapup_im_transkript")]
               and dry["stale"] == ["ancient"],
               f"dry-run reports what would close / go stale {dry}")
        _check({n: open(os.path.join(work, n), "rb").read() for n in os.listdir(work)} == before,
               "dry-run writes nothing")

        res = hs.close_pass(mem, run="nightly", now=now, native_root=native)
        _check([(c["sid"], c["closed_by"]) for c in res["closed"]] == [("wrapped", "wrapup_im_transkript")]
               and hs.marker_state(load("wrapped")) == "closed", "quiet marker with wrap-up in transcript is closed")
        _check("harvest" not in load("young") and hs.marker_state(load("young")) == "pending",
               "young marker is not evaluated (not quiet), even though its transcript is gone")
        _check(hs.marker_state(load("busy")) == "pending" and "harvest" not in load("busy"),
               "busy quiet marker without a rule stays open")
        _check(res["stale"] == ["ancient"] and isinstance(load("ancient").get("stale_reported"), dict),
               "marker older than 14 days is reported stale and stamped")
        _check(res["unreadable"] == ["dirty-kaputt.json"], "unreadable marker is reported, never touched")
        _check(res["open"] == 4, f"open count = young + busy + ancient + dirtywrapped ({res['open']})")
        _check(hs.marker_state(load("dirtywrapped")) == "dirty" and "harvest" not in load("dirtywrapped"),
               "dirty marker with a wrap-up in its transcript but real work after it stays dirty (H1)")
        again = hs.close_pass(mem, run="nightly", now=now, native_root=native)
        _check(again["stale"] == [] and again["closed"] == [], "second run: stale reported only once, nothing re-closed")
        _check(hs.close_pass(mem, run="nightly", now=now, native_root=native, dry=True)["stale"] == [],
               "a dry run after the stamp does not report the same drift again")
        anc = load("ancient")
        anc.update(write_count=10, updated=_ts(now - 15 * 86400))
        with open(os.path.join(work, "dirty-ancient.json"), "w", encoding="utf-8") as fh:
            json.dump(anc, fh)
        _check(hs.close_pass(mem, run="nightly", now=now, native_root=native)["stale"] == ["ancient"],
               "new work invalidates the stale stamp: reported again when it goes stale again")
        with open(os.path.join(native, "p", "memory", "n.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nmetadata:\n  originSessionId: busy\n---\n")
        res = hs.close_pass(mem, run="nightly", now=now, native_root=native)
        _check([(c["sid"], c["closed_by"]) for c in res["closed"]] == [("busy", "native_note")],
               "native note closes the busy session (index built once per pass)")

    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S)")
        return 1
    print("\nALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

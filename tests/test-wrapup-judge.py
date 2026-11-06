#!/usr/bin/env python3
"""Tests for the judge path of the wrap-up core (T-028b): wrapup_core.py pre / apply / followup.

Every case runs against throwaway dirs - a fake project (git repo + .agent-memory), a fake central
dir and a fake wiki root. Subprocesses run with a fresh temp home (tests/_isolated_home.py), so the
real stores, the real central handoff and the real wiki are never touched.

Exit codes: 0 = all pass, 1 = failures found.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")  # the results quote "→" (Windows console: cp1252)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolated_home import process_env  # noqa: E402

ENV, ISO_HOME = process_env()
ENV.pop("CLAUDE_CODE_SESSION_ID", None)
GIT = shutil.which("git")
CORE = os.path.join(ROOT, "scripts", "wrapup_core.py")
SID = "abcdef12-3456-7890-abcd-ef1234567890"
PARENT = tempfile.mkdtemp(prefix="wrapupjudge-")

TESTS = PASSED = ERRORS = 0


def check(cond, msg):
    global TESTS, PASSED, ERRORS
    TESTS += 1
    if cond:
        PASSED += 1
        print(f"  PASS: {msg}")
    else:
        ERRORS += 1
        print(f"  FAIL: {msg}")


def put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def read(path):
    return open(path, encoding="utf-8").read() if os.path.exists(path) else ""


def git(repo, *args):
    subprocess.run([GIT, "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True, text=True, encoding="utf-8", env=ENV)


def setup(learnings="[]", old_commits=False):
    base = tempfile.mkdtemp(dir=PARENT)
    proj = os.path.join(base, "proj")
    os.makedirs(proj)
    git(proj, "init", "-q")
    for name, msg in (("a.txt", "feat: add a"), ("b.txt", "fix(core): repair b")):
        put(os.path.join(proj, name), name)
        git(proj, "add", name)
        if old_commits:
            stamp = {"GIT_AUTHOR_DATE": "2001-01-01T12:00:00", "GIT_COMMITTER_DATE": "2001-01-01T12:00:00"}
            subprocess.run([GIT, "-C", proj, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg],
                           check=True, capture_output=True, env={**ENV, **stamp})
        else:
            git(proj, "commit", "-q", "-m", msg)
    mem = os.path.join(proj, ".agent-memory")
    wiki = os.path.join(base, "wiki")
    central = os.path.join(base, "central")
    home = os.path.join(base, "home")
    for d in (os.path.join(wiki, "wiki", "queries"), os.path.join(central, ".agent-memory"), home):
        os.makedirs(d)
    put(os.path.join(wiki, "index.md"), "# Index\n\n## Queries\n")
    put(os.path.join(wiki, "log.md"), "# Log\n")
    put(os.path.join(mem, "config.json"), json.dumps({"project_id": "projA", "wiki_root": wiki, "sync_enabled": True,
                                                       "session_note_threshold": 1}))
    put(os.path.join(mem, "context", "open-tasks.json"), json.dumps([
        {"id": "T-1", "title": "offene Aufgabe", "status": "open"},
        {"id": "T-2", "title": "gemeinsam", "status": "open", "cross_project": True}]))
    put(os.path.join(mem, "context", "decisions.json"), "[]")
    put(os.path.join(mem, "learnings", "learnings.json"), learnings)
    put(os.path.join(mem, "identity", "user.md"), "# User\n\n## Preferences\n- (Will be populated)\n")
    put(os.path.join(mem, "iterations", "errors.json"), "[]")
    put(os.path.join(mem, "working", f"dirty-{SID}.json"), json.dumps({
        "session_id": SID, "agent": "claude-code", "dirty": True, "started": "2000-01-01T00:00:00+00:00",
        "updated": "2000-01-01T00:00:00+00:00", "touched_files": [os.path.join(proj, "c.txt")], "write_count": 1}))
    return {"base": base, "proj": proj, "mem": mem, "wiki": wiki, "central": central, "home": home}


def run(env, cmd, plan=None, *extra, raw=None):
    args = [sys.executable, CORE, cmd, "--mem", env["mem"], "--session-id", SID, *extra]
    if cmd != "pre":
        args += ["--central-dir", env["central"], "--home", env["home"]]
    data = raw if raw is not None else (json.dumps(plan, ensure_ascii=False) if plan is not None else "")
    p = subprocess.run(args, input=data.encode("utf-8"), capture_output=True, timeout=180, env=ENV)
    text = p.stdout.decode("utf-8", "replace")
    try:
        out = json.loads(text)
    except ValueError:
        out = {"_raw": text[-800:], "_err": p.stderr.decode("utf-8", "replace")[-800:]}
    return p.returncode, out


def snapshot(*roots):
    seen = {}
    for root in roots:
        for d, _, files in os.walk(root):
            if os.sep + ".git" in d:
                continue
            for f in files:
                if f == "store.lock":  # the lock file itself: empty, created by whoever waits for the lock
                    continue
                path = os.path.join(d, f)
                with open(path, "rb") as fh:
                    seen[path] = (hashlib.sha256(fh.read()).hexdigest(), os.path.getmtime(path))
    return seen


def plan(**over):
    base = {"summary": {"what_was_done": ["Core gebaut", "Tests geschrieben"], "active_task": "T-1",
                        "current_state": "Branch grün", "blockers": "keine",
                        "checks": {"tests": "bestanden", "lint": "n/a"}},
            "identity": {"candidates": [], "none_because": "Keine Signale in dieser Sitzung."}}
    base.update(over)
    return base


def learning(text, importance=3, **extra):
    return {"text": text, "importance": importance, "tags": ["t1", "t2"], "derived_from": [], **extra}


def test_pre():
    print("=== pre ===")
    env = setup(json.dumps([{"id": "L1", "date": "2026-01-01", "text": "Alter Kandidat mit Text", "importance": 4,
                             "tags": [], "layer": "long-term", "bridge_status": "candidate"}]))
    try:
        put(os.path.join(env["mem"], "working", "user-candidates.json"), json.dumps([
            {"id": "UC1", "key": "vorhandener-schluessel", "observation": "Mag kurze Antworten", "status": "observed",
             "occurrences": 1, "confidence": 0.5, "first_seen": "2026-01-01", "last_seen": "2026-01-01",
             "trust_source": "conversation", "sessions": ["alt"]}]))
        put(os.path.join(env["mem"], "session-summary.md"),
            "# Last Session\n\n## What Was Done\n\n- x\n\n## Active Warnings\n\n- Gilt noch\n- Auch das\n\n## Handoff Context\n\n- y\n")
        before = snapshot(env["mem"], env["central"], env["wiki"])
        rc, out = run(env, "pre")
        check(rc == 0 and out.get("ok"), f"pre exits 0 {rc} {str(out)[:300]}")
        check(snapshot(env["mem"], env["central"], env["wiki"]) == before, "pre writes nothing (hash and mtime of every file)")
        check(out.get("session_id") == SID, "brief carries the session id")
        titles = [i.get("title") for i in out.get("iterations_harvested", [])]
        check("add a" in titles and "repair b" in titles, f"brief lists the harvested iterations {titles}")
        check([t.get("id") for t in out.get("open_tasks", [])] == ["T-1", "T-2"], "brief lists the open tasks")
        check([c.get("id") for c in out.get("bridge_candidates", [])] == ["L1"], "brief lists the open bridge candidates")
        check([c.get("key") for c in out.get("user_candidates", [])] == ["vorhandener-schluessel"],
              f"brief lists the existing identity keys {out.get('user_candidates')}")
        check(out.get("active_warnings") == ["Gilt noch", "Auch das"], f"brief shows the active warnings {out.get('active_warnings')}")
        check("rule_class_open" in out and "validation_errors" in out, "brief carries rule queue and validation errors")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)
    env = setup("{kaputt")
    try:
        rc, out = run(env, "pre")
        check(rc == 0 and out.get("ok") and any("learnings" in str(e) for e in out.get("validation_errors", [])),
              f"unreadable store is reported, not a crash {str(out)[:300]}")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)


def test_rejected_plans():
    print("=== rejected plans write nothing ===")
    env = setup()
    try:
        before = snapshot(env["mem"], env["central"], env["wiki"])
        bad = [("no identity", {"summary": plan()["summary"]}),
               ("identity without reason", plan(identity={"candidates": [], "none_because": "   "})),
               ("identity not an object", plan(identity=[])),
               ("no summary", {"identity": plan()["identity"]}),
               ("summary without work", plan(summary={"what_was_done": []})),
               ("session_summary is the core's", plan(session_summary={"what_was_done": ["x"]})),
               ("user_candidates belong under identity", plan(user_candidates=[]))]
        for name, p in bad:
            rc, out = run(env, "apply", p)
            check(rc == 2 and out.get("ok") is False, f"{name}: exit 2 {rc} {str(out)[:200]}")
            check(snapshot(env["mem"], env["central"], env["wiki"]) == before, f"{name}: nothing written")
        rc, out = run(env, "apply", raw="kein json")
        check(rc == 2 and out.get("ok") is False, f"unreadable plan: exit 2 {rc}")
        rc, out = run(env, "apply", raw="[]")
        check(rc == 2 and out.get("ok") is False, f"plan that is no object: exit 2 {rc}")
        rc, out = run(env, "apply", plan(decisions=[{"title": "x", "supersedes": "D-999"}]))
        check(rc == 2 and out.get("marker_written") is False, f"plan the applier refuses: exit 2, no marker {rc}")
        check(not os.path.exists(os.path.join(env["mem"], "consolidation-marker.json")), "refused plan: marker absent")
        check(json.loads(read(os.path.join(env["mem"], "working", f"dirty-{SID}.json"))).get("dirty") is True,
              "refused plan: dirty flag stays")
        check(snapshot(env["mem"], env["central"], env["wiki"]) == before, "refused plan: nothing written")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)


def test_apply():
    print("=== apply ===")
    env = setup()
    try:
        before = snapshot(env["mem"], env["central"], env["wiki"])
        rc, out = run(env, "apply", plan(), "--dry-run")
        check(rc == 0 and out.get("dry_run") is True, f"dry run exits 0 {rc} {str(out)[:300]}")
        check(snapshot(env["mem"], env["central"], env["wiki"]) == before, "dry run writes nothing")

        p = plan(consolidate=False, open_tasks={"add": [{"title": "neue Aufgabe", "source": "wrap-up"}]},
                 identity={"candidates": [{"key": "mag-kurze-antworten", "observation": "Will kurze Antworten",
                                           "signal_type": "preference", "confidence": 0.5, "evidence": [],
                                           "trust_source": "conversation"}], "none_because": ""})
        rc, out = run(env, "apply", p)
        mem = env["mem"]
        check(rc == 0 and out.get("ok") and out.get("mode") == "wrap-up", f"apply exits 0 {rc} {str(out)[:500]}")
        check(out.get("harvest_used") is True, "no iterations in the plan -> harvest used")
        log = read(os.path.join(mem, "iterations", "iteration-log.md"))
        check("add a" in log and "headless" not in log, "harvested iterations are not marked headless")
        summ = read(os.path.join(mem, "session-summary.md"))
        check("Core gebaut" in summ and "offene Aufgabe" in summ, "summary: judge lines plus open tasks from the store")
        check("neue Aufgabe" in summ, "summary sees the tasks this plan added")
        check("Iterations: 3" in summ, f"statistics measured, not typed {[l for l in summ.splitlines() if 'Iterations' in l]}")
        queue = read(os.path.join(mem, "working", "user-candidates.json"))
        check("mag-kurze-antworten" in queue, "identity candidates reach the queue")
        hand = read(os.path.join(env["central"], ".agent-memory", "session-summary.md"))
        check(hand.startswith("# Letzte Session") and "*Agent: Claude Code*" in hand and "*Projekt: projA*" in hand,
              "central handoff block written in the template")
        check("Core gebaut" in hand and "Branch grün" in hand and "Tests: bestanden" in hand and "Blocker: keine" in hand,
              "handoff carries the judge's lines")
        check("[cross-project] gemeinsam" in hand and "Projekt-Next-Steps:" in hand,
              "next steps: one pointer plus cross-project items only")
        check("headless" not in hand, "handoff is not marked headless")
        check("## projA" in read(os.path.join(env["central"], "cross-project-status.md")), "status board updated")
        note = os.path.join(env["wiki"], "wiki", "queries", f"{out.get('date')}-session-projA-{SID[:8]}.md")
        text = read(note)
        check("Core gebaut" in text and "headless" not in text, f"session note per session, judge lines {note}")
        check(f"{out.get('date')}-session-projA-{SID[:8]}" in read(os.path.join(env["wiki"], "index.md")), "wiki index line")
        check(str(out.get("wiki_sync_line", "")).startswith("Wiki-Sync: Note geschrieben"), f"visible wiki line {out.get('wiki_sync_line')}")
        marker = json.loads(read(os.path.join(mem, "consolidation-marker.json")) or "{}")
        check(marker.get("mode") == "wrap-up" and out.get("marker_written") is True, f"marker says wrap-up {marker}")
        dirty = json.loads(read(os.path.join(mem, "working", f"dirty-{SID}.json")))
        check(dirty.get("dirty") is False and dirty.get("consolidated_by") == "wrap-up", "dirty flag reset by wrap-up")
        check(str(out.get("identity_status_line", "")).startswith("Identity: 1 beobachtet"), f"identity line measured {out.get('identity_status_line')}")
        check("projections" in out and "reports" in out and "handoff" in out, "result carries the report data")
        check(out.get("reports", {}).get("state_hash") == "ok", "state hash refreshed")
        check(os.path.exists(os.path.join(mem, "working", "state-hash")), "state hash file exists")
        check(out.get("reports", {}).get("cost_trace") == "estimate", f"no transcript: the trace gets an estimate {out.get('reports', {}).get('cost_trace')}")
        check('"estimate": true' in read(os.path.join(mem, "metrics", "cost-trace.jsonl")).replace('"estimate":true', '"estimate": true'),
              "the estimate is marked as one")
        written = [os.path.join(env["central"], ".agent-memory", "session-summary.md"), note,
                   os.path.join(mem, "session-summary.md"), os.path.join(mem, "iterations", "iteration-log.md")]
        marker_time = os.path.getmtime(os.path.join(mem, "consolidation-marker.json"))
        check(all(os.path.getmtime(f) <= marker_time for f in written), "the marker is written after handoff, note and store")

        store = ("iterations/iteration-log.md", "learnings/learnings.json", "context/decisions.json",
                 "context/open-tasks.json", "working/user-candidates.json")
        first = {r: read(os.path.join(mem, *r.split("/"))) for r in store}
        rc, out2 = run(env, "apply", p)
        check(rc == 0, f"second run exits 0 {rc}")
        same = [r for r in store if read(os.path.join(mem, *r.split("/"))) == first[r]]
        check(len(same) == len(store), f"same plan twice leaves the store unchanged {sorted(set(store) - set(same))}")
        hand2 = read(os.path.join(env["central"], ".agent-memory", "session-summary.md"))
        check(hand2.count("*Projekt: projA*") == 1, "handoff keeps one block per project")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)


def test_plan_iterations_and_cap():
    print("=== plan iterations, learnings cap ===")
    env = setup()
    try:
        ls = [learning(f"Learning Nummer {w} mit eigenem Inhalt", i) for w, i in
              (("eins", 3), ("zwei", 5), ("drei", 3), ("vier", 4), ("fuenf", 4))]
        ls.append(learning("Regel: Goals nie am Zeilenumbruch abschneiden, das aendert die Verfassung", 3, rule_class=True))
        its = [{"type": "feature", "title": "Eigene Iteration", "tags": ["a", "b"], "files_changed": [], "summary": "s",
                "errors": [{"category": "runtime", "tags": ["a", "b"], "trigger": "t", "problem": "p", "root_cause": "r",
                            "fix": "f", "severity": "minor", "attempts": 1, "confidence": 3}]}]
        rc, out = run(env, "apply", plan(iterations=its, learnings=ls))
        check(rc == 0 and out.get("harvest_used") is False, f"plan iterations win over the harvest {rc} {str(out)[:400]}")
        log = read(os.path.join(env["mem"], "iterations", "iteration-log.md"))
        check("Eigene Iteration" in log and "add a" not in log, "only the judge's iteration is logged")
        rows = json.loads(read(os.path.join(env["mem"], "learnings", "learnings.json")))
        texts = " ".join(r.get("text", "") for r in rows)
        check(out.get("learnings_dropped_by_cap") == 2, f"cap drops two {out.get('learnings_dropped_by_cap')}")
        check("zwei" in texts and "vier" in texts and "fuenf" in texts, "highest importance stays")
        check("Nummer eins" not in texts and "Nummer drei" not in texts, "lowest importance goes")
        check("Regel: Goals" in texts, "a rule-class learning never falls to the cap")
        check(any("cap" in str(w).lower() for w in out.get("warnings", [])), f"cap is warned about {out.get('warnings')}")
        summ = read(os.path.join(env["mem"], "session-summary.md"))
        check("Iterations: 1 | Errors: 1" in summ, f"statistics counted from the plan {[l for l in summ.splitlines() if 'Iterations' in l]}")
        check([c.get("id") for c in out.get("bridge_candidates", [])] != [], "new candidates reported for the followup")
        check("needs_explanation" in str(out.get("rule_class_open")), "rule queue reported")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)


def test_failsoft_and_guards():
    print("=== fail-soft and guards ===")
    env = setup()
    try:  # wiki fails: the store is consolidated, the marker says so
        os.remove(os.path.join(env["wiki"], "index.md"))
        os.makedirs(os.path.join(env["wiki"], "index.md"))
        rc, out = run(env, "apply", plan())
        check(rc == 0 and str(out.get("wiki_note", "")).startswith("failed("), f"wiki failure is fail-soft {rc} {out.get('wiki_note')}")
        check(str(out.get("wiki_sync_line", "")).startswith("Wiki-Sync fehlgeschlagen"), f"wiki failure is visible {out.get('wiki_sync_line')}")
        check(out.get("marker_written") is True, "marker written despite the wiki failure")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)
    env = setup("{kaputt")
    try:  # store failure: no marker, dirty stays, file untouched
        rc, out = run(env, "apply", plan(learnings=[learning("Ein neuer Inhalt fuer den kaputten Store", 4)]))
        check(rc == 2 and out.get("marker_written") is False, f"store failure: exit 2 {rc} {str(out)[:300]}")
        check(read(os.path.join(env["mem"], "learnings", "learnings.json")) == "{kaputt", "broken file untouched")
        check(not os.path.exists(os.path.join(env["mem"], "consolidation-marker.json")), "store failure: no marker")
        check(json.loads(read(os.path.join(env["mem"], "working", f"dirty-{SID}.json"))).get("dirty") is True, "store failure: dirty stays")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)
    env = setup()
    try:  # path guard: the central dir may not lie inside the store
        args = [sys.executable, CORE, "apply", "--mem", env["mem"], "--session-id", SID, "--home", env["home"],
                "--central-dir", os.path.join(env["mem"], "inner")]
        p = subprocess.run(args, input=json.dumps(plan()).encode("utf-8"), capture_output=True, timeout=180, env=ENV)
        out = json.loads(p.stdout.decode("utf-8"))
        check(out.get("handoff", {}).get("central") == "failed(path guard)", f"path guard {out.get('handoff')}")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)
    env = setup()
    try:  # the store whose summary IS the central handoff is never overwritten
        cen_mem = os.path.join(env["central"], ".agent-memory")
        shutil.rmtree(cen_mem)
        shutil.move(env["mem"], cen_mem)
        put(os.path.join(cen_mem, "session-summary.md"), "# Letzte Session\n\n*Datum: 2026-01-01 10:00*\n*Agent: Claude Code*\n*Projekt: fremd*\n\n## Was wurde gemacht\n- alt\n")
        env2 = {**env, "mem": cen_mem}
        rc, out = run(env2, "apply", plan(), "--project-root", env["proj"])
        text = read(os.path.join(cen_mem, "session-summary.md"))
        check(rc == 0 and "fremd" in text and "Core gebaut" in text, f"central store: block prepended, foreign block kept {rc} {str(out)[:300]}")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)


def test_review_round_1():
    print("=== review round 1 ===")
    env = setup()
    try:  # F-03: a file that is not UTF-8 fails its step, the store still gets its marker
        with open(os.path.join(env["wiki"], "index.md"), "wb") as fh:
            fh.write(b"# Index\xff\n")
        rc, out = run(env, "apply", plan())
        check(rc == 0 and str(out.get("wiki_note", "")).startswith("failed("), f"index.md not UTF-8: fail-soft {rc} {out.get('wiki_note')}")
        check(out.get("marker_written") is True, "index.md not UTF-8: marker written")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)
    env = setup()
    try:
        with open(os.path.join(env["central"], ".agent-memory", "session-summary.md"), "wb") as fh:
            fh.write(b"# Letzte Session\xff\n")
        rc, out = run(env, "apply", plan())
        check(rc == 0 and str(out.get("handoff", {}).get("central", "")).startswith("failed("), f"handoff not UTF-8: fail-soft {rc} {str(out)[:300]}")
        check(out.get("marker_written") is True, "handoff not UTF-8: marker written")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)
    env = setup()
    try:  # the status board is not UTF-8: its step fails, and so does the rescue of a block pushed out by cap 5
        with open(os.path.join(env["central"], "cross-project-status.md"), "wb") as fh:
            fh.write(b"# Board\xff\n")
        heads = ["# Letzte Session"] + [f"# Vorherige Session (2026-01-0{n} 10:00 P{n}, erhalten)" for n in range(1, 5)]
        blocks = [f"{h}\n\n*Datum: 2026-01-0{n + 1} 10:00*\n*Agent: Claude Code*\n*Projekt: P{n}*\n\n## Was wurde gemacht\n- Arbeit {n}\n"
                  for n, h in enumerate(heads)]
        put(os.path.join(env["central"], ".agent-memory", "session-summary.md"), "\n---\n\n".join(blocks))
        rc, out = run(env, "apply", plan())
        hand = out.get("handoff", {})
        check(rc == 0 and str(hand.get("board", "")).startswith("failed("), f"board not UTF-8: fail-soft {rc} {str(out)[:300]}")
        check("failed(" in str(hand.get("rescued", {}).get("status")), f"rescue into that board: fail-soft {hand.get('rescued')}")
        check(out.get("marker_written") is True, "board not UTF-8: marker written")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)
    env = setup(json.dumps([{"id": "L1", "date": "2026-01-01", "text": "Alter Kandidat mit Text", "importance": 4,
                             "tags": [], "layer": "long-term", "bridge_status": "candidate"}]))
    try:
        s = plan()["summary"]
        s["handoff"] = {"active_task": "Aufgabe H", "current_state": "Stand H", "active_patterns": "Muster H", "open_questions": "Frage H"}
        p = plan(summary=s, bridge_decisions=[{"id": "L1", "status": "rejected", "note": "zu speziell", "plain": "Bleibt draußen."}])
        rc, out = run(env, "apply", p)
        summ = read(os.path.join(env["mem"], "session-summary.md"))
        check(rc == 0 and "## Handoff Context" in summ and "Frage H" in summ, f"handoff mode reaches the summary {rc}")
        check(str(out.get("bridge_status_line", "")).startswith("Bridge: 0 approved, 1 rejected"), f"bridge line of the main plan is reported {out.get('bridge_status_line')}")
        check(out.get("repo", {}).get("memory_uncommitted") == "ja" and out.get("repo", {}).get("uncommitted") == "nein",
              f"memory-only changes have their own flag {out.get('repo')}")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)
    env = setup()
    try:  # F-06: in a linked worktree .git is a file
        tree = os.path.join(env["base"], "tree")
        git(env["proj"], "worktree", "add", "-q", "-b", "nebenast", tree)
        put(os.path.join(tree, "a.txt"), "geändert")
        mem2 = os.path.join(tree, ".agent-memory")
        shutil.copytree(env["mem"], mem2)
        rc, out = run({**env, "mem": mem2}, "apply", plan())
        check(rc == 0 and out.get("repo", {}).get("branch") == "nebenast" and out.get("repo", {}).get("uncommitted") == "ja",
              f"worktree: branch and changes are seen {rc} {out.get('repo')}")
        titles = [i.get("title") for i in run({**env, "mem": mem2}, "pre")[1].get("iterations_harvested", [])]
        log = read(os.path.join(mem2, "iterations", "iteration-log.md"))
        check("add a" in log, f"worktree: the harvest sees the commits {titles}")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)


def test_summary_limit():
    print("=== session-summary.md stays within 30 lines (DCO #10330) ===")
    env = setup()
    try:
        tasks = [{"id": f"T-{n}", "title": f"Aufgabe {n}", "status": "open"} for n in range(1, 9)]
        put(os.path.join(env["mem"], "context", "open-tasks.json"), json.dumps(tasks))
        s = plan()["summary"]
        s["what_was_done"] = [f"Arbeit {n}" for n in range(1, 9)]
        s["warnings"] = [f"Warnung {n}" for n in range(1, 6)]
        rc, out = run(env, "apply", plan(summary=s))
        summ = read(os.path.join(env["mem"], "session-summary.md"))
        check(rc == 0 and len(summ.splitlines()) <= 30, f"full plan fits {rc} {len(summ.splitlines())} lines")
        check(not any("lines (contract" in str(w) for w in out.get("warnings", [])), f"no contract warning {out.get('warnings')}")
        check("Arbeit 1" in summ and "Warnung 1" in summ and "Aufgabe 1" in summ, "every section keeps its first lines")
        check("Projekt-Next-Steps" in summ and "8 offen" in summ, "the pointer still names all open tasks")
        hand = read(os.path.join(env["central"], ".agent-memory", "session-summary.md"))
        check("Arbeit 8" in hand, "the handoff block keeps all eight lines")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)
    env = setup()
    try:  # a small plan loses nothing
        s = plan()["summary"]
        s["warnings"] = ["Einzige Warnung"]
        rc, out = run(env, "apply", plan(summary=s))
        summ = read(os.path.join(env["mem"], "session-summary.md"))
        check("Core gebaut" in summ and "Tests geschrieben" in summ and "Einzige Warnung" in summ and "gemeinsam" in summ,
              "a small plan is not cut")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)


def test_wiki_gate():
    print("=== wiki gate (obsidian-sync's substantiality rule) ===")
    one = [{"type": "fix", "title": "Einzige Iteration", "tags": ["a", "b"], "files_changed": [], "summary": "s"}]
    env = setup()
    try:
        cfg = json.loads(read(os.path.join(env["mem"], "config.json")))
        cfg["session_note_threshold"] = 5  # the night run's threshold must not hide a judged session
        put(os.path.join(env["mem"], "config.json"), json.dumps(cfg))
        rc, out = run(env, "apply", plan(iterations=one))
        check(rc == 0 and str(out.get("wiki_note", "")).startswith("wiki/queries/"), f"one iteration warrants a note {out.get('wiki_note')}")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)
    env = setup(old_commits=True)
    try:
        rc, out = run(env, "apply", plan(iterations=[]))
        check(rc == 0 and out.get("wiki_note") == "skipped(Session nicht substanziell)", f"nothing happened: no note {out.get('wiki_note')}")
        check("nicht substanziell" in str(out.get("wiki_sync_line")), f"and the skip is visible {out.get('wiki_sync_line')}")
        check(out.get("marker_written") is True, "a skipped note still consolidates")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)
    env = setup(old_commits=True)
    try:
        rc, out = run(env, "apply", plan(iterations=[], learnings=[learning("Eine echte Erkenntnis ohne Iteration in dieser Sitzung", 4)]))
        check(rc == 0 and str(out.get("wiki_note", "")).startswith("wiki/queries/"), f"a learning alone warrants a note {out.get('wiki_note')}")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)
    env = setup(old_commits=True)
    try:  # logged mid-session with /agentic-os:log, so the plan sends no iterations
        today = datetime.date.today().isoformat()
        put(os.path.join(env["mem"], "iterations", "iteration-log.md"), f"# Log\n\n## {today} — feature: Schon geloggt\n- **Type:** feature\n")
        rc, out = run(env, "apply", plan(iterations=[]))
        check(rc == 0 and str(out.get("wiki_note", "")).startswith("wiki/queries/"), f"an iteration logged today warrants a note {out.get('wiki_note')}")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)
    env = setup()
    try:  # commits today, no iteration in the plan
        rc, out = run(env, "apply", plan(iterations=[]))
        check(rc == 0 and str(out.get("wiki_note", "")).startswith("wiki/queries/"), f"a commit today warrants a note {out.get('wiki_note')}")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)


def test_followup():
    print("=== followup ===")
    env = setup()
    put(os.path.join(env["proj"], "AGENTS.md"), "# Agents\n")
    try:
        rc, out = run(env, "apply", plan(learnings=[learning("Unter Windows Hook-stdin als Bytes lesen und selbst dekodieren", 4)]))
        ids = [c.get("id") for c in out.get("bridge_candidates", [])]
        check(rc == 0 and len(ids) == 1, f"new learning is an open candidate {ids}")
        rc, out = run(env, "followup", {"bridge_decisions": [{"id": ids[0], "status": "approved", "note": "gilt immer",
                                                               "plain": "Hook-Eingaben werden sicher gelesen."}]})
        check(rc == 0 and out.get("ok"), f"followup exits 0 {rc} {str(out)[:400]}")
        check(str(out.get("bridge_status_line", "")).startswith("Bridge: 1 approved"), f"bridge line {out.get('bridge_status_line')}")
        rows = json.loads(read(os.path.join(env["mem"], "learnings", "learnings.json")))
        check(rows[0].get("bridge_status") == "approved", "verdict stored")
        check(out.get("projections", {}).get("bridge") == "ok" and out.get("projections", {}).get("index") == "ok",
              f"projections rerun {out.get('projections')}")
        check("Hook-stdin" in read(os.path.join(env["proj"], "AGENTS.md")), "approved learning reaches AGENTS.md")
        before = snapshot(env["mem"])
        for name, p in (("iterations", {"iterations": []}), ("consolidate", {"consolidate": True}),
                        ("learnings", {"learnings": [learning("Nicht ueber den Nachlauf", 3)]})):
            rc, out = run(env, "followup", p)
            check(rc == 2 and out.get("ok") is False, f"followup refuses {name}")
        check(snapshot(env["mem"]) == before, "refused followup: nothing written")
    finally:
        shutil.rmtree(env["base"], ignore_errors=True)


def main():
    if not GIT:
        print("SKIPPED: git not found")
        return 0
    try:
        test_pre()
        test_rejected_plans()
        test_apply()
        test_plan_iterations_and_cap()
        test_failsoft_and_guards()
        test_review_round_1()
        test_summary_limit()
        test_wiki_gate()
        test_followup()
    finally:
        shutil.rmtree(PARENT, ignore_errors=True)
    print(f"\n{PASSED}/{TESTS} passed, {ERRORS} failed")
    return 1 if ERRORS else 0


if __name__ == "__main__":
    sys.exit(main())

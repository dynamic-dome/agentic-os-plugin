#!/usr/bin/env python3
"""Tests for the headless wrap-up core (T-028): scripts/wrapup_parts/* and scripts/wrapup_core.py.

Every case runs against throwaway dirs under tempfile.mkdtemp() - a fake project (git repo +
.agent-memory), a fake central dir (~/AI stand-in) and a fake wiki root. The real stores,
the real central handoff and the real wiki are never touched.

Exit codes: 0 = all pass, 1 = failures found.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
GIT = shutil.which("git")

TESTS = PASSED = ERRORS = 0


def pass_(msg):
    global TESTS, PASSED
    TESTS += 1
    PASSED += 1
    print(f"  PASS: {msg}")


def fail(msg):
    global TESTS, ERRORS
    TESTS += 1
    ERRORS += 1
    print(f"  FAIL: {msg}")


def check(cond, msg):
    pass_(msg) if cond else fail(msg)


def put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def git(repo, *args):
    subprocess.run([GIT, "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True, text=True, encoding="utf-8")


def make_project(dirty_started="2000-01-01T00:00:00+00:00", touched=None):
    """Fake project: git repo with 3 commits + .agent-memory with one dirty session file."""
    proj = tempfile.mkdtemp(prefix="wrapupcore-proj-")
    git(proj, "init", "-q")
    for name, msg in (("a.txt", "feat: add a"), ("b.txt", "fix(core): repair b"), ("README.md", "update readme")):
        put(os.path.join(proj, name), name)
        git(proj, "add", name)
        git(proj, "commit", "-q", "-m", msg)
    mem = os.path.join(proj, ".agent-memory")
    for d in ("working", "iterations", "context", "learnings", "identity"):
        os.makedirs(os.path.join(mem, d), exist_ok=True)
    if touched is None:
        touched = [os.path.join(proj, "c.txt"), os.path.join(proj, "a.txt"),
                   os.path.join(proj, ".agent-memory", "context", "x.json")]
    put(os.path.join(mem, "working", "dirty-s1.json"), json.dumps({
        "session_id": "s1", "agent": "claude-code", "dirty": True, "started": dirty_started,
        "updated": dirty_started, "touched_files": touched, "write_count": 3}))
    return proj, mem


def test_harvest():
    print("=== harvest (Ticket A) ===")
    from wrapup_parts.harvest import harvest
    proj, mem = make_project()
    try:
        its = harvest(proj, mem, "2000-01-01T00:00:00+00:00")
        by_title = {i["title"]: i for i in its}
        check({"add a", "repair b", "update readme"} <= set(by_title), f"commit iterations {sorted(by_title)}")
        check(by_title.get("add a", {}).get("type") == "feature", "feat -> feature")
        check(by_title.get("repair b", {}).get("type") == "fix", "fix(scope) -> fix, scope stripped from title")
        check(by_title.get("update readme", {}).get("type") == "feature", "no prefix -> feature")
        check(by_title.get("add a", {}).get("files_changed") == ["a.txt"], "files_changed relative")
        check(all(len(i.get("tags") or []) >= 2 and "headless" in i["tags"] for i in its), "tags >= 2 incl. headless")
        unc = [i for i in its if i.get("recovered_from") == "s1"]
        check(len(unc) == 1 and unc[0]["files_changed"] == ["c.txt"],
              f"uncommitted iteration only for c.txt (not a.txt, not .agent-memory) {unc}")
        check(unc and unc[0]["type"] == "config", "uncommitted -> type config")
        # 2099, not 2999: git ignores --since dates beyond its range and would return everything
        future = harvest(proj, mem, "2099-01-01T00:00:00+00:00")
        check([i.get("recovered_from") for i in future] == ["s1"], "since in the future -> only the dirty iteration")
        for n in range(10):  # busy repo (real DCO: 145 commits in 6 days) -> one iteration per type
            put(os.path.join(proj, f"f{n}.txt"), str(n))
            git(proj, "add", f"f{n}.txt")
            git(proj, "commit", "-q", "-m", f"fix: bug {n}")
        many = harvest(proj, mem, "2000-01-01T00:00:00+00:00")
        commit_its = [i for i in many if not i.get("recovered_from")]
        by_type = {i["type"]: i for i in commit_its}
        check(len(commit_its) == 2 and set(by_type) == {"fix", "feature"},
              f"> 8 commits grouped by type {[i['title'] for i in commit_its]}")
        check(by_type.get("fix", {}).get("title", "").startswith("11 fix-Commits")
              and "bug 9" in by_type["fix"].get("summary", ""), "group title counts, summary names subjects")
        for n in range(3):  # real DCO subjects run to several hundred characters
            put(os.path.join(proj, f"g{n}.txt"), str(n))
            git(proj, "add", f"g{n}.txt")
            git(proj, "commit", "-q", "-m", "docs: " + "sehr lang " * 60 + str(n))
        docs = [i for i in harvest(proj, mem, "2000-01-01T00:00:00+00:00") if i["type"] == "docs"]
        check(docs and len(docs[0]["summary"]) <= 700, f"group summary bounded ({len(docs[0]['summary']) if docs else '-'})")
        plain = tempfile.mkdtemp(prefix="wrapupcore-nogit-")
        os.makedirs(os.path.join(plain, ".agent-memory", "working"))
        check(harvest(plain, os.path.join(plain, ".agent-memory"), "2000-01-01T00:00:00+00:00") == [],
              "no git repo, no dirty files -> [] without crash")
        shutil.rmtree(plain, ignore_errors=True)
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def block(project, date, marker="Letzte Session"):
    return (f"# {marker}\n\n*Datum: {date}*\n*Agent: Claude Code*\n*Projekt: {project}*\n\n"
            f"## Was wurde gemacht\n- work in {project}\n")


def test_handoff():
    print("=== handoff (Ticket B) ===")
    from wrapup_parts.handoff import prepend_block, replace_board_section, write_central, write_board
    existing = "\n\n".join([block("X", "2026-09-23 10:00"),
                            block("Y", "2026-09-22 10:00", "Vorherige Session (2026-09-22 10:00 Y, erhalten)"),
                            block("Z", "2026-09-21 10:00", "Vorherige Session (2026-09-21 10:00 Z, erhalten)")])
    text, dropped = prepend_block(existing, block("Y", "2026-09-24 03:00"), "Y")
    heads = [ln for ln in text.splitlines() if ln.startswith("# ")]
    check(heads == ["# Letzte Session", "# Vorherige Session (2026-09-23 10:00 X, erhalten)",
                    "# Vorherige Session (2026-09-21 10:00 Z, erhalten)"], f"demote + ownership dedup {heads}")
    check(dropped == [], "nothing dropped under cap")
    again, _ = prepend_block(text, block("W", "2026-09-24 04:00"), "W")
    check(sum(1 for ln in again.splitlines() if "Vorherige Session (2026-09-23 10:00 X" in ln) == 1,
          "already demoted block is never re-wrapped")
    five = "\n\n".join([block("A", "2026-09-20 10:00")] + [
        block(p, f"2026-09-1{i} 10:00", f"Vorherige Session (2026-09-1{i} 10:00 {p}, erhalten)")
        for i, p in enumerate("BCDE")])
    capped, dropped = prepend_block(five, block("F", "2026-09-24 03:00"), "F")
    check(sum(1 for ln in capped.splitlines() if ln.startswith("# ")) == 5 and dropped == ["E"],
          f"hard cap 5, oldest dropped {dropped}")

    board = ("# Cross-Project Status Board\n\n## Cross-Project Notes\n- note\n\n---\n\n"
             "## Y (Extra)\n*Updated: old*\n- State: old\n\n---\n\n## Other\n- State: keep\n")
    nb = replace_board_section(board, "Y", "## Y (Extra)\n*Updated: new*\n- State: new\n\n---\n")
    check("- State: new" in nb and "- State: old" not in nb and "- State: keep" in nb and "- note" in nb,
          "board: only own section replaced (heading with suffix matched)")
    nb2 = replace_board_section(board, "Neu", "## Neu\n- State: x\n\n---\n")
    check(nb2.rstrip().endswith("## Neu\n- State: x\n\n---") and "## Y (Extra)" in nb2, "board: new section appended")
    check(replace_board_section(board, "Y-lang", "## Y-lang\n---\n").count("## Y (Extra)") == 1,
          "board: prefix of another project name does not match")
    real_board = board.replace("## Y (Extra)", "## dynamic_central_orchestrator (DCO)")
    nb3 = replace_board_section(real_board, ["dynamic-central-orchestrator"], "## dynamic-central-orchestrator\n- State: n\n\n---\n")
    check("## dynamic_central_orchestrator (DCO)" not in nb3 and nb3.count("- State: n") == 1 and "- State: keep" in nb3,
          "board: '-' vs '_' and suffix normalized (real DCO heading), no duplicate section")
    dco_old = block("dynamic_central_orchestrator", "2026-09-19 17:31",
                    "Vorherige Session (2026-09-19 17:31 dynamic_central_orchestrator, erhalten)")
    txt, _ = prepend_block(block("X", "2026-09-23 10:00") + "\n\n" + dco_old,
                           block("dynamic-central-orchestrator", "2026-09-24 03:00"),
                           ["dynamic-central-orchestrator", "dco"])
    check(sum(1 for ln in txt.splitlines() if ln.startswith("# ")) == 2, "central: ownership dedup across '-'/'_' spelling")

    central = tempfile.mkdtemp(prefix="wrapupcore-central-")
    try:
        check(write_central(central, "Y", block("Y", "2026-09-24 03:00")) == "skipped(no central dir)",
              "central .agent-memory missing -> skipped, nothing created")
        check(not os.path.exists(os.path.join(central, ".agent-memory")), "no dir created")
        os.makedirs(os.path.join(central, ".agent-memory"))
        hand = os.path.join(central, ".agent-memory", "session-summary.md")
        put(hand, existing)

        def foreign_write():
            put(hand, block("Q", "2026-09-24 02:59") + "\n\n" + existing.replace("# Letzte Session", "# Vorherige Session (2026-09-23 10:00 X, erhalten)", 1))
        status = write_central(central, "Y", block("Y", "2026-09-24 03:00"), _before_write=foreign_write)
        body = open(hand, encoding="utf-8").read()
        check(status == "drift-merged", f"drift detected and merged ({status})")
        check("*Projekt: Q*" in body and body.startswith("# Letzte Session") and "*Projekt: Y*" in body.split("---")[0],
              "foreign block preserved, own block on top")
        check(write_board(central, "Y", "## Y\n- State: s\n\n---\n") == "written", "board written")
        bfile = open(os.path.join(central, "cross-project-status.md"), encoding="utf-8").read()
        check(bfile.startswith("# Cross-Project Status Board") and "## Y\n- State: s" in bfile,
              "missing board created with stub + section")
        with open(hand, "wb") as fh:  # the real ~/AI files are CRLF (first real run 2026-09-24 made them LF)
            fh.write(existing.replace("\n", "\r\n").encode("utf-8"))
        write_central(central, "Y", block("Y", "2026-09-24 05:00"))
        raw = open(hand, "rb").read()
        check(raw.count(b"\r\n") > 5 and raw.count(b"\n") == raw.count(b"\r\n"),
              "CRLF file stays CRLF (no whole-file line-ending churn)")
        with open(hand, "wb") as fh:
            fh.write(b"\xef\xbb\xbf" + existing.encode("utf-8"))
        write_central(central, "Y", block("Y", "2026-09-24 06:00"))
        check(open(hand, "rb").read().startswith(b"\xef\xbb\xbf# Letzte Session"), "handoff keeps BOM")
    finally:
        shutil.rmtree(central, ignore_errors=True)


def make_wiki():
    wiki = tempfile.mkdtemp(prefix="wrapupcore-wiki-")
    put(os.path.join(wiki, "index.md"), "# Index\n\n## Queries\n- [old](wiki/queries/old.md) — x\n\n## Other\n- y\n")
    put(os.path.join(wiki, "log.md"), "# Log\n\n## [2026-09-21] agent-sync | other\n\n- Session-Note: x\n")
    os.makedirs(os.path.join(wiki, "wiki", "queries"))
    return wiki


def test_wikinote():
    print("=== wikinote (Ticket C) ===")
    from wrapup_parts.wikinote import write_session_note
    its = [{"type": "feature", "title": "add ä"}, {"type": "fix", "title": "repair b"}]
    wiki = make_wiki()
    try:
        check(write_session_note(wiki, "projA", "2026-09-24", its, enabled=False) == "skipped(sync disabled)",
              "sync_enabled false -> skipped")
        check(write_session_note(wiki, "projA", "2026-09-24", its, threshold=3) == "skipped(below threshold)",
              "iterations below session_note_threshold -> skipped")
        check(write_session_note(os.path.join(wiki, "nope"), "projA", "2026-09-24", its) == "skipped(no wiki root)",
              "missing wiki root -> skipped")
        rel = write_session_note(wiki, "projA", "2026-09-24", its)
        note = os.path.join(wiki, rel)
        check(rel == "wiki/queries/2026-09-24-session-projA-headless.md" and os.path.exists(note), f"note path {rel}")
        text = open(note, encoding="utf-8").read()
        check(all(s in text for s in ("type: query", "query_kind: session", "project: projA",
                                      "agent: claude-code\n", "  - headless", "iterations: 2", "authority: derived")),
              "frontmatter fields")
        # Wiki-Schema kennt nur kanonische agent-Werte (claude-code, codex, ...). "claude-code-headless"
        # war ungueltig und blockierte am 2026-09-24/25 den Wiki-Derived-Refresh; headless steht im Tag.
        check("claude-code-headless" not in text, "agent is canonical claude-code, headless only as tag")
        check("add ä" in text and "repair b" in text, "iteration titles in body (utf-8)")
        idx = open(os.path.join(wiki, "index.md"), encoding="utf-8").read()
        check(idx.index("2026-09-24-session-projA-headless.md") < idx.index("## Other")
              and idx.index("## Queries") < idx.index("2026-09-24-session-projA-headless.md"), "index line under ## Queries")
        log = open(os.path.join(wiki, "log.md"), encoding="utf-8").read()
        check(log.rstrip().endswith("- Total pages touched: 3") and "## [2026-09-24] agent-sync | projA (headless)" in log,
              "log block appended")
        its.append({"type": "docs", "title": "third"})
        rel2 = write_session_note(wiki, "projA", "2026-09-24", its)
        idx2 = open(os.path.join(wiki, "index.md"), encoding="utf-8").read()
        log2 = open(os.path.join(wiki, "log.md"), encoding="utf-8").read()
        check(rel2 == rel and "iterations: 3" in open(note, encoding="utf-8").read(), "same day -> note updated in place")
        check(idx2.count("2026-09-24-session-projA-headless.md") == 1 and log2.count("| projA (headless)") == 1,
              "idempotent: no second index line, no second log block")
        with open(os.path.join(wiki, "index.md"), "wb") as fh:  # real wiki index.md/log.md carry a BOM
            fh.write(b"\xef\xbb\xbf# Index\n\n## Queries\n")
        write_session_note(wiki, "projB", "2026-09-24", its)
        check(open(os.path.join(wiki, "index.md"), "rb").read().startswith(b"\xef\xbb\xbf# Index"),
              "BOM of an existing file is kept (first real run stripped it)")
    finally:
        shutil.rmtree(wiki, ignore_errors=True)


CORE = os.path.join(ROOT, "scripts", "wrapup_core.py")
HOURS_AGO = 3 * 3600


def age(path, seconds):
    t = __import__("time").time() - seconds
    os.utime(path, (t, t))


def setup_core(active=False):
    """Project + central + wiki + fake home; returns dict of paths and byte snapshots of judge-only stores."""
    proj, mem = make_project()
    age(os.path.join(mem, "working", "dirty-s1.json"), 60 if active else HOURS_AGO)
    wiki = make_wiki()
    central = tempfile.mkdtemp(prefix="wrapupcore-central-")
    os.makedirs(os.path.join(central, ".agent-memory"))
    home = tempfile.mkdtemp(prefix="wrapupcore-home-")
    put(os.path.join(mem, "config.json"), json.dumps({"project_id": "projA", "wiki_root": wiki,
                                                       "sync_enabled": True, "session_note_threshold": 1}))
    put(os.path.join(mem, "context", "open-tasks.json"),
        json.dumps([{"id": "T-1", "title": "offene Aufgabe", "status": "open"}]))
    judge_only = {rel: "fixture " + rel for rel in ("learnings/learnings.json", "context/decisions.json",
                                                    "identity/user.md")}
    judge_only["learnings/learnings.json"] = "[]"
    judge_only["context/decisions.json"] = "[]"
    for rel, text in judge_only.items():
        put(os.path.join(mem, *rel.split("/")), text)
    return {"proj": proj, "mem": mem, "wiki": wiki, "central": central, "home": home, "judge_only": judge_only}


def run_core(env, *extra):
    cmd = [sys.executable, CORE, "apply", "--headless", "--mem", env["mem"], "--central-dir", env["central"],
           "--home", env["home"], *extra]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=120)
    try:
        out = json.loads(p.stdout)
    except ValueError:
        out = {"_raw": p.stdout[-800:], "_err": p.stderr[-800:]}
    return p.returncode, out


def cleanup(env):
    for k in ("proj", "wiki", "central", "home"):
        shutil.rmtree(env[k], ignore_errors=True)


def read(path):
    return open(path, encoding="utf-8").read() if os.path.exists(path) else ""


def test_core():
    print("=== wrapup_core apply --headless (Ticket D) ===")
    env = setup_core()
    try:
        rc, out = run_core(env)
        mem = env["mem"]
        check(rc == 0 and out.get("ok") and out.get("mode") == "headless", f"exit 0, ok, mode headless {rc} {str(out)[:300]}")
        check("add a" in read(os.path.join(mem, "iterations", "iteration-log.md")), "iterations written via applier")
        summ = read(os.path.join(mem, "session-summary.md"))
        check("add a" in summ and "Headless" in summ, "session summary rendered with headless warning")
        hand = read(os.path.join(env["central"], ".agent-memory", "session-summary.md"))
        check(hand.startswith("# Letzte Session") and "*Projekt: projA*" in hand and "headless" in hand,
              "central handoff block written")
        check("## projA" in read(os.path.join(env["central"], "cross-project-status.md")), "status board section written")
        check(os.path.exists(os.path.join(env["wiki"], "wiki", "queries",
                                          f"{out.get('date')}-session-projA-headless.md")), "wiki note written")
        marker = json.loads(read(os.path.join(mem, "consolidation-marker.json")) or "{}")
        check(marker.get("consolidated_sessions") == ["s1"], f"marker names the session {marker}")
        dirty = json.loads(read(os.path.join(mem, "working", "dirty-s1.json")))
        check(dirty.get("dirty") is False, "dirty flag reset")
        check(all(read(os.path.join(mem, *rel.split("/"))) == text for rel, text in env["judge_only"].items()),
              "learnings/decisions/user.md untouched (no judge, no identity promotion)")
        check(out.get("identity_status_line") == "Identity: headless — kein Harvest", "identity status line headless")
        check(max(len(ln) for ln in hand.split("\n---\n")[0].splitlines()) <= 200, "handoff block lines bounded")
        check(out.get("marker_written") is True and out.get("handoff", {}).get("central") == "written", "tally fields")
        rc2, out2 = run_core(env)
        check(rc2 == 0 and out2.get("status") == "skipped(nothing to consolidate)", f"second run is a no-op {out2.get('status')}")
    finally:
        cleanup(env)

    env = setup_core()
    try:  # wrap-up's own tail writes after its marker: RECOVERY ignores them (session-start.sh), so must we
        dpath = os.path.join(env["mem"], "working", "dirty-s1.json")
        d = json.loads(read(dpath))
        d.update({"last_consolidated_at": "2026-09-24T08:20:00+02:00", "writes_since_consolidation": 3})
        put(dpath, json.dumps(d))
        age(dpath, HOURS_AGO)
        rc, out = run_core(env)
        check(rc == 0 and out.get("status") == "skipped(nothing to consolidate)",
              f"post-wrap-up tail writes (<= 5) are not re-consolidated {out.get('status')}")
        check(not read(os.path.join(env["central"], ".agent-memory", "session-summary.md")),
              "tail writes: central handoff untouched")
        d["writes_since_consolidation"] = 6
        put(dpath, json.dumps(d))
        age(dpath, HOURS_AGO)
        rc, out = run_core(env)
        check(out.get("status") == "consolidated", f"more than 5 writes after wrap-up -> real new work {out.get('status')}")
    finally:
        cleanup(env)

    env = setup_core()
    try:  # a later wrap-up already covered the old commits: harvest only after the marker (real-data dry run: DCO 145)
        put(os.path.join(env["mem"], "consolidation-marker.json"),
            json.dumps({"last_wrapup": "2099-01-01T00:00:00+00:00", "consolidated_sessions": ["old"]}))
        age(os.path.join(env["mem"], "working", "dirty-s1.json"), HOURS_AGO)
        rc, out = run_core(env)
        log = read(os.path.join(env["mem"], "iterations", "iteration-log.md"))
        check(out.get("iterations") == 1 and "add a" not in log and "Uncommitted changes" in log,
              f"since = max(dirty started, marker last_wrapup) {out.get('iterations')}")
    finally:
        cleanup(env)

    env = setup_core()
    try:  # work happened in other repos: nothing harvestable -> close RECOVERY, keep rich summaries/handoff intact
        dpath = os.path.join(env["mem"], "working", "dirty-s1.json")
        d = json.loads(read(dpath))
        d["touched_files"] = [os.path.join(os.path.dirname(env["proj"]), "other-repo", "x.py")]
        put(dpath, json.dumps(d))
        put(os.path.join(env["mem"], "consolidation-marker.json"),
            json.dumps({"last_wrapup": "2099-01-01T00:00:00+00:00", "consolidated_sessions": ["old"]}))
        put(os.path.join(env["mem"], "session-summary.md"), "# Last Session\n\nreiche manuelle Zusammenfassung\n")
        put(os.path.join(env["central"], ".agent-memory", "session-summary.md"), block("projA", "2026-09-24 08:20"))
        age(dpath, HOURS_AGO)
        rc, out = run_core(env)
        check(rc == 0 and out.get("status") == "consolidated(empty)", f"empty harvest status {out.get('status')}")
        check("reiche manuelle" in read(os.path.join(env["mem"], "session-summary.md")), "empty: local summary kept")
        check(read(os.path.join(env["central"], ".agent-memory", "session-summary.md")) == block("projA", "2026-09-24 08:20"),
              "empty: central handoff untouched")
        check(json.loads(read(dpath)).get("dirty") is False and out.get("marker_written") is True,
              "empty: dirty reset + marker (RECOVERY closed)")
    finally:
        cleanup(env)

    env = setup_core(active=True)
    try:
        rc, out = run_core(env)
        check(rc == 0 and out.get("status") == "skipped(active session)", f"active session skipped {out.get('status')}")
        check(not os.path.exists(os.path.join(env["mem"], "consolidation-marker.json")), "active: no marker")
    finally:
        cleanup(env)

    env = setup_core()
    try:
        rc, out = run_core(env, "--dry-run")
        check(rc == 0 and out.get("dry_run") is True, "dry-run exit 0")
        check(not os.path.exists(os.path.join(env["mem"], "consolidation-marker.json"))
              and not os.path.exists(os.path.join(env["mem"], "session-summary.md"))
              and not read(os.path.join(env["central"], ".agent-memory", "session-summary.md")),
              "dry-run writes nothing")
    finally:
        cleanup(env)

    env = setup_core()
    try:
        date = __import__("datetime").date.today().isoformat()
        os.makedirs(os.path.join(env["wiki"], "wiki", "queries", f"{date}-session-projA-headless.md"))
        rc, out = run_core(env)
        check(rc == 0 and str(out.get("wiki_note", "")).startswith("failed("), f"wiki failure is fail-soft {out.get('wiki_note')}")
        check(os.path.exists(os.path.join(env["mem"], "consolidation-marker.json")), "wiki failure: marker still written")
    finally:
        cleanup(env)

    env = setup_core()
    try:
        put(os.path.join(env["mem"], "working", "dirty-broken.json"), "{not json")
        age(os.path.join(env["mem"], "working", "dirty-broken.json"), HOURS_AGO)
        rc, out = run_core(env)
        check(rc == 2 and not out.get("ok"), f"corrupt dirty file -> exit 2 ({rc})")
        check(not os.path.exists(os.path.join(env["mem"], "consolidation-marker.json"))
              and not os.path.exists(os.path.join(env["mem"], "iterations", "iteration-log.md")),
              "corrupt dirty file: nothing written, no marker")
    finally:
        cleanup(env)

    # 5.2.1 review: a store text file in a legacy encoding must end the night run with
    # a JSON error (rc 2, reported per project), never with a traceback and empty stdout.
    env = setup_core()
    try:
        log = os.path.join(env["mem"], "iterations", "iteration-log.md")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "wb") as fh:
            fh.write("# Iteration Log\n\n## 2026-01-01 — fix: alt\n".encode("cp1252"))
        rc, out = run_core(env)
        check(rc == 2 and out.get("ok") is False and "_raw" not in out,
              f"non-UTF-8 iteration log -> exit 2 with JSON, no traceback ({rc}, {str(out)[:160]})")
        check(not os.path.exists(os.path.join(env["mem"], "consolidation-marker.json")),
              "non-UTF-8 iteration log: no marker")
    finally:
        cleanup(env)

    env = setup_core()
    try:
        env_inside = dict(env, central=os.path.join(env["mem"], "context"))
        os.makedirs(os.path.join(env_inside["central"], ".agent-memory"), exist_ok=True)
        rc, out = run_core(env_inside)
        check(out.get("handoff", {}).get("central") == "failed(path guard)", f"path guard {out.get('handoff')}")
        check(os.path.exists(os.path.join(env["mem"], "consolidation-marker.json")), "path guard is fail-soft")
    finally:
        cleanup(env)


NIGHTLY = os.path.join(ROOT, "scripts", "nightly_consolidate.py")


def project_at(root, rel, seconds_old, tail=False, project_id=None):
    proj, mem = make_project()
    dest = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(proj, dest)
    mem = os.path.join(dest, ".agent-memory")
    dpath = os.path.join(mem, "working", "dirty-s1.json")
    if tail:
        d = json.loads(read(dpath))
        d.update({"last_consolidated_at": "2026-09-24T08:20:00+02:00", "writes_since_consolidation": 2})
        put(dpath, json.dumps(d))
    put(os.path.join(mem, "config.json"), json.dumps({"project_id": project_id or rel.split("/")[-1]}))
    age(dpath, seconds_old)
    return dest


def run_nightly(root, central, home, report, *extra):
    cmd = [sys.executable, NIGHTLY, "--root", root, "--central-dir", central, "--home", home, "--report", report,
           "--deny", os.path.join(root, "deny"), "--kill-switch", os.path.join(root, "OFF"), *extra]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=600)
    try:
        return p.returncode, json.loads(p.stdout)
    except ValueError:
        return p.returncode, {"_raw": p.stdout[-800:], "_err": p.stderr[-800:]}


def test_nightly():
    print("=== nightly_consolidate (Ticket E) ===")
    root = tempfile.mkdtemp(prefix="wrapupcore-root-")
    central = tempfile.mkdtemp(prefix="wrapupcore-central-")
    home = tempfile.mkdtemp(prefix="wrapupcore-home-")
    os.makedirs(os.path.join(central, ".agent-memory"))
    report = os.path.join(central, "metrics", "nightly.jsonl")
    try:
        project_at(root, "projF", 4 * 3600)
        project_at(root, "sub/projA", 3 * 3600)
        project_at(root, "projB", 20 * 86400)
        project_at(root, "projC", 3 * 3600, tail=True)
        project_at(root, "node_modules/projD", 3 * 3600)
        project_at(root, "deny/projE", 3 * 3600)

        put(os.path.join(root, "OFF"), "off")
        rc, out = run_nightly(root, central, home, report)
        check(rc == 0 and out.get("status") == "killed" and not out.get("projects"), f"kill switch {out.get('status')}")
        os.remove(os.path.join(root, "OFF"))

        rc, out = run_nightly(root, central, home, report, "--dry-run")
        names = [os.path.basename(p["project_root"]) for p in out.get("projects", [])]
        check(rc == 0 and names == ["projF", "projA"], f"dry-run candidates oldest first, filters applied {names}")
        check(not read(os.path.join(central, ".agent-memory", "session-summary.md")), "dry-run: central untouched")

        rc, out = run_nightly(root, central, home, report)
        stats = [p.get("status") for p in out.get("projects", [])]
        check(rc == 0 and stats == ["consolidated", "consolidated"], f"both candidates consolidated {stats}")
        hand = read(os.path.join(central, ".agent-memory", "session-summary.md"))
        check(hand.split("---")[0].count("*Projekt: projA*") == 1, "most recently active project ends on top of the handoff")
        check(out.get("skipped", {}).get("stale", 0) == 1, f"stale project counted, not processed {out.get('skipped')}")
        lines = [ln for ln in read(report).splitlines() if ln.strip()]
        check(len(lines) == 3 and json.loads(lines[-1]).get("status") == "done", f"one report line per run ({len(lines)})")
        rc, out = run_nightly(root, central, home, report)
        check(not out.get("projects"), "second night: nothing left to consolidate")
    finally:
        for d in (root, central, home):
            shutil.rmtree(d, ignore_errors=True)


def main():
    if not GIT:
        print("SKIPPED: git not found")
        return 0
    test_harvest()
    test_handoff()
    test_wikinote()
    test_core()
    test_nightly()
    print(f"\n{PASSED}/{TESTS} passed, {ERRORS} failed")
    return 1 if ERRORS else 0


if __name__ == "__main__":
    sys.exit(main())

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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolated_home import process_env  # noqa: E402

# Subprozesse laufen mit frischem Temp-Home statt mit dem echten Home (P0, tests/_isolated_home.py)
ENV, ISO_HOME = process_env()
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
                   check=True, capture_output=True, text=True, encoding="utf-8", env=ENV)


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

    # DCO #9424 / template 7.6a point 5: a block pushed out by cap 5 is that project's ONLY
    # block - its 1-line state moves into the board first. Existing sections stay untouched.
    from wrapup_parts.handoff import rescue_blocks, rescue_dropped
    central = tempfile.mkdtemp(prefix="wrapupcore-rescue-")
    try:
        os.makedirs(os.path.join(central, ".agent-memory"))
        hand = os.path.join(central, ".agent-memory", "session-summary.md")
        bpath = os.path.join(central, "cross-project-status.md")
        e_block = block("E", "2026-09-14 10:00", "Vorherige Session (2026-09-14 10:00 E, erhalten)") \
            + "\n## Aktueller Stand\n- E wartet auf Review\n"
        five = "\n\n".join([block("A", "2026-09-20 10:00")] + [
            block(p, f"2026-09-1{i} 10:00", f"Vorherige Session (2026-09-1{i} 10:00 {p}, erhalten)")
            for i, p in enumerate("BCD")] + [e_block])
        put(hand, five)
        put(bpath, "# Cross-Project Status Board\n\n## B\n- State: b\n\n---\n")
        r = rescue_dropped(central, "F", block("F", "2026-09-24 03:00"))
        check(r["rescued"] == ["E"] and r["lost"] == [] and r["status"] == "written", f"cap drop -> E rescued {r}")
        board_text = open(bpath, encoding="utf-8").read()
        check("## E\n" in board_text and "- State: E wartet auf Review" in board_text and "2026-09-14 10:00" in board_text
              and "## B\n- State: b" in board_text, "rescue section appended, 'Aktueller Stand' line, B untouched")
        before = open(bpath, "rb").read()
        mtime = os.path.getmtime(bpath)
        r = rescue_dropped(central, "F", block("F", "2026-09-24 03:00"))
        check(r["rescued"] == [] and r["lost"] == [] and r["status"] == "none", f"E already on the board -> nothing to do {r}")
        check(open(bpath, "rb").read() == before and os.path.getmtime(bpath) == mtime, "no rescue need -> board not rewritten")
        check(rescue_dropped(central, "A", block("A", "2026-09-24 03:00"))["status"] == "none",
              "own project re-prepended -> nothing dropped")

        # Pruefer AOS-2: drift between the pre-read and write_central drops a DIFFERENT block (D).
        # write_central hands out what it really dropped; rescue_blocks catches D afterwards.
        put(hand, five)
        put(bpath, "# Cross-Project Status Board\n\n---\n")
        pre = rescue_dropped(central, "F", block("F", "2026-09-24 03:00"))

        def foreign():
            put(hand, block("G", "2026-09-24 02:59") + "\n\n"
                + five.replace("# Letzte Session", "# Vorherige Session (2026-09-20 10:00 A, erhalten)", 1))
        dropped = []
        write_central(central, "F", block("F", "2026-09-24 03:00"), _before_write=foreign, dropped_out=dropped)
        post = rescue_blocks(central, dropped)
        board_text = open(bpath, encoding="utf-8").read()
        check(pre["rescued"] == ["E"] and sorted(b.split("*Projekt: ")[1][0] for b in dropped) == ["D", "E"]
              and post["rescued"] == ["D"] and "## D\n" in board_text and board_text.count("## E\n") == 1,
              f"drift: D dropped too and rescued afterwards, E not twice (pre {pre}, post {post})")

        # a dropped block without any state line cannot be rescued -> reported as lost, never silent
        put(hand, five.replace("- work in E\n", "").replace("\n## Aktueller Stand\n- E wartet auf Review\n", "\n"))
        put(bpath, "# Cross-Project Status Board\n\n---\n")
        r = rescue_dropped(central, "F", block("F", "2026-09-24 03:00"))
        check(r["rescued"] == [] and r["lost"] == ["E"], f"no state line -> E reported lost {r}")
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
        # Session-Notizen sind Snapshot-Artefakte: status final (Wiki-Template, DR-2026-07-17-002, DCO #9891)
        check("status: final\n" in text and "status: active" not in text, "session note status is final")
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
HOURS_AGO = 13 * 3600  # past the 12 h quiet rule (harvest_state.QUIET_HOURS)


def stamp(seconds):
    return __import__("datetime").datetime.fromtimestamp(
        __import__("time").time() - seconds).astimezone().isoformat(timespec="seconds")


def age(path, seconds):
    """Age a dirty file: its `updated` field (the activity signal since Scheibe 6) AND its mtime."""
    try:
        d = json.loads(read(path))
        if isinstance(d, dict):
            d["updated"] = stamp(seconds)
            put(path, json.dumps(d))
    except ValueError:
        pass  # a deliberately broken fixture keeps its bytes
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
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=120, env=ENV)
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
        check(dirty.get("consolidated_by") == "headless" and dirty.get("last_consolidated_by") == "headless"
              and marker.get("mode") == "headless",
              f"headless run is stamped 'headless', never 'wrap-up' {dirty.get('consolidated_by')} {marker.get('mode')}")
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
    try:  # ...but a headless stamp has no tail: the night run is invisible to the tracker, so 3 writes are real work
        dpath = os.path.join(env["mem"], "working", "dirty-s1.json")
        d = json.loads(read(dpath))
        d.update({"last_consolidated_at": "2026-09-24T08:20:00+02:00", "last_consolidated_by": "headless",
                  "writes_since_consolidation": 3})
        put(dpath, json.dumps(d))
        age(dpath, HOURS_AGO)
        rc, out = run_core(env)
        check(rc == 0 and out.get("status") == "consolidated",
              f"writes after a headless stamp are never treated as tail {out.get('status')}")
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
        check(json.loads(read(dpath)).get("consolidated_by") == "headless",
              "empty: the closed session is stamped 'headless' as well")
    finally:
        cleanup(env)

    env = setup_core()
    try:  # DCO #9433: in the AI workspace the store's summary IS the central handoff
        handoff = block("projB", "2026-10-10 15:30") + "\n---\n\n" + block("projC", "2026-10-09 11:00")
        put(os.path.join(env["mem"], "session-summary.md"), handoff)
        rc, out = run_core(dict(env, central=env["proj"]))  # central/.agent-memory == mem
        hand = read(os.path.join(env["mem"], "session-summary.md"))
        check(rc == 0 and "work in projB" in hand and "work in projC" in hand,
              f"store == central: foreign handoff blocks survive {out.get('status')} {out.get('handoff')}")
        check(hand.startswith("# Letzte Session") and "*Projekt: projA*" in hand.split("\n---\n")[0],
              "store == central: own block prepended via write_central")
    finally:
        cleanup(env)

    env = setup_core()
    try:  # DCO #9696: a failed pattern update must not be reported as a clean "consolidated"
        put(os.path.join(env["mem"], "patterns", "patterns.json"), "{not json")
        rc, out = run_core(env)
        check(rc == 0 and out.get("status") == "consolidated(patterns failed)"
              and str(out.get("reports", {}).get("patterns", "")).startswith("failed("),
              f"pattern update failure shows in the status {out.get('status')} {out.get('reports')}")
        check(out.get("marker_written") is True, "pattern update failure stays fail-soft: marker written")
    finally:
        cleanup(env)

    env = setup_core(active=True)
    try:
        rc, out = run_core(env)
        check(rc == 0 and out.get("status") == "skipped(active session)", f"active session skipped {out.get('status')}")
        check(not os.path.exists(os.path.join(env["mem"], "consolidation-marker.json")), "active: no marker")
    finally:
        cleanup(env)

    # Scheibe 6: a session counts as ended only with session_end (after its last activity) or 12 h quiet
    def dpath(env, sid="s1"):
        return os.path.join(env["mem"], "working", f"dirty-{sid}.json")

    env = setup_core()
    try:
        age(dpath(env), 3 * 3600)
        rc, out = run_core(env)
        check(rc == 0 and out.get("status") == "deferred(store not quiet)"
              and json.loads(read(dpath(env))).get("dirty") is True
              and not os.path.exists(os.path.join(env["mem"], "consolidation-marker.json")),
              f"3 h quiet, no session_end -> deferred, nothing written {out.get('status')}")
        d = json.loads(read(dpath(env)))
        d["session_end"] = {"reason": "other", "ended_at": stamp(3 * 3600 - 60)}
        put(dpath(env), json.dumps(d))
        t = __import__("time").time() - 3 * 3600
        os.utime(dpath(env), (t, t))
        rc, out = run_core(env)
        check(rc == 0 and out.get("status") == "consolidated" and out.get("sessions") == ["s1"],
              f"session_end after the last write proves the end -> consolidated, sid reported "
              f"{out.get('status')} {out.get('sessions')}")
    finally:
        cleanup(env)

    env = setup_core()
    try:
        put(dpath(env, "s2"), json.dumps({"session_id": "s2", "dirty": True, "started": "2000-01-01T00:00:00+00:00",
                                         "touched_files": [], "write_count": 1}))
        age(dpath(env, "s2"), 3 * 3600)
        rc, out = run_core(env)
        check(out.get("status") == "deferred(store not quiet)" and json.loads(read(dpath(env))).get("dirty") is True,
              f"store level: one session not yet quiet defers the quiet one too {out.get('status')}")
    finally:
        cleanup(env)

    env = setup_core()
    try:
        os.utime(dpath(env), None)  # fresh mtime (e.g. a stale stamp), but `updated` is 13 h old
        rc, out = run_core(env)
        check(out.get("status") == "consolidated", f"activity comes from `updated`, not the file mtime {out.get('status')}")
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
           "--deny", os.path.join(root, "deny"), "--kill-switch", os.path.join(root, "OFF"),
           "--native-root", os.path.join(root, "native"), *extra]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=600, env=ENV)
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
        project_at(root, "projF", 14 * 3600)
        project_at(root, "sub/projA", 13 * 3600)
        project_at(root, "projB", 20 * 86400)
        project_at(root, "projC", 13 * 3600, tail=True)
        project_at(root, "node_modules/projD", 13 * 3600)
        project_at(root, "deny/projE", 13 * 3600)

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
        check(all(p.get("sessions") == ["s1"] for p in out.get("projects", [])), "each harvested sid is in the report")
        ht = out.get("harvest_totals") or {}
        stale_roots = [os.path.basename(e["project_root"]) for e in out.get("harvest", []) if e.get("stale")]
        check(ht.get("stale") == 1 and stale_roots == ["projB"],
              f"harvest pass reports the 20-day marker as stale {ht} {stale_roots}")
        lines = [ln for ln in read(report).splitlines() if ln.strip()]
        check(len(lines) == 3 and json.loads(lines[-1]).get("status") == "done", f"one report line per run ({len(lines)})")
        rc, out = run_nightly(root, central, home, report)
        check(not out.get("projects"), "second night: nothing left to consolidate")
        check((out.get("harvest_totals") or {}).get("stale") == 0, "second night: stale is reported only once")
    finally:
        for d in (root, central, home):
            shutil.rmtree(d, ignore_errors=True)

    # Scheibe 6: the harvest pass closes a session whose own transcript holds a real wrap-up
    root = tempfile.mkdtemp(prefix="wrapupcore-root-")
    central = tempfile.mkdtemp(prefix="wrapupcore-central-")
    home = tempfile.mkdtemp(prefix="wrapupcore-home-")
    os.makedirs(os.path.join(central, ".agent-memory"))
    report = os.path.join(central, "metrics", "nightly.jsonl")
    try:
        proj = project_at(root, "projG", 14 * 3600)
        dpath = os.path.join(proj, ".agent-memory", "working", "dirty-s1.json")
        tr = os.path.join(root, "s1.jsonl")
        records = (
            {"type": "user", "message": {"role": "user", "content": "<command-name>/agentic-os:wrap-up</command-name>"}},
            {"type": "assistant", "message": {"role": "assistant",
                                              "content": [{"type": "tool_use", "name": "Edit", "input": {}}]}},
        )
        put(tr, "".join(json.dumps(r) + "\n" for r in records))
        t = __import__("time").time() - 14 * 3600
        os.utime(tr, (t, t))
        d = json.loads(read(dpath))
        d["transcript_path"] = tr
        put(dpath, json.dumps(d))
        age(dpath, 14 * 3600)
        old_proj = project_at(root, "projH", 20 * 86400)
        os.utime(os.path.join(old_proj, ".agent-memory", "working", "dirty-s1.json"), None)  # fresh mtime only
        rc, out = run_nightly(root, central, home, report, "--dry-run")
        closed = [c for e in out.get("harvest", []) for c in e.get("closed", [])]
        check(closed == [] and "harvest" not in json.loads(read(dpath)),
              f"dry-run consolidates nothing, so the still-dirty session is not closable yet; nothing written {closed}")
        rc, out = run_nightly(root, central, home, report)
        d = json.loads(read(dpath))
        check([p.get("status") for p in out.get("projects", [])] == ["consolidated"]
              and (d.get("harvest") or {}).get("closed_by") == "wrapup_im_transkript" and d.get("dirty") is False,
              f"consolidated headless, then closed by the wrap-up in its transcript {d.get('harvest')}")
        check((out.get("harvest_totals") or {}).get("closed") == 1, f"closure counted {out.get('harvest_totals')}")
        check(out.get("skipped", {}).get("stale") == 1,
              f"candidate choice reads `updated`: a 20-day marker with a fresh mtime is stale {out.get('skipped')}")
    finally:
        for d in (root, central, home):
            shutil.rmtree(d, ignore_errors=True)


def test_collision():
    """Red-team collision test (plan Phase 2, design A+): two sessions in one store, one still
    young. Night run, then GC --apply: every session keeps its marker OR has an evidence line."""
    print("=== collision: night run + GC --apply (Scheibe 7) ===")
    root = tempfile.mkdtemp(prefix="wrapupcore-root-")
    central = tempfile.mkdtemp(prefix="wrapupcore-central-")
    home = tempfile.mkdtemp(prefix="wrapupcore-home-")
    os.makedirs(os.path.join(central, ".agent-memory"))
    report = os.path.join(central, "metrics", "nightly.jsonl")
    try:
        proj = project_at(root, "projK", 14 * 3600)
        mem = os.path.join(proj, ".agent-memory")
        work = os.path.join(mem, "working")
        tr = os.path.join(root, "s1.jsonl")
        put(tr, json.dumps({"type": "user", "message": {"role": "user",
                                                        "content": "<command-name>/agentic-os:wrap-up</command-name>"}}) + "\n")
        t = __import__("time").time() - 14 * 3600
        os.utime(tr, (t, t))
        d1 = json.loads(read(os.path.join(work, "dirty-s1.json")))
        d1["transcript_path"] = tr
        put(os.path.join(work, "dirty-s1.json"), json.dumps(d1))
        age(os.path.join(work, "dirty-s1.json"), 14 * 3600)
        put(os.path.join(work, "dirty-s2.json"), json.dumps({"session_id": "s2", "agent": "claude", "dirty": True,
                                                            "started": "2000-01-01T00:00:00+00:00",
                                                            "touched_files": ["z.txt"], "write_count": 4}))
        age(os.path.join(work, "dirty-s2.json"), 3 * 3600)
        rc, out = run_nightly(root, central, home, report)
        check([p.get("status") for p in out.get("projects", [])] == ["deferred(store not quiet)"],
              f"store with a young session is deferred {[p.get('status') for p in out.get('projects', [])]}")
        gc = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "gc_dirty_markers.py"), mem, "--apply"],
                            capture_output=True, text=True, encoding="utf-8", env=ENV)
        ev_path = os.path.join(mem, "metrics", "harvest-closures.jsonl")
        evidence = {json.loads(ln)["sid"] for ln in read(ev_path).splitlines() if ln.strip()}
        for sid in ("s1", "s2"):
            check(os.path.exists(os.path.join(work, f"dirty-{sid}.json")) or sid in evidence,
                  f"{sid}: marker kept or evidence written (gc: {gc.stdout.strip()[-120:]})")
        s1 = json.loads(read(os.path.join(work, "dirty-s1.json")))
        check(not evidence and s1.get("dirty") is True and "harvest" not in s1,
              "deferred store: the quiet dirty session is NOT closed - its work was never consolidated (H1)")
        check(not os.path.exists(os.path.join(mem, "consolidation-marker.json")), "deferred store: no consolidation marker")
        # the next night: both sessions quiet -> consolidated together, then s1 closes by its wrap-up
        age(os.path.join(work, "dirty-s2.json"), 13 * 3600)
        rc, out = run_nightly(root, central, home, report)
        check([p.get("status") for p in out.get("projects", [])] == ["consolidated"]
              and sorted(out["projects"][0].get("sessions") or []) == ["s1", "s2"],
              f"next night: the whole store is consolidated {[p.get('status') for p in out.get('projects', [])]}")
        t = __import__("time").time() - 3600  # next daily run, outside the 30-min window
        for sid in ("s1", "s2"):
            os.utime(os.path.join(work, f"dirty-{sid}.json"), (t, t))
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "gc_dirty_markers.py"), mem, "--apply"],
                       capture_output=True, text=True, encoding="utf-8", env=ENV)
        evidence = {json.loads(ln)["sid"] for ln in read(ev_path).splitlines() if ln.strip()}
        for sid in ("s1", "s2"):
            check(os.path.exists(os.path.join(work, f"dirty-{sid}.json")) or sid in evidence,
                  f"after GC: {sid} marker kept or evidence written")
        check(evidence == {"s1"} and json.loads(read(os.path.join(work, "dirty-s2.json"))).get("dirty") is False,
              f"s1 (wrap-up in transcript) went with evidence, s2 stays pending {sorted(evidence)}")
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
    test_collision()
    print(f"\n{PASSED}/{TESTS} passed, {ERRORS} failed")
    return 1 if ERRORS else 0


if __name__ == "__main__":
    sys.exit(main())

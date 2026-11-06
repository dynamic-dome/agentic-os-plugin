#!/usr/bin/env python3
"""wrap-up core (T-028): the mechanical half of a wrap-up in one script.

    python wrapup_core.py pre --mem .agent-memory --session-id <sid>
    python wrapup_core.py apply --mem .agent-memory --session-id <sid> < plan.json
    python wrapup_core.py followup --mem .agent-memory --session-id <sid> < plan.json
    python wrapup_core.py apply --headless --mem <project>/.agent-memory
        [--central-dir ~/AI] [--home ~] [--wiki-root <dir>] [--min-age-minutes 120] [--dry-run]

Judge path (T-028b, spec 2026-11-05): `pre` writes nothing and hands the model a brief; the model
sends ONE plan to `apply`; `followup` takes the verdicts that need ids which exist only after apply.
Every store write goes through apply_wrapup.py as a subprocess: one path for lock, identity store,
session binding and quote check. The core owns the order, the summary, handoff, wiki note, reports
and the marker.

Headless path: consolidates un-wrapped sessions of ONE project without a model: iterations from git
log + uncommitted dirty files, session summary, central handoff + status board, wiki session note,
pattern update, projections, then the consolidation marker LAST (apply_wrapup.apply_consolidation).
Never headless: learnings, decisions, identity (apply_user_candidates would promote the queue to
user.md), soul.md, open-task edits.

Error policy (spec §5.4): unreadable dirty evidence or a store failure -> exit 2, no marker;
handoff / wiki / projection / report failures are fail-soft (`failed(<reason>)`), marker is written.
Headless skips (exit 0, nothing written): no dirty session, or the newest dirty file is younger than
--min-age-minutes (a session is probably still running).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import apply_wrapup as aw  # noqa: E402
import harvest_state as hs  # noqa: E402
from harvest_state import TAIL_WRITES_MAX  # noqa: E402
import contextlib  # noqa: E402
import store_lock  # noqa: E402
from preprocess_state import open_tasks  # noqa: E402
from wrapup_parts.handoff import rescue_blocks, rescue_dropped, write_board, write_central  # noqa: E402
from wrapup_parts.harvest import harvest  # noqa: E402
from wrapup_parts.wikinote import write_session_note  # noqa: E402

IDENTITY_LINE = "Identity: headless — kein Harvest"
# Same downgrade rule as the RECOVERY line in session-start.sh: a dirty file that carries
# last_consolidated_at and at most this many writes since is wrap-up's own tail, not lost work.
# Never after a headless stamp: the night run is a plain subprocess the tracker never sees, so
# every write after it is real session work.
# TAIL_WRITES_MAX lives in harvest_state.py (one definition for core, night run and harvest rules).
HEADLESS_WARNING = ("Headless konsolidiert (Nachtlauf, ohne Judge): Learnings, Decisions und Identity "
                    "wurden nicht geerntet.")


def _emit(obj, code):
    print(json.dumps(obj, indent=2, ensure_ascii=False))
    return code


def _dirty_sessions(mem):
    """Strictly read every dirty-*.json. -> (dirty_entries[(path, data)], error or None)."""
    work = os.path.join(mem, "working")
    out = []
    if not os.path.isdir(work):
        return out, None
    for name in sorted(os.listdir(work)):
        if not (name.startswith("dirty-") and name.endswith(".json")):
            continue
        path = os.path.join(work, name)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            return [], f"working/{name} is unreadable ({exc})"
        if isinstance(data, dict) and data.get("dirty"):
            out.append((path, data))
    return out, None


def _is_tail(data):
    """True when the dirty file only holds wrap-up's own writes after its marker (session-start.sh rule).
    One definition: harvest_state.is_tail (the harvest rules need the same answer)."""
    return hs.is_tail(data)


def _parse(ts):
    try:
        t = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.astimezone()  # naive = local time


def _since(mem, dirty):
    """Later of (earliest dirty start, last wrap-up): commits before the last wrap-up were covered by it.

    Real-data dry run 2026-09-24: with the earliest start alone, one stale DCO session pulled 145
    already-logged commits back in.
    """
    points = [t for t in (_parse(d.get("started")) for _, d in dirty if d.get("started")) if t]
    earliest = min(points) if points else None
    last = None
    try:
        with open(os.path.join(mem, "consolidation-marker.json"), encoding="utf-8") as fh:
            last = _parse(json.load(fh).get("last_wrapup"))
    except (OSError, ValueError):
        pass
    best = max(t for t in (earliest, last) if t) if (earliest or last) else None
    return best.isoformat() if best else _dt.date.today().isoformat() + "T00:00:00"


def _config(mem):
    try:
        with open(os.path.join(mem, "config.json"), encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _git(project_root, *args):
    git = shutil.which("git")
    if not git or not os.path.exists(os.path.join(project_root, ".git")):
        return ""
    try:
        p = subprocess.run([git, "-C", project_root, *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30, stdin=subprocess.DEVNULL)
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _repo_status(project_root):
    lines = _git(project_root, "status", "--porcelain").splitlines()
    porcelain = [ln for ln in lines if ".agent-memory/" not in ln.replace("\\", "/")]
    return {"branch": _git(project_root, "rev-parse", "--abbrev-ref", "HEAD") or "n/a",
            "uncommitted": "ja" if porcelain else "nein",
            "memory_uncommitted": "ja" if len(lines) > len(porcelain) else "nein",
            "last": _git(project_root, "log", "-1", "--pretty=%h %s") or "n/a"}


def _inside(path, root):
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == os.path.abspath(root)
    except ValueError:
        return False


def _run_script(args, cwd, timeout=60):
    """Fail-soft helper for the mechanical side scripts -> 'ok' | 'failed(<reason>)'."""
    try:
        p = subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"failed({type(exc).__name__})"
    return "ok" if p.returncode == 0 else f"failed(exit {p.returncode})"


def _cut(text, limit=160):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _paths_of(iterations):
    files = []
    for it in iterations:
        for f in it.get("files_changed") or []:
            if f not in files:
                files.append(f)
    return files


def _handoff_block(project, now, iterations, tasks, repo, n_open, top):
    done = [_cut(f"- {it.get('type', 'feature')}: {it.get('title', '')}", 180)
            for it in iterations[:8]] or ["- (nichts geerntet)"]
    files = _paths_of(iterations)
    lines = ["# Letzte Session", "", f"*Datum: {now}*", "*Agent: Claude Code (headless)*", f"*Projekt: {project}*", "",
             "## Was wurde gemacht", *done, "",
             "## Aktueller Stand",
             f"- Automatisch konsolidiert (Nachtlauf, ohne Judge): {len(iterations)} Iterationen aus git/dirty; "
             "Learnings/Decisions nicht geerntet.", "",
             "## Repo-Status", f"- Branch: {repo['branch']}", f"- Uncommitted changes: {repo['uncommitted']}",
             f"- Letzter Commit: {_cut(repo['last'], 120)}", "",
             "## Offene Punkte / Blocker", *([_cut(f"- {t['title']}", 180) for t in tasks[:3]] or ["- keine"]),
             "- Blocker: unbekannt (headless)", "",
             "## Checks", "- Tests: nicht gelaufen", "- Lint/Validation: nicht gelaufen", "",
             "## Naechste Schritte",
             _cut(f"- Projekt-Next-Steps: {project}/.agent-memory/context/open-tasks.json ({n_open} offen; Top: {top})",
                  200), "",
             "## Wichtige Pfade", *([f"- {f}" for f in files[:8]] or ["- (keine)"])]
    return "\n".join(lines) + "\n"


def _judged_block(project, now, summary, iterations, tasks, cross, repo, top):
    """Template of references/handoff-template.md, filled from the judge's lines."""
    checks = summary.get("checks") if isinstance(summary.get("checks"), dict) else {}
    state = summary.get("current_state") or summary.get("active_task") or "siehe Was wurde gemacht"
    files = _paths_of(iterations)
    lines = ["# Letzte Session", "", f"*Datum: {now}*", "*Agent: Claude Code*", f"*Projekt: {project}*", "",
             "## Was wurde gemacht", *[_cut(f"- {x}", 180) for x in summary["what_was_done"][:8]], "",
             "## Aktueller Stand", _cut(f"- {state}", 200), "",
             "## Repo-Status", f"- Branch: {repo['branch']}", f"- Uncommitted changes: {repo['uncommitted']}",
             f"- Letzter Commit: {_cut(repo['last'], 120)}", "",
             "## Offene Punkte / Blocker", *([_cut(f"- {t.get('title', '')}", 180) for t in tasks[:3]] or ["- keine"]),
             _cut(f"- Blocker: {summary.get('blockers') or 'keine'}", 200), "",
             "## Checks", f"- Tests: {checks.get('tests', 'nicht gelaufen')}",
             f"- Lint/Validation: {checks.get('lint', 'nicht gelaufen')}", "",
             "## Naechste Schritte",
             _cut(f"- Projekt-Next-Steps: {project}/.agent-memory/context/open-tasks.json ({len(tasks)} offen; Top: {top})",
                  200), *[_cut(f"- [cross-project] {t}", 180) for t in cross], "",
             "## Wichtige Pfade", *([f"- {f}" for f in files[:8]] or ["- (keine)"])]
    return "\n".join(lines) + "\n"


def _publish(mem, args, block, section, names):
    """Central handoff + status board; the same order for the judged and the headless run."""
    central = os.path.abspath(args.central_dir)
    if _inside(central, mem):
        return {"central": "failed(path guard)", "board": "failed(path guard)"}
    # 7.6a point 5: a foreign block pushed out by cap 5 moves into the board BEFORE the drop;
    # a parallel writer can change what is dropped, so the really dropped blocks get a second pass
    pre, dropped = rescue_dropped(central, names, block), []
    central_status = write_central(central, names, block, dropped_out=dropped)
    post = rescue_blocks(central, dropped)
    return {"central": central_status, "board": write_board(central, names, section),
            "rescued": {"rescued": pre["rescued"] + post["rescued"], "lost": post["lost"],
                        "status": [pre["status"], post["status"]]}}


def _projections(mem, project_root, home):
    out = {"index": _run_script([os.path.join(HERE, "memory_index_projection.py"), mem,
                                 "--project-root", project_root, "--home", home], project_root)}
    agents_md = os.path.join(project_root, "AGENTS.md")
    out["bridge"] = (_run_script([os.path.join(HERE, "bridge_projection.py"), mem, "--agents-md", agents_md],
                                 project_root) if os.path.exists(agents_md) else "skipped(no AGENTS.md)")
    return out


def cmd_apply_headless(args):
    mem = os.path.abspath(args.mem)
    if not os.path.isdir(mem):
        return _emit({"ok": False, "error": f"memory dir not found: {args.mem}"}, 1)
    project_root = os.path.abspath(args.project_root or os.path.dirname(mem))
    base = {"ok": True, "mode": "headless", "dry_run": args.dry_run, "project_root": project_root,
            "identity_status_line": IDENTITY_LINE}

    dirty, err = _dirty_sessions(mem)
    if err:
        return _emit({"ok": False, "mode": "headless", "error": f"plan rejected: {err}",
                      "note": "consolidation marker NOT written - dirty state stays honest"}, 2)
    real = [(p, d) for p, d in dirty if not _is_tail(d)]
    if not real:
        return _emit({**base, "status": "skipped(nothing to consolidate)", "marker_written": False}, 0)
    now_ts = time.time()
    # Activity from the marker fields, the mtime only as fallback: a harvest or stale stamp
    # rewrites old markers and must never make them look active. Tail or not: "maybe running".
    newest = max(hs.last_activity(d, os.path.getmtime(p)) for p, d in dirty)
    if now_ts - newest < args.min_age_minutes * 60:
        return _emit({**base, "status": "skipped(active session)", "marker_written": False}, 0)
    if not all(hs.is_quiet(d, now_ts, hs.transcript_mtime(d), args.quiet_hours) for _, d in dirty):
        # Store level, all or nothing: apply_consolidation resets every dirty file and sets
        # last_wrapup, so one session that has not provably ended defers the whole store.
        return _emit({**base, "status": "deferred(store not quiet)", "marker_written": False}, 0)
    dirty = real
    sessions = [str(d.get("session_id") or os.path.basename(p)) for p, d in dirty]

    cfg = _config(mem)
    project = str(cfg.get("project_id") or os.path.basename(project_root))
    date = _dt.date.today().isoformat()
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    session_id = f"headless-{date}"
    iterations = harvest(project_root, mem, _since(mem, dirty))
    tasks = open_tasks(mem)
    top = _cut(tasks[0]["title"], 100) if tasks else "keine"
    aliases = cfg.get("project_aliases") if isinstance(cfg.get("project_aliases"), list) else []
    names = [project, os.path.basename(project_root), *[str(a) for a in aliases]]
    plan = {"date": date, "session_id": session_id, "iterations": iterations, "consolidate": True,
            "session_summary": {
                "what_was_done": [f"{it.get('type', 'feature')}: {it.get('title', '')}" for it in iterations]
                or ["(keine Commits oder Änderungen geerntet)"],
                "open_items": [_cut(t["title"]) for t in tasks[:5]],
                "next_steps": [f"Projekt-Next-Steps: .agent-memory/context/open-tasks.json "
                               f"({len(tasks)} offen; Top: {top})"],
                "statistics": {"iterations": len(iterations), "errors": 0, "new_patterns": 0},
                "warnings": [HEADLESS_WARNING]}}
    tally = {"iterations_logged": 0, "iterations_skipped_duplicate": 0, "errors_added": 0, "errors_recurred": 0,
             "learnings_added": 0, "session_summary_lines": 0, "dirty_files_consolidated": 0,
             "touched_files_seen": 0, "warnings": []}
    touched = []

    if not iterations:
        # Work happened elsewhere (other repos) or left no trace: close the RECOVERY loop (marker +
        # dirty reset) but never replace a richer summary / handoff block with an empty one.
        empty = {**base, "status": "consolidated(empty)", "project": project, "date": date, "iterations": 0,
                 "sessions": sessions,
                 "handoff": {"central": "skipped(empty harvest)", "board": "skipped(empty harvest)"},
                 "wiki_note": "skipped(empty harvest)", "projections": {}, "reports": {}}
        if args.dry_run:
            return _emit({**empty, "marker_written": False, "files_written": [], "tally": tally}, 0)
        try:
            with store_lock.store_lock(mem):
                aw.apply_consolidation(mem, {"consolidate": True}, session_id, False, touched, tally,
                                       by="headless")
        except (aw.PlanError, OSError, ValueError) as exc:  # ValueError: decode/shape (5.2.1)
            return _emit({**empty, "ok": False, "error": f"marker: {exc}", "marker_written": False}, 2)
        return _emit({**empty, "marker_written": True, "files_written": touched, "tally": tally}, 0)
    # The store lock covers each write block, never a subprocess call below:
    # extract_patterns takes the same lock and would wait for its own parent.
    guard = contextlib.nullcontext() if args.dry_run else store_lock.store_lock(mem)
    try:  # steps 1-3: store writes through the one applier; nothing judge-owned
        with guard:
            aw.validate_plan(mem, plan)
            aw.apply_iterations(mem, plan, date, args.dry_run, touched, tally)
            aw.apply_session_summary(mem, plan, args.dry_run, touched, tally, args.central_dir)
    except (aw.PlanError, OSError, ValueError) as exc:  # ValueError: decode/shape (5.2.1)
        return _emit({"ok": False, "mode": "headless", "error": f"store: {exc}", "files_written": touched,
                      "note": "consolidation marker NOT written - dirty state stays honest"}, 2)

    result = {**base, "status": "consolidated", "project": project, "date": date, "harvest_used": True,
              "iterations": len(iterations), "sessions": sessions}
    if args.dry_run:
        result.update({"handoff": {"central": "dry-run", "board": "dry-run"}, "wiki_note": "dry-run",
                       "projections": {}, "reports": {}, "marker_written": False,
                       "files_written": touched, "tally": tally})
        return _emit(result, 0)

    reports = {"patterns": _run_script([os.path.join(HERE, "extract_patterns.py"), mem, "--update"], project_root)}

    repo = _repo_status(project_root)
    block = _handoff_block(project, now, iterations, tasks, repo, len(tasks), top)
    section = (f"## {project}\n*Updated: {now} by Claude Code (headless)*\n"
               f"- State: automatisch konsolidiert, {len(iterations)} Iterationen\n- Next: {top}\n"
               f"- Repo: branch {repo['branch']}, uncommitted {repo['uncommitted']}, last {repo['last'][:40]}\n\n---\n")
    handoff = _publish(mem, args, block, section, names)

    wiki_root = args.wiki_root or cfg.get("wiki_root") or ""
    if not wiki_root:
        wiki_note = "skipped(no wiki root)"
    elif _inside(wiki_root, mem):
        wiki_note = "failed(path guard)"
    else:
        wiki_note = write_session_note(wiki_root, project, date, iterations, enabled=bool(cfg.get("sync_enabled")),
                                       threshold=cfg.get("session_note_threshold", 1))

    projections = _projections(mem, project_root, args.home)

    try:  # step 9: marker LAST
        with store_lock.store_lock(mem):
            aw.apply_consolidation(mem, plan, session_id, False, touched, tally, by="headless")
    except (aw.PlanError, OSError, ValueError) as exc:  # ValueError: decode/shape (5.2.1)
        return _emit({**result, "ok": False, "error": f"marker: {exc}", "handoff": handoff, "wiki_note": wiki_note,
                      "marker_written": False, "files_written": touched, "tally": tally}, 2)
    if reports["patterns"] != "ok":  # DCO #9696: fail-soft, but never reported as a clean run
        result["status"] = "consolidated(patterns failed)"
    reports["state_hash"] = _run_script([os.path.join(HERE, "preprocess_state.py"), mem, "--write-hash"], project_root)
    result.update({"handoff": handoff, "wiki_note": wiki_note, "projections": projections, "reports": reports,
                   "marker_written": True, "files_written": touched, "tally": tally})
    return _emit(result, 0)


# --- judge path (T-028b) ---------------------------------------------------------------------

CORE_KEYS = ("session_summary", "user_candidates")  # built here from `summary` and `identity`: never two ways in
RULE_TAGS = {"regel", "rule", "verfassung", "constitution"}
FOLLOWUP_KEYS = {"date", "session_id", "bridge_decisions", "rule_explanations", "rule_releases", "corrections",
                 "user_md_supersede", "user_md_retire"}


def _child(script, args, cwd, plan=None, timeout=120):
    """Run a plugin script. -> (returncode, parsed JSON or None, raw stdout). Bytes in and out: UTF-8 on Windows."""
    data = json.dumps(plan, ensure_ascii=False).encode("utf-8") if plan is not None else b""
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", os.path.join(HERE, script), *args], cwd=cwd, input=data,
                           capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, None, f"{type(exc).__name__}: {exc}"
    text = p.stdout.decode("utf-8", "replace")
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    return p.returncode, parsed if isinstance(parsed, dict) else None, text


def _read_plan():
    """stdin as bytes: the console code page must never decide the plan's encoding."""
    try:
        plan = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except (OSError, ValueError) as exc:
        return None, f"unreadable plan: {exc}"
    return (plan, None) if isinstance(plan, dict) else (None, "plan must be a JSON object")


def _applier_args(mem, args, session_id):
    out = [mem, "--session-id", session_id, "--central-dir", os.path.abspath(args.central_dir)]
    if args.identity_store:
        out += ["--identity-store", args.identity_store]
    if args.transcript:
        out += ["--transcript", args.transcript]
    return out


def _session_id(args):
    return args.session_id if args.session_id is not None else os.environ.get("CLAUDE_CODE_SESSION_ID", "")


def _rejected(error):
    return _emit({"ok": False, "error": f"plan rejected: {error}", "marker_written": False,
                  "note": "consolidation marker NOT written - dirty state stays honest"}, 2)


def _check_plan(plan):
    """Judge duties, before anything else runs. -> error text or None."""
    for key in CORE_KEYS:
        if key in plan:
            return f"`{key}` belongs to the core: send `summary` and `identity` instead"
    summary = plan.get("summary")
    if not isinstance(summary, dict):
        return "`summary` is required"
    done = summary.get("what_was_done")
    if not isinstance(done, list) or not [x for x in done if str(x).strip()]:
        return "`summary.what_was_done` needs at least one line"
    identity = plan.get("identity")
    if not isinstance(identity, dict):
        return "`identity` is required: {\"candidates\": [...], \"none_because\": \"...\"}"
    cands = identity.get("candidates")
    if not isinstance(cands, list):
        return "`identity.candidates` must be a list"
    if not cands and not str(identity.get("none_because") or "").strip():
        return "no identity candidates: `identity.none_because` must say why"
    return None


def _is_rule(learning):
    tags = {str(t).lower() for t in learning.get("tags") or []}
    return bool(learning.get("rule_class")) or bool(tags & RULE_TAGS)


def _apply_cap(learnings, cap):
    """Keep the `cap` most important learnings; a rule-class learning never falls to the cap. -> (kept, dropped)."""
    plain = [i for i, x in enumerate(learnings) if isinstance(x, dict) and not _is_rule(x)]
    if len(plain) <= cap:
        return learnings, 0
    def weight(i):
        value = learnings[i].get("importance")
        return value if isinstance(value, (int, float)) else 0  # the applier judges the row; the cap only ranks it

    gone = set(sorted(plain, key=lambda i: (-weight(i), i))[cap:])
    return [x for i, x in enumerate(learnings) if i not in gone], len(gone)


def _fit_summary(done, open_items, warnings):
    """Budget of session-summary.md (contract: SUMMARY_MAX_LINES, DCO #10330). -> the three lists, cut to fit.

    apply_session_summary spends 17 lines on headings and spacing, one on the next-steps pointer and three more when
    there are warnings. Warnings and the session's work come first; open items may go, the pointer names them all,
    and the handoff block and the wiki note keep every line of the work.
    """
    warnings = warnings[:3]
    room = aw.SUMMARY_MAX_LINES - 18 - (3 + len(warnings) if warnings else 0)
    done = done[:max(room - max(1, min(len(open_items), 2)), 1)]
    return done, open_items[:max(room - len(done), 0)], warnings


def _judged_iterations(iterations):
    """A harvest for a judged run: the same facts, without the night run's label."""
    out = []
    for it in iterations:
        row = dict(it)
        row["tags"] = ["harvest" if t == "headless" else t for t in it.get("tags") or []]
        row["summary"] = str(it.get("summary", "")).replace("Harvested headless", "Harvested").replace(" (no judge)", "")
        row["title"] = str(it.get("title", "")).replace("(headless gesammelt)", "(gesammelt)")
        out.append(row)
    return out


def _pattern_count(mem):
    try:
        with open(os.path.join(mem, "patterns", "patterns.json"), encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return 0
    rows = data.get("patterns", []) if isinstance(data, dict) else data
    return len(rows) if isinstance(rows, list) else 0


def _thresholds(mem, cwd):
    """memory-thresholds.sh reports only. -> its THRESHOLD lines."""
    bash = shutil.which("bash")
    if not bash:
        return ["THRESHOLD: nicht geprüft (bash fehlt)"]
    try:
        p = subprocess.run([bash, os.path.join(HERE, "memory-thresholds.sh"), mem], cwd=cwd, capture_output=True,
                           timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"THRESHOLD: nicht geprüft ({type(exc).__name__})"]
    return [ln for ln in p.stdout.decode("utf-8", "replace").splitlines() if ln.startswith("THRESHOLD:")]


def _review_sweep(mem, project_root, home):
    rc, _, native = _child("memory_index_projection.py", ["--print-native-dir", project_root, "--home", home], project_root)
    args = [mem, "--report", os.path.join(mem, "working", "review-sweep.md")]
    if rc == 0 and native.strip():
        args += ["--native-memory", native.strip()]
    rc, _, text = _child("review_sweep.py", args, project_root)
    return text.strip().splitlines()[-1] if rc == 0 and text.strip() else f"failed(exit {rc})"


def _cost_trace(mem, session_id, cwd):
    """Measured from the transcript; without one an estimate, so the trace never has a hole (T-016). Fail-soft."""
    rc, measured, _ = _child("measure_session_cost.py", ["--locate", session_id, "--append-trace", mem, "--task", "wrap-up"], cwd)
    if rc == 0 and (measured or {}).get("ok", True):
        return "ok"
    bash = shutil.which("bash")
    if not bash:
        return "skipped(no transcript, no bash)"
    try:
        p = subprocess.run([bash, os.path.join(HERE, "cost-trace.sh"), "append", "--mem", mem, "--task", "wrap-up",
                            "--class", "cheap-write", "--context-bytes", "0", "--escalated", "0"], cwd=cwd,
                           capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"failed({type(exc).__name__})"
    return "estimate" if p.returncode == 0 else f"failed(exit {p.returncode})"


def _logged_today(mem, date):
    try:
        with open(os.path.join(mem, "iterations", "iteration-log.md"), encoding="utf-8-sig") as fh:
            return any(line.startswith(f"## {date}") for line in fh)
    except (OSError, ValueError):
        return False


def _substantial(mem, project_root, date, iterations, tally):
    """obsidian-sync's gate (wiki-sync-gate): one iteration logged today, a commit today, a new learning or
    decision each warrant a note. A session that logged with /agentic-os:log sends no iterations of its own."""
    return bool(iterations or tally.get("learnings_added") or tally.get("decisions_added")
                or _logged_today(mem, date) or _git(project_root, "log", "--oneline", "--since=midnight"))


def _wiki_line(status):
    if status.startswith("failed("):
        return f"Wiki-Sync fehlgeschlagen: {status[7:-1]}. Session data is safe in .agent-memory/."
    if status.startswith("skipped("):
        return f"Wiki-Sync: übersprungen — {status[8:-1]}"
    return f"Wiki-Sync: Note geschrieben → {status}"


def _sharepoint_touched(dirty):
    for _, data in dirty:
        for key in ("touched_files", "foreign_touched"):
            value = data.get(key)
            if isinstance(value, (list, dict)) and "dynamic_sharepoint" in json.dumps(value, ensure_ascii=False):
                return True
    return False


def _active_warnings(mem):
    """`## Active Warnings` of the last summary: the file is rewritten whole, so the judge carries on what still holds."""
    try:
        with open(os.path.join(mem, "session-summary.md"), encoding="utf-8-sig") as fh:
            lines = fh.read().splitlines()
    except (OSError, ValueError):
        return []
    if "## Active Warnings" not in lines:
        return []
    out = []
    for line in lines[lines.index("## Active Warnings") + 1:]:
        if line.startswith("## "):
            break
        if line.startswith("- "):
            out.append(line[2:])
    return out


def _open_rows(mem):
    rows = aw.load_json(mem, "context/open-tasks.json", [])
    return [r for r in rows if isinstance(r, dict) and r.get("status") not in ("closed", "done")]


def _bridge_rows(rows):
    """Open candidates as full rows, whichever shape apply_wrapup's report helper returns."""
    by_id = {r.get("id"): r for r in rows if isinstance(r, dict)}
    found = aw.open_bridge_candidates(rows)
    return [by_id.get(c.get("id") if isinstance(c, dict) else c) or (c if isinstance(c, dict) else {"id": c})
            for c in found]


def cmd_pre(args):
    """Brief for the judge. Read-only (test: hash and mtime of every file unchanged)."""
    mem = os.path.abspath(args.mem)
    if not os.path.isdir(mem):
        return _emit({"ok": False, "error": f"memory dir not found: {args.mem}"}, 1)
    project_root = os.path.abspath(args.project_root or os.path.dirname(mem))
    session_id = _session_id(args)
    extra = ["--identity-store", args.identity_store] if args.identity_store else []
    _, state, _ = _child("preprocess_state.py", [mem, "--session-id", session_id, *extra], project_root)
    state = state or {}
    errors = list(state.get("validation_errors") or [])
    dirty, err = _dirty_sessions(mem)
    if err:
        errors.append(err)
    date = _dt.date.today().isoformat()
    candidates, rule_open, errors_today, tasks = [], {}, [], []
    try:
        rows = aw.load_json(mem, "learnings/learnings.json", [])
        candidates = [{"id": c.get("id"), "text": _cut(c.get("summary") or c.get("text", ""), 200),
                       "importance": c.get("importance")} for c in _bridge_rows(rows)]
        rule_open = aw.rule_class_open(rows)
    except (aw.PlanError, OSError, ValueError) as exc:
        errors.append(f"learnings/learnings.json: {exc}")
    try:
        errors_today = [e.get("id") for e in aw.load_json(mem, "iterations/errors.json", [])
                        if isinstance(e, dict) and str(e.get("date", ""))[:10] == date]
        tasks = [{"id": t.get("id"), "title": _cut(t.get("title", ""), 160), "status": t.get("status")}
                 for t in _open_rows(mem)]
    except (aw.PlanError, OSError, ValueError) as exc:
        errors.append(str(exc))
    real = [(p, d) for p, d in dirty if not _is_tail(d)]
    return _emit({"ok": True, "session_id": session_id, "date": date, "project_root": project_root,
                  "changed_files": state.get("changed_files", []), "git_diff_summary": state.get("git_diff_summary", ""),
                  "iterations_harvested": _judged_iterations(harvest(project_root, mem, _since(mem, real or dirty))),
                  "errors_today": errors_today, "open_tasks": tasks,
                  "user_candidates": state.get("user_candidates", []),
                  "user_candidates_total": state.get("user_candidates_total", 0),
                  "bridge_candidates": candidates, "rule_class_open": rule_open,
                  "active_warnings": _active_warnings(mem), "threshold_events": state.get("threshold_events", []), "validation_errors": errors}, 0)


def cmd_apply_plan(args):
    mem = os.path.abspath(args.mem)
    if not os.path.isdir(mem):
        return _emit({"ok": False, "error": f"memory dir not found: {args.mem}"}, 1)
    project_root = os.path.abspath(args.project_root or os.path.dirname(mem))
    plan, err = _read_plan()
    err = err or _check_plan(plan)
    if err:
        return _rejected(err)
    dirty, err = _dirty_sessions(mem)
    if err:
        return _rejected(err)

    session_id = _session_id(args)
    date = plan.get("date") or _dt.date.today().isoformat()
    summary, identity = plan["summary"], plan["identity"]
    store = {k: v for k, v in plan.items() if k not in ("summary", "identity", "consolidate")}
    store.update({"date": date, "session_id": session_id, "user_candidates": identity["candidates"]})
    learnings, dropped = _apply_cap(plan.get("learnings") or [], args.learnings_cap)
    if "learnings" in plan:
        store["learnings"] = learnings
    harvest_used = "iterations" not in plan
    if harvest_used:
        store["iterations"] = _judged_iterations(harvest(project_root, mem, _since(mem, dirty)))
    iterations = store["iterations"]
    warnings = []
    if dropped:
        warnings.append(f"learnings cap {args.learnings_cap}: {dropped} Learnings mit der niedrigsten Wichtigkeit "
                        "verworfen")

    applier = _applier_args(mem, args, session_id)
    rc, main_out, raw = _child("apply_wrapup.py", [*applier, *(["--dry-run"] if args.dry_run else [])], project_root, store)
    if rc != 0 or not main_out:
        return _emit({**(main_out or {"error": f"apply_wrapup exit {rc}: {_cut(raw, 300)}"}), "ok": False,
                      "mode": "wrap-up", "marker_written": False}, 2)
    tally = main_out.get("tally", {})
    warnings += tally.get("warnings", [])
    base = {"ok": True, "mode": "wrap-up", "dry_run": args.dry_run, "project_root": project_root, "date": date,
            "harvest_used": harvest_used, "learnings_dropped_by_cap": dropped,
            "identity_status_line": main_out.get("identity_status_line"),
            "near_duplicates": main_out.get("near_duplicates", {}), "tally": tally}
    if args.dry_run:
        return _emit({**base, "warnings": warnings, "marker_written": False}, 0)

    # Side scripts first, so the summary states the measured pattern count. The store lock is never
    # held across a subprocess: each child takes it itself.
    reports = {}
    rc, _, text = _child("ingest_codex_memory.py", [mem], project_root)
    reports["codex_ingest"] = text.strip().splitlines()[-1] if rc == 0 and text.strip() else f"failed(exit {rc})"
    patterns_before = _pattern_count(mem)
    rc, extracted, _ = _child("extract_patterns.py", [mem, "--update"], project_root)
    reports["patterns"] = "ok" if rc == 0 else f"failed(exit {rc})"

    tasks = _open_rows(mem)
    titles = [_cut(t.get("title", "")) for t in tasks]
    top = titles[0] if titles else "keine"
    errors = sum(len(it.get("errors") or []) for it in iterations)
    done, open_items, active = _fit_summary(summary["what_was_done"], titles[:5],
                                            [str(x) for x in summary.get("warnings") or []])
    session_summary = {"what_was_done": done, "open_items": open_items,
                       "next_steps": [f"Projekt-Next-Steps: .agent-memory/context/open-tasks.json "
                                      f"({len(tasks)} offen; Top: {top})"],
                       "statistics": {"iterations": len(iterations), "errors": errors,
                                      "new_patterns": max(0, _pattern_count(mem) - patterns_before)},
                       "warnings": active}
    if isinstance(summary.get("handoff"), dict):  # Handoff Mode: rendered by the applier as `## Handoff Context`
        session_summary["handoff"] = summary["handoff"]
    rc, written, raw = _child("apply_wrapup.py", applier, project_root,
                              {"date": date, "session_id": session_id, "session_summary": session_summary})
    if rc != 0:
        return _emit({**base, "ok": False, "error": f"summary: {_cut((written or {}).get('error') or raw, 300)}",
                      "marker_written": False, "reports": reports}, 2)
    warnings += (written or {}).get("tally", {}).get("warnings", [])

    cfg = _config(mem)
    project = str(cfg.get("project_id") or os.path.basename(project_root))
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    repo = _repo_status(project_root)
    aliases = cfg.get("project_aliases") if isinstance(cfg.get("project_aliases"), list) else []
    names = [project, os.path.basename(project_root), *[str(a) for a in aliases]]
    cross = [_cut(t.get("title", "")) for t in tasks if t.get("cross_project")]
    block = _judged_block(project, now, summary, iterations, tasks, cross, repo, top)
    state = _cut(summary.get("current_state") or summary.get("active_task") or summary["what_was_done"][0], 120)
    section = (f"## {project}\n*Updated: {now} by Claude Code*\n- State: {state}\n- Next: {top}\n"
               f"- Repo: branch {repo['branch']}, uncommitted {repo['uncommitted']}, last {repo['last'][:40]}\n\n---\n")
    handoff = _publish(mem, args, block, section, names)

    wiki_root = args.wiki_root or cfg.get("wiki_root") or ""
    if not wiki_root:
        wiki_note = "skipped(no wiki root)"
    elif _inside(wiki_root, mem):
        wiki_note = "failed(path guard)"
    elif not _substantial(mem, project_root, date, iterations, tally):
        wiki_note = "skipped(Session nicht substanziell)"
    else:
        wiki_note = write_session_note(wiki_root, project, date, iterations, enabled=bool(cfg.get("sync_enabled")),
                                       summary=summary, session_id=session_id)

    projections = _projections(mem, project_root, args.home)
    reports["threshold_lines"] = _thresholds(mem, project_root)
    reports["review_sweep"] = _review_sweep(mem, project_root, args.home)

    result = {**base, "status": "consolidated", "project": project, "handoff": handoff, "wiki_note": wiki_note,
              "wiki_sync_line": _wiki_line(wiki_note), "projections": projections, "reports": reports,
              "pattern_proposals": len((extracted or {}).get("proposals") or []), "bridge_candidates": [],
              "rule_class_open": tally.get("rule_class_open"), "sharepoint_touched": _sharepoint_touched(dirty),
              "repo": repo, "warnings": warnings}
    for line in ("rule_class_status_line", "bridge_status_line"):
        if main_out.get(line):
            result[line] = main_out[line]
    try:
        result["bridge_candidates"] = [{"id": c.get("id"), "text": _cut(c.get("summary") or c.get("text", ""), 200)}
                                       for c in _bridge_rows(aw.load_json(mem, "learnings/learnings.json", []))]
    except (aw.PlanError, OSError, ValueError) as exc:
        warnings.append(f"bridge candidates: {exc}")

    touched, marker_tally = [], {**aw.new_tally(), **tally}  # the marker records this run's measured counts
    try:  # marker LAST: handoff and note must not land behind it as tail writes
        with store_lock.store_lock(mem):
            aw.apply_consolidation(mem, {"consolidate": True}, session_id, False, touched, marker_tally)
    except (aw.PlanError, OSError, ValueError) as exc:
        return _emit({**result, "ok": False, "error": f"marker: {exc}", "marker_written": False}, 2)
    result["dirty_files_consolidated"] = marker_tally["dirty_files_consolidated"]
    reports["cost_trace"] = _cost_trace(mem, session_id, project_root)
    reports["state_hash"] = _run_script([os.path.join(HERE, "preprocess_state.py"), mem, "--write-hash"], project_root)
    return _emit({**result, "marker_written": True}, 0)


def cmd_followup(args):
    """Verdicts that need ids which exist only after `apply`; both projections follow."""
    mem = os.path.abspath(args.mem)
    if not os.path.isdir(mem):
        return _emit({"ok": False, "error": f"memory dir not found: {args.mem}"}, 1)
    project_root = os.path.abspath(args.project_root or os.path.dirname(mem))
    plan, err = _read_plan()
    if err:
        return _rejected(err)
    foreign = sorted(set(plan) - FOLLOWUP_KEYS)
    if foreign:
        return _rejected(f"followup takes only verdicts, not {', '.join(foreign)}")
    rc, out, raw = _child("apply_wrapup.py", _applier_args(mem, args, _session_id(args)), project_root, plan)
    if rc != 0 or not out:
        return _emit({**(out or {"error": f"apply_wrapup exit {rc}: {_cut(raw, 300)}"}), "ok": False}, 2)
    out["projections"] = _projections(mem, project_root, args.home)
    out["state_hash"] = _run_script([os.path.join(HERE, "preprocess_state.py"), mem, "--write-hash"], project_root)
    return _emit(out, 0)


def _common(parser, writes=True):
    parser.add_argument("--mem", default=".agent-memory")
    parser.add_argument("--project-root", default=None, help="default: parent of --mem")
    parser.add_argument("--session-id", default=None, help="default: $CLAUDE_CODE_SESSION_ID")
    parser.add_argument("--identity-store", default=None)
    if writes:
        parser.add_argument("--central-dir", default=os.path.join(os.path.expanduser("~"), "AI"))
        parser.add_argument("--home", default=os.path.expanduser("~"), help="home for the native MEMORY.md projection")
        parser.add_argument("--transcript", default=None)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="wrap-up core (T-028): judge path and headless consolidation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    _common(sub.add_parser("pre", help="read-only brief for the judge"), writes=False)
    _common(sub.add_parser("followup", help="verdicts that need ids from apply; plan on stdin"))
    a = sub.add_parser("apply", help="judge plan on stdin, or --headless without a plan")
    _common(a)
    a.add_argument("--headless", action="store_true", help="no plan, no judge (night run)")
    a.add_argument("--wiki-root", default=None, help="default: config.json wiki_root")
    a.add_argument("--learnings-cap", type=int, default=3, help="judge path: learnings kept per run, rule-class exempt")
    a.add_argument("--min-age-minutes", type=int, default=120)
    a.add_argument("--quiet-hours", type=float, default=hs.QUIET_HOURS,
                   help="a session without session_end counts as ended after this long without activity")
    a.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "pre":
        return cmd_pre(args)
    if args.cmd == "followup":
        return cmd_followup(args)
    return cmd_apply_headless(args) if args.headless else cmd_apply_plan(args)


if __name__ == "__main__":
    sys.exit(main())

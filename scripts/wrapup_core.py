#!/usr/bin/env python3
"""wrap-up core, headless path (T-028; V3 spec §4.2 steps 1-9 without a plan).

    python wrapup_core.py apply --headless --mem <project>/.agent-memory
        [--central-dir ~/AI] [--home ~] [--wiki-root <dir>] [--min-age-minutes 120] [--dry-run]

Consolidates un-wrapped sessions of ONE project without a model: iterations from git log +
uncommitted dirty files, session summary, central handoff + status board, wiki session note,
pattern update, projections, then the consolidation marker LAST (apply_wrapup.apply_consolidation).

Never headless: learnings, decisions, identity (apply_user_candidates would promote the queue to
user.md), soul.md, open-task edits. Those need the judge (T-028b).

Error policy (spec §5.4): unreadable dirty evidence or a store failure -> exit 2, no marker;
handoff / wiki / projection / report failures are fail-soft (`failed(<reason>)`), marker is written.
Skips (exit 0, nothing written): no dirty session, or the newest dirty file is younger than
--min-age-minutes (a session is probably still running).
The plan path (judge, `pre`, identity duty) is not implemented yet -> exit 1.
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
from preprocess_state import open_tasks  # noqa: E402
from wrapup_parts.handoff import write_board, write_central  # noqa: E402
from wrapup_parts.harvest import harvest  # noqa: E402
from wrapup_parts.wikinote import write_session_note  # noqa: E402

IDENTITY_LINE = "Identity: headless — kein Harvest"
# Same downgrade rule as the RECOVERY line in session-start.sh: a dirty file that carries
# last_consolidated_at and at most this many writes since is wrap-up's own tail, not lost work.
TAIL_WRITES_MAX = 5
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
    """True when the dirty file only holds wrap-up's own writes after its marker (session-start.sh rule)."""
    if not data.get("last_consolidated_at"):
        return False
    try:
        return int(data.get("writes_since_consolidation", 0)) <= TAIL_WRITES_MAX
    except (TypeError, ValueError):
        return False


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
    if not git or not os.path.isdir(os.path.join(project_root, ".git")):
        return ""
    try:
        p = subprocess.run([git, "-C", project_root, *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30, stdin=subprocess.DEVNULL)
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _repo_status(project_root):
    porcelain = [ln for ln in _git(project_root, "status", "--porcelain").splitlines()
                 if ".agent-memory/" not in ln.replace("\\", "/")]
    return {"branch": _git(project_root, "rev-parse", "--abbrev-ref", "HEAD") or "n/a",
            "uncommitted": "ja" if porcelain else "nein",
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


def _handoff_block(project, now, iterations, tasks, repo, n_open, top):
    done = [_cut(f"- {it.get('type', 'feature')}: {it.get('title', '')}", 180)
            for it in iterations[:8]] or ["- (nichts geerntet)"]
    files = []
    for it in iterations:
        for f in it.get("files_changed") or []:
            if f not in files:
                files.append(f)
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
    newest = max(os.path.getmtime(p) for p, _ in dirty)  # any fresh write, tail or not, means "maybe running"
    if time.time() - newest < args.min_age_minutes * 60:
        return _emit({**base, "status": "skipped(active session)", "marker_written": False}, 0)
    dirty = real

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
                 "handoff": {"central": "skipped(empty harvest)", "board": "skipped(empty harvest)"},
                 "wiki_note": "skipped(empty harvest)", "projections": {}, "reports": {}}
        if args.dry_run:
            return _emit({**empty, "marker_written": False, "files_written": [], "tally": tally}, 0)
        try:
            aw.apply_consolidation(mem, {"consolidate": True}, session_id, False, touched, tally)
        except (aw.PlanError, OSError) as exc:
            return _emit({**empty, "ok": False, "error": f"marker: {exc}", "marker_written": False}, 2)
        return _emit({**empty, "marker_written": True, "files_written": touched, "tally": tally}, 0)
    try:  # steps 1-3: store writes through the one applier; nothing judge-owned
        aw.validate_plan(mem, plan)
        aw.apply_iterations(mem, plan, date, args.dry_run, touched, tally)
        aw.apply_session_summary(mem, plan, args.dry_run, touched, tally)
    except (aw.PlanError, OSError) as exc:
        return _emit({"ok": False, "mode": "headless", "error": f"store: {exc}", "files_written": touched,
                      "note": "consolidation marker NOT written - dirty state stays honest"}, 2)

    result = {**base, "status": "consolidated", "project": project, "date": date, "harvest_used": True,
              "iterations": len(iterations)}
    if args.dry_run:
        result.update({"handoff": {"central": "dry-run", "board": "dry-run"}, "wiki_note": "dry-run",
                       "projections": {}, "reports": {}, "marker_written": False,
                       "files_written": touched, "tally": tally})
        return _emit(result, 0)

    reports = {"patterns": _run_script([os.path.join(HERE, "extract_patterns.py"), mem, "--update"], project_root)}

    central = os.path.abspath(args.central_dir)
    repo = _repo_status(project_root)
    if _inside(central, mem):
        handoff = {"central": "failed(path guard)", "board": "failed(path guard)"}
    else:
        block = _handoff_block(project, now, iterations, tasks, repo, len(tasks), top)
        section = (f"## {project}\n*Updated: {now} by Claude Code (headless)*\n"
                   f"- State: automatisch konsolidiert, {len(iterations)} Iterationen\n- Next: {top}\n"
                   f"- Repo: branch {repo['branch']}, uncommitted {repo['uncommitted']}, last {repo['last'][:40]}\n\n---\n")
        handoff = {"central": write_central(central, names, block), "board": write_board(central, names, section)}

    wiki_root = args.wiki_root or cfg.get("wiki_root") or ""
    if not wiki_root:
        wiki_note = "skipped(no wiki root)"
    elif _inside(wiki_root, mem):
        wiki_note = "failed(path guard)"
    else:
        wiki_note = write_session_note(wiki_root, project, date, iterations, enabled=bool(cfg.get("sync_enabled")),
                                       threshold=cfg.get("session_note_threshold", 1))

    projections = {"index": _run_script([os.path.join(HERE, "memory_index_projection.py"), mem,
                                         "--project-root", project_root, "--home", args.home], project_root)}
    agents_md = os.path.join(project_root, "AGENTS.md")
    projections["bridge"] = (_run_script([os.path.join(HERE, "bridge_projection.py"), mem, "--agents-md", agents_md],
                                         project_root) if os.path.exists(agents_md) else "skipped(no AGENTS.md)")

    try:  # step 9: marker LAST
        aw.apply_consolidation(mem, plan, session_id, False, touched, tally)
    except (aw.PlanError, OSError) as exc:
        return _emit({**result, "ok": False, "error": f"marker: {exc}", "handoff": handoff, "wiki_note": wiki_note,
                      "marker_written": False, "files_written": touched, "tally": tally}, 2)
    reports["state_hash"] = _run_script([os.path.join(HERE, "preprocess_state.py"), mem, "--write-hash"], project_root)
    result.update({"handoff": handoff, "wiki_note": wiki_note, "projections": projections, "reports": reports,
                   "marker_written": True, "files_written": touched, "tally": tally})
    return _emit(result, 0)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="wrap-up core (T-028): headless consolidation of one project")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply")
    a.add_argument("--headless", action="store_true", help="no plan, no judge (the only implemented path)")
    a.add_argument("--mem", default=".agent-memory")
    a.add_argument("--project-root", default=None, help="default: parent of --mem")
    a.add_argument("--central-dir", default=os.path.join(os.path.expanduser("~"), "AI"))
    a.add_argument("--home", default=os.path.expanduser("~"), help="home for the native MEMORY.md projection")
    a.add_argument("--wiki-root", default=None, help="default: config.json wiki_root")
    a.add_argument("--min-age-minutes", type=int, default=120)
    a.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if not args.headless:
        return _emit({"ok": False, "error": "plan path (judge) not implemented yet - T-028b; use --headless"}, 1)
    return cmd_apply_headless(args)


if __name__ == "__main__":
    sys.exit(main())

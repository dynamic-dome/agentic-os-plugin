#!/usr/bin/env python3
"""One-time backfill of the harvest state for markers from before 5.6.0 (Phase 2, Scheibe 8, D-024).

Before 5.5.0 the night run stamped the markers it consolidated `consolidated_by: "wrap-up"`
although no judge ran. Under 5.6.0 every such marker is `pending` and the GC keeps it forever.
This script sorts the clean (dirty: false) markers of every discovered store:

  victim    consolidated by a night run - its sid is named in the iteration log ("Harvested
            headless from working/dirty-<sid>.json") OR its consolidated_at falls into a night
            run of this project (metrics/nightly-consolidation.jsonl). A victim closes only
            when its transcript (recorded path, else ~/.claude/projects/*/<sid>.jsonl) shows a
            real wrap-up with <= 5 writes after it, or a native note names it; else it stays
            pending for the night run and the stale stamp.
  legacy    every other marker stamped "wrap-up": closed_by "legacy".

Dirty, already closed, headless-stamped and unstamped markers are never touched. Default is
a dry run; --apply writes each closure under the store lock with a snapshot check
(harvest_state.close_marker). Learnings, decisions and identity are never touched (D-001).

    python scripts/harvest_backfill.py                 # dry run over the stores under ~
    python scripts/harvest_backfill.py --apply         # after the owner's j
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harvest_state as hs  # noqa: E402
from nightly_consolidate import discover, HOME, _default_report  # noqa: E402

NIGHT_WINDOW_SECONDS = 1800  # a night run consolidates its projects within minutes of its ts
_VICTIM_LINE = re.compile(r"Harvested headless from working/dirty-([A-Za-z0-9_-]+)\.json")
E7_FOR_VICTIMS = ("wrapup_im_transkript", "native_note")


def night_runs(report_path: str) -> dict:
    """normcase(project_root) -> [start epoch] of every real night run that consolidated it."""
    runs = {}
    try:
        with open(report_path, encoding="utf-8-sig") as fh:
            lines = fh.readlines()
    except OSError:
        return runs
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        start = hs._epoch(r.get("ts")) if isinstance(r, dict) else None
        if start is None or r.get("dry_run"):
            continue
        for p in r.get("projects") or []:
            if isinstance(p, dict) and str(p.get("status", "")).startswith("consolidated") and p.get("project_root"):
                runs.setdefault(os.path.normcase(os.path.normpath(p["project_root"])), []).append(start)
    return runs


def victim_sids(mem: str) -> set:
    try:
        with open(os.path.join(mem, "iterations", "iteration-log.md"), encoding="utf-8-sig", errors="replace") as fh:
            return set(_VICTIM_LINE.findall(fh.read()))
    except OSError:
        return set()


def transcript_index(native_root: str | None = None) -> dict:
    """sid -> transcript path for every <root>/<project>/<sid>.jsonl (top level only, scanned once)."""
    index = {}
    try:
        projects = [e for e in os.scandir(native_root or hs._default_native_root()) if e.is_dir()]
    except OSError:
        return index
    for proj in projects:
        try:
            for e in os.scandir(proj.path):
                if e.name.endswith(".jsonl") and e.is_file():
                    index.setdefault(e.name[:-len(".jsonl")], e.path)
        except OSError:
            continue
    return index


def classify(d: dict, sid: str, victims: set, runs: list, tindex: dict, native_sids: set):
    """(category, closed_by or None). Pure apart from reading a found transcript."""
    state = hs.marker_state(d)
    if state != "pending":
        return state, None  # "dirty" or "closed": never touched
    stamp = d.get("consolidated_by") or d.get("last_consolidated_by")
    if stamp == "headless":
        return "headless", None  # 5.5.0+ night run: the nightly close pass owns it
    if stamp != "wrap-up":
        return "unstamped", None
    at = hs._epoch(d.get("consolidated_at"))
    night = at is not None and any(0 <= at - start <= NIGHT_WINDOW_SECONDS for start in runs)
    if not (sid in victims or night):
        return "legacy", "legacy"
    tp = d.get("transcript_path") if isinstance(d.get("transcript_path"), str) and d.get("transcript_path") \
        else tindex.get(sid)
    facts = hs.gather_facts(dict(d, transcript_path=tp), native_sids=native_sids)
    reason = hs.close_reason(dict(d, transcript_path=tp), facts)
    if reason in E7_FOR_VICTIMS:  # never trivial/transcript_fehlt: old markers lack reliable tracker data
        return "victim_closed", reason
    return "victim_open", None


def backfill_store(mem: str, runs: list, tindex: dict, native_sids: set, apply: bool, now_iso: str) -> dict:
    res = {"counts": {}, "closed": [], "victims_open": [], "unreadable": [], "refused": []}
    victims = victim_sids(mem)
    work = os.path.join(mem, "working")
    try:
        names = sorted(n for n in os.listdir(work) if hs._MARKER_NAME.match(n))
    except OSError:
        return res
    for name in names:
        try:
            with open(os.path.join(work, name), encoding="utf-8-sig") as fh:
                d = json.load(fh)
            if not isinstance(d, dict):
                raise ValueError("not an object")
        except (OSError, ValueError):
            res["unreadable"].append(name)
            continue
        sid = str(d.get("session_id") or name[len("dirty-"):-len(".json")])
        cat, reason = classify(d, sid, victims, runs, tindex, native_sids)
        res["counts"][cat] = res["counts"].get(cat, 0) + 1
        if cat == "victim_open":
            res["victims_open"].append(sid)
        if reason:
            if not apply or hs.close_marker(mem, name, reason, "backfill", hs.snapshot(d), now_iso=now_iso):
                res["closed"].append({"sid": sid, "closed_by": reason})
            else:
                res["refused"].append(sid)  # changed since read, or locked: the next run retries
    return res


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="one-time harvest-state backfill for pre-5.6.0 markers (D-024)")
    ap.add_argument("--root", action="append", default=None, help="search root (default ~)")
    ap.add_argument("--max-depth", type=int, default=4)
    ap.add_argument("--deny", action="append", default=None)
    ap.add_argument("--report", default=None, help="nightly-consolidation.jsonl (default as the night run)")
    ap.add_argument("--native-root", default=None, help="~/.claude/projects")
    ap.add_argument("--apply", action="store_true", help="write the closures (default: dry run)")
    a = ap.parse_args(argv)
    deny = a.deny if a.deny is not None else [os.path.join(HOME, "ich")]
    runs = night_runs(a.report or _default_report())
    tindex = transcript_index(a.native_root)
    native_sids = hs.native_session_ids(a.native_root)
    now_iso = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    out = {"apply": a.apply, "stores": [], "totals": {}}
    for proj in discover(a.root or [HOME], a.max_depth, deny):
        mem = os.path.join(proj, ".agent-memory")
        res = backfill_store(mem, runs.get(os.path.normcase(os.path.normpath(proj)), []), tindex, native_sids,
                             a.apply, now_iso)
        if not any(res["counts"].values()) and not res["unreadable"]:
            continue
        for k, v in res["counts"].items():
            out["totals"][k] = out["totals"].get(k, 0) + v
        for k in ("unreadable", "refused"):
            out["totals"][k] = out["totals"].get(k, 0) + len(res[k])
        out["stores"].append({"project_root": proj, **res})
    # "closed" in the class counts = closed before this run; "closed_now" = closures of this run
    out["totals"]["closed_now"] = sum(len(s["closed"]) for s in out["stores"])
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

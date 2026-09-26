#!/usr/bin/env python3
"""Decay report for the memory hub (spec §4). REPORT ONLY — never mutates.

(a) learnings with review_after < today (not superseded, not retired)
(b) native auto-memory notes not linked from MEMORY.md and older than --stale-days
(c) bridge candidates older than --candidate-days (decision overdue)

Usage: python review_sweep.py <mem-dir> [--native-memory <dir>] [--stale-days 90]
       [--candidate-days 30] [--report <md>] [--today YYYY-MM-DD]
Exit: 0 ok · 1 learnings.json unreadable · 2 usage.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

LINK_RE = re.compile(r"\]\(([^)]+\.md)\)")


def load_rows(mem_dir):
    path = os.path.join(mem_dir, "learnings", "learnings.json")
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [r for r in (data if isinstance(data, list) else data.get("learnings", [])) if isinstance(r, dict)]


def native_stale(native, stale_days, today):
    if not native or not os.path.isdir(native):
        return []
    linked = set()
    idx = os.path.join(native, "MEMORY.md")
    if os.path.isfile(idx):
        with open(idx, "r", encoding="utf-8") as f:
            for t in LINK_RE.findall(f.read()):
                linked.add(os.path.basename(t))
    cutoff = dt.datetime.combine(today, dt.time()).timestamp() - stale_days * 86400
    out = []
    for name in sorted(os.listdir(native)):
        p = os.path.join(native, name)
        if not os.path.isfile(p) or name == "MEMORY.md" or not name.endswith(".md"):
            continue
        if name in linked:
            continue
        mtime = os.path.getmtime(p)
        if mtime < cutoff:
            out.append((name, dt.date.fromtimestamp(mtime).isoformat()))
    return out


def main(argv):
    ap = argparse.ArgumentParser(prog="review_sweep.py")
    ap.add_argument("mem_dir")
    ap.add_argument("--native-memory")
    ap.add_argument("--stale-days", type=int, default=90)
    ap.add_argument("--candidate-days", type=int, default=30)
    ap.add_argument("--report")
    ap.add_argument("--today")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    try:
        rows = load_rows(a.mem_dir)
    except (OSError, ValueError) as exc:
        print(f"review-sweep: learnings.json unreadable: {exc}", file=sys.stderr)
        return 1

    due = [r for r in rows if r.get("review_after") and not r.get("superseded_by")
           and r.get("status") != "retired" and str(r["review_after"]) < today.isoformat()]
    cand_cutoff = (today - dt.timedelta(days=a.candidate_days)).isoformat()
    cands = [r for r in rows if r.get("bridge_status") == "candidate" and str(r.get("date", "")) < cand_cutoff]
    stale = native_stale(a.native_memory, a.stale_days, today)

    if a.report:
        lines = [f"# Review Sweep {today.isoformat()}", "",
                 f"## (a) review_after fällig ({len(due)})"]
        lines += [f"- [{r.get('id')}] (review_after {r.get('review_after')}) {str(r.get('text', ''))[:120]}" for r in due]
        lines += ["", f"## (b) Native Auto-Memory nicht indexiert, älter als {a.stale_days} Tage ({len(stale)})"]
        lines += [f"- {name} (mtime {m})" for name, m in stale]
        lines += ["", f"## (c) Bridge-Kandidaten älter als {a.candidate_days} Tage ({len(cands)})"]
        lines += [f"- [{r.get('id')}] candidate since {r.get('date')} {str(r.get('text', ''))[:120]}" for r in cands]
        lines += ["", "Entscheidung pro Eintrag: behalten (review_after +90) · ersetzen (superseded_by) · archivieren (status: retired / _archive/). Nichts wird automatisch geändert."]
        os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
        tmp = a.report + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, a.report)
    print(f"review-sweep: due={len(due)} native_stale={len(stale)} candidates_stale={len(cands)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Decay report for the memory hub (spec §4). REPORT ONLY — never mutates.

(a) learnings with review_after < today (not superseded, not retired)
(b) native auto-memory notes not linked from MEMORY.md and older than --stale-days
(c) bridge candidates older than --candidate-days (decision overdue)

--charges (5.3.0) groups every reviewable learning into charges instead:
"due:YYYY-MM" by review_after month, "ohne-termin" for rows without a date and
"restored:<store>" for rows brought back from an archive (derived_from
"restored:..."). Without these two charges, rows lacking review_after - 366 live
plus every restored one - never reach a review at all (plan blocker #3).

Usage: python review_sweep.py <mem-dir> [--native-memory <dir>] [--stale-days 90]
       [--candidate-days 30] [--report <md>] [--today YYYY-MM-DD]
       python review_sweep.py <mem-dir> --charges [--json]
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
    with open(path, "r", encoding="utf-8-sig") as f:  # a BOM is not corruption
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


def reviewable(r):
    # bridge_status is the real field (5.2.1): 'retired' is withdrawn, 'rejected' is
    # the owner's final veto (D18) - neither is up for review. The legacy 'status'
    # stays excluded for rows an older footer told someone to mark by hand.
    return (not r.get("superseded_by") and r.get("bridge_status") not in ("retired", "rejected")
            and r.get("status") != "retired")


def store_label(mem_dir):
    root = os.path.dirname(os.path.abspath(mem_dir).rstrip("\/"))
    return os.path.basename(root) or "store"


def charges(rows, label):
    """Every reviewable row lands in exactly one charge (read-only)."""
    out = {}
    for r in rows:
        if not reviewable(r):
            continue
        if any(str(d).startswith("restored:") for d in (r.get("derived_from") or [])
               if isinstance(r.get("derived_from"), list)):
            name = f"restored:{label}"
        elif r.get("review_after"):
            name = f"due:{str(r['review_after'])[:7]}"
        else:
            name = "ohne-termin"
        out.setdefault(name, []).append(str(r.get("id")))
    return [{"name": n, "count": len(ids), "ids": ids} for n, ids in sorted(out.items())]


def main(argv):
    ap = argparse.ArgumentParser(prog="review_sweep.py")
    ap.add_argument("mem_dir")
    ap.add_argument("--native-memory")
    ap.add_argument("--stale-days", type=int, default=90)
    ap.add_argument("--candidate-days", type=int, default=30)
    ap.add_argument("--report")
    ap.add_argument("--today")
    ap.add_argument("--charges", action="store_true", help="group reviewable rows into charges")
    ap.add_argument("--json", action="store_true", help="with --charges: print JSON")
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

    if a.charges:
        found = charges(rows, store_label(a.mem_dir))
        if a.json:
            print(json.dumps({"store": store_label(a.mem_dir), "charges": found}, ensure_ascii=False))
        else:
            for c in found:
                print(f"{c['name']}: {c['count']}")
        return 0

    due = [r for r in rows if r.get("review_after") and reviewable(r)
           and str(r["review_after"]) < today.isoformat()]
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
        lines += ["", "Entscheidung pro Eintrag: behalten · ersetzen (superseded_by mit Nachfolger) · zurückziehen (bridge_status retired). Nichts wird automatisch geändert und nichts archiviert; bis es einen Schreibweg dafür gibt, werden Entscheidungen nur notiert."]
        os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
        tmp = a.report + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, a.report)
    print(f"review-sweep: due={len(due)} native_stale={len(stale)} candidates_stale={len(cands)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

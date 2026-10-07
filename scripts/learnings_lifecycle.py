#!/usr/bin/env python3
"""learnings_lifecycle.py — READ-ONLY proposals for condensation (5.4.0, D-021 (3)).

Condensing means ASSIGNING learnings to a rule that already exists (8 of 10
hand-condensed principles were already a rule in CLAUDE.md, a wiki concept page
or a convention file); a new rule is the exception. This script only does the
mechanical half and writes nothing:

  propose  For every eligible learning, the top catalog sections by TF-IDF
           cosine. The model classifies (rule / new rule / fact), the owner
           decides at the gate, the `principles` plan section of
           apply_wrapup.py writes the pointer (/agentic-os:maintain Step 5c).
  report   eligible / anchored / pointers - the share that is already a member
           of a pointer (plan target: >= 60 % after the first run).

Eligible: not superseded, not a pointer, not a codex tip, importance >= N
(default 4), older than D days (default 30), not yet a member of a pointer.

Catalog: markdown files given with --catalog, or the list "rule_catalog" in
~/.claude/agentic-os.local.json (paths stay out of the code - export hygiene).
Each heading opens a section; its anchor is "<path>#<heading text>", the form
apply_wrapup.py resolves.

Usage:
  python learnings_lifecycle.py propose <mem> [--catalog a.md b.md] [--top 3]
         [--min-importance 4] [--min-age-days 30] [--today YYYY-MM-DD]
  python learnings_lifecycle.py report <mem> [--min-importance 4] [--min-age-days 30]
Exit: 0 ok · 1 usage / no catalog · 2 unreadable store.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import math
import os
import re
import sys
from collections import Counter

LOCAL_CONFIG = os.path.join(os.path.expanduser("~"), ".claude", "agentic-os.local.json")
TOKEN = re.compile(r"[a-zäöüß0-9]+")
STOP = set("""der die das den dem des ein eine einer eines einem und oder aber nicht ist sind war
wird werden mit von vom zum zur auf aus bei fuer für als auch nur noch schon dann wenn weil dass
sich sie ihr wir ich man kein keine nach vor ueber über unter durch the and for with that this
from are was not but all any can its has have into will must""".split())


def tokens(text):
    return [t for t in TOKEN.findall(str(text or "").lower()) if len(t) >= 3 and t not in STOP]


def load_rows(mem):
    path = os.path.join(mem, "learnings", "learnings.json")
    with open(path, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    rows = data if isinstance(data, list) else data.get("learnings", [])
    return [r for r in rows if isinstance(r, dict)]


def members_of_pointers(rows):
    return {m for r in rows if r.get("kind") == "principle" and not r.get("superseded_by")
            for m in (r.get("derived_from") or []) if isinstance(m, str)}


def eligible(rows, min_imp, min_age, today):
    cutoff = (today - _dt.timedelta(days=min_age)).isoformat()
    out = []
    for r in rows:
        imp = r.get("importance")
        if (r.get("superseded_by") or r.get("kind") == "principle" or r.get("source_agent") == "codex"
                or not isinstance(imp, int) or isinstance(imp, bool) or imp < min_imp):
            continue
        if str(r.get("date", ""))[:10] > cutoff:
            continue
        out.append(r)
    return out


def catalog_paths(given):
    if given:
        return given
    try:
        with open(LOCAL_CONFIG, encoding="utf-8-sig") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return []
    paths = []
    for pat in cfg.get("rule_catalog") or []:
        paths += sorted(glob.glob(os.path.expanduser(pat))) or [os.path.expanduser(pat)]
    return paths


def sections(paths):
    """-> [(anchor, heading, text)] - one section per markdown heading."""
    out = []
    for path in paths:
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        heading, body = None, []
        for line in lines + ["# <eof>"]:
            m = re.match(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
            if m:
                if heading is not None:
                    out.append((f"{path}#{heading}", heading, heading + "\n" + "\n".join(body)))
                heading, body = m.group(1).strip(), []
            elif heading is not None:
                body.append(line)
    return out


def tfidf(docs):
    df = Counter()
    toks = [Counter(tokens(d)) for d in docs]
    for t in toks:
        df.update(set(t))
    n = len(docs) or 1
    idf = {w: math.log((1 + n) / (1 + c)) + 1 for w, c in df.items()}
    vecs = []
    for t in toks:
        v = {w: c * idf[w] for w, c in t.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({w: x / norm for w, x in v.items()})
    return vecs, idf


def vectorize(text, idf):
    t = Counter(tokens(text))
    v = {w: c * idf[w] for w, c in t.items() if w in idf}
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {w: x / norm for w, x in v.items()}


def cosine(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def cmd_propose(a, rows, today):
    paths = catalog_paths(a.catalog)
    secs = sections(paths)
    if not secs:
        print(json.dumps({"ok": False, "error": "no rule catalog: pass --catalog or set "
                          f"'rule_catalog' in {LOCAL_CONFIG}"}))
        return 1
    vecs, idf = tfidf([s[2] for s in secs])
    taken = members_of_pointers(rows)
    proposals = []
    for r in eligible(rows, a.min_importance, a.min_age_days, today):
        if str(r.get("id")) in taken:
            continue
        q = vectorize(r.get("text", ""), idf)
        scored = sorted(((cosine(q, v), i) for i, v in enumerate(vecs)), reverse=True)[:a.top]
        proposals.append({
            "id": r.get("id"), "importance": r.get("importance"), "date": r.get("date"),
            "text": " ".join(str(r.get("text", "")).split())[:240],
            "matches": [{"anchor": secs[i][0], "heading": secs[i][1], "score": round(sc, 3)}
                        for sc, i in scored if sc > 0],
        })
    print(json.dumps({"ok": True, "catalog_files": len(paths), "catalog_sections": len(secs),
                      "proposals": proposals}, ensure_ascii=False, indent=2))
    return 0


def cmd_report(a, rows, today):
    pool = eligible(rows, a.min_importance, a.min_age_days, today)
    taken = members_of_pointers(rows)
    anchored = sum(1 for r in pool if str(r.get("id")) in taken)
    pointers = sum(1 for r in rows if r.get("kind") == "principle" and not r.get("superseded_by"))
    print(json.dumps({"ok": True, "eligible": len(pool), "anchored": anchored, "pointers": pointers,
                      "share": round(anchored / len(pool), 3) if pool else None}, ensure_ascii=False))
    return 0


def main(argv) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass
    ap = argparse.ArgumentParser(prog="learnings_lifecycle.py")
    ap.add_argument("cmd", choices=("propose", "report"))
    ap.add_argument("mem")
    ap.add_argument("--catalog", nargs="*")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--min-importance", type=int, default=4)
    ap.add_argument("--min-age-days", type=int, default=30)
    ap.add_argument("--today")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 1
    today = _dt.date.fromisoformat(a.today) if a.today else _dt.date.today()
    try:
        rows = load_rows(a.mem)
    except (OSError, ValueError) as e:
        print(json.dumps({"ok": False, "error": f"learnings.json unreadable: {e}"}))
        return 2
    return cmd_propose(a, rows, today) if a.cmd == "propose" else cmd_report(a, rows, today)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

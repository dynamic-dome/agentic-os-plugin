#!/usr/bin/env python3
"""Rule-class learnings: read-only queue and Erklaerbogen (DCO #9869, owner decision F4).

A learning that changes a rule or the constitution is held back from AGENTS.md and
MEMORY.md until a second model explained it and the owner put it into two sentences
of his own. This script never writes and never calls a model: it lists the queue and
renders the sheet the session hands to the second model. The verdicts reach the store
only through apply_wrapup.py (plan sections `rule_explanations`, `rule_releases`).

Usage:
  python learning_release.py list  <mem-dir> [--json]
  python learning_release.py sheet <mem-dir> <learning-id> [--out <file>]
Exit: 0 ok - 1 learnings.json unreadable - 2 usage / not a rule-class learning.
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(os.path.dirname(_HERE), "skills", "wrap-up", "references", "erklaerbogen-vorlage.md")
STATES = ("needs_explanation", "explained", "released", "rejected")


def load_rows(mem):
    path = os.path.join(mem, "learnings", "learnings.json")
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    return [r for r in (data if isinstance(data, list) else data.get("learnings", [])) if isinstance(r, dict)]


def queue(rows):
    out = {s: [] for s in STATES}
    for r in rows:
        if r.get("rule_class") is True and not r.get("superseded_by"):
            st = (r.get("release") or {}).get("status", "needs_explanation")
            out.setdefault(st, []).append({
                "id": r.get("id"), "date": r.get("date"), "text": r.get("text"),
                "explained_by": (r.get("release") or {}).get("explained_by")})
    return out


def render_sheet(row):
    with open(TEMPLATE, "r", encoding="utf-8") as fh:
        text = fh.read()
    fill = {"id": str(row.get("id")), "date": str(row.get("date")), "text": str(row.get("text") or ""),
            "tags": ", ".join(row.get("tags") or []) or "-",
            "status": (row.get("release") or {}).get("status", "needs_explanation")}
    for key, value in fill.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def main(argv):
    ap = argparse.ArgumentParser(prog="learning_release.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("mem")
    ls.add_argument("--json", action="store_true")
    sh = sub.add_parser("sheet")
    sh.add_argument("mem")
    sh.add_argument("id")
    sh.add_argument("--out")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return 2
    try:
        rows = load_rows(args.mem)
    except (OSError, ValueError) as exc:
        print(f"learning_release: learnings.json unreadable: {exc}", file=sys.stderr)
        return 1
    if args.cmd == "list":
        q = queue(rows)
        if args.json:
            print(json.dumps(q, ensure_ascii=False, indent=2))
        else:
            for st in STATES:
                print(f"{st}: {len(q[st])}" + "".join(f"\n  {r['id']} {str(r['text'])[:100]}" for r in q[st]))
        return 0
    matches = [r for r in rows if str(r.get("id")) == args.id]
    if len(matches) != 1 or matches[0].get("rule_class") is not True:
        print(f"learning_release: {args.id} is not a (unique) rule-class learning", file=sys.stderr)
        return 2
    sheet = render_sheet(matches[0])
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(sheet)
        print(f"learning_release: sheet for {args.id} -> {args.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(sheet)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

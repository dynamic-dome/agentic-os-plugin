#!/usr/bin/env python3
"""learnings_top.py — deterministic salience ranking for session-bootstrap's
heuristic fallback. Moves the ranking OUT of the model context: instead of
reading the full learnings.json (can be 2k+ words), bootstrap runs this and
gets only the top-N lines.

Usage: python scripts/learnings_top.py [path/to/learnings.json] [--top 10] [--tags tag1,tag2]
Output: one line per learning: [ID] (importance) [STALE?] text
Exit 0 with no output if file missing/empty (bootstrap skips the section).
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path


def main() -> int:
    args = sys.argv[1:]
    path = Path(".agent-memory/learnings/learnings.json")
    top_n = 10
    stack_tags: set[str] = set()
    i = 0
    while i < len(args):
        if args[i] == "--top" and i + 1 < len(args):
            top_n = int(args[i + 1]); i += 2
        elif args[i] == "--tags" and i + 1 < len(args):
            stack_tags = {t.strip().lower() for t in args[i + 1].split(",") if t.strip()}; i += 2
        else:
            path = Path(args[i]); i += 1

    if not path.is_file():
        return 0
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print("WARN: learnings.json unreadable/corrupt", file=sys.stderr)
        return 0
    if not isinstance(entries, list):
        return 0

    today = date.today()

    def score(e: dict) -> tuple:
        imp = float(e.get("importance", 1))
        try:
            last = datetime.strptime(e.get("last_relevant") or e.get("date", ""), "%Y-%m-%d").date()
            days = (today - last).days
        except ValueError:
            days = 9999
        recency = max(0.0, 1.0 - days / 90.0)
        tags = {str(t).lower() for t in e.get("tags", [])}
        overlap = (len(tags & stack_tags) / len(tags)) if tags and stack_tags else 0.0
        layer_bonus = 0.001 if e.get("layer") == "long-term" else 0.0
        return (imp * 0.4 + recency * 0.3 + overlap * 0.3 + layer_bonus, e.get("id", ""))

    live = [e for e in entries if isinstance(e, dict) and not e.get("superseded_by")]
    live.sort(key=score, reverse=True)

    for e in live[:top_n]:
        try:
            last = datetime.strptime(e.get("last_relevant") or e.get("date", ""), "%Y-%m-%d").date()
            stale = (today - last).days > 90
        except ValueError:
            stale = True
        marker = f" [STALE? last relevant {e.get('last_relevant', '?')}]" if stale else ""
        print(f"[{e.get('id', '?')}] ({e.get('importance', '?')}){marker} {e.get('text', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

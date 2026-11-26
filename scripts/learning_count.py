#!/usr/bin/env python3
"""Counts the learnings of a learnings.json by parsing it (6.0.2): the one count of the Pflegelauf trigger
(memory-thresholds.sh) and of its stamp (apply_wrapup.py pflegelauf_stamp), so both measure alike (L154).

    python learning_count.py PATH                  # prints the number of learnings, exit 0
    python learning_count.py PATH --before DATE    # only those dated before DATE (YYYY-MM-DD)

A learning is a JSON object with an "id" in the top-level list; format, tags and text do not matter. A row without
a readable date (missing, null, not YYYY-MM-DD at the start) counts as dated before any DATE: counted as new it would
come back after every run. A missing file counts 0. An unreadable file prints nothing and exits 1 - the caller then
knows the count is unknown. Read only.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys


def rows(text: str) -> list | None:
    """The learning rows of a learnings.json text; None when it is no JSON list."""
    try:
        data = json.loads(text) if text.strip() else []
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    return [r for r in data if isinstance(r, dict) and "id" in r]


def row_day(row) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(str(row.get("date") or "")[:10])
    except ValueError:
        return None


def count(text: str, before: str | None = None) -> int | None:
    found = rows(text)
    if found is None:
        return None
    if before is None:
        return len(found)
    limit = _dt.date.fromisoformat(before)
    return sum(1 for r in found if (row_day(r) is None or row_day(r) < limit))


def main(argv: list[str]) -> int:
    if not argv or len(argv) not in (1, 3) or (len(argv) == 3 and argv[1] != "--before"):
        print("usage: learning_count.py PATH [--before YYYY-MM-DD]", file=sys.stderr)
        return 2
    try:
        with open(argv[0], "rb") as fh:
            text = fh.read().decode("utf-8-sig")
    except FileNotFoundError:
        text = ""
    except (OSError, UnicodeDecodeError):
        return 1
    try:
        n = count(text, argv[2] if len(argv) == 3 else None)
    except ValueError:  # --before is no date
        return 2
    if n is None:
        return 1
    print(n)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

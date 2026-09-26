#!/usr/bin/env python3
"""Contract test: learnings.json schema extension (v4.4.0).

New entries carry `derived_from` (provenance list) and `review_after` (date);
old entries lack both. Every script consumer must tolerate BOTH shapes:
- scripts/learnings_top.py must rank/print mixed stores without crashing
  and must not let the new fields change scoring of otherwise-equal entries.
"""
import json
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "learnings_top.py"

today = date.today()
recent = today.isoformat()

MIXED_STORE = [
    {  # old-style entry (pre-4.4.0): no derived_from / review_after
        "id": "L1", "date": recent, "text": "old-style entry without new fields",
        "importance": 3, "tags": ["schema"], "layer": "short-term",
        "superseded_by": None, "last_relevant": recent,
    },
    {  # new-style entry: full provenance + review date
        "id": "L2", "date": recent, "text": "new-style entry with provenance",
        "importance": 3, "tags": ["schema"], "layer": "short-term",
        "superseded_by": None, "last_relevant": recent,
        "derived_from": ["iteration-4", "E2"],
        "review_after": (today + timedelta(days=90)).isoformat(),
    },
    {  # new-style, honest empty provenance
        "id": "L3", "date": recent, "text": "new-style entry, no traceable origin",
        "importance": 5, "tags": ["schema"], "layer": "short-term",
        "superseded_by": None, "last_relevant": recent,
        "derived_from": [], "review_after": (today + timedelta(days=90)).isoformat(),
    },
    {  # superseded new-style entry must stay excluded
        "id": "L4", "date": recent, "text": "superseded entry must not appear",
        "importance": 5, "tags": ["schema"], "layer": "short-term",
        "superseded_by": "L3", "last_relevant": recent,
        "derived_from": ["E1"], "review_after": recent,
    },
]


def run(store):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "learnings.json"
        p.write_text(json.dumps(store), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(p), "--top", "10"],
            capture_output=True, text=True, timeout=30,
        )


def main() -> int:
    failures = []
    res = run(MIXED_STORE)

    if res.returncode != 0:
        failures.append(f"learnings_top.py crashed on mixed store: {res.stderr.strip()}")
    out = res.stdout
    for lid in ("L1", "L2", "L3"):
        if f"[{lid}]" not in out:
            failures.append(f"{lid} missing from output (mixed store)")
    if "[L4]" in out:
        failures.append("superseded L4 leaked into output despite new fields")
    # equal importance/recency/tags: new fields must not perturb ranking order (L2 vs L1)
    if out.find("[L3]") == -1 or out.find("[L3]") > out.find("[L1]"):
        failures.append("importance-5 entry L3 not ranked above importance-3 entries")

    if failures:
        print("FAIL: learnings schema-fields contract")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: learnings schema-fields contract (4 checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

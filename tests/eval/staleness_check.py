#!/usr/bin/env python3
"""Baseline staleness check for the skill-redesign harness (T-35).

Non-fatal. Warns when a Schicht-2 baseline no longer reflects the current
session-bootstrap body, so a forgotten re-capture becomes visible instead of a
silent self-deception. Always exits 0 — this is a NOTICE, not a gate (Schicht 1
is the gate). A real behavioral regression is caught by check_sideeffects.py when
someone actually runs the capture protocol.

Logic per baseline/<scenario>.json:
  - _body_sha256 == null  -> NOTE: hand-authored, awaiting first real capture
  - _body_sha256 != current bootstrap body sha -> WARN: stale, re-capture
  - match                 -> silent

    python tests/eval/staleness_check.py
"""
import glob
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(HERE))
BOOTSTRAP = os.path.join(PLUGIN_ROOT, "skills", "session-bootstrap", "SKILL.md")


def body_sha(path):
    """Hash the body with line endings normalized to LF, so a CRLF checkout of
    the same git content does not produce a platform-dependent false-stale hash.
    Capture must record _body_sha256 the same way (normalized)."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        text = f.read().replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main():
    current = body_sha(BOOTSTRAP)

    notes, warns = [], []
    for p in sorted(glob.glob(os.path.join(HERE, "baseline", "*.json"))):
        name = os.path.basename(p)[:-5]
        try:
            d = json.load(open(p, encoding="utf-8"))
        except ValueError:
            warns.append(f"{name}: baseline is not valid JSON")
            continue
        recorded = d.get("_body_sha256")
        if recorded is None:
            notes.append(f"{name}: hand-authored, awaiting first real capture")
        elif recorded != current:
            warns.append(f"{name}: baseline stale vs current body — re-capture "
                         "(capture_protocol.md)")

    if notes:
        print("Schicht-2 baseline NOTES (non-fatal):")
        for n in notes:
            print(f"  NOTE: {n}")
    if warns:
        print("Schicht-2 baseline WARNINGS (non-fatal):")
        for w in warns:
            print(f"  WARN: {w}")
    if not notes and not warns:
        print("Schicht-2 baselines current.")
    sys.exit(0)


if __name__ == "__main__":
    main()

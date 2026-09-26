#!/usr/bin/env python3
"""Schicht 2 (behavioral, break-glass) diff for the skill-redesign harness (T-35).

Compares a captured side-effect signature (produced by a real agent run against a
fixture, per capture_protocol.md) with the frozen baseline for that scenario. Used
ONLY on demand before an aggressive redesign deploy — NOT part of run-all.sh.

Diff semantics (memevalharness.md):
  - gates_fired, files_written, questions_asked,
    json_keys_touched                            : EXACT match (behavior invariants)
  - briefing_blocks                              : SET match (order/wording free)
  - free text                                    : ignored
  - metrics/ writes (fail-soft cost-trace)       : excluded from files_written —
    they are telemetry, not a behavioral gate (see capture_protocol.md).

    python tests/eval/check_sideeffects.py <scenario> <captured.json>
        exit 0 = matches baseline, 1 = drift (or missing baseline)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE_DIR = os.path.join(HERE, "baseline")

EXACT_KEYS = ["gates_fired", "files_written", "questions_asked", "json_keys_touched"]
SET_KEYS = ["briefing_blocks"]


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def norm_files(v):
    """files_written -> order-free list of (path, op).

    Excludes fail-soft telemetry (metrics/). A malformed (non-dict) entry is NOT
    silently dropped — it becomes a distinct sentinel so it forces a diff instead
    of hiding a corrupted write record.
    """
    out = []
    for d in v or []:
        if not isinstance(d, dict):
            out.append(("<malformed>", repr(d)))
            continue
        path = d.get("path") or ""
        if path.replace("\\", "/").startswith("metrics/"):
            continue  # telemetry, not behavior
        out.append((path, d.get("op")))
    return sorted(out, key=lambda t: (str(t[0]), str(t[1])))


def norm_keys(v):
    """json_keys_touched -> {file: sorted(unique keys)} for order-free compare."""
    out = {}
    for f, keys in (v or {}).items():
        out[f] = sorted(set(keys)) if isinstance(keys, list) else keys
    return out


def diff(baseline, captured):
    problems = []

    for key in EXACT_KEYS:
        b = baseline.get(key)
        c = captured.get(key)
        if key == "files_written":
            b, c = norm_files(b), norm_files(c)
        elif key == "json_keys_touched":
            b, c = norm_keys(b), norm_keys(c)
        if b != c:
            problems.append(f"{key}: baseline={b!r} != captured={c!r}")

    for key in SET_KEYS:
        b = set(baseline.get(key) or [])
        c = set(captured.get(key) or [])
        if b != c:
            missing = b - c
            extra = c - b
            problems.append(
                f"{key}: missing={sorted(missing)} extra={sorted(extra)}")

    return problems


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    scenario, captured_path = sys.argv[1], sys.argv[2]
    baseline_path = os.path.join(BASELINE_DIR, f"{scenario}.json")

    if not os.path.isfile(baseline_path):
        print(f"FAIL: no baseline for scenario '{scenario}' ({baseline_path})")
        print("  -> capture one first (see capture_protocol.md), then commit it.")
        sys.exit(1)

    baseline = load(baseline_path)
    captured = load(captured_path)
    problems = diff(baseline, captured)

    if not problems:
        print(f"PASS: '{scenario}' side-effects match baseline.")
        sys.exit(0)

    print(f"FAIL: '{scenario}' side-effects DRIFTED from baseline:")
    for p in problems:
        print(f"  - {p}")
    sys.exit(1)


if __name__ == "__main__":
    main()

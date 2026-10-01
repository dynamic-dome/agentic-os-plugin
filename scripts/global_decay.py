#!/usr/bin/env python3
"""global_decay.py — /agentic-os:maintain Step 4b (global-decay), deterministic.

Confidence of entries in the global layer (~/.claude-memory/global/learnings.json and
patterns.json) decays by 0.1 per full 90-day step without recall, floored at 0.3.
This is the ONLY place confidence decays — never on the read path.

WHY THE BOOKKEEPING
-------------------
The rule used to be applied to the stored value: every maintain run subtracted all
due steps again, so two runs in the same quarter decayed the same step twice. Each
entry now books what it already paid:

  decay_steps_applied  full 90-day steps already subtracted
  decay_anchor         the last_relevant value those steps were counted from

Only the delta (due - applied) is subtracted. A genuine recall moves last_relevant;
the anchor then no longer matches and the counter restarts at 0 without any writer
having to reset it. An entry that carries decay_steps_applied but no anchor (the
manual workaround of 2026-10-01) counts as booked against its current last_relevant.

Rules (unchanged): floor 0.3 — a value already below it is never raised; decayed
confidence <= 0.3 AND last_relevant older than 365 days -> lifecycle "archived".
Never hard-delete. Archived entries are out of scope. Entries without a parsable
last_relevant, with a non-numeric confidence or an invalid decay_steps_applied are
left untouched and reported as "skipped" with their id.

Usage:
  python global_decay.py <global-dir>            # preview, writes nothing
  python global_decay.py <global-dir> --apply    # write the decayed values
  [--today YYYY-MM-DD]                           # fixed date for tests
Exit codes: 0 ok · 1 unreadable store (nothing written) · 2 usage error.
"""
import argparse
import datetime as _dt
import json
import os
import sys

FILES = ("learnings.json", "patterns.json")
STEP_DAYS = 90
STEP = 0.1
FLOOR = 0.3
ARCHIVE_DAYS = 365


def parse_day(value):
    """'YYYY-MM-DD' or an ISO datetime -> date; anything else -> None."""
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class InvalidBookkeeping(ValueError):
    """The entry's data cannot be evaluated (unparsable last_relevant, non-numeric
    confidence, decay_steps_applied not a non-negative int): leave it alone, report it."""


def decay_entry(e, today):
    """Apply due-but-unbooked steps to one entry in place. Returns (decayed, archived).
    Raises InvalidBookkeeping BEFORE touching the entry. Archived entries are out of
    scope by design and never reported."""
    if e.get("lifecycle") == "archived":
        return False, False
    anchor = e.get("last_relevant")
    day = parse_day(anchor)
    if day is None:
        raise InvalidBookkeeping(f"{e.get('id')}: last_relevant={anchor!r}")
    conf = e.get("confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        raise InvalidBookkeeping(f"{e.get('id')}: confidence={conf!r}")
    booked = e.get("decay_steps_applied", 0)
    if isinstance(booked, bool) or not isinstance(booked, int) or booked < 0:
        raise InvalidBookkeeping(f"{e.get('id')}: decay_steps_applied={booked!r}")
    age = (today - day).days
    due = max(age // STEP_DAYS, 0)
    if e.get("decay_anchor", anchor) != anchor:
        booked = 0  # a recall moved last_relevant: count from the new anchor
    delta = due - booked
    decayed = False
    if delta > 0:
        new = conf if conf <= FLOOR else max(FLOOR, round(conf - STEP * delta, 2))
        decayed = new != conf
        e["confidence"] = new
        e["decay_steps_applied"] = due
        e["decay_anchor"] = anchor
    archived = False
    if e["confidence"] <= FLOOR and age > ARCHIVE_DAYS:
        e["lifecycle"] = "archived"
        archived = True
    return decayed, archived


def write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass
    ap = argparse.ArgumentParser(prog="global_decay.py")
    ap.add_argument("global_dir")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--today")
    try:
        a = ap.parse_args(argv)
        today = _dt.date.fromisoformat(a.today) if a.today else _dt.date.today()
    except (SystemExit, ValueError):
        return 2
    if not os.path.isdir(a.global_dir):
        print(f"global-decay: not a directory: {a.global_dir}", file=sys.stderr)
        return 2

    # Read everything first: one unreadable file must not leave the other half-applied.
    stores = []
    for name in FILES:
        path = os.path.join(a.global_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                raw = f.read()
            rows = json.loads(raw)
        except (OSError, ValueError) as exc:
            print(f"global-decay: {name} unreadable: {exc}", file=sys.stderr)
            return 1
        if not isinstance(rows, list):
            print(f"global-decay: {name} is not a JSON list", file=sys.stderr)
            return 1
        stores.append((name, path, raw, rows))

    n_decayed = n_archived = n_unchanged = n_skipped = 0
    details = []
    writes = []
    for name, path, raw, rows in stores:
        changed = False
        for e in rows:
            if not isinstance(e, dict):
                continue
            before = json.dumps(e, sort_keys=True)
            old_conf = e.get("confidence")
            try:
                decayed, archived = decay_entry(e, today)
            except InvalidBookkeeping as exc:
                n_skipped += 1
                details.append(f"  skipped {exc}")
                continue
            n_decayed += decayed
            n_archived += archived
            n_unchanged += not (decayed or archived)
            if decayed or archived:
                details.append(f"  {e.get('id')}: {old_conf} -> {e['confidence']}"
                               + (" (archived)" if archived else ""))
            # bookkeeping alone (a step booked at the floor) is a change worth writing
            changed = changed or json.dumps(e, sort_keys=True) != before
        if changed:
            writes.append((path, json.dumps(rows, indent=2, ensure_ascii=False)
                           + ("\n" if raw.endswith("\n") else "")))
    # Write only after every store was computed: no half-applied pass.
    if a.apply:
        for path, text in writes:
            write_atomic(path, text)

    mode = "applied" if a.apply else "preview"
    print(f"global-decay: {n_decayed} decayed, {n_archived} archived, "
          f"{n_unchanged} unchanged, {n_skipped} skipped ({mode})")
    for line in details:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Tests for scripts/global_decay.py — /agentic-os:maintain Step 4b (global-decay).

Rule under test: -0.1 per full 90-day step since last_relevant, floored at 0.3, and
every step is booked exactly once (decay_steps_applied + decay_anchor). Running the
pass twice must not decay the same step twice — the bug the bookkeeping exists for.

Every case runs in tempfile.TemporaryDirectory(); the real global store is never touched.
Run: python tests/test-global-decay.py   (exit 0 = pass, 1 = fail)
"""
import json
import os
import subprocess
import sys
import tempfile

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "global_decay.py")
TODAY = "2026-10-01"
FAILURES = []


def check(cond, msg):
    if cond:
        print(f"  PASS: {msg}")
    else:
        print(f"  FAIL: {msg}")
        FAILURES.append(msg)


def entry(eid, conf, last_relevant, **extra):
    e = {"id": eid, "confidence": conf, "last_relevant": last_relevant, "lifecycle": "candidate"}
    e.update(extra)
    return e


def put(d, name, rows, trailing_newline=False):
    text = json.dumps(rows, indent=2, ensure_ascii=False) + ("\n" if trailing_newline else "")
    with open(os.path.join(d, name), "w", encoding="utf-8", newline="") as f:
        f.write(text)


def raw(d, name):
    with open(os.path.join(d, name), "rb") as f:
        return f.read()


def rows(d, name):
    return {e["id"]: e for e in json.loads(raw(d, name).decode("utf-8"))}


def run(d, *extra, today=TODAY):
    return subprocess.run([sys.executable, SCRIPT, d, "--today", today, *extra],
                          capture_output=True, encoding="utf-8", errors="replace")


def main():
    print("=== global_decay.py tests ===")
    check(os.path.isfile(SCRIPT), "script exists")
    if not os.path.isfile(SCRIPT):
        print("=== 1 failure (script missing) ===")
        return 1

    # 1. one 90-day step decays once; a second run decays nothing (the 2026-10-01 bug)
    with tempfile.TemporaryDirectory() as d:
        put(d, "learnings.json", [entry("G-learning-001", 0.7, "2026-06-01")])  # 122 days -> 1 step
        p = run(d, "--apply")
        check(p.returncode == 0, f"exit 0 ({p.stderr.strip()[:200]})")
        e = rows(d, "learnings.json")["G-learning-001"]
        check(e["confidence"] == 0.6, f"first run: 0.7 -> 0.6 ({e['confidence']})")
        check(e.get("decay_steps_applied") == 1 and e.get("decay_anchor") == "2026-06-01",
              f"step booked with its anchor ({e.get('decay_steps_applied')}, {e.get('decay_anchor')})")
        before = raw(d, "learnings.json")
        p = run(d, "--apply")
        check(raw(d, "learnings.json") == before, "second run on the same day: file byte-identical")
        check("0 decayed" in p.stdout, f"second run reports 0 decayed ({p.stdout.strip()})")

    # 2. only the delta is applied when a later step falls due
    with tempfile.TemporaryDirectory() as d:
        put(d, "patterns.json", [entry("G-pattern-002", 0.8, "2026-03-01", decay_steps_applied=1)])  # 214 d -> 2 steps
        run(d, "--apply")
        e = rows(d, "patterns.json")["G-pattern-002"]
        check(e["confidence"] == 0.7 and e["decay_steps_applied"] == 2,
              f"booked 1 of 2 due steps -> exactly one more decrement ({e['confidence']}, {e['decay_steps_applied']})")

    # 3. legacy bookkeeping from the manual workaround (field, no anchor) counts against
    #    the current last_relevant -> nothing due twice
    with tempfile.TemporaryDirectory() as d:
        put(d, "learnings.json", [entry("G-learning-003", 0.5, "2026-05-25", decay_steps_applied=1)])
        before = raw(d, "learnings.json")
        run(d, "--apply")
        check(raw(d, "learnings.json") == before, "legacy field without anchor is honoured as booked")

    # 4. a recall moves last_relevant -> the old counter no longer applies, a newly due
    #    step decays again (a counter without anchor would swallow it)
    with tempfile.TemporaryDirectory() as d:
        put(d, "learnings.json", [entry("G-learning-004", 0.9, "2026-06-15",
                                        decay_steps_applied=2, decay_anchor="2025-12-01")])
        run(d, "--apply")
        e = rows(d, "learnings.json")["G-learning-004"]
        check(e["confidence"] == 0.8 and e["decay_steps_applied"] == 1 and e["decay_anchor"] == "2026-06-15",
              f"recalled entry decays from its new anchor ({e['confidence']}, {e.get('decay_steps_applied')}, {e.get('decay_anchor')})")

    # 5. timestamps in last_relevant (sync-context writes ISO datetimes) are read as dates
    with tempfile.TemporaryDirectory() as d:
        put(d, "patterns.json", [entry("G-pattern-005", 0.6, "2026-07-01T09:41:49")])  # 92 days -> 1 step
        p = run(d, "--apply")
        e = rows(d, "patterns.json")["G-pattern-005"]
        check(p.returncode == 0 and e["confidence"] == 0.5 and e["decay_anchor"] == "2026-07-01T09:41:49",
              f"datetime last_relevant handled, anchor kept verbatim ({p.returncode}, {e['confidence']})")

    # 6. floor 0.3: decays toward, never past it — and never RAISES a value below it
    with tempfile.TemporaryDirectory() as d:
        put(d, "learnings.json", [entry("A", 0.35, "2026-03-01"),            # 2 steps -> 0.3
                                  entry("B", 0.2, "2026-03-01"),             # below floor: untouched
                                  entry("C", 0.7, "2026-09-01")])            # 30 days: no step
        run(d, "--apply")
        r = rows(d, "learnings.json")
        check(r["A"]["confidence"] == 0.3, f"floored at 0.3 ({r['A']['confidence']})")
        check(r["B"]["confidence"] == 0.2, f"a value below the floor is not raised ({r['B']['confidence']})")
        check(r["C"]["confidence"] == 0.7 and "decay_steps_applied" not in r["C"],
              "under 90 days: no decay, no bookkeeping field")

    # 7. archive past 365 days at the floor; never hard-delete; archived entries untouched
    with tempfile.TemporaryDirectory() as d:
        put(d, "learnings.json", [entry("OLD", 0.4, "2025-08-01"),           # 426 d -> 4 steps -> 0.3, archived
                                  entry("KEEP", 0.9, "2025-08-01"),          # 0.9 - 0.4 = 0.5 -> stays
                                  entry("GONE", 0.3, "2024-01-01", lifecycle="archived")])
        before_gone = rows(d, "learnings.json")["GONE"]
        p = run(d, "--apply")
        r = rows(d, "learnings.json")
        check(len(r) == 3, "no entry deleted")
        check(r["OLD"]["lifecycle"] == "archived" and r["OLD"]["confidence"] == 0.3,
              f"<= 0.3 and > 365 days -> lifecycle archived ({r['OLD']})")
        check(r["KEEP"]["lifecycle"] == "candidate" and r["KEEP"]["confidence"] == 0.5,
              f"above the floor stays live ({r['KEEP']})")
        check(r["GONE"] == before_gone, "already archived entry left as is")
        check("1 archived" in p.stdout, f"archive count reported ({p.stdout.strip()})")

    # 8. preview (default) writes nothing; format of the file is preserved on --apply
    with tempfile.TemporaryDirectory() as d:
        put(d, "learnings.json", [entry("P", 0.7, "2026-06-01", text="Umlaute bleiben: äöü")])
        put(d, "patterns.json", [entry("Q", 0.7, "2026-09-30")], trailing_newline=True)
        before_l, before_p = raw(d, "learnings.json"), raw(d, "patterns.json")
        p = run(d)
        check(raw(d, "learnings.json") == before_l and "preview" in p.stdout,
              f"preview leaves the store untouched ({p.stdout.strip()})")
        run(d, "--apply")
        after = raw(d, "learnings.json")
        check("äöü".encode("utf-8") in after and not after.endswith(b"\n"),
              "non-ASCII kept unescaped, no trailing newline added")
        check(raw(d, "patterns.json") == before_p, "unchanged file not rewritten (trailing newline kept)")

    # 9. invalid bookkeeping in one store must not leave the other half-applied
    #    (Codex verifier P2): the entry is skipped and reported, everything else applies
    with tempfile.TemporaryDirectory() as d:
        put(d, "learnings.json", [entry("DUE", 0.7, "2026-06-01")])
        put(d, "patterns.json", [entry("BAD", 0.9, "2026-01-01", decay_steps_applied="unbekannt"),
                                 entry("NEG", 0.9, "2026-01-01", decay_steps_applied=-3)])
        before_p = raw(d, "patterns.json")
        p = run(d, "--apply")
        check(p.returncode == 0 and rows(d, "learnings.json")["DUE"]["confidence"] == 0.6,
              f"valid store still decays ({p.returncode}, {p.stderr.strip()[:160]})")
        check(raw(d, "patterns.json") == before_p, "entries with invalid bookkeeping left untouched")
        check("2 skipped" in p.stdout, f"invalid bookkeeping reported ({p.stdout.strip()})")

    # 9b. entries that can never decay because their data is unusable are reported, not
    #     silently counted as unchanged; archived entries are out of scope by design
    with tempfile.TemporaryDirectory() as d:
        put(d, "learnings.json", [entry("NODATE", 0.7, "irgendwann"),
                                  entry("NOCONF", "hoch", "2026-01-01"),
                                  entry("ARCH", 0.3, "2024-01-01", lifecycle="archived",
                                        decay_steps_applied=-3)])
        before = raw(d, "learnings.json")
        p = run(d, "--apply")
        check(raw(d, "learnings.json") == before, "unusable entries left untouched")
        check("0 decayed, 0 archived, 1 unchanged, 2 skipped" in p.stdout
              and "NODATE" in p.stdout and "NOCONF" in p.stdout,
              f"unparsable last_relevant / non-numeric confidence -> skipped with id ({p.stdout.strip()})")

    # 10. usage / unreadable store
    with tempfile.TemporaryDirectory() as d:
        check(run(os.path.join(d, "missing")).returncode == 2, "missing dir -> exit 2")
        with open(os.path.join(d, "learnings.json"), "w", encoding="utf-8") as f:
            f.write("{oops")
        put(d, "patterns.json", [entry("Z", 0.9, "2026-01-01")])
        before = raw(d, "patterns.json")
        p = run(d, "--apply")
        check(p.returncode == 1 and raw(d, "patterns.json") == before,
              f"unreadable JSON -> exit 1 and nothing written ({p.returncode})")

    n = len(FAILURES)
    print(f"=== {n} failure{'s' if n != 1 else ''} ===")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

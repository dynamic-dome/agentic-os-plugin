#!/usr/bin/env python3
"""Tests for scripts/native_memory_audit.py — read-only audit of Claude Code's
native project memory stores (~/.claude/projects/*/memory), T-36.

Rules under test:
  - classification: age of newest note <= 21 d -> "active", else "dormant"
  - frozen flag: dormant AND (newest - oldest) <= 7 d (write-once store)
  - orphans: *.md notes (except MEMORY.md) not linked from MEMORY.md
  - dead_links: MEMORY.md links to *.md files that do not exist
  - injection level from MEMORY.md size: >=16384 "critical", >=10240 "warn", else "ok"
  - audit() scans only <root>/*/memory dirs, honors --exclude, is READ-ONLY
  - CLI writes --json/--md reports, exits 0, survives non-cp1252 output

Run: python tests/test-native-memory-audit.py   (exit 0 = pass, 1 = fail)
"""
import json
import os
import subprocess
import sys
import tempfile
import time

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                      "native_memory_audit.py")

FAILURES = []
DAY = 86400.0


def _check(cond, msg):
    if cond:
        print(f"  PASS: {msg}")
    else:
        print(f"  FAIL: {msg}")
        FAILURES.append(msg)


def _mk_store(root, slug, notes, index_lines=None, mtimes=None):
    """Create <root>/<slug>/memory with given note files.

    notes: dict filename -> content. index_lines: list of MEMORY.md lines
    (None -> auto-index every note). mtimes: dict filename -> epoch seconds.
    """
    mem = os.path.join(root, slug, "memory")
    os.makedirs(mem, exist_ok=True)
    for name, content in notes.items():
        with open(os.path.join(mem, name), "w", encoding="utf-8") as f:
            f.write(content)
    if index_lines is None:
        index_lines = [f"- [{n[:-3]}]({n}) — auto" for n in notes]
    with open(os.path.join(mem, "MEMORY.md"), "w", encoding="utf-8") as f:
        f.write("# Memory Index\n" + "\n".join(index_lines) + "\n")
    if mtimes:
        for name, ts in mtimes.items():
            os.utime(os.path.join(mem, name), (ts, ts))
    return mem


def main():
    sys.path.insert(0, os.path.dirname(SCRIPT))
    try:
        import native_memory_audit as nma
    except ImportError as exc:
        print(f"  FAIL: cannot import native_memory_audit ({exc})")
        return 1

    now = time.time()

    with tempfile.TemporaryDirectory() as root:
        # --- classification: active ---
        s1 = _mk_store(root, "p-active", {"a.md": "x"},
                       mtimes={"a.md": now - 5 * DAY, "MEMORY.md": now - 5 * DAY})
        r1 = nma.scan_store(s1, now)
        _check(r1["classification"] == "active",
               "newest 5 d old -> classification active")
        _check(r1["frozen"] is False, "active store is not frozen")

        # --- classification: dormant + frozen ---
        s2 = _mk_store(root, "p-frozen", {"b.md": "x"},
                       mtimes={"b.md": now - 45 * DAY,
                               "MEMORY.md": now - 44 * DAY})
        r2 = nma.scan_store(s2, now)
        _check(r2["classification"] == "dormant",
               "newest 44 d old -> classification dormant")
        _check(r2["frozen"] is True,
               "dormant + span <= 7 d -> frozen (write-once store)")

        # --- dormant but NOT frozen (long write history) ---
        s3 = _mk_store(root, "p-dormant", {"c.md": "x", "d.md": "y"},
                       mtimes={"c.md": now - 90 * DAY, "d.md": now - 30 * DAY,
                               "MEMORY.md": now - 30 * DAY})
        r3 = nma.scan_store(s3, now)
        _check(r3["classification"] == "dormant" and r3["frozen"] is False,
               "dormant + span 60 d -> not frozen")

        # --- orphans + dead links ---
        s4 = _mk_store(root, "p-links",
                       {"indexed.md": "x", "orphan.md": "y"},
                       index_lines=["- [indexed](indexed.md) — ok",
                                    "- [ghost](ghost.md) — dead"])
        r4 = nma.scan_store(s4, now)
        _check(r4["orphans"] == ["orphan.md"],
               "note missing from index -> orphans")
        _check(r4["dead_links"] == ["ghost.md"],
               "index link to missing file -> dead_links")

        # --- rotation-aware: archive index counts as linked, subdir links ---
        s4b = _mk_store(root, "p-rotated",
                        {"active.md": "x", "rotated.md": "y"},
                        index_lines=[
                            "- [active](active.md) — ok",
                            "> **[ARCH]** siehe [archive/MEMORY-archiv-2026-07-20.md]"
                            "(archive/MEMORY-archiv-2026-07-20.md)"])
        arch_dir = os.path.join(s4b, "archive")
        os.makedirs(arch_dir, exist_ok=True)
        with open(os.path.join(arch_dir, "MEMORY-archiv-2026-07-20.md"), "w",
                  encoding="utf-8") as f:
            f.write("# Archiv\n- [rotated](rotated.md) — alt\n")
        r4b = nma.scan_store(s4b, now)
        _check(r4b["orphans"] == [],
               "note linked only from archive index is NOT an orphan")
        _check(r4b["dead_links"] == [],
               "subdir link to existing archive file is NOT a dead link")

        # --- injection levels ---
        s5 = _mk_store(root, "p-big", {"e.md": "x"},
                       index_lines=["- [e](e.md) — pad\n" + "z" * 11000])
        r5 = nma.scan_store(s5, now)
        _check(r5["injection_level"] == "warn",
               "MEMORY.md >= 10 KB -> injection_level warn")
        s6 = _mk_store(root, "p-huge", {"f.md": "x"},
                       index_lines=["- [f](f.md) — pad\n" + "z" * 17000])
        r6 = nma.scan_store(s6, now)
        _check(r6["injection_level"] == "critical",
               "MEMORY.md >= 16 KB -> injection_level critical")
        _check(r1["injection_level"] == "ok",
               "small MEMORY.md -> injection_level ok")

        # --- empty store: no notes -> classification "empty", not active ---
        s7 = _mk_store(root, "p-empty", {})
        r7 = nma.scan_store(s7, now)
        _check(r7["classification"] == "empty" and r7["frozen"] is False,
               "store without notes -> classification empty (not active)")

        # --- traversal links leave the store -> ignored, not linkage ---
        s8 = _mk_store(root, "p-traversal", {"real.md": "x"},
                       index_lines=["- [esc](../ghost-outside.md) — traversal",
                                    "- [real](real.md) — ok"])
        r8 = nma.scan_store(s8, now)
        _check(all("ghost-outside" not in d for d in r8["dead_links"]),
               "../-traversal link is ignored (not dead, not linkage)")
        _check(r8["orphans"] == [], "real note stays linked despite traversal line")

        # --- audit(): scans only */memory, exclude honored ---
        os.makedirs(os.path.join(root, "no-memory-here"), exist_ok=True)
        result = nma.audit(root, now, exclude=["p-huge"])
        slugs = [s["slug"] for s in result["stores"]]
        _check("p-active" in slugs and "no-memory-here" not in slugs,
               "audit() finds memory stores, skips dirs without memory/")
        _check("p-huge" not in slugs, "audit() honors exclude list")
        _check(result["summary"]["stores"] == len(slugs),
               "summary.stores matches store count")
        _check(result["summary"]["orphans"] == 1 and
               result["summary"]["dead_links"] == 1,
               "summary aggregates orphans and dead links")

        # --- CLI: read-only, writes reports, utf-8 safe ---
        # Store name with non-cp1252 char in report path (L47 regression guard).
        _mk_store(root, "p-umläut-→", {"g.md": "x"})
        before = {}
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                before[p] = (os.path.getmtime(p), os.path.getsize(p))
        out_dir = tempfile.mkdtemp()  # OUTSIDE projects-root (CLI guard)
        out_json = os.path.join(out_dir, "audit.json")
        out_md = os.path.join(out_dir, "audit.md")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = ""  # force default console encoding path
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--projects-root", root,
             "--json", out_json, "--md", out_md],
            capture_output=True, text=True, env=env)
        _check(proc.returncode == 0,
               f"CLI exits 0 (stderr: {proc.stderr[:200]!r})")
        _check(os.path.isfile(out_json) and os.path.isfile(out_md),
               "CLI writes --json and --md reports")
        after_ok = True
        for p, (mt, sz) in before.items():
            if not os.path.isfile(p) or (os.path.getmtime(p),
                                         os.path.getsize(p)) != (mt, sz):
                after_ok = False
        _check(after_ok, "CLI is read-only on the scanned stores")
        with open(out_json, encoding="utf-8") as f:
            data = json.load(f)
        _check(any("uml" in s["slug"] for s in data["stores"]),
               "non-ascii store slug survives into JSON report")

        # --- CLI guard: output path inside projects-root is rejected ---
        evil = os.path.join(root, "p-active", "memory", "MEMORY.md")
        with open(evil, encoding="utf-8") as f:
            evil_before = f.read()
        proc2 = subprocess.run(
            [sys.executable, SCRIPT, "--projects-root", root, "--json", evil],
            capture_output=True, text=True)
        with open(evil, encoding="utf-8") as f:
            evil_after = f.read()
        _check(proc2.returncode == 2 and evil_before == evil_after,
               "CLI rejects --json target inside projects-root (store intact)")

        # --- CLI guard: missing parent dir -> clean error, exit 2 ---
        proc3 = subprocess.run(
            [sys.executable, SCRIPT, "--projects-root", root,
             "--json", os.path.join(root, "no-such-dir", "x.json")],
            capture_output=True, text=True)
        _check(proc3.returncode == 2 and "Traceback" not in proc3.stderr,
               "CLI reports missing output dir cleanly (exit 2, no traceback)")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

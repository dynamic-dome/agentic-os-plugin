#!/usr/bin/env python3
"""Read-only audit of Claude Code's native project memory stores (T-36).

Scans <projects-root>/*/memory (default: ~/.claude/projects) and reports per
store: size, freshness classification, orphaned notes, dead index links, and
MEMORY.md injection cost. NEVER writes into a store — the only outputs are the
optional --json/--md report files.

Classification:
  active   newest note <= 21 days old
  dormant  older than that
  frozen   dormant AND write span (newest - oldest) <= 7 days (write-once)
  empty    store has no note files (MEMORY.md alone does not count)

Known limit: markdown link targets containing parentheses are not parsed
(harness note names are slug-based; worst case is a visible false-positive
orphan, never a write).

Injection level (MEMORY.md is fully injected at session start of its project):
  ok < 10 KB <= warn < 16 KB <= critical

Usage:
  python native_memory_audit.py [--projects-root P] [--json OUT] [--md OUT]
                                [--exclude SLUG ...]

Exit codes: 0 ok · 2 usage/root missing.
"""
import argparse
import json
import os
import re
import sys
import time

ACTIVE_MAX_AGE_D = 21
FROZEN_MAX_SPAN_D = 7
WARN_BYTES = 10240
CRITICAL_BYTES = 16384
DAY = 86400.0

_LINK_RE = re.compile(r"\]\(([^)#?]+\.md)\)")


def _index_links(index_path):
    """Relative *.md targets linked from an index file (store-root-relative
    paths, order kept). Rotation archive indexes use the same convention:
    targets are relative to the memory/ root, not to the archive file."""
    try:
        with open(index_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    out = []
    for target in _LINK_RE.findall(text):
        target = target.strip().replace("\\", "/")
        if target.startswith(("http://", "https://", "/")):
            continue
        norm = os.path.normpath(target).replace("\\", "/")
        if norm.startswith("../") or norm == "..":  # leaves the store: ignore
            continue
        out.append(norm)
    return out


def scan_store(store_dir, now):
    """Audit one memory/ dir. Read-only; returns a plain dict."""
    notes = sorted(f for f in os.listdir(store_dir)
                   if f.endswith(".md") and f != "MEMORY.md")
    memory_md = os.path.join(store_dir, "MEMORY.md")
    has_index = os.path.isfile(memory_md)
    memory_md_bytes = os.path.getsize(memory_md) if has_index else 0

    mtimes = []
    total_bytes = memory_md_bytes
    for name in notes:
        path = os.path.join(store_dir, name)
        total_bytes += os.path.getsize(path)
        mtimes.append(os.path.getmtime(path))

    newest = max(mtimes) if mtimes else 0.0
    oldest = min(mtimes) if mtimes else 0.0
    age_d = (now - newest) / DAY if mtimes else None
    span_d = (newest - oldest) / DAY if mtimes else 0.0

    if not mtimes:
        classification = "empty"
    elif age_d <= ACTIVE_MAX_AGE_D:
        classification = "active"
    else:
        classification = "dormant"
    frozen = classification == "dormant" and span_d <= FROZEN_MAX_SPAN_D

    if memory_md_bytes >= CRITICAL_BYTES:
        injection_level = "critical"
    elif memory_md_bytes >= WARN_BYTES:
        injection_level = "warn"
    else:
        injection_level = "ok"

    active_links = _index_links(memory_md) if has_index else []
    linked = {os.path.basename(t) for t in active_links}
    archive_dir = os.path.join(store_dir, "archive")
    if os.path.isdir(archive_dir):
        for name in sorted(os.listdir(archive_dir)):
            if name.startswith("MEMORY") and name.endswith(".md"):
                linked.update(os.path.basename(t) for t in
                              _index_links(os.path.join(archive_dir, name)))
    orphans = [n for n in notes if n not in linked]
    dead_links = [t for t in active_links
                  if not os.path.isfile(os.path.join(store_dir, *t.split("/")))]

    return {
        "path": store_dir,
        "slug": os.path.basename(os.path.dirname(store_dir)),
        "has_index": has_index,
        "notes": len(notes),
        "total_bytes": total_bytes,
        "memory_md_bytes": memory_md_bytes,
        "newest": newest,
        "oldest": oldest,
        "age_days": round(age_d, 1) if age_d is not None else None,
        "span_days": round(span_d, 1),
        "classification": classification,
        "frozen": frozen,
        "injection_level": injection_level,
        "orphans": orphans,
        "dead_links": dead_links,
    }


def audit(projects_root, now, exclude=None):
    """Scan every <root>/*/memory store. Read-only."""
    exclude = set(exclude or [])
    stores = []
    try:
        entries = sorted(os.scandir(projects_root), key=lambda e: e.name)
    except OSError:
        return {"stores": [], "summary": {"stores": 0}}
    for entry in entries:
        if not entry.is_dir() or entry.name in exclude:
            continue
        mem = os.path.join(entry.path, "memory")
        if not os.path.isdir(mem):
            continue
        stores.append(scan_store(mem, now))
    stores.sort(key=lambda s: s["total_bytes"], reverse=True)
    summary = {
        "stores": len(stores),
        "total_bytes": sum(s["total_bytes"] for s in stores),
        "active": sum(1 for s in stores if s["classification"] == "active"),
        "dormant": sum(1 for s in stores if s["classification"] == "dormant"),
        "frozen": sum(1 for s in stores if s["frozen"]),
        "orphans": sum(len(s["orphans"]) for s in stores),
        "dead_links": sum(len(s["dead_links"]) for s in stores),
        "injection_warn": sum(1 for s in stores
                              if s["injection_level"] != "ok"),
    }
    return {"stores": stores, "summary": summary}


def render_markdown(result, now):
    kb = lambda b: f"{b / 1024:.1f}"
    lines = ["# Native-Memory-Audit (read-only)", "",
             f"*Stand: {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))} · "
             f"{result['summary']['stores']} Stores · "
             f"{kb(result['summary'].get('total_bytes', 0))} KB gesamt*", "",
             "| Store | Notizen | KB | MEMORY.md KB | Injektion | Status | "
             "Alter (d) | Orphans | Tote Links |",
             "|---|---|---|---|---|---|---|---|---|"]
    for s in result["stores"]:
        status = s["classification"] + (" (frozen)" if s["frozen"] else "")
        lines.append(
            f"| {s['slug']} | {s['notes']} | {kb(s['total_bytes'])} | "
            f"{kb(s['memory_md_bytes'])} | {s['injection_level']} | {status} | "
            f"{s['age_days'] if s['age_days'] is not None else '-'} | "
            f"{', '.join(s['orphans']) or '-'} | "
            f"{', '.join(s['dead_links']) or '-'} |")
    sm = result["summary"]
    lines += ["",
              f"**Summary:** {sm['active']} active · {sm['dormant']} dormant "
              f"(davon {sm['frozen']} frozen) · {sm['orphans']} Orphans · "
              f"{sm['dead_links']} tote Links · {sm['injection_warn']} Stores "
              f"mit Injektions-Warnung.", ""]
    return "\n".join(lines)


def main(argv):
    try:  # L47: report may contain non-cp1252 chars on a cp1252 console
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(prog="native_memory_audit.py")
    parser.add_argument("--projects-root",
                        default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument("--json", dest="json_out")
    parser.add_argument("--md", dest="md_out")
    parser.add_argument("--exclude", nargs="*", default=[])
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    if not os.path.isdir(args.projects_root):
        print(f"audit: projects root not found: {args.projects_root}",
              file=sys.stderr)
        return 2

    root_abs = os.path.abspath(args.projects_root)
    for out_path in (args.json_out, args.md_out):
        if out_path and os.path.abspath(out_path).lower().startswith(
                root_abs.lower() + os.sep):
            print(f"audit: refusing output path inside projects root "
                  f"(read-only contract): {out_path}", file=sys.stderr)
            return 2

    now = time.time()
    result = audit(args.projects_root, now, exclude=args.exclude)
    md = render_markdown(result, now)
    try:
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=1)
        if args.md_out:
            with open(args.md_out, "w", encoding="utf-8") as f:
                f.write(md)
    except OSError as exc:
        print(f"audit: cannot write report: {exc}", file=sys.stderr)
        return 2
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

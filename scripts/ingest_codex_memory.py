#!/usr/bin/env python3
"""E1: Codex native memory -> learnings.json candidates (membrain hub spec §1).

Reads ONLY <codex-memories>/memory_summary.md (Codex's own compact projection;
raw_memories.md is episodic and belongs to the Atlas adapter). Sections
'User preferences' -> kind=feedback, 'General Tips' -> kind=learning; only
top-level bullets. Dedupe against learnings.json via apply_wrapup.norm or the
provenance hash; hits only refresh last_relevant. New entries are
bridge_status=candidate with source_agent=codex (never auto-approved; the
wrap-up gate 3d decides). Codex memory is INPUT only - never written.

Snapshot-sync (Codex's current summary is canon; Codex rewrites it regularly):
- a bullet with Jaccard >= REPHRASE_MIN to an active codex entry is that
  entry reworded -> text replaced in place, status kept (no new gate round);
- active codex entries no longer in the summary get bridge_status=retired
  (drops them from the bridge); superseded_by links the best new or
  confirmed entry with Jaccard >= LINK_MIN, which carries supersedes:<id>;
- a rewording of an owner-rejected tip is absorbed (tie goes to the veto);
  rejected entries keep status and text; an empty parse retires nothing;
- a retired entry whose exact text returns is revived as candidate.
Every codex entry carries origin=agent (T-52: provenance at external inputs).

Usage: python ingest_codex_memory.py <mem-dir> [--codex-memories <dir>] [--dry-run]
Exit: 0 ok (also skipped/no-op) · 1 learnings.json unreadable · 2 usage error.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apply_wrapup import PlanError, _jaccard, archived_rows, next_id, norm, render_learnings_md  # noqa: E402
import contextlib  # noqa: E402
import store_lock  # noqa: E402

SECTIONS = {"user preferences": "feedback", "general tips": "learning"}
ADHOC = "[ad-hoc note]"
REVIEW_AFTER_DAYS = 90
PROV = "codex:memory_summary:"
# Measured on the DCO store 2026-09-25: verbatim-ish rewrites score 0.96,
# real rewordings 0.40-0.56, unrelated tips <= 0.41. In-place only when
# near-identical; below that only a superseded_by link, the gate re-decides.
REPHRASE_MIN = 0.8
LINK_MIN = 0.5
ORIGIN = "agent"


def parse_summary(text):
    """Yield (kind, text, adhoc) for top-level bullets in the mapped sections."""
    kind = None
    for line in text.splitlines():
        if line.startswith("## "):
            kind = SECTIONS.get(line[3:].strip().lower())
            continue
        if line.startswith("#"):
            kind = None
            continue
        if kind and line.startswith("- "):
            body = line[2:].strip()
            adhoc = body.endswith(ADHOC)
            if adhoc:
                body = body[: -len(ADHOC)].strip()
            if body:
                yield kind, body, adhoc


def load_rows(path):
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8-sig") as f:  # a BOM is not corruption (5.2.1)
        data = json.load(f)
    return data if isinstance(data, list) else data.get("learnings", [])


def write_atomic(path, text):
    # mkstemp, not path + ".tmp" (5.3.0): two runs at once shared one temp
    # file name and could publish each other's half-written text.
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv):
    ap = argparse.ArgumentParser(prog="ingest_codex_memory.py")
    ap.add_argument("mem_dir")
    ap.add_argument("--codex-memories", default=os.path.join(os.path.expanduser("~"), ".codex", "memories"))
    ap.add_argument("--dry-run", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2

    summary = os.path.join(a.codex_memories, "memory_summary.md")
    if not os.path.isfile(summary):
        print(f"codex-ingest: no memory_summary.md at {summary} — skipped")
        return 0
    store = os.path.join(a.mem_dir, "learnings", "learnings.json")
    # One store lock from reading learnings.json to writing it (5.3.0): the
    # ingest runs from a scheduled task and must not race a wrap-up.
    guard = contextlib.nullcontext() if a.dry_run else store_lock.store_lock(a.mem_dir)
    try:
        with guard:
            return _ingest(a, summary, store)
    except OSError as exc:  # LockTimeout included
        print(f"codex-ingest: store busy or unwritable: {exc}", file=sys.stderr)
        return 1


def _ingest(a, summary, store):
    try:
        rows = load_rows(store)
        reserved = archived_rows(a.mem_dir, "learnings/learnings.json")  # archived ids are taken
    except (OSError, ValueError, PlanError) as exc:
        print(f"codex-ingest: learnings.json unreadable: {exc}", file=sys.stderr)
        return 1
    with open(summary, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()

    today = dt.date.today().isoformat()
    by_norm = {norm(r.get("text", "")): r for r in rows if isinstance(r, dict)}
    by_hash = {}
    for r in rows:
        for d in (r.get("derived_from") or []):
            if str(d).startswith(PROV):
                by_hash[d] = r
    # T-52: every entry this ingest ever created is an external (agent) input.
    backfilled = 0
    for r in rows:
        if isinstance(r, dict) and is_codex(r) and "origin" not in r:
            r["origin"] = ORIGIN
            backfilled += 1
    # Sync scope: entries this ingest created and nobody has vetoed or retired.
    active = [r for r in rows if isinstance(r, dict) and is_codex(r) and not r.get("superseded_by")
              and r.get("bridge_status") not in ("rejected", "retired")]
    items = []
    for kind, text, adhoc in parse_summary(raw):
        key = norm(text)
        items.append((kind, text, adhoc, key, PROV + hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]))
    # Pass 1: exact hits first, so a fuzzy match can never steal an entry
    # that a later bullet matches verbatim.
    seen, confirmed, dup, revived, pending = set(), [], 0, 0, []
    for item in items:
        hit = by_hash.get(item[4]) or by_norm.get(item[3])
        if hit is None:
            pending.append(item)
            continue
        hit["last_relevant"] = today
        if is_codex(hit):
            confirmed.append(hit)
            if hit.get("bridge_status") == "retired":
                hit["bridge_status"], hit["superseded_by"] = "candidate", None
                hit["bridge_note"] = f"{today} codex-ingest: wieder in memory_summary.md, reaktiviert"
                revived += 1
        seen.add(id(hit))
        dup += 1
    # Pass 2: near-identical rewrites replace the old text in place. A rewrite
    # of an owner-REJECTED tip is absorbed (the veto survives rewording). The
    # closest entry wins; rejected entries come first, so a tie goes to the veto.
    rejected = [r for r in rows if isinstance(r, dict) and is_codex(r) and r.get("bridge_status") == "rejected"]
    new, rephrased, dup_rejected = [], 0, 0
    for kind, text, adhoc, key, prov in pending:
        pool = rejected + [r for r in active if id(r) not in seen]
        best = max(pool, key=lambda r: _jaccard(text, r.get("text", "")), default=None)
        if best is not None and _jaccard(text, best.get("text", "")) >= REPHRASE_MIN \
                and best.get("bridge_status") == "rejected":
            best["last_relevant"] = today
            dup_rejected += 1
            continue
        if best is not None and _jaccard(text, best.get("text", "")) >= REPHRASE_MIN:
            best["text"], best["last_relevant"] = text, today
            best["derived_from"] = (best.get("derived_from") or []) + [prov]
            confirmed.append(best)
            seen.add(id(best))
            by_norm[key], by_hash[prov] = best, best
            rephrased += 1
            continue
        entry = {
            "id": next_id(rows + new, "L", reserved=reserved), "date": today, "text": text, "importance": 2,
            "tags": ["codex-native", kind] + (["ad-hoc"] if adhoc else []),
            "layer": "short-term", "superseded_by": None, "last_relevant": today,
            "derived_from": [prov],
            "review_after": (dt.date.today() + dt.timedelta(days=REVIEW_AFTER_DAYS)).isoformat(),
            "bridge_status": "candidate", "source_agent": "codex", "kind": kind, "origin": ORIGIN,
        }
        new.append(entry)
        by_norm[key] = entry
        by_hash[prov] = entry
    # Pass 3: whatever Codex no longer lists leaves the bridge. An empty parse
    # (format change, truncated file) must never wipe the codex entries. The
    # successor may be new OR an entry confirmed this run (stale duplicates).
    # Unlinked retirees stay in learnings.md: bridge_status governs the bridge
    # only, exactly like an owner veto.
    retired = 0
    if items:
        successors = confirmed + new
        for r in active:
            if id(r) in seen:
                continue
            succ = max(successors, key=lambda e: _jaccard(e.get("text", ""), r.get("text", "")), default=None)
            link = succ is not None and _jaccard(succ.get("text", ""), r.get("text", "")) >= LINK_MIN
            r["bridge_status"] = "retired"
            r["superseded_by"] = succ["id"] if link else None
            r["bridge_note"] = (f"{today} codex-ingest: nicht mehr in memory_summary.md"
                                + (f", umformuliert als {succ['id']}" if link else ""))
            if link:
                succ["derived_from"] = (succ.get("derived_from") or []) + ["supersedes:" + r["id"]]
            retired += 1
    ignored = (sum(1 for line in raw.splitlines() if line.startswith("- "))
               - len(new) - dup - rephrased - dup_rejected)

    if not a.dry_run and (new or dup or rephrased or retired or dup_rejected or backfilled):
        rows.extend(new)
        write_atomic(store, json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
        if new or rephrased or retired or revived:
            render_learnings_md(a.mem_dir, rows, False, [])
    print(f"codex-ingest: {len(new)} new, {dup} dup, {max(ignored, 0)} ignored, {rephrased} rephrased, "
          f"{retired} retired, {revived} revived, {dup_rejected} dup-rejected from {summary}")
    return 0


def is_codex(row):
    """True for entries this ingest created (codex source + summary provenance)."""
    return row.get("source_agent") == "codex" and any(
        str(d).startswith(PROV) for d in (row.get("derived_from") or []))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

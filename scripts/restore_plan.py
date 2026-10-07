#!/usr/bin/env python3
"""restore_plan.py — READ-ONLY restore plan for one memory store (5.3.0, D-021).

Until 5.2.1, /agentic-os:maintain moved learnings and patterns into
`*-archive-*.json` by count or age. The Atlas does not read archives, so those
entries dropped out of every retrieval path. This script reads the archives
and the live store and writes nothing. It prints:

  - `restore.learnings`: the rows to bring back, ready for the `restore` plan
    section of scripts/apply_wrapup.py (the only writer; it re-checks
    everything against the fresh store at apply time);
  - `pattern_ids`: the protection class for
    `extract_patterns.py --restore-archive --ids ...`;
  - `report`: every class the owner gate needs to see.

Learning classes:
  candidate          restored
  duplicate          same normalized text already live (same or other id) - skipped
  near_duplicate     Jaccard >= 0.8 to a live row - restored, reported (deselect via --skip-ids)
  id_collision       id live with another text - restored under a new id + legacy:<id>
  above_live_max     archive id above the live maximum - informational
  float_importance   0..1 float mapped to 1..5, original kept in derived_from
  dangling_superseded  superseded_by names nothing and has no proof prefix - held back
  codex_excluded     bridge_status retired/rejected - stays archived (veto memory)
  invalid            fails the write rules - held back

Pattern classes: protected (ready/candidate, confidence >= 0.7, skill_candidate,
or cited by a live learning), report_only, already_live.

No artificial review_after: restored rows surface through the review_sweep
charge "restored:<store>". Archive files are never modified.

Usage: python restore_plan.py <mem-dir> [--skip-ids L4,L7] [--no-learnings] [--out plan.json]
Exit: 0 ok · 1 usage · 2 unreadable store/archive.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import apply_wrapup as aw  # noqa: E402  (one rule set: field_errors, norm, jaccard)

NEAR_DUP = 0.8
CODEX_VETO = ("retired", "rejected")
PROTECTED_STATUS = ("ready", "candidate")


def archive_files(mem: str, family: str) -> list:
    folder = os.path.join(mem, family)
    name = f"{family}.json"
    if not os.path.isdir(folder):
        return []
    return [os.path.join(folder, fn) for fn in sorted(os.listdir(folder))
            if fn.endswith(".json") and fn != name
            and (fn.startswith(f"{family}-archive") or fn.startswith(f"{name}-archive"))]


def read_rows(path: str) -> list:
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise aw.PlanError(f"{path} is unreadable ({e})")
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _num(rid) -> int:
    m = re.search(r"(\d+)$", str(rid))
    return int(m.group(1)) if m else -1


def map_importance(value):
    """0..1 float -> 1..5 (plan rule 4); everything else unchanged."""
    if isinstance(value, float) and not isinstance(value, bool) and 0 <= value <= 1:
        return max(1, min(5, int(value * 5 + 0.5)))
    return value


def plan_learnings(mem: str, skip: set) -> tuple:
    live = aw.load_json(mem, "learnings/learnings.json", [])
    live = [r for r in live if isinstance(r, dict)]
    live_by_id = {str(r.get("id")): r for r in live}
    live_by_norm = {aw.norm(r.get("text")): r for r in live}
    live_max = max((_num(r.get("id")) for r in live), default=0)
    archived = []
    for path in archive_files(mem, "learnings"):
        archived += [(os.path.basename(path), r) for r in read_rows(path)]
    all_ids = set(live_by_id) | {str(r.get("id")) for _, r in archived}

    rep = {k: [] for k in ("duplicate", "near_duplicate", "id_collision", "above_live_max",
                           "float_importance", "dangling_superseded", "codex_excluded",
                           "invalid", "skipped_by_owner")}
    rows, seen_norm = [], set()
    for fn, src in archived:
        rid = str(src.get("id"))
        text_norm = aw.norm(src.get("text"))
        entry = {"id": rid, "file": fn}
        if rid in skip:
            rep["skipped_by_owner"].append(entry)
            continue
        if text_norm in live_by_norm or text_norm in seen_norm:
            twin = live_by_norm.get(text_norm)
            rep["duplicate"].append({**entry, "of": twin.get("id") if twin else "archive"})
            continue
        if src.get("bridge_status") in CODEX_VETO:
            rep["codex_excluded"].append({**entry, "bridge_status": src.get("bridge_status")})
            continue
        row = {k: v for k, v in src.items() if k not in aw.ARCHIVE_STAMPS}
        der = list(row.get("derived_from") or []) if isinstance(row.get("derived_from"), (list, type(None))) \
            else row.get("derived_from")
        mapped = map_importance(row.get("importance"))
        if isinstance(der, list):
            der.append(f"restored:{fn}")
            if mapped != row.get("importance"):
                der.append(f"restored:{fn} (importance {row.get('importance')})")
                rep["float_importance"].append({**entry, "from": row.get("importance"), "to": mapped})
        row["importance"], row["derived_from"] = mapped, der
        sup = row.get("superseded_by")
        if sup and not str(sup).startswith(aw.SUPERSEDE_PREFIXES) and str(sup) not in all_ids:
            rep["dangling_superseded"].append({**entry, "superseded_by": sup})
            continue
        errs = aw.field_errors(row)
        if errs:
            rep["invalid"].append({**entry, "errors": errs})
            continue
        if rid in live_by_id:
            rep["id_collision"].append({**entry, "live_text": str(live_by_id[rid].get("text"))[:80]})
        elif _num(rid) > live_max:
            rep["above_live_max"].append(entry)
        best = max(live, key=lambda r: aw._jaccard(row.get("text"), r.get("text")), default=None)
        if best is not None:
            score = aw._jaccard(row.get("text"), best.get("text"))
            if score >= NEAR_DUP:
                rep["near_duplicate"].append({**entry, "of": best.get("id"), "score": round(score, 2)})
        rows.append(row)
        seen_norm.add(text_norm)
    rep["candidate"] = len(rows)
    rep["archived_total"] = len(archived)
    return rows, rep


def plan_patterns(mem: str, learnings: list) -> dict:
    live = [p for p in aw.load_json(mem, "patterns/patterns.json", []) if isinstance(p, dict)]
    live_ids = {str(p.get("id")) for p in live}
    cited = set()
    for r in learnings:
        blob = " ".join([str(r.get("text", ""))] + [str(d) for d in (r.get("derived_from") or [])])
        cited |= set(re.findall(r"\b(?:G-pattern-\d+|P\d+)\b", blob))
    out = {"protected": [], "report_only": [], "already_live": [], "reasons": {}}
    for path in archive_files(mem, "patterns"):
        for p in read_rows(path):
            pid = str(p.get("id"))
            if pid in live_ids:
                out["already_live"].append(pid)
                continue
            why = []
            if p.get("promotion_status") in PROTECTED_STATUS or p.get("lifecycle") in PROTECTED_STATUS:
                why.append(str(p.get("promotion_status") or p.get("lifecycle")))
            try:
                if float(p.get("confidence") or 0) >= 0.7:
                    why.append("confidence>=0.7")
            except (TypeError, ValueError):
                pass
            if p.get("skill_candidate"):
                why.append("skill_candidate")
            if pid in cited:
                why.append("cited by a live learning")
            if why:
                out["protected"].append(pid)
                out["reasons"][pid] = why
            else:
                out["report_only"].append(pid)
    return out


def main(argv) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass
    ap = argparse.ArgumentParser(prog="restore_plan.py")
    ap.add_argument("mem")
    ap.add_argument("--skip-ids", default="", help="comma-separated archive ids the owner deselected")
    ap.add_argument("--no-learnings", action="store_true", help="patterns only (stores whose learnings stay archived by owner choice)")
    ap.add_argument("--out", help="also write the plan to this file")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 1
    if not os.path.isdir(a.mem):
        print(json.dumps({"ok": False, "error": f"memory dir not found: {a.mem}"}))
        return 1
    skip = {s.strip() for s in a.skip_ids.split(",") if s.strip()}
    try:
        rows, rep = ([], {"candidate": 0, "note": "--no-learnings"}) if a.no_learnings \
            else plan_learnings(a.mem, skip)
        live = [r for r in aw.load_json(a.mem, "learnings/learnings.json", []) if isinstance(r, dict)]
        pats = plan_patterns(a.mem, live)
    except aw.PlanError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 2
    plan = {
        "ok": True,
        "store": os.path.abspath(a.mem),
        "restore": {"source": "restore_plan.py", "learnings": rows},
        "pattern_ids": pats["protected"],
        "report": {"learnings": rep, "patterns": pats},
    }
    text = json.dumps(plan, indent=2, ensure_ascii=False)
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

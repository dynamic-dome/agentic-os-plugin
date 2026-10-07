#!/usr/bin/env python3
"""extract_patterns.py — deterministic half of pattern-extractor.

WHY THIS EXISTS
---------------
wrap-up Step 4 used to invoke the `pattern-extractor` skill, which injects a
343-line body into the context. Measured over 7 transcripts / ~1300 API calls
(2026-07-27, normalized per opportunity): a skill invocation is followed by a
full prefix-cache rewrite in 41% of cases, against 0.5% for Bash and 0% for
Edit. A rewrite at 100k+ context is expensive, because cache_creation is
12.5x the cache_read price. Delegation chains are therefore an architectural
cost decision, not a neutral call (L34, D-010).

Almost nothing in that body needed a model. The skill spells out exact
thresholds - same category AND >= 2 overlapping tags, occurrences >= 3, Jaccard
>= 0.6, and a closed-form confidence formula. Those are code. What genuinely
needs a model is the WORDING of a newly discovered pattern, and that is the only
thing this script asks for.

Since T-019 the structured-iteration half is code too: iteration-log.md has a
pinned render format (apply_wrapup.py, 4.18.0), so file hotspots, repeated
successful approaches and fragile test areas are clustered here as well.
Prose-era blocks don't parse and are skipped, never guessed at.

MODES
-----
    python scripts/extract_patterns.py .agent-memory --update
        Applies everything that is fully determined: updates to existing
        patterns (evidence merge, occurrences, recomputed confidence,
        last_seen), legacy-shape normalization, patterns.md regeneration.
        Reports NEW clusters as `proposals` WITHOUT inventing wording for them.

    python scripts/extract_patterns.py .agent-memory --apply < plan.json
        Takes {"patterns": [{"cluster_key", "description", "recommendation",
        "type"?, "severity"?}]} and writes those clusters as real entries.

    python scripts/extract_patterns.py .agent-memory --refresh
        Regenerates patterns.md from patterns.json even when nothing changed
        ("refresh patterns"). Without it a run that changes nothing writes no
        file at all - which is right for the routine path and wrong for an
        explicit refresh request.

The plan supplies judgment only: description, recommendation, and the
classification fields type/severity (deliberately settable - deciding WHAT a
cluster is, e.g. relabeling a neutral hotspot as an anti-pattern, is exactly
the judgment call; the match_existing type gate keeps mislabels from merging
into foreign entries). evidence, occurrences, confidence, tags and the dates
are taken from the freshly recomputed measurement and CANNOT be overridden by
the plan - a caller can misjudge what a pattern means, but it can never
inflate the numbers that justify it (verify-subagent-tallies).

Most runs need one call: with no new clusters, --update is the whole job.

Exit codes:
  0 = applied (including the "not enough data" no-op)
  1 = usage error (memory dir missing) - nothing attempted
  2 = plan rejected or IO failure
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apply_wrapup as _aw  # noqa: E402  (archived_rows: one archive rule for all writers)
import contextlib  # noqa: E402
import store_lock  # noqa: E402
import store_snapshot  # noqa: E402

# This script owns patterns/ and nothing else. Every other memory file belongs
# to another writer (apply_wrapup.py, the skills, the bootstrap gate).
ALLOWED_PREFIX = "patterns/"

BASE_CONFIDENCE = 0.3
JACCARD_DUPLICATE = 0.6
MIN_ERRORS_FOR_COLD_START = 3
SKILL_CANDIDATE_OCCURRENCES = 3
SKILL_CANDIDATE_CONFIDENCE = 0.7
RECURRING_ERROR_OCCURRENCES = 3

# T-019: thresholds for the structured-iteration half of Step 2.
MIN_ITERATIONS_FOR_COLD_START = 3
HOTSPOT_MIN_ITERATIONS = 3
APPROACH_MIN_ITERATIONS = 3
APPROACH_MIN_CONFIDENCE = 4
FRAGILE_MIN_ITERATIONS = 2

LEGACY_FIELDS = {
    "solution": "recommendation",
    "prevention": "recommendation",
    "source_errors": "evidence",
    "error_ids": "evidence",
}


class PlanError(Exception):
    """Plan is malformed - nothing gets written."""


# ---------------------------------------------------------------- io helpers

def canon(rel: str) -> str:
    """Reduce to the canonical relative form before the ownership check.

    Raw string comparison let "patterns/../iterations/errors.json" pass the
    prefix test and resolve to a foreign file (Codex review of 1e5c504).
    """
    norm = os.path.normpath(rel.replace("\\", "/")).replace("\\", "/")
    if norm.startswith("../") or norm == ".." or os.path.isabs(norm):
        raise PlanError(f"refusing to write {rel}: path escapes the memory dir")
    return norm


def _p(mem: str, rel: str) -> str:
    key = canon(rel)
    if not key.startswith(ALLOWED_PREFIX):
        raise PlanError(f"refusing to write {key}: this script only owns {ALLOWED_PREFIX}")
    return os.path.join(mem, key)


def load_json(mem: str, rel: str, default):
    """Missing -> default. Unreadable -> PlanError, the file stays as it is.

    5.2.1: the former quarantine renamed the file - also on
    --dry-run - and the run continued on an empty catalog. A UTF-8 BOM is
    accepted; repair is an owner step (/agentic-os:maintain Step 2).
    """
    path = os.path.join(mem, rel)
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise PlanError(f"{rel} is not valid JSON ({e}) - nothing was written; "
                        f"repair it or run /agentic-os:maintain (Step 2)")


def archived_pattern_rows(mem: str) -> list:
    """Archived patterns - their ids are taken (see apply_wrapup.archived_rows)."""
    try:
        return _aw.archived_rows(mem, "patterns/patterns.json")
    except _aw.PlanError as e:
        raise PlanError(str(e))


def write_atomic(mem: str, rel: str, text: str, dry: bool, touched: list) -> None:
    path = _p(mem, rel)
    touched.append(rel)
    if dry:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_json(mem: str, rel: str, data, dry: bool, touched: list) -> None:
    write_atomic(mem, rel, json.dumps(data, indent=2, ensure_ascii=False) + "\n", dry, touched)


def norm_tokens(text: str) -> set:
    return set(re.sub(r"[^a-z0-9\s]", " ", str(text or "").lower()).split())


def jaccard(a: str, b: str) -> float:
    ta, tb = norm_tokens(a), norm_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def next_pattern_id(rows, reserved=()) -> str:
    """Continue the dominant id family (patterns.json holds one 'G-pattern-005'
    alongside 'P0nn' - a single outlier must not hijack the sequence)."""
    pat = re.compile(r"^(.*?)(\d+)$")
    groups: dict = {}
    for r in rows:
        m = pat.match(str(r.get("id", "")))
        if not m:
            continue
        digits = m.group(2)
        g = groups.setdefault(m.group(1), {"count": 0, "hi": 0, "pad": 0})
        g["count"] += 1
        g["hi"] = max(g["hi"], int(digits))
        g["pad"] = max(g["pad"], len(digits) if len(digits) > 1 else 0)
    if not groups:
        prefix, pad, hi = "P", 3, 0  # empty catalog: "P001" unless an archive holds it
    else:
        # On a frequency tie prefer the canonical "P" family, otherwise a single
        # foreign id ("G-900" next to one "P001") captures the sequence.
        prefix = max(groups, key=lambda k: (groups[k]["count"], k == "P", groups[k]["hi"]))
        pad, hi = groups[prefix]["pad"], groups[prefix]["hi"]
    # Archived ids only raise the number inside the live family, never vote on it.
    if callable(reserved):
        reserved = reserved()
    for r in reserved:
        m = pat.match(str(r.get("id", "")))
        if m and m.group(1) == prefix:
            hi = max(hi, int(m.group(2)))
    n = hi + 1
    return f"{prefix}{n:0{pad}d}" if pad else f"{prefix}{n}"


# ------------------------------------------------------------- normalization

def normalize_legacy(patterns, tally, reserved=()) -> None:
    """One-shape convergence (pattern-schema-canon). In place, never a parallel entry."""
    for p in patterns:
        changed = False
        for old, new in LEGACY_FIELDS.items():
            if old in p:
                value = p.pop(old)
                if not p.get(new):
                    p[new] = value
                elif str(value).strip() and norm_tokens(value) != norm_tokens(p[new]):
                    # Both shapes carry text and they differ. Popping the legacy
                    # one lost data silently (Codex review of 1e5c504) - park it
                    # with provenance instead of deciding for the user.
                    p.setdefault("legacy_values", {})[old] = value
                changed = True
        for old in ("name", "title"):
            if old in p:
                value = p.pop(old)
                if p.get("description"):
                    p["description"] = f"{value} — {p['description']}"
                else:
                    p["description"] = value
                changed = True
        if not re.match(r"^P\d+$", str(p.get("id", ""))) and str(p.get("id", "")).startswith("pattern-"):
            p["previous_id"] = p["id"]
            p["id"] = None  # assigned below, after the whole set is known
            changed = True
        if changed:
            tally["patterns_normalized"] += 1
    for p in patterns:
        if p.get("id") is None:
            p["id"] = next_pattern_id([q for q in patterns if q.get("id")], reserved)


# ---------------------------------------------------------------- clustering

def cluster_errors(errors):
    """Detection heuristics from pattern-extractor Step 2 - thresholds verbatim."""
    clusters, used = [], set()

    # (a) same category AND >= 2 overlapping tags
    for i, a in enumerate(errors):
        if a.get("id") in used:
            continue
        members = [a]
        for b in errors[i + 1:]:
            if b.get("id") in used or b.get("category") != a.get("category"):
                continue
            if len(set(a.get("tags") or []) & set(b.get("tags") or [])) >= 2:
                members.append(b)
        if len(members) >= 2:
            shared = set(members[0].get("tags") or [])
            for m in members[1:]:
                shared &= set(m.get("tags") or [])
            for m in members:
                used.add(m.get("id"))
            clusters.append(make_cluster(
                f"cat:{a.get('category')}|{'+'.join(sorted(shared))}", members))

    # (b) same root_cause across errors (fuzzy)
    for i, a in enumerate(errors):
        if a.get("id") in used:
            continue
        members = [a]
        for b in errors[i + 1:]:
            if b.get("id") in used:
                continue
            if jaccard(a.get("root_cause"), b.get("root_cause")) >= JACCARD_DUPLICATE:
                members.append(b)
        if len(members) >= 2:
            for m in members:
                used.add(m.get("id"))
            clusters.append(make_cluster(f"rc:{a.get('id')}", members))

    # (c) a single error that already recurred often enough is confirmed on its own
    for e in errors:
        if e.get("id") in used:
            continue
        if int(e.get("occurrences", 1)) >= RECURRING_ERROR_OCCURRENCES:
            used.add(e.get("id"))
            clusters.append(make_cluster(f"rec:{e.get('id')}", [e]))

    unmatched = [e.get("id") for e in errors if e.get("id") not in used]
    return clusters, unmatched


# T-019: iteration-log.md became parseable when apply_wrapup.py pinned the
# render format in 4.18.0 (`## {date} — {type}: {title}` + `- **Field:** value`).
# Older prose blocks simply don't match and are skipped, never guessed at.

ITER_HEADER_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2}) — ([\w-]+): (.+)$")
ITER_FIELD_RE = re.compile(r"^- \*\*([A-Za-z ]+):\*\* ?(.*)$")


def parse_iteration_log(mem):
    path = os.path.join(mem, "iterations", "iteration-log.md")
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return []  # no log yet; any other read error must stop the run (main: exit 2)
    iterations, current = [], None
    for line in text.splitlines():
        m = ITER_HEADER_RE.match(line)
        if m:
            current = {"date": m.group(1), "type": m.group(2),
                       "title": m.group(3).strip(), "tags": [], "files": [],
                       "tests": "", "confidence": None, "has_errors": False}
            iterations.append(current)
            continue
        if line.startswith("## "):
            # A heading that is NOT a canonical iteration ends attribution -
            # otherwise a legacy block's field lines overwrite the preceding
            # canonical block (Codex review 2026-07-27).
            current = None
            continue
        if current is None:
            continue
        fm = ITER_FIELD_RE.match(line)
        if not fm:
            continue
        field, value = fm.group(1).strip().lower(), fm.group(2).strip()
        if field == "tags":
            current["tags"] = _split_unique(value)
        elif field in ("files changed", "files created"):
            current["files"] = _split_unique(value)
        elif field == "tests":
            current["tests"] = value
        elif field == "confidence":
            cm = re.match(r"(\d)", value)
            if cm:
                current["confidence"] = int(cm.group(1))
        elif field.startswith("errors"):
            current["has_errors"] = True
    # A block without a single structured field is prose, not an iteration.
    return [it for it in iterations if it["tags"] or it["files"]]


def _split_unique(value):
    """Comma-split, order-preserving dedup: 'a.py, a.py, a.py' in ONE iteration
    must count once, or a single entry fakes a 3-iteration hotspot."""
    out = []
    for part in value.split(","):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def iter_ref(it) -> str:
    return f"it:{it['date']}:{it['title'][:40]}"


def _tests_failed(it) -> bool:
    t = it.get("tests", "").lower()
    if "flak" in t:
        return True
    # '10 passed, 0 failed' is a pass - only a nonzero count (or a bare
    # 'failed' without one) marks a failure (Codex review 2026-07-27).
    m = re.search(r"(\d+)\s*(?:tests?\s+)?fail", t)
    if m:
        return int(m.group(1)) > 0
    return "fail" in t


def _tests_passed(it) -> bool:
    return "passed" in it.get("tests", "").lower() and not _tests_failed(it)


def make_iteration_cluster(key, ctype, severity, members, tags):
    dates = sorted({m["date"] for m in members})
    c = BASE_CONFIDENCE + min(0.3, 0.1 * (len(members) - 1))
    if len(dates) >= 2:
        c += 0.1
    return {
        "cluster_key": key,
        "type": ctype,
        "evidence": [iter_ref(m) for m in members],
        "occurrences": len(members),
        "tags": list(tags),
        "severity": severity,
        "first_seen": dates[0] if dates else "",
        "last_seen": dates[-1] if dates else "",
        "confidence": round(min(1.0, c), 2),
        "sample_root_causes": [],
        "sample_summaries": [m["title"] for m in members][:3],
        "categories": [key.split(":", 1)[0]],
    }


def cluster_iterations(iterations):
    """The structured half of Step 2 (T-019): hotspots, repeated successful
    approaches, fragile test areas. Same trust rules as cluster_errors -
    everything here is measured, the model only words the proposals."""
    clusters = []

    # (d) file hotspot: same file changed in >= 3 iterations
    by_file = {}
    for it in iterations:
        for f in it["files"]:
            by_file.setdefault(f, []).append(it)
    for f, members in sorted(by_file.items()):
        if len(members) >= HOTSPOT_MIN_ITERATIONS:
            tags = []
            for m in members:
                for t in m["tags"]:
                    if t not in tags:
                        tags.append(t)
            clusters.append(make_iteration_cluster(
                f"hotspot:{f}", "pattern", "info", members, tags))

    # (e) repeated successful approach: confident, passing, >= 2 shared tags
    good = [it for it in iterations
            if (it["confidence"] or 0) >= APPROACH_MIN_CONFIDENCE and _tests_passed(it)]
    clusters += _tag_groups(good, APPROACH_MIN_ITERATIONS,
                            "approach", "best-practice", "info")

    # (f) fragile test area: failing/flaky tests or logged errors, shared tags
    shaky = [it for it in iterations if _tests_failed(it) or it["has_errors"]]
    clusters += _tag_groups(shaky, FRAGILE_MIN_ITERATIONS,
                            "fragile", "anti-pattern", "minor")
    return clusters


def _tag_groups(candidates, min_members, prefix, ctype, severity):
    """Greedy >= 2-shared-tags grouping - same rule as cluster_errors (a)."""
    out, used = [], set()
    for i, a in enumerate(candidates):
        ref = iter_ref(a)
        if ref in used:
            continue
        members = [a]
        for b in candidates[i + 1:]:
            if iter_ref(b) in used:
                continue
            if len(set(a["tags"]) & set(b["tags"])) >= 2:
                members.append(b)
        if len(members) < min_members:
            continue
        shared = set(members[0]["tags"])
        for m in members[1:]:
            shared &= set(m["tags"])
        if len(shared) < 2:
            # Pairwise anchor overlap without a global >=2-tag core is a
            # chain, not a cluster - and would produce an empty key.
            continue
        for m in members:
            used.add(iter_ref(m))
        out.append(make_iteration_cluster(
            f"{prefix}:{'+'.join(sorted(shared))}", ctype, severity,
            members, sorted(shared)))
    return out


def make_cluster(key, members):
    dates = sorted({m.get("date") for m in members if m.get("date")})
    for m in members:
        dates.extend(m.get("recurrence_dates") or [])
    dates = sorted({d for d in dates if d})
    tags = []
    for m in members:
        for t in (m.get("tags") or []):
            if t not in tags:
                tags.append(t)
    occurrences = sum(int(m.get("occurrences", 1)) for m in members)
    return {
        "cluster_key": key,
        "type": "anti-pattern",
        "evidence": [m.get("id") for m in members],
        "occurrences": occurrences,
        "tags": tags,
        "severity": pick_severity(members),
        "first_seen": dates[0] if dates else "",
        "last_seen": dates[-1] if dates else "",
        "confidence": score_confidence(members, occurrences),
        "sample_root_causes": [m.get("root_cause", "") for m in members][:3],
        "categories": sorted({m.get("category", "") for m in members}),
    }


def pick_severity(members):
    order = ["critical", "major", "minor", "info"]
    found = [m.get("severity") for m in members if m.get("severity") in order]
    return min(found, key=order.index) if found else "minor"


def score_confidence(members, occurrences) -> float:
    """Step 3, verbatim. Rounded so the value is comparable, not float noise."""
    c = BASE_CONFIDENCE
    c += min(0.3, 0.1 * max(0, occurrences - 1))
    if len(members) >= 2:
        first = members[0]
        if all(jaccard(first.get("root_cause"), m.get("root_cause")) >= JACCARD_DUPLICATE
               for m in members[1:]):
            c += 0.1
        files = [set(m.get("files_changed") or []) for m in members]
        if all(files) and set.intersection(*files):
            c += 0.1
        preventions = {str(m.get("prevention", "")).strip() for m in members}
        if len(preventions) == 1 and preventions != {""}:
            c += 0.1
    dates = {m.get("date") for m in members}
    for m in members:
        dates |= set(m.get("recurrence_dates") or [])
    if len({d for d in dates if d}) >= 2:
        c += 0.1
    return round(min(1.0, c), 2)


def match_existing(cluster, patterns, wording=""):
    """Step 4 dedup, RANKED instead of first-in-file-order.

    Two fixes from the Codex review of 1e5c504: (a) file order decided the
    match, so a weak two-tag hit on an early pattern beat an exact evidence hit
    on a later one; (b) the Jaccard branch was dead on the --update path,
    because a cluster has no description until the model supplies wording -
    hence the optional `wording` argument, passed on the --apply path.

    Returns (best_match, competitors). Competitors are reported rather than
    silently discarded: two patterns claiming one cluster is a catalog problem
    a human should see.
    """
    scored = []
    for p in patterns:
        shared_ev = set(cluster["evidence"]) & set(p.get("evidence") or [])
        shared_tags = set(cluster["tags"]) & set(p.get("tags") or [])
        sim = jaccard(wording, p.get("description")) if wording else 0.0
        # Type gate (T-019): tag/wording similarity across types is
        # coincidence, not identity - a best-practice approach must never
        # inflate an anti-pattern's numbers. Shared evidence stays exempt:
        # the same evidence IS the same catalog entry, whatever its label.
        same_type = p.get("type", "anti-pattern") == cluster["type"]
        if shared_ev:
            scored.append((3, len(shared_ev), p))
        elif same_type and sim >= JACCARD_DUPLICATE:
            scored.append((2, sim, p))
        elif same_type and len(shared_tags) >= 2:
            scored.append((1, len(shared_tags), p))
    if not scored:
        return None, []
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best = scored[0][2]
    competitors = [p.get("id") for _, _, p in scored[1:]]
    return best, competitors


def update_pattern(p, cluster, tally):
    evidence = list(p.get("evidence") or [])
    added = 0
    for e in cluster["evidence"]:
        if e not in evidence:
            evidence.append(e)
            added += 1
    p["evidence"] = evidence
    # Recompute over the MERGED set. max(old, new) plus an overwritten
    # confidence meant a pattern that just GAINED evidence could come out with
    # a lower score (0.8 -> 0.5 in the reviewed repro) and an occurrence count
    # that ignored half its own evidence.
    p["occurrences"] = max(int(p.get("occurrences", 1)) + added, len(evidence))
    p["confidence"] = max(float(p.get("confidence", 0)),
                          recompute_confidence(p, cluster))
    p["last_seen"] = max(str(p.get("last_seen") or ""), cluster["last_seen"])
    if not p.get("first_seen"):
        p["first_seen"] = cluster["first_seen"]
    tags = list(p.get("tags") or [])
    for t in cluster["tags"]:
        if t not in tags:
            tags.append(t)
    p["tags"] = tags
    p["skill_candidate"] = is_skill_candidate(p)
    tally["patterns_updated"] += 1
    # description / recommendation are the model's words - never overwritten.


def recompute_confidence(p, cluster) -> float:
    """Score the merged pattern, not just the incoming cluster: the occurrence
    booster has to see the evidence the pattern already carried."""
    occurrences = max(int(p.get("occurrences", 1)), len(p.get("evidence") or []))
    c = BASE_CONFIDENCE + min(0.3, 0.1 * max(0, occurrences - 1))
    # Keep whatever structural boosters the incoming cluster earned (root_cause
    # agreement, shared files, consistent prevention, multiple dates).
    structural = float(cluster["confidence"]) - BASE_CONFIDENCE - min(
        0.3, 0.1 * max(0, cluster["occurrences"] - 1))
    return round(min(1.0, c + max(0.0, structural)), 2)


def is_skill_candidate(p) -> bool:
    return (int(p.get("occurrences", 1)) >= SKILL_CANDIDATE_OCCURRENCES
            and float(p.get("confidence", 0)) >= SKILL_CANDIDATE_CONFIDENCE)


def new_pattern(cluster, spec, patterns, reserved=()):
    description = (spec.get("description") or "").strip()
    recommendation = (spec.get("recommendation") or "").strip()
    if not description:
        raise PlanError(f"pattern for cluster {cluster['cluster_key']} without 'description'")
    if not recommendation:
        raise PlanError(f"pattern for cluster {cluster['cluster_key']} without 'recommendation'")
    entry = {
        "id": next_pattern_id(patterns, reserved),
        "type": spec.get("type", cluster["type"]),
        "description": description,
        "evidence": list(cluster["evidence"]),
        "confidence": cluster["confidence"],
        "severity": spec.get("severity", cluster["severity"]),
        "tags": list(cluster["tags"]),
        "source_projects": ["current-project"],
        "first_seen": cluster["first_seen"],
        "last_seen": cluster["last_seen"],
        "occurrences": cluster["occurrences"],
        "recommendation": recommendation,
        "skill_candidate": False,
        "lifecycle": "active",
        "implemented_by": [],
        "implemented_at": None,
        "validated_by": [],
        "validated_at": None,
    }
    entry["skill_candidate"] = is_skill_candidate(entry)
    return entry


# ------------------------------------------------------------------ projection

def render_patterns_md(patterns, date) -> str:
    active = [p for p in patterns if p.get("lifecycle", "active") != "superseded"]
    anti = sum(1 for p in active if p.get("type") == "anti-pattern")
    best = sum(1 for p in active if p.get("type") == "best-practice")
    out = ["# Pattern Catalog", "", f"*Last updated: {date}*",
           f"*Total patterns: {len(active)} ({anti} anti-patterns, {best} best practices)*", ""]

    def block(title, rows):
        if not rows:
            return
        out.append(f"## {title}")
        out.append("")
        for p in sorted(rows, key=lambda x: -float(x.get("confidence", 0))):
            out.append(f"### {p.get('id')}: {p.get('description')} "
                       f"(confidence: {p.get('confidence')})")
            out.append(f"- **Type:** {p.get('type', 'pattern')}")
            out.append(f"- **Evidence:** {p.get('occurrences', 1)} occurrences "
                       f"({', '.join(p.get('evidence') or []) or 'n/a'})")
            out.append(f"- **Recommendation:** {p.get('recommendation', '')}")
            out.append(f"- **Tags:** {', '.join(p.get('tags') or [])}")
            out.append("")

    block("High Confidence Warnings", [p for p in active if float(p.get("confidence", 0)) >= 0.7])
    block("Medium Confidence",
          [p for p in active if 0.5 <= float(p.get("confidence", 0)) < 0.7])
    block("Low Confidence", [p for p in active if float(p.get("confidence", 0)) < 0.5])
    candidates = [p for p in active if p.get("skill_candidate")]
    if candidates:
        out.append("## Skill Candidates")
        out.append("")
        for p in candidates:
            state = p.get("generated_skill") or "ready for skill generation"
            out.append(f"- {p.get('id')}: {p.get('description')} — {state}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# -------------------------------------------------------------------- main

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass

    ap = argparse.ArgumentParser(description="Deterministic pattern extraction.")
    ap.add_argument("mem", nargs="?", default=".agent-memory")
    ap.add_argument("--update", action="store_true",
                    help="apply determined changes, propose new clusters")
    ap.add_argument("--apply", action="store_true",
                    help="write new clusters from a plan on stdin")
    ap.add_argument("--refresh", action="store_true",
                    help="regenerate patterns.md from patterns.json even when nothing changed")
    ap.add_argument("--plan", default="-", help="plan JSON file, '-' for stdin")
    ap.add_argument("--restore-archive", action="store_true",
                    help="bring archived patterns back unchanged (5.3.0, needs --ids)")
    ap.add_argument("--ids", default="", help="comma-separated pattern ids for --restore-archive")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--lock-timeout", type=float, default=store_lock.TIMEOUT_SECONDS)
    args = ap.parse_args()

    if not os.path.isdir(args.mem):
        print(json.dumps({"ok": False, "error": f"memory dir not found: {args.mem}"}))
        return 1
    if not (args.update or args.apply or args.refresh or args.restore_archive):
        print(json.dumps({"ok": False, "error": "need --update, --apply, --refresh or --restore-archive"}))
        return 1
    # One store lock per run (5.3.0) - the same lock apply_wrapup takes, so a
    # pattern update and a wrap-up cannot lose each other's writes.
    guard = (contextlib.nullcontext() if args.dry_run
             else store_lock.store_lock(args.mem, timeout=args.lock_timeout))
    try:
        with guard:
            if args.restore_archive:
                return restore_archive(args)
            return run(args)
    except OSError as e:  # LockTimeout included
        print(json.dumps({"ok": False, "error": f"io error: {e}", "files_written": []},
                         ensure_ascii=False))
        return 2


ARCHIVE_STAMPS = ("archived_at", "archived_by", "archive_reason", "archived")


def restore_archive(args) -> int:
    """Archived patterns back into patterns.json, numbers untouched (D-013).

    Only ids the caller names (restore_plan.py's protection class after the
    owner gate). A live id is skipped, so a second run changes nothing; an id
    found in no archive is rejected. Archive files are only read.
    """
    ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    date = _dt.date.today().isoformat()
    touched: list = []
    try:
        if not ids:
            raise PlanError("--restore-archive needs --ids")
        patterns = load_json(args.mem, "patterns/patterns.json", [])
        live = {str(p.get("id")) for p in patterns if isinstance(p, dict)}
        folder = os.path.join(args.mem, "patterns")
        found: dict = {}
        if os.path.isdir(folder):
            for fn in sorted(os.listdir(folder)):
                if not (fn.endswith(".json") and fn != "patterns.json"
                        and (fn.startswith("patterns-archive") or fn.startswith("patterns.json-archive"))):
                    continue
                for row in _restore_rows(os.path.join(folder, fn)):
                    found.setdefault(str(row.get("id")), (fn, row))
        missing = [i for i in ids if i not in found and i not in live]
        if missing:
            raise PlanError(f"not in any pattern archive: {', '.join(missing)}")
        restored, skipped = [], []
        for pid in ids:
            if pid in live:
                skipped.append(pid)
                continue
            fn, row = found[pid]
            entry = {k: v for k, v in row.items() if k not in ARCHIVE_STAMPS}
            entry["restored_from"] = fn
            entry["restored_at"] = date
            patterns.append(entry)
            live.add(pid)
            restored.append(pid)
        if restored:
            if not args.dry_run:
                store_snapshot.take(args.mem)
            write_json(args.mem, "patterns/patterns.json", patterns, args.dry_run, touched)
            write_atomic(args.mem, "patterns/patterns.md", render_patterns_md(patterns, date),
                         args.dry_run, touched)
    except (PlanError, ValueError) as e:
        print(json.dumps({"ok": False, "error": f"plan rejected: {e}", "files_written": touched},
                         ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, "dry_run": args.dry_run, "restored": restored,
                      "skipped_live": skipped, "files_written": touched}, indent=2, ensure_ascii=False))
    return 0


def _restore_rows(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise PlanError(f"archive {os.path.basename(path)} is unreadable ({e})")
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def run(args) -> int:

    date = _dt.date.today().isoformat()
    tally = {"patterns_added": 0, "patterns_updated": 0, "patterns_normalized": 0}
    touched: list = []

    try:
        errors = load_json(args.mem, "iterations/errors.json", [])
        patterns = load_json(args.mem, "patterns/patterns.json", [])
        # Archived ids are read lazily - only a run that assigns a NEW id needs them,
        # so a broken archive cannot block --refresh or an --update without new ids.
        _archived: list = []

        def reserved():
            if not _archived:
                _archived.append(archived_pattern_rows(args.mem))
            return _archived[0]
        iterations = parse_iteration_log(args.mem)
        # T-019: iterations lift the guard too - the error-only check was why
        # error-free sessions starved the pattern pipeline.
        if (len(errors) < MIN_ERRORS_FOR_COLD_START
                and len(iterations) < MIN_ITERATIONS_FOR_COLD_START
                and not patterns and not args.refresh):
            print(json.dumps({"ok": True, "skipped": "not-enough-data", "dry_run": args.dry_run,
                              "proposals": [], "files_written": [], "tally": tally,
                              "unmatched_errors": [], "skill_candidates": []}, indent=2))
            return 0

        normalize_legacy(patterns, tally, reserved)
        clusters, unmatched = cluster_errors(errors)
        clusters += cluster_iterations(iterations)

        matched, proposals, ambiguous = [], [], []
        for cluster in clusters:
            existing, competitors = match_existing(cluster, patterns)
            if existing is not None:
                update_pattern(existing, cluster, tally)
                matched.append(cluster["cluster_key"])
                if competitors:
                    ambiguous.append({"cluster_key": cluster["cluster_key"],
                                      "matched": existing.get("id"),
                                      "also_matched": competitors})
            else:
                proposals.append(cluster)

        if args.apply:
            plan = read_plan(args.plan)
            by_key = {c["cluster_key"]: c for c in proposals}
            seen_keys = set()
            for spec in (plan.get("patterns") or []):
                key = spec.get("cluster_key")
                if key in seen_keys:
                    # The same key twice in one plan used to create two entries
                    # with identical evidence (Codex review of 1e5c504).
                    raise PlanError(f"duplicate cluster_key {key!r} in one plan")
                if key not in by_key:
                    raise PlanError(f"unknown cluster_key {key!r} - re-run --update for the "
                                    f"current keys ({', '.join(by_key) or 'none'})")
                seen_keys.add(key)
                cluster = by_key[key]
                # Only now does wording exist, so this is the first moment the
                # documented Jaccard description dedup can actually run.
                twin, _ = match_existing(cluster, patterns, spec.get("description", ""))
                if twin is not None:
                    update_pattern(twin, cluster, tally)
                    matched.append(key)
                else:
                    patterns.append(new_pattern(cluster, spec, patterns, reserved))
                    tally["patterns_added"] += 1
                proposals = [p for p in proposals if p["cluster_key"] != key]

        # --refresh forces the projection: "refresh patterns" must actually rewrite
        # patterns.md, and a run that changed nothing writes nothing without it.
        if (args.refresh or tally["patterns_added"] or tally["patterns_updated"]
                or tally["patterns_normalized"]):
            write_json(args.mem, "patterns/patterns.json", patterns, args.dry_run, touched)
            write_atomic(args.mem, "patterns/patterns.md",
                         render_patterns_md(patterns, date), args.dry_run, touched)
    except (PlanError, OSError, ValueError) as e:  # ValueError: decode/shape (5.2.1)
        kind = ("plan rejected" if isinstance(e, PlanError)
                else "io error" if isinstance(e, OSError) else "unreadable input")
        print(json.dumps({"ok": False, "error": f"{kind}: {e}", "files_written": touched},
                         ensure_ascii=False))
        return 2

    print(json.dumps({
        "ok": True,
        "dry_run": args.dry_run,
        "proposals": proposals,
        "updated_clusters": matched,
        "ambiguous_matches": ambiguous,
        "unmatched_errors": unmatched,
        "skill_candidates": [p.get("id") for p in patterns if p.get("skill_candidate")
                             and not p.get("generated_skill")],
        "rueckfluss_candidates": [p.get("id") for p in patterns
                                  if float(p.get("confidence", 0)) >= 0.7
                                  and not p.get("delta_task_id")],
        "files_written": touched,
        "tally": tally,
    }, indent=2, ensure_ascii=False))
    return 0


def read_plan(source: str) -> dict:
    try:
        raw = sys.stdin.read() if source == "-" else open(source, encoding="utf-8").read()
        plan = json.loads(raw)
    except (json.JSONDecodeError, OSError) as e:
        raise PlanError(f"unreadable plan: {e}") from e
    if not isinstance(plan, dict):
        raise PlanError("plan must be a JSON object")
    return plan


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""apply_wrapup.py — single-pass batch writer for the wrap-up skill.

WHY THIS EXISTS
---------------
A measured wrap-up run (2026-07-20) took 28 API calls, 11.8M
cache-read + 1.38M cache-write tokens, but only 39k output tokens. 94% of the
cost was context transport, 6% was thinking.

The model is stateless, so every call resends the entire conversation. Cost is
therefore the SUM of context length over calls, not context length once - each
extra turn buys another full resend. The prompt cache makes a resend ~10x
cheaper, which is why the number is $15 and not ~$60; it dampens the problem
rather than removing it.

COUNTING CAVEAT (this was measured wrong on the first pass, 2026-07-27):
`usage` is reported per API response, but the transcript writes one record per
content block (text / thinking / tool_use), each carrying the same usage
object. Summing over assistant RECORDS overcounts by ~2.8x. Deduplicate by
`message.id` before adding anything up.

This script collapses all of wrap-up's file mutations into ONE call: the model
emits a single write plan, this script applies it and returns the resulting
tallies. Turn count for the write phase drops from ~40 to ~2.

Second benefit: the returned counts are computed from the files actually
written, so the mandatory Step 6.5 identity status line reports measured
numbers instead of a model self-report (see the verify-subagent-tallies rule).

WHAT IT DOES NOT DO
-------------------
No judgment. Deduplication beyond exact-text matching, learning importance,
what counts as an identity observation, what counts as ONE iteration or as a
decision of record — all of that stays with the model and arrives via the plan.
This script only applies deterministic rules that are already written down in
skills/wrap-up/SKILL.md.

OWNERSHIP (widened in T-015, not loosened)
------------------------------------------
Since the delegation rebuild the script also writes iteration-log.md,
errors.json and decisions.json, because wrap-up no longer injects the
iteration-logger / context-keeper skill bodies just to have those files
written (a skill invocation triggered a full prefix-cache rewrite in 41% of
measured cases, L34). Ownership moved rather than disappeared: those files are
reachable ONLY through their named applier (see APPLIER_OWNED), so the generic
write path still refuses them. patterns.json/patterns.md belong to
scripts/extract_patterns.py and stay refused here; soul.md is never written.
Since P1/B2 the identity files (user.md, user-changelog.json, user-candidates.json) are
owned the same way - applier "identity": queue promotion, encoding corrections and the
`user_md_supersede` plan section are the only ways in.

IDENTITY QUALITY (P1/B2, design 2026-10-27)
-------------------------------------------
- `confirmed` on a user candidate is earned: it needs a literal `quote` that
  scripts/identity_quote.py finds in ONE genuine user message of the session transcript
  (read-only; --transcript or <HOME>/.claude/projects/*/<session-id>.jsonl). Otherwise the
  candidate stays observed/inferred and the second mention confirms it as before.
- `user_md_supersede` replaces one user.md line losslessly, changelog entry first.
- Every owner-facing proposal (bridge_decisions, rule_explanations, user_md_supersede) carries
  `plain` - plain German, 1..600 characters; a missing or longer one rejects the whole plan.

USAGE
-----
    python scripts/apply_wrapup.py .agent-memory --session-id <sid> < plan.json
    python scripts/apply_wrapup.py .agent-memory --session-id <sid> --dry-run < plan.json
    python scripts/apply_wrapup.py .agent-memory --session-id <sid> --transcript <session.jsonl> < plan.json

Plan schema: skills/wrap-up/references/wrapup-schemas.md §Write plan.

Exit codes:
  0 = applied
  1 = usage error (bad arguments, memory dir missing) - nothing attempted
  2 = plan rejected OR IO failure mid-run - the run stopped before
      consolidation, so the marker is absent and dirty flags stay set

A programming error (TypeError and friends) is deliberately NOT caught: it
must crash loudly rather than be reported as a well-formed failure.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import difflib
import json
import os
import re
import sys
import tempfile
import unicodedata

# Files no path in this script may ever touch.
FORBIDDEN = {
    "patterns/patterns.json",         # scripts/extract_patterns.py owns these
    "patterns/patterns.md",
    "identity/soul.md",               # bootstrap [j/n] gate only - never here
}

# Files that carry their own deterministic ruleset. Since T-015 the wrap-up run
# no longer injects the iteration-logger / context-keeper skill bodies to get
# them written - the rules live here instead. The ownership is NOT dropped, it
# moves: each file is reachable ONLY through the applier named below, so the
# generic write path still cannot touch it (a hard bug, not a warning).
APPLIER_OWNED = {
    "iterations/iteration-log.md":   "iterations",
    "iterations/errors.json":        "iterations",
    "working/current-session.json":  "iterations",
    "context/decisions.json":        "decisions",
    # 5.3.0: the generic path could overwrite the whole store with any plan-
    # supplied content; now only the learnings and restore appliers reach it.
    "learnings/learnings.json":      "learnings",
    # P1/B2: the identity pipeline has one writer too. user.md and its audit log change only
    # through the queue promotion, the encoding corrections and user_md_supersede - all of them
    # `identity`; the queue file itself belongs to the same applier.
    "identity/user.md":              "identity",
    "identity/user-changelog.json":  "identity",
    "working/user-candidates.json":  "identity",
    # P2: the same files in the central store (identity_store.PROFILE_*), plus the briefing projection
    "identity/profile.md":              "identity",
    "identity/profile-changelog.json":  "identity",
    "identity/profile-candidates.json": "identity",
    "identity/briefing-lines.txt":      "identity",
    "identity/profile-briefing.txt":    "identity",
}
_FORBIDDEN_CI = {k.lower() for k in FORBIDDEN}
_OWNED_CI = {k.lower(): v for k, v in APPLIER_OWNED.items()}

SECTION_BY_SIGNAL = {
    "preference": "Preferences",
    "communication": "Preferences",
    "workflow": "Work Style",
    "correction": "Known Corrections",
}

SUMMARY_MAX_LINES = 30
REVIEW_AFTER_DAYS = 90
# Step 3a in code (5.3.0): token Jaccard >= DUP_MIN to any live learning is a
# duplicate - the rule the skill text always stated, now applied without the
# model reading the whole learnings.json. NEAR_MIN..DUP_MIN goes back to the
# model as near_duplicates for its own verdict (plan field `duplicate_of`).
DUP_MIN = 0.6
NEAR_MIN = 0.2
NEAR_TOP = 3
DERIVED_MAX = 30
SUPERSEDE_PREFIXES = ("fixed:", "obsolete:", "moved:", "codex:")
# One home for the 150: the projections' short-form rule. The own dir goes on the
# path because callers also load this file by path (importlib), not only as a script.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from projection_text import SUMMARY_CHARS as SUMMARY_MAX  # noqa: E402
from projection_text import anchor_ok, split_anchor  # noqa: E402
import store_lock  # noqa: E402
import store_snapshot  # noqa: E402
import harvest_state  # noqa: E402
import identity_quote  # noqa: E402
import identity_store  # noqa: E402
import identity_briefing  # noqa: E402
from identity_briefing import is_placeholder_line  # noqa: E402,F401  (one home for the placeholder rule)


class PlanError(Exception):
    """Plan is malformed - nothing gets written."""


# ---------------------------------------------------------------- io helpers

def canon(rel: str) -> str:
    """Reduce a memory-relative path to the form the ownership tables use.

    Without this the guard compares raw strings: "iterations/../iterations/
    errors.json" is not in APPLIER_OWNED, yet resolves to a protected file -
    a verified bypass of the generic writer (Codex review of 1e5c504). Any
    path that leaves the memory dir is refused outright.
    """
    norm = os.path.normpath(rel.replace("\\", "/")).replace("\\", "/")
    if norm.startswith("../") or norm == ".." or os.path.isabs(norm):
        raise PlanError(f"refusing to write {rel}: path escapes the memory dir")
    return norm


def _p(mem: str, rel: str, via: str | None = None) -> str:
    key = canon(rel)
    # Case-folded lookup: Windows resolves "Learnings/Learnings.json" to the
    # protected file, so a case variant must not pass the guard (5.3.0).
    if key.lower() in _FORBIDDEN_CI:
        raise PlanError(f"refusing to write {key}: owned by another script")
    owner = _OWNED_CI.get(key.lower())
    if owner and owner != via:
        raise PlanError(f"refusing to write {key}: only the '{owner}' applier may touch it")
    return os.path.join(mem, key)


def load_json(mem: str, rel: str, default, via: str | None = None):
    """Read a store file. Missing -> default. Unreadable -> PlanError, and the
    file stays exactly as it is.

    5.2.1: the former quarantine contract renamed an unreadable file
    to .corrupt.bak - even on --dry-run - and the run then wrote a store holding
    only the new rows, ids restarting at L1, exit 0, no warning. A UTF-8 BOM
    (PowerShell `Set-Content -Encoding utf8`) was enough to trigger it. A BOM is
    now simply accepted; the next write drops it. Repairing a really broken file
    is an owner step (/agentic-os:maintain Step 2), never a side effect of a
    wrap-up. `via` stays in the signature for call-site symmetry: reading needs
    no ownership, and this function no longer mutates anything.
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


# Every JSON store file an applier reads, with the top-level type the appliers
# expect. main() checks them all BEFORE the first write: the appliers run in
# sequence, so a problem found by a later applier would otherwise leave the
# iterations and decisions of the same plan already written.
STORE_JSON = {
    "iterations/errors.json": list, "working/current-session.json": dict,
    "context/decisions.json": list, "learnings/learnings.json": list,
    "working/user-candidates.json": list, "identity/user-changelog.json": list,
    "context/open-tasks.json": list,
}


def _check_utf8(mem: str, rel: str) -> None:
    path = os.path.join(mem, rel)
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            fh.read()
    except UnicodeDecodeError as e:
        raise PlanError(f"{rel} is not UTF-8 ({e}) - nothing was written; repair the encoding")


def read_dirty_file(mem: str, name: str):
    """Strict read of one working/dirty-*.json: un-readable evidence stops the run -
    a marker written over a corrupt dirty file would destroy the only record that
    work was left un-consolidated (Codex review of 1e5c504)."""
    try:
        with open(os.path.join(mem, "working", name), encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise PlanError(
            f"working/{name} is unreadable ({e}); refusing to consolidate over "
            f"un-consolidated work - fix or remove the file and re-run") from e


def preflight_readable(mem: str, plan: dict | None = None, paths=None) -> None:
    """Everything this plan will read must be readable before the first byte is
    written (5.2.1 review): live stores incl. their top-level type, the archives of
    every family that gets a new id, the text files the plan appends to, and the
    dirty files a consolidating plan consumes. Only what the plan needs - a broken
    learnings archive must not block a summary-only run."""
    plan = plan or {}
    paths = _id_paths(mem, paths)
    missing = object()  # a file holding JSON null exists - it must not pass as "missing"
    checks = [(mem, rel, kind) for rel, kind in STORE_JSON.items()]
    if paths.central and plan_touches_identity(plan):
        # P2: the central queue and changelog are read instead of the project's; a broken one stops the run here
        # (no silent fallback to the project files)
        checks += [(paths.root, paths.queue, list), (paths.root, paths.changelog, list)]
    for root, rel, kind in checks:
        data = load_json(root, rel, missing)
        if data is not missing and not isinstance(data, kind):
            raise PlanError(f"{rel} holds a JSON {type(data).__name__} at top level, expected "
                            f"a {kind.__name__} - nothing was written; repair it or run "
                            f"/agentic-os:maintain (Step 2)")
    spec = plan.get("open_tasks") or {}
    if plan.get("iterations"):
        archived_rows(mem, "iterations/errors.json")
        _check_utf8(mem, "iterations/iteration-log.md")
    if plan.get("decisions"):
        archived_rows(mem, "context/decisions.json")
    if plan.get("learnings") or (plan.get("restore") or {}).get("learnings") or plan.get("principles"):
        archived_rows(mem, "learnings/learnings.json")
    if spec.get("add"):
        archived_rows(mem, "context/open-tasks.json")
    if plan_touches_identity(plan):  # same condition, and a malformed corrections section reaches its validator
        _check_utf8(paths.root, paths.md)
    if plan.get("soul_candidates"):
        _check_utf8(mem, "identity/soul-candidates.md")
    if plan.get("consolidate"):
        work = os.path.join(mem, "working")
        if os.path.isdir(work):
            for name in sorted(os.listdir(work)):
                if name.startswith("dirty-") and name.endswith(".json"):
                    read_dirty_file(mem, name)


def archived_rows(mem: str, rel: str) -> list:
    """Rows of the archive siblings of a store file - used ONLY to reserve ids.

    maintain moved rows to `{stem}-archive-*.json` (also `{stem}-archive.json` and
    the misnamed `{name}-archive-*.json`). Re-issuing an archived id makes two
    records share one id as soon as the archive is restored, and the Atlas rebuild
    aborts on duplicate ids. An unreadable archive raises: then the ids cannot be
    guaranteed unique.
    """
    folder, name = os.path.split(os.path.join(mem, rel))
    stem = os.path.splitext(name)[0]
    rows: list = []
    if not os.path.isdir(folder):
        return rows
    for fn in sorted(os.listdir(folder)):
        if fn == name or not fn.endswith(".json"):
            continue
        if not (fn.startswith(stem + "-archive") or fn.startswith(name + "-archive")):
            continue
        try:
            with open(os.path.join(folder, fn), encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            raise PlanError(f"archive {fn} is unreadable ({e}) - cannot reserve its ids")
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), [])
        if isinstance(data, list):
            rows.extend(r for r in data if isinstance(r, dict))
    return rows


def write_atomic(mem: str, rel: str, text: str, dry: bool, touched: list,
                 via: str | None = None) -> None:
    path = _p(mem, rel, via)
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


def write_json(mem: str, rel: str, data, dry: bool, touched: list,
               via: str | None = None) -> None:
    write_atomic(mem, rel, json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                 dry, touched, via)


def next_id(rows, prefix_arg: str, pad: int = 0, reserved=()) -> str:
    """Continue the id sequence that is ACTUALLY on disk.

    The arguments are only a fallback for an empty store. The real files drifted
    away from their documented templates long ago (drift audit 2026-07-27:
    errors.json carries "err-007" where the template promised "E{n}",
    decisions.json "D-008" where it promised "D{n}"). Assuming the template
    would silently fork the sequence into two parallel id families, so the shape
    is derived from the data: pick the most common prefix, continue its highest
    number, keep its zero padding. A single foreign id (patterns.json holds one
    "G-pattern-005" among "P0nn") loses the vote instead of hijacking the format.
    """
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
        prefix, hi = prefix_arg, 0
    else:
        # Tie-break on the caller's canonical prefix: with one "D-001" and one
        # "G-900" the counts are equal and the higher number would win, handing
        # the sequence to the outlier - the exact opposite of the promise
        # (Codex review of 1e5c504).
        prefix = max(groups, key=lambda k: (groups[k]["count"], k == prefix_arg, groups[k]["hi"]))
        pad, hi = groups[prefix]["pad"], groups[prefix]["hi"]
    # Reserved rows (archives) only raise the number inside the chosen family -
    # they never vote on the family itself, so an archive full of an older id
    # style ("E1".."E25") cannot fork the live sequence ("err-0nn").
    for r in reserved:
        m = pat.match(str(r.get("id", "")))
        if m and m.group(1) == prefix:
            hi = max(hi, int(m.group(2)))
    n = hi + 1
    return f"{prefix}{n:0{pad}d}" if pad else f"{prefix}{n}"


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


# ------------------------------------------------------- mojibake (DCO #9393)
# UTF-8 text that was read as cp1252 ("Fünf" -> "FÃ¼nf") is the one text damage the
# stores actually showed. Detection and repair are deterministic, so the correction
# path can be fed by a script instead of by retyped text.
# Code points a cp1252 decode produces for the continuation bytes 0x80..0xBF (written as numbers:
# the characters themselves are invisible or look like noise in an editor).
_CONT_CODES = (list(range(0x80, 0xC0)) + [0x152, 0x153, 0x160, 0x161, 0x178, 0x17D, 0x17E, 0x192, 0x2C6, 0x2DC,
                0x2013, 0x2014, 0x2018, 0x2019, 0x201A, 0x201C, 0x201D, 0x201E, 0x2020, 0x2021, 0x2022,
                0x2026, 0x2030, 0x2039, 0x203A, 0x20AC, 0x2122])
_MOJI_RUN = re.compile("[" + chr(0xC2) + "-" + chr(0xF4) + "][" + "".join(re.escape(chr(c)) for c in _CONT_CODES) + "]{1,3}")


def _cp1252_bytes(text: str) -> bytes:
    out = bytearray()
    for ch in text:
        try:
            out += ch.encode("cp1252")
        except UnicodeEncodeError:
            if ord(ch) >= 256:
                raise
            out.append(ord(ch))  # the five bytes cp1252 leaves undefined (0x81 ...)
    return bytes(out)


def repair_mojibake(text: str) -> str:
    """Undo UTF-8-read-as-cp1252 run by run; undamaged text is a fixpoint."""
    def fix(m):
        run = m.group(0)
        for k in range(len(run), 0, -1):  # longest decodable prefix, rest stays as it is
            try:
                return _cp1252_bytes(run[:k]).decode("utf-8") + run[k:]
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
        return run
    return _MOJI_RUN.sub(fix, text)


def looks_mojibake(text) -> bool:
    return isinstance(text, str) and repair_mojibake(text) != text


def _is_iso_date(value) -> bool:
    try:
        _dt.date.fromisoformat(str(value)[:10])
        return True
    except ValueError:
        return False


def field_errors(row: dict) -> list:
    """Shape rules for a learning row this script WRITES (5.3.0).

    Only written or changed rows are checked; the legacy store is reported by
    --lint and never blocks - four stores hold None or float importance today,
    and a whole-store gate would lock every one of them out of wrap-up.
    """
    errs = []
    imp = row.get("importance")
    if isinstance(imp, bool) or not isinstance(imp, int) or not 1 <= imp <= 5:
        errs.append(f"importance {imp!r} is not an int 1..5")
    if not str(row.get("text") or "").strip():
        errs.append("empty text")
    if "date" in row and not _is_iso_date(row.get("date")):
        errs.append(f"date {row.get('date')!r} is not ISO")
    tags = row.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        errs.append("tags is not a list of strings")
    der = row.get("derived_from", [])
    if not isinstance(der, list) or not all(isinstance(d, str) for d in der):
        errs.append("derived_from is not a list of strings")
    elif len(der) > DERIVED_MAX:
        errs.append(f"derived_from has {len(der)} entries (max {DERIVED_MAX})")
    return errs


def supersede_errors(row: dict, by_id: dict) -> list:
    """superseded_by must name a real successor or carry a proof prefix (D-021),
    and following the chain must never come back to the row itself."""
    sup = row.get("superseded_by")
    if sup in (None, ""):
        return []
    sup = str(sup)
    if sup.startswith(SUPERSEDE_PREFIXES):
        return []
    if sup not in by_id:
        return [f"superseded_by {sup} names no known entry and carries no proof prefix"]
    seen, cur = {str(row.get("id"))}, sup
    while cur and cur in by_id:
        if cur in seen:
            return [f"superseded_by chain from {row.get('id')} is a cycle"]
        seen.add(cur)
        nxt = by_id[cur].get("superseded_by")
        cur = str(nxt) if nxt and not str(nxt).startswith(SUPERSEDE_PREFIXES) else None
    return []


def lint_learnings(mem: str) -> list:
    """Every live row that breaks the write rules - a report, never a gate."""
    rows = load_json(mem, "learnings/learnings.json", [])
    by_id = {str(r.get("id")): r for r in rows if isinstance(r, dict)}
    out, seen = [], set()
    for r in rows:
        if not isinstance(r, dict):
            out.append({"id": None, "errors": ["row is not an object"]})
            continue
        errs = field_errors(r) + supersede_errors(r, by_id)
        if str(r.get("id")) in seen:
            errs.append("duplicate id")
        seen.add(str(r.get("id")))
        if errs:
            out.append({"id": r.get("id"), "errors": errs})
    return out


# ------------------------------------------------------------------- steps

def validate_plan(mem, plan, paths=None):
    """Reject EVERY detectable plan error before the first byte is written.

    Originally this only checked `supersedes`. That left the other required
    fields to fail inside their applier - so a plan whose learnings were broken
    still wrote its iteration log first and exited 2 half-applied (Codex review
    of 1e5c504). Anything checkable without touching disk belongs here.
    """
    for it in (plan.get("iterations") or []):
        if not (it.get("title") or "").strip():
            raise PlanError("iteration without 'title'")
    if plan.get("date") is not None and not (
            isinstance(plan["date"], str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", plan["date"])
            and _is_iso_date(plan["date"])):
        # Every applier stamps this date; a bad one used to surface only in the
        # learnings applier, after the iteration log was written (Codex 5.3.0).
        raise PlanError(f"plan date {plan['date']!r} is not YYYY-MM-DD")
    live_ids = None
    for lrn in (plan.get("learnings") or []):
        if not (lrn.get("text") or "").strip():
            raise PlanError("learning without 'text'")
        # 5.3.0: strict shape before the first byte. A "5" or a 0 used to be
        # coerced and written; a None crashed after the iteration log was written.
        probe = {"text": lrn.get("text"), "importance": lrn.get("importance", 3),
                 "tags": lrn.get("tags", []), "derived_from": lrn.get("derived_from", [])}
        errs = field_errors({k: ([] if v is None and k in ("tags", "derived_from") else v)
                             for k, v in probe.items()})
        if errs:
            raise PlanError(f"learning '{str(lrn.get('text'))[:40]}': {'; '.join(errs)}")
        if "rule_class" in lrn and not isinstance(lrn["rule_class"], bool):
            raise PlanError(f"learning '{str(lrn.get('text'))[:40]}': rule_class must be a JSON bool")
        if lrn.get("duplicate_of"):
            if live_ids is None:
                live_ids = {str(r.get("id")) for r in load_json(mem, "learnings/learnings.json", [])
                            if isinstance(r, dict)}
            if str(lrn["duplicate_of"]) not in live_ids:
                raise PlanError(f"duplicate_of points at unknown learning {lrn['duplicate_of']}")
    validate_user_candidates(plan)
    for soul in (plan.get("soul_candidates") or []):
        if not (soul.get("proposal") or "").strip():
            raise PlanError("soul candidate without 'proposal'")
    for task in ((plan.get("open_tasks") or {}).get("add") or []):
        if not (task.get("title") or "").strip():
            raise PlanError("open task without 'title'")

    validate_principles(mem, plan)
    validate_bridge_decisions(mem, plan)
    validate_corrections(mem, plan, paths)
    validate_rule_gate(mem, plan)
    validate_user_md_supersede(mem, plan, paths)

    sups = [d.get("supersedes") for d in (plan.get("decisions") or []) if d.get("supersedes")]
    titles = [(d.get("title") or "").strip() for d in (plan.get("decisions") or [])]
    for i, title in enumerate(titles):
        if not title:
            raise PlanError("decision without 'title'")
    if sups:
        known = {str(r.get("id")) for r
                 in load_json(mem, "context/decisions.json", [], via="decisions")}
        for sup in sups:
            if str(sup) not in known:
                raise PlanError(f"supersedes points at unknown decision {sup}")


def iteration_header(it, date) -> str:
    """The dedup key. Compared as a whole line, never as a substring - otherwise
    'Fix' matches inside 'Fix extended' and swallows a real second iteration."""
    title = (it.get("title") or "").strip()
    if not title:
        raise PlanError("iteration without 'title'")
    if it.get("recovered_from"):
        title += f" (recovered from session {it['recovered_from']})"
    return f"## {date} — {it.get('type', 'feature')}: {title}\n"


def render_iteration(it, date, error_refs):
    """Render ONE iteration block in the shape the log actually uses.

    Not the shape iteration-logger's template described ("## Iteration #{n} —
    {date} {HH:MM}" plus "### Details"/"### Learnings" subsections): that
    template was never followed on disk. Every run re-invented the format from
    the surrounding entries, so it is pinned here once and for all.
    """
    out = [iteration_header(it, date).rstrip("\n")]

    def field(label, value):
        if value:
            out.append(f"- **{label}:** {value}")

    field("Type", it.get("type", "feature"))
    field("Tags", ", ".join(it.get("tags") or []))
    field("Files changed", ", ".join(it.get("files_changed") or []))
    field("Summary", it.get("summary"))
    if it.get("confidence"):
        out.append(f"- **Confidence:** {int(it['confidence'])}/5")
    field("Tests", it.get("tests"))
    field("Errors", ", ".join(error_refs))
    field("Learnings", it.get("learnings"))
    field("Commits", it.get("commits"))
    return "\n".join(out) + "\n"


def apply_iterations(mem, plan, date, dry, touched, tally):
    """Step 1.5 session-harvest without the iteration-logger body injection.

    Judgment (what counts as ONE iteration - distinct approaches, not file
    saves) stays with the model and arrives in the plan. Everything below is
    mechanical: id continuation, the recurrence rule (same category AND >= 2
    overlapping tags), markdown rendering, working-memory bookkeeping.
    """
    items = plan.get("iterations") or []
    if not items:
        return
    log = ""
    log_path = os.path.join(mem, "iterations/iteration-log.md")
    if os.path.exists(log_path):
        log = open(log_path, encoding="utf-8").read()
    if not log.strip():
        log = "# Iteration Log\n"

    errors = load_json(mem, "iterations/errors.json", [], via="iterations")
    err_archive = archived_rows(mem, "iterations/errors.json")
    session = load_json(mem, "working/current-session.json",
                        {"errors_this_session": [], "learnings_draft": []}, via="iterations")
    blocks, new_error_ids, wrote_errors = [], [], False

    for it in items:
        # Dedup FIRST. Rendering needs the error ids, but the header does not -
        # and processing errors before the skip made every re-run count the same
        # error as another recurrence (occurrences 2 -> 3 -> 4 over three
        # identical runs, Codex review of 1e5c504). A skipped iteration must
        # touch nothing at all.
        header = iteration_header(it, date)
        if header in log or any(b.startswith(header) for b in blocks):
            tally["iterations_skipped_duplicate"] += 1
            continue

        refs = []
        for err in (it.get("errors") or []):
            existing = find_recurrence(errors, err)
            if existing is not None:
                existing["occurrences"] = int(existing.get("occurrences", 1)) + 1
                existing.setdefault("recurrence_dates", []).append(date)
                existing["last_seen"] = date
                refs.append(f"(Recurrence of {existing.get('id')})")
                tally["errors_recurred"] += 1
            else:
                entry = {
                    "id": next_id(errors, "err-", pad=3, reserved=err_archive),
                    "date": date,
                    "category": err.get("category", "runtime"),
                    "tags": list(err.get("tags") or []),
                    "trigger": err.get("trigger", ""),
                    "problem": err.get("problem", ""),
                    "root_cause": err.get("root_cause", ""),
                    "fix": err.get("fix", ""),
                    "failed_approaches": list(err.get("failed_approaches") or []),
                    "prevention": err.get("prevention", ""),
                    "severity": err.get("severity", "minor"),
                    "attempts": int(err.get("attempts", 1)),
                    "confidence": int(err.get("confidence", 3)),
                    "occurrences": 1,
                    "recurrence_dates": [],
                    "last_seen": date,
                }
                errors.append(entry)
                refs.append(entry["id"])
                new_error_ids.append(entry["id"])
                tally["errors_added"] += 1
            wrote_errors = True

        blocks.append(render_iteration(it, date, refs))
        tally["iterations_logged"] += 1

    if blocks:
        write_atomic(mem, "iterations/iteration-log.md",
                     log.rstrip() + "\n\n" + "\n".join(blocks), dry, touched, via="iterations")
    if wrote_errors:
        write_json(mem, "iterations/errors.json", errors, dry, touched, via="iterations")
    if new_error_ids or blocks:
        for eid in new_error_ids:
            if eid not in session.setdefault("errors_this_session", []):
                session["errors_this_session"].append(eid)
        session.setdefault("learnings_draft", [])
        write_json(mem, "working/current-session.json", session, dry, touched, via="iterations")


def find_recurrence(errors, err):
    """iteration-logger Step 2: same category AND >= 2 overlapping tags, last 20."""
    cat = err.get("category")
    tags = set(err.get("tags") or [])
    for existing in reversed(errors[-20:]):
        if existing.get("category") != cat:
            continue
        if len(tags & set(existing.get("tags") or [])) >= 2:
            return existing
    return None


def apply_decisions(mem, plan, date, dry, touched, tally):
    """Step 4.5 decision-scan without the context-keeper body injection.

    Judgment (is this a decision of record at all?) stays with the model.
    Mechanical here: id continuation, the append-only rule, and the supersede
    flip - old records are never deleted or rewritten, only their status moves.
    """
    items = plan.get("decisions") or []
    if not items:
        return
    rows = load_json(mem, "context/decisions.json", [], via="decisions")
    archive = archived_rows(mem, "context/decisions.json")
    by_id = {str(r.get("id")): r for r in rows}
    # Identity is (title, supersedes), not title alone. Title-only dedup
    # discarded a legitimate replacement that reused its predecessor's title and
    # left the old record active (Codex review of 1e5c504); dropping the check
    # for superseding decisions instead made them non-idempotent - a re-run
    # appended the same decision again (caught by the smoke run against a copy
    # of the real store).
    seen = {(norm(r.get("title")), str(r.get("supersedes") or "")) for r in rows}

    for it in items:
        title = (it.get("title") or "").strip()
        sup = it.get("supersedes")
        key = (norm(title), str(sup or ""))
        if key in seen:
            tally["decisions_skipped_duplicate"] += 1
            continue
        seen.add(key)
        entry = {
            "id": next_id(rows, "D-", pad=3, reserved=archive),
            "date": date,
            "type": it.get("type", "architecture-decision"),
            "title": title,
            "status": "active",
            "context": it.get("context", ""),
            "options_considered": list(it.get("options_considered") or []),
            "decision": it.get("decision", ""),
            "consequences": it.get("consequences", ""),
            "supersedes": it.get("supersedes"),
            "tags": list(it.get("tags") or []),
        }
        if sup:
            # validate_plan() already proved the target exists.
            by_id[str(sup)]["status"] = "superseded"
            tally["decisions_superseded"] += 1
        rows.append(entry)
        by_id[entry["id"]] = entry
        tally["decisions_added"] += 1
        tally["decision_ids"].append(entry["id"])

    write_json(mem, "context/decisions.json", rows, dry, touched, via="decisions")


def _near(text, rows, lo, hi=1.01):
    """Live rows whose token Jaccard to `text` lies in [lo, hi), best first."""
    hits = []
    for r in rows:
        score = _jaccard(text, r.get("text", ""))
        if lo <= score < hi:
            hits.append((score, r))
    hits.sort(key=lambda x: -x[0])
    return hits


def apply_learnings(mem, plan, date, dry, touched, tally):
    items = plan.get("learnings") or []
    rows = load_json(mem, "learnings/learnings.json", [])
    archive = archived_rows(mem, "learnings/learnings.json") if items else []
    by_id = {str(r.get("id")): r for r in rows if isinstance(r, dict)}
    seen = {norm(r.get("text")) for r in rows}
    added, refreshed = [], False
    for it in items:
        text = (it.get("text") or "").strip()
        if not text:
            raise PlanError("learning without 'text'")
        # Step 3a in code: the model's verdict on a near duplicate it was shown
        # (duplicate_of), else exact text, else Jaccard >= DUP_MIN.
        twin = by_id.get(str(it.get("duplicate_of") or ""))
        if twin is None and norm(text) in seen:
            twin = next((r for r in rows if norm(r.get("text")) == norm(text)), None)
        if twin is None:
            close = _near(text, rows, DUP_MIN)
            twin = close[0][1] if close else None
        if twin is not None or norm(text) in seen:
            if twin is not None and twin.get("last_relevant") != date:
                twin["last_relevant"] = date
                refreshed = True
            tally["learnings_skipped_duplicate"] += 1
            continue
        near = _near(text, rows, NEAR_MIN, DUP_MIN)[:NEAR_TOP]
        if near:
            tally["near_duplicates"][text[:60]] = [
                {"id": r.get("id"), "score": round(sc, 2), "text": str(r.get("text", ""))[:200]}
                for sc, r in near]
        seen.add(norm(text))
        entry = {
            "id": next_id(rows + added, "L", reserved=archive),
            "date": date,
            "text": text,
            "importance": it.get("importance", 3),  # shape checked in validate_plan
            "tags": list(it.get("tags") or []),
            "layer": "short-term",
            "superseded_by": None,
            "last_relevant": date,
            "derived_from": list(it.get("derived_from") or []),
            "review_after": (
                _dt.date.fromisoformat(date) + _dt.timedelta(days=REVIEW_AFTER_DAYS)
            ).isoformat(),
        }
        # Optional short form the projections (MEMORY.md / AGENTS.md) show instead of
        # cutting the full text at 150 chars. Additive: written only when supplied.
        summary = " ".join(str(it.get("summary") or "").split())
        if summary:
            entry["summary"] = summary
            if len(summary) > SUMMARY_MAX:
                tally["warnings"].append(
                    f"{entry['id']}: summary {len(summary)} Zeichen > {SUMMARY_MAX} — die Projektion kuerzt sie")
        # Step 3d.1: derived from importance alone. Plan-supplied bridge_status
        # is deliberately dropped - a verdict exists only via the plan section
        # `bridge_decisions`, which demands a reason per entry (5.7.0).
        if entry["importance"] >= 4:
            entry["bridge_status"] = "candidate"
        # Mixed form (F4): the agent's own judgment (`rule_class: true`) or a rule tag
        # puts the learning into the gate - candidate whatever its importance, hidden
        # from Step 3d, approved only via rule_explanations + rule_releases.
        if it.get("rule_class") is True or RULE_TAGS & {str(t).lower() for t in entry["tags"]}:
            entry["rule_class"] = True
            entry["release"] = {"status": "needs_explanation"}
            entry["bridge_status"] = "candidate"
        if looks_mojibake(text):
            tally["warnings"].append(f"{entry['id']}: text looks like Mojibake (UTF-8 read as cp1252) - "
                                     f"fix it through the 'corrections' section")
        added.append(entry)
    if added or refreshed:
        rows.extend(added)
        write_json(mem, "learnings/learnings.json", rows, dry, touched, via="learnings")
        tally["learnings_added"] = len(added)
        tally["learning_ids"] = [e["id"] for e in added]
        if added:
            render_learnings_md(mem, rows, dry, touched)
    # Store-wide, not session-scoped: earlier undecided candidates must keep
    # reaching Step 3d.2. Pre-3d entries lack the field (never backfilled) and
    # are invisible here by design.
    refresh_queues(rows, tally)


def open_bridge_candidates(rows):
    """Live rows still waiting for a Step 3d verdict. A superseded candidate is
    not decidable (bridge_decisions refuses it), so listing it would ask forever."""
    return [r["id"] for r in rows if isinstance(r, dict) and r.get("bridge_status") == "candidate"
            and not r.get("superseded_by") and not is_rule_class(r)]


def refresh_queues(rows, tally):
    """The two open queues after a store write: bridge candidates the agent decides
    (Step 3d) and rule-class learnings waiting in the gate (Step 3f)."""
    tally["bridge_candidates"] = open_bridge_candidates(rows)
    tally["rule_class_open"] = rule_class_open(rows)


# Step 3d (5.7.0, Owner-Entscheid 2026-10-16): the bridge gate is delegated - the
# agent pre-assesses, decides and reports one reason per entry. Only these states
# are decidable: `retired` belongs to the Codex ingest, a principle pointer to
# maintain Step 5c, a superseded row to its successor.
BRIDGE_DECIDABLE = ("candidate", "approved", "rejected")
BRIDGE_VERDICTS = ("approved", "rejected")
BRIDGE_DECIDERS = ("agent", "owner")

# P1/B2 (owner request 2026-10-27: plain-language explanations were missing): every proposal the
# owner is shown carries `plain` - plain German, no jargon, what changes concretely. The technical
# wording stays next to it and is what gets written into the file; `plain` is the explanation.
PLAIN_MAX = 600


def clean_plain(value, what: str) -> str:
    """The plain-language text of an owner-facing proposal: a non-empty string of at most PLAIN_MAX
    characters once whitespace is collapsed. Returns the collapsed text, or raises PlanError."""
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"{what}: 'plain' is required (plain German for the owner, no jargon, "
                        f"what changes concretely)")
    text = " ".join(value.split())
    if len(text) > PLAIN_MAX:
        raise PlanError(f"{what}: 'plain' has {len(text)} characters (max {PLAIN_MAX})")
    return text


def validate_bridge_decisions(mem, plan):
    """Shape and target of every verdict, before the first byte (one bad entry
    rejects the whole plan, like a dangling `supersedes`)."""
    if "bridge_decisions" not in plan:
        return
    items = plan["bridge_decisions"]
    if not isinstance(items, list):
        raise PlanError("bridge_decisions must be a list")
    if not items:
        return
    live = {str(r.get("id")): r for r in load_json(mem, "learnings/learnings.json", [])
            if isinstance(r, dict)}
    seen = set()
    for it in items:
        if not isinstance(it, dict):
            raise PlanError("bridge decision is not an object")
        lid = str(it.get("id") or "").strip()
        if it.get("status") not in BRIDGE_VERDICTS:
            raise PlanError(f"bridge decision {lid}: status must be one of {BRIDGE_VERDICTS}, "
                            f"got {it.get('status')!r}")
        note = it.get("note")
        if not isinstance(note, str) or not note.strip():
            raise PlanError(f"bridge decision {lid}: 'note' (the reason) is required")
        clean_plain(it.get("plain"), f"bridge decision {lid}")
        if it.get("by", "agent") not in BRIDGE_DECIDERS:
            raise PlanError(f"bridge decision {lid}: 'by' must be one of {BRIDGE_DECIDERS}")
        if lid in seen:
            raise PlanError(f"bridge decision {lid} appears twice")
        seen.add(lid)
        row = live.get(lid)
        if row is None:
            raise PlanError(f"bridge decision points at unknown learning {lid!r}")
        if row.get("kind") == "principle":
            raise PlanError(f"{lid} is a principle pointer - its bridge state belongs to maintain Step 5c")
        if row.get("superseded_by"):
            raise PlanError(f"{lid} is superseded by {row['superseded_by']} - decide the successor")
        if is_rule_class(row) and it["status"] == "approved":
            raise PlanError(f"{lid} is a rule-class learning - approval only via 'rule_releases' "
                            f"(second-model explanation, then the owner)")
        if row.get("bridge_status") not in BRIDGE_DECIDABLE:
            raise PlanError(f"{lid} is not a bridge candidate (bridge_status "
                            f"{row.get('bridge_status')!r})")


def apply_bridge_decisions(mem, plan, date, dry, touched, tally):
    """Plan section `bridge_decisions` (Step 3d.4): sets bridge_status and the
    reason `bridge_note` = "<date> <by>: <note>" on the object. Same verdict,
    decider and reason again changes nothing - also on a later day, so the
    original decision date survives (Codex F1); a new reason for the same verdict
    is written but does not count as a status change (no projection needed)."""
    items = plan.get("bridge_decisions") or []
    if not items:
        return
    rows = load_json(mem, "learnings/learnings.json", [])
    by_id = {str(r.get("id")): r for r in rows if isinstance(r, dict)}
    dirty = False
    for it in items:
        row = by_id[str(it["id"]).strip()]
        status = it["status"]
        reason = f"{it.get('by', 'agent')}: {' '.join(it['note'].split())}"
        same_reason = str(row.get("bridge_note") or "").split(" ", 1)[-1] == reason
        plain = clean_plain(it.get("plain"), f"bridge decision {row.get('id')}")
        if row.get("bridge_status") != status:
            tally["bridge_status_changed"] += 1
        if row.get("bridge_status") != status or not same_reason:
            row["bridge_status"], row["bridge_note"] = status, f"{date} {reason}"
            dirty = True
        if row.get("bridge_plain") != plain:  # P1: the owner-facing text lives in its own field
            row["bridge_plain"] = plain       # (bridge_note keeps its '<date> <by>: <note>' shape - review_sweep reads it)
            dirty = True
        if is_rule_class(row) and status == "rejected" and (row.get("release") or {}).get("status") != "rejected":
            row["release"] = dict(row.get("release") or {}, status="rejected", rejected_by=it.get("by", "agent"),
                                  rejected_at=date)
            dirty = True
        tally[f"bridge_{status}"].append(str(row["id"]))
    if dirty:
        write_json(mem, "learnings/learnings.json", rows, dry, touched, via="learnings")
    refresh_queues(rows, tally)


# Corrections (DCO #9393): the one sanctioned way to change the TEXT of an existing
# row. The wrap-up path only ever adds rows, so a wrong title or a damaged learning
# text had no writer at all and the stores were repaired by hand - which the
# applier-ownership rule above forbids. A correction keeps the old text on the row
# (`corrections[]`: who, when, why, what it was), so nothing is lost and a wrong
# correction can be undone from the store itself.
CORRECTION_DECIDERS = ("agent", "owner")
CORRECTION_SECTIONS = ("tasks", "learnings", "decisions", "user_md")
# Decision fields a correction may rewrite (DCO #10057). `options_considered` is nested
# (list of {option, pros[], cons[]}); it may only change its strings, never its shape.
DECISION_TEXT_FIELDS = ("title", "context", "decision", "consequences")
DECISION_CORRECTABLE = DECISION_TEXT_FIELDS + ("options_considered",)
# Below this similarity a "correction" is a different statement, and a different
# statement is a new learning that supersedes the old one - not a rewrite in place.
CORRECTION_SIMILARITY_MIN = 0.6


def _unique_row(rows, rid, what):
    hits = [r for r in rows if isinstance(r, dict) and str(r.get("id")) == rid]
    if not hits:
        raise PlanError(f"correction points at unknown {what} {rid!r}")
    if len(hits) > 1:
        raise PlanError(f"correction id {rid!r} is ambiguous ({len(hits)} {what}s share it) - not guessing")
    return hits[0]


def _correction_common(it, kind, seen):
    if not isinstance(it, dict):
        raise PlanError(f"correction ({kind}) is not an object")
    rid = str(it.get("id") or "").strip()
    if not rid:
        raise PlanError(f"correction ({kind}) without 'id'")
    reason = it.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise PlanError(f"correction {rid}: 'reason' is required")
    if it.get("by", "agent") not in CORRECTION_DECIDERS:
        raise PlanError(f"correction {rid}: 'by' must be one of {CORRECTION_DECIDERS}")
    if (kind, rid) in seen:
        raise PlanError(f"correction {rid} appears twice")
    seen.add((kind, rid))
    return rid


def validate_corrections(mem, plan, paths=None):
    """Every detectable error before the first byte, like the other sections."""
    if "corrections" not in plan:
        return
    spec = plan["corrections"]
    if not isinstance(spec, dict) or not set(spec) <= set(CORRECTION_SECTIONS):
        raise PlanError("corrections must be an object with 'tasks', 'learnings', 'decisions' "
                        "and/or 'user_md'")
    for key in CORRECTION_SECTIONS:
        if not isinstance(spec.get(key, []), list):
            raise PlanError(f"corrections.{key} must be a list")
    seen: set = set()
    tasks = spec.get("tasks") or []
    if tasks:
        rows = load_json(mem, "context/open-tasks.json", [])
        for it in tasks:
            rid = _correction_common(it, "task", seen)
            row = _unique_row(rows, rid, "task")
            title = " ".join(str(it.get("title") or "").split())
            if not title:
                raise PlanError(f"correction {rid}: empty 'title'")
            if title == " ".join(str(row.get("title") or "").split()):
                continue  # already corrected: a re-run changes nothing
            if any(r is not row and r.get("status") != "closed" and norm(r.get("title")) == norm(title)
                   for r in rows if isinstance(r, dict)):
                raise PlanError(f"correction {rid}: title equals another open task")
    lrns = spec.get("learnings") or []
    if lrns:
        rows = load_json(mem, "learnings/learnings.json", [])
        for it in lrns:
            rid = _correction_common(it, "learning", seen)
            row = _unique_row(rows, rid, "learning")
            if row.get("kind") == "principle":
                raise PlanError(f"{rid} is a principle pointer - its wording is set by the 'principles' section")
            for field in ("text", "summary"):
                if field not in it:
                    continue
                new = it[field]
                if not isinstance(new, str) or not new.strip():
                    raise PlanError(f"correction {rid}: '{field}' must be a non-empty string")
                old = row.get(field)
                if old is None and field == "text":
                    raise PlanError(f"correction {rid}: row has no text")
                if old is not None and norm(new) != norm(old):
                    ratio = difflib.SequenceMatcher(None, norm(old), norm(new)).ratio()
                    if ratio < CORRECTION_SIMILARITY_MIN:
                        raise PlanError(
                            f"correction {rid}: new {field} is a different statement (similarity {ratio:.2f} < "
                            f"{CORRECTION_SIMILARITY_MIN}) - write a new learning and supersede this one")
            if "text" not in it and "summary" not in it:
                raise PlanError(f"correction {rid}: needs 'text' and/or 'summary'")
    decs = spec.get("decisions") or []
    if decs:
        rows = load_json(mem, "context/decisions.json", [], via="decisions")
        for it in decs:
            rid = _correction_common(it, "decision", seen)
            row = _unique_row(rows, rid, "decision")
            extra = set(it) - {"id", "reason", "by"} - set(DECISION_CORRECTABLE)
            if extra:
                raise PlanError(f"correction {rid}: field(s) {sorted(extra)} cannot be corrected - "
                                f"only {list(DECISION_CORRECTABLE)} (a status change is a new decision)")
            if not set(it) & set(DECISION_CORRECTABLE):
                raise PlanError(f"correction {rid}: needs at least one of {list(DECISION_CORRECTABLE)}")
            for field in DECISION_TEXT_FIELDS:
                if field not in it:
                    continue
                new, old = it[field], row.get(field)
                if not isinstance(new, str) or not new.strip():
                    raise PlanError(f"correction {rid}: '{field}' must be a non-empty string")
                if not isinstance(old, str):
                    raise PlanError(f"correction {rid}: the decision has no text in '{field}'")
                _check_similar(rid, field, old, new)
            if "options_considered" in it:
                _check_same_shape(rid, "options_considered", row.get("options_considered"), it["options_considered"])
    lines = spec.get("user_md") or []
    if lines:
        text = _read_user_md_raw(mem, paths=paths)
        have = [ln.rstrip("\r\n") for ln in text.splitlines(keepends=True)]
        for it in lines:
            if not isinstance(it, dict):
                raise PlanError("correction (user_md) is not an object")
            old, new = it.get("old"), it.get("new")
            if not isinstance(old, str) or not old.strip() or not isinstance(new, str) or not new.strip():
                raise PlanError("correction (user_md): 'old' and 'new' must be non-empty strings")
            tag = old[:40]
            reason = it.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise PlanError(f"correction user_md {tag!r}: 'reason' is required")
            if it.get("by", "agent") not in CORRECTION_DECIDERS:
                raise PlanError(f"correction user_md {tag!r}: 'by' must be one of {CORRECTION_DECIDERS}")
            if ("user_md", old) in seen:
                raise PlanError(f"correction user_md {tag!r} appears twice")
            seen.add(("user_md", old))
            if "\n" in old or "\n" in new or "\r" in old or "\r" in new:
                raise PlanError(f"correction user_md {tag!r}: one line per correction")
            # Identity text has its own gate (queue, trust check, owner review). The correction
            # path is NOT a way around it: it may repair the encoding of a line, never reword it.
            if new != repair_mojibake(old):
                raise PlanError(f"correction user_md {tag!r}: 'new' is not the encoding repair of 'old' - "
                                f"user.md changes only through the candidate queue (wrap-up Step 6)")
            if have.count(old) > 1:
                raise PlanError(f"correction user_md {tag!r}: the line occurs {have.count(old)} times - not guessing")
            if have.count(old) == 0 and have.count(new) == 0:
                raise PlanError(f"correction user_md {tag!r}: no such line in user.md")


def _check_similar(rid, field, old, new):
    """Same statement, repaired - or it is a new decision/learning, not a correction."""
    if norm(new) != norm(old):
        ratio = difflib.SequenceMatcher(None, norm(old), norm(new)).ratio()
        if ratio < CORRECTION_SIMILARITY_MIN:
            raise PlanError(f"correction {rid}: new {field} is a different statement (similarity {ratio:.2f} < "
                            f"{CORRECTION_SIMILARITY_MIN}) - record a new entry and supersede this one")


def _check_same_shape(rid, path, old, new):
    """`new` keeps the structure of `old` (lists same length, dicts same keys); only the
    strings inside may change, and each by a repair, not a rewrite."""
    if isinstance(old, str):
        if not isinstance(new, str) or not new.strip():
            raise PlanError(f"correction {rid}: {path} must stay a non-empty string")
        _check_similar(rid, path, old, new)
    elif isinstance(old, list):
        if not isinstance(new, list) or len(new) != len(old):
            raise PlanError(f"correction {rid}: {path} must keep its {len(old)} entries")
        for i, (o, n) in enumerate(zip(old, new)):
            _check_same_shape(rid, f"{path}[{i}]", o, n)
    elif isinstance(old, dict):
        if not isinstance(new, dict) or set(new) != set(old):
            raise PlanError(f"correction {rid}: {path} must keep its keys {sorted(old)}")
        for k in old:
            _check_same_shape(rid, f"{path}.{k}", old[k], new[k])
    elif new != old:
        raise PlanError(f"correction {rid}: {path} is not text and must stay {old!r}")


def _id_paths(mem, paths):
    """The identity files of this run: the given IdentityPaths, else the project's own (legacy) files."""
    return paths if paths is not None else identity_store.legacy_paths(mem)


def _origin(mem):
    """Central mode: where a row comes from - the project folder of this run's store (identity_store.project_key);
    `id` is the row's old project id, set only by the migration."""
    return {"projekt": identity_store.project_key(mem, os.path.expanduser("~")), "id": None}


def _legacy_contents(mem):
    """Lines of the project's own user.md, [] when it is missing or unreadable (only used to explain a skip)."""
    own = identity_store.legacy_paths(mem)
    try:
        return [c for c, _eol in split_user_md(_read_user_md_raw(mem, "user_md_supersede", own))]
    except PlanError:
        return []


def plan_touches_identity(plan) -> bool:
    """True when the plan writes the profile, its changelog or its queue (the central lock is needed)."""
    plan = plan or {}
    corr = plan.get("corrections")
    return bool("user_candidates" in plan or plan.get("consolidate") or plan.get("user_md_supersede")
                or (corr.get("user_md") if isinstance(corr, dict) else corr))


def _read_user_md_raw(mem, what="correction (user_md)", paths=None):
    paths = _id_paths(mem, paths)
    path = os.path.join(paths.root, paths.md)
    if not os.path.isfile(path):
        raise PlanError(f"{what}: {paths.md} does not exist")
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            return fh.read()
    except UnicodeDecodeError as e:
        raise PlanError(f"{paths.md} is not UTF-8 ({e}) - nothing was written; repair the encoding")


def _stamp_correction(row, field, old, date, it, session_id):
    row.setdefault("corrections", []).append({
        "date": date, "by": it.get("by", "agent"), "session": session_id or None, "field": field,
        "reason": " ".join(str(it["reason"]).split()), "was": old})


def apply_corrections(mem, plan, date, dry, touched, tally, session_id="", paths=None):
    spec = plan.get("corrections") or {}
    tasks, lrns = spec.get("tasks") or [], spec.get("learnings") or []
    decs, lines = spec.get("decisions") or [], spec.get("user_md") or []
    if not tasks and not lrns and not decs and not lines:
        return
    if tasks:
        rows = load_json(mem, "context/open-tasks.json", [])
        changed = False
        for it in tasks:
            row = _unique_row(rows, str(it["id"]).strip(), "task")
            title = " ".join(str(it["title"]).split())
            if title == " ".join(str(row.get("title") or "").split()):
                tally["corrections_skipped_unchanged"] += 1
                continue
            if looks_mojibake(title):
                tally["warnings"].append(f"{row.get('id')}: corrected title still looks like Mojibake")
            _stamp_correction(row, "title", row.get("title"), date, it, session_id)
            row["title"], row["updated"] = title, date
            tally["tasks_retitled"] += 1
            tally["correction_ids"].append(str(row.get("id")))
            if row.get("status") in ("open", "blocked"):
                tally["corrections_projection_needed"] = True
            changed = True
        if changed:
            write_json(mem, "context/open-tasks.json", rows, dry, touched)
    if lrns:
        rows = load_json(mem, "learnings/learnings.json", [])
        changed = False
        for it in lrns:
            row = _unique_row(rows, str(it["id"]).strip(), "learning")
            row_changed = False
            for field in ("text", "summary"):
                if field not in it:
                    continue
                new = it[field].strip() if field == "text" else " ".join(it[field].split())
                if new == (row.get(field) or "").strip():
                    continue
                if looks_mojibake(new):
                    tally["warnings"].append(f"{row.get('id')}: corrected {field} still looks like Mojibake")
                _stamp_correction(row, field, row.get(field), date, it, session_id)
                row[field] = new
                row_changed = True
            if not row_changed:
                tally["corrections_skipped_unchanged"] += 1
                continue
            if row.get("rule_class") and isinstance(row.get("release"), dict):
                # A rule's wording changed after it was explained or released: the
                # explanation no longer describes the text, so the gate starts again.
                if row["release"].get("status") in ("explained", "released"):
                    row["release"] = {"status": "needs_explanation", "reset_by_correction": date}
                    if row.get("bridge_status") == "approved":
                        row["bridge_status"] = "candidate"
                        row["bridge_note"] = f"{date} {it.get('by', 'agent')}: Regeltext korrigiert, Freigabe neu noetig"
                        tally["bridge_status_changed"] += 1
                    tally["warnings"].append(f"{row.get('id')}: rule-class text corrected - release reset")
            tally["learnings_corrected"] += 1
            tally["correction_ids"].append(str(row.get("id")))
            if row.get("bridge_status") == "approved":
                tally["corrections_projection_needed"] = True
            changed = True
        if changed:
            write_json(mem, "learnings/learnings.json", rows, dry, touched, via="learnings")
            render_learnings_md(mem, rows, dry, touched)
            refresh_queues(rows, tally)
    if decs:
        rows = load_json(mem, "context/decisions.json", [], via="decisions")
        changed = False
        for it in decs:
            row = _unique_row(rows, str(it["id"]).strip(), "decision")
            row_changed = False
            for field in DECISION_CORRECTABLE:
                if field not in it:
                    continue
                new = it[field].strip() if isinstance(it[field], str) else it[field]
                cur = row.get(field)
                if new == (cur.strip() if isinstance(cur, str) else cur):
                    continue
                if _contains_mojibake(new):
                    tally["warnings"].append(f"{row.get('id')}: corrected {field} still looks like Mojibake")
                _stamp_correction(row, field, row.get(field), date, it, session_id)
                row[field] = new
                row_changed = True
            if not row_changed:
                tally["corrections_skipped_unchanged"] += 1
                continue
            if row.get("wiki_ref") or row.get("promoted_at"):
                tally["warnings"].append(f"{row.get('id')}: already promoted to the wiki "
                                         f"({row.get('wiki_ref') or 'wiki_ref missing'}) - the wiki copy is not repaired here")
            tally["decisions_corrected"] += 1
            tally["correction_ids"].append(str(row.get("id")))
            changed = True
        if changed:
            write_json(mem, "context/decisions.json", rows, dry, touched, via="decisions")
    if lines:
        paths = _id_paths(mem, paths)
        text = _read_user_md_raw(mem, paths=paths)
        parts = text.splitlines(keepends=True)
        stripped = [p.rstrip("\r\n") for p in parts]
        now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        entries = []
        for it in lines:
            old, new = it["old"], it["new"]
            if old not in stripped or old == new:
                tally["corrections_skipped_unchanged"] += 1   # already repaired: a re-run changes nothing
                continue
            if looks_mojibake(new):
                tally["warnings"].append(f"user.md: corrected line still looks like Mojibake: {new[:40]}")
            i = stripped.index(old)
            parts[i] = new + parts[i][len(old):]
            stripped[i] = new
            entries.append({"ts": now, "field": "user.md/encoding-correction", "old_value": old, "new_value": new,
                            "reason": " ".join(str(it["reason"]).split()), "by": it.get("by", "agent")})
            if paths.central:
                entries[-1]["origin"] = _origin(mem)
            tally["user_lines_corrected"] += 1
        if entries:
            # audit entry BEFORE the edit - the same ordering contract as the promotion path
            changelog = load_json(paths.root, paths.changelog, [])
            write_json(paths.root, paths.changelog, changelog + entries, dry, touched, via="identity")
            write_atomic(paths.root, paths.md, "".join(parts), dry, touched, via="identity")


def _contains_mojibake(value) -> bool:
    if isinstance(value, str):
        return looks_mojibake(value)
    if isinstance(value, list):
        return any(_contains_mojibake(v) for v in value)
    if isinstance(value, dict):
        return any(_contains_mojibake(v) for v in value.values())
    return False


def _repair_deep(value):
    if isinstance(value, str):
        return repair_mojibake(value)
    if isinstance(value, list):
        return [_repair_deep(v) for v in value]
    if isinstance(value, dict):
        return {k: _repair_deep(v) for k, v in value.items()}
    return value


def propose_encoding_corrections(mem, identity_store_arg=None):
    """Read-only: the correction plan that repairs every row whose text, summary or
    title was read as cp1252. Rows already clean are not listed, so applying the
    result and asking again yields an empty plan."""
    out = {"tasks": [], "learnings": [], "decisions": [], "user_md": []}
    why = "Encoding-Reparatur: UTF-8 war als cp1252 gelesen (Mojibake), Zeichen rueckuebersetzt"
    for r in load_json(mem, "context/open-tasks.json", []):
        if isinstance(r, dict) and looks_mojibake(r.get("title")):
            out["tasks"].append({"id": r.get("id"), "title": repair_mojibake(r["title"]), "reason": why})
    for r in load_json(mem, "learnings/learnings.json", []):
        if not isinstance(r, dict) or r.get("kind") == "principle":
            continue
        item = {"id": r.get("id")}
        for field in ("text", "summary"):
            if looks_mojibake(r.get(field)):
                item[field] = repair_mojibake(r[field])
        if len(item) > 1:
            item["reason"] = why
            out["learnings"].append(item)
    for r in load_json(mem, "context/decisions.json", [], via="decisions"):
        if not isinstance(r, dict):
            continue
        item = {"id": r.get("id")}
        for field in DECISION_CORRECTABLE:
            if _contains_mojibake(r.get(field)):
                item[field] = _repair_deep(r[field])
        if len(item) > 1:
            item["reason"] = why
            out["decisions"].append(item)
    try:
        # read only, like stage 0: the active central profile when there is one, else the project's own file (R3)
        store, _source = identity_store.resolve_store(identity_store_arg, os.environ)
        own = (identity_store.central_paths(store) if identity_store.central_active(store)
               else identity_store.legacy_paths(mem))
        user_text = _read_user_md_raw(mem, paths=own) if os.path.isfile(os.path.join(own.root, own.md)) else ""
    except PlanError:
        user_text = ""   # not UTF-8: --lint / maintain Step 2 report that, the proposal stays read-only
    for line in user_text.splitlines():
        if looks_mojibake(line):
            out["user_md"].append({"old": line, "new": repair_mojibake(line), "reason": why})
    return {"corrections": out}


# ----------------------------------------------- user_md_supersede (P1 / B2)
# A profile line can be REPLACED. Until now user.md only grew: a promoted line stayed even when the
# owner had since said the opposite, and `corrections.user_md` may only repair the encoding. A
# supersede is the regular way to resolve such a contradiction: the old wording survives in
# user-changelog.json (written FIRST), the file changes by exactly one line.
#
# Contract (all checked before the first byte of the run is written):
#   - exactly one target per entry: `match` (the exact line text) or `line_id` (the candidate id the
#     line cites, "(UC12, date)"); zero or several hits reject the plan - the script never guesses
#   - `new_text` is one bullet line (no line separator of any kind), `reason`, `plain` (owner-facing,
#     <= 600 chars) and `quote` (the user's literal words) are required
#   - a placeholder, a heading or the preamble (everything before the first '## ') is never a target;
#     `trust_source` must be conversation; two entries with the same target or the same new_text reject the plan
#   - the quote must be VERIFIED against the session transcript (identity_quote.py); an entry whose quote is not
#     is skipped (nothing written for it, warning + tally), the rest of the plan runs
#   - the file is rewritten LOSSLESSLY: every byte except the target line stays - CRLF, BOM, the
#     preamble, the section order (read_user_md/write_user_md cannot promise any of that)
#   - repeatable: when the old line is gone but `new_text` stands exactly once, the entry counts as
#     applied (`supersede_skipped_unchanged`); with `line_id` the audit log must also hold an entry for that
#     id and text (the new line no longer cites the id); after a crash between the changelog entry and
#     the file the rerun does not write the entry twice
_BULLET_LINE = re.compile(r"- \S")
_LINE_ID = re.compile(r"[A-Za-z]{1,6}\d{1,6}")
# Everything str.splitlines() ends a line at: CR, LF, VT, FF, FS, GS, RS, NEL, LS, PS. A new_text holding one of them
# would turn one profile line into two for every reader that splits lines the Unicode way.
LINE_SEPARATORS = "".join(map(chr, (0x0D, 0x0A, 0x0B, 0x0C, 0x1C, 0x1D, 0x1E, 0x85, 0x2028, 0x2029)))
BOM = chr(0xFEFF)


def split_user_md(text: str) -> list:
    """[(content, eol)] with ''.join(content + eol) == text. Only '\\n' ends a line; a CR before it
    belongs to the line ending, so CRLF, LF and a last line without any ending all survive."""
    parts = text.split("\n")
    out = []
    for i, p in enumerate(parts):
        last = i == len(parts) - 1
        if p.endswith("\r"):
            out.append((p[:-1], "\r" + ("" if last else "\n")))
        else:
            out.append((p, "" if last else "\n"))
    return out


def _first_section_line(contents) -> int:
    """Index of the first '## ' heading (a BOM in front of it is ignored); everything before is the preamble."""
    for i, c in enumerate(contents):
        if c.lstrip(BOM).startswith("## "):
            return i
    return len(contents)


def _supersede_logged(changelog, line_id, new_text) -> bool:
    """True when the audit log proves that a supersede of `line_id` to `new_text` already ran."""
    return any(isinstance(e, dict) and e.get("field") == "user.md/supersede" and e.get("candidate_id") == line_id
               and e.get("new_value") == new_text for e in changelog)


def resolve_user_md_supersede(mem, items, paths=None):
    """Validate every entry against the file as it stands and resolve its target line.
    Returns (resolved, skipped, legacy_only): `resolved` = [{index, old, new, item, line_id, tag, quote}] for the
    lines that would change, `skipped` = entries that are already applied, `legacy_only` = tags of entries whose
    target stands only in the project's user.md while the central profile is active (P2: skipped with a warning).
    Raises PlanError on the first problem.
    The quote is only checked for its FORM here (a non-empty string); whether it is true is decided at apply time."""
    if not isinstance(items, list):
        raise PlanError("user_md_supersede must be a list")
    if not items:
        return [], 0, []
    paths = _id_paths(mem, paths)
    text = _read_user_md_raw(mem, "user_md_supersede", paths)
    contents = [c for c, _eol in split_user_md(text)]
    first_h2 = _first_section_line(contents)
    changelog = None  # read only when a line_id entry finds no line (rerun proof)
    resolved, skipped, taken, seen_new, seen_target = [], 0, set(), set(), set()
    legacy_only, legacy = [], None
    for it in items:
        if not isinstance(it, dict):
            raise PlanError("user_md_supersede entry is not an object")
        has_match, has_id = it.get("match") is not None, it.get("line_id") is not None
        if has_match == has_id:
            raise PlanError("user_md_supersede entry needs exactly one of 'match' or 'line_id'")
        tag = str(it.get("match") if has_match else it.get("line_id"))[:40]
        new = it.get("new_text")
        if not isinstance(new, str) or not new.strip():
            raise PlanError(f"user_md_supersede {tag!r}: 'new_text' is required")
        if any(ch in new for ch in LINE_SEPARATORS):
            raise PlanError(f"user_md_supersede {tag!r}: 'new_text' must be one line")
        if not _BULLET_LINE.match(new):
            raise PlanError(f"user_md_supersede {tag!r}: 'new_text' must be a bullet line ('- ...')")
        if is_placeholder_line(new):
            raise PlanError(f"user_md_supersede {tag!r}: 'new_text' is a placeholder")
        reason = it.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise PlanError(f"user_md_supersede {tag!r}: 'reason' is required")
        clean_plain(it.get("plain"), f"user_md_supersede {tag!r}")
        if not _clean_quote(it.get("quote")):
            # D7: replacing a profile line needs the user's own words. A missing quote is a form error; a quote
            # that cannot be verified later only skips this entry.
            raise PlanError(f"user_md_supersede {tag!r}: 'quote' is required (the user's literal words that justify "
                            f"the change, as a string)")
        if it.get("by", "agent") not in CORRECTION_DECIDERS:
            raise PlanError(f"user_md_supersede {tag!r}: 'by' must be one of {CORRECTION_DECIDERS}")
        if it.get("trust_source", "conversation") != "conversation":
            # Same boundary as Step 6.1: profile text may only originate from the user's own conversation.
            raise PlanError(f"user_md_supersede {tag!r}: trust_source must be 'conversation' "
                            f"(memory-poisoning defense), got {it.get('trust_source')!r}")
        if new in seen_new:
            raise PlanError(f"user_md_supersede {tag!r}: 'new_text' is the same as in another entry of this plan "
                            f"(two entries would leave the same line twice)")
        seen_new.add(new)
        line_id = None
        if has_match:
            old = it["match"]
            if not isinstance(old, str) or not old.strip() or "\n" in old or "\r" in old:
                raise PlanError(f"user_md_supersede {tag!r}: 'match' must be one non-empty line")
            target = ("match", old)
        else:
            line_id = it["line_id"]
            if not isinstance(line_id, str) or not _LINE_ID.fullmatch(line_id):
                raise PlanError(f"user_md_supersede: invalid line_id {line_id!r} (expected a candidate id like UC12)")
            target = ("line_id", line_id)
        if target in seen_target:
            raise PlanError(f"user_md_supersede {tag!r}: targets the same line twice")
        seen_target.add(target)
        if has_match:
            hits = [i for i, c in enumerate(contents) if c == old]
        else:
            cite = re.compile(r"[\[(]\s*" + re.escape(line_id) + r"\s*[,\])]")
            hits = [i for i, c in enumerate(contents) if cite.search(c)]
        if len(hits) > 1:
            raise PlanError(f"user_md_supersede {tag!r}: the target occurs {len(hits)} times - not guessing")
        if not hits:
            if has_match:
                applied = contents.count(new) == 1  # the old line is gone and the new one stands once
            else:
                # the new line no longer cites the id, so only the audit log can prove an earlier run (D6)
                if changelog is None:
                    changelog = load_json(paths.root, paths.changelog, [])
                applied = new in contents and _supersede_logged(changelog, line_id, new)
            if applied:
                skipped += 1  # applied by an earlier run
                continue
            if paths.central:
                if legacy is None:
                    legacy = _legacy_contents(mem)
                if (old in legacy) if has_match else any(cite.search(c) for c in legacy):
                    legacy_only.append(tag)  # the model still saw the old project file; not an error of the plan
                    continue
            raise PlanError(f"user_md_supersede {tag!r}: no such line in {os.path.basename(paths.md)}")
        idx = hits[0]
        if idx in taken:
            raise PlanError(f"user_md_supersede {tag!r}: targets the same line twice")
        taken.add(idx)  # before the no-op skip: an unchanged entry still claims its line (Codex round 2, P1-B2-R2-05)
        if idx < first_h2:
            raise PlanError(f"user_md_supersede {tag!r}: the target lies in the preamble (before the first '## ' heading) - "
                            f"only a bullet line inside a section can be replaced")
        old = contents[idx]
        if old == new:
            skipped += 1
            continue
        if not _BULLET_LINE.match(old):
            raise PlanError(f"user_md_supersede {tag!r}: the target is not a bullet line (heading, preamble or text)")
        if is_placeholder_line(old):
            raise PlanError(f"user_md_supersede {tag!r}: the target is a placeholder line")
        if new in contents:
            raise PlanError(f"user_md_supersede {tag!r}: 'new_text' already stands in {os.path.basename(paths.md)} "
                            f"as another line")
        resolved.append({"index": idx, "old": old, "new": new, "item": it, "line_id": line_id, "tag": tag,
                         "quote": _clean_quote(it.get("quote"))})
    return resolved, skipped, legacy_only


def validate_user_md_supersede(mem, plan, paths=None):
    if "user_md_supersede" in plan:
        resolve_user_md_supersede(mem, plan["user_md_supersede"], paths)


def apply_user_md_supersede(mem, plan, date, dry, touched, tally, session_id="", transcript=None, paths=None):
    """Plan section `user_md_supersede`: the audit entry goes into user-changelog.json FIRST, then the
    file is rewritten with exactly the target lines replaced (see the contract above).

    D7: an entry is applied only when its `quote` is verified against the session transcript (the same check
    as a candidate's quote, scripts/identity_quote.py). An entry whose quote is not verified is skipped - nothing
    is written for it, a warning and `tally.supersede_skipped_unverified` say so - and the rest of the plan runs."""
    items = plan.get("user_md_supersede") or []
    if not items:
        return
    paths = _id_paths(mem, paths)
    resolved, skipped, legacy_only = resolve_user_md_supersede(mem, items, paths)
    tally["supersede_skipped_unchanged"] += skipped
    for tag in legacy_only:
        tally["supersede_skipped_legacy"] += 1
        tally["warnings"].append(f"user_md_supersede {tag!r}: line not in profile.md - it stands only in the "
                                 f"project's user.md, which the central profile replaces; entry skipped, nothing "
                                 f"written for it")
    if not resolved:
        return
    verdicts, reasons = _verify_identity_quotes([r["quote"] for r in resolved], session_id, transcript, tally)
    kept = []
    for r in resolved:
        if verdicts.get(r["quote"]) is True:
            kept.append(r)
            if not _quote_related(r["quote"], r["new"]):
                tally["quote_unrelated"].append("supersede:" + r["tag"])
                tally["warnings"].append(f"user_md_supersede {r['tag']!r}: the verified quote shares no content word with "
                                         f"new_text - check that it really supports the change")
            if _polarity_differs(r["quote"], r["new"]):
                tally["quote_polarity"].append("supersede:" + r["tag"])  # calibration data only, no warning (noise)
        else:
            why = reasons.get(r["quote"]) or ("not verified" if verdicts.get(r["quote"]) is False else "no transcript")
            tally["supersede_skipped_unverified"] += 1
            tally["warnings"].append(f"user_md_supersede {r['tag']!r}: quote not verified ({why}) - entry skipped, "
                                     f"nothing written for it")
    resolved = kept
    if not resolved:
        return
    lines = split_user_md(_read_user_md_raw(mem, "user_md_supersede", paths))
    changelog = load_json(paths.root, paths.changelog, [])
    # A previous run may have died between the changelog write and the file write: its entries are the
    # tail of the log. Do not write them a second time.
    recent = [(e.get("field"), e.get("old_value"), e.get("new_value"), e.get("reason"), e.get("plain"), e.get("by"))
              for e in changelog[-len(resolved):] if isinstance(e, dict)]
    now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    entries = []
    for r in resolved:
        it = r["item"]
        entry = {"ts": now, "field": "user.md/supersede", "old_value": r["old"], "new_value": r["new"],
                 "reason": " ".join(it["reason"].split()), "plain": clean_plain(it["plain"], "user_md_supersede"),
                 "quote": r["quote"], "quote_verified": True,
                 "by": it.get("by", "agent"), "session": session_id or None}
        if r["line_id"]:
            entry["candidate_id"] = r["line_id"]
        if paths.central:
            entry["origin"] = _origin(mem)
        key = (entry["field"], entry["old_value"], entry["new_value"], entry["reason"], entry["plain"], entry["by"])
        if key not in recent:
            entries.append(entry)
        lines[r["index"]] = (r["new"], lines[r["index"]][1])
        if looks_mojibake(r["new"]):
            tally["warnings"].append(f"user.md: superseded line still looks like Mojibake: {r['new'][:40]}")
        tally["user_lines_superseded"] += 1
    if entries:
        # audit entry BEFORE the edit - the same ordering contract as the promotion path
        write_json(paths.root, paths.changelog, changelog + entries, dry, touched, via="identity")
    write_atomic(paths.root, paths.md, "".join(c + eol for c, eol in lines), dry, touched, via="identity")


# Learnings mixed form (DCO #9869, owner decision F4 2026-10-22). A normal learning
# is curated by the agent alone (Step 3d). A RULE-CLASS learning - one that changes
# a rule or the constitution - is held back from both projections until
#   1. a second model has explained it in plain words (`rule_explanations`, the
#      Erklaerbogen), and
#   2. the owner has put it into two sentences of his own (`rule_releases`).
# No live model call: the script only marks, stores the explanation it is handed and
# checks the shape; who explains is the session's job (wrap-up Step 3f).
RULE_TAGS = {"regel", "regeln", "rule", "rules", "verfassung", "constitution"}
RELEASE_OPEN = ("needs_explanation", "explained")
EXPLAIN_VERDICTS = ("confirms", "contradicts", "unsure")
TWO_SENTENCES_MAX = 400
SHEET_COPY_MAX = 0.9


def is_rule_class(row) -> bool:
    return isinstance(row, dict) and row.get("rule_class") is True


def rule_class_open(rows):
    """Store-wide queue of rule-class learnings still in the gate."""
    out = {"needs_explanation": [], "explained": []}
    for r in rows:
        if is_rule_class(r) and not r.get("superseded_by"):
            st = (r.get("release") or {}).get("status", "needs_explanation")
            if st in out:
                out[st].append(str(r.get("id")))
    return out


def sentence_count(text: str):
    parts = [p for p in re.split(r"(?<=[.!?])\s+", str(text or "").strip()) if p.strip()]
    return len(parts), all(len(p.split()) >= 3 for p in parts)


def validate_rule_gate(mem, plan):
    exps, rels = plan.get("rule_explanations"), plan.get("rule_releases")
    for name, items in (("rule_explanations", exps), ("rule_releases", rels)):
        if items is not None and not isinstance(items, list):
            raise PlanError(f"{name} must be a list")
    if not exps and not rels:
        return
    live = {str(r.get("id")): r for r in load_json(mem, "learnings/learnings.json", []) if isinstance(r, dict)}
    seen = set()
    for it in (exps or []):
        if not isinstance(it, dict):
            raise PlanError("rule explanation is not an object")
        lid = str(it.get("id") or "").strip()
        row = live.get(lid)
        if row is None:
            raise PlanError(f"rule explanation points at unknown learning {lid!r}")
        if not is_rule_class(row) or row.get("superseded_by"):
            raise PlanError(f"{lid} is not a live rule-class learning")
        if (row.get("release") or {}).get("status") not in RELEASE_OPEN:
            raise PlanError(f"{lid}: release state is {(row.get('release') or {}).get('status')!r} - "
                            f"explain only while the gate is open")
        for field in ("explained_by", "risk"):
            if not isinstance(it.get(field), str) or not it[field].strip():
                raise PlanError(f"rule explanation {lid}: '{field}' is required")
        clean_plain(it.get("plain"), f"rule explanation {lid}")  # P1: same limits as every owner-facing proposal
        if it.get("verdict") not in EXPLAIN_VERDICTS:
            raise PlanError(f"rule explanation {lid}: 'verdict' must be one of {EXPLAIN_VERDICTS}")
        if "affects" in it and not (isinstance(it["affects"], list) and all(isinstance(a, str) for a in it["affects"])):
            raise PlanError(f"rule explanation {lid}: 'affects' must be a list of strings")
        if ("exp", lid) in seen:
            raise PlanError(f"rule explanation {lid} appears twice")
        seen.add(("exp", lid))
    for it in (rels or []):
        if not isinstance(it, dict):
            raise PlanError("rule release is not an object")
        lid = str(it.get("id") or "").strip()
        row = live.get(lid)
        if row is None:
            raise PlanError(f"rule release points at unknown learning {lid!r}")
        if not is_rule_class(row) or row.get("superseded_by"):
            raise PlanError(f"{lid} is not a live rule-class learning")
        if it.get("by") != "owner":
            raise PlanError(f"rule release {lid}: only the owner releases a rule-class learning ('by': 'owner') - "
                            f"a self-declaration the script cannot verify; write it only after the owner's own sentences")
        status = it.get("status", "released")
        if status not in ("released", "rejected"):
            raise PlanError(f"rule release {lid}: status must be 'released' or 'rejected'")
        if ("rel", lid) in seen:
            raise PlanError(f"rule release {lid} appears twice")
        seen.add(("rel", lid))
        if status == "rejected":
            continue
        rel = row.get("release") or {}
        if rel.get("status") != "explained":
            raise PlanError(f"rule release {lid}: release state is {rel.get('status')!r} - the second "
                            f"model's explanation must be on file first")
        text = it.get("two_sentences")
        if not isinstance(text, str) or not text.strip():
            raise PlanError(f"rule release {lid}: 'two_sentences' (the owner's own words) is required")
        n, wordy = sentence_count(text)
        if not (2 <= n <= 3) or not wordy:
            raise PlanError(f"rule release {lid}: 'two_sentences' must be 2 (at most 3) real sentences "
                            f"of 3+ words, got {n}")
        if len(text) > TWO_SENTENCES_MAX:
            raise PlanError(f"rule release {lid}: 'two_sentences' longer than {TWO_SENTENCES_MAX} characters")
        for source in ((rel.get("sheet") or {}).get("plain"), row.get("text")):
            if source and difflib.SequenceMatcher(None, norm(text), norm(source)).ratio() >= SHEET_COPY_MAX:
                raise PlanError(f"rule release {lid}: 'two_sentences' is a copy of the sheet or the rule - "
                                f"the point is that the owner says it in his own words")


def apply_rule_explanations(mem, plan, date, dry, touched, tally):
    items = plan.get("rule_explanations") or []
    if not items:
        return
    rows = load_json(mem, "learnings/learnings.json", [])
    by_id = {str(r.get("id")): r for r in rows if isinstance(r, dict)}
    for it in items:
        row = by_id[str(it["id"]).strip()]
        sheet = {k: " ".join(str(it[k]).split()) for k in ("plain", "risk")}
        sheet["verdict"] = it["verdict"]
        for opt in ("why", "counter_note"):
            if isinstance(it.get(opt), str) and it[opt].strip():
                sheet[opt] = " ".join(it[opt].split())
        if it.get("affects"):
            sheet["affects"] = list(it["affects"])
        row["release"] = {"status": "explained", "explained_by": " ".join(it["explained_by"].split()),
                          "explained_at": date, "sheet": sheet}
        tally["rule_explained"].append(str(row["id"]))
    write_json(mem, "learnings/learnings.json", rows, dry, touched, via="learnings")
    refresh_queues(rows, tally)


def apply_rule_releases(mem, plan, date, dry, touched, tally):
    items = plan.get("rule_releases") or []
    if not items:
        return
    rows = load_json(mem, "learnings/learnings.json", [])
    by_id = {str(r.get("id")): r for r in rows if isinstance(r, dict)}
    for it in items:
        row = by_id[str(it["id"]).strip()]
        status = it.get("status", "released")
        before = row.get("bridge_status")
        rel = dict(row.get("release") or {})
        if status == "released":
            rel.update({"status": "released", "released_by": "owner", "released_at": date,
                        "two_sentences": " ".join(it["two_sentences"].split())})
            row["bridge_status"] = "approved"
            row["bridge_note"] = f"{date} owner: Regel-Freigabe nach Zwei-Saetze-Erklaerung"
            tally["bridge_approved"].append(str(row["id"]))
        else:
            rel.update({"status": "rejected", "rejected_by": "owner", "rejected_at": date})
            row["bridge_status"] = "rejected"
            note = " ".join(str(it.get("note") or "").split()) or "keine Angabe"
            row["bridge_note"] = f"{date} owner: {note}"
            tally["bridge_rejected"].append(str(row["id"]))
        row["release"] = rel
        if before != row["bridge_status"]:
            tally["bridge_status_changed"] += 1
        tally["rule_released" if status == "released" else "rule_rejected"].append(str(row["id"]))
    write_json(mem, "learnings/learnings.json", rows, dry, touched, via="learnings")
    refresh_queues(rows, tally)


# A member id is local (Lnn of THIS store) and must exist as a live learning. A
# qualified one ("dome-dynamics/L31", "native:<slug>/<file>:<sha8>") is evidence
# from elsewhere: there is no cross-repo lookup, so its rule_class / release state
# cannot be checked and the pointer would carry the rule past the gate. It is
# refused until such a lookup exists (DCO #10062 (1)).
_LOCAL_MEMBER = re.compile(r"^[A-Za-z-]*\d+$")


def _anchor_key(anchor):
    path, frag = split_anchor(anchor)
    return os.path.normcase(os.path.abspath(path)), frag.strip().lower()


def validate_principles(mem, plan):
    """Pointer shape, before the first byte (D-021 (4)): the rule's wording lives at
    `anchor`, so an anchor that does not resolve TODAY is refused."""
    items = plan.get("principles") or []
    if not items:
        return
    live = {str(r.get("id")): r for r in load_json(mem, "learnings/learnings.json", [])
            if isinstance(r, dict)}
    existing = {_anchor_key(r.get("anchor")): list(r.get("derived_from") or []) for r in live.values()
                if r.get("kind") == "principle" and not r.get("superseded_by")}
    for it in items:
        if not isinstance(it, dict):
            raise PlanError("principle entry is not an object")
        if not str(it.get("summary") or "").strip():
            raise PlanError("principle without 'summary'")
        clean_plain(it.get("plain"), f"principle {str(it.get('anchor') or '')[:60]!r}")  # D11: shown on the card
        anchor = str(it.get("anchor") or "").strip()
        if not anchor:
            raise PlanError("principle without 'anchor'")
        if not anchor_ok(anchor):
            raise PlanError(f"principle anchor does not resolve: {anchor}")
        members = it.get("members")
        if not isinstance(members, list) or not members or not all(isinstance(m, str) for m in members):
            raise PlanError(f"principle '{anchor}' needs a non-empty list of member ids")
        if len(members) > DERIVED_MAX:
            raise PlanError(f"principle '{anchor}' has {len(members)} members (max {DERIVED_MAX})")
        for m in members:
            if not _LOCAL_MEMBER.match(m):
                raise PlanError(
                    f"principle member {m!r} is a qualified id (other store or native note) - the rule_class "
                    f"gate cannot be checked without a cross-repo lookup, so only local Lnn ids of this store "
                    f"are accepted as members; keep the cross-store evidence in the summary/anchor text instead")
            if m not in live:
                raise PlanError(f"principle member {m} is not a live learning of this store")
            if live[m].get("kind") == "principle":
                raise PlanError(f"principle member {m} is itself a pointer")
            if live[m].get("superseded_by"):
                raise PlanError(f"principle member {m} is superseded - point at its successor")
            if is_rule_class(live[m]) and (live[m].get("release") or {}).get("status") != "released":
                # the pointer is approved the moment it is written - it would carry the
                # rule past the gate (DCO #10057 (1)); the member needs the same gate first
                raise PlanError(
                    f"principle member {m} is a rule-class learning (release state "
                    f"{(live[m].get('release') or {}).get('status', 'needs_explanation')!r}) - it needs the "
                    f"gate first ('rule_explanations', then the owner's 'rule_releases')")
        # The merge with an existing pointer is bounded HERE, before any applier
        # writes - not in apply_principles after the iteration log (Codex round 2).
        key = _anchor_key(anchor)
        merged = list(dict.fromkeys(existing.get(key, []) + members))
        if len(merged) > DERIVED_MAX:
            raise PlanError(f"principle '{anchor}' would hold {len(merged)} members (max {DERIVED_MAX})")
        existing[key] = merged


def apply_principles(mem, plan, date, dry, touched, tally):
    """Plan section `principles` (5.4.0, D-021 (4)): one pointer row per rule.

    The pointer is a learning with kind "principle": summary (the rule, <= 150
    chars), anchor (where the wording lives), derived_from (the members, which
    stay byte-identical), importance = max of the local members, no
    review_after. The same anchor again merges members into the existing
    pointer instead of adding a row. Written only after the owner's gate
    (/agentic-os:maintain Step 5c) - wrap-up never emits this section.
    """
    items = plan.get("principles") or []
    if not items:
        return
    rows = load_json(mem, "learnings/learnings.json", [])
    archive = archived_rows(mem, "learnings/learnings.json")
    by_id = {str(r.get("id")): r for r in rows if isinstance(r, dict)}
    pointers = {_anchor_key(r.get("anchor")): r for r in rows
                if isinstance(r, dict) and r.get("kind") == "principle" and not r.get("superseded_by")}
    added, changed = [], 0
    for it in items:
        members = list(dict.fromkeys(it["members"]))
        summary = " ".join(str(it["summary"]).split())
        key = _anchor_key(it["anchor"])
        ptr = pointers.get(key)
        if ptr is not None:
            merged = list(dict.fromkeys(list(ptr.get("derived_from") or []) + members))
            if len(merged) > DERIVED_MAX:
                raise PlanError(f"pointer {ptr.get('id')} would hold {len(merged)} members (max {DERIVED_MAX})")
            ptr["derived_from"], ptr["summary"] = merged, summary
            ptr["plain"] = clean_plain(it.get("plain"), f"principle {it['anchor']!r}")
            ptr["text"] = f"{summary} → {it['anchor']}"
            ptr["last_relevant"] = date
            members = merged
        else:
            ptr = {"id": next_id(rows + added, "L", reserved=archive), "date": date, "kind": "principle",
                   "text": f"{summary} → {it['anchor']}", "summary": summary,
                   "anchor": str(it["anchor"]).strip(), "importance": 3,
                   "plain": clean_plain(it.get("plain"), f"principle {it['anchor']!r}"),
                   "tags": list(it.get("tags") or []), "layer": "long-term", "superseded_by": None,
                   "last_relevant": date, "derived_from": members, "bridge_status": "approved"}
            added.append(ptr)
            pointers[key] = ptr
        imps = [by_id[m].get("importance") for m in members if m in by_id]
        imps = [i for i in imps if isinstance(i, int) and not isinstance(i, bool) and 1 <= i <= 5]
        ptr["importance"] = max(imps) if imps else 3
        if len(summary) > SUMMARY_MAX:
            tally["warnings"].append(f"{ptr['id']}: summary {len(summary)} Zeichen > {SUMMARY_MAX}")
        if ptr not in added:
            changed += 1
    rows.extend(added)
    write_json(mem, "learnings/learnings.json", rows, dry, touched, via="learnings")
    render_learnings_md(mem, rows, dry, touched)
    tally["principles_added"] = len(added)
    tally["principles_updated"] = changed
    tally["principle_ids"] = [str(p["id"]) for p in added]


# Stamps maintain put on archived rows; they describe the archive, not the entry.
ARCHIVE_STAMPS = ("archived_at", "archived_by", "archive_reason", "archived")


def apply_restore(mem, plan, date, dry, touched, tally):
    """Plan section `restore` (5.3.0, D-021): archived rows come back through this
    one writer, input from scripts/restore_plan.py.

    Decided here, at apply time, against the FRESH store (the plan may be days
    old): same id + same text -> skip, so a second run changes nothing; same
    text under another id -> skip; id taken by a different text -> next free id
    plus `legacy:<id>`. A free archive id is KEPT, so old citations stay valid.
    A row that fails validation is dropped and reported - never the whole run.
    Archive files are only read (reserved ids), never written.
    """
    spec = plan.get("restore") or {}
    items = spec.get("learnings") or []
    if not items:
        return
    source = str(spec.get("source") or "restore")
    rows = load_json(mem, "learnings/learnings.json", [])
    archive = archived_rows(mem, "learnings/learnings.json")
    by_id = {str(r.get("id")): r for r in rows if isinstance(r, dict)}
    norms = {norm(r.get("text")) for r in rows if isinstance(r, dict)}
    accepted, dropped, skipped, renumbered = [], [], 0, {}
    collided = set()  # archive ids that a DIFFERENT live entry holds today
    for raw in items:
        if not isinstance(raw, dict):
            dropped.append({"id": None, "errors": ["row is not an object"]})
            continue
        row = {k: v for k, v in raw.items() if k not in ARCHIVE_STAMPS}
        rid = str(row.get("id") or "")
        text_norm = norm(row.get("text"))
        live = by_id.get(rid)
        if (live is not None and norm(live.get("text")) == text_norm) or text_norm in norms:
            skipped += 1
            continue
        der = row.get("derived_from", [])
        if isinstance(der, list):
            der = list(der)
            if not any(str(d).startswith("restored:") for d in der):
                der.append(f"restored:{source}")
            if live is not None:
                der.append(f"legacy:{rid}")
        row["derived_from"] = der
        if live is not None:
            collided.add(rid)
        errs = field_errors(row) + ([] if rid else ["missing id"])
        if errs:
            dropped.append({"id": rid or None, "errors": errs})
            continue
        if live is not None or rid in {str(r["id"]) for r in accepted}:
            new_id = next_id(rows + accepted, "L", reserved=archive)
            renumbered[rid] = new_id
            row["id"] = new_id
        accepted.append(row)
        norms.add(text_norm)
    # superseded_by is checked against what is ACTUALLY written (Codex 5.3.0): a
    # successor that was dropped drops its predecessor too, and a successor that
    # was renumbered is followed - in the archive, its id meant the archived row.
    changed = True
    while changed:
        changed = False
        final = {**by_id, **{str(r["id"]): r for r in accepted}}
        for row in list(accepted):
            sup = row.get("superseded_by")
            if sup and str(sup) in renumbered and not str(sup).startswith(SUPERSEDE_PREFIXES):
                row["superseded_by"] = sup = renumbered[str(sup)]
            if sup and str(sup) in collided and str(sup) not in renumbered:
                # the archived successor was dropped; the live entry under that id is
                # ANOTHER entry and must not inherit the pointer (Codex round 2)
                errs = [f"superseded_by {sup}: the archived successor was dropped and the live "
                        f"{sup} is a different entry"]
            else:
                errs = supersede_errors(row, final)
            if errs:
                accepted.remove(row)
                dropped.append({"id": str(row["id"]), "errors": errs})
                changed = True
                break
    added = accepted
    tally["restored"] = len(added)
    tally["restored_ids"] = [str(r["id"]) for r in added]
    tally["restore_skipped_live"] = skipped
    tally["restore_dropped"] = dropped
    if added:
        if not dry:
            store_snapshot.take(mem)  # bulk mutation: byte copy of the store first
        rows.extend(added)
        write_json(mem, "learnings/learnings.json", rows, dry, touched, via="learnings")
        render_learnings_md(mem, rows, dry, touched)


def render_importance(value) -> int:
    """Bucket for learnings.md. Legacy rows hold None or a 0..1 float (23 rows in
    four stores); int(None) crashed the render and int(0.7) == 0 hid the row."""
    if isinstance(value, bool):
        return 3
    if isinstance(value, float) and 0 < value <= 1:
        return max(1, min(5, int(value * 5 + 0.5)))
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 3


LEARNINGS_MD_HEADER = "*Auto-generated from learnings.json — do not edit directly.*"


def render_learnings_md(mem, rows, dry, touched):
    """learnings.md is a pure projection of learnings.json - fully deterministic.
    The header is the marker /agentic-os:maintain Step 5.2 and memory-audit check."""
    out = ["# Learnings", "", LEARNINGS_MD_HEADER, ""]
    for imp in (5, 4, 3, 2, 1):
        bucket = [r for r in rows if render_importance(r.get("importance", 3)) == imp and not r.get("superseded_by")]
        if not bucket:
            continue
        out.append(f"## Importance {imp}")
        out.append("")
        for r in sorted(bucket, key=lambda x: str(x.get("date", "")), reverse=True):
            tags = " ".join(f"#{t}" for t in (r.get("tags") or []))
            out.append(f"- **{r.get('id')}** ({r.get('date')}) {r.get('text')}" + (f"  {tags}" if tags else ""))
        out.append("")
    write_atomic(mem, "learnings/learnings.md", "\n".join(out).rstrip() + "\n", dry, touched)


# P1/B2: what a plan may set on a candidate directly. `confirmed` is not on the list - it is
# earned (a verified user quote, or the second mention from ANOTHER session) and never declared.
PLAN_CANDIDATE_STATUSES = ("observed", "inferred")


def _clean_quote(value) -> str:
    """The quote as stored and looked up: one line, whitespace collapsed. Empty = no quote."""
    return " ".join(value.split()) if isinstance(value, str) else ""


def _verify_identity_quotes(quotes, session_id, transcript, tally):
    """Verdicts for quotes the plan relies on (candidates and supersede alike): {quote: True | False | "kein_transkript"}
    and {quote: reason}. Fills the tally fields both appliers share: the transcript used, the warnings, a refused
    --transcript override (D3) and a session mismatch (D2)."""
    verdicts, info = identity_quote.verify_quotes(quotes, session_id, transcript)
    if info["transcript"]:
        tally["quote_transcript"] = info["transcript"]
    for w in info["warnings"]:
        if w not in tally["warnings"]:
            tally["warnings"].append(w)
    if info.get("override_rejected"):
        tally["transcript_override_rejected"] = True
    if info.get("session_mismatch"):
        tally["session_binding_mismatch"] = True
    return verdicts, info["reasons"]


def validate_user_candidates(plan):
    """Shape of the user_candidates section, before the first byte. A candidate that CLAIMS
    `confirmed` is judged later (quote check) and downgraded, not rejected - but a field of the
    wrong type is a plan error: `confirmed` must be a JSON bool, `quote` a string."""
    cands = plan.get("user_candidates")
    if cands is None:
        return
    if not isinstance(cands, list):
        raise PlanError("user_candidates must be a list")
    for cand in cands:
        if not isinstance(cand, dict):
            raise PlanError("user candidate is not an object")
        if not (cand.get("key") or "").strip():
            raise PlanError("user candidate without 'key'")
        if "confirmed" in cand and not isinstance(cand["confirmed"], bool):
            raise PlanError(f"user candidate {cand['key']!r}: 'confirmed' must be a JSON bool")
        if cand.get("quote") is not None and not isinstance(cand["quote"], str):
            raise PlanError(f"user candidate {cand['key']!r}: 'quote' must be a string (the user's literal words)")
        if "confidence" in cand:
            try:
                float(cand["confidence"])
            except (TypeError, ValueError):
                raise PlanError(f"user candidate {cand['key']!r}: 'confidence' must be a number") from None


def _merge_plan_entries(items, date, verdicts):
    """One plan entry per key (D1): the entries of the same key in ONE plan are one mention, not several.
    Evidence is united in order, a claim to be confirmed holds if any entry makes it, and a VERIFIED quote wins
    over an unverified or missing one (else the first quote stays). Scalars come from the first entry."""
    first = items[0]
    evidence = []
    for it in items:
        for e in (it.get("evidence") or []):
            if e not in evidence:
                evidence.append(e)
    quotes = [q for q in (_clean_quote(it.get("quote")) for it in items) if q]
    quote = next((q for q in quotes if verdicts.get(q) is True), quotes[0] if quotes else "")
    wanted = next((it.get("status") for it in items if it.get("status") is not None), None)
    return {
        "observation": next(((it.get("observation") or "").strip() for it in items if (it.get("observation") or "").strip()), ""),
        "signal_type": first.get("signal_type", "preference"),
        "confidence": first.get("confidence", 0.5),
        "evidence": evidence or [f"session {date}"],
        "claims": any(bool(it.get("confirmed")) or it.get("status") == "confirmed" for it in items),
        "quote": quote,
        "statuses": [it.get("status") for it in items],
        "wanted": wanted,
    }


# Function words that carry no claim. A quote and a statement "share a content word" only through words that are
# not in this list (D5/D7 round 2); it is a lexical hint for the owner, not a proof of meaning.
_STOP_WORDS = frozenset("""
dass nicht kein keine keinen keinem keiner keines ohne immer wenn dann aber auch oder eine einen einer einem eines
sind sein wird werden wurde habe haben hatte bitte mach machen macht gerne gern mein meine meinen dies diese dieser
dieses diesem noch nach jetzt sehr mehr schon doch wieder will wollen soll sollen kann koennen muss muessen hier dort
alle allen alles etwas nichts ganz sowie damit dabei davon dafuer ueber fuer unter sich ihre ihnen dich mich unser
that this with have will from your what when they them then than there here been does just also only very more some
into over like make should would could about always never want need please okay
""".split())


_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _fold(text) -> str:
    """Word comparison form: NFC, case-folded, umlauts written out (a-umlaut = ae, o-umlaut = oe, u-umlaut = ue, sharp s = ss),
    so 'ausführliche' and 'ausfuehrliche' are one word (R2 round 3). Invisible characters left after NFC (format
    characters such as zero-width space and soft hyphen, combining marks such as a variation selector) go, so they
    cannot split a word inside it (rounds 5 and 6)."""
    text = unicodedata.normalize("NFC", str(text or ""))
    return "".join(ch for ch in text if unicodedata.category(ch) not in _INVISIBLE).casefold().translate(_UMLAUTS)


_INVISIBLE = ("Cf", "Mn")


def _spaced(text) -> str:
    """The text (NFC) with every invisible character read as a space."""
    nfc = unicodedata.normalize("NFC", str(text or ""))
    return "".join(" " if unicodedata.category(ch) in _INVISIBLE else ch for ch in nfc)


def _tokens(text):
    """Every word of 2+ characters in comparison form. An invisible character is read once as nothing and once as a
    space, so neither 'hal<ZWSP>ten' nor 'Antworten<ZWSP>kurz<ZWSP>halten' hides a word (round 6)."""
    return {w for t in (text, _spaced(text)) for w in re.findall(r"[^\W_]{2,}", _fold(t))}


def _content_words(text):
    return [w for w in re.findall(r"[^\W_]{4,}", _fold(text)) if w not in _STOP_WORDS and not w.isdigit()]


def _words_related(a, b) -> bool:
    """Same word, or the same stem: a common prefix of at least 4 characters that covers 80% of the shorter word
    (kurz/kurzen, Antwort/Antworten, ausfuehrlich/ausfuehrliche)."""
    if a == b:
        return True
    p = len(os.path.commonprefix([a, b]))
    return p >= 4 and p >= 0.8 * min(len(a), len(b))


def _quote_related(quote, text) -> bool:
    """True when the quote and the statement it is meant to support share at least one content word (D5/D7 round 2).
    A statement without any content word gives nothing to compare and counts as related. The check is a lexical
    hint: it does not change a verdict, it makes a quote that has nothing to do with the claim visible."""
    target = _content_words(_CITATION_SUFFIX.sub("", str(text or "")))
    if not target:
        return True
    return any(_words_related(q, t) for q in _content_words(quote) for t in target)


def _polarity_differs(quote, text) -> bool:
    """True when one of the two carries a negation word and the other none ("keine langen Antworten" cannot support
    "Mag lange Antworten", nor the reverse; R3 round 3). Like _quote_related a lexical hint: it does not change a
    verdict, it makes a quote that says the opposite of the statement visible."""
    return identity_quote.has_negation(quote) != identity_quote.has_negation(_CITATION_SUFFIX.sub("", str(text or "")))


def apply_user_candidates(mem, plan, date, dry, touched, tally, session_id="", transcript=None, paths=None):
    """Step 6.2 enqueue + 6.3 FULL queue re-review. Both rules are deterministic.

    P1/B2 - `confirmed` is earned, never declared. A candidate that claims it (`confirmed: true`
    or `status: "confirmed"`) is confirmed at first sight only when its `quote` is a substring
    of a genuine user message in the session transcript (scripts/identity_quote.py). Otherwise it stays
    observed / inferred and the old rule applies: the second mention confirms. The verdict is stored
    on the candidate (`quote_verified`: true | false | "kein_transkript") and measured in the tally.
    No transcript to check against is not an error, it just never confirms at first sight.

    D1 - the second mention counts only from ANOTHER session: the candidate carries `sessions` (the ids of the
    sessions that named it) and `occurrences` rises only when the current session id is not among them. The same
    key twice in one plan is one mention; a rerun in the same session adds nothing; an empty session id counts
    nothing (a repetition cannot be proven). A legacy row without `sessions` keeps its `occurrences` and the
    first new session adds one.
    D2 - if CLAUDE_CODE_SESSION_ID names another session than the one given, the identity evidence of this run
    is not verifiable: no quote verifies and nothing is counted.
    """
    obs = plan.get("user_candidates") or []
    paths = _id_paths(mem, paths)
    queue = load_json(paths.root, paths.queue, [])
    # A run that died after the changelog (written FIRST) and before the queue left its candidate id only in the
    # audit log and the profile; re-issuing it would make another statement a 'duplicate' of that line (Codex P2-A-01)
    taken = [{"id": e["candidate_id"]} for e in load_json(paths.root, paths.changelog, [])
             if isinstance(e, dict) and isinstance(e.get("candidate_id"), str)]
    by_key = {c.get("key"): c for c in queue}
    sid = str(session_id or "").strip()
    binding = identity_quote.session_binding(sid)
    can_count = bool(sid) and not binding["mismatch"]
    if obs and not can_count:
        if binding["mismatch"]:
            tally["session_binding_mismatch"] = True
            msg = identity_quote.mismatch_warning(binding)
        else:
            msg = "user candidates: no session id - repeats are not counted (a repetition cannot be proven)"
        if msg not in tally["warnings"]:
            tally["warnings"].append(msg)

    # Trust boundary first: a foreign-source candidate is dropped before anything is checked or merged for it.
    grouped = {}
    for o in obs:
        key = (o.get("key") or "").strip()
        if not key:
            raise PlanError("user candidate without 'key'")
        if o.get("trust_source", "conversation") != "conversation":
            # Step 6.1 trust boundary - memory-poisoning defense.
            tally["candidates_rejected_trust"] += 1
            continue
        if key in grouped:
            tally["plan_duplicates_merged"] += 1
            tally["warnings"].append(f"candidate {key!r} appears more than once in the plan - merged, counted as one mention")
        grouped.setdefault(key, []).append(o)

    quotes = [q for items in grouped.values() for q in (_clean_quote(it.get("quote")) for it in items) if q]
    verdicts, reasons = {}, {}
    if quotes:
        verdicts, reasons = _verify_identity_quotes(quotes, sid, transcript, tally)

    for key, items in grouped.items():
        eff = _merge_plan_entries(items, date, verdicts)
        quote = eff["quote"]
        verdict = verdicts.get(quote) if quote else None
        if quote:
            tally["quotes_verified" if verdict is True else
                  "quotes_no_transcript" if verdict == identity_quote.NO_TRANSCRIPT else "quotes_unverified"] += 1
            if verdict is not True and reasons.get(quote):
                tally["quote_reasons"][key] = reasons[quote]
            # R1 round 3: the relation is checked against the text that is STORED and later promoted - for a known
            # key that is the queue row's observation, not what this plan happens to carry.
            stored_obs = (by_key[key].get("observation") if key in by_key else eff["observation"]) or ""
            if verdict is True and stored_obs and not _quote_related(quote, stored_obs):
                tally["quote_unrelated"].append(key)
                tally["warnings"].append(f"candidate {key!r}: the verified quote shares no content word with the "
                                         f"observation - check that it really supports it")
            if verdict is True and stored_obs and _polarity_differs(quote, stored_obs):
                tally["quote_polarity"].append(key)  # calibration data only, no warning (noise)
        claims = eff["claims"]
        earned = claims and verdict is True
        wanted = eff["wanted"]
        for st in eff["statuses"]:
            if st not in (None, "confirmed") + PLAN_CANDIDATE_STATUSES:
                tally["warnings"].append(f"candidate {key!r}: status {st!r} is not allowed in a plan - recorded as observed")
        if key in by_key:
            c = by_key[key]
            sessions = c.get("sessions") if isinstance(c.get("sessions"), list) else []
            counted = False
            if can_count and "sessions" in c and not sessions:
                # D1 round 2: the row was born without a provable session (empty id, blank id, session mismatch).
                # The first real id is taken in WITHOUT counting - the earlier run may be this very session, and
                # a repetition cannot be proven. Only a row without the key (legacy) follows the plain D1 rule.
                c["sessions"] = [sid]
                tally["session_adopted"] += 1
                tally["warnings"].append(f"candidate {key!r}: created without a provable session - session {sid} taken in, "
                                         f"the mention is not counted as a repeat")
            elif can_count and sid not in sessions:
                c["occurrences"] = int(c.get("occurrences", 1)) + 1
                c["sessions"] = sessions + [sid]
                counted = True
            else:
                tally["repeat_not_counted"] += 1  # same session again, no session id, or a session mismatch
            c["last_seen"] = date
            ev = c.setdefault("evidence", [])
            for e in eff["evidence"]:
                if e not in ev:
                    ev.append(e)
            if quote and (verdict is True or c.get("quote_verified") is not True):
                c["quote"], c["quote_verified"] = quote, verdict
            if c.get("status") in PLAN_CANDIDATE_STATUSES and (earned or (counted and int(c["occurrences"]) >= 2)):
                c["status"] = "confirmed"
                c["confirmed_by"] = "quote" if earned else "repeat"
            elif earned and c.get("status") == "confirmed":
                c["confirmed_by"] = "quote"  # D9: the user's verified words lift a row confirmed by repetition to the top proof
            tally["candidates_updated"] += 1
        else:
            c = {
                "id": next_id(queue, paths.id_prefix, reserved=taken),
                "key": key,
                "observation": eff["observation"],
                "status": "confirmed" if earned else (wanted if wanted in PLAN_CANDIDATE_STATUSES else "observed"),
                "signal_type": eff["signal_type"],
                "confidence": float(eff["confidence"]),
                "occurrences": 1,
                "sessions": [sid] if can_count else [],
                "evidence": list(eff["evidence"]),
                "first_seen": date,
                "last_seen": date,
                "trust_source": "conversation",
            }
            if quote:
                c["quote"], c["quote_verified"] = quote, verdict
            if earned:
                c["confirmed_by"] = "quote"
            if paths.central:
                c["origin"] = _origin(mem)
            queue.append(c)
            by_key[key] = c
            tally["candidates_new"] += 1
        if claims and c.get("status") in PLAN_CANDIDATE_STATUSES:
            # the claim was not honoured: no quote, not found, or nothing to check it against
            tally["confirm_downgraded"] += 1
            tally["confirm_downgraded_keys"].append(key)

    marks = [c.get("blocked_by_supersede") for c in queue]
    promoted = promote_candidates(mem, queue, date, dry, touched, tally, paths=paths)
    if obs or promoted or marks != [c.get("blocked_by_supersede") for c in queue]:  # a new block note must reach the file
        write_json(paths.root, paths.queue, queue, dry, touched, via="identity")
    tally["queue_open"] = sum(1 for c in queue if c.get("status") != "promoted")


def _norm_tokens(text):
    return set(re.sub(r"[^a-z0-9\s]", " ", str(text or "").lower()).split())


def _jaccard(a, b):
    ta, tb = _norm_tokens(a), _norm_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


_CITATION_SUFFIX = re.compile(r"\s*[\[(]\s*(?:UC|PC)\d+\s*,[^\])]*[\])]\s*$")


def _already_in_user_md(cand, user_md):
    """Return the user.md line a candidate duplicates, else None. Duplicate =
    the candidate id is already cited, or the observation nearly repeats an
    existing line (Jaccard >= 0.8 after stripping the '(UCn, date)' citation).
    2026-07-27: UC1-UC3 were re-promoted as plain duplicates. Limitation: a
    negated restatement ('niemals') still scores as a duplicate - that is why
    every skip is written to the changelog instead of vanishing."""
    cid = str(cand.get("id", ""))
    obs = cand.get("observation", "")
    for lines in user_md.values():
        for line in lines:
            if cid and re.search(r"[\[(]\s*" + re.escape(cid) + r"\s*[,\])]", line):
                return line
            if _jaccard(obs, _CITATION_SUFFIX.sub("", line)) >= 0.8:
                return line
    return None


# D9: a statement the owner retracted through user_md_supersede must not come back on a lower proof. The word measure does
# not judge the MEANING (every such heuristic has a gap) - it only decides the PROOF LEVEL: a candidate that touches a
# retracted statement is promoted only when it is confirmed by a verified quote (confirmed_by == "quote", the user's own
# words); a repetition or an inference does not bring it back. "Touches" is measured generously, a false alarm costs one
# quote: the retracted line is read twice - without the citation "(UC3, 2026-07-01)" at its END (every other bracket
# stays content) and without every unnested bracket group at its end ("(Owner, 2026-07-01)", "(seit Juli)") - and it is
# touched when one reading is. From RETRACT_MIN_TOKENS content words (4+ letters, no function words, umlauts written
# out) on, at least RETRACT_MIN of them stand in the observation; a line of 1-2 content words needs all of them (a
# function word of the line may be missing, round 5), a line without one ("Mag TDD") all its tokens of 2+ characters.
# "Stand in" = an equal or same-stem word among ALL words of the observation, function words included ("noch einmal"
# stands for "nochmal", round 6). Known limits: a synonym or a paraphrase that keeps under half of the words ("kurz" ->
# "knapp" in a longer sentence), a candidate that names only part of the line ("Will keine Emojis" against "Will keine
# Emojis in Commits"), and deliberately disguised wording (look-alike letters) are not touched.
RETRACT_MIN = 0.5
RETRACT_MIN_TOKENS = 3


def _share(words, pool) -> float:
    """Share of `words` that stand in `pool` (the same word or the same stem)."""
    return sum(1 for w in words if any(_words_related(w, p) for p in pool)) / len(words)


_CITED_ID = re.compile(r"[\[(]\s*([A-Za-z]{1,6}\d{1,6})\s*[,\])]")
# The citation "(UC3, 2026-07-01)" at the END of the line (optionally followed by further notes): a bracket in the
# middle of the line ("Nutzt Python (py3, uv) ...") is no citation (Pruefer Runde 3, N6).
_CITED_AT_END = re.compile(r"[\[(]\s*([A-Za-z]{1,6}\d{1,6})\s*,[^\[\]()]*[\])]\s*(?:[\[(][^\[\]()]*[\])]\s*)*$")


def retracted_statements(changelog):
    """Every user.md supersede in the audit log, as {old, line, readings}: statements the owner has contradicted or
    refined, which must not come back through a fresh candidate (D9). `line` = the id of the replaced line (the entry's
    candidate_id, else the id the old line cited) or None; `readings` = the two readings of the old line the measure
    compares, taken from the line as written and from the line with its invisible characters read as spaces, each also
    with its invisible characters read as spaces (round 7; computed once per entry, not per candidate)."""
    out = []
    for e in changelog:
        if isinstance(e, dict) and e.get("field") == "user.md/supersede" and e.get("old_value"):
            old = str(e["old_value"])
            cited = _CITED_AT_END.search(old) or _CITED_ID.search(old)
            # the notes come off the line as written AND with its invisible characters read as spaces: a zero-width
            # character behind the closing bracket would otherwise keep the note in (Codex-Verifier 2026-10-28, P1-B2-01)
            readings = tuple(rx.sub("", s) for s in (old, _spaced(old)) for rx in (_CITED_AT_END, _NOTES_AT_END))
            out.append({"old": old, "line": e.get("candidate_id") or (cited.group(1) if cited else None),
                        "readings": tuple(dict.fromkeys(readings + tuple(_spaced(r) for r in readings)))})
    return out


_NOTES_AT_END = re.compile(r"(?:\s*[\[(][^\[\]()]*[\])])+[\s.,;:!?]*$")


def _touches_line(pool, line) -> bool:
    """Does the observation whose words are `pool` (_tokens) touch this reading of a retracted line?"""
    words = set(_content_words(line))
    if words:
        return _share(words, pool) >= (RETRACT_MIN if len(words) >= RETRACT_MIN_TOKENS else 1)
    words = set(re.findall(r"[^\W_]{2,}", _fold(line)))
    return bool(words) and _share(words, pool) == 1


def promote_candidates(mem, queue, date, dry, touched, tally, paths=None):
    """Step 6.3: review EVERY candidate, not just this session's."""
    promotable = []
    for c in queue:
        if c.get("status") == "promoted":
            continue
        if c.get("signal_type") == "mood":
            continue  # signal:mood is NEVER promoted
        if c.get("trust_source") != "conversation":
            # The enqueue path rejects foreign sources, but this full-queue
            # re-review also sees rows written by older versions, other code
            # paths, or a hand-edited file. Without this check a poisoned row
            # already sitting in the queue would be laundered into user.md -
            # exactly what the Step 6.1 boundary exists to prevent.
            tally["promotion_blocked_trust"] += 1
            continue
        status = c.get("status")
        occ = int(c.get("occurrences", 1))
        conf = float(c.get("confidence", 0.0))
        if status == "confirmed" or (status == "inferred" and occ >= 2 and conf >= 0.6):
            promotable.append(c)

    if not promotable:
        return []

    paths = _id_paths(mem, paths)
    changelog = load_json(paths.root, paths.changelog, [])
    user_md = read_user_md(mem, paths)
    retracted = retracted_statements(changelog)
    now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")

    promoted, additions, blocked, log_changed = [], [], [], False
    for c in promotable:
        dup = _already_in_user_md(c, user_md)
        if dup is not None:
            changelog.append({
                "ts": now,
                "field": "user.md/skipped-duplicate",
                "old_value": dup,
                "new_value": c.get("observation"),
                "candidate_id": c.get("id"),
                "evidence": c.get("evidence") or [],
                "reason": "duplicate_of_existing",
            })
            if paths.central:
                changelog[-1]["origin"] = c.get("origin") or _origin(mem)
            log_changed = True
            c["status"] = "promoted"
            c["status_after_promotion"] = "duplicate_of_existing"
            c.pop("blocked_by_supersede", None)
            tally["promotion_skipped_duplicate"] += 1
            continue
        # D9: a candidate that touches a statement the owner retracted needs the highest proof, his verified words.
        hit = None
        if c.get("confirmed_by") != "quote":
            pool = _tokens(c.get("observation", ""))
            hit = next((r for r in retracted if any(_touches_line(pool, ln) for ln in r["readings"])), None)
        if hit is not None:
            # There is no rejected status in the candidate schema: the status stays, it is just not promoted.
            # The block is noted on the candidate (blocked_by_supersede = the id of the replaced line) and warned about
            # once - the same candidate would otherwise be reported again by every wrap-up.
            tally["promotion_blocked_superseded"] += 1
            line = hit["line"] or "unknown"
            if c.get("blocked_by_supersede") != line:
                c["blocked_by_supersede"] = line
                tally["warnings"].append(f"candidate {c.get('id')} ({c.get('key')}) touches a statement a user.md supersede "
                                         f"retracted (line {line}) - not promoted: it needs the verified words of the user "
                                         f"(confirmed with a verified quote), a repetition is not enough (wrap-up 6.3)")
            blocked.append(c)
            continue
        c.pop("blocked_by_supersede", None)
        promoted.append(c)
        section = SECTION_BY_SIGNAL.get(c.get("signal_type", "preference"), "Preferences")
        line = f"- {c.get('observation')} ({c.get('id')}, {date})"
        # changelog BEFORE edit - ordering is part of the contract
        entry = {
            "ts": now,
            "field": f"user.md/{section}",
            "old_value": None,
            "new_value": c.get("observation"),
            "candidate_id": c.get("id"),
            "evidence": c.get("evidence") or [],
        }
        if c.get("quote"):  # P1/B2: the audit trail names the words the confirmation rests on
            entry["quote"], entry["quote_verified"] = c["quote"], c.get("quote_verified")
        if c.get("confirmed_by"):
            entry["confirmed_by"] = c["confirmed_by"]
        if paths.central:
            entry["origin"] = c.get("origin") or _origin(mem)
        changelog.append(entry)
        log_changed = True
        additions.append((section, line))
        c["status"] = "promoted"
        c["status_after_promotion"] = "promoted"

    if log_changed:
        write_json(paths.root, paths.changelog, changelog, dry, touched, via="identity")
    if additions:
        insert_user_md_lines(mem, additions, dry, touched, paths)
    tally["candidates_promoted"] = len(promoted)
    tally["promoted_ids"] = [c.get("id") for c in promoted]
    return [c for c in promotable if c not in blocked]


def read_user_md(mem, paths=None):
    """Parse user.md into {section: [lines]} preserving unknown sections."""
    paths = _id_paths(mem, paths)
    path = os.path.join(paths.root, paths.md)
    sections, current = {}, None
    if os.path.exists(path):
        for raw in open(path, encoding="utf-8-sig"):  # a BOM may stand right before the first '## '
            line = raw.rstrip("\n")
            if line.startswith("## "):
                current = line[3:].strip()
                sections.setdefault(current, [])
            elif line.startswith("# "):
                current = None
            elif current is not None and line.strip():
                sections[current].append(line)
    return sections


def write_user_md(mem, sections, dry, touched, paths=None):
    """The CANONICAL form, for a user.md that does not exist yet. An existing file is never rewritten this way
    (see insert_user_md_lines): it would lose the BOM, CRLF, the preamble and the section order."""
    order = ["Preferences", "Work Style", "Known Corrections"]
    ordered = order + [s for s in sections if s not in order]
    out = ["# User Profile", ""]
    for s in ordered:
        if s not in sections:
            continue
        out.append(f"## {s}")
        out.append("")
        out.extend(sections[s] or [])
        out.append("")
    paths = _id_paths(mem, paths)
    write_atomic(paths.root, paths.md, "\n".join(out).rstrip() + "\n", dry, touched, via="identity")


def _is_heading(content: str) -> bool:
    c = content.lstrip(BOM)
    return c.startswith("## ") or c.startswith("# ")


def _dominant_eol(lines) -> str:
    crlf = sum(1 for _c, e in lines if e == "\r\n")
    lf = sum(1 for _c, e in lines if e == "\n")
    return "\r\n" if crlf > lf else "\n"


def insert_user_md_lines(mem, additions, dry, touched, paths=None):
    """Add promoted lines to identity/user.md WITHOUT rewriting the file (D8): each `(section, line)` goes after
    the last non-blank line of its `## section`; a missing section is appended at the end. Everything else stays
    byte for byte - the BOM, CRLF or LF, the preamble, the order of the sections, a last line without a line ending.
    A user.md that does not exist yet (or is blank) gets the canonical form."""
    paths = _id_paths(mem, paths)
    path = os.path.join(paths.root, paths.md)
    text = _read_user_md_raw(mem, "promotion", paths) if os.path.isfile(path) else ""
    if not text.strip():
        sections = {}
        for section, line in additions:
            bucket = sections.setdefault(section, [])
            if line not in bucket:
                bucket.append(line)
        write_user_md(mem, sections, dry, touched, paths)
        return
    lines = split_user_md(text)
    for section, line in additions:
        contents = [c for c, _e in lines]
        head = next((i for i, c in enumerate(contents)
                     if c.lstrip(BOM).startswith("## ") and c.lstrip(BOM)[3:].strip() == section), None)
        if head is None:
            eol = _dominant_eol(lines)
            if len(lines) > 1 and lines[-1] == ("", ""):
                lines.pop()  # the empty tail after the final line ending is not a line
            if lines and lines[-1][1] == "":
                lines[-1] = (lines[-1][0], eol)  # the last line gets an ending before something follows it
            if lines and lines[-1][0].strip():
                lines.append(("", eol))
            lines += [(f"## {section}", eol), ("", eol), (line, eol)]
            continue
        end = next((i for i in range(head + 1, len(contents)) if _is_heading(contents[i])), len(contents))
        if any(contents[i] == line for i in range(head + 1, end)):
            continue  # the very line is already in this section
        last = head
        for i in range(head + 1, end):
            if contents[i].strip():
                last = i
        content, eol = lines[last]
        if eol:
            lines.insert(last + 1, (line, eol))
        else:  # the last line of the file has no ending: it gets one, the new line takes over "no ending"
            lines[last] = (content, _dominant_eol(lines))
            lines.insert(last + 1, (line, ""))
    write_atomic(paths.root, paths.md, "".join(c + e for c, e in lines), dry, touched, via="identity")


def apply_soul_candidates(mem, plan, date, dry, touched, tally):
    items = plan.get("soul_candidates") or []
    if not items:
        return
    path = os.path.join(mem, "identity/soul-candidates.md")
    existing = open(path, encoding="utf-8").read() if os.path.exists(path) else "# Soul Candidates\n"
    block = []
    for it in items:
        proposal = (it.get("proposal") or "").strip()
        if not proposal:
            raise PlanError("soul candidate without 'proposal'")
        if it.get("trust_source", "conversation") != "conversation":
            # Step 6.1 trust boundary, now enforced here too (DCO #10148): a proposal for the agent's identity may
            # only come from the user's own conversation, never from a file or tool output (memory poisoning)
            tally["soul_rejected_trust"] += 1
            continue
        if norm(proposal) in norm(existing):
            tally["soul_skipped_duplicate"] += 1
            continue
        ev = "; ".join(it.get("evidence") or [])
        block.append(f"\n## {date} — {proposal}\n\n- **Evidence:** {ev or 'n/a'}\n")
        tally["soul_candidates_added"] += 1
    if block:
        # the init stub "*Keine offenen Kandidaten.*" is wrong as soon as one candidate stands below it (DCO #10148:
        # 6 stores, bootstrap reported "none open")
        existing = re.sub(r"(?m)^\*Keine offenen Kandidaten\.\*[ \t]*(?:\n|\Z)(?:[ \t]*\n)?", "", existing)
        write_atomic(mem, "identity/soul-candidates.md",
                     existing.rstrip() + "\n" + "".join(block), dry, touched)


def apply_open_tasks(mem, plan, date, dry, touched, tally):
    spec = plan.get("open_tasks") or {}
    add, close = spec.get("add") or [], set(spec.get("close") or [])
    if not add and not close:
        return
    rows = load_json(mem, "context/open-tasks.json", [])
    archive = archived_rows(mem, "context/open-tasks.json") if add else []
    # Snapshot BEFORE applying closes: a plan that closes T-n and re-adds the
    # same title in one pass is contradictory - treat it as a duplicate rather
    # than silently creating a second row. Re-opening a task closed in an
    # EARLIER run still works, because it is not in this snapshot.
    open_titles = {norm(r.get("title")) for r in rows if r.get("status") != "closed"}
    for r in rows:
        if r.get("id") in close and r.get("status") != "closed":
            r["status"] = "closed"
            r["updated"] = date
            r["resolution"] = "resolved in wrap-up"
            tally["tasks_closed"] += 1
    for t in add:
        title = (t.get("title") or "").strip()
        if not title:
            raise PlanError("open task without 'title'")
        if norm(title) in open_titles:
            tally["tasks_skipped_duplicate"] += 1
            continue
        open_titles.add(norm(title))
        if looks_mojibake(title):
            tally["warnings"].append(f"task title {title[:40]!r} looks like Mojibake (UTF-8 read as cp1252)")
        rows.append({
            "id": next_id(rows, "T-", pad=3, reserved=archive),
            "title": title,
            "status": "open",
            "created": date,
            "updated": date,
            "resolution": None,
            "source": t.get("source", "wrap-up"),
            "cross_project": bool(t.get("cross_project", False)),
        })
        tally["tasks_added"] += 1
    write_json(mem, "context/open-tasks.json", rows, dry, touched)


DEFAULT_CENTRAL_DIR = os.path.join(os.path.expanduser("~"), "AI")


def is_central_store(mem, central_dir):
    """True when this store's session-summary.md IS the central handoff (the AI workspace)."""
    try:
        own = os.path.normcase(os.path.realpath(os.path.join(mem, "session-summary.md")))
        central = os.path.normcase(os.path.realpath(os.path.join(central_dir, ".agent-memory", "session-summary.md")))
    except (OSError, ValueError):
        return False
    return own == central


def apply_session_summary(mem, plan, dry, touched, tally, central_dir=DEFAULT_CENTRAL_DIR):
    s = plan.get("session_summary")
    if not s:
        return
    if is_central_store(mem, central_dir):
        # DCO #9433: overwriting here dropped every other project's handoff block
        # (2026-09-24). The block arrives via the prepend path (Step 7.6a / write_central).
        tally["warnings"].append("session-summary.md not written: this store's summary is the central handoff "
                                 "(prepend via Step 7.6a / write_central instead)")
        return
    st = s.get("statistics") or {}
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = [
        "# Last Session", "",
        f"*Date: {now}*", "*Agent: Claude Code*", "",
        "## What Was Done", "",
    ]
    out += [f"- {x}" for x in (s.get("what_was_done") or [])[:10]] or ["- (nothing recorded)"]
    out += ["", "## Open Items", ""]
    out += [f"- {x}" for x in (s.get("open_items") or [])] or ["- none"]
    out += ["", "## Next Steps", ""]
    out += [f"{i}. {x}" for i, x in enumerate((s.get("next_steps") or [])[:3], 1)] or ["1. none"]
    out += ["", "## Statistics", "",
            f"- Iterations: {st.get('iterations', 0)} | Errors: {st.get('errors', 0)} "
            f"| New Patterns: {st.get('new_patterns', 0)}"]
    warn = s.get("warnings") or []
    if warn:
        out += ["", "## Active Warnings", ""] + [f"- {x}" for x in warn]
    if s.get("handoff"):
        h = s["handoff"]
        out += ["", "## Handoff Context", "",
                f"- **Active task**: {h.get('active_task', '')}",
                f"- **Current state**: {h.get('current_state', '')}",
                f"- **Active patterns**: {h.get('active_patterns', '')}",
                f"- **Open questions**: {h.get('open_questions', '')}"]
    body = "\n".join(out).rstrip() + "\n"
    n = len(body.splitlines())
    if n > SUMMARY_MAX_LINES and not s.get("handoff"):
        tally["warnings"].append(f"session-summary.md is {n} lines (contract: max {SUMMARY_MAX_LINES})")
    write_atomic(mem, "session-summary.md", body, dry, touched)
    tally["session_summary_lines"] = n


CONSOLIDATION_MODES = ("wrap-up", "headless")


def apply_consolidation(mem, plan, session_id, dry, touched, tally, by="wrap-up"):
    """Step 9.5 - runs LAST and only when everything above succeeded.

    `by` names who consolidated: "wrap-up" (the skill, with judge) or "headless"
    (wrapup_core's night run, no judge). Stamping "wrap-up" for both made a
    mechanically harvested session indistinguishable from a judged one
    (Lebenszyklus Phase 2).
    """
    if by not in CONSOLIDATION_MODES:
        raise PlanError(f"unknown consolidation mode {by!r} (allowed: {', '.join(CONSOLIDATION_MODES)})")
    if not plan.get("consolidate"):
        return
    work = os.path.join(mem, "working")
    dirty_files, sessions, seen_files = [], [], 0
    if os.path.isdir(work):
        for name in sorted(os.listdir(work)):
            if not (name.startswith("dirty-") and name.endswith(".json")):
                continue
            d = read_dirty_file(mem, name)  # strict: see read_dirty_file
            if not isinstance(d, dict) or not d.get("dirty"):
                continue
            dirty_files.append((name, d))
            sessions.append(d.get("session_id", name))
            seen_files += len(d.get("touched_files") or [])

    now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")

    # Dirty flags FIRST, marker LAST. The marker is the claim "everything below
    # is consolidated"; publishing it before the flags meant an IO failure while
    # resetting them left exit 2 WITH a marker - the exact inversion of the
    # Step 9.5 rule-5 contract.
    for name, d in dirty_files:
        d["dirty"] = False
        d["consolidated_at"] = now
        d["consolidated_by"] = by
        d["last_consolidated_at"] = now
        d["last_consolidated_by"] = by
        d["writes_since_consolidation"] = 0
        # D-024: a judged wrap-up harvests its OWN conversation only; the other dirty
        # sessions it consolidates stay pending until an E7 rule closes them.
        if by == "wrap-up" and session_id and d.get("session_id") == session_id:
            d["harvest"] = harvest_state.make_closure(d, "wrap-up", "wrap-up", now)
        write_json(mem, f"working/{name}", d, dry, touched)
    tally["dirty_files_consolidated"] = len(dirty_files)
    tally["touched_files_seen"] = seen_files

    write_json(mem, "consolidation-marker.json", {
        "last_wrapup": now,
        "mode": by,
        "consolidated_sessions": sessions or ([session_id] if session_id else []),
        # Measured first, plan-declared only as a fallback for runs whose
        # iterations were logged elsewhere (see verify-subagent-tallies).
        "iterations_logged": tally["iterations_logged"] or int(plan.get("iterations_logged", 0)),
        "learnings_added": tally["learnings_added"],
        "touched_files_seen": seen_files,
    }, dry, touched)


# -------------------------------------------------------------------- tally


def new_tally() -> dict:
    """The measured counters of one run, all at zero. One home for the field list: main() uses it and so can
    a test that calls an applier in-process."""
    return {
        "iterations_logged": 0, "iterations_skipped_duplicate": 0,
        "errors_added": 0, "errors_recurred": 0,
        "decisions_added": 0, "decisions_superseded": 0,
        "decisions_skipped_duplicate": 0, "decision_ids": [],
        "learnings_added": 0, "learnings_skipped_duplicate": 0, "learning_ids": [],
        "near_duplicates": {}, "bridge_candidates": [],
        "bridge_approved": [], "bridge_rejected": [], "bridge_status_changed": 0,
        "rule_class_open": None, "rule_explained": [], "rule_released": [], "rule_rejected": [],
        "tasks_retitled": 0, "learnings_corrected": 0, "corrections_skipped_unchanged": 0,
        "decisions_corrected": 0, "user_lines_corrected": 0,
        "correction_ids": [], "corrections_projection_needed": False,
        "restored": 0, "restored_ids": [], "restore_skipped_live": 0, "restore_dropped": [],
        "principles_added": 0, "principles_updated": 0, "principle_ids": [],
        "candidates_new": 0, "candidates_updated": 0, "candidates_promoted": 0,
        "candidates_rejected_trust": 0, "promotion_blocked_trust": 0,
        "promotion_skipped_duplicate": 0,
        "quotes_verified": 0, "quotes_unverified": 0, "quotes_no_transcript": 0, "quote_transcript": None,
        "confirm_downgraded": 0, "confirm_downgraded_keys": [],
        "user_lines_superseded": 0, "supersede_skipped_unchanged": 0, "supersede_skipped_legacy": 0,
        "identity_target": "project", "central_path": None,
        "promoted_ids": [], "queue_open": 0,
        "soul_candidates_added": 0, "soul_skipped_duplicate": 0, "soul_rejected_trust": 0,
        "tasks_added": 0, "tasks_closed": 0, "tasks_skipped_duplicate": 0,
        "session_summary_lines": 0, "dirty_files_consolidated": 0,
        "touched_files_seen": 0, "warnings": [],
        "quote_reasons": {}, "transcript_override_rejected": False, "session_binding_mismatch": False,
        "repeat_not_counted": 0, "session_adopted": 0, "plan_duplicates_merged": 0,
        "supersede_skipped_unverified": 0, "promotion_blocked_superseded": 0, "quote_unrelated": [], "quote_polarity": [],
    }


# -------------------------------------------------------------------- main

def main() -> int:
    # Windows default stdout is cp1252; the identity status line contains "→".
    # Without this the whole run dies AFTER the files were written.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass

    ap = argparse.ArgumentParser(description="Apply a wrap-up write plan in one pass.")
    ap.add_argument("mem", nargs="?", default=".agent-memory")
    ap.add_argument("--session-id", default=None)
    ap.add_argument("--plan", default="-", help="plan JSON file, '-' for stdin")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--render-learnings", action="store_true",
                    help="only regenerate learnings.md from learnings.json (no plan, no marker)")
    ap.add_argument("--propose-encoding-corrections", action="store_true",
                    help="print the corrections plan that repairs Mojibake in tasks, learnings, decisions and user.md lines (read-only)")
    ap.add_argument("--lint", action="store_true",
                    help="report live learning rows that break the write rules (read-only)")
    ap.add_argument("--lock-timeout", type=float, default=store_lock.TIMEOUT_SECONDS,
                    help="seconds to wait for working/store.lock")
    ap.add_argument("--transcript", default=None,
                    help="transcript file the quotes (user_candidates, user_md_supersede) are checked against. Accepted "
                         "only as <HOME>/.claude/projects/<dir>/<session-id>.jsonl, otherwise refused (default: the "
                         "session's own transcript there, resolved at call time; read-only)")
    ap.add_argument("--identity-store", default=None,
                    help="central identity store (P2); else $AGENTIC_OS_IDENTITY_STORE, else ~/.agent-memory. It is "
                         "used only when its identity/profile.md exists, otherwise the project files stay the target")
    ap.add_argument("--central-dir", default=DEFAULT_CENTRAL_DIR,
                    help="central handoff dir; a store whose summary IS <dir>/.agent-memory/session-summary.md "
                         "never overwrites it (DCO #9433)")
    args = ap.parse_args()

    if not os.path.isdir(args.mem):
        print(json.dumps({"ok": False, "error": f"memory dir not found: {args.mem}"}))
        return 1

    if args.lint:
        try:
            found = lint_learnings(args.mem)
        except PlanError as e:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            return 2
        print(json.dumps({"ok": True, "count": len(found), "lint": found}, ensure_ascii=False, indent=2))
        return 0

    if args.propose_encoding_corrections:
        try:
            print(json.dumps(propose_encoding_corrections(args.mem, args.identity_store), ensure_ascii=False, indent=2))
        except PlanError as e:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            return 2
        return 0

    if args.render_learnings:
        # Strict read: unlike load_json (missing -> default), a render-only run must
        # never project an empty store over a real one - a missing file is an error too.
        touched: list = []
        guard = (contextlib.nullcontext() if args.dry_run
                 else store_lock.store_lock(args.mem, timeout=args.lock_timeout))
        try:
            with guard:  # a write like any other (Codex 5.3.0)
                with open(os.path.join(args.mem, "learnings", "learnings.json"), encoding="utf-8-sig") as fh:
                    rows = json.load(fh)
                if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                    raise PlanError("learnings.json is not a JSON list of objects")
                render_learnings_md(args.mem, rows, args.dry_run, touched)
        except (PlanError, OSError, ValueError) as e:
            print(json.dumps({"ok": False, "error": f"render failed: {e}"}, ensure_ascii=False))
            return 2
        print(json.dumps({"ok": True, "dry_run": args.dry_run, "files_written": touched},
                         ensure_ascii=False))
        return 0

    try:
        raw = sys.stdin.read() if args.plan == "-" else open(args.plan, encoding="utf-8").read()
        plan = json.loads(raw)
        if not isinstance(plan, dict):
            raise PlanError("plan must be a JSON object")
    except (json.JSONDecodeError, OSError) as e:
        print(json.dumps({"ok": False, "error": f"unreadable plan: {e}"}))
        return 2

    date = plan.get("date") or _dt.date.today().isoformat()
    # the flag wins, an explicitly empty one too: it counts nothing, the plan's id does not step in (Codex round 2, R2-06)
    session_id = args.session_id if args.session_id is not None else plan.get("session_id", "")

    tally = new_tally()
    touched: list = []
    try:
        paths = identity_store.select_paths(args.mem, args.identity_store, os.environ)
    except identity_store.GuardError as e:
        if plan_touches_identity(plan):
            print(json.dumps({"ok": False, "error": f"plan rejected: {e}", "files_written": [],
                              "note": "consolidation marker NOT written - dirty state stays honest"}, ensure_ascii=False))
            return 2
        # a plan that writes no identity data (iterations, decisions, ...) runs; the guard only protects the profile
        paths = identity_store.legacy_paths(args.mem)
    if paths.central:
        tally["identity_target"], tally["central_path"] = "central", paths.root

    # One store lock from the first read to the last write (5.3.0): without it a
    # wrap-up and the nightly run (or extract_patterns) lose each other's rows.
    # A dry run only reads and takes no lock.
    guard = (contextlib.nullcontext() if args.dry_run
             else store_lock.store_lock(args.mem, timeout=args.lock_timeout))
    # P2: the central profile has its own lock, taken AFTER the project lock (never the other way round) and only
    # when this run writes identity data into it - an iterations-only plan never waits for another project.
    central_guard = (store_lock.store_lock(paths.root, timeout=args.lock_timeout, lock_rel=identity_store.PROFILE_LOCK)
                     if paths.central and not args.dry_run and plan_touches_identity(plan)
                     else contextlib.nullcontext())
    try:
        with guard, central_guard:
            preflight_readable(args.mem, plan, paths)  # before the FIRST write, not per applier
            validate_plan(args.mem, plan, paths)
            apply_iterations(args.mem, plan, date, args.dry_run, touched, tally)
            apply_decisions(args.mem, plan, date, args.dry_run, touched, tally)
            apply_restore(args.mem, plan, date, args.dry_run, touched, tally)
            # BEFORE corrections and promotions: they are the only other writers of user.md, and the
            # check above resolved the target lines against the file as it stands now.
            apply_user_md_supersede(args.mem, plan, date, args.dry_run, touched, tally, session_id, args.transcript,
                                    paths=paths)
            apply_corrections(args.mem, plan, date, args.dry_run, touched, tally, session_id, paths=paths)
            apply_learnings(args.mem, plan, date, args.dry_run, touched, tally)
            apply_principles(args.mem, plan, date, args.dry_run, touched, tally)
            apply_bridge_decisions(args.mem, plan, date, args.dry_run, touched, tally)
            apply_rule_explanations(args.mem, plan, date, args.dry_run, touched, tally)
            apply_rule_releases(args.mem, plan, date, args.dry_run, touched, tally)
            if "user_candidates" in plan or plan.get("consolidate"):
                # Identity growth is wrap-up Step 6's job: an iterations-only plan
                # (/agentic-os:log) must not re-review the queue and promote to user.md.
                apply_user_candidates(args.mem, plan, date, args.dry_run, touched, tally, session_id, args.transcript,
                                      paths=paths)
            if paths.md in touched or paths.queue in touched:
                # P2: the briefing shows the most confirmed profile lines, never the oldest ones (DCO #10148)
                identity_briefing.write_projection(paths, args.dry_run, touched)
            apply_soul_candidates(args.mem, plan, date, args.dry_run, touched, tally)
            apply_open_tasks(args.mem, plan, date, args.dry_run, touched, tally)
            apply_session_summary(args.mem, plan, args.dry_run, touched, tally, args.central_dir)
            # LAST: marker only after everything else succeeded (Step 9.5 rule 5)
            apply_consolidation(args.mem, plan, session_id, args.dry_run, touched, tally)
    except (PlanError, OSError, ValueError) as e:
        # All mean the same thing to the caller: the run stopped before
        # consolidation, so the marker is absent and the dirty flags still say
        # "un-consolidated". Reported as JSON so a harness can branch on it
        # instead of parsing a traceback (ValueError: a decode or shape error the
        # preflight did not foresee).
        kind = ("plan rejected" if isinstance(e, PlanError)
                else "io error" if isinstance(e, OSError) else "unreadable input")
        print(json.dumps({"ok": False, "error": f"{kind}: {e}", "files_written": touched,
                          "note": "consolidation marker NOT written - dirty state stays honest"},
                         ensure_ascii=False))
        return 2

    if tally["rule_class_open"] is None:
        tally["rule_class_open"] = rule_class_open(load_json(args.mem, "learnings/learnings.json", []))
    identity_line = (
        f"Identity: {tally['candidates_new'] + tally['candidates_updated']} beobachtet, "
        f"{tally['candidates_promoted']} → {'profile.md' if paths.central else 'user.md'} promotet, "
        f"{tally['soul_candidates_added']} soul-candidates; "
        f"Queue: {tally['queue_open']} offen"
    )
    result = {
        "ok": True,
        "dry_run": args.dry_run,
        "date": date,
        "session_id": session_id,
        "files_written": touched,
        # Step 3a: live learnings close to a planned one, for the model's verdict.
        "near_duplicates": tally.pop("near_duplicates"),
        "tally": tally,
        "identity_status_line": identity_line,
    }
    queue = tally["rule_class_open"]
    if queue["needs_explanation"] or queue["explained"] or any(
            k in plan for k in ("rule_explanations", "rule_releases")):
        # Step 3f: the measured line for the report, like the bridge and identity lines.
        result["rule_class_status_line"] = (
            f"Regel-Learnings: {len(queue['needs_explanation'])} warten auf die Zweitmodell-Erklärung "
            f"({', '.join(queue['needs_explanation']) or '-'}), {len(queue['explained'])} auf die "
            f"Owner-Freigabe ({', '.join(queue['explained']) or '-'}); "
            f"erklärt {len(tally['rule_explained'])}, freigegeben {len(tally['rule_released'])}, "
            f"abgelehnt {len(tally['rule_rejected'])}")
    if "bridge_decisions" in plan:
        # Step 3d.4: the measured line for the mandatory report, like Step 6.5's.
        result["bridge_status_line"] = (
            f"Bridge: {len(tally['bridge_approved'])} approved, {len(tally['bridge_rejected'])} rejected, "
            f"{tally['bridge_status_changed']} geaendert; offen: {len(tally['bridge_candidates'])}")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

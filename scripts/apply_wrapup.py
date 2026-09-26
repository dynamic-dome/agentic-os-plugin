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

USAGE
-----
    python scripts/apply_wrapup.py .agent-memory --session-id <sid> < plan.json
    python scripts/apply_wrapup.py .agent-memory --session-id <sid> --dry-run < plan.json

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
import datetime as _dt
import json
import os
import re
import sys
import tempfile

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
}

SECTION_BY_SIGNAL = {
    "preference": "Preferences",
    "communication": "Preferences",
    "workflow": "Work Style",
    "correction": "Known Corrections",
}

SUMMARY_MAX_LINES = 30
REVIEW_AFTER_DAYS = 90


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
    if key in FORBIDDEN:
        raise PlanError(f"refusing to write {key}: owned by another script")
    owner = APPLIER_OWNED.get(key)
    if owner and owner != via:
        raise PlanError(f"refusing to write {key}: only the '{owner}' applier may touch it")
    return os.path.join(mem, key)


def load_json(mem: str, rel: str, default, via: str | None = None):
    path = os.path.join(mem, rel)
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Error Handling contract: quarantine, do not crash the whole wrap-up.
        # Route the rename through _p() so quarantining is covered by the same
        # guard as every other mutation - otherwise this would be a second,
        # unguarded write path into the memory dir.
        guarded = _p(mem, rel, via)
        os.replace(guarded, guarded + ".corrupt.bak")
        return default


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


def next_id(rows, prefix_arg: str, pad: int = 0) -> str:
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
    n = hi + 1
    return f"{prefix}{n:0{pad}d}" if pad else f"{prefix}{n}"


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


# ------------------------------------------------------------------- steps

def validate_plan(mem, plan):
    """Reject EVERY detectable plan error before the first byte is written.

    Originally this only checked `supersedes`. That left the other required
    fields to fail inside their applier - so a plan whose learnings were broken
    still wrote its iteration log first and exited 2 half-applied (Codex review
    of 1e5c504). Anything checkable without touching disk belongs here.
    """
    for it in (plan.get("iterations") or []):
        if not (it.get("title") or "").strip():
            raise PlanError("iteration without 'title'")
    for lrn in (plan.get("learnings") or []):
        if not (lrn.get("text") or "").strip():
            raise PlanError("learning without 'text'")
        try:
            int(lrn.get("importance", 3))
        except (TypeError, ValueError):
            # Uncaught, this ValueError surfaced as a traceback mid-run
            # instead of the fail-soft JSON (Codex review 2026-07-27).
            raise PlanError(f"learning with non-numeric importance "
                            f"{lrn.get('importance')!r}")
    for cand in (plan.get("user_candidates") or []):
        if not (cand.get("key") or "").strip():
            raise PlanError("user candidate without 'key'")
    for soul in (plan.get("soul_candidates") or []):
        if not (soul.get("proposal") or "").strip():
            raise PlanError("soul candidate without 'proposal'")
    for task in ((plan.get("open_tasks") or {}).get("add") or []):
        if not (task.get("title") or "").strip():
            raise PlanError("open task without 'title'")

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
                    "id": next_id(errors, "err-", pad=3),
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
            "id": next_id(rows, "D-", pad=3),
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


def apply_learnings(mem, plan, date, dry, touched, tally):
    items = plan.get("learnings") or []
    rows = load_json(mem, "learnings/learnings.json", [])
    seen = {norm(r.get("text")) for r in rows}
    added = []
    for it in items:
        text = (it.get("text") or "").strip()
        if not text:
            raise PlanError("learning without 'text'")
        if norm(text) in seen:
            tally["learnings_skipped_duplicate"] += 1
            continue
        seen.add(norm(text))
        entry = {
            "id": next_id(rows + added, "L"),
            "date": date,
            "text": text,
            "importance": int(it.get("importance", 3)),
            "tags": list(it.get("tags") or []),
            "layer": "short-term",
            "superseded_by": None,
            "last_relevant": date,
            "derived_from": list(it.get("derived_from") or []),
            "review_after": (
                _dt.date.fromisoformat(date) + _dt.timedelta(days=REVIEW_AFTER_DAYS)
            ).isoformat(),
        }
        # Step 3d.1: derived from importance alone. Plan-supplied bridge_status
        # is deliberately dropped - "approved" exists only via the [j/n] gate.
        if entry["importance"] >= 4:
            entry["bridge_status"] = "candidate"
        added.append(entry)
    if added:
        rows.extend(added)
        write_json(mem, "learnings/learnings.json", rows, dry, touched)
        tally["learnings_added"] = len(added)
        tally["learning_ids"] = [e["id"] for e in added]
        render_learnings_md(mem, rows, dry, touched)
    # Store-wide, not session-scoped: earlier declined candidates must keep
    # triggering the Step 3d.2 prompt line. Pre-3d entries lack the field
    # (never backfilled) and are invisible here by design.
    tally["bridge_candidates"] = [
        r["id"] for r in rows if r.get("bridge_status") == "candidate"]


def render_learnings_md(mem, rows, dry, touched):
    """learnings.md is a pure projection of learnings.json - fully deterministic."""
    out = ["# Learnings", ""]
    for imp in (5, 4, 3, 2, 1):
        bucket = [r for r in rows if int(r.get("importance", 3)) == imp and not r.get("superseded_by")]
        if not bucket:
            continue
        out.append(f"## Importance {imp}")
        out.append("")
        for r in sorted(bucket, key=lambda x: str(x.get("date", "")), reverse=True):
            tags = " ".join(f"#{t}" for t in (r.get("tags") or []))
            out.append(f"- **{r.get('id')}** ({r.get('date')}) {r.get('text')}" + (f"  {tags}" if tags else ""))
        out.append("")
    write_atomic(mem, "learnings/learnings.md", "\n".join(out).rstrip() + "\n", dry, touched)


def apply_user_candidates(mem, plan, date, dry, touched, tally):
    """Step 6.2 enqueue + 6.3 FULL queue re-review. Both rules are deterministic."""
    obs = plan.get("user_candidates") or []
    queue = load_json(mem, "working/user-candidates.json", [])
    by_key = {c.get("key"): c for c in queue}

    for o in obs:
        key = (o.get("key") or "").strip()
        if not key:
            raise PlanError("user candidate without 'key'")
        trust = o.get("trust_source", "conversation")
        if trust != "conversation":
            # Step 6.1 trust boundary - memory-poisoning defense.
            tally["candidates_rejected_trust"] += 1
            continue
        if key in by_key:
            c = by_key[key]
            c["occurrences"] = int(c.get("occurrences", 1)) + 1
            c["last_seen"] = date
            ev = c.setdefault("evidence", [])
            for e in (o.get("evidence") or [f"session {date}"]):
                if e not in ev:
                    ev.append(e)
            if o.get("confirmed") or int(c["occurrences"]) >= 2:
                if c.get("status") in ("observed", "inferred"):
                    c["status"] = "confirmed"
            tally["candidates_updated"] += 1
        else:
            c = {
                "id": next_id(queue, "UC"),
                "key": key,
                "observation": (o.get("observation") or "").strip(),
                "status": "confirmed" if o.get("confirmed") else o.get("status", "observed"),
                "signal_type": o.get("signal_type", "preference"),
                "confidence": float(o.get("confidence", 0.5)),
                "occurrences": 1,
                "evidence": list(o.get("evidence") or [f"session {date}"]),
                "first_seen": date,
                "last_seen": date,
                "trust_source": "conversation",
            }
            queue.append(c)
            by_key[key] = c
            tally["candidates_new"] += 1

    promoted = promote_candidates(mem, queue, date, dry, touched, tally)
    if obs or promoted:
        write_json(mem, "working/user-candidates.json", queue, dry, touched)
    tally["queue_open"] = sum(1 for c in queue if c.get("status") != "promoted")


def _norm_tokens(text):
    return set(re.sub(r"[^a-z0-9\s]", " ", str(text or "").lower()).split())


def _jaccard(a, b):
    ta, tb = _norm_tokens(a), _norm_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


_CITATION_SUFFIX = re.compile(r"\s*[\[(]\s*UC\d+\s*,[^\])]*[\])]\s*$")


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


def promote_candidates(mem, queue, date, dry, touched, tally):
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

    changelog = load_json(mem, "identity/user-changelog.json", [])
    user_md = read_user_md(mem)
    now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")

    promoted = []
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
            c["status"] = "promoted"
            c["status_after_promotion"] = "duplicate_of_existing"
            tally["promotion_skipped_duplicate"] += 1
            continue
        promoted.append(c)
        section = SECTION_BY_SIGNAL.get(c.get("signal_type", "preference"), "Preferences")
        line = f"- {c.get('observation')} ({c.get('id')}, {date})"
        # changelog BEFORE edit - ordering is part of the contract
        changelog.append({
            "ts": now,
            "field": f"user.md/{section}",
            "old_value": None,
            "new_value": c.get("observation"),
            "candidate_id": c.get("id"),
            "evidence": c.get("evidence") or [],
        })
        user_md.setdefault(section, [])
        if line not in user_md[section]:
            user_md[section].append(line)
        c["status"] = "promoted"
        c["status_after_promotion"] = "promoted"

    write_json(mem, "identity/user-changelog.json", changelog, dry, touched)
    write_user_md(mem, user_md, dry, touched)
    tally["candidates_promoted"] = len(promoted)
    tally["promoted_ids"] = [c.get("id") for c in promoted]
    return promotable


def read_user_md(mem):
    """Parse user.md into {section: [lines]} preserving unknown sections."""
    path = os.path.join(mem, "identity/user.md")
    sections, current = {}, None
    if os.path.exists(path):
        for raw in open(path, encoding="utf-8"):
            line = raw.rstrip("\n")
            if line.startswith("## "):
                current = line[3:].strip()
                sections.setdefault(current, [])
            elif line.startswith("# "):
                current = None
            elif current is not None and line.strip():
                sections[current].append(line)
    return sections


def write_user_md(mem, sections, dry, touched):
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
    write_atomic(mem, "identity/user.md", "\n".join(out).rstrip() + "\n", dry, touched)


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
        if norm(proposal) in norm(existing):
            tally["soul_skipped_duplicate"] += 1
            continue
        ev = "; ".join(it.get("evidence") or [])
        block.append(f"\n## {date} — {proposal}\n\n- **Evidence:** {ev or 'n/a'}\n")
        tally["soul_candidates_added"] += 1
    if block:
        write_atomic(mem, "identity/soul-candidates.md",
                     existing.rstrip() + "\n" + "".join(block), dry, touched)


def apply_open_tasks(mem, plan, date, dry, touched, tally):
    spec = plan.get("open_tasks") or {}
    add, close = spec.get("add") or [], set(spec.get("close") or [])
    if not add and not close:
        return
    rows = load_json(mem, "context/open-tasks.json", [])
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
        rows.append({
            "id": next_id(rows, "T-", pad=3),
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


def apply_session_summary(mem, plan, dry, touched, tally):
    s = plan.get("session_summary")
    if not s:
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


def apply_consolidation(mem, plan, session_id, dry, touched, tally):
    """Step 9.5 - runs LAST and only when everything above succeeded."""
    if not plan.get("consolidate"):
        return
    work = os.path.join(mem, "working")
    dirty_files, sessions, seen_files = [], [], 0
    if os.path.isdir(work):
        for name in sorted(os.listdir(work)):
            if not (name.startswith("dirty-") and name.endswith(".json")):
                continue
            # Strict on purpose: the generic loader would quarantine a corrupt
            # dirty file, return None and let the run finish with a marker -
            # destroying the only record that work was left un-consolidated
            # (Codex review of 1e5c504). Un-readable evidence must stop the run.
            path = os.path.join(work, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    d = json.load(fh)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                raise PlanError(
                    f"working/{name} is unreadable ({e}); refusing to consolidate over "
                    f"un-consolidated work - fix or remove the file and re-run") from e
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
        d["consolidated_by"] = "wrap-up"
        d["last_consolidated_at"] = now
        d["last_consolidated_by"] = "wrap-up"
        d["writes_since_consolidation"] = 0
        write_json(mem, f"working/{name}", d, dry, touched)
    tally["dirty_files_consolidated"] = len(dirty_files)
    tally["touched_files_seen"] = seen_files

    write_json(mem, "consolidation-marker.json", {
        "last_wrapup": now,
        "consolidated_sessions": sessions or ([session_id] if session_id else []),
        # Measured first, plan-declared only as a fallback for runs whose
        # iterations were logged elsewhere (see verify-subagent-tallies).
        "iterations_logged": tally["iterations_logged"] or int(plan.get("iterations_logged", 0)),
        "learnings_added": tally["learnings_added"],
        "touched_files_seen": seen_files,
    }, dry, touched)


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
    ap.add_argument("--session-id", default="")
    ap.add_argument("--plan", default="-", help="plan JSON file, '-' for stdin")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(args.mem):
        print(json.dumps({"ok": False, "error": f"memory dir not found: {args.mem}"}))
        return 1

    try:
        raw = sys.stdin.read() if args.plan == "-" else open(args.plan, encoding="utf-8").read()
        plan = json.loads(raw)
        if not isinstance(plan, dict):
            raise PlanError("plan must be a JSON object")
    except (json.JSONDecodeError, OSError) as e:
        print(json.dumps({"ok": False, "error": f"unreadable plan: {e}"}))
        return 2

    date = plan.get("date") or _dt.date.today().isoformat()
    session_id = args.session_id or plan.get("session_id", "")

    tally = {
        "iterations_logged": 0, "iterations_skipped_duplicate": 0,
        "errors_added": 0, "errors_recurred": 0,
        "decisions_added": 0, "decisions_superseded": 0,
        "decisions_skipped_duplicate": 0, "decision_ids": [],
        "learnings_added": 0, "learnings_skipped_duplicate": 0, "learning_ids": [],
        "bridge_candidates": [],
        "candidates_new": 0, "candidates_updated": 0, "candidates_promoted": 0,
        "candidates_rejected_trust": 0, "promotion_blocked_trust": 0,
        "promotion_skipped_duplicate": 0,
        "promoted_ids": [], "queue_open": 0,
        "soul_candidates_added": 0, "soul_skipped_duplicate": 0,
        "tasks_added": 0, "tasks_closed": 0, "tasks_skipped_duplicate": 0,
        "session_summary_lines": 0, "dirty_files_consolidated": 0,
        "touched_files_seen": 0, "warnings": [],
    }
    touched: list = []

    try:
        validate_plan(args.mem, plan)
        apply_iterations(args.mem, plan, date, args.dry_run, touched, tally)
        apply_decisions(args.mem, plan, date, args.dry_run, touched, tally)
        apply_learnings(args.mem, plan, date, args.dry_run, touched, tally)
        if "user_candidates" in plan or plan.get("consolidate"):
            # Identity growth is wrap-up Step 6's job: an iterations-only plan
            # (/agentic-os:log) must not re-review the queue and promote to user.md.
            apply_user_candidates(args.mem, plan, date, args.dry_run, touched, tally)
        apply_soul_candidates(args.mem, plan, date, args.dry_run, touched, tally)
        apply_open_tasks(args.mem, plan, date, args.dry_run, touched, tally)
        apply_session_summary(args.mem, plan, args.dry_run, touched, tally)
        # LAST: marker only after everything else succeeded (Step 9.5 rule 5)
        apply_consolidation(args.mem, plan, session_id, args.dry_run, touched, tally)
    except (PlanError, OSError) as e:
        # Both classes mean the same thing to the caller: the run stopped before
        # consolidation, so the marker is absent and the dirty flags still say
        # "un-consolidated". Reported as JSON so a harness can branch on it
        # instead of parsing a traceback.
        kind = "plan rejected" if isinstance(e, PlanError) else "io error"
        print(json.dumps({"ok": False, "error": f"{kind}: {e}", "files_written": touched,
                          "note": "consolidation marker NOT written - dirty state stays honest"},
                         ensure_ascii=False))
        return 2

    identity_line = (
        f"Identity: {tally['candidates_new'] + tally['candidates_updated']} beobachtet, "
        f"{tally['candidates_promoted']} → user.md promotet, "
        f"{tally['soul_candidates_added']} soul-candidates; "
        f"Queue: {tally['queue_open']} offen"
    )
    print(json.dumps({
        "ok": True,
        "dry_run": args.dry_run,
        "date": date,
        "session_id": session_id,
        "files_written": touched,
        "tally": tally,
        "identity_status_line": identity_line,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

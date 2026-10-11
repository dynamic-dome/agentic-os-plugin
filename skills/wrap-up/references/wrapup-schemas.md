# wrap-up — write-time schemas & templates

Inert JSON schemas and output templates lifted out of `skills/wrap-up/SKILL.md`
under the T-35 redesign rule: **no gate logic leaves the body**. These blocks are
read only when the agent actually WRITES the corresponding artifact — every gate's
trigger and result-contract stay in the body, which points here and loads this file
only at the write step it names.

Design: `memskillredesign.md` / `memevalharness.md` (membrain). The wrap-up gate
inventory (`gate_linkage.py`, 28 gates) and `validate-skills.sh` anchors stay in the
body, never here.

## Write plan (batch writer)

`scripts/apply_wrapup.py` applies every mutation below in ONE pass. Emit this
object once, pipe it into the script, read the returned tally — do not write
these files with individual Write/Edit calls (that is what made wrap-up cost
28 API calls per run, each resending the full session context — the
model is stateless, so cost is the sum of context length over calls).

```json
{
  "date": "{YYYY-MM-DD}",
  "session_id": "{sid}",
  "iterations":      [ { "type": "feature", "title": "...", "tags": [], "files_changed": [],
                         "summary": "...", "confidence": 5, "tests": "passed (n/n)",
                         "learnings": "...", "commits": "abc1234",
                         "recovered_from": null,
                         "errors": [ { "category": "runtime", "tags": [], "trigger": "...",
                                       "problem": "...", "root_cause": "...", "fix": "...",
                                       "failed_approaches": [], "prevention": "...",
                                       "severity": "major", "attempts": 1,
                                       "confidence": 4 } ] } ],
  "decisions":       [ { "type": "architecture-decision", "title": "...", "context": "...",
                         "options_considered": [ { "option": "...", "pros": [], "cons": [] } ],
                         "decision": "...", "consequences": "...",
                         "supersedes": null, "tags": [] } ],
  "learnings":       [ { "text": "...", "summary": "...", "importance": 3, "tags": [], "derived_from": [],
                         "duplicate_of": null } ],
  "restore":         { "source": "restore_plan.py", "learnings": [ "...rows from restore_plan.py..." ] },
  "user_candidates": [ { "key": "kebab-key", "observation": "...", "signal_type": "preference",
                         "confidence": 0.5, "evidence": [], "confirmed": false,
                         "status": "observed", "trust_source": "conversation" } ],
  "soul_candidates": [ { "proposal": "...", "evidence": [] } ],
  "open_tasks":      { "add": [ { "title": "...", "source": "wrap-up", "cross_project": false } ],
                       "close": ["T-001"] },
  "session_summary": { "what_was_done": [], "open_items": [], "next_steps": [],
                       "statistics": { "iterations": 0, "errors": 0, "new_patterns": 0 },
                       "warnings": [],
                       "handoff": { "active_task": "", "current_state": "",
                                    "active_patterns": "", "open_questions": "" } },
  "consolidate": true,
  "iterations_logged": 0
}
```

Every key is optional — omit what the session did not produce. The script owns
all deterministic rules and needs no help with them: id assignment continuing
whatever format is already on disk (`L{n}` / `UC{n}` / `T-{00n}` / `err-{00n}` /
`D-{00n}` — never assume, the real files drifted from their templates),
the iteration markdown shape, the recurrence rule (same category AND ≥2
overlapping tags), the decision supersede flip, `review_after` = date + 90d,
exact-text dedup,
the `signal_type: mood` block, the promotion rule (`confirmed` OR `inferred`
AND `occurrences >= 2` AND `confidence >= 0.6`), changelog-before-edit
ordering, `learnings.md` regeneration, the bridge-candidate derivation
(`importance >= 4` → `bridge_status: "candidate"`, Step 3d.1 — a plan-supplied
`bridge_status` is dropped; `approved` exists only via the Step 3d.3 gate),
the consolidation marker and the dirty
flags. Supply judgment only: what is a learning, what is an identity signal,
how important, which section.

Guarantees worth relying on:

- `trust_source` other than `conversation` is dropped (Step 6.1 boundary) —
  both when enqueuing a new observation **and** when the full-queue re-review
  considers a row that was already on disk. A poisoned row that somehow
  reached the queue can never be promoted into `user.md`.
- `patterns.json`/`patterns.md` (owned by `scripts/extract_patterns.py`) and
  `soul.md` are refused outright. `iteration-log.md`, `errors.json`,
  `current-session.json` and `decisions.json` are reachable ONLY through their
  named applier (`iterations` / `decisions`) — the generic write path still
  refuses them. An unreadable store file is never renamed or emptied: the run
  stops with exit 2 before the first write (5.2.1).
- A `supersedes` pointing at an unknown decision id rejects the WHOLE plan
  before the first byte is written, so a rejected plan never leaves a
  half-written iteration log behind.
- On a rejected plan or an IO failure the run stops there, **the consolidation
  marker is skipped and dirty flags stay set** (Step 9.5 rule 5), and the
  failure is reported as JSON with exit code 2. `session_id` may be passed in
  the plan or via `--session-id` (the flag wins).
- `--dry-run` reports the full tally without touching a file.
- (5.3.0) `learnings/learnings.json` is applier-owned too (`learnings`; the
  `restore` section uses the same applier). Ownership is checked case-insensitively,
  so `Learnings/Learnings.json` is refused like the canonical path.
- (5.3.0) Every learning the script writes is validated before the first byte:
  `importance` a JSON int 1..5 (no `"5"`, no float, no bool), `tags` a list of
  strings, `derived_from` a list of at most 30 strings, `duplicate_of` a live id.
  Legacy rows are never a gate — `--lint` lists them, read-only.
- (5.3.0, Step 3a) Jaccard >= 0.6 to a live learning is a duplicate (that entry's
  `last_relevant` is refreshed); the top-level `near_duplicates` of the result lists
  live entries at 0.2–0.6 for the `duplicate_of` verdict.
- (5.3.0) `restore`: rows from `scripts/restore_plan.py`, decided against the fresh
  store at apply time — same id + text or same text live → skipped (a second run
  changes nothing); a free archive id is kept; an id taken by another text gets the
  next id plus `legacy:<id>` in `derived_from`; an invalid row is dropped and listed
  in `tally.restore_dropped`, never the whole run. A snapshot is taken first.
- (5.3.0) A writing run holds `working/store.lock` from the first read to the last
  write; a store locked longer than `--lock-timeout` (10 s) is exit 2, nothing written.

The returned `identity_status_line` is computed from the writes that actually
happened — use it verbatim for the mandatory Step 6.5 line instead of counting
by hand. Likewise `tally.bridge_candidates` lists ALL store-wide
`bridge_status: "candidate"` ids (this session's and earlier declined ones) —
build the Step 3d.2 `BRIDGE CANDIDATES:` prompt from it instead of re-reading
`learnings.json`.

## Learning entry (Step 3b)

Append to `learnings/learnings.json`:

```json
{
  "id": "L{next_number}", "date": "{YYYY-MM-DD}", "text": "{insight with context}",
  "summary": "{conclusion, <= 150 chars, optional}",
  "importance": 3, "tags": ["tag1", "tag2"], "layer": "short-term",
  "superseded_by": null, "last_relevant": "{YYYY-MM-DD}",
  "derived_from": ["iteration-{n}", "E{id}"], "review_after": "{YYYY-MM-DD}"
}
```

importance: 5 = prevents data loss/security issue · 4 = prevents multi-attempt debugging ·
3 = non-obvious behavior · 2 = workflow optimization · 1 = trivia.

**`derived_from` (provenance, memideaspec §7.4):** IDs of THIS session's origins —
iteration numbers from `iteration-log.md` (`iteration-{n}`), error IDs from
`errors.json` (`E{n}`), decision IDs (`D{n}`). No traceable origin → `[]`. Never
invent provenance; an honest empty list beats a guessed reference.

**`summary` (short form, optional — strongly recommended for `importance >= 4`):** the
conclusion in one sentence, <= 150 characters — the rule, not its context. The MEMORY.md
and AGENTS.md projections show it instead of the first 150 characters of `text`, which
are usually context. Omit it rather than paraphrase badly; without it the projection
cuts `text`. Longer than 150 → kept, but the tally warns and the projection cuts it.

**`review_after` (staleness contract):** date when the learning's validity should be
re-checked; default = `date` + 90 days (matches the bootstrap STALE threshold). Set
ONCE at creation — bootstrap and /agentic-os:maintain read it, wrap-up never updates it
on existing entries.

Backward compatibility: entries created before v4.4.0 lack both fields — leave them
as-is (consumers use `.get()`); do NOT backfill.

## User candidate (Step 6.2)

Enqueue into `working/user-candidates.json` (new observation → new candidate; same
`key` exists → increment `occurrences`, update `last_seen`, raise `status` if warranted):

```json
{
  "id": "UC{n}", "key": "kebab-case-key",
  "observation": "{1-line observed preference}",
  "status": "observed", "signal_type": "preference",
  "confidence": 0.5, "occurrences": 1,
  "evidence": ["session {YYYY-MM-DD}"],
  "first_seen": "{date}", "last_seen": "{date}",
  "trust_source": "conversation"
}
```

## Consolidation marker (Step 9.5)

Overwrite `.agent-memory/consolidation-marker.json` (single file, no history — git
history preserves older markers):

```json
{
  "last_wrapup": "{ISO timestamp}",
  "mode": "wrap-up",
  "consolidated_sessions": ["{session_id}", "..."],
  "iterations_logged": 0,
  "learnings_added": 0,
  "touched_files_seen": 0
}
```

`mode` (and `consolidated_by` / `last_consolidated_by` on every consumed dirty file)
names who consolidated: `"wrap-up"` for this skill, `"headless"` for the night run
(`wrapup_core.py apply --headless`, no judge — learnings, decisions and identity were
not harvested). `apply_wrapup.apply_consolidation(by=...)` writes both; any other
value is a plan error.

## Local session-summary.md (Step 5)

Overwrite `.agent-memory/session-summary.md` (English headers; **max 30 lines**;
delta-update — rewrite only changed sections):

Exception (DCO #9433): when the store's summary IS the central handoff
(`<central-dir>/.agent-memory/session-summary.md`, default `~/AI` — the AI workspace),
`apply_wrapup.py` does not overwrite it and reports a warning instead; the block
reaches that file only through the prepend path (Step 7.6a / `write_central`).

```markdown
# Last Session

*Date: {YYYY-MM-DD HH:MM}*
*Agent: Claude Code*

## What Was Done
- {completed work, max 10 bullets}

## Open Items
- {unfinished work, blockers}

## Next Steps
1. {highest priority} 2. {second} 3. {third}

## Statistics
- Iterations: {n} | Errors: {n} | New Patterns: {n}

## Active Warnings
- {high-confidence patterns / declining trends, if any}
```

## Handoff Mode (Pre-Compression)

When triggered by long context or explicit handoff request, append to session-summary.md:

```markdown
## Handoff Context
- **Active task**: {what was being worked on right now}
- **Current state**: {done / next}
- **Active patterns**: {top high-confidence patterns}
- **Open questions**: {decisions pending user input}
```

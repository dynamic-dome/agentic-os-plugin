---
name: memory-audit
description: Read-only drift, provenance, and staleness report over .agent-memory/. Run this instead of trusting a months-old manual audit — it reads the live store so conclusions can never be stale.
allowed_tools: ["Read", "Glob", "Grep"]
---

# Memory Audit

A repeatable, **strictly read-only** report on the health of `.agent-memory/`. It exists
because a manual audit measured a stale clone of the store and drew two phantom-gap
conclusions — this command always reads the live files, so its numbers are ground truth.

**Never write, edit, or repair anything here.** If the audit finds drift, it reports it and
names the skill that heals it (`/agentic-os:maintain`, `wrap-up`, etc.) — it does not act.

## Step 0: Preconditions

If `.agent-memory/` does not exist → output "No memory system in this project. Run
`/agentic-os:init`." and stop.

## Step 1: Drift

Detect divergences between where data IS and where it SHOULD be:

1. **open-tasks root drift** — does `.agent-memory/open-tasks.json` exist at the ROOT?
   Canonical is `context/open-tasks.json` only. If the root copy exists → flag
   "open-tasks root drift — the SessionStart briefing flags it; wrap-up / /agentic-os:maintain merge it".
2. **patterns schema drift** — read `patterns/patterns.json`. Flag any entry that does NOT use
   the canonical fields (`description`/`recommendation`/`evidence`) — i.e. legacy
   `name`/`solution`/`source_errors` or `title`/`prevention`/`error_ids`. Name them by `id`.
3. **learnings.md vs learnings.json** — does `learnings.md` start with the
   "Auto-generated from learnings.json" header? If not → flag regeneration needed.
4. **project-context.md vs docs/** — note that project-context.md is a cache; if its content
   names things absent from `docs/` (deleted agents/skills), flag possible cache drift.

## Step 2: Staleness

Report age-based risks (the dimension the manual audit got wrong from old data):

1. **learnings layer rot** — for each entry in `learnings/learnings.json`: if `layer` is
   `short-term` AND `date` (or `last_relevant`) is older than 30 days → flag "promotion-due".
   Report the count and the **newest** `last_relevant` so the reader can see how fresh the
   store actually is (avoids the stale-clone trap).
2. **quality never initialized** — if `quality/quality-score.json` has `last_updated: null`
   while `iterations/iteration-log.md` has entries → flag "quality metrics never run".
3. **user.md stub** — if `identity/user.md` is still only the init stub (no real Preferences)
   → flag "user.md not growing — check wrap-up Step 6 candidate queue".
4. **soul candidates pending** — if `identity/soul-candidates.md` has open candidates → note
   "N soul candidates awaiting the [j/n] gate at next session start".

## Step 3: Provenance / Schema consistency

1. **patterns provenance** — for each pattern, confirm `evidence`, `source_projects`,
   `confidence`, `first_seen`/`last_seen` are present. Flag entries missing provenance.
2. **superseded integrity** — if any pattern/learning has `lifecycle: superseded`, confirm it
   carries `superseded_by`. Flag dangling supersessions.
3. **JSON validity** — for each JSON store, confirm it parses. Flag any that would need
   `.corrupt.bak` recovery (but do NOT perform the recovery here).
4. **global provenance (global-provenance-audit)** — only if `~/.claude-memory/global/`
   exists. This is read-only reporting of the 4.A invariants — it heals nothing:
   - Flag global entries missing `scope` / `valid_from` / `source_projects` / `lifecycle`
     (un-migrated → name the count; heals via `migrate-global-schema-4A.sh`).
   - Count `active` global entries with `|source_projects| < 2` → "promotion-gate violation"
     (should be `candidate`, not `active`).
   - Count entries with `confidence <= 0.3` AND `lifecycle != archived` older than 365d →
     "decay-due" (heals via `/agentic-os:maintain` Step 4b).

## Step 3.4: Backflow Completeness (rueckfluss-audit)

The fields that close the experience→behavior loop exist and are written by
`wrap-up` (`derived_from`, `review_after`) and `pattern-extractor`
(`implemented_by`, `validated_by`). Nothing reports whether they are actually
being filled — so a formally complete loop can sit idle unnoticed (source:
membrain `memperfectflowharvest.md`, Rosine R1 / T-41). Report all four,
**always by record ID**, never as a bare count:

1. **learnings without provenance** — entries in `learnings/learnings.json` whose
   `derived_from` is missing or an empty list. List the IDs. Early-store entries
   predating the provenance contract are expected; a *recent* entry without
   `derived_from` is the real signal, so report the newest offender's `date`.
2. **overdue reviews** — entries in `learnings/learnings.json` (and patterns, if
   they carry the field) whose `review_after` is earlier than today. List the IDs
   with their dates. Heals via `wrap-up` (re-confirm and push the date out) or by
   setting `superseded_by`.
3. **ready but not implemented** — patterns with `promotion_status: ready` (the
   value set is `candidate` | `ready`, set by `obsidian-sync` Step 6) whose
   `implemented_by` is missing or empty. These claim maturity without a landing
   site. List the IDs — this is the `feedback-loop-gap` class made visible.
4. **implemented but not validated** — patterns with a non-empty `implemented_by`
   but missing/empty `validated_by`. The change landed; nothing shows it improved
   behavior. List the IDs.

Report **zero counts explicitly** (`0 — clean`). A silent block reads as
"not checked" and re-creates exactly the uncertainty this step exists to remove.

## Step 3.5: Classify Every Finding (gap-taxonomy)

Label each finding from Steps 1–3 with exactly one gap class, so the reader knows WHERE
in the memory loop the problem sits (source: membrain Loop-8 harvest, Rosine 3 / T-17):

| Class | Meaning |
|---|---|
| `knowledge-gap` | the information was never captured anywhere |
| `capture-gap` | recognized during a session, but never written to a durable store |
| `index-gap` | stored, but invisible to the index (quarantine, adapter error, bad frontmatter) |
| `retrieval-gap` | indexed, but not findable (filter/ranking/mapping error) |
| `link-gap` | both entries exist, but the relation between them is missing |
| `usage-gap` | found, but ignored by the acting agent |
| `feedback-loop-gap` | problem recognized repeatedly, but no procedural change followed |

**Diagnosis rule before any verdict:** a zero-hit search after a late filter is NOT proof
that the record does not exist — check for an index-gap or retrieval-gap before declaring
a knowledge-gap. (This is the real-world L23 failure class: quarantined records looked
like missing knowledge.)

## Step 4: Report

Print a compact table. Example shape:

```
MEMORY AUDIT — {project} — {date} (read-only)

DRIFT
  open-tasks root:     {none | FOUND at root}
  patterns schema:     {all canonical | legacy: P003,...}
  learnings.md header: {ok | regenerate}

STALENESS
  learnings:           {n} entries · {m} promotion-due · newest last_relevant {date}
  quality metrics:     {initialized | never run}
  user.md:             {growing | still stub}
  soul candidates:     {n} pending gate

PROVENANCE
  patterns w/o evidence: {n}
  dangling supersessions: {n}
  JSON validity:         {all ok | corrupt: <file>}

BACKFLOW (Step 3.4 — always by ID, "0 — clean" when empty)
  learnings w/o derived_from: {n} — {L1,L2,...} (newest {date})
  review_after overdue:       {n} — {L7 2026-05-01, ...}
  ready w/o implemented_by:   {n} — {P3,...}
  implemented w/o validated_by: {n} — {P5,...}

GLOBAL (~/.claude-memory/global/ — omit block if absent)
  entries w/o provenance: {n} (un-migrated → migrate-global-schema-4A.sh)
  promotion-gate violations: {n} (active but |source_projects| < 2)
  decay-due:             {n} (conf<=0.3, not archived, >365d)

FINDINGS (one line per flagged item — GAP CLASS from Step 3.5)
  {finding}            | {GAP CLASS}        | heals via {skill}
  e.g. legacy P003     | index-gap          | heals via pattern-extractor normalization
  e.g. P7 no evidence  | link-gap           | heals via wrap-up backfill
  e.g. P3 ready, no impl | feedback-loop-gap | heals via pattern-extractor delta gate

VERDICT: {clean | N drift items, M staleness items, K backflow items — see above}
Heals via: {/agentic-os:maintain | wrap-up | pattern-extractor | migrate-global-schema-4A.sh | none needed}
```

Keep the blocks terse — one line per metric, IDs inline. Report only — never mutate. End by
naming which skill heals any flagged item, so the user can act deliberately.

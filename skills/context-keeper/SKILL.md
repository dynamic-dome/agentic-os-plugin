---
name: context-keeper
description: >
  Maintains the living project context and decision log (ADRs) in
  .agent-memory/context/. Use when an architecture decision, stack change,
  or constraint lands ("record decision", "new dependency"), or to answer
  "why did we choose X".
metadata:
  author: agentic-os
  version: '3.3'
  part-of: agentic-os
  layer: core
---

# Context Keeper

Maintain `.agent-memory/context/project-context.md` and `.agent-memory/context/decisions.json`.

## When to Use

- Architecture decision made (new library, pattern choice, trade-off)
- Tech stack change (new dependency, framework swap, version upgrade)
- Constraint discovered (performance limit, API restriction, compliance rule)
- User asks "why did we choose X?" (retrieval mode)
- Project status changes significantly

## Dual-File Responsibility

| File | Mode | Purpose |
|------|------|---------|
| `project-context.md` | Overwrite | **Cache** — a compact agent view distilled from the project docs |
| `decisions.json` | Append + field-extend | Decision log — never delete records or rewrite their content; supersede via status flip. Existing records may only GAIN fields (`status`, `wiki_ref`, `promoted_at`) |

## Source-of-Truth Hierarchy (IMPORTANT)

`project-context.md` is NOT the source of truth — the project docs are (Regel 13):

1. **`docs/PROJECT.md`** — name, status, stack, one-liner, open items, dependencies
2. **`docs/ARCHITECTURE.md`** — components, data flow, persistence
3. **`docs/CAPABILITIES.md`** — features/tools and their status
4. **`HOW-TO-USE.md`** — entry-point map
5. **`CLAUDE.md`** — project conventions

`project-context.md` is a **cache**: a compact distillation of those docs for fast
agent restore. When docs and cache disagree, the **docs win** — refresh the cache
from them, never treat the cache as truth. Runtime-only knowledge that is not yet in
the docs (a fresh decision, a just-discovered constraint) may live in the cache until
it is promoted into the docs.

## Step 1: Classify the Update

Determine the type:

| Type | Description | Target |
|------|-------------|--------|
| `stack-change` | New/removed technology | project-context.md |
| `architecture-decision` | Design choice with alternatives | Both files |
| `constraint-update` | New limitation or requirement | project-context.md |
| `dependency-note` | New dependency with rationale | project-context.md |
| `status-update` | Project milestone or phase change | project-context.md |

## Step 1.5: Read the Project Docs (primary source)

Before writing the cache, gather the authoritative state from the docs. Read each
that exists; skip silently if absent (a project may not have the full Regel-13 skeleton):

1. `docs/PROJECT.md` — frontmatter (`stack`, `status`, `repo`) + "Einzeiler", "Aktueller Stand", "Offene Baustellen", "Abhaengigkeiten"
2. `docs/ARCHITECTURE.md` — "Ueberblick" + "Kernkomponenten" (for the Architecture section)
3. `docs/CAPABILITIES.md` — the tools/features table (for status notes)
4. `HOW-TO-USE.md` and `CLAUDE.md` — for build/test commands and conventions

**Rules:**
- The docs are the source of truth. Derive the cache's Tech Stack / Architecture /
  Constraints / Status from them — do NOT re-infer the stack from config files when a
  doc already states it (that is `/init`'s job on first run, not context-keeper's).
- If a doc CONTRADICTS the current `project-context.md`, the doc wins. Note the drift
  in the Step 5 output so the user knows the cache was stale.
- **Partial docs:** if SOME docs exist but a needed section is missing (e.g. PROJECT.md
  is present but ARCHITECTURE.md is absent), use the docs for what they cover and fall
  back to config-file detection / user-ask ONLY for the missing facts. Do not block.
- If NO docs exist at all → fall back to reading config files / asking the user, and
  add a note suggesting the user create the Regel-13 skeleton.
- This step is read-only.

## Step 2: Update project-context.md (cache)

Read the current file, then overwrite with content distilled from Step 1.5. Keep the
cache compact (~60 lines) — it summarizes the docs, it does not duplicate them. Add a
pointer line listing whichever docs you actually read, e.g.
`*Source: docs/ (PROJECT.md, ARCHITECTURE.md, CAPABILITIES.md) + HOW-TO-USE.md/CLAUDE.md. This file is a cache.*`.
Maintain this structure:

```markdown
# Project Context

*Last updated: {date}*

## Project
{project name} — {one-line description}

## Tech Stack
- **Language:** {e.g., Python 3.11}
- **Framework:** {e.g., FastAPI}
- **Database:** {e.g., PostgreSQL 15}
- **Testing:** {e.g., pytest + coverage}
- **Build:** {e.g., Docker + GitHub Actions}

## Architecture
{2-3 sentences describing the architecture}

## Key Dependencies
| Package | Version | Purpose |
|---------|---------|---------|
| {name} | {version} | {why it's needed} |

## Constraints
- {performance, compliance, API limits, etc.}

## Current Status
- **Phase:** {development/testing/production}
- **Priority:** {what's being worked on now}

## Open Questions
- {decisions pending, unknowns}
```

## Step 3: Record Decision (if applicable)

**NOT at session end.** Since T-015 `wrap-up` Step 4.5 puts decisions of record into
its own write plan instead of invoking this skill — a skill-body injection triggered
a full prefix-cache rewrite in 41% of measured cases (L34/D-010). This skill remains
the entry point for recording a decision *during* a session, and it is the sole owner
of `project-context.md` either way.

**Write path (write-path):** `scripts/apply_wrapup.py` is the single writer of
`decisions.json`. It continues the id format already on disk (`D-00n`, not the
`D{n}` this template used to claim), enforces append-only, and performs the
supersede flip:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/apply_wrapup.py" .agent-memory --session-id <sid> <<'PLAN'
{"date": "YYYY-MM-DD", "decisions": [ { ...one object per decision... } ]}
PLAN
```

Supply the content below; omit `id`, `date` and `status` — the script sets them.
A `supersedes` pointing at an unknown id rejects the whole plan rather than
silently creating an orphan.

For `architecture-decision` and `stack-change` types, the record shape is:

```json
{
  "id": "D{n}",
  "date": "YYYY-MM-DD",
  "type": "architecture-decision | stack-change | constraint-update",
  "title": "Short decision title",
  "status": "active | superseded | reverted",
  "context": "Why this decision was needed",
  "options_considered": [
    {"option": "Option A", "pros": ["..."], "cons": ["..."]},
    {"option": "Option B", "pros": ["..."], "cons": ["..."]}
  ],
  "decision": "What was chosen and why",
  "consequences": "Expected impact",
  "supersedes": null,
  "tags": ["architecture", "database"]
}
```

If this decision supersedes a previous one, set `supersedes: "<old id>"` — the
script flips the old entry's `status` to `"superseded"` (never deletes it) and
counts the supersession in its tally.

## Step 3.5: Wiki-ADR Writeback (if Wiki configured)

If `.agent-memory/config.json` exists and `sync_enabled: true`:

For `architecture-decision` type decisions, additionally write back to the Wiki:

| Decision Subtype | Wiki Target | Action |
|-----------------|-------------|--------|
| Runtime/status decision | Projekt-Entity in `wiki/entities/{project_id}.md` | Update Status/Timeline section |
| Systemic architecture decision | `wiki/synthesis/` or `wiki/topics/` | Append to matching page or create ADR section |

**Routing logic:**
1. Read `config.json` → get `wiki_root` and `project_id`
2. Classify: Is this a runtime/status change (e.g., "switch to v2", "deprecate feature X") or a systemic architecture decision (e.g., "use SQLite instead of PostgreSQL", "adopt event sourcing")?
3. Runtime → update the project entity's Status/Timeline section
4. Systemic → find the best matching synthesis/topic page and append, or add to the entity's Architecture section if no better target exists

**Guardrails:**
- Only the main agent writes (subagents skip this step)
- Wiki write failures do NOT block the context-keeper flow (warn + continue)
- Do NOT duplicate what `wrap-up` Step 7.5 / `obsidian-sync` already does — context-keeper writes the **decision itself**, wrap-up writes the **session summary**
- Do NOT create new Wiki pages here — only update existing ones

**Promotion marker (shared with obsidian-sync Step 4.5):** after a successful
wiki write, extend the decision's record in `decisions.json` with
`"wiki_ref": "{wiki-relative target path}"` and `"promoted_at": "YYYY-MM-DD"`.
obsidian-sync's batch Decision Promotion skips records that carry `wiki_ref` —
without the marker the same decision would be projected twice. Field extension
only; a failed/skipped wiki write must NOT set the marker.

## Step 4: Consistency Checks

After updating, verify:

1. **Contradiction check**: Does the new decision contradict any `active` decision in `decisions.json`? If yes, flag it and ask the user to resolve.

2. **Constraint check**: Does the new decision violate any listed constraint in `project-context.md`? If yes, flag it.

3. **Open questions**: Does this decision resolve any open question? If yes, remove it from the Open Questions section.

4. **Tag consistency**: Are the tags used consistent with existing tags in `decisions.json`? Reuse existing tags.

## Retrieval Mode

When the user asks "Why did we choose X?" or "What was the rationale for Y?":

1. Search `decisions.json` for matching entries (search `title`, `decision`, `tags`)
2. Filter to `status: "active"` entries (unless user asks about historical decisions)
3. Present the decision with its context, options considered, and consequences
4. If no match found, search the source of truth in order: the project docs
   (`docs/PROJECT.md`, `docs/ARCHITECTURE.md`, `docs/CAPABILITIES.md`) FIRST, then the
   `project-context.md` cache as a fallback. Report which source the answer came from.

## Step 5: Confirm

Output:

```
Context updated: {type} — {title}
  File: {which file(s) were updated}
  Decision: D{n} recorded (if applicable)

  Resolved questions: {if any}
  Warnings: {contradictions or constraint violations, if any}
```

## What NOT to Do

- Do NOT delete entries from decisions.json (set status to "superseded" instead)
- Do NOT create decisions.json records with Write/Edit — new records go through
  `apply_wrapup.py` (Step 3 write-path). The Step 3.5 marker (`wiki_ref` +
  `promoted_at` on an EXISTING record) stays a direct field extension: it happens
  after a successful wiki write, outside any write plan
- Do NOT make project-context.md longer than ~60 lines
- Do NOT record trivial decisions (choosing a variable name is not an ADR)
- Do NOT modify files outside of context/ directory — sole exception: the Step 3.5
  wiki writeback (existing wiki pages only, plus its wiki_ref/promoted_at marker
  write-back into decisions.json)
- Do NOT guess the tech stack — read actual config files or ask the user

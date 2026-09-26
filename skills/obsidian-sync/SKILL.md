---
name: obsidian-sync
description: >
  Syncs session results from .agent-memory/ (RAM) into the Obsidian wiki
  (Brain): session note, project-entity updates, decision promotion, rolling
  synthesis. Main agent only. Use at session end (invoked by wrap-up) or on
  "sync to wiki" / "obsidian sync".
model: sonnet
effort: medium
metadata:
  author: agentic-os
  version: '1.5'
  part-of: agentic-os
  layer: core
---

# Obsidian Sync

Sync session results from .agent-memory/ (RAM) into ~/wiki/ (Brain).

## When to Use

- At session end via wrap-up Step 7.5 (auto-triggered, conditional)
- Manually when user says "sync to wiki", "obsidian sync", etc.
- After a productive session with >= 2 iterations or meaningful learnings

## When NOT to Use

- Trivial sessions (< 2 iterations, no meaningful outcome)
- When sync_enabled is false in config.json
- From subagents (only the main agent writes to wiki)

## Prerequisites

- `.agent-memory/config.json` must exist with `wiki_root` and `sync_enabled: true`
- The wiki at `wiki_root` must have a valid `CLAUDE.md` file
- `.agent-memory/session-summary.md` should have current session data

## Step 0: Pre-Run Commit (backup light)

Sync consolidates syntheses and updates sync-state in the store. Snapshot it before
writing: run the shared procedure `${CLAUDE_PLUGIN_ROOT}/references/pre-run-commit.md`
with commit message `chore(memory): pre-run snapshot vor obsidian-sync`. The wiki side
needs no extra snapshot — it is its own git repo; never commit foreign wiki drift.

## Step 1: Read Config and Validate

Read `.agent-memory/config.json`:
- Extract `wiki_root`, `project_id`, `sync_enabled`
- If `sync_enabled` is false → output "Wiki sync disabled for this project." and stop.
- If config.json does not exist → output "No wiki config found. Run /agentic-os:init to set up." and stop.

Validate wiki connection:
- Check if `$WIKI_ROOT/CLAUDE.md` exists
- If not → warn "Wiki not found at configured path" and stop.

## Step 2: Gather Session Data

Read from .agent-memory/:
1. `session-summary.md` — current session summary
2. `iterations/iteration-log.md` — today's iterations (filter by today's date)
3. `learnings/learnings.json` OR `learnings/learnings.md` — new learnings from this session (check .json first, fall back to .md; parse .md as bullet list if no .json exists)
4. `patterns/patterns.json` OR `patterns/patterns.md` — patterns with updated confidence (check .json first, fall back to .md)
5. `context/decisions.json` — new decisions from this session (skip if not found)

**Substantiality gate (wiki-sync-gate):** the session must be substantial — ANY of:
>= 1 iteration logged today, OR today's commits exist (`git log --oneline --since=midnight`),
OR a meaningful learning (importance >= 4), OR a new decision landed. A single real
iteration or any commit already warrants a note. This gate is OWNED here — wrap-up
Step 7.5 delegates the substantiality decision to this step and never restates the rule.
If none hold → output "Wiki-Sync: übersprungen — Session nicht substanziell." and stop.

## Step 3: Create Session Note

**This step ALWAYS runs first when sync proceeds.**

Create a new file in `$WIKI_ROOT/wiki/queries/` following the session-note template:

Filename: `YYYY-MM-DD-session-{project_id}-{kebab-case-summary}.md`

Frontmatter fields:
```yaml
---
type: query
status: active
created: YYYY-MM-DD
updated: YYYY-MM-DD
source_count: 0
tags:
  - session
  - agent-generated
aliases: []
query_kind: session
question: Was wurde in dieser Session erarbeitet?
scope: []
project: {project_id}
agent: claude-code
iterations: {count}
quality_delta: {delta or null}
patterns_found: {count}
authority: derived
---
```

`query_kind: session` is required, and `agent` must be a canonical value of the wiki schema
(`claude-code`, `codex`, `chatgpt`, `notebooklm`, `unknown`) — never a variant such as
`claude-code-headless` (mark such details as a tag). The wiki's frontmatter gate rejects anything
else, and one invalid note blocks the wiki's nightly derived refresh.

Body: Fill in Kontext, Was wurde gemacht, Entscheidungen, Erkenntnisse, Offene Fragen, Beruehrte Seiten sections from session data.

Link to relevant wiki pages using `[[wiki/...]]` format.

## Step 4: Update Project Entity (conditional)

**Only if** new decisions exist with type `status-change` or `runtime-decision`:

1. Find the project entity at `$WIKI_ROOT/wiki/entities/{project_id}.md`
   - Resolution order: project_id → project_aliases → filename grep
2. If entity exists: append status/decision update to the entity's relevant section
3. If entity does not exist: skip (do NOT create entities in this step)

**Do NOT update entity for:** architecture-decisions — their live writeback is
context-keeper Step 3.5; the batch projection is Step 4.5 below.

## Step 4.5: Decision Promotion (conditional)

Project decisions live in `context/decisions.json` — the **leading** store, owned
by context-keeper. The wiki only ever receives a projection, never the other way
around. This step batch-promotes architecture-relevant decisions that have no
wiki projection yet.

**Candidates:** every decision in `context/decisions.json` with ALL of:
- `type` in {`architecture-decision`, `stack-change`}
- `status: "active"`
- no `wiki_ref` field (the promotion marker; records that context-keeper
  Step 3.5 already wrote live carry it and are skipped here)

No candidates → skip silently, continue with Step 5.

**Target:** `$WIKI_ROOT/wiki/entities/{project_id}.md`, section
`## Architecture Decisions`.

**Duplicate guard (before every append):** check the target section for an
existing `### {id}:` heading. If present, the block was already written — e.g.
by a prior run whose marker write-back failed. Do NOT append again; only set
the missing marker in decisions.json (self-healing closes the
wiki-written-but-unmarked window).
- Entity exists → append to that section (create the section if missing).
- Entity missing → create a **minimal** entity page: frontmatter (`type: entity`,
  `status: active`, `created`/`updated`, `source_count: 0`,
  `tags: [project, agent-generated]`, `aliases`), a one-line `What it is`, then
  the decisions section. This is the single entity-creation exception to
  Step 4's "do NOT create entities" — report it in the output.

**Projection format** (compact; the full record with options_considered stays
in decisions.json):

```
### {id}: {title}
*{date} · {type} · Status: {status}*
- **Entscheidung:** {decision, condensed to 1-2 sentences}
- **Konsequenz:** {consequences, condensed to 1 sentence}
- Quelle (fuehrend): `{project_root}/.agent-memory/context/decisions.json` → {id}
```

**Write-back marker (idempotency):** after each successful wiki write, extend
the promoted record in decisions.json with:
- `"wiki_ref": "wiki/entities/{project_id}.md"`
- `"promoted_at": "YYYY-MM-DD"`

Field extension only — never restructure the record. Consumers must tolerate
records with and without these fields.

**Supersede care:** if a candidate has `supersedes: "D{x}"` and D{x} carries a
`wiki_ref`, append `— superseded by {id} ({date})` to D{x}'s status line in the
wiki block instead of deleting it (wiki rule: mark, never silently delete).

**Do NOT promote:** `scope-decision`, `constraint-update`, or runtime/status
decisions — those follow Step 4 / context-keeper routing. Never write from a
subagent; never edit wiki history.

## Step 5: Update Rolling Synthesis (conditional)

**Only if** new learnings exist with importance >= 4:

1. Read `$WIKI_ROOT/wiki/synthesis/agent-learnings-aktuell.md`
   - If it does not exist: create it with sections `## Pattern-basiert`, `## Tooling`, `## Architektur`
2. For each learning with importance >= 4:
   - Determine which existing main section (`## ...`) it belongs to
   - Check for duplicates (Jaccard similarity >= 0.6 on key terms)
   - If not duplicate: insert it as one concise bullet INSIDE that section (with learning id and
     project reference). Never add a new `## ...` section for it — no "Nachtrag"/addendum/dated
     sections at the end of the file.
3. Respect the wiki's rolling caps (checked by the wiki's `scripts/session_distillation.py`):
   at most 5 main sections, at most 15 bullets per section, at most 200 lines.
   - Section already at 15 bullets: merge the new learning into the closest existing bullet, or
     skip it and report "rolling section full — merge in the weekly consolidation"; never exceed.
   - Do NOT auto-offload to an archive page (that is the manual weekly consolidation).
4. If an in-place Edit of the page is blocked (e.g. a PreToolUse hook reacts to quoted code in the
   learning text): rewrite the whole page with the Write tool, keeping all existing content, or
   skip the learning and report it. Appending a new section as a workaround is not allowed.

## Step 6: Update Pattern Promotion Status (conditional)

**Only update patterns.json, NO wiki writes in this step:**

For each pattern in patterns.json:
- If confidence 0.70–0.84 → set `"promotion_status": "candidate"` if not already set
- If confidence >= 0.85 AND (occurrences >= 2 OR source_projects >= 2) → set `"promotion_status": "ready"`
- If confidence >= 0.85 BUT single project/single occurrence → set `"promotion_status": "candidate"`

**Scope gate:** whenever `promotion_status` is set
to `"ready"`, also record how far the pattern is allowed to travel — frequency alone does
not prove transferability:
- If source_projects >= 2 → set `"promotion_scope": "global"` (evidence spans projects)
- If ready via occurrences within a single project → set `"promotion_scope": "project"`

Whoever later promotes a ready pattern into the wiki (migration or manual) MUST respect
the scope: a `"promotion_scope": "project"` pattern becomes a project-bound note with an
explicit scope remark, never a general concept page.

**Do NOT create Concept pages from patterns.** That happens during migration (Sprint 4+) or manually.

## Step 7: Update Index and Log

### index.md
Read `$WIKI_ROOT/index.md`. Add the new session-note under the "Session Notes" or "Queries" section:
```
- [YYYY-MM-DD Session: summary](wiki/queries/YYYY-MM-DD-session-{project_id}-summary.md) — project, {n} iterations
```

### log.md
Append to `$WIKI_ROOT/log.md`:
```
## [YYYY-MM-DD] agent-sync | {project_id}

- Session-Note: wiki/queries/YYYY-MM-DD-session-{project_id}-summary.md
- Entity updated: {yes/no}
- Decisions promoted: {count} (entity created: {yes/no})
- Learnings promoted: {count}
- Patterns flagged: {count candidates, count ready}
- Total pages touched: {n}
```

## Output (wiki-sync-visible)

Always report the outcome — both success and skip cases must be visible to the user; this
skill never returns silently (a silent skip is what made the auto-sync feel broken). On
success:
```
Wiki sync complete:
  Session-Note: wiki/queries/YYYY-MM-DD-session-...
  Entity updated: {yes/no}
  Decisions promoted: {count}
  Learnings promoted: {count}
  Patterns: {count} candidates, {count} ready
  Pages touched: {total}
```
On a skip (disabled / no config / not substantial), output the one-line reason from
Step 1 or Step 2 instead — never nothing.

## Error Handling

- **Any step fails:** Warn user, continue with remaining steps. Never abort wrap-up.
- **Wiki path unreachable:** Warn and skip all wiki writes. Still update patterns.json locally.
- **Frontmatter parse error:** Warn, write file without updating existing frontmatter.
- **index.md/log.md not found:** Create minimal versions and continue.

## Guards

- Only the **main agent** may invoke this skill. Subagents must NOT call obsidian-sync.
- This skill writes to `~/wiki/` which is OUTSIDE the current project directory. This is intentional and expected.
- Never delete or overwrite existing wiki pages. Only append or create new.
- All wiki writes use `[[wiki/...]]` link format per the wiki's CLAUDE.md conventions.

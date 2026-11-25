---
name: session-bootstrap
description: >
  Bootstraps project context at session start: central handoff, open tasks,
  key learnings and health checks, condensed into a compact briefing.
  Use at the start of a coding session ("start session", "new session") or
  when the user asks where things stand ("where were we", "project status").
model: sonnet
effort: low
metadata:
  author: agentic-os
  version: '4.0'
  part-of: agentic-os
  layer: core
---

# Session Bootstrap

Restore full project context on request. The SessionStart hook already injects the short briefing
(branch, top open tasks, handoff head, RECOVERY, THRESHOLD and PFLEGELAUF lines); this skill adds what the
hook does not deliver: the cross-project status, key learnings, wiki context and the Sharepoint pull.

**When to use:** on request at the start of a session, after a context-window reset or a long pause, when the
user asks "Where were we?" / "Project status?", or after an agent switch (Claude Code <-> other).

## Step 0.5: Cross-Project Handoff (read-only)

Read TWO cross-project files before the local state. Both are **read-only** here — the write side lives in `wrap-up`.

1. **Central handoff:** `~/AI/.agent-memory/session-summary.md`. A STACKED file: the most recent
   session is at the TOP (possibly from another project), older blocks below are history — scan only if needed.
2. **Workflow file:** `~/AI/SESSION-WORKFLOW.md` **only if** the central handoff references it or
   this is the first session in a new project.
3. **Status board:** from `~/AI/cross-project-status.md` load ONLY two sections — never the full
   file (it grows with every project): Grep with context (`-A 6`) for (i) `## {current project}` and (ii)
   `## Cross-Project Notes`.

**Rules:**
- The central handoff is authoritative for "what happened last", the status board for "where each project stands".
- If the central handoff is **newer** than the local `.agent-memory/session-summary.md`, it takes precedence for
  the LAST SESSION block. If it names a **different project**, say so ("Last handoff was from project X — switching context to Y").
- **Next-steps ownership (open-tasks-priority):** the central handoff's "Naechste Schritte" is a POINTER, not a
  list. This project's next steps come from the local `context/open-tasks.json`; from the central handoff take
  ONLY items prefixed `[cross-project]`. Never harvest next steps from stacked history blocks.
- A missing or unreadable file → skip THAT file silently and continue. Never modify either file.

## Step 1: Check Memory System Exists

Read `.agent-memory/session-summary.md`:

- If `.agent-memory/` does not exist → the SessionStart hook creates it only in a git repo (or in any folder when the plugin option `auto_init_non_git` is on). Otherwise output "Memory system not found. The SessionStart hook creates a store only in git repos; run /agentic-os:init here to create one." and stop.
- If it exists but `session-summary.md` is missing → note "No previous session found", continue with other files.

**Authority** (detail in `references/bootstrap-wrapup-rationale.md`): central handoff = "what happened last";
status board = per-project state; local session-summary = "where THIS project stands". All three merge in Step 4.

## Step 1.5: Deterministic Preflight + Fast Path (bootstrap-fast-path)

Run the stage-0 preprocessor:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/preprocess_state.py" .agent-memory
```

*(No `--session-id` here on purpose: the fast path must see unconsolidated work from ANY session.)*

FAST PATH: if `previous_state_hash == current_state_hash` (both non-empty)
AND `changed_files` is empty, the memory store is unchanged since the last
consolidated wrap-up. Then load ONLY `session-summary.md` and
`context/open-tasks.json`, skip the full knowledge load of Step 2, and state
in the briefing: "Memory unchanged since last session — briefing served from
existing state." Health checks (Step 3) still run in both paths.

Otherwise continue with Step 2, preferring the files named in `changed_files` and surfacing
`validation_errors` / `threshold_events` from the preprocess output instead of re-deriving them.

## Step 2: Load Knowledge Files

Read in this order, skip any that don't exist:

1. **`session-summary.md`** — this project's last session state (complements the central handoff)
2. **`identity/user.md`** — user preferences and work style (see central profile below)
3. **`context/project-context.md`** — tech stack, architecture, constraints
4. Learnings — via the retrieval below (do NOT full-read learnings.json)
5. **`working/current-session.json`** — if it exists, resume working memory from an interrupted session
6. **`context/open-tasks.json`** — open/blocked tasks (SSoT for next steps; feeds Step 5)

**Central profile (central-profile-read).** When `~/.agent-memory/identity/profile.md` exists (or the one under
`$AGENTIC_OS_IDENTITY_STORE`), the central profile is active: item 2 then reads the projection
`identity/profile-briefing.txt` next to it (at most 6 lines) instead of the project's `user.md`. Open the full
`profile.md` only when a question really needs it. The candidate queues are never read by the bootstrap.

### Learnings Retrieval (RAG-Hybrid)

**Primary path — Atlas MCP (when available).** Load the deferred tool with EXACTLY ONE
`ToolSearch("select:mcp__agent-memory-atlas__memory_search_tool")` per run, covering ALL Atlas queries of this run
(each extra ToolSearch rewrites the prefix cache, L34/T-017).

1. Build a query from `context/open-tasks.json`: the titles of the top 3 open/blocked tasks
   (fallback: project name + stack keywords).
2. Call `mcp__agent-memory-atlas__memory_search_tool` with `query`, `top_k: 5`, `source_system: "agent-memory"`.
3. Drop entries whose `superseded_by` is not null; show the ≤5 results as KEY LEARNINGS, header prefixed `(RAG)`.
4. On any MCP error or an empty result → fall through to the heuristic fallback.
5. **Codex gotchas:** a SECOND query with the same text, `source_system: "codex-agents"`, `top_k: 3`. Non-empty →
   `CODEX GOTCHAS (RAG)` section (one line per hit: AGENTS.md path + snippet). Empty or error → omit silently.

**Fallback path — heuristic rank.** Do NOT read learnings.json into context. Run the deterministic ranking and use
its ≤10 lines directly, header prefixed `(heuristic fallback)` (formula: `references/bootstrap-wrapup-rationale.md`):

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/learnings_top.py" .agent-memory/learnings/learnings.json --top 10 --tags {stack-keywords,comma-separated}
```

Without python: read only the LAST 15 entries of learnings.json and pick by importance.

**Staleness wrap (staleness-wrap) — display only, never a write.** Annotate entries with
`now − last_relevant > 90 days` as `[STALE? last relevant {date}] {text}`.
Do NOT decay/write confidence or last_relevant — that is /agentic-os:maintain's job;
bootstrap is strictly read-only.

## Step 2.5: Wiki Context Loading (optional)

**Gate:** Read `.agent-memory/config.json`. If it does not exist or
`sync_enabled` is false → **skip this step silently** (no error, no warning).

**Contract (when enabled):** resolve the project's wiki entity (via `project_id` / `project_aliases`) and load
**at most 5 pages total** — entity (≤80 lines) + existing `default_entrypoints` + last 3 session notes + optional
rolling synthesis. No brute-force vault search. Add a `WIKI CONTEXT` block to the briefing; if the wiki is
unreachable, omit the block silently. Optional cross-vault enrichment: Atlas `memory_search_tool`
(`scope: "project:agent-lab"`, `top_k: 2`) with the Step-2 query; a hit adds `Ideas: {t1}; {t2} (agent-lab)`.
Resolution order, line limits and layout: `references/bootstrap-procedures.md` (§Wiki Context Loading), loaded
only when this step runs.

## Step 3: Health Checks

- **Files:** `session-summary.md`, `identity/user.md`, `context/project-context.md`,
  `iterations/iteration-log.md` should exist. Missing → warn, suggest the skill or command that creates them.
- **JSON validity:** for each JSON file loaded: if parse fails → warn the user with file and parse error and suggest `/agentic-os:maintain` (Step 4 is the only repair path). Never rename, recreate or empty the file here — the bootstrap is read-only, and a UTF-8 BOM is not corruption (read as `utf-8-sig`).
- **Scaling guards and Pflegelauf:** run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/memory-thresholds.sh" .agent-memory`.
  Exit 10 → include its `THRESHOLD:` and `PFLEGELAUF:` lines under HEALTH (a PFLEGELAUF line means
  `/agentic-os:maintain` is due). Exit 0 → nothing to report. The numbers live ONLY in that script.

### Recovery Detection (recovery-detect) — read-only

Detect sessions that ended WITHOUT consolidation (crash, closed window, skipped wrap-up). Sources:
`working/dirty-*.json` (PostToolUse dirty-tracker) and `consolidation-marker.json` (written last by `wrapup_core.py apply`).

1. List `working/dirty-*.json` with `dirty: true`.
2. Ignore files whose `updated` is younger than **30 minutes** (most likely a session running in parallel right
   now). Exception: a `session_end` block whose `ended_at` is at or after the last activity (`updated`,
   `foreign_updated`) proves the session ended — flag it regardless of age (same rule as the hook and the night run).
3. Every remaining dirty file is an **un-consolidated session**: extract `session_id` (short), `updated`,
   `write_count`, up to 3 `touched_files`, and `agent` if present (`"codex"` marks a standalone Codex session).
4. If the marker's `last_wrapup` is NEWER than the dirty file's `updated`, downgrade to a one-line note (probably
   consolidated by a later wrap-up; the flag survived a crash between edit and marker).
4b. **Tail-write downgrade:** if the dirty file carries `last_consolidated_at` AND
   `writes_since_consolidation <= 5` AND `updated` is within **120 minutes** of `last_consolidated_at` (DCO-9209)
   AND the `session_id` appears in the marker's `consolidated_sessions` AND `last_consolidated_by` is not
   `"headless"`, downgrade to a one-line note: the flag was re-set by the wrap-up's own post-marker writes. A
   headless stamp has no tail — every write after it is real work. The note still names count and files
   (`{n} Writes nach Konsolidierung, z.B. {file}`) — downgraded, never silent. More writes or a larger gap → keep
   the full RECOVERY block.
5. Output a RECOVERY block in the briefing (Step 4) and recommend the fix:

```
RECOVERY
  Unkonsolidierte {Codex-|}Session {sid-8} vom {updated}: {write_count} Writes,
  z.B. {file1}, {file2}
  → Empfehlung: wrap-up ausführen (harvestet aus touched_files + git)
```

Strictly read-only: bootstrap never resets dirty flags, deletes dirty files or writes the marker — that is
`wrapup_core.py`'s job. Declined recovery simply re-reports next session.

## Step 3.5: Sharepoint-Pull (Cross-Device)

If the Google-Drive Sharepoint is mounted (`G:\Meine Ablage\dynamic-AI\dynamic_sharepoint`), run a read-only
pull-check and fold the finding (conflict files, changed files, open handoffs, index drift) into 1-3 lines:

```
powershell -File "${CLAUDE_PLUGIN_ROOT}/skills/session-bootstrap/scripts/sharepoint-pull-check.ps1" -Since "<last-session-timestamp>"
```

`-Since` = timestamp of the last `session-summary.md` (omit for the last 3 days).
- **Conflict-files found** → STOP, point to `00_INBOX/_conflicts/`, do NOT read them as truth.
- **Path not mounted** (script says "STOP: Sharepoint-Pfad nicht gefunden") → skip silently.

## Step 4: Produce Briefing

```
SESSION BRIEFING — {project name}
{date}
---
HANDOFF (central)
  {Agent}: {project} — {date}
  {1-2 lines: what was done, key outcome; context switch note if another project}
  {omit if the central handoff doesn't exist or is older than the local summary}
CROSS-PROJECT (status board)
  This project: {1-line state from its section}
  Notes: {Cross-Project Notes relevant for ALL projects}
LAST SESSION (this project)
  {2-3 lines from the local session-summary.md}
  Next steps: {open/blocked items from context/open-tasks.json; fallback: summary "Next Steps"}
PROJECT STATUS
  {1-2 lines from project-context.md} · Stack: {compact}
KEY LEARNINGS (top 5 via RAG · fallback: top 10 heuristic)
  {(RAG) or (heuristic fallback) results — [ID] importance text}
CODEX GOTCHAS (RAG)
  {up to 3 codex-agents hits; omit if empty}
ACTIVE WARNINGS
  {at most 3 entries of `## Active Warnings` in the local session-summary.md}
HEALTH
  {missing files, THRESHOLD and PFLEGELAUF lines}
RECOVERY
  {un-consolidated sessions with wrap-up recommendation; omit if nothing was flagged}
---
```

**Keep the briefing under 15 lines.** Omit empty blocks. Do NOT dump file contents — summarize.
`errors.json` and `patterns/` are frozen data since 6.0.0 and are not read.

## Step 5: Recommend Next Steps

```
RECOMMENDED NEXT STEPS
  1. {open/blocked tasks from context/open-tasks.json — highest priority first}
  2. {[cross-project] items from the central handoff that apply to this project}
  3. {from active warnings, a due Pflegelauf, or recovery}

  Ready — was steht heute an?
```

Local `context/open-tasks.json` comes first, then `[cross-project]` items, then system warnings. Deduplicate: an
item both local and central shows ONCE (local wording wins).

## What NOT to Do

- Do NOT write to any files yourself: no gate, no queue, no exception (`soul.md` and the identity candidate
  queues are read-only history; there is no identity question at start)
- Do NOT dump full file contents to the user
- Do NOT scan skill registries or build context matrices (Claude already knows available skills)
- Do NOT estimate token budgets (unreliable, leads to false warnings)

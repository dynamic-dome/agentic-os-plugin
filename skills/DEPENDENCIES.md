# Skill Dependency Graph — Agentic OS v6

> Reflects v6.0.0. The local store schema is owned by `scripts/mem-schema.sh`
> (see `references/memory-structure.md`). The decommissioned **global** layer (sync-context,
> global-schema.sh, global_decay.py, migrate-global-schema-4A.sh) lives in `_archived/` since 5.11.0.
> All scaling/archiving threshold NUMBERS live in `scripts/memory-thresholds.sh`
> (threshold SSoT — read by the SessionStart hook, session-bootstrap Step 3 and wrapup_core.py `pre`; its
> PFLEGELAUF line in the SessionStart briefing triggers /agentic-os:maintain, which does not run it).
> When this graph disagrees with a skill's own SKILL.md, the SKILL.md wins.

## Session Lifecycle (Execution Order)

```
SESSION START (SessionStart hook → session-bootstrap)
  │
  ▼
  session-bootstrap (READ-ONLY)
  │  ├── cross-project (read-only): ~/AI/.agent-memory/session-summary.md,
  │  │     ~/AI/cross-project-status.md, ~/AI/SESSION-WORKFLOW.md (conditional)
  │  ├── local: session-summary.md, identity/user.md (or the central profile projection),
  │  │     context/project-context.md,
  │  │     working/current-session.json, context/open-tasks.json (SSoT for next steps)
  │  ├── learnings: RAG via Atlas MCP, fallback scripts/learnings_top.py
  │  │     (deterministic salience ranking — never full-reads learnings.json)
  │  ├── health: bash scripts/memory-thresholds.sh (exit 10 → THRESHOLD / PFLEGELAUF lines)
  │  └── wiki (optional, if config.json sync_enabled): entity + entrypoints (≤5 pages)
  │
  ▼
WORK PHASE (user-driven, no auto-triggers on code changes)
  │
  ├── /agentic-os:log (command, mid-session, on request)
  │     └── writes via scripts/apply_wrapup.py (`iterations` plan section — never by hand):
  │           iteration-log.md, working/current-session.json (errors.json: frozen data, 6.0.0)
  │           (append-only; never rotated — memory-thresholds.sh only reports growth, D-021)
  │
  ├── context-keeper (on architecture/stack decisions)
  │     ├── reads: docs/PROJECT.md+ARCHITECTURE.md+CAPABILITIES.md (SoT, Step 1.5),
  │     │     context/project-context.md, context/decisions.json, config.json
  │     ├── writes: context/project-context.md (cache), context/decisions.json
  │     └── writes (optional, Step 3.5 if sync_enabled): ~/wiki/wiki/entities/<id>.md
  │
  └── obsidian-sync (wiki write-path; also mid-session on request)
        └── see matrix — session notes, entity updates, synthesis, decision promotion
  │
  ▼
SESSION END (manual: /agentic-os:wrap-up — no hook can trigger it; a skipped
             wrap-up shows as a RECOVERY line at the next SessionStart)
  │
  ▼
  wrap-up (MANUAL-ONLY — invoke as slash command; that path applies model: sonnet)
  │  Judge since T-028b: three turns, no skill invoked. scripts/wrapup_core.py does the rest.
  │  ├── pre (read-only) → brief: harvested iterations, open tasks (preprocess_state.py),
  │  │     open bridge candidates, rule queue, Pflegelauf due (memory-thresholds.sh)
  │  ├── apply (ONE plan: summary required) → apply_wrapup.py owns every store
  │  │     write: session-harvest (`iterations`, harvest when the plan has none),
  │  │     decision-scan (`decisions`), learnings (cap 3, rule-class exempt), open tasks;
  │  │     old sections (identity, user_candidates, iteration errors) are ignored and counted
  │  │     (tally.ignored_sections, 6.0.0); NEVER writes soul.md
  │  │     → then ingest_codex_memory.py, session-summary.md,
  │  │     cross-project ~/AI/.agent-memory/session-summary.md (prepend handoff) and
  │  │     ~/AI/cross-project-status.md (template: references/handoff-template.md), session note
  │  │     as a projection of the plan, both projections,
  │  │     consolidation marker LAST, cost trace, state hash
  │  ├── report → verbatim lines from the result (wiki-sync-visible, ignored sections);
  │  │     recommends /agentic-os:maintain, invokes neither
  │  └── followup (only when needed) → bridge_decisions and other verdicts that need ids
  │        from apply, user_md_supersede / user_md_retire (quote verified by identity_quote.py
  │        against the session transcript, read-only); both projections rerun. Full rules: references/judge-rules.md
```

## Dependency Matrix

| Skill | Reads From | Writes To | Invokes |
|-------|-----------|-----------|---------|
| session-bootstrap | local: session-summary.md, user.md (or the central `profile-briefing.txt`), project-context.md, working/current-session.json, context/open-tasks.json (SSoT for next steps), config.json · learnings via Atlas-RAG or `scripts/learnings_top.py` · health via `scripts/memory-thresholds.sh` · cross-project: ~/AI/.agent-memory/session-summary.md, cross-project-status.md, SESSION-WORKFLOW.md · wiki (optional): entity + entrypoints | (read-only, no exception since 6.0.0: the user candidate gate is gone). soul.md and soul-candidates.md are not read (history since 5.11.0). Staleness display (`[STALE? …]`) is DISPLAY-ONLY, never a write. | — |
| context-keeper | docs/PROJECT.md+ARCHITECTURE.md+CAPABILITIES.md (SoT), project-context.md, decisions.json, config.json | project-context.md (cache, own write), decisions.json — new records **only through `scripts/apply_wrapup.py`** (`decisions` plan section; Step 3.5 wiki_ref/promoted_at stays a direct field extension), ~/wiki/wiki/entities/<id>.md (optional) | — |
| wrap-up | iteration-log.md, learnings.json, working/current-session.json, context/open-tasks.json, skills/wrap-up/references/handoff-template.md, the session's own transcript (read ONLY by `scripts/identity_quote.py`, to verify the quote of a user_md_supersede / user_md_retire — never by the model) | session-summary.md, learnings.json + learnings.md, user.md (only user_md_supersede / user_md_retire in followup, changelog first; no harvest since 6.0.0), user-changelog.json (audit), working/current-session.json (reset), context/open-tasks.json (Step 5.5 SSoT); cross-project handoff (max 1 block per project, next steps as pointer + `[cross-project]` only) + status-board + Sharepoint | — (obsidian-sync stays manual since T-028b). Step 9 only recommends /agentic-os:maintain. Step 1.5 session-harvest and Step 4.5 decision-scan route through the write plan instead of invoking iteration-logger/context-keeper (T-015) |
| obsidian-sync | config.json, session-summary.md, iteration-log.md, learnings.json/.md, decisions.json, ~/wiki/{index.md,log.md,entities,synthesis} | ~/wiki/wiki/queries/*.md, ~/wiki/{index.md,log.md}, entity + synthesis (append), decisions.json (`wiki_ref` / `promoted_at` marker only) | — |

### Commands with a script core (v5.0.0)

| Command | Script core | Writes |
|---|---|---|
| /agentic-os:maintain | review_sweep.py --candidates-json, learnings_lifecycle.py propose --skip-reviewed / report, Atlas MCP (memory_search_tool / memory_related_tool, scoped to the project; without Atlas no pair search), working/pflegelauf.json, rule_catalog + held_rules (~/.claude/agentic-os.local.json), identity_store.py check | Pflegelauf: the agent's verdicts (doppelt, überholt, gegenstandslos, verwandt, global, bleibt) in chunks of 30 subjects, one plan each through apply_wrapup.py, --snapshot on the first chunk only (the run's rollback point) (`learning_updates`, `principles`, rule-class `learnings`), `pflegelauf_stamp` only in the final plan of a complete run, then both projections once; a non-critical global rule edited into a rule_catalog file (outside the store); repaired JSON (owner-confirmed, Step 4, the only owner step). Never deletes, archives or rewrites a learning (D-021) |
| /agentic-os:log | apply_wrapup.py (`iterations` section) | iteration-log.md, working/current-session.json |

## Agents

| Agent | Used By | Reads / Writes | Purpose |
|-------|---------|----------------|---------|
| context-detective | /agentic-os:init (optional) | reads manifests + docs/ (docs-first); writes project-context.md | Auto-detect project stack |

## Skills (v6.0.0)

4 active skills, grouped by layer (see `references/skill-template.md` Layer Guide):

| Layer | Skills | Note |
|-------|--------|------|
| core | session-bootstrap, wrap-up, context-keeper | session lifecycle + decisions of record |
| knowledge | obsidian-sync | write-path to the Obsidian wiki |

### Converted to commands / archived in v5.0.0

- **memory-maintenance → /agentic-os:maintain** — 12 KB body for mechanics that never ran; the scripts own the writes, the command wraps them.
- **iteration-logger → /agentic-os:log** — 70 % mechanics already in `apply_wrapup.py`; no usage evidence as a skill.
- **sync-context → /agentic-os:sync-context** — became a command in v5.0.0; **archived in 5.11.0** together with the decommissioned global layer (`_archived/`, `STILLGELEGT.md` since 2026-10-05). The remaining commands `maintain` and `log` keep `disable-model-invocation: true` (a command file is otherwise Skill-tool-resolvable), enforced by the invocation-contract test.
- **self-improve, /rollback, /auto-commit, improvements/ → `_archived/`** — silent since 2026-06-21; reversible via `git mv` (see `_archived/README.md`).

### Removed in 6.0.0 (with reason)

Code is removed, store data never: every file listed here stays in existing stores, untouched.

- **Pattern mechanics** (the pattern skill, its extractor script, the pattern hint and count in the start hook, pattern fields in bootstrap/maintain/status, the pattern step of obsidian-sync) — the extraction produced nothing the sessions used; `patterns/patterns.json` and `patterns.md` stay in existing stores as data, no feature reads or writes them, and new stores create neither `patterns/` nor `generated-skills/`.
- **Error log** (`errors` inside an iteration → `iterations/errors.json`) — its only consumer was the pattern mechanics; the file is frozen data, an old `errors` field is ignored and counted.
- **Identity harvest** (candidate queue growth, promotion into user.md, the bootstrap gates) — it served neither reachable learnings nor re-entry; the profile and its six briefing lines stay read-only, the queues stay as files.
- **Restore of archived rows** (the read-only restore planner and maintain Step 5b; a plan with `restore` is refused), the one-time backfill and identity-migration scripts, the cost-trace shell helper and the escalation markers (no reader), and the memory audit command (its report is part of the Pflegelauf).

### Removed in v4.0.0 (with reason)

- **retrospective** — trend metrics never drove a decision; the read-only report duplicated what the status command and patterns.md already surface.
- **research-pipeline** — external research is better served by user-level skills (Perplexity/NotebookLM CLI); the plugin wrapper added indirection, not value.
- **wiki-query** — a plain wiki lookup needs no skill; the wiki MCP / direct Read covers it without a trigger-phrase surface.
- **quality-gate** (skill + agent) — review/test/TDD enforcement moved to user-level skills and the test suite itself; the in-plugin score pipeline had no consumer.
- **skill-generator** — not deleted but folded into the pattern skill (itself removed in 6.0.0, see above).
- Wrapper commands **/log, /patterns, /research, /sync, /run-loop** — thin wrappers around directly-invocable skills (and shadow-risk, L17). Commands now (v6.0.0): init, status, maintain, log — `log` returned in v5.0.0 as a real command with a script core, not a wrapper.

Removed agents: `improvement-scout`, `fix-reviewer` (2026-04-30); `improvement-agent`, `research-agent` (4.15.0).

## Key Design Principles

1. **No circular dependencies** — DAG only.
2. **No auto-triggers on code changes** — user/CLAUDE.md driven (the only hook-driven skill is session-bootstrap on start; wrap-up is a manual slash command).
3. **session-bootstrap is read-only** — never writes during startup. The soul gate is retired (5.11.0, soul.md is read-only history), the user-candidate gate too (6.0.0, no harvest).
4. **Skills that invoke other skills:** none since T-028b. `wrap-up` is a judge over `scripts/wrapup_core.py`: it recommends /agentic-os:maintain and leaves obsidian-sync to the user. All skills are leaf nodes.
5. **No cross-project auto-sync** — the global layer and its manual command sync-context are archived (5.11.0); nothing syncs between projects.
6. **docs/ is the source of truth for project-context.md** — context-keeper (and context-detective, /init) read docs first; project-context.md is a cache (Regel 13 / L9).
7. **Thresholds live in ONE script** — `scripts/memory-thresholds.sh` is the only place scaling numbers exist; skills and commands reference it, never restate the numbers.
8. **Identity is frozen (6.0.0)** — no harvest: nothing enqueues or promotes. The profile changes only through `user_md_supersede` / `user_md_retire` (verified quote, changelog first) and `identity_expire` marks old queue rows; old `identity` / `user_candidates` plan sections are ignored and counted. **Central profile (P2):** while `~/.agent-memory/identity/profile.md` exists, those sections write `identity/profile*` in the central store instead of the project's identity files (`scripts/identity_store.py`), and the bootstrap reads the projection `identity/profile-briefing.txt`. The one-time migration of the project profiles into it is done and its script is gone (6.0.0).

## Session-Bracket Coverage

The supported minimal workflow is the **two-call bracket**: `session-bootstrap` at start,
`wrap-up` at end, nothing in between. This table states what that bracket covers and what
stays deliberately on-demand:

| Covered by the bracket (automatic) | How |
|---|---|
| Context restore, health checks, salience learnings, wiki context, Sharepoint pull | bootstrap on request (learnings_top.py / Atlas-RAG, memory-thresholds.sh); the hook's briefing covers the short form |
| Auto-init (only in git repos, elsewhere via the `auto_init_non_git` option or `/agentic-os:init`), model-visible start briefing (tasks SSoT, recovery, root-drift, handoff head; re-fires after /compact), dirty-state tracking | hooks (2 command hooks; prompt hooks removed 4.21.0) |
| Iteration logging | wrap-up session-harvest → plan → `scripts/wrapup_core.py` → `scripts/apply_wrapup.py` |
| Decisions of record | wrap-up decision-scan → plan → `scripts/apply_wrapup.py` |
| Profile changes (user.md / profile.md) | wrap-up followup `user_md_supersede` / `user_md_retire` (rules in references/judge-rules.md); no harvest since 6.0.0 |
| Learnings, open-tasks SSoT | wrap-up plan |
| Session note, central handoff, status board, maintenance recommendation | `scripts/wrapup_core.py` (handoff-template.md, memory-thresholds.sh); entity pages and decision promotion stay with the manual obsidian-sync skill |

| Deliberately on-demand (NOT in the bracket) | Why |
|---|---|
| obsidian-sync (manual mid-session) | reactive; the bracket already covers the end-of-session sync |
| /agentic-os:maintain | Pflegelauf, triggered by the PFLEGELAUF line of the SessionStart briefing (30 new learnings since the last stamp); runs in a session because the agent judges; wrap-up only recommends it |
| /status, /log | inspection / mid-session logging commands |

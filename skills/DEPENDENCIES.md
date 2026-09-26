# Skill Dependency Graph — Agentic OS v5

> Reflects v5.0.0. The local store schema is owned by `scripts/mem-schema.sh`
> (see `references/memory-structure.md`); the **global** layer's pure logic
> (provenance, promotion gate, decay, privacy denylist) lives in `scripts/global-schema.sh`.
> All scaling/archiving threshold NUMBERS live in `scripts/memory-thresholds.sh`
> (threshold SSoT — read by session-bootstrap Step 3, wrap-up Step 9, /agentic-os:maintain Step 1).
> When this graph disagrees with a skill's own SKILL.md, the SKILL.md wins.

## Session Lifecycle (Execution Order)

```
SESSION START (SessionStart hook → session-bootstrap)
  │
  ▼
  session-bootstrap (READ-ONLY, one gated exception below)
  │  ├── cross-project (read-only): ~/AI/.agent-memory/session-summary.md,
  │  │     ~/AI/cross-project-status.md, ~/AI/SESSION-WORKFLOW.md (conditional)
  │  ├── local: session-summary.md, identity/soul.md, identity/user.md,
  │  │     context/project-context.md, patterns/patterns.md,
  │  │     quality/quality-score.json (legacy), iterations/errors.json (tail 3),
  │  │     working/current-session.json, context/open-tasks.json (SSoT for next steps)
  │  ├── learnings: RAG via Atlas MCP, fallback scripts/learnings_top.py
  │  │     (deterministic salience ranking — never full-reads learnings.json)
  │  ├── health: bash scripts/memory-thresholds.sh (exit 10 → THRESHOLD lines in briefing)
  │  ├── wiki (optional, if config.json sync_enabled): entity + entrypoints (≤5 pages)
  │  └── Step 6.5 IDENTITY GATES (consumer side of wrap-up Step 6):
  │        (a) soul-candidates.md → "[j/n]" gate — soul.md write ONLY on explicit `j`
  │        (b) user-candidates.json fallback promotion gate — also only on explicit `j`
  │        (c) starvation warning (read-only) when the identity pipeline is stale
  │
  ▼
WORK PHASE (user-driven, no auto-triggers on code changes)
  │
  ├── /agentic-os:log (command, mid-session, on request)
  │     └── writes via scripts/apply_wrapup.py (`iterations` plan section — never by hand):
  │           iteration-log.md, errors.json, working/current-session.json
  │           (append-only; rotation is /agentic-os:maintain's job, thresholds in memory-thresholds.sh)
  │
  ├── context-keeper (on architecture/stack decisions)
  │     ├── reads: docs/PROJECT.md+ARCHITECTURE.md+CAPABILITIES.md (SoT, Step 1.5),
  │     │     context/project-context.md, context/decisions.json, config.json
  │     ├── writes: context/project-context.md (cache), context/decisions.json
  │     └── writes (optional, Step 3.5 if sync_enabled): ~/wiki/wiki/entities/<id>.md
  │
  ├── pattern-extractor (every ~5 iterations, on request, "refresh" mode)
  │     ├── runs: scripts/extract_patterns.py (--update / --apply) — sole writer of
  │     │     patterns.json + patterns.md; detection, confidence and dedup live there
  │     └── Step 6.5 SKILL CANDIDATE GENERATION (former skill-generator, folded in v4.0.0):
  │           pattern with skill_candidate=true, conf≥0.7, occ≥3 →
  │           writes generated-skills/<name>/SKILL.md + back-ref in patterns.json
  │
  ├── /agentic-os:sync-context (command, MANUAL ONLY)
  │     └── reads/writes: ~/.claude-memory/global/{patterns,learnings,projects}.json ↔ local
  │           (privacy pre-filter → promotion gate → provenance schema; pull serves lifecycle:active only)
  │
  └── obsidian-sync (wiki write-path; also mid-session on request)
        └── see matrix — session notes, entity updates, synthesis, promotion_status
  │
  ▼
SESSION END (manual: /agentic-os:wrap-up — no hook can trigger it; a skipped
             wrap-up shows as a RECOVERY line at the next SessionStart)
  │
  ▼
  wrap-up (MANUAL-ONLY — invoke as slash command; that path applies model: sonnet)
  │  ├── reads: iteration-log.md, errors.json, learnings.json,
  │  │     working/current-session.json, working/user-candidates.json,
  │  │     context/open-tasks.json, skills/wrap-up/references/handoff-template.md
  │  ├── writes: session-summary.md, learnings.json + learnings.md,
  │  │     context/open-tasks.json (Step 5.5, SSoT for next steps),
  │  │     working/current-session.json (reset)
  │  ├── Step 1.5 → session-harvest: retro-logs the session's iterations into
  │  │     the write plan (`iterations`) when none were logged today — since
  │  │     T-015 NO iteration-logger invoke (apply_wrapup.py owns the writes)
  │  ├── Step 4   → runs scripts/extract_patterns.py (if 3+ new iterations);
  │  │     invokes pattern-extractor ONLY for skill/rueckfluss candidates
  │  ├── Step 4.5 → decision-scan: decisions of record into the write plan
  │  │     (`decisions`) — since T-015 NO context-keeper invoke
  │  ├── Step 6   → IDENTITY GROWTH — the ONLY producer of identity observations:
  │  │     checklist harvest → working/user-candidates.json (queue) →
  │  │     FULL queue re-review (queue-re-review) → identity/user.md promotion
  │  │     (changelog-before-write) → identity/soul-candidates.md (direct +
  │  │     escalation-path; NEVER writes soul.md) → MANDATORY status line
  │  │     (identity-visible). Consumed by bootstrap Step 6.5 gates.
  │  ├── Step 7.5 → invokes obsidian-sync (gates: sync_enabled + substantiality;
  │  │     always reports — wiki-sync-visible)
  │  ├── Step 7.6 → writes cross-project: ~/AI/.agent-memory/session-summary.md
  │  │     (prepend handoff), ~/AI/cross-project-status.md, Sharepoint (optional)
  │  │     — templates + dedup rules in skills/wrap-up/references/handoff-template.md
  │  └── Step 9   → runs scripts/memory-thresholds.sh + review_sweep.py; exit 10
  │        → prints THRESHOLD lines and recommends /agentic-os:maintain (never invoked)
```

## Dependency Matrix

| Skill | Reads From | Writes To | Invokes |
|-------|-----------|-----------|---------|
| session-bootstrap | local: session-summary.md, soul.md, user.md, soul-candidates.md, user-candidates.json, project-context.md, patterns.md, quality-score.json (legacy), errors.json (tail), working/current-session.json, context/open-tasks.json (SSoT for next steps), config.json · learnings via Atlas-RAG or `scripts/learnings_top.py` · health via `scripts/memory-thresholds.sh` · cross-project: ~/AI/.agent-memory/session-summary.md, cross-project-status.md, SESSION-WORKFLOW.md · wiki (optional): entity + entrypoints | (read-only) EXCEPT the user-confirmed identity gates in Step 6.5 → soul.md + user.md, user-changelog.json, soul-candidates.md / user-candidates.json (only on explicit `j`). Staleness display (`[STALE? …]`) is DISPLAY-ONLY, never a write. | — |
| pattern-extractor | errors.json, iteration-log.md, patterns.json (all via `scripts/extract_patterns.py`) | patterns.json — **written only through `scripts/extract_patterns.py`** (sole writer; the skill keeps schema ownership and the judgment-bound Steps 6.5/6.6; authorized field-GAIN exceptions: obsidian-sync → promotion metadata, implementing/validating main session → implemented_by/validated_by + dates per Step 6.6), patterns.md, generated-skills/<name>/SKILL.md (Step 6.5 skill-candidate generation, former skill-generator), context/open-tasks.json (Step 6.6 delta-draft tasks; decisions route via context-keeper, never written directly) | context-keeper (Step 6.6 architecture-level delta drafts) |
| context-keeper | docs/PROJECT.md+ARCHITECTURE.md+CAPABILITIES.md (SoT), project-context.md, decisions.json, config.json | project-context.md (cache, own write), decisions.json — new records **only through `scripts/apply_wrapup.py`** (`decisions` plan section; Step 3.5 wiki_ref/promoted_at stays a direct field extension), ~/wiki/wiki/entities/<id>.md (optional) | — |
| wrap-up | iteration-log.md, errors.json, learnings.json, working/current-session.json, working/user-candidates.json, context/open-tasks.json, skills/wrap-up/references/handoff-template.md | session-summary.md, learnings.json + learnings.md, user.md (via queue promotion, changelog first), user-candidates.json (queue), user-changelog.json (audit), soul-candidates.md (propose — never soul.md), working/current-session.json (reset), context/open-tasks.json (Step 5.5 SSoT); cross-project handoff (max 1 block per project, next steps as pointer + `[cross-project]` only) + status-board + Sharepoint | pattern-extractor (Step 4, ONLY for skill/rueckfluss candidates), obsidian-sync (Step 7.5). Step 9 only recommends /agentic-os:maintain. Step 1.5 session-harvest and Step 4.5 decision-scan route through the write plan instead of invoking iteration-logger/context-keeper (T-015) |
| obsidian-sync | config.json, session-summary.md, iteration-log.md, learnings.json/.md, patterns.json/.md, decisions.json, ~/wiki/{index.md,log.md,entities,synthesis} | ~/wiki/wiki/queries/*.md, ~/wiki/{index.md,log.md}, entity + synthesis (append), patterns.json (promotion_status + promotion_scope only) | — |

### Commands with a script core (v5.0.0)

| Command | Script core | Writes |
|---|---|---|
| /agentic-os:maintain | memory-thresholds.sh, gc_dirty_markers.py, native_memory_audit.py, review_sweep.py, extract_patterns.py --refresh, global-schema.sh (apply_decay) | archives/*, repaired JSON, compacted session-summary.md + learnings.md, working/ scratch cleanup, global decayed confidence + lifecycle:archived (never hard-delete) |
| /agentic-os:log | apply_wrapup.py (`iterations` section) | iteration-log.md, errors.json, working/current-session.json |
| /agentic-os:sync-context | global-schema.sh (is_denied, compute_scope, passes_promotion_gate), mem-schema.sh (MEM_GLOBAL_DENY_TAGS) | local + ~/.claude-memory/global/{patterns,learnings,projects}.json with provenance schema; privacy-filter before gate; pull serves lifecycle:active only |

## Agents

| Agent | Used By | Reads / Writes | Purpose |
|-------|---------|----------------|---------|
| context-detective | /agentic-os:init (optional) | reads manifests + docs/ (docs-first); writes project-context.md | Auto-detect project stack |

## Skills (v5.0.0)

5 active skills, grouped by layer (see `references/skill-template.md` Layer Guide):

| Layer | Skills | Note |
|-------|--------|------|
| core | session-bootstrap, wrap-up, context-keeper | session lifecycle + decisions of record |
| analysis | pattern-extractor | absorbed skill-generator (Step 6.5) in v4.0.0 |
| knowledge | obsidian-sync | write-path to the Obsidian wiki |

### Converted to commands / archived in v5.0.0

- **memory-maintenance → /agentic-os:maintain** — 12 KB body for mechanics that never ran; the scripts own the writes, the command wraps them.
- **iteration-logger → /agentic-os:log** — 70 % mechanics already in `apply_wrapup.py`; no usage evidence as a skill.
- **sync-context → /agentic-os:sync-context** — keeps `disable-model-invocation: true`; all three commands carry the flag (a command file is otherwise Skill-tool-resolvable), enforced by the invocation-contract test.
- **self-improve, /rollback, /auto-commit, improvements/ → `_archived/`** — silent since 2026-06-21; reversible via `git mv` (see `_archived/README.md`).

### Removed in v4.0.0 (with reason)

- **retrospective** — trend metrics never drove a decision; the read-only report duplicated what /memory-audit + patterns.md already surface.
- **research-pipeline** — external research is better served by user-level skills (Perplexity/NotebookLM CLI); the plugin wrapper added indirection, not value.
- **wiki-query** — a plain wiki lookup needs no skill; the wiki MCP / direct Read covers it without a trigger-phrase surface.
- **quality-gate** (skill + agent) — review/test/TDD enforcement moved to user-level skills and the test suite itself; the in-plugin score pipeline had no consumer.
- **skill-generator** — not deleted but FOLDED into pattern-extractor Step 6.5 (single writer of patterns.json generates the skills its candidates describe).
- Wrapper commands **/log, /patterns, /research, /sync, /run-loop** — thin wrappers around directly-invocable skills (and shadow-risk, L17). Commands now (v5.0.0): init, status, memory-audit, maintain, log, sync-context — `log` returned as a real command with a script core, not a wrapper.

Removed agents: `improvement-scout`, `fix-reviewer` (2026-04-30); `improvement-agent`, `research-agent` (4.15.0).

## Key Design Principles

1. **No circular dependencies** — DAG only.
2. **No auto-triggers on code changes** — user/CLAUDE.md driven (the only hook-driven skill is session-bootstrap on start; wrap-up is a manual slash command).
3. **session-bootstrap is read-only** — never writes during startup, with ONE exception: the user-confirmed identity gates (Step 6.5) write soul.md/user.md + user-changelog.json + the queues, but only on an explicit `j` from the user (never autonomously).
4. **Skills that invoke other skills:** `wrap-up` (pattern-extractor only for skill/rueckfluss candidates, obsidian-sync — iteration-logger/context-keeper are NOT invoked since T-015, memory-maintenance not since v5.0.0; Step 9 only recommends /agentic-os:maintain). All other skills are leaf nodes.
5. **/agentic-os:sync-context is manual-only** — a command, no auto-sync.
6. **docs/ is the source of truth for project-context.md** — context-keeper (and context-detective, /init) read docs first; project-context.md is a cache (Regel 13 / L9).
7. **Thresholds live in ONE script** — `scripts/memory-thresholds.sh` is the only place scaling numbers exist; skills and commands reference it, never restate the numbers.
8. **Identity growth is producer/consumer split** — wrap-up Step 6 is the only PRODUCER of identity observations (queues + promotions + mandatory status line); session-bootstrap Step 6.5 is the CONSUMER (gates + starvation check). Nothing else touches identity files.

## Session-Bracket Coverage

The supported minimal workflow is the **two-call bracket**: `session-bootstrap` at start,
`wrap-up` at end, nothing in between. This table states what that bracket covers and what
stays deliberately on-demand:

| Covered by the bracket (automatic) | How |
|---|---|
| Context restore, health checks, salience learnings, wiki context, Sharepoint pull | bootstrap (learnings_top.py / Atlas-RAG, memory-thresholds.sh) |
| Auto-init, model-visible start briefing (tasks SSoT, recovery, root-drift, handoff head; re-fires after /compact), dirty-state tracking | hooks (2 command hooks; prompt hooks removed 4.21.0) |
| Iteration logging | wrap-up Step 1.5 session-harvest → write plan → `scripts/apply_wrapup.py` |
| Pattern extraction | wrap-up Step 4 (3+ iterations, fed by harvest) → `scripts/extract_patterns.py` |
| Skill-candidate generation, rueckfluss drafts | wrap-up Step 4 → pattern-extractor Steps 6.5/6.6 (only when the extractor reports candidates) |
| Decisions of record | wrap-up Step 4.5 decision-scan → write plan → `scripts/apply_wrapup.py` |
| Identity growth (user.md, soul candidates) + gates | wrap-up Step 6 (producer) + bootstrap Step 6.5 (consumer) |
| Learnings, open-tasks SSoT | wrap-up Steps 3-5.5 |
| Wiki sync, central handoff, status board, maintenance recommendation | wrap-up Steps 7-9 (handoff-template.md, memory-thresholds.sh) |

| Deliberately on-demand (NOT in the bracket) | Why |
|---|---|
| /agentic-os:sync-context | Design Principle 5: manual-only command, no auto cross-project sync |
| obsidian-sync (manual mid-session) | reactive; the bracket already covers the end-of-session sync |
| /agentic-os:maintain | threshold-gated via memory-thresholds.sh (wrap-up only recommends it); not part of every wrap-up |
| /memory-audit, /status, /log | inspection / mid-session logging commands |

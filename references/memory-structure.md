# .agent-memory/ Structure Reference

> **Authoritative source:** `scripts/mem-schema.sh` (`create_memory_structure`).
> This file is documentation — when it disagrees with the SSoT, the SSoT wins.
> The directory tree is created by the SessionStart hook (`scripts/session-start.sh`)
> AND by `/agentic-os:init` (`commands/init.md`); both source `mem-schema.sh` so they
> never diverge. A drift test in `tests/validate-plugin.sh` enforces this.

```
.agent-memory/
├── session-summary.md                  # Last session summary (max 30 lines)
│
├── identity/
│   ├── soul.md                         # Agent behavior, priorities, guard rails
│   └── user.md                         # User profile, preferences, corrections
│
├── context/
│   ├── project-context.md              # Cache of docs/ (stack, architecture, constraints)
│   ├── decisions.json                  # Architecture decisions (append-only)
│   └── open-tasks.json                 # Open tasks (SSoT; rendered into the SessionStart briefing + summary)
│
├── iterations/
│   ├── iteration-log.md                # Chronological iteration entries
│   └── errors.json                     # Structured error records
│
├── patterns/
│   ├── patterns.md                     # Human-readable pattern catalog
│   └── patterns.json                   # Machine-readable patterns
│
├── quality/
│   ├── test-results.json               # Test run history
│   ├── code-reviews.json               # Code review history
│   └── quality-score.json              # Aggregated quality metrics
│
├── knowledge/
│   └── notebook-registry.md            # NotebookLM KB registry (topics, keywords)
│
├── learnings/
│   ├── learnings.json                  # Structured learnings + salience metadata (read by bootstrap/wrap-up)
│   └── learnings.md                    # Human-readable learnings mirror
│
├── working/
│   └── current-session.json            # Volatile working memory for the active session
│
└── generated-skills/                    # Auto-generated skills from patterns
    └── <skill-name>/
        └── SKILL.md
```

## Created by the SSoT vs. by a consumer

- **`mem-schema.sh` creates:** all directories above, the empty-array JSON files,
  `quality-score.json` (structured default), `working/current-session.json`, the
  `.md` placeholders (`iteration-log.md`, `patterns.md`, `learnings.md`,
  `notebook-registry.md`), `session-summary.md`, and `identity/soul.md` + `user.md`.
- **NOT created by the SSoT:** `context/project-context.md`. It needs stack
  auto-detection / docs distillation, so each consumer (hook, `/init`,
  `context-keeper`) writes it itself after calling `create_memory_structure`.
  Its source of truth is `docs/` (Regel 13) — the file is a cache.

## JSON Defaults

| File | Default Value |
|------|--------------|
| `errors.json` | `[]` |
| `patterns.json` | `[]` |
| `decisions.json` | `[]` |
| `open-tasks.json` | `[]` |
| `test-results.json` | `[]` |
| `code-reviews.json` | `[]` |
| `learnings.json` | `[]` |
| `quality-score.json` | `{"last_updated": null, "test_health": {"current_score": null, "trend": "unknown"}, "code_quality": {"current_score": null, "trend": "unknown"}}` |
| `working/current-session.json` | `{"session_start": "<date>", "errors_this_session": [], "learnings_draft": []}` |

## Archiving Thresholds

> **Authoritative source:** `commands/maintain.md` Step 3.
> Archiving runs only when `/agentic-os:maintain` is run (on demand or when
> wrap-up detects an exceeded threshold) — it is never part of the normal
> end-of-session flow.

| File | Threshold | Action |
|------|-----------|--------|
| `iteration-log.md` | > 100 entries | Keep newest 100, archive rest to `iteration-log-archive-{YYYY-MM}.md` |
| `errors.json` | > 50 entries | Keep newest 50, archive rest to `errors-archive-{YYYY-MM}.json` |
| `learnings/learnings.json` | > 100 entries | Keep newest 100, archive rest to `learnings-archive-{YYYY-MM}.json` |
| `code-reviews.json` | > 100 entries | Keep newest 100, archive rest to `code-reviews-archive-{YYYY-MM}.json` |
| `test-results.json` | > 100 entries | Keep newest 100, archive rest to `test-results-archive-{YYYY-MM}.json` |
| `patterns.json` | `last_seen` > 60 days OR `confidence` < 0.3 | Archive stale/low-confidence entries to `patterns-archive-{YYYY-MM}.json` |
| `decisions.json` | `status: superseded` > 90 days | Archive superseded; keep all `active` regardless of age |
| `learnings/learnings.md` | > 200 lines | Keep last 12 months, archive older to `learnings-archive-{YYYY}.md` |
| `session-summary.md` | > 30 lines | Compress to 30 lines (never drop "Open Items") |

If an archive file for the current month already exists, append to it instead of overwriting.

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
│   ├── soul.md                         # Agent behavior defaults (read-only history since 5.11.0)
│   └── user.md                         # User profile, preferences, corrections
│
├── context/
│   ├── project-context.md              # Cache of docs/ (stack, architecture, constraints)
│   ├── decisions.json                  # Architecture decisions (append-only)
│   └── open-tasks.json                 # Open tasks (SSoT; rendered into the SessionStart briefing + summary)
│
├── iterations/
│   ├── iteration-log.md                # Chronological iteration entries
│   └── errors.json                     # Error records up to 5.x; frozen data since 6.0.0 (never written)
│
├── learnings/
│   ├── learnings.json                  # Structured learnings + salience metadata (read by bootstrap/wrap-up)
│   └── learnings.md                    # Generated projection of learnings.json (apply_wrapup.py)
│
└── working/
    └── current-session.json            # Volatile working memory for the active session
```

Older stores may also hold `patterns/` and `generated-skills/` (pattern mechanics, removed in 6.0.0): frozen
data, never written and never created for a new store.

## Created by the SSoT vs. by a consumer

- **`mem-schema.sh` creates:** all directories above, the empty-array JSON files,
  `working/current-session.json`, the `.md` placeholders (`iteration-log.md`,
  `learnings.md`), `session-summary.md`, and `identity/soul.md` + `user.md`.
- **No longer created (5.11.0):** the `quality/` folder (test-results, code-reviews, quality-score)
  and the NotebookLM registry stub in `knowledge/` — empty stubs without an active writer.
  Existing stores keep them untouched; wrap-up Step 7 reads the registry only if it exists.
- **NOT created by the SSoT:** `context/project-context.md`. It needs stack
  auto-detection / docs distillation, so each consumer (hook, `/init`,
  `context-keeper`) writes it itself after calling `create_memory_structure`.
  Its source of truth is `docs/` (Regel 13) — the file is a cache.

## JSON Defaults

| File | Default Value |
|------|--------------|
| `errors.json` | `[]` |
| `decisions.json` | `[]` |
| `open-tasks.json` | `[]` |
| `learnings.json` | `[]` |
| `working/current-session.json` | `{"session_start": "<date>", "errors_this_session": [], "learnings_draft": []}` |

## Archiving Thresholds

> **Authoritative source:** `scripts/memory-thresholds.sh` (limits; its lines appear in the
> SessionStart briefing and point to `/agentic-os:maintain`). **Nothing is ever moved, archived or
> compressed because of a count or an age** (D-021, owner decision 2026-10-07): archive files are invisible to the
> Atlas RAG, and count-based archiving took long-term and importance>=4 learnings
> and ready entries out of retrieval. A threshold only reports.

| File | Limit | What happens |
|------|-------|--------------|
| `iteration-log.md` | soft 100 entries | reported |
| `learnings/learnings.json` | ceiling 1000 entries | reported (catches runaway growth only) |
| `learnings/learnings.md` | ceiling 2000 lines | reported; a projection — re-render with `apply_wrapup.py <mem> --render-learnings`, never cut by hand |
| `context/open-tasks.json` | soft 30 done entries | reported |
| `session-summary.md` | soft 40 lines | reported; the next wrap-up rewrites it (AI workspace: central handoff, 5-block cap via Step 7.6a) |
| `decisions.json` | `status: superseded` | listed, stays (every reader already filters it) |

Existing `*-archive-*` files from earlier versions stay untouched. Their ids are
reserved: every writer continues the sequence above the archived ids. The restore path of 5.3.0 is
removed in 6.0.0 (a plan with `restore` is refused). The archive files themselves are never modified.

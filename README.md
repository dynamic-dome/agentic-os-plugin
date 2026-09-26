# Agentic OS: persistent project memory for Claude Code

Claude Code starts every session with an empty head. Agentic OS gives each project a small, file-based
memory in `.agent-memory/`, so the next session knows what the last one did, what was decided and what
is still open.

It is the memory layer I use every day across my own projects. This repository is its public export:
development happens in a private repository, and each version is exported here after its test suite passes.

## How it works

Two calls bracket a session:

- **Start** (`session-bootstrap`, also triggered by a `SessionStart` hook) reads the project memory and
  prints a short briefing: last session, open tasks, warnings.
- **End** (`wrap-up`) writes what happened back through deterministic scripts: iterations, decisions,
  learnings, open tasks and a session summary.

Everything in between is normal Claude Code work. Two command hooks do bookkeeping only (the briefing at
start, a dirty-tracker after edits). No hook calls a language model.

```
.agent-memory/
├── context/        project context and decisions
├── iterations/     what was done, including what failed
├── learnings/      lessons that should outlive the session
├── patterns/       recurring patterns extracted from iterations
├── identity/       gated candidate traits for user and agent
├── working/        short-lived session state
└── session-summary.md
```

## Long-Term Memory Routine

After each substantial task or session, run `wrap-up` to consolidate the work into
the central `.agent-memory/` knowledge base. The routine preserves durable facts
instead of leaving them only in chat: `/agentic-os:log` records distinct work
iterations in `.agent-memory/iterations/iteration-log.md`, `wrap-up` extracts
genuine reusable learnings into `.agent-memory/learnings/learnings.json`,
`context-keeper` records durable decisions in
`.agent-memory/context/decisions.json`, open next steps stay in
`.agent-memory/context/open-tasks.json`, and the handoff snapshot is refreshed in
`.agent-memory/session-summary.md`.

## Install

```bash
claude plugin marketplace add dynamic-dome/agentic-os-plugin
claude plugin install agentic-os@agentic-os-marketplace
```

Then run `/agentic-os:init` in a project. `/agentic-os:status` shows the health of the store,
`/agentic-os:memory-audit` reports drift and stale entries.

## Optional: a handoff across projects

If you keep a central folder `~/AI/`, the skills also read and write a cross-project handoff there
(`~/AI/.agent-memory/session-summary.md` and a status board `~/AI/cross-project-status.md`). The paths are
fixed in this version.

## Limits

- Built and used on Windows 11 with Git Bash. Other platforms are untested.
- Skills and README are in English, some internal docs (`docs/ARCHITECTURE.md`, `docs/CHANGELOG.md`) are in German.
- `scripts/measure_session_cost.py` measures context size per API call from a session transcript.
- Tests: `bash tests/run-all.sh`.

More about the design (in German): https://dynamic-dome.com/systeme/agent-memory/

License: MIT

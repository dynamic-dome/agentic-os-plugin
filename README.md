# Agentic OS: persistent project memory for Claude Code

Claude Code starts every session with an empty head. Agentic OS gives each project a small, file-based
memory in `.agent-memory/`, so the next session knows what the last one did, what was decided and what
is still open.

It is the memory layer I have used every day across my own projects since March 2026. This repository is
its public export: development happens in a private repository, and each version is exported here after
its test suite passes. That is why the history here starts fresh.

More about the design (in German): https://dynamic-dome.com/systeme/agent-memory/

## What a session start looks like

Every session opens with a short briefing like this (structure of a real briefing, contents replaced):

```
Agentic OS active | Branch: [branch]
[n] iterations, [n] errors, [n] patterns
Next steps: [n] open
  T-001: [open task]
Warnings:
  - [warning from an earlier failure]
Last session ([date]):
  [where the last session stopped]
```

## How it works

Two calls bracket a session:

- **Start** (`session-bootstrap`, also triggered by a `SessionStart` hook) reads the project memory and
  prints the briefing above: last session, open tasks, warnings.
- **End** (`wrap-up`) writes what happened back through deterministic scripts: iterations, decisions,
  learnings, open tasks and a session summary. What the agent notices about how you work is queued as a
  candidate first. It moves into `identity/user.md` once you confirm it or the same signal comes up twice,
  and every change is logged before the edit. The agent's self-description (`identity/soul.md`) changes
  only on your explicit yes.

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

Agentic OS is the first of three layers in my setup: it **remembers** the state between sessions. An
Obsidian wiki **keeps** what should last (the `obsidian-sync` skill writes session notes into it), and a
separate search index **finds** things across all projects. The wiki and the index are not part of this
repository.

## Install

```bash
claude plugin marketplace add dynamic-dome/agentic-os-plugin
claude plugin install agentic-os@agentic-os-marketplace
```

Then run `/agentic-os:init` in a project. `/agentic-os:status` shows the health of the store,
`/agentic-os:memory-audit` reports drift and stale entries.

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

## Optional: a handoff across projects

If you keep a central folder `~/AI/`, the skills also read and write a cross-project handoff there
(`~/AI/.agent-memory/session-summary.md` and a status board `~/AI/cross-project-status.md`). At the start of
a session in a new project, they also read a workflow file `~/AI/SESSION-WORKFLOW.md` if it exists. The paths
are fixed in this version.

## Limits

- Built and used on Windows 11 with Git Bash. Other platforms are untested.
- Skills and README are in English, some internal docs (`docs/ARCHITECTURE.md`, `docs/CHANGELOG.md`) are in German.
- The name of `scripts/measure_session_cost.py` is historical: it measures context size per API call from a
  session transcript.
- Some comments and the changelog refer to `membrain`, a private research repository in which several of
  the designs were worked out. Those references cannot be followed from here.
- Tests: `bash tests/run-all.sh`.

License: MIT

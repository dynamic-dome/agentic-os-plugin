# Bootstrap / Wrap-up — extracted rationale

Behavior-neutral "why" text lifted out of `session-bootstrap/SKILL.md` and
`wrap-up/SKILL.md` to keep the executable bodies lean (T-32, safe extraction cut).
Nothing here is an execution step — it is background/rationale only. The bodies keep
every step, gate, output contract, and error path verbatim. Read this only when you
need the reasoning behind a step.

## session-bootstrap

### Step 0.5a — what the central handoff tells you
The central handoff (`~/AI/.agent-memory/session-summary.md`) surfaces: which project
was worked on last (may differ from the current one), what was accomplished and what is
still open, and which agent wrote it (Claude, Codex, …). The TOP block is the last
session; older `# Vorherige Session (…erhalten)` blocks are history (wrap-up Step 7.6a
prepends, never blank-overwrites).

### Step 1 — the three state sources and their authority
- **Central handoff** (`~/AI/.agent-memory/session-summary.md`): cross-project
  agent-to-agent handoff — authoritative for "what happened last".
- **Status board** (`~/AI/cross-project-status.md`): per-project current state of ALL
  touched projects — authoritative for "where does each project stand".
- **Local `.agent-memory/session-summary.md`**: project-specific operational state —
  authoritative for "where THIS project stands".
All three are valid; the briefing (Step 4) merges them.

### Step 2 — learnings fallback formula
`learnings_top.py` ranks by `importance*0.4 + recency*0.3 + tag_overlap*0.3` and skips
superseded entries. That is why the body just runs the script and uses its ≤10 lines
instead of reading `learnings.json` into context (it can be 2k+ words).

### Step 2 — why the staleness annotation is display-only
Marking `[STALE? …]` is a read-time annotation. Decaying/writing `confidence` or
`last_relevant` is /agentic-os:maintain's job; bootstrap is strictly read-only, so it only
marks, never mutates.

### Step 6.5 — why the identity gates exist
`wrap-up` grows identity via queues (`working/user-candidates.json`,
`identity/soul-candidates.md`) but never writes `soul.md` autonomously (Stufe B). The
soul.md merge on explicit `j` is the ONLY exception to bootstrap's read-only rule:
user-triggered, never autonomous, touches only soul.md + changelog + queue. (This guard
rail is also restated under "What NOT to Do".)

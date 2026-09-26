# Central Handoff — Template + Prepend Algorithm (SSoT)

Referenced by wrap-up Step 7.6. Write-side of the cross-project handoff; read-side is
session-bootstrap Step 0.5. Paths per SESSION-WORKFLOW.md §1/§3.

## 7.6a — Central handoff file

Target: `~/AI/.agent-memory/session-summary.md` (NOTE: under `.agent-memory\`,
NOT flat in `~/AI\`). If that directory does not exist → skip silently, do not create it.

**PREPEND, never blank-overwrite** — the file may hold a DIFFERENT project's handoff chain;
blind overwrite destroys another agent's work (incident 2026-06-01).

**Guard the cycle (T-19):** snapshot with `scripts/handoff_write_guard.py` right after
step 1 (the read), `check` immediately before step 4's write; exit 20 = a parallel
session wrote in between → re-read, merge your block into the NEW content, re-snapshot,
then write. Details in wrap-up Step 7.6.

Deterministic prepend algorithm:

1. Read the file FIRST. Structure: line 1 `# Letzte Session` (TOP block), below it zero or
   more `# Vorherige Session (...erhalten)` blocks.
2. Demote the existing TOP block: rewrite ONLY its first line to
   `# Vorherige Session ({its date} {its project}, erhalten)`. Never re-wrap an already
   demoted block (prevents nested wrappers).
3. **Ownership-dedup:** after demoting, DELETE every older `# Vorherige Session (...)` block
   whose project equals the NEW block's project — at most one block per project. Nothing is
   lost: detail lives in that project's local `.agent-memory/session-summary.md`.
4. Write the new block with `# Letzte Session` as first line, then `---`, then the demoted
   old content.
5. **Hard cap: 5 blocks total** (1 current + 4 distinct-project history). Drop oldest beyond
   that — but never drop a foreign project's ONLY block; move its 1-line state into the
   status board (7.6b) first.

**Naechste Schritte = pointer, not copy:** ONE pointer line to the local
`{project}/.agent-memory/context/open-tasks.json` (open count + top item) plus ONLY entries
with `cross_project: true`, each prefixed `[cross-project]`. Project-specific steps live
exclusively in the local store.

Block template (German headings, do not invent a new format):

```markdown
# Letzte Session

*Datum: {YYYY-MM-DD HH:MM}*
*Agent: Claude Code*
*Projekt: {current project name}*

## Was wurde gemacht
- {bullet points, mirror wrap-up Step 2}

## Aktueller Stand
- {where things stand right now}

## Repo-Status
- Branch: {branch}
- Uncommitted changes: {ja/nein}
- Letzter Commit: {hash} {message}

## Offene Punkte / Blocker
- {open items}
- Blocker: {keine | description}

## Checks
- Tests: {bestanden | fehlgeschlagen | nicht gelaufen | n/a}
- Lint/Validation: {bestanden | fehlgeschlagen | nicht gelaufen | n/a}

## Naechste Schritte
- Projekt-Next-Steps: {project}/.agent-memory/context/open-tasks.json ({N} offen; Top: {1 Zeile})
- [cross-project] {items with cross_project=true — omit line if none}

## Wichtige Pfade
- {key paths touched this session}
```

## 7.6b — Cross-project status board

Target: `~/AI/cross-project-status.md`. NOT overwritten — touch ONLY this
project's `## {project}` section (replace heading → next `---`), append a new section if
missing. Never touch other projects' sections. Only add to `## Cross-Project Notes` on
explicit user request. Same read-then-write guard as 7.6a (snapshot after read,
check before write — see wrap-up Step 7.6).

If the file does not exist, create it with:

```markdown
# Cross-Project Status Board

*One section per project. Each wrap-up updates only its own project's section.*
*Read by session-bootstrap Step 0.5b. Last-session detail lives in the central handoff.*

## Cross-Project Notes
- (items relevant for ALL projects — added on explicit user request only)

---
```

Section format (~5 lines — dashboard, not log):

```markdown
## {project name}
*Updated: {YYYY-MM-DD HH:MM} by Claude Code*
- State: {1-line current state}
- Next: {1-line highest-priority next step}
- Repo: branch {branch}, uncommitted {ja/nein}, last {hash}

---
```

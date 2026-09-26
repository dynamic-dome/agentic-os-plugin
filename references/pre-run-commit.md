# Pre-Run Commit (backup light)

Shared snapshot procedure for skills and commands that mutate `.agent-memory/`
(/agentic-os:maintain, obsidian-sync). Gives every mutating run a one-command
rollback. The invoking skill or command supplies its own commit message.

1. `git -C {project_root} rev-parse --is-inside-work-tree` fails → skip silently
   (optionally one hint line: "Store unversioniert — kein Pre-Run-Snapshot möglich").
2. `git -C {project_root} status --porcelain -- .agent-memory` empty → skip (clean).
3. Otherwise stage ONLY the store — `git add .agent-memory` (NEVER `-A`; foreign
   project files stay untouched) — and commit with the invoking skill's message.
4. Any failure is non-blocking: report one line and continue — a missed snapshot
   must never prevent the run itself.

Rollback later: `git log --oneline -- .agent-memory` →
`git checkout {hash} -- .agent-memory`.

# session-bootstrap — extracted procedures

Verbose, inert detail mechanics lifted out of `skills/session-bootstrap/SKILL.md`
under the T-35 redesign rule: **no gate logic leaves the body** — only the
step-by-step mechanics live here. The body keeps every gate's trigger condition
and result-contract; it points here for the exact procedure and loads this file
only when it actually reaches the step.

Design: `memskillredesign.md` / `memevalharness.md` (membrain). Anchor policy:
`gate_linkage.py` gate tokens and `validate-skills.sh` anchors stay in the body,
never here.

## Wiki Context Loading (Step 2.5)

Loaded only when `config.json` exists AND `sync_enabled` is true (the body gates
this; if it is false the step is skipped silently and this file is never read).

### Resolution Order
1. Extract `wiki_root`, `project_id`, `project_aliases`, `default_entrypoints` from config.json
2. Validate wiki: check if `$WIKI_ROOT/CLAUDE.md` exists. If not → skip silently.
3. **Project Entity Resolution** — find the project's wiki page:
   - Try `$WIKI_ROOT/wiki/entities/{project_id}.md`
   - If not found: try each alias in `project_aliases` as filename
   - If not found: try Grep for `project_id` in entity filenames
   - If still not found: skip entity, continue with other steps
   - **Read at most the first 80 lines** of the entity page (frontmatter + summary +
     patterns) — entity pages are uncapped in length and can cost 2k+ tokens full-read
4. **Entry Points** — load pages from `default_entrypoints`:
   - For each path: check if file exists at `$WIKI_ROOT/{path}`
   - **Skip non-existing entry points silently** (no error — they may be planned for later sprints)
   - Read only existing entry points
5. **Last 3 Session Notes** — find recent sessions for this project:
   - Glob: `$WIKI_ROOT/wiki/queries/*session*{project_id}*.md` OR match `project_aliases`
   - Sort by date (filename prefix), take last 3
   - Read only frontmatter + first 10 lines of body (not full content)
6. **Optional: Rolling Synthesis** — if `$WIKI_ROOT/wiki/synthesis/agent-learnings-aktuell.md` exists, read last 20 lines

### Limits
- **Max 5 pages total** loaded in this step (entity + entry points + sessions + synthesis)
- No brute-force search over the whole vault
- No deep source pages unless explicitly listed as entry point
- This step must complete in < 3 seconds

### Briefing Extension — WIKI CONTEXT block
Add to the briefing output (Step 4):

```
WIKI CONTEXT
  Entity: [[wiki/entities/{slug}]] (updated: {date})
  Sessions: {n} (last: {date} — {summary})
  Patterns: {list of high-confidence patterns from entity page}
  Docs: {count} Claude Code Sources available
```

If wiki is not configured or unreachable: omit this block entirely. Do NOT output
"Wiki not found" — just skip.

### Cross-Vault-Enrichment (RAG, optional)
After loading the wiki pages above, query the Atlas MCP for cross-vault ideas
relevant to today's tasks:

1. Use the same task-title query built for Learnings Retrieval (Step 2).
2. Call `mcp__agent-memory-atlas__memory_search_tool` with:
   - `query`: task-title query
   - `top_k`: 2
   - `scope`: `"project:agent-lab"`
3. On success: append to the WIKI CONTEXT block:
   `  Ideas: {title1}; {title2} (agent-lab)`
4. On error or empty result: skip silently.

If Atlas MCP is unavailable: skip the entire Cross-Vault-Enrichment silently.

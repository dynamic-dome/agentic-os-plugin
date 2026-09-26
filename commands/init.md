---
name: init
description: Initialize Agentic OS v2 memory system in the current project
argument-hint: "[--force]"
allowed_tools: ["Read", "Write", "Bash", "Glob", "Grep"]
---

# Initialize Agentic OS

Bootstrap the `.agent-memory/` knowledge system in the current project directory.

## What to do

1. **Check if `.agent-memory/` already exists.** If it does and `--force` was NOT passed, abort with: "Memory system already exists. Use `--force` to reinitialize (backs up existing)." If `--force` was passed, rename existing `.agent-memory/` to `.agent-memory.bak-{YYYY-MM-DD}/`.

2. **Create the directory structure — via the Single Source of Truth.**

   Do NOT hand-create the files. Run the canonical schema script, which creates the
   entire structure with correct empty defaults (idempotent — never overwrites existing):

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/mem-schema.sh" "$PWD/.agent-memory"
   ```

   `scripts/mem-schema.sh` is the ONE definition of the memory schema — the SessionStart
   hook sources the same file. This guarantees `/init` and the hook can never drift
   (the historical L4 bug). If you add a new memory file, add it in `mem-schema.sh` only.

   The structure it produces (for reference — the script is authoritative):

   ```
   .agent-memory/
   ├── identity/{soul.md, user.md}
   ├── context/{project-context.md*, decisions.json, open-tasks.json}
   ├── iterations/{iteration-log.md, errors.json}
   ├── patterns/{patterns.md, patterns.json}
   ├── quality/{test-results.json, code-reviews.json, quality-score.json}
   ├── learnings/{learnings.md, learnings.json}
   ├── knowledge/notebook-registry.md
   ├── working/current-session.json
   ├── generated-skills/
   ├── config.json              (optional, created by Wiki Integration step)
   └── session-summary.md
   ```
   `*` `project-context.md` is NOT created by the script — it needs stack auto-detection
   (step 6 below). Everything else is created with empty/stub defaults by the script.

3. **(Defaults are handled by the script.)** `mem-schema.sh` already wrote the empty
   JSON stores (`[]`), the `quality-score.json` skeleton, the `working/current-session.json`
   working-memory stub, all Markdown stubs, `notebook-registry.md`, `session-summary.md`,
   and the `soul.md` / `user.md` identity defaults. Do not re-create them. Only proceed to
   customize identity (step 5) and project context (step 6) below.

   Key files the script guarantees (each MANDATORY for a downstream consumer):
   - `context/open-tasks.json` — task SSoT, read by the SessionStart briefing and wrap-up Step 5.5 (canonical location: `context/`)
   - `learnings/learnings.json` — wrap-up dedup/scoring + session-bootstrap salience
   - `working/current-session.json` — /agentic-os:log appends (via apply_wrapup.py), wrap-up Step 3.5 consumes + resets

4. **(Markdown stubs are handled by the script.)** See step 3. No manual creation needed.

5. **Customize identity files (optional):**

   The script already wrote default `identity/soul.md` and `identity/user.md`. Only edit
   them if the user wants different communication style, guard rails, or priorities than
   the defaults (en/balanced/correctness-first). Otherwise leave them as-is.

6. **Build project context (docs first, then detect):**
   - **Source of truth:** if `docs/PROJECT.md`, `docs/ARCHITECTURE.md`, `docs/CAPABILITIES.md`,
     or `HOW-TO-USE.md` exist, read them and derive the context from them. `project-context.md`
     is a CACHE of the docs — it must not contradict them.
   - Only for facts the docs do NOT state: read `README.md`, `package.json`, `pyproject.toml`,
     `requirements.txt`, `Cargo.toml`, `go.mod` to detect language, framework, test runner.
   - Write findings to `.agent-memory/context/project-context.md` using the context-keeper
     7-section format (include the `*Source: docs/ ... This file is a cache.*` pointer line).
   - If NO docs exist, suggest creating the Regel-13 skeleton (HOW-TO-USE.md + docs/).
   - Ask the user to confirm or supplement the detected context

7. **Optional: Wiki Integration (config.json)**

   Ask the user: "Soll dieses Projekt mit dem Obsidian Wiki verbunden werden? (Pfad zum Wiki angeben oder 'nein')"

   - If user says no or skips → do NOT create config.json, continue to step 8.
   - If user provides a wiki path:
     1. Validate: check if `{wiki_path}/CLAUDE.md` exists. If not → warn "Wiki not found at this path." and ask again or skip.
     2. Auto-detect `project_id` from the current directory name (kebab-case)
     3. **Idempotent write**: If `.agent-memory/config.json` already exists, **merge** new values into existing config (do NOT overwrite). If it does not exist, create it.
     4. Write/merge `.agent-memory/config.json`:
        ```json
        {
          "wiki_root": "{validated_wiki_path}",
          "project_id": "{auto_detected_id}",
          "project_aliases": ["{directory_name}"],
          "sync_enabled": true,
          "session_note_threshold": 2,
          "default_entrypoints": []
        }
        ```
     5. `default_entrypoints` is left empty by default. The user can add entry points later when the corresponding wiki pages exist.
     6. If the project has an entity page at `{wiki_path}/wiki/entities/{project_id}.md`, add it to `default_entrypoints` automatically.

   **Idempotency rule**: If config.json already has `wiki_root` set and `--force` was not passed, skip this step with: "Wiki already configured (wiki_root: {path}). Use --force to reconfigure."

8. **Ensure CLAUDE.md contains Knowledge Base section:**

   Check if a `CLAUDE.md` exists in the project root. If not, create one. If it exists, check if it already has a "Knowledge Base" section. If not, append the following section:

   ```markdown
   ## Knowledge Base (NotebookLM) — Required for all Agents

   This project has a curated knowledge base in Google NotebookLM.
   The registry at `.agent-memory/knowledge/notebook-registry.md` lists all
   available notebooks with their topics and keywords.

   **IMPORTANT — Instruction for EVERY Agent (Claude Code, Subagents, Scheduled Tasks):**
   You MUST check the registry before answering expert questions.
   If a matching notebook exists, open NotebookLM in the browser to query the knowledge base
   and incorporate the result into your answer.
   Do NOT answer from memory when the registry has a notebook on the topic.

   **When to consult NotebookLM:**
   - Expert questions on topics covered by notebooks
   - Best practices, reference material, or expert knowledge needed
   - Uncertainty about the right approach for Claude Code features
   - User asks for "expert knowledge", "what do the sources say", "research this"
   - Architecture decisions that benefit from collected knowledge

   **When NOT to consult:**
   - Pure code changes without knowledge requirements
   - Simple commands like "create file X" or "fix this bug"
   - Topics not covered by any notebook in the registry

   **Workflow:**
   1. Read registry: `.agent-memory/knowledge/notebook-registry.md`
   2. Identify matching notebook by keywords
   3. Open NotebookLM in browser — ask question directly (mention notebook name in prompt)
   4. Incorporate result into the answer
   ```

9. **Output summary:**

```
Agentic OS initialized!
  Directories: {count actually created}
  Files: {count actually created}
  Detected stack: {language} + {framework}
  Knowledge Base: notebook-registry.md created
  Wiki: {connected to ~/wiki/ | not configured}

  Next steps:
  1. Review .agent-memory/context/project-context.md
  2. Customize .agent-memory/identity/soul.md if needed
  3. Review .agent-memory/knowledge/notebook-registry.md
  4. Start coding — iterations will be tracked automatically
```

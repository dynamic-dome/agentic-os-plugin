#!/bin/bash
# Agentic OS — Memory Schema (Single Source of Truth)
# ----------------------------------------------------
# This file is the ONE canonical definition of the .agent-memory/ structure.
# Both consumers source it and call create_memory_structure:
#   - scripts/session-start.sh  (SessionStart hook, Auto-Init)
#   - commands/init.md          (/agentic-os:init, via `bash scripts/mem-schema.sh <dir>`)
#
# NEVER duplicate the file list elsewhere. Add a new memory file HERE only.
# A drift test (tests/validate-plugin.sh) fails if init.md's tree diverges from this.
#
# Defaults are intentionally MINIMAL (empty stores / stubs). Real content is written
# at runtime by the owning skill (see skills/DEPENDENCIES.md).

# Directories to create (mkdir -p, in order)
MEM_DIRS=(
  identity
  context
  iterations
  patterns
  quality
  learnings
  generated-skills
  knowledge
  working
)

# JSON files initialized to an empty array
MEM_JSON_ARRAY=(
  context/decisions.json
  context/open-tasks.json
  iterations/errors.json
  patterns/patterns.json
  quality/test-results.json
  quality/code-reviews.json
  learnings/learnings.json
  working/user-candidates.json
  identity/user-changelog.json
)

# Tags/signals that are NEVER promoted into the global cross-project layer
# (privacy / memory-poisoning guard). The sync-context Push pre-filter (4.A) drops any
# entry carrying one of these tags BEFORE the promotion gate, so denied facts never
# reach ~/.claude-memory/global/. Read by is_denied() in scripts/global-schema.sh.
# This array creates no files — it is a sourceable constant, not part of the store schema.
MEM_GLOBAL_DENY_TAGS=(
  credentials secret token api_key api-key password
  personal pii email phone address
  client-name internal-only private
)

# create_memory_structure <memory_dir>
# Idempotent: only creates files/dirs that are absent. Never overwrites existing data.
create_memory_structure() {
  local MEMORY_DIR="$1"
  [ -z "$MEMORY_DIR" ] && { echo "create_memory_structure: missing dir arg" >&2; return 1; }

  local d
  for d in "${MEM_DIRS[@]}"; do
    mkdir -p "$MEMORY_DIR/$d"
  done

  local f
  for f in "${MEM_JSON_ARRAY[@]}"; do
    [ -f "$MEMORY_DIR/$f" ] || echo "[]" > "$MEMORY_DIR/$f"
  done

  # quality-score.json — structured default
  [ -f "$MEMORY_DIR/quality/quality-score.json" ] || cat > "$MEMORY_DIR/quality/quality-score.json" << 'EOFILE'
{"last_updated": null, "test_health": {"current_score": null, "trend": "unknown"}, "code_quality": {"current_score": null, "trend": "unknown"}}
EOFILE

  # working/current-session.json — volatile working memory
  if [ ! -f "$MEMORY_DIR/working/current-session.json" ]; then
    local WM_DATE
    WM_DATE=$(date +%Y-%m-%d 2>/dev/null || echo "unknown")
    printf '{"session_start": "%s", "errors_this_session": [], "learnings_draft": []}\n' "$WM_DATE" > "$MEMORY_DIR/working/current-session.json"
  fi

  # Markdown stubs
  [ -f "$MEMORY_DIR/iterations/iteration-log.md" ] || printf '# Iteration Log\n\n*No entries yet.*\n' > "$MEMORY_DIR/iterations/iteration-log.md"
  [ -f "$MEMORY_DIR/patterns/patterns.md" ]        || printf '# Pattern Catalog\n\n*No patterns detected yet.*\n' > "$MEMORY_DIR/patterns/patterns.md"
  [ -f "$MEMORY_DIR/learnings/learnings.md" ]      || printf '# Learnings\n\n*No session learnings yet.*\n' > "$MEMORY_DIR/learnings/learnings.md"

  # identity/soul-candidates.md — soul.md growth queue (Stufe B): wrap-up appends proposed
  # identity signals here; session-bootstrap surfaces them for a [j/n] gate. NEVER auto-written
  # into soul.md without explicit user confirmation (trust boundary: conversation-only).
  [ -f "$MEMORY_DIR/identity/soul-candidates.md" ] || printf '# Soul Candidates\n\n*Keine offenen Kandidaten.*\n' > "$MEMORY_DIR/identity/soul-candidates.md"

  # knowledge/notebook-registry.md
  [ -f "$MEMORY_DIR/knowledge/notebook-registry.md" ] || printf '# NotebookLM Knowledge Base Registry\n\n*No notebooks registered yet. Add entries here as you create NotebookLM knowledge bases.*\n\n## Active Notebooks\n\n(none)\n\n## When to consult NotebookLM\n- For expert knowledge on topics covered by a notebook\n- When best practices or reference material is needed\n- When uncertain about the right approach\n' > "$MEMORY_DIR/knowledge/notebook-registry.md"

  # session-summary.md
  [ -f "$MEMORY_DIR/session-summary.md" ] || cat > "$MEMORY_DIR/session-summary.md" << 'EOFILE'
# Last Session

*First session — system freshly initialized.*

## Next Steps
1. Review project context
2. Start first coding iteration
EOFILE

  # identity/soul.md
  [ -f "$MEMORY_DIR/identity/soul.md" ] || cat > "$MEMORY_DIR/identity/soul.md" << 'EOFILE'
# Agent Identity

## Communication
- Language: en (switch to de if user writes in German)
- Brevity: 3/5 (balanced)
- Proactivity: 3/5

## Guard Rails
- Confirm before deleting files
- Justify new dependencies
- For multi-file changes: write a brief plan first
- No architecture decisions without discussion

## Priorities
1. Correctness over speed
2. Simplicity over cleverness
3. Working code over perfect code
EOFILE

  # identity/user.md
  if [ ! -f "$MEMORY_DIR/identity/user.md" ]; then
    local TODAY
    TODAY=$(date +%Y-%m-%d 2>/dev/null || echo "unknown")
    cat > "$MEMORY_DIR/identity/user.md" << EOFILE
# User Profile

*Initialized: ${TODAY}*

## Preferences
- (Will be populated through observed patterns)

## Work Style
- (Will be populated through session observations)

## Known Corrections
- (Recorded when user corrects agent behavior 3+ times)
EOFILE
  fi

  # context/project-context.md is NOT created here — it needs stack auto-detection,
  # which differs between the hook (inline grep) and /init (LLM + user confirmation).
  # Each consumer writes project-context.md itself after calling this function.
}

# Allow direct invocation: `bash mem-schema.sh <dir>` (used by commands/init.md)
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  create_memory_structure "$1"
fi

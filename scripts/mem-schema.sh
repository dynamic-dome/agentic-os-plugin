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

# Where this file lives (resolved when sourced): scripts/soul-template.txt sits next to it
MEM_SCHEMA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Directories to create (mkdir -p, in order)
MEM_DIRS=(
  identity
  context
  iterations
  patterns
  learnings
  generated-skills
  working
)

# JSON files initialized to an empty array
MEM_JSON_ARRAY=(
  context/decisions.json
  context/open-tasks.json
  iterations/errors.json
  patterns/patterns.json
  learnings/learnings.json
  working/user-candidates.json
  identity/user-changelog.json
)

# Non-array files that create_memory_structure ALWAYS writes on a first init (stubs/defaults).
# Together with MEM_JSON_ARRAY this is the complete list of files the init must produce:
# session-start.sh verifies every entry after the Auto-Init (DCO #9680), and
# tests/test-session-start-nested-guard.sh fails if this list and the function drift apart.
# user.md is written only while absent, but a first init always finds it absent, so it is listed.
# context/project-context.md is NOT here: each consumer writes it itself (see end of the function).
MEM_INIT_FILES=(
  working/current-session.json
  iterations/iteration-log.md
  patterns/patterns.md
  learnings/learnings.md
  identity/soul-candidates.md
  session-summary.md
  identity/soul.md
  identity/user.md
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

  # working/current-session.json — volatile working memory
  if [ ! -f "$MEMORY_DIR/working/current-session.json" ]; then
    local WM_DATE
    WM_DATE=$(date +%Y-%m-%d 2>/dev/null || echo "unknown")
    printf '{"session_start": "%s", "errors_this_session": [], "learnings_draft": []}\n' "$WM_DATE" > "$MEMORY_DIR/working/current-session.json"
  fi

  # Markdown stubs
  [ -f "$MEMORY_DIR/iterations/iteration-log.md" ] || printf '# Iteration Log\n\n*No entries yet.*\n' > "$MEMORY_DIR/iterations/iteration-log.md"
  [ -f "$MEMORY_DIR/patterns/patterns.md" ]        || printf '# Pattern Catalog\n\n*No patterns detected yet.*\n' > "$MEMORY_DIR/patterns/patterns.md"
  # Byte-identical to apply_wrapup.py's render of an empty learnings.json (projection header).
  [ -f "$MEMORY_DIR/learnings/learnings.md" ]      || printf '# Learnings\n\n*Auto-generated from learnings.json — do not edit directly.*\n' > "$MEMORY_DIR/learnings/learnings.md"

  # identity/soul-candidates.md — soul.md growth queue (Stufe B): wrap-up appends proposed
  # identity signals here; session-bootstrap surfaces them for a [j/n] gate. NEVER auto-written
  # into soul.md without explicit user confirmation (trust boundary: conversation-only).
  [ -f "$MEMORY_DIR/identity/soul-candidates.md" ] || printf '# Soul Candidates\n\n*Keine offenen Kandidaten.*\n' > "$MEMORY_DIR/identity/soul-candidates.md"

  # session-summary.md
  [ -f "$MEMORY_DIR/session-summary.md" ] || cat > "$MEMORY_DIR/session-summary.md" << 'EOFILE'
# Last Session

*First session — system freshly initialized.*

## Next Steps
1. Review project context
2. Start first coding iteration
EOFILE

  # identity/soul.md: the current part of scripts/soul-template.txt - the ONE home of the template lines (the
  # briefing filters every template line, current and earlier versions, out of its Identity line; DCO #10148).
  # A missing template creates nothing: the hook's completeness check then reports the store as incomplete.
  if [ ! -f "$MEMORY_DIR/identity/soul.md" ] && [ -f "$MEM_SCHEMA_DIR/soul-template.txt" ]; then
    sed '/^<!-- earlier versions/,$d' "$MEM_SCHEMA_DIR/soul-template.txt" | tr -d '\r' > "$MEMORY_DIR/identity/soul.md"
  fi

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

# exclude_store_from_git <project_dir>
# Adds `.agent-memory/` to the repository's LOCAL exclude file (git rev-parse --git-path info/exclude:
# .git/info/exclude, or the shared one in a worktree), so a freshly created store does not show up in
# `git status`. No .gitignore is touched, nothing is shared. Idempotent and fail-soft (always returns 0;
# no git, no repo or an unwritable file simply means nothing happens).
# Call it ONLY after a fresh creation, never from create_memory_structure: that function also runs as
# the backfill of an existing store, and a repo that tracks its store on purpose must stay untouched.
exclude_store_from_git() {
  local dir="$1" excl
  [ -n "$dir" ] && [ -d "$dir" ] || return 0
  command -v git > /dev/null 2>&1 || return 0
  excl="$(git -C "$dir" rev-parse --git-path info/exclude 2>/dev/null)" || return 0
  [ -n "$excl" ] || return 0
  case "$excl" in /*|[A-Za-z]:*) ;; *) excl="$dir/$excl" ;; esac   # relative to the git -C directory
  mkdir -p "$(dirname "$excl")" 2>/dev/null || return 0
  grep -qxF '.agent-memory/' "$excl" 2>/dev/null && return 0
  # an exclude file whose last line has no newline must not swallow our line
  if [ -s "$excl" ] && [ -n "$(tail -c1 "$excl" 2>/dev/null)" ]; then
    printf '\n' >> "$excl" 2>/dev/null || return 0
  fi
  printf '.agent-memory/\n' >> "$excl" 2>/dev/null
  return 0
}

# Allow direct invocation: `bash mem-schema.sh <dir>` (used by commands/init.md).
# The store counts as fresh only when the directory did not exist before this call.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  _store_existed=false
  [ -d "$1" ] && _store_existed=true
  create_memory_structure "$1"
  _rc=$?
  if [ "$_rc" -eq 0 ] && [ "$_store_existed" = false ] && [ "$(basename "$1")" = ".agent-memory" ]; then
    exclude_store_from_git "$(dirname "$1")"
  fi
  exit "$_rc"
fi

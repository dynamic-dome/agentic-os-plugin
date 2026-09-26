#!/bin/bash
# Contract test: obsidian-sync Step 4.5 Decision Promotion (v4.5.x).
#
# decisions.json is the LEADING store; the wiki only receives a projection.
# The promotion marker (wiki_ref + promoted_at) must be documented on BOTH
# writers (obsidian-sync batch path, context-keeper live path) — otherwise
# the same decision gets projected twice.

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OBSIDIAN_SYNC="$ROOT_DIR/skills/obsidian-sync/SKILL.md"
CONTEXT_KEEPER="$ROOT_DIR/skills/context-keeper/SKILL.md"

fail() {
    echo "FAIL: $1"
    exit 1
}

grep -q "Step 4.5: Decision Promotion" "$OBSIDIAN_SYNC" \
    || fail "obsidian-sync must carry a Step 4.5 Decision Promotion section"

# Scope the step-specific checks to the Step 4.5 section itself, not the whole file.
STEP45="$(awk '/^## Step 4.5: Decision Promotion/,/^## Step 5/' "$OBSIDIAN_SYNC")"
[ -n "$STEP45" ] || fail "could not extract the Step 4.5 section"

for token in \
    "wiki_ref" \
    "promoted_at" \
    "architecture-decision" \
    "stack-change" \
    "scope-decision" \
    "wiki/entities/{project_id}.md" \
    "Architecture Decisions" \
    "supersedes" \
    'status: "active"' \
    "entity-creation exception" \
    "Duplicate guard" \
    "self-healing"
do
    printf '%s' "$STEP45" | grep -qF -- "$token" \
        || fail "obsidian-sync Step 4.5 must mention $token"
done

# Marker only after a SUCCESSFUL write (both writers)
printf '%s' "$STEP45" | grep -q "successful wiki write" \
    || fail "obsidian-sync Step 4.5 must bind the marker to a successful wiki write"

# Leading-store direction must be stated (projection, never reverse).
# Token kept single-line safe: SKILL.md prose wraps lines, grep is line-based.
printf '%s' "$STEP45" | grep -qi "only ever receives a projection" \
    || fail "obsidian-sync must state the projection direction (decisions.json leads)"

# Stale reference from pre-1.2 must be gone (context-keeper has no Step 4.5).
# Whitespace-normalized so a line wrap between words cannot hide the reference.
tr -s '[:space:]' ' ' < "$OBSIDIAN_SYNC" | grep -q "context-keeper Step 4.5" \
    && fail "obsidian-sync still references non-existent context-keeper Step 4.5"

# The live-path counterpart must document the shared marker (both fields),
# bound to a successful write, with the failure case explicit.
for token in \
    "wiki_ref" \
    "promoted_at" \
    "obsidian-sync Step 4.5" \
    "must NOT set the marker"
do
    grep -qF -- "$token" "$CONTEXT_KEEPER" \
        || fail "context-keeper Step 3.5 must document: $token"
done
grep -q "successful" "$CONTEXT_KEEPER" \
    || fail "context-keeper must bind the marker to a successful wiki write"

# Field-extension must not be contradicted by an unqualified append-only rule.
tr -s '[:space:]' ' ' < "$CONTEXT_KEEPER" | grep -qi "may only GAIN fields" \
    || fail "context-keeper must reconcile field-extension with the decisions.json write mode"

echo "Obsidian-sync decision promotion contract passed."

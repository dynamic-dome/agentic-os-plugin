#!/bin/bash
# Contract test: README and wrap-up skill must document the long-term memory routine.

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
README="$ROOT_DIR/README.md"
WRAP_UP="$ROOT_DIR/skills/wrap-up/SKILL.md"

fail() {
    echo "FAIL: $1"
    exit 1
}

grep -q "Long-Term Memory Routine" "$README" \
    || fail "README must expose a Long-Term Memory Routine section"

for token in \
    ".agent-memory/" \
    ".agent-memory/learnings/learnings.json" \
    ".agent-memory/context/decisions.json" \
    ".agent-memory/context/open-tasks.json" \
    ".agent-memory/session-summary.md" \
    "wrap-up" \
    "agentic-os:log" \
    "context-keeper"
do
    grep -q "$token" "$README" \
        || fail "README Long-Term Memory Routine must mention $token"
done

grep -q "long-term-memory-routine" "$WRAP_UP" \
    || fail "wrap-up skill must carry the long-term-memory-routine anchor"

for token in \
    "central .agent-memory/ knowledge base" \
    "iterations/iteration-log.md" \
    "learnings/learnings.json" \
    "context/decisions.json" \
    "context/open-tasks.json" \
    "session-summary.md"
do
    grep -q "$token" "$WRAP_UP" \
        || fail "wrap-up skill long-term memory route must mention $token"
done

echo "Wrap-up long-term memory contract passed."

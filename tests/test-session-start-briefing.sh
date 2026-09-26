#!/usr/bin/env bash
# session-start.sh must deliver its briefing where the MODEL sees it.
#
# Measured 2026-09-08 (Claude Code 2.1.263, transcript 15c40732): a SessionStart
# hook that returns {"systemMessage": ...} lands in the transcript as
# `hook_system_message` and is shown to the USER only — the model context never
# contains it. Only `hookSpecificOutput.additionalContext` is injected
# (`hook_additional_context`). The briefing, its "Next steps" and the RECOVERY
# line were therefore invisible to the agent for months.
#
# This test pins: (1) the additionalContext contract, (2) the iteration counter
# against the on-disk log format (`## {date} — {type}: {title}`, 4.18.0) — the
# old `^## Iteration` grep counted 0 on every real store, (3) next steps from the
# SSoT context/open-tasks.json (open/blocked only, max 3) instead of a regex over
# session-summary.md, (4) the mechanical RECOVERY line, (5) the root open-tasks
# drift warning (formerly a SessionEnd prompt-hook promise), (6) the slash-path
# hint — `/agentic-os:wrap-up` applies the skill's `model:` frontmatter, the
# Skill tool does not (4 transcripts, 2.1.263).
set -uo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
MEM="$TMP/proj/.agent-memory"
mkdir -p "$MEM/context" "$MEM/iterations" "$MEM/patterns" "$MEM/identity" "$MEM/working"
FAIL=0
pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; FAIL=1; }

cat > "$MEM/session-summary.md" <<'EOF'
# Last Session

*Date: 2026-09-01 10:00*
*Agent: Claude Code*

## What Was Done

- Something

## Next Steps

1. STALE-FROM-SUMMARY should not be the source
EOF
cat > "$MEM/iterations/iteration-log.md" <<'EOF'
# Iteration Log

## 2026-09-01 — feature: first thing
- **Type:** feature

## 2026-09-02 — bugfix: second thing
- **Type:** bugfix

## Iteration #3: legacy header format
- legacy
EOF
echo '[{"id":"err-001"},{"id":"err-002"}]' > "$MEM/iterations/errors.json"
echo '[{"id":"P001"}]' > "$MEM/patterns/patterns.json"
cat > "$MEM/context/open-tasks.json" <<'EOF'
[
  {"id": "T-001", "title": "Done task must not appear", "status": "done"},
  {"id": "T-002", "title": "First open task — Ümlaut", "status": "open"},
  {"id": "T-003", "title": "Blocked task", "status": "blocked"},
  {"id": "T-004", "title": "Second open task", "status": "open"},
  {"id": "T-005", "title": "Fourth candidate beyond cap", "status": "open"}
]
EOF
printf '# Agent Identity\n\n- Language: de\n' > "$MEM/identity/soul.md"
# Un-consolidated session: dirty flag older than 30 minutes -> RECOVERY line.
printf '{"session_id": "deadbeef-1", "dirty": true, "write_count": 3, "touched_files": ["a.py"]}\n' > "$MEM/working/dirty-deadbeef-1.json"
touch -d '-2 hours' "$MEM/working/dirty-deadbeef-1.json"
# Root-level open-tasks drift (canonical location is context/).
echo '[]' > "$MEM/open-tasks.json"

cd "$TMP/proj"
OUT=$(echo '{"session_id":"t"}' | CLAUDE_PROJECT_DIR="$TMP/proj" bash "$DIR/scripts/session-start.sh" 2>"$TMP/err.txt")
RC=$?
[ "$RC" -eq 0 ] && pass "exit 0 (fail-soft)" || fail "exit $RC: $(cat "$TMP/err.txt")"

CTX=$(printf '%s' "$OUT" | PYTHONIOENCODING=utf-8 python -c "
import sys, json
d = json.loads(sys.stdin.read())
assert 'systemMessage' not in d, 'systemMessage is user-only, model never sees it'
h = d['hookSpecificOutput']
assert h['hookEventName'] == 'SessionStart', h
print(h['additionalContext'])
" 2>"$TMP/py.txt")
if [ -n "$CTX" ]; then
  pass "contract: hookSpecificOutput.additionalContext (no systemMessage)"
else
  fail "contract: $(cat "$TMP/py.txt") — raw: $(printf '%s' "$OUT" | head -c 300)"
fi

echo "$CTX" | grep -q "3 iterations" && pass "stats: counts on-disk log format (2 dated + 1 legacy = 3)" || fail "stats: iteration count wrong: $(echo "$CTX" | grep -o '[0-9]* iterations')"
echo "$CTX" | grep -q "T-002" && echo "$CTX" | grep -q "T-003" && echo "$CTX" | grep -q "T-004" && pass "next steps: open/blocked tasks from open-tasks.json" || fail "next steps: T-002/T-003/T-004 missing"
echo "$CTX" | grep -q "T-001" && fail "next steps: done task T-001 leaked" || pass "next steps: done tasks excluded"
echo "$CTX" | grep -q "T-005" && fail "next steps: cap of 3 not applied (T-005 shown)" || pass "next steps: capped at 3"
echo "$CTX" | grep -q "STALE-FROM-SUMMARY" && fail "next steps: still sourced from session-summary.md regex" || pass "next steps: SSoT is open-tasks.json, not the summary"
echo "$CTX" | grep -q "4 open" && pass "next steps: total open/blocked count shown" || fail "next steps: total count missing"
echo "$CTX" | grep -q "RECOVERY" && pass "recovery: stale dirty file flagged" || fail "recovery: RECOVERY line missing"
echo "$CTX" | grep -qi "root drift" && pass "drift: root open-tasks.json flagged" || fail "drift: root open-tasks.json not flagged"
echo "$CTX" | grep -q "First open task — Ümlaut" && ! echo "$CTX" | grep -q "Ã" && pass "utf8: non-ASCII survives (no cp1252 mojibake)" || fail "utf8: mojibake in briefing: $(echo "$CTX" | grep -o '.\{0,20\}Ã.\{0,20\}' | head -1)"
echo "$CTX" | grep -q "/agentic-os:wrap-up" && pass "hint: slash-path for wrap-up (model frontmatter applies only there)" || fail "hint: /agentic-os:wrap-up missing"

# Fresh init path still works and still uses the same contract.
mkdir -p "$TMP/fresh" && cd "$TMP/fresh"
OUT2=$(echo '{}' | CLAUDE_PROJECT_DIR="$TMP/fresh" bash "$DIR/scripts/session-start.sh" 2>/dev/null)
printf '%s' "$OUT2" | grep -q '"additionalContext"' && [ -d "$TMP/fresh/.agent-memory" ] && pass "fresh init: store created + additionalContext" || fail "fresh init: broken"

rm -rf "$TMP"
exit $FAIL

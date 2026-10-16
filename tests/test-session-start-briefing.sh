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
# Not counted: clean, too young, wrap-up's own tail writes (<=5 after the marker).
printf '{"session_id": "deadbeef-2", "dirty": false, "write_count": 9}\n' > "$MEM/working/dirty-deadbeef-2.json"
touch -d '-2 hours' "$MEM/working/dirty-deadbeef-2.json"
printf '{"session_id": "deadbeef-3", "dirty": true, "write_count": 1}\n' > "$MEM/working/dirty-deadbeef-3.json"
printf '{"session_id": "deadbeef-4", "dirty": true, "last_consolidated_at": "2026-09-01T10:00:00", "writes_since_consolidation": 2}\n' > "$MEM/working/dirty-deadbeef-4.json"
touch -d '-2 hours' "$MEM/working/dirty-deadbeef-4.json"
# Counted: the same few writes after a HEADLESS stamp are real work (night run has no tail).
printf '{"session_id": "deadbeef-5", "dirty": true, "last_consolidated_at": "2026-09-01T10:00:00", "last_consolidated_by": "headless", "writes_since_consolidation": 2}\n' > "$MEM/working/dirty-deadbeef-5.json"
touch -d '-2 hours' "$MEM/working/dirty-deadbeef-5.json"
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
echo "$CTX" | grep -q "RECOVERY: 2 unkonsolidierte" && pass "recovery: counts only stale dirty work (clean, young, tail writes skipped; headless has no tail)" || fail "recovery: wrong count: $(echo "$CTX" | grep -o 'RECOVERY: [0-9]*')"
echo "$CTX" | grep -qi "root drift" && pass "drift: root open-tasks.json flagged" || fail "drift: root open-tasks.json not flagged"
echo "$CTX" | grep -q "First open task — Ümlaut" && ! echo "$CTX" | grep -q "Ã" && pass "utf8: non-ASCII survives (no cp1252 mojibake)" || fail "utf8: mojibake in briefing: $(echo "$CTX" | grep -o '.\{0,20\}Ã.\{0,20\}' | head -1)"
echo "$CTX" | grep -q "/agentic-os:wrap-up" && pass "hint: slash-path for wrap-up (model frontmatter applies only there)" || fail "hint: /agentic-os:wrap-up missing"

# DCO #9711: blocked first, then newest updated (else created) - the oldest task must not lead.
# DCO #8966: a marker younger than 30 min counts once it has provably ended (harvest_state.is_quiet).
MEM2="$TMP/proj2/.agent-memory"
mkdir -p "$MEM2/context" "$MEM2/working"
cat > "$MEM2/context/open-tasks.json" <<'EOF'
[
  {"id": "T-OLD", "title": "oldest", "status": "open", "updated": "2026-07-06"},
  {"id": "T-NEW", "title": "newest", "status": "open", "created": "2026-10-01", "updated": "2026-10-11"},
  {"id": "T-MID", "title": "middle", "status": "open", "created": "2026-09-01"},
  {"id": "T-BLK", "title": "blocked", "status": "blocked", "updated": "2026-08-01"}
]
EOF
iso() { python -c "import datetime,sys; print((datetime.datetime.now().astimezone() - datetime.timedelta(minutes=int(sys.argv[1]))).isoformat(timespec='seconds'))" "$1"; }
W2="$MEM2/working"
TR="$TMP/resumed.jsonl"; echo '{}' > "$TR"
TRW=$(cygpath -m "$TR" 2>/dev/null || printf '%s' "$TR")  # a marker holds a native path
# counted: ended after its last activity
printf '{"session_id": "e1", "dirty": true, "updated": "%s", "session_end": {"ended_at": "%s"}}\n' "$(iso 10)" "$(iso 5)" > "$W2/dirty-e1.json"
# counted: fresh mtime (a stale stamp rewrote it) but 13 h without activity
printf '{"session_id": "e2", "dirty": true, "updated": "%s"}\n' "$(iso 780)" > "$W2/dirty-e2.json"
# not counted: wrote again after an earlier session_end (running)
printf '{"session_id": "e3", "dirty": true, "updated": "%s", "session_end": {"ended_at": "%s"}}\n' "$(iso 1)" "$(iso 10)" > "$W2/dirty-e3.json"
# not counted: resumed - its transcript was written 20 min after the end
printf '{"session_id": "e4", "dirty": true, "updated": "%s", "session_end": {"ended_at": "%s"}, "transcript_path": "%s"}\n' "$(iso 25)" "$(iso 20)" "$TRW" > "$W2/dirty-e4.json"
# not counted: ended, but only a judged wrap-up's tail writes
printf '{"session_id": "e5", "dirty": true, "updated": "%s", "session_end": {"ended_at": "%s"}, "last_consolidated_at": "%s", "last_consolidated_by": "wrap-up", "writes_since_consolidation": 2}\n' "$(iso 10)" "$(iso 5)" "$(iso 15)" > "$W2/dirty-e5.json"
# not counted: young, no end, recent activity (a parallel session)
printf '{"session_id": "e6", "dirty": true, "updated": "%s"}\n' "$(iso 2)" > "$W2/dirty-e6.json"
mkdir -p "$TMP/proj2" && cd "$TMP/proj2"
CTX2=$(echo '{"session_id":"t2"}' | CLAUDE_PROJECT_DIR="$TMP/proj2" bash "$DIR/scripts/session-start.sh" 2>/dev/null | PYTHONIOENCODING=utf-8 python -c "import sys,json; print(json.loads(sys.stdin.read())['hookSpecificOutput']['additionalContext'])" 2>/dev/null)
echo "$CTX2" | grep -q "Next steps: 4 open — T-BLK \[blocked\]: blocked; T-NEW: newest; T-MID: middle" \
  && pass "next steps: blocked first, then newest updated/created (oldest not leading)" \
  || fail "next steps order: $(echo "$CTX2" | grep -o 'Next steps: [^|]*')"
echo "$CTX2" | grep -q "RECOVERY: 2 unkonsolidierte" \
  && pass "recovery: young markers count once ended (session_end / 12 h quiet); running, resumed, tail, parallel do not" \
  || fail "recovery young: $(echo "$CTX2" | grep -o 'RECOVERY: [0-9]*' || echo 'no RECOVERY line')"

# Fresh init path still works and still uses the same contract.
mkdir -p "$TMP/fresh" && cd "$TMP/fresh"
OUT2=$(echo '{}' | CLAUDE_PROJECT_DIR="$TMP/fresh" bash "$DIR/scripts/session-start.sh" 2>/dev/null)
printf '%s' "$OUT2" | grep -q '"additionalContext"' && [ -d "$TMP/fresh/.agent-memory" ] && pass "fresh init: store created + additionalContext" || fail "fresh init: broken"

rm -rf "$TMP"
exit $FAIL

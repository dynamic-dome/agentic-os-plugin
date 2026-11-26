#!/usr/bin/env bash
# memory-thresholds.sh — single source of truth for .agent-memory scaling thresholds.
# Used by: session-start.sh (THRESHOLD + PFLEGELAUF lines in the start briefing), session-bootstrap (HEALTH)
# and wrapup_core.py (only the PFLEGELAUF line, as `pflegelauf_due`). Only the PFLEGELAUF line points to
# /agentic-os:maintain; that command does not run this script.
# Exit 0 = all within limits. Exit 10 = at least one threshold exceeded (lines on stdout).
# 5.2.1 (D-021, owner decision 2026-10-07): a
# threshold only REPORTS. Nothing is archived, pruned, compacted or moved because of a
# count - archive files are invisible to the Atlas RAG, and count-based archiving took
# long-term and importance>=4 learnings out of retrieval. The lines name what grew;
# tests/test-memory-thresholds.sh pins the wording.
# Usage: bash scripts/memory-thresholds.sh [path-to-.agent-memory]  (default: ./.agent-memory)

set -u
MEM="${1:-.agent-memory}"
EXCEEDED=0
PFLEGE_NEU=30   # new learnings since the last maintenance run that make a run due (6.0.0)

# 6.0.0: a THRESHOLD line names no command - /agentic-os:maintain (the Pflegelauf) does not act on these counts;
# only the PFLEGELAUF line below points there.
note() { echo "THRESHOLD: $1 — report only, nothing is moved (D-021)"; EXCEEDED=1; }

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
case "$(uname -s 2>/dev/null)" in  # on Windows python3 is often only a launcher stub (as session-start.sh)
  MINGW*|MSYS*|CYGWIN*) PY_ORDER="python python3" ;;
  *) PY_ORDER="python3 python" ;;
esac

count_ids() { # number of learnings in a learnings.json
  # 6.0.2: parsed by learning_count.py - the same count the Pflegelauf stamp uses (L154); format, tags and text of
  # a row do not matter. Without python or for a file it cannot parse: the "id" keys by text as a fallback.
  [ -f "$1" ] || { echo 0; return; }
  local py n
  for py in $PY_ORDER; do
    command -v "$py" >/dev/null 2>&1 || continue
    n=$("$py" "$SELF_DIR/learning_count.py" "$1" 2>/dev/null) && [ -n "$n" ] && { echo "$n"; return; }
  done  # a name that exists but fails (launcher stub, broken install) does not stop the next one
  grep -o -E '(^|[^\\])"id"[[:blank:]]*:' "$1" | wc -l | tr -d ' '
}

count_ids_legacy() { # the count before 6.0.2 (every '"id"'), only for a stamp written before 6.0.2
  [ -f "$1" ] && grep -o '"id"' "$1" | wc -l | tr -d ' ' || echo 0
}

# iteration-log.md: max 100 entries (## headers)
# NOTE: no `|| echo 0` after grep -c — grep prints "0" itself on no-match (exit 1),
# the fallback would append a SECOND 0 and break the numeric compare (Codex finding).
if [ -f "$MEM/iterations/iteration-log.md" ]; then
  n=$(grep -c '^## ' "$MEM/iterations/iteration-log.md" 2>/dev/null); n=${n:-0}
  [ "$n" -gt 100 ] && note "iteration-log.md has $n entries (soft limit 100)"
fi

# learnings.json: max 1000 entries. Owner decision 2026-10-06: learnings are collected, not
# pruned, until a retrieval/condensation strategy exists (archive files are invisible to the
# Atlas RAG and the wrap-up lifecycle never promotes layers). The ceiling only catches runaway growth.
LEARNINGS_N=$(count_ids "$MEM/learnings/learnings.json")
[ "$LEARNINGS_N" -gt 1000 ] && note "learnings.json has $LEARNINGS_N entries (ceiling 1000)"

# open-tasks.json: max 30 done entries kept inline
if [ -f "$MEM/context/open-tasks.json" ]; then
  n=$(grep -c '"status": *"done"' "$MEM/context/open-tasks.json" 2>/dev/null); n=${n:-0}
  [ "$n" -gt 30 ] && note "open-tasks.json has $n done entries (soft limit 30)"
fi

# session-summary.md: max 30 lines (handoff-mode append may exceed briefly)
if [ -f "$MEM/session-summary.md" ]; then
  n=$(wc -l < "$MEM/session-summary.md" | tr -d ' ')
  [ "$n" -gt 40 ] && note "session-summary.md has $n lines (soft limit 40; the next wrap-up rewrites it - in the AI workspace it is the central handoff, capped at 5 blocks by wrap-up Step 7.6a)"
fi

# learnings.md: max 2000 lines (projection of learnings.json, one line per entry; see ceiling above)
if [ -f "$MEM/learnings/learnings.md" ]; then
  n=$(wc -l < "$MEM/learnings/learnings.md" | tr -d ' ')
  [ "$n" -gt 2000 ] && note "learnings.md has $n lines (ceiling 2000; a projection - re-render with apply_wrapup.py --render-learnings)"
fi

# working/: stale scratch files older than 7 days (scripts, tmp) — session artifacts
# like current-session.json and user-candidates.json are exempt (living data).
if [ -d "$MEM/working" ]; then
  # *.bak is evidence (e.g. a quarantined store file), not scratch - never counted here.
  stale=$(find "$MEM/working" -maxdepth 1 -type f \( -name '*.py' -o -name '*.tmp' \) -mtime +7 2>/dev/null | wc -l | tr -d ' ')
  [ "$stale" -gt 0 ] && note "working/ has $stale scratch file(s) (*.py, *.tmp) older than 7 days"

  # dirty-*.json recovery markers accumulate: wrap-up only resets its own session, so
  # consolidated (dirty:false) AND superseded (dirty:true, older than last_wrapup)
  # markers pile up. Coarse total-count signal here (the exact eligibility rule +
  # deletion live in scripts/gc_dirty_markers.py — a count of dirty:false alone would
  # miss the superseded dirty:true markers).
  n_dirty=$(find "$MEM/working" -maxdepth 1 -name 'dirty-*.json' 2>/dev/null | wc -l | tr -d ' ')
  [ "$n_dirty" -gt 10 ] && note "working/ has $n_dirty dirty-marker(s) — preview with scripts/gc_dirty_markers.py; only closed markers are removed (daily run, D-024)"
fi

# Pflegelauf due (6.0.0): learnings added since the last maintenance run. Base = working/pflegelauf.json,
# written by apply_wrapup.py (pflegelauf_stamp); its id_keys counts the learnings like count_ids (learning_count.py,
# same measure, L154), only those dated before the run's start date (6.0.2): what came in during a run counts as new.
# A stamp without "zaehlung": 2 was written before 6.0.2 over every '"id"' text hit; it is compared with that count
# until the next run writes a new stamp.
# No stamp = no run yet, all learnings count. A stamp that cannot be read (no id_keys or no date) or whose
# id_keys is above today's count (learnings removed) gives no line: unclear is not due.
stamp="$MEM/working/pflegelauf.json"
total=$LEARNINGS_N
if [ ! -f "$stamp" ]; then
  [ "$total" -ge "$PFLEGE_NEU" ]     && { echo "PFLEGELAUF: $total Learnings, noch kein Pflegelauf → /agentic-os:maintain"; EXCEEDED=1; }
else
  base=$(grep -o '"id_keys": *[0-9]*' "$stamp" 2>/dev/null | head -1 | grep -o '[0-9]*$')
  day=$(grep -o '"date": *"[^"]*"' "$stamp" 2>/dev/null | head -1 | sed 's/.*: *"\(.*\)"/\1/')
  grep -q '"zaehlung": *2' "$stamp" 2>/dev/null || total=$(count_ids_legacy "$MEM/learnings/learnings.json")
  if [ -n "$base" ] && [ -n "$day" ] && [ "$total" -ge "$base" ] && [ $((total - base)) -ge "$PFLEGE_NEU" ]; then
    echo "PFLEGELAUF: $((total - base)) neue Learnings seit $day → /agentic-os:maintain"; EXCEEDED=1
  fi
fi

[ "$EXCEEDED" -eq 1 ] && exit 10
exit 0

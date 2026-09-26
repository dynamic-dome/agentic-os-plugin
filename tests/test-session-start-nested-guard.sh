#!/usr/bin/env bash
# session-start.sh must never auto-init a .agent-memory INSIDE an existing .agent-memory.
# cost-trace.sh must likewise refuse to create metrics/ inside a nested .agent-memory cwd.
set -uo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
mkdir -p "$TMP/proj/.agent-memory/working"
cd "$TMP/proj/.agent-memory/working"
# session-start.sh derives PROJECT_DIR from $CLAUDE_PROJECT_DIR (NOT from stdin
# JSON's "cwd" field, which the script never reads) — set it explicitly to the
# real second start path (session-start.sh line 19) so the test hits the actual
# derivation instead of the unused "." default.
echo '{"cwd":"'"$TMP/proj/.agent-memory/working"'","session_id":"t"}' | \
  CLAUDE_PROJECT_DIR="$TMP/proj/.agent-memory/working" bash "$DIR/scripts/session-start.sh" > "$TMP/out.txt" 2>&1
RC=$?
FAIL=0
if [ -d "$TMP/proj/.agent-memory/working/.agent-memory" ] || [ -d "$TMP/proj/.agent-memory/.agent-memory" ]; then
  echo "  FAIL: nested .agent-memory created"; FAIL=1
else
  echo "  PASS: no nested .agent-memory"
fi
if [ "$RC" -eq 0 ]; then echo "  PASS: exit 0 (fail-soft)"; else echo "  FAIL: exit $RC"; FAIL=1; fi

# Second case: cost-trace.sh run from a cwd inside .agent-memory must not create
# a nested metrics/ dir (controller ruling: this is the actual origin of the
# nested stores found in hygiene 2026-09, not session-start.sh).
cd "$TMP/proj/.agent-memory/working"
bash "$DIR/scripts/cost-trace.sh" append --mem .agent-memory --task test --class cheap-write --context-bytes 100 --escalated 0 > "$TMP/ct-out.txt" 2>&1
if [ -d "$TMP/proj/.agent-memory/working/.agent-memory" ]; then
  echo "  FAIL: cost-trace.sh created nested .agent-memory/metrics"; FAIL=1
else
  echo "  PASS: cost-trace.sh created no nested .agent-memory"
fi

# Third case (V7): a subdirectory of a git repo must not get its own store;
# a plain non-git directory still auto-inits (the AI workspace is not a repo).
mkdir -p "$TMP/repo/scripts/work" "$TMP/plain"
git -C "$TMP/repo" init -q
echo '{"cwd":"x","session_id":"t"}' | CLAUDE_PROJECT_DIR="$TMP/repo/scripts/work" bash "$DIR/scripts/session-start.sh" > "$TMP/sub-out.txt" 2>&1
RC=$?
if [ -d "$TMP/repo/scripts/work/.agent-memory" ]; then
  echo "  FAIL: auto-init in git subdirectory"; FAIL=1
else
  echo "  PASS: no auto-init in git subdirectory"
fi
grep -q "subdirectory of git repo" "$TMP/sub-out.txt" && echo "  PASS: subdirectory skip is explained" || { echo "  FAIL: no skip message"; FAIL=1; }
[ "$RC" -eq 0 ] && echo "  PASS: exit 0 (fail-soft)" || { echo "  FAIL: exit $RC"; FAIL=1; }
echo '{"cwd":"x","session_id":"t"}' | CLAUDE_PROJECT_DIR="$TMP/plain" bash "$DIR/scripts/session-start.sh" > "$TMP/plain-out.txt" 2>&1
if [ -d "$TMP/plain/.agent-memory" ]; then
  echo "  PASS: non-git directory still auto-inits"
else
  echo "  FAIL: non-git directory not initialised"; FAIL=1
fi
echo '{"cwd":"x","session_id":"t"}' | CLAUDE_PROJECT_DIR="$TMP/repo" bash "$DIR/scripts/session-start.sh" > "$TMP/root-out.txt" 2>&1
if [ -d "$TMP/repo/.agent-memory" ]; then
  echo "  PASS: git toplevel still auto-inits"
else
  echo "  FAIL: git toplevel not initialised"; FAIL=1
fi
# Fourth case (Codex verifier 5.0.2): subdirectory of a repo whose root HAS a store ->
# no second store, but the hook still emits the JSON briefing from the root store.
echo '{"cwd":"x","session_id":"t"}' | CLAUDE_PROJECT_DIR="$TMP/repo/scripts/work" bash "$DIR/scripts/session-start.sh" > "$TMP/sub2-out.txt" 2>&1
RC=$?
if [ -d "$TMP/repo/scripts/work/.agent-memory" ]; then
  echo "  FAIL: second store created next to a root store"; FAIL=1
else
  echo "  PASS: no second store when the repo root has one"
fi
if grep -q '"additionalContext"' "$TMP/sub2-out.txt" && grep -q "repo-root store" "$TMP/sub2-out.txt"; then
  echo "  PASS: JSON briefing served from the root store"
else
  echo "  FAIL: no JSON briefing for subdirectory session"; FAIL=1; head -5 "$TMP/sub2-out.txt"
fi
[ "$RC" -eq 0 ] && echo "  PASS: exit 0" || { echo "  FAIL: exit $RC"; FAIL=1; }

rm -rf "$TMP"
exit $FAIL

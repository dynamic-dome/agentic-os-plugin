#!/usr/bin/env bash
# test-memory-thresholds.sh — scripts/memory-thresholds.sh is the threshold SSoT.
# 5.2.1 (D-021, owner decision 2026-10-07): thresholds only REPORT. No line may
# tell anyone to archive, prune or move store entries, and the dirty-marker line
# recommends the preview, not --apply. Every case runs in a throwaway dir.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$SCRIPT_DIR/../scripts/memory-thresholds.sh"
ERRORS=0
pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; ERRORS=$((ERRORS + 1)); }

echo "=== memory-thresholds.sh tests ==="
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
MEM="$TMP/.agent-memory"
mkdir -p "$MEM/learnings" "$MEM/iterations" "$MEM/working"

learnings_json() { # $1 = number of entries
  python -c "import json,sys; print(json.dumps([{'id': f'L{i}'} for i in range(1, int(sys.argv[1]) + 1)]))" "$1"
}

learnings_json 1000 > "$MEM/learnings/learnings.json"
out=$(bash "$SCRIPT" "$MEM"); rc=$?
[ "$rc" -eq 0 ] && pass "1000 learnings are within the ceiling (exit 0)" || fail "1000 learnings -> exit $rc: $out"

learnings_json 1001 > "$MEM/learnings/learnings.json"
out=$(bash "$SCRIPT" "$MEM"); rc=$?
[ "$rc" -eq 10 ] && pass "1001 learnings exceed the ceiling (exit 10)" || fail "1001 learnings -> exit $rc"
echo "$out" | grep -q "learnings.json has 1001" && pass "THRESHOLD line names the count" || fail "no learnings line: $out"

python -c "print('\n'.join('- L%d' % i for i in range(2000)))" > "$MEM/learnings/learnings.md"
learnings_json 3 > "$MEM/learnings/learnings.json"
out=$(bash "$SCRIPT" "$MEM"); rc=$?
[ "$rc" -eq 0 ] && pass "learnings.md with 2000 lines is within the ceiling" || fail "2000 lines -> exit $rc: $out"
echo "- L2000" >> "$MEM/learnings/learnings.md"
out=$(bash "$SCRIPT" "$MEM"); rc=$?
[ "$rc" -eq 10 ] && pass "learnings.md with 2001 lines exceeds the ceiling" || fail "2001 lines -> exit $rc"

# Every store threshold at once: none of the lines may tell anyone to move entries.
learnings_json 1001 > "$MEM/learnings/learnings.json"
{ echo "# Iteration Log"; for i in $(seq 1 101); do echo "## 2026-01-01 — fix: n$i"; done; } \
  > "$MEM/iterations/iteration-log.md"
python -c "import json; print(json.dumps([{'id': f'err-{i:03d}'} for i in range(1, 52)]))" \
  > "$MEM/iterations/errors.json"
python -c "print('\n'.join('x' for _ in range(41)))" > "$MEM/session-summary.md"
for i in $(seq 1 11); do echo '{}' > "$MEM/working/dirty-s$i.json"; done
out=$(bash "$SCRIPT" "$MEM"); rc=$?
n=$(echo "$out" | grep -c '^THRESHOLD:')
[ "$rc" -eq 10 ] && [ "$n" -ge 6 ] && pass "all six store thresholds fire ($n lines)" || fail "expected >= 6 lines, got $n (rc=$rc): $out"
if echo "$out" | grep -qiE '\b(archiv(e|ing)?|prune|compact|delete|move (the|old|rest))\b'; then
  fail "a THRESHOLD line still tells someone to archive/prune/move (D-021): $(echo "$out" | grep -iE '\b(archiv(e|ing)?|prune|compact|delete|move (the|old|rest))\b')"
else
  pass "no THRESHOLD line asks to archive, prune or move store entries"
fi
if echo "$out" | grep '^THRESHOLD:' | grep -vq 'report only'; then
  fail "a THRESHOLD line does not say 'report only': $(echo "$out" | grep '^THRESHOLD:' | grep -v 'report only')"
else
  pass "every THRESHOLD line says 'report only'"
fi
if echo "$out" | grep -q -- '--apply'; then
  fail "the dirty-marker line still recommends --apply"
else
  pass "the dirty-marker line recommends the preview, not --apply"
fi
echo "$out" | grep -q 'agentic-os:maintain' && pass "lines point to /agentic-os:maintain (report)" \
  || fail "no line points to /agentic-os:maintain: $out"

echo "=== $ERRORS failure(s) ==="
[ "$ERRORS" -eq 0 ]

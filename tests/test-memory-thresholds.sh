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

# 6.0.0: the Pflegelauf rule counts learnings against working/pflegelauf.json. The older cases below test
# other thresholds, so they start from a stamp whose id_keys is far above any count here: no PFLEGELAUF line.
stamp() { printf '{"date": "2026-11-01", "id_keys": %s}\n' "$1" > "$MEM/working/pflegelauf.json"; }
stamp 99999

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
[ "$rc" -eq 10 ] && [ "$n" -ge 5 ] && pass "all five store thresholds fire ($n lines)" || fail "expected >= 5 lines, got $n (rc=$rc): $out"
# 6.0.0: errors.json is frozen data - nothing writes it, so no threshold watches it (51 entries above stay silent)
echo "$out" | grep -q 'errors.json' && fail "errors.json still has a threshold line: $(echo "$out" | grep errors.json)"   || pass "errors.json has no threshold rule any more (6.0.0)"
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
# 6.0.0: /agentic-os:maintain is the Pflegelauf over learnings; it does nothing about these counts, so a
# THRESHOLD line must not send its reader there (only the PFLEGELAUF line does, tested below).
if echo "$out" | grep '^THRESHOLD:' | grep -q 'agentic-os:maintain'; then
  fail "a THRESHOLD line points to /agentic-os:maintain, which does not act on it: $(echo "$out" | grep '^THRESHOLD:' | grep 'agentic-os:maintain' | head -1)"
else
  pass "no THRESHOLD line points to /agentic-os:maintain (6.0.0)"
fi

# --- 6.0.0: Pflegelauf trigger (new learnings since the last maintenance run) ---
rm -rf "$MEM/working" "$MEM/iterations/iteration-log.md" "$MEM/session-summary.md" "$MEM/learnings/learnings.md"
mkdir -p "$MEM/working"
pfl() { bash "$SCRIPT" "$MEM" | grep '^PFLEGELAUF:'; }

learnings_json 31 > "$MEM/learnings/learnings.json"
out=$(bash "$SCRIPT" "$MEM"); rc=$?
echo "$out" | grep -qF 'PFLEGELAUF: 31 Learnings, noch kein Pflegelauf → /agentic-os:maintain'   && pass "no stamp, 31 learnings: 'noch kein Pflegelauf' line" || fail "no stamp, 31: $out"
[ "$rc" -eq 10 ] && pass "the Pflegelauf line counts as exceeded (exit 10)" || fail "no stamp, 31 -> exit $rc"

learnings_json 29 > "$MEM/learnings/learnings.json"
[ -z "$(pfl)" ] && pass "no stamp, 29 learnings: below PFLEGE_NEU, no line" || fail "no stamp, 29: $(pfl)"

stamp 10; learnings_json 39 > "$MEM/learnings/learnings.json"
out=$(bash "$SCRIPT" "$MEM"); rc=$?
[ -z "$(pfl)" ] && [ "$rc" -eq 0 ] && pass "stamp id_keys 10, 39 learnings: 29 new, no line (exit 0)" || fail "stamp 10/39: rc=$rc $out"

learnings_json 40 > "$MEM/learnings/learnings.json"
[ "$(pfl)" = "PFLEGELAUF: 30 neue Learnings seit 2026-11-01 → /agentic-os:maintain" ]   && pass "stamp id_keys 10, 40 learnings: '30 neue Learnings seit <date>'" || fail "stamp 10/40: $(pfl)"

stamp 50
[ -z "$(pfl)" ] && pass "id_keys larger than today (learnings removed): no line" || fail "stamp 50/40: $(pfl)"

# 6.0.2 (Codex): only "id" keys count - a tag "id" and an escaped \"id\": inside a text do not (25 rows -> 25)
python -c "import json; print(json.dumps([{'id': f'L{i}', 'tags': ['id'], 'text': 'x \"id\": y'} for i in range(1, 26)], indent=2))" \
  > "$MEM/learnings/learnings.json"
stamp2() { printf '{"date": "2026-11-01", "id_keys": %s, "zaehlung": 2}\n' "$1" > "$MEM/working/pflegelauf.json"; }
stamp2 10
[ -z "$(pfl)" ] && pass "25 rows with tag 'id' and an escaped key in the text: 15 new, no line" || fail "tag id counted: $(pfl)"
stamp2 0
[ "$(pfl)" = "" ] && pass "stamp 0, 25 rows: 25 new, below 30, no line" || fail "stamp 0/25 tag id: $(pfl)"
# Codex re-check: a stamp from before 6.0.2 (no "zaehlung") was counted over every '"id"' - it is compared with that
# count: 50 hits (key + tag per row) - 20 = 30 new -> due; with the new count (25 - 20 = 5) the line would stay away
stamp 20
[ "$(pfl)" = "PFLEGELAUF: 30 neue Learnings seit 2026-11-01 → /agentic-os:maintain" ] \
  && pass "old stamp: compared with the old count, the due run is not hidden" || fail "old stamp after upgrade: $(pfl)"
# Codex re-checks: valid JSON with "id" at the start of a line, or "id" and the colon on two lines, is counted
python -c "print('[' + ','.join('{\n\"id\"\n: \"L%d\"\n}' % i for i in range(1, 41)) + ']')" > "$MEM/learnings/learnings.json"
stamp2 10
[ "$(pfl)" = "PFLEGELAUF: 30 neue Learnings seit 2026-11-01 → /agentic-os:maintain" ] \
  && pass "40 unindented rows, 'id' and colon on two lines, stamp 10: 30 new, line" || fail "unindented rows: $(pfl)"
# an unparsable file falls back to the "id" keys by text: 40 keys, stamp 10 -> 30 new, the line comes (Codex: a
# fallback that always gave 0 must not pass)
python -c "print('[' + ', '.join('{\"id\": \"L%d\"}' % i for i in range(1, 41)) + ',')" > "$MEM/learnings/learnings.json"
stamp2 10
out=$(bash "$SCRIPT" "$MEM"); rc=$?
[ "$rc" -eq 10 ] && [ "$(pfl)" = "PFLEGELAUF: 30 neue Learnings seit 2026-11-01 → /agentic-os:maintain" ] \
  && pass "broken learnings.json: text fallback counts 40 keys, line" || fail "broken learnings: rc=$rc $out"

echo 'not json at all' > "$MEM/working/pflegelauf.json"
out=$(bash "$SCRIPT" "$MEM"); rc=$?
[ -z "$(pfl)" ] && [ "$rc" -ne 1 ] && pass "broken stamp: no PFLEGELAUF line, exit not 1" || fail "broken stamp: rc=$rc $out"

printf '{"date": "2026-11-01"}
' > "$MEM/working/pflegelauf.json"
[ -z "$(pfl)" ] && pass "stamp without id_keys: no line" || fail "stamp without id_keys: $(pfl)"

echo "=== $ERRORS failure(s) ==="
[ "$ERRORS" -eq 0 ]

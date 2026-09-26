#!/usr/bin/env bash
# Tests for scripts/cost-trace.sh — append-only context/cost trace (v4.7.0).
# Exit codes: 0 = all pass, 1 = failures found

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CT="$PLUGIN_ROOT/scripts/cost-trace.sh"
ERRORS=0
TESTS=0
PASSED=0

pass() { TESTS=$((TESTS + 1)); PASSED=$((PASSED + 1)); echo "  PASS: $1"; }
fail() { TESTS=$((TESTS + 1)); ERRORS=$((ERRORS + 1)); echo "  FAIL: $1"; }

echo "=== cost-trace tests ==="

if [ ! -f "$CT" ]; then
    fail "scripts/cost-trace.sh missing"
    echo "=== Results: $PASSED/$TESTS passed, $ERRORS failures ==="
    exit 1
fi
pass "script exists"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
MEM="$TMP/.agent-memory"
mkdir -p "$MEM"

# 1. append creates metrics/cost-trace.jsonl with one line
bash "$CT" append --mem "$MEM" --task wrap-up --class cheap-write --context-bytes 8000 --escalated 0
if [ "$?" -eq 0 ]; then pass "append exits 0"; else fail "append must exit 0"; fi
TRACE="$MEM/metrics/cost-trace.jsonl"
if [ -f "$TRACE" ]; then pass "trace file created"; else fail "trace file missing"; fi
n=$(wc -l < "$TRACE" | tr -d ' ')
if [ "$n" -eq 1 ]; then pass "one line after first append"; else fail "expected 1 line, got $n"; fi

# 2. line carries the contract fields (est_input_tokens = bytes/4 = 2000)
LINE=$(head -1 "$TRACE")
for fieldcheck in '"task_type":"wrap-up"' '"model_class":"cheap-write"' '"context_bytes":8000' '"est_input_tokens":2000' '"escalated":0' '"estimate":true' '"ts":"'; do
    if echo "$LINE" | grep -qF "$fieldcheck"; then
        pass "field present: $fieldcheck"
    else
        fail "field missing: $fieldcheck in: $LINE"
    fi
done

# 3. line is valid JSON (python optional, else skip)
PY_BIN=""
command -v python3 > /dev/null 2>&1 && PY_BIN="python3"
[ -z "$PY_BIN" ] && command -v python > /dev/null 2>&1 && PY_BIN="python"
if [ -n "$PY_BIN" ]; then
    if echo "$LINE" | "$PY_BIN" -c "import json,sys; json.loads(sys.stdin.read())" 2>/dev/null; then
        pass "line is valid JSON"
    else
        fail "line is not valid JSON: $LINE"
    fi
fi

# 4. second append appends (2 lines)
bash "$CT" append --mem "$MEM" --task session-bootstrap --class cheap-write --context-bytes 999 --escalated 1
n=$(wc -l < "$TRACE" | tr -d ' ')
if [ "$n" -eq 2 ]; then pass "two lines after second append"; else fail "expected 2 lines, got $n"; fi
if tail -1 "$TRACE" | grep -qF '"escalated":1'; then pass "escalated=1 recorded"; else fail "escalated flag lost"; fi

# 5. non-numeric context-bytes -> coerced to 0, still exit 0
bash "$CT" append --mem "$MEM" --task x --class standard --context-bytes abc --escalated 0
if [ "$?" -eq 0 ] && tail -1 "$TRACE" | grep -qF '"context_bytes":0'; then
    pass "non-numeric bytes coerced to 0"
else
    fail "non-numeric bytes must coerce to 0 and exit 0"
fi

# 6. fail-soft: unwritable mem dir -> exit 0, warning only
bash "$CT" append --mem "$TMP/does/not/exist/deep" --task x --class standard --context-bytes 1 --escalated 0 2>/dev/null
if [ "$?" -eq 0 ]; then pass "fail-soft exit 0 on bad mem dir"; else fail "must never exit non-zero (fail-soft)"; fi

# 7. trailing flag without value must terminate (no infinite loop) and exit 0
timeout 10 bash "$CT" append --mem "$MEM" --task 2>/dev/null
rc=$?
if [ "$rc" -eq 0 ]; then pass "trailing flag without value terminates with exit 0"; else fail "trailing flag hung or exited $rc (must terminate, exit 0)"; fi

# 8. control chars in --task must not split the JSONL record
before=$(wc -l < "$TRACE" | tr -d ' ')
bash "$CT" append --mem "$MEM" --task "$(printf 'evil\ntask')" --class standard --context-bytes 10 --escalated 0
after=$(wc -l < "$TRACE" | tr -d ' ')
if [ "$((after - before))" -eq 1 ]; then pass "newline in task adds exactly one line"; else fail "newline in task split the record ($before -> $after lines)"; fi
if tail -1 "$TRACE" | grep -qF '"task_type":"eviltask"'; then pass "control chars stripped from task_type"; else fail "task_type not sanitized: $(tail -1 "$TRACE")"; fi

echo ""
echo "=== Results: $PASSED/$TESTS passed, $ERRORS failures ==="
[ "$ERRORS" -eq 0 ]

#!/usr/bin/env bash
# Tests for the real-home lock in run-all.sh and for tests/run-isolated.py (5.13.0).
# The lock refuses a run against a home that holds Claude Code session data unless the isolated runner
# set AGENTIC_OS_TEST_HOME. Every case uses `--check-home` (lock check only, no tests are started), so
# this test can never start the suite recursively.
# Exit codes: 0 = all pass, 1 = any failure

set -uo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAILED=0

pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; FAILED=1; }

PY_BIN=""
command -v python3 > /dev/null 2>&1 && PY_BIN="python3"
[ -z "$PY_BIN" ] && command -v python > /dev/null 2>&1 && PY_BIN="python"
if [ -z "$PY_BIN" ]; then
    echo "  FAIL: no python found"
    exit 1
fi

# run-all.sh --check-home with a given HOME; stdout+stderr in $OUT, exit code in $RC.
# `timeout` keeps a missing lock from turning into a long run.
check_home() {
    local home="$1"; shift
    OUT="$(env -u AGENTIC_OS_TEST_HOME "$@" HOME="$home" USERPROFILE="$home" \
        timeout "${CHECK_HOME_TIMEOUT:-60}" bash "$DIR/tests/run-all.sh" --check-home 2>&1)"
    RC=$?
}

echo "== real-home lock in run-all.sh =="

mkdir -p "$TMP/fakehome/.claude/projects" "$TMP/cleanhome"

check_home "$TMP/fakehome"
if [ "$RC" -eq 2 ] && echo "$OUT" | grep -q "run-isolated.py"; then
    pass "home with .claude/projects and no AGENTIC_OS_TEST_HOME: exit 2 naming run-isolated.py"
else
    fail "expected exit 2 naming run-isolated.py, got exit $RC: $(echo "$OUT" | head -3)"
fi

check_home "$TMP/fakehome" AGENTIC_OS_TEST_HOME=1
if [ "$RC" -eq 0 ]; then
    pass "AGENTIC_OS_TEST_HOME=1 lets the same home pass (exit 0)"
else
    fail "AGENTIC_OS_TEST_HOME=1 should pass, got exit $RC: $(echo "$OUT" | head -3)"
fi

check_home "$TMP/cleanhome"
if [ "$RC" -eq 0 ]; then
    pass "home without .claude/projects passes without the variable (exit 0)"
else
    fail "home without .claude/projects should pass, got exit $RC: $(echo "$OUT" | head -3)"
fi

check_home "$TMP/fakehome" AGENTIC_OS_TEST_HOME=
if [ "$RC" -eq 2 ]; then
    pass "empty AGENTIC_OS_TEST_HOME does not count (exit 2)"
else
    fail "empty AGENTIC_OS_TEST_HOME should still lock, got exit $RC"
fi

echo "== tests/run-isolated.py =="

# Probe scripts: print what a test would see.
cat > "$TMP/probe-env.py" <<'EOF'
import os, sys
print("PROBE_HOME=" + os.path.expanduser("~"))
print("PROBE_FLAG=" + os.environ.get("AGENTIC_OS_TEST_HOME", ""))
print("PROBE_CLAUDE=" + ",".join(sorted(k for k in os.environ if k.upper().startswith("CLAUDE_"))))
print("PROBE_BASH=" + os.environ.get("PROBE_SENTINEL", "gone"))
sys.exit(0)
EOF
cat > "$TMP/probe-fail.py" <<'EOF'
import sys
sys.exit(3)
EOF
cat > "$TMP/probe-sh.sh" <<'EOF'
#!/usr/bin/env bash
echo "SH_HOME=$HOME"
exit 0
EOF
cat > "$TMP/probe-leak.py" <<'EOF'
import os
with open(os.path.join(os.environ["PROBE_GUARD"], "leak.txt"), "w", encoding="utf-8") as fh:
    fh.write("leak\n")
EOF
mkdir -p "$TMP/guard"
echo "alt" > "$TMP/guard/alt.txt"

OUT="$(CLAUDE_CODE_SESSION_ID=abc CLAUDE_FOO=1 PROBE_SENTINEL=seen \
    "$PY_BIN" "$DIR/tests/run-isolated.py" "$TMP/probe-env.py" 2>&1)"
RC=$?
LAST="$(echo "$OUT" | tail -2 | tr -d '\r')"
if [ "$RC" -eq 0 ] && [ "$LAST" = "$(printf 'EXIT 0\nABWEICHUNGEN 0')" ]; then
    pass "passing script: exit 0, output ends with EXIT 0 / ABWEICHUNGEN 0"
else
    fail "passing script: exit $RC, tail: $LAST"
fi
echo "$OUT" | tr -d '\r' | grep -q "^PROBE_FLAG=1$" && pass "script sees AGENTIC_OS_TEST_HOME=1" \
    || fail "script does not see AGENTIC_OS_TEST_HOME=1"
echo "$OUT" | tr -d '\r' | grep -q "^PROBE_CLAUDE=$" && pass "no CLAUDE_* variable reaches the script" \
    || fail "CLAUDE_* leaked: $(echo "$OUT" | grep PROBE_CLAUDE)"
echo "$OUT" | tr -d '\r' | grep -q "^PROBE_BASH=seen$" && pass "unrelated variables are kept" \
    || fail "unrelated variable was dropped"
SEEN_HOME="$(echo "$OUT" | tr -d '\r' | sed -n 's/^PROBE_HOME=//p')"
if echo "$SEEN_HOME" | grep -q "aos-testhome-"; then
    pass "script runs under a fresh aos-testhome-* home ($SEEN_HOME)"
else
    fail "script home is not a fresh test home: $SEEN_HOME"
fi
if [ -n "$SEEN_HOME" ] && [ ! -d "$SEEN_HOME" ]; then
    pass "temp home is removed after the run"
else
    fail "temp home was not removed: $SEEN_HOME"
fi

OUT="$("$PY_BIN" "$DIR/tests/run-isolated.py" "$TMP/probe-fail.py" 2>&1)"
RC=$?
LAST="$(echo "$OUT" | tail -2 | tr -d '\r')"
if [ "$RC" -ne 0 ] && [ "$LAST" = "$(printf 'EXIT 3\nABWEICHUNGEN 0')" ]; then
    pass "failing script: nonzero exit, output ends with EXIT 3"
else
    fail "failing script: exit $RC, tail: $LAST"
fi

OUT="$("$PY_BIN" "$DIR/tests/run-isolated.py" "$TMP/probe-sh.sh" 2>&1)"
RC=$?
if [ "$RC" -eq 0 ] && echo "$OUT" | tr -d '\r' | grep -q "^SH_HOME=.*aos-testhome-"; then
    pass ".sh script runs through bash under the fresh home"
else
    fail ".sh script: exit $RC: $(echo "$OUT" | head -3)"
fi

# Deviations are listed and counted, but they do not change the exit code (parallel sessions make noise).
GUARD_WIN="$(cd "$TMP/guard" && pwd -W 2>/dev/null || pwd)"
OUT="$(PROBE_GUARD="$GUARD_WIN" "$PY_BIN" "$DIR/tests/run-isolated.py" --guard "$GUARD_WIN" \
    "$TMP/probe-leak.py" 2>&1)"
RC=$?
LAST="$(echo "$OUT" | tail -2 | tr -d '\r')"
if [ "$RC" -eq 0 ] && [ "$LAST" = "$(printf 'EXIT 0\nABWEICHUNGEN 1')" ] && echo "$OUT" | grep -q "leak.txt"; then
    pass "a write into a guarded folder is listed (leak.txt) and counted, exit stays 0"
else
    fail "guard deviation: exit $RC, tail: $LAST"
fi
rm -f "$TMP/guard/leak.txt"

OUT="$(PROBE_GUARD="$GUARD_WIN" "$PY_BIN" "$DIR/tests/run-isolated.py" --guard "$GUARD_WIN" \
    "$TMP/probe-env.py" 2>&1)"
LAST="$(echo "$OUT" | tail -1 | tr -d '\r')"
[ "$LAST" = "ABWEICHUNGEN 0" ] && pass "unchanged guarded folder: ABWEICHUNGEN 0" \
    || fail "unchanged guard should give ABWEICHUNGEN 0, got: $LAST"

# V-05: a test process that cannot start (explicit --bash path that does not exist) is exit 2 with a readable
# message, and the store comparison plus the EXIT / ABWEICHUNGEN lines still run.
OUT="$("$PY_BIN" "$DIR/tests/run-isolated.py" --bash "$TMP/no-such-bash" "$TMP/probe-sh.sh" 2>&1)"
RC=$?
NORM="$(echo "$OUT" | tr -d '\r')"
if [ "$RC" -eq 2 ] && echo "$NORM" | grep -q "^EXIT 2$" && echo "$NORM" | grep -Eq "^ABWEICHUNGEN [0-9]+$"; then
    pass "unstartable test process: exit 2, EXIT 2 and ABWEICHUNGEN lines still printed"
else
    fail "unstartable test process: exit $RC, tail: $(echo "$NORM" | tail -3)"
fi
if echo "$NORM" | grep -q "Testprozess nicht startbar" && ! echo "$NORM" | grep -q "Traceback"; then
    pass "unstartable test process: readable message, no traceback"
else
    fail "unstartable test process: message missing or traceback: $(echo "$NORM" | head -4)"
fi

echo ""
if [ "$FAILED" -eq 0 ]; then
    echo "run-isolated tests: ALL PASSED"
    exit 0
fi
echo "run-isolated tests: FAILURES DETECTED"
exit 1

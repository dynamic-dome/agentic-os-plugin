#!/usr/bin/env bash
# Tests for scripts/codex-session-briefing.sh — T-24 Codex bootstrap briefing.
# Exit codes: 0 = all pass, 1 = failures found

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CB="$PLUGIN_ROOT/scripts/codex-session-briefing.sh"
SS="$PLUGIN_ROOT/scripts/session-start.sh"
ERRORS=0; TESTS=0; PASSED=0
pass() { TESTS=$((TESTS + 1)); PASSED=$((PASSED + 1)); echo "  PASS: $1"; }
fail() { TESTS=$((TESTS + 1)); ERRORS=$((ERRORS + 1)); echo "  FAIL: $1"; }

echo "=== codex-session-briefing tests ==="
if [ ! -f "$CB" ]; then fail "scripts/codex-session-briefing.sh missing"; echo "=== Results: 0/1 ==="; exit 1; fi
pass "script exists"
bash -n "$CB" && pass "bash -n clean" || fail "syntax error"

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

# 1. Kein Store -> minimales JSON, exit 0, stderr leer, KEIN Auto-Init
PROJ1="$TMP/proj-nostore"; mkdir -p "$PROJ1"
OUT=$(cd "$PROJ1" && echo '{}' | CLAUDE_PROJECT_DIR="$PROJ1" bash "$CB" 2>"$TMP/err1"); RC=$?
[ "$RC" -eq 0 ] && pass "no-store: exit 0" || fail "no-store: exit $RC"
echo "$OUT" | python -c "import sys,json; d=json.load(sys.stdin); assert d.get('continue') is True" 2>/dev/null \
  && pass "no-store: valid continue-JSON" || fail "no-store: bad JSON: $OUT"
[ -s "$TMP/err1" ] && fail "no-store: stderr not empty" || pass "no-store: stderr empty"
[ -d "$PROJ1/.agent-memory" ] && fail "no-store: MUST NOT auto-init" || pass "no-store: no auto-init"

# 2. Store + open-tasks -> Briefing enthaelt Task-ID + Titel, gefiltert nach Status
PROJ2="$TMP/proj-store"; mkdir -p "$PROJ2/.agent-memory/context"
cat > "$PROJ2/.agent-memory/context/open-tasks.json" << 'EOF'
[
 {"id": "T-99", "title": "Beispieltask fuer Codex-Briefing", "status": "open"},
 {"id": "T-98", "title": "Erledigter Task", "status": "done"}
]
EOF
OUT=$(cd "$PROJ2" && echo '{}' | CLAUDE_PROJECT_DIR="$PROJ2" bash "$CB" 2>"$TMP/err2"); RC=$?
[ "$RC" -eq 0 ] && pass "store: exit 0" || fail "store: exit $RC"
echo "$OUT" | grep -q "T-99" && pass "store: open task id present" || fail "store: T-99 missing: $OUT"
echo "$OUT" | grep -q "T-98" && fail "store: done task must be filtered" || pass "store: done task filtered"
echo "$OUT" | python -c "import sys,json; json.load(sys.stdin)" 2>/dev/null && pass "store: valid JSON" || fail "store: invalid JSON"
[ -s "$TMP/err2" ] && fail "store: stderr not empty" || pass "store: stderr empty"

# 3. Korruptes open-tasks.json -> fail-soft
PROJ3="$TMP/proj-corrupt"; mkdir -p "$PROJ3/.agent-memory/context"
echo '{kaputt' > "$PROJ3/.agent-memory/context/open-tasks.json"
OUT=$(cd "$PROJ3" && echo '{}' | CLAUDE_PROJECT_DIR="$PROJ3" bash "$CB" 2>"$TMP/err3"); RC=$?
[ "$RC" -eq 0 ] && pass "corrupt: exit 0" || fail "corrupt: exit $RC"
echo "$OUT" | python -c "import sys,json; json.load(sys.stdin)" 2>/dev/null && pass "corrupt: valid JSON" || fail "corrupt: invalid JSON"
[ -s "$TMP/err3" ] && fail "corrupt: stderr not empty" || pass "corrupt: stderr empty"

# 2b. Briefing nutzt das Codex-Schema (hookSpecificOutput.additionalContext)
OUT=$(cd "$PROJ2" && echo '{}' | CLAUDE_PROJECT_DIR="$PROJ2" bash "$CB" 2>/dev/null)
echo "$OUT" | python -c "
import sys, json
d = json.load(sys.stdin)
h = d.get('hookSpecificOutput') or {}
assert h.get('hookEventName') == 'SessionStart', 'hookEventName missing'
assert 'T-99' in (h.get('additionalContext') or ''), 'additionalContext missing task'
" 2>/dev/null && pass "schema: additionalContext carries briefing" || fail "schema: wrong hook output shape: $OUT"

# 2c. UTF-8-Integritaet: non-ASCII (Em-Dash im statischen Header) darf nicht als
#     Mojibake (cp1252-Fehldekodierung der UTF-8-Bytes) in additionalContext landen.
#     Windows-Falle: sys.stdin.read() liest cp1252 statt utf-8 (globale CLAUDE.md).
OUT=$(cd "$PROJ2" && echo '{}' | CLAUDE_PROJECT_DIR="$PROJ2" bash "$CB" 2>/dev/null)
echo "$OUT" | python -c "
import sys, json
d = json.load(sys.stdin)
ac = (d.get('hookSpecificOutput') or {}).get('additionalContext') or ''
assert '—' in ac, 'em-dash lost (mojibake?)'
assert 'â' not in ac, 'mojibake lead byte present'
" 2>/dev/null && pass "utf8: additionalContext is clean UTF-8" || fail "utf8: mojibake in additionalContext"

# 2d. Codex-Realfall: CLAUDE_PROJECT_DIR unset, $PWD != Projekt, aber payload.cwd
#     zeigt auf den Store -> Briefing MUSS den Task tragen (S0-a: cwd im Payload).
#     Ohne cwd-Auswertung landet der Hook in emit_minimal (kein additionalContext) —
#     genau der interaktive Codex-Fehlerfall (T-25).
OUT=$(cd "$TMP" && printf '{"cwd": "%s"}' "$PROJ2" | env -u CLAUDE_PROJECT_DIR bash "$CB" 2>/dev/null)
echo "$OUT" | grep -q "T-99" && pass "payload-cwd: task resolved from payload cwd" || fail "payload-cwd: T-99 missing: $OUT"
echo "$OUT" | grep -q "additionalContext" && pass "payload-cwd: additionalContext present" || fail "payload-cwd: no additionalContext: $OUT"

# 2e. Vorrang: CLAUDE_PROJECT_DIR (Claude-Pfad) schlaegt payload.cwd. Payload zeigt
#     auf einen Store-losen Ort, CLAUDE_PROJECT_DIR auf den echten Store -> Briefing.
OUT=$(cd "$TMP" && printf '{"cwd": "%s"}' "$TMP" | CLAUDE_PROJECT_DIR="$PROJ2" bash "$CB" 2>/dev/null)
echo "$OUT" | grep -q "T-99" && pass "precedence: CLAUDE_PROJECT_DIR wins over payload cwd" || fail "precedence: T-99 missing: $OUT"

# 2f. Windows-Realfall: payload.cwd kommt mit Backslashes (echtes Codex auf Windows)
#     -> tr-Normalisierung findet den Store. Payload valide via python erzeugt.
WINCWD=$(cd "$PROJ2" && pwd -W 2>/dev/null || echo "$PROJ2")
python - "$TMP/win-payload.json" "$WINCWD" << 'EOPY'
import json, sys
open(sys.argv[1], "w", encoding="utf-8").write(json.dumps({"cwd": sys.argv[2].replace("/", "\\")}))
EOPY
OUT=$(cd "$TMP" && env -u CLAUDE_PROJECT_DIR bash "$CB" < "$TMP/win-payload.json" 2>/dev/null)
echo "$OUT" | grep -q "T-99" && pass "win-cwd: backslash payload cwd normalized" || fail "win-cwd: T-99 missing: $OUT"

# 2h. Regression (4.9.3-Hang, statischer Guard — Timing ist auf Windows-Git-Bash
#     unzuverlaessig): `PAYLOAD=$(cat)` wartete auf EOF und liess den SessionStart-Hook
#     in interaktivem Codex (stdin bleibt offen) nach 5s timeouten. Das darf nie zurueck-
#     kehren; stdin-Lesen muss timeout-begrenzt sein (read -t). Der funktionale Nachweis,
#     dass payload.cwd trotzdem gelesen wird, liegt in Test 2d ("payload-cwd").
grep -Eq '\$\(\s*cat' "$CB" && fail "no-hang: blocking \$(cat) on stdin reintroduced" || pass "no-hang: no blocking cat on stdin"
grep -Eq 'read -r -t [0-9]' "$CB" && pass "no-hang: stdin read is timeout-bounded" || fail "no-hang: stdin read not timeout-bounded"

# 4. Headless-Escape-Hatch: Env gesetzt -> kein Briefing-Feld
OUT=$(cd "$PROJ2" && echo '{}' | CLAUDE_PROJECT_DIR="$PROJ2" AGENTIC_OS_CODEX_HEADLESS=1 bash "$CB" 2>/dev/null)
echo "$OUT" | grep -q "additionalContext" && fail "headless: must not brief" || pass "headless: no briefing"

# 5. Routing: session-start.sh unter /.codex/-Pfad -> Codex-Zweig, KEIN Auto-Init
FAKE="$TMP/fakehome/.codex/plugins/cache/m/agentic-os/9.9.9/scripts"; mkdir -p "$FAKE"
cp "$SS" "$CB" "$FAKE/"
PROJ5="$TMP/proj-route"; mkdir -p "$PROJ5"
OUT=$(cd "$PROJ5" && echo '{}' | CLAUDE_PROJECT_DIR="$PROJ5" bash "$FAKE/session-start.sh" 2>/dev/null); RC=$?
[ "$RC" -eq 0 ] && pass "route: exit 0" || fail "route: exit $RC"
[ -d "$PROJ5/.agent-memory" ] && fail "route: codex path MUST NOT auto-init" || pass "route: no auto-init under codex"
echo "$OUT" | python -c "import sys,json; d=json.load(sys.stdin); assert d.get('continue') is True" 2>/dev/null \
  && pass "route: valid JSON" || fail "route: bad JSON: $OUT"

# 6. Claude-Pfad unveraendert: session-start.sh AUSSERHALB /.codex/ initialisiert weiterhin
PROJ6="$TMP/proj-claude"; mkdir -p "$PROJ6"
OUT=$(cd "$PROJ6" && echo '{}' | CLAUDE_PROJECT_DIR="$PROJ6" bash "$SS" 2>/dev/null); RC=$?
[ "$RC" -eq 0 ] && pass "claude: exit 0" || fail "claude: exit $RC"
[ -d "$PROJ6/.agent-memory" ] && pass "claude: auto-init still works" || fail "claude: auto-init broken"

echo "=== Results: $PASSED/$TESTS passed, $ERRORS failures ==="
[ "$ERRORS" -eq 0 ] && exit 0 || exit 1

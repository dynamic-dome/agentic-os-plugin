#!/usr/bin/env bash
# Unit tests for scripts/global-schema.sh — the pure, sourceable helpers that back
# the global cross-project memory layer (4.A). Unlike the marker/grep tests in the
# other suites, these call the functions with real inputs and check real outputs, so
# a broken invariant fails loudly (the L11 reason the logic lives in a script, not a prompt).
# Exit codes: 0 = all pass, 1 = failures found

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCHEMA="$PLUGIN_ROOT/scripts/global-schema.sh"
ERRORS=0
TESTS=0
PASSED=0

pass() { TESTS=$((TESTS + 1)); PASSED=$((PASSED + 1)); echo "  PASS: $1"; }
fail() { TESTS=$((TESTS + 1)); ERRORS=$((ERRORS + 1)); echo "  FAIL: $1"; }

# assert_eq <label> <expected> <actual>
assert_eq() {
  if [ "$2" = "$3" ]; then pass "$1 (= '$2')"; else fail "$1 — expected '$2', got '$3'"; fi
}
# assert_rc <label> <expected_rc> <actual_rc>
assert_rc() {
  if [ "$2" = "$3" ]; then pass "$1 (rc=$2)"; else fail "$1 — expected rc $2, got $3"; fi
}

echo "=== Global Schema Helper Tests ==="

if [ ! -f "$SCHEMA" ]; then
  fail "scripts/global-schema.sh missing — the 4.A helper contract must live in one sourceable file"
  echo ""
  echo "=== Results: $PASSED/$TESTS passed, $ERRORS failures ==="
  [ "$ERRORS" -eq 0 ] && exit 0 || exit 1
fi

# shellcheck source=/dev/null
. "$SCHEMA"
# Denylist lives in mem-schema.sh (SSoT) — source it so is_denied() sees the array.
# shellcheck source=/dev/null
. "$PLUGIN_ROOT/scripts/mem-schema.sh" 2>/dev/null || true

echo "-- normalize: lowercase, trim, strip punctuation, collapse whitespace --"
assert_eq "normalize collapses + lowercases" "use pytest" "$(normalize '  Use   PyTest!! ')"
assert_eq "normalize is idempotent" "use pytest" "$(normalize "$(normalize '  Use   PyTest!! ')")"

echo "-- compute_scope: type + normalized SORTED tags --"
assert_eq "compute_scope sorts tags" "pattern|bash,windows" "$(compute_scope pattern 'windows,Bash')"
assert_eq "compute_scope sort is order-independent" "pattern|bash,windows" "$(compute_scope pattern 'Bash,windows')"

echo "-- passes_promotion_gate: conf>=0.6 AND occ>=3 AND projects>=2 --"
passes_promotion_gate 0.6 3 2; assert_rc "gate passes at exact thresholds" 0 $?
passes_promotion_gate 0.6 2 2; assert_rc "gate fails when occ<3" 1 $?
passes_promotion_gate 0.5 3 2; assert_rc "gate fails when conf<0.6" 1 $?
passes_promotion_gate 0.6 3 1; assert_rc "gate fails when projects<2" 1 $?

echo "-- apply_decay: -0.1 per 90 days, floor 0.3 --"
assert_eq "decay one step" "0.80" "$(apply_decay 0.9 90)"
assert_eq "decay floors at 0.3 (not 0.15)" "0.30" "$(apply_decay 0.35 180)"
assert_eq "decay no-op under 90 days" "0.70" "$(apply_decay 0.7 89)"

echo "-- is_denied: privacy denylist (MEM_GLOBAL_DENY_TAGS) --"
is_denied api_key; assert_rc "denied tag is blocked" 0 $?
is_denied credentials; assert_rc "credentials blocked" 0 $?
is_denied pytest; assert_rc "ordinary tag is allowed" 1 $?

echo "-- migration: lifecycle decided by the promotion gate (no two classes of active) --"
MIG="$PLUGIN_ROOT/scripts/migrate-global-schema-4A.sh"
if [ -f "$MIG" ]; then
  MIGTMP=$(mktemp -d)/global
  mkdir -p "$MIGTMP"
  # one entry that PASSES the gate (conf>=0.6, occ>=3, 2 projects), one that FAILS (1 project)
  # X3 carries a QUALITATIVE confidence ("low") — legacy stores have these. It must not
  # crash float() and must coerce to 0.3 (-> candidate, since 0.3 < 0.6 gate threshold).
  cat > "$MIGTMP/patterns.json" <<'JSON'
[
  {"id":"X1","description":"passing","confidence":0.8,"occurrences":4,"source_projects":["a","b"],"tags":["x"]},
  {"id":"X2","description":"failing","confidence":0.9,"occurrences":5,"source_project":"a","tags":["y"]},
  {"id":"X3","description":"qualitative","confidence":"low","occurrences":4,"source_projects":["a","b"],"tags":["z"]}
]
JSON
  echo '[]' > "$MIGTMP/learnings.json"
  echo '{"projects":[]}' > "$MIGTMP/projects.json"
  CLAUDE_MEMORY_GLOBAL="$MIGTMP" bash "$MIG" --apply >/dev/null 2>&1
  MIG_RC=$?
  assert_rc "migration: survives qualitative confidence (no float crash)" 0 "$MIG_RC"
  # Read via stdin pipe, not a path arg — Git-Bash /c/... paths break python's open() on Windows.
  LC_PASS=$(cat "$MIGTMP/patterns.json" | python -c "import sys,json;print(json.load(sys.stdin)[0]['lifecycle'])" 2>/dev/null)
  LC_FAIL=$(cat "$MIGTMP/patterns.json" | python -c "import sys,json;print(json.load(sys.stdin)[1]['lifecycle'])" 2>/dev/null)
  CONF_QUAL=$(cat "$MIGTMP/patterns.json" | python -c "import sys,json;print(json.load(sys.stdin)[2]['confidence'])" 2>/dev/null)
  LC_QUAL=$(cat "$MIGTMP/patterns.json" | python -c "import sys,json;print(json.load(sys.stdin)[2]['lifecycle'])" 2>/dev/null)
  assert_eq "migration: gate-passing entry -> active" "active" "$LC_PASS"
  assert_eq "migration: gate-failing (1 project) entry -> candidate" "candidate" "$LC_FAIL"
  assert_eq "migration: qualitative 'low' confidence coerced to 0.3" "0.3" "$CONF_QUAL"
  assert_eq "migration: qualitative-confidence entry -> candidate" "candidate" "$LC_QUAL"
else
  fail "scripts/migrate-global-schema-4A.sh missing — cannot verify gate-consistent lifecycle"
fi

echo ""
echo "=== Results: $PASSED/$TESTS passed, $ERRORS failures ==="
[ "$ERRORS" -eq 0 ] && exit 0 || exit 1

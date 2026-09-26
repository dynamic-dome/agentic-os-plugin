#!/usr/bin/env bash
# Tests for scripts/model-routing.sh — the model-class SSoT (v4.7.0).
# Exit codes: 0 = all pass, 1 = failures found

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MR="$PLUGIN_ROOT/scripts/model-routing.sh"
ERRORS=0
TESTS=0
PASSED=0

pass() { TESTS=$((TESTS + 1)); PASSED=$((PASSED + 1)); echo "  PASS: $1"; }
fail() { TESTS=$((TESTS + 1)); ERRORS=$((ERRORS + 1)); echo "  FAIL: $1"; }

echo "=== model-routing SSoT tests ==="

# 1. Script exists and `list` exits 0
if [ -f "$MR" ] && OUT=$(bash "$MR" list); then
    pass "list runs and exits 0"
else
    fail "scripts/model-routing.sh missing or 'list' failed"
    echo "=== Results: $PASSED/$TESTS passed, $ERRORS failures ==="
    exit 1
fi

# 2. Exactly 5 rows (one per skill)
n=$(echo "$OUT" | grep -c .)
if [ "$n" -eq 5 ]; then pass "list has 5 rows"; else fail "list has $n rows (expected 5)"; fi

# 3. Every listed skill directory exists
while IFS=$'\t' read -r sk cls mdl eff; do
    if [ -d "$PLUGIN_ROOT/skills/$sk" ]; then
        pass "skill dir exists: $sk"
    else
        fail "SSoT lists unknown skill: $sk"
    fi
done <<< "$OUT"

# 4. Classes are from the allowed set
if echo "$OUT" | awk -F'\t' '{print $2}' | grep -vqE '^(deterministic|cheap-read|cheap-write|standard|strong)$'; then
    fail "list contains invalid class value"
else
    pass "all classes valid"
fi

# 5. cheap-write rows use sonnet; standard/strong rows use '-'
if echo "$OUT" | awk -F'\t' '$2=="cheap-write" && $3!="sonnet"' | grep -q .; then
    fail "cheap-write row without model=sonnet"
else
    pass "cheap-write => sonnet"
fi
if echo "$OUT" | awk -F'\t' '($2=="standard" || $2=="strong") && ($3!="-" || $4!="-")' | grep -q .; then
    fail "standard/strong row must have model=- and effort=-"
else
    pass "standard/strong => inherit (-)"
fi

# 6. list-agents: 3 rows, each agent file exists
AOUT=$(bash "$MR" list-agents)
an=$(echo "$AOUT" | grep -c .)
if [ "$an" -eq 1 ]; then pass "list-agents has 1 row"; else fail "list-agents has $an rows (expected 1 — improvement-agent/research-agent removed in 4.15.0, self-improve runs inline)"; fi
while IFS=$'\t' read -r ag cls mdl eff; do
    if [ -f "$PLUGIN_ROOT/agents/$ag.md" ]; then
        pass "agent file exists: $ag"
    else
        fail "SSoT lists unknown agent: $ag"
    fi
done <<< "$AOUT"

# 8. Reverse direction: every skill dir / agent file must appear in the SSoT
for d in "$PLUGIN_ROOT"/skills/*/; do
    sk=$(basename "$d")
    if echo "$OUT" | cut -f1 | grep -qx "$sk"; then
        pass "skill in SSoT: $sk"
    else
        fail "skill dir $sk missing from model-routing.sh list (SSoT must cover every skill)"
    fi
done
for f in "$PLUGIN_ROOT"/agents/*.md; do
    ag=$(basename "$f" .md)
    if echo "$AOUT" | cut -f1 | grep -qx "$ag"; then
        pass "agent in SSoT: $ag"
    else
        fail "agent $ag missing from model-routing.sh list-agents"
    fi
done

# 7. Unknown command exits 2
bash "$MR" bogus >/dev/null 2>&1
if [ "$?" -eq 2 ]; then pass "unknown command exits 2"; else fail "unknown command must exit 2"; fi

echo ""
echo "=== Results: $PASSED/$TESTS passed, $ERRORS failures ==="
[ "$ERRORS" -eq 0 ]

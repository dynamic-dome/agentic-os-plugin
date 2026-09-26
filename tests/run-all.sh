#!/usr/bin/env bash
# Runs all plugin validation tests.
# Exit codes: 0 = all pass, 1 = any failures

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOTAL_ERRORS=0

echo "========================================"
echo "  Agentic-OS Plugin Test Suite"
echo "========================================"
echo ""

# Run plugin structure validation
echo ">>> Running plugin structure validation..."
if bash "$SCRIPT_DIR/validate-plugin.sh"; then
    echo ">>> Plugin validation: ALL PASSED"
else
    echo ">>> Plugin validation: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run skill validation
echo ">>> Running skill validation..."
if bash "$SCRIPT_DIR/validate-skills.sh"; then
    echo ">>> Skill validation: ALL PASSED"
else
    echo ">>> Skill validation: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run global-schema helper unit tests (4.A)
echo ">>> Running global-schema helper tests..."
if bash "$SCRIPT_DIR/test-global-schema.sh"; then
    echo ">>> Global-schema tests: ALL PASSED"
else
    echo ">>> Global-schema tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run PreToolUse shell circuit breaker tests
echo ">>> Running PreToolUse shell circuit breaker tests..."
if bash "$SCRIPT_DIR/test-pretooluse-shell-circuit-breaker.sh"; then
    echo ">>> PreToolUse shell circuit breaker tests: ALL PASSED"
else
    echo ">>> PreToolUse shell circuit breaker tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run PostToolUse dirty-tracker tests (python; try python3 first, then python)
echo ">>> Running PostToolUse dirty-tracker tests..."
PY_BIN=""
command -v python3 > /dev/null 2>&1 && PY_BIN="python3"
[ -z "$PY_BIN" ] && command -v python > /dev/null 2>&1 && PY_BIN="python"
if [ -n "$PY_BIN" ] && "$PY_BIN" "$SCRIPT_DIR/test-posttooluse-dirty-tracker.py"; then
    echo ">>> PostToolUse dirty-tracker tests: ALL PASSED"
elif [ -z "$PY_BIN" ]; then
    echo ">>> PostToolUse dirty-tracker tests: SKIPPED (no python found)"
else
    echo ">>> PostToolUse dirty-tracker tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run dirty-marker GC tests (python; try python3 first, then python)
echo ">>> Running dirty-marker GC tests..."
PY_BIN=""
command -v python3 > /dev/null 2>&1 && PY_BIN="python3"
[ -z "$PY_BIN" ] && command -v python > /dev/null 2>&1 && PY_BIN="python"
if [ -n "$PY_BIN" ] && "$PY_BIN" "$SCRIPT_DIR/test-gc-dirty-markers.py"; then
    echo ">>> Dirty-marker GC tests: ALL PASSED"
elif [ -z "$PY_BIN" ]; then
    echo ">>> Dirty-marker GC tests: SKIPPED (no python found)"
else
    echo ">>> Dirty-marker GC tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run native-memory audit tests (T-36: read-only store audit)
echo ">>> Running native-memory audit tests..."
PY_BIN=""
command -v python3 > /dev/null 2>&1 && PY_BIN="python3"
[ -z "$PY_BIN" ] && command -v python > /dev/null 2>&1 && PY_BIN="python"
if [ -n "$PY_BIN" ] && "$PY_BIN" "$SCRIPT_DIR/test-native-memory-audit.py"; then
    echo ">>> Native-memory audit tests: ALL PASSED"
elif [ -z "$PY_BIN" ]; then
    echo ">>> Native-memory audit tests: SKIPPED (no python found)"
else
    echo ">>> Native-memory audit tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run learnings schema-fields contract test (v4.4.0: derived_from + review_after)
echo ">>> Running learnings schema-fields contract test..."
PY_BIN=""
command -v python3 > /dev/null 2>&1 && PY_BIN="python3"
[ -z "$PY_BIN" ] && command -v python > /dev/null 2>&1 && PY_BIN="python"
if [ -n "$PY_BIN" ] && "$PY_BIN" "$SCRIPT_DIR/test-learnings-schema-fields.py"; then
    echo ">>> Learnings schema-fields contract: ALL PASSED"
elif [ -z "$PY_BIN" ]; then
    echo ">>> Learnings schema-fields contract: SKIPPED (no python found)"
else
    echo ">>> Learnings schema-fields contract: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Python unit tests + quality-signal contract test removed in v4.0.0
# (tools/ watermark pipeline and quality-gate skill deleted)

# Run wrap-up long-term memory contract test
echo ">>> Running wrap-up long-term memory contract test..."
if bash "$SCRIPT_DIR/test-wrap-up-long-term-memory-contract.sh"; then
    echo ">>> Wrap-up long-term memory contract: ALL PASSED"
else
    echo ">>> Wrap-up long-term memory contract: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run obsidian-sync decision promotion contract test (v4.5.0)
echo ">>> Running obsidian-sync decision promotion contract test..."
if bash "$SCRIPT_DIR/test-obsidian-sync-decision-promotion.sh"; then
    echo ">>> Obsidian-sync decision promotion contract: ALL PASSED"
else
    echo ">>> Obsidian-sync decision promotion contract: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run stage-0 preprocess-state tests (v4.7.0)
echo ">>> Running preprocess-state tests..."
PY_BIN=""
command -v python3 > /dev/null 2>&1 && PY_BIN="python3"
[ -z "$PY_BIN" ] && command -v python > /dev/null 2>&1 && PY_BIN="python"
if [ -n "$PY_BIN" ] && "$PY_BIN" "$SCRIPT_DIR/test-preprocess-state.py"; then
    echo ">>> Preprocess-state tests: ALL PASSED"
elif [ -z "$PY_BIN" ]; then
    echo ">>> Preprocess-state tests: SKIPPED (no python found)"
else
    echo ">>> Preprocess-state tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run apply_wrapup batch-writer tests
echo ">>> Running apply-wrapup batch-writer tests..."
PY_BIN=""
command -v python3 > /dev/null 2>&1 && PY_BIN="python3"
[ -z "$PY_BIN" ] && command -v python > /dev/null 2>&1 && PY_BIN="python"
if [ -n "$PY_BIN" ] && "$PY_BIN" "$SCRIPT_DIR/test-apply-wrapup.py"; then
    echo ">>> Apply-wrapup tests: ALL PASSED"
elif [ -z "$PY_BIN" ]; then
    echo ">>> Apply-wrapup tests: SKIPPED (no python found)"
else
    echo ">>> Apply-wrapup tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run headless wrap-up core tests (T-028: harvest, handoff, wikinote, apply --headless)
echo ">>> Running wrapup-core headless tests..."
PY_BIN=""
command -v python3 > /dev/null 2>&1 && PY_BIN="python3"
[ -z "$PY_BIN" ] && command -v python > /dev/null 2>&1 && PY_BIN="python"
if [ -n "$PY_BIN" ] && "$PY_BIN" "$SCRIPT_DIR/test-wrapup-core.py"; then
    echo ">>> Wrapup-core tests: ALL PASSED"
elif [ -z "$PY_BIN" ]; then
    echo ">>> Wrapup-core tests: SKIPPED (no python found)"
else
    echo ">>> Wrapup-core tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run extract_patterns tests (T-015: deterministic half of pattern-extractor)
echo ">>> Running extract-patterns tests..."
PY_BIN=""
command -v python3 > /dev/null 2>&1 && PY_BIN="python3"
[ -z "$PY_BIN" ] && command -v python > /dev/null 2>&1 && PY_BIN="python"
if [ -n "$PY_BIN" ] && "$PY_BIN" "$SCRIPT_DIR/test-extract-patterns.py"; then
    echo ">>> Extract-patterns tests: ALL PASSED"
elif [ -z "$PY_BIN" ]; then
    echo ">>> Extract-patterns tests: SKIPPED (no python found)"
else
    echo ">>> Extract-patterns tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run measured session-cost tests (4.17.0: measurement replaces estimates)
echo ">>> Running measure-session-cost tests..."
PY_BIN=""
command -v python3 > /dev/null 2>&1 && PY_BIN="python3"
[ -z "$PY_BIN" ] && command -v python > /dev/null 2>&1 && PY_BIN="python"
if [ -n "$PY_BIN" ] && "$PY_BIN" "$SCRIPT_DIR/test-measure-session-cost.py"; then
    echo ">>> Measure-session-cost tests: ALL PASSED"
elif [ -z "$PY_BIN" ]; then
    echo ">>> Measure-session-cost tests: SKIPPED (no python found)"
else
    echo ">>> Measure-session-cost tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run handoff write-guard tests (T-19)
echo ">>> Running handoff write-guard tests..."
PY_BIN=""
command -v python3 > /dev/null 2>&1 && PY_BIN="python3"
[ -z "$PY_BIN" ] && command -v python > /dev/null 2>&1 && PY_BIN="python"
if [ -n "$PY_BIN" ] && "$PY_BIN" "$SCRIPT_DIR/test-handoff-write-guard.py"; then
    echo ">>> Handoff write-guard tests: ALL PASSED"
elif [ -z "$PY_BIN" ]; then
    echo ">>> Handoff write-guard tests: SKIPPED (no python found)"
else
    echo ">>> Handoff write-guard tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run bridge projection tests (T-14)
echo ">>> Running bridge projection tests..."
PY_BIN=""
command -v python3 > /dev/null 2>&1 && PY_BIN="python3"
[ -z "$PY_BIN" ] && command -v python > /dev/null 2>&1 && PY_BIN="python"
if [ -n "$PY_BIN" ] && "$PY_BIN" "$SCRIPT_DIR/test-bridge-projection.py"; then
    echo ">>> Bridge projection tests: ALL PASSED"
elif [ -z "$PY_BIN" ]; then
    echo ">>> Bridge projection tests: SKIPPED (no python found)"
else
    echo ">>> Bridge projection tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run model-routing SSoT tests (v4.7.0)
echo ">>> Running model-routing SSoT tests..."
if bash "$SCRIPT_DIR/test-model-routing.sh"; then
    echo ">>> Model-routing SSoT tests: ALL PASSED"
else
    echo ">>> Model-routing SSoT tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run cost-trace tests (v4.7.0)
echo ">>> Running cost-trace tests..."
if bash "$SCRIPT_DIR/test-cost-trace.sh"; then
    echo ">>> Cost-trace tests: ALL PASSED"
else
    echo ">>> Cost-trace tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run codex-session-briefing tests (v4.9.0: T-24 Codex-Session-Lifecycle)
echo ">>> Running codex-session-briefing tests..."
if bash "$SCRIPT_DIR/test-codex-session-briefing.sh"; then
    echo ">>> Codex-session-briefing tests: ALL PASSED"
else
    echo ">>> Codex-session-briefing tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run pattern rueckfluss contract test (v4.6.0: implemented_by/validated_by + delta gate)
echo ">>> Running pattern rueckfluss contract test..."
if bash "$SCRIPT_DIR/test-pattern-rueckfluss-contract.sh"; then
    echo ">>> Pattern rueckfluss contract: ALL PASSED"
else
    echo ">>> Pattern rueckfluss contract: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run codex-memory ingest tests (memory hub E1)
echo ">>> Running codex-memory ingest tests..."
PY_BIN=""
command -v python3 > /dev/null 2>&1 && PY_BIN="python3"
[ -z "$PY_BIN" ] && command -v python > /dev/null 2>&1 && PY_BIN="python"
if [ -n "$PY_BIN" ] && "$PY_BIN" "$SCRIPT_DIR/test-ingest-codex-memory.py"; then
    echo ">>> Codex-memory ingest tests: ALL PASSED"
elif [ -z "$PY_BIN" ]; then
    echo ">>> Codex-memory ingest tests: SKIPPED (no python found)"
else
    echo ">>> Codex-memory ingest tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run memory index projection tests (memory hub E2)
echo ">>> Running memory index projection tests..."
PY_BIN=""
command -v python3 > /dev/null 2>&1 && PY_BIN="python3"
[ -z "$PY_BIN" ] && command -v python > /dev/null 2>&1 && PY_BIN="python"
if [ -n "$PY_BIN" ] && "$PY_BIN" "$SCRIPT_DIR/test-memory-index-projection.py"; then
    echo ">>> Memory index projection tests: ALL PASSED"
elif [ -z "$PY_BIN" ]; then
    echo ">>> Memory index projection tests: SKIPPED (no python found)"
else
    echo ">>> Memory index projection tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run review sweep tests (memory hub decay report, Phase 4)
echo ">>> Running review sweep tests..."
PY_BIN=""
command -v python3 > /dev/null 2>&1 && PY_BIN="python3"
[ -z "$PY_BIN" ] && command -v python > /dev/null 2>&1 && PY_BIN="python"
if [ -n "$PY_BIN" ] && "$PY_BIN" "$SCRIPT_DIR/test-review-sweep.py"; then
    echo ">>> Review sweep tests: ALL PASSED"
elif [ -z "$PY_BIN" ]; then
    echo ">>> Review sweep tests: SKIPPED (no python found)"
else
    echo ">>> Review sweep tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run session-start nested-store guard tests (memory hub, hygiene 2026-09)
echo ">>> Running session-start nested-store guard tests..."
if bash "$SCRIPT_DIR/test-session-start-nested-guard.sh"; then
    echo ">>> Session-start nested-store guard tests: ALL PASSED"
else
    echo ">>> Session-start nested-store guard tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run session-start briefing contract tests (4.21.0: additionalContext, counters, tasks SSoT)
echo ">>> Running session-start briefing tests..."
if bash "$SCRIPT_DIR/test-session-start-briefing.sh"; then
    echo ">>> Session-start briefing tests: ALL PASSED"
else
    echo ">>> Session-start briefing tests: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""

# Run skill-redesign eval harness (T-35: Schicht 1 signals + gate-linkage)
echo ">>> Running skill-redesign eval harness..."
if bash "$SCRIPT_DIR/eval/run-eval.sh"; then
    echo ">>> Skill-redesign eval harness: ALL PASSED"
else
    echo ">>> Skill-redesign eval harness: FAILURES DETECTED"
    ((TOTAL_ERRORS++))
fi

echo ""
echo "========================================"
if [ "$TOTAL_ERRORS" -eq 0 ]; then
    echo "  ALL TEST SUITES PASSED"
else
    echo "  $TOTAL_ERRORS TEST SUITE(S) FAILED"
fi
echo "========================================"

[ "$TOTAL_ERRORS" -eq 0 ] && exit 0 || exit 1

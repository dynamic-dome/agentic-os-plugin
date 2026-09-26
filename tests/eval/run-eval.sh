#!/usr/bin/env bash
# Skill-redesign eval harness — Schicht 1 (deterministic) + gate-linkage +
# baseline staleness notice. This is the CI-facing entry point wired into
# run-all.sh. Schicht 2 (capture_protocol.md / check_sideeffects.py) is
# on-demand break-glass and deliberately NOT run here.
#
# Exit: 0 = all deterministic checks pass, 1 = any failure. Staleness is non-fatal.
# Design: memevalharness.md (membrain).

set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
ERRORS=0

PY_BIN=""
command -v python3 > /dev/null 2>&1 && PY_BIN="python3"
[ -z "$PY_BIN" ] && command -v python > /dev/null 2>&1 && PY_BIN="python"

if [ -z "$PY_BIN" ]; then
    echo "  SKIPPED: no python found — eval harness needs python"
    exit 0
fi

echo ">> Schicht 1: deterministic script signals"
"$PY_BIN" "$DIR/eval_signals.py" || ERRORS=$((ERRORS + 1))
echo ""
echo ">> Gate-linkage: every gate conjunct present (CNF)"
"$PY_BIN" "$DIR/gate_linkage.py" || ERRORS=$((ERRORS + 1))
echo ""
echo ">> Gate-linkage selftest: every conjunct load-bearing"
"$PY_BIN" "$DIR/gate_linkage.py" --selftest || ERRORS=$((ERRORS + 1))
echo ""
echo ">> Retrieval golden set: authority matrix well-formed + store anchors live"
"$PY_BIN" "$DIR/retrieval_golden.py" || ERRORS=$((ERRORS + 1))
echo ""
echo ">> Retrieval golden selftest: every validation rule load-bearing"
"$PY_BIN" "$DIR/retrieval_golden.py" --selftest || ERRORS=$((ERRORS + 1))
echo ""
echo ">> Baseline staleness (non-fatal)"
"$PY_BIN" "$DIR/staleness_check.py"

exit $([ "$ERRORS" -eq 0 ] && echo 0 || echo 1)

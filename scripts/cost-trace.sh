#!/usr/bin/env bash
# cost-trace.sh — append-only context/cost trace for routed memory tasks (v4.7.0).
# Spec: docs/superpowers/specs/2026-07-15-model-routing-design.md section 3.6
#
# est_input_tokens = context_bytes / 4. This is an ESTIMATE ("estimate":true):
# Claude Code exposes no real per-run token counts to skills; we trace what is
# deterministically measurable (bytes of files actually read, model class,
# escalation flag).
#
# DO NOT reason about cost from these records (4.17.0). The estimate has no
# error bar and a skill's own guess at "bytes read this run" is a guess: a
# wrap-up run traced --context-bytes 96000 while the transcript showed 6.6M
# context tokens actually transported. For real numbers use
#   python scripts/measure_session_cost.py <transcript.jsonl> --append-trace .agent-memory
# which reads the transcript and writes records with "estimate": false.
# Both record kinds live in the same JSONL — filter on the estimate flag.
#
# Fail-soft contract: NEVER exits non-zero, never blocks a skill run.
# Usage:
#   bash scripts/cost-trace.sh append --mem .agent-memory --task wrap-up \
#     --class cheap-write --context-bytes 12345 --escalated 0
set -u

cmd="${1:-}"
shift 2>/dev/null || true

MEM=".agent-memory"
TASK="unknown"
CLASS="unknown"
BYTES="0"
ESC="0"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mem)            MEM="${2:-.agent-memory}"; [ "$#" -ge 2 ] && shift 2 || shift ;;
    --task)           TASK="${2:-unknown}"; [ "$#" -ge 2 ] && shift 2 || shift ;;
    --class)          CLASS="${2:-unknown}"; [ "$#" -ge 2 ] && shift 2 || shift ;;
    --context-bytes)  BYTES="${2:-0}"; [ "$#" -ge 2 ] && shift 2 || shift ;;
    --escalated)      ESC="${2:-0}"; [ "$#" -ge 2 ] && shift 2 || shift ;;
    *) shift ;;
  esac
done

# Guard (memory hub, hygiene 2026-09): the nested .agent-memory/.agent-memory
# stores found in the audit came from here (mkdir -p "$MEM/metrics" below) when
# cwd was already inside .agent-memory and $MEM stayed the relative default —
# never create a store inside a store. Resolve $MEM to an absolute path first
# (it need not exist yet) and check its PARENT for an .agent-memory segment.
# TODO(memory-hub): trailing slash in --mem and Windows-absolute C:/ paths are not normalised here (no in-repo caller passes them)
case "$MEM" in
  /*) RESOLVED_MEM="$MEM" ;;
  *)  RESOLVED_MEM="$(pwd)/$MEM" ;;
esac
case "${RESOLVED_MEM%/*}" in
  */.agent-memory|*/.agent-memory/*)
    echo "cost-trace: refusing to create a store inside .agent-memory ($RESOLVED_MEM) — skipping" >&2
    exit 0
    ;;
esac

if [ "$cmd" != "append" ]; then
  echo "usage: cost-trace.sh append --mem DIR --task NAME --class CLASS --context-bytes N --escalated 0|1" >&2
  exit 0  # fail-soft: even usage errors must not break a skill run
fi

# sanitize numerics (non-numeric -> 0) and enum-ish strings (whitelist A-Za-z0-9._:-)
case "$BYTES" in (*[!0-9]*|"") BYTES=0 ;; esac
case "$ESC" in (0|1) : ;; (*) ESC=0 ;; esac
TASK=$(printf '%s' "$TASK" | tr -cd 'A-Za-z0-9._:-' | cut -c1-64)
CLASS=$(printf '%s' "$CLASS" | tr -cd 'A-Za-z0-9._:-' | cut -c1-32)
[ -n "$TASK" ] || TASK="unknown"
[ -n "$CLASS" ] || CLASS="unknown"
TOKENS=$((BYTES / 4))
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "")

{
  mkdir -p "$MEM/metrics" &&
  printf '{"ts":"%s","task_type":"%s","model_class":"%s","context_bytes":%s,"est_input_tokens":%s,"escalated":%s,"estimate":true}\n' \
    "$TS" "$TASK" "$CLASS" "$BYTES" "$TOKENS" "$ESC" >> "$MEM/metrics/cost-trace.jsonl"
} 2>/dev/null || echo "cost-trace: append skipped (unwritable $MEM)" >&2

exit 0

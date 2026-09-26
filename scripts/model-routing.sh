#!/usr/bin/env bash
# model-routing.sh — single source of truth for model-class routing (v4.7.0).
# Spec: docs/superpowers/specs/2026-07-15-model-routing-design.md
#
# !! MEASURED STATUS 2026-07-27: the `model:` frontmatter is a NO-OP. !!
# Claude Code does NOT switch the main-loop model when a skill carrying
# `model: sonnet` is invoked via the Skill tool. Verified twice:
#   1. CC 2.1.220 — probe skill whose only payload was `model: sonnet`:
#      every assistant message after the Skill result stayed on the session
#      model (claude-opus-5).
#   2. Session transcripts (~/.claude/projects/**/*.jsonl) from 2026-07-05,
#      07-07 and 07-20 on CC 2.1.215, with this frontmatter already in place
#      since 91fcb6f (2026-07-15): wrap-up ran 70-90 turns end to end on
#      claude-opus-4-8 / claude-fable-5, never on sonnet.
# The runtime hook exists in the binary (resolveSkillModelOverride -> a
# contextLayer {kind:"model"} -> options.mainLoopModel) but did not fire in
# either test. Both test sessions had an explicit `/model` set; that was NOT
# isolated as the cause, so "never works" is not proven — "does not work for
# this setup" is.
#
# Therefore the table below documents INTENT, not observed behaviour. Do NOT
# reason about cost or safety as if a skill "runs on sonnet". The escalation
# rules remain valid regardless: they define which decisions need the session
# model, independent of which model actually ran.
#
# tests/validate-skills.sh enforces that frontmatter and table never drift
# apart — that is a consistency check and can never detect this no-op. The
# only real test is a transcript probe; procedure in
# docs/model-routing-eval-checklist.md.
# Real model names live ONLY here and in frontmatter — never in skill prose.
#
# Classes:
#   deterministic  no model — scripts only (preprocess_state.py, thresholds, ...)
#   cheap-read     haiku  — read-only tasks; UNUSED in release 1 (reserved for
#                           the phase-2 fork read path; haiku has a 200k window)
#   cheap-write    sonnet — routine skills that write through existing gates
#   standard       inherit session model (no frontmatter field)
#   strong         inherit session model (no frontmatter field)
#
# Usage:
#   bash scripts/model-routing.sh list         # TSV: skill<TAB>class<TAB>model<TAB>effort
#   bash scripts/model-routing.sh list-agents  # TSV: agent<TAB>class<TAB>model<TAB>effort
# "-" means: no frontmatter field (inherit). Exit 0; unknown command exit 2.
set -u
cmd="${1:-list}"
case "$cmd" in
  list)
    printf 'wrap-up\tcheap-write\tsonnet\tmedium\n'
    printf 'session-bootstrap\tcheap-write\tsonnet\tlow\n'
    printf 'obsidian-sync\tcheap-write\tsonnet\tmedium\n'
    printf 'context-keeper\tstandard\t-\t-\n'
    printf 'pattern-extractor\tstandard\t-\t-\n'
    ;;
  list-agents)
    printf 'context-detective\tcheap-write\tsonnet\tmedium\n'
    ;;
  *)
    echo "usage: model-routing.sh [list|list-agents]" >&2
    exit 2
    ;;
esac
exit 0

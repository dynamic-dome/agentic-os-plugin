#!/bin/bash
# Contract test (v4.6.x, membrain T-15/T-17 — Loop-8 Rosinen 1, 3, 4):
# the pattern feedback loop ("Rueckfluss") must be documented end-to-end:
#   1. pattern-extractor: canonical schema carries implemented_by/validated_by
#      (+ dated implemented_at/validated_at) + a delta-draft gate for changes
#      to EXISTING components (no auto-change, idempotent via marker fields,
#      ownership-clean routing for decisions).
#   2. obsidian-sync Step 6: promotion_scope (project|global) derived from
#      source_projects — scope gate before any wiki promotion.
#   3. memory-audit: findings labeled with the gap taxonomy (7 classes)
#      + the late-filter diagnosis rule + gap class visible in the report.
#   4. DEPENDENCIES.md names every authorized patterns.json field-writer.
#   5. memory-audit Step 3.4: backflow completeness — all four rueckfluss fields
#      audited AND surfaced in the report, by record ID, zeros stated explicitly
#      (membrain T-41 / memperfectflowharvest.md Rosine R1).
# v4.6.1: greps section-scoped + coupling/negative checks (Verifier Minor 1).

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXTRACTOR="$ROOT_DIR/skills/pattern-extractor/SKILL.md"
OBSIDIAN="$ROOT_DIR/skills/obsidian-sync/SKILL.md"
AUDIT="$ROOT_DIR/commands/memory-audit.md"
DEPS="$ROOT_DIR/skills/DEPENDENCIES.md"

fail() {
    echo "FAIL: $1"
    exit 1
}

# Section extractors (scope greps to the section they belong to — Minor 1)
extractor_step5()  { awk '/^## Step 5: Write Pattern Entry/,/^## Step 6: Update/'      "$EXTRACTOR"; }
extractor_step66() { awk '/^## Step 6.6: Feedback Loop/,/^## Step 7: Flag/'            "$EXTRACTOR"; }
obsidian_step6()   { awk '/^## Step 6: Update Pattern Promotion/,/^## Step 7: Update/' "$OBSIDIAN"; }
audit_step34()     { awk '/^## Step 3.4: Backflow/,/^## Step 3.5: Classify/'           "$AUDIT"; }
audit_step35()     { awk '/^## Step 3.5: Classify/,/^## Step 4: Report/'               "$AUDIT"; }
audit_step4()      { awk '/^## Step 4: Report/,0'                                      "$AUDIT"; }

# --- 1. pattern-extractor: schema fields in the Step 5 template -------------

for field in '"implemented_by"' '"validated_by"' '"implemented_at"' '"validated_at"'; do
    extractor_step5 | grep -q "$field" \
        || fail "pattern-extractor Step 5 template must carry $field"
done

extractor_step5 | grep -q '"implemented_at": null' \
    || fail "implemented_at must default to null in the template"

extractor_step5 | grep -q '"validated_at": null' \
    || fail "validated_at must default to null in the template"

# --- 2. pattern-extractor: delta-draft gate (Step 6.6) ----------------------

grep -q 'rueckfluss-delta-gate' "$EXTRACTOR" \
    || fail "pattern-extractor must carry the rueckfluss-delta-gate anchor"

for token in \
    "Affected component" \
    "Observed problem" \
    "Proposed change" \
    "Acceptance check"
do
    extractor_step66 | grep -q "$token" \
        || fail "delta draft must contain field: $token"
done

# Gate semantics: never auto-modify the target
extractor_step66 | grep -qi "do NOT modify the target component" \
    || fail "gate must forbid auto-modifying existing components"

# Ownership routing: tasks go to open-tasks.json (any agent may write tasks);
# decisions are NEVER written directly — they route through context-keeper.
extractor_step66 | grep -q "open-tasks.json" \
    || fail "gate must persist delta drafts as open tasks"

extractor_step66 | grep -q "never write .*decisions.json directly" \
    || fail "gate must forbid writing decisions.json directly"

extractor_step66 | grep -q "context-keeper" \
    || fail "gate must route decision-level drafts through context-keeper"

# Idempotency: marker fields + dedup check before drafting (Verifier Major 4)
extractor_step66 | grep -q '"delta_task_id"' \
    || fail "gate must mark drafted patterns with delta_task_id"

extractor_step66 | grep -q '"delta_drafted_at"' \
    || fail "gate must timestamp drafts with delta_drafted_at"

extractor_step66 | grep -qi "before writing.*delta_task_id\|delta_task_id.*before writing" \
    || fail "gate must check delta_task_id BEFORE writing a new draft"

# Temporal validation rule (Verifier Major 3): dated fields + comparison rule
extractor_step66 | grep -q "only after the change has landed" \
    || fail "implemented_by must be bound to a landed change"

extractor_step66 | grep -q "implemented_at" \
    || fail "gate must set implemented_at alongside implemented_by"

extractor_step66 | grep -q "must not precede implemented_at" \
    || fail "gate must define the validated_at >= implemented_at comparison rule"

extractor_step66 | grep -qi "session other than the implementing one\|never validate its own change" \
    || fail "gate must forbid self-validation by the implementing session"

# Provenance chain must be stated (closes the loop from iteration to validation)
grep -q "derived_from.*evidence.*implemented_by.*validated_by" "$EXTRACTOR" \
    || fail "pattern-extractor must document the full provenance chain"

# --- 3. pattern-extractor canon: authorized field-writers (Major 1) ---------

grep -q "sole creator" "$EXTRACTOR" \
    || fail "canon must define pattern-extractor as sole creator of entries"

grep -q "obsidian-sync.*promotion_status.*promotion_scope" "$EXTRACTOR" \
    || fail "canon must authorize obsidian-sync for promotion metadata"

# --- 4. obsidian-sync: promotion scope gate ---------------------------------

obsidian_step6 | grep -q '"promotion_scope"' \
    || fail "obsidian-sync Step 6 must set promotion_scope"

obsidian_step6 | grep -q 'source_projects >= 2.*"promotion_scope": "global"' \
    || fail "global scope must be bound to source_projects >= 2"

obsidian_step6 | grep -q 'single project.*"promotion_scope": "project"' \
    || fail "project scope must be bound to the single-project condition"

# --- 5. DEPENDENCIES.md: writer matrix current (Major 1) --------------------

grep -q "promotion_status + promotion_scope" "$DEPS" \
    || fail "DEPENDENCIES must list obsidian-sync's patterns.json fields completely"

grep -q "implemented_by/validated_by" "$DEPS" \
    || fail "DEPENDENCIES must name the rueckfluss field-writer exception"

# --- 6. memory-audit: gap taxonomy (Loop-8 Rosine 3) ------------------------

grep -q 'gap-taxonomy' "$AUDIT" \
    || fail "memory-audit must carry the gap-taxonomy anchor"

for gap in \
    "knowledge-gap" \
    "capture-gap" \
    "index-gap" \
    "retrieval-gap" \
    "link-gap" \
    "usage-gap" \
    "feedback-loop-gap"
do
    audit_step35 | grep -q "$gap" \
        || fail "gap taxonomy must contain class: $gap"
done

audit_step35 | grep -qi "late filter" \
    || fail "memory-audit must state the late-filter diagnosis rule"

# Taxonomy must reach the report (Verifier Major 5)
audit_step4 | grep -q "GAP CLASS" \
    || fail "report template must carry a GAP CLASS column for findings"

# --- 7. memory-audit: backflow completeness (membrain T-41) -----------------
# The four rueckfluss fields are written by wrap-up / pattern-extractor. Without
# an audit that NAMES the offenders, a formally complete loop can sit idle. Each
# field must be checked in Step 3.4 AND surface in the Step 4 report.

audit_step34 | grep -q 'rueckfluss-audit' \
    || fail "memory-audit must carry the rueckfluss-audit anchor (Step 3.4)"

for field in derived_from review_after implemented_by validated_by; do
    audit_step34 | grep -q "$field" \
        || fail "backflow audit must check field: $field"
    audit_step4 | grep -q "$field" \
        || fail "backflow field must reach the report template: $field"
done

audit_step4 | grep -q "BACKFLOW" \
    || fail "report template must carry a BACKFLOW block"

# IDs, not bare counts — the whole point of T-41 (a count cannot be acted on)
audit_step34 | grep -qi "record ID" \
    || fail "backflow audit must demand record IDs, not aggregate counts"

# Zero must be stated, else an empty block reads as "not checked"
audit_step34 | grep -q "zero counts explicitly" \
    || fail "backflow audit must require explicit zero counts"

# Live value set, not the invented one: obsidian-sync writes candidate|ready
audit_step34 | grep -q 'promotion_status: ready' \
    || fail "backflow audit must key on the real promotion_status value (ready)"

audit_step34 | grep -q 'candidate` | `ready' \
    || fail "backflow audit must name the full promotion_status value set"

echo "PASS: pattern rueckfluss contract (schema+dates, delta gate w/ idempotency+ownership, scope gate, deps matrix, gap taxonomy in report, backflow audit w/ IDs)"

#!/usr/bin/env bash
# Validates all SKILL.md files for required sections and quality.
# Exit codes: 0 = all pass, 1 = failures found

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ERRORS=0
TESTS=0
PASSED=0

pass() { TESTS=$((TESTS + 1)); PASSED=$((PASSED + 1)); echo "  PASS: $1"; }
fail() { TESTS=$((TESTS + 1)); ERRORS=$((ERRORS + 1)); echo "  FAIL: $1"; }

echo "=== Skill Validation ==="

SKILLS_DIR="$PLUGIN_ROOT/skills"

for skill_dir in "$SKILLS_DIR"/*/; do
    [ -d "$skill_dir" ] || continue
    skill_name=$(basename "$skill_dir")
    skill_file="$skill_dir/SKILL.md"

    echo ""
    echo "-- $skill_name --"

    if [ ! -f "$skill_file" ]; then
        fail "$skill_name: SKILL.md not found"
        continue
    fi
    pass "$skill_name: SKILL.md exists"

    # Check frontmatter
    if head -1 "$skill_file" | grep -q "^---"; then
        pass "$skill_name: has frontmatter"

        if grep -q "^name:" "$skill_file"; then
            pass "$skill_name: has name"
        else
            fail "$skill_name: missing name field in frontmatter — skill cannot be identified by registry"
        fi

        if grep -q "^description:" "$skill_file"; then
            pass "$skill_name: has description"
            # Handle both inline and multiline (| or >) YAML descriptions
            desc_line=$(grep "^description:" "$skill_file" | head -1)
            if echo "$desc_line" | grep -qE '\|[[:space:]]*$|>[[:space:]]*$'; then
                # Multiline: measure content after description: line until next frontmatter key or ---
                desc_len=$(awk '
                    /^description:/ { in_desc=1; next }
                    in_desc && (/^[a-z_][a-z_]*:/ || /^---/) { exit }
                    in_desc { print }
                ' "$skill_file" | wc -c)
            else
                desc_len=$(echo "$desc_line" | sed 's/^description: *//' | wc -c)
            fi
            if [ "$desc_len" -gt 20 ]; then
                pass "$skill_name: description meaningful (${desc_len}c)"
            else
                fail "$skill_name: description too short (${desc_len}c)"
            fi
        else
            fail "$skill_name: missing description"
        fi
    else
        fail "$skill_name: missing frontmatter"
    fi

    content=$(cat "$skill_file")

    # Trigger/intent information
    if echo "$content" | grep -qi "trigger\|when to use\|intent\|invoke\|use this"; then
        pass "$skill_name: has trigger info"
    else
        fail "$skill_name: missing trigger info"
    fi

    # Steps/procedure
    if echo "$content" | grep -qi "step\|procedure\|workflow\|process\|phase\|ablauf\|cycle\|schritt"; then
        pass "$skill_name: has steps"
    else
        fail "$skill_name: missing steps"
    fi

    # Content length
    content_len=$(wc -c < "$skill_file")
    if [ "$content_len" -gt 500 ]; then
        pass "$skill_name: sufficient content (${content_len}b)"
    else
        fail "$skill_name: too short (${content_len}b)"
    fi

    # Examples or output format
    if echo "$content" | grep -qi "example\|output\|format\|template"; then
        pass "$skill_name: has examples/format"
    else
        fail "$skill_name: missing examples/format"
    fi
done

# skill-generator folded into pattern-extractor (v4.0.0): the Skill Candidate
# Generation section must gate on confidence>=0.7 + occurrences>=3 and carry the
# minimal generated-skill template (name/description/type: skill).
echo ""
echo "-- pattern-extractor: skill candidate generation (folded skill-generator) --"
PE_SCG_FILE="$SKILLS_DIR/pattern-extractor/SKILL.md"
if [ -f "$PE_SCG_FILE" ]; then
    if grep -qi "Skill Candidate Generation" "$PE_SCG_FILE" \
       && grep -qE "confidence >= 0\.7" "$PE_SCG_FILE" \
       && grep -qE "occurrences >= 3" "$PE_SCG_FILE" \
       && grep -q "generated-skills" "$PE_SCG_FILE" \
       && grep -q "type: skill" "$PE_SCG_FILE"; then
        pass "pattern-extractor: skill candidate generation present (gate + generated-skills path + minimal template)"
    else
        fail "pattern-extractor: Skill Candidate Generation section missing or incomplete — must gate on confidence>=0.7 + occurrences>=3, write to .agent-memory/generated-skills/, and include the minimal template (name/description/type: skill)"
    fi
fi

# test-validator, code-reviewer, quality-gate skills removed — v3/v4 consolidation

echo ""
echo "-- wrap-up session-summary template language consistency --"
WU_FILE="$SKILLS_DIR/wrap-up/SKILL.md"
if [ -f "$WU_FILE" ]; then
    # The LOCAL .agent-memory/session-summary.md template (Step 5) must use English
    # for consistency with the rest of the plugin. The CENTRAL cross-project handoff
    # (Step 7.6a) is EXEMPT: it follows the external SESSION-WORKFLOW.md spec, which
    # mandates German headers ("# Letzte Session" etc.) and forbids inventing other
    # formats. So we scan only the body BEFORE Step 7.6.
    WU_LOCAL_PART=$(awk '/^## Step 7.6/{exit} {print}' "$WU_FILE")
    if echo "$WU_LOCAL_PART" | grep -q "## Was wurde gemacht\|## Offene Punkte\|## Naechste Schritte\|## Aktive Warnungen"; then
        fail "wrap-up: LOCAL session-summary.md template (Step 5) uses German section headers — must use English (Step 7.6a central handoff is exempt by SESSION-WORKFLOW.md)"
    else
        pass "wrap-up: LOCAL session-summary.md template uses English section headers (central handoff in Step 7.6a correctly exempt)"
    fi
fi

# --- Handoff-Ownership (2026-06-12): vier Marker, je strip->FAIL-verifiziert (L11) ---
echo ""
echo "-- handoff ownership (open-tasks SSoT, one-block-per-project, pointer) --"
WU_HO_FILE="$SKILLS_DIR/wrap-up/SKILL.md"
if [ -f "$WU_HO_FILE" ]; then
    # (open-tasks-ssot): wrap-up persistiert Next Steps nach context/open-tasks.json
    OTS_BLOCK=$(grep -A4 "(open-tasks-ssot)" "$WU_HO_FILE")
    if echo "$OTS_BLOCK" | grep -q "context/open-tasks.json" && echo "$OTS_BLOCK" | grep -qi "single source of truth"; then
        pass "wrap-up: (open-tasks-ssot) — Next Steps persisted to context/open-tasks.json as SSoT"
    else
        fail "wrap-up: missing (open-tasks-ssot) block — wrap-up must write Next Steps/Open Items into context/open-tasks.json (SSoT), summary is only a rendering"
    fi

    # (handoff-dedup): 7.6a prepend droppt aeltere Bloecke desselben Projekts
    HD_BLOCK=$(grep -A4 "(handoff-dedup)" "$WU_HO_FILE")
    if echo "$HD_BLOCK" | grep -qi "one block per project" && echo "$HD_BLOCK" | grep -qi "same project"; then
        pass "wrap-up: (handoff-dedup) — central handoff keeps max one block per project"
    else
        fail "wrap-up: missing (handoff-dedup) rule in Step 7.6a — prepend must drop older blocks of the SAME project (kills next-step stacking)"
    fi

    # (next-steps-pointer): zentraler Block verweist statt zu kopieren
    NSP_BLOCK=$(grep -A5 "(next-steps-pointer)" "$WU_HO_FILE")
    if echo "$NSP_BLOCK" | grep -q "open-tasks.json" && echo "$NSP_BLOCK" | grep -q "cross-project"; then
        pass "wrap-up: (next-steps-pointer) — central Naechste Schritte is pointer + [cross-project] items only"
    else
        fail "wrap-up: missing (next-steps-pointer) rule in Step 7.6a — central handoff must point to local open-tasks.json and list only [cross-project] items inline"
    fi
fi

# (open-tasks-priority): bootstrap liest Next Steps aus der lokalen SSoT, zentral nur [cross-project]
SB_HO_FILE="$SKILLS_DIR/session-bootstrap/SKILL.md"
if [ -f "$SB_HO_FILE" ]; then
    OTP_BLOCK=$(grep -A4 "(open-tasks-priority)" "$SB_HO_FILE")
    if echo "$OTP_BLOCK" | grep -q "context/open-tasks.json" && echo "$OTP_BLOCK" | grep -qi "cross-project"; then
        pass "session-bootstrap: (open-tasks-priority) — local open-tasks.json is authoritative for next steps"
    else
        fail "session-bootstrap: missing (open-tasks-priority) — recommendations must come from local context/open-tasks.json first; central handoff contributes only [cross-project] items"
    fi
fi

# --- Wiki-Sync hardening (2026-06-27): three markers, each strip->FAIL-verifiable (L11) ---
# Hardens the auto wiki session-summary chain (wrap-up Step 7.5 -> obsidian-sync) so a
# substantial session never silently skips the wiki note. Levers: visibility + looser gate.
echo ""
echo "-- wiki-sync hardening (visible outcome + substantiality gate) --"
WU_WS_FILE="$SKILLS_DIR/wrap-up/SKILL.md"
OS_WS_FILE="$SKILLS_DIR/obsidian-sync/SKILL.md"

# (wiki-sync-visible): wrap-up Step 7.5 must report the sync outcome instead of skipping silently
if [ -f "$WU_WS_FILE" ]; then
    WSV_BLOCK=$(grep -A6 "(wiki-sync-visible)" "$WU_WS_FILE")
    if echo "$WSV_BLOCK" | grep -qi "never skips silently" && echo "$WSV_BLOCK" | grep -qi "status line"; then
        pass "wrap-up: (wiki-sync-visible) — Step 7.5 always reports wiki-sync outcome, never silent skip"
    else
        fail "wrap-up: missing (wiki-sync-visible) — Step 7.5 must emit a visible status line in every case (synced / skipped+reason / failed), not skip silently"
    fi

    # (wiki-sync-gate): single-sourced in obsidian-sync Step 2 since 4.15.0 —
    # wrap-up Step 7.5 keeps only the hard gate (config + sync_enabled) and delegates
    # the substantiality decision. It must NOT restate the rule (duplicate gates drift).
    WSG_BLOCK=$(grep -A8 "Trigger Conditions (wiki-sync-gate)" "$WU_WS_FILE")
    if echo "$WSG_BLOCK" | grep -qi "obsidian-sync" && ! echo "$WSG_BLOCK" | grep -q "since=midnight"; then
        pass "wrap-up: (wiki-sync-gate) — delegates substantiality to obsidian-sync (single source, no restated rule)"
    else
        fail "wrap-up: Step 7.5 must delegate the substantiality decision to obsidian-sync and not restate the since=midnight rule — the gate is single-sourced in obsidian-sync Step 2"
    fi
fi

# (wiki-sync-gate) + (wiki-sync-visible): obsidian-sync is the OWNER of the substantiality rule
if [ -f "$OS_WS_FILE" ]; then
    OSG_BLOCK=$(grep -A6 "(wiki-sync-gate)" "$OS_WS_FILE")
    if echo "$OSG_BLOCK" | grep -qi "substantial" && echo "$OSG_BLOCK" | grep -q "since=midnight"; then
        pass "obsidian-sync: (wiki-sync-gate) — owns the substantiality rule (>=1 iteration / any commit / learning / decision)"
    else
        fail "obsidian-sync: missing/weakened (wiki-sync-gate) — Step 2 is the single source of the substantiality rule and must keep the since=midnight check"
    fi

    OSV_BLOCK=$(grep -A4 "(wiki-sync-visible)" "$OS_WS_FILE")
    if echo "$OSV_BLOCK" | grep -qi "never returns silently\|never returns silent\|never nothing"; then
        pass "obsidian-sync: (wiki-sync-visible) — reports outcome on both success and skip"
    else
        fail "obsidian-sync: missing (wiki-sync-visible) — Output section must state both success and skip cases are reported, never silent"
    fi
fi

# tdd, code-reviewer JSON template tests removed — merged into quality-gate in v3

echo ""
echo "-- pattern-extractor trigger language consistency --"
PE_FILE="$SKILLS_DIR/pattern-extractor/SKILL.md"
if [ -f "$PE_FILE" ]; then
    if grep -q "Muster extrahieren\|Patterns analysieren\|welche Muster erkennst du\|was lief heute schief\|warum passiert das immer wieder\|welche fehler mache ich oft" "$PE_FILE"; then
        fail "pattern-extractor: description contains German trigger phrases — triggers must use English for consistent auto-matching"
    else
        pass "pattern-extractor: description trigger phrases use English (no German triggers)"
    fi
fi

echo ""
echo "-- context-keeper trigger language consistency --"
CK_FILE="$SKILLS_DIR/context-keeper/SKILL.md"
if [ -f "$CK_FILE" ]; then
    if grep -q "kontext aktualisieren\|entscheidung festhalten\|warum haben wir\|projektstand aktualisieren\|ADR erstellen\|wir haben uns fuer\|ich nutze jetzt\|projekt hat sich geaendert" "$CK_FILE"; then
        fail "context-keeper: description contains German trigger phrases — triggers must use English for consistent auto-matching"
    else
        pass "context-keeper: description trigger phrases use English (no German triggers)"
    fi
fi

echo ""
echo "-- session-bootstrap trigger language consistency --"
SB_FILE="$SKILLS_DIR/session-bootstrap/SKILL.md"
if [ -f "$SB_FILE" ]; then
    if grep -q "Session starten\|Briefing laden\|woran habe ich gearbeitet\|wo waren wir\|was wissen wir\|neue session\|Projektstand\|lass uns weitermachen\|wo stehen wir\|was ist der aktuelle stand" "$SB_FILE"; then
        fail "session-bootstrap: description contains German trigger phrases — triggers must use English for consistent auto-matching"
    else
        pass "session-bootstrap: description trigger phrases use English (no German triggers)"
    fi
fi

echo ""
echo "-- wrap-up trigger language consistency --"
WU_FILE="$SKILLS_DIR/wrap-up/SKILL.md"
if [ -f "$WU_FILE" ]; then
    if grep -q "Session beenden\|Zusammenfassung\|fertig fuer heute\|kontext sichern\|ich hoer jetzt auf\|schluss fuer heute\|mach mal ne zusammenfassung\|session beenden\|Keine Iterationen\|Fehler behoben\|aktualisiert\." "$WU_FILE"; then
        fail "wrap-up: description or body contains German phrases — must use English for consistent auto-matching"
    else
        pass "wrap-up: trigger phrases and body use English (no German phrases)"
    fi
fi

# code-reviewer trigger test removed — merged into quality-gate in v3

echo ""
echo "-- session-bootstrap body language consistency --"
SB_FILE="$SKILLS_DIR/session-bootstrap/SKILL.md"
if [ -f "$SB_FILE" ]; then
    if grep -q "Keine vorherige Session gefunden\|Stichwörter" "$SB_FILE"; then
        fail "session-bootstrap: body contains German strings — all user-facing text must use English for consistency"
    else
        pass "session-bootstrap: body uses English (no German strings)"
    fi
fi

echo ""
echo "-- sync-context body language consistency --"
SC_BODY_FILE="$PLUGIN_ROOT/commands/sync-context.md"
if [ -f "$SC_BODY_FILE" ]; then
    if grep -q "holen\|importieren\|teilen\|exportieren\|beides\|was gibt es" "$SC_BODY_FILE"; then
        fail "sync-context: body contains German intent phrases — all direction-matching phrases must use English for consistency"
    else
        pass "sync-context: body uses English (no German direction phrases)"
    fi
fi

echo ""
echo "-- wrap-up: optional NotebookLM sync --"
WU2_FILE="$SKILLS_DIR/wrap-up/SKILL.md"
if [ -f "$WU2_FILE" ]; then
    if grep -qi "notebooklm\|notebook.*sync\|sync.*notebook" "$WU2_FILE"; then
        pass "wrap-up: has optional NotebookLM sync step to close learnings feedback loop"
    else
        fail "wrap-up: missing optional NotebookLM sync — session learnings never reach NotebookLM knowledge base (P1: close bidirectional flows)"
    fi
fi

echo ""
echo "-- session-bootstrap: errors.json load count consistent with usage --"
SB2_FILE="$SKILLS_DIR/session-bootstrap/SKILL.md"
if [ -f "$SB2_FILE" ]; then
    if grep -q "last 5 entries" "$SB2_FILE" && grep -q "Last 3 errors\|last 3 errors" "$SB2_FILE"; then
        fail "session-bootstrap: Step 2 loads 'last 5 entries' from errors.json but Step 4/5 only use 'last 3 errors' — inconsistent, causes confusion about buffer size"
    else
        pass "session-bootstrap: errors.json load count consistent with usage in Step 4/5"
    fi
fi

# retrospective skill removed in v4.0.0 — periodic-retrospective bracket test removed

# --- Memory Growth Engine (Master-Plan 2026-06-03, Sprint user.md + soul.md) ---
# Pins the growth mechanics into the owning skill bodies. Marker-based + concept phrase,
# bidirectionally verified (strip -> FAIL, restore -> PASS).
WU_GROW_FILE="$SKILLS_DIR/wrap-up/SKILL.md"
SB_GROW_FILE="$SKILLS_DIR/session-bootstrap/SKILL.md"
MM_GROW_FILE="$PLUGIN_ROOT/commands/maintain.md"

echo ""
echo "-- wrap-up: user.md growth engine (candidate queue + 3-stage classification) --"
if [ -f "$WU_GROW_FILE" ]; then
    # Also pin the promotion rule (inferred + occ>=2 + conf>=0.6) and the changelog-before-write
    # atomicity — otherwise the test stays green if those guarantees are stripped (Codex MAJOR #1).
    if grep -qiE "\(user-growth\)" "$WU_GROW_FILE" \
       && grep -qiE "user-candidates\.json" "$WU_GROW_FILE" \
       && grep -qiE "observed.*inferred.*confirmed|observed / inferred / confirmed" "$WU_GROW_FILE" \
       && grep -qiE "inferred.*AND.*occurrences|occurrences.*AND.*confidence" "$WU_GROW_FILE" \
       && grep -qE "0\.6" "$WU_GROW_FILE" \
       && grep -qiE "changelog BEFORE|before the user\.md|changelog.*before the|first.*append.*changelog" "$WU_GROW_FILE"; then
        pass "wrap-up: user.md growth engine present — queue + 3-stage + promotion rule + changelog-before-write"
    else
        fail "wrap-up: user.md growth engine missing — Step 6 must use working/user-candidates.json with observed/inferred/confirmed, the promotion rule (inferred + occ>=2 + conf>=0.6) AND changelog-before-write atomicity, replacing the dead '3+ corrections' direct-write"
    fi
fi

echo ""
echo "-- wrap-up: user.md growth — mood never promoted + changelog/rollback --"
if [ -f "$WU_GROW_FILE" ]; then
    if grep -qiE "signal:mood|mood.*never promot|never promot.*mood" "$WU_GROW_FILE" \
       && grep -qiE "user-changelog\.json" "$WU_GROW_FILE"; then
        pass "wrap-up: user.md growth — mood-marker excluded from promotion + user-changelog.json audit/rollback"
    else
        fail "wrap-up: user.md growth missing safeguards — must exclude signal:mood from promotion AND log every change to identity/user-changelog.json (audit + rollback)"
    fi
fi

echo ""
echo "-- wrap-up: user.md growth — trust boundary (conversation-only) --"
if [ -f "$WU_GROW_FILE" ]; then
    # Candidates must originate from user conversation, NEVER from web/docs/notebooklm/wiki (poisoning).
    # Bind the forbidden-source list to the (trust-boundary) marker's own block (grep -A2), so an
    # unrelated 'NotebookLM' elsewhere (Step 7 sync) can't keep this green if the boundary is stripped
    # (Codex MAJOR #2 — verified strip->FAIL on the marker block).
    TB_BLOCK=$(grep -A2 -iE "\(trust-boundary\)" "$WU_GROW_FILE")
    if [ -n "$TB_BLOCK" ] \
       && echo "$TB_BLOCK" | grep -qiE "trust_source|conversation" \
       && echo "$TB_BLOCK" | grep -qiE "web/docs|NotebookLM|wiki content|untrusted"; then
        pass "wrap-up: user.md growth — trust boundary enforced (conversation-only, forbidden sources named in marker block)"
    else
        fail "wrap-up: user.md growth missing trust boundary — the (trust-boundary) block must name the forbidden sources (web/docs/NotebookLM/wiki) candidates may NEVER come from (memory poisoning)"
    fi
fi

echo ""
echo "-- wrap-up: soul.md growth — candidate queue (Stufe B, no auto-write) --"
if [ -f "$WU_GROW_FILE" ]; then
    # Also pin the no-autonomous-write invariant, not just the queue file (Codex MAJOR #3).
    if grep -qiE "\(soul-growth\)" "$WU_GROW_FILE" \
       && grep -qiE "soul-candidates\.md" "$WU_GROW_FILE" \
       && grep -qiE "[Nn]ever write .?soul\.md|[Nn]ever auto-write|NEVER auto-written|not write .?soul\.md here" "$WU_GROW_FILE"; then
        pass "wrap-up: soul.md growth present — appends to soul-candidates.md, never auto-writes soul.md"
    else
        fail "wrap-up: soul.md growth missing — must append to identity/soul-candidates.md AND state the never-write-soul.md-directly invariant"
    fi
fi

echo ""
echo "-- wrap-up: identity quality (P1/B2) — quote duty, key reuse, supersede, plain text --"
if [ -f "$WU_GROW_FILE" ]; then
    # Prose anchors for the B2 contract the scripts enforce (tests/test-identity-b2.py proves the code side).
    # quote duty: confirmed needs a LITERAL quote that is verified against the transcript; the verdict values are named
    QD_BLOCK=$(grep -A14 -iE "\(quote-duty\)" "$WU_GROW_FILE")
    if [ -n "$QD_BLOCK" ] \
       && echo "$QD_BLOCK" | grep -qE "LITERAL" \
       && echo "$QD_BLOCK" | grep -qE "quote_verified" \
       && echo "$QD_BLOCK" | grep -qE "kein_transkript" \
       && echo "$QD_BLOCK" | grep -qiE "second mention"; then
        pass "wrap-up: quote duty present — confirmed needs a literal, verified quote (true|false|kein_transkript), else the second mention"
    else
        fail "wrap-up: quote duty missing — the (quote-duty) block of Step 6.2 must demand a LITERAL quote, name quote_verified and kein_transkript, and keep the second-mention rule"
    fi
    # key reuse: stage 0 hands the existing keys to the model, Step 6.2 tells it to reuse them
    if grep -qE "\(key-reuse\)" "$WU_GROW_FILE" \
       && grep -qE "user_candidates" "$WU_GROW_FILE" \
       && grep -A8 -E "\(key-reuse\)" "$WU_GROW_FILE" | grep -qiE "verbatim"; then
        pass "wrap-up: key reuse present — Step 0 state lists user_candidates, Step 6.2 reuses the key verbatim"
    else
        fail "wrap-up: key reuse missing — Step 6.2 (key-reuse) must tell the model to send the existing key verbatim from the stage-0 user_candidates"
    fi
    # supersede: own plan section, changelog first, plain text and reason required
    SS_BLOCK=$(grep -A32 -iE "\(user-supersede\)" "$WU_GROW_FILE")
    if [ -n "$SS_BLOCK" ] \
       && echo "$SS_BLOCK" | grep -qE "user_md_supersede" \
       && echo "$SS_BLOCK" | grep -qE "plain" \
       && echo "$SS_BLOCK" | grep -qE "reason" \
       && echo "$SS_BLOCK" | grep -qiE "FIRST" \
       && echo "$SS_BLOCK" | grep -qiE "nothing written"; then
        pass "wrap-up: user_md_supersede present — reason + plain required, changelog first, a rejected plan writes nothing"
    else
        fail "wrap-up: user_md_supersede missing — Step 6.3b (user-supersede) must name the section, require reason and plain, write the changelog FIRST and state that a rejected plan writes nothing"
    fi
    # plain text duty on the bridge verdicts and the rule explanations
    BG_BLOCK=$(grep -A24 -iE "^4\. Apply the verdicts" "$WU_GROW_FILE")
    if echo "$BG_BLOCK" | grep -qE "\"plain\"" \
       && echo "$BG_BLOCK" | grep -qE "plain-text-duty" \
       && echo "$BG_BLOCK" | grep -qE "both required" \
       && echo "$BG_BLOCK" | grep -qE "bridge_plain" \
       && echo "$BG_BLOCK" | grep -qE "600" \
       && grep -qE "L26 approved .{1,5}<plain>" "$WU_GROW_FILE" \
       && grep -qE "at most 600 characters - longer is rejected" "$WU_GROW_FILE"; then
        pass "wrap-up: plain-text duty present — bridge_decisions and rule_explanations carry plain (<= 600), the report line shows <plain>"
    else
        fail "wrap-up: plain-text duty missing — Step 3d.4 must put plain into the bridge_decisions example (plain-text-duty, bridge_plain, 600 characters), 3d.5 must report <plain>, 3f must cap rule_explanations plain at 600"
    fi

    # --- Nachbesserung nach dem Pruefer-Urteil (D1-D11): the prose must carry what the scripts now enforce ---
    # quote limits (D2, D3, D5): wording not meaning, the session binding, the transcript path rule
    QL_BLOCK=$(grep -A12 -E "\(quote-limits\)" "$WU_GROW_FILE")
    if [ -n "$QL_BLOCK" ] \
       && echo "$QL_BLOCK" | grep -qE "WORDING, not the MEANING" \
       && echo "$QL_BLOCK" | grep -qiE "calibrates" \
       && echo "$QL_BLOCK" | grep -qE "CLAUDE_CODE_SESSION_ID" \
       && echo "$QL_BLOCK" | grep -qE "session_binding_mismatch" \
       && echo "$QL_BLOCK" | grep -qE "\.claude/projects/<dir>/<session-id>\.jsonl" \
       && echo "$QL_BLOCK" | grep -qE "transcript_override_rejected"; then
        pass "wrap-up: quote limits present — wording not meaning, owner calibrates, session binding and transcript path rule named"
    else
        fail "wrap-up: quote limits missing — Step 6.2 (quote-limits) must say the check proves the WORDING, not the MEANING, that Dominic calibrates the thresholds, and name CLAUDE_CODE_SESSION_ID / session_binding_mismatch and the .claude/projects/<dir>/<session-id>.jsonl rule / transcript_override_rejected"
    fi
    # quote duty details (D4, D5): length, word boundaries, negation, which sources do not count
    QD2_BLOCK=$(grep -A30 -iE "\(quote-duty\)" "$WU_GROW_FILE")
    if echo "$QD2_BLOCK" | grep -qE "8 characters and 3 words" \
       && echo "$QD2_BLOCK" | grep -qiE "word boundaries" \
       && echo "$QD2_BLOCK" | grep -qiE "negation word" \
       && echo "$QD2_BLOCK" | grep -qiE "accepted prompt suggestions" \
       && echo "$QD2_BLOCK" | grep -qiE "pasted text" \
       && echo "$QD2_BLOCK" | grep -qE "quote_reasons"; then
        pass "wrap-up: quote duty details present — 3 words, word boundaries, negation neighbour, sources that do not count, quote_reasons"
    else
        fail "wrap-up: quote duty details missing — Step 6.2 (quote-duty) must name 3 words, word boundaries, the negation neighbour rule, accepted prompt suggestions and pasted text as non-sources, and tally.quote_reasons"
    fi
    # repeat per session (D1)
    KR_BLOCK=$(grep -A12 -E "\(key-reuse\)" "$WU_GROW_FILE")
    if echo "$KR_BLOCK" | grep -qE "ONCE per plan" \
       && echo "$KR_BLOCK" | grep -qE "ANOTHER session" \
       && echo "$KR_BLOCK" | grep -qE "repeat_not_counted"; then
        pass "wrap-up: repeat rule present — once per plan, a repeat counts only from ANOTHER session (repeat_not_counted)"
    else
        fail "wrap-up: repeat rule missing — Step 6.2 (key-reuse) must say each observation is sent ONCE per plan and a repeat counts only from ANOTHER session (tally.repeat_not_counted)"
    fi
    # supersede needs a verified quote (D6, D7)
    SS2_BLOCK=$(grep -A46 -iE "\(user-supersede\)" "$WU_GROW_FILE")
    if echo "$SS2_BLOCK" | grep -qE "\"quote\"" \
       && echo "$SS2_BLOCK" | grep -qE "VERIFIED quote" \
       && echo "$SS2_BLOCK" | grep -qiE "skipped" \
       && echo "$SS2_BLOCK" | grep -qE "supersede_skipped_unverified" \
       && echo "$SS2_BLOCK" | grep -qiE "line separator" \
       && echo "$SS2_BLOCK" | grep -qiE "preamble" \
       && echo "$SS2_BLOCK" | grep -qiE "same target or the same .new_text."; then
        pass "wrap-up: supersede quote rule present — verified quote or the entry is skipped, no line separator, no preamble, no duplicate entries"
    else
        fail "wrap-up: supersede quote rule missing — Step 6.3b must require a quote, say only a VERIFIED quote applies (else skipped, supersede_skipped_unverified), forbid line separators and the preamble, and reject entries with the same target or new_text"
    fi
    # lossless promotion and no return of a retracted statement (D8, D9)
    QR_BLOCK=$(grep -A24 -iE "\(queue-re-review\)" "$WU_GROW_FILE")
    if echo "$QR_BLOCK" | grep -qE "INSERTED" \
       && echo "$QR_BLOCK" | grep -qE "NOT promoted" \
       && echo "$QR_BLOCK" | grep -qE "promotion_blocked_superseded"; then
        pass "wrap-up: promotion rules present — lines are INSERTED (lossless), a retracted statement is NOT promoted"
    else
        fail "wrap-up: promotion rules missing — Step 6.3 must say a promoted line is INSERTED without rewriting user.md and that a statement retracted by a supersede is NOT promoted (promotion_blocked_superseded)"
    fi
    # the session id is the real one (D2) and the key list needs --session-id (D10)
    S74_BLOCK=$(grep -A22 -E "^## Step 7\.4" "$WU_GROW_FILE")
    S0_BLOCK=$(grep -A22 -E "^## Step 0: " "$WU_GROW_FILE")
    if echo "$S74_BLOCK" | grep -qE "CLAUDE_CODE_SESSION_ID" \
       && echo "$S74_BLOCK" | grep -qE "session_binding_mismatch" \
       && echo "$S0_BLOCK" | grep -qE "listed only because" \
       && echo "$S0_BLOCK" | grep -qE "\-\-session-id"; then
        pass "wrap-up: session id rules present — Step 7.4 names the real session id, Step 0 lists the candidate keys only with --session-id"
    else
        fail "wrap-up: session id rules missing — Step 7.4 must say the session id is \$CLAUDE_CODE_SESSION_ID (session_binding_mismatch) and Step 0 that user_candidates are listed only because --session-id is passed"
    fi
    # maintain Step 5c: the card carries the plain text (D11)
    M5C_BLOCK=$(sed -n '/^## Step 5c:/,/^## Step 6:/p' "$MM_GROW_FILE" 2>/dev/null)
    if echo "$M5C_BLOCK" | grep -qE "\*\*\`plain\`\*\*" \
       && echo "$M5C_BLOCK" | grep -qE "summary, anchor, members, plain, tags" \
       && echo "$M5C_BLOCK" | grep -qE "600" \
       && echo "$M5C_BLOCK" | grep -qiE "refuses a card without .plain."; then
        pass "maintain Step 5c: the pointer card carries plain (<= 600), the plan shows it, the applier refuses a card without it"
    else
        fail "maintain Step 5c: plain missing — the card must show **\`plain\`**, the plan must be {summary, anchor, members, plain, tags}, 600 characters, and the applier refuses a card without plain"
    fi
    # --- second round after the reviewer's verdict (D1 gap, D3, D5, D7, D9) ---
    # a row born without a provable session takes the first real id in without counting (D1 round 2)
    KR2_BLOCK=$(grep -A18 -E "\(key-reuse\)" "$WU_GROW_FILE")
    if echo "$KR2_BLOCK" | grep -qE "without a provable session" \
       && echo "$KR2_BLOCK" | grep -qE "WITHOUT counting" \
       && echo "$KR2_BLOCK" | grep -qE "session_adopted"; then
        pass "wrap-up: provable-session rule present — a candidate created without a provable session takes the first real session in WITHOUT counting (session_adopted)"
    else
        fail "wrap-up: provable-session rule missing — Step 6.2 (key-reuse) must say a candidate created without a provable session takes the first real session in WITHOUT counting it (tally.session_adopted)"
    fi
    # the negation reach and the relation hint (D5, D7 round 2)
    QL2_BLOCK=$(grep -A22 -E "\(quote-limits\)" "$WU_GROW_FILE")
    QD3_BLOCK=$(grep -A30 -iE "\(quote-duty\)" "$WU_GROW_FILE")
    if echo "$QD3_BLOCK" | grep -qE "within 3 words of a negation word" \
       && echo "$QL2_BLOCK" | grep -qE "content word" \
       && echo "$QL2_BLOCK" | grep -qE "quote_unrelated"; then
        pass "wrap-up: reach and relation hint present — negation within 3 words, a quote without a shared content word is reported (quote_unrelated)"
    else
        fail "wrap-up: reach and relation hint missing — Step 6.2 must say the negation counts within 3 words of the quote, and (quote-limits) that a verified quote sharing no content word with the claim is reported (tally.quote_unrelated)"
    fi
    # a retracted statement needs the user's verified words to come back; the measure is generous and does not judge meaning (D9 round 4)
    QR2_BLOCK=$(grep -A26 -iE "\(queue-re-review\)" "$WU_GROW_FILE")
    if echo "$QR2_BLOCK" | grep -qF 'confirmed_by` is `quote`' \
       && echo "$QR2_BLOCK" | grep -qE "50% of the content words" \
       && echo "$QR2_BLOCK" | grep -qiE "synonym" \
       && echo "$QR2_BLOCK" | grep -qiE "proof level"; then
        pass "wrap-up: retraction proof level present — a touching candidate needs confirmed_by quote, the measure is 50% of the content words, a synonym is beyond the check"
    else
        fail "wrap-up: retraction proof level missing — Step 6.3 must say a candidate that touches a retracted statement is promoted only with confirmed_by quote (a verified quote), that the measure sets the proof level (50% of the content words), and that a synonym is beyond the check"
    fi
    # --- third round after the second verdict (R2, R3): false blocks, the release, the negation hints ---
    # content words, a refinement is no exception, the block is noted on the candidate and reported once, the release (R2 round 3)
    QR3_BLOCK=$(grep -A40 -iE "\(queue-re-review\)" "$WU_GROW_FILE")
    if echo "$QR3_BLOCK" | grep -qE "content words" \
       && echo "$QR3_BLOCK" | grep -qiE "refinement is no exception" \
       && echo "$QR3_BLOCK" | grep -qE "blocked_by_supersede" \
       && echo "$QR3_BLOCK" | grep -qiE "once" \
       && echo "$QR3_BLOCK" | grep -qE "user_md_supersede" \
       && echo "$QR3_BLOCK" | grep -qiE "verified quote"; then
        pass "wrap-up: retraction rules round 3 present — content words, a refinement is no exception, the block is noted (blocked_by_supersede) and reported once, release = user_md_supersede with a verified quote"
    else
        fail "wrap-up: retraction rules round 3 missing — Step 6.3 must say the measure counts content words, that a refinement is no exception, that a block is noted on the candidate (blocked_by_supersede) and reported once, and that it is released through user_md_supersede with a verified quote"
    fi
    # the relation hint looks at the stored text, a quote and a claim of different polarity are reported (R1, R3 round 3)
    QL3_BLOCK=$(grep -A34 -E "\(quote-limits\)" "$WU_GROW_FILE")
    if echo "$QL3_BLOCK" | grep -qE "STORED" \
       && echo "$QL3_BLOCK" | grep -qE "quote_polarity" \
       && echo "$QL3_BLOCK" | grep -qiE "negation"; then
        pass "wrap-up: relation and polarity hints present — checked against the STORED observation, one-sided negation is counted (quote_polarity)"
    else
        fail "wrap-up: relation and polarity hints missing — Step 6.2 (quote-limits) must say the relation is checked against the STORED observation and that a verified quote and a claim of which only one carries a negation word are reported (tally.quote_polarity)"
    fi
    # reviewer round 3/4: the short-line rule is named, the polarity counter is a weak hint without a warning line
    QR4_BLOCK=$(grep -A44 -iE "\(queue-re-review\)" "$WU_GROW_FILE")
    QL4_BLOCK=$(grep -A38 -E "\(quote-limits\)" "$WU_GROW_FILE")
    if echo "$QR4_BLOCK" | grep -qE "fewer than 3" \
       && echo "$QR4_BLOCK" | grep -qE "all its words" \
       && echo "$QL4_BLOCK" | grep -qE "WEAK hint" \
       && echo "$QL4_BLOCK" | grep -qE "NO warning line"; then
        pass "wrap-up: reviewer round 4 present — a line of fewer than 3 content words needs all its words, quote_polarity is a weak hint without a warning line"
    else
        fail "wrap-up: reviewer round 4 missing — Step 6.3 must name the rule for a retracted line of fewer than 3 content words (all its words), and Step 6.2 (quote-limits) must call quote_polarity a WEAK hint with NO warning line"
    fi
    # round 5: the second reading without closing brackets, a quote proves wording only, a promoted key is closed
    QR5_BLOCK=$(grep -A48 -iE "\(queue-re-review\)" "$WU_GROW_FILE")
    if echo "$QR5_BLOCK" | grep -qE "every closing bracket" \
       && echo "$QR5_BLOCK" | grep -qE "WORDING only" \
       && echo "$QR5_BLOCK" | grep -qE "promoted key is closed"; then
        pass "wrap-up: round 5 present — the line is also read without every closing bracket, a quote proves wording only, a promoted key is closed"
    else
        fail "wrap-up: round 5 missing — Step 6.3 must say the retracted line is also read without every closing bracket, that a verified quote proves WORDING only, and that a promoted key is closed (new statement, new key)"
    fi
fi

echo ""
echo "-- session-bootstrap: soul.md candidate gate ([j/n], confirmed-write only) --"
if [ -f "$SB_GROW_FILE" ]; then
    if grep -qiE "soul-candidates\.md" "$SB_GROW_FILE" \
       && grep -qiE "\[j/n\]|j/n" "$SB_GROW_FILE" \
       && grep -qiE "only (on|after) (confirm|user|explicit)|confirmed.*write|nur bei" "$SB_GROW_FILE"; then
        pass "session-bootstrap: soul candidate gate present — [j/n], soul.md write only on explicit confirmation"
    else
        fail "session-bootstrap: soul candidate gate missing — must surface soul-candidates.md with a [j/n] gate and write soul.md ONLY on explicit user confirmation (the single read-only exception)"
    fi
fi

echo ""
echo "-- maintain: soul.md 80-line anti-bloat cap --"
if [ -f "$MM_GROW_FILE" ]; then
    if grep -qiE "soul\.md" "$MM_GROW_FILE" && grep -qiE "80[ -]?(line|Zeile)" "$MM_GROW_FILE"; then
        pass "maintain: soul.md 80-line cap present (anti-bloat linter)"
    else
        fail "maintain: soul.md 80-line cap missing — consistency check must warn when soul.md exceeds 80 lines (identity dilution)"
    fi
fi

# --- patterns.json schema canon (Audit-Hebel #3, 2026-06-03) ---
# pattern-extractor is the sole writer of patterns.json, so its documented schema is the
# Single Source of Truth. The canonical fields are description / recommendation / evidence
# (NOT the legacy name/solution/source_errors or title/prevention/error_ids shapes). The skill
# must also instruct normalization of legacy entries on read, so the store converges to one shape.
PE_SCHEMA_FILE="$SKILLS_DIR/pattern-extractor/SKILL.md"

echo ""
echo "-- pattern-extractor: canonical patterns.json schema (description/recommendation/evidence) --"
if [ -f "$PE_SCHEMA_FILE" ]; then
    if grep -qiE "\(pattern-schema-canon\)" "$PE_SCHEMA_FILE" \
       && grep -qE '"recommendation"' "$PE_SCHEMA_FILE" \
       && grep -qE '"evidence"' "$PE_SCHEMA_FILE" \
       && grep -qE '"severity"' "$PE_SCHEMA_FILE" \
       && grep -qiE "normaliz|legacy.*(solution|source_errors|prevention|error_ids)|solution.*->.*recommendation" "$PE_SCHEMA_FILE"; then
        pass "pattern-extractor: canonical schema pinned + legacy-normalization instruction present"
    else
        fail "pattern-extractor: patterns.json schema canon missing — must declare description/recommendation/evidence as canonical AND instruct normalizing legacy name/solution/source_errors + title/prevention/error_ids entries on read (single-shape convergence)"
    fi
fi

# --- sync-context recency supersession (Audit-Hebel #4, 2026-06-03) ---
# Conflicting facts in the same scope must be resolved by recency (write-time supersession),
# NOT by confidence alone — a stale high-confidence fact must not beat a newer one (Mem0
# interference). Max one `active` per (fact_type, scope); the older one becomes `superseded`
# (never deleted). Confidence-merge for NON-conflicting same-fact entries stays.
SC_SUP_FILE="$PLUGIN_ROOT/commands/sync-context.md"

echo ""
echo "-- sync-context: recency supersession on conflict (not confidence-only) --"
if [ -f "$SC_SUP_FILE" ]; then
    if grep -qiE "\(recency-supersession\)" "$SC_SUP_FILE" \
       && grep -qiE "supersed" "$SC_SUP_FILE" \
       && grep -qiE "max(imum)? (one|1) active|one active per|never delete" "$SC_SUP_FILE" \
       && grep -qiE "recency|newer.*wins|timestamp|last_seen.*wins" "$SC_SUP_FILE" \
       && grep -qiE "confidence (only|does not|only ranks)|ranks .*non-conflicting|non-conflicting.*merge" "$SC_SUP_FILE"; then
        pass "sync-context: recency supersession present — conflicts by recency (superseded not deleted), confidence-merge kept for non-conflicts"
    else
        fail "sync-context: recency supersession missing — same-scope CONFLICTS must be resolved by recency (write-time supersession: max 1 active per scope, older -> superseded, never deleted), not by confidence alone (stale-high-confidence interference)"
    fi
fi

# --- sync-context 4.A privacy pre-filter (must run BEFORE the gate) ---
# A denied tag (credentials/pii/secret) or signal_type "mood" must be dropped from the
# push set BEFORE the confidence threshold / promotion gate — privacy cannot be bought
# back by a high confidence or occurrence count.
echo ""
echo "-- sync-context: privacy pre-filter precedes the gate --"
if [ -f "$SC_SUP_FILE" ]; then
    if grep -qiE "\(privacy-filter\)" "$SC_SUP_FILE" \
       && grep -qiE "MEM_GLOBAL_DENY_TAGS|is_denied" "$SC_SUP_FILE" \
       && grep -qiE "Denied \(privacy\)" "$SC_SUP_FILE" \
       && grep -qiE "BEFORE the (threshold|gate)|runs BEFORE|checked first" "$SC_SUP_FILE"; then
        pass "sync-context: privacy pre-filter present and ordered before the gate"
    else
        fail "sync-context: privacy pre-filter missing or not ordered — denied tags / signal_type mood must be dropped BEFORE the confidence/promotion gate (Denied (privacy)), reading MEM_GLOBAL_DENY_TAGS via is_denied"
    fi
fi

# --- sync-context 4.A global provenance schema on push ---
# Every entry written to the global store carries the G-<type>-<n> provenance contract:
# scope (conflict key), valid_from, source_evidence, lifecycle.
echo ""
echo "-- sync-context: global provenance schema stamped on push --"
if [ -f "$SC_SUP_FILE" ]; then
    if grep -qiE "\(provenance-schema\)" "$SC_SUP_FILE" \
       && grep -qiE "G-<?fact_type>?-|G-pattern-" "$SC_SUP_FILE" \
       && grep -qiE "valid_from" "$SC_SUP_FILE" \
       && grep -qiE "source_evidence" "$SC_SUP_FILE" \
       && grep -qiE "scope.*compute_scope|compute_scope\(fact_type" "$SC_SUP_FILE"; then
        pass "sync-context: global provenance schema present (G-<type>-<n>, scope, valid_from, source_evidence)"
    else
        fail "sync-context: global provenance schema missing — push must stamp G-<fact_type>-<n> id, scope=compute_scope(fact_type,tags), valid_from, source_evidence, lifecycle on every global entry"
    fi
fi

# --- sync-context 4.A promotion gate (local -> global) ---
# Three hard conditions to promote to active: confidence>=0.6 AND occurrences>=3 AND
# |source_projects|>=2. Failing the gate keeps the entry as candidate (never active).
echo ""
echo "-- sync-context: promotion gate (conf>=0.6 ∧ occ>=3 ∧ projects>=2) --"
if [ -f "$SC_SUP_FILE" ]; then
    if grep -qiE "\(promotion-gate\)" "$SC_SUP_FILE" \
       && grep -qiE "passes_promotion_gate" "$SC_SUP_FILE" \
       && grep -qiE "occurrences >= 3" "$SC_SUP_FILE" \
       && grep -qiE "source_projects\` ?>= 2|source_projects\| ?>= 2|\|source_projects\| >= 2" "$SC_SUP_FILE" \
       && grep -qiE "lifecycle: ?candidate|stays .candidate" "$SC_SUP_FILE"; then
        pass "sync-context: promotion gate present — 3 conditions, fail keeps candidate"
    else
        fail "sync-context: promotion gate missing — promotion to active needs confidence>=0.6 AND occurrences>=3 AND |source_projects|>=2 (passes_promotion_gate); failing keeps lifecycle:candidate"
    fi
fi

# --- sync-context 4.A pull lifecycle filter ---
# Pull serves ONLY lifecycle:active; candidate/superseded/archived are never pulled.
echo ""
echo "-- sync-context: pull serves only lifecycle:active --"
if [ -f "$SC_SUP_FILE" ]; then
    if grep -qiE "\(pull-lifecycle-filter\)" "$SC_SUP_FILE" \
       && grep -qiE "lifecycle: ?active" "$SC_SUP_FILE" \
       && grep -qiE "[Ss]kip .*(candidate|superseded|archived)" "$SC_SUP_FILE"; then
        pass "sync-context: pull lifecycle filter present — only active served"
    else
        fail "sync-context: pull lifecycle filter missing — Pull must serve ONLY lifecycle:active and skip candidate/superseded/archived"
    fi
fi

# --- /agentic-os:maintain 4.A global decay ---
# -0.1 per 90 days without recall, floor 0.3, lifecycle:archived (never hard-delete).
MM_FILE="$PLUGIN_ROOT/commands/maintain.md"
echo ""
echo "-- maintain: global confidence decay (floor 0.3, archive not delete) --"
if [ -f "$MM_FILE" ]; then
    if grep -qiE "\(global-decay\)" "$MM_FILE" \
       && grep -qE "scripts/global_decay\.py" "$MM_FILE" \
       && grep -qiE "0\.1 per .*90|90-day step" "$MM_FILE" \
       && grep -qiE "floor(ed)? at 0\.3" "$MM_FILE" \
       && grep -qiE "decay_steps_applied" "$MM_FILE" \
       && grep -qiE "lifecycle: ?.archived|never hard-delete" "$MM_FILE"; then
        pass "maintain: global decay present — global_decay.py, -0.1/90d booked once, floor 0.3, archive not delete"
    else
        fail "maintain: global decay missing — must run scripts/global_decay.py (-0.1 per 90 days booked once via decay_steps_applied, floor 0.3), set lifecycle:archived past 365d, never hard-delete"
    fi
fi
# --- 5.2.1: maintain only reports (D-021, owner decision 2026-10-06, widened 2026-10-07) ---
# Count-based archiving took long-term and importance>=4 learnings and ready patterns out
# of retrieval (archive files are invisible to the Atlas). Nothing in maintain may move
# store entries any more, and the dirty-marker GC runs as preview (the daily run deletes closed markers).
echo ""
echo "-- maintain: report only, never moves store entries (D-021) --"
if [ -f "$MM_FILE" ]; then
    if grep -qiE "keep the newest|move the rest|move to .*-archive|→ move" "$MM_FILE"; then
        fail "maintain: still moves store entries into archive files — D-021 allows reporting only"
    else
        pass "maintain: no instruction moves store entries into archive files"
    fi
    if grep -qE "gc_dirty_markers\.py.*--apply" "$MM_FILE"; then
        fail "maintain: dirty-marker GC runs with --apply - deleting is the daily run's job (D-024)"
    else
        pass "maintain: dirty-marker GC runs as preview only"
    fi
    if grep -qE "\(no-archive\)" "$MM_FILE" && grep -qE "D-021" "$MM_FILE"; then
        pass "maintain: no-archive rule pinned ((no-archive) marker + D-021)"
    else
        fail "maintain: (no-archive) marker or D-021 reference missing"
    fi
fi
for f in commands/sync-context.md commands/memory-audit.md; do
    if grep -q "STILLGELEGT.md" "$PLUGIN_ROOT/$f"; then
        pass "$f honours the decommissioned global layer (STILLGELEGT.md)"
    else
        fail "$f does not check ~/.claude-memory/global/STILLGELEGT.md (G-05)"
    fi
done
# The guard only helps if it runs before the auto-setup creates the global store.
SC_GUARD=$(grep -n "STILLGELEGT.md" "$PLUGIN_ROOT/commands/sync-context.md" | head -1 | cut -d: -f1)
SC_PREREQ=$(grep -n "^## Prerequisites" "$PLUGIN_ROOT/commands/sync-context.md" | head -1 | cut -d: -f1)
if [ -n "$SC_GUARD" ] && [ -n "$SC_PREREQ" ] && [ "$SC_GUARD" -lt "$SC_PREREQ" ]; then
    pass "sync-context checks STILLGELEGT.md before its auto-setup"
else
    fail "sync-context STILLGELEGT check (line ${SC_GUARD:-none}) must precede ## Prerequisites (line ${SC_PREREQ:-none})"
fi

# 5.2.1 review: the rule binds every ACTIVE doc that steers a model or describes the
# flow - a rollback in wrap-up, bootstrap or references stayed green before.
echo ""
echo "-- active docs: no count-based archiving, no silent reset (D-021) --"
DOC_HITS=$(grep -n -i -E "keep (the )?newest|archive rest|move the rest|backup \+ recreate|create fresh|maintain archives|rotation is|gc_dirty_markers\.py.*--apply" \
    "$PLUGIN_ROOT"/skills/*/SKILL.md "$PLUGIN_ROOT"/skills/*/references/*.md "$PLUGIN_ROOT"/skills/DEPENDENCIES.md \
    "$PLUGIN_ROOT"/references/*.md "$PLUGIN_ROOT"/commands/maintain.md "$PLUGIN_ROOT"/commands/status.md \
    "$PLUGIN_ROOT"/commands/init.md "$PLUGIN_ROOT"/commands/log.md "$PLUGIN_ROOT"/commands/memory-audit.md 2>/dev/null)
if [ -z "$DOC_HITS" ]; then
    pass "no active doc archives by count, resets a store or deletes dirty markers"
else
    fail "active docs still prescribe archiving/reset: $(echo "$DOC_HITS" | head -5 | tr '\n' ' ')"
fi

# The decay formula has ONE home (global_decay.py). A second copy in the sourceable
# helpers would let the prose path decay from the stored value again (2026-10-01 bug).
if grep -qE "^apply_decay\(\)" "$PLUGIN_ROOT/scripts/global-schema.sh" 2>/dev/null; then
    fail "global-schema.sh still defines apply_decay — the decay rule lives only in scripts/global_decay.py"
else
    pass "decay rule has a single home (global-schema.sh defines no apply_decay)"
fi

# --- session-bootstrap 4.A staleness wrap (read-only display, NO write) ---
SB_FILE="$SKILLS_DIR/session-bootstrap/SKILL.md"
echo ""
echo "-- session-bootstrap: staleness wrap is read-only display (no decay/write) --"
if [ -f "$SB_FILE" ]; then
    if grep -qiE "\(staleness-wrap\)" "$SB_FILE" \
       && grep -qiE "90 days|> ?90" "$SB_FILE" \
       && grep -qiE "display only|read-time annotation|never .*(write|mutate)" "$SB_FILE" \
       && grep -qiE "do NOT .*(decay|write).*confidence|only marks, never mutates"  "$SB_FILE"; then
        pass "session-bootstrap: staleness wrap present and read-only (marks, never mutates)"
    else
        fail "session-bootstrap: staleness wrap missing or not read-only — must mark entries >90d for display WITHOUT writing/decaying confidence (decay belongs to /agentic-os:maintain)"
    fi
fi

# skill-generator removed in v4.0.0 — its skill-candidate flow lives inside pattern-extractor,
# which is itself the canonical-schema writer; legacy-field consumption test removed

# --- obsidian-sync: Rolling Synthesis gates on `importance`, not nonexistent `salience` ---
# wrap-up (sole writer of learnings.json) stores `importance` (1-5); there is no stored
# `salience` field (session-bootstrap only DERIVES a salience score from importance).
# obsidian-sync's synthesis gate must read `importance >= 4` to match the writer schema.
OS_FILE="$SKILLS_DIR/obsidian-sync/SKILL.md"
echo ""
echo "-- obsidian-sync: Rolling Synthesis gate uses importance (writer schema), not salience --"
if [ -f "$OS_FILE" ]; then
    if ! grep -qE "salience >= 4" "$OS_FILE" \
       && grep -qE "importance >= 4" "$OS_FILE"; then
        pass "obsidian-sync: synthesis gate uses importance >= 4 (matches learnings.json schema)"
    else
        fail "obsidian-sync: synthesis gate reads nonexistent stored field 'salience >= 4' — wrap-up writes 'importance'; must gate on importance >= 4"
    fi
fi

# --- Model-Routing SSoT consistency (v4.7.0) ---
# The frontmatter `model:`/`effort:` of every skill must match the routing
# table in scripts/model-routing.sh ("-" in the table = field must be ABSENT).
# Only top-level fields count (^model:), so metadata sub-keys never match.
#
# SCOPE WARNING: this asserts frontmatter and SSoT table agree. It does NOT
# assert the runtime honours either — and as of 2026-07-27 it does not (the
# `model:` frontmatter is a measured no-op; see the header of
# scripts/model-routing.sh). A green run here means "declared consistently",
# never "ran on the cheap class". The effect test is the E0 transcript probe
# in docs/model-routing-eval-checklist.md and cannot run in CI.
echo ""
echo "-- model routing: skill frontmatter matches scripts/model-routing.sh --"
MR_SCRIPT="$PLUGIN_ROOT/scripts/model-routing.sh"
if [ -f "$MR_SCRIPT" ]; then
    while IFS=$'\t' read -r mr_skill mr_class mr_model mr_effort; do
        [ -n "$mr_skill" ] || continue
        mr_file="$SKILLS_DIR/$mr_skill/SKILL.md"
        if [ ! -f "$mr_file" ]; then
            fail "model-routing: $mr_skill listed in SSoT but SKILL.md missing"
            continue
        fi
        mr_fm=$(awk '/^---/{c++} c==1{print} c==2{exit}' "$mr_file")
        got_model=$(echo "$mr_fm" | grep '^model:' | head -1 | sed 's/^model: *//' | tr -d ' \r')
        got_effort=$(echo "$mr_fm" | grep '^effort:' | head -1 | sed 's/^effort: *//' | tr -d ' \r')
        want_model="$mr_model"; [ "$want_model" = "-" ] && want_model=""
        want_effort="$mr_effort"; [ "$want_effort" = "-" ] && want_effort=""
        if [ "$got_model" = "$want_model" ] && [ "$got_effort" = "$want_effort" ]; then
            pass "model-routing: $mr_skill frontmatter matches SSoT ($mr_class: model='${want_model:--}' effort='${want_effort:--}')"
        else
            fail "model-routing: $mr_skill frontmatter (model='$got_model' effort='$got_effort') != SSoT (model='$want_model' effort='$want_effort') — fix frontmatter OR scripts/model-routing.sh, they must never drift"
        fi
    done < <(bash "$MR_SCRIPT" list)

    # Agents: same check against list-agents (agents/<name>.md)
    while IFS=$'\t' read -r mr_agent mr_class mr_model mr_effort; do
        [ -n "$mr_agent" ] || continue
        mr_afile="$PLUGIN_ROOT/agents/$mr_agent.md"
        if [ ! -f "$mr_afile" ]; then
            fail "model-routing: agent $mr_agent listed in SSoT but agents/$mr_agent.md missing"
            continue
        fi
        mr_afm=$(awk '/^---/{c++} c==1{print} c==2{exit}' "$mr_afile")
        got_model=$(echo "$mr_afm" | grep '^model:' | head -1 | sed 's/^model: *//' | tr -d ' \r')
        got_effort=$(echo "$mr_afm" | grep '^effort:' | head -1 | sed 's/^effort: *//' | tr -d ' \r')
        want_model="$mr_model"; [ "$want_model" = "-" ] && want_model=""
        want_effort="$mr_effort"; [ "$want_effort" = "-" ] && want_effort=""
        if [ "$got_model" = "$want_model" ] && [ "$got_effort" = "$want_effort" ]; then
            pass "model-routing: agent $mr_agent frontmatter matches SSoT"
        else
            fail "model-routing: agent $mr_agent frontmatter (model='$got_model' effort='$got_effort') != SSoT (model='$want_model' effort='$want_effort')"
        fi
    done < <(bash "$MR_SCRIPT" list-agents)

    # Reverse direction: every skill dir / agent file must have an SSoT row
    MR_SKILL_LIST=$(bash "$MR_SCRIPT" list | cut -f1)
    for mr_dir in "$SKILLS_DIR"/*/; do
        mr_name=$(basename "$mr_dir")
        if echo "$MR_SKILL_LIST" | grep -qx "$mr_name"; then
            pass "model-routing: $mr_name covered by SSoT"
        else
            fail "model-routing: skill $mr_name has no row in scripts/model-routing.sh — every skill needs a class assignment"
        fi
    done
    MR_AGENT_LIST=$(bash "$MR_SCRIPT" list-agents | cut -f1)
    for mr_af in "$PLUGIN_ROOT"/agents/*.md; do
        mr_aname=$(basename "$mr_af" .md)
        if echo "$MR_AGENT_LIST" | grep -qx "$mr_aname"; then
            pass "model-routing: agent $mr_aname covered by SSoT"
        else
            fail "model-routing: agent $mr_aname has no row in scripts/model-routing.sh list-agents"
        fi
    done
else
    fail "model-routing: scripts/model-routing.sh missing — model-class SSoT required since v4.7.0"
fi

# --- Model-Routing v4.7.0: wrap-up stage-0 + context diet + escalation + trace ---
echo ""
echo "-- wrap-up: stage-0 preprocess, context diet, delta update, escalation, cost trace --"
WU_MR_FILE="$SKILLS_DIR/wrap-up/SKILL.md"
if [ -f "$WU_MR_FILE" ]; then
    if grep -q "(stage0-preprocess)" "$WU_MR_FILE" && grep -q "preprocess_state.py" "$WU_MR_FILE"; then
        pass "wrap-up: (stage0-preprocess) — deterministic preflight via preprocess_state.py"
    else
        fail "wrap-up: missing (stage0-preprocess) — Step 0 must run scripts/preprocess_state.py and use its JSON as primary data source"
    fi
    CD_BLOCK=$(grep -A4 "(context-diet)" "$WU_MR_FILE")
    if echo "$CD_BLOCK" | grep -qi "NOT systematically re-read" && echo "$CD_BLOCK" | grep -qi "targeted"; then
        pass "wrap-up: (context-diet) — no systematic transcript re-read, targeted lookups only"
    else
        fail "wrap-up: missing (context-diet) — must forbid systematic transcript/full-memory re-reads (state object + held context first, targeted lookups only)"
    fi
    DU_BLOCK=$(grep -A4 "(delta-update)" "$WU_MR_FILE")
    if echo "$DU_BLOCK" | grep -qi "delta" && echo "$DU_BLOCK" | grep -qi "unchanged sections"; then
        pass "wrap-up: (delta-update) — session-summary updated as delta, unchanged sections untouched"
    else
        fail "wrap-up: missing (delta-update) — Step 5 must update session-summary.md as a delta (only changed sections), not rewrite the whole file"
    fi
    ER_BLOCK=$(grep -A20 "(escalation-rules)" "$WU_MR_FILE")
    if echo "$ER_BLOCK" | grep -q "escalations-" \
       && echo "$ER_BLOCK" | grep -q "ESKALATION:" \
       && echo "$ER_BLOCK" | grep -qi "contradict" \
       && echo "$ER_BLOCK" | grep -qi "identity" \
       && echo "$ER_BLOCK" | grep -qi "difficult to reverse"; then
        pass "wrap-up: (escalation-rules) — conditions + escalations log + visible marker"
    else
        fail "wrap-up: missing/incomplete (escalation-rules) — must log to working/escalations-<sid>.json, emit ESKALATION: line, and name the conditions (contradiction, identity, decision replacement, pattern promotion, hard-to-reverse, missing sources)"
    fi
    CT_BLOCK=$(grep -A24 "(cost-trace)" "$WU_MR_FILE")
    if echo "$CT_BLOCK" | grep -q "measure_session_cost.py" \
       && echo "$CT_BLOCK" | grep -q -- "--locate" \
       && echo "$CT_BLOCK" | grep -q "cost-trace.sh" \
       && echo "$CT_BLOCK" | grep -q "cheap-write"; then
        pass "wrap-up: (cost-trace) — measured via measure_session_cost.py --locate, estimate fallback via cost-trace.sh"
    else
        fail "wrap-up: missing/incomplete (cost-trace) — end of run must measure via measure_session_cost.py --locate <session-id> --append-trace and keep the cost-trace.sh cheap-write estimate as fallback (T-016)"
    fi
fi

# --- Model-Routing v4.7.0: session-bootstrap fast path + escalation + trace ---
echo ""
echo "-- session-bootstrap: fast path, escalation, cost trace --"
SB_MR_FILE="$SKILLS_DIR/session-bootstrap/SKILL.md"
if [ -f "$SB_MR_FILE" ]; then
    FP_BLOCK=$(grep -A16 "(bootstrap-fast-path)" "$SB_MR_FILE")
    if echo "$FP_BLOCK" | grep -q "preprocess_state.py" \
       && echo "$FP_BLOCK" | grep -q "previous_state_hash" \
       && echo "$FP_BLOCK" | grep -qi "skip the full knowledge load" \
       && echo "$FP_BLOCK" | grep -qi "health checks.*still run"; then
        pass "session-bootstrap: (bootstrap-fast-path) — hash short-circuit skips full load, health checks kept"
    else
        fail "session-bootstrap: missing/incomplete (bootstrap-fast-path) — must run preprocess_state.py, compare previous_state_hash == current_state_hash, skip the full knowledge load on equality while health checks still run"
    fi
    SB_ER_BLOCK=$(grep -A12 "(escalation-rules)" "$SB_MR_FILE")
    if echo "$SB_ER_BLOCK" | grep -q "escalations-" && echo "$SB_ER_BLOCK" | grep -q "ESKALATION:"; then
        pass "session-bootstrap: (escalation-rules) — escalations log + visible marker"
    else
        fail "session-bootstrap: missing (escalation-rules) — conflicts/stale states found during bootstrap must be logged to working/escalations-<sid>.json and flagged with ESKALATION:, not resolved by this run"
    fi
    SB_CT_BLOCK=$(grep -A8 "(cost-trace)" "$SB_MR_FILE")
    if echo "$SB_CT_BLOCK" | grep -q "cost-trace.sh" && echo "$SB_CT_BLOCK" | grep -q "session-bootstrap"; then
        pass "session-bootstrap: (cost-trace) — run cost logged via cost-trace.sh"
    else
        fail "session-bootstrap: missing (cost-trace) — end of briefing must call scripts/cost-trace.sh append --task session-bootstrap"
    fi
fi

# --- wrap-up: batch-apply runs BEFORE wiki sync / handoff; marker is its own final call (T-024, L44) ---
# obsidian-sync (7.5) and the central handoff (7.6) read learnings/decisions/session-summary
# from DISK. When the main batch write came after them (old Step 8.5) they saw the state
# BEFORE this session. The marker must NOT ride on that main call, or the wiki/handoff
# writes land after it as tail writes and trip bootstrap's recovery detection.
echo ""
echo "-- wrap-up: (batch-apply) precedes wiki sync + handoff; (consolidation-marker) has its own call --"
WU_ORD_FILE="$SKILLS_DIR/wrap-up/SKILL.md"
if [ -f "$WU_ORD_FILE" ]; then
    BA_LINE=$(grep -n "(batch-apply)" "$WU_ORD_FILE" | head -1 | cut -d: -f1)
    WS_LINE=$(grep -n "^## Step.*Obsidian Wiki Sync" "$WU_ORD_FILE" | head -1 | cut -d: -f1)
    HO_LINE=$(grep -n "^## Step.*Central Cross-Project Handoff" "$WU_ORD_FILE" | head -1 | cut -d: -f1)
    if [ -n "$BA_LINE" ] && [ -n "$WS_LINE" ] && [ -n "$HO_LINE" ] \
       && [ "$BA_LINE" -lt "$WS_LINE" ] && [ "$BA_LINE" -lt "$HO_LINE" ]; then
        pass "wrap-up: (batch-apply) step comes before Obsidian Wiki Sync and Central Handoff (they read the written state)"
    else
        fail "wrap-up: (batch-apply) must precede the Obsidian Wiki Sync and Central Handoff steps — otherwise obsidian-sync reads learnings/decisions/summary from BEFORE this session (L44)"
    fi
    BA_BLOCK=$(awk '/\(batch-apply\)/{f=1; print; next} f && /^## /{exit} f' "$WU_ORD_FILE")
    CM_BLOCK=$(awk '/\(consolidation-marker\)/{f=1; print; next} f && /^## /{exit} f' "$WU_ORD_FILE")
    if echo "$CM_BLOCK" | grep -q "apply_wrapup.py" && echo "$CM_BLOCK" | grep -q '"consolidate": true' \
       && ! echo "$BA_BLOCK" | grep -q '"consolidate": true'; then
        pass "wrap-up: (consolidation-marker) is a separate marker-only apply_wrapup call; main (batch-apply) plan carries no consolidate"
    else
        fail "wrap-up: (consolidation-marker) must run its own apply_wrapup.py call with {\"consolidate\": true} AFTER wiki/handoff, and the (batch-apply) plan must not set consolidate (tail-write false recoveries)"
    fi
fi

echo ""
echo "-- identity: central profile (P2, Release A) --"
# Prose anchors for the central-profile contract the scripts enforce (tests/test-identity-central.py proves the code side).
CP_BLOCK=$(grep -A24 -E "\(central-profile\)" "$SKILLS_DIR/wrap-up/SKILL.md")
if [ -n "$CP_BLOCK" ] \
   && echo "$CP_BLOCK" | grep -qF "profile.md" \
   && echo "$CP_BLOCK" | grep -qF -- "--identity-store" \
   && echo "$CP_BLOCK" | grep -qF "AGENTIC_OS_IDENTITY_STORE" \
   && echo "$CP_BLOCK" | grep -qF "origin" \
   && echo "$CP_BLOCK" | grep -qE "PC[0-9n]" \
   && echo "$CP_BLOCK" | grep -qiE "not in profile.md"; then
    pass "wrap-up: (central-profile) present — active only with profile.md, --identity-store / AGENTIC_OS_IDENTITY_STORE, PC ids with origin, legacy-only supersede skipped"
else
    fail "wrap-up: (central-profile) missing — Step 6 must say the central profile is active exactly when profile.md exists, name --identity-store and AGENTIC_OS_IDENTITY_STORE, the PC ids with origin, and that a supersede of a line found only in the project's user.md is skipped ('not in profile.md')"
fi
if grep -qF 'profile.md' <(grep -E "^\`Identity: " "$SKILLS_DIR/wrap-up/SKILL.md"); then
    pass "wrap-up: the identity status line names profile.md for the central mode"
else
    fail "wrap-up: the Step 6.5 identity status line must show the central variant (→ profile.md promotet)"
fi
BS_CP=$(grep -A10 -E "\(central-profile-read\)" "$SKILLS_DIR/session-bootstrap/SKILL.md")
if [ -n "$BS_CP" ] && echo "$BS_CP" | grep -qF "profile-briefing.txt" && echo "$BS_CP" | grep -qF "identity.promotable" \
   && echo "$BS_CP" | grep -qF "identity.last_seen" && echo "$BS_CP" | grep -qiE "central profile" \
   && echo "$BS_CP" | grep -qiE "never read"; then
    pass "session-bootstrap: (central-profile-read) present — reads the projection profile-briefing.txt and takes gates 6.5(b)/(c) from stage 0 (identity.promotable, identity.last_seen), never the central queue"
else
    fail "session-bootstrap: (central-profile-read) missing — with the central profile active, Step 2 reads identity/profile-briefing.txt and gates 6.5(b)/(c) use stage-0 identity.promotable / identity.last_seen; the central queue is never read"
fi
SCH="$SKILLS_DIR/wrap-up/references/wrapup-schemas.md"
if grep -qF -- "--identity-store" "$SCH" && grep -qE "\"origin\"" "$SCH" && grep -qE "PC[0-9]" "$SCH"; then
    pass "wrapup-schemas: central profile documented — --identity-store, origin, PC ids"
else
    fail "wrapup-schemas: the central profile is missing — document --identity-store, the candidate field origin and the PC ids"
fi
if grep -qiE "central profile" "$PLUGIN_ROOT/commands/memory-audit.md"; then
    pass "memory-audit: the user.md stub check knows the central profile (no false 'not growing')"
else
    fail "memory-audit: the user.md stub check must skip 'not growing' while the central profile is active"
fi

echo ""
echo "=== Results: $PASSED/$TESTS passed, $ERRORS failures ==="
[ "$ERRORS" -eq 0 ]

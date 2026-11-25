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

    # (handoff-dedup), (next-steps-pointer): mechanics are code since T-028b (wrapup_parts/handoff.py,
    # _judged_block); tests/test-wrapup-judge.py and test-wrapup-core.py prove them. The body must not
    # send the model back to writing the handoff by hand.
    if ! grep -qE "handoff_write_guard|Read-then-write guard" "$WU_HO_FILE"; then
        pass "wrap-up: the handoff is written by the core, the body has no hand-written path"
    else
        fail "wrap-up: body still describes writing the central handoff by hand — that is wrapup_core.py's job since T-028b"
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

    # The session note is a projection of the plan since T-028b; obsidian-sync stays manual.
    if ! grep -qE "invoke .obsidian-sync|Delegates to the .obsidian-sync" "$WU_WS_FILE" && grep -qF "wiki_sync_line" "$WU_WS_FILE"; then
        pass "wrap-up: the wiki note comes from the core, obsidian-sync is not invoked (T-028b)"
    else
        fail "wrap-up: body must report wiki_sync_line and must not invoke obsidian-sync (spec 2026-09-08 scope decision)"
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
echo "-- wrap-up: no NotebookLM offer in the routine path (spec 2026-09-08, D-018) --"
WU2_FILE="$SKILLS_DIR/wrap-up/SKILL.md"
if [ -f "$WU2_FILE" ]; then
    if ! grep -qiE "notebook-registry|NotebookLM Sync|offer sync" "$WU2_FILE"; then
        pass "wrap-up: no NotebookLM offer; the notebooklm user skill stays manual"
    else
        fail "wrap-up: body still offers a NotebookLM sync — owner scope decision 2026-09-08 took it out of the flow"
    fi
fi

echo ""
# retrospective skill removed in v4.0.0 — periodic-retrospective bracket test removed

# --- Memory Growth Engine (Master-Plan 2026-06-03, Sprint user.md + soul.md) ---
# Pins the growth mechanics into the owning skill bodies. Marker-based + concept phrase,
# bidirectionally verified (strip -> FAIL, restore -> PASS).
WU_GROW_FILE="$SKILLS_DIR/wrap-up/references/judge-rules.md"  # rules moved here in T-028b
SB_GROW_FILE="$SKILLS_DIR/session-bootstrap/SKILL.md"
MM_GROW_FILE="$PLUGIN_ROOT/commands/maintain.md"

echo ""
echo "-- wrap-up: profile changes — trust boundary (conversation-only) --"
if [ -f "$WU_GROW_FILE" ]; then
    # Profile changes must originate from user conversation, NEVER from web/docs/notebooklm/wiki (poisoning).
    # Bind the forbidden-source list to the (trust-boundary) marker's own block (grep -A2), so an
    # unrelated 'NotebookLM' elsewhere (Step 7 sync) can't keep this green if the boundary is stripped
    # (Codex MAJOR #2 — verified strip->FAIL on the marker block).
    TB_BLOCK=$(grep -A2 -iE "\(trust-boundary\)" "$WU_GROW_FILE")
    if [ -n "$TB_BLOCK" ] \
       && echo "$TB_BLOCK" | grep -qiE "trust_source|conversation" \
       && echo "$TB_BLOCK" | grep -qiE "web/docs|NotebookLM|wiki content|untrusted"; then
        pass "wrap-up: profile changes — trust boundary enforced (conversation-only, forbidden sources named in marker block)"
    else
        fail "wrap-up: profile changes missing trust boundary — the (trust-boundary) block must name the forbidden sources (web/docs/NotebookLM/wiki) a profile change may NEVER come from (memory poisoning)"
    fi
fi

echo ""
echo "-- wrap-up: soul.md growth retired (5.11.0), never auto-write --"
if [ -f "$WU_GROW_FILE" ]; then
    # 5.11.0: Step 6.4 is retired - it must say so, forbid planning soul candidates and keep the never-write invariant.
    if grep -qiE "\(soul-growth\)" "$WU_GROW_FILE" \
       && grep -qiE "Retired in 5\.11\.0" "$WU_GROW_FILE" \
       && grep -qiE "Do NOT plan .soul_candidates." "$WU_GROW_FILE" \
       && grep -qiE "[Nn]ever write .?soul\.md|[Nn]ever auto-write|NEVER auto-written|not write .?soul\.md here" "$WU_GROW_FILE"; then
        pass "wrap-up: soul.md growth retired — no soul candidates planned, soul.md never written"
    else
        fail "wrap-up: Step 6.4 must state the retirement (5.11.0), forbid planning soul_candidates and keep the never-write-soul.md invariant"
    fi
fi

echo ""
echo "-- wrap-up: identity quality (P1/B2) — quote duty, supersede, plain text --"
if [ -f "$WU_GROW_FILE" ]; then
    # Prose anchors for the B2 contract the scripts enforce (tests/test-identity-b2.py proves the code side).
    # quote duty: a supersede / retire needs a LITERAL quote that is verified against the transcript; the verdict values are named
    QD_BLOCK=$(grep -A14 -iE "\(quote-duty\)" "$WU_GROW_FILE")
    if [ -n "$QD_BLOCK" ] \
       && echo "$QD_BLOCK" | grep -qE "LITERAL" \
       && echo "$QD_BLOCK" | grep -qE "quote_verified" \
       && echo "$QD_BLOCK" | grep -qE "kein_transkript" \
       && echo "$QD_BLOCK" | grep -qE "user_md_retire|retire"; then
        pass "wrap-up: quote duty present — a supersede or retire needs a literal, verified quote (true|false|kein_transkript)"
    else
        fail "wrap-up: quote duty missing — the (quote-duty) block of Step 6 must demand a LITERAL quote for supersede and retire and name quote_verified and kein_transkript"
    fi
    # 6.0.0: the harvest is gone, judge-rules says so and names the ignored sections
    if grep -qE "\(key-reuse\)|\(queue-re-review\)|\(identity-harvest\)" "$WU_GROW_FILE"; then
        fail "wrap-up: judge-rules still carries harvest anchors (key-reuse / queue-re-review / identity-harvest) - the harvest was removed in 6.0.0"
    elif grep -qE "ignored_sections" "$WU_GROW_FILE" && grep -qF 'Ignoriert (6.0.0)' "$WU_GROW_FILE"; then
        pass "wrap-up: harvest removed — no harvest anchors, old sections are named as ignored (ignored_sections, Ignoriert (6.0.0))"
    else
        fail "wrap-up: Step 6 must say that old identity/user_candidates/errors sections are ignored (tally.ignored_sections, 'Ignoriert (6.0.0)')"
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
    # quote duty details (D4, D5): length, word boundaries, negation, which sources do not count, the reasons
    QD2_BLOCK=$(grep -A30 -iE "\(quote-duty\)" "$WU_GROW_FILE" | tr -s '[:space:]' ' ')
    if echo "$QD2_BLOCK" | grep -qE "8 characters and 3 words" \
       && echo "$QD2_BLOCK" | grep -qiE "word boundaries" \
       && echo "$QD2_BLOCK" | grep -qiE "negation word" \
       && echo "$QD2_BLOCK" | grep -qiE "accepted prompt suggestions" \
       && echo "$QD2_BLOCK" | grep -qiE "pasted text" \
       && echo "$QD2_BLOCK" | grep -qE "too_few_words"; then
        pass "wrap-up: quote duty details present — 3 words, word boundaries, negation neighbour, sources that do not count, the reasons"
    else
        fail "wrap-up: quote duty details missing — Step 6 (quote-duty) must name 3 words, word boundaries, the negation neighbour rule, accepted prompt suggestions and pasted text as non-sources, and the reasons (too_few_words, ...)"
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
    # the session id is the real one (D2) and pre hands it to stage 0
    if grep -qE "CLAUDE_CODE_SESSION_ID" "$SKILLS_DIR/wrap-up/SKILL.md" \
       && grep -qE "session_binding_mismatch" "$SKILLS_DIR/wrap-up/SKILL.md" \
       && awk '/^def cmd_pre/{f=1} /^def cmd_apply_plan/{f=0} f' "$PLUGIN_ROOT/scripts/wrapup_core.py" | grep -qE '"preprocess_state.py", \[mem, "--session-id", session_id'; then
        pass "wrap-up: session id rules present — the body names the real session id, pre hands it to stage 0"
    else
        fail "wrap-up: session id rules missing — the body must say the session id is \$CLAUDE_CODE_SESSION_ID (session_binding_mismatch), wrapup_core.py must pass --session-id on"
    fi
    # maintain (Pflegelauf) Steps 2-3: a pointer entry carries the plain text (D11), the applier refuses one without it
    MPF_BLOCK=$(sed -n '/^## Step 2:/,/^## Step 4:/p' "$MM_GROW_FILE" 2>/dev/null)
    if echo "$MPF_BLOCK" | grep -qE "\*\*\`plain\`\*\*" \
       && echo "$MPF_BLOCK" | grep -qE "summary, anchor, members, plain, tags" \
       && echo "$MPF_BLOCK" | grep -qE "600" \
       && echo "$MPF_BLOCK" | grep -qiE "refuses a pointer without .plain."; then
        pass "maintain Steps 2-3: the pointer entry carries plain (<= 600), the plan shows it, the applier refuses one without it"
    else
        fail "maintain Steps 2-3: plain missing — the pointer entry must show **\`plain\`**, the plan must be {summary, anchor, members, plain, tags}, 600 characters, and the applier refuses a pointer without plain"
    fi
    # --- second round after the reviewer's verdict (D1 gap, D3, D5, D7, D9) ---
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
    # a quote and a new_text of different polarity are counted; the counter is a weak hint without a warning line (R3 round 3/4)
    QL3_BLOCK=$(grep -A34 -E "\(quote-limits\)" "$WU_GROW_FILE")
    if echo "$QL3_BLOCK" | grep -qE "quote_polarity" \
       && echo "$QL3_BLOCK" | grep -qiE "negation" \
       && echo "$QL3_BLOCK" | grep -qE "WEAK hint" \
       && echo "$QL3_BLOCK" | grep -qE "NO warning line"; then
        pass "wrap-up: polarity hint present — one-sided negation is counted (quote_polarity), a WEAK hint with NO warning line"
    else
        fail "wrap-up: polarity hint missing — (quote-limits) must say that a verified quote and a new_text of which only one carries a negation word are counted (tally.quote_polarity), a WEAK hint with NO warning line"
    fi
fi

echo ""
echo "-- session-bootstrap: read-only without exception (soul gate 5.11.0, identity gates 6.0.0 gone) --"
if [ -f "$SB_GROW_FILE" ]; then
    if grep -qE "Do NOT write to any files yourself: no gate, no queue, no exception" "$SB_GROW_FILE" \
       && ! grep -qiE "SOUL CANDIDATES|soul-candidates\.md|\[j/n\]" "$SB_GROW_FILE"; then
        pass "session-bootstrap: read-only without exception — no soul or identity gate, no [j/n] question"
    else
        fail "session-bootstrap: must state it writes nothing (no gate, no queue, no exception) and carry no SOUL CANDIDATES / soul-candidates.md / [j/n] gate"
    fi
fi

# 6.0.0: the soul.md 80-line check left maintain with the identity harvest (frozen); maintain is the Pflegelauf now.

# --- 5.11.0: the global layer (sync-context, global decay, global schema) is archived ---
# maintain must no longer carry Step 4b / global_decay; the tests of the removed pieces went to _archived/.
MM_FILE="$PLUGIN_ROOT/commands/maintain.md"
echo ""
echo "-- maintain: no global-layer step left (5.11.0) --"
if [ -f "$MM_FILE" ]; then
    if grep -qiE "global_decay|global-decay|claude-memory/global" "$MM_FILE"; then
        fail "maintain: still references the archived global layer (global_decay / ~/.claude-memory/global)"
    else
        pass "maintain: no reference to the archived global layer"
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
# 5.2.1 review: the rule binds every ACTIVE doc that steers a model or describes the
# flow - a rollback in wrap-up, bootstrap or references stayed green before.
echo ""
echo "-- active docs: no count-based archiving, no silent reset (D-021) --"
DOC_HITS=$(grep -n -i -E "keep (the )?newest|archive rest|move the rest|backup \+ recreate|create fresh|maintain archives|rotation is|gc_dirty_markers\.py.*--apply" \
    "$PLUGIN_ROOT"/skills/*/SKILL.md "$PLUGIN_ROOT"/skills/*/references/*.md "$PLUGIN_ROOT"/skills/DEPENDENCIES.md \
    "$PLUGIN_ROOT"/references/*.md "$PLUGIN_ROOT"/commands/maintain.md "$PLUGIN_ROOT"/commands/status.md \
    "$PLUGIN_ROOT"/commands/init.md "$PLUGIN_ROOT"/commands/log.md 2>/dev/null)
if [ -z "$DOC_HITS" ]; then
    pass "no active doc archives by count, resets a store or deletes dirty markers"
else
    fail "active docs still prescribe archiving/reset: $(echo "$DOC_HITS" | head -5 | tr '\n' ' ')"
fi

# 5.11.0: no active file may point at the archived global layer any more.
echo ""
echo "-- active files: no reference to the archived global layer (5.11.0) --"
GL_HITS=$(grep -n -E "sync-context|global_decay|global-schema|migrate-global-schema|claude-memory/global|MEM_GLOBAL_DENY_TAGS" \
    "$PLUGIN_ROOT"/skills/*/SKILL.md "$PLUGIN_ROOT"/skills/*/references/*.md \
    "$PLUGIN_ROOT"/references/memory-structure.md "$PLUGIN_ROOT"/commands/*.md "$PLUGIN_ROOT"/agents/*.md \
    "$PLUGIN_ROOT"/scripts/*.sh "$PLUGIN_ROOT"/scripts/*.py "$PLUGIN_ROOT"/hooks/hooks.json 2>/dev/null)
if [ -z "$GL_HITS" ]; then
    pass "no active skill, command, script or hook references the archived global layer"
else
    fail "active files still reference the archived global layer: $(echo "$GL_HITS" | head -5 | tr '\n' ' ')"
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

# --- Model-Routing v4.7.0: wrap-up stage-0 + context diet + measured cost trace (escalation markers removed in 6.0.0) ---
echo ""
echo "-- wrap-up: stage-0 preprocess, context diet, delta update, cost trace --"
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
    CORE_PY="$PLUGIN_ROOT/scripts/wrapup_core.py"
    if grep -q "measure_session_cost.py" "$CORE_PY" \
       && grep -q -- "--locate" "$CORE_PY" \
       && [ ! -e "$PLUGIN_ROOT/scripts/cost-trace.sh" ] \
       && ! grep -q "cost-trace\.sh" "$CORE_PY"; then
        pass "wrap-up: (cost-trace) — wrapup_core.py measures via measure_session_cost.py --locate, no estimate fallback (cost-trace.sh removed in 6.0.0)"
    else
        fail "wrap-up: (cost-trace) — wrapup_core.py must measure via measure_session_cost.py --locate and must not call the removed cost-trace.sh"
    fi
fi

# --- Model-Routing v4.7.0: session-bootstrap fast path ---
echo ""
echo "-- session-bootstrap: fast path --"
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
fi

# --- wrap-up: the store write precedes handoff and note, the marker comes last (T-024, L44; one process since T-028b) ---
# Handoff and note read what the run wrote; a marker before them would make them tail writes that trip
# bootstrap's recovery detection. tests/test-wrapup-judge.py proves the behaviour, this pins the order in the source.
echo ""
echo "-- wrap-up: apply order in wrapup_core.py (store, handoff, note, marker) --"
CORE_ORD="$PLUGIN_ROOT/scripts/wrapup_core.py"
ORD_BLOCK=$(awk '/^def cmd_apply_plan/{f=1} /^def cmd_followup/{f=0} f' "$CORE_ORD")
ST_LINE=$(echo "$ORD_BLOCK" | grep -n '_child("apply_wrapup.py", \[\*applier' | head -1 | cut -d: -f1)
HO_LINE=$(echo "$ORD_BLOCK" | grep -n '_publish(mem' | head -1 | cut -d: -f1)
WN_LINE=$(echo "$ORD_BLOCK" | grep -n 'write_session_note(' | head -1 | cut -d: -f1)
MK_LINE=$(echo "$ORD_BLOCK" | grep -n 'aw.apply_consolidation(' | head -1 | cut -d: -f1)
if [ -n "$ST_LINE" ] && [ -n "$HO_LINE" ] && [ -n "$WN_LINE" ] && [ -n "$MK_LINE" ] \
   && [ "$ST_LINE" -lt "$HO_LINE" ] && [ "$HO_LINE" -lt "$WN_LINE" ] && [ "$WN_LINE" -lt "$MK_LINE" ]; then
    pass "wrap-up: cmd_apply_plan writes the store first and the marker last"
else
    fail "wrap-up: cmd_apply_plan order broken — store write, handoff, note, marker (L44, tail writes)"
fi

echo ""
echo "-- identity: central profile (P2, Release A) --"
# Prose anchors for the central-profile contract the scripts enforce (tests/test-identity-central.py proves the code side).
CP_BLOCK=$(grep -A24 -E "\(central-profile\)" "$SKILLS_DIR/wrap-up/references/judge-rules.md")
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
if grep -qE "^\`Identity: |identity_status_line" "$SKILLS_DIR/wrap-up/references/judge-rules.md" "$SKILLS_DIR/wrap-up/SKILL.md"; then
    fail "wrap-up: the Identity status line is gone since 6.0.0 (no harvest) - judge-rules/SKILL.md must not prescribe it"
else
    pass "wrap-up: no Identity status line any more (6.0.0)"
fi
BS_CP=$(grep -A10 -E "\(central-profile-read\)" "$SKILLS_DIR/session-bootstrap/SKILL.md")
if [ -n "$BS_CP" ] && echo "$BS_CP" | grep -qF "profile-briefing.txt" && echo "$BS_CP" | grep -qiE "central profile" \
   && echo "$BS_CP" | grep -qiE "never read"; then
    pass "session-bootstrap: (central-profile-read) present — reads the projection profile-briefing.txt, never the central queue"
else
    fail "session-bootstrap: (central-profile-read) missing — with the central profile active, Step 2 reads identity/profile-briefing.txt; the central queue is never read"
fi
if grep -qE "USER CANDIDATES: |User candidate fallback gate|Identity-Scan zuletzt" "$SKILLS_DIR/session-bootstrap/SKILL.md"; then
    fail "session-bootstrap: Step 6.5(b)/(c) (user candidate gate, starvation warning) must be gone since 6.0.0 - nothing is promoted any more"
else
    pass "session-bootstrap: no user candidate gate and no starvation warning (6.0.0)"
fi
SCH="$SKILLS_DIR/wrap-up/references/wrapup-schemas.md"
if grep -qF -- "--identity-store" "$SCH" && grep -qE "\"origin\"" "$SCH" && grep -qE "PC[0-9]" "$SCH"; then
    pass "wrapup-schemas: central profile documented — --identity-store, origin, PC ids"
else
    fail "wrapup-schemas: the central profile is missing — document --identity-store, the candidate field origin and the PC ids"
fi

echo ""
echo "=== Results: $PASSED/$TESTS passed, $ERRORS failures ==="
[ "$ERRORS" -eq 0 ]

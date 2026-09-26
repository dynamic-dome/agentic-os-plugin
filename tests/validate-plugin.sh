#!/usr/bin/env bash
# Validates agentic-os plugin structure, JSON files, and schema compliance.
# Exit codes: 0 = all pass, 1 = failures found

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ERRORS=0
TESTS=0
PASSED=0

pass() { TESTS=$((TESTS + 1)); PASSED=$((PASSED + 1)); echo "  PASS: $1"; }
fail() { TESTS=$((TESTS + 1)); ERRORS=$((ERRORS + 1)); echo "  FAIL: $1"; }

check_json() {
    node -e "JSON.parse(require('fs').readFileSync(process.argv[1],'utf8'))" "$1" 2>/dev/null
}

check_json_has() {
    node -e "const d=JSON.parse(require('fs').readFileSync(process.argv[1],'utf8')); if(!(process.argv[2] in d)) process.exit(1)" "$1" "$2" 2>/dev/null
}

echo "=== Plugin Structure Validation ==="

# 1. plugin.json
echo ""
echo "-- plugin.json --"
MANIFEST="$PLUGIN_ROOT/.claude-plugin/plugin.json"
if [ -f "$MANIFEST" ]; then
    pass "plugin.json exists"
    if check_json "$MANIFEST"; then
        pass "plugin.json is valid JSON"
        for field in name version description; do
            if check_json_has "$MANIFEST" "$field"; then
                pass "plugin.json has '$field'"
            else
                fail "plugin.json missing '$field'"
            fi
        done
    else
        fail "plugin.json is not valid JSON"
    fi
else
    fail "plugin.json not found"
fi

# 2. hooks.json
echo ""
echo "-- hooks.json --"
HOOKS="$PLUGIN_ROOT/hooks/hooks.json"
if [ -f "$HOOKS" ]; then
    pass "hooks.json exists"
    if check_json "$HOOKS"; then
        pass "hooks.json is valid JSON"
    else
        fail "hooks.json is not valid JSON"
    fi
    # No prompt-type hooks (4.21.0). Measured/documented dead: SessionEnd hooks
    # cannot invoke skills or take further actions, PreCompact hook output is
    # compacted away, and a UserPromptSubmit prompt hook is one extra model call
    # per prompt whose output is discarded on approve. Only command hooks remain.
    prompt_count=$(python3 -c "
import json, sys
h = json.load(open(sys.argv[1], encoding='utf-8'))
hooks = [x for g in h['hooks'].values() for e in g for x in (e.get('hooks') or [])]
print(sum(1 for x in hooks if x.get('type') != 'command'))
" "$HOOKS" 2>/dev/null || echo "?")
    if [ "$prompt_count" = "0" ]; then
        pass "hooks.json: command hooks only (no prompt/agent hooks — they cannot invoke skills or survive compaction)"
    else
        fail "hooks.json: $prompt_count non-command hook(s) — prompt hooks were removed in 4.21.0 (dead by construction, see CHANGELOG)"
    fi
    for ev in UserPromptSubmit PreCompact SessionEnd; do
        if grep -q "\"$ev\"" "$HOOKS"; then
            fail "hooks.json: $ev hook re-added — it cannot do what its prompt promised (4.21.0)"
        else
            pass "hooks.json: no $ev hook"
        fi
    done
else
    fail "hooks.json not found"
fi

# 3. Memory JSON files
echo ""
echo "-- Memory JSON files --"
MEMORY_DIR="$PLUGIN_ROOT/.agent-memory"
if [ -d "$MEMORY_DIR" ]; then
    for jsonfile in $(find "$MEMORY_DIR" -name "*.json" 2>/dev/null); do
        bname=$(basename "$jsonfile")
        if check_json "$jsonfile"; then
            pass "$bname is valid JSON"
        else
            fail "$bname is NOT valid JSON"
        fi
    done
else
    echo "  SKIP: .agent-memory/ not found"
fi

# 4. Agent frontmatter
echo ""
echo "-- Agent frontmatter --"
for agent_file in "$PLUGIN_ROOT/agents"/*.md; do
    [ -f "$agent_file" ] || continue
    aname=$(basename "$agent_file")
    if head -1 "$agent_file" | grep -q "^---"; then
        pass "$aname has frontmatter"
        for field in name description model; do
            if grep -q "^${field}:" "$agent_file"; then
                pass "$aname has '$field'"
            else
                fail "$aname missing '$field'"
            fi
        done
        # Tool restriction key must be 'allowed_tools' (not bare 'tools')
        if grep -q "^tools:" "$agent_file"; then
            fail "$aname uses 'tools:' instead of 'allowed_tools:' — wrong frontmatter key"
        else
            pass "$aname uses correct tool key (allowed_tools or none)"
        fi
    else
        fail "$aname missing frontmatter"
    fi
done

# 4b. Reviewer agents must have plugin-specific rules
for agent_file in "$PLUGIN_ROOT/agents"/*reviewer*.md "$PLUGIN_ROOT/agents"/*gate*.md; do
    [ -f "$agent_file" ] || continue
    aname=$(basename "$agent_file")
    if grep -qi "hook\|skill.*dependenc\|plugin-specific\|tdd\|circular" "$agent_file"; then
        pass "$aname has plugin-specific review rules"
    else
        fail "$aname missing plugin-specific review rules"
    fi
done

# 5. Command frontmatter
echo ""
echo "-- Command frontmatter --"
for cmd_file in "$PLUGIN_ROOT/commands"/*.md; do
    [ -f "$cmd_file" ] || continue
    cname=$(basename "$cmd_file")
    if head -1 "$cmd_file" | grep -q "^---"; then
        pass "$cname has frontmatter"
    else
        fail "$cname missing frontmatter"
    fi
done

# 5b. Commands must have a name field in frontmatter
echo ""
echo "-- Command name field --"
for cmd_file in "$PLUGIN_ROOT/commands"/*.md; do
    [ -f "$cmd_file" ] || continue
    cname=$(basename "$cmd_file")
    FRONTMATTER=$(awk '/^---/{c++} c==1{print} c==2{exit}' "$cmd_file")
    if echo "$FRONTMATTER" | grep -q "^name:"; then
        pass "$cname has name field in frontmatter"
    else
        fail "$cname missing name field — command manifests must declare name for consistent identification"
    fi
done

# 5c. Commands must use allowed_tools (underscore), not allowed-tools (hyphen)
echo ""
echo "-- Command allowed_tools key --"
for cmd_file in "$PLUGIN_ROOT/commands"/*.md; do
    [ -f "$cmd_file" ] || continue
    cname=$(basename "$cmd_file")
    if grep -q "^allowed-tools:" "$cmd_file"; then
        fail "$cname uses 'allowed-tools:' instead of 'allowed_tools:' — wrong frontmatter key (hyphen vs underscore)"
    else
        pass "$cname uses correct allowed_tools key (underscore) or omits it"
    fi
done

# 7. All skills referenced in DEPENDENCIES.md
echo ""
echo "-- DEPENDENCIES.md completeness --"
DEPS="$PLUGIN_ROOT/skills/DEPENDENCIES.md"
if [ -f "$DEPS" ]; then
    for skill_dir in "$PLUGIN_ROOT/skills"/*/; do
        [ -d "$skill_dir" ] || continue
        sname=$(basename "$skill_dir")
        if grep -q "$sname" "$DEPS"; then
            pass "$sname documented in DEPENDENCIES.md"
        else
            fail "$sname missing from DEPENDENCIES.md"
        fi
    done
else
    echo "  SKIP: DEPENDENCIES.md not found"
fi


# 8. sync-context command has error handling guidance
echo ""
echo "-- sync-context error handling --"
SYNC_SKILL="$PLUGIN_ROOT/commands/sync-context.md"
if [ -f "$SYNC_SKILL" ]; then
    if grep -qi "error\|corrupt\|fail\|missing\|not exist\|does not exist\|fallback" "$SYNC_SKILL"; then
        pass "sync-context: has error handling guidance"
    else
        fail "sync-context: missing error handling guidance (what to do when files are corrupt or missing)"
    fi
else
    fail "sync-context: commands/sync-context.md not found"
fi

# 9. session-start.sh has file size guard before stats counting
echo ""
echo "-- session-start.sh size safety --"
SESSION_HOOK="$PLUGIN_ROOT/scripts/session-start.sh"
if [ -f "$SESSION_HOOK" ]; then
    if grep -q "wc -c\|wc -l\|file_size\|MAX_\|size_guard\|head -c\|-maxdepth\|stat " "$SESSION_HOOK"; then
        pass "session-start.sh: has file size guard"
    else
        fail "session-start.sh: missing file size guard — large files can cause timeout on SessionStart hook"
    fi
else
    fail "session-start.sh: not found"
fi

# 10. pattern-extractor skill candidate generation (skill-generator folded in, v4.0.0)
#     Must gate on confidence>=0.7 + occurrences>=3, include a uniqueness check,
#     and write to .agent-memory/generated-skills/.
echo ""
echo "-- pattern-extractor skill candidate generation --"
PE_SKILL="$PLUGIN_ROOT/skills/pattern-extractor/SKILL.md"
if [ -f "$PE_SKILL" ]; then
    if grep -qi "Skill Candidate Generation" "$PE_SKILL" \
       && grep -q "generated-skills" "$PE_SKILL" \
       && grep -qi "duplicate\|unique\|already exist\|conflict\|exists" "$PE_SKILL"; then
        pass "pattern-extractor: skill candidate generation section with uniqueness check present"
    else
        fail "pattern-extractor: missing Skill Candidate Generation section (folded-in skill-generator) with generated-skills path and uniqueness check"
    fi
else
    fail "pattern-extractor: SKILL.md not found"
fi


# 11. context-detective has concrete output template
echo ""
echo "-- context-detective output template --"
CD_AGENT="$PLUGIN_ROOT/agents/context-detective.md"
if [ -f "$CD_AGENT" ]; then
    if grep -q "# Project:" "$CD_AGENT" || grep -q "project-context.md" "$CD_AGENT" && grep -q "\`\`\`" "$CD_AGENT"; then
        pass "context-detective: has concrete output template"
    else
        fail "context-detective: missing concrete output template — agents produce inconsistent context files"
    fi
else
    fail "context-detective: agent file not found"
fi

# 12. Dead loop agents stay deleted (removed 4.15.0)
#     self-improve v4.0 runs all phases inline ("no external skill delegation") —
#     improvement-agent and research-agent were never spawned anymore, but their
#     descriptions still loaded into every session's agent list. Contract: the
#     files must NOT exist; context-detective is the only remaining agent.
echo ""
echo "-- dead loop agents removed (self-improve runs inline) --"
for DEAD_AGENT in improvement-agent research-agent; do
    if [ -f "$PLUGIN_ROOT/agents/$DEAD_AGENT.md" ]; then
        fail "agents/$DEAD_AGENT.md exists but self-improve runs inline — dead agent burns context in every session; delete it (and re-check scripts/model-routing.sh list-agents)"
    else
        pass "agents/$DEAD_AGENT.md: stays deleted (self-improve is inline-only)"
    fi
done


# 18. Agents that write files must declare Write in allowed_tools
echo ""
echo "-- agent write-tool consistency --"
for agent_file in "$PLUGIN_ROOT/agents"/*.md; do
    [ -f "$agent_file" ] || continue
    aname=$(basename "$agent_file")
    # Check if agent body instructs writing to files
    if grep -qiE "^Write |^Append |write.*\.agent-memory|\.agent-memory.*write|write.*project-context|write.*quality/" "$agent_file"; then
        if grep -A20 "^allowed_tools:" "$agent_file" | grep -q "Write"; then
            pass "$aname: declares Write tool (agent writes files)"
        else
            fail "$aname: writes files but 'Write' missing from allowed_tools — tool may be blocked at runtime"
        fi
    fi
done


# 19. SubagentStop prompt hook must not instruct LLM to run bash commands
#     Prompt hooks run in LLM context without bash execution — instructing LLM
#     to "run git status" leads to hallucinated results; use agent context instead
echo ""
echo "-- SubagentStop hook no-bash-in-prompt --"
HOOKS_FILE="$PLUGIN_ROOT/hooks/hooks.json"
if [ -f "$HOOKS_FILE" ]; then
    # Check if SubagentStop section contains misleading bash-run instructions
    # Use grep directly on the JSON file since the SubagentStop prompt is on one line
    if grep -A10 '"SubagentStop"' "$HOOKS_FILE" | grep -qiE "via \`git |run \`git |execute.*git |via git status|run git "; then
        fail "SubagentStop: prompt hook instructs LLM to run bash commands (e.g. 'via \`git status\`') — prompt hooks have no bash access, leads to hallucinated results"
    else
        pass "SubagentStop: prompt hook does not instruct LLM to run bash commands directly"
    fi
else
    fail "hooks.json not found"
fi


# 30. pattern-extractor skill-candidate template must include 'name:' in generated frontmatter.
#     (skill-generator folded into pattern-extractor in v4.0.0.) Beyond pattern-extractor's own
#     'name: pattern-extractor' frontmatter line, the generated-skill template must carry a
#     second 'name:' placeholder line (e.g. 'name: <skill-name>').
echo ""
echo "-- pattern-extractor skill-candidate template name field --"
PE_SKILL="$PLUGIN_ROOT/skills/pattern-extractor/SKILL.md"
if [ -f "$PE_SKILL" ]; then
    NAME_COUNT=$(grep -c "^[[:space:]]*name:" "$PE_SKILL")
    if [ "$NAME_COUNT" -ge 2 ]; then
        pass "pattern-extractor: generated SKILL.md template includes 'name:' in frontmatter"
    else
        fail "pattern-extractor: generated SKILL.md template missing 'name:' in frontmatter — generated skills won't be identifiable by registry or pass skill validation"
    fi
else
    fail "pattern-extractor: SKILL.md not found"
fi


# 33. init command must not reference notebooklm:navigate (nonexistent tool — should use notebooklm:chat)
echo ""
echo "-- init command notebooklm workflow consistency --"
INIT_CMD="$PLUGIN_ROOT/commands/init.md"
if [ -f "$INIT_CMD" ]; then
    if grep -q "notebooklm:navigate" "$INIT_CMD"; then
        fail "init: references 'notebooklm:navigate' which does not exist — the generated CLAUDE.md workflow will be broken; use 'notebooklm:chat' consistently"
    else
        pass "init: notebooklm workflow references valid commands only (no notebooklm:navigate)"
    fi
else
    fail "init: command file not found"
fi


# 34. session-bootstrap and init must not reference notebooklm:chat as a callable command
echo ""
echo "-- notebooklm:chat phantom command reference --"
SB_SKILL="$PLUGIN_ROOT/skills/session-bootstrap/SKILL.md"
INIT_CMD2="$PLUGIN_ROOT/commands/init.md"
NB_FAIL=0
if [ -f "$SB_SKILL" ]; then
    if grep -q "notebooklm:chat" "$SB_SKILL"; then
        fail "session-bootstrap: references 'notebooklm:chat' which is not a skill in this plugin — phantom command confuses agents"
        NB_FAIL=1
    fi
fi
if [ -f "$INIT_CMD2" ]; then
    if grep -q "notebooklm:chat" "$INIT_CMD2"; then
        fail "init: references 'notebooklm:chat' which is not a skill in this plugin — phantom command in generated CLAUDE.md will mislead agents"
        NB_FAIL=1
    fi
fi
if [ "$NB_FAIL" -eq 0 ]; then
    pass "session-bootstrap and init: no phantom notebooklm:chat command references"
fi


# 35. sync-context command must not reference AskUserQuestion (nonexistent Claude Code tool)
#     AskUserQuestion is not a real tool in Claude Code. When the skill instructs the agent
#     to "Use AskUserQuestion", execution fails or the agent hallucinates the tool.
#     The correct approach is to output a question as plain text and wait for user response.
echo ""
echo "-- sync-context no phantom AskUserQuestion tool --"
SYNC_SKILL="$PLUGIN_ROOT/commands/sync-context.md"
if [ -f "$SYNC_SKILL" ]; then
    if grep -q "AskUserQuestion" "$SYNC_SKILL"; then
        fail "sync-context: references 'AskUserQuestion' which is not a real Claude Code tool — replace with plain text question output"
    else
        pass "sync-context: does not reference nonexistent AskUserQuestion tool"
    fi
else
    fail "sync-context: commands/sync-context.md not found"
fi



# 36. skill-generator removed in v4.0.0 (folded into pattern-extractor with a
#     minimal template: name/description/type) — user_invocable template test removed



# 37. wrap-up skill must not claim "Stop hook triggers this automatically"
#     The hooks.json Stop hook only does lightweight iteration logging — it does NOT
#     trigger the wrap-up skill. Claiming it does misleads users into thinking wrap-up
#     runs automatically when it is actually manual-only.
echo ""
echo "-- wrap-up hook trigger accuracy --"
WU_SKILL="$PLUGIN_ROOT/skills/wrap-up/SKILL.md"
if [ -f "$WU_SKILL" ]; then
    if grep -qiE "Stop hook triggers this automatically|Stop hook.*triggers.*wrap.?up|automatically.*Stop hook" "$WU_SKILL"; then
        fail "wrap-up: claims 'Stop hook triggers this automatically' but hooks.json Stop hook only does lightweight logging — this misleads users; wrap-up is manual-only"
    else
        pass "wrap-up: does not claim false automatic Stop hook trigger"
    fi
else
    fail "wrap-up: SKILL.md not found"
fi


# 38. auto-init must create knowledge/notebook-registry.md
#     session-bootstrap health check verifies knowledge/notebook-registry.md exists.
#     If auto-init skips this file, bootstrap always warns about a missing file after
#     a fresh auto-init, producing spurious health alerts.
#     FUNCTIONAL check (not grep): the file may be created by the hook inline OR by the
#     sourced mem-schema.sh — we verify it actually appears, regardless of where defined.
echo ""
echo "-- auto-init creates knowledge/notebook-registry.md (functional) --"
SESSION_HOOK="$PLUGIN_ROOT/scripts/session-start.sh"
if [ -f "$SESSION_HOOK" ]; then
    NR_TMP=$(mktemp -d)
    CLAUDE_PROJECT_DIR="$NR_TMP" bash "$SESSION_HOOK" > "$NR_TMP/out.json" 2>/dev/null
    NR_EXIT=$?
    if [ "$NR_EXIT" -eq 0 ] && [ -f "$NR_TMP/.agent-memory/knowledge/notebook-registry.md" ]; then
        pass "auto-init creates knowledge/notebook-registry.md (hook exit 0)"
    else
        fail "auto-init missing knowledge/notebook-registry.md or hook exited non-zero ($NR_EXIT) — session-bootstrap health check will warn after auto-init"
    fi
    rm -rf "$NR_TMP"
else
    fail "session-start.sh: not found"
fi



# 39. status command must reference full subdirectory paths for memory files
#     status.md tells agents to count entries in bare filenames like "patterns.json",
#     "errors.json", "decisions.json", "iteration-log.md" — without the subdirectory
#     prefix. The actual paths are patterns/patterns.json, iterations/errors.json,
#     context/decisions.json, iterations/iteration-log.md. An agent following the
#     bare-name instructions will look in the wrong location and report 0 entries.
echo ""
echo "-- status command memory file paths --"
STATUS_CMD="$PLUGIN_ROOT/commands/status.md"
if [ -f "$STATUS_CMD" ]; then
    # The statistics section should reference subdirectory paths, not bare filenames
    if grep -qE "iterations/iteration-log|patterns/patterns\.json|iterations/errors\.json|context/decisions\.json" "$STATUS_CMD"; then
        pass "status: memory file paths include subdirectory prefixes (agents can locate files correctly)"
    else
        fail "status: statistics section references bare filenames (e.g. 'patterns.json') without subdirectory paths — agents will look in wrong location and always report 0 entries"
    fi
else
    fail "status: command file not found"
fi



# 40. plugin.json skill count must match actual number of skill directories
#     plugin.json description claims a specific number of skills. If skills are
#     added without updating the description, the count becomes stale and misleads
#     users about the plugin's capabilities.
echo ""
echo "-- plugin.json skill count accuracy --"
MANIFEST="$PLUGIN_ROOT/.claude-plugin/plugin.json"
if [ -f "$MANIFEST" ]; then
    ACTUAL_SKILL_COUNT=$(ls -d "$PLUGIN_ROOT/skills"/*/  2>/dev/null | wc -l | tr -d '[:space:]')
    # Extract the number from description (e.g. "10 skills" -> 10)
    CLAIMED_COUNT=$(grep -o '[0-9]\+ skills' "$MANIFEST" | grep -o '[0-9]\+' | head -1)
    if [ -z "$CLAIMED_COUNT" ]; then
        echo "  SKIP: plugin.json description does not mention a skill count"
    elif [ "$CLAIMED_COUNT" -eq "$ACTUAL_SKILL_COUNT" ]; then
        pass "plugin.json: skill count in description ($CLAIMED_COUNT) matches actual skill directories ($ACTUAL_SKILL_COUNT)"
    else
        fail "plugin.json: skill count in description ($CLAIMED_COUNT) does not match actual directories ($ACTUAL_SKILL_COUNT) — update description to reflect current skill count"
    fi
else
    fail "plugin.json not found"
fi


# 41b. DEPENDENCIES.md inter-skill-call accuracy (Design Principle 4).
#      Principle 4 names the skills that invoke OTHER skills. If a skill's SKILL.md
#      contains a real cross-skill invocation (`invoke ... <other-skill>`) but is NOT
#      listed as an invoker in DEPENDENCIES.md (or vice versa), the graph has drifted.
#      Catches the v3.2.4 finding: wrap-up invokes obsidian-sync (Step 7.5) but the
#      old graph omitted it, and Principle 4 wrongly claimed only wrap-up + self-improve call others.
echo ""
echo "-- DEPENDENCIES.md inter-skill-call accuracy (Principle 4) --"
if [ -f "$DEPS" ]; then
    OTHER_SKILLS="pattern-extractor obsidian-sync context-keeper"
    CALL_DRIFT=""
    for skill_dir in "$PLUGIN_ROOT/skills"/*/; do
        [ -d "$skill_dir" ] || continue
        sname=$(basename "$skill_dir")
        SFILE="$skill_dir/SKILL.md"
        [ -f "$SFILE" ] || continue
        # Does this skill REALLY invoke another skill? (an "invoke ... <other>" line,
        # excluding self-references and the depends-on metadata block)
        REAL_CALL=false
        for other in $OTHER_SKILLS; do
            [ "$other" = "$sname" ] && continue
            if grep -iE "(invoke|trigger|call)[^.]*\`?$other\`?" "$SFILE" \
                 | grep -ivE "depends-on|owned by|no longer|does NOT|not replicate|instead of" >/dev/null 2>&1; then
                REAL_CALL=true
                break
            fi
        done
        # Is this skill listed as an invoker in DEPENDENCIES.md Principle 4 line?
        LISTED=false
        grep -E "^4\. \*\*Skills that invoke" "$DEPS" | grep -q "\`$sname\`" && LISTED=true
        if [ "$REAL_CALL" = true ] && [ "$LISTED" = false ]; then
            CALL_DRIFT="$CALL_DRIFT $sname(calls-but-unlisted)"
        fi
    done
    if [ -z "$CALL_DRIFT" ]; then
        pass "DEPENDENCIES.md Principle 4 lists every skill that invokes another skill"
    else
        fail "DEPENDENCIES.md Principle 4 drift — these skills invoke others but aren't listed as invokers:$CALL_DRIFT"
    fi
else
    fail "DEPENDENCIES.md not found"
fi


# 42. status command must show code-reviews count in statistics
#     The status command tracks iterations, patterns, errors, and decisions.
#     code-reviews.json is a key quality metric — omitting it leaves users blind
#     to how many reviews have been logged. All four tracked quality assets should
#     appear together for a complete health picture.
echo ""
echo "-- status command code-reviews count --"
STATUS_CMD="$PLUGIN_ROOT/commands/status.md"
if [ -f "$STATUS_CMD" ]; then
    if grep -qi "code.review\|reviews" "$STATUS_CMD"; then
        pass "status: includes code-reviews count in statistics"
    else
        fail "status: missing code-reviews count — statistics section tracks iterations/patterns/errors/decisions but omits code-reviews.json, leaving review history invisible to the user"
    fi
else
    fail "status.md not found"
fi


# 43. quality-gate skill removed in v4.0.0 — test removed



# 44. init command must use English default content for initialized Markdown files
#     The plugin uses English for all technical content (skills, agents, commands).
#     init.md initializes iteration-log.md, patterns.md, and session-summary.md with
#     German placeholder strings ("Noch keine Eintraege", "Pattern-Katalog", etc.).
#     This is inconsistent with the plugin's language convention and was fixed for
#     test-validator, code-reviewer, and wrap-up in prior iterations.
echo ""
echo "-- init command markdown defaults use English --"
INIT_CMD="$PLUGIN_ROOT/commands/init.md"
if [ -f "$INIT_CMD" ]; then
    if grep -q "Noch keine\|Pattern-Katalog\|Letzte Session\|Erste Session\|Naechste Schritte" "$INIT_CMD"; then
        fail "init: default Markdown file content uses German strings (e.g. 'Noch keine Eintraege', 'Pattern-Katalog') — should use English to match plugin language convention"
    else
        pass "init: default Markdown file content uses English (no German placeholder strings)"
    fi
else
    fail "init.md not found"
fi



# 45. context-detective agent example response must use English, not German
#     context-detective.md has an example where the assistant says
#     "Ich analysiere das Repository..." (German). All skill examples and agent
#     content should be in English for consistency with the plugin's language convention.
echo ""
echo "-- context-detective agent example uses English --"
CTX_DET="$PLUGIN_ROOT/agents/context-detective.md"
if [ -f "$CTX_DET" ]; then
    if grep -q "Ich analysiere\|Ich überprüfe\|Ich starte\|Das Repository" "$CTX_DET"; then
        fail "context-detective: example assistant response uses German (e.g. 'Ich analysiere das Repository...') — should use English"
    else
        pass "context-detective: example assistant response uses English (no German phrases)"
    fi
else
    fail "context-detective.md not found"
fi


# 46. hooks.json SessionEnd prompt must use English, not German
#     SessionEnd hook prompt uses German section headers: "Was wurde gemacht",
#     "Offene Punkte", "Naechste Schritte". All hook prompts should be in English.
echo ""
echo "-- hooks.json SessionEnd prompt language consistency --"
HOOKS_FILE="$PLUGIN_ROOT/hooks/hooks.json"
if [ -f "$HOOKS_FILE" ]; then
    if grep -A20 '"SessionEnd"' "$HOOKS_FILE" | grep -q "Was wurde gemacht\|Offene Punkte\|Naechste Schritte"; then
        fail "hooks.json SessionEnd: prompt uses German headers (e.g. 'Was wurde gemacht', 'Offene Punkte', 'Naechste Schritte') — should use English"
    else
        pass "hooks.json SessionEnd: prompt uses English (no German headers)"
    fi
else
    fail "hooks.json not found"
fi

# 47. hooks.json SubagentStop prompt must use English, not German
#     SubagentStop hook prompts user with German question: "Soll ich diese
#     Aenderungen committen?" — should be English for plugin language consistency.
echo ""
echo "-- hooks.json SubagentStop prompt language consistency --"
if [ -f "$HOOKS_FILE" ]; then
    if grep -A10 '"SubagentStop"' "$HOOKS_FILE" | grep -q "Soll ich\|Aenderungen\|committen\|Vorgeschlagene"; then
        fail "hooks.json SubagentStop: prompt uses German dialog (e.g. 'Soll ich diese Aenderungen committen?') — should use English"
    else
        pass "hooks.json SubagentStop: prompt uses English (no German dialog)"
    fi
else
    fail "hooks.json not found"
fi


# 48. init command notebook-registry.md template must use English
#     commands/init.md initializes knowledge/notebook-registry.md with a German
#     template ("Zentrales Register", "Aktive Notebooks", "Stichwörter", etc.).
#     session-start.sh already uses English for the same file. When users run
#     /init manually, they get German content — inconsistent with the plugin's
#     English language convention applied everywhere else.
echo ""
echo "-- init command notebook-registry.md template language consistency --"
INIT_CMD="$PLUGIN_ROOT/commands/init.md"
if [ -f "$INIT_CMD" ]; then
    if grep -q "Zentrales Register\|Aktive Notebooks\|Stichwörter\|aktualisieren\|Wann NotebookLM\|Thema:\|Staerken:" "$INIT_CMD"; then
        fail "init: notebook-registry.md template contains German strings — must use English for language consistency (cf. session-start.sh uses English)"
    else
        pass "init: notebook-registry.md template uses English (no German placeholder strings)"
    fi
else
    fail "init: commands/init.md not found"
fi


# 49. init command soul.md template must not default to German language
#     commands/init.md creates soul.md with "Language: de" as the default,
#     which forces every new project to start in German. All other plugin
#     content uses English. The default should be "en" to match the plugin's
#     English-first convention; users can change it to "de" if desired.
echo ""
echo "-- init command soul.md language default consistency --"
INIT_CMD="$PLUGIN_ROOT/commands/init.md"
if [ -f "$INIT_CMD" ]; then
    if grep -q "Language: de" "$INIT_CMD"; then
        fail "init: soul.md template defaults to 'Language: de' (German) — inconsistent with the plugin's English-first convention; default should be 'en'"
    else
        pass "init: soul.md template language default is English (no 'Language: de')"
    fi
else
    fail "init: commands/init.md not found"
fi


# 50. iteration-logger and test-validator must not reference phantom plugin settings
#     Both skills say their log-rotation thresholds are "configurable via plugin settings"
#     (max_iterations_log_entries, max_error_log_entries, max_test_result_entries), but
#     no such configuration file or mechanism exists in the plugin. Agents following these
#     instructions would search for a non-existent config, causing confusion. The thresholds
#     should be stated as hardcoded values.
echo ""
echo "-- log phantom plugin settings reference --"
IL_FILE="$PLUGIN_ROOT/commands/log.md"
if [ -f "$IL_FILE" ]; then
    if grep -q "configurable via plugin setting" "$IL_FILE"; then
        fail "log: references 'configurable via plugin settings' (max_iterations_log_entries, max_error_log_entries) but no such plugin config exists — agents will look for a non-existent mechanism; remove phantom setting references"
    else
        pass "log: does not reference phantom plugin settings (log rotation thresholds are hardcoded)"
    fi
else
    fail "log: commands/log.md not found"
fi

# test-validator merged into quality-gate — test removed in v3 consolidation


# 51. session-start.sh auto-init must use English for all initialized markdown file content
#     session-start.sh creates iteration-log.md, patterns.md, learnings.md, and
#     session-summary.md during auto-init. The current content uses German strings
#     ("Noch keine Eintraege", "Pattern-Katalog", "Erste Session — frisch initialisiert",
#     "Naechste Schritte"). The init.md command was fixed (iteration 38) to use English
#     for the same files, but session-start.sh was never updated. This means auto-init
#     (SessionStart hook) still creates German content, while /init creates English content —
#     a language inconsistency depending on how the memory system is initialized.
echo ""
echo "-- session-start.sh auto-init markdown content uses English --"
SS_HOOK="$PLUGIN_ROOT/scripts/session-start.sh"
if [ -f "$SS_HOOK" ]; then
    if grep -q "Noch keine Eintraege\|Pattern-Katalog\|Noch keine Patterns\|Noch keine Session-Learnings\|Erste Session.*frisch initialisiert\|frisch initialisiert\|Language: de" "$SS_HOOK"; then
        fail "session-start.sh: auto-init creates markdown files with German content (e.g. 'Noch keine Eintraege', 'Pattern-Katalog', 'Language: de') — inconsistent with /init command which uses English; fix to match init.md"
    else
        pass "session-start.sh: auto-init markdown file content uses English (language-consistent with /init command)"
    fi
else
    fail "session-start.sh: not found"
fi



# 52. session-start.sh runtime context messages must use English, not German
#     session-start.sh injects context into Claude's system prompt at session start.
#     Several runtime messages are in German: the INIT_MSG variable ("Memory-System
#     initialisiert fuer..."), quality warning ("WARNUNG: Quality Scores declining!"),
#     error hint ("Hinweis: Viele Fehler — Pattern-Extract empfohlen."), briefing section
#     extractors for "Naechste Schritte" / "Offene Punkte" / "Aktive Warnungen", and the
#     main briefing instruction to Claude ("Bei deiner ERSTEN Antwort in dieser Session...").
#     These German strings directly instruct Claude in German, causing inconsistent
#     behavior vs. the plugin's English-first convention established everywhere else.
echo ""
echo "-- session-start.sh runtime context messages use English --"
SS_HOOK="$PLUGIN_ROOT/scripts/session-start.sh"
if [ -f "$SS_HOOK" ]; then
    if grep -q "Memory-System initialisiert\|Bei deiner ERSTEN Antwort\|WARNUNG:.*declining\|Hinweis: Viele Fehler\|Naechste Schritte\|Offene Punkte\|Aktive Warnungen\|Neu initialisiert\|Iterationen.*Fehler.*Patterns" "$SS_HOOK"; then
        fail "session-start.sh: runtime context messages injected into Claude's session use German (e.g. 'Bei deiner ERSTEN Antwort', 'Memory-System initialisiert', 'Naechste Schritte') — these directly instruct Claude in German; must use English for consistency"
    else
        pass "session-start.sh: runtime context messages use English (no German in Claude-facing strings)"
    fi
else
    fail "session-start.sh: not found"
fi


# 53. marketplace.json skill count must match actual number of skill directories
#     marketplace.json is the public-facing listing for the Claude Code marketplace.
#     If marketplace.json description says "10 skills" but there are 11 skill directories,
#     potential users browsing the marketplace get an incorrect picture of plugin capabilities.
#     The plugin.json was fixed in iteration 32 but marketplace.json was overlooked.
echo ""
echo "-- marketplace.json skill count accuracy --"
MKTPLACE="$PLUGIN_ROOT/.claude-plugin/marketplace.json"
if [ -f "$MKTPLACE" ]; then
    ACTUAL_SKILL_COUNT=$(ls -d "$PLUGIN_ROOT/skills"/*/  2>/dev/null | wc -l | tr -d '[:space:]')
    # Extract the number from description (e.g. "10 skills" -> 10)
    CLAIMED_COUNT=$(grep -o '[0-9]\+ skills' "$MKTPLACE" | grep -o '[0-9]\+' | head -1)
    if [ -z "$CLAIMED_COUNT" ]; then
        echo "  SKIP: marketplace.json description does not mention a skill count"
    elif [ "$CLAIMED_COUNT" -eq "$ACTUAL_SKILL_COUNT" ]; then
        pass "marketplace.json: skill count in description ($CLAIMED_COUNT) matches actual skill directories ($ACTUAL_SKILL_COUNT)"
    else
        fail "marketplace.json: skill count in description ($CLAIMED_COUNT) does not match actual skill directories ($ACTUAL_SKILL_COUNT) — update description to reflect current skill count"
    fi
else
    echo "  SKIP: .claude-plugin/marketplace.json not found"
fi



# 54. quality-gate skill removed in v4.0.0 — test removed



# 55. pre-compact.sh must use English for all Claude-facing strings
#     pre-compact.sh injects a context restoration message into Claude's system
#     prompt before context compression. The current script uses German strings:
#     "KONTEXT-WIEDERHERSTELLUNG", "Session-Kontext", "Kontext wurde komprimiert.
#     Bei Bedarf relevante Dateien neu lesen." and the fallback message
#     "Kontext wiederhergestellt." — these directly instruct Claude in German,
#     which is inconsistent with the plugin's English-first convention.
echo ""
echo "-- pre-compact.sh body language consistency --"
PC_HOOK="$PLUGIN_ROOT/scripts/pre-compact.sh"
if [ -f "$PC_HOOK" ]; then
    if grep -q "KONTEXT-WIEDERHERSTELLUNG\|Session-Kontext\|Kontext wurde komprimiert\|Bei Bedarf relevante Dateien\|Kontext wiederhergestellt\|Re-injected.*Kontext\|BEVOR die Context-Komprimierung\|Weist Claude an" "$PC_HOOK"; then
        fail "pre-compact.sh: contains German strings visible to Claude (e.g. 'KONTEXT-WIEDERHERSTELLUNG', 'Kontext wurde komprimiert') — must use English for language consistency"
    else
        pass "pre-compact.sh: body uses English (no German strings in Claude-facing content)"
    fi
else
    echo "  SKIP: scripts/pre-compact.sh not found"
fi


# 56. session-end.sh must use English for all Claude-facing strings
#     session-end.sh injects a wrap-up instruction into Claude's systemMessage
#     at session end. The current script uses German: "Session wird beendet.
#     Führe jetzt das Wrap-Up durch:", "Aktualisiere session-summary.md",
#     "Logge alle ungeloggten Iterationen", "Extrahiere Learnings",
#     "Führe Pattern-Extract aus", "Session beendet. Bitte wrap-up durchführen."
#     These German instructions directly tell Claude what to do in German,
#     which is inconsistent with the plugin's English-first convention.
echo ""
echo "-- session-end.sh body language consistency --"
SE_HOOK="$PLUGIN_ROOT/scripts/session-end.sh"
if [ -f "$SE_HOOK" ]; then
    if grep -q "Session wird beendet\|Führe jetzt\|Aktualisiere session-summary\|Logge alle ungeloggten\|Extrahiere Learnings\|Führe Pattern-Extract\|Session beendet.*wrap-up durchführen\|Weist Claude an.*wrap-up\|Kein .agent-memory\|Statistiken sammeln\|systemMessage bauen\|JSON-safe escapen\|beendet\. Bitte\|Naechste Schritte\|offene Punkte\|nächste Schritte\|was wurde gemacht" "$SE_HOOK"; then
        fail "session-end.sh: contains German strings in Claude-facing systemMessage (e.g. 'Session wird beendet', 'Führe jetzt das Wrap-Up durch') — must use English for language consistency"
    else
        pass "session-end.sh: body uses English (no German strings in Claude-facing content)"
    fi
else
    echo "  SKIP: scripts/session-end.sh not found"
fi



# 57. schedule-manager must reference agentic-os, not self-improve-loop (deprecated plugin)
echo ""
echo "-- schedule-manager plugin reference --"
SM_SKILL="$PLUGIN_ROOT/skills/schedule-manager/SKILL.md"
if [ -f "$SM_SKILL" ]; then
    # Only check frontmatter metadata (part-of and depends-on), not body text like task IDs
    FRONTMATTER=$(awk '/^---/{c++} c==1{print} c==2{exit}' "$SM_SKILL")
    if echo "$FRONTMATTER" | grep -q "self-improve-loop"; then
        fail "schedule-manager: frontmatter references deprecated 'self-improve-loop' plugin — must use 'agentic-os'"
    else
        pass "schedule-manager: frontmatter correctly references 'agentic-os' (no stale self-improve-loop refs)"
    fi
else
    echo "  SKIP: skills/schedule-manager/SKILL.md not found"
fi


# 58. meta-improve body must not reference deprecated 'self-improve-loop' plugin name
echo ""
echo "-- meta-improve body reference --"
MI_SKILL="$PLUGIN_ROOT/skills/meta-improve/SKILL.md"
if [ -f "$MI_SKILL" ]; then
    # Extract body (after second ---)
    BODY=$(awk 'BEGIN{c=0} /^---/{c++; next} c>=2{print}' "$MI_SKILL")
    if echo "$BODY" | grep -q "self-improve-loop plugin"; then
        fail "meta-improve: body references 'self-improve-loop plugin' — should say 'agentic-os plugin'"
    else
        pass "meta-improve: body correctly references agentic-os plugin (no stale self-improve-loop)"
    fi
fi

# 59. SubagentStop matcher must reference improvement-agent (not deprecated improvement-scout)
echo ""
echo "-- SubagentStop matcher references active agent --"
if grep -q '"matcher":' "$PLUGIN_ROOT/hooks/hooks.json"; then
    SUBAGENT_MATCHER=$(grep '"matcher":' "$PLUGIN_ROOT/hooks/hooks.json" | tail -1)
    if echo "$SUBAGENT_MATCHER" | grep -q "improvement-scout"; then
        fail "SubagentStop: matcher references deprecated improvement-scout — should be improvement-agent"
    else
        pass "SubagentStop: matcher references active agents (no deprecated improvement-scout)"
    fi
fi

# 64. session-start.sh must produce valid JSON output
echo ""
echo "-- session-start.sh JSON output validity --"
SS_SCRIPT="$PLUGIN_ROOT/scripts/session-start.sh"
if [ -f "$SS_SCRIPT" ]; then
    # Run in a temp dir to test auto-init path
    TMPDIR_TEST=$(mktemp -d)
    SS_OUTPUT=$(CLAUDE_PROJECT_DIR="$TMPDIR_TEST" bash "$SS_SCRIPT" 2>/dev/null || true)
    if echo "$SS_OUTPUT" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
        pass "session-start.sh: produces valid JSON output"
    else
        fail "session-start.sh: output is not valid JSON"
    fi
    # The briefing must travel in hookSpecificOutput.additionalContext (model-visible);
    # a top-level systemMessage is user-only (measured 2.1.263, 4.21.0).
    HAS_MSG=$(echo "$SS_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); h=d.get('hookSpecificOutput') or {}; print('yes' if h.get('hookEventName')=='SessionStart' and h.get('additionalContext') and 'systemMessage' not in d else 'no')" 2>/dev/null || echo "no")
    if [ "$HAS_MSG" = "yes" ]; then
        pass "session-start.sh: JSON output carries hookSpecificOutput.additionalContext (no systemMessage)"
    else
        fail "session-start.sh: JSON output must carry hookSpecificOutput.additionalContext and no top-level systemMessage (the model never sees systemMessage)"
    fi
    rm -rf "$TMPDIR_TEST"
fi

# 65. session-start.sh auto-init creates required directory structure
echo ""
echo "-- session-start.sh auto-init directory structure --"
if [ -f "$SS_SCRIPT" ]; then
    TMPDIR_TEST=$(mktemp -d)
    CLAUDE_PROJECT_DIR="$TMPDIR_TEST" bash "$SS_SCRIPT" > /dev/null 2>&1 || true
    MISSING=""
    for subdir in identity context iterations patterns quality learnings generated-skills knowledge; do
        [ ! -d "$TMPDIR_TEST/.agent-memory/$subdir" ] && MISSING="$MISSING $subdir"
    done
    if [ -z "$MISSING" ]; then
        pass "session-start.sh: auto-init creates all required subdirectories"
    else
        fail "session-start.sh: auto-init missing directories:$MISSING"
    fi
    # Check key files created
    FILES_OK=true
    for keyfile in session-summary.md identity/soul.md context/project-context.md iterations/errors.json patterns/patterns.json quality/quality-score.json knowledge/notebook-registry.md; do
        [ ! -f "$TMPDIR_TEST/.agent-memory/$keyfile" ] && FILES_OK=false
    done
    if [ "$FILES_OK" = true ]; then
        pass "session-start.sh: auto-init creates all required seed files"
    else
        fail "session-start.sh: auto-init missing seed files"
    fi
    rm -rf "$TMPDIR_TEST"
fi

# 66. session-start.sh existing memory dir does not re-init
echo ""
echo "-- session-start.sh no re-init on existing memory --"
if [ -f "$SS_SCRIPT" ]; then
    TMPDIR_TEST=$(mktemp -d)
    mkdir -p "$TMPDIR_TEST/.agent-memory"
    echo "# Existing summary" > "$TMPDIR_TEST/.agent-memory/session-summary.md"
    CLAUDE_PROJECT_DIR="$TMPDIR_TEST" bash "$SS_SCRIPT" > /dev/null 2>&1 || true
    CONTENT=$(cat "$TMPDIR_TEST/.agent-memory/session-summary.md" 2>/dev/null)
    if echo "$CONTENT" | grep -q "Existing summary"; then
        pass "session-start.sh: does not overwrite existing .agent-memory/ files"
    else
        fail "session-start.sh: overwrites existing .agent-memory/ files on re-run"
    fi
    rm -rf "$TMPDIR_TEST"
fi

# 67. SessionStart briefing reaches the MODEL (4.21.0)
#     A SessionStart hook's top-level "systemMessage" is shown to the user only; the
#     transcript records it as hook_system_message and the model context never
#     contains it (measured 2026-09-08, Claude Code 2.1.263). The briefing must ship
#     as hookSpecificOutput.additionalContext. The former SessionEnd prompt hook
#     ("delegate to wrap-up", "wiki verify") was removed: SessionEnd hooks cannot
#     invoke skills; the RECOVERY line at the next SessionStart is the backstop.
echo ""
echo "-- SessionStart briefing contract (additionalContext) --"
SS_HOOK="$PLUGIN_ROOT/scripts/session-start.sh"
if [ -f "$SS_HOOK" ]; then
    if grep -q "hookSpecificOutput" "$SS_HOOK" && grep -q "additionalContext" "$SS_HOOK"; then
        pass "session-start.sh: emits hookSpecificOutput.additionalContext (model-visible)"
    else
        fail "session-start.sh: must emit hookSpecificOutput.additionalContext — systemMessage is user-only"
    fi
    if grep -qE '^[[:space:]]*"systemMessage"' "$SS_HOOK"; then
        fail "session-start.sh: still emits a top-level systemMessage (invisible to the model)"
    else
        pass "session-start.sh: no top-level systemMessage"
    fi
    if grep -q "/agentic-os:wrap-up" "$SS_HOOK"; then
        pass "session-start.sh: briefing names the slash path /agentic-os:wrap-up (applies model: frontmatter; the Skill tool does not)"
    else
        fail "session-start.sh: briefing must name /agentic-os:wrap-up — the slash path is the only one where the skill's model class applies (measured 2.1.263)"
    fi
else
    fail "session-start.sh: not found"
fi

# 68. Dead script cleanup — session-end.sh and pre-compact.sh should not exist
echo ""
echo "-- Dead hook script cleanup --"
if [ -f "$PLUGIN_ROOT/scripts/session-end.sh" ]; then
    fail "scripts/session-end.sh exists but is not referenced in hooks.json (dead code)"
else
    pass "scripts/session-end.sh: removed (was dead code — prompt hook handles SessionEnd)"
fi
if [ -f "$PLUGIN_ROOT/scripts/pre-compact.sh" ]; then
    fail "scripts/pre-compact.sh exists but is not referenced in hooks.json (dead code)"
else
    pass "scripts/pre-compact.sh: removed (was dead code — prompt hook handles PreCompact)"
fi

# 76. wrap-up version must be 3.0 (consistent with all other skills)
#     wrap-up was the only skill still carrying version '2.0' from the v2 era.
#     All other skills are at '3.0' — stale version causes confusion about
#     when the skill was last updated relative to the rest of the plugin.
echo ""
echo "-- wrap-up version consistency --"
WU_VER_SKILL="$PLUGIN_ROOT/skills/wrap-up/SKILL.md"
if [ -f "$WU_VER_SKILL" ]; then
    WU_FM=$(awk 'BEGIN{c=0} /^---/{c++; next} c==1{print}' "$WU_VER_SKILL")
    if echo "$WU_FM" | grep -qE "version:.*['\"]?2\.0"; then
        fail "wrap-up: version is 2.0 — should be 3.0 (all other skills are at version 3.0; stale version number misleads about update history)"
    else
        pass "wrap-up: version is consistent with other skills (not stale 2.0)"
    fi
fi

# 77. Invocation contract (migrated 2026-07-24)
#     user_invocable is NOT a Claude Code frontmatter field (the real fields are
#     user-invocable / disable-model-invocation, docs: code.claude.com/docs/en/skills)
#     — it was silently ignored by the harness. Contract now:
#     (a) no skill or command may declare the dead user_invocable field;
#     (b) manual-only behaviour is structural since v5.0.0 (sync-context is a command); skills invoked by wrap-up must NOT declare
#         disable-model-invocation: true — keeps their descriptions out of context
#         and blocks autonomous invocation mechanically instead of via prose;
#     (c) skills invoked by other skills via the Skill tool MUST stay
#         model-invocable (disable-model-invocation blocks Skill-tool access).
echo ""
echo "-- invocation contract (no dead user_invocable; manual-only skills disabled) --"
for SKILL_FILE in "$PLUGIN_ROOT"/skills/*/SKILL.md "$PLUGIN_ROOT"/commands/*.md; do
    SKILL_NAME=$(basename "$(dirname "$SKILL_FILE")")/$(basename "$SKILL_FILE")
    FM=$(awk 'BEGIN{c=0} /^---/{c++; next} c==1{print} c==2{exit}' "$SKILL_FILE")
    if echo "$FM" | grep -q "user_invocable:"; then
        fail "$SKILL_NAME: declares dead user_invocable field — harness ignores it; use disable-model-invocation (or drop the line for model-invoked default)"
    else
        pass "$SKILL_NAME: no dead user_invocable field"
    fi
done
for CALLED_SKILL in context-keeper pattern-extractor obsidian-sync; do
    FM=$(awk 'BEGIN{c=0} /^---/{c++; next} c==1{print} c==2{exit}' "$PLUGIN_ROOT/skills/$CALLED_SKILL/SKILL.md")
    if echo "$FM" | grep -q "^disable-model-invocation: true"; then
        fail "skill $CALLED_SKILL: has disable-model-invocation but is invoked by wrap-up/self-improve via the Skill tool — the flag would break that delegation"
    else
        pass "skill $CALLED_SKILL: stays model-invocable (required for skill-to-skill delegation)"
    fi
done
# (c) the three script-core commands (v5.0.0) are slash-only. A command file is
#     still resolvable by the Skill tool and its description loads into context;
#     only `disable-model-invocation: true` makes "manual-only" mechanical
#     (Codex Verifier finding, 2026-09-08).
for MANUAL_CMD in maintain log sync-context; do
    FM=$(awk 'BEGIN{c=0} /^---/{c++; next} c==1{print} c==2{exit}' "$PLUGIN_ROOT/commands/$MANUAL_CMD.md")
    if echo "$FM" | grep -q "^disable-model-invocation: true"; then
        pass "command $MANUAL_CMD: slash-only via disable-model-invocation: true"
    else
        fail "command $MANUAL_CMD: missing disable-model-invocation: true — the command would be model-invocable and its description would burn context every turn (prose alone cannot prevent that)"
    fi
done

echo ""
echo "-- memory schema: Single Source of Truth exists and is the only definition --"
SCHEMA_FILE="$PLUGIN_ROOT/scripts/mem-schema.sh"
HOOK_FILE="$PLUGIN_ROOT/scripts/session-start.sh"
INITCMD_FILE="$PLUGIN_ROOT/commands/init.md"
if [ ! -f "$SCHEMA_FILE" ]; then
    fail "mem-schema.sh missing — the .agent-memory/ schema must live in ONE sourceable file (scripts/mem-schema.sh)"
else
    pass "mem-schema.sh exists (single source of truth for memory schema)"

    if grep -q "create_memory_structure()" "$SCHEMA_FILE"; then
        pass "mem-schema.sh defines create_memory_structure()"
    else
        fail "mem-schema.sh: create_memory_structure() not defined — both hook and /init depend on it"
    fi

    # The hook must SOURCE the schema, not re-inline the file list (L4 drift guard)
    if [ -f "$HOOK_FILE" ]; then
        if grep -q "mem-schema.sh" "$HOOK_FILE" && grep -q "create_memory_structure" "$HOOK_FILE"; then
            pass "session-start.sh sources mem-schema.sh (no inlined duplicate schema)"
        else
            fail "session-start.sh does NOT source mem-schema.sh — re-inlining the file list reintroduces the L4 hook/command drift"
        fi
    fi

    # /init command must invoke the schema script, not hand-list files
    if [ -f "$INITCMD_FILE" ]; then
        if grep -q "mem-schema.sh" "$INITCMD_FILE"; then
            pass "commands/init.md invokes mem-schema.sh (shared schema with the hook)"
        else
            fail "commands/init.md does NOT reference mem-schema.sh — /init would drift from the hook (L4)"
        fi
    fi

    # Functional check: running the schema produces EVERY file it is responsible for
    # (full list — not a representative subset — so a dropped file is always caught).
    SCHEMA_TMP=$(mktemp -d 2>/dev/null)
    if [ -n "$SCHEMA_TMP" ]; then
        bash "$SCHEMA_FILE" "$SCHEMA_TMP/.agent-memory" >/dev/null 2>&1
        MISSING=""
        for req in identity/soul.md identity/user.md \
                   identity/user-changelog.json identity/soul-candidates.md \
                   context/decisions.json context/open-tasks.json \
                   iterations/iteration-log.md iterations/errors.json \
                   patterns/patterns.md patterns/patterns.json \
                   quality/test-results.json quality/code-reviews.json quality/quality-score.json \
                   learnings/learnings.md learnings/learnings.json \
                   knowledge/notebook-registry.md working/current-session.json \
                   working/user-candidates.json \
                   session-summary.md; do
            [ -f "$SCHEMA_TMP/.agent-memory/$req" ] || MISSING="$MISSING $req"
        done
        rm -rf "$SCHEMA_TMP"
        if [ -z "$MISSING" ]; then
            pass "mem-schema.sh produces all schema files (full list)"
        else
            fail "mem-schema.sh did not create:$MISSING"
        fi
    fi

    # Negative drift guard: the hook must NOT inline schema WRITES into .agent-memory
    # subfiles. The ONLY permitted inline write is project-context.md (intentionally
    # outside the SSoT — needs stack detection). Match only real file-creating writes
    # (redirect-to-file / heredoc into a memory subdir), NOT reads that merely use 2>
    # or `|| echo "0"`. Pattern: `> "$MEMORY_DIR/<schema-subdir>/...`.
    if [ -f "$HOOK_FILE" ]; then
        LEAK=$(grep -nE '(>|cat >)[[:space:]]*"\$MEMORY_DIR/(identity|iterations|patterns|quality|learnings|knowledge|working)/' "$HOOK_FILE" || true)
        if [ -z "$LEAK" ]; then
            pass "session-start.sh has no inline schema writes outside mem-schema.sh (project-context.md exempt)"
        else
            fail "session-start.sh inlines schema writes that belong in mem-schema.sh (L4 drift):
$LEAK"
        fi
    fi

    # Reference-doc drift guard (L8/L9): references/memory-structure.md documents the
    # store layout. Every memory file it names in its tree/JSON-defaults must actually
    # be produced by the SSoT — otherwise the doc silently drifts (the bug fixed
    # 2026-06-01: doc omitted learnings.json, open-tasks.json, working/). Direction is
    # "doc subset of real"; the full-list check above already guards "real subset of doc".
    REF_DOC="$PLUGIN_ROOT/references/memory-structure.md"
    REF_TMP=$(mktemp -d 2>/dev/null)
    if [ -f "$REF_DOC" ] && [ -n "$REF_TMP" ]; then
        # Materialize the schema into a fresh temp dir (the SCHEMA_TMP above is gone)
        bash "$SCHEMA_FILE" "$REF_TMP/.agent-memory" >/dev/null 2>&1
        # Extract documented memory paths: only tokens under a known .agent-memory/
        # top-level dir (so prose refs like `commands/init.md` or `memory-maintenance/
        # SKILL.md` in the source-header are not mistaken for store paths).
        # project-context.md is exempt (SSoT does not create it — written by consumers).
        DOC_MISSING=""
        DOC_PATHS=$(grep -oE '(identity|context|iterations|patterns|quality|knowledge|learnings|working|generated-skills)/[a-zA-Z0-9_.-]+\.(json|md)' "$REF_DOC" | sort -u)
        for p in $DOC_PATHS; do
            case "$p" in
                context/project-context.md) continue ;;        # consumer-written, not SSoT
                *-archive-*) continue ;;                        # archive examples, created on rotation
            esac
            [ -f "$REF_TMP/.agent-memory/$p" ] || DOC_MISSING="$DOC_MISSING $p"
        done
        rm -rf "$REF_TMP"
        if [ -z "$DOC_MISSING" ]; then
            pass "references/memory-structure.md documents only paths the SSoT produces"
        else
            fail "references/memory-structure.md names paths the SSoT does NOT create (doc drift):$DOC_MISSING"
        fi
    fi
fi

echo ""
echo "-- project-context.md: all writers honor docs-as-source-of-truth --"
# project-context.md is a CACHE of the project docs. EVERY writer (hook, /init,
# context-detective, context-keeper) must reference the docs as source of truth so the
# cache can never silently diverge from docs/. Guards against the divergence paths the
# Codex verifier flagged (2026-06-01).
PC_WRITERS_OK=true
# context-keeper must declare the source-of-truth hierarchy
if [ -f "$PLUGIN_ROOT/skills/context-keeper/SKILL.md" ]; then
    grep -q "Source-of-Truth Hierarchy" "$PLUGIN_ROOT/skills/context-keeper/SKILL.md" || { PC_WRITERS_OK=false; echo "    (context-keeper: missing Source-of-Truth Hierarchy)"; }
fi
# /init must read docs before detecting
if [ -f "$PLUGIN_ROOT/commands/init.md" ]; then
    grep -qiE "docs first|source of truth" "$PLUGIN_ROOT/commands/init.md" || { PC_WRITERS_OK=false; echo "    (init.md: missing docs-first rule)"; }
fi
# context-detective must read docs first
if [ -f "$PLUGIN_ROOT/agents/context-detective.md" ]; then
    grep -qiE "docs FIRST|source of truth" "$PLUGIN_ROOT/agents/context-detective.md" || { PC_WRITERS_OK=false; echo "    (context-detective: missing docs-first rule)"; }
fi
# hook template must carry the cache pointer line
if [ -f "$PLUGIN_ROOT/scripts/session-start.sh" ]; then
    grep -q "This file is a cache" "$PLUGIN_ROOT/scripts/session-start.sh" || { PC_WRITERS_OK=false; echo "    (session-start.sh: missing cache pointer in project-context template)"; }
fi
if [ "$PC_WRITERS_OK" = true ]; then
    pass "all project-context.md writers reference docs as source of truth"
else
    fail "a project-context.md writer does not honor docs-as-source-of-truth — cache can silently diverge from docs/"
fi

# --- open-tasks root-drift surfaced at SessionStart (Audit-Hebel #6, moved 4.21.0) ---
# /agentic-os:maintain heals the root-vs-context open-tasks.json drift but is threshold-gated.
# The SessionEnd prompt hook that used to promise the heal could not act (SessionEnd hooks
# take no further actions); the drift is now flagged mechanically in the SessionStart
# briefing, which the model actually sees. Behaviour is pinned by test-session-start-briefing.sh.
echo ""
echo "-- session-start.sh: open-tasks root-drift check --"
if grep -q 'MEMORY_DIR/open-tasks.json' "$PLUGIN_ROOT/scripts/session-start.sh" \
   && grep -qi "root drift" "$PLUGIN_ROOT/scripts/session-start.sh"; then
    pass "session-start.sh: flags a stray root .agent-memory/open-tasks.json (root drift) in the briefing"
else
    fail "session-start.sh: must flag a stray root .agent-memory/open-tasks.json as 'root drift' in the briefing"
fi

# --- /memory-audit command (Audit-Hebel #5, 2026-06-03) ---
# A repeatable read-only drift/provenance/staleness report. The whole point is that running
# it instead of trusting a months-old manual audit prevents stale-data conclusions.
echo ""
echo "-- /memory-audit command exists and is read-only --"
MA_CMD="$PLUGIN_ROOT/commands/memory-audit.md"
if [ ! -f "$MA_CMD" ]; then
    fail "commands/memory-audit.md missing — Audit-Hebel #5: a repeatable read-only drift/provenance/staleness report"
else
    pass "commands/memory-audit.md exists"
    # Must be read-only: allowed_tools may contain ONLY non-mutating tools. Bash is excluded too
    # because `echo > file` / `rm` through Bash would bypass the read-only boundary (Codex MAJOR).
    if grep -qE "^allowed_tools:" "$MA_CMD" && ! grep -qiE "\"(Write|Edit|NotebookEdit|Bash)\"" "$MA_CMD"; then
        pass "memory-audit: read-only enforced by tool boundary (no Write/Edit/Bash in allowed_tools)"
    else
        fail "memory-audit: read-only not tool-enforced — allowed_tools must exclude Write/Edit/NotebookEdit AND Bash (Bash would let the audit mutate the store it inspects via echo>/rm)"
    fi
    # Must cover the three audit dimensions the manual audit got wrong from stale data.
    if grep -qiE "drift" "$MA_CMD" && grep -qiE "staleness|stale" "$MA_CMD" && grep -qiE "provenance|schema" "$MA_CMD"; then
        pass "memory-audit: covers drift + staleness + provenance/schema dimensions"
    else
        fail "memory-audit: must report drift (root open-tasks, schema), staleness (learnings layer age, quality last_updated) and provenance/schema consistency"
    fi

    # 4.A: memory-audit must also report the global provenance layer (read-only).
    if grep -qiE "\(global-provenance-audit\)" "$MA_CMD" \
       && grep -qiE "scope" "$MA_CMD" && grep -qiE "valid_from" "$MA_CMD" \
       && grep -qiE "source_projects" "$MA_CMD" \
       && grep -qiE "promotion-gate violation" "$MA_CMD"; then
        pass "memory-audit: reports global provenance (scope/valid_from/source_projects, promotion-gate violation)"
    else
        fail "memory-audit: must report the 4.A global layer (global-provenance-audit) — entries missing scope/valid_from/source_projects and active promotion-gate violations"
    fi

    # 4.A: the global audit must stay read-only — allowed_tools may not gain a write tool.
    if grep -qiE "^allowed_tools:.*(Write|Edit|Bash)" "$MA_CMD"; then
        fail "memory-audit: allowed_tools gained a write/exec tool — the audit MUST stay read-only"
    else
        pass "memory-audit: allowed_tools stays read-only (no Write/Edit/Bash)"
    fi
fi

# --- Command/Skill name shadowing guard (L17, 2026-06-12) ---
# A command with the same name as a skill SHADOWS the skill in the Skill tool:
# invoking 'agentic-os:<name>' returns the command wrapper (which says "invoke the
# skill") instead of the skill body -> infinite indirection loop (observed live
# 2026-06-12 with wrap-up). Skills are directly slash-invocable, so delegating
# wrapper commands are redundant. No command may share a name with a skill.
echo ""
echo "-- command/skill name shadowing (L17) --"
SHADOWED=""
for CMD_FILE in "$PLUGIN_ROOT"/commands/*.md; do
    [ -f "$CMD_FILE" ] || continue
    CMD_NAME="$(basename "$CMD_FILE" .md)"
    if [ -d "$PLUGIN_ROOT/skills/$CMD_NAME" ]; then
        SHADOWED="$SHADOWED $CMD_NAME"
    fi
done
if [ -z "$SHADOWED" ]; then
    pass "no command shadows a skill name (Skill-tool loop guard, L17)"
else
    fail "command(s) shadow skill name(s):$SHADOWED — the Skill tool resolves the name to the command wrapper, causing an invoke loop (L17). Delete or rename the wrapper command."
fi

# --- wrap-up session-bracket coverage: session-harvest + decision-scan ---------
# Users who only run bootstrap + wrap-up never call iteration-logger or
# context-keeper manually -> without a retro-harvest the pattern pipeline starves
# (after months of sessions the store held 5 iterations, 3 errors and a null
# quality score, observed 2026-06-12). v3.6.0 fixed that by DELEGATING to those
# skills; T-015 keeps the coverage but drops the delegation: a skill-body
# injection was followed by a full prefix-cache rewrite in 41% of measured cases
# (L34/D-010), and those bodies were mechanical rules that now live in the
# scripts. So the requirement flipped from "must delegate" to "must route into
# the write plan, and must NOT delegate".
#
# The delegation budget below IS the T-015 definition of done: the number of skill
# invocations one wrap-up run prescribes. Counted, not estimated - the previous
# version of this test greped for "invoke ... <skill>", which goes false-green on
# "do NOT invoke <skill>" (that is exactly how it read this file after the rebuild).
echo ""
echo "-- wrap-up delegation budget (T-015) --"
WU_SKILL36="$PLUGIN_ROOT/skills/wrap-up/SKILL.md"
if [ -f "$WU_SKILL36" ]; then
    # Flatten first: the prohibition spans a line break, so a per-line grep sees
    # "invoke the" and "`iteration-logger`" as unrelated hits.
    WU_FLAT="$(tr -s '[:space:]' ' ' < "$WU_SKILL36")"
    # Counts delegation verbs pointing at <skill>, minus the negated forms.
    # TWO ways this went wrong before, both found by review:
    #  - only "invoke" was matched, so "call/delegate to/trigger `<skill>`" read
    #    as zero and would have waved a real delegation through (Codex, 1e5c504);
    #  - the negation list must cover every way this file says no, or a
    #    prohibition is counted as a delegation.
    # Prose regexes stay approximate: keep delegations in the form
    # "invoke the `<skill>` skill" and prohibitions as "do NOT invoke `<skill>`".
    VERBS="invokes?|calls?|delegates? to|triggers?|hands? off to"
    positive_invokes() {
        ALL=$(echo "$WU_FLAT" | grep -oiE "($VERBS)[^.]{0,60}\`?$1\`?" | wc -l)
        NEG=$(echo "$WU_FLAT" \
              | grep -oiE "(n[o']t|never|no longer|instead of)[^.]{0,14}($VERBS)[^.]{0,60}\`?$1\`?" \
              | wc -l)
        echo $((ALL - NEG))
    }
    for FORBIDDEN_CALLEE in iteration-logger context-keeper; do
        N=$(positive_invokes "$FORBIDDEN_CALLEE")
        if [ "$N" -eq 0 ]; then
            pass "wrap-up: does not invoke $FORBIDDEN_CALLEE (its rules live in apply_wrapup.py)"
        else
            fail "wrap-up: still prescribes $N skill invocation(s) of $FORBIDDEN_CALLEE — each one risks a full prefix-cache rewrite (41%, L34); route the data through the write plan instead"
        fi
    done
    # pattern-extractor keeps exactly ONE conditional invoke: Steps 6.5/6.6 (skill
    # generation, delta drafts) are judgment work that no script can take over.
    N=$(positive_invokes "pattern-extractor")
    if [ "$N" -le 1 ]; then
        pass "wrap-up: at most one conditional pattern-extractor invoke (Steps 6.5/6.6)"
    else
        fail "wrap-up: $N pattern-extractor invocations — the routine path must call scripts/extract_patterns.py, not the skill"
    fi

    # Coverage must survive the rebuild: both blocks still have to route somewhere.
    HARVEST_BLOCK="$(grep -A 24 "(session-harvest)" "$WU_SKILL36")"
    if echo "$HARVEST_BLOCK" | grep -qE '`iterations`'; then
        pass "wrap-up: (session-harvest) routes reconstructed iterations into the write plan"
    else
        fail "wrap-up: (session-harvest) block no longer routes iterations anywhere — the bootstrap+wrap-up bracket starves the pattern pipeline"
    fi
    DSCAN_BLOCK="$(grep -A 16 "(decision-scan)" "$WU_SKILL36")"
    if echo "$DSCAN_BLOCK" | grep -qE '`decisions`'; then
        pass "wrap-up: (decision-scan) routes decisions of record into the write plan"
    else
        fail "wrap-up: (decision-scan) block no longer routes decisions anywhere — session decisions are lost"
    fi
    PEXT_BLOCK="$(grep -A 26 "(pattern-extraction)" "$WU_SKILL36")"
    if echo "$PEXT_BLOCK" | grep -q "extract_patterns.py"; then
        pass "wrap-up: (pattern-extraction) calls the deterministic extractor script"
    else
        fail "wrap-up: (pattern-extraction) block does not call scripts/extract_patterns.py"
    fi
    # Write ownership still has to be stated - it moved to the script, it did not vanish.
    if echo "$HARVEST_BLOCK" | grep -q "apply_wrapup.py"; then
        pass "wrap-up: session-harvest names apply_wrapup.py as the owner of those writes"
    else
        fail "wrap-up: session-harvest block lacks the write-ownership clause (apply_wrapup.py owns iteration-log.md/errors.json)"
    fi
else
    fail "wrap-up: SKILL.md not found (session-harvest check)"
fi
# The dependency graph must reflect the new conditional invokes WHERE they matter:
# in wrap-up's matrix row (Invokes column) and as a dedicated coverage section —
# a stray whole-file mention must not satisfy this (L11).
WU_MATRIX_ROW="$(grep -E "^\| wrap-up \|" "$DEPS" 2>/dev/null)"
if echo "$WU_MATRIX_ROW" | grep -q "session-harvest" \
   && echo "$WU_MATRIX_ROW" | grep -q "decision-scan" \
   && grep -q "^## Session-Bracket Coverage" "$DEPS" 2>/dev/null; then
    pass "DEPENDENCIES.md: wrap-up matrix row + Session-Bracket Coverage section document the new invokes"
else
    fail "DEPENDENCIES.md: wrap-up matrix row or Session-Bracket Coverage section missing session-harvest/decision-scan — graph drifted from wrap-up SKILL.md"
fi

echo ""
echo "=== Results: $PASSED/$TESTS passed, $ERRORS failures ==="
[ "$ERRORS" -eq 0 ]

#!/usr/bin/env bash
# session-start.sh: the "User:" line of the briefing (P2, Release A).
#
# Before P2 the hook showed `head -4` of the project's user.md - the four OLDEST lines, placeholders included
# ("- (Will be populated through session observations)"). Now it reads, in this order:
#   1. the central profile's projection <store>/identity/profile-briefing.txt, when <store>/identity/profile.md exists
#      (<store> = $AGENTIC_OS_IDENTITY_STORE, else $HOME/.agent-memory),
#   2. the project's own projection $MEMORY_DIR/identity/briefing-lines.txt,
#   3. the first 4 real bullets of the project's user.md (placeholders filtered).
# The projection is written by scripts/identity_briefing.py (ranked by confirmations, capped in length).
set -uo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAIL=0
pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; FAIL=1; }

TEMPLATE_USER_MD='# User Profile

*Initialized: 2026-07-01*

## Preferences
- (Will be populated through session observations)

## Known Corrections
- (Recorded when user corrects agent behavior 3+ times)
'

# fresh fake home + project per case; the hook runs with both HOME and USERPROFILE on the fake home
setup() {
  rm -rf "$TMP/home" "$TMP/proj"
  mkdir -p "$TMP/home" "$TMP/proj/.agent-memory/identity"
  printf '%s' "$TEMPLATE_USER_MD" > "$TMP/proj/.agent-memory/identity/user.md"
}
central() {  # central() <store> <first line>
  mkdir -p "$1/identity"
  printf '# Profil\n\n## Preferences\n%s\n' "$2" > "$1/identity/profile.md"
  printf '%s\n- Zweite Zentralzeile\n' "$2" > "$1/identity/profile-briefing.txt"
}
brief() {  # brief [VAR=value ...] -> the hook's JSON on stdout
  echo '{"cwd":"x","session_id":"t"}' | env -u AGENTIC_OS_IDENTITY_STORE HOME="$TMP/home" USERPROFILE="$TMP/home" \
    CLAUDE_PROJECT_DIR="$TMP/proj" "$@" bash "$DIR/scripts/session-start.sh" 2>/dev/null
}

# H1 central profile with its projection -> the User line comes from there
setup
central "$TMP/home/.agent-memory" "- Zentralzeile eins"
OUT="$(brief)"
if printf '%s' "$OUT" | grep -q "User: - Zentralzeile eins - Zweite Zentralzeile"; then
  pass "H1 central projection feeds the User line"
else
  fail "H1 central projection feeds the User line: $(printf '%s' "$OUT" | grep -o 'User:[^\\]*' | head -1)"
fi

# H2 no central profile, but a project projection -> the project projection
setup
printf -- '- Projektprojektion\n' > "$TMP/proj/.agent-memory/identity/briefing-lines.txt"
OUT="$(brief)"
if printf '%s' "$OUT" | grep -q "User: - Projektprojektion"; then
  pass "H2 project projection without a central profile"
else
  fail "H2 project projection without a central profile: $(printf '%s' "$OUT" | grep -o 'User:[^\\]*' | head -1)"
fi
# a central projection WITHOUT profile.md is not active (the migration writes profile.md last)
mkdir -p "$TMP/home/.agent-memory/identity"
printf -- '- Halbe Migration\n' > "$TMP/home/.agent-memory/identity/profile-briefing.txt"
OUT="$(brief)"
if printf '%s' "$OUT" | grep -q "User: - Projektprojektion" && ! printf '%s' "$OUT" | grep -q "Halbe Migration"; then
  pass "H2 a central projection without profile.md is ignored"
else
  fail "H2 a central projection without profile.md is ignored"
fi

# H3 neither projection, template user.md -> no User line at all (placeholders only)
setup
OUT="$(brief)"
if printf '%s' "$OUT" | grep -q "User:"; then
  fail "H3 template user.md gives no User line: $(printf '%s' "$OUT" | grep -o 'User:[^\\]*' | head -1)"
else
  pass "H3 template user.md gives no User line"
fi
# a real bullet next to the placeholders is still shown (fallback stays useful)
printf -- '- Echte Zeile\n' >> "$TMP/proj/.agent-memory/identity/user.md"
OUT="$(brief)"
if printf '%s' "$OUT" | grep -q "User: - Echte Zeile" && ! printf '%s' "$OUT" | grep -q "Will be populated"; then
  pass "H3 fallback shows real bullets without placeholders"
else
  fail "H3 fallback shows real bullets without placeholders"
fi

# H4 AGENTIC_OS_IDENTITY_STORE names another central store than $HOME/.agent-memory -> the variable wins
setup
central "$TMP/home/.agent-memory" "- Home-Zentral"
central "$TMP/other" "- Variablen-Zentral"
touch "$TMP/marker"
OUT="$(brief AGENTIC_OS_IDENTITY_STORE="$TMP/other")"
if printf '%s' "$OUT" | grep -q "User: - Variablen-Zentral" && ! printf '%s' "$OUT" | grep -q "Home-Zentral"; then
  pass "H4 AGENTIC_OS_IDENTITY_STORE wins over \$HOME/.agent-memory"
else
  fail "H4 AGENTIC_OS_IDENTITY_STORE wins: $(printf '%s' "$OUT" | grep -o 'User:[^\\]*' | head -1)"
fi

# the hook never writes into the central profile
if [ -n "$(find "$TMP/home/.agent-memory/identity" "$TMP/other/identity" -newer "$TMP/marker" -type f 2>/dev/null)" ]; then
  fail "hook wrote into a central store"
else
  pass "hook leaves the central stores untouched"
fi

# F3 (DCO #10148) the Identity line shows own soul.md lines only, never the init template
setup
bash -c 'source "$1/scripts/mem-schema.sh"; create_memory_structure "$2"' _ "$DIR" "$TMP/fresh" >/dev/null 2>&1
cp "$TMP/fresh/identity/soul.md" "$TMP/proj/.agent-memory/identity/soul.md"
OUT="$(brief)"
if printf '%s' "$OUT" | grep -q "Identity:"; then
  fail "F3 a template-only soul.md gives no Identity line: $(printf '%s' "$OUT" | grep -o 'Identity:[^\\]*' | head -1)"
else
  pass "F3 a template-only soul.md gives no Identity line"
fi
printf -- '- Beispielzeile eins\r\n' >> "$TMP/proj/.agent-memory/identity/soul.md"
OUT="$(brief)"
IDL="$(printf '%s' "$OUT" | grep -o 'Identity:[^\\|]*' | head -1)"
IDL="${IDL%"${IDL##*[! ]}"}"  # trailing blanks before the next briefing part
if [ "$IDL" = "Identity: - Beispielzeile eins" ]; then
  pass "F3 an own line next to the template: the Identity line holds exactly that line"
else
  fail "F3 an own line next to the template: got '$IDL'"
fi
# the older template versions count as template too (they stand in real stores)
printf '# Agent Identity\n\n## Communication\n- Language: en (switch to de if user writes in German)\n- Brevity: 3/5 (balanced — concise but explain when needed)\n- Proactivity: 3/5\n' \
  > "$TMP/proj/.agent-memory/identity/soul.md"
OUT="$(brief)"
if printf '%s' "$OUT" | grep -q "Identity:"; then
  fail "F3 older template versions give no Identity line"
else
  pass "F3 older template versions give no Identity line"
fi

# F4 the init template says Language: de, and it comes from scripts/soul-template.txt (one source)
if grep -qxF -- "- Language: de (switch to en if user writes in English)" "$TMP/fresh/identity/soul.md"; then
  pass "F4 create_memory_structure writes soul.md with 'Language: de (switch to en if user writes in English)'"
else
  fail "F4 fresh soul.md language line: $(grep -E '^- Language' "$TMP/fresh/identity/soul.md")"
fi
if [ -f "$DIR/scripts/soul-template.txt" ] && ! grep -q "Agent Identity" "$DIR/scripts/mem-schema.sh" \
   && diff -q <(sed '/^<!-- earlier versions/,$d' "$DIR/scripts/soul-template.txt" | tr -d '\r') <(tr -d '\r' < "$TMP/fresh/identity/soul.md") >/dev/null; then
  pass "F4 soul.md is the current part of scripts/soul-template.txt, mem-schema.sh holds no copy"
else
  fail "F4 soul.md is the current part of scripts/soul-template.txt, mem-schema.sh holds no copy"
fi
if ! grep -q "<!--" "$TMP/fresh/identity/soul.md"; then
  pass "F4 the older template lines are not written into a new soul.md"
else
  fail "F4 the older template lines are not written into a new soul.md"
fi

# H5 the home project (its store IS the central store): central active -> central projection; after the way back
# (profile.md renamed) the home project shows its OWN projection, never the central one with every project's lines
rm -rf "$TMP/home"
mkdir -p "$TMP/home/.agent-memory/identity"
central "$TMP/home/.agent-memory" "- Zentral fuer alle"
printf -- '- Nur das Home-Projekt\n' > "$TMP/home/.agent-memory/identity/briefing-lines.txt"
home_brief() {
  echo '{"cwd":"x","session_id":"t"}' | env -u AGENTIC_OS_IDENTITY_STORE HOME="$TMP/home" USERPROFILE="$TMP/home" \
    CLAUDE_PROJECT_DIR="$TMP/home" bash "$DIR/scripts/session-start.sh" 2>/dev/null
}
OUT="$(home_brief)"
if printf '%s' "$OUT" | grep -q "User: - Zentral fuer alle"; then
  pass "H5 home project with an active central profile: central projection"
else
  fail "H5 home project with an active central profile: $(printf '%s' "$OUT" | grep -o 'User:[^\\]*' | head -1)"
fi
mv "$TMP/home/.agent-memory/identity/profile.md" "$TMP/home/.agent-memory/identity/profile.md.aus"
OUT="$(home_brief)"
if printf '%s' "$OUT" | grep -q "User: - Nur das Home-Projekt" && ! printf '%s' "$OUT" | grep -q "Zentral fuer alle"; then
  pass "H5 way back (profile.md renamed): the home project's own projection, not the central one"
else
  fail "H5 way back: $(printf '%s' "$OUT" | grep -o 'User:[^\\]*' | head -1)"
fi

[ "$FAIL" -eq 0 ] && echo "=== ALL PASSED ===" || echo "=== FAILURES ==="
exit "$FAIL"

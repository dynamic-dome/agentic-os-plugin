#!/usr/bin/env bash
# session-start.sh must never auto-init a .agent-memory INSIDE an existing .agent-memory.
# cost-trace.sh must likewise refuse to create metrics/ inside a nested .agent-memory cwd.
set -uo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
mkdir -p "$TMP/proj/.agent-memory/working"
cd "$TMP/proj/.agent-memory/working"
# session-start.sh derives PROJECT_DIR from $CLAUDE_PROJECT_DIR (NOT from stdin
# JSON's "cwd" field, which the script never reads) — set it explicitly to the
# real second start path (session-start.sh line 19) so the test hits the actual
# derivation instead of the unused "." default.
echo '{"cwd":"'"$TMP/proj/.agent-memory/working"'","session_id":"t"}' | \
  CLAUDE_PROJECT_DIR="$TMP/proj/.agent-memory/working" bash "$DIR/scripts/session-start.sh" > "$TMP/out.txt" 2>&1
RC=$?
FAIL=0
# Since 5.13.0 a folder without git gets no store unless the plugin option is set. The cases below that
# exercise the init path itself (system directory guard, write verification, partial writes) run with
# the option on, so they still reach the code under test; the new section at the end covers the default.
export CLAUDE_PLUGIN_OPTION_AUTO_INIT_NON_GIT=true
if [ -d "$TMP/proj/.agent-memory/working/.agent-memory" ] || [ -d "$TMP/proj/.agent-memory/.agent-memory" ]; then
  echo "  FAIL: nested .agent-memory created"; FAIL=1
else
  echo "  PASS: no nested .agent-memory"
fi
if [ "$RC" -eq 0 ]; then echo "  PASS: exit 0 (fail-soft)"; else echo "  FAIL: exit $RC"; FAIL=1; fi

# Second case: cost-trace.sh run from a cwd inside .agent-memory must not create
# a nested metrics/ dir (controller ruling: this is the actual origin of the
# nested stores found in hygiene 2026-09, not session-start.sh).
cd "$TMP/proj/.agent-memory/working"
bash "$DIR/scripts/cost-trace.sh" append --mem .agent-memory --task test --class cheap-write --context-bytes 100 --escalated 0 > "$TMP/ct-out.txt" 2>&1
if [ -d "$TMP/proj/.agent-memory/working/.agent-memory" ]; then
  echo "  FAIL: cost-trace.sh created nested .agent-memory/metrics"; FAIL=1
else
  echo "  PASS: cost-trace.sh created no nested .agent-memory"
fi

# Third case (V7): a subdirectory of a git repo must not get its own store;
# a plain non-git directory inits only with the option (the section at the end covers the default).
mkdir -p "$TMP/repo/scripts/work" "$TMP/plain"
git -C "$TMP/repo" init -q
echo '{"cwd":"x","session_id":"t"}' | CLAUDE_PROJECT_DIR="$TMP/repo/scripts/work" bash "$DIR/scripts/session-start.sh" > "$TMP/sub-out.txt" 2>&1
RC=$?
if [ -d "$TMP/repo/scripts/work/.agent-memory" ]; then
  echo "  FAIL: auto-init in git subdirectory"; FAIL=1
else
  echo "  PASS: no auto-init in git subdirectory"
fi
grep -q "subdirectory of git repo" "$TMP/sub-out.txt" && echo "  PASS: subdirectory skip is explained" || { echo "  FAIL: no skip message"; FAIL=1; }
[ "$RC" -eq 0 ] && echo "  PASS: exit 0 (fail-soft)" || { echo "  FAIL: exit $RC"; FAIL=1; }
echo '{"cwd":"x","session_id":"t"}' | CLAUDE_PROJECT_DIR="$TMP/plain" bash "$DIR/scripts/session-start.sh" > "$TMP/plain-out.txt" 2>&1
if [ -d "$TMP/plain/.agent-memory" ]; then
  echo "  PASS: non-git directory auto-inits with the option"
else
  echo "  FAIL: non-git directory not initialised"; FAIL=1
fi
echo '{"cwd":"x","session_id":"t"}' | CLAUDE_PROJECT_DIR="$TMP/repo" bash "$DIR/scripts/session-start.sh" > "$TMP/root-out.txt" 2>&1
if [ -d "$TMP/repo/.agent-memory" ]; then
  echo "  PASS: git toplevel still auto-inits"
else
  echo "  FAIL: git toplevel not initialised"; FAIL=1
fi
# Fourth case (Codex verifier 5.0.2): subdirectory of a repo whose root HAS a store ->
# no second store, but the hook still emits the JSON briefing from the root store.
echo '{"cwd":"x","session_id":"t"}' | CLAUDE_PROJECT_DIR="$TMP/repo/scripts/work" bash "$DIR/scripts/session-start.sh" > "$TMP/sub2-out.txt" 2>&1
RC=$?
if [ -d "$TMP/repo/scripts/work/.agent-memory" ]; then
  echo "  FAIL: second store created next to a root store"; FAIL=1
else
  echo "  PASS: no second store when the repo root has one"
fi
if grep -q '"additionalContext"' "$TMP/sub2-out.txt" && grep -q "repo-root store" "$TMP/sub2-out.txt"; then
  echo "  PASS: JSON briefing served from the root store"
else
  echo "  FAIL: no JSON briefing for subdirectory session"; FAIL=1; head -5 "$TMP/sub2-out.txt"
fi
[ "$RC" -eq 0 ] && echo "  PASS: exit 0" || { echo "  FAIL: exit $RC"; FAIL=1; }

# Fifth case (DCO #9680): the Windows system directory is never a project. The tests point
# WINDIR/SYSTEMROOT at a fake tree - the hook is NEVER run with the real C:\Windows as project.
mkdir -p "$TMP/fakewin/system32/sub" "$TMP/fakewinx/proj"
FAKEWIN_ENV=(WINDIR="$TMP/fakewin" SYSTEMROOT="$TMP/fakewin" SystemRoot="$TMP/fakewin")
echo '{"cwd":"x","session_id":"t"}' | env "${FAKEWIN_ENV[@]}" CLAUDE_PROJECT_DIR="$TMP/fakewin/system32" bash "$DIR/scripts/session-start.sh" > "$TMP/sys-out.txt" 2>&1
RC=$?
if [ -e "$TMP/fakewin/system32/.agent-memory" ]; then
  echo "  FAIL: auto-init in the system directory"; FAIL=1
else
  echo "  PASS: no auto-init in the system directory"
fi
if grep -q "initialized for" "$TMP/sys-out.txt" || grep -q "Newly initialized" "$TMP/sys-out.txt"; then
  echo "  FAIL: init success reported for the system directory"; FAIL=1; head -3 "$TMP/sys-out.txt"
else
  echo "  PASS: no init success message for the system directory"
fi
grep -q "Windows system directory" "$TMP/sys-out.txt" && echo "  PASS: system directory skip is explained" || { echo "  FAIL: no system directory skip message"; FAIL=1; }
[ "$RC" -eq 0 ] && echo "  PASS: exit 0 (fail-soft)" || { echo "  FAIL: exit $RC"; FAIL=1; }
# A subdirectory of the system directory counts too, and so do the other spellings of the path
# (backslashes + upper case, /c/... posix form) - derived from the real path of the fake tree.
if WINFORM="$(cd "$TMP/fakewin/system32/sub" && pwd -W 2>/dev/null)" && [ -n "$WINFORM" ]; then
  UPPER_BS="$(printf '%s' "$WINFORM" | tr 'a-z' 'A-Z' | tr '/' '\\')"
  DRIVE="$(printf '%s' "$WINFORM" | cut -c1 | tr 'A-Z' 'a-z')"
  POSIX_FORM="/$DRIVE$(printf '%s' "$WINFORM" | cut -c3-)"
  for form in "$WINFORM" "$UPPER_BS" "$POSIX_FORM"; do
    echo '{"cwd":"x","session_id":"t"}' | env "${FAKEWIN_ENV[@]}" CLAUDE_PROJECT_DIR="$form" bash "$DIR/scripts/session-start.sh" > "$TMP/sys2-out.txt" 2>&1
    if [ -e "$TMP/fakewin/system32/sub/.agent-memory" ] || grep -q "Newly initialized" "$TMP/sys2-out.txt"; then
      echo "  FAIL: system directory subfolder initialised via path form '$form'"; FAIL=1
      rm -rf "$TMP/fakewin/system32/sub/.agent-memory"
    else
      echo "  PASS: system directory subfolder skipped via path form '$form'"
    fi
  done
fi
# Prefix is not containment: a sibling that merely starts with the same characters still inits.
echo '{"cwd":"x","session_id":"t"}' | env "${FAKEWIN_ENV[@]}" CLAUDE_PROJECT_DIR="$TMP/fakewinx/proj" bash "$DIR/scripts/session-start.sh" > "$TMP/sib-out.txt" 2>&1
if [ -d "$TMP/fakewinx/proj/.agent-memory" ]; then
  echo "  PASS: sibling with a common name prefix still auto-inits"
else
  echo "  FAIL: sibling with a common name prefix was treated as system directory"; FAIL=1
fi
# Each variable alone must be enough (the hook reads WINDIR, SystemRoot and SYSTEMROOT). The other two
# are removed first; on Windows bash may re-add the real value for them, which is harmless here.
for var in WINDIR SYSTEMROOT SystemRoot; do
  rm -rf "$TMP/fakewin/system32/.agent-memory"
  echo '{"cwd":"x","session_id":"t"}' | env -u WINDIR -u SYSTEMROOT -u SystemRoot "$var=$TMP/fakewin" CLAUDE_PROJECT_DIR="$TMP/fakewin/system32" bash "$DIR/scripts/session-start.sh" > "$TMP/var-out.txt" 2>&1
  if [ -e "$TMP/fakewin/system32/.agent-memory" ]; then
    echo "  FAIL: system directory initialised when only $var points at it"; FAIL=1
    rm -rf "$TMP/fakewin/system32/.agent-memory"
  else
    echo "  PASS: system directory skipped when only $var points at it"
  fi
done
# The system directory ITSELF (not just a subfolder) is no project either.
echo '{"cwd":"x","session_id":"t"}' | env "${FAKEWIN_ENV[@]}" CLAUDE_PROJECT_DIR="$TMP/fakewin" bash "$DIR/scripts/session-start.sh" > "$TMP/self-out.txt" 2>&1
if [ -e "$TMP/fakewin/.agent-memory" ]; then
  echo "  FAIL: auto-init in the system directory itself"; FAIL=1
  rm -rf "$TMP/fakewin/.agent-memory"
else
  echo "  PASS: no auto-init in the system directory itself"
fi
grep -q "Windows system directory" "$TMP/self-out.txt" && echo "  PASS: skip for the system directory itself is explained" || { echo "  FAIL: no skip message for the system directory itself"; FAIL=1; }
# The guard only applies while the store is missing: a system-dir project WITH a store is served normally.
mkdir -p "$TMP/fakewin/system32/.agent-memory"
echo '{"cwd":"x","session_id":"t"}' | env "${FAKEWIN_ENV[@]}" CLAUDE_PROJECT_DIR="$TMP/fakewin/system32" bash "$DIR/scripts/session-start.sh" > "$TMP/have-out.txt" 2>&1
if grep -q '"additionalContext"' "$TMP/have-out.txt" && ! grep -q "Windows system directory" "$TMP/have-out.txt"; then
  echo "  PASS: system directory with an existing store gets its briefing"
else
  echo "  FAIL: system directory with an existing store was skipped"; FAIL=1; head -3 "$TMP/have-out.txt"
fi
rm -rf "$TMP/fakewin/system32/.agent-memory"
# Function level (no hook run, so a broken guard can never create a store in the real C:\Windows):
# the c:/windows fallback when no variable is set, and the string normalisation for paths that do
# not exist (so cd + pwd -W cannot do the work): posix, cygdrive and mixed-case forms.
sed -n '/^_norm_path()/,/^}/p;/^is_windows_system_dir()/,/^}/p' "$DIR/scripts/session-start.sh" > "$TMP/fn.sh"
fn_check() { # <expected rc> <path>
  local want="$1" path="$2" got
  # unset INSIDE the child: on Windows bash re-adds the real WINDIR/SYSTEMROOT at startup, which would hide the fallback
  bash -c 'source "$1"; unset WINDIR SYSTEMROOT SystemRoot; is_windows_system_dir "$2"' _ "$TMP/fn.sh" "$path"
  got=$?
  [ "$got" -eq "$want" ] && echo "  PASS: is_windows_system_dir '$path' -> $got" || { echo "  FAIL: is_windows_system_dir '$path' -> $got, expected $want"; FAIL=1; }
}
if [ -s "$TMP/fn.sh" ]; then
  fn_check 0 "/c/Windows/aos-nonexistent-probe"
  fn_check 0 "/cygdrive/c/Windows/aos-nonexistent-probe"
  fn_check 0 'C:\WINDOWS\aos-nonexistent-probe'
  fn_check 1 "/c/Windowsx/aos-nonexistent-probe"
else
  echo "  FAIL: could not extract the guard functions"; FAIL=1
fi

# Sixth case (DCO #9680): the hook must verify what it wrote. ".agent-memory" is a regular FILE
# here, so mkdir -p really fails - no success message, a warning instead, still exit 0 + JSON.
mkdir -p "$TMP/blocked"
: > "$TMP/blocked/.agent-memory"
echo '{"cwd":"x","session_id":"t"}' | CLAUDE_PROJECT_DIR="$TMP/blocked" bash "$DIR/scripts/session-start.sh" > "$TMP/blk-out.txt" 2> "$TMP/blk-err.txt"
RC=$?
if grep -q "initialized for" "$TMP/blk-out.txt" || grep -q "Newly initialized" "$TMP/blk-out.txt"; then
  echo "  FAIL: init success reported although nothing was written"; FAIL=1; head -3 "$TMP/blk-out.txt"
else
  echo "  PASS: no init success message when the write failed"
fi
grep -q "WARNING: auto-init failed - no memory store was created" "$TMP/blk-out.txt" \
  && echo "  PASS: failed init surfaces a warning in the briefing block" || { echo "  FAIL: no auto-init failed line in the briefing block"; FAIL=1; }
grep -q "auto-init failed for 'blocked'" "$TMP/blk-out.txt" \
  && echo "  PASS: failed init surfaces a warning in the context prefix" || { echo "  FAIL: no auto-init failed warning in the context prefix"; FAIL=1; }
grep -q "Agentic OS NOT active" "$TMP/blk-out.txt" && ! grep -q "Agentic OS active |" "$TMP/blk-out.txt" \
  && echo "  PASS: briefing header says NOT active after a failed init" || { echo "  FAIL: briefing header still claims Agentic OS active after a failed init"; FAIL=1; }
grep -q '"additionalContext"' "$TMP/blk-out.txt" && echo "  PASS: JSON briefing still emitted" || { echo "  FAIL: no JSON briefing after failed init"; FAIL=1; }
[ "$RC" -eq 0 ] && echo "  PASS: exit 0 (fail-soft)" || { echo "  FAIL: exit $RC"; FAIL=1; }
# Regression: a normal project keeps its success message and the core files exist.
grep -q "initialized for 'plain'" "$TMP/plain-out.txt" && grep -q "Newly initialized" "$TMP/plain-out.txt" \
  && echo "  PASS: successful init still reports success" || { echo "  FAIL: success message lost for a normal project"; FAIL=1; }
grep -q "auto-init failed" "$TMP/plain-out.txt" && { echo "  FAIL: warning on a successful init"; FAIL=1; } || echo "  PASS: no warning on a successful init"
grep -q "Agentic OS active |" "$TMP/plain-out.txt" && ! grep -q "Agentic OS NOT active" "$TMP/plain-out.txt" \
  && echo "  PASS: briefing header says active after a successful init" || { echo "  FAIL: briefing header wrong after a successful init"; FAIL=1; }

# The warning carries the path. A Windows path in backslash form with a folder starting with "n" must not
# be split across lines (the JSON emitter turns every literal backslash-n into a newline).
mkdir -p "$TMP/nblocked"
: > "$TMP/nblocked/.agent-memory"
if NWIN="$(cd "$TMP/nblocked" && pwd -W 2>/dev/null)" && [ -n "$NWIN" ]; then
  NBS="$(printf '%s' "$NWIN" | tr '/' '\\')"
  echo '{"cwd":"x","session_id":"t"}' | CLAUDE_PROJECT_DIR="$NBS" bash "$DIR/scripts/session-start.sh" > "$TMP/nblk-out.txt" 2> "$TMP/nblk-err.txt"
  grep -q "/nblocked/.agent-memory is incomplete" "$TMP/nblk-out.txt" \
    && echo "  PASS: warning path stays on one line for a backslash path with an n-folder" || { echo "  FAIL: warning path broken by backslash-n in '$NBS'"; FAIL=1; head -3 "$TMP/nblk-out.txt"; }
fi

# Partial write (store dir exists, one SSoT dir or JSON file missing): no seam in the repo - a COPY of
# scripts/ gets a create_memory_structure wrapper that removes one item again. Never touches the repo.
for variant in json dir file empty; do
  rm -rf "$TMP/scripts-copy" "$TMP/partial-$variant"
  cp -r "$DIR/scripts" "$TMP/scripts-copy"
  mkdir -p "$TMP/partial-$variant"
  if [ "$variant" = json ]; then
    RMCMD='rm -f "$1/${MEM_JSON_ARRAY[0]}"'
    EXPECT="missing: context/decisions.json)"
  elif [ "$variant" = file ]; then
    # Not in MEM_JSON_ARRAY: a file create_memory_structure writes itself (DCO #9680, V-002). The wrapper
    # removes it after EVERY call, so the Phase 2 backfill cannot heal it before the hook reports.
    RMCMD='rm -f "$1/iterations/iteration-log.md"'
    EXPECT="missing: iterations/iteration-log.md)"
  elif [ "$variant" = empty ]; then
    # A full disk leaves a 0-byte file behind (the redirect creates it, the write fails): the check
    # must be -s, not -e. The backfill's [ -f ] guard keeps the empty file, so nothing heals it.
    RMCMD=': > "$1/iterations/iteration-log.md"'
    EXPECT="missing: iterations/iteration-log.md)"
  else
    RMCMD='rm -rf "$1/${MEM_DIRS[0]}"'
    EXPECT="missing: identity/)"
  fi
  {
    echo ''
    echo 'eval "orig_$(declare -f create_memory_structure)"'
    echo 'create_memory_structure() { orig_create_memory_structure "$@"; '"$RMCMD"'; }'
  } >> "$TMP/scripts-copy/mem-schema.sh"
  echo '{"cwd":"x","session_id":"t"}' | CLAUDE_PROJECT_DIR="$TMP/partial-$variant" bash "$TMP/scripts-copy/session-start.sh" > "$TMP/partial-out.txt" 2>&1
  RC=$?
  if grep -qF "$EXPECT" "$TMP/partial-out.txt"; then
    echo "  PASS: partial write ($variant) is reported as '$EXPECT'"
  else
    echo "  FAIL: partial write ($variant) not reported as '$EXPECT'"; FAIL=1; head -c 600 "$TMP/partial-out.txt"; echo
  fi
  if grep -q "Newly initialized" "$TMP/partial-out.txt" || grep -q "initialized for" "$TMP/partial-out.txt"; then
    echo "  FAIL: init success reported after a partial write ($variant)"; FAIL=1
  else
    echo "  PASS: no init success message after a partial write ($variant)"
  fi
  [ "$RC" -eq 0 ] && echo "  PASS: exit 0 (fail-soft, $variant)" || { echo "  FAIL: exit $RC ($variant)"; FAIL=1; }
  if grep -q "Agentic OS NOT active" "$TMP/partial-out.txt" && ! grep -q "Agentic OS active |" "$TMP/partial-out.txt"; then
    echo "  PASS: briefing header says NOT active after a partial write ($variant)"
  else
    echo "  FAIL: briefing header does not say NOT active after a partial write ($variant)"; FAIL=1
  fi
done

# Drift guard (DCO #9680, V-002): the hook's write check iterates MEM_JSON_ARRAY and MEM_INIT_FILES,
# so together they must equal the FILES create_memory_structure really creates - in BOTH directions.
# schema_drift runs the function in a temp dir and prints the differences (empty = no drift); an empty
# created or listed set is drift too, else a mem-schema.sh that fails to source would pass silently.
schema_drift() { # <scripts dir>
  # mktemp, not $RANDOM: subshells inherit the RANDOM state, so every $(schema_drift ...) would get
  # the same dir and see the files of the previous call.
  local sdir="$1" work
  work="$(mktemp -d "$TMP/drift-XXXXXX")"
  bash -c 'source "$1/mem-schema.sh"; create_memory_structure "$2/store"; printf "%s\n" "${MEM_JSON_ARRAY[@]}" "${MEM_INIT_FILES[@]}" | grep -v "^$" | sort -u > "$2/listed.txt"' _ "$sdir" "$work" 2>/dev/null
  (cd "$work/store" 2>/dev/null && find . -type f | sed 's|^\./||' | sort -u) > "$work/created.txt"
  touch "$work/listed.txt"
  [ -s "$work/created.txt" ] || echo "created set is empty"
  [ -s "$work/listed.txt" ] || echo "listed set is empty"
  comm -13 "$work/listed.txt" "$work/created.txt" | sed 's/^/created but not listed: /'
  comm -23 "$work/listed.txt" "$work/created.txt" | sed 's/^/listed but not created: /'
  rm -r "$work"
}
DRIFT="$(schema_drift "$DIR/scripts")"
[ -z "$DRIFT" ] && echo "  PASS: MEM_JSON_ARRAY + MEM_INIT_FILES equal the files create_memory_structure creates" \
  || { echo "  FAIL: schema lists drifted from create_memory_structure:"; echo "$DRIFT"; FAIL=1; }
# Negative controls: a mutated COPY must be reported, one per direction.
cp -r "$DIR/scripts" "$TMP/mut-a"; cp -r "$DIR/scripts" "$TMP/mut-b"
{ echo ''; echo 'eval "orig_$(declare -f create_memory_structure)"'; echo 'create_memory_structure() { orig_create_memory_structure "$@"; echo x > "$1/working/drift-probe.txt"; }'; } >> "$TMP/mut-a/mem-schema.sh"
{ echo ''; echo 'MEM_INIT_FILES+=(working/never-created.txt)'; } >> "$TMP/mut-b/mem-schema.sh"
# Capture first, then grep: `schema_drift | grep -q` would let grep close the pipe early and kill the
# function before its cleanup.
DRIFT_A="$(schema_drift "$TMP/mut-a")"; DRIFT_B="$(schema_drift "$TMP/mut-b")"
grep -qF "created but not listed: working/drift-probe.txt" <<< "$DRIFT_A" \
  && echo "  PASS: drift test catches a file created without a list entry" || { echo "  FAIL: drift test missed a created-but-unlisted file"; FAIL=1; }
grep -qF "listed but not created: working/never-created.txt" <<< "$DRIFT_B" \
  && echo "  PASS: drift test catches a list entry without a file" || { echo "  FAIL: drift test missed a listed-but-never-created file"; FAIL=1; }
cp -r "$DIR/scripts" "$TMP/mut-c"; echo 'return 0 2>/dev/null; exit 0' > "$TMP/mut-c/mem-schema.sh"
DRIFT_C="$(schema_drift "$TMP/mut-c")"
grep -qF "created set is empty" <<< "$DRIFT_C" \
  && echo "  PASS: drift test fails on a mem-schema.sh that defines nothing" || { echo "  FAIL: drift test passed on an empty schema"; FAIL=1; }

# ---------------------------------------------------------------------------------------------
# 5.13.0 (F22): safe hook start. No store without git unless the option is set; a fresh store in a git
# repo is excluded locally (.git/info/exclude, no user file touched); the env file is shell-quoted.
# ---------------------------------------------------------------------------------------------
NOOPT=(env -u CLAUDE_PLUGIN_OPTION_AUTO_INIT_NON_GIT)
start_hook() { # <project dir> [VAR=value ...] -> hook stdout+stderr in $TMP/s-out.txt, rc in $RC
  local proj="$1"; shift
  echo '{"cwd":"x","session_id":"t"}' | "${NOOPT[@]}" "$@" CLAUDE_PROJECT_DIR="$proj" bash "$DIR/scripts/session-start.sh" > "$TMP/s-out.txt" 2>&1
  RC=$?
}
# Without git and without the option: no store, exit 0, reason named (plain echo like the other skip branches).
mkdir -p "$TMP/nogit"
start_hook "$TMP/nogit"
[ -e "$TMP/nogit/.agent-memory" ] && { echo "  FAIL: store created without git and without the option"; FAIL=1; } || echo "  PASS: no store without git and without the option"
[ "$RC" -eq 0 ] && echo "  PASS: exit 0 (no git, no option)" || { echo "  FAIL: exit $RC (no git, no option)"; FAIL=1; }
grep -q "no git repo here - skipping auto-init (run /agentic-os:init to create a store)" "$TMP/s-out.txt" \
  && echo "  PASS: the skip reason is named" || { echo "  FAIL: no skip reason"; FAIL=1; head -3 "$TMP/s-out.txt"; }
# The option accepts true, 1 and yes; anything else keeps the default.
for val in true 1 yes; do
  rm -rf "$TMP/nogit/.agent-memory"
  start_hook "$TMP/nogit" "CLAUDE_PLUGIN_OPTION_AUTO_INIT_NON_GIT=$val"
  [ -d "$TMP/nogit/.agent-memory" ] && echo "  PASS: option '$val' creates the store without git" || { echo "  FAIL: option '$val' did not create the store"; FAIL=1; }
done
for val in false 0 ""; do
  rm -rf "$TMP/nogit/.agent-memory"
  start_hook "$TMP/nogit" "CLAUDE_PLUGIN_OPTION_AUTO_INIT_NON_GIT=$val"
  [ -e "$TMP/nogit/.agent-memory" ] && { echo "  FAIL: option '$val' created a store"; FAIL=1; } || echo "  PASS: option '$val' keeps the default (no store)"
done
rm -rf "$TMP/nogit/.agent-memory"
# An existing store in a folder without git is served unchanged, with or without the option (the AI workspace).
mkdir -p "$TMP/nogit-store/.agent-memory"
start_hook "$TMP/nogit-store"
if grep -q '"additionalContext"' "$TMP/s-out.txt" && ! grep -q "no git repo here" "$TMP/s-out.txt"; then
  echo "  PASS: existing store without git still gets its briefing"
else
  echo "  FAIL: existing store without git was skipped"; FAIL=1; head -3 "$TMP/s-out.txt"
fi
# A folder inside a repo is judged by the repo, never by the option: the subdirectory guard stays as it was.
start_hook "$TMP/repo/scripts/work" "CLAUDE_PLUGIN_OPTION_AUTO_INIT_NON_GIT=true"
[ -d "$TMP/repo/scripts/work/.agent-memory" ] && { echo "  FAIL: option created a store in a git subdirectory"; FAIL=1; } || echo "  PASS: option does not override the git subdirectory guard"

# Git toplevel: store created and excluded locally; git does not see it; a second creation adds no second line.
mkdir -p "$TMP/g1"; git -C "$TMP/g1" init -q
start_hook "$TMP/g1"
[ -d "$TMP/g1/.agent-memory" ] && echo "  PASS: git toplevel creates the store without the option" || { echo "  FAIL: git toplevel got no store"; FAIL=1; }
grep -qxF '.agent-memory/' "$TMP/g1/.git/info/exclude" && echo "  PASS: .agent-memory/ is in .git/info/exclude" || { echo "  FAIL: no exclude line"; FAIL=1; }
if git -C "$TMP/g1" status --porcelain --untracked-files=all | grep -q 'agent-memory'; then
  echo "  FAIL: git status still lists the store"; FAIL=1
else
  echo "  PASS: git status does not list the store"
fi
[ -e "$TMP/g1/.gitignore" ] && { echo "  FAIL: the hook created a .gitignore"; FAIL=1; } || echo "  PASS: no .gitignore touched or created"
rm -rf "$TMP/g1/.agent-memory"
start_hook "$TMP/g1"
[ "$(grep -cxF '.agent-memory/' "$TMP/g1/.git/info/exclude")" -eq 1 ] && echo "  PASS: second fresh start leaves the line exactly once" || { echo "  FAIL: exclude line duplicated"; FAIL=1; }
# A pre-existing exclude without a trailing newline keeps its last line intact.
mkdir -p "$TMP/g2"; git -C "$TMP/g2" init -q
printf 'mine.tmp' >> "$TMP/g2/.git/info/exclude"
start_hook "$TMP/g2"
grep -qxF 'mine.tmp' "$TMP/g2/.git/info/exclude" && grep -qxF '.agent-memory/' "$TMP/g2/.git/info/exclude" \
  && echo "  PASS: exclude without trailing newline keeps its own line" || { echo "  FAIL: exclude file mangled"; FAIL=1; }
# Worktree: .git is a file, the rule lands in the shared info/exclude.
mkdir -p "$TMP/g3"; git -C "$TMP/g3" init -q
git -C "$TMP/g3" -c user.email=t@example.invalid -c user.name=t commit -q --allow-empty -m init
git -C "$TMP/g3" worktree add -q "$TMP/g3wt" -b wtbranch
start_hook "$TMP/g3wt"
[ -f "$TMP/g3wt/.git" ] && echo "  PASS: worktree has a .git file" || { echo "  FAIL: worktree .git is not a file"; FAIL=1; }
[ -d "$TMP/g3wt/.agent-memory" ] && echo "  PASS: worktree gets its store" || { echo "  FAIL: worktree got no store"; FAIL=1; }
grep -qxF '.agent-memory/' "$TMP/g3/.git/info/exclude" && echo "  PASS: worktree rule stands in the shared info/exclude" || { echo "  FAIL: worktree rule missing in the shared exclude"; FAIL=1; }
git -C "$TMP/g3wt" status --porcelain --untracked-files=all | grep -q 'agent-memory' && { echo "  FAIL: worktree status lists the store"; FAIL=1; } || echo "  PASS: worktree status does not list the store"
# A repo with a deliberately tracked store: the hook (backfill) and the CLI on the existing store leave the exclude alone.
mkdir -p "$TMP/g4/.agent-memory/working"; git -C "$TMP/g4" init -q
echo '[]' > "$TMP/g4/.agent-memory/working/x.json"
git -C "$TMP/g4" add .agent-memory/working/x.json 2>/dev/null
cp "$TMP/g4/.git/info/exclude" "$TMP/g4-exclude.before"
start_hook "$TMP/g4"
bash "$DIR/scripts/mem-schema.sh" "$TMP/g4/.agent-memory" > /dev/null 2>&1
cmp -s "$TMP/g4/.git/info/exclude" "$TMP/g4-exclude.before" && echo "  PASS: tracked store: exclude unchanged (hook backfill + CLI)" || { echo "  FAIL: exclude changed for a tracked store"; FAIL=1; }
# CLI path (/agentic-os:init): a fresh store in a repo is excluded too, a folder without git still works.
mkdir -p "$TMP/g5" "$TMP/g6"; git -C "$TMP/g5" init -q
bash "$DIR/scripts/mem-schema.sh" "$TMP/g5/.agent-memory" > /dev/null 2>&1; RC=$?
[ "$RC" -eq 0 ] && grep -qxF '.agent-memory/' "$TMP/g5/.git/info/exclude" && echo "  PASS: CLI init excludes a fresh store" || { echo "  FAIL: CLI init did not exclude (rc $RC)"; FAIL=1; }
bash "$DIR/scripts/mem-schema.sh" "$TMP/g6/.agent-memory" > /dev/null 2>&1; RC=$?
[ "$RC" -eq 0 ] && [ -d "$TMP/g6/.agent-memory/identity" ] && echo "  PASS: CLI init in a folder without git still creates the store (the opt-in)" || { echo "  FAIL: CLI init without git (rc $RC)"; FAIL=1; }
# Sourcing the schema (the hook's path) must not run the exclude by itself.
mkdir -p "$TMP/g7"; git -C "$TMP/g7" init -q
bash -c 'source "$1/scripts/mem-schema.sh"; create_memory_structure "$2/.agent-memory"' _ "$DIR" "$TMP/g7" > /dev/null 2>&1
grep -qxF '.agent-memory/' "$TMP/g7/.git/info/exclude" && { echo "  FAIL: create_memory_structure itself wrote the exclude (backfill would too)"; FAIL=1; } || echo "  PASS: create_memory_structure never touches the exclude"

# CLAUDE_ENV_FILE: the memory path is shell-quoted. A path with a space and a command substitution must
# come back exactly and execute nothing when the env file is sourced.
PWNPROJ="$TMP/Mein Projekt \$(touch pwned)"
mkdir -p "$PWNPROJ" "$TMP/envcwd"
ENVF="$TMP/claude-env.sh"; : > "$ENVF"
start_hook "$PWNPROJ" "CLAUDE_PLUGIN_OPTION_AUTO_INIT_NON_GIT=true" "CLAUDE_ENV_FILE=$ENVF"
(cd "$TMP/envcwd" && source "$ENVF" && printf '%s' "$AGENTIC_OS_MEMORY_DIR" > "$TMP/env-value.txt")
[ "$(cat "$TMP/env-value.txt" 2>/dev/null)" = "$PWNPROJ/.agent-memory" ] \
  && echo "  PASS: AGENTIC_OS_MEMORY_DIR survives sourcing with space and \$(...) in the path" || { echo "  FAIL: env value differs: $(cat "$TMP/env-value.txt" 2>/dev/null)"; FAIL=1; }
[ -e "$TMP/envcwd/pwned" ] && { echo "  FAIL: sourcing the env file executed the path"; FAIL=1; } || echo "  PASS: sourcing the env file executed nothing"
grep -q '^export AGENTIC_OS_ACTIVE=true$' "$ENVF" && echo "  PASS: AGENTIC_OS_ACTIVE still exported" || { echo "  FAIL: AGENTIC_OS_ACTIVE missing"; FAIL=1; }

# Manifest: the option is declared as a plain boolean (no options field, so the minimum version stays).
python - "$DIR/.claude-plugin/plugin.json" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8-sig"))
o = (d.get("userConfig") or {}).get("auto_init_non_git")
ok = isinstance(o, dict) and o.get("type") == "boolean" and o.get("default") is False \
    and "options" not in o and o.get("title") and o.get("description")
sys.exit(0 if ok else 1)
PYEOF
[ $? -eq 0 ] && echo "  PASS: plugin.json declares userConfig.auto_init_non_git (boolean, default false)" || { echo "  FAIL: userConfig.auto_init_non_git missing or malformed"; FAIL=1; }

rm -rf "$TMP"
exit $FAIL

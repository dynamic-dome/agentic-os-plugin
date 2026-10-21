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
# a plain non-git directory still auto-inits (the AI workspace is not a repo).
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
  echo "  PASS: non-git directory still auto-inits"
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
    RMCMD='rm -f "$1/quality/quality-score.json"'
    EXPECT="missing: quality/quality-score.json)"
  elif [ "$variant" = empty ]; then
    # A full disk leaves a 0-byte file behind (the redirect creates it, the write fails): the check
    # must be -s, not -e. The backfill's [ -f ] guard keeps the empty file, so nothing heals it.
    RMCMD=': > "$1/quality/quality-score.json"'
    EXPECT="missing: quality/quality-score.json)"
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

rm -rf "$TMP"
exit $FAIL

#!/usr/bin/env bash
# Agentic OS — Global Memory Schema Helpers (4.A)
# ------------------------------------------------
# Pure, sourceable functions backing the global cross-project layer
# (~/.claude-memory/global/). The /agentic-os:sync-context, /agentic-os:maintain and migration
# flows SOURCE this file and call these helpers — the logic lives here (not inline
# in the SKILL prompts) so its invariants get real strip->FAIL unit tests (L11),
# not just marker greps. Tested by tests/test-global-schema.sh.
#
# Design notes:
#   - No side effects. Every function reads args / stdin and writes stdout or sets rc.
#   - Bash has no float math, so float/JSON steps shell out to inline `python` (proven
#     on PATH; chosen over node for consistency with the .agent-memory maintenance path, L1).
#   - set +e safe: callers must not run under `set -e` and rely on rc-returning helpers.
#   - The privacy denylist itself lives in mem-schema.sh (SSoT: MEM_GLOBAL_DENY_TAGS);
#     is_denied() reads that array, it does not redefine it here.

# normalize <string> -> stdout
# Lowercase, trim, strip punctuation, collapse internal whitespace to single spaces.
# Idempotent. Used to build a stable scope key from free-text values.
normalize() {
  printf '%s' "$1" | python -c '
import sys, re
s = sys.stdin.read().lower()
s = re.sub(r"[^a-z0-9\s]", "", s)   # strip punctuation
s = re.sub(r"\s+", " ", s).strip()  # collapse whitespace + trim
sys.stdout.write(s)
'
}

# compute_scope <fact_type> <tags-csv> -> stdout: "<type>|<sorted,normalized,tags>"
# The scope is the conflict key: at most one `active` entry per scope. Tags are
# normalized and SORTED so that "windows,Bash" and "Bash,windows" map to one scope.
compute_scope() {
  local ftype="$1" tags="$2"
  local sorted
  sorted="$(printf '%s' "$tags" | python -c '
import sys
raw = sys.stdin.read()
tags = [t.strip().lower() for t in raw.split(",") if t.strip()]
sys.stdout.write(",".join(sorted(set(tags))))
')"
  printf '%s|%s' "$ftype" "$sorted"
}

# passes_promotion_gate <confidence> <occurrences> <num_source_projects> -> rc 0=pass / 1=fail
# Local->global promotion gate (4.A): all three conditions must hold.
#   confidence >= 0.6  (existing push threshold, unchanged)
#   occurrences >= 3   (existing +0.1-boost trigger, now a hard requirement)
#   |source_projects| >= 2  (NEW — keeps single-project quirks out of the global layer)
passes_promotion_gate() {
  local conf="$1" occ="$2" projs="$3"
  python -c "
import sys
conf, occ, projs = float('$conf'), int('$occ'), int('$projs')
sys.exit(0 if (conf >= 0.6 and occ >= 3 and projs >= 2) else 1)
"
}

# apply_decay <confidence> <age_days> -> stdout: decayed confidence (2 decimals)
# -0.1 per full 90-day step since last recall, floored at 0.3. Never below the floor.
# Decay is applied only by /agentic-os:maintain (manual), never on the read path.
apply_decay() {
  local conf="$1" age="$2"
  python -c "
import sys
conf, age = float('$conf'), int('$age')
steps = age // 90
new = conf - 0.1 * steps
new = max(0.3, new)   # floor — a stale fact decays toward, not past, 0.3
sys.stdout.write(f'{new:.2f}')
"
}

# is_denied <tag> -> rc 0=denied (must never be pushed) / 1=allowed
# Reads MEM_GLOBAL_DENY_TAGS from mem-schema.sh (SSoT). The privacy pre-filter in
# sync-context Push runs this BEFORE the promotion gate, so denied entries never
# reach the global store at all.
is_denied() {
  local tag
  tag="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  local deny
  for deny in "${MEM_GLOBAL_DENY_TAGS[@]}"; do
    [ "$tag" = "$deny" ] && return 0
  done
  return 1
}

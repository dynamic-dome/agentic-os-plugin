---
name: maintain
description: Compacts, archives and integrity-checks the .agent-memory/ store. Script core (memory-thresholds.sh, gc_dirty_markers.py, native_memory_audit.py, review_sweep.py, extract_patterns.py --refresh); prose only where a threshold is exceeded. Run on demand or when wrap-up / the SessionStart briefing print THRESHOLD lines.
disable-model-invocation: true
allowed_tools: ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]
---

# Memory Maintenance

Compacts and verifies `.agent-memory/`. Never part of the end-of-session flow — wrap-up
prints the `THRESHOLD:` lines and recommends this command; it does not run it.

## Preconditions

- `.agent-memory/` exists — otherwise print "Memory system not initialized — run
  `/agentic-os:init` first" and stop.
- Snapshot first (this run mutates the store): follow
  `${CLAUDE_PLUGIN_ROOT}/references/pre-run-commit.md` with commit message
  `chore(memory): pre-run snapshot vor maintain`.

## Step 1: Mechanical pass (scripts own these — run, do not re-derive)

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/memory-thresholds.sh" .agent-memory; echo "thresholds exit=$?"
python "${CLAUDE_PLUGIN_ROOT}/scripts/gc_dirty_markers.py" .agent-memory            # preview
python "${CLAUDE_PLUGIN_ROOT}/scripts/gc_dirty_markers.py" .agent-memory --apply    # delete safe markers
python "${CLAUDE_PLUGIN_ROOT}/scripts/native_memory_audit.py"; echo "audit exit=$?"
python "${CLAUDE_PLUGIN_ROOT}/scripts/review_sweep.py" .agent-memory \
  --native-memory "$(python "${CLAUDE_PLUGIN_ROOT}/scripts/memory_index_projection.py" --print-native-dir .)" \
  --report .agent-memory/working/review-sweep.md
python "${CLAUDE_PLUGIN_ROOT}/scripts/extract_patterns.py" .agent-memory --refresh   # regenerate patterns.md
```

- `memory-thresholds.sh`: exit 0 → nothing to archive, skip Step 3. Exit 10 → each
  `THRESHOLD:` line names file, count, limit and action; Step 3 acts on exactly those.
- `gc_dirty_markers.py`: removes a `working/dirty-*.json` only when it is safe
  (`dirty: false` / `consolidated_at` set, or `updated` older than
  `consolidation-marker.last_wrapup`). It never touches a file with mtime < 30 min
  (live session) or an un-consolidated session without a later wrap-up (recovery
  evidence). Carry the removed count into the report. Never hand-pick these files.
- `native_memory_audit.py`: exit 0 → copy its `**Summary:**` line verbatim into the
  report. Exit 2 (usage/path) or 1 (crash) → one line "native audit failed: …" and
  continue. This is a REPORTER: it never rotates, deletes or edits native stores;
  `warn`/`critical` injection warnings are surfaced, not fixed.
- `review_sweep.py`: emit its one-line result verbatim; any count > 0 → name the report
  path. Keep / supersede / retire are owner decisions taken in Step 5.
- `extract_patterns.py --refresh`: rewrites `patterns/patterns.md` from
  `patterns/patterns.json` (sole writer of both). Do not regenerate it by hand.

## Step 2: JSON integrity

For `iterations/errors.json`, `patterns/patterns.json`, `context/decisions.json`,
`context/open-tasks.json`, `learnings/learnings.json`, `working/current-session.json`,
`working/user-candidates.json`, `identity/user-changelog.json`: attempt to parse. On
failure rename to `{file}.corrupt.bak` (if that exists already: `{file}.corrupt-{YYYYMMDDHHMMSS}.bak`),
recreate with the default (`[]`, or `{}` for `current-session.json`), and report path + parse
error. Count repairs for the report.

## Step 3: Archive what the thresholds flagged (only on exit 10)

For every flagged store file (`iterations/iteration-log.md`, `iterations/errors.json`,
`learnings/learnings.json`, `session-summary.md`, `learnings/learnings.md`): keep the newest
entries within the script's limit, move the rest to `{filename}-archive-{YYYY-MM}.{ext}` in
the same directory (append if this month's archive exists). For `session-summary.md` /
`learnings.md` compress instead of cut: keep the date header, top 5 "What Was Done"
bullets, ALL "Open Items", top 3 "Next Steps", the stats footer; `learnings.md` keeps the
last 12 months and is deduplicated by normalized text (lowercase, stripped punctuation,
collapsed whitespace).

Also delete files directly in `working/` matching `*.py`, `*.tmp`, `*.bak` older than the
`working/` staleness window in `memory-thresholds.sh` (7 days). Never delete
`working/current-session.json`, `working/user-candidates.json`, or any `dirty-*.json`
(Step 1's collector owns those).

## Step 4: Prune stale patterns and superseded decisions

- `patterns/patterns.json`: entries with `last_seen` older than 60 days OR
  `confidence < 0.3` → move to `patterns/patterns-archive-{YYYY-MM}.json`. **Never prune
  `skill_candidate: true`.** Then rerun `extract_patterns.py --refresh` (Step 1 command).
- `context/decisions.json`: archive `status: "superseded"` entries older than 90 days to
  `context/decisions-archive-{YYYY-MM}.json`. Keep every `status: "active"` entry.

## Step 4b: Decay the global layer (global-decay)

Only when `~/.claude-memory/global/` exists. This is the **only** place confidence decays —
never on the read path (session-bootstrap stays read-only). `. "${CLAUDE_PLUGIN_ROOT}/scripts/global-schema.sh"`,
then for each entry in the global `patterns.json` / `learnings.json`:

1. `new_confidence = apply_decay(confidence, days_since(last_relevant))` — **−0.1 per full
   90-day step without recall, floored at 0.3**. Write it back.
2. Decayed `confidence <= 0.3` AND `days_since(last_relevant) > 365` → set
   `lifecycle: "archived"` (the pull-lifecycle filter stops serving it).
3. **Never hard-delete** — archived entries stay for audit, exactly like `superseded` ones.

`last_relevant` is bumped only by a genuine recall, never by this pass and never by a read.

## Step 5: Consistency

1. `.agent-memory/open-tasks.json` at the ROOT must not exist (canonical:
   `context/open-tasks.json`). If both exist: merge into `context/`, delete the root copy.
2. `learnings/learnings.md` header must contain "Auto-generated from learnings.json";
   otherwise regenerate it from `learnings.json`.
3. **soul.md anti-bloat:** if `identity/soul.md` exceeds **80 lines**, warn
   "soul.md is {n} lines (cap 80) — condense; an overlong identity file dilutes its
   effect". Never edit soul.md here (user-owned).
4. `review-sweep.md` counts > 0: list them and ask the owner per item — keep / supersede /
   retire. Apply only what the owner confirms; a supersede goes through the `decisions`
   plan section of `apply_wrapup.py`, never by hand.

## Step 6: Report

```
Memory Maintenance:
  JSON Integrity: {n}/{total} valid ({n_repaired} repaired)
  Thresholds: {ok | n exceeded → archived: {iterations} iterations, {errors} errors, {learnings} learnings}
  Dirty markers GC: {n_removed} removed
  Patterns pruned: {n_stale} stale, {n_low_conf} low-confidence
  Decisions archived: {n}
  Global decay: {n_decayed} decayed, {n_archived} archived | (no global store)
  Native stores: {verbatim **Summary:** line | native audit failed: ...}
  Review sweep: {verbatim one-liner}
  Consistency: {n_issues} issues found, {n_fixed} fixed
```

Stop after the report. Do NOT suggest a commit — the store is not versioned by the target
project; the pre-run snapshot in Preconditions is the rollback point.

## What NOT to Do

- Do NOT modify `identity/soul.md` or `identity/user.md`
- Do NOT prune patterns with `skill_candidate: true`
- Do NOT delete archive files (they are history)
- Do NOT delete `working/dirty-*.json` by hand — `gc_dirty_markers.py` owns the safety rule
- Do NOT write `patterns.md` by hand — `extract_patterns.py --refresh` owns it
- Do NOT combine with wrap-up into one call — wrap-up only recommends this command

---
name: maintain
description: Reports growth and integrity of the .agent-memory/ store - never moves, archives or deletes store entries (D-021). Script core (memory-thresholds.sh, gc_dirty_markers.py preview, native_memory_audit.py, review_sweep.py, extract_patterns.py --refresh, global_decay.py); prose only for JSON repair and owner decisions. Run on demand or when wrap-up / the SessionStart briefing print THRESHOLD lines.
disable-model-invocation: true
allowed_tools: ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]
---

# Memory Maintenance

Reports on and verifies `.agent-memory/`. Never part of the end-of-session flow — wrap-up
prints the `THRESHOLD:` lines and recommends this command; it does not run it.

**(no-archive) — D-021 (owner decision 2026-10-07; widens the 2026-10-06 rule to every store file):
this command never moves, archives, prunes, compacts or deletes store entries** — not
learnings, patterns, decisions, errors, iterations, open tasks or the session summary.
Archive files are invisible to the Atlas RAG; count- and age-based archiving took long-term
and importance>=4 learnings and ready patterns out of retrieval. Growth is reported; what
leaves the active view is decided by the owner and written through a named applier (an
evidenced `superseded_by`), never by a count. Existing `*-archive-*` files stay untouched.

## Preconditions

- `.agent-memory/` exists — otherwise print "Memory system not initialized — run
  `/agentic-os:init` first" and stop.
- Snapshot first (this run mutates the store): follow
  `${CLAUDE_PLUGIN_ROOT}/references/pre-run-commit.md` with commit message
  `chore(memory): pre-run snapshot vor maintain`.

## Step 1: Mechanical pass (scripts own these — run, do not re-derive)

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/memory-thresholds.sh" .agent-memory; echo "thresholds exit=$?"
python "${CLAUDE_PLUGIN_ROOT}/scripts/gc_dirty_markers.py" .agent-memory            # preview only
python "${CLAUDE_PLUGIN_ROOT}/scripts/native_memory_audit.py"; echo "audit exit=$?"
python "${CLAUDE_PLUGIN_ROOT}/scripts/review_sweep.py" .agent-memory \
  --native-memory "$(python "${CLAUDE_PLUGIN_ROOT}/scripts/memory_index_projection.py" --print-native-dir .)" \
  --report .agent-memory/working/review-sweep.md
python "${CLAUDE_PLUGIN_ROOT}/scripts/extract_patterns.py" .agent-memory --refresh   # regenerate patterns.md
```

- `memory-thresholds.sh`: exit 0 → nothing grew past a limit. Exit 10 → each `THRESHOLD:`
  line names file, count and limit; Step 3 reports exactly those. No line is an order to
  move anything.
- `gc_dirty_markers.py` runs as **preview only** (no apply flag): a headless-consolidated
  marker is still the last trace of a session whose knowledge was never harvested, and
  the harvest ledger that would keep that trace does not exist yet. Carry the "would
  remove" count into the report. Never hand-pick or delete these files.
- `native_memory_audit.py`: exit 0 → copy its `**Summary:**` line verbatim into the
  report. Exit 2 (usage/path) or 1 (crash) → one line "native audit failed: …" and
  continue. This is a REPORTER: it never rotates, deletes or edits native stores;
  `warn`/`critical`/`truncated` injection warnings are surfaced, not fixed. The level is the
  share of Claude Code's load limit (first 200 lines or 25 000 bytes, whichever is hit first):
  `truncated` means part of that MEMORY.md is never loaded — name those stores explicitly.
- `review_sweep.py`: emit its one-line result verbatim; any count > 0 → name the report
  path. Keep / supersede / retire are owner decisions taken in Step 5.
- `extract_patterns.py --refresh`: rewrites `patterns/patterns.md` from
  `patterns/patterns.json` (sole writer of both). Do not regenerate it by hand.

## Step 2: JSON integrity

For `iterations/errors.json`, `patterns/patterns.json`, `context/decisions.json`,
`context/open-tasks.json`, `learnings/learnings.json`, `working/current-session.json`,
`working/user-candidates.json`, `identity/user-changelog.json`: attempt to parse as
`utf-8-sig` (a UTF-8 BOM is not corruption). The writers refuse to touch an unreadable
file (exit 2, file unchanged), so this step is the ONLY repair path — show the owner the
parse error first (it names line and column). **Repair in place** with the owner's OK: a
truncated tail, a stray comma or a wrong top-level shape is fixed in the file itself, so every
row and id survives. Only if the owner confirms the content is unrecoverable: keep it as
`{file}.corrupt-{YYYYMMDDHHMMSS}.bak`, start the file with its default (`[]`, or `{}` for
`current-session.json`) and say in the report that ids held only by the `.bak` can be issued
again. Count repairs for the report.

## Step 3: Report what the thresholds flagged (only on exit 10)

(no-archive) Every flagged store file is **reported, never cut**: name file, count and
limit in the report. Nothing goes into an archive file, nothing is compressed — the store
grows on purpose until the condensation path (rules + pointers) carries the load. The next
wrap-up rewrites `session-summary.md` on its own.

`learnings/learnings.md` is a projection of `learnings.json`. When it is flagged or its
header is missing, re-render it:
`python "${CLAUDE_PLUGIN_ROOT}/scripts/apply_wrapup.py" .agent-memory --render-learnings`.

Scratch files directly in `working/` matching `*.py` or `*.tmp` and older than 7 days may be
deleted after the owner confirms the list. Never delete `*.bak` (evidence, e.g. a
quarantined store file), `working/current-session.json`, `working/user-candidates.json` or
any `dirty-*.json`.

## Step 4: Report stale patterns and superseded decisions

(no-archive) Report only — both stay in place:
- `patterns/patterns.json`: list entries with `last_seen` older than 60 days or
  `confidence < 0.3`, with their `promotion_status` and whether a learning references them.
  `last_seen` is fed by errors.json alone, so "stale" is a hint, not a verdict;
  ready patterns, skill candidates and referenced patterns are explicitly no candidates.
- `context/decisions.json`: list `status: "superseded"` entries; they are history and stay.
  Every reader already filters a superseded decision.

## Step 4b: Decay the global layer (global-decay)

Only when `~/.claude-memory/global/` exists. Since 2026-10-05 the layer is decommissioned
(`STILLGELEGT.md`, G-05): the script then prints `global-decay: skipped (stillgelegt …)`,
writes nothing and exits 0 — carry that line into the report. This is the **only** place confidence decays —
never on the read path (session-bootstrap stays read-only). The script owns the rule — run it,
do not compute or write decayed values by hand:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/global_decay.py" ~/.claude-memory/global           # preview
python "${CLAUDE_PLUGIN_ROOT}/scripts/global_decay.py" ~/.claude-memory/global --apply   # write
```

What it does to the global `patterns.json` / `learnings.json`:

1. **−0.1 per full 90-day step since `last_relevant`, floored at 0.3** (a value already
   below the floor is never raised). Each step is booked exactly once:
   `decay_steps_applied` counts the steps already subtracted, `decay_anchor` names the
   `last_relevant` they were counted from — only the delta is subtracted, so a second run
   in the same quarter changes nothing. A recall moves `last_relevant`; the anchor then no
   longer matches and the count restarts.
2. Decayed `confidence <= 0.3` AND `last_relevant` older than 365 days → set
   `lifecycle: "archived"` (the pull-lifecycle filter stops serving it).
3. **Never hard-delete** — archived entries stay for audit, exactly like `superseded` ones.

Carry its first line (`global-decay: … decayed, … archived, … unchanged, … skipped`) into the
report; `skipped` > 0 means entries whose `last_relevant`, `confidence` or
`decay_steps_applied` cannot be evaluated (they never decay) — list the ids it prints.
Exit 1 (unreadable store) writes nothing — report it and continue. `last_relevant` is bumped
only by a genuine recall, never by this pass and never by a read.

## Step 5: Consistency

1. `.agent-memory/open-tasks.json` at the ROOT must not exist (canonical:
   `context/open-tasks.json`). If both exist: merge into `context/`, delete the root copy.
2. `learnings/learnings.md` header must contain "Auto-generated from learnings.json";
   otherwise regenerate it with the Step 3 `--render-learnings` command (never by hand —
   the layout is owned by `apply_wrapup.py`).
3. **soul.md anti-bloat:** if `identity/soul.md` exceeds **80 lines**, warn
   "soul.md is {n} lines (cap 80) — condense; an overlong identity file dilutes its
   effect". Never edit soul.md here (user-owned).
4. `review-sweep.md` counts > 0: list them and ask the owner per item — keep / supersede /
   retire. There is no write path for learnings yet (planned: a `learning_updates` applier);
   until then record the owner's answers in the report and change nothing by hand. A
   superseded **decision** goes through the `decisions` plan section of `apply_wrapup.py`.

## Step 6: Report

```
Memory Maintenance:
  JSON Integrity: {n}/{total} valid ({n_repaired} repaired)
  Thresholds: {ok | n exceeded → reported, nothing moved: <file count/limit, …>}
  Dirty markers GC (preview): {n_would_remove} would be removed, 0 removed
  Patterns (report): {n_stale} stale, {n_low_conf} low-confidence, 0 moved
  Superseded decisions (report): {n}, 0 moved
  Global decay: {n_decayed} decayed, {n_archived} archived | skipped (stillgelegt) | (no global store)
  Native stores: {verbatim **Summary:** line | native audit failed: ...}
  Review sweep: {verbatim one-liner}
  Consistency: {n_issues} issues found, {n_fixed} fixed
```

Stop after the report. Do NOT suggest a commit — the store is not versioned by the target
project; the pre-run snapshot in Preconditions is the rollback point.

## What NOT to Do

- Do NOT modify `identity/soul.md` or `identity/user.md`
- Do NOT prune patterns with `skill_candidate: true`
- Do NOT move, archive, prune or compress any store entry (D-021) — report it
- Do NOT delete archive files (they are history)
- Do NOT delete `working/dirty-*.json` by hand — `gc_dirty_markers.py` owns the safety rule
- Do NOT write `patterns.md` by hand — `extract_patterns.py --refresh` owns it
- Do NOT combine with wrap-up into one call — wrap-up only recommends this command

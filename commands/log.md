---
name: log
description: Log one coding iteration (feature, bugfix, refactor, config, docs, test) with its errors, tags and learnings into .agent-memory/iterations/ through scripts/apply_wrapup.py — the single writer of iteration-log.md, errors.json and working/current-session.json.
argument-hint: "[one-line summary of the iteration]"
disable-model-invocation: true
allowed_tools: ["Read", "Bash", "Glob", "Grep"]
---

# Log Iteration

Mid-session logging of ONE unit of work. Not for session end — wrap-up Step 1.5 harvests
unlogged work into its own write plan and does not call this command.

## Step 1: Gather the iteration (judgment part)

From `$ARGUMENTS` (the summary, if given) and the conversation:

1. **type**: `feature` | `bugfix` | `refactor` | `config` | `docs` | `test`
2. **title**: one line (from `$ARGUMENTS` when present)
3. **files_changed**: `git diff --name-only HEAD` plus untracked files you created
   (`git status --porcelain | awk '$1=="??"{print $2}'`); if not a git repo, list from the
   conversation
4. **tags**: at least 2, lowercase — language/framework · domain · error type · pattern.
   Reuse tags already present in `.agent-memory/iterations/errors.json`
   (`grep -o '"tags": \[[^]]*\]' .agent-memory/iterations/errors.json | sort | uniq -c | sort -rn | head`).
5. **confidence**: 1–5 (how sure the change is correct); **tests**: `passed (n/n)` |
   `failed` | `skipped` | `not applicable`
6. **errors** (only if any occurred): one object per DISTINCT error with `category`
   (`runtime|test|build|config|logic|import|type`), `tags`, `trigger`, `problem`,
   `root_cause`, `fix`, `failed_approaches` (list), `prevention`, `severity`
   (`critical|major|minor`), `attempts`, `confidence`. Do NOT set `id`, `date`,
   `occurrences`, `recurrence_dates`, `last_seen` — the script owns them.
7. **learnings**: one non-obvious insight or empty string (wrap-up promotes or discards it)

**Counting rule:** distinct approaches, not edits. Three failed fixes before the right one
= ONE iteration with `attempts: 3`. Skip trivia (typos, whitespace) unless part of a
larger iteration.

## Step 2: Write through the script (mechanical part)

Session id: the tracker file of THIS session is the newest `.agent-memory/working/dirty-*.json`
whose `updated` is within the last 30 minutes — take `<sid>` from its filename. If there is
none, omit `--session-id`.

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/apply_wrapup.py" .agent-memory --session-id <sid> <<'PLAN'
{"date": "YYYY-MM-DD",
 "iterations": [{"type": "bugfix", "title": "...", "tags": ["python", "import-error"],
                 "files_changed": ["a.py"], "summary": "...", "confidence": 4,
                 "tests": "passed (12/12)", "learnings": "", "commits": "",
                 "errors": []}]}
PLAN
```

The script assigns ids in the format already on disk (`err-00n`), applies the recurrence
rule (same `category` AND ≥ 2 overlapping `tags` → `occurrences++` on the existing error
instead of a new entry), renders the `## {date} — {type}: {title}` block, and appends new
error ids to `working/current-session.json`. An identical header on a re-run is skipped,
so applying the same plan twice is safe. Use `--dry-run` first when unsure.

## Step 3: Confirm

From the returned JSON:

```
Iteration logged: {type} — {title}
  Files: {len(files_changed)} | Errors: +{tally.errors_added} new, {tally.errors_recurred} recurrence(s) | Confidence: {confidence}/5
  Tags: {tags}
```

`tally.iterations_logged == 0` with `iterations_skipped_duplicate == 1` → say "already
logged (identical header)". Exit code 2 → print the script's JSON error verbatim and read
its `files_written` list: `plan rejected` means nothing was written; `io error` means the
files listed there WERE written before the failure (the script stops there, the
consolidation marker is skipped and dirty flags stay set — nothing to undo by hand).

This command never touches the identity queue: `apply_wrapup.py` runs the
`user_candidates` / `soul_candidates` appliers only for plans that carry those sections or
`consolidate: true` — identity growth stays wrap-up Step 6's job.

Then: `n=$(grep -c '^## ' .agent-memory/iterations/iteration-log.md)` — if `n % 5 == 0`,
suggest `python "${CLAUDE_PLUGIN_ROOT}/scripts/extract_patterns.py" .agent-memory --update`
(wrap-up Step 4 runs it anyway). If an error recurred for the 3rd+ time, say so — it is an
anti-pattern candidate for pattern-extractor.

## What NOT to Do

- Do NOT write `iteration-log.md`, `errors.json` or `current-session.json` with Write/Edit
- Do NOT modify `patterns.json` (`scripts/extract_patterns.py`) or `decisions.json`
  (`context-keeper` via the `decisions` plan section)
- Do NOT push to global memory (`/agentic-os:sync-context`)
- Do NOT count individual file saves as attempts

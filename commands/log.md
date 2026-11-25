---
name: log
description: Log one coding iteration (feature, bugfix, refactor, config, docs, test) with its tags and learnings into .agent-memory/iterations/ through scripts/apply_wrapup.py — the single writer of iteration-log.md and working/current-session.json.
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
   Reuse tags already present in `.agent-memory/iterations/iteration-log.md`
   (`grep -h '^- \*\*Tags:\*\*' .agent-memory/iterations/iteration-log.md | sort | uniq -c | sort -rn | head`).
5. **confidence**: 1–5 (how sure the change is correct); **tests**: `passed (n/n)` |
   `failed` | `skipped` | `not applicable`
6. **summary**: what changed and why; a root cause worth keeping goes here (the error log
   `errors.json` is frozen data since 6.0.0 — an `errors` field is ignored and counted in
   `tally.ignored_sections`, nothing is written for it)
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
                 "tests": "passed (12/12)", "learnings": "", "commits": ""}]}
PLAN
```

The script renders the `## {date} — {type}: {title}` block and keeps
`working/current-session.json` in step. An identical header on a re-run is skipped,
so applying the same plan twice is safe. Use `--dry-run` first when unsure.

## Step 3: Confirm

From the returned JSON:

```
Iteration logged: {type} — {title}
  Files: {len(files_changed)} | Confidence: {confidence}/5
  Tags: {tags}
```

`tally.iterations_logged == 0` with `iterations_skipped_duplicate == 1` → say "already
logged (identical header)". Exit code 2 → print the script's JSON error verbatim and read
its `files_written` list: `plan rejected` means nothing was written; `io error` means the
files listed there WERE written before the failure (the script stops there, the
consolidation marker is skipped and dirty flags stay set — nothing to undo by hand).

`tally.ignored_sections` not empty → name it (`ignored_status_line`): the plan carried a section
of an older version that wrote nothing.

This command never touches the identity files: an iterations-only plan writes no profile line.

## What NOT to Do

- Do NOT write `iteration-log.md` or `current-session.json` with Write/Edit (`errors.json` is frozen
  data since 6.0.0: nothing writes it)
- Do NOT modify `patterns.json` (frozen data since 6.0.0) or `decisions.json`
  (`context-keeper` via the `decisions` plan section)
- Do NOT count individual file saves as attempts

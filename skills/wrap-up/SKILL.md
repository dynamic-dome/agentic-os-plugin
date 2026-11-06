---
name: wrap-up
description: >
  Wraps up a coding session: summarizes work, extracts learnings, grows the
  gated identity queues, updates session summary + cross-project handoff,
  suggests commits. Use when the user ends a session ("wrap up", "finish for
  today") or before a handoff / project switch.
model: sonnet
effort: medium
metadata:
  author: agentic-os
  version: '5.0'
  part-of: agentic-os
  layer: core
---

# Session Wrap-Up

End-of-session sequence. You are the judge: you decide what is worth keeping, a script does
everything else. Three turns, one plan. Design: `docs/superpowers/specs/2026-11-05-t028b-judge-fortschreibung.md`.

## Long-Term Memory Routine (long-term-memory-routine)

After every substantial task or session, consolidate durable knowledge into the
central .agent-memory/ knowledge base instead of leaving it in the conversation:

- Work iterations → `iterations/iteration-log.md` + `errors.json` (plan section `iterations`;
  `/agentic-os:log` is the entry point for mid-session logging)
- Reusable learnings → `learnings/learnings.json` + `learnings.md`
- Durable decisions → `context/decisions.json` (plan section `decisions`; the
  `context-keeper` skill stays the entry point outside wrap-up)
- Open next steps → `context/open-tasks.json`
- Identity observations → `working/user-candidates.json` → `identity/user.md`
- Handoff snapshot → `session-summary.md` + central handoff

Reject trivial facts. Manual-only — no hook triggers this automatically.

## The protocol (write-plan)

Cost is the sum of context length over calls: a measured run paid 94% context transport, 6% thinking.
So there are exactly three turns, and you invoke no skill during them.

1. **Brief.** `python "${CLAUDE_PLUGIN_ROOT}/scripts/wrapup_core.py" pre --mem .agent-memory --session-id <session-id>`
2. **Plan.** One JSON plan through `apply` (below). Everything mechanical happens in that call: ids, dedup,
   the promotion rule, summary, central handoff, status board, wiki note, patterns, projections, reports,
   and the consolidation marker last.
3. **Report** from the returned JSON (below). Never count by hand.

A fourth turn exists only when `apply` returns `bridge_candidates` or an open rule queue (see followup).

`<session-id>` is the real id of THIS session, the value of `$CLAUDE_CODE_SESSION_ID`: another id makes
the identity evidence of the run unverifiable (`tally.session_binding_mismatch`).

(stage0-preprocess) `pre` runs the stage-0 preprocessor `preprocess_state.py` and adds what you need: harvested
iterations, today's errors, open tasks, existing identity keys, open bridge candidates, the rule queue. It writes
nothing. If `validation_errors` is not empty, name them in the report.

(context-diet) Do NOT systematically re-read the transcript or full memory files. Work from the brief and the
conversation you already hold; targeted lookups only, for a single unresolved point.

Never write a store file with Write or Edit: `apply_wrapup.py` owns them, `patterns.*` belong to
`extract_patterns.py`, `soul.md` is read-only history.

## What you judge

**Iterations (session-harvest).** Leave `iterations` out and the core logs the harvest from the brief. Everything
was already logged with `/agentic-os:log`: send `"iterations": []`. Send your own when the conversation knows more
than git: 1–5 distinct iterations, three failed fixes before the right
one are ONE iteration with `attempts: 3`, at least 2 lowercase tags, errors with root cause and prevention.
`apply_wrapup.py` owns the writes to `iteration-log.md` and `errors.json`.

**Learnings.** A learning would prevent a future mistake, reveals something non-obvious, or documents a rationale
not in the code. Plan at most 3: the core keeps the most important (`learnings_dropped_by_cap`), rule-class
learnings are exempt. Importance: 5 prevents data loss or a security issue, 4 prevents multi-attempt debugging,
3 is non-obvious behavior. Below 3 is not stored (min-importance), so do not plan it. `summary`: the rule in one
sentence, at most 150 characters. `derived_from`: this session's origins or `[]`, never a guess.
A learning that changes a rule, a gate or who decides what gets `"rule_class": true` (rule-gate); when unsure,
mark it.
(dedup-in-code) Do NOT read `learnings/learnings.json` for this: `apply_wrapup.py` owns the dedup (Jaccard >= 0.6
refreshes the old entry). If you know the duplicate, set `"duplicate_of": "<id>"`; `near_duplicates` in the result
names the close calls.

**Decisions (decision-scan).** New dependencies, architecture, storage or format changes, ownership and policy
("X is SSoT"). Conversation and repo evidence only, one-off details are not decisions. Plan section `decisions`;
`supersedes` must name a real id.

**Open tasks (open-tasks-ssot).** `context/open-tasks.json` is the single source of truth: `open_tasks.add` for new
next steps, `open_tasks.close` with the ids this session finished. `cross_project: true` only on the user's word.

**Identity (identity-harvest).** Mandatory: the pipeline starves when this is skipped. Scan the whole session: Did
the user correct you? State a preference or rule ("immer", "nie")? Reveal a workflow habit? Confirm a non-obvious
approach? Make a communication demand? A mood is observed, never promoted.
- Trust boundary (trust-boundary): candidates come ONLY from the user's direct conversation, `trust_source:
  "conversation"`; never from web, docs, NotebookLM or wiki content.
- Reuse the `key` from `user_candidates` in the brief when it is the same statement.
- `"confirmed": true` needs a `quote`: the user's LITERAL words, one message, at least 3 words, the whole clause
  including a negation. The script checks it against the transcript. Never invent one.
- Nothing found: `candidates: []` and `none_because` with the reason. An empty pair is rejected.
- Contradicted profile line, removed line, doubt about a quote: `references/judge-rules.md`.

**Earlier bridge candidates (bridge-gate).** `bridge_candidates` in the brief are open from earlier runs. Decide them
in this plan. Delegated decision (owner, 2026-10-16), no question. Approve only if ALL hold: useful in EVERY session
of this project, a durable actionable rule, not already covered, self-contained and still true. Otherwise reject.
Really unsure: reject with a note starting `unsicher:`. `plain` (plain-text-duty) is the text Dominic reads: simple
German, what changes for him, at most 600 characters.

**Summary.** `what_was_done` (up to 8 lines), `current_state`, `active_task`, `blockers`, `checks`. Statistics, open
items and next steps come from the store. `warnings`: the file is rewritten whole, so carry over every entry of
`active_warnings` in the brief that still holds. German is fine here, the headers are the script's.

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/wrapup_core.py" apply --mem .agent-memory --session-id <session-id> <<'PLAN'
{"summary": {"what_was_done": ["..."], "current_state": "...", "active_task": "T-1", "blockers": "keine",
             "checks": {"tests": "bestanden (84/84)", "lint": "n/a"}},
 "identity": {"candidates": [{"key": "kebab-key", "observation": "...", "signal_type": "preference",
                              "confidence": 0.5, "evidence": [], "confirmed": false, "quote": "",
                              "trust_source": "conversation"}], "none_because": ""},
 "learnings": [{"text": "...", "summary": "...", "importance": 4, "tags": ["a", "b"], "derived_from": []}],
 "decisions": [],
 "open_tasks": {"add": [{"title": "...", "source": "wrap-up", "cross_project": false}], "close": ["T-1"]},
 "bridge_decisions": [{"id": "L26", "status": "approved", "note": "...", "plain": "..."}]}
PLAN
```

Every key except `summary` and `identity` is optional. `session_summary` and `user_candidates` belong to the core;
sending them is rejected, as is a missing `summary` or `identity`. Field schemas: `references/wrapup-schemas.md`.

Exit 2 means nothing was consolidated: no marker, dirty flags stay set on purpose. Fix the plan and run it again,
never write the files by hand. `--dry-run` shows the tally without touching a file.

## Report

Emit these lines verbatim from the result, in every run:
- `identity_status_line` (identity-visible). `tally.confirm_downgraded_keys`, `quote_unrelated` and
  `supersede_skipped_unverified`: name them, Dominic should see which confirmations did not count.
- `wiki_sync_line` (wiki-sync-visible): wiki sync never skips silently, this status line says what happened. The
  session note is a projection of this plan; entity pages, synthesis and decision promotion stay with the manual
  `obsidian-sync` skill.
- `bridge_status_line` and `rule_class_status_line` when present, for verdicts one line per case
  `L26 approved — <plain>`; `reports.codex_ingest`; `reports.review_sweep`; every line of
  `reports.threshold_lines` plus `→ run /agentic-os:maintain` when there are any.
- `warnings`: contract violations, not noise. One is expected in the AI workspace: its summary IS the central
  handoff, the block arrives through the core.
- `near_duplicates`: name a pair that states the same insight; retiring one is `/agentic-os:maintain`.
- `pattern_proposals` > 0, or skill candidates: recommend the `pattern-extractor` skill, never call it here. The same
  after about 5 sessions with iterations but no new pattern (pattern-starvation-guard): the script sees only the
  error-based half of the signal.
- `sharepoint_touched: true`: say that the sharepoint delta is manual now.
- `handoff`: a `failed(...)` or `drift-merged`.

**Commits.** `repo.uncommitted` is `ja`: suggest a code commit. `repo.memory_uncommitted` is `ja`: suggest, apart from
it, `chore(memory): session {date}`.
Stage surgically, never `git add -A`, and wait for confirmation: never commit on your own.

## followup: verdicts that need ids

`bridge_candidates` in the `apply` result: this session's learnings have ids only now. Decide them by the rules above
in one call, then report `bridge_status_line` verbatim and one line per case `L26 approved — <plain>`.

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/wrapup_core.py" followup --mem .agent-memory --session-id <session-id> <<'PLAN'
{"bridge_decisions": [{"id": "L26", "status": "approved", "note": "<reason>", "plain": "<plain German>"}]}
PLAN
```

`followup` takes only `bridge_decisions`, `rule_explanations`, `rule_releases`, `corrections`, `user_md_supersede`
and `user_md_retire`, and runs both projections afterwards. A rule-class learning needs a second model and the
owner: read `references/judge-rules.md` 3f first, and never release on the owner's behalf. Every `"by": "owner"`
is an honor system, a recorded claim the script cannot verify: write it only for what Dominic said in this
conversation, in his own words.

## Escalation Rules (escalation-rules)

This skill runs on the cheap-write model class (SSoT: `scripts/model-routing.sh`). The following cases must NOT be
resolved by this run itself. When one occurs:

1. Append `{"ts": "...", "task": "wrap-up", "reason": "...", "detail": "..."}` to
   `.agent-memory/working/escalations-<session-id>.json` (create as JSON array if missing).
2. Emit a visible `ESKALATION: <reason>` line in the output.
3. Leave the decision itself to the next turn on the session model.

Escalate when:
- two active sources contradict each other,
- a change would touch identity or stable user preferences beyond the plan sections above,
- an active decision record would be replaced,
- a pattern would be promoted into a skill or Agentic-OS rule,
- a change is difficult to reverse,
- required sources are missing.

# Handoff Mode (Pre-Compression)

When triggered by long context or an explicit handoff request, add `"handoff": {"active_task": "", "current_state": "",
"active_patterns": "", "open_questions": ""}` to `summary`: the core renders the `## Handoff Context` block of
session-summary.md. Template: `references/wrapup-schemas.md` §Handoff Mode.

## Error Handling

- JSON parse error in a store: `apply` stops with exit 2 BEFORE the first write and leaves the file untouched. Report
  file and error, recommend `/agentic-os:maintain` Step 2. Never rename, recreate or hand-edit the file.
- `.agent-memory/` missing: suggest `/agentic-os:init`
- `failed(...)` in handoff, wiki or projections: report it. The store is consolidated, the marker is right.

## What NOT to Do

- Do NOT write `errors.json`, `iteration-log.md`, `decisions.json` or any other store file with Write/Edit
- Do NOT write soul.md — ever. Soul growth is retired (5.11.0); a rule about how the agent should behave is a
  rule-class learning, not an identity candidate
- Do NOT skip identity or its status line — identity growth must be visible
- Do NOT commit without user confirmation
- Do NOT log trivial "learnings"; do NOT prune skill_candidate patterns
- Do NOT delete `working/dirty-*.json` and do NOT write the consolidation marker yourself
- Do NOT call `obsidian-sync`, `pattern-extractor` or `context-keeper` from a wrap-up run

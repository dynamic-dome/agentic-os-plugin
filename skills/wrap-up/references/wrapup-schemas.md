# wrap-up — write-time schemas & templates

Inert JSON schemas and output templates lifted out of `skills/wrap-up/SKILL.md`
under the T-35 redesign rule: **no gate logic leaves the body**. These blocks are
read only when the agent actually WRITES the corresponding artifact — every gate's
trigger and result-contract stay in the body, which points here and loads this file
only at the write step it names.

Design: `memskillredesign.md` / `memevalharness.md` (membrain). The wrap-up gate
inventory (`gate_linkage.py`, 28 gates) and `validate-skills.sh` anchors stay in the
body, never here.

## Write plan (batch writer)

`scripts/apply_wrapup.py` applies every mutation below in ONE pass. Emit this
object once, pipe it into the script, read the returned tally (exception:
`bridge_decisions` travels in its own call after it, see below) — do not write
these files with individual Write/Edit calls (that is what made wrap-up cost
28 API calls per run, each resending the full session context — the
model is stateless, so cost is the sum of context length over calls).

```json
{
  "date": "{YYYY-MM-DD}",
  "session_id": "{sid}",
  "iterations":      [ { "type": "feature", "title": "...", "tags": [], "files_changed": [],
                         "summary": "...", "confidence": 5, "tests": "passed (n/n)",
                         "learnings": "...", "commits": "abc1234",
                         "recovered_from": null,
                         "errors": [ { "category": "runtime", "tags": [], "trigger": "...",
                                       "problem": "...", "root_cause": "...", "fix": "...",
                                       "failed_approaches": [], "prevention": "...",
                                       "severity": "major", "attempts": 1,
                                       "confidence": 4 } ] } ],
  "decisions":       [ { "type": "architecture-decision", "title": "...", "context": "...",
                         "options_considered": [ { "option": "...", "pros": [], "cons": [] } ],
                         "decision": "...", "consequences": "...",
                         "supersedes": null, "tags": [] } ],
  "learnings":       [ { "text": "...", "summary": "...", "importance": 3, "tags": [], "derived_from": [],
                         "duplicate_of": null } ],
  "restore":         { "source": "restore_plan.py", "learnings": [ "...rows from restore_plan.py..." ] },
  "bridge_decisions": [ { "id": "L26", "status": "approved", "note": "...", "plain": "...", "by": "agent" } ],
  "corrections":     { "tasks": [ { "id": "T-116", "title": "...", "reason": "...", "by": "agent" } ],
                       "learnings": [ { "id": "L154", "text": "...", "summary": "...", "reason": "...", "by": "agent" } ] },
  "rule_explanations": [ { "id": "L26", "explained_by": "<model>", "plain": "...", "risk": "...",
                           "verdict": "confirms", "affects": [] } ],
  "rule_releases":   [ { "id": "L26", "by": "owner", "two_sentences": "..." } ],
  "user_candidates": [ { "key": "kebab-key", "observation": "...", "signal_type": "preference",
                         "confidence": 0.5, "evidence": [], "confirmed": false, "quote": "",
                         "status": "observed", "trust_source": "conversation" } ],
  "user_md_supersede": [ { "line_id": "UC12", "match": null, "new_text": "- ...", "reason": "...",
                           "plain": "...", "quote": "the user's literal words", "by": "agent",
                           "trust_source": "conversation" } ],
  "principles":      [ { "summary": "...", "anchor": "<file>#<heading>", "members": ["L12", "L40"],
                         "plain": "...", "tags": [], "member_sha": null } ],
  "principle_retire": [ { "ids": ["L90"], "reason": "..." } ],
  "soul_candidates": [ { "proposal": "...", "evidence": [] } ],
  "open_tasks":      { "add": [ { "title": "...", "source": "wrap-up", "cross_project": false } ],
                       "close": ["T-001"] },
  "session_summary": { "what_was_done": [], "open_items": [], "next_steps": [],
                       "statistics": { "iterations": 0, "errors": 0, "new_patterns": 0 },
                       "warnings": [],
                       "handoff": { "active_task": "", "current_state": "",
                                    "active_patterns": "", "open_questions": "" } },
  "consolidate": true,
  "iterations_logged": 0
}
```

Every key is optional — omit what the session did not produce. The script owns
all deterministic rules and needs no help with them: id assignment continuing
whatever format is already on disk (`L{n}` / `UC{n}` / `T-{00n}` / `err-{00n}` /
`D-{00n}` — never assume, the real files drifted from their templates),
the iteration markdown shape, the recurrence rule (same category AND ≥2
overlapping tags), the decision supersede flip, `review_after` = date + 90d,
exact-text dedup,
the `signal_type: mood` block, the promotion rule (`confirmed` OR `inferred`
AND `occurrences >= 2` AND `confidence >= 0.6`), changelog-before-edit
ordering, `learnings.md` regeneration, the bridge-candidate derivation
(`importance >= 4` → `bridge_status: "candidate"`, Step 3d.1 — a plan-supplied
`bridge_status` is dropped; a verdict exists only via `bridge_decisions`, Step 3d.4),
the consolidation marker and the dirty
flags. Supply judgment only: what is a learning, what is an identity signal,
how important, which section.

Guarantees worth relying on:

- `trust_source` other than `conversation` is dropped (Step 6.1 boundary) —
  both when enqueuing a new observation **and** when the full-queue re-review
  considers a row that was already on disk. A poisoned row that somehow
  reached the queue can never be promoted into `user.md`.
- `patterns.json`/`patterns.md` (owned by `scripts/extract_patterns.py`) and
  `soul.md` are refused outright. `iteration-log.md`, `errors.json`,
  `current-session.json` and `decisions.json` are reachable ONLY through their
  named applier (`iterations` / `decisions`) — the generic write path still
  refuses them. An unreadable store file is never renamed or emptied: the run
  stops with exit 2 before the first write (5.2.1).
- A `supersedes` pointing at an unknown decision id rejects the WHOLE plan
  before the first byte is written, so a rejected plan never leaves a
  half-written iteration log behind.
- On a rejected plan or an IO failure the run stops there, **the consolidation
  marker is skipped and dirty flags stay set** (Step 9.5 rule 5), and the
  failure is reported as JSON with exit code 2. `session_id` may be passed in
  the plan or via `--session-id` (the flag wins).
- `--dry-run` reports the full tally without touching a file.
- (5.3.0) `learnings/learnings.json` is applier-owned too (`learnings`; the
  `restore` section uses the same applier). Ownership is checked case-insensitively,
  so `Learnings/Learnings.json` is refused like the canonical path.
- (5.3.0) Every learning the script writes is validated before the first byte:
  `importance` a JSON int 1..5 (no `"5"`, no float, no bool), `tags` a list of
  strings, `derived_from` a list of at most 30 strings, `duplicate_of` a live id.
  Legacy rows are never a gate — `--lint` lists them, read-only.
- (5.3.0, Step 3a) Jaccard >= 0.6 to a live learning is a duplicate (that entry's
  `last_relevant` is refreshed); the top-level `near_duplicates` of the result lists
  live entries at 0.2–0.6 for the `duplicate_of` verdict.
- (5.3.0) `restore`: rows from `scripts/restore_plan.py`, decided against the fresh
  store at apply time — same id + text or same text live → skipped (a second run
  changes nothing); a free archive id is kept; an id taken by another text gets the
  next id plus `legacy:<id>` in `derived_from`; an invalid row is dropped and listed
  in `tally.restore_dropped`, never the whole run. A snapshot is taken first.
- (5.3.0) A writing run holds `working/store.lock` from the first read to the last
  write; a store locked longer than `--lock-timeout` (10 s) is exit 2, nothing written.
- (P1/B2) `confirmed` is earned, not declared. A `user_candidates` entry that claims it
  (`"confirmed": true` or `"status": "confirmed"`) is confirmed at first sight only when its
  `quote` is a substring of ONE genuine user message in the session's transcript
  (`scripts/identity_quote.py`; the transcript is the session's own
  `<HOME>/.claude/projects/*/<session-id>.jsonl`, read-only, resolved at call time; `--transcript <file>`
  is accepted ONLY for exactly that shape `<HOME>/.claude/projects/<dir>/<session-id>.jsonl` (realpath,
  case-folded) - anything else is refused: verdict `"kein_transkript"`, a warning and
  `tally.transcript_override_rejected`). Genuine = a `user` record of THIS session (a record whose
  `sessionId` differs is ignored) that is no tool result, no `isMeta`/compact summary, no subagent prompt,
  no machine-originated record (`origin.kind`, `turnOrigin` other than `human`, `promptSource`
  system/sdk) and no accepted prompt suggestion (`promptSource: suggestion_accepted` - the words are the
  model's); injected blocks (`<system-reminder>`, `<local-command-stdout>`, `<command-name>`,
  `<task-notification>`, `<pasted_content>`, `<ide_selection>`, `<ide_opened_file>`, `<teammate-message>`,
  ...) are cut out first, nested ones until nothing changes, as is the harness line
  `[Request interrupted by user ...]`. Case, whitespace and typographic quotes are normalised. The quote
  must be at least 8 characters AND 3 words long, must start and end on a word boundary, and must not
  stand within 3 words of a negation word in the source message (3 words before and 3 words after the quote;
  a negation INSIDE the quote does not count): nicht, nie, niemals, kein/keine/keinen/keinem/keiner/keines/
  keinesfalls/keinerlei/keineswegs, nein, nö/noe/nee, ohne, weder, nichts, niemand, nirgends, not, never, no,
  without, cannot, nothing, neither, nor, nobody and every `...n't` contraction - also typed without the
  apostrophe (dont, cant, doesnt, ...) or with the acute accent, the grave accent or U+02BC for the
  apostrophe. A negation further away is not seen. **This proves the
  wording, not the meaning:** a substring cannot tell sarcasm, a question or a quoted counter-position from
  a statement; the thresholds (8 characters, 3 words, one message, the negation list and its reach of 3
  words either side) are heuristics the owner calibrates. `tally.quote_reasons` says per candidate key why a quote did not verify
  (`too_short`, `too_few_words`, `word_boundary`, `negation`, `not_found`, `no_transcript`,
  `unreadable`, `override_rejected`, `session_mismatch`). The verdict is stored on the candidate as `quote_verified`
  (`true` | `false` | `"kein_transkript"`; absent without a quote) next to `quote` and
  `confirmed_by` (`quote` | `repeat`). Only `true` confirms at first sight - otherwise the
  candidate stays `observed`/`inferred` and the second mention confirms it as before; a missing
  transcript is not an error. A plan `status` other than `observed`/`inferred`/`confirmed` is not
  honoured (recorded as `observed`, with a warning); `confirmed` is the claim described above.
  `confirmed` must be a JSON bool, `quote` a string (`null` counts as no quote) and `confidence` a
  number, else the plan is rejected. Tally: `quotes_verified`, `quotes_unverified`,
  `quotes_no_transcript`, `quote_transcript` (the path used), `quote_reasons`, `confirm_downgraded`,
  `confirm_downgraded_keys`. The promotion changelog entry carries `quote`, `quote_verified`,
  `confirmed_by`. A verified quote that shares no content word with the observation (or a supersede's
  `new_text`) is not downgraded - it is reported: `tally.quote_unrelated` (candidate keys, supersede
  entries as `supersede:<target>`) and a warning. A content word is a word of 4+ letters that is not a
  function word, umlauts are written out (ä = ae, ö = oe, ü = ue, ß = ss); two words relate when they are
  equal or share a stem (common prefix of 4+ characters covering 80% of the shorter word). The text compared
  is the one that is STORED and later promoted: for a known key the observation of the queue row, not the one
  the plan carries. A verified quote and an observation (or `new_text`) of which only one carries a negation
  word are counted the same way, no downgrade: `tally.quote_polarity` (same key format as
  `quote_unrelated`), a counter without a warning line (a weak hint, calibration data).
- (P1/B2, D3) The transcript is found at `<HOME>/.claude/projects/<dir>/<session-id>.jsonl` only: the same
  rule holds for `--transcript` and for the default lookup (a junction or symlink out of `projects/` is
  no hit). Residual risk by design: a file the agent itself places at that path (a plain write, a hard
  link) cannot be told from a real transcript. A path with a `\\?\` prefix is refused (safe side).
- (P1/B2, D1) The second mention counts only from ANOTHER session. A candidate carries `sessions` (the
  session ids that named it); `occurrences` rises, and the second mention confirms, only when the current
  `--session-id` is not in `sessions`. The same `key` twice in one plan is ONE mention (evidence united, a
  verified quote wins; `tally.plan_duplicates_merged`); the same plan applied twice in one session adds
  nothing (`tally.repeat_not_counted`); an empty session id counts nothing and warns. A legacy row without
  `sessions` keeps its `occurrences`; the first new session adds one. The Step 0 key list
  (`preprocess_state.py --session-id`) is what makes a repeat reuse its key.
  Round 2: a row created without a provable session (empty or blank id, session mismatch) carries
  `sessions: []` - the KEY is there, the list is empty. The first real session takes it in WITHOUT
  raising `occurrences` (`tally.session_adopted`, a warning); the next, different session counts. Only a row
  without the `sessions` key (legacy) follows the plain rule above. A non-list value counts as empty.
  Known limit (migration): a legacy row without `sessions` that the old code created earlier in the very
  session the new code now runs in is counted once and can confirm - the old rows carry no session to compare.
  Known limit: without `CLAUDE_CODE_SESSION_ID` (Codex, tests, headless) there is nothing to compare the id
  against, so two invented ids count as two sessions.
- (P1/B2, D2) If `CLAUDE_CODE_SESSION_ID` is set in the environment and differs from `--session-id`, the
  identity evidence of the run is not verifiable: no quote verifies (`"kein_transkript"`), nothing is
  counted, a warning and `tally.session_binding_mismatch` say so; the rest of the plan runs normally.
  Without that variable (Codex, tests, headless) there is nothing to compare and the given id stands.
- (P1/B2) `identity/user.md`, `identity/user-changelog.json` and `working/user-candidates.json`
  are applier-owned (`identity`): the generic write path refuses them like the other stores.

The returned `identity_status_line` is computed from the writes that actually
happened — use it verbatim for the mandatory Step 6.5 line instead of counting
by hand. Likewise `tally.bridge_candidates` lists ALL open store-wide
`bridge_status: "candidate"` ids (this session's and earlier undecided ones, superseded
rows excluded) — Step 3d.2 decides from it instead of re-reading `learnings.json`.

`bridge_decisions` (Step 3d.4, 5.7.0) is applied in its own call after the main plan,
because the ids of this session's learnings exist only then. Per entry: `id` (a live
learning whose `bridge_status` is `candidate`, `approved` or `rejected`; not superseded,
not a `kind: principle` pointer), `status` `approved`|`rejected`, `note` (required, the
reason), `plain` (required since P1/B2: the owner-facing explanation in plain German, no
jargon, what changes concretely, 1..600 characters once whitespace is collapsed) and optional
`by` `agent` (default) | `owner`. The script sets `bridge_status`, `bridge_note` =
`<date> <by>: <note>` and `bridge_plain` = the collapsed `plain` (its own field: review_sweep
reads the shape of `bridge_note`); one bad entry - also a missing, empty, non-string or
too long `plain` - rejects the whole plan before the first byte. A changed `plain` for the
same verdict is written without counting as a status change. Same verdict, decider and reason again change nothing, also on a later
day (the decision date is kept). The result carries
`tally.bridge_approved`/`bridge_rejected` (ids), `tally.bridge_status_changed`
(projections needed when > 0), the recomputed `tally.bridge_candidates` and a
top-level `bridge_status_line` for the Step 3d.5 report.

`corrections` (Step 3g, 5.8.0) is the only way to change the TEXT of an existing row. Per entry:
`id` (a unique live task / non-principle learning / decision), `reason` (required) and `title` (task) or
`text`/`summary` (learning) or `title`/`context`/`decision`/`consequences`/`options_considered` (decision;
the nested options keep their shape), optional `by` `agent` (default) | `owner` - a recorded claim, never
verified. `user_md` entries are `{old, new, reason}` on one whole line of `identity/user.md`; `new` must be the
deterministic encoding repair of `old` (a reworded line is refused), each repair is logged first in
`user-changelog.json` as `user.md/encoding-correction`. The row keeps
`corrections[]` = `{date, by, session, field, reason, was}`. The same text again changes nothing; a new
text below similarity 0.6 to the old one, a principle pointer, an unknown or ambiguous id rejects the
whole plan before the first byte. Correcting a rule-class learning resets its gate. `tally.tasks_retitled`,
`learnings_corrected`, `decisions_corrected`, `user_lines_corrected`, `corrections_skipped_unchanged`, `correction_ids`, `corrections_projection_needed`.
`--propose-encoding-corrections` (read-only) prints a `corrections` plan for every Mojibake row (tasks, learnings,
decisions, user.md lines); a new
learning/task whose text looks like Mojibake is written but warned about (`tally.warnings`).

`user_md_supersede` (Step 6.3b, P1/B2) replaces ONE line of `identity/user.md` per entry - the way to
resolve a contradiction that the append-only queue cannot. Per entry: exactly one target, `line_id` (the
candidate id the line cites, `(UC12, date)`) or `match` (the exact line text); it must hit exactly one
line. `new_text` is one bullet line (`- ...`, no placeholder), `reason` and `plain` (1..600 characters) are
required, optional `by` `agent` (default) | `owner` (a recorded claim), `trust_source` must be
`conversation`. **`quote` is required (D7):** the user's literal words that justify the change, a
non-empty string (a missing or non-string quote rejects the plan). The quote is checked like a candidate's
(same transcript, same session binding, same rules); only a VERIFIED quote applies the entry. An entry
whose quote is not verified is skipped - nothing is written for it, `tally.supersede_skipped_unverified`
and a warning say so, the rest of the plan runs - so a supersede can never rest on the agent's word alone.
Corrections of an encoding damage go through `corrections` and need no quote. Rejected before the first
byte (exit 2, nothing written): target not found or not unique, empty/missing `reason`, `plain` or
`quote`, a placeholder line / heading as target, a target in the preamble (everything before the first
`## ` heading), a `new_text` that already stands as another line or that two entries of the plan share,
two entries on one line (by `match` or `line_id`), a `new_text` with any line separator (CR, LF, VT, FF,
FS, GS, RS, NEL, U+2028, U+2029), a foreign `trust_source`, an unreadable `user.md` or changelog. Order: the audit entry (`field: user.md/supersede`, `old_value`,
`new_value`, `reason`, `plain`, `quote`, `quote_verified`, `by`, `session`, `candidate_id` with `line_id`) goes into
`user-changelog.json` FIRST, then `user.md` is rewritten with exactly the target lines replaced -
every other byte (CRLF, BOM, preamble, section order, a last line without ending) stays. Repeatable:
with `match`, an entry whose old line is gone while `new_text` stands exactly once counts as applied;
with `line_id` (the new line no longer cites the id) the audit log must also hold a `user.md/supersede`
entry for that id and that `new_text`, else the entry is "no such line" (both:
`tally.supersede_skipped_unchanged`); after a crash between changelog and file the rerun does not
write the audit entry twice. Tally: `user_lines_superseded`, `supersede_skipped_unchanged`,
`supersede_skipped_unverified`. The section runs before `corrections` and the promotion; the check
resolves the targets against the file as it stands before the run.

The promotion into `user.md` (queue re-review, Step 6.3) is lossless too (D8): a promoted line is INSERTED
after the last non-blank line of its `## ` section (a missing section is appended at the end), every other
byte - BOM, CRLF or LF, preamble, section order, a last line without ending - stays. Only a `user.md` that
does not exist yet or holds nothing but whitespace gets the canonical form.
A retracted statement needs the verified words of the user to come back (D9). The word measure does not judge
meaning, it decides the proof level: a candidate that TOUCHES the `old_value` of a `user.md/supersede` changelog
entry is promoted only when `confirmed_by` is `quote` (a verified quote of the user); a repetition (`repeat`), the
inferred route and a row without `confirmed_by` are not promoted - the status stays, `tally.promotion_blocked_superseded`
counts it and a warning says so. "Touches" is measured generously (a false alarm costs one quote): the old line
is read twice - without the citation `(UCn, date)` at its END (every other bracket stays content, also the
candidate's) and without every unnested bracket group at its end (`(Owner, 2026-07-01)`, `(seit Juli)`; `.,;:!?`
may follow) - against the observation; one touching reading is enough. With 3 or more content words in the read
line (4+ letters, no function words, umlauts written out, invisible characters of category Cf/Mn removed) >= 50% of
them must stand in the candidate; with 1 or 2 all of them must (a function word of the line may be missing); with
none ("Mag TDD") all tokens of 2+ characters must. "Stand in" = an equal or same-stem word among ALL words of the
observation, function words included ("noch einmal" stands for "nochmal"); an invisible character in the
observation or the retracted line is read once as nothing and once as a space. There is no refinement exception: the new line's wording
carries retracted words as well. Known limits: a synonym or a paraphrase that keeps under 50% of the words, a
candidate that names only part of the line ("Will keine Emojis" against "Will keine Emojis in Commits", or the
core of a long line), and deliberately disguised wording (look-alike letters) are not touched; a verified quote
proves wording, not meaning, so even the retraction's own quote lifts a candidate that shares its words.
The block is noted on the candidate as `blocked_by_supersede` (the id of the replaced line: the entry's `candidate_id`, else the
citation `(UCn, date)` at the END of the old line, else the first bracketed id of the line, else `"unknown"`)
and warned about once - a later wrap-up counts the block but does not
repeat the warning, unless another line blocks it; the note goes when the candidate is promoted or closed as
a duplicate. The way back is the user's quote: a plan entry of the SAME key with `confirmed: true` and a verified
`quote` lifts the row to `confirmed_by: "quote"`; or a `user_md_supersede` of the replacing line to the candidate's
wording with a verified quote makes the next review close the candidate as a duplicate of that line.

`principles` (Step 5c of `/agentic-os:maintain`) needs `plain` per entry (D11): the pointer card the owner
reads, plain German, 1..600 characters, stored on the pointer row as `plain`; a missing, empty,
non-string or too long one rejects the whole plan before the first byte, a merge into an existing
pointer updates it. P3-A safeguards, all checked before the first byte: at most one `rid:<id>` tag per entry, and a
merge into a pointer that carries a different `rid:` is refused (tags are merged otherwise; a pointer without one
adopts it); a rule on the hold list (`held_rules` in `~/.claude/agentic-os.local.json`: `R11`, `rid:R11` or an
anchor) is refused, also when only the target pointer carries the held `rid:`; a member that the same plan corrects
(`corrections.learnings`) is refused (correction first, pointer in a second plan); `restore` together with
`principles` or `principle_retire` is refused (restore first); `member_sha` (optional, `{member id: sha256 of its text, full or >= 12 hex chars}`, exactly the
entry's members; `apply_wrapup.member_sha(row)`) rejects the whole plan when one member changed since the plan was
built. An entry sent unchanged writes nothing (no `last_relevant`, `tally.principles_unchanged`); every other change
to an existing pointer (also one created earlier in the same plan) appends `merges[]` = `{date, tag (its condense:<date> tag or null), added_members,
old_summary, old_plain, old_tags, old_text}`. `principle_retire` (way back, never in the same plan as
`principles`): each entry takes `ids` (pointer ids) or `tag` (`condense:<date>`) plus `reason` (1..200 characters).
By id a live pointer gets `superseded_by = obsolete:<reason>`, an already retired one is skipped. By tag a pointer
created under the tag is retired and a pointer the tag only merged into is restored from that `merges[]` entry
(members, summary, plain, tags, text; audit in `unmerges[]`); a newer change under another tag on top refuses the
plan. Tally: `principles_retired`, `principles_unmerged`, `principle_retire_skipped`. `--snapshot` takes a store
snapshot under the lock after the plan check (not on `--dry-run`, path in `snapshot`); `--lint` also reports
pointers: anchor without fragment, member not local, not in the store, not live or itself a pointer, anchor
shared with another live pointer.

`rule_class` on a learning (flag or tag `regel`/`rule`/`verfassung`/`constitution`), `rule_explanations`
and `rule_releases` (Step 3f, 5.8.0, owner decision F4) form the mixed form of the learnings gate. A
rule-class learning stores `rule_class: true` and `release` = `{status: needs_explanation|explained|
released|rejected, ...}`; it is always a bridge candidate, never in `tally.bridge_candidates`, and
`bridge_decisions` cannot approve it. `rule_explanations` (second model): `explained_by`, `plain` (1..600 characters, like every owner-facing proposal),
`risk`, `verdict` (`confirms|contradicts|unsure`), optional `affects`/`why`/`counter_note`; only while the gate is open.
`rule_releases` (owner): `by` must be `owner`, state `explained`, `two_sentences` = 2..3 real sentences of
>= 3 words, <= 400 characters, not a copy of the sheet or the rule; `"status":"rejected"` + `note` is
allowed from any state. **`by: owner` is an honor system:** the script checks the value of the field, not who
typed it - the agent writes the plan. The gate is a self-commitment of the agent (release only on sentences the
owner wrote in this conversation, verbatim), made visible afterwards by `release.two_sentences` / `released_at`
in the store; it is not an authentication and must not be described as one. A principle pointer
(`principles`) over a rule-class learning needs the same gate: the plan is refused until every rule-class
member is `released` (DCO #10057), and a pointer written by hand around that is not projected. `tally.rule_class_open` = `{needs_explanation: [ids], explained: [ids]}`;
result `rule_class_status_line` for the report. The sheet comes from `scripts/learning_release.py sheet`.

## Learning entry (Step 3b)

Append to `learnings/learnings.json`:

```json
{
  "id": "L{next_number}", "date": "{YYYY-MM-DD}", "text": "{insight with context}",
  "summary": "{conclusion, <= 150 chars, optional}",
  "importance": 3, "tags": ["tag1", "tag2"], "layer": "short-term",
  "superseded_by": null, "last_relevant": "{YYYY-MM-DD}",
  "derived_from": ["iteration-{n}", "E{id}"], "review_after": "{YYYY-MM-DD}"
}
```

importance: 5 = prevents data loss/security issue · 4 = prevents multi-attempt debugging ·
3 = non-obvious behavior · 2 = workflow optimization · 1 = trivia.

**`derived_from` (provenance, memideaspec §7.4):** IDs of THIS session's origins —
iteration numbers from `iteration-log.md` (`iteration-{n}`), error IDs from
`errors.json` (`E{n}`), decision IDs (`D{n}`). No traceable origin → `[]`. Never
invent provenance; an honest empty list beats a guessed reference.

**`summary` (short form, optional — strongly recommended for `importance >= 4`):** the
conclusion in one sentence, <= 150 characters — the rule, not its context. The MEMORY.md
and AGENTS.md projections show it instead of the first 150 characters of `text`, which
are usually context. Omit it rather than paraphrase badly; without it the projection
cuts `text`. Longer than 150 → kept, but the tally warns and the projection cuts it.

**`review_after` (staleness contract):** date when the learning's validity should be
re-checked; default = `date` + 90 days (matches the bootstrap STALE threshold). Set
ONCE at creation — bootstrap and /agentic-os:maintain read it, wrap-up never updates it
on existing entries.

Backward compatibility: entries created before v4.4.0 lack both fields — leave them
as-is (consumers use `.get()`); do NOT backfill.

## User candidate (Step 6.2)

Enqueue into `working/user-candidates.json` (new observation → new candidate; same
`key` exists → increment `occurrences` when the session has not named it yet, update `last_seen`,
raise `status` if warranted):

```json
{
  "id": "UC{n}", "key": "kebab-case-key",
  "observation": "{1-line observed preference}",
  "status": "observed", "signal_type": "preference",
  "confidence": 0.5, "occurrences": 1, "sessions": ["{session-id}"],
  "evidence": ["session {YYYY-MM-DD}"],
  "first_seen": "{date}", "last_seen": "{date}",
  "trust_source": "conversation"
}
```

Written by the script when the plan entry carried a `quote` (P1/B2, see "Guarantees"):
`"quote": "{the user's literal words}", "quote_verified": true | false | "kein_transkript"`; and
`"confirmed_by": "quote" | "repeat"` once the status became `confirmed`; `"sessions": ["{session-id}", ...]`
lists the sessions that named the candidate (D1). Rows that predate B2 have none of these and promote as
before; the first new session counts one repetition for them. `"blocked_by_supersede": "{line id}"` marks a
candidate the promotion held back because it touches a statement a `user_md_supersede` retracted and lacks the verified quote (D9).

**Central profile (P2).** While `<store>/identity/profile.md` exists (`<store>` = `--identity-store`, else
`$AGENTIC_OS_IDENTITY_STORE`, else `~/.agent-memory`), the same rows live in `<store>/identity/profile-candidates.json`
with global ids `PC1`, `PC2`, ... and one more field, set by the script:
`"origin": {"projekt": "work/tools", "id": null}` (the project folder relative to home, `~` for the home store;
`id` = the old project id, set only by the migration). Promotions go to `profile.md` as `(PCn, date)`, audit entries
to `profile-changelog.json` (same `field` names, plus `origin`). Without `profile.md` nothing changes.

## Consolidation marker (Step 9.5)

Overwrite `.agent-memory/consolidation-marker.json` (single file, no history — git
history preserves older markers):

```json
{
  "last_wrapup": "{ISO timestamp}",
  "mode": "wrap-up",
  "consolidated_sessions": ["{session_id}", "..."],
  "iterations_logged": 0,
  "learnings_added": 0,
  "touched_files_seen": 0
}
```

`mode` (and `consolidated_by` / `last_consolidated_by` on every consumed dirty file)
names who consolidated: `"wrap-up"` for this skill, `"headless"` for the night run
(`wrapup_core.py apply --headless`, no judge — learnings, decisions and identity were
not harvested). `apply_wrapup.apply_consolidation(by=...)` writes both; any other
value is a plan error.

Harvest state (D-024): with `by="wrap-up"` the dirty file of THIS run's `--session-id`
additionally gets `harvest: {closed_by: "wrap-up", closed_at, run: "wrap-up", snap:
{write_count, updated, foreign_updated}}` and is then deletable by the daily GC. Every
other consumed file stays pending until a rule in `scripts/harvest_state.py` closes it;
headless never closes. A closure counts only while `snap` matches the file.

## Local session-summary.md (Step 5)

Overwrite `.agent-memory/session-summary.md` (English headers; **max 30 lines**;
delta-update — rewrite only changed sections):

Exception (DCO #9433): when the store's summary IS the central handoff
(`<central-dir>/.agent-memory/session-summary.md`, default `~/AI` — the AI workspace),
`apply_wrapup.py` does not overwrite it and reports a warning instead; the block
reaches that file only through the prepend path (Step 7.6a / `write_central`).

```markdown
# Last Session

*Date: {YYYY-MM-DD HH:MM}*
*Agent: Claude Code*

## What Was Done
- {completed work, max 10 bullets}

## Open Items
- {unfinished work, blockers}

## Next Steps
1. {highest priority} 2. {second} 3. {third}

## Statistics
- Iterations: {n} | Errors: {n} | New Patterns: {n}

## Active Warnings
- {high-confidence patterns / declining trends, if any}
```

## Handoff Mode (Pre-Compression)

When triggered by long context or explicit handoff request, append to session-summary.md:

```markdown
## Handoff Context
- **Active task**: {what was being worked on right now}
- **Current state**: {done / next}
- **Active patterns**: {top high-confidence patterns}
- **Open questions**: {decisions pending user input}
```

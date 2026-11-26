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

**Judge plan (T-028b):** `wrapup_core.py apply` takes this object with `summary` in place of
`session_summary` (`SKILL.md` shows the shape) and hands the rest to `apply_wrapup.py`.
The sections below describe what the applier accepts. `consolidate` is ignored there: the core sets the marker.

**Old sections (6.0.0):** `identity` and `user_candidates` (the removed identity harvest) and `errors` inside an
iteration (the removed error log; `errors.json` is frozen data) are NOT rejected. Nothing is written for them;
`tally.ignored_sections` lists them (sorted, without duplicates, always present, `[]` when nothing was ignored) and
the result carries `ignored_status_line` = `Ignoriert (6.0.0): <keys, comma-separated>` when the list is not empty.

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
                         "recovered_from": null } ],
  "decisions":       [ { "type": "architecture-decision", "title": "...", "context": "...",
                         "options_considered": [ { "option": "...", "pros": [], "cons": [] } ],
                         "decision": "...", "consequences": "...",
                         "supersedes": null, "tags": [] } ],
  "learnings":       [ { "text": "...", "summary": "...", "importance": 3, "tags": [], "derived_from": [],
                         "duplicate_of": null } ],
  "bridge_decisions": [ { "id": "L26", "status": "approved", "note": "...", "plain": "...", "by": "agent" } ],
  "corrections":     { "tasks": [ { "id": "T-116", "title": "...", "reason": "...", "by": "agent" } ],
                       "learnings": [ { "id": "L154", "text": "...", "summary": "...", "reason": "...", "by": "agent" } ] },
  "rule_explanations": [ { "id": "L26", "explained_by": "<model>", "plain": "...", "risk": "...",
                           "verdict": "confirms", "affects": [] } ],
  "rule_releases":   [ { "id": "L26", "by": "owner", "owner_go": "..." } ],
  "user_md_supersede": [ { "line_id": "UC12", "match": null, "new_text": "- ...", "reason": "...",
                           "plain": "...", "quote": "the user's literal words", "by": "agent",
                           "trust_source": "conversation" } ],
  "user_md_retire":  [ { "line_id": "UC12", "match": null, "reason": "...", "plain": "...",
                         "quote": "the user's literal words", "by": "agent", "trust_source": "conversation" } ],
  "identity_expire": { "older_than_days": 30, "reason": "..." },
  "principles":      [ { "summary": "...", "anchor": "<file>#<heading>", "members": ["L12", "L40"],
                         "plain": "...", "tags": [], "member_sha": null } ],
  "principle_retire": [ { "ids": ["L90"], "reason": "..." } ],
  "learning_updates": [ { "id": "L12", "superseded_by": "L40", "reason": "..." },
                        { "id": "L17", "review_after": "YYYY-MM-DD", "reason": "..." },
                        { "id": "L21", "obsolete": true, "reason": "..." } ],
  "pflegelauf_stamp": true,
  "soul_candidates": [ { "proposal": "...", "evidence": [] } ],
  "open_tasks":      { "add": [ { "title": "...", "source": "wrap-up", "cross_project": false } ],
                       "close": ["T-001"] },
  "session_summary": { "what_was_done": [], "open_items": [], "next_steps": [],
                       "statistics": { "iterations": 0 },
                       "warnings": [],
                       "handoff": { "active_task": "", "current_state": "",
                                    "open_questions": "" } },
  "consolidate": true,
  "iterations_logged": 0
}
```

Every key is optional — omit what the session did not produce. The script owns
all deterministic rules and needs no help with them: id assignment continuing
whatever format is already on disk (`L{n}` / `T-{00n}` /
`D-{00n}` — never assume, the real files drifted from their templates),
the iteration markdown shape, the decision supersede flip, `review_after` = date + 90d,
exact-text dedup, changelog-before-edit
ordering, `learnings.md` regeneration, the bridge-candidate derivation
(`importance >= 4` → `bridge_status: "candidate"`, Step 3d.1 — a plan-supplied
`bridge_status` is dropped; a verdict exists only via `bridge_decisions`, Step 3d.4),
the consolidation marker and the dirty
flags. Supply judgment only: what is a learning, what is a decision,
how important, which section.

Guarantees worth relying on:

- A `user_md_supersede` / `user_md_retire` entry with a `trust_source` other than
  `conversation` rejects the plan (memory-poisoning defense); since 6.0.0 nothing
  is enqueued or promoted any more.
- `patterns.json`/`patterns.md` and `errors.json` (data of the pattern and error
  mechanics removed in 6.0.0) and `soul.md` are refused outright. `iteration-log.md`,
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
- (5.3.0) `learnings/learnings.json` is applier-owned too (`learnings`).
  Ownership is checked case-insensitively,
  so `Learnings/Learnings.json` is refused like the canonical path.
- (5.3.0) Every learning the script writes is validated before the first byte:
  `importance` a JSON int 1..5 (no `"5"`, no float, no bool), `tags` a list of
  strings, `derived_from` a list of at most 30 strings, `duplicate_of` a live id.
  Legacy rows are never a gate — `--lint` lists them, read-only.
- (5.3.0, Step 3a) Jaccard >= 0.6 to a live learning is a duplicate (that entry's
  `last_relevant` is refreshed); the top-level `near_duplicates` of the result lists
  live entries at 0.2–0.6 for the `duplicate_of` verdict.
- (6.0.0) A plan with a `restore` section is refused (exit 2, "removed in 6.0.0"), before every other check.
- (5.3.0) A writing run holds `working/store.lock` from the first read to the last
  write; a store locked longer than `--lock-timeout` (10 s) is exit 2, nothing written.
- (P1/B2) The `quote` of a `user_md_supersede` / `user_md_retire` entry applies the entry only when it
  is a substring of ONE genuine user message in the session's transcript
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
  words either side) are heuristics the owner calibrates. The warning of a skipped entry names why its quote did not verify
  (`too_short`, `too_few_words`, `word_boundary`, `negation`, `not_found`, `no_transcript`,
  `unreadable`, `override_rejected`, `session_mismatch`). The verdict is `true` | `false` | `"kein_transkript"`;
  only `true` applies the entry (the changelog entry records `quote` and `quote_verified`), a missing transcript
  is not an error, it skips the entry. Tally: `quote_transcript` (the path used). A verified quote that shares no
  content word with a supersede's `new_text` still applies - it is reported: `tally.quote_unrelated`
  (`supersede:<target>`) and a warning. A content word is a word of 4+ letters that is not a
  function word, umlauts are written out (ä = ae, ö = oe, ü = ue, ß = ss); two words relate when they are
  equal or share a stem (common prefix of 4+ characters covering 80% of the shorter word). A verified quote and
  a `new_text` of which only one carries a negation word are counted the same way: `tally.quote_polarity` (same key format as
  `quote_unrelated`), a counter without a warning line (a weak hint, calibration data).
- (P1/B2, D3) The transcript is found at `<HOME>/.claude/projects/<dir>/<session-id>.jsonl` only: the same
  rule holds for `--transcript` and for the default lookup (a junction or symlink out of `projects/` is
  no hit). Residual risk by design: a file the agent itself places at that path (a plain write, a hard
  link) cannot be told from a real transcript. A path with a `\\?\` prefix is refused (safe side).
- (P1/B2, D2) If `CLAUDE_CODE_SESSION_ID` is set in the environment and differs from `--session-id`, the
  identity evidence of the run is not verifiable: no quote verifies (`"kein_transkript"`), a warning and
  `tally.session_binding_mismatch` say so; the rest of the plan runs normally.
  Without that variable (Codex, tests, headless) there is nothing to compare and the given id stands.
- (P1/B2) `identity/user.md`, `identity/user-changelog.json` and `working/user-candidates.json`
  are applier-owned (`identity`): the generic write path refuses them like the other stores.

The tally is computed from the writes that actually happened — report from it
instead of counting by hand. `tally.bridge_candidates` lists ALL open store-wide
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
`user-changelog.json` as `user.md/encoding-correction`. `profile_candidates` entries are `{id, observation?, evidence?, reason}` on the candidate queue; each field must be the same encoding repair, no other field may appear. The row keeps
`corrections[]` = `{date, by, session, field, reason, was}`. The same text again changes nothing; a new
text below similarity 0.6 to the old one, a principle pointer, an unknown or ambiguous id rejects the
whole plan before the first byte. Correcting a rule-class learning resets its gate. `tally.tasks_retitled`,
`learnings_corrected`, `decisions_corrected`, `user_lines_corrected`, `candidates_corrected`, `corrections_skipped_unchanged`, `correction_ids`, `corrections_projection_needed`.
`--propose-encoding-corrections` (read-only) prints a `corrections` plan for every Mojibake row (tasks, learnings,
decisions, user.md lines, queue candidates); a new
learning/task whose text looks like Mojibake is written but warned about (`tally.warnings`).

`user_md_supersede` (Step 6.3b, P1/B2) replaces ONE line of `identity/user.md` per entry - the way to
resolve a contradiction in the profile. Per entry: exactly one target, `line_id` (the
candidate id the line cites, `(UC12, date)`) or `match` (the exact line text); it must hit exactly one
line. `new_text` is one bullet line (`- ...`, no placeholder), `reason` and `plain` (1..600 characters) are
required, optional `by` `agent` (default) | `owner` (a recorded claim), `trust_source` must be
`conversation`. **`quote` is required (D7):** the user's literal words that justify the change, a
non-empty string (a missing or non-string quote rejects the plan). The quote is checked against the session
transcript (see "Guarantees": session binding, sources, length, negation); only a VERIFIED quote applies the entry. An entry
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
`supersede_skipped_unverified`. The section runs before `corrections`; the check
resolves the targets against the file as it stands before the run.

`principles` (verdicts `verwandt` / `global` of `/agentic-os:maintain`) needs `plain` per entry (D11): the rule in
plain German, 1..600 characters, stored on the pointer row as `plain`; a missing, empty,
non-string or too long one rejects the whole plan before the first byte, a merge into an existing
pointer updates it. P3-A safeguards, all checked before the first byte: at most one `rid:<id>` tag per entry, and a
merge into a pointer that carries a different `rid:` is refused (tags are merged otherwise; a pointer without one
adopts it); a rule on the hold list (`held_rules` in `~/.claude/agentic-os.local.json`: `R11`, `rid:R11` or an
anchor) is refused, also when only the target pointer carries the held `rid:`; a member that the same plan corrects
(`corrections.learnings`) is refused (correction first, pointer in a second plan); `member_sha` (optional, `{member id: sha256 of its text, full or >= 12 hex chars}`, exactly the
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

`learning_updates` and `pflegelauf_stamp` (6.0.0, `/agentic-os:maintain` Step 3; a wrap-up plan that carries either is
refused by `wrapup_core.py`, exit 2) are the write path of the Pflegelauf. Each entry: `id` (a local `Lnn`; a qualified id is refused), `reason` (1..300
characters once whitespace is collapsed) and exactly one of `superseded_by` (a local id, or `null` to take a wrong
verdict back), `review_after` (`YYYY-MM-DD`) and `obsolete: true` (verdict "gegenstandslos": the row describes a
mechanism, file or rule that no longer exists and no other row replaces it; written as
`superseded_by = "obsolete:pflegelauf-<plan date>"`, taken back with `superseded_by: null`); any other field is
refused - the section never touches `text` or the row's existence. Refused before the first byte (exit 2, whole
plan): `superseded_by` (set or cleared) or `obsolete` on a rule-class learning, a pointer row (`kind: principle`) or a member of a live pointer or of one the same plan writes (those keep
`principle_retire` and the rule-class gate; `review_after` is allowed on them); a target that is unknown, the id
itself, a pointer, a `bridge_status` rejected/retired entry, an unreleased rule-class learning, retired by
`principle_retire` in the same plan or itself superseded (also by the same plan, including `obsolete`: no chains, no
pairs); `obsolete` that is not `true` or on a row that is already superseded; the same id twice; a row whose `update_log` is not a list. Each applied entry appends
`{date, reason, by: "pflegelauf", field, old, new}` (field `superseded_by` or `review_after`, the value before and
after) to the row's `update_log`; an entry that changes nothing writes and logs nothing (`tally.learning_updates_unchanged`).
`pflegelauf_stamp: true` (a JSON bool, else refused) writes `working/pflegelauf.json` = `{date, id_keys, zaehlung}` after every
learnings writer of the run - the counter base of the `PFLEGELAUF` trigger line. Tally:
`learning_updates` = `{superseded, cleared, review_after, obsolete}`, `learning_updates_unchanged`, `pflegelauf_stamp`
(the stamp written, or `null`).

`rule_class` on a learning (flag or tag `regel`/`rule`/`verfassung`/`constitution`), `rule_explanations`
and `rule_releases` (Step 3f, 5.8.0, owner decision F4) form the mixed form of the learnings gate. A
rule-class learning stores `rule_class: true` and `release` = `{status: needs_explanation|explained|
released|rejected, ...}`; it is always a bridge candidate, never in `tally.bridge_candidates`, and
`bridge_decisions` cannot approve it. `rule_explanations` (second model): `explained_by`, `plain` (1..600 characters, like every owner-facing proposal),
`risk`, `verdict` (`confirms|contradicts|unsure`), optional `affects`/`why`/`counter_note`; only while the gate is open.
`rule_releases` (owner): `by` must be `owner`, state `explained`, `owner_go` = the owner's go in his own words
(>= 3 words, <= 400 characters, not a copy of the sheet or the rule; F4 since 2026-11-15, the retired field
`two_sentences` is refused, releases stored before keep it); `"status":"rejected"` + `note` is
allowed from any state. **`by: owner` is an honor system:** the script checks the value of the field, not who
typed it - the agent writes the plan. The gate is a self-commitment of the agent (release only on sentences the
owner wrote in this conversation, verbatim), made visible afterwards by `release.owner_go` / `released_at`
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
iteration numbers from `iteration-log.md` (`iteration-{n}`), decision IDs (`D{n}`). No traceable origin → `[]`. Never
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

## Candidate queue (frozen in 6.0.0)

`working/user-candidates.json` is the queue of the removed identity harvest: nothing enqueues or promotes any more.
Its rows stay data - `identity_expire` still marks old one-off observations `status: "expired"`, and the briefing
projection ranks profile lines by them (quote-confirmed first, then by `occurrences`). Shape of a row:

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

Older rows may carry `quote`, `quote_verified`, `confirmed_by` and `blocked_by_supersede` from the harvest.

**Central profile (P2).** While `<store>/identity/profile.md` exists (`<store>` = `--identity-store`, else
`$AGENTIC_OS_IDENTITY_STORE`, else `~/.agent-memory`), the same rows live in `<store>/identity/profile-candidates.json`
with global ids `PC1`, `PC2`, ... and one more field:
`"origin": {"projekt": "work/tools", "id": null}` (the project folder relative to home, `~` for the home store;
`id` = the old project id, set only by the migration). Profile lines cite `(PCn, date)`; audit entries go
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
(`wrapup_core.py apply --headless`, no judge — learnings and decisions were
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
- Iterations: {n}

## Active Warnings
- {declining trends, if any}
```

## Handoff Mode (Pre-Compression)

When triggered by long context or explicit handoff request, append to session-summary.md:

```markdown
## Handoff Context
- **Active task**: {what was being worked on right now}
- **Current state**: {done / next}
- **Open questions**: {decisions pending user input}
```

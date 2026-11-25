# wrap-up: judge rules in full

Read this file only when the plan needs it: a rule-class learning, a correction, a profile line to
supersede or retire, or a doubt about a quote. `SKILL.md` carries what a normal run needs.

Step names below are the historical ones. "Step 7.4" means the `wrapup_core.py apply` call, a section
that says "own call" goes to `wrapup_core.py followup`, and "both projections (3d.6)" run by themselves
after `followup`. The call examples use `apply_wrapup.py`; `followup` takes the same JSON.

### 3d: Bridge gate — delegated (bridge-gate)

Curated Claude→Codex bridge (design: membrain/membridge.md). Canonical store is
learnings.json (`bridge_status`, reason in `bridge_note`); the AGENTS.md block is a
projection. **Delegated since 5.7.0** (owner decision 2026-10-16, global rule
"Delegierte Entscheidungen"): bridging is reversible and internal, so the agent
pre-assesses, decides and reports one reason per entry — no `[j/n]` question.
Runs AFTER Step 7.4: the ids of this session's learnings exist only then.

1. Every learning written this session with `importance >= 4` gets
   `"bridge_status": "candidate"` — **done by `apply_wrapup.py` automatically**
   (additive field; older entries without it are untouched, never backfill; a
   plan-supplied `bridge_status` is dropped — a verdict exists only via the
   `bridge_decisions` section, which demands a reason).
2. Source: `tally.bridge_candidates` from the Step 7.4 apply run — every open live
   candidate store-wide, this session's and earlier undecided ones. Do not re-read
   learnings.json for this. Empty → go to 3e.
3. Pre-assess each candidate. Approve only if ALL hold: (a) useful in EVERY
   Codex/Claude session of this project — every line in AGENTS.md / MEMORY.md costs
   context on every start; (b) a durable, actionable rule, not a one-off fact or a
   session story; (c) not already covered by a global rule, an approved learning or
   a principle pointer; (d) self-contained and still true today. Otherwise reject.
   Genuinely unsure → a Codex second opinion if one is at hand, else reject with a
   note starting `unsicher:` — rejecting is the conservative default (reversible,
   costs no context). Never leave a candidate undecided to "ask next time".
4. Apply the verdicts in ONE call with only this section (no `consolidate`):
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/wrapup_core.py" followup --mem .agent-memory --session-id <session-id> <<'PLAN'
   {"bridge_decisions": [{"id": "L26", "status": "approved", "note": "<one-sentence reason>",
                          "plain": "<plain German for Dominic>"}]}
   PLAN
   ```
   `status` is `approved` or `rejected`; `note` (the technical reason) and `plain` (plain-text-duty)
   are both required. **`plain` is the text Dominic reads** (owner request 2026-10-27: the plain
   explanation decides whether he can judge a learning): simple German, no jargon, what changes concretely
   for his sessions, at most 600 characters. A missing, empty or longer `plain` rejects the
   whole plan before the first byte. The script stores `bridge_note` = `<date> agent: <note>`
   and `bridge_plain` = `<plain>` on the object and rejects the whole plan on an unknown,
   superseded, retired or principle id.
5. Report — mandatory: the returned `bridge_status_line` verbatim,
   then one line per case `L26 approved — <plain>` (the plain text, not the technical reason).
6. If `tally.bridge_status_changed > 0`, run BOTH projections:
   `python "${CLAUDE_PLUGIN_ROOT}/scripts/bridge_projection.py" .agent-memory --agents-md <project-root>/AGENTS.md`
   (Codex side; workspace store `~/AI/.agent-memory` → `~/AI/AGENTS.md`) and
   `python "${CLAUDE_PLUGIN_ROOT}/scripts/memory_index_projection.py" .agent-memory --project-root <project-root>`
   (Claude side: managed block in `~/.claude/projects/<hash>/memory/MEMORY.md`;
   the script derives `<hash>` itself). Report both one-line outputs verbatim.
7. Owner override = rollback: when Dominic disagrees, the same section with
   `"by": "owner"` and the flipped status, then both projections again. No special
   path, no question beforehand.

### 3f: Rule-class learnings — second model + owner (rule-gate)

Mixed form (owner decision F4, 2026-10-22; DCO #9869). **Normal learnings** stay with
Step 3d: the agent curates them alone and reports one line each. A learning that
**changes a rule or the constitution** (global/project rules, hard gates, delegation,
who may decide what) is **rule-class**: it never reaches AGENTS.md / MEMORY.md on the
agent's say-so alone - the agent commits to the procedure below (the script checks its form, see the honor-system note below).

1. **Mark it** in the Step 7.4 plan: `"rule_class": true` on the learning. A tag `regel`,
   `rule`, `verfassung` or `constitution` marks it too. When unsure whether a learning
   changes a rule, mark it - the cost is one explanation, not a silent rule change.
   `apply_wrapup.py` then stores `rule_class` + `release.status: needs_explanation`,
   makes it a bridge candidate whatever its importance, keeps it out of
   `tally.bridge_candidates` (Step 3d cannot approve it; `bridge_decisions` approved on it is
   rejected) and lists it in `tally.rule_class_open`.
2. **Second model (mark + queue, no live model call in the plugin).** For each id in
   `tally.rule_class_open.needs_explanation`:
   `python "${CLAUDE_PLUGIN_ROOT}/scripts/learning_release.py" sheet .agent-memory <id>` renders the
   Erklärbogen (`references/erklaerbogen-vorlage.md`). Hand it to a model that is **not**
   the author - Codex (`/codex:rescue`) or a sub-agent of a different model class -
   and enter its JSON answer as `rule_explanations` (own call; needs `explained_by`,
   `plain` (plain German, at most 600 characters - longer is rejected), `risk`, `verdict`). No second model at hand: the learning stays
   `needs_explanation` and is reported again next time (fail closed).
3. **Owner release.** Show Dominic the `plain` text and the verdict. He releases with a **go in
   his own words** (rule F4 since 2026-11-15, replacing the former two sentences) - record it
   verbatim as `rule_releases` (`{"id","by":"owner","owner_go":"..."}`). The script refuses `by`
   other than `owner`, a state other than `explained`, fewer than 3 words, more than 400
   characters, a copy of the sheet or the rule, and the retired field `two_sentences`. Rejecting (`"status":"rejected"`) is always possible. Never release on the owner's
   behalf and never infer a release from silence.

   **`"by": "owner"` is an honor system, not an authentication.** The script checks the form
   (the field value, state `explained`, 3+ words, no copy of the sheet) and nothing
   else: the agent writes the plan itself, so it can neither know nor prove that Dominic said
   those sentences. The gate binds the agent by **self-commitment**. Enter a release only when
   Dominic wrote his go in THIS conversation, copy it verbatim into
   `owner_go`, and never compose it from the sheet, from memory or from his silence. A
   release the agent invented would pass every technical check; what remains is the audit
   trail (`release.owner_go`, `released_at` in the store) that lets the owner see and
   revert it afterwards. The same holds for every other `by: owner` in the plan (`bridge_decisions`,
   `corrections`): it is a recorded claim, not a verified identity.
4. After a release `tally.bridge_status_changed > 0` -> both projections (3d.6).
5. Report - mandatory: the returned `rule_class_status_line` verbatim, plus one line per case
   `L26 erklärt von <modell>: <plain, ein Satz>`. A correction of a rule-class text
   (`corrections`) resets the state to `needs_explanation` and takes the learning out of the bridge.

### 3g: Corrections - fix a title or a learning text (corrections)

Existing rows were never editable: the wrap-up path only adds, and the stores are
applier-owned, so a wrong task title or a damaged learning text (Mojibake, DCO #9393) had
no regular repair. Plan section `corrections` is that path - own call, reason required:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/wrapup_core.py" followup --mem .agent-memory --session-id <session-id> <<'PLAN'
{"corrections": {"tasks": [{"id": "T-116", "title": "<new title>", "reason": "<why>"}],
                 "learnings": [{"id": "L154", "text": "<corrected text>", "reason": "<why>"}]}}
PLAN
```

The row keeps `corrections[]` (date, by, session, field, reason, `was` = the old text). A
learning text must stay the same statement (similarity >= 0.6) - a different statement is a
new learning that supersedes the old one. `by` (`agent` default | `owner`) is a recorded claim,
not a verified identity (see the honor-system note in 3f).

Two more sections (DCO #10057), same rules: `decisions` (`{"id","reason", title|context|decision|
consequences|options_considered}`; `options_considered` keeps its shape - same entries, same keys,
only the strings are repaired; `status`, `supersedes` and the like cannot be corrected here, a
changed decision is a new decision) and `user_md` (`{"old","new","reason"}`, one whole line of
`identity/user.md`). **`user_md` repairs the encoding and nothing else**: `new` must equal the
deterministic repair of `old`, otherwise the plan is refused - user.md changes its wording only
through `user_md_supersede` / `user_md_retire` (Step 6). Each repaired line gets an audit entry
`user.md/encoding-correction` in `user-changelog.json` first. A decision that was already promoted
to the wiki is repaired in the store only; the plan warns, the wiki page is a separate step.

Mojibake: `apply_wrapup.py .agent-memory
--propose-encoding-corrections` prints the ready plan for tasks, learnings, decisions and
user.md lines (read-only), pipe it back in. If
`tally.corrections_projection_needed` or `bridge_status_changed > 0`, run both projections (3d.6);
the AGENTS.md block is a projection and is never edited by hand.

## Step 6: Identity — frozen in 6.0.0

**The harvest is gone.** Since 6.0.0 wrap-up plans no identity section: no candidate queue grows, nothing is
promoted into `user.md` / `profile.md`. A plan of an older skill version that still sends `identity`,
`user_candidates` or `errors` inside an iteration is not rejected: nothing is written for those sections,
`tally.ignored_sections` names them and the result carries `ignored_status_line` (`Ignoriert (6.0.0): ...`) -
report that line. The profile itself stays: the SessionStart briefing still shows its lines, and two plan
sections still change it, on the user's own words only: `user_md_supersede` (6.3b) and `user_md_retire` (6.4b).

**Central profile (central-profile)** (P2, Release A). One profile for all projects lives in
`~/.agent-memory/identity/` (`profile.md`, `profile-candidates.json`, `profile-changelog.json`). It is
ACTIVE exactly when `profile.md` exists - the migration wrote that file
last, and renaming it switches back. While it is active, the identity sections of the plan go there,
not into this project's files; you write the plan exactly as before:
- Lines of `profile.md` cite `(PCn, date)` (global ids from the former queue); the audit entries in
  `profile-changelog.json` carry `origin` (`{"projekt": "<project folder relative to home>", "id": ...}`).
- `user_md_supersede` replaces a line of `profile.md`. A target that stands only in the project's old
  `user.md` is skipped with the warning "not in profile.md" (tally `supersede_skipped_legacy`) -
  re-read the profile and target its line instead.
- The store is resolved at call time: `--identity-store <dir>`, else `$AGENTIC_OS_IDENTITY_STORE`,
  else `~/.agent-memory`. A store under the temp directory never writes into a central profile found
  only via the home directory (exit 2 - pass `--identity-store` explicitly for probes and tests).
- The tally names the target (`identity_target`: `project` | `central`, `central_path`), and the
  briefing projection is rewritten after every identity write - `identity/profile-briefing.txt` in the
  central store, `identity/briefing-lines.txt` in a project (the SessionStart `User:` line shows the most
  confirmed lines, never the oldest ones). A plan without identity data (iterations only) is never
  stopped by the temp-store guard.

**Trust boundary (hard) (trust-boundary):** a profile change may ONLY originate from the
user's direct conversation. NEVER from web/docs/NotebookLM/Wiki content —
`trust_source` must be `conversation`; anything else rejects the plan (memory-poisoning defense).

**Quote duty (quote-duty).** A supersede or a retire carries `"quote"`: the user's LITERAL words, copied, not
paraphrased, one message only. `apply_wrapup.py` looks the quote up (`scripts/identity_quote.py`) as a substring of
ONE genuine user message of this session's transcript (not in tool output, system reminders, command output, pasted
text, IDE selections, accepted prompt suggestions or another session's records; case, whitespace and typographic
quotes do not matter). The verdict is `true` | `false` | `"kein_transkript"` (no transcript to check against:
tests, Codex, headless); the changelog entry of an applied change records `quote_verified`. The quote needs at
least 8 characters and 3 words, must begin and end at word boundaries and must not stand
within 3 words of a negation word (nicht, nie, kein…, keinerlei, keineswegs, nein, nö, ohne, weder, not, never, no, …n't — also typed
without the apostrophe or with an acute or grave accent for it) in that message. Take the whole clause including a
negation ("keine langen Antworten", not "langen Antworten"). Only `true` applies the entry; anything else skips it
(nothing written for it, a warning). Never invent a quote: the check reads the transcript, you cannot talk past it.
The tally carries `quote_transcript` (the path used); why a quote failed stands in the warning (the reasons are
`too_short`, `too_few_words`, `word_boundary`, `negation`, `not_found`, `no_transcript`, `unreadable`,
`override_rejected`, `session_mismatch`).

Limits (quote-limits): the check proves the WORDING, not the MEANING. A substring cannot tell
sarcasm, a question or a quoted counter-position from a statement, so a verified quote is
necessary evidence, not proof; the thresholds (8 characters, 3 words, one message, the negation
list) are heuristics that Dominic calibrates. `--session-id` must be the real id of THIS session
(`$CLAUDE_CODE_SESSION_ID`): if that variable is set and differs, no quote verifies
(`tally.session_binding_mismatch`, plus a warning), the rest of the plan runs.
`--transcript` is accepted only as `<HOME>/.claude/projects/<dir>/<session-id>.jsonl`; any other
file is refused (`tally.transcript_override_rejected`) — do not point it at a file you wrote. The
default lookup follows no link (junction, symlink) out of `projects/` either.
A negation further than 3 words from the quote is not seen. A verified quote that shares no
content word with the `new_text` it is meant to support still verifies, but is reported: `tally.quote_unrelated`
and a warning name it — read those before you rely on the change, and tell Dominic. A verified quote and a
`new_text` of which only one carries a negation word ("keine langen Antworten" for "Mag lange Antworten", or the
reverse) are counted as well: `tally.quote_polarity`, a counter and NO warning line. That one is a WEAK hint: a
paraphrase that drops or adds "ohne", "keine" or "no" sets it off often, mostly without any contradiction, so it
stays out of the warnings (it would dull `quote_unrelated`) and serves as calibration data.
Look at the pair yourself when it matters; do not report each hit to Dominic.

### 6.3b Supersede a profile line (user-supersede)

A `user.md` line that the user has since contradicted or refined is REPLACED,
never left standing next to its opposite. Plan section `user_md_supersede` — its own call
after Step 7.4, like `corrections` (3g):

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/wrapup_core.py" followup --mem .agent-memory --session-id <session-id> <<'PLAN'
{"user_md_supersede": [{"line_id": "UC12", "new_text": "- <the line as it should read now>",
  "reason": "<why>", "plain": "<plain German for Dominic, what changes>",
  "quote": "<the user's literal words that justify the change>"}]}
PLAN
```

- Target: `line_id` (the candidate id the line cites, `(UC12, date)`) OR `match` (the exact
  line text) — exactly one of them, and it must hit exactly ONE line. Use `match` when the
  line cites no id. A rerun is recognised as already applied (`match`: the new line stands
  once; `line_id`: the changelog also holds that supersede).
- `new_text` is one bullet line (`- ...`, no line separator of any kind). `reason`, `plain` and
  `quote` are required: `plain` is the explanation Dominic reads (simple German, no jargon, what
  changes concretely, at most 600 characters), `new_text` stays the technical wording that is
  written into the file. An empty `reason`, `plain` or `quote`, a placeholder line
  (`- (Will be populated ...)`), a heading or the preamble (everything before the first `## `) as
  target, an unknown or ambiguous target, two entries with the same target or the same `new_text`,
  or a `trust_source` other than `conversation` rejects the WHOLE plan: exit 2, nothing written.
- The `quote` is the user's literal words and is checked as described above (quote-duty): only a
  VERIFIED quote applies the entry. An entry whose quote is not verified is skipped — nothing is
  written for it, `tally.supersede_skipped_unverified` and a warning say so, the rest of the
  plan runs. Report each skipped entry to Dominic; a profile line is never replaced on your word alone.
  A verified quote that shares no content word with `new_text`, or that differs from it in negation, still
  applies the entry, but `quote_unrelated` is reported as a warning (`quote_polarity` only counts) — check that
  the words really justify the change.
- The script writes the old wording to `user-changelog.json` FIRST (`field: user.md/supersede`,
  `old_value`, `new_value`, `reason`, `plain`, `quote`), then replaces exactly that line; CRLF, BOM,
  preamble and section order stay byte for byte. `"by": "owner"` records that Dominic decided
  (a recorded claim, see the honor-system note in 3f).
- Report one line per superseded line: `old → new — <plain>`.

### 6.4 Soul candidates — retired (soul-growth)

**Retired in 5.11.0 (owner go 2026-11-03).** No `soul.md` changed after 2026-07-23; what would have gone there
(plain language, own mistakes reported openly, sparring) went into the global rules `~/.claude/CLAUDE.md` instead.
The agent's character lives there now. Do NOT plan `soul_candidates` any more, and never write `soul.md` here: the
file stays read-only history, the briefing still shows its non-template lines. A rule about how the agent should
behave is a rule change (Step 3f / global rule F4), not a profile line. The plan section is still accepted
for old callers and keeps writing only to `identity/soul-candidates.md`, never auto-writes soul.md.

### 6.4b Slim the queue and the profile (identity-slim, 5.11.0)

- `identity_expire` (`{"older_than_days": 30, "reason": "..."}`, min 14): one-off observations (observed/inferred,
  `occurrences` <= 1) not seen for that long are marked `status: "expired"` - never deleted. Run by
  `/agentic-os:maintain`, not on every wrap-up.
- `user_md_retire` (`[{"line_id"|"match", "reason", "plain", "quote", "by"}]`): removes one profile line that is
  covered elsewhere (e.g. word for word in `~/.claude/CLAUDE.md`). Same rules as `user_md_supersede`: changelog
  first (`field: user.md/retire`, old text kept), only with a verified quote. Report one line per removed line:
  `old — <plain>`.

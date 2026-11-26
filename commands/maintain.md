---
name: maintain
description: Pflegelauf over the learnings of .agent-memory/ - the agent judges each candidate itself (doppelt, überholt, gegenstandslos, verwandt, global, bleibt) and writes the verdicts through apply_wrapup.py in chunks of 30, stamp only after the last; plus the JSON repair, the only owner step. Never deletes or rewrites a learning (D-021). Run when the SessionStart briefing shows a PFLEGELAUF line, or on demand.
disable-model-invocation: true
allowed_tools: ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "ToolSearch", "mcp__agent-memory-atlas__memory_search_tool", "mcp__agent-memory-atlas__memory_related_tool"]
---

# Pflegelauf (memory maintenance)

Keeps the learnings of `.agent-memory/` reachable and lean: duplicates, outdated rows and rows about
a mechanism that is gone get a `superseded_by`, related rows join a rule pointer, rules worth having
everywhere are lifted to a global rule, the rest stays and gets its next review date. **You are the judge** - every verdict is
yours, reported in one line each. The owner gets no review cards and no yes/no question per item
(owner decision F4, 2026-11-03); the only owner step is the JSON repair (Step 4).

Trigger: the SessionStart briefing prints `PFLEGELAUF: …` once 30 learnings were added since the
last run (the threshold lives in the briefing's threshold script; this command does not recount
it). wrap-up only recommends this command. The run happens in a session, never headless.

**(no-archive) — D-021:** nothing is deleted, archived, moved or compressed, and no verdict rests on
age, count or importance alone. A superseded learning stays in `learnings.json`; projections,
`learnings_top` and the Atlas search filter it. Existence and `text` of a learning are untouchable;
only the metadata `superseded_by`, `review_after` and pointer membership change.

## Preconditions

- `.agent-memory/` exists - otherwise print "Memory system not initialized — run
  `/agentic-os:init` first" and stop.
- Snapshots (`scripts/store_snapshot.py`, byte copy outside the store): the FIRST chunk apply of a
  run (Step 3, also of a resumed run) takes ONE with `--snapshot` - under the store lock, after the plan
  check; if the copy fails, the applier stops with exit 2 before the first byte. That snapshot is the
  rollback point of the whole run, named in the report. Every later chunk apply of the run takes none:
  the snapshot folder keeps only the newest 10 plus the newest of each day, so per-chunk snapshots
  would prune the run's one on a run of 10 or more chunks; every single verdict stays reversible
  through `update_log` (`old`/`new`). The hand repair in Step 4 takes one first:
  `python "${CLAUDE_PLUGIN_ROOT}/scripts/store_snapshot.py" .agent-memory`.
- A candidate script that reports `learnings.json` unreadable (review_sweep exit 1,
  learnings_lifecycle exit 2): do Step 4 first, then start again at Step 1.

## Step 1: Candidates (read-only)

The run's rollback point is taken by the first chunk apply (Step 3), not here:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/review_sweep.py" .agent-memory --candidates-json
python "${CLAUDE_PLUGIN_ROOT}/scripts/learnings_lifecycle.py" propose .agent-memory --skip-reviewed
python "${CLAUDE_PLUGIN_ROOT}/scripts/learnings_lifecycle.py" report .agent-memory
```

Three sources, merged into one list of **subjects** (an id found by several sources is judged
once). **Resume (Atlas pair set only):** from the dated rows of the pair set (source 1), leave out
every row whose `update_log` already holds an entry with `by: "pflegelauf"` dated on or after the
`date` in `working/pflegelauf.json` (no stamp: any such entry) - an earlier, interrupted run judged
it. Never apply this rule to the candidates of sources 2 and 3: they leave out judged rows
themselves (a future `review_after`, `superseded_by`, pointer membership), and a `bleibt` row
whose `review_after` has passed is due again even though its log carries a pflegelauf entry.

1. **Atlas pairs.** Load both tools with ONE call:
   `ToolSearch` query `select:mcp__agent-memory-atlas__memory_search_tool,mcp__agent-memory-atlas__memory_related_tool`.
   The pair set: rows dated on or after the stamp `date` (no stamp: every live row), minus
   superseded rows, pointer rows (`kind: principle`), members of a live pointer and
   rows with `bridge_status` rejected or retired (`review_sweep --candidates-json` drops retired rows and owner
   rejections only, so an agent-rejected row can still arrive as a candidate of source 2);
   plus every candidate of sources 2 and 3. Call `memory_search_tool` per subject with the row's `summary`
   (else the first 200 characters of `text`), `source_system: "agent-memory"`,
   `scope: "project:<project folder name>"` (the folder that contains `.agent-memory`), `top_k: 5`,
   `snippet_len: 80`. The Atlas also returns hits from other stores flagged `"outside_filter": true`,
   even with `scope`: drop every hit with `outside_filter: true` first, then the self-hit and every hit
   that is no learning (e.g. `type: pattern`); a remaining hit that maps to a local `Lnn` and says the
   same thing or contradicts it forms a pair. The self-hit check uses only the in-scope hits (without
   `outside_filter: true`): if the self-hit is not among them, print `Scope nicht gefunden, Paarsuche übersprungen`
   once and continue without pairs. Use `memory_related_tool` on a hit's `record_id` only when the
   search alone does not settle whether two rows say the same. An unscoped search (no `scope`) runs
   only for a row you judge a "global" candidate, to collect cross-store evidence; hits from other
   stores are never a write target. If ToolSearch finds no Atlas tool or a call fails, print the line
   `Atlas nicht erreichbar, keine Paarsuche` and continue with sources 2 and 3.
2. **Fällig** - `review_sweep.py --candidates-json`: `[{"id", "reason"}]`, reason
   `due:YYYY-MM` (review date passed) or `ohne-termin` (no review date). Live pointers and their
   members are already left out.
3. **Nicht verankert** - `learnings_lifecycle.py propose --skip-reviewed`: eligible learnings that
   belong to no pointer and were not judged already (left out: a future `review_after`, or no
   `review_after` and a `pflegelauf` entry in `update_log`), with the top catalog sections (TF-IDF scores are hints, not verdicts). The catalog is
   `rule_catalog` in `~/.claude/agentic-os.local.json`. Exit 1 `no rule catalog`: source 3 and the
   verdict "global" are not available this run - say so in the report (`Lücke: kein rule_catalog`).

`report` gives `eligible`, `anchored`, `pointers` and `lost_anchors` (live pointers whose anchor
no longer resolves) for the report. Read a candidate's full row in `learnings/learnings.json`
(Grep for its id with context); never edit that file.

## Step 2: One verdict per candidate

Judge the content: what the learning says, how specific it is, what evidence it carries. Exactly
one verdict per candidate:

| Verdict | When | Write (Step 3 plan) |
|---|---|---|
| doppelt | two rows say the same | `learning_updates` `{"id": <weaker>, "superseded_by": <stronger>, "reason"}` - weaker = less specific, less evidence |
| überholt | a newer row replaces or corrects an older one | `learning_updates` `{"id": <older>, "superseded_by": <newer>, "reason"}` |
| gegenstandslos | the row describes a mechanism, file or rule that no longer exists and no other row replaces it; name what is gone in the reason | `learning_updates` `{"id", "obsolete": true, "reason"}` - the applier writes `superseded_by = "obsolete:pflegelauf-<plan date>"` |
| verwandt | the row belongs to an existing live pointer | `principles` entry with that pointer's anchor; the candidate joins `members` |
| global | the row is a rule worth having in every project | see "global" below |
| bleibt | none of the above | `learning_updates` `{"id", "review_after": <run date + 6 months>, "reason"}` |

- **gegenstandslos** never for a rule that still applies, even if the example it names is gone: then
  `bleibt` (or `überholt` when a newer row states the rule). Gone means removed code, a retired file or
  a dropped mechanism (e.g. the pattern sync), not "rarely needed". Never for an approved feedback row
  (`kind: feedback`, `bridge_status: approved` - an owner correction): nothing would carry its content,
  so only `doppelt`/`überholt` with a live successor (the applier refuses `obsolete` there).
- **bleibt** sets `review_after` to the run date plus exactly 6 months (same day of the month; if that
  day does not exist, the last day of that month). Never pick another date - it keeps the next run
  from showing the row again.
- A **pair** gives each of its two ids a verdict: the superseded one `doppelt`/`überholt`, the
  surviving one `bleibt` (or its own verdict when it is a candidate for another reason). A pair that
  turns out to say different things: both `bleibt`.
- **What the applier refuses** (the whole plan, exit 2, before the first byte - choose around it):
  `superseded_by` or `obsolete` on a rule-class learning (the flag or a rule tag `regel`/`rule`/
  `verfassung`/`constitution`, also on older rows without the flag), a pointer row or a member of a pointer (also
  of one the same plan writes) - for those only `bleibt`/`verwandt` remain, a pointer goes back only via
  `principle_retire`, a rule-class learning only via the rule gate. `superseded_by` or `obsolete` on a row
  that is the live successor of another row (some row's `superseded_by` points at it; also from an earlier
  chunk) - re-point that row in the same plan or judge `bleibt`. Approved feedback superseded by anything
  but approved feedback (MEMORY.md shows approved feedback only; the owner correction would vanish there).
  A target that is a pointer, has
  `bridge_status` rejected or retired, is an unreleased rule-class learning, is itself superseded
  or declared `obsolete` (point at the live successor, no chains), the id itself, or a pair that
  supersedes each other. `obsolete` on a row that is already superseded. `obsolete` on a source
  (`derived_from`) of a rule-class learning - a rule proposal of the same plan or a live one from an earlier
  chunk: the rule would lose its evidence; judge the source `bleibt` (or `doppelt`/`überholt` with a live
  successor).
  `reason` is 1..300 characters. `review_after` is allowed on every row.
- **verwandt** - a `principles` entry `{summary, anchor, members, plain, tags}` with the anchor of
  the existing pointer: the same anchor merges the new member in. Copy `summary`, **`plain`** and
  `tags` from the pointer row unchanged (a merge updates them; a `rid:` tag different from the
  pointer's is refused). Only local `Lnn` ids go into `members`.

### global

Needs `rule_catalog` (the rule files; private config, not in the plugin). Without it the verdict
is not available: judge the row `bleibt` and name it as `Lücke` in the report.

1. **The rule exists** in a catalog file (a `propose` match or your own reading) but has no pointer
   yet: a `principles` entry with anchor `<file>#<heading>` (or `<file>#^r-<slug>` when several
   rules share a heading - add that block id at the rule's line first) and the candidate as member.
2. **No rule yet:** a new rule needs evidence of >= 3 learnings from >= 2 stores (the Atlas hits of
   other stores count; only local `Lnn` ids go into `members`, the cross-store evidence goes into
   `plain` and the report line). Less evidence: `bleibt`, reported as `Lücke`.
   - **Not critical:** you decide (F4). Write the rule into the fitting catalog file with Edit
     (outside the store, no store writer), one rule at its own heading or with a `^r-<slug>` block
     id, then the `principles` entry pointing at it. If the plan later drops that pointer, undo the
     rule edit.
   - **Critical** - security, rights/accounts/secrets, deleting or data loss, owner gates, the
     constitution or rule precedence, identity, or the rule loosens a protection: write NO rule.
     Put the proposed rule into the plan as a new learning
     (`learnings`: `{"text", "summary", "importance", "tags", "derived_from": ["Lnn"], "rule_class": true}`)
     and give the candidate `review_after` (run date + 6 months, reason names the proposal). The
     rule-class learning waits for the existing rule gate (wrap-up Step 3f: second-model explanation,
     then the owner's go); say so in the report line.
- Every `principles` entry needs **`plain`**: the rule in plain German, no jargon, what changes
  concretely, at most 600 characters. The applier refuses a pointer without `plain` (empty, not a
  string or over 600 characters), an anchor that does not resolve, an unknown, qualified or pointer
  member, a rule on the hold list (`held_rules` in `~/.claude/agentic-os.local.json`), a stale
  `member_sha`, and a rule-class member that is not yet `released`.

## Step 3: Write - chunks of 30, dry run, apply, stamp last, projections

Every plan of one run carries the run's **start date** as `date` (the day Step 1 collected the candidates), also
in a session after midnight or a resumed run: the stamp takes that date, so a learning written while the run was
going is still in the next run's pair set. The stamp's count (`id_keys`) covers only the learnings dated before
that date, so the trigger counts such a learning as new as well (6.0.2).

Work through the subjects in **chunks of exactly 30** (the last chunk may be smaller): search,
judge (Step 2) and write one chunk before the next starts, so a compaction or an interrupted session
loses at most one chunk. Each chunk gets its own plan file outside the store (for example in the
system temp directory), a `--dry-run`, then the apply WITHOUT `pflegelauf_stamp`. The FIRST chunk apply
of the run (also of a resumed run) runs with `--snapshot`: the `snapshot` path in its output is the
`Rückholpunkt`. Note it in a file next to the chunk plans (outside the store, for example
`pflegelauf-rueckholpunkt.txt` in the system temp directory), so a compaction or a later session still
knows it; a resumed run appends its own path and reports BOTH. Every later chunk apply of the run runs
WITHOUT `--snapshot` (it would prune the run's snapshot).
A pair partner outside the chunk goes into the same plan as its subject.

```json
{
  "date": "YYYY-MM-DD",
  "learning_updates": [ { "id": "L12", "superseded_by": "L40", "reason": "..." },
                        { "id": "L17", "review_after": "YYYY-MM-DD", "reason": "..." },
                        { "id": "L21", "obsolete": true, "reason": "..." } ],
  "principles":       [ { "summary": "...", "anchor": "<file>#<heading>", "members": ["L23"],
                          "plain": "...", "tags": [] } ],
  "learnings":        [ { "text": "...", "summary": "...", "importance": 4, "tags": [],
                          "derived_from": ["L31"], "rule_class": true } ],
  "pflegelauf_stamp": true
}
```

Omit empty sections. `pflegelauf_stamp: true` goes ONLY into the final plan, written after every
pair-set row and every candidate of the run is done (the last chunk's plan, or a plan of its own
with nothing but `date` and the stamp when nothing is left to write). Never stamp a partial run:
the stamp resets the trigger and moves the pair-set start, so a stamp after an interrupted run
would hide every row not yet judged. A run that stops early writes no stamp; the next session
resumes (Step 1). Never put `principle_retire` into these plans.

```bash
# first chunk of the run (also of a resumed run) - its output's `snapshot` is the Rückholpunkt:
python "${CLAUDE_PLUGIN_ROOT}/scripts/apply_wrapup.py" .agent-memory --plan <first-chunk-plan.json> --dry-run
python "${CLAUDE_PLUGIN_ROOT}/scripts/apply_wrapup.py" .agent-memory --plan <first-chunk-plan.json> --snapshot
# every later chunk:
python "${CLAUDE_PLUGIN_ROOT}/scripts/apply_wrapup.py" .agent-memory --plan <chunk-plan.json> --dry-run
python "${CLAUDE_PLUGIN_ROOT}/scripts/apply_wrapup.py" .agent-memory --plan <chunk-plan.json>
# once at the end, after the final plan (the one with the stamp):
python "${CLAUDE_PLUGIN_ROOT}/scripts/memory_index_projection.py" .agent-memory --project-root .
python "${CLAUDE_PLUGIN_ROOT}/scripts/bridge_projection.py" .agent-memory --agents-md AGENTS.md
```

- Dry run exit 2 (`plan rejected: …`): the error names the entry. Change that verdict (usually to
  `bleibt`), fix the plan, dry-run again. Never write a store file by hand around a refusal.
- Apply only after a clean dry run. Report from the tallies (summed over the chunks), not by hand:
  `learning_updates` = `{superseded, cleared, review_after, obsolete}`, `learning_updates_unchanged`,
  `pflegelauf_stamp` = `{date, id_keys, zaehlung}`, `snapshot` (first chunk only: the Rückholpunkt), `principles_added` /
  `principles_updated` / `principle_ids`, and `learning_ids` for a critical rule proposal.
  `learnings_skipped_duplicate` > 0 means a proposal read as a duplicate of a live row and was not
  written: write the rule's wording, not the candidate's text, and report it as `Lücke` if it stays.
- Both projections run once, after the final plan: MEMORY.md (Claude) and AGENTS.md (Codex) show
  the pointers first. The AGENTS.md block is a projection, never edited by hand.
- **Way back:** a wrong `superseded_by` (also a wrong `gegenstandslos`) → `learning_updates`
  `{"id", "superseded_by": null, "reason"}`;
  a wrong `review_after` → `learning_updates` with the date before (each `update_log` entry of the
  run records `field`, `old` and `new`; a row that had no `review_after` before (`old: null`) cannot be set
  back to none - give it today's date, so the next run shows it again, or use the snapshot); a wrong pointer → plan section `principle_retire`
  (`{"ids": [...]}` or `{"tag": "condense:<date>"}`, plus `reason`) in its own plan; then both
  projections again. The whole run goes back to the snapshot of its first chunk apply (the
  `Rückholpunkt`) while it exists; otherwise through the `update_log` entries of the run (`old`/`new`).

## Step 4: JSON repair (the only owner step)

For `context/decisions.json`, `context/open-tasks.json`, `learnings/learnings.json`,
`working/current-session.json`, `working/user-candidates.json`, `identity/user-changelog.json`:
attempt to parse as `utf-8-sig` (a UTF-8 BOM is not corruption). The writers refuse to touch an
unreadable file (exit 2, file unchanged), so this step is the ONLY repair path - show the owner the
parse error first (it names line and column). **Repair in place** with the owner's OK: a truncated
tail, a stray comma or a wrong top-level shape is fixed in the file itself, so every row and id
survives. Take the snapshot of the Preconditions first; it skips `working/` and `identity/`, so
for a file there keep a byte copy `{file}.pre-repair-{YYYYMMDDHHMMSS}.bak` before the edit. Only if
the owner confirms the content is unrecoverable: keep it as `{file}.corrupt-{YYYYMMDDHHMMSS}.bak`,
start the file with its default (`[]`, or `{}` for `current-session.json`) and say in the report
that ids held only by the `.bak` can be issued again. Count repairs for the report.

The central profile (P2) is checked too: `python "${CLAUDE_PLUGIN_ROOT}/scripts/identity_store.py" check`
(read-only, JSON `{active, store, problems}`). With `active: true`, `profile-candidates.json` and
`profile-changelog.json` get the same parse-and-repair treatment as above (it is ONE profile for all
projects: repair only with the owner's OK and under no running wrap-up); an unreadable `profile.md` is
shown to the owner, never rewritten here; a missing or stale `profile-briefing.txt` is only reported.

## Step 5: Report (German)

```
Pflegelauf {date}:
  Kandidaten: {n} (fällig {a}, nicht verankert {b}, Atlas-Paare {c})
  {Atlas nicht erreichbar, keine Paarsuche | Scope nicht gefunden, Paarsuche übersprungen — only when it applied}
  L12 → doppelt — <Begründung>
  L21 → gegenstandslos — <Begründung: was es nicht mehr gibt>
  L17 → bleibt — <Begründung> (nächste Prüfung {review_after})
  L31 → global (kritisch) — <Begründung>; Regelvorschlag L{new} wartet im Regel-Gate (wrap-up Step 3f)
  …
  Urteile: {n} (doppelt {a}, überholt {b}, gegenstandslos {g}, verwandt {c}, global {d}, bleibt {e}); geschrieben: {superseded} ersetzt, {obsolete} gegenstandslos, {review_after} Prüftermine, {p} Zeiger; Rückholpunkt {run_snapshot}
  Lücken: {ids with reason | keine}
  Verankerung: {anchored}/{eligible}, {pointers} Zeiger, verlorene Anker: {ids | keine}
  JSON: {n}/{total} lesbar ({n_repaired} repariert); zentrales Profil: {inaktiv | ok | n Probleme}
```

One line per verdict, `Lnn → <urteil> — <Begründung>`, in plain German. Stop after the report.
Before printing `Rückholpunkt {run_snapshot}`, check the path exists
(`test -d "<path>" && echo vorhanden`); if it does not, print instead
`Rückholpunkt entfernt — Lauf nur über update_log (old/new) rückholbar`. A resumed run names both
paths (`Rückholpunkt {first_run_snapshot}, {resumed_snapshot}`), each checked the same way.
Do NOT suggest a commit: the snapshot of the first chunk apply (`Rückholpunkt`) is the rollback point.

## What NOT to Do

- Do NOT delete, archive, move or compress a learning or any other store entry (D-021)
- Do NOT change the `text` of a learning (text corrections belong to wrap-up `corrections`)
- Do NOT decide by age, count or importance - only by content
- Do NOT ask the owner per item (no review cards, no yes/no questions); only the JSON repair needs him
- Do NOT write a critical rule yourself - it goes through `rule_class` and the rule gate
- Do NOT write store files with Write/Edit - every store change goes through `apply_wrapup.py`
- Do NOT modify `identity/soul.md` or `identity/user.md`
- Do NOT combine with wrap-up into one call - wrap-up only recommends this command

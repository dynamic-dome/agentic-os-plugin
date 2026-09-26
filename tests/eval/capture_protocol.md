# Capture Protocol — Schicht 2 (behavioral, break-glass)

**When to run:** ONLY before an aggressive `session-bootstrap` redesign deploy, when
you do not trust Schicht 1 (`eval_signals.py` + `gate_linkage.py`) alone to prove
behavior preservation. This is NOT part of `run-all.sh` — it costs real agent runs
and is mildly non-deterministic.

**What it proves:** that an agent, given only the (redesigned) skill body plus a
fixture store, still fires the same gates / writes the same files / asks the same
questions as the frozen baseline. Schicht 1 proves the triggers still exist; this
proves the agent still *acts* on them.

## Procedure (per scenario)

1. **Stage** a throwaway copy of the fixture — never run against the real store:
   ```
   python -c "import shutil; shutil.copytree('tests/eval/fixtures/<scenario>', '<TMP>/mem')"
   ```
   For `recovery` and `conflict`, backdate the dirty markers >30 min (as
   `eval_signals.py` does) so the marker is a real crash, not mtime-protected.

1b. **Isolate — the capture MUST be hermetic.** The bootstrap body reads real
   cross-project sources (central handoff `~/AI/.agent-memory/session-summary.md`,
   status board `~/AI/cross-project-status.md`, the Atlas MCP, and optionally the
   Sharepoint `G:` drive). If any is reachable, `HANDOFF`, `CROSS-PROJECT`,
   RAG-learning results and Sharepoint-conflict lines become machine-dependent and
   the diff is meaningless. Before the run, make ALL of them unreachable:
   point the handoff/status paths at empty temp files (or a HOME with none),
   disconnect the Atlas MCP, and unmount/hide the `G:` drive. The baselines assume
   these blocks are ABSENT — a capture that still produces them is invalid, not a
   regression.

2. **Run the skill body** in a subagent whose ONLY instructions are the candidate
   `session-bootstrap` SKILL.md, pointed at `<TMP>/mem` as its `.agent-memory`.
   The subagent must NOT be given this repo's other context (isolate the body).
   For gate scenarios (`identity`), answer every gate prompt with **`n`** so no
   confirmed-write side effects fire — we are testing which gates *appear*, not
   what a `j` does.

3. **Emit the side-effect signature** as JSON (schema below). Capture:
   - `briefing_blocks`: which top-level briefing sections the agent produced.
   - `gates_fired`: which gates the agent surfaced (recovery, fast_path,
     soul_candidate, user_candidate, escalation, ...).
   - `files_written`: files the agent created/modified in `<TMP>/mem`
     (bootstrap is read-only except a `j`-confirmed soul write and an escalation
     append). **Normalize** any session-id in a path to the literal `<sid>`.
     **Exclude** `metrics/` writes (the fail-soft cost-trace append) — they are
     telemetry, not behavior, and `check_sideeffects.py` drops them anyway.
   - `json_keys_touched`: for each modified JSON file, which top-level keys changed.
   - `questions_asked`: which gate questions the agent posed (by gate name).

4. **Compare** to the frozen baseline:
   ```
   python tests/eval/check_sideeffects.py <scenario> <captured.json>
   ```
   Exit 0 = behavior preserved. Exit 1 = drift (inspect the listed diffs).

## Side-effect signature schema

```json
{
  "briefing_blocks": ["HANDOFF", "LAST SESSION", "RECOVERY", "HEALTH"],
  "gates_fired": {"recovery": true, "fast_path": false, "soul_candidate": false},
  "files_written": [{"path": "working/escalations-<sid>.json", "op": "append"}],
  "json_keys_touched": {"consolidation-marker.json": ["last_wrapup"]},
  "questions_asked": ["soul_candidate_gate"]
}
```

Diff semantics (in `check_sideeffects.py`): `gates_fired`, `files_written`,
`questions_asked` must match EXACTLY; `briefing_blocks` is a set match; free text
is ignored.

## Refreshing the baseline

The committed `baseline/<scenario>.json` files are the golden reference: the
CURRENT body's behavior. When you INTENTIONALLY change bootstrap behavior (not just
trim it), re-capture against the new body and overwrite the baseline in the same
commit. The `_provenance` field records whether a baseline is a real capture or a
hand-authored expected signature awaiting first capture.

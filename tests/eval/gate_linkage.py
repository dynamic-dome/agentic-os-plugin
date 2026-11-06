#!/usr/bin/env python3
"""Gate-linkage check for the skill-redesign harness (T-35).

The core protection against SILENT GATE LOSS during an aggressive body cut. Each
gate is expressed in CNF: a list of CLAUSES that must ALL hold; a clause is a list
of alternative tokens of which at least ONE must be present. So every load-bearing
conjunct of a gate (both trigger conditions AND the action) is mandatory, while a
single clause may offer alternative spellings for legitimate rephrasing.

    single token  -> a required conjunct with one canonical spelling
    [a, b] clause -> a required conjunct that may be phrased as a OR b

If a redesign drops any conjunct of a gate, that gate's clause goes unsatisfied
and this fails — forcing the move back (rule: "no gate logic leaves the body").

    python tests/eval/gate_linkage.py                 # check ALL covered skills
    python tests/eval/gate_linkage.py --selftest      # prove every clause has teeth
    python tests/eval/gate_linkage.py path/to/SKILL.md # check one body (skill from dir)

Substring matching, not regex — tokens contain *, `, <> and must match literally.

Maintenance: this list IS the gate inventory. Add a real gate here when one is
added to a covered skill; remove one here (same commit, stated in the message)
when it is intentionally removed. Derived from the bodies on 2026-07-18; bootstrap
extended after the Codex verifier review (CNF + 6 previously-missing gates);
wrap-up coverage added T-35 (before wrap-up is structurally cut).

T-028b cut it: the mechanics are code now, so wrap-up's surface is the body, its references and the core
(WRAPUP_SURFACE). A gate may live in any of them, but it must still be found: the rule "no gate logic leaves
the body" became "no gate logic leaves the surface". Removed on purpose in that commit: cross-session-rag
(owner scope decision 2026-09-08, D-018) and layer-lifecycle (prose without any executable path).

Design: memevalharness.md (membrain).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(HERE))


def skill_body(name):
    return os.path.join(PLUGIN_ROOT, "skills", name, "SKILL.md")


BOOTSTRAP = skill_body("session-bootstrap")
WRAPUP_SURFACE = [skill_body("wrap-up"),
                  os.path.join(PLUGIN_ROOT, "skills", "wrap-up", "references", "judge-rules.md"),
                  os.path.join(PLUGIN_ROOT, "skills", "wrap-up", "references", "handoff-template.md"),
                  os.path.join(PLUGIN_ROOT, "scripts", "wrapup_core.py"),
                  os.path.join(PLUGIN_ROOT, "scripts", "wrapup_parts", "harvest.py"),
                  os.path.join(PLUGIN_ROOT, "scripts", "wrapup_parts", "handoff.py")]

# gate = {"name", "clauses": [clause, ...]}; clause = [token, ...] (OR within).
# ALL clauses of a gate must be satisfied (AND across clauses).
BOOTSTRAP_GATES = [
    {"name": "fast-path", "clauses": [
        ["previous_state_hash == current_state_hash"],   # trigger: hashes equal
        ["changed_files` is empty"],                     # trigger: no dirty work
        ["Memory unchanged since last", "load ONLY"],    # action: slim load
    ]},
    {"name": "recovery-detect", "clauses": [
        ["dirty-*.json"],                                # trigger: crash markers
        ["30 minutes"],                                  # trigger: age grace
        ["RECOVERY"],                                    # action: surface block
        ["wrap-up ausführen"],                           # action: recommend fix
    ]},
    {"name": "recovery-tail-downgrade", "clauses": [
        ["writes_since_consolidation <= 5"],             # trigger: few tail writes
        ["120 minutes"],                                 # trigger: near consolidation (DCO-9209, widened from 15 min)
        ["`last_consolidated_by` is not"],               # trigger: never after a headless stamp (5.5.0)
        ["downgrade"],                                   # action: one-line note
    ]},
    {"name": "soul-candidate-gate", "clauses": [
        ["Soul candidate gate — retired"],               # 5.11.0: gate retired, no [j/n]
        ["never write `soul.md`"],                       # action: read-only without exception
    ]},
    {"name": "user-candidate-gate", "clauses": [
        ["user-candidates.json"],                        # trigger: candidate queue
        ["promotion"],                                   # action: promote to user.md
    ]},
    {"name": "identity-starvation", "clauses": [
        ["2+ sessions old"],                             # trigger: stale scan
        ["Pipeline verhungert"],                         # action: HEALTH warning
    ]},
    {"name": "thresholds", "clauses": [
        ["memory-thresholds.sh"],                        # trigger: SSoT script
        ["Exit 10"],                                     # trigger: scaling signal
        ["THRESHOLD:"],                                  # action: HEALTH lines
    ]},
    {"name": "escalation", "clauses": [
        ["escalations-<session-id>.json"],               # action: append record
        ["ESKALATION:"],                                 # action: visible line
    ]},
    {"name": "rag-learnings", "clauses": [
        ["memory_search_tool"],                          # trigger: Atlas primary
        ["learnings_top.py"],                            # action: heuristic fallback
    ]},
    {"name": "sharepoint-conflict", "clauses": [
        ["_conflicts/"],                                 # trigger: conflict files
        ["STOP"],                                        # action: halt, don't trust
    ]},
    # --- gates added after the Codex verifier review (were missing) ---
    {"name": "missing-memory-stop", "clauses": [
        ["should not happen"],                           # trigger: .agent-memory gone
        ["Memory system not found"],                     # action: stop message
    ]},
    {"name": "staleness-wrap-90d", "clauses": [
        ["90 days"],                                     # trigger: last_relevant age
        ["STALE? last relevant"],                        # action: read-time mark
    ]},
    {"name": "wiki-sync-gate", "clauses": [
        ["sync_enabled` is false"],                      # trigger: config off
        ["skip this step silently"],                     # action: skip, no error
    ]},
    # 5.2.1: the gate flipped - the bootstrap is read-only and the old "rename to
    # .corrupt.bak + recreate" emptied stores (a BOM sufficed). It now warns and
    # points to the one repair path.
    {"name": "json-repair", "clauses": [
        ["parse fails"],                                 # trigger: corrupt JSON
        ["Never rename"],                                # action: no rename / recreate
        ["Step 2 is the only repair path"],              # action: route to maintain
    ]},
    {"name": "active-warning-thresholds", "clauses": [
        ["confidence >= 0.7"],                           # trigger: pattern confidence
        ["occurrences >= 3"],                            # trigger: pattern recurrence
    ]},
    {"name": "cost-trace", "clauses": [
        ["cost-trace.sh"],                               # trigger: SSoT script
        ["context-bytes"],                               # action: log run trace
    ]},
]

# wrap-up has more WRITE gates than bootstrap; every one must survive. Tokens verified against
# WRAPUP_SURFACE on 2026-11-05 (T-028b).
WRAPUP_GATES = [
    {"name": "stage0-preprocess", "clauses": [
        ["preprocess_state.py"],                         # trigger: run preprocessor
        ["--session-id"],                                # trigger: session-scoped
        ["(stage0-preprocess)"],                         # action: the brief is the data source
    ]},
    {"name": "context-diet", "clauses": [
        ["(context-diet)"],                              # marker
        ["Do NOT systematically re-read"],               # action: no full re-scan
    ]},
    {"name": "session-harvest", "clauses": [
        ["(session-harvest)"],                           # marker
        ["harvest(project_root, mem, _since(mem, dirty))"],  # trigger: plan without iterations
        ["`iterations`"],                                # action: into the plan
    ]},
    {"name": "stale-session-harvest", "clauses": [
        ["recovered_from"],                              # action: marked reconstruction
        ["_dirty_sessions"],                             # trigger: every dirty session, not only this one
    ]},
    {"name": "learnings-dedup-jaccard", "clauses": [
        ["(dedup-in-code)"],                             # marker
        ["Jaccard >= 0.6"],                              # trigger: threshold
        ["duplicate_of"],                                # action: the judge's override
    ]},
    {"name": "bridge-gate", "clauses": [
        ["bridge_candidates"],                           # trigger: open candidates
        ["Approve only if ALL hold"],                    # judgment: delegated bridge test (5.7.0)
        ["bridge_decisions"],                            # action: verdict + reason via the applier
        ["bridge_status_line"],                          # contract: mandatory report
        ['out["projections"] = _projections('],          # trigger: projections after the verdicts
        ["bridge_projection.py"],                        # action: projection run
    ]},
    {"name": "pattern-extraction", "clauses": [
        ['"extract_patterns.py", [mem, "--update"]'],    # action: run the extractor script
        ["pattern_proposals"],                           # action: unnamed clusters go to the report
    ]},
    {"name": "decision-scan", "clauses": [
        ["(decision-scan)"],                             # marker
        ["Plan section `decisions`"],                    # action: into the plan (T-015)
    ]},
    {"name": "session-summary-cap", "clauses": [
        ["up to 8 lines"],                               # contract: bounded summary
    ]},
    {"name": "open-tasks-ssot", "clauses": [
        ["(open-tasks-ssot)"],                           # marker
        ["single source of truth"],                      # contract: SSoT
        ["cross_project"],                               # action: flag feeds handoff
    ]},
    {"name": "identity-harvest", "clauses": [
        ["(identity-harvest)"],                          # marker
        ["(user-growth)"],                               # marker
        ["none_because"],                                # action: an empty scan must say why
    ]},
    {"name": "trust-boundary", "clauses": [
        ["(trust-boundary)"],                            # marker
        ["conversation"],                                # trigger: only source allowed
        ["web/docs/NotebookLM/Wiki"],                    # action: discard poisoned
    ]},
    {"name": "queue-re-review", "clauses": [
        ["(queue-re-review)"],                           # marker
        ["user-changelog.json"],                         # action: changelog before edit
        ["confidence >= 0.6"],                           # trigger: promotion gate
    ]},
    {"name": "soul-growth", "clauses": [
        ["(soul-growth)"],                               # marker
        ["Retired in 5.11.0"],                           # 5.11.0: no soul candidates planned
        ["never write `soul.md` here"],                  # action: soul.md stays read-only
    ]},
    {"name": "identity-visible", "clauses": [
        ["(identity-visible)"],                          # marker
        ["Identity: {n} beobachtet"],                    # action: mandatory status line
    ]},
    {"name": "wiki-sync-gate", "clauses": [
        ["(wiki-sync-gate)"],                            # marker
        ['enabled=bool(cfg.get("sync_enabled"))'],       # trigger: config on
        ["obsidian-sync"],                               # action: everything beyond the note stays manual
    ]},
    {"name": "wiki-sync-visible", "clauses": [
        ["(wiki-sync-visible)"],                         # marker
        ["Wiki-Sync:"],                                  # action: always-report line
    ]},
    {"name": "central-summary", "clauses": [
        ["AI workspace"],                                # trigger: summary IS the handoff
        ["handoff, the block arrives through the core"], # action: not from the file
    ]},
    {"name": "handoff-guard", "clauses": [
        ["handoff_write_guard"],                         # trigger: read-modify-write
        ["_guarded_write"],                              # action: guard cycle in process
        ["drift-merged"],                                # action: re-read and merge on drift
    ]},
    {"name": "handoff-dedup", "clauses": [
        ["Ownership-dedup"],                             # marker
        ["at most one block per project"],               # action: ownership dedup
        ["5 blocks total"],                              # action: hard cap
    ]},
    {"name": "next-steps-pointer", "clauses": [
        ["Naechste Schritte = pointer"],                 # contract
        ["Projekt-Next-Steps:"],                         # action: pointer, not copy
    ]},
    {"name": "git-commit-confirm", "clauses": [
        ["git add -A"],                                  # trigger: never blanket-stage
        ["wait for confirmation"],                       # action: no commit without OK
    ]},
    {"name": "consolidation-marker", "clauses": [
        ["aw.apply_consolidation("],                     # action: write marker
        ["marker LAST"],                                 # contract: nothing after it but the trace
        ["do NOT write the consolidation marker"],       # action: never by hand
        ["consolidation marker NOT written"],            # action: honest dirty on fail
    ]},
    {"name": "thresholds", "clauses": [
        ["memory-thresholds.sh"],                        # trigger: SSoT script
        ["threshold_lines"],                             # trigger: scaling signal in the report
        ["/agentic-os:maintain"],                        # action: recommend cleanup command
    ]},
    {"name": "escalation-rules", "clauses": [
        ["escalations-<session-id>.json"],               # action: append record
        ["ESKALATION:"],                                 # action: visible line
    ]},
    {"name": "cost-trace", "clauses": [
        ["cost-trace.sh"],                               # trigger: SSoT script
        ["--write-hash"],                                # action: refresh state hash
    ]},
]

# skill dir name -> (body path, gate inventory)
SKILLS = {
    "session-bootstrap": (BOOTSTRAP, BOOTSTRAP_GATES),
    "wrap-up": (WRAPUP_SURFACE, WRAPUP_GATES),
}

# Back-compat alias for anything importing GATES from this module.
GATES = BOOTSTRAP_GATES

TESTS = 0
FAILS = 0


def check(cond, label):
    global TESTS, FAILS
    TESTS += 1
    if cond:
        print(f"  PASS: {label}")
    else:
        FAILS += 1
        print(f"  FAIL: {label}")


def clause_satisfied(clause, body):
    return any(tok in body for tok in clause)


def gate_ok(gate, body):
    """True iff every clause of the gate is satisfied."""
    return all(clause_satisfied(c, body) for c in gate["clauses"])


def run_checks(body, label, gates):
    print(f"=== Gate-Linkage — {label} (CNF: every conjunct mandatory) ===")
    for gate in gates:
        for i, clause in enumerate(gate["clauses"]):
            hit = [t for t in clause if t in body]
            check(len(hit) >= 1,
                  f"{gate['name']}: clause {i} present ({hit or clause})")
    print(f"  ({len(gates)} gates checked in {label})\n")


def selftest(body, label, gates):
    """Prove every clause is load-bearing: removing any single clause's tokens
    must make its gate fail. Guards against the OR-within-gate hole the Codex
    review found."""
    print(f"=== Gate-Linkage SELFTEST — {label} "
          "(single-conjunct-loss must be caught) ===")
    global TESTS, FAILS
    for gate in gates:
        # sanity: gate must pass on the untouched body first
        if not gate_ok(gate, body):
            TESTS += 1
            FAILS += 1
            print(f"  FAIL: {gate['name']}: does not hold on current body")
            continue
        for i, clause in enumerate(gate["clauses"]):
            mutated = body
            for tok in clause:
                mutated = mutated.replace(tok, "<<removed>>")
            TESTS += 1
            if gate_ok(gate, mutated):
                FAILS += 1
                print(f"  FAIL: {gate['name']} clause {i} NOT load-bearing "
                      f"(gate still passes without {clause})")
            else:
                print(f"  PASS: {gate['name']} clause {i} load-bearing")


def read_body(path):
    if isinstance(path, list):  # a surface of several files
        return "\n".join(read_body(p) for p in path)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def main():
    # Some gate tokens are exact body substrings containing non-cp1252 chars
    # (e.g. "→"); force utf-8 stdout so printing them never crashes on a Windows
    # console (CLAUDE.md Windows-subprocess rule: explicit utf-8).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = sys.argv[1:]

    if args and args[0] == "--selftest":
        for name, (path, gates) in SKILLS.items():
            selftest(read_body(path), name, gates)
    elif args:
        body_path = args[0]
        skill = os.path.basename(os.path.dirname(body_path))
        if skill not in SKILLS:
            print(f"  FAIL: unknown skill '{skill}' for path {body_path} — "
                  f"known: {', '.join(SKILLS)}")
            sys.exit(1)
        run_checks(read_body(body_path), skill, SKILLS[skill][1])
    else:
        for name, (path, gates) in SKILLS.items():
            run_checks(read_body(path), name, gates)

    total = TESTS - FAILS
    print(f"{total}/{TESTS} clause checks passed.")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()

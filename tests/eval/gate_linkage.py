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

Design: memevalharness.md (membrain).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(HERE))


def skill_body(name):
    return os.path.join(PLUGIN_ROOT, "skills", name, "SKILL.md")


BOOTSTRAP = skill_body("session-bootstrap")
WRAPUP = skill_body("wrap-up")

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
        ["15 minutes"],                                  # trigger: near consolidation
        ["downgrade"],                                   # action: one-line note
    ]},
    {"name": "soul-candidate-gate", "clauses": [
        ["soul-candidates.md"],                          # trigger: candidate queue
        ["explicit `j`"],                                # trigger: user confirmation
        ["user-changelog.json"],                         # action: audit trail
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
    {"name": "json-repair", "clauses": [
        ["rename to"],                                   # trigger: corrupt JSON
        [".corrupt.bak"],                                # action: backup + recreate
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

# wrap-up is the bigger structural lever but has more WRITE gates; every one must
# survive a body cut. Tokens verified present in skills/wrap-up/SKILL.md (2026-07-18).
WRAPUP_GATES = [
    {"name": "stage0-preprocess", "clauses": [
        ["preprocess_state.py"],                         # trigger: run preprocessor
        ["--session-id"],                                # trigger: session-scoped
        ["PRIMARY data source"],                         # action: use its state object
    ]},
    {"name": "context-diet", "clauses": [
        ["(context-diet)"],                              # marker
        ["Do NOT systematically re-read"],               # action: no full re-scan
    ]},
    {"name": "session-harvest", "clauses": [
        ["(session-harvest)"],                           # marker
        ["git log --oneline --since=midnight"],          # trigger: substantial work
        ["`iterations` array"],                          # action: into the write plan (T-015)
    ]},
    {"name": "stale-session-harvest", "clauses": [
        ["is NOT this session"],                         # trigger: crashed session
        ["recovered from session"],                      # action: marked reconstruction
    ]},
    {"name": "learnings-dedup-jaccard", "clauses": [
        ["Jaccard similarity"],                          # trigger: local dedup
        ["0.6 → duplicate"],                             # trigger: threshold
        ["last_relevant"],                               # action: bump, skip create
    ]},
    {"name": "cross-session-rag", "clauses": [
        ["memory_search_tool"],                          # trigger: Atlas dedup
        ['source_system="agent-memory"'],                # trigger: own-project scope
        ["Fail-soft"],                                   # action: never block wrap-up
    ]},
    {"name": "bridge-gate", "clauses": [
        ["bridge_status"],                               # trigger: candidate field
        ["BRIDGE CANDIDATES"],                           # action: [j/n] prompt
        ["explicit `j`"],                                # trigger: user confirmation
        ["bridge_projection.py"],                        # action: projection run
    ]},
    {"name": "layer-lifecycle", "clauses": [
        ["older than 30 days"],                          # trigger: short-term age
        ["archive-candidate"],                           # action: demote
    ]},
    {"name": "pattern-extraction", "clauses": [
        ["3+ new iterations"],                           # trigger: enough new data
        ["extract_patterns.py"],                         # action: run the extractor script
    ]},
    {"name": "decision-scan", "clauses": [
        ["(decision-scan)"],                             # marker
        ["`decisions` array"],                           # action: into the write plan (T-015)
    ]},
    {"name": "session-summary-cap", "clauses": [
        ["max 30 lines"],                                # contract: bounded summary
    ]},
    {"name": "open-tasks-ssot", "clauses": [
        ["(open-tasks-ssot)"],                           # marker
        ["single source of truth"],                      # contract: SSoT
        ["cross_project"],                               # action: flag feeds handoff
    ]},
    {"name": "identity-harvest", "clauses": [
        ["(identity-harvest)"],                          # marker
        ["(user-growth)"],                               # marker
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
        ["soul-candidates.md"],                          # action: propose only
        ["Never write `soul.md`"],                       # action: Stufe-B guard
    ]},
    {"name": "identity-visible", "clauses": [
        ["(identity-visible)"],                          # marker
        ["Identity: {n} beobachtet"],                    # action: mandatory status line
    ]},
    {"name": "wiki-sync-gate", "clauses": [
        ["(wiki-sync-gate)"],                            # marker
        ["sync_enabled: true"],                          # trigger: config on
        ["obsidian-sync"],                               # action: delegate writes
    ]},
    {"name": "wiki-sync-visible", "clauses": [
        ["(wiki-sync-visible)"],                         # marker
        ["Wiki-Sync:"],                                  # action: always-report line
    ]},
    {"name": "handoff-guard", "clauses": [
        ["handoff_write_guard.py"],                      # trigger: read-modify-write
        ["Exit 20"],                                     # trigger: drift detected
        ["snapshot"],                                    # action: guard cycle
    ]},
    {"name": "handoff-dedup", "clauses": [
        ["(handoff-dedup)"],                             # marker
        ["at most one block per project"],               # action: ownership dedup
        ["5 blocks total"],                              # action: hard cap
    ]},
    {"name": "next-steps-pointer", "clauses": [
        ["(next-steps-pointer)"],                        # marker
        ["pointer"],                                     # action: pointer, not copy
    ]},
    {"name": "git-commit-confirm", "clauses": [
        ["git add -A"],                                  # trigger: never blanket-stage
        ["wait for confirmation"],                       # action: no commit without OK
    ]},
    {"name": "consolidation-marker", "clauses": [
        ["(consolidation-marker)"],                      # marker
        ["consolidation-marker.json"],                   # action: write marker
        ["consolidated_by"],                             # action: flag dirty files
        ["do NOT write the marker"],                     # action: honest dirty on fail
    ]},
    {"name": "thresholds", "clauses": [
        ["memory-thresholds.sh"],                        # trigger: SSoT script
        ["Exit 10"],                                     # trigger: scaling signal
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
    "wrap-up": (WRAPUP, WRAPUP_GATES),
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

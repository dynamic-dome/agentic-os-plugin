#!/usr/bin/env python3
"""Retrieval golden set — makes the authority matrix measurable (membrain T-42).

The authority matrix (which source LEADS for which question type) is canonical
prose in `memauthority.md`, but nothing ever tested it. Semantic similarity is not
authority: a stale card or a rendered handoff can out-rank the store that actually
owns the answer, and no check would notice.

Two layers, mirroring the rest of this harness:

    Schicht 1 (CI, deterministic) — validate the golden set itself:
        * every case is complete and internally consistent
        * every named source exists in the registry
        * every `kind: store` source is ANCHORED in skills/DEPENDENCIES.md, so a
          store rename turns this red instead of silently invalidating the set
        * the wrong-pick is a real, plausible source — not a strawman
        * the required question types are all covered

    Schicht 2 (break-glass, NOT in CI) — score a recorded run:
        python retrieval_golden.py --score answers.json
        answers.json = {"answers": {"R01": "open-tasks", ...}}
        Exits 1 on any mismatch; hits on `typical_wrong_pick` are called out
        separately, because those are the failure modes the set was built to catch.

Anchor limitation (stated on purpose): store anchors match on BASENAME. A rename
(`patterns.json` -> `pattern-store.json`) breaks the test as intended; a pure move
between directories does not. Path-level anchoring would couple this file to the
directory layout, which `mem-schema.sh` owns.

    python tests/eval/retrieval_golden.py             # Schicht 1
    python tests/eval/retrieval_golden.py --selftest  # prove every rule has teeth
    python tests/eval/retrieval_golden.py --score answers.json

Design: memevalharness.md / memperfectflowharvest.md (membrain).
"""
import copy
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(HERE))
GOLDEN = os.path.join(HERE, "retrieval_golden.json")
DEPS = os.path.join(PLUGIN_ROOT, "skills", "DEPENDENCIES.md")

# A set that skips these has holes exactly where the matrix is hardest.
REQUIRED_TYPES = {
    "next-steps",
    "architecture-decision",
    "project-state",
    "handoff",
    "past-error",
    "experience",
    "capability",
    "user-preference",
    "identity",
    "concept",
    "backflow",
}
MIN_CASES = 18
CASE_FIELDS = (
    "id",
    "question",
    "question_type",
    "expected_leading_source",
    "allowed_supporting",
    "typical_wrong_pick",
    "why",
)


def load(path=GOLDEN):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def validate(data, deps_text):
    """Return a list of failure strings (empty = valid)."""
    errors = []
    sources = data.get("sources") or {}
    cases = data.get("cases") or []

    if not data.get("_provenance"):
        errors.append("golden set must carry _provenance (who/when/canon)")

    if len(cases) < MIN_CASES:
        errors.append("golden set has %d cases, need >= %d" % (len(cases), MIN_CASES))

    # --- sources registry ---------------------------------------------------
    for name, meta in sorted(sources.items()):
        kind = meta.get("kind")
        if kind not in ("store", "artifact", "index"):
            errors.append("source %s: kind must be store|artifact|index" % name)
        if kind == "store":
            anchor = meta.get("anchor")
            if not anchor:
                errors.append("source %s: store needs an anchor" % name)
            elif anchor not in deps_text:
                errors.append(
                    "source %s: anchor %r not found in DEPENDENCIES.md — store renamed "
                    "or golden set stale" % (name, anchor)
                )
        elif not meta.get("rationale"):
            errors.append(
                "source %s: non-store source needs a rationale (why it may lead at all)" % name
            )

    # --- cases --------------------------------------------------------------
    seen_ids = set()
    seen_types = set()
    used_sources = set()

    for case in cases:
        cid = case.get("id", "<no id>")
        for field in CASE_FIELDS:
            if not case.get(field):
                errors.append("%s: missing field %s" % (cid, field))
        if cid in seen_ids:
            errors.append("%s: duplicate case id" % cid)
        seen_ids.add(cid)
        seen_types.add(case.get("question_type"))

        expected = case.get("expected_leading_source")
        wrong = case.get("typical_wrong_pick")
        supporting = case.get("allowed_supporting") or []
        if not isinstance(supporting, list):
            errors.append("%s: allowed_supporting must be a list" % cid)
            supporting = []

        for ref in [expected, wrong] + list(supporting):
            if ref and ref not in sources:
                errors.append("%s: unknown source %r (not in registry)" % (cid, ref))
            elif ref:
                used_sources.add(ref)

        if expected and expected == wrong:
            errors.append("%s: typical_wrong_pick equals the expected source" % cid)
        if expected and expected in supporting:
            errors.append(
                "%s: the leading source must not also be listed as supporting" % cid
            )
    missing_types = REQUIRED_TYPES - seen_types
    if missing_types:
        errors.append("uncovered question types: %s" % ", ".join(sorted(missing_types)))

    dead = set(sources) - used_sources
    if dead:
        errors.append(
            "registry entries never used by any case: %s" % ", ".join(sorted(dead))
        )

    return errors


def score(data, answers_path):
    with io.open(answers_path, encoding="utf-8") as fh:
        payload = json.load(fh)
    answers = payload.get("answers") or {}
    cases = {c["id"]: c for c in data["cases"]}

    missing = sorted(set(cases) - set(answers))
    wrong_pick_hits, plain_misses, ok = [], [], 0

    for cid, chosen in sorted(answers.items()):
        case = cases.get(cid)
        if case is None:
            plain_misses.append("%s: unknown case id" % cid)
            continue
        if chosen == case["expected_leading_source"]:
            ok += 1
        elif chosen == case["typical_wrong_pick"]:
            wrong_pick_hits.append(
                "%s: picked the KNOWN trap %r instead of %r — %s"
                % (cid, chosen, case["expected_leading_source"], case["why"])
            )
        else:
            plain_misses.append(
                "%s: picked %r, expected %r" % (cid, chosen, case["expected_leading_source"])
            )

    print("RETRIEVAL GOLDEN — %d/%d correct" % (ok, len(cases)))
    for line in wrong_pick_hits:
        print("  TRAP  %s" % line)
    for line in plain_misses:
        print("  MISS  %s" % line)
    for cid in missing:
        print("  GAP   %s: no answer recorded" % cid)

    return 0 if (ok == len(cases) and not missing) else 1


def selftest(data, deps_text):
    """Every rule must have teeth: break the set one way at a time, expect a failure."""
    mutations = [
        ("drop provenance", lambda d: d.pop("_provenance", None)),
        ("unknown source", lambda d: d["cases"][0].__setitem__("expected_leading_source", "nope")),
        ("wrong == expected", lambda d: d["cases"][0].__setitem__(
            "typical_wrong_pick", d["cases"][0]["expected_leading_source"])),
        ("leading also supporting", lambda d: d["cases"][0]["allowed_supporting"].append(
            d["cases"][0]["expected_leading_source"])),
        ("missing why", lambda d: d["cases"][0].__setitem__("why", "")),
        ("duplicate id", lambda d: d["cases"].append(copy.deepcopy(d["cases"][0]))),
        ("store anchor renamed", lambda d: d["sources"]["patterns"].__setitem__(
            "anchor", "pattern-store.json")),
        ("non-store without rationale", lambda d: d["sources"]["skill-file"].pop("rationale", None)),
        ("too few cases", lambda d: d.__setitem__("cases", d["cases"][:3])),
        ("dead registry entry", lambda d: d["sources"].__setitem__(
            "ghost", {"kind": "index", "rationale": "never referenced"})),
    ]
    failures = []
    for name, mutate in mutations:
        broken = copy.deepcopy(data)
        mutate(broken)
        if not validate(broken, deps_text):
            failures.append("mutation %r was NOT caught — rule is toothless" % name)

    # Uncovered type needs its own mutation (drop every case of one type).
    broken = copy.deepcopy(data)
    broken["cases"] = [c for c in broken["cases"] if c["question_type"] != "capability"]
    if not any("uncovered question types" in e for e in validate(broken, deps_text)):
        failures.append("mutation 'drop capability cases' was NOT caught")

    if failures:
        for line in failures:
            print("FAIL: %s" % line)
        return 1
    print("PASS: retrieval golden selftest (%d mutations, all caught)" % (len(mutations) + 1))
    return 0


def main(argv):
    data = load()
    with io.open(DEPS, encoding="utf-8") as fh:
        deps_text = fh.read()

    if "--score" in argv:
        idx = argv.index("--score")
        if idx + 1 >= len(argv):
            print("FAIL: --score needs an answers.json path")
            return 1
        return score(data, argv[idx + 1])

    if "--selftest" in argv:
        return selftest(data, deps_text)

    errors = validate(data, deps_text)
    if errors:
        for line in errors:
            print("FAIL: %s" % line)
        return 1
    print(
        "PASS: retrieval golden set (%d cases, %d sources, %d question types, store anchors live)"
        % (
            len(data["cases"]),
            len(data["sources"]),
            len({c["question_type"] for c in data["cases"]}),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

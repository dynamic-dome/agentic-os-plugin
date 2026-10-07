#!/usr/bin/env python3
"""Tests for scripts/ingest_codex_memory.py (E1 Codex -> learnings.json).
Run: python tests/test-ingest-codex-memory.py  (exit 0 = pass)"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PLUGIN_ROOT, "scripts", "ingest_codex_memory.py")
FIXTURE = os.path.join(PLUGIN_ROOT, "tests", "fixtures", "codex-memory-summary.md")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "scripts"))
FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS: {name}")
    else:
        print(f"  FAIL: {name} {detail}")
        FAILURES.append(name)


def run(args, cwd):
    return subprocess.run([sys.executable, SCRIPT] + args, capture_output=True,
                          encoding="utf-8", errors="replace", cwd=cwd)


def setup(tmp, learnings):
    mem = os.path.join(tmp, ".agent-memory")
    os.makedirs(os.path.join(mem, "learnings"), exist_ok=True)
    with open(os.path.join(mem, "learnings", "learnings.json"), "w", encoding="utf-8") as f:
        json.dump(learnings, f, ensure_ascii=False)
    codex = os.path.join(tmp, "codex-memories")
    os.makedirs(codex)
    shutil.copy(FIXTURE, os.path.join(codex, "memory_summary.md"))
    return mem, codex


def load(mem):
    with open(os.path.join(mem, "learnings", "learnings.json"), encoding="utf-8") as f:
        return json.load(f)


def main():
    print("=== ingest_codex_memory.py tests ===")
    check("script exists", os.path.isfile(SCRIPT))
    if not os.path.isfile(SCRIPT):
        print("=== 1 failure (script missing) ===")
        return 1
    from ingest_codex_memory import norm as ingest_norm
    from apply_wrapup import norm as wrapup_norm
    for s in ["Hello, World!", "  Über-Prüfung: `code` [x]  ", "a  b\tc"]:
        check(f"norm identical to apply_wrapup: {s!r}", ingest_norm(s) == wrapup_norm(s))

    with tempfile.TemporaryDirectory() as tmp:
        existing = [{"id": "L3", "date": "2026-08-01", "text": "Old claude learning.", "importance": 3,
                     "tags": [], "layer": "short-term", "superseded_by": None, "last_relevant": "2026-08-01"},
                    {"id": "L7", "date": "2026-08-02", "importance": 3, "tags": [], "layer": "short-term",
                     "superseded_by": None, "last_relevant": "2026-08-02",
                     "text": "Treat exact file boundaries and STOP instructions as hard constraints."}]
        mem, codex = setup(tmp, existing)

        # dry-run: reports, writes nothing
        p = run([mem, "--codex-memories", codex, "--dry-run"], cwd=tmp)
        check("dry-run exit 0", p.returncode == 0, p.stderr[:300])
        check("dry-run line", p.stdout.strip().startswith("codex-ingest: 4 new, 1 dup, "), p.stdout)
        check("dry-run no write", len(load(mem)) == 2)

        # apply
        p = run([mem, "--codex-memories", codex], cwd=tmp)
        check("apply exit 0", p.returncode == 0, p.stderr[:300])
        rows = load(mem)
        check("4 added", len(rows) == 6, str(len(rows)))
        new = [r for r in rows if r.get("source_agent") == "codex"]
        check("all new are codex candidates", len(new) == 4 and all(r.get("bridge_status") == "candidate" for r in new))
        ids = [r["id"] for r in new]
        check("ids continue L numbering", ids == ["L8", "L9", "L10", "L11"], str(ids))
        kinds = {r["text"][:20]: r["kind"] for r in new}
        check("preferences -> feedback", kinds.get("Start substantial wo") == "feedback", str(kinds))
        check("tips -> learning", kinds.get("Verify an installed ") == "learning", str(kinds))
        adhoc = [r for r in new if r["text"].startswith("Start substantial")][0]
        check("ad-hoc marker stripped + tagged", "[ad-hoc note]" not in adhoc["text"] and "ad-hoc" in adhoc["tags"], str(adhoc))
        check("nested + episodic lines ignored", not any("nested detail" in r["text"] or "Example topic" in r["text"] for r in rows))
        check("provenance hash", all(r["derived_from"][0].startswith("codex:memory_summary:") and len(r["derived_from"][0]) == len("codex:memory_summary:") + 8 for r in new))
        check("importance 2, review_after set", all(r["importance"] == 2 and r.get("review_after") for r in new))
        dup = [r for r in rows if r["id"] == "L7"][0]
        check("dup only last_relevant touched", dup["last_relevant"] == new[0]["date"] and "source_agent" not in dup, str(dup))
        old = [r for r in rows if r["id"] == "L3"][0]
        check("untouched old entry keeps no new fields", "source_agent" not in old and "kind" not in old)
        check("learnings.md regenerated", os.path.isfile(os.path.join(mem, "learnings", "learnings.md")))

        # idempotent
        p = run([mem, "--codex-memories", codex], cwd=tmp)
        check("second run 0 new", p.stdout.strip().startswith("codex-ingest: 0 new, 5 dup"), p.stdout)
        check("second run no growth", len(load(mem)) == 6)
        rows = load(mem)
        check("T-52 origin=agent on codex entries", all(r.get("origin") == "agent" for r in rows if r.get("source_agent") == "codex"))
        check("T-52 no origin on non-codex entries", all("origin" not in r for r in rows if r["id"] in ("L3", "L7")))

        # snapshot-sync: Codex rewrote its summary (Codex's current text is canon)
        by_id = {r["id"]: r for r in rows}
        rag, terse, cli, shot = (by_id[i] for i in ("L8", "L9", "L10", "L11"))
        check("fixture order as expected", rag["text"].startswith("Start substantial") and terse["text"].startswith("For a terse")
              and cli["text"].startswith("Verify an installed") and shot["text"].startswith("Before overwriting"), str(ids))
        for r in rows:
            if r["id"] in ("L8", "L11"):
                r["bridge_status"] = "approved"
        rows.append({"id": "L12", "date": "2026-08-03", "text": "Verify an installed CLI through its resolved shim, installed package, and upstream version — never one signal alone.", "importance": 2, "tags": [],
                     "layer": "short-term", "superseded_by": None, "last_relevant": "2026-08-03",
                     "derived_from": ["codex:memory_summary:deadbeef"], "bridge_status": "rejected",
                     "source_agent": "codex", "kind": "learning", "bridge_note": "owner said no"})
        with open(os.path.join(mem, "learnings", "learnings.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        v2 = ("v2\n\n## User preferences\n\n"
              "- Start substantial work RAG-first: check the local wiki and memory before planning. [ad-hoc note]\n"
              "- Treat exact file boundaries and STOP instructions as hard constraints.\n\n"
              "## General Tips\n\n"
              "- Verify an installed CLI through its resolved shim, installed package, and upstream version — not one signal alone.\n"
              "- Before a screenshot write, prove the page is loaded and not `about:blank`.\n")
        with open(os.path.join(codex, "memory_summary.md"), "w", encoding="utf-8") as f:
            f.write(v2)
        p = run([mem, "--codex-memories", codex, "--dry-run"], cwd=tmp)
        check("sync dry-run reports", "1 new" in p.stdout and "1 rephrased" in p.stdout and "2 retired" in p.stdout, p.stdout)
        check("sync dry-run no write", load(mem) == rows)
        p = run([mem, "--codex-memories", codex], cwd=tmp)
        check("sync exit 0", p.returncode == 0, p.stderr[:300])
        rows = load(mem)
        by_id = {r["id"]: r for r in rows}
        check("sync: only the low-overlap rewrite is new", len(rows) == 8 and "L13" in by_id, str([r["id"] for r in rows]))
        rag = by_id["L8"]
        check("near-identical rewrite updated in place", rag["text"].startswith("Start substantial work RAG-first: check the local")
              and rag["bridge_status"] == "approved" and not rag.get("superseded_by")
              and len([d for d in rag["derived_from"] if d.startswith("codex:memory_summary:")]) == 2, str(rag))
        shot, new_shot = by_id["L11"], by_id["L13"]
        check("low-overlap rewrite retires old, links successor", shot["bridge_status"] == "retired"
              and shot["superseded_by"] == "L13" and shot.get("bridge_note"), str(shot))
        check("successor is a gated candidate", new_shot["bridge_status"] == "candidate"
              and "supersedes:L11" in new_shot["derived_from"] and new_shot["origin"] == "agent", str(new_shot))
        terse = by_id["L9"]
        check("dropped tip retired without successor", terse["bridge_status"] == "retired" and not terse.get("superseded_by"), str(terse))
        check("unchanged codex tip untouched", by_id["L10"]["bridge_status"] == "candidate" and not by_id["L10"].get("superseded_by"))
        check("rejected tip keeps owner decision", by_id["L12"]["bridge_status"] == "rejected" and by_id["L12"]["bridge_note"] == "owner said no")
        check("rejected tip text never rewritten", "never one signal" in by_id["L12"]["text"], by_id["L12"]["text"])
        with open(os.path.join(codex, "memory_summary.md"), "a", encoding="utf-8") as f:
            f.write("- Verify an installed CLI through its resolved shim, installed package, and upstream version — never rely on one signal alone.\n")
        p = run([mem, "--codex-memories", codex], cwd=tmp)
        check("rewording of a rejected tip is absorbed, not re-proposed", len(load(mem)) == 8 and "1 dup-rejected" in p.stdout
              and {r["id"]: r for r in load(mem)}["L12"]["bridge_status"] == "rejected", p.stdout)
        check("origin backfilled on every codex entry incl. rejected/retired",
              all(r.get("origin") == "agent" for r in load(mem) if r.get("source_agent") == "codex"),
              str([(r["id"], r.get("origin")) for r in load(mem) if r.get("source_agent") == "codex"]))
        with open(os.path.join(codex, "memory_summary.md"), "w", encoding="utf-8") as f:
            f.write(v2)
        check("non-codex entries untouched by sync", all("bridge_status" not in by_id[i] and not by_id[i]["superseded_by"]
              and "origin" not in by_id[i] for i in ("L3", "L7")), str([by_id["L3"], by_id["L7"]]))
        p = run([mem, "--codex-memories", codex], cwd=tmp)
        check("sync idempotent", p.stdout.startswith("codex-ingest: 0 new, 4 dup") and "0 rephrased" in p.stdout
              and "0 retired" in p.stdout and len(load(mem)) == 8, p.stdout)
        with open(os.path.join(codex, "memory_summary.md"), "a", encoding="utf-8") as f:
            f.write("- For a terse, ambiguous request such as `update`, clarify the target before changing anything.\n")
        p = run([mem, "--codex-memories", codex], cwd=tmp)
        terse = {r["id"]: r for r in load(mem)}["L9"]
        check("returning tip revived as candidate", "1 revived" in p.stdout and terse["bridge_status"] == "candidate"
              and not terse["superseded_by"] and len(load(mem)) == 8, p.stdout + str(terse))

    # Verifier fe63f2b #1 + #2: veto wins over an equally close active entry;
    # a stale duplicate links to an EXISTING successor, not only to new ones.
    with tempfile.TemporaryDirectory() as tmp:
        def cx(i, text, status):
            return {"id": i, "date": "2026-09-08", "text": text, "importance": 2, "tags": [], "layer": "short-term",
                    "superseded_by": None, "last_relevant": "2026-09-08", "derived_from": [f"codex:memory_summary:{i:0>8}"],
                    "bridge_status": status, "source_agent": "codex", "kind": "learning"}
        base = "Verify an installed CLI through its resolved shim installed package and upstream version"
        rows0 = [cx("L1", base + " always.", "approved"), cx("L2", base + " never.", "rejected"),
                 cx("L3", "Start substantial work RAG-first: check relevant local wiki/memory/skill/tool surfaces first.", "approved"),
                 cx("L4", "Start substantial work RAG-first: check local wiki/memory/skill/tool surfaces first.", "approved")]
        mem, codex = setup(tmp, rows0)
        with open(os.path.join(codex, "memory_summary.md"), "w", encoding="utf-8") as f:
            f.write("## General Tips\n\n- " + base + " sometimes.\n"
                    "- Start substantial work RAG-first: check local wiki/memory/skill/tool surfaces first.\n")
        p = run([mem, "--codex-memories", codex], cwd=tmp)
        by_id = {r["id"]: r for r in load(mem)}
        check("tie between veto and active entry goes to the veto", "1 dup-rejected" in p.stdout and by_id["L1"]["text"] == base + " always."
              and by_id["L2"]["bridge_status"] == "rejected", p.stdout + str(by_id["L1"]))
        check("stale duplicate links to existing successor", by_id["L3"]["bridge_status"] == "retired"
              and by_id["L3"]["superseded_by"] == "L4" and "supersedes:L3" in by_id["L4"]["derived_from"], str(by_id["L3"]))

        # fail-soft: missing file
        p = run([mem, "--codex-memories", os.path.join(tmp, "nope")], cwd=tmp)
        check("missing summary exit 0 + skipped", p.returncode == 0 and "skipped" in p.stdout, p.stdout + p.stderr)

        # fail-soft: unknown structure
        with open(os.path.join(codex, "memory_summary.md"), "w", encoding="utf-8") as f:
            f.write("v2\n\n# totally different\n\nprose only\n")
        p = run([mem, "--codex-memories", codex], cwd=tmp)
        check("unknown structure exit 0, 0 new", p.returncode == 0 and "0 new" in p.stdout, p.stdout)
        check("unknown structure retires nothing (empty-parse guard)", "0 retired" in p.stdout
              and not any(r.get("bridge_status") == "retired" and r["id"] == "L10" for r in load(mem)), p.stdout)

        # invalid learnings.json -> exit 1
        with open(os.path.join(mem, "learnings", "learnings.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        p = run([mem, "--codex-memories", codex], cwd=tmp)
        check("invalid store exit 1", p.returncode == 1)

    # 5.2.1: a BOM is not corruption, and ids held by archive files are taken.
    with tempfile.TemporaryDirectory() as tmp:
        existing = [{"id": "L3", "date": "2026-08-01", "text": "Old claude learning.", "importance": 3,
                     "tags": [], "layer": "short-term", "superseded_by": None, "last_relevant": "2026-08-01"}]
        mem, codex = setup(tmp, existing)
        store = os.path.join(mem, "learnings", "learnings.json")
        raw = open(store, "rb").read()
        with open(store, "wb") as f:
            f.write(b"\xef\xbb\xbf" + raw)
        with open(os.path.join(mem, "learnings", "learnings-archive-2026-07.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "L20", "text": "archiviert"}], f)
        p = run([mem, "--codex-memories", codex], cwd=tmp)
        rows = json.loads(open(store, encoding="utf-8-sig").read())
        new_ids = [r["id"] for r in rows if r.get("source_agent") == "codex"]
        check("BOM store is ingested, not rejected", p.returncode == 0 and rows[0]["id"] == "L3", p.stderr[:200])
        check("new codex ids skip archived ids", new_ids[:1] == ["L21"], str(new_ids))

    n = len(FAILURES)
    print(f"=== {n} failure{'s' if n != 1 else ''} ===")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

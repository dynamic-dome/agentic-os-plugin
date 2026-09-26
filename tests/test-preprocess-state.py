#!/usr/bin/env python3
"""Tests for scripts/preprocess_state.py (stage-0 deterministic preprocessing, v4.7.0).

Covers spec test cases 24.1 (unchanged state -> hash equality) and the
deterministic half of 24.4 (structured delta instead of transcripts).
Run: python tests/test-preprocess-state.py  (exit 0 = pass)
"""
import json
import os
import subprocess
import sys
import tempfile

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PLUGIN_ROOT, "scripts", "preprocess_state.py")

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS: {name}")
    else:
        print(f"  FAIL: {name} {detail}")
        FAILURES.append(name)


def run(args, cwd):
    proc = subprocess.run(
        [sys.executable, SCRIPT] + args,
        capture_output=True, encoding="utf-8", errors="replace", cwd=cwd,
    )
    return proc


def main():
    print("=== preprocess_state.py tests ===")
    check("script exists", os.path.isfile(SCRIPT))
    if not os.path.isfile(SCRIPT):
        print("=== 1 failure (script missing) ===")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        mem = os.path.join(tmp, ".agent-memory")
        os.makedirs(os.path.join(mem, "working"))

        # 1. Empty store -> valid JSON, empty fields, exit 0
        p = run([mem], cwd=tmp)
        check("empty store exits 0", p.returncode == 0, f"rc={p.returncode} err={p.stderr[:200]}")
        try:
            state = json.loads(p.stdout)
            check("empty store emits valid JSON", True)
        except Exception as e:
            check("empty store emits valid JSON", False, str(e))
            state = {}
        for key in ("session_id", "changed_files", "git_diff_summary",
                    "threshold_events", "validation_errors", "open_tasks",
                    "previous_state_hash", "current_state_hash"):
            check(f"key present: {key}", key in state)
        check("changed_files empty", state.get("changed_files") == [])
        check("previous hash empty on first run", state.get("previous_state_hash") == "")
        check("current hash non-empty", bool(state.get("current_state_hash")))

        # 2. Dirty file -> changed_files picked up
        dirty = {"dirty": True, "touched": ["src/app.py", "README.md"]}
        with open(os.path.join(mem, "working", "dirty-abc.json"), "w", encoding="utf-8") as f:
            json.dump(dirty, f)
        p = run([mem], cwd=tmp)
        state = json.loads(p.stdout)
        check("dirty touched files in changed_files",
              set(state["changed_files"]) >= {"src/app.py", "README.md"},
              str(state.get("changed_files")))

        # 3. open-tasks.json -> only non-done tasks
        os.makedirs(os.path.join(mem, "context"), exist_ok=True)
        tasks = [{"id": "T1", "title": "open one", "status": "open"},
                 {"id": "T2", "title": "done one", "status": "done"}]
        with open(os.path.join(mem, "context", "open-tasks.json"), "w", encoding="utf-8") as f:
            json.dump(tasks, f)
        p = run([mem], cwd=tmp)
        state = json.loads(p.stdout)
        ids = [t.get("id") for t in state["open_tasks"]]
        check("open task listed", "T1" in ids, str(ids))
        check("done task excluded", "T2" not in ids, str(ids))

        # 3b. Learnings citing a pattern id that patterns.json does not hold
        os.makedirs(os.path.join(mem, "patterns"), exist_ok=True)
        os.makedirs(os.path.join(mem, "learnings"), exist_ok=True)
        with open(os.path.join(mem, "patterns", "patterns.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "P010"}, {"id": "G-pattern-005"}], f)
        with open(os.path.join(mem, "learnings", "learnings.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "L1", "text": "Lehre (verlaengert P006/L6): siehe auch P010"},
                       {"id": "L2", "text": "ok", "derived_from": ["G-pattern-005", "iteration-7"]},
                       {"id": "L3", "text": "ok", "derived_from": ["P004"]},
                       {"id": "L4", "text": "Die Grafikkarte P100 ist schnell, Preis P250"}], f)
        p = run([mem], cwd=tmp)
        state = json.loads(p.stdout)
        errs = state["validation_errors"]
        check("dangling pattern refs reported",
              "learnings.json: L1 references unknown pattern P006" in errs
              and "learnings.json: L3 references unknown pattern P004" in errs, str(errs))
        check("known pattern refs are not errors",
              not any("P010" in e or "G-pattern-005" in e for e in errs), str(errs))
        check("prose like P100/P250 is not a pattern ref", not any("L4" in e for e in errs), str(errs))
        # patterns.json = null must not crash the validator or hide the rest of the state
        with open(os.path.join(mem, "patterns", "patterns.json"), "w", encoding="utf-8") as f:
            f.write("null")
        p = run([mem], cwd=tmp)
        state = json.loads(p.stdout)
        check("patterns.json null: exit 0, state intact, ref check silent",
              p.returncode == 0 and isinstance(state.get("validation_errors"), list)
              and not any("pattern" in e for e in state["validation_errors"])
              and isinstance(state.get("open_tasks"), list), p.stdout[:300])

        # 4. Broken JSON -> validation_errors names file, still exit 0
        os.makedirs(os.path.join(mem, "learnings"), exist_ok=True)
        with open(os.path.join(mem, "learnings", "learnings.json"), "w", encoding="utf-8") as f:
            f.write("{broken json")
        p = run([mem], cwd=tmp)
        check("broken json still exits 0", p.returncode == 0)
        state = json.loads(p.stdout)
        check("validation_errors names broken file",
              any("learnings.json" in e for e in state["validation_errors"]),
              str(state.get("validation_errors")))

        # 5. --write-hash -> state-hash file created; second run: prev == current
        p = run([mem, "--write-hash"], cwd=tmp)
        check("--write-hash exits 0", p.returncode == 0)
        hash_file = os.path.join(mem, "working", "state-hash")
        check("state-hash file written", os.path.isfile(hash_file))
        p = run([mem], cwd=tmp)
        state = json.loads(p.stdout)
        check("hash equality after write (spec 24.1 fast path)",
              state["previous_state_hash"] == state["current_state_hash"]
              and state["current_state_hash"] != "")

        # 6. Memory change after write-hash -> hashes differ
        with open(os.path.join(mem, "session-summary.md"), "w", encoding="utf-8") as f:
            f.write("# Session Summary\nnew content\n")
        p = run([mem], cwd=tmp)
        state = json.loads(p.stdout)
        check("hash differs after change",
              state["previous_state_hash"] != state["current_state_hash"])

        # 7. No git repo -> git_diff_summary empty string, exit 0
        check("no-git-repo yields empty diff summary",
              isinstance(state["git_diff_summary"], str))

        # 8. --session-id restricts dirty scan to that session's file
        with open(os.path.join(mem, "working", "dirty-zzz.json"), "w", encoding="utf-8") as f:
            json.dump({"dirty": True, "touched": ["other.txt"]}, f)
        p = run([mem, "--session-id", "abc"], cwd=tmp)
        state = json.loads(p.stdout)
        check("--session-id scopes changed_files",
              "other.txt" not in state["changed_files"], str(state["changed_files"]))

        # 9. Malformed CLI args -> still JSON + exit 0 (fail-soft contract)
        p = run([mem, "--bogus-flag"], cwd=tmp)
        check("bogus flag still exits 0", p.returncode == 0, f"rc={p.returncode}")
        try:
            json.loads(p.stdout)
            check("bogus flag still emits valid JSON", True)
        except Exception as e:
            check("bogus flag still emits valid JSON", False, str(e))

        # 10. --help stays pure help text (exit 0, no JSON mixed into stdout)
        p = run(["--help"], cwd=tmp)
        check("--help exits 0", p.returncode == 0, f"rc={p.returncode}")
        check("--help prints usage", p.stdout.lstrip().startswith("usage"), p.stdout[:80])
        check("--help stdout contains no JSON object line",
              not any(ln.lstrip().startswith("{") for ln in p.stdout.splitlines()),
              p.stdout[-120:])

        # 11. Non-ASCII state content must not crash on cp1252 stdout (fail-soft)
        tasks_cjk = [{"id": "T3", "title": "emoji \U0001F600 and CJK 中文", "status": "open"}]
        with open(os.path.join(mem, "context", "open-tasks.json"), "w", encoding="utf-8") as f:
            json.dump(tasks_cjk, f, ensure_ascii=False)
        env = dict(os.environ)
        env.pop("PYTHONIOENCODING", None)
        env.pop("PYTHONUTF8", None)
        proc = subprocess.run(
            [sys.executable, "-X", "utf8=0", SCRIPT, mem],
            capture_output=True, cwd=tmp, env=env,
        )  # bytes mode: no encoding arg, so nothing masks an encode crash
        check("non-ascii content exits 0", proc.returncode == 0, f"rc={proc.returncode} err={proc.stderr[:200]}")
        try:
            state = json.loads(proc.stdout.decode("utf-8"))
            check("non-ascii content emits utf-8 JSON", True)
        except Exception as e:
            check("non-ascii content emits utf-8 JSON", False, str(e))

    n = len(FAILURES)
    print(f"=== {('ALL PASSED' if n == 0 else str(n) + ' FAILURE(S)')} ===")
    return 0 if n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

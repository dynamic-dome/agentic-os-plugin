#!/usr/bin/env python3
"""Tests for scripts/preprocess_state.py (stage-0 deterministic preprocessing, v4.7.0).

Covers spec test cases 24.1 (unchanged state -> hash equality) and the
deterministic half of 24.4 (structured delta instead of transcripts).
Run: python tests/test-preprocess-state.py  (exit 0 = pass)
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PLUGIN_ROOT, "scripts", "preprocess_state.py")

from _isolated_home import process_env  # noqa: E402

# Subprozesse laufen mit frischem Temp-Home statt mit dem echten Home (P0, tests/_isolated_home.py)
ENV, ISO_HOME = process_env()

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
        capture_output=True, encoding="utf-8", errors="replace", cwd=cwd, env=ENV,
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
        # 5.2.1 review: apply_open_tasks closes with 'closed', real stores also carry
        # 'cancelled' and 'deferred' - the night run listed all of them as open items.
        tasks = [{"id": "T1", "title": "open", "status": "open"},
                 {"id": "T2", "title": "blocked", "status": "blocked"},
                 {"id": "T3", "title": "no status"},
                 {"id": "T4", "title": "done", "status": "done"},
                 {"id": "T5", "title": "closed", "status": "closed"},
                 {"id": "T6", "title": "cancelled", "status": "cancelled"},
                 {"id": "T7", "title": "deferred", "status": "deferred"}]
        with open(os.path.join(mem, "context", "open-tasks.json"), "w", encoding="utf-8") as f:
            json.dump(tasks, f)
        state = json.loads(run([mem], cwd=tmp).stdout)
        ids = [t.get("id") for t in state["open_tasks"]]
        check("closed/cancelled/deferred are not open", sorted(ids) == ["T1", "T2", "T3"], str(ids))
        # DCO #9711: consumers take the head - blocked first, then newest updated/created.
        tasks = [{"id": "T-old", "title": "old", "status": "open", "updated": "2026-07-06"},
                 {"id": "T-new", "title": "new", "status": "open", "created": "2026-10-01", "updated": "2026-10-11"},
                 {"id": "T-mid", "title": "mid", "status": "open", "created": "2026-09-01"},
                 {"id": "T-blk", "title": "blocked", "status": "blocked", "updated": "2026-08-01"}]
        with open(os.path.join(mem, "context", "open-tasks.json"), "w", encoding="utf-8") as f:
            json.dump(tasks, f)
        ids = [t.get("id") for t in json.loads(run([mem], cwd=tmp).stdout)["open_tasks"]]
        check("order: blocked first, then newest updated (else created)", ids == ["T-blk", "T-new", "T-mid", "T-old"], str(ids))

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

        # 11b. Nested-store guard (DCO-9192): a relative default mem-dir with a
        # cwd already inside .agent-memory must not create .agent-memory/.agent-memory
        nested_cwd = os.path.join(mem, "working")
        os.makedirs(nested_cwd, exist_ok=True)
        p = run([".agent-memory", "--write-hash"], cwd=nested_cwd)
        check("nested-store guard: still exits 0", p.returncode == 0, f"rc={p.returncode}")
        nested_store = os.path.join(nested_cwd, ".agent-memory")
        check("nested-store guard: no nested .agent-memory created",
              not os.path.isdir(nested_store), nested_store)
        check("nested-store guard: warns on stderr",
              "nested-store guard" in p.stderr, p.stderr[:200])

        # 11. Non-ASCII state content must not crash on cp1252 stdout (fail-soft)
        tasks_cjk = [{"id": "T3", "title": "emoji \U0001F600 and CJK 中文", "status": "open"}]
        with open(os.path.join(mem, "context", "open-tasks.json"), "w", encoding="utf-8") as f:
            json.dump(tasks_cjk, f, ensure_ascii=False)
        env = dict(ENV)
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

    # 12. DCO #9695: a UTF-8 BOM (PowerShell Set-Content -Encoding utf8) is not corruption.
    # Same store twice, once with BOM'd JSON files: every derived field must be identical
    # (the raw-byte state hash differs by design and is not compared).
    with tempfile.TemporaryDirectory() as tmp:
        files = {
            "working/dirty-abc.json": {"dirty": True, "touched": ["src/app.py"]},
            "context/open-tasks.json": [{"id": "T1", "title": "open", "status": "open"},
                                        {"id": "T2", "title": "done", "status": "done"}],
            "patterns/patterns.json": [{"id": "P010"}],
            "learnings/learnings.json": [{"id": "L1", "text": "cites P006"}],
        }
        states = {}
        for label, prefix in (("plain", b""), ("bom", b"\xef\xbb\xbf")):
            mem = os.path.join(tmp, label, ".agent-memory")
            for rel, payload in files.items():
                path = os.path.join(mem, *rel.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(prefix + json.dumps(payload).encode("utf-8"))
            p = run([mem], cwd=tmp)
            check(f"BOM twin ({label}): exits 0 with JSON", p.returncode == 0 and p.stdout.strip().startswith("{"),
                  p.stderr[:200])
            states[label] = json.loads(p.stdout)
        for key in ("changed_files", "open_tasks", "validation_errors"):
            check(f"BOM store: {key} equals the BOM-less result",
                  states["bom"].get(key) == states["plain"].get(key),
                  f"bom={states['bom'].get(key)} plain={states['plain'].get(key)}")
        check("BOM store: findings are real (task, changed file, dangling ref)",
              [t["id"] for t in states["bom"]["open_tasks"]] == ["T1"]
              and states["bom"]["changed_files"] == ["src/app.py"]
              and states["bom"]["validation_errors"] == ["learnings.json: L1 references unknown pattern P006"],
              str(states["bom"]))

    # 13. P1/B2: the state hands the model the keys of the existing user candidates, so a repeated
    # observation reuses its key and the repeat path (occurrences++) works again. Capped, open first.
    CAP, OBS_MAX = 40, 80
    SID_ARGS = ["--session-id", "s-wrapup"]  # D10: the candidate list is only given to a wrap-up (it passes a session id)

    def write_queue(mem, rows, raw=None):
        os.makedirs(os.path.join(mem, "working"), exist_ok=True)
        with open(os.path.join(mem, "working", "user-candidates.json"), "wb") as f:
            f.write(raw if raw is not None else json.dumps(rows, ensure_ascii=False).encode("utf-8"))

    def row(i, status="observed", seen=None, obs=None, occ=1, signal="preference"):
        return {"id": f"UC{i}", "key": f"key-{i:03d}", "observation": obs if obs is not None else f"Beobachtung {i}",
                "status": status, "signal_type": signal, "confidence": 0.5, "occurrences": occ,
                "last_seen": seen or f"2026-09-{(i % 28) + 1:02d}", "trust_source": "conversation"}

    with tempfile.TemporaryDirectory() as tmp:
        mem = os.path.join(tmp, ".agent-memory")
        os.makedirs(mem)
        state = json.loads(run([mem] + SID_ARGS, cwd=tmp).stdout)
        check("candidates: no queue file -> empty list, total 0",
              state.get("user_candidates") == [] and state.get("user_candidates_total") == 0, str(state.get("user_candidates")))

        write_queue(mem, [row(1, "observed", obs="Kurz", occ=3), row(2, "promoted"), row(3, "inferred", obs="x" * 200),
                          row(4, "confirmed"), {"key": 5}, "kein objekt", {"id": "UC6", "observation": "ohne key"}])
        p = run([mem] + SID_ARGS, cwd=tmp)
        state = json.loads(p.stdout)
        got = state.get("user_candidates") or []
        check("candidates: exit 0 and the listed entries carry exactly key, observation, status, occurrences",
              p.returncode == 0 and isinstance(got, list) and got
              and all(set(e) == {"key", "observation", "status", "occurrences"} for e in got), str(got)[:300])
        by = {e["key"]: e for e in got or []}
        check("candidates: key, status and occurrences are passed through",
              by.get("key-001") == {"key": "key-001", "observation": "Kurz", "status": "observed", "occurrences": 3}, str(by.get("key-001")))
        check("candidates: an observation over 80 characters is cut to 80, with an ellipsis",
              len(by.get("key-003", {}).get("observation", "")) == OBS_MAX and by.get("key-003", {}).get("observation", "").endswith("…"), str(len(by.get("key-003", {}).get("observation", ""))))
        check("candidates: rows without a key and non-objects are skipped, total counts the usable ones",
              set(by) == {"key-001", "key-002", "key-003", "key-004"} and state.get("user_candidates_total") == 4,
              f"{sorted(by)} total={state.get('user_candidates_total')}")
        order = [e["key"] for e in got]
        check("candidates: open ones first (a promoted one is last)", order[-1:] == ["key-002"] and "key-004" in order and order.index("key-002") > order.index("key-004"), str(order))

        # cap: 45 open + 15 promoted -> 40, all of them open, the newest open first
        rows = [row(i, "observed", seen=f"2026-08-{(i % 28) + 1:02d}") for i in range(1, 46)] +                [row(100 + i, "promoted", seen="2026-10-27") for i in range(15)]
        write_queue(mem, rows)
        state = json.loads(run([mem] + SID_ARGS, cwd=tmp).stdout)
        got = state.get("user_candidates", [])
        # independent oracle: newest last_seen first, ties -> later in the file first
        newest_open = [r for _pos, r in sorted([(pos, r) for pos, r in enumerate(rows) if r["status"] == "observed"],
                                               key=lambda t: (t[1]["last_seen"], t[0]), reverse=True)][:CAP]
        check("candidates: the cap holds (40 of 60), total says 60",
              len(got) == CAP and state.get("user_candidates_total") == 60, f"len={len(got)} total={state.get('user_candidates_total')}")
        check("candidates: with 45 open ones only open ones are listed",
              all(e["status"] == "observed" for e in got), str({e["status"] for e in got}))
        check("candidates: of the open ones the newest (last_seen) are kept",
              [e["key"] for e in got] == [r["key"] for r in newest_open], str([e["key"] for e in got][:5]))
        # cap: 30 open + 30 promoted -> 30 open, then the 10 newest promoted
        rows = [row(i, "observed") for i in range(1, 31)] +                [row(100 + i, "promoted", seen=f"2026-07-{i + 1:02d}") for i in range(30)]
        write_queue(mem, rows)
        got = json.loads(run([mem] + SID_ARGS, cwd=tmp).stdout).get("user_candidates", [])
        check("candidates: 30 open first, the remaining 10 slots go to the newest promoted ones",
              len(got) == CAP and [e["status"] for e in got].count("observed") == 30
              and [e["key"] for e in got][30:] == [f"key-{100 + i:03d}" for i in range(29, 19, -1)],
              str([e["key"] for e in got][28:]))
        # mood can never be promoted, so it does not take an "open" slot
        write_queue(mem, [row(1, "confirmed", signal="mood", seen="2026-10-27"), row(2, "observed", seen="2026-01-01")])
        got = json.loads(run([mem] + SID_ARGS, cwd=tmp).stdout).get("user_candidates", [])
        check("candidates: a mood candidate ranks behind the open ones", [e["key"] for e in got] == ["key-002", "key-001"], str(got))

        # the fast-path hash is untouched by the queue (working/ stays out of the state hash)
        with open(os.path.join(mem, "session-summary.md"), "w", encoding="utf-8") as f:
            f.write("# Last Session\nso the hash covers something\n")
        write_queue(mem, [row(1)])
        h1 = json.loads(run([mem] + SID_ARGS, cwd=tmp).stdout)["current_state_hash"]
        write_queue(mem, [row(i) for i in range(1, 50)])
        s2 = json.loads(run([mem] + SID_ARGS, cwd=tmp).stdout)
        check("candidates: a changed queue leaves current_state_hash alone (bootstrap fast path)",
              s2["current_state_hash"] == h1 and h1 != hashlib.sha256(b"").hexdigest() and len(s2.get("user_candidates", [])) == CAP,
              f"{h1[:8]} vs {s2['current_state_hash'][:8]}")

        # fail-soft: damaged queue files never break the state
        for label, raw in (("broken json", b"{kaputt"), ("a dict instead of a list", b'{"a": 1}'), ("null", b"null"),
                           ("a BOM in front", b"\xef\xbb\xbf" + json.dumps([row(1)]).encode("utf-8"))):
            write_queue(mem, None, raw=raw)
            p = run([mem] + SID_ARGS, cwd=tmp)
            try:
                state = json.loads(p.stdout)
            except ValueError:
                state = {}
            want = ["key-001"] if "BOM" in label else []
            check(f"candidates: {label} -> exit 0, valid JSON, {'key kept' if want else 'empty list'}",
                  p.returncode == 0 and "user_candidates" in state and [e["key"] for e in state["user_candidates"]] == want, p.stdout[:200] + p.stderr[:200])
        # a malformed command line still yields the full state shape
        state = json.loads(run([mem, "--bogus-flag"], cwd=tmp).stdout)
        check("candidates: the fail-soft fallback state has the new fields too",
              state.get("user_candidates") == [] and state.get("user_candidates_total") == 0, str(state)[:200])


    # 14. D10: the candidate list costs up to ~7 KB per start, but only wrap-up uses it. The bootstrap call carries no
    # --session-id and must not pay for it; the total still says how many exist, the hash and the fast path stay as they are.
    with tempfile.TemporaryDirectory() as tmp:
        mem = os.path.join(tmp, ".agent-memory")
        os.makedirs(os.path.join(mem, "working"))
        with open(os.path.join(mem, "session-summary.md"), "w", encoding="utf-8") as f:
            f.write("# Last Session\nso the hash covers something\n")
        write_queue(mem, [row(i) for i in range(1, 51)])
        bare = run([mem], cwd=tmp)
        withsid = run([mem] + SID_ARGS, cwd=tmp)
        sb, sw = json.loads(bare.stdout), json.loads(withsid.stdout)
        check("D10: without --session-id no candidate list (the bootstrap call does not pay for it)",
              bare.returncode == 0 and sb.get("user_candidates") == [], str(sb.get("user_candidates"))[:120])
        check("D10: without --session-id the total still says how many candidates exist (50)",
              sb.get("user_candidates_total") == 50, str(sb.get("user_candidates_total")))
        check("D10: with --session-id the list is given (capped at 40), total 50",
              withsid.returncode == 0 and len(sw.get("user_candidates", [])) == CAP and sw.get("user_candidates_total") == 50,
              f"{len(sw.get('user_candidates', []))} total={sw.get('user_candidates_total')}")
        check("D10: the state hash is the same with and without the list (fast path unchanged)",
              sb["current_state_hash"] == sw["current_state_hash"] and sb["current_state_hash"] != "", f"{sb['current_state_hash'][:8]}")
        check("D10: every other field is identical in both calls",
              {k: v for k, v in sb.items() if k not in ("user_candidates", "user_candidates_total", "session_id")}
              == {k: v for k, v in sw.items() if k not in ("user_candidates", "user_candidates_total", "session_id")},
              "state differs")
        check("D10: the bare state is smaller than the wrap-up state", len(bare.stdout) + 3000 < len(withsid.stdout),
              f"{len(bare.stdout)} vs {len(withsid.stdout)}")
        check("D10: an empty --session-id counts as none", json.loads(run([mem, "--session-id", ""], cwd=tmp).stdout).get("user_candidates") == [])

    # 15. P2: stage 0 knows the central profile (--identity-store; active only with identity/profile.md)
    with tempfile.TemporaryDirectory() as tmp:
        mem = os.path.join(tmp, ".agent-memory")
        os.makedirs(os.path.join(mem, "working"))
        os.makedirs(os.path.join(mem, "identity"))
        with open(os.path.join(mem, "working", "user-candidates.json"), "w", encoding="utf-8") as f:
            json.dump([{"key": "projekt-key", "observation": "aus dem Projekt", "status": "observed"}], f)
        with open(os.path.join(mem, "identity", "user.md"), "w", encoding="utf-8") as f:
            f.write("# User Profile\n\n## Preferences\n- Projektzeile\n")
        central = os.path.join(tmp, "central")
        os.makedirs(os.path.join(central, "identity"))
        with open(os.path.join(central, "identity", "profile-candidates.json"), "w", encoding="utf-8") as f:
            json.dump([{"key": "pc-key", "observation": "aus dem Zentral", "status": "observed"}], f)
        cs = ["--identity-store", central]

        legacy = json.loads(run([mem, "--session-id", "s1"] + cs, cwd=tmp).stdout)
        check("P1: without profile.md the central store is inactive, keys come from the project",
              [c["key"] for c in legacy.get("user_candidates", [])] == ["projekt-key"], str(legacy.get("user_candidates")))
        profile = os.path.join(central, "identity", "profile.md")
        with open(profile, "w", encoding="utf-8") as f:
            f.write("# Profil\n\n## Preferences\n- Zentralzeile\n")
        active = json.loads(run([mem, "--session-id", "s1"] + cs, cwd=tmp).stdout)
        check("P1: with an active central profile the keys come from profile-candidates.json",
              [c["key"] for c in active.get("user_candidates", [])] == ["pc-key"] and active.get("user_candidates_total") == 1,
              str(active.get("user_candidates")))

        # P2 the central profile.md is part of the state hash; without a central profile the hash is today's
        check("P2: an active central profile changes the state hash",
              active["current_state_hash"] != legacy["current_state_hash"], legacy["current_state_hash"][:8])
        with open(profile, "a", encoding="utf-8") as f:
            f.write("- Noch eine Zentralzeile\n")
        changed = json.loads(run([mem] + cs, cwd=tmp).stdout)
        check("P2: a change to the central profile.md changes the state hash",
              changed["current_state_hash"] not in (active["current_state_hash"], legacy["current_state_hash"]))
        os.remove(profile)
        back = json.loads(run([mem] + cs, cwd=tmp).stdout)
        check("P2: without a central profile the hash is the project-only value again",
              back["current_state_hash"] == legacy["current_state_hash"])
        h = hashlib.sha256()
        with open(os.path.join(mem, "identity", "user.md"), "rb") as f:
            h.update(b"identity/user.md" + b"\0" + f.read() + b"\0")
        check("P2: the project-only hash is exactly the STATE_FILES rule (regression value)",
              back["current_state_hash"] == h.hexdigest(), f"{back['current_state_hash'][:8]} vs {h.hexdigest()[:8]}")

        # P3 a broken central queue is fail-soft: empty list, a warning, exit 0
        with open(profile, "w", encoding="utf-8") as f:
            f.write("# Profil\n")
        with open(os.path.join(central, "identity", "profile-candidates.json"), "w", encoding="utf-8") as f:
            f.write("{kaputt")
        p = run([mem, "--session-id", "s1"] + cs, cwd=tmp)
        broken = json.loads(p.stdout) if p.returncode == 0 and p.stdout.strip() else {}
        check("P3: broken central queue -> exit 0, empty candidate list, warning on stderr",
              p.returncode == 0 and broken.get("user_candidates") == [] and "profile-candidates.json" in p.stderr,
              f"rc={p.returncode} {p.stderr[:160]}")

    # 16. P2 (Gesamtpruefung R2): Stufe 0 liefert dem Bootstrap Zahl und Beispiel der promotebaren Kandidaten und das
    # letzte Identitaets-Datum aus der RICHTIGEN Queue - der Bootstrap muss die 357-KB-Queue nicht selbst lesen
    with tempfile.TemporaryDirectory() as tmp:
        mem = os.path.join(tmp, ".agent-memory")
        os.makedirs(os.path.join(mem, "working"))
        with open(os.path.join(mem, "working", "user-candidates.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "UC1", "key": "p", "status": "observed", "last_seen": "2026-09-01",
                        "trust_source": "conversation"}], f)
        central = os.path.join(tmp, "central")
        os.makedirs(os.path.join(central, "identity"))
        with open(os.path.join(central, "identity", "profile-candidates.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "PC1", "key": "ja", "status": "confirmed", "last_seen": "2026-10-20", "trust_source": "conversation"},
                       {"id": "PC2", "key": "inf", "status": "inferred", "occurrences": 2, "confidence": 0.7,
                        "last_seen": "2026-10-25", "trust_source": "conversation"},
                       {"id": "PC3", "key": "nein", "status": "inferred", "occurrences": 1, "confidence": 0.9,
                        "last_seen": "2026-10-27", "trust_source": "conversation"},
                       {"id": "PC4", "key": "laune", "status": "confirmed", "signal_type": "mood", "trust_source": "conversation"},
                       {"id": "PC5", "key": "datei", "status": "confirmed", "trust_source": "file"},
                       {"id": "PC6", "key": "fertig", "status": "promoted", "last_seen": "2026-10-28", "trust_source": "conversation"}], f)
        with open(os.path.join(central, "identity", "profile.md"), "w", encoding="utf-8") as f:
            f.write("# Profil\n")
        st = json.loads(run([mem, "--identity-store", central], cwd=tmp).stdout)
        idn = st.get("identity") or {}
        check("P4: central identity summary (target, promotable 2, example, newest last_seen)",
              idn.get("target") == "central" and idn.get("promotable") == 2 and idn.get("promotable_example") == "PC1: ja"
              and idn.get("last_seen") == "2026-10-28", str(idn))
        os.remove(os.path.join(central, "identity", "profile.md"))
        st = json.loads(run([mem, "--identity-store", central], cwd=tmp).stdout)
        idn = st.get("identity") or {}
        check("P4: without a central profile the summary comes from the project queue",
              idn.get("target") == "project" and idn.get("promotable") == 0 and idn.get("last_seen") == "2026-09-01", str(idn))

    n = len(FAILURES)
    print(f"=== {('ALL PASSED' if n == 0 else str(n) + ' FAILURE(S)')} ===")
    return 0 if n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

"""Standalone tests for scripts/posttooluse-dirty-tracker.py (fail-soft contract).

Run directly (python tests/test-posttooluse-dirty-tracker.py) or via run-all.sh.
Exit 0 = all pass, 1 = failures.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "posttooluse-dirty-tracker.py")
FAILURES = []


def run_hook(payload, env_project=None, script=None, extra_env=None, raw_utf8=False, cwd=None):
    env = os.environ.copy()
    env.pop("CLAUDE_PROJECT_DIR", None)
    if env_project:
        env["CLAUDE_PROJECT_DIR"] = env_project
    for key in ("PYTHONIOENCODING", "PYTHONUTF8"):  # worst case: the console code page decides
        env.pop(key, None)
    env.update(extra_env or {})
    data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=not raw_utf8)
    return subprocess.run(
        [sys.executable, script or SCRIPT],
        input=data.encode("utf-8"),
        capture_output=True,
        env=env,
        cwd=cwd,
        timeout=15,
    )


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def _has_store_above(path):
    """True when `path` or any ancestor holds an .agent-memory (it would own the path)."""
    d = os.path.abspath(path)
    while True:
        if os.path.isdir(os.path.join(d, ".agent-memory")):
            return True
        parent = os.path.dirname(d)
        if parent == d:
            return False
        d = parent


def store_free_dir():
    """A fresh directory with no store in any ancestor, or None.

    "Outside every store" cannot be built under a %TEMP% that has a machine-wide
    .agent-memory above it, so the filesystem root is the fallback. That is a deliberate
    exception to the temp-only rule (here it is the normal path); the caller removes the
    directory in a finally block.
    """
    tmp = tempfile.gettempdir()
    for base in (tmp, os.path.splitdrive(tmp)[0] + os.sep):
        try:
            d = tempfile.mkdtemp(prefix="dirty-free-", dir=base)
        except OSError:
            continue
        if not _has_store_above(d):
            return d
        shutil.rmtree(d, ignore_errors=True)
    return None


def make_dir_link(link, target):
    """Directory link at `link` -> `target`: symlink, on Windows else junction. Kind or None."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        try:
            import _winapi
            _winapi.CreateJunction(target, link)
            return "junction"
        except (ImportError, OSError, AttributeError):
            pass
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True)
        if r.returncode == 0 and os.path.isdir(link):
            return "junction"
    return None


def main():
    root = tempfile.mkdtemp(prefix="dirty-test-")
    proj = os.path.join(root, "proj")
    os.makedirs(os.path.join(proj, ".agent-memory"))
    sid = "abc123-DEF_456"
    dirty_file = os.path.join(proj, ".agent-memory", "working", f"dirty-{sid}.json")

    base = {
        "session_id": sid,
        "cwd": proj,
        "tool_name": "Write",
        "tool_input": {"file_path": os.path.join(proj, "src", "mainä.py")},
    }

    # A: first write creates dirty file
    p = run_hook(base, env_project=proj)
    ok = p.returncode == 0 and os.path.isfile(dirty_file)
    state = json.load(open(dirty_file, encoding="utf-8")) if ok else {}
    check("A create", ok and state.get("dirty") is True and state.get("write_count") == 1
          and len(state.get("touched_files", [])) == 1, str(state)[:200])

    # B: same file again -> dedup, count 2
    run_hook(base, env_project=proj)
    state = json.load(open(dirty_file, encoding="utf-8"))
    check("B dedup+count", state.get("write_count") == 2 and len(state["touched_files"]) == 1)

    # C: absolute write inside .agent-memory -> ignored
    payload = dict(base)
    payload["tool_input"] = {"file_path": os.path.join(proj, ".agent-memory", "session-summary.md")}
    run_hook(payload, env_project=proj)
    state = json.load(open(dirty_file, encoding="utf-8"))
    check("C memory-write ignored", state.get("write_count") == 2)

    # C2: RELATIVE memory path -> equally ignored (review finding: skip bypass)
    payload = dict(base)
    payload["tool_input"] = {"file_path": ".agent-memory/session-summary.md"}
    run_hook(payload, env_project=proj)
    state = json.load(open(dirty_file, encoding="utf-8"))
    check("C2 relative memory path ignored", state.get("write_count") == 2)

    # C3: case variation -> equally ignored (Windows case-insensitive filesystems)
    payload = dict(base)
    payload["tool_input"] = {"file_path": os.path.join(proj, ".Agent-Memory", "x.md")}
    run_hook(payload, env_project=proj)
    state = json.load(open(dirty_file, encoding="utf-8"))
    check("C3 case-insensitive skip", state.get("write_count") == 2)

    # C4: relative WORK path -> tracked (resolved against project dir)
    payload = dict(base)
    payload["tool_input"] = {"file_path": "src/relative.py"}
    run_hook(payload, env_project=proj)
    state = json.load(open(dirty_file, encoding="utf-8"))
    check("C4 relative work path tracked", state.get("write_count") == 3
          and "src/relative.py" in state["touched_files"])

    # D: project without .agent-memory -> no file, exit 0
    proj2 = os.path.join(root, "proj2")
    os.makedirs(proj2)
    payload = dict(base)
    payload["cwd"] = proj2
    payload["tool_input"] = {"file_path": os.path.join(proj2, "x.txt")}
    p = run_hook(payload, env_project=proj2)
    check("D no store -> noop", p.returncode == 0 and not os.path.isdir(os.path.join(proj2, ".agent-memory")))

    # E: corrupt dirty file -> rebuilt, still exit 0
    with open(dirty_file, "w", encoding="utf-8") as fh:
        fh.write("{ kaputt !!!")
    p = run_hook(base, env_project=proj)
    state = json.load(open(dirty_file, encoding="utf-8"))
    check("E corrupt rebuilt", p.returncode == 0 and state.get("dirty") is True and state.get("write_count") == 1)

    # F: garbage stdin -> exit 0, no stderr
    p = run_hook("das ist kein json", env_project=proj)
    check("F garbage stdin fail-soft", p.returncode == 0 and p.stderr == b"")

    # G: untracked tool -> ignored
    payload = dict(base)
    payload["tool_name"] = "Bash"
    run_hook(payload, env_project=proj)
    state = json.load(open(dirty_file, encoding="utf-8"))
    check("G untracked tool ignored", state.get("write_count") == 1)

    # H: consolidation flags get reset on re-dirty (self-healing)
    state.update({"dirty": False, "consolidated_at": "2026-07-14T00:00:00", "consolidated_by": "wrap-up"})
    with open(dirty_file, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    run_hook(base, env_project=proj)
    state = json.load(open(dirty_file, encoding="utf-8"))
    check("H re-dirty self-healing", state.get("dirty") is True and state.get("consolidated_at") is None)

    # I: Claude scratchpad writes ignored
    payload = dict(base)
    payload["tool_input"] = {"file_path": "C:/Users/x/AppData/Local/Temp/claude/session/scratchpad/t.py"}
    run_hook(payload, env_project=proj)
    state = json.load(open(dirty_file, encoding="utf-8"))
    check("I scratchpad write ignored", state.get("write_count") == 2)  # H run counted one write

    # J: re-dirty preserves consolidation history (tail-write vs. crash distinction)
    state.update({"dirty": False, "consolidated_at": "2026-07-15T09:03:28+02:00", "consolidated_by": "wrap-up"})
    state.pop("last_consolidated_at", None)
    state.pop("writes_since_consolidation", None)
    with open(dirty_file, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    run_hook(base, env_project=proj)
    state = json.load(open(dirty_file, encoding="utf-8"))
    check("J re-dirty keeps history", state.get("consolidated_at") is None
          and state.get("last_consolidated_at") == "2026-07-15T09:03:28+02:00"
          and state.get("last_consolidated_by") == "wrap-up"
          and state.get("writes_since_consolidation") == 1, str(state)[:300])

    # K: further writes increment writes_since_consolidation
    run_hook(base, env_project=proj)
    state = json.load(open(dirty_file, encoding="utf-8"))
    check("K tail-write counter", state.get("writes_since_consolidation") == 2
          and state.get("last_consolidated_at") == "2026-07-15T09:03:28+02:00")

    # M: corrupt counter values must not stall the tracker (fail-soft = keep tracking)
    state = json.load(open(dirty_file, encoding="utf-8"))
    state["writes_since_consolidation"] = "kaputt"
    state["write_count"] = ["auch", "kaputt"]
    with open(dirty_file, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    p = run_hook(base, env_project=proj)
    state = json.load(open(dirty_file, encoding="utf-8"))
    check("M corrupt counters normalized", p.returncode == 0
          and state.get("writes_since_consolidation") == 1
          and state.get("write_count") == 1 and state.get("dirty") is True, str(state)[:300])

    # L: never-consolidated sessions carry no consolidation-history fields
    sid2 = "never-consolidated-1"
    payload = dict(base)
    payload["session_id"] = sid2
    run_hook(payload, env_project=proj)
    state2 = json.load(open(os.path.join(proj, ".agent-memory", "working", f"dirty-{sid2}.json"), encoding="utf-8"))
    check("L no phantom history", "last_consolidated_at" not in state2
          and "writes_since_consolidation" not in state2, str(state2)[:300])

    # N: agent field defaults to claude (script lives outside /.codex/)
    state = json.load(open(dirty_file, encoding="utf-8"))
    check("N agent claude", state.get("agent") == "claude", str(state.get("agent")))

    # O: script copy under a /.codex/ path -> agent codex (T-24)
    codex_scripts = os.path.join(root, ".codex", "plugins", "cache", "m", "agentic-os", "9.9.9", "scripts")
    os.makedirs(codex_scripts)
    script_copy = os.path.join(codex_scripts, "posttooluse-dirty-tracker.py")
    shutil.copyfile(SCRIPT, script_copy)
    sid3 = "codex-agent-test"
    payload = dict(base)
    payload["session_id"] = sid3
    p = run_hook(payload, env_project=proj, script=script_copy)
    f3 = os.path.join(proj, ".agent-memory", "working", f"dirty-{sid3}.json")
    ok = p.returncode == 0 and os.path.isfile(f3)
    state3 = json.load(open(f3, encoding="utf-8")) if ok else {}
    check("O agent codex", ok and state3.get("agent") == "codex", str(state3)[:200])

    # P: apply_patch payload (codex) -> paths parsed from patch text (no file_path field)
    sid4 = "codex-applypatch"
    patch = "*** Begin Patch\n*** Add File: src/new1.py\n+x\n*** Update File: src/old2.py\n+y\n*** End Patch"
    payload = {"session_id": sid4, "cwd": proj, "tool_name": "apply_patch", "tool_input": {"command": patch}}
    p = run_hook(payload, env_project=proj, script=script_copy)
    f4 = os.path.join(proj, ".agent-memory", "working", f"dirty-{sid4}.json")
    ok = p.returncode == 0 and os.path.isfile(f4)
    state4 = json.load(open(f4, encoding="utf-8")) if ok else {}
    check("P apply_patch paths", ok and "src/new1.py" in state4.get("touched_files", [])
          and "src/old2.py" in state4.get("touched_files", []) and state4.get("write_count") == 1, str(state4)[:300])

    # P2: apply_patch touching ONLY .agent-memory -> skipped like file_path writes
    patch2 = "*** Begin Patch\n*** Update File: .agent-memory/session-summary.md\n+z\n*** End Patch"
    payload = {"session_id": sid4, "cwd": proj, "tool_name": "apply_patch", "tool_input": {"command": patch2}}
    run_hook(payload, env_project=proj, script=script_copy)
    state4 = json.load(open(f4, encoding="utf-8"))
    check("P2 apply_patch memory skip", state4.get("write_count") == 1)

    # --- Lebenszyklus Phase 2, slice 4: data basis for the harvest ledger + DCO #9693 ---
    # Q: transcript_path and cwd come from the payload; a later payload without them keeps them
    sid5 = "ledger-basis"
    f5 = os.path.join(proj, ".agent-memory", "working", f"dirty-{sid5}.json")
    payload = dict(base, session_id=sid5, transcript_path="C:/t/ledger-basis.jsonl")
    run_hook(payload, env_project=proj)
    payload = dict(base, session_id=sid5)
    payload.pop("cwd")
    run_hook(payload, env_project=proj)
    state5 = json.load(open(f5, encoding="utf-8"))
    check("Q transcript_path + cwd kept", state5.get("transcript_path") == "C:/t/ledger-basis.jsonl"
          and state5.get("cwd") == proj and state5.get("write_count") == 2, str(state5)[:300])
    payload = dict(base, session_id="bad-transcript", transcript_path=123)
    run_hook(payload, env_project=proj)
    state6 = json.load(open(os.path.join(proj, ".agent-memory", "working", "dirty-bad-transcript.json"),
                            encoding="utf-8"))
    check("Q2 non-string transcript_path omitted", "transcript_path" not in state6, str(state6)[:200])

    # R: a write into ANOTHER store goes to foreign_touched; neither store becomes dirty
    other = os.path.join(root, "other")
    os.makedirs(os.path.join(other, ".agent-memory"))
    sid7 = "foreign-only"
    f7 = os.path.join(proj, ".agent-memory", "working", f"dirty-{sid7}.json")
    payload = dict(base, session_id=sid7, transcript_path="C:/t/foreign-only.jsonl",
                   tool_input={"file_path": os.path.join(other, "src", "x.py")})
    p = run_hook(payload, env_project=proj)
    state7 = json.load(open(f7, encoding="utf-8")) if os.path.isfile(f7) else {}
    ft = state7.get("foreign_touched") or {}
    keys = [os.path.normcase(os.path.realpath(k)) for k in ft]
    check("R foreign write recorded, own store not dirty", p.returncode == 0 and state7.get("dirty") is False
          and keys == [os.path.normcase(os.path.realpath(other))] and not state7.get("touched_files"),
          str(state7)[:300])
    check("R2 foreign store untouched", not os.path.isdir(os.path.join(other, ".agent-memory", "working")))
    check("R3 foreign-only session keeps transcript_path", state7.get("transcript_path") == "C:/t/foreign-only.jsonl")

    # S: own work afterwards makes the own store dirty and keeps the foreign record
    payload = dict(base, session_id=sid7)
    run_hook(payload, env_project=proj)
    state7 = json.load(open(f7, encoding="utf-8"))
    check("S own work after foreign", state7.get("dirty") is True and state7.get("write_count") == 1
          and len(state7.get("touched_files", [])) == 1 and len(state7.get("foreign_touched") or {}) == 1,
          str(state7)[:300])
    payload = dict(base, session_id=sid7, tool_input={"file_path": os.path.join(other, "src", "y.py")})
    run_hook(payload, env_project=proj)
    state7 = json.load(open(f7, encoding="utf-8"))
    ft7 = state7.get("foreign_touched") or {}
    check("S2 foreign write after own work keeps dirty:true", state7.get("dirty") is True
          and state7.get("write_count") == 1 and sum(len(v) for v in ft7.values()) == 2, str(state7)[:300])
    run_hook(payload, env_project=proj)  # the same foreign file once more: a path list cannot count it
    state7 = json.load(open(f7, encoding="utf-8"))
    check("S2b foreign edits are counted, not only distinct paths (trivial rule, D-024)",
          state7.get("foreign_write_count") == 3 and sum(len(v) for v in (state7.get("foreign_touched") or {}).values()) == 2,
          str(state7)[:300])
    # S2b2: one apply_patch over two foreign files is two edits, not one store
    patch = (f"*** Begin Patch\n*** Update File: {os.path.join(other, 'src', 'p1.py')}\n@@\n-a\n+b\n"
             f"*** Update File: {os.path.join(other, 'src', 'p2.py')}\n@@\n-a\n+b\n*** End Patch\n")
    run_hook(dict(base, session_id=sid7, tool_name="apply_patch", tool_input={"command": patch}), env_project=proj)
    state7 = json.load(open(f7, encoding="utf-8"))
    check("S2b2 a multi-file patch counts every foreign file", state7.get("foreign_write_count") == 5,
          str(state7.get("foreign_write_count")))
    # S2c: a marker from an older tracker has foreign paths but no count - starting at 0 would
    # undercount and let the trivial rule close a busy session, so it never gets a count
    f7c = os.path.join(proj, ".agent-memory", "working", "dirty-legacy-foreign.json")
    with open(f7c, "w", encoding="utf-8") as fh:
        json.dump({"session_id": "legacy-foreign", "dirty": False,
                   "foreign_touched": {other: [os.path.join(other, "src", "old.py")]}}, fh)
    run_hook(dict(base, session_id="legacy-foreign", tool_input={"file_path": os.path.join(other, "src", "x.py")}),
             env_project=proj)
    state7c = json.load(open(f7c, encoding="utf-8"))
    check("S2c legacy foreign record gets no (undercounting) foreign_write_count",
          "foreign_write_count" not in state7c and sum(len(v) for v in state7c["foreign_touched"].values()) == 2,
          str(state7c)[:300])

    # S3: two hooks of ONE session at the same time (own + foreign): own work must never be lost
    lost = []
    for i in range(12):
        sid_r = f"race-{i}"
        procs = []
        for fp in (os.path.join(proj, "src", f"own{i}.py"), os.path.join(other, "src", f"f{i}.py")):
            env = os.environ.copy()
            env["CLAUDE_PROJECT_DIR"] = proj
            pl = json.dumps(dict(base, session_id=sid_r, tool_input={"file_path": fp})).encode("utf-8")
            pr = subprocess.Popen([sys.executable, SCRIPT], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, env=env)
            procs.append((pr, pl))
        for pr, pl in procs:  # feed both before waiting on either: the hooks really overlap
            pr.stdin.write(pl)
            pr.stdin.close()
        for pr, _ in procs:
            pr.wait(timeout=30)
        fr = os.path.join(proj, ".agent-memory", "working", f"dirty-{sid_r}.json")
        try:
            st = json.load(open(fr, encoding="utf-8"))
        except (OSError, ValueError) as exc:
            lost.append(f"{i}: unreadable {exc}")
            continue
        if not (st.get("dirty") is True and st.get("foreign_touched")):
            lost.append(f"{i}: dirty={st.get('dirty')} foreign={bool(st.get('foreign_touched'))}")
    check("S3 parallel own+foreign hooks lose nothing (12 rounds)", not lost, "; ".join(lost)[:300])

    # T: scratch under the temp dir is never work, even where HOME's store is an ancestor of it
    fake_tmp = os.path.join(root, "faketemp")
    os.makedirs(fake_tmp)
    # an ancestor store above the temp dir (like HOME's on a real machine): without the
    # temp rule the scratch file would count as that store's foreign work
    os.makedirs(os.path.join(root, ".agent-memory"))
    env_tmp = {"TMPDIR": fake_tmp, "TEMP": fake_tmp, "TMP": fake_tmp}
    proj3 = os.path.join(os.path.dirname(fake_tmp), "proj3")  # sibling, NOT under the (fake) temp dir
    os.makedirs(os.path.join(proj3, ".agent-memory"))
    sid8 = "temp-scratch"
    f8 = os.path.join(proj3, ".agent-memory", "working", f"dirty-{sid8}.json")
    payload = dict(base, session_id=sid8, cwd=proj3, tool_input={"file_path": os.path.join(fake_tmp, "helper.py")})
    p = run_hook(payload, env_project=proj3, extra_env=env_tmp)
    check("T temp scratch ignored", p.returncode == 0 and not os.path.isfile(f8),
          open(f8, encoding="utf-8").read()[:300] if os.path.isfile(f8) else "")
    look_alike = os.path.join(root, "faketemp2")  # string prefix of the temp dir, not inside it
    os.makedirs(look_alike)
    payload = dict(base, session_id=sid8, cwd=proj3, tool_input={"file_path": os.path.join(look_alike, "x.py")})
    run_hook(payload, env_project=proj3, extra_env=env_tmp)
    state8 = json.load(open(f8, encoding="utf-8")) if os.path.isfile(f8) else {}
    keys8 = [os.path.normcase(os.path.realpath(k)) for k in (state8.get("foreign_touched") or {})]
    check("T2 temp rule is a path test, not a string prefix", keys8 == [os.path.normcase(os.path.realpath(root))],
          str(state8)[:300])

    # U: a path on another drive is ignored on its own (commonpath ValueError) - the own
    # path in the same patch is still recorded; the outer fail-soft catch would drop both
    if os.name == "nt":
        drive = "Z:" if not os.path.abspath(proj3).upper().startswith("Z:") else "Y:"
        patch3 = f"*** Begin Patch\n*** Update File: {drive}/nowhere/x.py\n+a\n*** Update File: src/ok.py\n+b\n*** End Patch"
        payload = {"session_id": "drive-change", "cwd": proj3, "tool_name": "apply_patch", "tool_input": {"command": patch3}}
        p = run_hook(payload, env_project=proj3, extra_env=env_tmp)  # project outside temp: the drive check runs
        f9 = os.path.join(proj3, ".agent-memory", "working", "dirty-drive-change.json")
        state9 = json.load(open(f9, encoding="utf-8")) if os.path.isfile(f9) else {}
        check("U drive change skips only that path", p.returncode == 0 and p.stderr == b""
              and state9.get("touched_files") == ["src/ok.py"] and not state9.get("foreign_touched"), str(state9)[:300])

    # V: inside the project, a deeper store without its own repository (stray/auto-init stub,
    # e.g. AI/dual-bridge/scripts) never takes the project's work; one with .git is a real project
    stray = os.path.join(proj, "stray")
    os.makedirs(os.path.join(stray, ".agent-memory"))
    nested_repo = os.path.join(proj, "repo2")
    os.makedirs(os.path.join(nested_repo, ".agent-memory"))
    os.makedirs(os.path.join(nested_repo, ".git"))
    sid10 = "nested-stores"
    f10 = os.path.join(proj, ".agent-memory", "working", f"dirty-{sid10}.json")
    run_hook(dict(base, session_id=sid10, tool_input={"file_path": os.path.join(stray, "a.py")}), env_project=proj)
    run_hook(dict(base, session_id=sid10, tool_input={"file_path": os.path.join(nested_repo, "b.py")}), env_project=proj)
    state10 = json.load(open(f10, encoding="utf-8")) if os.path.isfile(f10) else {}
    keys10 = [os.path.normcase(os.path.realpath(k)) for k in (state10.get("foreign_touched") or {})]
    check("V stray store inside the project stays own work", state10.get("dirty") is True
          and state10.get("touched_files") == [os.path.join(stray, "a.py")], str(state10)[:300])
    check("V2 nested repository with its own store is foreign",
          keys10 == [os.path.normcase(os.path.realpath(nested_repo))], str(state10)[:300])

    # W: raw UTF-8 payload with an umlaut in the project path (Claude Code does not ASCII-escape)
    uproj = os.path.join(root, "\u00dcbung_\u00e4\u00f6\u00fc")
    os.makedirs(os.path.join(uproj, ".agent-memory"))
    target = os.path.join(uproj, "src", "\u00e4.py")
    p = run_hook(dict(base, session_id="umlaut", cwd=uproj, tool_input={"file_path": target}),
                 env_project=uproj, raw_utf8=True)
    fw = os.path.join(uproj, ".agent-memory", "working", "dirty-umlaut.json")
    statew = json.load(open(fw, encoding="utf-8")) if os.path.isfile(fw) else {}
    check("W UTF-8 payload: umlaut path is own work", p.returncode == 0 and statew.get("dirty") is True
          and statew.get("touched_files") == [target] and not statew.get("foreign_touched"), str(statew)[:300])

    # X: at most 20 foreign stores, the least recently written one is dropped first
    caps = os.path.join(root, "caps")
    for n in range(21):
        os.makedirs(os.path.join(caps, f"s{n}", ".agent-memory"))
    sid11 = "caps"
    f11 = os.path.join(proj, ".agent-memory", "working", f"dirty-{sid11}.json")
    for n in [*range(20), 0, 20]:
        run_hook(dict(base, session_id=sid11, tool_input={"file_path": os.path.join(caps, f"s{n}", "x.py")}),
                 env_project=proj)
    state11 = json.load(open(f11, encoding="utf-8"))
    names11 = {os.path.basename(os.path.normpath(k)) for k in state11.get("foreign_touched", {})}
    check("X foreign stores capped at 20, least recent dropped", len(names11) == 20 and "s0" in names11
          and "s20" in names11 and "s1" not in names11, str(sorted(names11))[:300])

    # Y: another writer holds the store lock past LOCK_TIMEOUT_S -> the hook still records (unlocked), exit 0
    sys.path.insert(0, os.path.dirname(SCRIPT))
    import store_lock
    import time
    sid12 = "lock-held"
    f12 = os.path.join(proj, ".agent-memory", "working", f"dirty-{sid12}.json")
    with store_lock.store_lock(os.path.join(proj, ".agent-memory")):
        t0 = time.time()
        p = run_hook(dict(base, session_id=sid12), env_project=proj)
        waited = time.time() - t0
    state12 = json.load(open(f12, encoding="utf-8")) if os.path.isfile(f12) else {}
    check("Y lock held by another writer: hook waits, then records unlocked", p.returncode == 0
          and state12.get("dirty") is True and waited >= 1.5, f"rc={p.returncode} waited={waited:.1f}s {state12}"[:300])

    # Z: a path that is LEXICALLY in the project but resolves through a symlink/junction to
    # somewhere outside every store is still own work (DCO #9705, "when in doubt: own work")
    free = None
    try:
        free = store_free_dir()
        link_kind = None
        if free:
            zt = os.path.join(free, "target")
            os.makedirs(zt)
            link_kind = make_dir_link(os.path.join(proj, "linked"), zt)
        if not link_kind:
            print("  SKIP: Z link tests - no store-free directory or no right to create a symlink/junction")
        else:
            sidz = "link-own"
            fz = os.path.join(proj, ".agent-memory", "working", f"dirty-{sidz}.json")
            linked_abs = os.path.join(proj, "linked", "x.py")
            run_hook(dict(base, session_id=sidz, tool_input={"file_path": linked_abs}), env_project=proj)
            statez = json.load(open(fz, encoding="utf-8")) if os.path.isfile(fz) else {}
            check(f"Z {link_kind} out of the project, outside every store: own work", statez.get("dirty") is True
                  and statez.get("touched_files") == [linked_abs] and statez.get("write_count") == 1
                  and not statez.get("foreign_touched"), str(statez)[:300])
            run_hook(dict(base, session_id=sidz, tool_input={"file_path": "linked/y.py"}), env_project=proj)
            statez = json.load(open(fz, encoding="utf-8")) if os.path.isfile(fz) else {}
            check("Z2 relative path through the link is own work too", statez.get("write_count") == 2
                  and "linked/y.py" in statez.get("touched_files", []), str(statez)[:300])

            # Z3: a link into ANOTHER store stays foreign - the real path wins for foreign
            make_dir_link(os.path.join(proj, "flinked"), other)
            sidz3 = "link-foreign"
            fz3 = os.path.join(proj, ".agent-memory", "working", f"dirty-{sidz3}.json")
            run_hook(dict(base, session_id=sidz3, tool_input={"file_path": os.path.join(proj, "flinked", "x.py")}),
                     env_project=proj)
            statez3 = json.load(open(fz3, encoding="utf-8")) if os.path.isfile(fz3) else {}
            keysz3 = [os.path.normcase(os.path.realpath(k)) for k in (statez3.get("foreign_touched") or {})]
            check("Z3 link into another store stays foreign", statez3.get("dirty") is False
                  and keysz3 == [os.path.normcase(os.path.realpath(other))] and not statez3.get("touched_files"),
                  str(statez3)[:300])

            # Z4: the same target reached WITHOUT the link (lexically outside, really outside) is still dropped
            sidz4 = "link-bypass"
            fz4 = os.path.join(proj, ".agent-memory", "working", f"dirty-{sidz4}.json")
            run_hook(dict(base, session_id=sidz4, tool_input={"file_path": os.path.join(zt, "x.py")}), env_project=proj)
            check("Z4 lexically and really outside every store: ignored", not os.path.isfile(fz4),
                  open(fz4, encoding="utf-8").read()[:300] if os.path.isfile(fz4) else "")

            # Z5: "lexically in the project" is a path test, not a string prefix (pr vs pr2)
            pr = os.path.join(free, "pr")
            os.makedirs(os.path.join(pr, ".agent-memory"))
            os.makedirs(os.path.join(free, "pr2"))
            make_dir_link(os.path.join(free, "pr2", "linked"), zt)
            sidz5 = "link-prefix"
            fz5 = os.path.join(pr, ".agent-memory", "working", f"dirty-{sidz5}.json")
            run_hook(dict(base, session_id=sidz5, cwd=pr, tool_input={"file_path": os.path.join(free, "pr2", "linked", "x.py")}),
                     env_project=pr)
            check("Z5 sibling project with the same name prefix is not the project", not os.path.isfile(fz5),
                  open(fz5, encoding="utf-8").read()[:300] if os.path.isfile(fz5) else "")

            # Z6: temp rule unchanged - a link to scratch under the (fake) temp dir stays ignored
            make_dir_link(os.path.join(proj3, "tlinked"), fake_tmp)
            sidz6 = "link-temp"
            fz6 = os.path.join(proj3, ".agent-memory", "working", f"dirty-{sidz6}.json")
            run_hook(dict(base, session_id=sidz6, cwd=proj3, tool_input={"file_path": os.path.join(proj3, "tlinked", "h.py")}),
                     env_project=proj3, extra_env=env_tmp)
            check("Z6 link to temp scratch is still scratch", not os.path.isfile(fz6),
                  open(fz6, encoding="utf-8").read()[:300] if os.path.isfile(fz6) else "")

            # Z7: ".." traversal out of the project into a store-free sibling is not own work
            # (the lexical check needs normpath; plain commonpath would still see ".../z7proj/...")
            z7proj = os.path.join(free, "z7proj")
            os.makedirs(os.path.join(z7proj, ".agent-memory"))
            os.makedirs(os.path.join(free, "z7other"))
            sidz7 = "dotdot-out"
            fz7 = os.path.join(z7proj, ".agent-memory", "working", f"dirty-{sidz7}.json")
            run_hook(dict(base, session_id=sidz7, cwd=z7proj,
                          tool_input={"file_path": os.path.join(z7proj, "..", "z7other", "x.py")}),
                     env_project=z7proj)
            check("Z7 '..' out of the project into a store-free dir is ignored", not os.path.isfile(fz7),
                  open(fz7, encoding="utf-8").read()[:300] if os.path.isfile(fz7) else "")

            # Z8: the project itself is opened THROUGH a link; the lexical root is the name given
            # (projlink), not the real path - a write through a link inside it is own work
            projlink = os.path.join(free, "projlink")
            if make_dir_link(projlink, proj):
                sidz8 = "projlink-own"
                fz8 = os.path.join(proj, ".agent-memory", "working", f"dirty-{sidz8}.json")
                z8_abs = os.path.join(projlink, "linked", "z8.py")
                run_hook(dict(base, session_id=sidz8, cwd=projlink, tool_input={"file_path": z8_abs}),
                         env_project=projlink)
                statez8 = json.load(open(fz8, encoding="utf-8")) if os.path.isfile(fz8) else {}
                check("Z8 project opened through a link: write through a link inside it is own work",
                      statez8.get("dirty") is True and statez8.get("touched_files") == [z8_abs]
                      and statez8.get("write_count") == 1, str(statez8)[:300])
            else:
                print("  SKIP: Z8 - could not link the project")

            # Z9: relative project dir (CLAUDE_PROJECT_DIR=".", cwd = project) AND a relative file path:
            # the lexical root is absolute, so the candidate must be made absolute before the comparison
            sidz9 = "relative-own"
            fz9 = os.path.join(proj, ".agent-memory", "working", f"dirty-{sidz9}.json")
            run_hook(dict(base, session_id=sidz9, tool_input={"file_path": "linked/x.py"}),
                     env_project=".", cwd=proj)
            statez9 = json.load(open(fz9, encoding="utf-8")) if os.path.isfile(fz9) else {}
            check("Z9 relative project dir + relative path through a link is own work",
                  statez9.get("dirty") is True and statez9.get("touched_files") == ["linked/x.py"]
                  and statez9.get("write_count") == 1 and not statez9.get("foreign_touched"), str(statez9)[:300])
    finally:
        if free:
            shutil.rmtree(free, ignore_errors=True)

    shutil.rmtree(root, ignore_errors=True)

    # DCO #9890: on Windows os.replace fails with PermissionError while another process reads
    # the marker -> retry, then write in place instead of losing the count; no tmp left.
    import importlib.util
    spec = importlib.util.spec_from_file_location("dirty_tracker", SCRIPT)
    tracker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tracker)
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "dirty-lk.json")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("{}")
        real_replace, calls = os.replace, []

        def blocked(src, dst):
            calls.append(src)
            raise PermissionError(13, "Zugriff verweigert")
        os.replace = blocked
        try:
            tracker._write_state(target, {"write_count": 7})
        finally:
            os.replace = real_replace
        check("blocked os.replace -> 3 tries, then in-place write",
              json.load(open(target, encoding="utf-8")).get("write_count") == 7 and len(calls) == 3, str(calls))
        check("blocked os.replace leaves no tmp file", not [f for f in os.listdir(tmp) if f.endswith(".tmp")])

    print()
    if FAILURES:
        print(f"DIRTY-TRACKER TESTS FAILED: {len(FAILURES)} -> {FAILURES}")
        sys.exit(1)
    print("ALL DIRTY-TRACKER TESTS PASSED")


if __name__ == "__main__":
    main()

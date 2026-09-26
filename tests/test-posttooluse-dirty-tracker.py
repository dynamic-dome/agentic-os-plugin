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


def run_hook(payload, env_project=None, script=None):
    env = os.environ.copy()
    env.pop("CLAUDE_PROJECT_DIR", None)
    if env_project:
        env["CLAUDE_PROJECT_DIR"] = env_project
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, script or SCRIPT],
        input=data.encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=15,
    )


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}: {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


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

    shutil.rmtree(root, ignore_errors=True)
    print()
    if FAILURES:
        print(f"DIRTY-TRACKER TESTS FAILED: {len(FAILURES)} -> {FAILURES}")
        sys.exit(1)
    print("ALL DIRTY-TRACKER TESTS PASSED (20 tests)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run the plugin tests against a throwaway home (5.13.0).

    python tests/run-isolated.py                          # the whole suite (tests/run-all.sh)
    python tests/run-isolated.py tests/test-x.py [...]    # only these scripts (.py or .sh)
    python tests/run-isolated.py --guard PATH ...         # watch an extra path (repeatable)

Why: a test run that inherits the real home can read and write the real stores. run-all.sh refuses to
start without AGENTIC_OS_TEST_HOME; this runner is the way in. It builds the child environment with
`_isolated_home` (HOME and USERPROFILE point to a fresh home on the drive root, never under a store;
session variables and every AGENTIC_OS_* are dropped), removes every CLAUDE_* variable, sets
AGENTIC_OS_TEST_HOME=1 and starts the tests with the bash found on PATH (never the WSL launcher).

Before and after the run it takes a snapshot (relative path, mtime, size) of the real stores that exist:
~/.agent-memory, ~/AI/.agent-memory, the repo's own .agent-memory and every --guard path. Differences are
listed and counted but do not change the exit code, because parallel sessions write there too.

The output ends with the two lines `EXIT <n>` and `ABWEICHUNGEN <n>`. The exit code is the test run's
(with several scripts: the first non-zero one), 2 if the run could not be started.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _isolated_home import IsolationError, isolated_env  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
MAX_LISTED = 20  # deviations printed; the rest is only counted


def real_stores() -> list[Path]:
    """The stores a leaking test would hit, resolved before HOME is redirected. Only those that exist."""
    home = Path(os.path.expanduser("~"))
    candidates = [home / ".agent-memory", home / "AI" / ".agent-memory", REPO / ".agent-memory"]
    return [p for p in candidates if p.exists()]


def snapshot(path: Path) -> dict[str, str]:
    """Relative path -> 'mtime_ns|size' for a folder tree (os.scandir, fast on NTFS) or a single file."""
    if path.is_file():
        st = path.stat()
        return {".": f"{st.st_mtime_ns}|{st.st_size}"}
    state, stack = {}, [str(path)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        st = entry.stat(follow_symlinks=False)
                        rel = Path(entry.path).relative_to(path).as_posix()
                        state[rel] = f"{st.st_mtime_ns}|{st.st_size}"
        except OSError:
            continue  # a vanishing temp file of a parallel session is not a finding
    return state


def deviations(before: dict, after: dict) -> list[str]:
    found = []
    for root in sorted(set(before) | set(after)):
        old, new = before.get(root, {}), after.get(root, {})
        for rel in sorted(set(old) | set(new)):
            if rel not in old:
                kind = "neu"
            elif rel not in new:
                kind = "gelöscht"
            elif old[rel] != new[rel]:
                kind = "geändert"
            else:
                continue
            found.append(f"{root}/{rel} ({kind})" if rel != "." else f"{root} ({kind})")
    return found


def find_bash(explicit: str | None) -> str:
    bash = explicit or shutil.which("bash")
    if not bash:
        raise SystemExit("bash nicht im PATH (Git Bash nötig, notfalls --bash PFAD)")
    if "system32" in bash.lower():
        raise SystemExit(f"{bash} ist die WSL-bash; Git Bash mit --bash PFAD angeben")
    return bash


def commands(scripts: list[str], bash: str) -> list[list[str]]:
    if not scripts:
        return [[bash, str(REPO / "tests" / "run-all.sh")]]
    cmds = []
    for name in scripts:
        path = Path(name).resolve()
        if not path.is_file():
            raise SystemExit(f"Testskript nicht gefunden: {name}")
        if path.suffix == ".py":
            cmds.append([sys.executable, str(path)])
        elif path.suffix == ".sh":
            cmds.append([bash, str(path)])
        else:
            raise SystemExit(f"Nur .py und .sh werden gestartet: {name}")
    return cmds


def strip_claude_vars(env: dict) -> dict:
    for key in [k for k in env if k.upper().startswith("CLAUDE_")]:
        del env[key]
    return env


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scripts", nargs="*", help="Testskripte (.py/.sh); ohne Angabe läuft tests/run-all.sh")
    ap.add_argument("--guard", action="append", default=[], metavar="PFAD", help="zusätzlich beobachteter Pfad")
    ap.add_argument("--bash", default=None, help="Pfad zu Git Bash (Standard: bash aus dem PATH)")
    args = ap.parse_args(argv)

    try:
        cmds = commands(args.scripts, find_bash(args.bash))
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2
    guarded = real_stores() + [p for p in (Path(g) for g in args.guard) if p.exists()]
    before = {str(p): snapshot(p) for p in guarded}

    code = 0
    try:
        with isolated_env(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
                          AGENTIC_OS_TEST_HOME="1") as (env, _home):
            strip_claude_vars(env)
            for cmd in cmds:
                sys.stdout.flush()
                try:
                    rc = subprocess.run(cmd, cwd=REPO, env=env).returncode
                except OSError as exc:  # e.g. an explicit --bash path that does not exist
                    print(f"Testprozess nicht startbar ({cmd[0]}): {exc}", file=sys.stderr)
                    code = 2
                    break
                code = code or rc
    except IsolationError as exc:
        print(f"Isolation nicht herstellbar: {exc}", file=sys.stderr)
        return 2

    found = deviations(before, {str(p): snapshot(p) for p in guarded})
    for line in found[:MAX_LISTED]:
        print(f"ABWEICHUNG {line}")
    if len(found) > MAX_LISTED:
        print(f"ABWEICHUNG ... und {len(found) - MAX_LISTED} weitere")
    print(f"EXIT {code}")
    print(f"ABWEICHUNGEN {len(found)}")
    return code


if __name__ == "__main__":
    sys.exit(main())

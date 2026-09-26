#!/usr/bin/env python3
"""PostToolUse hook: mechanical dirty-state tracker (agentic-os).

Records un-consolidated work per session: after every successful Write/Edit
outside .agent-memory/, upserts .agent-memory/working/dirty-<session_id>.json
with dirty: true and the touched file list. wrap-up consumes these files
(sets dirty: false + consolidated_at); session-bootstrap and session-start.sh
use them to detect sessions that ended without consolidation.

Contract (must never break):
- Fail-soft: any error -> silent no-op, always exit 0. A memory hook must
  never block or delay real work.
- Only mechanical facts (paths, counts, timestamps) — no LLM, no content.
- Writes are atomic (tmp + os.replace) so a killed session never leaves a
  half-written state file.
- Re-dirtying is self-healing: if wrap-up consolidates a live parallel
  session's file, that session's next write simply sets dirty: true again.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

TRACKED_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch"}
MAX_TOUCHED_FILES = 200
# T-24: Herkunft des Flags. Unter Codex laeuft dieses Skript aus dem
# Codex-Plugin-Cache — der eigene Pfad ist das deterministische Signal.
AGENT = "codex" if "/.codex/" in os.path.abspath(__file__).replace("\\", "/").lower() else "claude"
# T-24: Codex' apply_patch traegt KEIN file_path — die Pfade stehen im
# Patch-Text (S0-a, membrain/memcodexlifecycle.md §3.1).
PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
# Paths that are never "work": memory consolidation itself, the Claude
# scratchpad (session-temporary by definition) and git internals.
# Compared lowercase against an absolute, slash-normalized path (Windows
# filesystems are case-insensitive; relative inputs are resolved first).
SKIP_MARKERS = ("/.agent-memory/", "/appdata/local/temp/claude/", "/.git/")


def _safe_count(value) -> int:
    """Counter aus unvalidiertem State: nie werfen, nie negativ.

    Ein korrupter Wert ("kaputt", Liste, ...) darf das Tracking nicht dauerhaft
    stilllegen — int() wuerde werfen, der aeussere Fail-soft-Catch schluckt das,
    und JEDER folgende Hook-Aufruf scheitert am selben Feld erneut.
    """
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def main() -> None:
    data = json.loads(sys.stdin.read() or "{}")
    if (data.get("tool_name") or "") not in TRACKED_TOOLS:
        return

    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    candidates = [file_path] if file_path else PATCH_FILE_RE.findall(str(tool_input.get("command") or ""))
    if not candidates:
        return

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()

    # Resolve relative paths against the project dir BEFORE the skip check —
    # a relative ".agent-memory/x" must be skipped exactly like its absolute
    # form. Lowercase both sides: Windows filesystems are case-insensitive.
    kept = []
    for cand in candidates:
        abs_path = cand if os.path.isabs(cand) else os.path.join(project_dir, cand)
        norm = abs_path.replace("\\", "/").lower()
        if any(marker in norm for marker in SKIP_MARKERS):
            continue
        kept.append(cand)
    if not kept:
        return
    memory_dir = os.path.join(project_dir, ".agent-memory")
    if not os.path.isdir(memory_dir):
        return  # project does not use agentic-os

    raw_sid = str(data.get("session_id") or "unknown")[:64]
    sid = "".join(c for c in raw_sid if c.isalnum() or c in "-_") or "unknown"

    working_dir = os.path.join(memory_dir, "working")
    os.makedirs(working_dir, exist_ok=True)
    state_path = os.path.join(working_dir, f"dirty-{sid}.json")

    state = {}
    if os.path.isfile(state_path):
        try:
            with open(state_path, encoding="utf-8") as fh:
                state = json.load(fh)
            if not isinstance(state, dict):
                state = {}
        except Exception:
            state = {}  # corrupt file -> rebuild from scratch

    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    touched = state.get("touched_files")
    if not isinstance(touched, list):
        touched = []
    for cand in kept:
        if cand not in touched:
            touched.append(cand)
    touched = touched[-MAX_TOUCHED_FILES:]

    # Re-dirtying a consolidated file must not erase the consolidation fact:
    # bootstrap needs "last_consolidated_at + few writes since" to tell wrap-up
    # tail writes (false positive) apart from a genuinely crashed session.
    if state.get("consolidated_at"):
        state["last_consolidated_at"] = state["consolidated_at"]
        state["last_consolidated_by"] = state.get("consolidated_by")
        state["writes_since_consolidation"] = 0
    if state.get("last_consolidated_at"):
        state["writes_since_consolidation"] = _safe_count(state.get("writes_since_consolidation")) + 1

    state.update(
        {
            "session_id": raw_sid,
            "agent": AGENT,
            "dirty": True,
            "started": state.get("started") or now,
            "updated": now,
            "touched_files": touched,
            "write_count": _safe_count(state.get("write_count")) + 1,
            "consolidated_at": None,
            "consolidated_by": None,
        }
    )

    tmp_path = state_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
    os.replace(tmp_path, state_path)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail-soft by contract
    sys.exit(0)

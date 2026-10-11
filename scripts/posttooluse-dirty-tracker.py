#!/usr/bin/env python3
"""PostToolUse hook: mechanical dirty-state tracker (agentic-os).

Records un-consolidated work per session: after a successful Write/Edit of
this store's own work, upserts .agent-memory/working/dirty-<session_id>.json
with dirty: true and the touched file list (other stores' paths only land in
foreign_touched; temp and out-of-store paths are ignored - see the contract). wrap-up consumes these files
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
- Only this store's own work makes it dirty (Lebenszyklus Phase 2, DCO #9693):
  inside the project everything is own work unless a deeper store is its own
  repository (.git); outside it, the deepest ancestor holding an .agent-memory
  owns the path. A path owned by ANOTHER store goes to
  foreign_touched[<store root>] without making either store dirty; scratch
  under the temp dir and paths outside every store are ignored. When in
  doubt a path stays own work: noise in the own store, never a lost session.
  transcript_path and cwd from the payload are kept for the harvest ledger.
- One update at a time per store (the store lock, shared with apply_wrapup):
  two hooks of one session must never lose each other's dirty flag. After
  LOCK_TIMEOUT_S the hook writes unlocked - tracking beats a lost write.
"""
import contextlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import store_lock as _sl
    _LockTimeout = _sl.LockTimeout
except Exception:  # the hook must keep working even without its sibling module
    _sl = None

    class _LockTimeout(Exception):
        pass

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
FOREIGN_MAX_STORES = 20
LOCK_TIMEOUT_S = 2.0


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


def _under(path, root) -> bool:
    """True when normcased `path` lies inside (or is) normcased `root`; another drive never is."""
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _owner(path, own_root):
    """Directory whose store owns `path` (real case), or None.

    Inside the project: the project itself, unless a deeper store is its own
    repository (.git) - a stray or auto-init store (e.g. AI/dual-bridge/scripts)
    never takes the project's work. Outside: the deepest ancestor with a store.
    """
    own_n = os.path.normcase(own_root)
    inside = _under(os.path.normcase(path), own_n)
    d = os.path.dirname(path)
    while True:
        if inside and os.path.normcase(d) == own_n:
            return own_root
        if os.path.isdir(os.path.join(d, ".agent-memory")) and (
                not inside or os.path.exists(os.path.join(d, ".git"))):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _store_lock(memory_dir):
    return _sl.store_lock(memory_dir, timeout=LOCK_TIMEOUT_S) if _sl else contextlib.nullcontext()


def main() -> None:
    # The payload is UTF-8; read as the Windows console code page (cp1252) an
    # umlaut path turns into mojibake and the own project into a foreign one.
    data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
    if (data.get("tool_name") or "") not in TRACKED_TOOLS:
        return

    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    candidates = [file_path] if file_path else PATCH_FILE_RE.findall(str(tool_input.get("command") or ""))
    if not candidates:
        return

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    memory_dir = os.path.join(project_dir, ".agent-memory")
    if not os.path.isdir(memory_dir):
        return  # project does not use agentic-os
    own_root = os.path.realpath(project_dir)
    temp_root = os.path.normcase(os.path.realpath(tempfile.gettempdir()))
    project_in_temp = _under(os.path.normcase(own_root), temp_root)  # e.g. a test fixture

    # Resolve relative paths against the project dir BEFORE the skip check —
    # a relative ".agent-memory/x" must be skipped exactly like its absolute
    # form. Lowercase both sides: Windows filesystems are case-insensitive.
    kept, foreign = [], {}
    for cand in candidates:
        abs_path = cand if os.path.isabs(cand) else os.path.join(project_dir, cand)
        norm = abs_path.replace("\\", "/").lower()
        if any(marker in norm for marker in SKIP_MARKERS):
            continue
        real = os.path.realpath(abs_path)
        if not project_in_temp and _under(os.path.normcase(real), temp_root):
            continue  # helper scripts and scratch in %TEMP% are never work
        store = _owner(real, own_root)
        if store is None:
            continue  # outside every store
        if os.path.normcase(store) == os.path.normcase(own_root):
            kept.append(cand)
        else:
            foreign.setdefault(store, []).append(real)
    if not kept and not foreign:
        return

    raw_sid = str(data.get("session_id") or "unknown")[:64]
    sid = "".join(c for c in raw_sid if c.isalnum() or c in "-_") or "unknown"

    working_dir = os.path.join(memory_dir, "working")
    os.makedirs(working_dir, exist_ok=True)
    state_path = os.path.join(working_dir, f"dirty-{sid}.json")
    try:
        with _store_lock(memory_dir):
            _update(state_path, data, raw_sid, kept, foreign)
    except _LockTimeout:
        _update(state_path, data, raw_sid, kept, foreign)


def _update(state_path, data, raw_sid, kept, foreign) -> None:
    """Read-modify-write of this session's marker (callers hold the store lock)."""
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
    for key in ("transcript_path", "cwd"):  # ledger basis; a payload without them keeps the old value
        value = data.get(key)
        if isinstance(value, str) and value:
            state[key] = value
    if foreign:
        record = state.get("foreign_touched")
        if not isinstance(record, dict):
            record = {}
        for store, paths in foreign.items():
            known = record.pop(store, None)  # re-insert at the end: the cap drops the least recent store
            known = known if isinstance(known, list) else []
            record[store] = (known + [p for p in paths if p not in known])[-MAX_TOUCHED_FILES:]
        state["foreign_touched"] = dict(list(record.items())[-FOREIGN_MAX_STORES:])
        state["foreign_updated"] = now
    if not kept:  # foreign-only: record it, but this store has no new work of its own
        state.setdefault("session_id", raw_sid)
        state.setdefault("agent", AGENT)
        state.setdefault("dirty", False)
        state.setdefault("started", now)
        _write_state(state_path, state)
        return
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

    _write_state(state_path, state)


def _write_state(state_path, state) -> None:
    tmp_path = f"{state_path}.{os.getpid()}.tmp"  # per process: an unlocked fallback never shares a tmp file
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
        os.replace(tmp_path, state_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail-soft by contract
    sys.exit(0)

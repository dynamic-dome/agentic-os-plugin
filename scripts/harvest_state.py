#!/usr/bin/env python3
"""harvest_state.py - the harvest state of a session, kept inside its dirty marker
(Lebenszyklus Phase 2, Teil 2; design A+, D-024; docs: ~/AI/analysis-artifacts/
agentic-os-lebenszyklus-2026-10-07/06-design-scheibe5.md).

A session is
  dirty    - un-consolidated own work (`dirty: true`), whatever else the marker says
  closed   - `harvest.closed_by` is set AND `harvest.snap` still equals the marker's
             current (write_count, updated, foreign_updated, foreign_write_count). Any later write, own or
             foreign and by any tracker version, changes one of them, so a session that
             continues reopens by itself - the tracker needs no harvest logic.
  pending  - everything else: consolidated headless, consolidated by ANOTHER session's
             wrap-up (D-024: that harvests git, not this session's conversation), or a
             foreign-only trace.

Closing (E7 a, D-023) follows four deterministic rules - never a judgment:
  wrapup_im_transkript  a real wrap-up invocation in the session's own transcript,
                        followed by at most TAIL_WRITES_MAX writes
  native_note           a native memory note carries originSessionId == sid
  trivial               fewer than TRIVIAL_EDITS edits outside temp, and only for
                        markers with tracker data (transcript_path) and a known
                        foreign edit count
  transcript_fehlt      the RECORDED transcript is gone (never inferred from a missing
                        path, never for codex)
plus "wrap-up" (the session's own judged wrap-up, set by apply_wrapup) and "legacy"
(one-time backfill of pre-5.5.0 markers).

Learnings, decisions and identity are never touched here (D-001). Closing only marks a
marker deletable; gc_dirty_markers.py stays the only deleter.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import store_lock  # noqa: E402

# Shared with wrapup_core / nightly_consolidate: a dirty file that carries
# last_consolidated_at and at most this many writes since is wrap-up's own tail.
TAIL_WRITES_MAX = 5
TRIVIAL_EDITS = 3
QUIET_HOURS = 12
STALE_DAYS = 14  # same horizon as nightly_consolidate --max-age-days
# A transcript written shortly after the SessionEnd hook is the session's own last
# flush, not a resumed session.
END_FLUSH_SECONDS = 300
CLOSE_REASONS = ("wrap-up", "wrapup_im_transkript", "native_note", "trivial", "transcript_fehlt", "legacy")
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
WRAPUP_TAGS = ("<command-name>/agentic-os:wrap-up</command-name>", "<command-name>/wrap-up</command-name>")
# A slash invocation IS the message: its command tags come first (re.match anchors), in either order.
_WRAPUP_INVOCATION = re.compile(
    r"\s*(?:<command-message>[^<]*</command-message>\s*)?<command-name>/(?:agentic-os:)?wrap-up</command-name>")
WRAPUP_SKILLS = {"agentic-os:wrap-up", "wrap-up"}
_MARKER_NAME = re.compile(r"^dirty-[A-Za-z0-9_-]+\.json$")


def snapshot(d: dict) -> dict:
    """The fields every tracker version changes on new work. The counters matter: the
    timestamps have one-second resolution, two writes in one second differ only there."""
    return {"write_count": d.get("write_count"), "updated": d.get("updated"),
            "foreign_updated": d.get("foreign_updated"), "foreign_write_count": d.get("foreign_write_count")}


def is_tail(d: dict) -> bool:
    """True when a dirty file only holds a judged wrap-up's own writes after its consolidation
    (at most TAIL_WRITES_MAX). Never after a headless stamp: the night run is a plain subprocess
    the tracker never sees, so every write after it is real session work."""
    if not d.get("last_consolidated_at") or d.get("last_consolidated_by") == "headless":
        return False
    w = d.get("writes_since_consolidation")  # no default: an unrecorded count is no tail evidence
    try:
        return not isinstance(w, bool) and int(w) <= TAIL_WRITES_MAX
    except (TypeError, ValueError):
        return False


def closable(d: dict) -> bool:
    """A marker may be closed only when its work is consolidated (not dirty) or is the tail
    of an explicitly JUDGED wrap-up. Closing un-consolidated work would skip the consolidation
    and let the GC delete the last trace of edits nobody harvested."""
    return not d.get("dirty") or (is_tail(d) and d.get("last_consolidated_by") == "wrap-up")


def marker_state(d) -> str:
    if not isinstance(d, dict):
        return "pending"
    if d.get("dirty"):
        return "dirty"
    h = d.get("harvest")
    if isinstance(h, dict) and h.get("closed_by") in CLOSE_REASONS and h.get("snap") == snapshot(d):
        return "closed"
    return "pending"


def make_closure(d: dict, closed_by: str, run: str, now_iso: str | None = None) -> dict:
    if closed_by not in CLOSE_REASONS:
        raise ValueError(f"unknown closed_by {closed_by!r} (allowed: {', '.join(CLOSE_REASONS)})")
    if not isinstance(run, str) or not run:
        raise ValueError("run must name the closing run")
    now_iso = now_iso or _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    return {"closed_by": closed_by, "closed_at": now_iso, "run": run, "snap": snapshot(d)}


def _epoch(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        t = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t if t.tzinfo else t.astimezone()).timestamp()  # naive = local time


def last_activity(d: dict, fallback_mtime: float | None = None):
    """Epoch of the newest own or foreign write; the file mtime only when neither is
    recorded (a harvest write must never make an old marker look active)."""
    points = [t for t in (_epoch(d.get("updated")), _epoch(d.get("foreign_updated"))) if t is not None]
    return max(points) if points else fallback_mtime


def is_quiet(d: dict, now: float, transcript_mtime: float | None = None, quiet_hours: float = QUIET_HOURS) -> bool:
    """True when the session has provably ended: a session_end block at or after the last
    activity (the hook leaves the block in place when a session continues), or
    `quiet_hours` without any activity. No activity information at all is never quiet."""
    act = last_activity(d)
    se = d.get("session_end")
    end = _epoch(se.get("ended_at")) if isinstance(se, dict) else None
    if end is not None and act is not None and end >= act and (
            transcript_mtime is None or transcript_mtime <= end + END_FLUSH_SECONDS):
        return True
    refs = [t for t in (act, transcript_mtime) if t is not None]
    return bool(refs) and now - max(refs) >= quiet_hours * 3600


def transcript_mtime(d: dict):
    """mtime of the recorded transcript, or None (no path recorded or file gone)."""
    tp = d.get("transcript_path")
    try:
        return os.path.getmtime(tp) if isinstance(tp, str) and tp else None
    except OSError:
        return None


def _count(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def edit_count(d: dict):
    """Own plus foreign edits, or None when foreign writes exist but were not counted
    (markers from trackers before 5.6.0)."""
    own = _count(d.get("write_count"))
    if d.get("foreign_touched"):
        if "foreign_write_count" not in d:
            return None
        return own + _count(d.get("foreign_write_count"))
    return own


def close_reason(d: dict, facts: dict):
    """The first E7 rule that closes this session, or None. Pure over gathered facts."""
    tp = d.get("transcript_path")
    has_tp = isinstance(tp, str) and bool(tp)
    w = facts.get("writes_after_wrapup")
    if isinstance(w, int) and not isinstance(w, bool) and w <= TAIL_WRITES_MAX:
        return "wrapup_im_transkript"
    if facts.get("native_note") is True:
        return "native_note"
    edits = edit_count(d)
    if has_tp and edits is not None and edits < TRIVIAL_EDITS:
        return "trivial"
    if has_tp and d.get("agent") != "codex" and facts.get("transcript_exists") is False:
        return "transcript_fehlt"
    return None


def _is_wrapup_record(o: dict) -> bool:
    msg = o.get("message") if isinstance(o.get("message"), dict) else {}
    content = msg.get("content")
    if o.get("type") == "user":
        texts = [content] if isinstance(content, str) else [
            b.get("text") for b in content if isinstance(b, dict) and b.get("type") == "text"] \
            if isinstance(content, list) else []
        # A tag quoted inside prose or after another command is a discussion, not a run.
        return any(isinstance(t, str) and _WRAPUP_INVOCATION.match(t) for t in texts)
    if o.get("type") == "assistant" and isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Skill"
                   and isinstance(b.get("input"), dict) and b["input"].get("skill") in WRAPUP_SKILLS
                   for b in content)
    return False


def _writes_in(o: dict) -> int:
    if o.get("type") != "assistant":
        return 0
    content = (o.get("message") or {}).get("content") if isinstance(o.get("message"), dict) else None
    if not isinstance(content, list):
        return 0
    return sum(1 for b in content if isinstance(b, dict) and b.get("type") == "tool_use"
               and b.get("name") in WRITE_TOOLS)


def writes_after_wrapup(transcript_path: str):
    """Write tools used after the LAST real wrap-up invocation, or None when the
    transcript has none or cannot be read. Text inside tool results never counts."""
    count = None
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(o, dict):
                    continue
                if _is_wrapup_record(o):
                    count = 0
                elif count is not None:
                    count += _writes_in(o)
    except OSError:
        return None
    return count


_ORIGIN = re.compile(r"^\s*originSessionId:\s*[\"']?([A-Za-z0-9_-]+)[\"']?\s*$", re.MULTILINE)


def _default_native_root() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude", "projects")


def native_session_ids(native_root: str | None = None) -> set:
    """Every originSessionId of the native memory notes (<root>/*/memory/*.md), read once."""
    found = set()
    try:
        projects = list(os.scandir(native_root or _default_native_root()))
    except OSError:
        return found
    for proj in projects:
        try:
            notes = [e.path for e in os.scandir(os.path.join(proj.path, "memory"))
                     if e.is_file() and e.name.endswith(".md")]
        except OSError:
            continue
        for path in notes:
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    found.update(_ORIGIN.findall(fh.read()))
            except OSError:
                continue
    return found


def gather_facts(d: dict, native_root: str | None = None, native_sids: set | None = None) -> dict:
    """Read-only IO for close_reason. Run it OUTSIDE the store lock (transcripts can be
    large); close_marker re-checks the snapshot under the lock. Pass `native_sids`
    (native_session_ids) when evaluating many markers."""
    if native_sids is None:
        native_sids = native_session_ids(native_root)
    tp = d.get("transcript_path")
    sid = str(d.get("session_id") or "")
    facts = {"transcript_exists": None, "writes_after_wrapup": None,
             "native_note": bool(sid) and sid in native_sids}
    if isinstance(tp, str) and tp:
        facts["transcript_exists"] = os.path.isfile(tp)
        if facts["transcript_exists"] and d.get("agent") != "codex":
            facts["writes_after_wrapup"] = writes_after_wrapup(tp)
    return facts


def close_marker(mem: str, name: str, closed_by: str, run: str, expected_snap: dict,
                 now_iso: str | None = None, lock_timeout: float = store_lock.TIMEOUT_SECONDS) -> bool:
    """Close one marker under the store lock. False (file untouched) when it is missing,
    unreadable, already closed, or changed since the facts were gathered."""
    if closed_by not in CLOSE_REASONS:
        raise ValueError(f"unknown closed_by {closed_by!r} (allowed: {', '.join(CLOSE_REASONS)})")
    if not _MARKER_NAME.match(name or ""):
        raise ValueError(f"not a dirty marker name: {name!r}")
    def close(d):
        if marker_state(d) == "closed" or not closable(d):
            return False
        d["harvest"] = make_closure(d, closed_by, run, now_iso)
        d["dirty"] = False
        return True

    return _locked_update(mem, name, expected_snap, close, lock_timeout)


def _locked_update(mem, name, expected_snap, mutate, lock_timeout) -> bool:
    """Re-read the marker under the store lock, refuse a changed snapshot, apply
    `mutate(d) -> bool` and write atomically. False = file untouched."""
    path = os.path.join(mem, "working", name)
    if not os.path.isfile(path):
        return False
    try:
        with store_lock.store_lock(mem, timeout=lock_timeout):
            try:
                with open(path, encoding="utf-8-sig") as fh:
                    d = json.load(fh)
            except (OSError, ValueError):
                return False
            if not isinstance(d, dict) or snapshot(d) != expected_snap or not mutate(d):
                return False
            tmp = f"{path}.{os.getpid()}.tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(d, fh, ensure_ascii=False, indent=1)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
    except store_lock.LockTimeout:
        return False
    return True


def stale_reported(d: dict) -> bool:
    """True while the stale stamp still describes the marker (new work clears it)."""
    s = d.get("stale_reported")
    return isinstance(s, dict) and s.get("snap") == snapshot(d)


def mark_stale(mem: str, name: str, expected_snap: dict, now_iso: str | None = None,
               lock_timeout: float = store_lock.TIMEOUT_SECONDS) -> bool:
    """Stamp a marker as reported stale, so the drift is reported exactly once."""
    if not _MARKER_NAME.match(name or ""):
        raise ValueError(f"not a dirty marker name: {name!r}")

    def stamp(d):
        if stale_reported(d) or marker_state(d) == "closed":
            return False
        d["stale_reported"] = {"at": now_iso or _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                               "snap": snapshot(d)}
        return True

    return _locked_update(mem, name, expected_snap, stamp, lock_timeout)


def close_pass(mem: str, run: str, now: float | None = None, native_root: str | None = None,
               native_sids: set | None = None, stale_days: float = STALE_DAYS, dry: bool = False) -> dict:
    """One store: close every quiet, not yet closed marker an E7 rule covers; stamp the
    rest that is older than `stale_days` as stale (once). Learnings, decisions and
    identity are never touched. Returns {closed: [{sid, closed_by}], stale: [sid],
    open: n, unreadable: [name]}."""
    import time as _time
    now = _time.time() if now is None else now
    if native_sids is None:
        native_sids = native_session_ids(native_root)
    result = {"closed": [], "stale": [], "open": 0, "unreadable": []}
    work = os.path.join(mem, "working")
    try:
        names = sorted(n for n in os.listdir(work) if _MARKER_NAME.match(n))
    except OSError:
        return result
    for name in names:
        path = os.path.join(work, name)
        try:
            with open(path, encoding="utf-8-sig") as fh:
                d = json.load(fh)
            if not isinstance(d, dict):
                raise ValueError("not an object")
        except (OSError, ValueError):
            result["unreadable"].append(name)
            continue
        if marker_state(d) == "closed":
            continue
        sid = str(d.get("session_id") or name[len("dirty-"):-len(".json")])
        if closable(d) and is_quiet(d, now, transcript_mtime(d)):
            reason = close_reason(d, gather_facts(d, native_sids=native_sids))
            if reason and (dry or close_marker(mem, name, reason, run, snapshot(d))):
                result["closed"].append({"sid": sid, "closed_by": reason})
                continue
        result["open"] += 1
        act = last_activity(d)
        if act is not None and now - act >= stale_days * 86400 and not stale_reported(d):
            if dry or mark_stale(mem, name, snapshot(d)):
                result["stale"].append(sid)
    return result

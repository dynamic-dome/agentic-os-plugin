"""Gap list for the wrap-up judge (6.0.0, spec docs/superpowers/specs/2026-11-20-prelude-luecken-design.md).

Reads the log of the prelude-gate mod (one JSON file per prompt, `<UTC day>/<ts>-<session[:8]>.json`) and lists
the prompts of this session for which the gate kept no learning. Read-only. Where the log lives is a setting
(`config.json` -> `prelude_log_dir`, else `AGENTIC_OS_PRELUDE_LOG_DIR`); there is no built-in path.

A gap: reason `ok` or `empty` and no kept learning. `budget`, `daemon`, `gate` are unchecked (nobody knows whether
something was missing); `skip` (too short) and `repeat` (already served) do not count at all.
Only today and the DAYS UTC days before are read; a session with entries before that (or an older day folder that
cannot be listed) gets the status `teilweise: ...`. The older check looks at file names only (session[:8]), so a
rare session with the same prefix can also mark the list as partial.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re

ENV_VAR = "AGENTIC_OS_PRELUDE_LOG_DIR"
CHECKED = ("ok", "empty")
UNCHECKED = ("budget", "daemon", "gate")
MAX_GAPS = 15
PROMPT_CAP = 300
DAYS = 3
_CODE = re.compile(r"```.*?(```|$)", re.S)


def resolve_log_dir(cfg: dict) -> str | None:
    """config.json wins over the environment; empty values count as unset."""
    value = (cfg or {}).get("prelude_log_dir") or os.environ.get(ENV_VAR) or ""
    return str(value).strip() or None


def _clean(prompt: str) -> str:
    return " ".join(_CODE.sub(" [Code] ", prompt).split())[:PROMPT_CAP]


def prelude_gaps(session_id: str, log_dir: str | None, now: _dt.datetime | None = None) -> dict:
    """-> {status, gaps: [{prompt, ts}] newest first (at most 15), geprueft, nicht_geprueft}."""
    out = {"status": "ok", "gaps": [], "geprueft": 0, "nicht_geprueft": 0}
    if not log_dir:
        out["status"] = "nicht eingerichtet"
        return out
    if not os.path.isdir(log_dir):
        out["status"] = "nicht geprüft: Ordner fehlt"
        return out
    if not session_id:
        out["status"] = "nicht geprüft: keine Sitzungs-ID"
        return out
    now = now or _dt.datetime.now(_dt.timezone.utc)
    first_day = (now.astimezone(_dt.timezone.utc).date() - _dt.timedelta(days=DAYS)).isoformat()
    suffix = f"-{(session_id or '')[:8]}.json"
    gaps, seen = [], 0
    try:
        days = sorted(d for d in os.listdir(log_dir) if d >= first_day and os.path.isdir(os.path.join(log_dir, d)))
        for day in days:
            for name in os.listdir(os.path.join(log_dir, day)):
                if not name.endswith(suffix):
                    continue
                try:  # one odd file (format drift, deep nesting) is skipped, never fatal
                    with open(os.path.join(log_dir, day, name), encoding="utf-8-sig") as fh:
                        rec = json.load(fh)
                    if not isinstance(rec, dict) or rec.get("session") != session_id:
                        continue
                    seen += 1
                    reason = rec.get("reason")
                    if reason in UNCHECKED:
                        out["nicht_geprueft"] += 1
                        continue
                    if reason not in CHECKED:
                        continue
                    kept = rec.get("kept")
                    learnings = kept.get("learnings") if isinstance(kept, dict) else None
                    out["geprueft"] += 1
                    prompt = _clean(str(rec.get("prompt") or ""))
                    if not (isinstance(learnings, list) and learnings) and prompt:
                        gaps.append({"prompt": prompt, "ts": _ts(rec.get("ts"))})
                except Exception:  # noqa: BLE001
                    continue
    except OSError as exc:
        return {"status": f"nicht geprüft: {str(exc)[:120]}", "gaps": [], "geprueft": 0, "nicht_geprueft": 0}
    gaps.sort(key=lambda g: -g["ts"])
    out["gaps"] = gaps[:MAX_GAPS]
    try:
        older = _has_older(log_dir, first_day, suffix)
    except OSError:
        older = True  # an older day that cannot be listed: completeness unknown, the gaps found so far stay
    if older:  # 6.0.2 (Codex): a session longer than the window gets an incomplete list, never a plain "ok"
        out["status"] = f"teilweise: Einträge vor {first_day} nicht gelesen"
        return out
    if not seen:  # wrong folder, mod off, or nothing logged for this session: not a silent "nothing missing"
        out["status"] = "nicht geprüft: keine Einträge dieser Sitzung"
    return out


def _has_older(log_dir: str, first_day: str, suffix: str) -> bool:
    """True when a day folder before the window holds a file of this session (name check only, nothing is read;
    the file name carries only session[:8], so a rare prefix twin can also mark the list as partial)."""
    for day in sorted((d for d in os.listdir(log_dir) if d < first_day), reverse=True):
        path = os.path.join(log_dir, day)
        if os.path.isdir(path) and any(n.endswith(suffix) for n in os.listdir(path)):
            return True
    return False


def _ts(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0

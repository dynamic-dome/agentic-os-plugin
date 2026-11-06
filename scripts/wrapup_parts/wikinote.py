"""Session note as a deterministic projection (obsidian-sync Step 3/7 as code, T-028 headless path).

Writes <wiki_root>/wiki/queries/YYYY-MM-DD-session-<project>-headless.md plus one index line under
'## Queries' and one log block. Idempotent per project and day: the note is rewritten in place,
index line and log block are added once. Never writes outside <wiki_root>.
"""
from __future__ import annotations

import os
import tempfile

MAX_ITEMS = 12


def _read(path):
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


def _write(path, text):
    try:  # keep an existing file's line endings and BOM (real wiki index.md/log.md have one); new files: LF, no BOM
        with open(path, "rb") as fh:
            raw = fh.read()
        eol = "\r\n" if b"\r\n" in raw else "\n"
        enc = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
    except OSError:
        eol, enc = "\n", "utf-8"
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=enc, newline=eol) as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _render_judged(project_id, date, iterations, summary):
    """Note of a judged wrap-up: a deterministic projection of the plan (spec 2026-11-05, A6)."""
    state = summary.get("current_state") or summary.get("active_task") or ""
    lines = ["---", "type: query", "status: final", f"created: {date}", f"updated: {date}", "source_count: 0",
             "tags:", "  - session", "  - agent-generated", "aliases: []", "query_kind: session",
             "question: Was wurde in dieser Session erarbeitet?", "scope: []", f"project: {project_id}",
             "agent: claude-code", f"iterations: {len(iterations)}", "quality_delta: null",
             "patterns_found: 0", "authority: derived", "---", "",
             f"# {date} Session: {project_id}", "", "## Was wurde gemacht", ""]
    lines += [f"- {x}" for x in (summary.get("what_was_done") or [])[:MAX_ITEMS]]
    if state:
        lines += ["", "## Stand", "", str(state)]
    files = []
    for it in iterations:
        for f in it.get("files_changed") or []:
            if f not in files:
                files.append(f)
    if files:
        lines += ["", "## Berührte Dateien", ""] + [f"- `{f}`" for f in files[:MAX_ITEMS]]
    return "\n".join(lines).rstrip() + "\n"


def _render(project_id, date, iterations):
    lines = ["---", "type: query", "status: final", f"created: {date}", f"updated: {date}", "source_count: 0",
             "tags:", "  - session", "  - agent-generated", "  - headless", "aliases: []", "query_kind: session",
             "question: Was wurde in dieser Session erarbeitet?", "scope: []", f"project: {project_id}",
             # agent: kanonischer Wiki-Schema-Wert; "headless" steht als Tag (5.1.4)
             "agent: claude-code", f"iterations: {len(iterations)}", "quality_delta: null",
             "patterns_found: 0", "authority: derived", "---", "",
             f"# {date} Session: {project_id} (headless)", "",
             "## Kontext", "",
             "Automatisch konsolidiert (Nachtlauf, `wrapup_core.py apply --headless`), ohne Judge: Iterationen "
             "stammen mechanisch aus git log und uncommitted dirty-Dateien. Learnings, Decisions und Identity "
             "wurden nicht geerntet.", "",
             "## Was wurde gemacht", ""]
    lines += [f"- {it.get('type', 'feature')}: {it.get('title', '')}" for it in iterations[:MAX_ITEMS]]
    if len(iterations) > MAX_ITEMS:
        lines.append(f"- … {len(iterations) - MAX_ITEMS} weitere")
    files = []
    for it in iterations:
        for f in it.get("files_changed") or []:
            if f not in files:
                files.append(f)
    lines += ["", "## Berührte Dateien", ""] + [f"- `{f}`" for f in files[:MAX_ITEMS]]
    return "\n".join(lines).rstrip() + "\n"


def _add_index_line(index_path, line, rel):
    text = _read(index_path) if os.path.exists(index_path) else "# Index\n"
    if rel in text:
        return False
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == "## Queries":
            lines.insert(i + 1, line)
            break
    else:
        lines += ["", "## Queries", line]
    _write(index_path, "\n".join(lines).rstrip("\n") + "\n")
    return True


def _add_log_block(log_path, header, block):
    text = _read(log_path) if os.path.exists(log_path) else "# Log\n"
    if header in text:
        return False
    _write(log_path, text.rstrip("\n") + "\n\n" + block.rstrip("\n") + "\n")
    return True


def write_session_note(wiki_root, project_id, date, iterations, *, enabled=True, threshold=1, summary=None,
                       session_id=""):
    """-> '<rel path>' on success, 'skipped(<reason>)' or 'failed(<reason>)'.

    With `summary` the note belongs to a judged wrap-up and carries the session in its name: two sessions
    of one day must not overwrite each other; the caller has applied the substantiality gate. Without it,
    it is the night run's note of the day and `threshold` decides.
    """
    if not enabled:
        return "skipped(sync disabled)"
    if summary is None and len(iterations) < max(1, int(threshold or 1)):
        return "skipped(below threshold)"
    queries = os.path.join(wiki_root, "wiki", "queries")
    if not os.path.isdir(queries):
        return "skipped(no wiki root)"
    judged = summary is not None
    label = (session_id[:8] or "session") if judged else "headless"
    origin = "Wrap-up" if judged else "automatisch konsolidiert"
    stamp = "manuell per obsidian-sync" if judged else "headless"
    rel = f"wiki/queries/{date}-session-{project_id}-{label}.md"
    try:
        text = _render_judged(project_id, date, iterations, summary) if judged else _render(project_id, date, iterations)
        _write(os.path.join(wiki_root, *rel.split("/")), text)
        _add_index_line(os.path.join(wiki_root, "index.md"),
                        f"- [{date} Session: {project_id} ({label})]({rel}) — {project_id}, "
                        f"{len(iterations)} Iterationen ({origin})", rel)
        header = f"## [{date}] agent-sync | {project_id} ({label})"
        _add_log_block(os.path.join(wiki_root, "log.md"), header,
                       f"{header}\n\n- Session-Note: {rel}\n- Entity updated: no ({stamp})\n"
                       f"- Decisions promoted: 0 ({stamp})\n- Learnings promoted: 0 ({stamp})\n"
                       f"- Patterns flagged: 0 ({stamp})\n- Total pages touched: 3")
    except (OSError, ValueError) as exc:  # ValueError: a file that is not UTF-8
        return f"failed({type(exc).__name__})"
    return rel

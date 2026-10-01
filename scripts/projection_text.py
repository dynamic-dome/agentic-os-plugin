#!/usr/bin/env python3
"""Short-form text for the learnings projections (MEMORY.md and AGENTS.md).

Both blocks are loaded into every session of their agent (Claude Code reads
MEMORY.md, Codex reads AGENTS.md), so an entry is a short form behind its [id];
the full text stays in learnings.json. One home for the rule, so the two
projections cannot drift apart.
"""

SUMMARY_CHARS = 150
LINE_LIMIT = 200
ELLIPSIS = "…"
POINTER = "Kurzfassungen — Volltext per ID: .agent-memory/learnings/learnings.json"


BOM = "﻿"


def read_raw(path):
    """-> (bom, body exactly as on disk minus the BOM, eol for NEW lines).
    Projections never touch bytes outside their block: the rest of the file keeps
    its own line endings (even mixed ones), only the rendered block takes the
    file's style - CRLF as soon as one CRLF exists (handoff.py rule since 5.1.2).
    The BOM is split off so a block right at the start is still recognised."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    bom = raw.startswith(BOM)
    body = raw[1:] if bom else raw
    return bom, body, ("\r\n" if "\r\n" in body else "\n")


def with_eol(text, eol):
    return text.replace("\n", eol) if eol != "\n" else text


def _ends_with_blank_line(text):
    return text.endswith("\n\n") or text.endswith("\n\r\n")


def strip_block(text, begin_prefix, end):
    """Remove a managed block; every other line keeps its bytes. Trailing blank
    lines left behind are trimmed - that is the separator the projection itself
    inserted before the block, for LF and CRLF alike (otherwise a CRLF file would
    grow one blank line per run). Returns text unchanged if no block exists."""
    out, inside, found = [], False, False
    for line in text.split("\n"):
        if not inside and line.startswith(begin_prefix):
            inside, found = True, True
            continue
        if inside:
            if line.strip() == end:
                inside = False
            continue
        out.append(line)
    if not found:
        return text
    result = "\n".join(out)
    while _ends_with_blank_line(result):
        result = result[:-2] if result.endswith("\r\n") else result[:-1]
    return result


def assemble(bom, base, block, eol):
    """Existing content (raw) + separator + block, with the BOM put back."""
    if base and not base.endswith("\n"):
        base += eol
    return (BOM if bom else "") + base + (eol if base else "") + with_eol(block, eol)


def shorten(text, limit):
    """Whitespace-collapsed text, cut to <= limit chars at a word boundary + ellipsis."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    head = text[:limit - len(ELLIPSIS)]
    cut = head.rfind(" ")
    if cut > 0:
        head = head[:cut]
    return head.rstrip(" ,;:-—") + ELLIPSIS


def bounded_line(prefix, text, limit=SUMMARY_CHARS):
    """prefix + short form of text; the whole line never exceeds LINE_LIMIT."""
    room = max(LINE_LIMIT - len(prefix), len(ELLIPSIS))
    line = prefix + shorten(text, min(limit, room))
    # an absurdly long id/date alone could still exceed the limit: hard cap
    return line if len(line) <= LINE_LIMIT else line[:LINE_LIMIT - len(ELLIPSIS)] + ELLIPSIS


def entry_line(e):
    """'- [id] (date[, codex]) short form' for one learnings.json entry. An
    author-written `summary` (wrap-up, optional) wins over cutting the full text,
    whose first 150 chars are often context rather than the conclusion."""
    src = ", codex" if e.get("source_agent", "claude") == "codex" else ""
    summary = e.get("summary")
    text = summary if isinstance(summary, str) and summary.strip() else e.get("text")
    return bounded_line(f"- [{e.get('id')}] ({e.get('date')}{src}) ", text)

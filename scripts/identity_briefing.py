#!/usr/bin/env python3
"""identity_briefing.py — the profile lines the SessionStart briefing shows (P2, Release A; DCO #10148 point 3).

WHY: the hook used to show `head -4` of user.md - the four OLDEST lines, placeholders included ("- (Will be
populated through session observations)"), and in 36 stores the newest, most confirmed statements never reached the
briefing. This module writes a small projection, `identity/briefing-lines.txt`, next to the profile; the hook only
reads that file.

Contract:
  - Only bullet lines of the profile, no placeholder, no preamble, no heading.
  - Ranking: a line confirmed by the user's own words (`confirmed_by == "quote"` on the candidate its citation
    names) first; then the candidate's `occurrences` (a line without a candidate counts 1), descending; then its
    `last_seen`, descending; then the order in the file.
  - The text loses its citation "(PC12, 2026-10-28)"; every backslash becomes "/" (the hook turns a literal "\\n"
    into a line break), control characters become spaces. At most MAX_LINES lines, each at most MAX_CHARS_LINE
    characters (cut with "…"), together at most MAX_CHARS_TOTAL.
  - Written by apply_wrapup.py after every run that wrote the profile or its queue, and by the migration.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata

MAX_LINES, MAX_CHARS_LINE, MAX_CHARS_TOTAL = 6, 160, 900
BRIEFING_LINES = "identity/briefing-lines.txt"  # identity_store.BRIEFING_LINES, relative to the store

# One home for the placeholder rule (apply_wrapup imports it from here)
_PLACEHOLDER_LINE = re.compile(r"^-\s*\((?:Will be populated|Recorded when|Weiteres wird|Wird erst nach)", re.IGNORECASE)
_PAREN_ONLY_LINE = re.compile(r"-\s*\([^()]*\)")
# a candidate citation "(PC12, 2026-10-28)" among the bracket groups that END the line; the LAST one is the citation, an
# earlier group that merely looks like one ("(Python3, lokal)") is content (Codex P2-A-R2-01)
_CITATION_AT_END = re.compile(r"\s*[\[(]\s*(?P<id>[A-Za-z]{1,6}\d{1,6})\s*,[^\[\]()]*[\])]"
                              r"(?=(?:\s*[\[(][^\[\]()]*[\])])*\s*$)")


def is_placeholder_line(line: str) -> bool:
    """The stub lines of a fresh user.md ('- (Will be populated ...)') and any bullet that is only a remark in parentheses."""
    s = line.strip()
    return bool(_PLACEHOLDER_LINE.match(s)) or bool(_PAREN_ONLY_LINE.fullmatch(s))


def _clean(text: str) -> str:
    text = text.replace("\\", "/")
    text = "".join(" " if unicodedata.category(ch) in ("Cc", "Zl", "Zp") else ch for ch in text)
    return " ".join(text.split())


def _cut(line: str) -> str:
    return line if len(line) <= MAX_CHARS_LINE else line[:MAX_CHARS_LINE - 1].rstrip() + "…"


def select_lines(md_text: str, queue: list[dict]) -> list[str]:
    by_id = {str(c.get("id")): c for c in queue if isinstance(c, dict) and c.get("id")}
    ranked = []
    # a profile line ends only at LF, like for every other reader of the file: VT, FS, NEL, LS or PS inside a line
    # become spaces (_clean) instead of splitting it (Codex P2-A-03)
    for pos, raw in enumerate(md_text.lstrip("\ufeff").split("\n")):
        line = raw.strip()
        if not line.startswith("- ") or is_placeholder_line(line):
            continue
        cites = list(_CITATION_AT_END.finditer(line))
        cited = cites[-1] if cites else None
        cand = by_id.get(cited.group("id")) if cited else None
        cand = cand or {}
        try:
            occ = int(cand.get("occurrences", 1))
        except (TypeError, ValueError):
            occ = 1
        # without the citation, also when bracket notes follow it (P2-A-04)
        text = _clean(line[:cited.start()] + line[cited.end():] if cited else line)
        if text in ("-", ""):
            continue
        ranked.append((cand.get("confirmed_by") == "quote", occ, str(cand.get("last_seen") or ""), pos, _cut(text)))
    ranked.sort(key=lambda r: r[3])                 # file order (least significant)
    ranked.sort(key=lambda r: r[2], reverse=True)   # last_seen, newest first
    ranked.sort(key=lambda r: r[1], reverse=True)   # occurrences
    ranked.sort(key=lambda r: r[0], reverse=True)   # confirmed by a quote, before everything else
    out, total = [], 0
    for *_, text in ranked:
        if len(out) == MAX_LINES or total + len(text) > MAX_CHARS_TOTAL:
            break
        out.append(text)
        total += len(text)
    return out


def _read_text(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


def _read_queue(path: str) -> list:
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    return data if isinstance(data, list) else []


def write_projection(paths, dry: bool, touched: list) -> None:
    """Write paths.root/paths.briefing (project: identity/briefing-lines.txt, central: identity/profile-briefing.txt)
    from the profile and queue named by `paths` (an identity_store.IdentityPaths).
    Called inside the run that already read both files under the lock, so they are readable here."""
    lines = select_lines(_read_text(os.path.join(paths.root, paths.md)),
                         _read_queue(os.path.join(paths.root, paths.queue)))
    touched.append(paths.briefing)
    if dry:
        return
    path = os.path.join(paths.root, *paths.briefing.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(line + "\n" for line in lines))
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

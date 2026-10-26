"""Central handoff + status board writers (wrap-up Step 7.6a/b as code, T-028).

Algorithm and templates: skills/wrap-up/references/handoff-template.md (SSoT). Read-then-write
drift detection reuses handoff_write_guard.file_state (sha256) in-process: hash at read, re-hash
right before the write; on drift re-read, merge the own block into the NEW content, write once,
report `drift-merged`. A second drift in the same run is reported as failed, never forced.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from handoff_write_guard import file_state  # noqa: E402

HEAD_RE = re.compile(r"^# (Letzte Session|Vorherige Session\b.*)$")
DEMOTED_RE = re.compile(r"^# Vorherige Session \((\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?) (.+), erhalten\)\s*$")
PROJECT_RE = re.compile(r"^\*Projekt:\s*(.+?)\*\s*$")
DATE_RE = re.compile(r"^\*Datum:\s*(.+?)\*\s*$")
SEP = "\n\n---\n\n"
CAP = 5
BOARD_STUB = ("# Cross-Project Status Board\n\n"
              "*One section per project. Each wrap-up updates only its own project's section.*\n"
              "*Read by session-bootstrap Step 0.5b. Last-session detail lives in the central handoff.*\n\n"
              "## Cross-Project Notes\n- (items relevant for ALL projects — added on explicit user request only)\n\n"
              "---\n")


def _read(path):
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


def _eol(path):
    """Keep the file's own line endings: the real ~/AI handoff files are CRLF (first real run 2026-09-24
    rewrote them LF - a whole-file diff for one changed section)."""
    try:
        with open(path, "rb") as fh:
            return "\r\n" if b"\r\n" in fh.read() else "\n"
    except OSError:
        return "\n"


def _has_bom(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(3) == b"\xef\xbb\xbf"
    except OSError:
        return False


def _write(path, text):
    eol = _eol(path)
    enc = "utf-8-sig" if _has_bom(path) else "utf-8"  # _read strips the BOM; put it back
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=enc, newline=eol) as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def split_blocks(text):
    """-> (preamble, [block, ...]); blocks start at '# Letzte Session' / '# Vorherige Session ...'."""
    lines = (text or "").splitlines()
    starts = [i for i, ln in enumerate(lines) if HEAD_RE.match(ln)]
    if not starts:
        return (text or "").strip(), []
    pre = "\n".join(lines[:starts[0]]).strip()
    blocks = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        chunk = lines[start:end]
        while chunk and chunk[-1].strip() in ("", "---"):
            chunk.pop()
        blocks.append("\n".join(chunk))
    return pre, blocks


def block_project(blk):
    for ln in blk.splitlines():
        m = PROJECT_RE.match(ln.strip())
        if m:
            return m.group(1).strip()
    m = DEMOTED_RE.match(blk.splitlines()[0]) if blk else None
    return m.group(2).strip() if m else ""


def _block_date(blk):
    for ln in blk.splitlines():
        m = DATE_RE.match(ln.strip())
        if m:
            return m.group(1).strip()
    return ""


def _demote(blk):
    first, _, rest = blk.partition("\n")
    if first.strip() != "# Letzte Session":
        return blk  # already demoted - never re-wrap
    return f"# Vorherige Session ({_block_date(blk)} {block_project(blk)}, erhalten)\n{rest}"


def _norm(name):
    """'dynamic_central_orchestrator' == 'Dynamic-Central-Orchestrator': letters and digits only."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _keys(project):
    """Project display name or [display name, aliases...] -> set of normalized match keys."""
    names = [project] if isinstance(project, str) else list(project)
    return {_norm(n) for n in names if _norm(n)}


def _prepend(existing, new_block, project, cap=CAP):
    """-> (text, [dropped blocks]); see prepend_block."""
    keys = _keys(project)
    pre, blocks = split_blocks(existing)
    kept = [b for b in (_demote(b) for b in blocks) if _norm(block_project(b)) not in keys]
    allb = [new_block.strip()] + kept
    body = SEP.join(allb[:cap]) + "\n"
    return (f"{pre}\n\n{body}" if pre else body), allb[cap:]


def prepend_block(existing, new_block, project, cap=CAP):
    """Demote, ownership-dedup (all spellings/aliases), prepend, cap. -> (text, [projects of dropped blocks])."""
    text, dropped = _prepend(existing, new_block, project, cap)
    return text, [block_project(b) for b in dropped]


def rescue_line(blk):
    """1-line state of a handoff block: first bullet of 'Aktueller Stand', else of 'Was wurde gemacht'."""
    lines = [ln.strip() for ln in blk.splitlines()]
    for head in ("## Aktueller Stand", "## Was wurde gemacht"):
        if head not in lines:
            continue
        for ln in lines[lines.index(head) + 1:]:
            if ln.startswith("## "):
                break
            if ln.startswith("- ") and ln[2:].strip():
                return ln[2:].strip()[:200]
    return ""


def _heading_matches(line, project):
    """'## dynamic_central_orchestrator (DCO)' matches 'dynamic-central-orchestrator'; 'Y-lang' never matches 'Y'."""
    if not line.startswith("## "):
        return False
    head = line[3:].strip()
    base = re.split(r"\s+[(/|–—-]", head, maxsplit=1)[0]
    keys = _keys(project)
    return _norm(head) in keys or _norm(base) in keys


def replace_board_section(existing, project, section):
    """Replace only this project's '## <project>' section (heading -> next '---' or '## '), else append."""
    lines = (existing if existing is not None else BOARD_STUB).splitlines()
    sec = section.strip("\n").splitlines()
    for i, ln in enumerate(lines):
        if _heading_matches(ln, project):
            end = i + 1
            while end < len(lines) and not lines[end].startswith("## "):
                if lines[end].strip() == "---":
                    end += 1
                    break
                end += 1
            return "\n".join(lines[:i] + sec + lines[end:]).rstrip("\n") + "\n"
    base = "\n".join(lines).rstrip("\n")
    return f"{base}\n\n" + "\n".join(sec) + "\n"


def _guarded_write(path, compute, _before_write=None):
    """compute(old_text_or_None) -> new text. Write with one drift-merge retry."""
    digest, _ = file_state(path)
    old = _read(path) if digest else None
    new = compute(old)
    if _before_write:
        _before_write()
    now_digest, _ = file_state(path)
    if now_digest == digest:
        _write(path, new)
        return "written"
    old = _read(path) if now_digest else None
    new = compute(old)
    if file_state(path)[0] != now_digest:
        return "failed(drift twice)"
    _write(path, new)
    return "drift-merged"


def write_central(central_dir, project, new_block, _before_write=None, dropped_out=None):
    """Prepend the block to <central_dir>/.agent-memory/session-summary.md; never create the directory.
    dropped_out (list): receives the blocks cap 5 really dropped in the write that happened."""
    folder = os.path.join(central_dir, ".agent-memory")
    if not os.path.isdir(folder):
        return "skipped(no central dir)"

    def compute(old):
        text, dropped = _prepend(old or "", new_block, project)
        if dropped_out is not None:
            dropped_out[:] = dropped
        return text

    try:
        return _guarded_write(os.path.join(folder, "session-summary.md"), compute, _before_write)
    except OSError as exc:
        return f"failed({type(exc).__name__})"


def rescue_blocks(central_dir, blocks, _before_write=None):
    """Template 7.6a point 5: move the 1-line state of handoff blocks that cap 5 drops (after ownership-dedup
    each is its project's ONLY block) into the board. Appends a section only for projects the board does not
    know yet - existing sections are never touched (7.6b); nothing to rescue -> board not rewritten.
    Limit: aliases of foreign projects are unknown here, so a section headed only by an alias gets a second,
    visible rescue section - never a loss. -> {"rescued", "lost", "status"}; "lost" names projects whose block
    could not be saved (no state line or failed write) - reported, never silent, never raised."""
    board = os.path.join(central_dir, "cross-project-status.md")
    rescued, lost = [], []

    def compute(old):
        text = old if old is not None else BOARD_STUB
        rescued.clear()
        lost.clear()
        for blk in blocks:
            name, line = block_project(blk), rescue_line(blk)
            if not name or any(_heading_matches(ln, name) for ln in text.splitlines()):
                continue
            if not line:
                lost.append(name or "?")
                continue
            stamp = _block_date(blk) or "?"
            text = replace_board_section(text, name, f"## {name}\n*Gerettet aus dem zentralen Handoff (Cap 5), "
                                                     f"Stand {stamp}*\n- State: {line}\n\n---\n")
            rescued.append(name)
        return text

    try:
        compute(_read(board) if os.path.isfile(board) else None)
        if not rescued:
            return {"rescued": [], "lost": list(lost), "status": "none"}
        status = _guarded_write(board, compute, _before_write)
    except OSError as exc:
        status = f"failed({type(exc).__name__})"
    if status not in ("written", "drift-merged"):
        return {"rescued": [], "lost": list(lost) + list(rescued), "status": status}
    return {"rescued": list(rescued), "lost": list(lost), "status": status}


def rescue_dropped(central_dir, project, new_block, _before_write=None):
    """Pre-write pass: the blocks write_central WOULD drop now (template order: move first, then drop).
    A parallel writer can change that set before the write - the caller passes write_central's dropped_out
    to rescue_blocks afterwards to catch the rest."""
    hand = os.path.join(central_dir, ".agent-memory", "session-summary.md")
    try:
        if not os.path.isfile(hand):
            return {"rescued": [], "lost": [], "status": "none"}
        _, dropped = _prepend(_read(hand), new_block, project)
    except OSError as exc:
        return {"rescued": [], "lost": [], "status": f"failed({type(exc).__name__})"}
    return rescue_blocks(central_dir, dropped, _before_write)


def write_board(central_dir, project, section, _before_write=None):
    """Replace/append this project's section in <central_dir>/cross-project-status.md."""
    if not os.path.isdir(central_dir):
        return "skipped(no central dir)"
    try:
        return _guarded_write(os.path.join(central_dir, "cross-project-status.md"),
                              lambda old: replace_board_section(old, project, section), _before_write)
    except OSError as exc:
        return f"failed({type(exc).__name__})"

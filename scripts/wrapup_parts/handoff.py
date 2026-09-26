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


def prepend_block(existing, new_block, project, cap=CAP):
    """Demote, ownership-dedup (all spellings/aliases), prepend, cap. -> (text, [projects of dropped blocks])."""
    keys = _keys(project)
    pre, blocks = split_blocks(existing)
    kept = [b for b in (_demote(b) for b in blocks) if _norm(block_project(b)) not in keys]
    allb = [new_block.strip()] + kept
    dropped = [block_project(b) for b in allb[cap:]]
    body = SEP.join(allb[:cap]) + "\n"
    return (f"{pre}\n\n{body}" if pre else body), dropped


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


def write_central(central_dir, project, new_block, _before_write=None):
    """Prepend the block to <central_dir>/.agent-memory/session-summary.md; never create the directory."""
    folder = os.path.join(central_dir, ".agent-memory")
    if not os.path.isdir(folder):
        return "skipped(no central dir)"
    try:
        return _guarded_write(os.path.join(folder, "session-summary.md"),
                              lambda old: prepend_block(old or "", new_block, project)[0], _before_write)
    except OSError as exc:
        return f"failed({type(exc).__name__})"


def write_board(central_dir, project, section, _before_write=None):
    """Replace/append this project's section in <central_dir>/cross-project-status.md."""
    if not os.path.isdir(central_dir):
        return "skipped(no central dir)"
    try:
        return _guarded_write(os.path.join(central_dir, "cross-project-status.md"),
                              lambda old: replace_board_section(old, project, section), _before_write)
    except OSError as exc:
        return f"failed({type(exc).__name__})"

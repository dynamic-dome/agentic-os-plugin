#!/usr/bin/env python3
"""identity_quote.py - checks a quoted user sentence against the session transcript (P1 / B2).

WHY THIS EXISTS
---------------
A user-profile candidate used to become `confirmed` on the agent's word alone
(`"confirmed": true` in the wrap-up plan). That is a self-declaration: nothing
proved that the user ever said it, and 264 open candidates in the stores show
how loosely the flag was set. B2 makes `confirmed` conditional on a LITERAL
quote of the user, and this module is the check: the quote must be a substring
of ONE genuine user message of the session's own transcript. The same check
guards `user_md_supersede` (a profile line is replaced only on a verified quote).

WHAT COUNTS AS A GENUINE USER MESSAGE
-------------------------------------
A transcript record of `type: user` that is NOT
- a tool-result carrier (a `tool_result` block, `toolUseResult`, `sourceToolAssistantUUID`),
- meta / injected context (`isMeta`, `isCompactSummary`, `isVisibleInTranscriptOnly`),
- a subagent prompt (`isSidechain`),
- machine-originated (`origin.kind` other than `human`, `turnOrigin` other than `human`,
  `promptSource` system / sdk) - task notifications, plugin context, scheduled turns, headless prompts,
- a prompt SUGGESTION the user merely accepted (`promptSource: suggestion_accepted`) - the words are the
  model's, not the user's,
- a record of ANOTHER session (`sessionId` differs from the session being checked),
and from whose text the injected blocks (`<system-reminder>`, `<local-command-stdout>`,
`<command-name>`, `<task-notification>`, `<pasted_content>`, `<ide_selection>`, `<teammate-message>`, ...)
are removed - nested ones too, until nothing changes - as well as the harness line
`[Request interrupted by user ...]`. Assistant records never count.

Older transcripts have no `origin` / `promptSource`; for them the structural
rules above and the tag stripping carry the decision.

WHAT A QUOTE MUST SATISFY (D5)
------------------------------
Normalised (NFC, case-folded, whitespace collapsed, typographic quotes -> ASCII) it must
- be at least QUOTE_MIN_CHARS characters and QUOTE_MIN_WORDS words long ("ja, gerne" proves nothing),
- start and end on a word boundary (a fragment from the middle of a word proves nothing),
- not stand within NEGATION_REACH words of a negation word in the source message (before or after:
  "keine langen Antworten" must not verify the quote "langen Antworten"; "Auf keinen Fall lange Antworten",
  "Nie wieder ...", "... gut? Nein." and "ohne ..." must not either). The contractions count typed with or
  without the apostrophe ("don't", "dont", "cant"); the acute and the grave accent stand for an apostrophe too.
This proves the WORDING, not the MEANING: a substring cannot tell sarcasm, a question or a
quoted counter-position from a statement, and a negation further away than NEGATION_REACH words is not seen.
The thresholds are heuristics the owner calibrates.

SESSION BINDING (D2, D3)
------------------------
- If CLAUDE_CODE_SESSION_ID is set in the environment and differs from the session id the caller
  names, the evidence of the run is not verifiable: every quote gets `"kein_transkript"`.
- `transcript=` (an override path) is honoured only when it resolves (realpath, case-folded) to
  `<HOME>/.claude/projects/<dir>/<session-id>.jsonl`. Anything else is refused - no test back door, tests
  put their transcripts into their temporary HOME.
- The default lookup (`<HOME>/.claude/projects/*/<session-id>.jsonl`) holds its hits to the same rule: a
  junction or symlink under projects/ that leads out of it is not followed.
- Residual risk, by design: a file the agent itself places at `projects/<dir>/<session-id>.jsonl` (a plain
  write, a hard link) is indistinguishable from a real transcript. The check separates the user's words from
  the agent's, not a forged transcript from a real one.

RESULT
------
`verify_quotes()` returns one verdict per quote: `True` (found), `False` (not found, too short,
too few words, mid-word, negated) or `"kein_transkript"` (no transcript could be used - tests, Codex,
headless runs, a refused override, a session mismatch; not an error). Only `True` lets a candidate
be confirmed at first sight. `info["reasons"]` says why a quote did not verify.

DESIGN NOTES
------------
- Read-only. The transcript is only opened for reading, with `errors="replace"`
  and a BOM-tolerant decode; a malformed line is skipped, never fatal.
- Lazy: the transcript path and the environment are resolved at call time from the CALLER's
  HOME (`os.path.expanduser("~")` follows USERPROFILE on Windows and HOME on POSIX), never at
  import - a test that redirects HOME sees only its own files.
- stdlib only. The scan streams the file; a quote that never verifies costs one pass over the file.
"""

from __future__ import annotations

import glob
import json
import os
import re
import unicodedata

NO_TRANSCRIPT = "kein_transkript"

# A quote shorter than this proves nothing ("ja", "nie" are substrings of almost any
# message). Counted after normalisation and after the surrounding quote marks are removed.
QUOTE_MIN_CHARS = 8
QUOTE_MIN_WORDS = 3

SESSION_ENV = "CLAUDE_CODE_SESSION_ID"

_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")

# Typographic quote marks -> ASCII, so a quote copied from a rendered message still matches. The acute accent
# (U+00B4), the grave accent (`) and the modifier apostrophe (U+02BC) are what people type for an apostrophe
# ("don´t", "don`t"): they must not hide an "n't" from the negation check (R3 round 3).
_QUOTE_MAP = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "‹": "'", "›": "'", "´": "'", "`": "'", "ʼ": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"', "«": '"', "»": '"',
})

# Tag blocks the harness, the IDE or a hook injects into a user-role record. Their content is not what
# the user typed, so it is cut out before matching.
_INJECTED_TAGS = (
    "system-reminder", "local-command-stdout", "local-command-stderr", "local-command-caveat",
    "command-name", "command-message", "command-args", "task-notification", "tools-used-before-summary",
    "user-prompt-submit-hook", "bash-input", "bash-stdout", "bash-stderr",
    "pasted_content", "ide_selection", "ide_opened_file", "teammate-message",
)
_TAG_ALT = "|".join(re.escape(t) for t in _INJECTED_TAGS)
# The INNERMOST closed block: its body holds no further opening of the same tag. Applied until nothing changes,
# nested blocks fall from the inside out (a lazy match from the outer opening would stop at the INNER closing
# tag and leave the tail of the outer block standing).
_CLOSED_BLOCK = re.compile(r"<(" + _TAG_ALT + r")\b[^>]*>(?:(?!<\1\b).)*?</\1\s*>", re.DOTALL | re.IGNORECASE)
_OPEN_BLOCK = re.compile(r"<(?:" + _TAG_ALT + r")\b[^>]*>.*\Z", re.DOTALL | re.IGNORECASE)  # never closed: cut to the end
_INTERRUPT = re.compile(r"\[Request interrupted by user[^\]]*\]", re.IGNORECASE)

# promptSource values that are not the user's own words.
_NOT_USER_PROMPT_SOURCES = ("system", "suggestion_accepted", "sdk")

# A negation just before or after the quote (within NEGATION_REACH words) turns a quoted fragment into its opposite.
_NEGATIONS = frozenset({
    "nicht", "nie", "niemals", "kein", "keine", "keinen", "keinem", "keiner", "keines", "keinesfalls", "keinerlei", "keineswegs",
    "nein", "nö", "noe", "nee", "ohne", "weder", "nichts", "niemand", "nirgends",
    "not", "never", "no", "without", "cannot", "nothing", "neither", "nor", "nobody",
    # the contractions as they are typed without an apostrophe (dont, cant, doesnt, ...)
    "dont", "cant", "wont", "isnt", "arent", "wasnt", "werent", "doesnt", "didnt", "havent", "hasnt", "hadnt",
    "wouldnt", "couldnt", "shouldnt", "mustnt", "aint",
})
_NEGATION_SUFFIX = "n't"  # don't, won't, can't, shouldn't, ... (typographic apostrophes, acute and grave are normalised before)
_WORD_TOKEN = re.compile(r"\w+(?:'\w+)*")
NEGATION_REACH = 3  # words looked at on each side of the quote
_NEIGHBOUR_WINDOW = 160  # characters cut out on each side before the words are counted


def normalize(text) -> str:
    """Comparison form: NFC, typographic quotes -> ASCII, whitespace collapsed, case-folded."""
    t = unicodedata.normalize("NFC", str(text or "")).translate(_QUOTE_MAP)
    return re.sub(r"\s+", " ", t).strip().casefold()


def normalize_quote(quote) -> str:
    """normalize() plus: quote marks that wrap the whole quote are not part of what was said."""
    return normalize(quote).strip(" \"'")


def projects_root() -> str:
    """`<HOME>/.claude/projects`, resolved now (HOME follows the caller's environment)."""
    return os.path.join(os.path.expanduser("~"), ".claude", "projects")


def _real(path) -> str:
    return os.path.normcase(os.path.realpath(path))


def override_allowed(path, session_id) -> bool:
    """True only for `<HOME>/.claude/projects/<dir>/<session-id>.jsonl` (realpath, case-folded). A file the
    agent wrote itself, a transcript of another session or a path that climbs out with `..` is refused."""
    sid = str(session_id or "")
    if not path or not _SESSION_ID.fullmatch(sid):
        return False
    real, root = _real(path), _real(projects_root())
    # the shape of the default lookup: exactly <root>/<dir>/<sid>.jsonl (the root of the real path, one level up twice)
    return (os.path.basename(real) == os.path.normcase(sid + ".jsonl")
            and os.path.dirname(os.path.dirname(real)) == root
            and os.path.basename(os.path.dirname(real)) != "")


def session_binding(session_id) -> dict:
    """{given, env, mismatch}: `mismatch` when CLAUDE_CODE_SESSION_ID is set and names another session than
    the one the caller claims. Read from the environment at call time. No env value = nothing to compare."""
    given = str(session_id or "").strip()
    env = os.environ.get(SESSION_ENV, "").strip()
    return {"given": given, "env": env, "mismatch": bool(env and given and env != given)}


def mismatch_warning(binding) -> str:
    """The one warning text for a session mismatch, shared by the quote check and the repeat counter."""
    return (f"{SESSION_ENV} ({binding['env']}) differs from the session id given ({binding['given']}) - the identity "
            f"evidence of this run is not verifiable (quotes are not checked, repeats are not counted)")


def resolve_transcript(session_id, override=None):
    """Path of the session's transcript or None. Same lookup as `measure_session_cost.py --locate`:
    `<HOME>/.claude/projects/*/<session-id>.jsonl`, newest mtime on a collision. `override` (a path) wins
    when it passes override_allowed() and is an existing file. Resolved on every call - HOME is read now."""
    sid = str(session_id or "")
    if not _SESSION_ID.fullmatch(sid):  # no glob syntax, no separators, no traversal
        return None
    if override:
        return override if override_allowed(override, sid) and os.path.isfile(override) else None
    matches = glob.glob(os.path.join(glob.escape(projects_root()), "*", sid + ".jsonl"))
    matches = [m for m in matches if override_allowed(m, sid)]  # a link that leads out of projects/ is no hit
    return max(matches, key=os.path.getmtime) if matches else None


def _strip_injected(text: str) -> str:
    """Cut every injected block out of a user message: innermost closed blocks first, until nothing changes,
    then a block that is never closed (to the end of the text), then harness interrupt lines."""
    prev = None
    while prev != text:
        prev = text
        text = _CLOSED_BLOCK.sub("", text)
    return _INTERRUPT.sub("", _OPEN_BLOCK.sub("", text))


def _genuine_texts(rec, session_id=None):
    """Texts of one record when it is a genuine user message, else []."""
    if not isinstance(rec, dict) or rec.get("type") != "user":
        return []
    sess = rec.get("sessionId")
    if session_id and isinstance(sess, str) and sess and sess != session_id:
        return []  # a record of another session (spliced, copied or resumed file)
    if rec.get("isMeta") or rec.get("isSidechain") or rec.get("isCompactSummary") or rec.get("isVisibleInTranscriptOnly"):
        return []
    if rec.get("toolUseResult") is not None or rec.get("sourceToolAssistantUUID"):
        return []
    origin = rec.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return []
    if rec.get("promptSource") in _NOT_USER_PROMPT_SOURCES or rec.get("turnOrigin") not in (None, "human"):
        return []
    msg = rec.get("message")
    if not isinstance(msg, dict) or msg.get("role") not in (None, "user"):
        return []
    content = msg.get("content")
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return []  # a tool-result carrier, whatever else it holds
    return [b["text"] for b in content
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]


def real_user_texts(path, session_id=None):
    """Yield the normalised text of every genuine user message in the transcript, in order.
    Streams the file; a malformed or empty line is skipped. With `session_id`, records that name
    another session are skipped."""
    sid = str(session_id or "").strip() or None
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            if '"user"' not in line:  # cheap pre-filter: only user-role records can qualify
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            texts = _genuine_texts(rec, sid)
            if texts:
                # the text parts of ONE message are read as one text: a block whose opening and closing tag stand in
                # different parts is cut out too (Codex-Verifier round 2, P1-B2-R2-01)
                clean = normalize(_strip_injected("\n".join(texts)))
                if clean:
                    yield clean


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def is_negation(word: str) -> bool:
    return word in _NEGATIONS or word.endswith(_NEGATION_SUFFIX)


def has_negation(text) -> bool:
    """A negation word stands anywhere in `text` (normalised like a quote). apply_wrapup.py compares this for the
    quote and the statement it is meant to support: one side negated, the other not, is reported (R3 round 3)."""
    return any(is_negation(w) for w in _WORD_TOKEN.findall(normalize(text)))


def _negated(text: str, start: int, end: int) -> bool:
    """A negation word stands within NEGATION_REACH words before or after text[start:end]. The cut-out grows until it
    holds more than NEGATION_REACH words or reaches the end of the text: a cut word is then never one of the words
    counted, and a very long word does not push a negation out of reach (Codex-Verifier 2026-10-28, P1-B2-02)."""
    width = _NEIGHBOUR_WINDOW
    while True:
        lo, hi = max(0, start - width), min(len(text), end + width)
        before, after = _WORD_TOKEN.findall(text[lo:start]), _WORD_TOKEN.findall(text[end:hi])
        if (lo == 0 or len(before) > NEGATION_REACH) and (hi == len(text) or len(after) > NEGATION_REACH):
            break
        width *= 2
    return any(is_negation(w) for w in before[-NEGATION_REACH:] + after[:NEGATION_REACH])


# Ranking of why a quote did not verify: the most informative reason of all messages wins.
_REASON_RANK = {"negation": 2, "word_boundary": 1}


def locate(nq: str, text: str):
    """Look up the normalised quote `nq` in the normalised message `text`.
    Returns True (found at word boundaries and not next to a negation), "negation" (found, but next to a
    negation word), "word_boundary" (found only inside a word) or None (not in the text)."""
    best = None
    pos = 0
    while True:
        i = text.find(nq, pos)
        if i < 0:
            return best
        j = i + len(nq)
        left_ok = not (_is_word_char(nq[0]) and i > 0 and _is_word_char(text[i - 1]))
        right_ok = not (_is_word_char(nq[-1]) and j < len(text) and _is_word_char(text[j]))
        if left_ok and right_ok:
            if not _negated(text, i, j):
                return True
            found = "negation"
        else:
            found = "word_boundary"
        if best is None or _REASON_RANK[found] > _REASON_RANK[best]:
            best = found
        pos = i + 1


def verify_quotes(quotes, session_id="", transcript=None):
    """Check each quote against the session transcript.

    Returns (results, info): `results` maps every quote (as given) to True / False / "kein_transkript";
    `info` = {"transcript": path or None, "warnings": [...], "reasons": {quote: why it did not verify},
    "override_rejected": bool, "session_mismatch": bool}. A missing transcript is not an error."""
    info = {"transcript": None, "warnings": [], "reasons": {}, "override_rejected": False, "session_mismatch": False}
    wanted = {}
    for q in quotes:
        wanted.setdefault(q, normalize_quote(q))
    results = {}
    searchable = {}
    for q, nq in wanted.items():
        if len(nq) < QUOTE_MIN_CHARS:
            results[q], info["reasons"][q] = False, "too_short"  # empty or too short to prove anything
        elif len(_WORD_TOKEN.findall(nq)) < QUOTE_MIN_WORDS:  # counted like the negation reach: "don't" is one word
            results[q], info["reasons"][q] = False, "too_few_words"
        else:
            searchable[q] = nq
    if not searchable:
        return results, info

    sid = str(session_id or "").strip()

    def unusable(reason, warning=None):
        if warning:
            info["warnings"].append(warning)
        for q in searchable:
            results[q], info["reasons"][q] = NO_TRANSCRIPT, reason
        return results, info

    binding = session_binding(sid)
    if binding["mismatch"]:
        info["session_mismatch"] = True
        return unusable("session_mismatch", mismatch_warning(binding))
    if transcript and not override_allowed(transcript, sid):
        info["override_rejected"] = True
        return unusable("override_rejected",
                        f"transcript override {transcript} refused - only <HOME>/.claude/projects/<dir>/<session-id>.jsonl "
                        f"counts - quotes cannot be verified")
    path = resolve_transcript(sid, transcript)
    if path is None:
        return unusable("no_transcript",
                        f"transcript {transcript} not found - quotes cannot be verified" if transcript else None)
    info["transcript"] = path

    open_quotes = dict(searchable)
    ranks = {}
    try:
        for text in real_user_texts(path, sid):
            for q, nq in list(open_quotes.items()):
                found = locate(nq, text)
                if found is True:
                    results[q] = True
                    del open_quotes[q]
                elif found is not None and _REASON_RANK[found] > _REASON_RANK.get(ranks.get(q), 0):
                    ranks[q] = found
            if not open_quotes:
                break
    except OSError as exc:
        info["transcript"] = None
        for q in open_quotes:
            results[q], info["reasons"][q] = NO_TRANSCRIPT, "unreadable"
        info["warnings"].append(f"transcript {path} unreadable ({exc}) - quotes cannot be verified")
        return results, info
    for q in open_quotes:
        results[q], info["reasons"][q] = False, ranks.get(q, "not_found")
    return results, info

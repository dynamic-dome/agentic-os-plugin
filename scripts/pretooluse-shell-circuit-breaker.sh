#!/usr/bin/env bash
# PreToolUse circuit breaker for shell commands.
# Exit 2 blocks the tool call in Claude Code.

set -u

payload="$(cat)"

python_bin=""
if command -v python3 >/dev/null 2>&1; then
  python_bin="python3"
elif command -v python >/dev/null 2>&1; then
  python_bin="python"
else
  echo "Agentic OS PreToolUse circuit breaker: python runtime not found; allowing command." >&2
  exit 0
fi

printf '%s' "$payload" | "$python_bin" -c '
import json
import re
import sys


def extract_command(payload):
    try:
        data = json.loads(payload or "{}")
    except json.JSONDecodeError:
        return None, None

    tool_name = data.get("tool_name") or data.get("tool") or data.get("name")
    tool_input = data.get("tool_input") or data.get("input") or data
    if isinstance(tool_input, dict):
        command = tool_input.get("command") or tool_input.get("cmd")
    else:
        command = None
    return tool_name, command


def normalize(command):
    command = command.replace("\\\n", " ")
    command = command.replace("\r", "\n")
    command = re.sub(r"\s+", " ", command)
    return command.strip()


_READONLY_WRAPPER = re.compile(
    r"^\s*(grep|rg|ag|ack|cat|less|more|head|tail|echo|printf|view|bat|fgrep|egrep|ls|git\s+grep|git\s+commit|git\s+log|git\s+show)\b",
    re.IGNORECASE,
)
_CMD_SEPARATOR = re.compile(r"(?:\|\||&&|[;|\r\n])")
_QUOTE_CHARS = {"\"", chr(39)}
_QUOTED_SPAN = re.compile("\"[^\"]*\"|" + chr(39) + "[^" + chr(39) + "]*" + chr(39))
_EXECUTOR_FLAG_PREFIX = re.compile(
    r"(?:powershell|pwsh|cmd|bash|sh|zsh|python\d?|node)(?:\.exe)?\s+(?:\S+\s+)*(?:-command|-c|/c|/k|-e|-lc)\s*$",
    re.IGNORECASE,
)
# T-32: default/alternate parameter-expansion operators. ${var:-CMD}, ${var-CMD},
# ${var:=CMD}, ${var=CMD}, ${var:+CMD}, ${var+CMD} run CMD as a command when the
# expansion sits in command position; the punctuation (:- / - / := / =) otherwise
# reads as a non-boundary for the anchored rules AND is swallowed by the disk-
# format lookbehind class [\w.=-]. The parameter part covers normal + positional
# names, the optional !-indirection (${!ref:-CMD}), and the special params @ / *
# (${@:-CMD}, ${*:-CMD}) — in the argument-less shell of the Bash tool, $@/$* are
# empty, so their default DOES execute (Codex T-32 review). Always-set specials
# (${#:-} / ${?:-} / ${$:-}) never run their default and are left as ALLOW; the
# ? error-message form and substring/pattern forms (${v:1:2}, ${v#x}, ${v/x/y})
# do not match either. Residual known-open (exotic): array-subscript defaults
# ${arr[@]:-CMD}.
_PARAM_EXPANSION_OP = re.compile(r"\$\{!?[A-Za-z0-9_@*]*:?[-=+]")


def has_balanced_quotes(command):
    active = None
    escaped = False
    for char in command:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if active:
            if char == active:
                active = None
        elif char in _QUOTE_CHARS:
            active = char
    return active is None


def has_executable_substitution(command, single_quotes_inert=True):
    """Return True for shell substitution syntax that executes code.

    Single-quoted text is inert in Bash, so `$(` there is data. Double-quoted
    text still expands command/process substitutions and backticks, so those
    constructs remain code and must be visible to the destructive rules.
    """
    active = None
    escaped = False
    i = 0
    while i < len(command):
        char = command[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if char == "\\":
            escaped = True
            i += 1
            continue
        if active == chr(39):
            if char == active:
                active = None
            i += 1
            continue
        if active == "\"":
            if char == active:
                active = None
                i += 1
                continue
            if char == "`" or command.startswith("$(", i) or command.startswith("<(", i) or command.startswith(">(", i):
                return True
            i += 1
            continue
        if char == chr(39) and single_quotes_inert:
            active = char
            i += 1
            continue
        if char == "\"":
            active = char
            i += 1
            continue
        if char == "`" or command.startswith("$(", i) or command.startswith("<(", i) or command.startswith(">(", i):
            return True
        i += 1
    return False


def quoted_span_contains_code(quoted):
    if not quoted.startswith("\""):
        return False
    return has_executable_substitution(quoted[1:-1], single_quotes_inert=False)


def is_readonly_command(command):
    if not has_balanced_quotes(command):
        return False
    if has_executable_substitution(command):
        return False
    segments = [segment.strip() for segment in _CMD_SEPARATOR.split(command) if segment.strip()]
    if not segments:
        return False
    return all(_READONLY_WRAPPER.match(segment) for segment in segments)


def mask_quoted_content(command):
    if not has_balanced_quotes(command):
        return command

    def mask(match):
        prefix = command[:match.start()]
        if _EXECUTOR_FLAG_PREFIX.search(prefix):
            return match.group(0)
        quoted = match.group(0)
        if quoted_span_contains_code(quoted):
            return quoted
        return quoted[0] + ("\0" * (len(quoted) - 2)) + quoted[-1]

    return _QUOTED_SPAN.sub(mask, command)


def expose_param_expansion(command):
    """T-32: expose a command smuggled as a parameter-expansion default/alternate
    value so every destructive rule and the disk-format lookbehind see it.

    Bash executes the default/alternate word of ${var:-CMD}, ${var-CMD},
    ${var:=CMD}, ${var=CMD}, ${var:+CMD}, ${var+CMD} as a command when the
    expansion sits in command position; before this pass ${x:-mkfs ...} (and the
    same for rm/git-reset/shutdown/dd) slipped past every anchored rule. Rewriting
    the operator prefix to a command separator feeds the existing boundary anchors
    and defuses the lookbehind at once, including nested and positional forms.
    Covers normal/positional names, !-indirection and the empty-able specials
    @/* (all executable in the argument-less shell of the tool). Runs on the already
    quote-masked string, so quoted defaults stay masked (T-19 known-open). The
    error-message form (:? / ?) is not command position and is left intact;
    always-set specials (${#:-} etc.) never run their default (ALLOW)."""
    return _PARAM_EXPANSION_OP.sub("; ", command)


RULES = [
    (
        "recursive forced deletion",
        re.compile(r"(?i)(^|[;&|]\s*)(sudo\s+)?rm\s+-[A-Za-z-]*r[A-Za-z-]*f[A-Za-z-]*\b"),
    ),
    (
        "PowerShell recursive forced deletion",
        re.compile(r"(?i)\b(remove-item|rm|del|erase|rd|rmdir)\b[^;&|]*-(recurse|r)\b[^;&|]*-(force|fo)\b"),
    ),
    (
        "hard git reset",
        re.compile(r"(?i)(^|[;&|]\s*)(?:sudo\s+)?git\s+(?:-C\s+\S+\s+)?reset\s+--hard\b"),
    ),
    (
        "forced git clean",
        re.compile(r"(?i)(^|[;&|]\s*)(?:sudo\s+)?git\s+(?:-C\s+\S+\s+)?clean\b(?=[^;&|]*-[^\s;&|]*f)(?=[^;&|]*-[^\s;&|]*d)"),
    ),
    (
        "world-writable recursive chmod",
        re.compile(r"(?i)(^|[;&|]\s*)chmod\s+-R\s+777\b"),
    ),
    (
        "recursive ownership change",
        re.compile(r"(?i)(^|[;&|]\s*)(sudo\s+)?chown\s+-R\b"),
    ),
    (
        "remote script pipe execution",
        re.compile(r"(?i)\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(sh|bash)\b"),
    ),
    (
        "PowerShell download cradle execution",
        re.compile(r"(?i)\b(iwr|irm|invoke-webrequest|invoke-restmethod)\b[^|;&]*\|\s*(iex|invoke-expression)\b"),
    ),
    (
        "disk formatting or partitioning",
        # T-18: un-verankert (analog Delete-Regel), damit destruktive Befehle
        # auch INNERHALB von powershell -Command "..." erfasst werden. Drei
        # FP-Bremsen ersetzen den alten Zeilenanfangs-Anker:
        # (a) Lookbehind (?<![\w.=-]) — kein Treffer in Flag-/Wort-Umgebung
        #     (--format, =format, date-format, x.format);
        # (b) nacktes `format` blockt nur mit Datentraeger-Syntax dahinter
        #     (Switch `/q`/`/fs:`, Laufwerk `c:`, Volume-GUID/UNC `\\...`
        #     oder Volume-Label `format DATA /q`), nicht als blosses Wort
        #     (Commit-Messages, Pfadsegmente wie src/format/);
        #     Lookbehind darf `/`+`\` NICHT ausschliessen, sonst Bypass via
        #     Vollpfad (`C:\...\format.com`, `/usr/sbin/mkfs.ext4`);
        # (c) von den Format-*-Cmdlets blockt nur das destruktive
        #     Format-Volume — Table/List/Wide/Custom/Hex bleiben strukturell
        #     frei (T-17-Garantie).
        # Bekannt offen (T-19): gequotete String-Literale werden nicht
        # gestrippt; `grep "diskpart|x"` bleibt ein bekannter False Positive.
        re.compile(r"(?i)(?<![\w.=-])(?:mkfs(?:\.[A-Za-z0-9_+-]+)?\b|diskpart\b|format-volume\b|format(?:\.com|\.exe)?(?=\s+(?:/[a-z?]|[a-z]:|\\\\|\S+\s+/[a-z?])))"),
    ),
    (
        "raw disk write",
        re.compile(r"(?i)(^|[;&|]\s*)dd\b(?=[^;&|]*\bof=/dev/)"),
    ),
    (
        "system shutdown or reboot",
        re.compile(r"(?i)(^|[;&|]\s*)(shutdown|reboot|halt|poweroff)\b"),
    ),
]


payload = sys.stdin.read()
tool_name, command = extract_command(payload)
if tool_name and str(tool_name).lower() not in {"bash", "shell", "shell_command"}:
    sys.exit(0)

if not isinstance(command, str) or not command.strip():
    sys.exit(0)

normalized = normalize(command)
if is_readonly_command(normalized):
    sys.exit(0)

checked_command = expose_param_expansion(mask_quoted_content(normalized))
for label, pattern in RULES:
    if pattern.search(checked_command):
        print(
            f"Agentic OS PreToolUse circuit breaker blocked shell command: {label}",
            file=sys.stderr,
        )
        sys.exit(2)

sys.exit(0)
'

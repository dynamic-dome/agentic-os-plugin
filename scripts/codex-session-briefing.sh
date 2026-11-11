#!/bin/bash
# Agentic OS — T-24: Bootstrap-Briefing fuer Codex-Sessions.
# Aufruf NUR via session-start.sh-Routing (Skriptpfad liegt unter /.codex/).
# Kontrakt (Nerv-Schutz, membrain/memcodexlifecycle.md §3.4): exit IMMER 0,
# stderr IMMER leer, KEIN Auto-Init, fehlender Store -> stiller Minimal-Output.
# Headless-Guard: S0-Befund c — es gibt kein zuverlaessiges Interaktiv-Signal;
# Schutz kommt aus Fail-fast, die Env-Variable bleibt als Escape-Hatch.
set +e
exec 2>/dev/null

emit_minimal() { echo '{"continue": true}'; exit 0; }

[ -n "${AGENTIC_OS_CODEX_HEADLESS:-}" ] && emit_minimal

# Same order as session-start.sh: on Windows python first, because python3 is often only a launcher of the
# Python manager, which downloads and registers a runtime when LOCALAPPDATA is redirected. Elsewhere python3
# stays first, python can be missing or Python 2.
case "$OSTYPE" in
  msys*|cygwin*) PY_ORDER="python python3" ;;
  *) PY_ORDER="python3 python" ;;
esac
PYBIN=""
for PY_CANDIDATE in $PY_ORDER; do
  command -v "$PY_CANDIDATE" >/dev/null 2>&1 && { PYBIN="$PY_CANDIDATE"; break; }
done
[ -n "$PYBIN" ] || emit_minimal

# Projektpfad-Aufloesung. Normalfall (interaktive TUI, `codex exec`, Desktop im Projekt):
# der Hook laeuft mit $PWD = Projekt -> KEIN stdin-Lesen noetig (schnell, kein Hang-Risiko,
# wichtig gegen den 5 s SessionStart-Timeout unter Startup-Last).
CENTRAL_HANDOFF="$HOME/AI/.agent-memory/session-summary.md"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
PROJECT_DIR=$(printf '%s' "$PROJECT_DIR" | tr '\\' '/')
MEMORY_DIR="$PROJECT_DIR/.agent-memory"

# Nur wenn dort kein Store liegt: Fallback auf payload.cwd (S0-a: der SessionStart-Payload
# traegt `cwd`). stdin NICHT-blockierend lesen — `cat` wartet auf EOF und haengt, wenn der
# Hook-Runner stdin offenhaelt (interaktives Codex) -> SessionStart-Timeout (4.9.3-Bug).
# Der Payload ist eine JSON-Zeile; read -t holt sie und blockiert nie laenger als 1 s.
if [ ! -d "$MEMORY_DIR" ]; then
  PAYLOAD=""
  IFS= read -r -t 1 PAYLOAD 2>/dev/null
  PAYLOAD_CWD=$(printf '%s' "$PAYLOAD" | "$PYBIN" -c "import sys,json
try: print(json.loads(sys.stdin.buffer.read().decode('utf-8','replace') or '{}').get('cwd') or '')
except Exception: print('')" 2>/dev/null)
  if [ -n "$PAYLOAD_CWD" ]; then
    PROJECT_DIR=$(printf '%s' "$PAYLOAD_CWD" | tr '\\' '/')
    MEMORY_DIR="$PROJECT_DIR/.agent-memory"
  fi
fi

# Kein Store -> kein Briefing, NIE Auto-Init (Spec §3.2)
[ -d "$MEMORY_DIR" ] || emit_minimal

ctx=""

# 1) Zentraler Handoff: Top-Block, max 3 Inhaltszeilen
if [ -f "$CENTRAL_HANDOFF" ]; then
  HANDOFF=$(head -14 "$CENTRAL_HANDOFF" | grep -Ev '^(#|\*|$|---)' | head -3 | tr '\n' ' ')
  [ -n "$HANDOFF" ] && ctx="Letzter Handoff (cross-project): $HANDOFF"
fi

# 2) Open Tasks (IDs + Titel), fail-soft via python
OPEN_TASKS=$("$PYBIN" - "$MEMORY_DIR/context/open-tasks.json" << 'EOPY'
import json, sys
try:
    raw = json.load(open(sys.argv[1], encoding="utf-8-sig"))  # BOM-tolerant (#9695)
    tasks = raw if isinstance(raw, list) else raw.get("tasks", [])
    items = [t for t in tasks if isinstance(t, dict) and t.get("status") in ("open", "blocked")]
    # the pipe is cp1252 on Windows unless set: umlauts became U+FFFD, a non-cp1252 character dropped the list
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("; ".join(f"{t.get('id','?')}: {str(t.get('title',''))[:120]}" for t in items[:5]))
except Exception:
    pass
EOPY
)
[ -n "$OPEN_TASKS" ] && ctx="$ctx\nOffene Tasks: $OPEN_TASKS"

# Memory search hint, same rule as session-start.sh: rg and Grep skip a git-ignored store, only an explicit
# path finds it (eval 2026-11-07: a fact that stood only in the store was found in 5 of 10 runs). Said only when true.
if git -C "$PROJECT_DIR" check-ignore -q .agent-memory 2>/dev/null; then
  ctx="$ctx\nMemory search: .agent-memory/ is git-ignored, so rg and Grep find it only with an explicit path. Before you debug an error or change a design, search it for earlier attempts and decisions: context/decisions.json, learnings/learnings.json, iterations/iteration-log.md."
fi

# 3) Statischer Atlas-Hinweis (Pull-Pfad aus T-14, membrain/membridge.md §3.5)
ATLAS_HINT="Vor Spezialaufgaben Atlas fragen: curl -s -X POST http://127.0.0.1:7801/search -H \"Content-Type: application/json\" -d '{\"query\": \"<thema>\", \"top_k\": 5}'"
ctx="$ctx\n$ATLAS_HINT"

ctx="[AGENTIC OS — CODEX BRIEFING]$ctx"

# Codex konsumiert bei SessionStart NUR hookSpecificOutput.additionalContext
# (oder rohen stdout) — systemMessage wird ignoriert (S0-Nachbefund, Rollout-
# Verifikation 2026-07-16). additionalContext versteht auch Claude Code.
# Windows-Falle (globale CLAUDE.md): sys.stdin.read() dekodiert cp1252 -> Mojibake
# bei UTF-8-Quellen. Bytes explizit als utf-8 lesen. print() bleibt ASCII-safe,
# weil json.dumps per Default ensure_ascii=True (\uXXXX) ausgibt.
escaped=$(printf '%b' "$ctx" | "$PYBIN" -c "import sys,json; print(json.dumps(sys.stdin.buffer.read().decode('utf-8','replace')))") || emit_minimal
[ -n "$escaped" ] || emit_minimal
cat << EOJSON
{
  "continue": true,
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": $escaped
  }
}
EOJSON
exit 0

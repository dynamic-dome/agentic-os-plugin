# Scripts

## session-start.sh (v3 — aktiv)

Shell-Skript fuer den `SessionStart`-Command-Hook.

**Features:**
- Auto-Init: Erstellt `.agent-memory/` mit allen 14 Dateien falls nicht vorhanden
- Stack-Erkennung: Sprache, Framework, Package Manager aus Projektdateien
- Kontext-Injection: Git-Branch, Session-Summary, Identity, Quality-Warnings, Statistiken
- Env-Vars: Setzt `AGENTIC_OS_ACTIVE` und `AGENTIC_OS_MEMORY_DIR`

## pretooluse-shell-circuit-breaker.sh (aktiv)

Command-Hook fuer `PreToolUse` mit Matcher `Bash`. Das Skript liest das
Claude-Code-Hook-Payload von stdin, extrahiert `tool_input.command` und blockiert
bekannte Hochrisiko-Shell-Muster deterministisch mit Exit-Code `2`.

**Blockierte Muster:**
- rekursives Forced-Delete (`rm -rf`, `Remove-Item -Recurse -Force`)
- destruktive Git-Operationen (`git reset --hard`, `git clean -fd*`)
- Remote-Script-Pipes (`curl|bash`, `wget|sh`, PowerShell `iwr|iex`)
- Disk-/Systemoperationen (`mkfs`, `diskpart`, raw `dd of=/dev/*`, shutdown/reboot)
- rekursive Rechte-/Owner-Aenderungen (`chmod -R 777`, `chown -R`)

## session-end.sh / pre-compact.sh (entfernt)

Erst durch Prompt-Hooks ersetzt, diese in 4.21.0 gestrichen: SessionEnd-Hooks koennen
keine Skills aufrufen, PreCompact-Output wird wegkomprimiert. Das SessionStart-Briefing
(`session-start.sh`, `hookSpecificOutput.additionalContext`) ist der einzige Hook-Kanal,
den das Modell sieht; es feuert auch nach `/compact` erneut.

## store_lock.py / store_snapshot.py / restore_plan.py (5.3.0)

- `store_lock.py`: `working/store.lock` je Store. Jeder schreibende Lauf haelt ihn vom
  ersten Lesen bis zum letzten Schreiben, nie ueber einen Subprozess. Timeout = Exit 2.
- `store_snapshot.py <mem> [--keep 10]`: Bytekopie von `*.json`/`*.md` ohne identity/,
  working/, metrics/ nach `$AGENTIC_OS_SNAPSHOT_DIR` (Default `~/.agentic-os/snapshots`).
  Laeuft automatisch vor jeder Rueckholung.
- `restore_plan.py <mem> [--skip-ids ..] [--out plan.json]`: rein lesend; Plan + Bericht
  fuer die Rueckholung archivierter Learnings und Patterns (Ablauf: maintain Step 5b).
- `learnings_lifecycle.py propose|report <mem>`: rein lesend; Verdichtungs-Vorschlaege (TF-IDF gegen den
  Regelkatalog aus `~/.claude/agentic-os.local.json` `rule_catalog`) und Verankerungsquote (maintain Step 5c).

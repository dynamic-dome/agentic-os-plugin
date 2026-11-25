# Architektur — Agentic OS

## Ueberblick

Das Plugin installiert Skills, Hooks, Agents und Commands, die Projektwissen in einem `.agent-memory/`-Store persistieren. Der Store hat ein striktes Schema (eine Single Source of Truth) und einen einzigen Schreiber: jede Store-Aenderung laeuft ueber `scripts/apply_wrapup.py` (ein Schreibplan rein, gemessene Zaehlung raus). Seit 6.0.0 dient jeder Baustein einem von zwei Zielen, Learnings global erreichbar halten und den Wiedereinstieg im Projekt tragen, oder er schuetzt den Store.

## Komponentendiagramm

```
SessionStart-Hook (session-start.sh)
  └─ sourct scripts/mem-schema.sh → create_memory_structure()  [Init + Backfill]
       └─ .agent-memory/  (identity, context, iterations, learnings, working)
  └─ Briefing: Stand, offene Tasks, Handoff, RECOVERY, THRESHOLD- und PFLEGELAUF-Zeilen

session-bootstrap (read-only, auf Abruf)  →  WORK PHASE  →  wrap-up (Judge ueber wrapup_core.py)
        ▲ liest Store + zentralen Handoff        │ apply_wrapup.py schreibt Iterationen, Learnings,
                                                  │ Entscheidungen, Tasks; danach Summary, zentraler
                                                  │ Handoff (prepend), Status-Board, Projektionen
   /agentic-os:log → iterations/ (ueber apply_wrapup.py)   context-keeper → context/
   /agentic-os:maintain (Pflegelauf) → learning_updates / principles (ueber apply_wrapup.py)
```

## Kernkomponenten

### Schema Single Source of Truth
- **Datei(en):** `scripts/mem-schema.sh`
- **Aufgabe:** Definiert die komplette `.agent-memory/`-Struktur + Defaults in `create_memory_structure()`. Idempotent. Seit 6.0.0 ohne `patterns/` und `generated-skills/`; vorhandene Stores behalten sie.
- **Abhaengigkeiten:** Konsumiert von `scripts/session-start.sh` (Hook) und `commands/init.md` (`/init`).

### SessionStart-Hook
- **Datei(en):** `scripts/session-start.sh`, `hooks/hooks.json`
- **Aufgabe:** Phase 0 sourct die SSoT; Phase 1 = Auto-Init bei fehlendem Store (nur in Git-Repos oder mit `auto_init_non_git`; Erfolg erst nach Schreibprüfung gegen die SSoT, sonst WARNING); Phase 2 = Backfill (heilt partielle Stores) + Kontext-Injection. Die Schwellen- und Pflegelauf-Zeilen werden im Briefing gemeldet.
- **Abhaengigkeiten:** `mem-schema.sh`, `memory-thresholds.sh`, `reentry_check.py`. Schreibt `project-context.md` selbst (Inline-Stack-Detection — bewusst ausserhalb der SSoT).

### Skills (4, geschichtet) + Commands (4)
- **Datei(en):** `skills/*/SKILL.md` (session-bootstrap, wrap-up, context-keeper, obsidian-sync); `commands/{init,status,maintain,log}.md` (`maintain` und `log` mit `disable-model-invocation: true` — nur per Slash)
- **Aufgabe:** Session-Lifecycle + Memory-Management. Genau ein Schreiber pro Store-Datei.
- **Abhaengigkeiten:** Strikt azyklisch (`skills/DEPENDENCIES.md`). v6.0.0: pattern-extractor mit `extract_patterns.py`, Fehlerprotokoll (`errors.json`), Identity-Ernte, Archiv-Restore, `/agentic-os:memory-audit`, `cost-trace.sh` und Eskalationsmarker entfernt; Code ist weg, Store-Dateien bleiben. v5.11.0: sync-context und die stillgelegte globale Schicht nach `_archived/`. v5.0.0: memory-maintenance/iteration-logger zu Commands, self-improve + rollback + auto-commit archiviert. Entfernt in v4.0.0: retrospective, research-pipeline, wiki-query, quality-gate (Skill + Agent), skill-generator.

### Pflegelauf (`/agentic-os:maintain`, 6.0.0)
- **Datei(en):** `commands/maintain.md`; Kandidaten aus `scripts/review_sweep.py`, `scripts/learnings_lifecycle.py` und der Atlas-Paarsuche (projektbezogen, ohne Atlas entfaellt sie)
- **Aufgabe:** Der Agent urteilt je Kandidat (doppelt, ueberholt, gegenstandslos, verwandt, global, bleibt) und meldet je Urteil eine Zeile; geschrieben wird in 30er-Paketen ueber die Plansektionen `learning_updates` (`superseded_by`, `review_after`, `obsolete`) und `principles`. Das erste Paket nimmt den Schnappschuss (`--snapshot`, Rueckholpunkt), die weiteren keinen. Der Stempel `working/pflegelauf.json` (`pflegelauf_stamp`) entsteht erst nach einem vollstaendigen Lauf. Nie loeschen, nie `text` aendern; `superseded_by` und `obsolete` werden fuer rule_class-Learnings, Regelzeiger und deren Mitglieder verweigert.
- **Abhaengigkeiten:** Ausgeloest durch die PFLEGELAUF-Zeile im Start-Briefing (Schwelle in `memory-thresholds.sh`); laeuft in einer Sitzung, nie headless.

### Threshold-SSoT
- **Datei(en):** `scripts/memory-thresholds.sh`
- **Aufgabe:** EINZIGE Definition aller Skalierungs-Schwellen und der Pflegelauf-Schwelle; exit 10 bei Ueberschreitung. Seit 5.2.1 melden die Schwellen nur — nichts wird nach Anzahl oder Alter archiviert (D-021).
- **Abhaengigkeiten:** Gemeldet im Start-Briefing (THRESHOLD- und PFLEGELAUF-Zeilen); session-bootstrap (Step 3) und `wrapup_core.py pre` (nur die PFLEGELAUF-Zeile) lesen es ebenfalls. Skill-Bodies nennen keine Zahlen.

### Projektionen
- **Datei(en):** `scripts/memory_index_projection.py` (MEMORY.md), `scripts/bridge_projection.py` (AGENTS.md), `scripts/projection_text.py`
- **Aufgabe:** MEMORY.md zeigt seit 6.0.0 nur Regelzeiger und freigegebene Feedback-Learnings (einzelne Learnings kommen in Claude Code ueber den Prelude oder den Atlas); AGENTS.md bleibt unveraendert (Zeiger, bis zu 6 Learnings, Tasks).

### Salience-Ranking
- **Datei(en):** `scripts/learnings_top.py`
- **Aufgabe:** Deterministisches Top-N-Ranking der Learnings (`importance*0.4 + recency*0.3 + tag_overlap*0.3`) fuer den Bootstrap-Fallback ohne Full-Read und ohne Atlas.
- **Abhaengigkeiten:** session-bootstrap (Learnings Retrieval, Fallback-Pfad).

## Datenfluss

1. SessionStart-Hook sourct `mem-schema.sh`, erzeugt/heilt `.agent-memory/`, injiziert das Kurz-Briefing.
2. `session-bootstrap` liest auf Abruf Store + zentralen Handoff (read-only, ohne Ausnahme), produziert das volle Briefing.
3. Work Phase: User/CLAUDE.md-getrieben; `/agentic-os:log` und context-keeper schreiben ueber `apply_wrapup.py`.
4. `wrap-up` urteilt in einem Plan; `scripts/wrapup_core.py` schreibt ueber `apply_wrapup.py` und erzeugt session-summary, zentralen Handoff (prepend), Status-Board, Session-Note und beide Projektionen. Alte Plansektionen (`identity`, `user_candidates`, Iterations-`errors`) werden ignoriert und in `tally.ignored_sections` gezaehlt.
5. Sind seit dem letzten Stempel 30 Learnings dazugekommen, nennt das Briefing den Pflegelauf.

## Persistenz

| Speicher | Typ | Pfad | Inhalt |
|----------|-----|------|--------|
| Memory-Store | Dateien | `.agent-memory/` | Identity (lesend), Context, Iterations, Learnings, Working; aeltere Stores zusaetzlich `patterns/`, `errors.json`, Kandidaten-Queues als eingefrorene Daten |
| Zentraler Handoff | Markdown | `~/AI/.agent-memory/session-summary.md` | Cross-Project, gestapelt (prepend) |
| Status-Board | Markdown | `~/AI/cross-project-status.md` | Ein Abschnitt pro Projekt |

## Sicherheit

Ein einziger Schreiber mit Store-Lock; jede Ablehnung vor dem ersten Byte. rule_class-Learnings erreichen AGENTS.md und MEMORY.md nur mit Erklaerbogen und Freigabe des Owners. Nichts wird nach Anzahl oder Alter geloescht oder archiviert (D-021). `.agent-memory/` wird von Commits ausgeschlossen (`.git/info/exclude` beim Anlegen).

## Deployment

Reines Markdown/JSON/Bash/Python, kein Build. Installation via `claude plugin install`. Laufende Instanz nutzt eine versionierte Cache-Kopie unter `~/.claude/plugins/cache/...` — Repo-Edits greifen erst nach Cache-Spiegelung oder Marketplace-Update.

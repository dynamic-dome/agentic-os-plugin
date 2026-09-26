# VERSIONING — agentic-os

**Source of Truth: `.claude-plugin/plugin.json` → `version`.** Nichts anderes
(README, CHANGELOG, Skill-Metadaten) definiert die Plugin-Version — alles andere
folgt ihr. Die Marketplace-Quelle ist ungepinnt (git-URL, HEAD): `claude plugin
update agentic-os` installiert immer den letzten gepushten Stand und legt ihn im
Cache unter `.../agentic-os/{version}/` ab.

## Bump-Mapping (SemVer)

| Bump | Wann | Beispiele |
|---|---|---|
| **MAJOR** | Breaking: Store-Format inkompatibel, Hook-Kontrakt geaendert (Fail-soft, Exit-Codes), Skill entfaellt/umbenannt | 3.x → 4.0 (Store-Restrukturierung) |
| **MINOR** | Neue Faehigkeit: neuer Hook/Skill/Step, neue Store-Felder (abwaertskompatibel) | 4.3.0 Dirty-Tracker+Marker; 4.4.0 Learnings-Provenance |
| **PATCH** | Fixes + Doku ohne neue Faehigkeit; kleine Feld-Ergaenzungen im Dienst eines Fixes | 4.4.1 Recovery-Falsch-Positiv (inkl. Pre-Run-Commit-Step) |

Grauzone im Zweifel: Was ein Nutzer/eine Folge-Session als "kann jetzt mehr"
wahrnimmt → MINOR; was "verhaelt sich jetzt korrekt" ist → PATCH.

## Regeln

1. **Bump im selben Commit** wie die erste Aenderung des Release — nie nachtraeglich.
2. **CHANGELOG-Eintrag pro Release** (`docs/CHANGELOG.md`, neueste oben); Follow-ups
   VOR dem Push (z.B. Verifier-Fixes) bleiben unter derselben Version und erweitern
   den bestehenden Eintrag.
3. **Skill-Metadaten-Version** (`metadata.version` in SKILL.md) ist eine separate,
   skill-lokale Zahl: bei jeder inhaltlichen Skill-Aenderung minor bumpen. Sie ersetzt
   nie die Plugin-Version.
4. **Deploy-Zweischritt:** `git push origin main` → `claude plugin update agentic-os`.
   Vorher: `bash tests/run-all.sh` gruen (Circuit-Breaker-Suite darf >2 min brauchen).
5. **Aenderungen greifen erst in neu gestarteten Sessions** — laufende Sessions
   behalten Hooks/Skills ihrer Start-Version. Nie im Cache patchen.
6. **Rollback:** alte Cache-Versionen bleiben liegen; im Notfall Quellrepo-Revert
   committen + pushen + erneut `claude plugin update` (nie Cache-Ordner tauschen).

# Changelog — Agentic OS

Neueste Eintraege oben. Format: `## [YYYY-MM-DD] Kurztitel`

---

## [2026-10-01] Release v5.1.5 — learnings.md mit Kopf, Decay einmal pro Stufe, MEMORY.md-Kurzfassungen

PATCH. Drei Befunde aus einem `/agentic-os:maintain`-Lauf am 2026-10-01 (Plugin 5.1.4).
(1) `apply_wrapup.py` schrieb `learnings.md` ohne den Kopf `*Auto-generated from learnings.json — do not
edit directly.*`, den maintain 5.2 und memory-audit pruefen - jeder Store galt als "regenerate", und die
Doku (wrap-up 3c: nach Datum gruppiert; maintain 3: von Hand kuerzen/deduplizieren) beschrieb ein anderes
Format als das Skript. Eine Wahrheit: `learnings.md` ist reine Projektion aus `apply_wrapup.py` (Kopf, dann
`## Importance 5..1`, neueste zuerst). Neu `apply_wrapup.py <mem> --render-learnings` (ohne Plan, ohne Marker)
fuer maintain 3/5.2; der Init-Platzhalter in `mem-schema.sh` ist byte-identisch zum Render eines leeren Stores.
(2) maintain 4b zog die 90-Tage-Stufen vom gespeicherten Wert ab - jeder weitere Lauf wertete dieselbe Stufe
erneut ab. Neu `scripts/global_decay.py` (Preview, `--apply`) als einziger Ort der Regel: Buchfuehrung je
Eintrag mit `decay_steps_applied` + `decay_anchor` (das `last_relevant`, gegen das gezaehlt wurde), nur die
Differenz wird abgezogen; ein Recall verschiebt `last_relevant`, der Zaehler beginnt dann neu (ein Zaehler ohne
Anker haette die naechsten Stufen verschluckt). Eintraege mit Zaehler aber ohne Anker (manueller Workaround vom
2026-10-01) gelten als gegen das aktuelle `last_relevant` gebucht. Zwei Nebenfunde: `last_relevant` steht im
Bestand teils als ISO-Zeitstempel (jetzt gelesen), und der alte Helfer hob Werte unter 0,3 auf 0,3 an (jetzt
unberuehrt). `apply_decay` aus `global-schema.sh` entfernt, ein Test haelt die Regel an einer Stelle.
(3) `memory_index_projection.py` begrenzte nur die Anzahl (20), nicht die Laenge - der Bruecken-Block machte
in einem Projekt 12 von 13,5 KB der `MEMORY.md` aus. Jetzt Kurzfassung (Whitespace normalisiert, ab 150 Zeichen
an der Wortgrenze mit `…` gekuerzt) hinter der `[Lnn]`-ID, jede Zeile <= 200 Zeichen, Verweiszeile auf den
Volltext in `learnings.json`.
(4) `native_memory_audit.py`: Die Injektionsstufe hing an selbst gesetzten 10/16 KB, und der Docstring
behauptete, MEMORY.md werde vollstaendig geladen. Claude Code laedt aber nur die ersten 200 Zeilen ODER 25 KB
(code.claude.com/docs/en/memory; offen, ob 25 000 oder 25 600 Bytes gemeint sind - die strengere Lesart
25 000 gilt). Stufe jetzt = Anteil der staerker ausgelasteten Grenze: ok < 60 %, warn < 85 %, critical < 100 %,
truncated >= 100 %, bestimmt auf dem UNGERUNDETEN Verhaeltnis (21 249 B = 84,996 % bleibt warn). Zeilen
binaer gezaehlt (CRLF = eine Zeile, letzte Zeile ohne Umbruch zaehlt). Neue Felder `memory_md_lines`,
`load_pct`, `load_limit`; Summary-Zeile nennt "(davon N abgeschnitten)". `memory_index_projection.py`
nutzt dieselbe Regel und meldet seine Ladequote; ab 100 % warnt es, weil der Bruecken-Block am Dateiende als
Erstes abgeschnitten wird.
Codex-Verifier zu (1)-(3), vier Befunde, alle eingearbeitet: `--render-learnings` liest strikt (kaputte oder
fehlende Quelle -> Exit 2, nichts umbenannt oder geschrieben; vorher Quarantaene + leere Projektion);
die 200-Zeichen-Grenze gilt auch bei ueberlanger ID; `global_decay.py` ueberspringt ungueltige
`decay_steps_applied` und meldet sie (`skipped`) und schreibt erst, wenn alle Stores berechnet sind (vorher
Abbruch nach halbem Schreiben); DEPENDENCIES-Kopf nennt `global_decay.py`. Zweiter Verifier-Lauf
(#9543 + Fixes), drei Befunde eingearbeitet: `--render-learnings` lehnt auch Nicht-Objekt-Zeilen mit Exit 2
ab; `global_decay.py` meldet Eintraege mit unlesbarem `last_relevant` oder nicht-numerischer Confidence als
`skipped` statt sie still als `unchanged` zu zaehlen (archivierte bleiben bewusst ausser Betracht); die
Projektion meldet die Ladequote auch bei 0 freigegebenen Eintraegen.
Tests: apply_wrapup 132/132, validate-plugin 150/150, validate-skills 101/101, native-memory-audit 44 Checks,
memory-index-projection +13, neu test-global-decay (28 Checks); ALL TEST SUITES PASSED.

---

## [2026-09-26] Release v5.1.4 — Wiki-Notizen schema-konform, Rolling-Synthese ohne Nachtraege

PATCH. Befund aus dem Wiki (DCO #9450, 2026-09-25): Der WikiRAG-Rebuild stand vom 2026-07-22 bis
2026-09-25 still, zuletzt blockiert von zwei ungueltigen Session-Notizen und einer Rolling-Synthese mit fuenf
angehaengten "Nachtrag"-Abschnitten. (1) `wrapup_parts/wikinote.py` schrieb `agent: claude-code-headless`;
das Wiki-Schema kennt nur kanonische Werte -> jetzt `agent: claude-code`, "headless" bleibt Tag. Test pinnte
den falschen Wert und prueft jetzt das Gegenteil; Querprobe gegen den echten Wiki-Validator: 0 Fehler.
(2) `skills/obsidian-sync/SKILL.md`: Vorlage enthaelt `query_kind: session` (fehlte, Hermes-Notiz 2026-09-24)
und die Regel fuer kanonische `agent`-Werte. Step 5 sortiert Learnings in den passenden Abschnitt ein, kennt die
Wiki-Caps (5 Abschnitte, 15 Eintraege, 200 Zeilen) und verbietet angehaengte Nachtrag-Abschnitte; blockiertes
In-place-Edit -> ganze Seite per Write oder Learning melden. Suite: ALL TEST SUITES PASSED (wrapup-core 81/81).

---

## [2026-09-25] Release v5.1.3 — Codex-Ingest als Snapshot-Sync + origin-Feld

PATCH. Befund membrain-Eval T-46 (2026-09-25): Die Claude-Frage B1 fiel 2/2 aus, weil
`ingest_codex_memory.py` nur exakt deduppte. Jede Codex-Umformulierung legte einen neuen Kandidaten an, und die
Duplikate verdraengten den DCO-Eintrag L186 aus der Bridge. Gemessen: Jaccard 0.8 faengt nur fast woertliche
Fassungen (0.96), echte Umformulierungen liegen bei 0.40-0.56. Neu: Die aktuelle `memory_summary.md` ist Kanon.
Fast identische Fassungen (>= 0.8) werden in-place ersetzt (Status bleibt), nicht mehr gelistete Tipps werden
`retired` (`superseded_by` verweist auf den Nachfolger ab 0.5), Umformulierungen abgelehnter Tipps bleiben
abgelehnt (`dup-rejected`), zurueckkehrende Tipps werden als Kandidat reaktiviert. Ein leerer Parse retiriert
nichts. membrain T-52 (Teil Codex-Bridge): Codex-Eintraege tragen `origin: "agent"`. Ingest-Tests 25 -> 47 Checks. Codex-Verifier (fe63f2b): 4 von 5 Befunden eingearbeitet (Gleichstand geht ans Veto, Nachfolger auch unter bestaetigten Eintraegen, origin-Backfill, Projektionen nach dem Ingest neu); abgelehnt: last_relevant an Nicht-Codex-Treffern ist gewolltes Relevanzsignal.

---

## [2026-09-24] Release v5.1.2 — BOM der Zieldateien erhalten

PATCH. Befund im Wrap-up nach dem ersten Echtlauf: `wikinote.py` (und derselbe Pfad in `handoff.py`) las mit
`utf-8-sig` und schrieb ohne BOM zurueck - der Echtlauf entfernte das BOM aus `~/wiki/index.md` und `log.md`.
Beide Writer behalten jetzt BOM und Zeilenenden der vorhandenen Datei. Tests +2 (80 Checks). BOMs im Wiki von
Hand zurueckgesetzt.

---

## [2026-09-24] Release v5.1.1 — Zeilenenden des zentralen Handoffs erhalten

PATCH. Befund aus dem ersten beobachteten Echtlauf von 5.1.0 (11:05): `handoff.py` schrieb die
CRLF-Dateien `~/AI/.agent-memory/session-summary.md` und `~/AI/cross-project-status.md` als LF zurueck -
inhaltlich nur die DCO-Sektion geaendert, aber Ganzdatei-Diff. Jetzt behalten `handoff.py` und
`wikinote.py` die Zeilenenden der vorhandenen Datei (neue Dateien LF). Test „CRLF file stays CRLF“ (+1,
78 Checks). Die zwei Dateien wurden inhaltsgleich auf CRLF zurueckgestellt (Backup vorher gezogen).
Nebenbefund, nicht angefasst: das Status-Board fuehrt DCO schon vor dem Lauf doppelt
(`dynamic_central_orchestrator (DCO)` Z.97 und `dynamic-central-orchestrator` Z.197) - der Lauf
aktualisiert nur die erste; Aufraeumen ist Owner-Sache.

---

## [2026-09-24] Release v5.1.0 — headless wrap-up + naechtliche Konsolidierung (T-028 Durchstich, V4)

MINOR (neue Faehigkeit). Owner-Auftrag: wrap-up soll „ohne meine Disziplin“ laufen. Plan
`docs/superpowers/plans/2026-09-24-t028-headless-consolidation.md`; zieht den headless-Pfad der V3-Spec
vor und haengt V4 als naechtlichen Lauf an (Design-Input R2 aus membrain/memopenclawharvest.md statt
SessionEnd-Ausloesung). Der Judge-Umbau (Skill-Body ≤3 Turns, `pre`, Plan-Pfad) bleibt T-028b.

- **`scripts/wrapup_core.py apply --headless`:** Iterationen (git log seit max(dirty-Start, letzter
  wrap-up) + uncommitted dirty-Dateien; ab 9 Commits nach Typ gruppiert) → `apply_wrapup`
  (`apply_iterations`, `apply_session_summary`, zuletzt `apply_consolidation`) → Pattern-Update →
  zentraler Handoff + Board → Wiki-Note → Projektionen. **Nie** `apply_user_candidates`, Learnings,
  Decisions, soul. Skips: keine echte dirty-Arbeit (Nachlaeufer-Regel wie RECOVERY), aktive Session
  (juengste dirty-Datei < 120 min). Leere Ernte → nur Marker (RECOVERY zu), reiche Summary/Handoff bleiben.
  Store-Fehler/unlesbare Evidenz → Exit 2 ohne Marker; Handoff/Wiki/Projektion fail-soft.
- **`scripts/wrapup_parts/`** (statt `wrapup_core/` der Spec, das Paket haette das Modul verdeckt):
  `harvest.py`, `handoff.py` (Demote, Ownership-Dedup ueber normalisierte Namen + `project_aliases`,
  Cap 5, Board-Sektion, Drift-Merge via `handoff_write_guard.file_state`), `wikinote.py` (Note +
  index/log, idempotent je Projekt und Tag, Gate `sync_enabled`/`session_note_threshold`).
- **`scripts/nightly_consolidate.py`:** Discovery (scandir, Tiefe 4, Sperrzone `~/ich`, ohne
  node_modules/worktrees), nur Arbeit juenger als 14 Tage, aelteste zuerst (juengstes Projekt oben im
  Handoff), Kill-Switch, JSONL-Bericht. Launcher + Windows-Task in `~/AI/Hooks-bau/nightly-consolidation/`.
- **Tests:** `tests/test-wrapup-core.py` (77 Checks, nur tmp-Projekte/-Zentrale/-Wiki/-Home),
  in `run-all.sh`. `apply_wrapup.py` + seine Tests unveraendert.
- **Befunde aus Echtdaten-Trockenlauf + Kopie-Lauf (DCO-Klon), alle per Test gefixt:** 145 alte
  Commits (since), leerer Block haette reiche 08:20-Uebergabe ueberschrieben, 145 Einzel-Iterationen,
  `dynamic-central-orchestrator` vs `dynamic_central_orchestrator (DCO)` → Board-Duplikat, ueberlange
  Commit-Betreffe.
- **Regelabweichung:** Versions-Bump kam nicht im ersten Commit des Releases (VERSIONING Regel 1),
  sondern nachgezogen.

---

## [2026-09-10] Release v5.0.4 — Bridge-Block Diaet (Cap 10 → 6, Text-Cap 220)

PATCH. Anlass membrain T-47 (DCO-Startkontext): der generierte AGENTS.md-Bridge-Block
lag bei 5,7 KB, Learnings 450-600 B je Eintrag.

- **bridge_projection.py:** Learnings-Cap `CAP` 10 → 6 (Ueberhang bleibt sichtbar als
  `(n ältere: learnings.json)`), neu `TEXT_CAP = 220` — Learning-Text wird wie der
  Task-Titel mit `…` gekuerzt; learnings.json bleibt kanonisch und vollstaendig.
  Gemessen DCO: 5,7 → ~2,3 KB. Tests: Cap-Fall auf 6 angepasst, Text-Cap-Fall neu (+2).

## [2026-09-09] Release v5.0.3 — Codex-Verifier-Befunde zu 5.0.2 (4 MEDIUM, 1 LOW)

PATCH. Verifier-Lauf (`codex exec --sandbox read-only`, Verdikt PARTIAL) gegen 7d2b1bf.

- **Promotion-Dedup (MEDIUM):** Jaccard-Schwelle 0,6 → 0,8 und Zitat-Suffix `(UCn, Datum)`
  wird vor dem Vergleich entfernt (sonst lag ein echtes Duplikat bei genau 0,60 und eine
  verwandte, andere Praeferenz bei 0,64). Jeder Skip schreibt jetzt einen
  `user.md/skipped-duplicate`-Eintrag mit der getroffenen Zeile ins user-changelog —
  eine negierte Restatement-Praeferenz („niemals") ist per Jaccard nicht erkennbar,
  darf aber nicht stumm verschwinden. Test 34 erweitert (+ Gegenprobe).
- **Pattern-Ref-Check (2× MEDIUM):** `patterns.json = null` warf `AttributeError` und
  unterdrueckte den restlichen State — `_rows` typgeprueft, Aufruf fail-soft gekapselt.
  Regex auf `P0\d{2}` verengt (`P100`/`P250` in Prosa sind keine Pattern-ids). Tests +2.
- **Subdir-Guard (MEDIUM):** Session in einem Unterverzeichnis eines Repos, dessen Wurzel
  einen Store HAT, bekommt jetzt das Briefing/Recovery aus dem Root-Store (PROJECT_DIR
  wird auf das Toplevel umgebogen, Hinweiszeile im additionalContext) statt eines
  stillen `exit 0` vor dem JSON-Vertrag. Ohne Root-Store bleibt es beim Skip. Test +3.
- **stdin-Fail-soft (LOW):** `ValueError` (geschlossenes stdin) in beide
  `reconfigure`-Guards aufgenommen.

## [2026-09-09] Release v5.0.2 — stdin ist UTF-8 (Mojibake-Quelle), E2-Cap 40 → 20

PATCH laut VERSIONING: „verhaelt sich jetzt korrekt".

- **Mojibake-Ursache gefunden und behoben:** `apply_wrapup.py` und `extract_patterns.py`
  lesen den Plan per Heredoc von stdin; stdout war seit 4.x auf UTF-8 umgestellt, stdin
  blieb auf Windows cp1252. Jeder Gedankenstrich im Plan landete als `â€”` im Store
  (membrain L57/L59/L60 + session-summary, DCO L177–L179 + `Â§`, agentic-os L34 + user.md
  UC12). Fix: `sys.stdin.reconfigure(encoding="utf-8")` neben dem stdout-Reconfigure.
  Test 33 in `test-apply-wrapup.py` (roh-UTF-8-Plan unter `PYTHONIOENCODING=cp1252`;
  gegen die 5.0.1-Version rc 2, jetzt gruen). Bestandsdaten in allen drei Stores
  repariert (cp1252→utf-8-Redecode, 0 Restfunde).
- **E2-Cap 40 → 20 Zeilen** in `memory_index_projection.py` (Owner-Entscheid T-43,
  membrain): der MEMORY.md-Block ist der teuerste Posten des Startkontexts (~370 B/Zeile);
  Overflow-Zeile verweist auf learnings.json. Test angepasst (25 approved → 20 + 5 overflow).
- **V7-Hygiene (Gesamtanalyse):**
  - `apply_wrapup.py` promotet keinen Kandidaten mehr, dessen id schon in user.md zitiert
    ist oder dessen Text eine bestehende Zeile fast wiederholt (Jaccard ≥ 0,6) — Tally
    `promotion_skipped_duplicate`, Queue-Status `duplicate_of_existing`; Test 34 mit dem
    realen UC1–UC3-Fall vom 2026-07-27. Die 3 Duplikate im eigenen user.md entfernt.
  - `preprocess_state.py` meldet Learnings, die eine nicht existierende Pattern-id zitieren,
    als `validation_errors` (`L5 references unknown pattern P006`); L5/L10/L12 auf
    `G-pattern-005` umgehaengt, toter P004-Verweis aus patterns.json entfernt.
  - `session-start.sh` initialisiert keinen Store mehr in einem Unterverzeichnis eines
    Git-Repos (77 Stub-Stores in Scratch-/Work-/Temp-Ordnern gefunden); Nicht-Git-Ordner
    (AI-Workspace) und Repo-Wurzeln initialisieren weiter. Guard-Suite +5 Faelle.
    Stub-Loeschung bewusst NICHT durchgefuehrt: 60 liegen in `%TEMP%` (Test-Artefakte),
    der Rest sind Fixtures/Quarantaene/Loop-Work-Dirs.
  - Doku: `plugin.json`-description auf einen Satz, `CONSUMERS.md` auf die realen Konsumenten
    (research-pipeline/quality-gate raus; haengende Referenzen in dome-loop und
    devil-advocate-swarms benannt), `ANALYSE.md` (2.0.0) nach `docs/archive/`.

## [2026-09-08] Release v5.0.1 — wrap-up: Batch-Write vor Wiki-Sync/Handoff, Marker als eigener Call (T-024, L44)

PATCH laut VERSIONING: „verhaelt sich jetzt korrekt", keine neue Faehigkeit.

- **Step 8.5 → Step 7.4 (batch-apply):** der Haupt-Batch-Write (learnings, decisions,
  session-summary, open-tasks, identity) laeuft jetzt VOR obsidian-sync (7.5), dem zentralen
  Handoff (7.6) und dem Commit-Angebot (8). Vorher las obsidian-sync bei jedem regulaeren Lauf
  den Stand VOR der Session (L44: 0 statt 2 substanzielle Learnings gesehen), und der
  Memory-Commit wurde angeboten, bevor die Dateien existierten.
- **Step 9.5 ist ein eigener Marker-Call** (`{"consolidate": true}` an `apply_wrapup.py`), der
  Hauptplan traegt kein `consolidate` mehr. Sonst landen Wiki-Note und Handoff-Dateien als
  Tail-Writes hinter dem Marker und loesen im naechsten Bootstrap RECOVERY-Fehlalarme aus
  (heute live: 6 Tail-Writes, Downgrade-Schwelle 5). Drei Batch-Calls pro Lauf: 1.5
  (iterations), 7.4 (Rest), 9.5 (Marker).
- **Tests:** 2 Ordnungs-Assertions in `validate-skills.sh` (batch-apply vor Wiki-Sync +
  Handoff; consolidation-marker hat eigenen Call, batch-apply-Plan ohne consolidate),
  erst rot, dann gruen. Bekannte Einschraenkung: der Marker-Call kennt den Tally des
  Hauptlaufs nicht, `learnings_added` im Marker ist daher 0 (informativ, Bootstrap liest
  nur `last_wrapup` + `consolidated_sessions`).
- **T-026 geschlossen:** Live-Verifikation in neuer Session — maintain/log/sync-context
  fehlen in der Skill-Liste, `disable-model-invocation: true` wirkt auf Command-Dateien.

## [2026-09-08] Release v5.0.0 — Portfolio-Schnitt: 9 Skills → 5, mechanische Skills werden Commands

Owner-Entscheid aus der Gesamtanalyse (`~/AI/membrain/memgesamtanalyse-2026-09.md`, F6/V5),
Reihenfolge V5 → V3 → V4 (V5 raeumt V3 den Tisch frei). MAJOR laut VERSIONING: Skills entfallen.

- **Archiviert nach `_archived/` (reversibel, `git mv`):** `skills/self-improve` (still seit
  2026-06-21, 21 KB Policy ohne Nutzungsnachweis), `commands/rollback`, `commands/auto-commit`
  (nur von self-improve gerufen), `improvements/` (Loop-State). Der Eval-Harness (Lever 6)
  lebt in `tests/eval/` weiter. 17 validate-plugin- und 10 validate-skills-Bloecke entfernt.
- **`memory-maintenance` → `/agentic-os:maintain`:** Command mit Skript-Kern
  (`memory-thresholds.sh`, `gc_dirty_markers.py`, `native_memory_audit.py`, `review_sweep.py`,
  `extract_patterns.py --refresh`). wrap-up Step 9 ruft den Skill nicht mehr auf, sondern druckt
  die `THRESHOLD:`-Zeilen und empfiehlt den Command (L34/D-010: jede Skill-Body-Injektion
  riskiert einen vollen Prefix-Cache-Rewrite).
- **`iteration-logger` → `/agentic-os:log "<summary>"`:** baut einen `iterations`-Plan und
  schreibt ueber `apply_wrapup.py` (einziger Writer seit 4.16.0). Trigger-Phrasen entfallen —
  der Body war 70 % Mechanik.
- **`sync-context` → `/agentic-os:sync-context [pull|push|sync]`:** war schon
  `disable-model-invocation: true`; jetzt ein Command, dessen Regeln (Privacy-Filter,
  Promotion-Gate, Provenance, Recency-Supersession, Pull-Lifecycle) unveraendert getestet werden.
- **Bleiben Skills (echter Urteilsanteil):** session-bootstrap, wrap-up, pattern-extractor
  (Steps 6.5/6.6), context-keeper, obsidian-sync. `scripts/model-routing.sh` fuehrt genau
  diese fuenf.
- Commands jetzt 6: init, status, memory-audit, maintain, log, sync-context. Die drei neuen
  tragen `disable-model-invocation: true` — ein Command ist sonst ueber das Skill-Tool
  aufrufbar und seine Description laedt in jeden Prompt (Codex-Verifier-Finding; Test im
  Invocation-Contract). `apply_wrapup.py` fuehrt die Identity-Applier nur noch fuer Plaene mit
  `user_candidates`/`soul_candidates`-Sektion oder `consolidate: true` aus, damit `/agentic-os:log`
  nie nach `user.md` promotet (Prinzip 8; vorher stille Nebenwirkung des iteration-logger-Pfads).
- Doku nachgezogen: README, CLAUDE.md, CAPABILITIES, DEPENDENCIES (Lifecycle, Matrix,
  Prinzipien 4/5/6/8/9), references/skill-template + memory-structure, plugin.json-Description.

## [2026-09-08] Release v4.21.0 — Hook-Schicht ehrlich: Briefing erreicht das Modell, tote Prompt-Hooks entfernt

Befund aus der Gesamtanalyse (`~/AI/membrain/memgesamtanalyse-2026-09.md`, F1/F2/V8):

- **SessionStart-Briefing war fürs Modell unsichtbar.** `session-start.sh` gab
  `{"systemMessage": ...}` aus; das Transkript (Claude Code 2.1.263, Session 15c40732)
  führt das als `hook_system_message` — nur der User sah es, der Modellkontext nie.
  Nur `hookSpecificOutput.additionalContext` kommt an (`hook_additional_context`).
  Jetzt: additionalContext-Kontrakt, kein systemMessage mehr. Test:
  `tests/test-session-start-briefing.sh` (registriert in run-all.sh).
- **Zähler repariert:** `^## Iteration` zählte auf jedem realen Store 0 (Format auf Platte
  ist `## {date} — {type}: {title}`); `grep -c '"id"'` zählte Zeilen statt Treffer.
- **Next steps aus dem SSoT:** Top-3 offene/blockierte Tasks plus Gesamtzahl direkt aus
  `context/open-tasks.json` (blocked zuerst), nicht mehr per Regex aus session-summary.md.
  Fallback auf die Summary nur ohne Python.
- **Neu im Briefing:** Kopf des zentralen Handoffs (Projekt/Datum/Agent), Warnung bei
  Root-Drift von `open-tasks.json`, und der Hinweis auf den Slash-Pfad (unten).
  UTF-8 auf stdin UND stdout erzwungen (Windows-cp1252-Mojibake, err-008-Klasse).
- **Drei Prompt-Hooks entfernt** (UserPromptSubmit, PreCompact, SessionEnd). Laut Doku
  (hooks.md, 2026-09-08): „SessionEnd hooks cannot invoke skills or take further actions",
  „Hook output is not preserved after compaction"; ein UserPromptSubmit-Prompt-Hook ist ein
  eigener Modell-Call pro Prompt, dessen Output bei `ok` verworfen wird. Die versprochenen
  Wirkungen („invoke wrap-up", „survival summary", „Task-Guard") waren nie möglich. Ersatz:
  Task↔Summary-Abgleich lebt deterministisch in `apply_wrapup.py` (Step 5.5 rendert Open
  Items aus open-tasks.json); Recovery/Drift kommen mechanisch im SessionStart-Briefing,
  das nach `/compact` erneut feuert. Tests 2/67/67b/Drift in validate-plugin.sh ersetzt.
- **Modell-Frontmatter: Aufrufweg entscheidet (gemessen, 2.1.263, 4 Transkripte):**
  `/agentic-os:wrap-up` als Slash-Command → Calls auf `claude-sonnet-5` (2/2);
  `Skill(agentic-os:wrap-up)` über das Skill-Tool → Calls bleiben auf `claude-fable-5-1`
  (2/2); kein `/model`-Befehl in den Transkripten. Der No-Op-Befund D-009 gilt damit nur
  für den Skill-Tool-Pfad. Konsequenz: Klammer-Skills immer per Slash aufrufen; das
  Briefing sagt es, CLAUDE.md dokumentiert es.
- plugin.json-`description` auf einen Absatz gekürzt; README/CLAUDE.md/DEPENDENCIES/
  scripts-README auf zwei Hooks nachgezogen.

## [2026-09-08] Release v4.20.0 — Memory-Hub: Codex→Claude-Kante, MEMORY.md-Projektion, Review-Sweep

Spec: membrain/docs/2026-09-08-memory-hub-kreislauf-spec.md. learnings.json bleibt Hub;
neue additive Felder `source_agent` (claude|codex, Default claude) und `kind`
(learning|feedback, Default learning).

- **E1 `scripts/ingest_codex_memory.py`** — liest `~/.codex/memories/memory_summary.md`
  (User preferences → feedback, General Tips → learning), dedupe via `norm`/Provenance-Hash,
  neue Einträge als `bridge_status=candidate`, `source_agent=codex`. wrap-up Step 3e.
- **E3 `bridge_projection.py`** — Loop-Schutz: Einträge mit `source_agent=codex` werden nicht nach AGENTS.md zurückprojiziert.
- **E2 `scripts/memory_index_projection.py`** — approved Learnings + Feedback (inkl. Codex-Quelle) als managed Block in Claudes nativer MEMORY.md (Cap 40, handgeschriebene Zeilen byte-identisch).
- **Verfall `scripts/review_sweep.py`** — Bericht über fällige `review_after`, nicht indexierte alte Native-Memories und überfällige Bridge-Kandidaten; wrap-up Step 9. Nur Bericht, keine Löschung.
- **session-start.sh** — Guard: kein Auto-Init, wenn cwd innerhalb `.agent-memory` liegt (Ursache der 4 verschachtelten Stores, membrain memhygiene-2026-09). Controller-Befund: die tatsächlichen verschachtelten `.agent-memory/.agent-memory`-Stores kamen nicht von session-start.sh, sondern von `scripts/cost-trace.sh` (`mkdir -p "$MEM/metrics"`), wenn cwd bereits innerhalb `.agent-memory` lag — dieselbe Guard wurde daher zusätzlich dort eingebaut.
- **wrap-up SKILL** 4.2 → 4.3 (Steps 3e, 3d.3, 9); Projektionsaufrufe in 3d.3 jetzt mit `${CLAUDE_PLUGIN_ROOT}`.

## [2026-07-27] Release v4.18.0 — T-015: Delegations-Umbau des wrap-up

wrap-up ruft auf dem Routine-Pfad keine Skills mehr auf, deren Arbeit mechanisch
ist. Grundlage ist die Messung aus D-010/L34: ueber 7 Transkripte und ~1300
API-Calls, **normalisiert auf Gelegenheiten**, folgt auf einen Skill-Aufruf in 41%
der Faelle ein voller Prefix-Cache-Rewrite (9/22) — gegen 0,5% bei Bash (3/618)
und 0% bei Edit (0/233). Ein Rewrite bei 100k+ Kontext ist teuer, weil
`cache_creation` das 12,5-fache von `cache_read` kostet. Eine Skill-Injektion ist
damit eine Architektur-Entscheidung, kein neutraler Aufruf.

**Was verschoben wurde (nicht geloescht).** Ein Skill-Aufruf startet keinen
zweiten Prozess — er laedt Anleitungstext in denselben Kontext, der die Arbeit
ohnehin macht. Die drei Bodies zerfielen daher in zwei Haelften: die mechanische
wanderte in Code, die urteilende in ~10 Zeilen wrap-up-Body.

- `iterations` + `decisions` sind neue Plan-Sektionen von `scripts/apply_wrapup.py`
  (Step 1.5 / Step 4.5). Das Skript besitzt ID-Fortschreibung, die Recurrence-Regel
  (gleiche category UND >= 2 ueberlappende Tags), das Markdown-Format, die
  Working-Memory-Buchhaltung, den Append-only-Kontrakt und den Supersede-Flip.
- `scripts/extract_patterns.py` (neu) besitzt die Detektions-Heuristiken, die
  Confidence-Formel, den Jaccard-Dedup, die Legacy-Normalisierung und die
  `patterns.md`-Projektion. `--update` wendet alles Determinierte an und meldet
  unbenannte Cluster als `proposals`; `--apply` nimmt **nur Sprache** —
  evidence/occurrences/confidence stammen aus der Messung und sind nicht setzbar.
- `pattern-extractor` behaelt genau einen bedingten Aufruf: Steps 6.5/6.6
  (Skill-Generierung, Rueckfluss-Drafts) sind echte Urteilsarbeit.

**Ownership verschoben, nicht aufgeweicht.** `apply_wrapup.py` verweigerte diese
Dateien bisher pauschal (D-008). Jetzt sind sie ueber `APPLIER_OWNED` **nur** durch
ihren benannten Applier erreichbar — der generische Schreibpfad und die
Quarantaene-Route verweigern sie weiterhin, `patterns.*` und `soul.md` bleiben
komplett gesperrt.

**Nebenbefund: die Schemata stimmten nicht.** Die dokumentierten Templates
versprachen `E{n}`, `D{n}` und `## Iteration #{n}` — auf Platte stehen seit Monaten
`err-007`, `D-008` und `## {date} — {type}: {title}`. Jeder Lauf hatte das Format
neu interpretiert. Beide Skripte **erkennen** das dominante Format jetzt, statt es
anzunehmen (ein Ausreisser wie `G-pattern-005` unter `P0nn` kann die Sequenz nicht
kapern), und das Markdown-Format ist einmalig fixiert.

**DoD (bewusst geaendert).** T-015 forderte urspruenglich eine Halbierung der
Rewrite-Zahl gegen die Baseline (6). Der Umbau entfernt 3 Injektionen, was bei
p=0,41 ~1,2 Rewrites erwarten laesst — kleiner als das Rauschen zwischen zwei
Laeufen. Gemessen wird deshalb das **Skill-Invocation-Budget pro wrap-up-Lauf**:
deklarierte Invokes 5 -> 3 (pattern-extractor bedingt, obsidian-sync,
memory-maintenance bedingt), auf einem typischen Lauf 4 -> 1, weil nur
obsidian-sync unbedingt feuert. Erzwungen vom Test `wrap-up delegation budget`
in `validate-plugin.sh`.
Der ersetzte Vorgaenger-Test greppte `invoke ... <skill>` und lief nach dem Umbau
**false-green** auf `do NOT invoke <skill>`.

**Codex-Verifier-Runde (Verdikt: rejected, 14 Befunde) — alle behoben:**

- **Idempotenz:** der Header-Dedup lief NACH der Fehlerverarbeitung, ein
  wiederholter Plan zaehlte denselben Fehler jedes Mal erneut als Recurrence
  (reproduziert: occurrences 2 -> 3 -> 4). Dedup laeuft jetzt zuerst; eine
  uebersprungene Iteration fasst nichts mehr an.
- **Guard umgehbar:** `iterations/../iterations/errors.json` passierte den
  Ownership-Check als Rohstring und ueberschrieb die geschuetzte Datei. Beide
  Skripte kanonisieren Pfade jetzt vor der Pruefung und weisen Ausbrueche ab.
- **Marker-Reihenfolge:** der Konsolidierungs-Marker wurde VOR den Dirty-Flags
  geschrieben — ein IO-Fehler dabei hinterliess Exit 2 *mit* Marker. Jetzt
  Flags zuerst, Marker als letzter Schreibvorgang.
- **Korrupte `dirty-*.json`** wurde quarantaeniert, uebersprungen und der Marker
  trotzdem geschrieben: die einzige Spur unkonsolidierter Arbeit verschwand.
  Wird jetzt strikt behandelt und bricht den Lauf ab.
- **Plan-Validierung** deckt alle Pflichtfelder vorab ab, nicht nur `supersedes`.
- **Decisions:** Identitaet ist `(title, supersedes)` — Titel-Dedup allein
  verwarf eine legitime Ablesung, gar kein Dedup machte sie nicht-idempotent
  (zweites Problem erst vom Smoke-Lauf gegen eine Store-Kopie gefunden).
- **`match_existing()`** rankt jetzt (exakte Evidenz > Jaccard > Tags) statt
  Dateireihenfolge, meldet Mehrfachtreffer als `ambiguous_matches`; der
  Jaccard-Zweig war auf dem `--update`-Pfad toter Code und laeuft jetzt beim
  `--apply`, wo Wording existiert.
- **Merge-Rechnung:** occurrences/confidence werden ueber die vereinigte
  Evidenz neu berechnet — ein Merge konnte die Confidence vorher *senken*.
- **Legacy-Normalisierung** parkt widerspruechliche Altwerte unter
  `legacy_values` statt sie zu verwerfen; `--refresh` umgeht den Cold-Start-Guard;
  doppelte `cluster_key` in einem Plan werden abgewiesen; ID-Tie-Break bevorzugt
  die kanonische Familie.
- **Budget-Test** erkennt jetzt auch `call/delegate to/trigger/hands off to` —
  vorher haette eine so formulierte Delegation das Budget passiert
  (per Strip-Probe gegengeprueft).

**Ehrlichkeitskorrektur an der Doku:** `extract_patterns.py` implementiert die
*fehlerbasierte* Haelfte von Step 2. Datei-Hotspots, wiederholte erfolgreiche
Ansaetze und fragile Testbereiche brauchen strukturierte Iterationsdaten —
`iteration-log.md` ist Prosa. Der Skill sagt das jetzt, statt die Abdeckung zu
behaupten (T-019).

**Reihenfolge-Fix (Design):** Step 4 las `errors.json` von der Platte, waehrend
die geharvesteten Fehler noch im Schreibplan lagen — der Extractor haette nie die
Fehler der eigenen Session gesehen. Die `iterations`-Sektion wird deshalb frueh
angewendet (Step 1.5), der Rest in Step 8.5: zwei Batch-Calls statt einem.

**Tests.** `test-apply-wrapup.py` 55 -> 109, neu `test-extract-patterns.py` (64),
Eval `gate_linkage.py` nachgezogen. Volle Suite gruen. Verifikation zusaetzlich
per Smoke gegen eine Kopie des echten Stores (3 Laeufe, vollstaendig idempotent).

---

## [2026-07-21] Release v4.13.0 — T-41/T-42: Rueckfluss sichtbar, Autoritaets-Matrix messbar

Zwei Rosinen aus der membrain-Ernte `memperfectflowharvest.md`. Beide schliessen
keine Funktions-, sondern eine **Sichtbarkeitsluecke**: die Mechanik lief bereits,
nur konnte niemand pruefen, ob sie laeuft.

**T-41 — `memory-audit` Step 3.4 (rueckfluss-audit).** Die vier Rueckflussfelder
werden von wrap-up (`derived_from`, `review_after`) und pattern-extractor
(`implemented_by`, `validated_by`) geschrieben, aber von keinem Report geprueft.
Neuer Step 3.4 meldet: Learnings ohne `derived_from` (mit Datum des juengsten
Verstosses — alte Eintraege vor dem Provenienz-Kontrakt sind erwartbar, ein
frischer ist das Signal), ueberfaellige `review_after`, Patterns mit
`promotion_status: ready` ohne `implemented_by`, Patterns mit `implemented_by`
ohne `validated_by`. **Immer mit Record-IDs**, Nullwerte explizit ("0 — clean"),
weil ein leerer Block sonst wie "nicht geprueft" liest. Report bekommt einen
BACKFLOW-Block; `feedback-loop-gap` ist die passende Gap-Klasse. Live-Beleg fuer
den Wert (membrain-Store): 14/56 Learnings ohne `derived_from` waren unsichtbar.
Absicherung: `test-pattern-rueckfluss-contract.sh` Abschnitt 7 (jedes Feld muss in
Step 3.4 UND im Report stehen), mutationsgetestet. Korrektur gegenueber der
Ernte-Notiz: der reale `promotion_status`-Wertebereich ist `candidate | ready`
(obsidian-sync Step 6) — ein "promoted" gibt es nicht.

**T-42 — Retrieval-Golden-Set (`tests/eval/retrieval_golden.{json,py}`).** Die
Autoritaets-Matrix (welche Quelle FUEHRT bei welchem Fragetyp) war kanonische
Prosa ohne Pruefpfad; semantische Aehnlichkeit konnte die zustaendige Quelle
ueberstimmen, ohne dass etwas rot wird. 22 Faelle ueber 15 Fragetypen, je mit
erwarteter Leitquelle, erlaubten Ergaenzungsquellen, typischem Fehlgriff und
Begruendung — inklusive des kanonischen P1-Paars R11/R12 (Card waehlt aus, reale
Skill-Datei belegt — und die Umkehrung, sonst kollabiert die Regel zu "immer
Dateien lesen"). Schicht 1 validiert den Satz deterministisch und **verankert
jede `kind: store`-Quelle in `DEPENDENCIES.md`**, damit ein Store-Rename CI rot
faerbt statt den Satz still zu entwerten (Anker per Basename — ein reiner
Verzeichnis-Move greift bewusst nicht, das Layout gehoert `mem-schema.sh`).
`--selftest` beweist mit 11 Mutationen, dass jede Regel traegt. Schicht 2
(`--score answers.json`) bewertet einen aufgezeichneten Lauf und meldet Treffer
auf `typical_wrong_pick` getrennt als TRAP. Eingehaengt in `run-eval.sh`, damit in
`run-all.sh`.

Anmerkung: v4.11.x und v4.12.0 haben keine CHANGELOG-Eintraege (Bumps liefen in
fix-Commits mit) — hier bewusst nicht rueckwirkend rekonstruiert.

## [2026-07-17] Release v4.10.0 — T-25 Pivot: offene Tasks in AGENTS.md projizieren

Kern-Erkenntnis aus T-25 (Codex-eigene Session-Transcripts): interaktives Codex
(TUI/Desktop) injiziert SessionStart-Hook-`additionalContext` NICHT — der Marker
fehlt in jedem interaktiven Transcript, waehrend AGENTS.md nachweislich als
user-Message + world_state injiziert und genutzt wird (Codex zitierte L2/L19/L28).
Der Hook-Kanal bedient nur headless `codex exec`. Konsequenz: die dynamische
open-tasks-Info wandert vom Hook in die AGENTS.md-Projektion, den Kanal, den Codex
interaktiv wirklich liest. `bridge_projection.py` rendert jetzt einen zweiten
managed Abschnitt "## Bridge: Offene Tasks" (open/blocked, IDs+Titel, Cap 5) VOR
den Learnings, im selben idempotenten Block; Tasks sind fail-soft (fehlende/korrupte
open-tasks.json -> uebersprungen, nie exit 1; Learnings bleiben kanonisch/exit 1).
Trigger unveraendert: das bestehende wrap-up-Bridge-Gate. Der volatile
Cross-Project-Handoff bleibt bewusst Pointer (globale AGENTS.md), nicht projiziert.
Bridge-Testsuite +6 Faelle (Tasks-only, Tasks+Learnings-Reihenfolge, Cap, korrupt
fail-soft, Idempotenz, Widerruf). Der SessionStart-Hook (4.9.2-4.9.4-Fixes) bleibt
fuer den headless-Pfad aktiv und korrekt.

## [2026-07-17] Release v4.9.4 — T-25 Fix: Codex-Briefing-Hook haengt nicht mehr (stdin)

T-25-Retest (interaktive Codex-TUI in membrain) lieferte via Codex' eigenem
Session-Transcript (~/.codex/sessions/) die harte Evidenz: der SessionStart-Hook
FEUERT interaktiv, aber `SessionStart hook failed: timed out after 5s`. Root-Cause
war eine Regression aus 4.9.3: `PAYLOAD=$(cat)` wartet auf stdin-EOF; interaktives
Codex haelt stdin offen -> `cat` blockiert -> Hook wird nach 5s gekillt -> kein
Briefing (reproduziert: exit 124 gegen offene Pipe). Fix: (a) Normalpfad nutzt
`${CLAUDE_PROJECT_DIR:-$PWD}` (in TUI/`codex exec`/Desktop-im-Projekt ist $PWD =
Projekt) und liest stdin GAR NICHT -> schneller (~2,8s statt Timeout), kein
Hang-Risiko; (b) payload.cwd nur noch als Fallback, wenn dort kein Store liegt, und
dann NICHT-blockierend (`read -r -t 1` statt `cat`). Testsuite 28 Checks (+no-hang
statischer Guard gegen Reintroduktion von `$(cat` + timeout-begrenztes read).
Befund am Rande: Codex Desktop im "neue Aufgabe"-Modus laeuft in einem ephemeren
Pro-Prompt-cwd (Documents\Codex\<slug>) ohne Store — dort ist kein Briefing
moeglich; nur TUI/`codex exec` im Projekt werden bedient.

## [2026-07-17] Release v4.9.3 — T-24/T-25 Fix: Codex-Briefing findet den Store (payload.cwd)

T-25-Sichtpruefung (User, interaktive Codex-Session) falsifizierte die
Interaktiv-Annahme: Codex erhielt KEINEN Kontext (fiel auf globales Wissen
zurueck). Root-Cause: `codex-session-briefing.sh` ermittelte das Projekt via
`${CLAUDE_PROJECT_DIR:-$PWD}` — Codex setzt `CLAUDE_PROJECT_DIR` nicht, und
interaktiv startet der Hook nicht zwingend im Projektverzeichnis, also war
`$PWD` falsch, der Store wurde verfehlt und der Hook fiel in `emit_minimal`
(Ausgabe nur `{"continue": true}`, kein additionalContext). Fix: den vom
SessionStart-Payload getragenen `cwd` (S0-a) auswerten; Vorrang
`CLAUDE_PROJECT_DIR > payload.cwd > $PWD`, Windows-Backslashes normalisiert.
Testsuite 26 Checks (+payload-cwd, +precedence, +win-cwd Backslash-Realfall).
Erklaert, warum headless (dort ist `$PWD` = Projekt) funktionierte, interaktiv
nicht. NOTE: bestaetigt nur die Store-Aufloesung; ob interaktives Codex
additionalContext ueberhaupt ingestiert, bleibt der offene T-25-Retest.

## [2026-07-17] Release v4.9.2 — T-24 Fix: Codex-Briefing UTF-8 (Windows-Mojibake)

Pre-Flight zu T-25 (User-Sichtpruefung) deckte auf: `codex-session-briefing.sh`
lieferte auf Windows durchgaengig Mojibake — der JSON-Escaping-Schritt las stdin
per `sys.stdin.read()` mit cp1252 statt utf-8, wodurch alle Umlaute/Sonderzeichen
(ä/ö/ü/ß, Em-Dash, Pfeil) doppelt kodiert in `additionalContext` landeten
(genau die Windows-Subprocess-Falle aus der globalen CLAUDE.md). Fix: Bytes
explizit als utf-8 dekodieren (`sys.stdin.buffer.read().decode('utf-8','replace')`);
`print()` bleibt ASCII-safe, weil `json.dumps` per Default ensure_ascii ausgibt.
Testsuite auf 22 Checks erweitert (fixture-freier UTF-8-Integritaetstest gegen
den statischen Em-Dash-Header). Betrifft nur den Codex-Briefing-Pfad;
Claude-Verhalten unveraendert.

## [2026-07-16] Release v4.9.1 — T-24 Hotfix: Codex-Briefing-Ausgabeschema

Rollout-Verifikation zeigte: Codex ingestiert bei SessionStart-Hooks NUR
`hookSpecificOutput.additionalContext` (oder rohen stdout) — `systemMessage`
wird ignoriert. codex-session-briefing.sh auf additionalContext umgestellt
(Claude-kompatibel), Testsuite auf 21 Checks erweitert.

S0-e-Korrektur aus dem 4.9.0-Deploy: der Trust-Hash wird ueber die
UNAUFGELOESTE Hook-Definition gebildet — Plugin-Updates brechen den Trust nur,
wenn sich hooks.json selbst aendert. codex-hook-trust.py bleibt trotzdem
idempotenter Deploy-Check ("Nichts zu tun" kostet nichts).

---

## [2026-07-16] Release v4.9.0 — T-24 Codex-Session-Lifecycle

Interaktive (und headless) Codex-Sessions bekommen Bootstrap-Briefing +
Dirty-Tracking ueber dieselben Plugin-Hooks wie Claude. Design + S0-Findings:
membrain/memcodexlifecycle.md.

- **scripts/codex-session-briefing.sh (neu):** schmales Codex-Briefing
  (zentraler Handoff, open-tasks, Atlas-Hinweis); KEIN Auto-Init, exit immer 0,
  stderr leer; Escape-Hatch `AGENTIC_OS_CODEX_HEADLESS=1`.
- **session-start.sh:** Routing-Zweig — liegt das Skript unter `/.codex/`
  (Codex-Plugin-Cache), wird nur das Codex-Briefing gefahren; Claude-Pfad
  unveraendert (Testsuite).
- **posttooluse-dirty-tracker.py:** `agent`-Feld (codex/claude via Skriptpfad);
  `apply_patch` getrackt — Pfade aus dem Patch-Text geparst (Codex liefert kein
  file_path); hooks.json-Matcher entsprechend erweitert.
- **session-bootstrap SKILL.md:** RECOVERY-Zeile traegt Codex-Praefix bei
  `agent: "codex"`.
- **scripts/codex-hook-trust.py (neu, Deploy-Werkzeug):** trusted + enabled
  die 2 agentic-os-Hooks in Codex via app-server (`hooks/list` +
  `config/batchWrite`). NACH JEDEM PLUGIN-UPDATE AUSFUEHREN — der versionierte
  Cache-Pfad im Hook-Command bricht den Trust bei jedem Update (S0-e).
- Tests: test-codex-session-briefing.sh (20 Checks, in run-all.sh verdrahtet),
  dirty-tracker-Suite auf 20 Checks erweitert.

---

## [2026-07-15] Release v4.6.1 — Verifier-Findings an der Rueckfluss-Bruecke

Codex-Verifier-Review von 3fa7751: FAIL, 5 Major + 1 Minor — alle eingearbeitet
(P2 bestaetigt sich 6/6; erster Lauf des rueckfluss-delta-gates auf sich selbst).

- **Major 1 (Writer-Widerspruch):** pattern-extractor-Kanon von "only writer"
  auf "sole creator + schema owner" praezisiert; autorisierte Field-GAIN-Writer
  explizit benannt (obsidian-sync: promotion_status/promotion_scope; Haupt-
  Session: implemented_by/validated_by + Daten per Step 6.6). DEPENDENCIES-
  Matrix beide Zeilen nachgezogen.
- **Major 2 (Store-Ownership):** Step 6.6 schreibt Delta-Entwuerfe nur noch
  nach open-tasks.json (Authority-Trias: Tasks darf jeder Agent schreiben);
  Architektur-Entscheide routen ueber context-keeper, decisions.json wird nie
  direkt geschrieben.
- **Major 3 (Zeitregel unpruefbar):** implemented_at/validated_at (ISO, null-
  Default) neben den Ref-Listen kanonisiert; Vergleichsregel: validated_at
  darf implemented_at nicht vorausgehen + Evidenz aus anderer Session.
- **Major 4 (Idempotenz):** Step 6.6 prueft delta_task_id VOR jedem Draft und
  markiert Patterns mit delta_task_id + delta_drafted_at (analog Step 6.5
  generated_skill); vager Trigger ("clearly implies") gestrichen — nur noch
  explizit benannte Komponenten, sonst Unmatched.
- **Major 5 (Taxonomie erreichte Report nicht):** memory-audit Step-4-Template
  um FINDINGS-Block mit GAP-CLASS-Spalte ergaenzt.
- **Minor 1 (Test zu lasch):** Contract-Test section-scoped (awk-Extraktion je
  Step), Kopplungs-Checks (Scope-Bedingungen beidseitig, Idempotenz-Marker,
  Ownership-Routing, GAP CLASS im Report), Template-Bindung der neuen Felder.

## [2026-07-15] Release v4.6.0 — Rueckfluss-Bruecke Pattern→Verhaltensaenderung (membrain T-15/T-17)

Loop-8-Ernte (membrain memloop8harvest.md, Rosinen 1+3+4): schliesst die letzte
grosse Luecke des Gedaechtnis-Kreislaufs — vom bestaetigten Pattern zur
validierten Aenderung an BESTEHENDEN Komponenten.

- **pattern-extractor (3.1):** kanonisches Schema um `implemented_by` +
  `validated_by` erweitert (ehrliche Leerlisten, Legacy-Eintraege ohne Felder
  bleiben gueltig — gleicher Kontrakt wie derived_from/review_after in v4.4.0).
  Neuer Step 6.6 (rueckfluss-delta-gate): Patterns mit conf>=0.7, deren
  recommendation eine bestehende Komponente betrifft, erzeugen einen
  4-Zeilen-Delta-Entwurf (Affected component / Observed problem / Proposed
  change / Acceptance check) als Task/Decision — NIE eine Auto-Aenderung.
  implemented_by erst nach gelandetem Change; validated_by nur aus Evidenz,
  die NACH implemented_by datiert (die implementierende Session validiert
  sich nie selbst). Herkunftskette geschlossen: iteration → learning
  (derived_from) → pattern (evidence) → change (implemented_by) → effect
  (validated_by).
- **obsidian-sync (1.4), Scope-Gate:** promotion_status "ready" traegt jetzt
  `promotion_scope` — "global" nur bei source_projects >= 2, sonst "project"
  (Haeufigkeit allein beweist keine Uebertragbarkeit); Wiki-Promotion muss
  den Scope respektieren (projektgebundene Notiz statt Konzeptseite).
- **memory-audit (Command), Luecken-Taxonomie:** Step 3.5 klassifiziert jeden
  Befund in 7 Klassen (knowledge/capture/index/retrieval/link/usage/
  feedback-loop-gap) + Diagnose-Regel "zero-hit nach spaetem Filter beweist
  keine Wissensluecke" (L23-Fehlerklasse).
- **Test:** tests/test-pattern-rueckfluss-contract.sh (TDD, RED verifiziert),
  in run-all.sh registriert.

## [2026-07-15] Release v4.5.1 — Verifier-Findings am Decision-Promotion-Release

Codex-Verifier-Review von 9a3dbc3: FAIL, 2 Major + 2 Minor — alle eingearbeitet
(P2 bestaetigt sich 5/5).

- **context-keeper (3.2), Major 1:** zwei Regel-Widersprueche aufgeloest, die den
  Marker-Writeback regelkonform verhinderbar machten — decisions.json-Modus ist
  jetzt "Append + field-extend" (Records duerfen Felder nur GEWINNEN:
  status/wiki_ref/promoted_at), und die "nur context/"-Verbotsregel nennt den
  Step-3.5-Wiki-Writeback als dokumentierte Ausnahme.
- **obsidian-sync (1.3), Major 2:** Idempotenz-Fehlerfenster geschlossen —
  Duplicate guard vor jedem Append (existiert schon ein `### {id}:`-Block, wird
  nicht erneut angehaengt, sondern nur der fehlende Marker nachgetragen;
  self-healing fuer den Fall Wiki-Write ok / Marker-Write fail).
- **Contract-Test, Minor 3+4:** Step-4.5-Checks auf die Sektion gescoped (awk),
  promoted_at + "must NOT set the marker" + successful-Write-Bindung auch fuer
  context-keeper geprueft, neue Tokens (Duplicate guard, self-healing,
  status active, entity-creation exception), Stale-Ref-Negativ-Check
  whitespace-normalisiert (tr) gegen Zeilenumbruch-Maskierung.

## [2026-07-15] Release v4.5.0 — Decision-Promotion (decisions.json → Wiki-Projektion)

membrain Schnitt 5 (T-3): `context/decisions.json` bleibt der fuehrende
Entscheidungsspeicher; das Wiki erhaelt erstmals eine mechanisch idempotente
Batch-Projektion.

- **obsidian-sync (1.2), neuer Step 4.5 Decision Promotion:** promotet
  architekturrelevante Decisions (`architecture-decision`/`stack-change`,
  status active, ohne `wiki_ref`) als Kompakt-Bloecke in die Sektion
  `## Architecture Decisions` des Projekt-Entities; legt bei Bedarf ein
  minimales Entity an (einzige Entity-Erzeugungs-Ausnahme, wird im Report
  ausgewiesen). Idempotenz via Write-back-Marker `wiki_ref` + `promoted_at`
  im decisions.json-Record (reine Feld-Erweiterung). Supersede-Pflege:
  Status-Zeile im Wiki wird markiert, nie geloescht. Stalen Verweis
  "context-keeper Step 4.5" in Step 4 korrigiert (real: Step 3.5).
- **context-keeper (3.1), Step 3.5:** setzt beim Live-Writeback denselben
  Marker, damit Batch- und Live-Pfad sich nicht doppeln; fehlgeschlagener
  Wiki-Write setzt nie einen Marker.
- **Tests:** neuer Contract-Test `test-obsidian-sync-decision-promotion.sh`
  (beide Writer, Marker-Felder, Typ-Filter, Projektion-Richtung, Stale-Ref-Guard);
  in run-all.sh registriert.

## [2026-07-15] Release v4.4.1 — Recovery-Falsch-Positiv nach Wrap-up-Tail-Writes behoben

Organischer T-1-Befund (membrain): Nach einem sauberen wrap-up schrieben Session-Ende-
Writes AUSSERHALB von `.agent-memory/` (native Claude-Memory, Handoff-Dateien) das
Dirty-File erneut — der Hook clobberte dabei `consolidated_at` auf `null`, und der
naechste Bootstrap meldete die laengst konsolidierte Session als RECOVERY-Kandidat.

- **Hook (`posttooluse-dirty-tracker.py`):** Re-Dirty bewahrt die Konsolidierungs-
  Tatsache — `consolidated_at/by` wandern nach `last_consolidated_at/by`, neuer
  Zaehler `writes_since_consolidation`. 3 neue Tests (J/K/L, jetzt 15).
- **session-bootstrap (recovery-detect, neue Regel 4b):** Dirty-File mit
  `last_consolidated_at`, `writes_since_consolidation <= 5` und session_id im
  Marker → Downgrade auf Ein-Zeilen-Notiz statt RECOVERY-Block. >5 Writes seit
  Konsolidierung = echte Arbeit nach wrap-up → weiterhin voller Block.
- **session-start.sh:** mechanischer Check ueberspringt Dirty-Files mit
  `writes_since_consolidation <= 5` (sed-Extraktion, Feld existiert nur nach
  Konsolidierung).
- **wrap-up Step 9.5:** Self-Healing-Regel dokumentiert die bewahrte Historie.

**T-9-Rest — Pre-Run Commit (backup light):** memory-maintenance (1.2) und
obsidian-sync (1.1) haben einen neuen Step 0: Ist `.agent-memory/` git-versioniert,
wird der Store VOR jedem mutierenden Lauf chirurgisch committet
(`git add .agent-memory`, nie `-A`) — Ein-Kommando-Rollback via
`git checkout {hash} -- .agent-memory`. Fail-open: fehlender Snapshot blockt nie.

**Codex-Verifier-Fixes (Review nach Erst-Commit, 2 Major + 2 Minor):**

- Regel 4b verschaerft: Downgrade zusaetzlich nur bei `updated` <= 15 min nach
  `last_consolidated_at` (echte Nacharbeit mit wenigen Writes + Crash bleibt
  voller RECOVERY-Block); Downgrade-Notiz muss Zaehler + Tail-Dateien nennen.
- session-start.sh: Skip verlangt jetzt auch `last_consolidated_at`-Praesenz —
  ein einsamer Zaehler in korruptem State verschluckt keine Recovery mehr.
- Hook: `_safe_count()` fuer writes_since_consolidation UND write_count — ein
  korrupter Zaehlerwert ("kaputt", Liste) legte das Tracking sonst dauerhaft
  still (int() warf, Fail-soft-Catch schluckte, jeder Folge-Aufruf scheiterte
  erneut; gleiche Fehlerklasse wie E4/P3). Test M (jetzt 16).

---

## [2026-07-14] Release v4.3.0 — Crash-sichere Konsolidierung: Dirty-Tracker, Marker, Recovery

Erster Schnitt aus der membrain-Gedaechtnis-Spezifikation (Realitaets-Abgleich
`membrain/memrealitycheck.md`): Der Uebergang Session → Abschluss → naechster Start
haengt nicht mehr allein an Disziplin bzw. Best-Effort-Prompt-Hooks.

**Neu — PostToolUse Dirty-State-Tracker (Hook 7, `posttooluse-dirty-tracker.py`):**

- Schreibt nach jedem erfolgreichen Write/Edit/MultiEdit/NotebookEdit ausserhalb von
  `.agent-memory/` mechanisch `working/dirty-<session_id>.json` (`dirty: true`,
  `touched_files` max 200, `write_count`, Timestamps). Kein LLM, rein mechanisch.
- Fail-soft-Kontrakt: jeder Fehler → stiller No-op, immer Exit 0; atomare Writes
  (tmp + `os.replace`); Skips: `.agent-memory/`-Pfade, Claude-Scratchpad
  (`AppData/Local/Temp/claude/`), `.git/`. Session-Dateien pro session_id →
  parallele Sessions kollidieren nicht.
- 12 Tests in `tests/test-posttooluse-dirty-tracker.py` (in run-all.sh eingehaengt):
  create, dedup, Skips absolut/relativ/case-insensitiv, relativer Work-Pfad, no-store,
  korrupte Datei, garbage stdin, fremdes Tool, Re-Dirty-Self-Healing, Scratchpad-Skip.

**Codex-Verifier-Fixes (Review nach Erst-Commit):** Skip-Marker greifen jetzt auch bei
relativen Pfaden + case-insensitiv (Windows); session-start.sh nutzt while-read statt
unquoted for-loop (Projektpfade mit Leerzeichen); README/CLAUDE.md auf 7 Hooks/v4.3.0
aktualisiert (Design-Prinzip ehrlich angepasst: einziger Per-Edit-Hook ist der
mechanische Dirty-Tracker).

**wrap-up 4.0 → 4.1 — Konsolidierungsmarker (`consolidation-marker`):**

- Step 1 liest `working/dirty-*.json` als harte Evidenz; Step 1.5 harvestet auch
  fremde/verwaiste Dirty-Sessions (`recovered from session {id}`), erfindet nichts.
- Neuer Step 9.5: schreibt `.agent-memory/consolidation-marker.json`
  (last_wrapup, consolidated_sessions, Zaehler), setzt konsumierte Dirty-Files auf
  `dirty: false` + `consolidated_at/by`. Loescht nie (Archivierung ist
  memory-maintenance). Bei unvollstaendigem wrap-up: kein Marker, kein Reset —
  ehrlicher Dirty-State ist die Recovery-Grundlage. Self-Healing-Regel: konsolidierte
  Parallel-Session re-dirtied sich beim naechsten Write selbst.

**session-bootstrap 3.0 → 3.1 — Recovery Detection (`recovery-detect`, read-only):**

- Dirty-Files mit `dirty: true` und `updated` aelter als 30 Minuten → RECOVERY-Block
  im Briefing (Session-ID, Writes, Beispieldateien, wrap-up-Empfehlung). Juengere
  Dateien = vermutlich laufende Parallel-Session, nie flaggen. Marker-Cross-Check
  gegen False Positives. Bootstrap bleibt strikt read-only.

**session-start.sh v3 → v4:** mechanischer Grep-Check derselben Bedingung direkt im
SessionStart-Hook — die RECOVERY-Zeile erscheint auch, wenn niemand session-bootstrap
aufruft. Live getestet (alte Dirty-Datei erkannt, frische ignoriert).

**memory-maintenance:** archiviert nur konsolidierte Dirty-Files (`dirty: false`,
aelter 7 Tage); `dirty: true` ist Recovery-Evidenz und wird NIE geloescht.

Suite: 49 PreToolUse-Tests + wrap-up-Memory-Contract weiterhin gruen.

---

## [2026-07-06] Release v4.0.0 — Konsolidierung: 9 Skills, Identity-Growth-Fixes, Token-Diaet, Threshold-SSoT

**BREAKING — Komponenten entfernt:**

- **5 Skills geloescht:** `retrospective` (Trend-Report ohne Konsumenten), `research-pipeline`
  (User-Level-Skills decken das besser), `wiki-query` (Wiki-MCP/direktes Read reicht),
  `quality-gate` (Review/TDD wandert zu User-Level-Skills + Test-Suite),
  `skill-generator` (in `pattern-extractor` Step 6.5 gefaltet — der alleinige
  patterns.json-Writer generiert jetzt selbst die Skills seiner Kandidaten). 14 → 9 Skills.
- **1 Agent geloescht:** `quality-gate`. 4 → 3 Agents (context-detective, improvement-agent, research-agent).
- **5 Wrapper-Commands geloescht:** `/log`, `/patterns`, `/research`, `/sync`, `/run-loop` —
  duenne Wrapper um direkt invocierbare Skills (Schatten-Risiko L17). 10 → 5 Commands
  (init, status, rollback, auto-commit, memory-audit).

**Identity-Growth-Fixes (Pipeline verhungerte monatelang still):**

- wrap-up Step 6.5: **Pflicht-Statuszeile** `(identity-visible)` — Identity-Growth skippt nie
  mehr still; jede wrap-up-Ausgabe enthaelt genau eine Identity-Zeile.
- Step 6.3 **Queue-Re-Review** `(queue-re-review)`: JEDER Kandidat in user-candidates.json wird
  reviewt, nicht nur die dieser Session (enqueue-only liess Promotions wochenlang liegen).
- Step 6.1 **Harvest-Checkliste** `(identity-harvest)`: konkreter 5-Punkte-Scan (Korrekturen,
  explizite Regeln, Workflow-Gewohnheiten, bestaetigte Ansaetze, Kommunikations-Anforderungen)
  statt vagem "scan the session".
- Step 6.4 **Eskalationspfad** `(escalation-path)`: promotete user.md-Eintraege, die 2+ Sessions
  re-bestaetigt wurden oder Agent-Verhalten beschreiben, eskalieren nach soul-candidates.md.
- SessionStart-Hook injiziert jetzt **identity/user.md** in den Session-Kontext.

**Token-Diaet (Descriptions/Prompts landen permanent in jedem System-Prompt):**

- Hook-Prompts gestrafft; Skill-/Agent-Descriptions auf ~50-60 Woerter gekuerzt
  (`<example>`-Bloecke raus, Trigger auf die 5-6 wichtigsten reduziert, Englisch erhalten).
- session-bootstrap: Read-Deckelung (Learnings via RAG/`scripts/learnings_top.py` statt
  Full-Read, errors.json nur Tail, Entity-Seiten max 80 Zeilen).

**Neue SSoT-Skripte:**

- `scripts/memory-thresholds.sh` — EINZIGE Definition aller Skalierungs-Schwellen
  (exit 10 bei Ueberschreitung). Konsumiert von session-bootstrap Step 3, wrap-up Step 9,
  memory-maintenance Step 3. Skill-Bodies nennen keine Zahlen mehr (iteration-loggers
  widerspruechliche 500/200er-Rotation entfernt).
- `scripts/learnings_top.py` — deterministisches Salience-Ranking
  (`importance*0.4 + recency*0.3 + tag_overlap*0.3`) fuer den Bootstrap-Fallback.
- `skills/wrap-up/references/handoff-template.md` — SSoT fuer den Cross-Project-Handoff
  (Prepend-Algorithmus, Dedup, Hard-Cap, Templates), von wrap-up Step 7.6 gelesen.
- memory-maintenance Step 3b: raeumt `working/`-Leichen auf (*.py/*.tmp/*.bak aelter 7 Tage;
  current-session.json + user-candidates.json ausgenommen) — bisher fuehlte sich kein Skill
  zustaendig.

Doku nachgezogen: plugin.json (4.0.0, 9 Skills), CLAUDE.md, DEPENDENCIES.md (v4-Datenfluss +
Removed-Begruendungen), PROJECT/CAPABILITIES/ARCHITECTURE.

## [2026-06-27] Release v3.9.0 — Wiki-Session-Summary-Auto-Sync gehaertet

Die Auto-Wiki-Session-Zusammenfassung (SessionEnd-Hook → wrap-up Step 7.5 → obsidian-sync)
existierte vollstaendig, feuerte aber unzuverlaessig: bei normal beendeten Sessions verfehlte
das Gate einzelne reale Iterationen, und ein nicht erfuelltes Gate fuehrte zu einem **stillen
Skip ohne jede Ausgabe** — wodurch sich das Feature "kaputt" anfuehlte (agentic-os-plugin
selbst hatte seit 2026-06-12 keine Wiki-Note mehr, obwohl mehrere Releases dazwischenlagen).
Drei Haertungs-Hebel, alle innerhalb der bestehenden Kette, je strip→FAIL-verifiziert (L11):

- **(wiki-sync-visible):** wrap-up Step 7.5 und obsidian-sync skippen nie mehr still — in
  jedem Fall eine sichtbare Status-Zeile (`Note geschrieben → …` / `übersprungen — {Grund}` /
  `fehlgeschlagen — …`). Der stille Skip war die Hauptursache des "passiert nichts"-Eindrucks.
- **(wiki-sync-gate):** Substanzialitaets-Gate gelockert von `>= session_note_threshold (2)`
  auf "ANY: >= 1 Iteration heute ODER heutige Commits ODER importance≥4-Learning ODER neue
  Decision". Eine einzelne echte Iteration / ein Commit rechtfertigt bereits eine Note;
  in wrap-up UND obsidian-sync identisch verankert.
- **(sessionend-wiki-verify):** Der SessionEnd-Hook ist jetzt Backstop — bei sync-enabled +
  substanzieller Session prueft er, ob eine heutige `wiki/queries/{date}-session-{project}*`-Note
  existiert, und ruft sonst obsidian-sync nach. Bleibt reine Delegation (keine dupl. Schreiblogik).

**Bekannte Grenze (bewusst):** Eine plugin-interne Haertung greift NICHT bei hart
abgebrochenen Sessions (Crash) oder headless-Laeufen mit `disableAllHooks` (autonome
Bridge-Runs) — dort feuert kein Hook. Das deckte ein optionaler deterministischer
command-Hook mit git-Fallback-Stub ab (nicht umgesetzt, bewusst verworfen).

Tests: +5 Marker-Tests (4 in validate-skills.sh, 1 in validate-plugin.sh), alle Suiten gruen.

## [2026-06-24] Release v3.8.0 — Eval-driven self-improve (lever 6) + retrospective skill (14.)

Zwei Mechanismen aus der v1.0-"self-improving-agent"-Urgeneration ins aktuelle Plugin
ueberfuehrt (die anderen fuenf Urvaeter-Skills waren bereits — oft moderner — aufgegangen):

- **self-improve lever 6 (Eval-Driven Acceptance Gate):** Phase 0.4 legt pro Ziel-Skill ein
  binaeres Eval-Set an (`improvements/evals/<skill>.eval.json`); Phase 4.2b scort die Mutation
  vor/nach gegen dieses Set und rollbackt bei `EVAL-REGRESSION` (gesunkener Eval-Score) —
  unabhaengig davon, ob die Test-Suite gruen ist. Haertet die bisher weiche, subjektive
  Phase-4.2-Quality-Pruefung (lever 5 schuetzt die Suite, lever 6 den Skill-Kontrakt).
  Verworfene Mutationen landen als Research Asset in `improvements/evals/failed/`.
- **retrospective (14. Skill, quality-Layer):** Multi-Session-Trend-Metriken (Effizienz,
  Qualitaet, Lernen, Wachstum), Blind-Spot-Analyse und Health-Grade, read-only ueber den
  Store, schreibt nur `retrospectives/`. In die bootstrap+wrap-up-Klammer eingehaengt
  (wrap-up Step 10, periodisch: Metriken >7d alt ODER 5+ neue Iterationen) — sonst toter
  Code mit gruener Suite (L19).

Beide TDD-abgesichert (RED→GREEN, Marker `(lever 6)` / `(periodic-retrospective)`,
bidirektional verifiziert, L11). Skill-Count 13→14 ueber alle Manifeste/Doku gezogen
(plugin.json, marketplace.json, README, CLAUDE.md, ARCHITECTURE, CAPABILITIES,
skill-template, DEPENDENCIES). Versions-Bump `3.7.0` → `3.8.0`.

## [2026-06-21] Release v3.7.0 — Skill-Datenfluss-Fixes + Versions-Bump

Versions-Bump `3.6.0` → `3.7.0`. Buendelt die seit 3.6.0 angesammelte Feature-Arbeit
(PreToolUse Shell-Circuit-Breaker-Hook, die `verified_scanner`/`generate_watermark`/
`refresh_verify_status`-Tool-Pipeline, quality-gate-Tool-Signal, wrap-up Long-Term-Memory-
Routine) zu einem Release und ergaenzt zwei funktionale Skill-Fixes aus Self-Improve-
Iteration #81:

- **skill-generator** liest jetzt die kanonischen Pattern-Felder `evidence`/`recommendation`
  statt der Legacy-Namen `error_ids`/`recommended_action`/`avoid`, die pattern-extractor
  (alleiniger Writer) wegnormalisiert — repariert die Pattern→Skill-Generierungs-Pipeline.
- **obsidian-sync** Rolling-Synthesis gated jetzt auf `importance >= 4` (learnings.json-
  Schema, wie wrap-ups eigener Trigger) statt auf das nie geschriebene Feld `salience`.

Beide mit RED→GREEN-Tests in `validate-skills.sh` abgesichert.

## [2026-06-13] PreToolUse Shell-Circuit-Breaker

Neuer command-basierter `PreToolUse`-Hook fuer `Bash`: `scripts/pretooluse-shell-circuit-breaker.sh`
liest das Claude-Code-Hook-Payload von stdin, extrahiert den Shell-Befehl und blockiert
bekannte Hochrisiko-Aktionen deterministisch mit Exit-Code `2`. Abgedeckt sind unter
anderem rekursives Forced-Delete, `git reset --hard`, `git clean -fd*`, Remote-Script-Pipes,
PowerShell-Download-Cradles, Disk-/Shutdown-Kommandos sowie rekursive Rechte-/Owner-Aenderungen.

`hooks/hooks.json`, README, Scripts-Doku und `docs/CAPABILITIES.md` dokumentieren die neue
sechste Hook-Flaeche. Neuer Funktionstest `tests/test-pretooluse-shell-circuit-breaker.sh`
prueft Allow-/Block-Faelle inklusive Exit-Code `2` und ist in `tests/run-all.sh` eingebunden.

## [2026-06-13] Refresh Verify Status Wrapper

Neues Python-Artefakt `tools/refresh_verify_status.py` mit `main`: ruft den
Verified-Scanner und den README-Watermark-Generator nacheinander auf. Der
Standardaufruf `python tools/refresh_verify_status.py` scannt `docs/`, nimmt das
aelteste `verified: YYYY-MM-DD` als Watermark-Datum und aktualisiert `README.md`.

`--dry-run` erzeugt eine Diff-Preview ohne Schreibzugriff. Die Exit-Codes sind
dokumentiert: `0` bei Erfolg, `1` bei fatalen Eingabefehlern und `2` bei
recoverable Skip-Faellen wie fehlendem `docs/`, fehlenden Markdown-Dokumenten
oder keinen gueltigen `verified:`-Zeilen. CI-Integration bleibt Folge-Sprint.

Tests: neuer Integrationstest in `tests/test_refresh_verify_status.py` mit
Temp-Repo-Mock fuer README-Update, Dry-Run und recoverable Skip-Faelle.

## [2026-06-13] README-Wasserscheide-Anzeige

Neues Python-Artefakt `tools/generate_watermark.py` mit
`inject_verify_watermark(readme_path, min_date)`: schreibt die README-Konvention
`<!-- Doku verifiziert bis: YYYY-MM-DD -->` als einzelne Watermark-Zeile direkt nach
dem ersten H1-Heading oder, falls kein H1 existiert, am Dateianfang. Bestehende
Watermarks werden ersetzt und Duplikate auf eine kanonische Zeile reduziert.

Tests: neue Python-Unit-Tests in `tests/test_generate_watermark.py` fuer
Idempotenz, Datums-Updates, Duplikat-Bereinigung und README-Dateien ohne H1.

## [2026-06-13] Verified-Frontmatter-Scanner

Neues Python-Artefakt `tools/verified_scanner.py` mit
`find_min_verified_date(docs_root)`: traversiert Markdown-Dokumentation rekursiv,
extrahiert robuste `verified: YYYY-MM-DD`-Varianten, validiert ISO-Daten und gibt
`{"min_date": "...", "entries": [...]}` zurueck. Die Doku-Wurzel kann direkt uebergeben
oder per `VERIFIED_SCANNER_DOCS_ROOT` gesetzt werden; leere Verzeichnisse, fehlende
Treffer, malformed Daten und unlesbare Dateien erzeugen keine Exception.

Tests: neue Python-Unit-Tests in `tests/test_verified_scanner.py`, eingebunden in
`tests/run-all.sh`.

## [2026-06-12] Session-Bracket-Coverage: wrap-up Session-Harvest + Decision-Scan (v3.6.0)

Der minimal unterstuetzte Workflow ist die Zwei-Aufruf-Klammer (bootstrap am Anfang, wrap-up
am Ende, dazwischen nichts). Befund: die Arbeitsphasen-Kette iteration-logger →
pattern-extractor → skill-generator hing komplett an manuellen `/log`-Aufrufen — wer nur die
Klammer nutzt, fuetterte die Pattern-Pipeline NIE (Live-Beweis: 5 Iterationen, 3 Errors,
Quality-Score null nach Monaten). Zwei neue wrap-up-Schritte schliessen das:

- **Step 1.5 Session-Harvest `(session-harvest)`:** hat iteration-log.md keine heutigen
  Eintraege, rekonstruiert wrap-up die Iterationen der Session (Konversation + git log) und
  delegiert pro Iteration an iteration-logger (1-5 distinct iterations, Counting-Rule).
  Schreibrechte unveraendert: iteration-logger bleibt einziger Writer von iteration-log.md/
  errors.json. Danach Re-Gather, damit Step 4 (pattern-extractor ab 3+) echte Daten sieht.
- **Step 4.5 Decision-Scan `(decision-scan)`:** Architektur-/Stack-/Policy-Entscheidungen
  der Session werden erkannt und an context-keeper delegiert (decisions.json blieb sonst
  leer, weil niemand "record decision" sagt). Trust boundary: conversation+repo only.
- **DEPENDENCIES.md:** Execution-Order + Matrix + Prinzip 4 nachgezogen; neue Sektion
  "Session-Bracket Coverage" dokumentiert, was die Klammer abdeckt und was bewusst
  on-demand bleibt (quality-gate voll, sync-context, self-improve, research-pipeline,
  wiki-query, skill-generator-Erzeugung).
- **Guard-Tests (TDD, erst rot 4×):** validate-plugin.sh bindet beide Marker-Bloecke an die
  Delegation (iteration-logger/context-keeper), die Write-Ownership-Klausel und die
  Graph-Doku (181/185 rot → 185/185 gruen).
- **Bugfix sharepoint-pull-check.ps1:** Handoff-Dateien ohne `target_agent`-Frontmatter
  (z.B. INDEX.md) warfen "Index auf NULL-Array" (live im Bootstrap 2026-06-12). Fix:
  Get-FmField-Guard statt Direktzugriff auf .Matches.Groups; gegen echten Sharepoint
  verifiziert; in den 3.5.1-Cache gespiegelt (byte-diff ok), regulaeres Deploy mit 3.6.0.

Tests gruen: 185 validate-plugin (+4 Bracket-Guards auf Basis 181) +
165 validate-skills + 19 global-schema.

## [2026-06-12] Fix: Command/Skill-Namensschatten entfernt (v3.5.1)

Ein Command mit demselben Namen wie ein Skill beschattet den Skill im Skill-Tool: der Aufruf
`agentic-os:wrap-up` lieferte den COMMAND-Wrapper zurueck (der wiederum "invoke the skill" sagt)
statt des Skill-Bodys — Endlos-Indirektion (L17, live beobachtet 2026-06-12, zwei identische
Versuche). Betroffen waren genau die zwei Wrapper, deren Name mit einem Skill kollidierte:
`commands/wrap-up.md` und `commands/quality-gate.md`.

- **Fix:** beide Wrapper-Commands GELOESCHT (nicht umbenannt). Skills sind direkt
  slash-invocierbar (ground-truth: `/agentic-os:session-bootstrap` laeuft ohne Command-Wrapper)
  — `/agentic-os:wrap-up` und `/agentic-os:quality-gate` funktionieren weiter und treffen jetzt
  direkt den Skill. Umbenennen haette den Schatten nur verschoben.
- **Guard-Test (TDD, erst rot):** validate-plugin.sh prueft, dass KEIN `commands/<name>.md` ein
  `skills/<name>/`-Verzeichnis spiegelt; fing vor der Loeschung beide Kollisionen.
- Doku nachgezogen: CLAUDE.md (10 Commands + Schatten-Verbot), PROJECT.md, CAPABILITIES bleibt
  unveraendert (listete Commands nie), architecture-map.html Sektion 2 (5 Wrapper / 5 Inline /
  8 Skills ohne Command).
- 12 → 10 Slash-Commands. hooks.json (SessionEnd "invoke agentic-os:wrap-up") loest jetzt
  eindeutig zum Skill auf — unveraendert gelassen.

Tests gruen: 180 validate-plugin (−6 Frontmatter-Checks der geloeschten Dateien, +1 Guard) +
165 validate-skills + 19 global-schema.

## [2026-06-12] Handoff-Ownership — lokale vs. globale Uebergaben (v3.5.0)

Next Steps leben jetzt genau einmal — projekt-lokal in `context/open-tasks.json` (neuer
wrap-up **Step 5.5**, SSoT mit `{id,title,status,created,updated,source,cross_project}`; die Hooks erwarteten das
Schema schon, nur schrieb es bisher kein Skill systematisch). Der zentrale Handoff
(`~/AI/.agent-memory/session-summary.md`) haelt max. **1 Block pro Projekt** (7.6a
Ownership-Dedup, Regel 2.5) und **verweist** auf die lokale Quelle statt Next Steps zu
kopieren — inline nur noch `[cross-project]`-Punkte. session-bootstrap liest die lokale
SSoT zuerst (Step 6 Prioritaet gedreht, Dedup lokal-gewinnt). SESSION-WORKFLOW.md §3/§7
entsprechend angepasst (explizit User-genehmigt 2026-06-12, Aenderungsvermerk im Dokument).
Bestands-Handoff migriert (6 → 3 Bloecke, Gate-B-genehmigt, Backup `.bak-2026-06-12`).
4 neue Marker-Tests (`open-tasks-ssot`, `handoff-dedup`, `next-steps-pointer`,
`open-tasks-priority`), alle bidirektional strip→FAIL-verifiziert (L11).
Behebt: Next-Step-Duplikate (2–3x) durch gestapelte Session-Bloecke desselben Projekts.
Plan: `docs/plans/2026-06-12-handoff-ownership-master-plan.md`.

## [2026-06-03] Fix: qualitative Confidence in 4.A-Migration (v3.4.1)

`migrate-global-schema-4A.sh` stuerzte auf realen globalen Stores mit `ValueError: could not convert string to float: 'low'` ab: Legacy-Patterns tragen qualitative Confidence (`low`/`medium`/`high`) neben numerischen Werten, und die Promotion-Gate-Berechnung rief blind `float(out["confidence"])`. Beobachtet am Live-Store `~/.claude-memory/global/` (23 Patterns, 5 mit String-Confidence).

- **Fix:** `coerce_conf()` mappt `very low`/`low`/`medium`/`high`/`very high` → `0.1`/`0.3`/`0.5`/`0.8`/`0.9` (numerische Strings parsen weiterhin; Unbekanntes → Default `0.5`). `out["confidence"]` wird durchnormalisiert, sodass auch der gespeicherte Wert numerisch ist.
- **Regressionstest:** `test-global-schema.sh` erhaelt einen `confidence: "low"`-Pattern, der ohne Crash migrieren, zu `0.3` coercen und als `candidate` landen muss (4 neue Assertions).

Tests gruen: test-global-schema 19/19 (von 16), run-all ALL PASSED. Migration bleibt idempotent, `--dry-run`-Default, Backups `*.4A.bak`, row-count-invariant (in==out).

## [2026-06-03] Global Memory Layer 4.A — Provenance, Promotion, Decay, Privacy (v3.4.0)

Macht den globalen Cross-Project-Layer (`~/.claude-memory/global/`) von einem flachen Pattern-Store zu einem provenance-grounded, selektiv promotenden, alterungsfaehigen Gedaechtnis. Master-Plan: `Downloads/2026-06-03-global-memory-layer-4A-master-plan.md`. **Architektur-Entscheidung: Hybrid** — pure testbare Logik in `scripts/global-schema.sh` (sourcebar), Orchestrierung im sync-context-Prompt, damit die kritischen Invarianten echte strip→FAIL-Unit-Tests bekommen statt nur Marker-greps (L11). Durchgehend TDD, bidirektional verifiziert.

- **Phase 0 — Denylist + Helper (SSoT):** `MEM_GLOBAL_DENY_TAGS` in `mem-schema.sh` (credentials/pii/secrets); 5 pure Helfer in `scripts/global-schema.sh` (`normalize`, `compute_scope`, `passes_promotion_gate`, `apply_decay`, `is_denied`) mit echten Unit-Tests in neuer `tests/test-global-schema.sh` (in run-all.sh; final 16 nach dem Gate-Konsistenz-Fix).
- **Phase 1 — Provenance-Schema + Privacy-Pre-Filter:** sync-context Push stempelt `G-<type>-<n>`, `scope`, `valid_from`, `source_evidence`, `lifecycle`, `source_projects`. Privacy-Filter laeuft VOR dem Gate (denied tags / `signal_type:mood` erreichen den globalen Store nie). `migrate-global-schema-4A.sh` (idempotent, `--dry-run`-Default, Backups `*.4A.bak`).
- **Phase 2 — Promotion-Gate + Pull-Filter + Migration angewandt:** Promotion zu `active` nur bei `confidence≥0.6 ∧ occurrences≥3 ∧ |source_projects|≥2` (0.6-Schwelle woertlich erhalten); Pull serviert nur `lifecycle:active`. **Migration real angewandt:** 44 Eintraege (12 Patterns + 32 Learnings) → 44, 0 Verlust, alle Provenance-Felder gesetzt, `schema_version:4A`.
- **Phase 3 — Decay + Staleness-Wrap:** memory-maintenance Step 4b: globaler Decay −0.1/90 Tage ohne Recall, Floor 0.3, `lifecycle:archived` ab 365d (nie hartes Loeschen). session-bootstrap: read-only `[STALE? …]`-Anzeige >90d (kein Write — Decay bleibt Maintenance-Job).
- **Phase 4 — /memory-audit GLOBAL-Sicht:** read-only Report ueber un-migrierte Eintraege, promotion-gate-Verstoesse, decay-due — nennt den heilenden Skill, mutiert nie.

Tests gruen: validate-plugin 185/185, validate-skills 161/161, test-global-schema 16/16 (von 183/155). Boundaries gewahrt: sync-context manuell, bootstrap read-only, nie hartes Loeschen, Privacy-vor-Gate. Codex-Verifier-MINOR (Doku-Test-Count 14→16, durch den Gate-Konsistenz-Fix) behoben; Live-/memory-audit fand 35 promotion-gate-violations aus dem Migrations-Default → gate-konsistente lifecycle-Zuweisung nachgezogen.

## [2026-06-03] Memory-Audit restliche Hebel #3–#6 (v3.3.1)

Die nach Ground-Truth-Verifikation real verbliebenen Hebel aus dem Memory-Audit (nach Sprint #1+#2). Wiki-TODO: `2026-06-03-agentic-os-memory-growth-restliche-hebel`. Durchgehend TDD, marker-basierte bidirektional verifizierte Drift-Tests.

- **#3 patterns.json-Schema vereinheitlicht:** 3 divergierende Schemata → Kanon = `pattern-extractor` (der einzige Schreiber): `description`/`recommendation`/`evidence` + `severity`. Legacy-Normalisierungs-Tabelle im Skill (`solution`/`prevention`→`recommendation`, `source_errors`/`error_ids`→`evidence`, `name`/`title`→`description`, `pattern-001`→`P{n}`+`previous_id`) + Re-Dedup. Reale 4 Bestands-Einträge lokal mit-normalisiert.
- **#4 Recency-Supersession in sync-context:** Konflikt-Auflösung von Confidence-only auf Write-Time-Supersession umgestellt — neuerer Eintrag bleibt `active`, älterer → `lifecycle:superseded`+`superseded_by` (nie gelöscht), max 1 `active` pro `(type, scope)`. Behebt die Mem0-Interferenz (stale-high-confidence schlägt neu). Confidence rankt nur noch nicht-widersprechende Merges. `lifecycle`-Feld im pattern-extractor-Schema.
- **#5 `/memory-audit`-Command:** read-only Drift/Staleness/Provenance-Report über `.agent-memory/`. Verhindert genau die veraltete-Daten-Panne, die das manuelle Audit hatte (es maß 3 statt 10 learnings → Phantom-Gaps). Meldet nur, mutiert nie, nennt den heilenden Skill. 11 → 12 Slash-Commands.
- **#6 open-tasks-Drift-Trigger:** Heal-Mechanismus existierte (`memory-maintenance` Step 8.2), war aber threshold-gated → lief nie. Fix: Schritt 1.5 im SessionEnd-Hook (liest `context/open-tasks.json` ohnehin) erkennt stray Root-Datei, merged, löscht. Alt-Root-Datei (leer, seit 25. Mai) entfernt.

Tests gruen: validate-plugin 181/181, validate-skills 155/155. Codex-Verifier: durch.

## [2026-06-03] Memory Growth Engine — user.md + soul.md wachsen mit (v3.3.0)

Sprint #1+#2 aus dem Memory-Audit (`Downloads/agentic-os-memory-audit-2026-06.md`). Behebt, dass `user.md` nach 80 Iterationen noch der Init-Stub war und `soul.md` nicht mitwuchs — ohne die Sicherheits-Boundary zu brechen, dass nichts Untrusted autonom in die Agent-Identität schreibt. Master-Plan: `docs/plans/2026-06-03-memory-growth-engine-master-plan.md`. Durchgehend TDD, bidirektional verifizierte Drift-Tests.

- **Phase 0 — Schema (SSoT):** 3 neue Stores in `scripts/mem-schema.sh`: `working/user-candidates.json` (Präferenz-Queue), `identity/user-changelog.json` (Audit/Rollback), `identity/soul-candidates.md` (soul-Growth-Queue). RED-first via voller Datei-Liste in `validate-plugin.sh`.
- **Phase 1 — user.md Growth (wrap-up Step 6):** Toter "3+ Korrekturen"-Direct-Write ersetzt durch Kandidaten-Queue mit `observed/inferred/confirmed`-Klassifikation. Promotion nur `confirmed` ODER (`inferred` + occ≥2 + conf≥0.6). Schwelle 3→2 gesenkt. `signal:mood` wird NIE promoted. Jede Änderung → `user-changelog.json` VOR dem Write (Atomarität). **Trust-Boundary:** Kandidaten nur aus User-Konversation, nie aus web/docs/NotebookLM/Wiki (Memory-Poisoning-Schutz, Unit-42).
- **Phase 2 — soul.md Growth Stufe B (propose, don't commit):** `wrap-up` Step 6.5 sammelt Identitäts-Kandidaten in `soul-candidates.md` (nie Auto-Write). `session-bootstrap` Step 6.5 zeigt beim Start "SOUL CANDIDATES: n — [j/n]"; soul.md-Write NUR auf explizites `j` (die eine, präzisierte read-only-Ausnahme). `memory-maintenance`: 80-Zeilen-Anti-Bloat-Linter für soul.md.
- **6 neue Drift-Tests** (marker-basiert: `(user-growth)`/`(soul-growth)`/`(trust-boundary)` + Konzept-Phrase). Bidirektional verifiziert (strip→FAIL, restore→PASS); der trust-boundary-Test wurde nach erster zu lockerer Fassung gehärtet (gleiche Lehre wie bei den self-improve-Hebeln). Suiten gruen: validate-plugin 175/175, validate-skills 153/153.
- **DEPENDENCIES.md** nachgezogen (neue Stores + Schreiber + die bedingte bootstrap-Ausnahme).

## [2026-06-03] self-improve-Loop um 5 Haertungs-Hebel erweitert (v3.2.6)

Umsetzung des Wiki-TODO `2026-06-02-self-improve-mechanismus-haerten` (5 Hebel aus der 80-Iterationen-Retro). Reine Spec-/Prozess-Haertung am `self-improve`-SKILL.md-Body, manuell eingebaut (No-Self-Mod-Boundary, Policy 5 — der Loop editiert seinen eigenen Pfad nicht autonom). Jeder Hebel ist mit einem eindeutigen `(lever N)`-Marker im Body verankert und durch einen Drift-Test gepinnt.

- **Hebel 1 (Phase 3, groesster ROI):** Pre-Commit-Grep des gerade gefixten Musters ueber den ganzen Skill/Plugin-Tree — alle Vorkommen in derselben Iteration fixen statt nur die Erst-Fundstelle (haette ~6-8 Iterationen gespart: `tools:`->`allowed_tools:` iter 5/56, DE->EN iter 41/50, "10 skills" iter 32/52).
- **Hebel 2 (Circuit Breaker):** substanz-basierter Stopp — 3 Iterationen in Folge nur kosmetische Fixes (Sprache/Counts, kein funktionaler Bug) -> `SUBSTANCE-CONVERGENCE`-Pause. Plus `functional_fixes`/`cosmetic_fixes` im State-Eintrag. Fix-Count allein feuerte iter 35-54 nie.
- **Hebel 3 (Phase 2):** funktionale Analyse-Lens (Output-Gaps, Gate-Integritaet, Lifecycle-Dead-Ends, Control-Flow) — adressiert dass nur ~8% der Funde echte Logik-Bugs waren und die spaet/doppelt kamen.
- **Hebel 4 (Phase 4):** State<->.md-Atomaritaet — `.md`-Block vor State-Eintrag schreiben, `STATE-MD-DRIFT`-Konsistenz-Check + Backfill (iter 56-80 hatten keinen `.md`-Log).
- **Hebel 5 (Phase 0/4):** absoluter Baseline-Sanity-Check — Test-Zahl 0 oder auf <=Haelfte gefallen -> `BASELINE-SANITY`-Abort/Rollback, nicht nur das Per-Iteration-Delta (iter 64 hatte 0 Plugin-Tests, unbemerkt).
- **5 neue Drift-Tests** in `validate-skills.sh` (marker-basiert, bidirektional verifiziert: strip->5x FAIL, restore->5x PASS). Suiten gruen: validate-plugin 174/174, validate-skills 146/146 (war 141).

## [2026-06-02] DEPENDENCIES.md gegen Skill-Realitaet korrigiert + Inter-Skill-Call-Test (v3.2.5)

- `skills/DEPENDENCIES.md` vollstaendig gegen die 13 SKILL.md + 4 Agents neu gefasst: fehlende Reads/Writes ergaenzt (session-bootstrap Cross-Project + learnings.json + working/; wrap-up obsidian-sync-Aufruf Step 7.5 + Cross-Project-Handoff; context-keeper docs-als-SoT + Wiki-Writeback; obsidian-sync patterns.json promotion_status)
- Design-Prinzip 4 korrigiert: Invoker sind wrap-up/self-improve/memory-maintenance (NICHT quality-gate — dessen pattern-extractor/context-keeper stehen nur in toten depends-on-Metadaten); Prinzip 10 (docs-als-SoT) ergaenzt
- Neuer Test (validate-plugin.sh #41b): prueft, dass jeder Skill mit echtem Body-Aufruf eines anderen Skills in Prinzip 4 gelistet ist (depends-on-Metadaten ausgenommen). In beide Richtungen verifiziert; fand sofort einen falschen quality-gate-Invoker-Claim in der Neufassung
- Prio-3-Carry-over: self-improve-Haertungs-TODO im Wiki festgehalten (5 Hebel aus 80-Iterationen-Retro)

## [2026-06-02] Reference-Docs gegen SSoT korrigiert + Drift-Test (v3.2.4)

- `references/memory-structure.md` gegen die SSoT (`scripts/mem-schema.sh`) korrigiert: fehlende Store-Files ergaenzt (`learnings/learnings.json`, `context/open-tasks.json`, `working/current-session.json`); Archiving-Schwellen gegen `memory-maintenance` Step 3/4 berichtigt (iteration-log 500->100, errors 200->50, patterns `last_seen >60d OR confidence <0.3`); SSoT-Source-Header
- `references/skill-template.md`: Layer-Guide gegen die echten 13 Skills (geloeschte `code-reviewer`/`test-validator`/`tdd` -> `quality-gate`), v2->v3
- Neuer Drift-Guard in `validate-plugin.sh`: jeder in `memory-structure.md` dokumentierte Store-Pfad muss real von der SSoT erzeugt werden (doc subset of real); in beide Richtungen verifiziert (173/173)
- Codex-Verifier-Runde: 2 MINOR behoben (patterns-Schwelle vollstaendig, mktemp-Guard robuster)
- PROJECT.md-Version 3.2.2 -> 3.2.4 nachgezogen (war beim 3.2.3-Bump nicht mitgezogen)

## [2026-06-01] Docs-als-SoT durchgezogen + Codex-Verifier-Fixes (v3.2.2)

- Veraltete `docs/plugin-documentation.md` (v2-Stand, nannte geloeschte Agents) entfernt — die Regel-13-Docs decken den Inhalt aktueller ab
- Divergenz-Pfade geschlossen: context-detective + /init lesen jetzt die Docs ZUERST, bevor sie project-context.md schreiben
- Neuer Konsistenz-Test: alle project-context.md-Schreiber muessen Docs-als-SoT referenzieren
- context-keeper: partial-doc-fallback, Retrieval-Mode liest Docs zuerst, Quellenliste konsistent
- PROJECT.md-Version 3.2.1 -> 3.2.2 korrigiert; Hook-Layout um `*Last updated*`-Zeile ergaenzt

## [2026-06-01] Projekt-Dokumentation + Docs-als-Source-of-Truth

- Regel-13-Skelett angelegt: PROJECT.md, CAPABILITIES.md, ARCHITECTURE.md, CHANGELOG.md, HOW-TO-USE.md
- context-keeper liest jetzt die Docs als primaere Quelle fuer project-context.md (Docs = Source of Truth, project-context.md = Cache)
- Hook-Init schreibt das volle 7-Sektionen-Layout (Format-Drift behoben)

## [2026-06-01] Schema Single Source of Truth (v3.2.0/3.2.1)

- `.agent-memory/`-Schema in `scripts/mem-schema.sh` extrahiert; Hook + /init konsumieren dieselbe Quelle (L4-Drift beseitigt)
- Phase-2-Backfill heilt partielle Stores vollstaendig; negativer Drift-Guard im Test
- Codex-Verifier: 5 MAJOR + 2 MINOR behoben

## [2026-06-01] Cross-Project-Handoff gehaertet (v3.1.8/3.1.9)

- Pfad-Fix (~/AI/.agent-memory/session-summary.md), Schreib-Luecke geschlossen
- Status-Board `cross-project-status.md` eingefuehrt; zentraler Handoff auf prepend (Datenverlust-Schutz)

## [4.0.1] - 2026-07-06

### Fixed
- Codex-Verifier-Runde (§9): memory-thresholds.sh Doppel-Null-Bug bei grep -c ohne Treffer; memory-maintenance Step 6/7 Zeilen-Limits an Threshold-SSoT delegiert; optimization-goals C3 als obsolet markiert; architecture-map.html Historik-Banner.

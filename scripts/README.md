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

## identity_quote.py (P1/B2)

- Prueft das woertliche User-Zitat eines Kandidaten mit `confirmed: true` und das Zitat eines
  `user_md_supersede`-Eintrags gegen das Transkript der Sitzung (`apply_wrapup.py`, Feld `quote`). Nur lesend.
  Pfad: `<HOME>/.claude/projects/*/<session-id>.jsonl`, erst beim Aufruf aufgeloest (HOME-Umleitung wirkt).
  `--transcript <datei>` gilt nur fuer genau diese Form `<HOME>/.claude/projects/<dir>/<session-id>.jsonl`
  (realpath, ohne Gross-/Kleinschreibung); jede andere Datei wird abgelehnt (`"kein_transkript"`, Warnung,
  `tally.transcript_override_rejected`) - es gibt keine Test-Hintertuer, Tests legen ihr Transkript ins Temp-Home.
  Dieselbe Regel gilt fuer die Standardsuche: ein Junction/Symlink unter `projects/`, der hinausfuehrt, ist kein
  Treffer. Restrisiko: eine vom Agenten selbst dort abgelegte Datei (Schreiben, Hardlink) ist von einem echten
  Transkript nicht zu unterscheiden. Ein Pfad mit `\\?\`-Praefix wird abgelehnt (sichere Richtung).
  Ist `CLAUDE_CODE_SESSION_ID` gesetzt und weicht von `--session-id` ab, ist die Evidenz des Laufs nicht
  pruefbar (`tally.session_binding_mismatch`).
  Zaehlt als Treffer nur ein Teilstring EINER echten User-Nachricht DIESER Sitzung (kein tool_result, keine
  system-reminder-/Befehlsausgabe, kein isMeta, kein Subagent-Prompt, kein origin/turnOrigin ausser `human`,
  kein angenommener Vorschlag `promptSource: suggestion_accepted`, kein Record mit fremder `sessionId`;
  Bloecke wie `<pasted_content>`, `<ide_selection>`, `<teammate-message>` und die Zeile
  `[Request interrupted by user]` zaehlen nicht). Das Zitat braucht mindestens 8 Zeichen und 3 Woerter, beginnt und
  endet an Wortgrenzen und steht nicht innerhalb von 3 Woertern neben einem Negationswort (nicht, nie, kein...,
  keinerlei, keineswegs, nein, nö/noe/nee, ohne, weder, nichts, not, never, no, ...n't, auch ohne Apostroph getippt:
  dont, cant, doesnt, ...; Akut, Gravis und U+02BC gelten wie ein Apostroph). Das belegt den Wortlaut, nicht die
  Bedeutung; die Schwellen kalibriert der Owner. Teilt ein verifiziertes Zitat kein Inhaltswort mit der Aussage,
  die es stuetzen soll (Beobachtung bzw. `new_text`), bleibt das Urteil `true`, aber `apply_wrapup.py` meldet es
  (`tally.quote_unrelated`, Warnung). Verglichen wird mit dem GESPEICHERTEN Text (bei einem bekannten Key die
  Beobachtung der Queue-Zeile, nicht die des Plans). Traegt nur eines von beiden ein Negationswort
  (`has_negation()`), zaehlt es ebenso in `tally.quote_polarity` (keine Herabstufung und keine Warnzeile; ein schwacher
  Hinweis, denn eine Umschreibung mit oder ohne "ohne"/"keine" loest ihn oft ohne Widerspruch aus - Kalibrierdaten).
  Ergebnis je Zitat: `true` | `false` | `"kein_transkript"` (kein Fehler), dazu `info["reasons"]`.
  Nur `true` bestaetigt beim ersten Sehen.
- Ein verifiziertes Zitat (`confirmed_by: quote`) ist auch die hoechste Beweisstufe der Promotion: eine per
  `user_md_supersede` widerrufene Aussage braucht die verifizierten Worte des Users, um zurueckzukommen. Eine Zweitnennung
  oder der inferred-Weg reicht nicht, wenn der Kandidat sie beruehrt (`apply_wrapup.py`, `tally.promotion_blocked_superseded`).
  Das Mass ist grosszuegig, ein Fehlalarm kostet ein Zitat: mindestens 50 % der Inhaltswoerter der widerrufenen Zeile
  im Kandidaten (unter allen seinen Woertern, Fuellwoerter eingeschlossen), bei 1-2 Inhaltswoertern alle, ohne
  Inhaltswort alle Tokens ab 2 Zeichen. Die Zeile wird zweimal gelesen, ohne die Zitierung am Zeilenende und ohne jede
  unverschachtelte Schlussklammer; eine beruehrende Lesart genuegt.
  Bekannte Grenzen: Synonyme und Umschreibungen unter 50 % Wortanteil, ein Kandidat, der nur einen Teil der Zeile nennt,
  und absichtlich verschleierter Wortlaut (Doppelgaenger-Buchstaben) werden nicht beruehrt; ein verifiziertes Zitat
  belegt den Wortlaut, nicht die Bedeutung.

## identity_store.py / identity_briefing.py / soul-template.txt (P2, Release A)

- `identity_store.py`: wo das Profil liegt. Das Zentralprofil `~/.agent-memory/identity/profile.*` gilt genau dann,
  wenn `profile.md` existiert (die Migration schreibt es zuletzt; Umbenennen schaltet zurueck). Der Ort wird erst beim
  Aufruf aufgeloest: `--identity-store`, dann `AGENTIC_OS_IDENTITY_STORE`, dann das Home. Ein Store unter dem
  Temp-Ordner schreibt nie in ein Zentral, das nur ueber das Home gefunden wurde (`GuardError`, Exit 2).
- `apply_wrapup.py` und `preprocess_state.py` nehmen `--identity-store`. Im Zentralmodus: Ids `PCn`, je Zeile
  `origin {projekt, id}`, eigenes Lock `identity/profile.lock` (nach dem Projekt-Lock, nie umgekehrt).
- `identity_briefing.py`: schreibt nach jedem Identitaetslauf die Projektion - im Zentral
  `identity/profile-briefing.txt`, im Projekt `identity/briefing-lines.txt` (eigene Namen, weil der Home-Store beides
  ist; hoechstens 6 Zeilen,
  zitat-bestaetigte zuerst, dann nach Bestaetigungen; ohne Platzhalter, ohne Zitierung, gekuerzt). Der Hook liest
  nur diese Datei (Zentral, sonst Projekt, sonst die ersten 4 echten Zeilen von `user.md`).
- `soul-template.txt`: die soul.md-Vorlage (eine Quelle fuer `mem-schema.sh`) samt frueheren Fassungen; der Hook
  filtert alle Vorlagenzeilen aus der Zeile `Identity:` (DCO #10148).

## preprocess_state.py

- `user_candidates` (bis zu 40 vorhandene Kandidaten-Keys) gibt es nur mit `--session-id` (wrap-up). Der
  Bootstrap-Aufruf ohne `--session-id` bekommt `[]` und zahlt die rund 7 KB nicht; `user_candidates_total`
  ist immer die echte Zahl, der Hash bleibt unveraendert.

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

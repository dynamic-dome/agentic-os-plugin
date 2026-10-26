# Erklärbogen {{id}} — Regel-Learning

*Vorlage: `skills/wrap-up/references/erklaerbogen-vorlage.md`. Erzeugt von `scripts/learning_release.py sheet`. Stand der Freigabe: **{{status}}**.*

Dieses Learning ändert eine Regel oder die Verfassung. Es kommt erst in AGENTS.md und MEMORY.md, wenn
(1) ein **anderes Modell** (Zweitmodell, nicht das Modell, das es geschrieben hat) es verständlich erklärt hat und
(2) Dominic es in **zwei eigenen Sätzen** wiedergeben kann.

## Das Learning (Volltext, unverändert)

- **ID:** {{id}} · **Datum:** {{date}} · **Tags:** {{tags}}
- **Text:** {{text}}

## Teil 1 — Prüfauftrag an das Zweitmodell

Lies das Learning und prüfe es gegen die geltenden Regeln (`~/.claude/CLAUDE.md`, Verfassung, betroffene Projekt-Regeln). Antworte nur mit dem JSON unten.

1. **plain** — Erkläre in einfachem Deutsch, ohne Fachjargon, was sich konkret ändert (höchstens 600 Zeichen). Ein Leser ohne Vorwissen muss es nachsprechen können.
2. **risk** — Was kann damit schiefgehen? Welcher Gegenfall spricht dagegen?
3. **verdict** — `confirms` (das Learning stimmt und ist neu), `contradicts` (es widerspricht einer geltenden Regel) oder `unsure`.
4. **affects** — Welche Regeln oder Dateien ändern sich dadurch (Liste, optional).
5. **explained_by** — Dein Modellname.

Antwort des Zweitmodells (JSON-Abschnitt für den Write-Plan, `rule_explanations`):

```json
{"rule_explanations": [{"id": "{{id}}", "explained_by": "<modell>", "plain": "<Erklärung>", "risk": "<Risiko>", "verdict": "confirms", "affects": ["<datei#abschnitt>"]}]}
```

Aufruf (eigener Aufruf, danach beide Projektionen nur bei Änderung des Bridge-Stands):

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/apply_wrapup.py" .agent-memory --session-id <session-id> <<'PLAN'
<JSON von oben>
PLAN
```

## Teil 2 — Freigabe durch Dominic

Erst lesen, wenn Teil 1 im Store steht (`{{status}}` = `explained`).

- **Meine zwei Sätze (eigene Worte, nicht kopiert):**

  > Zwei Sätze: ______________________________________________________________

Kann Dominic es nicht in zwei Sätzen sagen, wird **nicht** freigegeben. Dann bleibt das Learning im Zustand `explained`, oder er lehnt es ab (`"status": "rejected"`).

```json
{"rule_releases": [{"id": "{{id}}", "by": "owner", "two_sentences": "<seine zwei Sätze>"}]}
```

Ablehnen: `{"rule_releases": [{"id": "{{id}}", "by": "owner", "status": "rejected", "note": "<warum>"}]}`.

Nach der Freigabe zeigt `tally.bridge_status_changed > 0` an, dass beide Projektionen laufen müssen (wrap-up Schritt 3d.6).

#!/usr/bin/env python3
"""Tests fuer P1 (B2, Qualitaet der Identitaetsdaten): Zitatpflicht, Supersede, Klartext-Pflicht.

Spec: docs/superpowers/specs/2026-10-27-identitaet-verdichtung-messbar-design.md, Abschnitt P1.

Was hier bewiesen wird (jeweils gegen apply_wrapup.py als Subprozess, mit frischem Temp-Home aus _isolated_home):
  A  `confirmed: true` zaehlt nur mit einem woertlichen Zitat, das als Teilstring einer ECHTEN User-Nachricht im
     Transkript der Sitzung steht (kein tool_result, keine system-reminder/command-Ausgabe, keine isMeta-Zeile).
     Ergebnis im Kandidaten: quote_verified true / false / "kein_transkript"; nur true erlaubt die Promotion beim
     ersten Sehen. Ohne Zitat oder bei false: observed bzw. inferred, die zweite Nennung bestaetigt wie bisher.
  B  scripts/identity_quote.py (Normalisierung, Auswahl echter User-Nachrichten) als Einheit.
  D  Plan-Sektion user_md_supersede: verlustfreie Zeilenersetzung (CRLF, BOM, Praeambel, Sektionsreihenfolge bleiben
     Byte fuer Byte), Changelog VOR der Datei, Ablehnung schreibt nichts (Learning L94), Wiederholung aendert nichts.
  E  Klartext-Pflicht (`plain`, 1..600 Zeichen) an bridge_decisions und rule_explanations; user_md_supersede siehe D.

Nachbesserung nach dem Pruefer-Urteil (Befunde Q1-Q15, S1-S8; Entscheidungen D1-D9 des Koordinators):
  F  (D1) die Zweitnennung zaehlt nur aus einer ANDEREN Sitzung (sessions[]), doppelte Keys im Plan zaehlen einmal
  H  (D2) CLAUDE_CODE_SESSION_ID != --session-id: Identitaets-Evidenz des Laufs ist nicht pruefbar
  I  (D3) --transcript nur als <HOME>/.claude/projects/<dir>/<session-id>.jsonl, fremde sessionId-Records zaehlen nicht
  J  (D4) keine Modell-/Paste-/Harness-Texte als Worte des Users (suggestion_accepted, pasted_content, ide_*, ...)
  K  (D5) Wortgrenzen, mindestens 3 Woerter, Negations-Waechter
  L  (D6) Supersede-Haerten: Praeambel, doppelte Eintraege, Zeilentrenner, Wiederholung mit line_id
  M  (D7) Supersede braucht ein verifiziertes Zitat
  N  (D8) die Promotion nach user.md ist verlustfrei
  O  (D9) eine per Supersede widerlegte Aussage kehrt nicht per Promotion zurueck
  P-U  Runde 2 (Zeile ohne beweisbare Sitzung, Junction, Negationsreichweite, Zitat-Bezug, Enthaltensein-Mass, BOM)

Runde 3 (Empfehlungen des Pruefers nach Runde 2, R1-R4):
  V  (R1) der Zitat-Bezug (quote_unrelated) gilt dem GESPEICHERTEN Text, auch bei einem bekannten Key
  W  (R2) D9-Fehlsperren: Umschrift, Zusaetze am Zeilenende, Inhaltswoerter, Vermerk blocked_by_supersede, Freigabe
  X  (R3) mehr Negationswoerter, Akut/Gravis, Polaritaets-Zaehler (quote_polarity, seit Runde 4 ohne Warnzeile)
  Y  (R4) Testluecken: BOM vor der ersten Ueberschrift (Supersede, Promotion), spaeteres unverifiziertes Zitat
  Z  Faelle des Pruefers aus Runde 3 unter dem Beweisstufen-Vertrag

Runde 4 (Entscheidung des Koordinators): die Wort-Heuristik der Sperre entscheidet nicht ueber Bedeutung, nur ueber die BEWEISSTUFE:
  AA eine Aussage, die einen widerrufenen Satz beruehrt, wird nur mit verifiziertem Zitat promotet (confirmed_by == quote); Gesamtablauf
     Promotion -> Supersede mit Zitat -> neuer Key fuer jeden Fall der Pruefer (probe5 N, probe6 R/B, probe7 F/K)

Der Transkriptpfad kommt aus --transcript (nur unter <HOME>/.claude/projects/) oder aus
<HOME>/.claude/projects/*/<session-id>.jsonl; HOME ist das Temp-Home dieses Tests. Echte Transkripte und echte Stores
werden nie gelesen oder geschrieben.
Exit-Codes: 0 = alles gruen, 1 = Fehler.
"""

from __future__ import annotations

import atexit
import collections
import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
SCRIPT = os.path.join(SCRIPTS, "apply_wrapup.py")

from _isolated_home import process_env  # noqa: E402

ENV, ISO_HOME = process_env(PYTHONIOENCODING="utf-8")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

TESTS = PASSED = ERRORS = 0


def check(cond, msg):
    global TESTS, PASSED, ERRORS
    TESTS += 1
    if cond:
        PASSED += 1
        print(f"  PASS: {msg}")
    else:
        ERRORS += 1
        print(f"  FAIL: {msg}")


# ----------------------------------------------------------------------------- Fixtures

DATE = "2026-10-27"
SID = "sess-b2"
PLAIN = "Kurz gesagt: Der Agent faehrt die Regel von vorher zurueck. Ab jetzt gilt nur noch die neue Fassung."

BASE_USER_MD = (
    "# User Profile\n\n"
    "*Initialized: 2026-07-01*\n\n"
    "## Preferences\n"
    "- Antworten kurz halten (UC3, 2026-07-01)\n"
    "- Immer mit Quelle belegen (UC4, 2026-07-02)\n\n"
    "## Work Style\n"
    "- (Will be populated through session observations)\n\n"
    "## Known Corrections\n"
    "- (Recorded when user corrects agent behavior 3+ times)\n\n"
    "## Eigene Sektion\n"
    "- Sonderzeile bleibt stehen\n"
)


def write_bytes(path, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def write(mem, rel, text):
    write_bytes(os.path.join(mem, rel), text.encode("utf-8"))


def put(mem, rel, data):
    write(mem, rel, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def read_bytes(mem, rel) -> bytes:
    with open(os.path.join(mem, rel), "rb") as fh:
        return fh.read()


def read(mem, rel) -> str:
    return read_bytes(mem, rel).decode("utf-8")


def load(mem, rel):
    return json.loads(read(mem, rel))


# Alle Test-Stores unter EINEM Elternordner, der am Ende (auch nach einem Absturz) geloescht wird - vorher blieben je Lauf
# Hunderte Ordner b2-test-* in %TEMP% liegen (Pruefer Runde 4).
MEM_ROOT = tempfile.mkdtemp(prefix="b2-run-")
atexit.register(shutil.rmtree, MEM_ROOT, ignore_errors=True)


def make_mem(user_md: bytes | None = None) -> str:
    mem = tempfile.mkdtemp(prefix="b2-test-", dir=MEM_ROOT)
    for d in ("learnings", "working", "identity", "context"):
        os.makedirs(os.path.join(mem, d), exist_ok=True)
    put(mem, "learnings/learnings.json", [
        {"id": "L1", "date": "2026-10-01", "text": "Kandidat A", "importance": 4, "tags": [], "layer": "short-term",
         "superseded_by": None, "last_relevant": "2026-10-01", "bridge_status": "candidate"},
        {"id": "L2", "date": "2026-10-01", "text": "Kandidat B", "importance": 5, "tags": [], "layer": "short-term",
         "superseded_by": None, "last_relevant": "2026-10-01", "bridge_status": "candidate"},
    ])
    put(mem, "working/user-candidates.json", [])
    put(mem, "identity/user-changelog.json", [])
    put(mem, "context/open-tasks.json", [])
    write_bytes(os.path.join(mem, "identity/user.md"), BASE_USER_MD.encode("utf-8") if user_md is None else user_md)
    write(mem, "identity/soul.md", "# Soul\n\n- unantastbar\n")
    return mem


def snapshot(mem):
    files = {}
    for base, _dirs, names in os.walk(mem):
        for n in names:
            p = os.path.join(base, n)
            rel = os.path.relpath(p, mem).replace(os.sep, "/")
            if rel == "working/store.lock":
                continue  # OS-Lock-Handle: Infrastruktur, keine Daten
            with open(p, "rb") as fh:
                files[rel] = fh.read()
    return files


class Tally(dict):
    """Fehlende Tally-Felder liefern None statt KeyError: ein alter Stand soll rot melden, nicht abstuerzen."""

    def __missing__(self, key):
        return None


def run(mem, plan, *extra, sid=SID, env_extra=None):
    """apply_wrapup.py als Subprozess. `env_extra` setzt einzelne Variablen zusaetzlich zur isolierten Umgebung
    (z. B. CLAUDE_CODE_SESSION_ID, die process_env sonst entfernt)."""
    run_env = dict(ENV)
    run_env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, SCRIPT, mem, "--session-id", sid, *extra],
        input=json.dumps(plan), capture_output=True, text=True, encoding="utf-8", env=run_env,
    )
    try:
        res = json.loads(proc.stdout)
    except json.JSONDecodeError:
        res = {"_stdout": proc.stdout, "_stderr": proc.stderr}
    res["tally"] = Tally(res.get("tally") or {})
    return proc.returncode, res


# Fake-Transkripte (Formate wie in echten Claude-Code-Transkripten, nur lesend nachgebaut)

def rec_user(text, **extra):
    r = {"type": "user", "isSidechain": False, "message": {"role": "user", "content": text},
         "origin": {"kind": "human"}, "promptSource": "typed", "turnOrigin": "human"}
    r.update(extra)
    return r


def rec_user_old(text):
    """Aelteres Format: Textbloecke, kein origin/promptSource."""
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def rec_tool_result(text):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": text}]},
        "toolUseResult": {"stdout": text}, "sourceToolAssistantUUID": "a1"}


def rec_meta_reminder(text):
    return {"type": "user", "isMeta": True,
            "message": {"role": "user", "content": "<system-reminder>\n" + text + "\n</system-reminder>"}}


def rec_assistant(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def rec_cmd_stdout(text):
    return {"type": "user", "message": {"role": "user",
                                        "content": "<local-command-stdout>" + text + "</local-command-stdout>"}}


def rec_task_note(text):
    return {"type": "user", "message": {"role": "user", "content": "<task-notification>" + text + "</task-notification>"},
            "origin": {"kind": "task-notification", "producer": "session-task"}, "promptSource": "system",
            "turnOrigin": "task_notification"}


def rec_meta_flag(text):
    return rec_user(text, isMeta=True)


def rec_sidechain(text):
    return rec_user(text, isSidechain=True)


def write_transcript(path, records, bom=False, garbage=False):
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    if garbage:
        lines.insert(1, "{das ist kein json")
        lines.insert(2, "")
    data = ("\n".join(lines) + "\n").encode("utf-8")
    write_bytes(path, (b"\xef\xbb\xbf" if bom else b"") + data)
    return path


PROJ_DIR = os.path.join(ISO_HOME, ".claude", "projects", "proj-x")


def home_transcript(sid, records, **kw):
    """Legt das Transkript dort ab, wo die Aufloesung ohne --transcript sucht: <HOME>/.claude/projects/*/<sid>.jsonl.
    Nur dort nimmt das Skript auch einen --transcript-Pfad an (D3)."""
    return write_transcript(os.path.join(PROJ_DIR, f"{sid}.jsonl"), records, **kw)


def drop_home_transcript(sid=None):
    shutil.rmtree(os.path.join(ISO_HOME, ".claude"), ignore_errors=True)


@contextlib.contextmanager
def inproc_env():
    """Fuer Aufrufe im Testprozess selbst (identity_quote / Applier direkt): HOME und USERPROFILE zeigen auf das Temp-Home,
    CLAUDE_CODE_SESSION_ID (die echte Sitzung, wenn der Test in einer laeuft) ist entfernt. Danach wie vorher."""
    names = ("HOME", "USERPROFILE", "CLAUDE_CODE_SESSION_ID")
    saved = {k: os.environ.get(k) for k in names}
    try:
        os.environ["HOME"] = os.environ["USERPROFILE"] = ISO_HOME
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


USER_SAYS = "Ich will das immer so: erst eine kurze Zusammenfassung, dann die Details."
QUOTE = "erst eine kurze Zusammenfassung, dann die Details"


def cand(key="kurz-zuerst", **kw):
    c = {"key": key, "observation": "Antworten beginnen mit einer kurzen Zusammenfassung", "signal_type": "preference",
         "confidence": 0.8, "evidence": [f"session {DATE}"], "trust_source": "conversation"}
    c.update(kw)
    return c


def one(mem, key):
    hits = [c for c in load(mem, "working/user-candidates.json") if c["key"] == key]
    return hits[0] if hits else {}


def try_import(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, name + ".py"))
    if spec is None or not os.path.isfile(os.path.join(SCRIPTS, name + ".py")):
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, SCRIPTS)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001 - ein defektes Modul soll rot melden, nicht den Lauf beenden
        print(f"  (Import von {name} schlug fehl: {exc})")
        return None
    return mod


print("=== P1 / B2 tests (Zitatpflicht, Supersede, Klartext) ===")

# ============================================================================= A  Zitatpflicht

print("--- A: confirmed braucht ein Zitat, das im Transkript steht")
T_DIR = tempfile.mkdtemp(prefix="b2-transcripts-")  # ausserhalb des Homes: dort wird ein --transcript abgelehnt (D3)
atexit.register(shutil.rmtree, T_DIR, ignore_errors=True)
TR = os.path.join(PROJ_DIR, f"{SID}.jsonl")  # das Transkript der Standard-Sitzung SID, im Temp-Home

# A1 Zitat steht in einer echten User-Nachricht (--transcript): Erstsicht-Promotion
mem = make_mem()
write_transcript(TR, [rec_user("Hallo"), rec_assistant("Antwort"), rec_user(USER_SAYS)])
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, "--transcript", TR)
c = one(mem, "kurz-zuerst") if rc == 0 else {}
check(rc == 0 and c.get("quote_verified") is True and c.get("status") == "promoted",
      f"A1 verifiziertes Zitat -> Promotion beim ersten Sehen (rc={rc}, status={c.get('status')}, qv={c.get('quote_verified')}, {out.get('error')})")
check(c.get("quote") == QUOTE and c.get("confirmed_by") == "quote",
      "A1 der Kandidat traegt quote und confirmed_by=quote")
log = load(mem, "identity/user-changelog.json")
check(len(log) == 1 and log[0].get("quote") == QUOTE and log[0].get("quote_verified") is True,
      "A1 der Changelog-Eintrag der Promotion traegt Zitat und Pruefergebnis")
check("Antworten beginnen mit einer kurzen Zusammenfassung" in read(mem, "identity/user.md"),
      "A1 die Zeile landet in user.md")
t = out.get("tally", {})
check(t.get("quotes_verified") == 1 and t.get("quotes_unverified") == 0 and t.get("quotes_no_transcript") == 0
      and t.get("confirm_downgraded") == 0 and t.get("quote_transcript") == TR,
      f"A1 Tally: quotes_verified=1, nichts herabgestuft, Transkript benannt ({t.get('quotes_verified')}, {t.get('quote_transcript')})")

# A2 Aufloesung ueber HOME/.claude/projects/*/<sid>.jsonl ohne --transcript (HOME-Umleitung wird beachtet)
mem = make_mem()
path = home_transcript("sess-home", [rec_user_old("nur zur Tarnung"), rec_user_old(USER_SAYS)])
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, sid="sess-home")
c = one(mem, "kurz-zuerst") if rc == 0 else {}
check(rc == 0 and c.get("quote_verified") is True and c.get("status") == "promoted",
      f"A2 Transkript im (Temp-)Home gefunden, Textbloecke im aelteren Format zaehlen (rc={rc}, qv={c.get('quote_verified')})")
check(os.path.normcase(str(out.get("tally", {}).get("quote_transcript"))) == os.path.normcase(path),
      "A2 der benutzte Pfad steht im Tally")
drop_home_transcript("sess-home")

# A3 Zitat nur in Quellen, die keine echte User-Nachricht sind: false, nichts wird promotet
NOT_A_USER_MESSAGE = {
    "tool_result": [rec_user("Etwas anderes"), rec_tool_result(USER_SAYS)],
    "tool_result-Traeger mit Textblock": [rec_user("Etwas anderes"), {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_2", "content": "ok"}, {"type": "text", "text": USER_SAYS}]}}],
    "system-reminder (isMeta)": [rec_user("Etwas anderes"), rec_meta_reminder(USER_SAYS)],
    "system-reminder in echter Nachricht": [rec_user("Bitte pruefen.\n<system-reminder>" + USER_SAYS + "</system-reminder>")],
    # a `"user"` token elsewhere on the line defeats the cheap line pre-filter, so the type/role guards are what hold here
    "Assistenten-Text": [rec_user("Etwas anderes"), dict(rec_assistant(USER_SAYS), note="user")],
    "local-command-stdout": [rec_user("Etwas anderes"), rec_cmd_stdout(USER_SAYS)],
    "task-notification (origin)": [rec_user("Etwas anderes"), rec_task_note(USER_SAYS)],
    "isMeta-Flag": [rec_user("Etwas anderes"), rec_meta_flag(USER_SAYS)],
    "Sidechain (Subagent-Prompt)": [rec_user("Etwas anderes"), rec_sidechain(USER_SAYS)],
    "origin plugin": [rec_user("Etwas anderes"), rec_user(USER_SAYS, origin={"kind": "plugin", "name": "example-plugin"})],
}
for name, recs in NOT_A_USER_MESSAGE.items():
    mem = make_mem()
    write_transcript(TR, recs)
    rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, "--transcript", TR)
    c = one(mem, "kurz-zuerst") if rc == 0 else {}
    t = out.get("tally", {})
    check(rc == 0 and c.get("quote_verified") is False and c.get("status") == "observed"
          and t.get("quotes_unverified") == 1 and t.get("confirm_downgraded") == 1
          and t.get("confirm_downgraded_keys") == ["kurz-zuerst"] and not t.get("promoted_ids"),
          f"A3 Zitat nur in '{name}' -> quote_verified=false, observed, nicht promotet (rc={rc}, status={c.get('status')}, qv={c.get('quote_verified')})")
    check(load(mem, "identity/user-changelog.json") == [] and "Zusammenfassung" not in read(mem, "identity/user.md"),
          f"A3 '{name}': kein Changelog-Eintrag, user.md unberuehrt")

# A4 Normalisierung: Gross/Klein, Whitespace, typografische Anfuehrungszeichen, aussen gesetzte Anfuehrungszeichen
mem = make_mem()
write_transcript(TR, [rec_user("Das gilt „immer“:   ERST eine KURZE\n  Zusammenfassung – it’s fine")])
for label, q in (("Gross/Klein + Whitespace", "erst eine kurze   zusammenfassung"),
                 ("typografische Anfuehrungszeichen", "gilt \"immer\": erst eine kurze"),
                 ("Apostroph", "zusammenfassung – it's fine"),
                 ("aussen umschlossen", "„erst eine kurze Zusammenfassung“")):
    mem = make_mem()
    rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=q)]}, "--transcript", TR)
    c = one(mem, "kurz-zuerst") if rc == 0 else {}
    check(rc == 0 and c.get("quote_verified") is True, f"A4 Normalisierung: {label} (qv={c.get('quote_verified')})")

# A5 ein Zitat ueber zwei Nachrichten hinweg zaehlt nicht
mem = make_mem()
write_transcript(TR, [rec_user("Das ist der Anfang vom Satz bar"), rec_user("baz und danach das Ende vom Satz")])
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote="Anfang vom Satz bar baz und danach")]},
              "--transcript", TR)
check(rc == 0 and one(mem, "kurz-zuerst").get("quote_verified") is False,
      "A5 Zitat ueber zwei Nachrichten -> false")

# A6 kein Zitat / leeres Zitat / zu kurzes Zitat: observed, kein Fehler
for label, kw in (("kein quote-Feld", {}), ("leeres Zitat", {"quote": ""}), ("nur Whitespace", {"quote": "  \n "}),
                  ("zu kurzes Zitat", {"quote": "ja"})):
    mem = make_mem()
    write_transcript(TR, [rec_user("ja, genau so. " + USER_SAYS)])
    rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, **kw)]}, "--transcript", TR)
    c = one(mem, "kurz-zuerst") if rc == 0 else {}
    t = out.get("tally", {})
    check(rc == 0 and c.get("status") == "observed" and not t.get("promoted_ids") and t.get("confirm_downgraded") == 1,
          f"A6 confirmed ohne brauchbares Zitat ({label}) -> observed, herabgestuft (rc={rc}, status={c.get('status')})")
mem = make_mem()
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True)]}, "--transcript", TR)
check("quote_verified" not in one(mem, "kurz-zuerst") and out["tally"]["quotes_verified"] == 0
      and out["tally"]["quotes_unverified"] == 0,
      "A6 ohne Zitat bekommt der Kandidat kein quote_verified-Feld und zaehlt nicht als geprueft")

# A7 kein Transkript auffindbar: 'kein_transkript', kein Erstsicht-Confirm, aber kein Fehler
mem = make_mem()
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, sid="sess-ohne-transkript")
c = one(mem, "kurz-zuerst") if rc == 0 else {}
t = out.get("tally", {})
check(rc == 0 and c.get("quote_verified") == "kein_transkript" and c.get("status") == "observed"
      and t.get("quotes_no_transcript") == 1 and t.get("confirm_downgraded") == 1 and t.get("quote_transcript") is None,
      f"A7 ohne Transkript -> quote_verified='kein_transkript', observed, rc=0 (rc={rc}, qv={c.get('quote_verified')})")
mem = make_mem()
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]},
              "--transcript", os.path.join(ISO_HOME, ".claude", "projects", "proj-leer", f"{SID}.jsonl"))
check(rc == 0 and one(mem, "kurz-zuerst").get("quote_verified") == "kein_transkript"
      and any("not found" in w.lower() for w in (out["tally"]["warnings"] or [])) and not out["tally"]["transcript_override_rejected"],
      "A7 ein zulaessiger, aber fehlender --transcript-Pfad ist kein Fehler, nur eine Warnung (und keine Ablehnung)")
mem = make_mem()
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, sid="../evil")
check(rc == 0 and one(mem, "kurz-zuerst").get("quote_verified") == "kein_transkript",
      "A7 eine Session-ID mit Pfadzeichen loest nie ein Transkript auf")

# A8 ein ungueltiger Typ lehnt den ganzen Plan ab, nichts wird geschrieben
for label, c_bad in (("quote als Zahl", cand(confirmed=True, quote=5)), ("quote als Liste", cand(confirmed=True, quote=["x"])),
                     ("confirmed als String", cand(confirmed="ja", quote=QUOTE))):
    mem = make_mem()
    before = snapshot(mem)
    rc, out = run(mem, {"date": DATE, "iterations": [{"title": "Vorher geschrieben?"}], "user_candidates": [c_bad]})
    check(rc == 2 and out.get("ok") is False and snapshot(mem) == before,
          f"A8 {label}: Plan abgelehnt, nichts geschrieben (rc={rc}, {out.get('error')})")

# A9 'status' im Plan ist kein Hintertuerchen: confirmed/promoted ohne Zitat wird zu observed
for label, st in (("status=confirmed", "confirmed"), ("status=promoted", "promoted"), ("status=quatsch", "quatsch")):
    mem = make_mem()
    rc, out = run(mem, {"date": DATE, "user_candidates": [cand(status=st)]}, "--transcript", TR)
    c = one(mem, "kurz-zuerst") if rc == 0 else {}
    check(rc == 0 and c.get("status") == "observed" and not out["tally"]["promoted_ids"],
          f"A9 {label} im Plan ohne Zitat -> observed, nichts promotet (rc={rc}, status={c.get('status')})")
mem = make_mem()
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(status="inferred")]}, "--transcript", TR)
check(rc == 0 and one(mem, "kurz-zuerst").get("status") == "inferred", "A9 status=inferred bleibt erlaubt")

# A10 die bisherige Regel bleibt: die zweite Nennung aus einer ANDEREN Sitzung bestaetigt ohne Zitat (D1)
mem = make_mem()
rc1, out1 = run(mem, {"date": DATE, "user_candidates": [cand()]}, sid="sess-erste")
check(one(mem, "kurz-zuerst").get("status") == "observed", "A10 erste Nennung ohne Zitat: observed")
rc2, out2 = run(mem, {"date": DATE, "user_candidates": [cand()]}, sid="sess-zweite")
c = one(mem, "kurz-zuerst")
check(c.get("occurrences") == 2 and c.get("status") == "promoted" and c.get("confirmed_by") == "repeat"
      and out2["tally"]["promoted_ids"] == [c.get("id")] and c.get("sessions") == ["sess-erste", "sess-zweite"],
      f"A10 zweite Nennung aus einer anderen Sitzung bestaetigt und promotet (status={c.get('status')}, confirmed_by={c.get('confirmed_by')})")

# A11 ein verifiziertes Zitat beim zweiten Sehen haelt ebenfalls (und ersetzt ein frueheres false)
mem = make_mem()
write_transcript(TR, [rec_user("nichts Passendes")])
run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, "--transcript", TR)
check(one(mem, "kurz-zuerst").get("quote_verified") is False, "A11 erst false ...")
write_transcript(TR, [rec_user(USER_SAYS)])
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, "--transcript", TR)
c = one(mem, "kurz-zuerst")
check(c.get("quote_verified") is True and c.get("status") == "promoted", "A11 ... dann true: das verifizierte Ergebnis gewinnt")

# A12 mood wird auch mit verifiziertem Zitat nie promotet
mem = make_mem()
write_transcript(TR, [rec_user("Ich bin heute wirklich genervt von dem Ablauf")])
rc, out = run(mem, {"date": DATE, "user_candidates": [
    cand(key="laune", observation="War genervt", signal_type="mood", confirmed=True, quote="wirklich genervt von dem Ablauf")]},
    "--transcript", TR)
c = one(mem, "laune")
check(c.get("status") == "confirmed" and c.get("quote_verified") is True and not out["tally"]["promoted_ids"],
      "A12 mood mit verifiziertem Zitat: confirmed, aber nie promotet")

# A13 Trust-Grenze gilt vor der Zitatpruefung
mem = make_mem()
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE, trust_source="web")]}, "--transcript", TR)
check(out["tally"]["candidates_rejected_trust"] == 1 and load(mem, "working/user-candidates.json") == []
      and out["tally"]["quotes_verified"] == 0 and out["tally"]["quotes_unverified"] == 0
      and out["tally"]["quote_transcript"] is None,
      "A13 fremde Quelle: abgewiesen, das Zitat wird gar nicht erst geprueft (kein Transkript geoeffnet)")

# A14 --dry-run: gleiches Ergebnis im Tally, kein Byte geschrieben
mem = make_mem()
write_transcript(TR, [rec_user(USER_SAYS)])
before = snapshot(mem)
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, "--transcript", TR, "--dry-run")
check(rc == 0 and snapshot(mem) == before and out["tally"]["quotes_verified"] == 1 and out["tally"]["candidates_promoted"] == 1,
      "A14 dry-run meldet die Pruefung und die Promotion, schreibt aber nichts")

# A15 ein Transkript mit BOM, kaputter und leerer Zeile bleibt lesbar
mem = make_mem()
write_transcript(TR, [rec_user("x"), rec_user(USER_SAYS)], bom=True, garbage=True)
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, "--transcript", TR)
check(rc == 0 and one(mem, "kurz-zuerst").get("quote_verified") is True, "A15 BOM + kaputte Zeile im Transkript: Zitat wird gefunden")

# A16 mehrere Zitate in einem Plan, jedes einzeln geprueft
mem = make_mem()
write_transcript(TR, [rec_user(USER_SAYS), rec_user("Nie wieder ungefragt pushen, das ist eine harte Regel")])
rc, out = run(mem, {"date": DATE, "user_candidates": [
    cand(key="a", observation="Zusammenfassung zuerst", confirmed=True, quote=QUOTE),
    cand(key="b", observation="Nie ungefragt pushen", confirmed=True, quote="nie wieder ungefragt pushen"),
    cand(key="c", observation="Erfunden", confirmed=True, quote="das hat er nie gesagt, ganz sicher nicht")]},
    "--transcript", TR)
t = out["tally"]
check(one(mem, "a").get("quote_verified") is True and one(mem, "b").get("quote_verified") is True
      and one(mem, "c").get("quote_verified") is False and t["quotes_verified"] == 2 and t["quotes_unverified"] == 1
      and t["confirm_downgraded_keys"] == ["c"],
      f"A16 drei Zitate, zwei echt, eins erfunden (verified={t['quotes_verified']}, unverified={t['quotes_unverified']})")

# A17 die Statuszeile bleibt das Format von Step 6.5 (Skript, Skill und Tests teilen sich den Wortlaut)
check(str(out.get("identity_status_line")).startswith("Identity: ") and "→ user.md promotet" in str(out.get("identity_status_line")),
      "A17 identity_status_line bleibt im Format des Step 6.5")

# ============================================================================= B  Einheit identity_quote

print("--- B: scripts/identity_quote.py")
iq = try_import("identity_quote")
check(iq is not None, "B0 scripts/identity_quote.py ist importierbar")
if iq is not None:
    check(iq.normalize("  Das  „GROSSE“\n\tTest  ") == "das \"grosse\" test", "B1 normalize: Whitespace, Case, Anfuehrungszeichen")
    check(iq.normalize("Straße") == iq.normalize("STRASSE"), "B1 normalize: beide Seiten gleich behandelt (casefold)")
    check(iq.normalize("über") == iq.normalize("über"), "B1 normalize: NFC (zusammengesetzte und getrennte Umlaute)")
    write_transcript(TR, [rec_user("Eins eins eins"), rec_tool_result("Zwei zwei zwei"), rec_meta_reminder("Drei drei drei"),
                          rec_user("Vier eins <system-reminder>Fuenf</system-reminder> sechs sieben")])
    texts = list(iq.real_user_texts(TR))
    check(texts == ["eins eins eins", "vier eins sechs sieben"], f"B2 real_user_texts: nur echte Nachrichten, Tags entfernt ({texts})")
    with inproc_env():
        res, info = iq.verify_quotes(["eins eins eins", "zwei zwei zwei", "vier eins sechs sieben"], SID, TR)
        check(res == {"eins eins eins": True, "zwei zwei zwei": False, "vier eins sechs sieben": True}
              and info["transcript"] == TR and info.get("reasons") == {"zwei zwei zwei": "not_found"},
              f"B3 verify_quotes liefert je Zitat true/false und den Grund ({res}, {info.get('reasons')})")
        res, info = iq.verify_quotes(["irgendein zitat hier"], "sess-gibt-es-nicht", None)
        check(res == {"irgendein zitat hier": "kein_transkript"} and info["transcript"] is None,
              "B4 ohne Transkript: 'kein_transkript'")
    check(iq.resolve_transcript("../x") is None and iq.resolve_transcript("") is None and iq.resolve_transcript("a/b") is None,
          "B5 resolve_transcript lehnt Session-IDs mit Pfadzeichen und leere ab")
    home_transcript("sess-unit", [rec_user("im home")])
    with inproc_env():
        found = iq.resolve_transcript("sess-unit")
    drop_home_transcript("sess-unit")
    check(found and os.path.basename(found) == "sess-unit.jsonl",
          "B6 resolve_transcript loest erst beim Aufruf auf und folgt der HOME-Umleitung")
    home_now = os.path.normcase(os.path.expanduser("~"))
    check(not [n for n, v in vars(iq).items() if isinstance(v, str) and home_now in os.path.normcase(v)],
          "B7 kein Modulwert mit einem Home-Pfad (alles wird erst beim Aufruf aufgeloest)")

drop_home_transcript()

# ============================================================================= D  user_md_supersede

print("--- D: user_md_supersede")
OLD = "- Antworten kurz halten (UC3, 2026-07-01)"
NEW = "- Antworten kurz halten, bei der Fehlersuche ausfuehrlich (UC3, 2026-07-01)"
# D7: ein Supersede braucht ein woertliches Zitat des Users, das im Transkript der Sitzung steht
SUP_SAYS = "Bei der Fehlersuche will ich lieber ausfuehrliche Antworten mit allen Details."
SUP_QUOTE = "lieber ausfuehrliche Antworten mit allen Details"
SUP = {"match": OLD, "new_text": NEW, "reason": "Dominic hat die Regel am 27.10. eingeschraenkt", "plain": PLAIN,
       "quote": SUP_QUOTE}
write_transcript(TR, [rec_user("Hallo"), rec_user(SUP_SAYS), rec_user(USER_SAYS)])  # das Transkript der Sitzung SID


def sup_plan(*entries, **extra):
    return dict({"date": DATE, "user_md_supersede": list(entries)}, **extra)


# D1 per match, LF: nur die Zielzeile aendert sich, Changelog hat den Eintrag
mem = make_mem()
before = snapshot(mem)
rc, out = run(mem, sup_plan(SUP))
after = snapshot(mem)
check(rc == 0 and after["identity/user.md"] == BASE_USER_MD.replace(OLD, NEW).encode("utf-8"),
      f"D1 user.md: genau die Zielzeile ersetzt, alle anderen Bytes gleich (rc={rc}, {out.get('error')})")
log = load(mem, "identity/user-changelog.json")
e = log[0] if log else {}
check(len(log) == 1 and e.get("field") == "user.md/supersede" and e.get("old_value") == OLD and e.get("new_value") == NEW
      and e.get("reason") == SUP["reason"] and e.get("plain") == PLAIN and e.get("by") == "agent" and e.get("ts"),
      f"D1 Changelog-Eintrag mit old_value, new_value, reason, plain ({sorted(e)})")
check(out["tally"]["user_lines_superseded"] == 1 and out["tally"]["supersede_skipped_unchanged"] == 0,
      "D1 Tally: user_lines_superseded=1")
check(all(after[k] == v for k, v in before.items() if k not in ("identity/user.md", "identity/user-changelog.json")),
      "D1 alle anderen Dateien (auch soul.md, Queue) bleiben Byte-gleich")

# D2 Schreibordnung: Changelog VOR der Datei (files_written in Aufrufreihenfolge)
fw = out["files_written"]
check("identity/user-changelog.json" in fw and "identity/user.md" in fw
      and fw.index("identity/user-changelog.json") < fw.index("identity/user.md"),
      f"D2 Reihenfolge: Changelog vor user.md ({fw})")

# D3 per line_id
mem = make_mem()
rc, out = run(mem, sup_plan({"line_id": "UC4", "new_text": "- Immer mit Quelle und Datum belegen (UC4, 2026-07-02)",
                             "reason": "praezisiert", "plain": PLAIN, "quote": SUP_QUOTE}))
check(rc == 0 and "Quelle und Datum" in read(mem, "identity/user.md") and "Immer mit Quelle belegen" not in read(mem, "identity/user.md"),
      f"D3 line_id ersetzt die zitierende Zeile (rc={rc}, {out.get('error')})")
check((load(mem, "identity/user-changelog.json") or [{}])[0].get("candidate_id") == "UC4", "D3 der Changelog nennt die Kandidaten-Id")

# D4 verlustfrei: CRLF, BOM, Praeambel, Sektionsreihenfolge, Zeile ohne Zeilenende am Dateiende
CRLF_TEXT = ("# User Profile\r\n\r\n*Initialized: 2026-07-01*\r\n\r\n"
             "## Work Style\r\n- Arbeitet abends\r\n\r\n"
             "## Preferences\r\n- Antworten kurz halten (UC3, 2026-07-01)\r\n- Zeile mit Leerzeichen am Ende  \r\n\r\n"
             "## Eigene Sektion\r\n- Letzte Zeile ohne Zeilenende")
for label, raw in (("CRLF", CRLF_TEXT.encode("utf-8")), ("CRLF + BOM", b"\xef\xbb\xbf" + CRLF_TEXT.encode("utf-8")),
                   ("gemischte Zeilenenden", CRLF_TEXT.replace("\r\n## Preferences", "\n## Preferences").encode("utf-8"))):
    mem = make_mem(raw)
    rc, out = run(mem, sup_plan(SUP))
    expected = raw.replace(OLD.encode("utf-8"), NEW.encode("utf-8"))
    got = read_bytes(mem, "identity/user.md")
    check(rc == 0 and got == expected and got != raw,
          f"D4 verlustfrei ({label}): nur die Zielzeile geaendert, Zeilenenden/BOM/Praeambel/Reihenfolge bleiben (rc={rc}, {out.get('error')})")
mem = make_mem(CRLF_TEXT.encode("utf-8"))
rc, out = run(mem, sup_plan({"match": "- Letzte Zeile ohne Zeilenende", "new_text": "- Neue letzte Zeile", "reason": "r", "plain": PLAIN, "quote": SUP_QUOTE}))
check(rc == 0 and read_bytes(mem, "identity/user.md") == CRLF_TEXT.replace("- Letzte Zeile ohne Zeilenende", "- Neue letzte Zeile").encode("utf-8"),
      "D4 auch die Zeile ohne Zeilenende am Dateiende wird verlustfrei ersetzt")
mem = make_mem(CRLF_TEXT.encode("utf-8"))
rc, out = run(mem, sup_plan({"match": "- Zeile mit Leerzeichen am Ende  ", "new_text": "- Zeile ohne", "reason": "r", "plain": PLAIN, "quote": SUP_QUOTE}))
check(rc == 0 and b"- Zeile ohne\r\n" in read_bytes(mem, "identity/user.md"),
      "D4 der exakte Zeilentext (mit Leerzeichen am Ende) trifft, das CR bleibt stehen")

# D5 Ablehnungen: Exit 2, kein Byte geschrieben, auch nicht von einer gueltigen Sektion im selben Plan
def rejected(label, mem_, plan, *, needle=None):
    before_ = snapshot(mem_)
    rc_, out_ = run(mem_, plan)
    ok = rc_ == 2 and out_.get("ok") is False and snapshot(mem_) == before_ and out_.get("files_written") in ([], None)
    if needle:
        ok = ok and needle.lower() in str(out_.get("error", "")).lower()
    check(ok, f"D5 abgelehnt, nichts geschrieben: {label} (rc={rc_}, {str(out_.get('error'))[:90]})")


def with_other(entry, *more):
    """Plan mit einer sonst gueltigen Iteration: zeigt, dass ein Fehler in der Sektion auch sie verhindert."""
    return sup_plan(entry, *more, iterations=[{"title": "Darf nicht geschrieben werden"}])


mem = make_mem()
rejected("Zeile nicht gefunden (match)", mem, with_other(dict(SUP, match="- Gibt es nicht")), needle="no such line")
rejected("Zeile nicht gefunden (line_id)", mem, with_other({"line_id": "UC99", "new_text": NEW, "reason": "r", "plain": PLAIN, "quote": SUP_QUOTE}), needle="UC99")
amb = make_mem((BASE_USER_MD + "- Antworten kurz halten (UC3, 2026-07-01)\n").encode("utf-8"))
rejected("match trifft zwei Zeilen", amb, with_other(SUP), needle="not guessing")
amb2 = make_mem((BASE_USER_MD + "- Anderer Text (UC3, 2026-07-09)\n").encode("utf-8"))
rejected("line_id zitiert von zwei Zeilen", amb2, with_other({"line_id": "UC3", "new_text": NEW, "reason": "r", "plain": PLAIN, "quote": SUP_QUOTE}), needle="not guessing")
rejected("leerer reason", mem, with_other(dict(SUP, reason="")), needle="reason")
rejected("reason fehlt", mem, with_other({k: v for k, v in SUP.items() if k != "reason"}), needle="reason")
rejected("reason nur Whitespace", mem, with_other(dict(SUP, reason="  \n")), needle="reason")
rejected("leeres plain", mem, with_other(dict(SUP, plain="")), needle="plain")
rejected("plain fehlt", mem, with_other({k: v for k, v in SUP.items() if k != "plain"}), needle="plain")
rejected("plain kein String", mem, with_other(dict(SUP, plain=7)), needle="plain")
rejected("plain laenger als 600 Zeichen", mem, with_other(dict(SUP, plain="Wort " * 130)), needle="600")
rejected("Platzhalter als Ziel (match)", mem, with_other(dict(SUP, match="- (Will be populated through session observations)")), needle="placeholder")
rejected("Platzhalter als Ziel (Recorded when)", mem, with_other(dict(SUP, match="- (Recorded when user corrects agent behavior 3+ times)")), needle="placeholder")
rejected("Ueberschrift als Ziel", mem, with_other(dict(SUP, match="## Preferences")), needle="bullet")
rejected("Praeambelzeile als Ziel", mem, with_other(dict(SUP, match="*Initialized: 2026-07-01*")), needle="bullet")
rejected("neuer Text ist ein Platzhalter", mem, with_other(dict(SUP, new_text="- (Will be populated)")), needle="placeholder")
rejected("neuer Text leer", mem, with_other(dict(SUP, new_text="")), needle="new_text")
rejected("neuer Text mehrzeilig", mem, with_other(dict(SUP, new_text="- a\n- b")), needle="one line")
rejected("neuer Text ohne Aufzaehlungszeichen", mem, with_other(dict(SUP, new_text="Antworten lang")), needle="bullet")
rejected("trust_source != conversation", mem, with_other(dict(SUP, trust_source="web")), needle="trust")
rejected("by unbekannt", mem, with_other(dict(SUP, by="codex")), needle="by")
rejected("weder match noch line_id", mem, with_other({k: v for k, v in SUP.items() if k != "match"}), needle="exactly one")
rejected("match und line_id zugleich", mem, with_other(dict(SUP, line_id="UC3")), needle="exactly one")
rejected("line_id mit Muell", mem, with_other({"line_id": "UC3)|x", "new_text": NEW, "reason": "r", "plain": PLAIN, "quote": SUP_QUOTE}), needle="line_id")
rejected("zweimal dieselbe Zielzeile", mem, with_other(SUP, dict(SUP, new_text="- anders")), needle="twice")
rejected("Eintrag ist kein Objekt", mem, with_other("UC3"), needle="object")
rejected("Sektion ist keine Liste", mem, {"date": DATE, "user_md_supersede": {"UC3": "x"}}, needle="list")
rejected("eine gute und eine schlechte Zeile", mem, with_other(SUP, dict(SUP, match="- Gibt es nicht", new_text="- x")), needle="no such line")
rejected("quote fehlt", mem, with_other({k: v for k, v in SUP.items() if k != "quote"}), needle="quote")
rejected("quote kein String", mem, with_other(dict(SUP, quote=5)), needle="quote")
rejected("quote leer", mem, with_other(dict(SUP, quote="")), needle="quote")
rejected("quote nur Whitespace", mem, with_other(dict(SUP, quote="  \n ")), needle="quote")
rejected("by=owner braucht ebenfalls ein quote", mem, with_other({k: v for k, v in dict(SUP, by="owner").items() if k != "quote"}), needle="quote")
nofile = make_mem()
os.remove(os.path.join(nofile, "identity/user.md"))
rejected("user.md fehlt", nofile, with_other(SUP), needle="user.md")
notutf8 = make_mem(b"# User Profile\n\n## Preferences\n- Zeile \xff\xfe kaputt\n")
rejected("user.md nicht UTF-8", notutf8, with_other(SUP), needle="utf-8")
badlog = make_mem()
write(badlog, "identity/user-changelog.json", "{}")
rejected("user-changelog.json hat den falschen Typ", badlog, with_other(SUP), needle="changelog")

# D6 Wiederholung: derselbe Plan nochmal aendert nichts (match: Zeile ist schon neu), kein zweiter Changelog-Eintrag
mem = make_mem()
run(mem, sup_plan(SUP))
before = snapshot(mem)
rc, out = run(mem, sup_plan(SUP))
check(rc == 0 and snapshot(mem) == before and out["tally"]["user_lines_superseded"] == 0
      and out["tally"]["supersede_skipped_unchanged"] == 1,
      f"D6 zweiter identischer Lauf: rc=0, kein Byte geaendert, als unveraendert gezaehlt (rc={rc}, {out.get('error')})")

# D7 Abbruch nach dem Changelog-Eintrag konvergiert: Eintrag steht, Datei unveraendert; der naechste Lauf schreibt keinen zweiten
aw = try_import("apply_wrapup")
mem = make_mem()
if aw is not None and hasattr(aw, "apply_user_md_supersede") and hasattr(aw, "new_tally"):
    real_write = aw.write_atomic

    def failing_write(mem_, rel, text, dry, touched, via=None):
        if rel == "identity/user.md":
            raise OSError("simulierter Schreibfehler an user.md")
        return real_write(mem_, rel, text, dry, touched, via)

    aw.write_atomic = failing_write
    try:
        tally = aw.new_tally()
        try:
            with inproc_env():
                aw.apply_user_md_supersede(mem, sup_plan(SUP), DATE, False, [], tally, SID)
            crashed = False
        except OSError:
            crashed = True
    finally:
        aw.write_atomic = real_write
    check(crashed and read(mem, "identity/user.md") == BASE_USER_MD and len(load(mem, "identity/user-changelog.json")) == 1,
          "D7 Schreibfehler an user.md: der Changelog-Eintrag steht schon, die Datei ist unveraendert (Changelog zuerst)")
    rc, out = run(mem, sup_plan(SUP))
    check(rc == 0 and read(mem, "identity/user.md") == BASE_USER_MD.replace(OLD, NEW) and len(load(mem, "identity/user-changelog.json")) == 1,
          "D7 der Wiederholungslauf konvergiert: Datei ersetzt, genau ein Changelog-Eintrag")
else:
    check(False, "D7 apply_wrapup.apply_user_md_supersede und new_tally existieren")
    check(False, "D7 Wiederholungslauf konvergiert")

# D8 Besitz: user.md, Changelog und Queue sind nur ueber den Identitaets-Applier erreichbar
if aw is not None:
    mem = make_mem()
    for rel in ("identity/user.md", "identity/user-changelog.json", "working/user-candidates.json"):
        try:
            aw.write_atomic(mem, rel, "gekapert", False, [])
            check(False, f"D8 allgemeiner Schreibpfad darf {rel} nicht erreichen")
        except aw.PlanError:
            check(True, f"D8 {rel}: allgemeiner Schreibpfad abgelehnt (via= noetig)")
    check(read(mem, "identity/user.md") == BASE_USER_MD, "D8 nichts wurde geschrieben")
    try:
        aw.write_atomic(mem, "identity/user.md", "# ok\n", False, [], via="identity")
        check(read(mem, "identity/user.md") == "# ok\n", "D8 der Applier 'identity' darf schreiben")
    except aw.PlanError as exc:
        check(False, f"D8 der Applier 'identity' darf schreiben ({exc})")

# D9 zusammen mit einer Promotion im selben Plan: erst Supersede, dann Promotion (beides wirkt)
mem = make_mem()
rc, out = run(mem, sup_plan(SUP, user_candidates=[cand(confirmed=True, quote=QUOTE)]))
check(rc == 0 and out["tally"]["candidates_promoted"] == 1,
      f"D9 Supersede und Promotion im selben Plan laufen (rc={rc}, {out.get('error')})")
text = read(mem, "identity/user.md")
check(NEW in text and OLD not in text and "Antworten beginnen mit einer kurzen Zusammenfassung" in text,
      "D9 die Ersetzung und die neu promotete Zeile stehen beide in user.md")

# D10 dry-run
mem = make_mem()
before = snapshot(mem)
rc, out = run(mem, sup_plan(SUP), "--dry-run")
check(rc == 0 and snapshot(mem) == before and out["tally"]["user_lines_superseded"] == 1,
      "D10 dry-run: meldet die Ersetzung, schreibt nichts")

# D11 Ersetzen durch den eigenen Text ist keine Aenderung
mem = make_mem()
before = snapshot(mem)
rc, out = run(mem, sup_plan(dict(SUP, new_text=OLD)))
check(rc == 0 and snapshot(mem) == before and out["tally"]["supersede_skipped_unchanged"] == 1,
      "D11 new_text == bestehende Zeile: nichts geschrieben, als unveraendert gezaehlt")

# D12 by=owner wird als Behauptung festgehalten
mem = make_mem()
rc, out = run(mem, sup_plan(dict(SUP, by="owner")))
check(rc == 0 and (load(mem, "identity/user-changelog.json") or [{}])[0].get("by") == "owner", "D12 by=owner steht im Changelog")

# ============================================================================= E  Klartext-Pflicht

print("--- E: plain an bridge_decisions und rule_explanations")
BD = {"id": "L1", "status": "approved", "note": "Gilt in jeder Sitzung dieses Projekts", "plain": PLAIN}


def bd_plan(*items):
    return {"date": DATE, "bridge_decisions": list(items), "iterations": [{"title": "Darf nicht geschrieben werden"}]}


mem = make_mem()
for label, item in (("plain fehlt", {k: v for k, v in BD.items() if k != "plain"}),
                    ("plain leer", dict(BD, plain="")), ("plain nur Whitespace", dict(BD, plain=" \n ")),
                    ("plain kein String", dict(BD, plain=3)), ("plain zu lang (601)", dict(BD, plain="x" * 601)),
                    ("plain zu lang (Woerter)", dict(BD, plain="wort " * 130))):
    before = snapshot(mem)
    rc, out = run(mem, bd_plan(item))
    check(rc == 2 and out.get("ok") is False and snapshot(mem) == before and "plain" in str(out.get("error")).lower(),
          f"E1 bridge_decisions, {label}: Plan abgelehnt, nichts geschrieben (rc={rc}, {str(out.get('error'))[:80]})")
before = snapshot(mem)
rc, out = run(mem, bd_plan(dict(BD, status="rejected", by="owner", plain="")))
check(rc == 2 and snapshot(mem) == before, "E1 auch der Owner-Rueckweg (by=owner) braucht plain")

mem = make_mem()
rc, out = run(mem, {"date": DATE, "bridge_decisions": [dict(BD, plain="y" * 600)]})
check(rc == 0, f"E2 genau 600 Zeichen sind erlaubt (rc={rc}, {out.get('error')})")
mem = make_mem()
rc, out = run(mem, {"date": DATE, "bridge_decisions": [dict(BD, plain="Einfach  gesagt:\n  das gilt ueberall."), dict(
    BD, id="L2", status="rejected", note="Nur fuer dieses Repo", plain="Das betrifft nur dieses eine Projekt.")]})
rows = {r["id"]: r for r in load(mem, "learnings/learnings.json")}
check(rc == 0 and rows["L1"].get("bridge_plain") == "Einfach gesagt: das gilt ueberall."
      and rows["L2"].get("bridge_plain") == "Das betrifft nur dieses eine Projekt.",
      f"E3 plain landet normalisiert in bridge_plain (rc={rc}, {rows['L1'].get('bridge_plain')!r})")
check(rows["L1"]["bridge_note"] == "2026-10-27 agent: Gilt in jeder Sitzung dieses Projekts",
      "E3 bridge_note behaelt sein Format '<date> <by>: <note>' (review_sweep liest es)")
before = snapshot(mem)
rc, out = run(mem, {"date": DATE, "bridge_decisions": [dict(BD, plain="Einfach  gesagt:\n  das gilt ueberall.")]})
check(rc == 0 and snapshot(mem) == before and out["tally"]["bridge_status_changed"] == 0,
      "E4 derselbe Beschluss mit demselben Klartext: kein Byte geaendert (idempotent)")
rc, out = run(mem, {"date": DATE, "bridge_decisions": [dict(BD, plain="Ganz anders erklaert.")]})
rows = {r["id"]: r for r in load(mem, "learnings/learnings.json")}
check(rc == 0 and rows["L1"].get("bridge_plain") == "Ganz anders erklaert." and out["tally"]["bridge_status_changed"] == 0,
      "E4 ein neuer Klartext zum selben Beschluss wird geschrieben, ohne Statuswechsel")

# rule_explanations: plain leer / zu lang wird abgelehnt (die Pflicht gab es schon, die Obergrenze nicht)
mem = make_mem()
put(mem, "learnings/learnings.json", [
    {"id": "L1", "date": "2026-10-01", "text": "Regel: Der Agent darf kleine Dinge ohne Rueckfrage tun", "importance": 4, "tags": [],
     "layer": "short-term", "superseded_by": None, "last_relevant": "2026-10-01", "rule_class": True,
     "release": {"status": "needs_explanation"}, "bridge_status": "candidate"}])
EXP = {"id": "L1", "explained_by": "codex", "plain": "Der Agent darf kleine, umkehrbare Dinge ohne Rueckfrage erledigen.",
       "risk": "Eine Fehlentscheidung wird spaet bemerkt.", "verdict": "confirms"}
for label, item in (("plain fehlt", {k: v for k, v in EXP.items() if k != "plain"}), ("plain leer", dict(EXP, plain=" ")),
                    ("plain zu lang (601)", dict(EXP, plain="x" * 601)), ("plain kein String", dict(EXP, plain=4))):
    before = snapshot(mem)
    rc, out = run(mem, {"date": DATE, "rule_explanations": [item], "iterations": [{"title": "Darf nicht geschrieben werden"}]})
    check(rc == 2 and snapshot(mem) == before and "plain" in str(out.get("error")).lower(),
          f"E5 rule_explanations, {label}: abgelehnt, nichts geschrieben (rc={rc}, {str(out.get('error'))[:80]})")
rc, out = run(mem, {"date": DATE, "rule_explanations": [dict(EXP, plain="y" * 600)]})
check(rc == 0, f"E5 rule_explanations mit genau 600 Zeichen: angenommen (rc={rc}, {out.get('error')})")

# ============================================================================= F  Zweitnennung nur aus anderer Sitzung (D1)

print("--- F: die Zweitnennung zaehlt nur aus einer anderen Sitzung, doppelte Keys im Plan zaehlen einmal (D1)")


def qrow(key, **kw):
    """Eine Zeile der Kandidaten-Queue (Altformat ohne sessions, solange kw sie nicht setzt)."""
    r = {"id": "UC1", "key": key, "observation": "Antworten beginnen mit einer kurzen Zusammenfassung", "status": "observed",
         "signal_type": "preference", "confidence": 0.5, "occurrences": 1, "evidence": ["session 2026-10-01"],
         "first_seen": "2026-10-01", "last_seen": "2026-10-01", "trust_source": "conversation"}
    r.update(kw)
    return r


def make_mem_q(queue, user_md=None):
    mem_ = make_mem(user_md)
    put(mem_, "working/user-candidates.json", queue)
    return mem_


def queue_of(mem_):
    return load(mem_, "working/user-candidates.json")


write_transcript(TR, [rec_user(USER_SAYS)])

# F1 (Befund Q1) derselbe neue Key zweimal im selben Plan, ohne Zitat: eine Nennung, nicht bestaetigt
mem = make_mem()
rc, out = run(mem, {"date": DATE, "user_candidates": [cand("doppelt"), cand("doppelt")]})
c, t = one(mem, "doppelt"), out["tally"]
check(rc == 0 and len(queue_of(mem)) == 1 and c.get("occurrences") == 1 and c.get("status") == "observed"
      and not t["promoted_ids"] and t["candidates_new"] == 1 and t["candidates_updated"] == 0
      and t["plan_duplicates_merged"] == 1 and c.get("sessions") == [SID]
      and "kurzen Zusammenfassung" not in read(mem, "identity/user.md"),
      f"F1 derselbe Key zweimal im Plan zaehlt einmal: occurrences=1, observed, nichts promotet (rc={rc}, occ={c.get('occurrences')}, status={c.get('status')})")

# F2 die Evidence beider Eintraege wird vereinigt
mem = make_mem()
rc, out = run(mem, {"date": DATE, "user_candidates": [cand("ev", evidence=["a", "b"]), cand("ev", evidence=["b", "c"])]})
check(rc == 0 and one(mem, "ev").get("evidence") == ["a", "b", "c"], f"F2 Evidence vereinigt, ohne Dopplung ({one(mem, 'ev').get('evidence')})")

# F3 ein verifiziertes Zitat gewinnt, in jeder Reihenfolge
BAD_Q = "das hat er nie gesagt, ganz sicher nicht"
for label, entries in (("unverifiziert zuerst", [cand("zitat", confirmed=True, quote=BAD_Q), cand("zitat", confirmed=True, quote=QUOTE)]),
                       ("verifiziert zuerst", [cand("zitat", confirmed=True, quote=QUOTE), cand("zitat", confirmed=True, quote=BAD_Q)]),
                       ("ohne Zitat zuerst", [cand("zitat"), cand("zitat", confirmed=True, quote=QUOTE)])):
    mem = make_mem()
    rc, out = run(mem, {"date": DATE, "user_candidates": entries})
    c, t = one(mem, "zitat"), out["tally"]
    check(rc == 0 and c.get("quote") == QUOTE and c.get("quote_verified") is True and c.get("status") == "promoted"
          and c.get("confirmed_by") == "quote" and c.get("occurrences") == 1 and t["quotes_verified"] == 1
          and t["confirm_downgraded"] == 0 and t["plan_duplicates_merged"] == 1,
          f"F3 ({label}) das verifizierte Zitat gewinnt, einmal gezaehlt (rc={rc}, qv={c.get('quote_verified')}, status={c.get('status')})")
mem = make_mem()
rc, out = run(mem, {"date": DATE, "user_candidates": [cand("zitat", confirmed=True, quote=BAD_Q),
                                                      cand("zitat", quote="auch das steht dort nirgends")]})
c, t = one(mem, "zitat"), out["tally"]
check(rc == 0 and c.get("quote") == BAD_Q and c.get("quote_verified") is False and c.get("status") == "observed"
      and t["confirm_downgraded"] == 1 and t["confirm_downgraded_keys"] == ["zitat"] and t["quotes_unverified"] == 1,
      "F3 kein Zitat verifiziert: das erste bleibt stehen, die Bestaetigung wird einmal herabgestuft")

# F4 (Befund Q2) derselbe Plan zweimal in derselben Sitzung: keine zweite Nennung
mem = make_mem()
plan = {"date": DATE, "user_candidates": [cand("rerun")]}
run(mem, plan, sid="sess-eins")
rc, out = run(mem, plan, sid="sess-eins")
c, t = one(mem, "rerun"), out["tally"]
check(rc == 0 and c.get("occurrences") == 1 and c.get("status") == "observed" and c.get("sessions") == ["sess-eins"]
      and not t["promoted_ids"] and t["repeat_not_counted"] == 1,
      f"F4 derselbe Plan zweimal in einer Sitzung: occurrences=1, observed (occ={c.get('occurrences')}, status={c.get('status')})")
# F5 ... die Gegenprobe: dieselbe Aussage aus einer ANDEREN Sitzung ist die zweite Nennung
rc, out = run(mem, plan, sid="sess-zwei")
c, t = one(mem, "rerun"), out["tally"]
check(rc == 0 and c.get("occurrences") == 2 and c.get("status") == "promoted" and c.get("confirmed_by") == "repeat"
      and c.get("sessions") == ["sess-eins", "sess-zwei"] and t["repeat_not_counted"] == 0 and t["promoted_ids"] == [c.get("id")],
      f"F5 aus einer anderen Sitzung: occurrences=2, bestaetigt per repeat, beide Sitzungen vermerkt (status={c.get('status')})")

# F6 (Befund Q15) bekannter Key aus der Stufe-0-Liste, in derselben Sitzung erneut gesendet: keine Bestaetigung
mem = make_mem_q([qrow("einmal", sessions=[SID])])
rc, out = run(mem, {"date": DATE, "user_candidates": [cand("einmal", observation="irgendwas anderes")]})
c, t = one(mem, "einmal"), out["tally"]
check(rc == 0 and c.get("status") == "observed" and c.get("occurrences") == 1 and not c.get("confirmed_by")
      and not t["promoted_ids"] and t["repeat_not_counted"] == 1,
      f"F6 Key aus derselben Sitzung erneut gesendet bestaetigt nichts (status={c.get('status')}, occ={c.get('occurrences')})")

# F6b ein bereits gezaehlter Zaehlerstand (occurrences 2, inferred, Konfidenz unter der Promotionsschwelle) wird durch
# ein erneutes Senden in derselben Sitzung nicht nachtraeglich bestaetigt - bestaetigt wird nur im Moment einer ZAEHLENDEN Nennung
mem = make_mem_q([qrow("zwei", status="inferred", confidence=0.4, occurrences=2, sessions=["a", SID])])
rc, out = run(mem, {"date": DATE, "user_candidates": [cand("zwei")]})
c = one(mem, "zwei")
check(rc == 0 and c.get("status") == "inferred" and c.get("occurrences") == 2 and not c.get("confirmed_by") and not out["tally"]["promoted_ids"],
      f"F6b erneutes Senden in derselben Sitzung bestaetigt einen inferred-Eintrag mit occurrences=2 nicht (status={c.get('status')})")
rc, out = run(mem, {"date": DATE, "user_candidates": [cand("zwei")]}, sid="sess-neu")
c = one(mem, "zwei")
check(c.get("occurrences") == 3 and c.get("status") == "promoted" and c.get("confirmed_by") == "repeat",
      "F6b Gegenprobe: dieselbe Aussage aus einer neuen Sitzung zaehlt, bestaetigt (repeat) und wird promotet")

# F7 Altbestand ohne sessions: occurrences bleibt, die erste neue Sitzung zaehlt +1
mem = make_mem_q([qrow("alt")])
rc, out = run(mem, {"date": DATE, "user_candidates": [cand("alt")]})
c = one(mem, "alt")
check(rc == 0 and c.get("occurrences") == 2 and c.get("sessions") == [SID] and c.get("confirmed_by") == "repeat"
      and c.get("status") == "promoted",
      f"F7 Altzeile ohne sessions: die erste neue Sitzung zaehlt +1 (occ={c.get('occurrences')}, sessions={c.get('sessions')})")
mem = make_mem_q([qrow("alt", occurrences=3, status="inferred", confidence=0.4)])
rc, out = run(mem, {"date": DATE, "user_candidates": [cand("alt")]})
c = one(mem, "alt")
check(c.get("occurrences") == 4 and c.get("sessions") == [SID], "F7 der bisherige Zaehler einer Altzeile bleibt erhalten und wird fortgefuehrt")

# F8 leere Sitzungs-ID: nichts wird hochgezaehlt, eine Warnung benennt den Grund
mem = make_mem_q([qrow("alt2")])
rc, out = run(mem, {"date": DATE, "user_candidates": [cand("alt2"), cand("ohne-sid")]}, sid="")
c, n, t = one(mem, "alt2"), one(mem, "ohne-sid"), out["tally"]
check(rc == 0 and c.get("occurrences") == 1 and c.get("status") == "observed" and not t["promoted_ids"]
      and n.get("occurrences") == 1 and n.get("sessions") == [] and t["repeat_not_counted"] == 1
      and any("session" in w.lower() for w in t["warnings"]),
      f"F8 leere Sitzungs-ID: kein Hochzaehlen, Warnung (occ={c.get('occurrences')}, warnings={t['warnings']})")

# F9 das Zitat bestaetigt auch beim bekannten Key aus einer neuen Sitzung (unabhaengig vom Sitzungszaehler)
mem = make_mem_q([qrow("zit2", sessions=["frueher"])])
rc, out = run(mem, {"date": DATE, "user_candidates": [cand("zit2", confirmed=True, quote=QUOTE)]})
c = one(mem, "zit2")
check(c.get("confirmed_by") == "quote" and c.get("quote_verified") is True and c.get("status") == "promoted",
      "F9 verifiziertes Zitat bestaetigt einen bekannten Key (confirmed_by=quote)")

# F10 ein fremder Eintrag (trust_source) wird vor dem Zusammenfassen abgewiesen und zaehlt nicht mit
mem = make_mem()
rc, out = run(mem, {"date": DATE, "user_candidates": [cand("t", trust_source="web"), cand("t")]})
c, t = one(mem, "t"), out["tally"]
check(rc == 0 and t["candidates_rejected_trust"] == 1 and t["candidates_new"] == 1 and t["plan_duplicates_merged"] == 0
      and c.get("occurrences") == 1 and c.get("trust_source") == "conversation",
      "F10 fremde Quelle wird abgewiesen, der Rest zaehlt einmal")

# ============================================================================= H  Sitzungsbindung (D2)

print("--- H: CLAUDE_CODE_SESSION_ID weicht von --session-id ab -> die Evidenz des Laufs ist nicht pruefbar (D2)")
write_transcript(TR, [rec_user(USER_SAYS)])
REAL_SESSION = {"CLAUDE_CODE_SESSION_ID": "die-echte-sitzung"}
mem = make_mem_q([qrow("bekannt")])
rc, out = run(mem, {"date": DATE, "iterations": [{"title": "Laeuft trotzdem"}],
                    "user_candidates": [cand(confirmed=True, quote=QUOTE), cand("bekannt")]}, env_extra=REAL_SESSION)
c, b, t = one(mem, "kurz-zuerst"), one(mem, "bekannt"), out["tally"]
check(rc == 0 and c.get("quote_verified") == "kein_transkript" and c.get("status") == "observed"
      and t["session_binding_mismatch"] is True and t["quote_transcript"] is None and not t["promoted_ids"]
      and any("CLAUDE_CODE_SESSION_ID" in w for w in t["warnings"]),
      f"H1 Zitate werden nicht verifiziert, Warnung und Tally-Feld (qv={c.get('quote_verified')}, mismatch={t['session_binding_mismatch']})")
check(b.get("occurrences") == 1 and b.get("status") == "observed" and not b.get("sessions"),
      "H1 ein bekannter Key wird in diesem Lauf nicht hochgezaehlt")
check("Laeuft trotzdem" in read(mem, "iterations/iteration-log.md"), "H1 der Rest des Plans laeuft normal")
mem = make_mem()
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, env_extra={"CLAUDE_CODE_SESSION_ID": SID})
check(one(mem, "kurz-zuerst").get("quote_verified") is True and not out["tally"]["session_binding_mismatch"],
      "H2 CLAUDE_CODE_SESSION_ID gleich --session-id: normal verifiziert")
# H3 (Befund Q4) ein fremdes, aber vorhandenes Transkript ueber eine fremde --session-id
home_transcript("andere-sitzung", [rec_user(USER_SAYS, sessionId="andere-sitzung")])
mem = make_mem()
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, sid="andere-sitzung",
              env_extra={"CLAUDE_CODE_SESSION_ID": SID})
c = one(mem, "kurz-zuerst")
check(rc == 0 and c.get("quote_verified") == "kein_transkript" and c.get("status") == "observed"
      and out["tally"]["session_binding_mismatch"] is True,
      f"H3 eine fremde --session-id (Transkript vorhanden) verifiziert nichts (qv={c.get('quote_verified')})")
mem = make_mem()
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, sid="andere-sitzung")
check(one(mem, "kurz-zuerst").get("quote_verified") is True,
      "H3 ohne CLAUDE_CODE_SESSION_ID gibt es keinen Vergleichswert: die genannte Sitzung gilt (Grenze, Codex/Tests)")
os.remove(os.path.join(PROJ_DIR, "andere-sitzung.jsonl"))

# ============================================================================= I  --transcript (D3)

print("--- I: --transcript nur unter <HOME>/.claude/projects/<dir>/<session-id>.jsonl; fremde sessionId-Records zaehlen nicht (D3)")
SELF_WRITTEN = os.path.join(T_DIR, f"{SID}.jsonl")
write_transcript(SELF_WRITTEN, [rec_user(USER_SAYS)])
write_transcript(TR, [rec_user("Nichts Passendes in der echten Sitzung")])
PLAN_Q = {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}


def override_refused(label, path):
    mem_ = make_mem()
    rc_, out_ = run(mem_, PLAN_Q, "--transcript", path)
    c_, t_ = one(mem_, "kurz-zuerst"), out_["tally"]
    check(rc_ == 0 and c_.get("quote_verified") == "kein_transkript" and c_.get("status") == "observed"
          and t_["transcript_override_rejected"] is True and t_["quote_transcript"] is None
          and any("refused" in w for w in t_["warnings"]) and not t_["promoted_ids"],
          f"I1 {label}: abgelehnt, 'kein_transkript', Tally-Feld (rc={rc_}, qv={c_.get('quote_verified')}, "
          f"rejected={t_['transcript_override_rejected']})")


override_refused("selbst geschriebene Datei ausserhalb von projects (Befund Q3)", SELF_WRITTEN)
os.makedirs(os.path.join(PROJ_DIR), exist_ok=True)
write_transcript(os.path.join(PROJ_DIR, "egal.jsonl"), [rec_user(USER_SAYS)])
override_refused("richtiges Verzeichnis, falscher Dateiname", os.path.join(PROJ_DIR, "egal.jsonl"))
write_transcript(os.path.join(ISO_HOME, ".claude", "ausserhalb", f"{SID}.jsonl"), [rec_user(USER_SAYS)])
override_refused("Pfad klettert per .. aus projects heraus", os.path.join(PROJ_DIR, "..", "..", "ausserhalb", f"{SID}.jsonl"))
write_transcript(os.path.join(ISO_HOME, ".claude", "projects-evil", f"{SID}.jsonl"), [rec_user(USER_SAYS)])
override_refused("Nachbarordner mit gleichem Namensanfang (projects-evil)", os.path.join(ISO_HOME, ".claude", "projects-evil", f"{SID}.jsonl"))
override_refused("Datei direkt in projects (es fehlt das Projektverzeichnis)", os.path.join(ISO_HOME, ".claude", "projects", f"{SID}.jsonl"))
write_transcript(os.path.join(PROJ_DIR, "tief", f"{SID}.jsonl"), [rec_user(USER_SAYS)])
override_refused("zwei Ebenen unter projects", os.path.join(PROJ_DIR, "tief", f"{SID}.jsonl"))
# I2 die Gegenprobe: unter projects, richtiger Name, anderes Projektverzeichnis -> angenommen
other = os.path.join(ISO_HOME, ".claude", "projects", "proj-zwei", f"{SID}.jsonl")
write_transcript(other, [rec_user(USER_SAYS)])
mem = make_mem()
rc, out = run(mem, PLAN_Q, "--transcript", other)
check(one(mem, "kurz-zuerst").get("quote_verified") is True and out["tally"]["quote_transcript"] == other
      and not out["tally"]["transcript_override_rejected"],
      "I2 ein zulaessiger Pfad (anderes Projektverzeichnis) wird angenommen")
# I3 (Befund Q4b) Records mit fremder sessionId im eigenen Transkript zaehlen nicht
drop_home_transcript()
write_transcript(TR, [rec_user(USER_SAYS, sessionId="ganz-fremd"), rec_user("Etwas anderes Harmloses hier", sessionId=SID)])
mem = make_mem()
rc, out = run(mem, PLAN_Q, "--transcript", TR)
c = one(mem, "kurz-zuerst")
check(c.get("quote_verified") is False and out["tally"]["quote_reasons"] == {"kurz-zuerst": "not_found"},
      f"I3 ein Record mit fremder sessionId belegt nichts (qv={c.get('quote_verified')}, {out['tally']['quote_reasons']})")
write_transcript(TR, [rec_user(USER_SAYS, sessionId=SID)])
mem = make_mem()
rc, out = run(mem, PLAN_Q, "--transcript", TR)
check(one(mem, "kurz-zuerst").get("quote_verified") is True, "I3 derselbe Record mit der eigenen sessionId zaehlt")

# ============================================================================= J  Quellen (D4)

print("--- J: Modell-, Paste- und Harness-Texte sind keine Worte des Users (D4)")


def raw_user(content, **extra):
    r = {"type": "user", "message": {"role": "user", "content": content}}
    r.update(extra)
    return r


def blocks(*texts):
    return [{"type": "text", "text": t} for t in texts]


J_CASES = (
    ("suggestion_accepted (Modelltext, vom User nur angenommen; Befund Q8)",
     [rec_user(USER_SAYS, promptSource="suggestion_accepted")], QUOTE),
    ("pasted_content-Block (Befund Q9)",
     [rec_user('schau mal <pasted_content id="ab12">' + USER_SAYS + "</pasted_content> bitte")], QUOTE),
    ("ide_selection-Block (Befund Q10)",
     [raw_user(blocks("<ide_selection>Die Auswahl: " + USER_SAYS + "</ide_selection>", "was meinst du?"))], QUOTE),
    ("ide_opened_file-Block",
     [raw_user(blocks("<ide_opened_file>Die Datei: " + USER_SAYS + "</ide_opened_file>", "was meinst du?"))], QUOTE),
    ("teammate-message-Block ohne origin (Befund Q10b)",
     [raw_user('<teammate-message teammate_id="x">' + USER_SAYS + "</teammate-message>")], QUOTE),
    ("verschachtelter system-reminder, Rest hinter dem inneren Ende (Befund Q11)",
     [rec_user("hallo <system-reminder>a <system-reminder>b</system-reminder> " + USER_SAYS + "</system-reminder>")], QUOTE),
    ("verschachtelter pasted_content in system-reminder",
     [rec_user("<system-reminder>x <pasted_content>y</pasted_content> " + USER_SAYS + "</system-reminder>")], QUOTE),
    ("Unterbrechungszeile des Harness (Befund Q13)",
     [raw_user(blocks("[Request interrupted by user for tool use]"))], "Request interrupted by user for tool use"),
    ("turnOrigin sdk ohne origin und ohne promptSource",
     [raw_user(USER_SAYS, turnOrigin="sdk")], QUOTE),
    ("promptSource sdk ohne turnOrigin und ohne origin",
     [raw_user(USER_SAYS, promptSource="sdk")], QUOTE),
    ("promptSource system ohne turnOrigin und ohne origin",
     [raw_user(USER_SAYS, promptSource="system")], QUOTE),
    ("turnOrigin scheduled", [raw_user(USER_SAYS, turnOrigin="scheduled")], QUOTE),
    ("turnOrigin peer", [raw_user(USER_SAYS, turnOrigin="peer")], QUOTE),
)
for label, recs, quote in J_CASES:
    mem = make_mem()
    write_transcript(TR, [rec_user("Etwas ganz anderes"), *recs])
    rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=quote)]}, "--transcript", TR)
    c = one(mem, "kurz-zuerst")
    check(rc == 0 and c.get("quote_verified") is False and c.get("status") == "observed" and not out["tally"]["promoted_ids"],
          f"J1 {label}: nicht verifiziert (qv={c.get('quote_verified')})")
J_OK = (
    ("promptSource typed", [rec_user(USER_SAYS, promptSource="typed")]),
    ("promptSource queued (waehrend der Arbeit getippt)", [rec_user(USER_SAYS, promptSource="queued")]),
    ("Altformat ohne origin/promptSource/turnOrigin", [rec_user_old(USER_SAYS)]),
    ("eigener Text neben einem pasted_content-Block", [rec_user("schau mal <pasted_content>Fremdtext</pasted_content> " + USER_SAYS)]),
    ("eigener Text neben einem ide_selection-Block", [raw_user(blocks("<ide_selection>Auswahl</ide_selection>", USER_SAYS))]),
)
for label, recs in J_OK:
    mem = make_mem()
    write_transcript(TR, recs)
    rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, "--transcript", TR)
    check(one(mem, "kurz-zuerst").get("quote_verified") is True, f"J2 Gegenprobe, zaehlt als Wort des Users: {label}")
if iq is None or not hasattr(iq, "locate"):
    check(False, "J3 identity_quote._strip_injected/locate vorhanden")
else:
    nested = iq._strip_injected("a <system-reminder>b <system-reminder>c</system-reminder> d</system-reminder> e")
    check(nested.split() == ["a", "e"], f"J3 _strip_injected: verschachtelte Bloecke fallen bis zum Fixpunkt ({nested!r})")
    check(iq._strip_injected("a <system-reminder>nie geschlossen").strip() == "a", "J3 ein nie geschlossener Block wird bis zum Ende entfernt")
    check(iq._strip_injected("vorher [Request interrupted by user] nachher").split() == ["vorher", "nachher"], "J3 die Unterbrechungszeile faellt weg")

# ============================================================================= K  Teilstring-Schwaeche (D5)

print("--- K: Wortgrenzen, mindestens 3 Woerter, Negations-Waechter (D5)")


def one_quote(text, quote, key="kq"):
    mem_ = make_mem()
    write_transcript(TR, [rec_user(text)])
    rc_, out_ = run(mem_, {"date": DATE, "user_candidates": [cand(key, confirmed=True, quote=quote)]}, "--transcript", TR)
    return rc_, one(mem_, key), out_, mem_


# K1 (Befund Q5) ein Bruchstueck aus der Mitte eines Wortes
rc, c, out, mem = one_quote("Ich arbeite niemals nachts gern an Code", "beite niemals nach")
check(rc == 0 and c.get("quote_verified") is False and c.get("status") == "observed" and out["tally"]["quote_reasons"] == {"kq": "word_boundary"},
      f"K1 Bruchstueck mitten im Wort (Q5): nicht verifiziert, Grund word_boundary ({out['tally']['quote_reasons']})")
rc, c, out, mem = one_quote("Ich arbeite niemals nachts gern an Code", "arbeite niemals nac")
check(c.get("quote_verified") is False, "K1 auch ein Zitat, das nur rechts mitten im Wort endet, zaehlt nicht")
rc, c, out, mem = one_quote("Ich arbeite niemals nachts gern an Code", "arbeite niemals nachts gern")
check(c.get("quote_verified") is True, "K1 Gegenprobe: an Wortgrenzen beginnend und endend zaehlt es")
rc, c, out, mem = one_quote("Ich arbeite niemals nachts gern an Code", "niemals nachts gern an Code")
check(c.get("quote_verified") is True, "K1 Gegenprobe: das Zitat reicht bis zum Ende der Nachricht")
# K2 (Befund Q6) die Negation vor dem Zitat
rc, c, out, mem = one_quote("Ich mag keine langen Antworten mit vielen Tabellen", "langen Antworten mit vielen Tabellen", "lang")
check(rc == 0 and c.get("quote_verified") is False and out["tally"]["quote_reasons"] == {"lang": "negation"}
      and not out["tally"]["promoted_ids"] and "lange Antworten" not in read(mem, "identity/user.md"),
      f"K2 Negation direkt vor dem Zitat (Q6): nicht verifiziert, nichts promotet ({out['tally']['quote_reasons']})")
# K3 die Negation hinter dem Zitat
rc, c, out, mem = one_quote("Alle Antworten mit Tabellen sind nicht gewuenscht", "Alle Antworten mit Tabellen sind")
check(c.get("quote_verified") is False and out["tally"]["quote_reasons"] == {"kq": "negation"}, "K3 Negation direkt hinter dem Zitat: nicht verifiziert")
# K4 eine Negation INNERHALB des Zitats ist in Ordnung (nur die Nachbarwoerter zaehlen)
rc, c, out, mem = one_quote("Ich will nie wieder ungefragt pushen", "nie wieder ungefragt pushen")
check(c.get("quote_verified") is True, "K4 Negation im Zitat selbst: zaehlt (der Waechter prueft nur die Nachbarwoerter)")
# K5 (Befund Q7) eine allgemeine Kurzantwort
rc, c, out, mem = one_quote("ja, gerne", "ja, gerne", "gern")
check(c.get("quote_verified") is False and out["tally"]["quote_reasons"] == {"gern": "too_few_words"},
      f"K5 zwei Woerter (Q7): nicht verifiziert ({out['tally']['quote_reasons']})")
rc, c, out, mem = one_quote("ja gerne bitte", "ja gerne bitte", "gern")
check(c.get("quote_verified") is True, "K5 drei Woerter reichen")
rc, c, out, mem = one_quote("a b c d e f", "a b c", "kurz")
check(c.get("quote_verified") is False and out["tally"]["quote_reasons"] == {"kurz": "too_short"}, "K5 drei Woerter, aber unter 8 Zeichen: zu kurz")
# K6 mehrere Fundstellen: eine saubere genuegt
rc, c, out, mem = one_quote("Keine Tabellen im Fliesstext. Tabellen im Fliesstext sind aber erlaubt", "Tabellen im Fliesstext", "mehr")
check(c.get("quote_verified") is True, "K6 zwei Fundstellen, nur die erste ist negiert: die zweite genuegt")
# K7 jedes Negationswort, vor und hinter dem Zitat (Einheit)
if iq is None or not hasattr(iq, "locate"):
    check(False, "K7 identity_quote.locate existiert")
else:
    NEGS = ("nicht", "nie", "niemals", "kein", "keine", "keinen", "keinem", "keiner", "keines", "not", "never", "no", "don't")
    bad_before = [n for n in NEGS if iq.locate("jede antwort mit tabellen", f"ich will {n} jede antwort mit tabellen") != "negation"]
    bad_after = [n for n in NEGS if iq.locate("antworten mit tabellen", f"antworten mit tabellen {n} bitte") != "negation"]
    check(not bad_before and not bad_after, f"K7 alle Negationswoerter greifen vor und hinter dem Zitat (vor: {bad_before}, nach: {bad_after})")
    check(iq.locate("jede antwort mit tabellen", "ich will noch jede antwort mit tabellen") is True
          and iq.locate("antworten mit tabellen", "antworten mit tabellen nett bitte") is True,
          "K7 ein anderes Nachbarwort loest nichts aus")
    check(iq.locate("jede antwort mit tabellen", "keinesfalls ich will heute etwas, jede antwort mit tabellen") is True,
          "K7 ein Negationswort 4 Woerter vor dem Zitat zaehlt nicht (die Reichweite ist 3 Woerter, siehe R)")
    typo = iq.normalize("I don’t want long answers")
    check(iq.locate("want long answers", typo) == "negation", "K7 typografischer Apostroph (don’t) wird normalisiert und erkannt")
    check(iq.locate("zusammenfassung zuerst", "xzusammenfassung zuerst") == "word_boundary"
          and iq.locate("zusammenfassung zuerst", "zusammenfassung zuerstx") == "word_boundary"
          and iq.locate("zusammenfassung zuerst", "gar nichts davon") is None,
          "K7 locate unterscheidet word_boundary und nicht gefunden")

# ============================================================================= L  Supersede-Haerten (D6)

print("--- L: Supersede-Haerten (D6)")
write_transcript(TR, [rec_user(SUP_SAYS)])
PRE_MD = ("# User Profile\n- Praeambel-Stichpunkt bleibt (UC1, 2026-07-01)\n\n"
          "## Preferences\n- Antworten kurz halten (UC3, 2026-07-01)\n").encode("utf-8")
PRE_LINE = "- Praeambel-Stichpunkt bleibt (UC1, 2026-07-01)"
for label, raw in (("LF", PRE_MD), ("CRLF + BOM", b"\xef\xbb\xbf" + PRE_MD.replace(b"\n", b"\r\n"))):
    rejected(f"Stichpunkt in der Praeambel als Ziel per match, {label} (Befund S1)", make_mem(raw),
             with_other(dict(SUP, match=PRE_LINE)), needle="preamble")
    rejected(f"Stichpunkt in der Praeambel als Ziel per line_id, {label} (Befund S1b)", make_mem(raw),
             with_other({"line_id": "UC1", "new_text": "- ersetzt", "reason": "r", "plain": PLAIN, "quote": SUP_QUOTE}), needle="preamble")
mem = make_mem(b"# User Profile\n- Alles ist Praeambel (UC1, 2026-07-01)\n")
rejected("Datei ohne jede ##-Ueberschrift: alles ist Praeambel", mem, with_other(dict(SUP, match="- Alles ist Praeambel (UC1, 2026-07-01)")), needle="preamble")
mem = make_mem(PRE_MD)
rc, out = run(mem, sup_plan(SUP))
check(rc == 0 and read_bytes(mem, "identity/user.md") == PRE_MD.replace(OLD.encode("utf-8"), NEW.encode("utf-8")),
      f"L1 Gegenprobe: dieselbe Datei, Ziel in einer Sektion -> ersetzt, Praeambel bleibt (rc={rc}, {out.get('error')})")

# L2 (Befund S2) Wiederholung mit line_id nach Erfolg ist idempotent
mem = make_mem()
LP = sup_plan({"line_id": "UC3", "new_text": "- Antworten ausfuehrlich, mit Beispielen", "reason": "Dominic: lieber ausfuehrlich",
               "plain": PLAIN, "quote": SUP_QUOTE})
rc1, _ = run(mem, LP)
before = snapshot(mem)
rc2, out2 = run(mem, LP)
check(rc1 == 0 and rc2 == 0 and snapshot(mem) == before and out2["tally"]["supersede_skipped_unchanged"] == 1
      and out2["tally"]["user_lines_superseded"] == 0 and len(load(mem, "identity/user-changelog.json")) == 1,
      f"L2 Wiederholung mit line_id: rc=0, kein Byte geaendert, als bereits angewendet gezaehlt (rc2={rc2}, {out2.get('error')})")
write(mem, "identity/user-changelog.json", "[]\n")
rejected("L2 ohne Changelog-Beleg wird nicht geraten (line_id)", mem, LP, needle="no such line")
mem = make_mem()
run(mem, LP)
write(mem, "identity/user.md", read(mem, "identity/user.md").replace("Antworten ausfuehrlich, mit Beispielen", "etwas ganz anderes"))
rejected("L2 steht der Text nicht mehr in der Datei, gilt es nicht als angewendet", mem, LP, needle="no such line")
mem = make_mem()
run(mem, LP)
rejected("L2 anderer new_text zur selben line_id gilt nicht als angewendet", mem,
         sup_plan({"line_id": "UC3", "new_text": "- ganz andere Zeile", "reason": "r", "plain": PLAIN, "quote": SUP_QUOTE}), needle="no such line")

# L3 (Befund S3) zwei Eintraege mit demselben new_text oder demselben Ziel
rejected("zwei Eintraege mit gleichem new_text (Befund S3)", make_mem(),
         with_other({"line_id": "UC3", "new_text": "- Gleiche neue Zeile", "reason": "r", "plain": PLAIN, "quote": SUP_QUOTE},
                    {"line_id": "UC4", "new_text": "- Gleiche neue Zeile", "reason": "r", "plain": PLAIN, "quote": SUP_QUOTE}), needle="same")
rejected("dasselbe Ziel einmal per match, einmal per line_id", make_mem(),
         with_other(SUP, {"line_id": "UC3", "new_text": "- anders", "reason": "r", "plain": PLAIN, "quote": SUP_QUOTE}), needle="twice")
rejected("dasselbe Ziel zweimal, einmal davon ohne Aenderung", make_mem(), with_other(SUP, dict(SUP, new_text=OLD)), needle="twice")

# L4 (Befund S8) jeder Zeilentrenner in new_text
SEPARATORS = (("\r", "CR"), ("\n", "LF"), ("\v", "VT"), ("\f", "FF"), ("\x1c", "FS"), ("\x1d", "GS"), ("\x1e", "RS"),
              ("\x85", "NEL"), ("\u2028", "U+2028"), ("\u2029", "U+2029"))
for ch, name in SEPARATORS:
    rejected(f"new_text mit Zeilentrenner {name}", make_mem(), with_other(dict(SUP, new_text=f"- erste{ch}- zweite")), needle="one line")

# ============================================================================= M  Supersede braucht ein verifiziertes Zitat (D7)

print("--- M: Supersede braucht ein verifiziertes Zitat (D7)")
write_transcript(TR, [rec_user(SUP_SAYS)])
mem = make_mem()
rc, out = run(mem, sup_plan(SUP))
e = (load(mem, "identity/user-changelog.json") or [{}])[0]
check(rc == 0 and read(mem, "identity/user.md") == BASE_USER_MD.replace(OLD, NEW) and e.get("quote") == SUP_QUOTE
      and e.get("quote_verified") is True and out["tally"]["supersede_skipped_unverified"] == 0
      and out["tally"]["user_lines_superseded"] == 1 and out["tally"]["quote_transcript"] == TR,
      f"M1 verifiziertes Zitat: angewendet, der Changelog traegt Zitat und Pruefergebnis (rc={rc}, {out.get('error')})")


def skipped(label, mem_, plan_, *extra, **kw):
    """Ein nicht verifiziertes Zitat ueberspringt den Eintrag: rc 0, user.md und Changelog unveraendert, Tally und Warnung."""
    before_ = snapshot(mem_)
    rc_, out_ = run(mem_, plan_, *extra, **kw)
    after_ = snapshot(mem_)
    t_ = out_["tally"]
    same = (after_.get("identity/user.md") == before_.get("identity/user.md")
            and after_.get("identity/user-changelog.json") == before_.get("identity/user-changelog.json"))
    check(rc_ == 0 and same and t_["supersede_skipped_unverified"] == 1 and t_["user_lines_superseded"] == 0
          and any("supersede" in w.lower() for w in t_["warnings"]),
          f"M2 {label}: Eintrag uebersprungen, nichts geschrieben, Warnung und Tally (rc={rc_}, skipped={t_['supersede_skipped_unverified']}, "
          f"{str(out_.get('error'))[:60]})")
    return out_


mem = make_mem()
out = skipped("Zitat steht nicht im Transkript", mem,
              sup_plan(dict(SUP, quote="das hat er nie gesagt und schon gar nicht so"), iterations=[{"title": "Laeuft trotzdem"}]))
check("Laeuft trotzdem" in read(mem, "iterations/iteration-log.md"), "M2 der Rest des Plans laeuft normal")
skipped("Sitzung ohne Transkript", make_mem(), sup_plan(SUP), sid="sess-ohne-transkript")
skipped("Sitzungsbindung verletzt (D2)", make_mem(), sup_plan(SUP), env_extra=REAL_SESSION)
skipped("Zitat zu kurz", make_mem(), sup_plan(dict(SUP, quote="ja, gerne")))
skipped("Zitat mit zwei Woertern", make_mem(), sup_plan(dict(SUP, quote="ausfuehrliche Antworten")))
skipped("--transcript ausserhalb von projects", make_mem(), sup_plan(SUP), "--transcript", SELF_WRITTEN)
write_transcript(TR, [rec_user("Bei der Fehlersuche will ich keine ausfuehrlichen Antworten mit allen Details.")])
skipped("Zitat steht hinter einer Negation", make_mem(), sup_plan(dict(SUP, quote="ausfuehrlichen Antworten mit allen Details")))
write_transcript(TR, [rec_user(SUP_SAYS, promptSource="suggestion_accepted")])
skipped("Zitat nur in einem angenommenen Vorschlag", make_mem(), sup_plan(SUP))
write_transcript(TR, [rec_user(SUP_SAYS)])
# M3 gemischt: ein Eintrag verifiziert, einer nicht -> genau einer wird angewendet
mem = make_mem()
before = snapshot(mem)
rc, out = run(mem, sup_plan(SUP, {"line_id": "UC4", "new_text": "- Immer mit Quelle belegen und Datum nennen",
                                  "reason": "r", "plain": PLAIN, "quote": "das hat er nie gesagt und schon gar nicht so"}))
text = read(mem, "identity/user.md")
log = load(mem, "identity/user-changelog.json")
check(rc == 0 and NEW in text and "Immer mit Quelle belegen (UC4, 2026-07-02)" in text and "Datum nennen" not in text and len(log) == 1
      and out["tally"]["user_lines_superseded"] == 1 and out["tally"]["supersede_skipped_unverified"] == 1,
      f"M3 gemischter Plan: der verifizierte Eintrag gilt, der andere wird uebersprungen (rc={rc}, superseded={out['tally']['user_lines_superseded']})")
# M4 ein uebersprungener Eintrag wird trotzdem auf Formfehler geprueft: ein unbekanntes Ziel bleibt ein Plan-Fehler
rejected("unverifiziertes Zitat UND unbekanntes Ziel", make_mem(),
         sup_plan(dict(SUP, match="- Gibt es nicht", quote="das hat er nie gesagt und schon gar nicht so")), needle="no such line")
# M5 dry-run meldet dasselbe, schreibt nichts
mem = make_mem()
before = snapshot(mem)
rc, out = run(mem, sup_plan(SUP, dict(SUP, match="- Immer mit Quelle belegen (UC4, 2026-07-02)", new_text="- Quelle immer nennen",
                                      quote="das hat er nie gesagt und schon gar nicht so")), "--dry-run")
check(rc == 0 and snapshot(mem) == before and out["tally"]["user_lines_superseded"] == 1 and out["tally"]["supersede_skipped_unverified"] == 1,
      "M5 dry-run: meldet Anwendung und Ueberspringen, schreibt nichts")

# ============================================================================= N  verlustfreie Promotion (D8)

print("--- N: die Promotion nach user.md ist verlustfrei (D8)")
write_transcript(TR, [rec_user(SUP_SAYS)])
PROMO_LINE = "- Reifer Kandidat (UC9, 2026-10-27)"


def ripe(**kw):
    return qrow("reif", id="UC9", observation="Reifer Kandidat", status="confirmed", occurrences=2, sessions=["a", "b"], **kw)


def promote_bytes(label, md, expected, plan=None, queue=None):
    mem_ = make_mem_q(queue if queue is not None else [ripe()], md)
    rc_, out_ = run(mem_, plan or {"date": DATE, "user_candidates": []})
    got = read_bytes(mem_, "identity/user.md")
    check(rc_ == 0 and got == expected and out_["tally"]["promoted_ids"] == ["UC9"],
          f"{label} (rc={rc_}, {out_.get('error')}, {'gleich' if got == expected else 'ABWEICHUNG ' + repr(got[-120:])})")
    return mem_


CRLF_BOM_MD = (b"\xef\xbb\xbf# User Profile\r\n\r\n*Initialized: 2026-07-01*\r\n\r\n"
               b"## Preferences\r\n- Antworten kurz halten (UC3, 2026-07-01)\r\n")
# N1 (Befund S5) Supersede und Promotion im selben Lauf auf CRLF + BOM + Praeambel
promote_bytes("N1 Supersede + Promotion im selben Lauf: BOM, CRLF, Praeambel bleiben, nur Zielzeile und neue Zeile aendern sich",
              CRLF_BOM_MD, CRLF_BOM_MD.replace(OLD.encode("utf-8"), NEW.encode("utf-8")) + (PROMO_LINE + "\r\n").encode("utf-8"),
              plan=sup_plan(SUP, user_candidates=[]))
promote_bytes("N2 Promotion allein auf CRLF + BOM + Praeambel: nur die neue Zeile kommt hinzu",
              CRLF_BOM_MD, CRLF_BOM_MD + (PROMO_LINE + "\r\n").encode("utf-8"))
# N3 LF-Datei mit mehreren Sektionen und eigener Sektion: die Zeile landet am Ende ihrer Sektion
promote_bytes("N3 Einfuegen am Ende der passenden Sektion (Work Style), alles andere byte-gleich",
              BASE_USER_MD.encode("utf-8"),
              BASE_USER_MD.replace("- (Will be populated through session observations)\n",
                                   "- (Will be populated through session observations)\n" + PROMO_LINE + "\n").encode("utf-8"),
              queue=[ripe(signal_type="workflow")])
promote_bytes("N3 Preferences: nach der letzten Zeile der Sektion, vor der Leerzeile",
              BASE_USER_MD.encode("utf-8"),
              BASE_USER_MD.replace("- Immer mit Quelle belegen (UC4, 2026-07-02)\n",
                                   "- Immer mit Quelle belegen (UC4, 2026-07-02)\n" + PROMO_LINE + "\n").encode("utf-8"))
# N4 fehlende Sektion: wird am Ende angehaengt, die Reihenfolge der anderen bleibt
NO_WS = b"# User Profile\n\n## Preferences\n- Antworten kurz halten (UC3, 2026-07-01)\n"
promote_bytes("N4 fehlende Sektion wird am Dateiende angelegt (LF)", NO_WS,
              NO_WS + b"\n## Work Style\n\n" + PROMO_LINE.encode("utf-8") + b"\n", queue=[ripe(signal_type="workflow")])
NO_WS_CRLF = NO_WS.replace(b"\n", b"\r\n")
promote_bytes("N4 fehlende Sektion wird am Dateiende angelegt (CRLF)", NO_WS_CRLF,
              NO_WS_CRLF + b"\r\n## Work Style\r\n\r\n" + PROMO_LINE.encode("utf-8") + b"\r\n", queue=[ripe(signal_type="workflow")])
# N5 letzte Zeile ohne Zeilenende: das Dateiende bleibt ohne Zeilenende
NO_EOL = b"# User Profile\n\n## Preferences\n- Antworten kurz halten (UC3, 2026-07-01)"
promote_bytes("N5 Datei ohne Zeilenende am Schluss: es bleibt dabei", NO_EOL,
              NO_EOL + b"\n" + PROMO_LINE.encode("utf-8"))
# N6 Sektionsreihenfolge, Leerzeichen am Zeilenende und gemischte Zeilenenden bleiben
MIXED = ("# User Profile\r\n\r\n*Initialized: 2026-07-01*\r\n\r\n"
         "## Work Style\r\n- Arbeitet abends\r\n\r\n"
         "## Preferences\r\n- Antworten kurz halten (UC3, 2026-07-01)\r\n- Zeile mit Leerzeichen am Ende  \r\n\r\n"
         "## Eigene Sektion\r\n- Letzte Zeile ohne Zeilenende").encode("utf-8")
promote_bytes("N6 Sektionsreihenfolge und Leerzeichen bleiben, die Zeile steht hinter der letzten Preferences-Zeile",
              MIXED, MIXED.replace(b"- Zeile mit Leerzeichen am Ende  \r\n", b"- Zeile mit Leerzeichen am Ende  \r\n" + PROMO_LINE.encode("utf-8") + b"\r\n"))
# N7 zwei Kandidaten in einem Lauf: beide, in Queue-Reihenfolge
two = [ripe(), qrow("zweiter", id="UC10", observation="Zweiter Kandidat", status="confirmed", occurrences=2, sessions=["a", "b"])]
mem = make_mem_q(two, NO_WS)
rc, out = run(mem, {"date": DATE, "user_candidates": []})
check(read_bytes(mem, "identity/user.md") == NO_WS + (PROMO_LINE + "\n- Zweiter Kandidat (UC10, 2026-10-27)\n").encode("utf-8"),
      "N7 zwei Kandidaten derselben Sektion in einem Lauf: beide, in Queue-Reihenfolge")
# N8 gibt es noch keine user.md, entsteht die kanonische Form wie bisher
mem = make_mem_q([ripe()])
os.remove(os.path.join(mem, "identity/user.md"))
rc, out = run(mem, {"date": DATE, "user_candidates": []})
check(rc == 0 and read(mem, "identity/user.md") == "# User Profile\n\n## Preferences\n\n" + PROMO_LINE + "\n",
      "N8 fehlende user.md: kanonische Form wie bisher")

# ============================================================================= O  keine Wiederkehr widerlegter Aussagen (D9)

print("--- O: eine per Supersede widerlegte Aussage kehrt nicht per Promotion zurueck (D9)")
write_transcript(TR, [rec_user(SUP_SAYS)])
RE_OBS = "Antworten kurz halten"
REJECTED_ROW = qrow("kurz-wieder", id="UC20", observation=RE_OBS, status="confirmed", occurrences=2, sessions=["a", "b"])
mem = make_mem()
run(mem, sup_plan(SUP))
put(mem, "working/user-candidates.json", [REJECTED_ROW])
before_md = read_bytes(mem, "identity/user.md")
before_log = read_bytes(mem, "identity/user-changelog.json")
rc, out = run(mem, {"date": DATE, "user_candidates": []})
t = out["tally"]
check(rc == 0 and t["promotion_blocked_superseded"] == 1 and not t["promoted_ids"] and read_bytes(mem, "identity/user.md") == before_md
      and read_bytes(mem, "identity/user-changelog.json") == before_log and one(mem, "kurz-wieder").get("status") == "confirmed"
      and any("UC20" in w and "supersede" in w.lower() for w in t["warnings"]),
      f"O1 die widerlegte Aussage unter neuem Key wird nicht promotet, Status bleibt, Warnung und Tally (blocked={t['promotion_blocked_superseded']})")
# O2 im selben Lauf: erst Supersede (Changelog), dann Promotion
mem = make_mem_q([REJECTED_ROW, ripe()])
rc, out = run(mem, sup_plan(SUP, user_candidates=[]))
t = out["tally"]
check(rc == 0 and t["promotion_blocked_superseded"] == 1 and t["promoted_ids"] == ["UC9"],
      f"O2 im selben Lauf wie der Supersede: blockiert, der unabhaengige Kandidat wird weiter promotet ({t['promoted_ids']})")
# O3 Gegenprobe: dieselbe Zeile ohne Supersede-Eintrag im Changelog wird promotet
mem = make_mem_q([REJECTED_ROW], b"# User Profile\n\n## Preferences\n- Immer mit Quelle belegen (UC4, 2026-07-02)\n")
rc, out = run(mem, {"date": DATE, "user_candidates": []})
check(rc == 0 and out["tally"]["promoted_ids"] == ["UC20"] and not out["tally"]["promotion_blocked_superseded"],
      "O3 Gegenprobe: ohne Supersede-Eintrag im Changelog wird dieselbe Zeile promotet")
# O4 nur Supersede-Eintraege zaehlen: ein anderer Changelog-Eintrag mit demselben alten Wert blockiert nicht
mem = make_mem_q([REJECTED_ROW], b"# User Profile\n\n## Preferences\n- Immer mit Quelle belegen (UC4, 2026-07-02)\n")
put(mem, "identity/user-changelog.json", [{"ts": "2026-10-01T00:00:00+00:00", "field": "user.md/skipped-duplicate",
                                           "old_value": OLD, "new_value": RE_OBS, "candidate_id": "UC20"}])
rc, out = run(mem, {"date": DATE, "user_candidates": []})
check(out["tally"]["promoted_ids"] == ["UC20"], "O4 nur Eintraege des Felds user.md/supersede sperren eine Wiederkehr")

# ============================================================================= P  Zeile ohne beweisbare Sitzung (D1, Runde 2)

print("--- P: eine Zeile ohne beweisbare Sitzung nimmt die erste echte ID auf, ohne hochzuzaehlen (D1, Runde 2)")
write_transcript(TR, [rec_user(USER_SAYS)])
PLAN_P = {"date": DATE, "user_candidates": [cand("ohne-sid")]}


def last_run(label, mem_, sid_, **kw):
    rc_, out_ = run(mem_, PLAN_P, sid=sid_, **kw)
    return rc_, out_["tally"], dict(one(mem_, "ohne-sid"))


# P1 (Befund A1) leere Sitzungs-ID, danach die echte ID derselben Sitzung: das ist keine zweite Nennung
mem = make_mem()
rc1, t1, c1 = last_run("leer", mem, "")
rc2, t2, c2 = last_run("echt", mem, "sess-real")
check(rc1 == 0 and c1.get("sessions") == [] and c1.get("occurrences") == 1
      and rc2 == 0 and c2.get("occurrences") == 1 and c2.get("status") == "observed" and not c2.get("confirmed_by")
      and c2.get("sessions") == ["sess-real"] and not t2["promoted_ids"] and t2["session_adopted"] == 1
      and t2["repeat_not_counted"] == 0 and "ohne-sid" not in read(mem, "identity/user.md").lower(),
      f"P1 leere ID, dann echte ID (A1): occurrences=1, observed, Sitzung aufgenommen, nichts promotet "
      f"(occ={c2.get('occurrences')}, status={c2.get('status')}, sessions={c2.get('sessions')}, adopted={t2['session_adopted']})")
check("Antworten beginnen mit einer kurzen Zusammenfassung" not in read(mem, "identity/user.md"),
      "P1 die einzelne Nennung steht nicht in user.md")
# P1b derselbe Plan nochmal in der echten Sitzung: weiterhin nichts (jetzt gewoehnliche Wiederholung)
rc3, t3, c3 = last_run("echt nochmal", mem, "sess-real")
check(rc3 == 0 and c3.get("occurrences") == 1 and c3.get("status") == "observed" and c3.get("sessions") == ["sess-real"]
      and t3["repeat_not_counted"] == 1 and not t3["session_adopted"],
      f"P1b dieselbe Sitzung nochmal: keine Aufnahme, keine Zaehlung (occ={c3.get('occurrences')}, status={c3.get('status')})")
# P1c Gegenprobe: eine ANDERE echte Sitzung ist die zweite Nennung und bestaetigt
rc4, t4, c4 = last_run("andere Sitzung", mem, "sess-other")
check(rc4 == 0 and c4.get("occurrences") == 2 and c4.get("status") == "promoted" and c4.get("confirmed_by") == "repeat"
      and c4.get("sessions") == ["sess-real", "sess-other"] and t4["promoted_ids"] == [c4.get("id")] and not t4["session_adopted"],
      f"P1c leer -> Sitzung X -> Sitzung Y: Y ist die zweite Nennung und bestaetigt (occ={c4.get('occurrences')}, "
      f"status={c4.get('status')}, sessions={c4.get('sessions')})")

# P2 (Befund A1b) nur Leerzeichen als Sitzungs-ID
mem = make_mem()
last_run("blank", mem, " ")
rc2, t2, c2 = last_run("echt", mem, "sess-real")
check(rc2 == 0 and c2.get("occurrences") == 1 and c2.get("status") == "observed" and not t2["promoted_ids"]
      and c2.get("sessions") == ["sess-real"] and t2["session_adopted"] == 1,
      f"P2 Sitzungs-ID aus Leerzeichen, dann echte ID (A1b): nicht bestaetigt (occ={c2.get('occurrences')}, status={c2.get('status')})")

# P3 (Befund A2) Sitzungs-Mismatch im ersten Lauf, danach die echte ID
mem = make_mem()
rc1, t1, c1 = last_run("Mismatch", mem, SID, env_extra={"CLAUDE_CODE_SESSION_ID": "die-echte-sitzung"})
rc2, t2, c2 = last_run("echt", mem, "die-echte-sitzung", env_extra={"CLAUDE_CODE_SESSION_ID": "die-echte-sitzung"})
check(rc1 == 0 and t1["session_binding_mismatch"] is True and c1.get("sessions") == []
      and rc2 == 0 and c2.get("occurrences") == 1 and c2.get("status") == "observed" and not t2["promoted_ids"]
      and c2.get("sessions") == ["die-echte-sitzung"] and t2["session_adopted"] == 1,
      f"P3 Mismatch-Lauf, dann echte ID (A2): nicht bestaetigt (occ={c2.get('occurrences')}, status={c2.get('status')}, "
      f"sessions={c2.get('sessions')})")

# P4 ohne beweisbare Sitzung bleibt die Zeile ohne Sitzung: es wird nichts aufgenommen und nichts gezaehlt
mem = make_mem()
last_run("leer", mem, "")
rc2, t2, c2 = last_run("wieder leer", mem, "")
check(rc2 == 0 and c2.get("sessions") == [] and c2.get("occurrences") == 1 and c2.get("status") == "observed"
      and not t2["session_adopted"] and t2["repeat_not_counted"] == 1,
      f"P4 zweiter Lauf ohne Sitzungs-ID: nichts aufgenommen, nichts gezaehlt (sessions={c2.get('sessions')})")

# P5 die Regel gilt nur fuer Zeilen MIT dem Schluessel sessions: eine Altzeile ohne den Schluessel folgt weiter D1 (+1)
mem = make_mem_q([qrow("ohne-sid")])
rc2, t2, c2 = last_run("echt", mem, "sess-real")
check(rc2 == 0 and c2.get("occurrences") == 2 and c2.get("sessions") == ["sess-real"] and c2.get("confirmed_by") == "repeat"
      and not t2["session_adopted"],
      f"P5 Altzeile ohne Schluessel sessions: die erste neue Sitzung zaehlt +1 (occ={c2.get('occurrences')})")
mem = make_mem_q([qrow("ohne-sid", sessions=[])])
rc2, t2, c2 = last_run("echt", mem, "sess-real")
check(rc2 == 0 and c2.get("occurrences") == 1 and c2.get("sessions") == ["sess-real"] and c2.get("status") == "observed"
      and t2["session_adopted"] == 1,
      f"P5 Zeile mit leerem sessions[]: aufgenommen, nicht gezaehlt (occ={c2.get('occurrences')})")
# P5b ein unbrauchbarer Wert im Schluessel (null, Text) gilt wie leer: die sichere Richtung ist nicht zu zaehlen
for bad in (None, "sess-x", 7):
    mem = make_mem_q([qrow("ohne-sid", sessions=bad)])
    rc2, t2, c2 = last_run("echt", mem, "sess-real")
    check(rc2 == 0 and c2.get("occurrences") == 1 and c2.get("sessions") == ["sess-real"] and not t2["promoted_ids"],
          f"P5b sessions={bad!r} (kein Listenwert) zaehlt nicht hoch (occ={c2.get('occurrences')}, sessions={c2.get('sessions')})")

# P6 die Aufnahme sperrt den Zitatweg nicht: ein verifiziertes Zitat bestaetigt auch eine Zeile ohne Sitzung
mem = make_mem()
last_run("leer", mem, "")
rc2, out2 = run(mem, {"date": DATE, "user_candidates": [cand("ohne-sid", confirmed=True, quote=QUOTE)]}, "--transcript", TR)
c2 = one(mem, "ohne-sid")
check(rc2 == 0 and c2.get("status") == "promoted" and c2.get("confirmed_by") == "quote" and c2.get("occurrences") == 1
      and c2.get("sessions") == [SID],
      f"P6 verifiziertes Zitat bestaetigt weiterhin (status={c2.get('status')}, confirmed_by={c2.get('confirmed_by')})")

# ============================================================================= Q  Junction/Symlink unter projects (D3, Runde 2)

print("--- Q: ein Junction/Symlink unter projects fuehrt aus projects hinaus -> die Datei zaehlt nicht (D3, Runde 2)")


def make_dir_link(link, target):
    """Verzeichnis-Junction (Windows, ohne Adminrechte) bzw. Symlink (POSIX). True, wenn angelegt."""
    try:
        if os.name == "nt":
            comspec = os.environ.get("COMSPEC") or "cmd.exe"
            res = subprocess.run([comspec, "/c", "mklink", "/J", link, target], capture_output=True, env=ENV)
            return res.returncode == 0 and os.path.isdir(link)
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        return False


def drop_dir_link(link):
    try:
        os.rmdir(link)  # Junction: nur der Link verschwindet, nie das Ziel
    except OSError:
        try:
            os.unlink(link)  # POSIX-Symlink
        except OSError:
            pass


drop_home_transcript()
JX_SID = "sess-jx"
JX_OUTSIDE = os.path.join(ISO_HOME, ".claude", "ausserhalb-jx")
write_transcript(os.path.join(JX_OUTSIDE, f"{JX_SID}.jsonl"), [rec_user(USER_SAYS)])
os.makedirs(os.path.join(ISO_HOME, ".claude", "projects"), exist_ok=True)
JX_LINK = os.path.join(ISO_HOME, ".claude", "projects", "jx")
JX_PLAN = {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}
if not make_dir_link(JX_LINK, JX_OUTSIDE):
    print("  SKIP: Q - hier laesst sich kein Verzeichnis-Link anlegen (Junction/Symlink); die Faelle Q1-Q3 laufen nicht")
else:
    try:
        # Q1 (Probe B2) --transcript ueber den Link: der Pfad sieht richtig aus, die Datei liegt aber ausserhalb
        mem = make_mem()
        rc, out = run(mem, JX_PLAN, "--transcript", os.path.join(JX_LINK, f"{JX_SID}.jsonl"), sid=JX_SID)
        c, t = one(mem, "kurz-zuerst"), out["tally"]
        check(rc == 0 and c.get("quote_verified") == "kein_transkript" and c.get("status") == "observed"
              and t["transcript_override_rejected"] is True and t["quote_transcript"] is None and not t["promoted_ids"],
              f"Q1 --transcript ueber einen Link nach ausserhalb: abgelehnt (qv={c.get('quote_verified')}, "
              f"rejected={t['transcript_override_rejected']})")
        # Q2 (Probe B2b) die Standardsuche ohne --transcript folgt dem Link nicht
        mem = make_mem()
        rc, out = run(mem, JX_PLAN, sid=JX_SID)
        c, t = one(mem, "kurz-zuerst"), out["tally"]
        check(rc == 0 and c.get("quote_verified") == "kein_transkript" and c.get("status") == "observed"
              and t["quote_transcript"] is None and not t["promoted_ids"],
              f"Q2 Standardsuche: eine Datei hinter einem Link nach ausserhalb ist kein Transkript (qv={c.get('quote_verified')}, "
              f"transcript={t['quote_transcript']})")
        # Q3 Gegenprobe: dieselbe Datei in einem gewoehnlichen Projektverzeichnis zaehlt
        write_transcript(os.path.join(PROJ_DIR, f"{JX_SID}.jsonl"), [rec_user(USER_SAYS)])
        mem = make_mem()
        rc, out = run(mem, JX_PLAN, sid=JX_SID)
        check(rc == 0 and one(mem, "kurz-zuerst").get("quote_verified") is True
              and os.path.normcase(os.path.dirname(str(out["tally"]["quote_transcript"]))) == os.path.normcase(PROJ_DIR),
              "Q3 Gegenprobe: dasselbe Transkript in einem gewoehnlichen Projektverzeichnis wird gefunden und verifiziert")
        # Q4 Einheit: resolve_transcript und override_allowed
        if iq is not None:
            with inproc_env():
                via_link = os.path.join(JX_LINK, f"{JX_SID}.jsonl")
                os.remove(os.path.join(PROJ_DIR, f"{JX_SID}.jsonl"))
                check(iq.override_allowed(via_link, JX_SID) is False and iq.resolve_transcript(JX_SID) is None
                      and iq.resolve_transcript(JX_SID, via_link) is None,
                      "Q4 override_allowed und resolve_transcript lehnen den Pfad ueber den Link ab")
    finally:
        drop_dir_link(JX_LINK)
drop_home_transcript()

# ============================================================================= R  Negations-Reichweite (D5, Runde 2)

print("--- R: Negation bis zu 3 Woerter vor/hinter dem Zitat, mehr Negationswoerter (D5, Runde 2)")
write_transcript(TR, [rec_user(USER_SAYS)])
for label, text, quote in (
        ("C3 'Auf keinen Fall ...'", "Auf keinen Fall lange Antworten schreiben", "lange Antworten schreiben"),
        ("C4 'Nie wieder ...'", "Nie wieder lange Antworten schreiben", "lange Antworten schreiben"),
        ("C5 '... gut? Nein.'", "Lange Antworten sind gut? Nein.", "Lange Antworten sind gut"),
        ("C6 'ohne ...'", "Bitte ohne lange Antworten und Tabellen", "lange Antworten und Tabellen"),
        ("C7 \"won't\"", "I won't accept long answers here", "accept long answers here"),
        ("weder ... noch", "Das will weder lange Antworten noch Tabellen", "lange Antworten noch Tabellen"),
        ("nichts", "Davon halte ich nichts: lange Antworten schreiben", "lange Antworten schreiben")):
    rc, c, out, mem = one_quote(text, quote, "neg")
    check(rc == 0 and c.get("quote_verified") is False and out["tally"]["quote_reasons"] == {"neg": "negation"}
          and c.get("status") == "observed" and not out["tally"]["promoted_ids"],
          f"R1 {label}: nicht verifiziert, Grund negation (qv={c.get('quote_verified')}, {out['tally']['quote_reasons']})")
rc, c, out, mem = one_quote("Ich will gern lange Antworten schreiben lassen", "lange Antworten schreiben lassen", "neg")
check(c.get("quote_verified") is True, "R1 Gegenprobe: ohne Negationswort in der Naehe zaehlt das Zitat")
# R2 Einheit: jedes Negationswort, in Abstand 1 bis 3 vor und hinter dem Zitat; Abstand 4 loest nichts mehr aus
if iq is None or not hasattr(iq, "locate"):
    check(False, "R2 identity_quote.locate existiert")
else:
    NEGS2 = ("nicht", "nie", "niemals", "kein", "keine", "keinen", "keinem", "keiner", "keines", "keinesfalls", "nein", "ohne",
             "weder", "nichts", "niemand", "nirgends", "not", "never", "no", "without", "cannot", "nothing", "neither", "nor",
             "nobody", "don't", "dont", "won't", "can't", "shouldn't", "isn't", "doesn't", "couldn't")
    FILL = ("alpha", "beta", "gamma", "delta")
    Q = "jede antwort mit tabellen"
    bad = []
    for n in NEGS2:
        for k in (1, 2, 3):
            before = " ".join((n,) + FILL[:k - 1] + (Q,))
            after = " ".join((Q,) + FILL[:k - 1] + (n,))
            if iq.locate(Q, before) != "negation":
                bad.append(f"vor {n}/{k}")
            if iq.locate(Q, after) != "negation":
                bad.append(f"nach {n}/{k}")
        if iq.locate(Q, " ".join((n,) + FILL[:3] + (Q,))) is not True:
            bad.append(f"vor {n}/4 zu weit")
        if iq.locate(Q, " ".join((Q,) + FILL[:3] + (n,))) is not True:
            bad.append(f"nach {n}/4 zu weit")
    check(not bad, f"R2 alle {len(NEGS2)} Negationswoerter greifen im Abstand 1-3 und nicht im Abstand 4 ({bad[:6]})")
    check(iq.locate(Q, "ich will ordentlich " + Q + " haben") is True and iq.locate(Q, "ich kenne " + Q + " nett") is True,
          "R2 Gegenprobe: Woerter, die nur AEHNLICH klingen (kenne, ordentlich), sind keine Negation")
    # R2b (Probe R2 des Pruefers) bei mehreren Fundstellen gewinnt der informativste Grund, in jeder Reihenfolge
    check(iq.locate("tabellen im fliesstext", "xtabellen im fliesstext. keine tabellen im fliesstext") == "negation"
          and iq.locate("tabellen im fliesstext", "keine tabellen im fliesstext. xtabellen im fliesstext") == "negation",
          "R2b Fundstellen mit word_boundary UND negation: der Grund ist negation, egal in welcher Reihenfolge")

# ============================================================================= S  Bezug zwischen Zitat und Aussage (D5/D7, Runde 2)

print("--- S: ein verifiziertes Zitat ohne gemeinsames Inhaltswort mit der Aussage wird gemeldet (D5/D7, Runde 2)")
write_transcript(TR, [rec_user("Ja, gerne, mach das so bitte"), rec_user(USER_SAYS), rec_user(SUP_SAYS)])
apply_mod = try_import("apply_wrapup")
# S1 (Probe C12) die Kurzantwort 'Ja, gerne, mach' belegt 'Pusht ohne Rueckfrage'
mem = make_mem()
rc, out = run(mem, {"date": DATE, "user_candidates": [
    cand("fremd", observation="Pusht ohne Rueckfrage", confirmed=True, quote="Ja, gerne, mach"),
    cand("passend", confirmed=True, quote=QUOTE)]}, "--transcript", TR)
t = out["tally"]
check(rc == 0 and t["quote_unrelated"] == ["fremd"] and any("fremd" in w and "content word" in w for w in t["warnings"])
      and one(mem, "fremd").get("quote_verified") is True and t["quotes_verified"] == 2,
      f"S1 fremdes Zitat: Warnung und Tally quote_unrelated, das passende Zitat bleibt ohne Meldung (rc={rc}, {t['quote_unrelated']})")
# S2 Beugung und Wortstamm zaehlen als gemeinsames Wort (kurze/kurzen, Zusammenfassung/Zusammenfassungen)
check(apply_mod is not None and hasattr(apply_mod, "_quote_related") and apply_mod._quote_related("erst eine kurze Zusammenfassung", "Antworten beginnen mit einer kurzen Zusammenfassung")
      and apply_mod._quote_related("ausfuehrliche Antworten", "- Antworten ausfuehrlich")
      and apply_mod._quote_related("Zusammenfassungen sind gut", "Eine Zusammenfassung zuerst")
      and not apply_mod._quote_related("Ja, gerne, mach", "Pusht ohne Rueckfrage")
      and not apply_mod._quote_related("lieber ausfuehrliche Antworten mit allen Details", "- Quellen sind unwichtig")
      and not apply_mod._quote_related("Das ist immer so", "Pusht ohne Rueckfrage")
      and not apply_mod._quote_related("Das mache ich immer so", "Immer mit Quelle belegen")
      and apply_mod._quote_related("irgendwas", "Das haben wir nicht"),
      "S2 _quote_related: gemeinsames Inhaltswort mit Wortstamm; Fuellwoerter zaehlen nicht")
# S3 (Probe D10) Supersede mit verifiziertem, aber fremdem Zitat: wird angewendet, aber gemeldet
mem = make_mem()
rc, out = run(mem, sup_plan(dict(SUP, new_text="- Quellen sind unwichtig")))
t = out["tally"]
check(rc == 0 and t["user_lines_superseded"] == 1 and t["quote_unrelated"] == ["supersede:" + OLD[:40]]
      and any("supersede" in w and "content word" in w for w in t["warnings"]),
      f"S3 Supersede mit fremdem Zitat: Warnung und Tally (rc={rc}, {t['quote_unrelated']})")
mem = make_mem()
rc, out = run(mem, sup_plan(SUP))
check(rc == 0 and out["tally"]["user_lines_superseded"] == 1 and out["tally"]["quote_unrelated"] == []
      and not any("content word" in w for w in out["tally"]["warnings"]),
      "S3 Gegenprobe: passendes Zitat, keine Meldung")
# S4 nicht verifizierte Zitate werden nicht zusaetzlich gemeldet (sie scheitern ohnehin)
mem = make_mem()
rc, out = run(mem, {"date": DATE, "user_candidates": [cand("falsch", observation="Pusht ohne Rueckfrage", confirmed=True,
                                                          quote="das hat er nie gesagt, ganz sicher nicht")]}, "--transcript", TR)
check(rc == 0 and out["tally"]["quote_unrelated"] == [], "S4 ein nicht verifiziertes Zitat steht nicht in quote_unrelated")

# ============================================================================= T  Enthaltensein-Mass fuer widerlegte Aussagen (D9, Runde 2)

print("--- T: eine widerlegte Aussage kehrt auch erweitert oder umgestellt nicht zurueck (D9, Runde 2)")
T_USER_MD = b"# User Profile\n\n## Preferences\n- Immer mit Quelle belegen (UC4, 2026-07-02)\n"


def retracted_case(old_line, obs):
    mem_ = make_mem_q([qrow("t-wieder", id="UC30", observation=obs, status="confirmed", occurrences=2, sessions=["a", "b"])], T_USER_MD)
    put(mem_, "identity/user-changelog.json", [{"ts": "2026-10-20T00:00:00+00:00", "field": "user.md/supersede", "old_value": old_line,
                                                "new_value": "- etwas ganz anderes", "reason": "r", "plain": PLAIN}])
    rc_, out_ = run(mem_, {"date": DATE, "user_candidates": []})
    return rc_, out_["tally"], mem_


def blocked_expect(label, old_line, obs, blocked):
    rc_, t_, mem_ = retracted_case(old_line, obs)
    back = "UC30" in (t_["promoted_ids"] or [])  # ein abgestuerzter Lauf meldet FAIL, kein TypeError
    check(rc_ == 0 and (t_["promotion_blocked_superseded"] == 1) == blocked and back == (not blocked),
          f"{label}: {'blockiert' if blocked else 'promotet'} (blocked={t_['promotion_blocked_superseded']}, promoted={t_['promoted_ids']})")


RET3 = "- Antworten kurz halten (UC3, 2026-07-01)"
blocked_expect("T1 (E2) ein Fuellwort mehr", RET3, "Antworten bitte kurz halten", True)
blocked_expect("T2 (E3) umgestellt, mit zusaetzlichen Woertern", RET3, "Kurz halten sollen Antworten immer", True)
blocked_expect("T3 (E5) mit Zusatz", RET3, "Antworten kurz halten und immer mit Quelle belegen", True)
blocked_expect("T2b (E3) umgestellt und gebeugt: 'Kurze Antworten halten' (Wortstamm kurz/kurze)", RET3, "Kurze Antworten halten", True)
blocked_expect("T2b Wortstamm in allen Woertern: 'Antwort kurz halten' (Antwort/Antworten)", RET3, "Antwort kurz halten", True)
blocked_expect("T2b Mass (Runde 4): 2 von 3 Woertern (0,67) beruehren die widerlegte Aussage ('Antworten laenger halten')", RET3, "Antworten laenger halten", True)
blocked_expect("T2b Gegenprobe: 1 von 3 Woertern (0,33) beruehrt nicht ('Antworten laenger fassen')", RET3, "Antworten laenger fassen", False)
blocked_expect("T4 (E4) Mass (Runde 4): ein Synonym mit zwei gleichen Woertern (2 von 3) beruehrt - ein Fehlalarm kostet ein Zitat", RET3, "Antworten knapp halten", True)
blocked_expect("T4 bekannte Grenze: ein Synonym in einem anderen Satz (1 von 3 Woertern) wird nicht beruehrt", RET3, "Knappe Antworten bevorzugen", False)
RET5 = "- alpha beta gamma delta epsilon (UC3, 2026-07-01)"
blocked_expect("T5 fuenf Woerter, vier davon im neuen Text (0,8): beruehrt", RET5, "alpha beta gamma delta zeta eta theta", True)
blocked_expect("T5 fuenf Woerter, drei davon im neuen Text (0,6): beruehrt (Schwelle 0,5)", RET5, "alpha beta gamma zeta eta theta iota", True)
blocked_expect("T5 fuenf Woerter, zwei davon im neuen Text (0,4): darunter nicht beruehrt", RET5, "alpha beta zeta eta theta iota", False)
# (Runde 3: gezaehlt werden Inhaltswoerter - mindestens 4 Buchstaben, keine Fuellwoerter -, deshalb statt 'a1 b2 ...' echte Woerter
# ohne gemeinsamen Wortanfang; Runde 4: zehn Woerter, 5 davon (0,5) beruehren, 4 (0,4) nicht)
FRUIT10 = "apfel birne citrone dattel erdbeere feige guave himbeere ingwer jackfrucht".split()
RET10 = "- " + " ".join(FRUIT10) + " (UC3, 2026-07-01)"
blocked_expect("T6 zehn Woerter, fuenf davon im neuen Text (0,5): an der Schwelle beruehrt", RET10, " ".join(FRUIT10[:5]) + " neu eins zwei", True)
blocked_expect("T6 zehn Woerter, vier davon im neuen Text (0,4): darunter nicht beruehrt", RET10, " ".join(FRUIT10[:4]) + " neu eins zwei drei", False)
blocked_expect("T7 (Runde 4) eine kurze Aussage (2 Woerter): stehen beide im langen Text, beruehrt sie ihn", "- Quellen pruefen (UC3, 2026-07-01)",
               "Quellen pruefen und danach alles andere noch machen", True)
blocked_expect("T7 Gegenprobe: steht nur eines der beiden Woerter im langen Text, nicht", "- Quellen pruefen (UC3, 2026-07-01)",
               "Quellen sammeln und danach alles andere noch machen", False)
blocked_expect("T7 Gegenprobe: dieselbe kurze Aussage fast woertlich", "- Quellen pruefen (UC3, 2026-07-01)", "Quellen pruefen", True)

# ============================================================================= U  Altbestand und Randfaelle (Runde 2)

print("--- U: user.md mit BOM direkt vor der ersten Ueberschrift, Beleg des Reruns nur aus dem Feld user.md/supersede")
# U1 die Duplikatpruefung sieht auch die erste Sektion einer Datei, die mit BOM direkt vor '## ' beginnt
for label, raw in (("LF", b"\xef\xbb\xbf## Preferences\n- Antworten kurz halten (UC3, 2026-07-01)\n"),
                   ("CRLF", b"\xef\xbb\xbf## Preferences\r\n- Antworten kurz halten (UC3, 2026-07-01)\r\n")):
    mem = make_mem_q([qrow("dup-bom", id="UC12", observation="Antworten kurz halten", status="confirmed", occurrences=2,
                           sessions=["a", "b"])], raw)
    rc, out = run(mem, {"date": DATE, "user_candidates": []})
    t = out["tally"]
    check(rc == 0 and t["promotion_skipped_duplicate"] == 1 and not t["promoted_ids"] and read_bytes(mem, "identity/user.md") == raw,
          f"U1 BOM direkt vor '## Preferences' ({label}): die Zeile wird als Duplikat erkannt, user.md bleibt Byte-gleich "
          f"(dup={t['promotion_skipped_duplicate']}, promoted={t['promoted_ids']})")
# U2 (Probe R5 des Pruefers) nur ein Changelog-Eintrag des Felds user.md/supersede belegt, dass der Supersede schon lief
mem = make_mem()
write_transcript(TR, [rec_user(SUP_SAYS)])
run(mem, LP)
log = load(mem, "identity/user-changelog.json")
for e in log:
    e["field"] = "user.md/skipped-duplicate"
put(mem, "identity/user-changelog.json", log)
rejected("U2 ein Eintrag mit demselben candidate_id und new_value, aber anderem Feld, belegt keinen frueheren Supersede", mem, LP,
         needle="no such line")

# ============================================================================= V  Bezug am gespeicherten Text (R1, Runde 3)

print("--- V: der Bezug des Zitats wird gegen den GESPEICHERTEN Text geprueft, der tatsaechlich promotet wird (R1, Runde 3)")
write_transcript(TR, [rec_user("Ja, gerne, mach das so bitte. Ich mag Tabellen mit vielen Spalten sehr gern")])


def known_key_case(stored_obs, plan_obs, quote, key="push"):
    """Ein bekannter Key (Zeile in der Queue) und ein Plan-Eintrag mit confirmed + quote. Ohne `plan_obs` traegt der Plan
    keine Beobachtung. Gespeichert und promotet wird immer die Beobachtung der Queue-Zeile."""
    mem_ = make_mem_q([qrow(key, id="UC5", observation=stored_obs, sessions=["frueher"])])
    c_ = {"key": key, "confirmed": True, "quote": quote, "signal_type": "preference", "trust_source": "conversation"}
    if plan_obs is not None:
        c_["observation"] = plan_obs
    rc_, out_ = run(mem_, {"date": DATE, "user_candidates": [c_]}, "--transcript", TR)
    return rc_, out_["tally"], mem_


# V1 (Probe K1) der Plan traegt KEINE Beobachtung: geprueft wird trotzdem, was gespeichert ist und promotet wird
rc, t, mem = known_key_case("Pusht ohne Rueckfrage", None, "Ja, gerne, mach")
check(rc == 0 and t["quote_unrelated"] == ["push"] and t["promoted_ids"] == ["UC5"] and one(mem, "push").get("quote_verified") is True
      and any("push" in w and "content word" in w for w in t["warnings"]) and "Pusht ohne Rueckfrage" in read(mem, "identity/user.md"),
      f"V1 bekannter Key, Plan ohne observation (K1): das Zitat ohne gemeinsames Wort wird gemeldet, das Urteil bleibt true "
      f"(unrelated={t['quote_unrelated']}, promoted={t['promoted_ids']})")
# V2 (Probe K2) die Plan-Beobachtung passt zum Zitat, die gespeicherte nicht: massgeblich ist die gespeicherte
rc, t, mem = known_key_case("Pusht ohne Rueckfrage", "Mag Tabellen mit Spalten", "Tabellen mit vielen Spalten")
check(rc == 0 and t["quote_unrelated"] == ["push"] and t["promoted_ids"] == ["UC5"] and "Pusht ohne Rueckfrage" in read(mem, "identity/user.md"),
      f"V2 bekannter Key, Plan-observation passt, die gespeicherte nicht (K2): gemeldet (unrelated={t['quote_unrelated']})")
# V3 Gegenprobe (Probe K3, umgekehrt): die gespeicherte Beobachtung passt, die der Plan nicht -> keine Meldung
rc, t, mem = known_key_case("Mag Tabellen mit Spalten", "Pusht ohne Rueckfrage", "Tabellen mit vielen Spalten")
check(rc == 0 and t["quote_unrelated"] == [] and not any("content word" in w for w in t["warnings"]) and t["promoted_ids"] == ["UC5"],
      f"V3 die gespeicherte Beobachtung passt zum Zitat, die des Plans nicht: keine Meldung (unrelated={t['quote_unrelated']})")
rc, t, mem = known_key_case("Mag Tabellen mit Spalten", None, "Tabellen mit vielen Spalten")
check(rc == 0 and t["quote_unrelated"] == [] and t["promoted_ids"] == ["UC5"], "V3 Gegenprobe: gespeichert passt, Plan ohne observation: keine Meldung")
# V4 ein nicht verifiziertes Zitat wird auch bei bekanntem Key nicht zusaetzlich gemeldet
rc, t, mem = known_key_case("Pusht ohne Rueckfrage", None, "das hat er nie gesagt, ganz sicher nicht")
check(rc == 0 and t["quote_unrelated"] == [] and one(mem, "push").get("quote_verified") is False and t["quotes_unverified"] == 1
      and not any("content word" in w for w in t["warnings"]),
      "V4 bekannter Key, nicht verifiziertes Zitat: keine Bezugsmeldung")
# V5 eine Altzeile ohne Beobachtung hat nichts zu vergleichen: keine Meldung (wie ein Text ohne Inhaltswort)
rc, t, mem = known_key_case("", None, "Ja, gerne, mach")
check(rc == 0 and t["quote_unrelated"] == [], "V5 gespeicherte Beobachtung leer: nichts zu vergleichen, keine Meldung")

# ============================================================================= W  D9-Fehlsperren (R2, Runde 3)

print("--- W: eine widerlegte Aussage kehrt auch in Umschrift und mit Zusatz nicht zurueck; Verfeinerung und anderer Gegenstand werden nicht gesperrt (R2, Runde 3)")


def d9_case(old_line, new_line, obs, entry_extra=None):
    """Ein Changelog mit EINEM Supersede-Eintrag (alt -> neu) und ein reifer Kandidat `obs`; ein Wrap-up-Lauf."""
    mem_ = make_mem_q([qrow("w-wieder", id="UC30", observation=obs, status="confirmed", occurrences=2, sessions=["a", "b"])], T_USER_MD)
    entry = {"ts": "2026-10-20T00:00:00+00:00", "field": "user.md/supersede", "old_value": old_line, "new_value": new_line,
             "reason": "r", "plain": PLAIN}
    entry.update(entry_extra or {})
    put(mem_, "identity/user-changelog.json", [entry])
    rc_, out_ = run(mem_, {"date": DATE, "user_candidates": []})
    return rc_, out_["tally"], mem_


def d9_expect(label, old_line, new_line, obs, blocked, entry_extra=None):
    rc_, t_, mem_ = d9_case(old_line, new_line, obs, entry_extra)
    back = "UC30" in (t_["promoted_ids"] or [])  # ein abgestuerzter Lauf meldet FAIL, kein TypeError
    check(rc_ == 0 and (t_["promotion_blocked_superseded"] == 1) == blocked and back == (not blocked),
          f"{label}: {'gesperrt' if blocked else 'nicht gesperrt (promotet)'} "
          f"(blocked={t_['promotion_blocked_superseded']}, promoted={t_['promoted_ids']})")
    return t_, mem_


OTHER_LINE = "- Mag knappe Antworten mit allen Details"
# W1 (Probe F1) Umschrift und Unicode: ae/ue/oe, ss, zusammengesetzte und getrennte Umlaute
UML_OLD = "- Mag ausführliche Erklärungen (UC3, 2026-07-01)"
d9_expect("W1 (F1) Umschrift: 'ausfuehrliche Erklaerungen' bringt die widerlegte 'ausführliche Erklärungen' zurueck", UML_OLD, OTHER_LINE,
          "Mag ausfuehrliche Erklaerungen", True)
d9_expect("W1b (F1b) gleiche Schreibweise mit Fuellwort", UML_OLD, OTHER_LINE, "Mag sehr ausführliche Erklärungen", True)
d9_expect("W1c Umschrift in der Gegenrichtung (alt ae/ue, neu mit Umlauten)", "- Mag ausfuehrliche Erklaerungen (UC3, 2026-07-01)",
          OTHER_LINE, "Mag ausführliche Erklärungen", True)
d9_expect("W1d ß = ss", "- Immer Maßnahmen dokumentieren (UC3, 2026-07-01)", OTHER_LINE, "Massnahmen immer dokumentieren", True)
d9_expect("W1e getrennte Umlaute (u + U+0308) zaehlen wie zusammengesetzte", "- Mag ausführliche Erklärungen (UC3, 2026-07-01)",
          OTHER_LINE, "Mag ausführliche Erklärungen", True)
d9_expect("W1f Gegenprobe: ein anderes Wort mit Umlaut ('Erklärvideos') sperrt nicht", UML_OLD, OTHER_LINE, "Mag ausführliche Erklärvideos", False)
# W2 (Probe F2/F2b) ein Zusatz am Zeilenende, der keine UC-Zitierung ist
d9_expect("W2 (F2) Zusatz '(seit 2026-07)' am Zeilenende", "- Antworten kurz halten (seit 2026-07)", OTHER_LINE, "Antworten kurz halten", True)
d9_expect("W2b (F2b) Zusatz '[Owner, Telefonat]' in eckigen Klammern", "- Antworten kurz halten [Owner, Telefonat]", OTHER_LINE,
          "Antworten kurz halten", True)
d9_expect("W2c zwei Zusaetze: Zitierung und Vermerk", "- Antworten kurz halten (UC3, 2026-07-01) [Owner]", OTHER_LINE, "Antworten kurz halten", True)
d9_expect("W2d Gegenprobe: eine Klammer MITTEN in der Zeile bleibt Inhalt und verduennt den Anteil (2 von 5 Woertern = 0,4)",
          "- Antworten (nur bei Fehlersuche oder Reviews oder Planung) liefern (UC3, 2026-07-01)", OTHER_LINE, "Antworten liefern", False)
d9_expect("W2e (Runde 4) nur die Zitierung am Zeilenende wird abgeschnitten, nicht jede Schlussklammer davor: der Kern in der Klammer "
          "zaehlt mit (3 von 4 Woertern = 0,75)", "- Mag Stil (ausfuehrliche Erklaerungen mit Beispielen) (UC3, 2026-07-01)", OTHER_LINE,
          "ausfuehrliche Erklaerungen mit Beispielen", True)
d9_expect("W2f eine alte Zeile ohne ein Wort (nur die Zitierung) beruehrt nichts und bricht den Lauf nicht ab", "- (UC3, 2026-07-01)", OTHER_LINE,
          "Antworten kurz halten", False)
# W3 (Probe F3) Verfeinerung: seit Runde 4 keine Ausnahme mehr - der Kandidat beruehrt die widerlegte Zeile und braucht ein Zitat
REF_OLD = "- Antworten kurz halten (UC3, 2026-07-01)"
REF_NEW = "- Antworten kurz halten, ausser bei Fehlersuche"
d9_expect("W3 (F3) Runde 4: auch eine echte Verfeinerung der neuen Zeile braucht per Zweitnennung ein Zitat (gesperrt; mit Zitat siehe AA)",
          REF_OLD, REF_NEW, "Antworten kurz halten, ausser bei der Fehlersuche ausfuehrlich mit Details", True)
d9_expect("W3b Gegenprobe: derselbe Eintrag, der Kandidat nennt nur den alten Wortlaut (3 von 5 = 0,6): gesperrt", REF_OLD, REF_NEW,
          "Antworten bitte kurz halten", True)
# W4 (Probe F5) ein anderer Gegenstand ('PR-Texte' statt 'Commits')
CM_OLD = "- Commits immer auf Deutsch schreiben (UC3, 2026-07-01)"
CM_NEW = "- Commits immer auf Englisch schreiben"
d9_expect("W4 (F5) Runde 4: 'PR-Texte immer auf Deutsch schreiben' teilt 2 von 3 Woertern mit dem Widerruf (0,67): beruehrt, ein Fehlalarm kostet ein Zitat",
          CM_OLD, CM_NEW, "PR-Texte immer auf Deutsch schreiben", True)
d9_expect("W4a anderer Gegenstand und anderer Inhalt ('PR-Texte kurz halten', 0 von 3 Woertern): per Zweitnennung promotet", CM_OLD, CM_NEW,
          "PR-Texte kurz halten", False)
d9_expect("W4b Gegenprobe: dieselbe Aussage ueber Commits wieder", CM_OLD, CM_NEW, "Commits immer auf Deutsch schreiben", True)
d9_expect("W4c Gegenprobe: mit Fuellwort und ohne 'immer'", CM_OLD, CM_NEW, "Bitte Commits auf Deutsch schreiben", True)

# W5 (Probe F5b) eine Sperre wird am Kandidaten vermerkt und nur beim ersten Mal gewarnt
rc, t1, mem = d9_case(REF_OLD, REF_NEW, "Antworten bitte kurz halten", {"candidate_id": "UC3"})
row = one(mem, "w-wieder")
check(rc == 0 and t1["promotion_blocked_superseded"] == 1 and row.get("blocked_by_supersede") == "UC3" and row.get("status") == "confirmed"
      and sum(1 for w in t1["warnings"] if "UC30" in w and "supersede" in w.lower()) == 1,
      f"W5 erste Sperre: Vermerk blocked_by_supersede mit der Zeilen-Id (UC3), Status bleibt, genau eine Warnung "
      f"(mark={row.get('blocked_by_supersede')}, warnings={len(t1['warnings'])})")
frozen = {k: read_bytes(mem, k) for k in ("identity/user.md", "identity/user-changelog.json", "working/user-candidates.json")}
rc, out2 = run(mem, {"date": DATE, "user_candidates": []})
t2 = out2["tally"]
check(rc == 0 and t2["promotion_blocked_superseded"] == 1 and not any("UC30" in w for w in t2["warnings"])
      and all(read_bytes(mem, k) == v for k, v in frozen.items()) and one(mem, "w-wieder").get("status") == "confirmed",
      f"W5b (F5b) zweiter Lauf: weiter gesperrt und gezaehlt, aber KEINE Wiederholungswarnung, user.md, Changelog und Queue Byte-gleich "
      f"(warnings={t2['warnings']})")
# W5c die Zeilen-Id kommt aus dem Eintrag, sonst aus der Zitierung der alten Zeile, sonst 'unknown'
for label, old_line, extra, want in (("aus der Zitierung der alten Zeile", REF_OLD, None, "UC3"),
                                     ("aus candidate_id des Eintrags (vor der Zitierung)", REF_OLD, {"candidate_id": "UC7"}, "UC7"),
                                     ("ohne jede Id", "- Antworten kurz halten", None, "unknown")):
    rc, t1, mem = d9_case(old_line, REF_NEW, "Antworten bitte kurz halten", extra)
    check(rc == 0 and one(mem, "w-wieder").get("blocked_by_supersede") == want,
          f"W5c Zeilen-Id im Vermerk {label}: {want!r} (ist {one(mem, 'w-wieder').get('blocked_by_supersede')!r})")
# W5d eine ANDERE sperrende Zeile als im Vermerk ist eine neue Information: der Vermerk wird erneuert und wieder gewarnt
rc, t1, mem = d9_case(REF_OLD, REF_NEW, "Antworten bitte kurz halten", {"candidate_id": "UC8"})
q = queue_of(mem)
q[0]["blocked_by_supersede"] = "UC3"
put(mem, "working/user-candidates.json", q)
rc, out2 = run(mem, {"date": DATE, "user_candidates": []})
check(rc == 0 and any("UC30" in w for w in out2["tally"]["warnings"]) and one(mem, "w-wieder").get("blocked_by_supersede") == "UC8",
      f"W5d sperrt jetzt eine andere Zeile (UC8 statt UC3 im Vermerk): Vermerk erneuert und wieder gewarnt "
      f"(mark={one(mem, 'w-wieder').get('blocked_by_supersede')})")

# W5e trifft die Sperre nicht mehr zu (hier: kein Supersede-Eintrag mehr), wird der Kandidat promotet und der Vermerk entfernt
mem = make_mem_q([qrow("w-frei", id="UC32", observation="Etwas ganz Neues zum Testen", status="confirmed", occurrences=2,
                       sessions=["a", "b"], blocked_by_supersede="UC3")], T_USER_MD)
rc, out = run(mem, {"date": DATE, "user_candidates": []})
check(rc == 0 and out["tally"]["promoted_ids"] == ["UC32"] and "blocked_by_supersede" not in one(mem, "w-frei"),
      f"W5e ein nicht mehr gesperrter Kandidat wird promotet und traegt den Vermerk nicht mehr ({one(mem, 'w-frei')})")

# W6 (e) Freigabe: der vorhandene Weg user_md_supersede mit verifiziertem Zitat - kein neuer Mechanismus
REL_SAYS = "Doch, die Antworten sollen bitte wieder kurz gehalten werden, das ist mir wichtig."
REL_QUOTE = "Antworten sollen bitte wieder kurz gehalten werden"
write_transcript(TR, [rec_user(SUP_SAYS), rec_user(REL_SAYS)])
REPL = "- Antworten ausfuehrlich, mit allen Details"
mem = make_mem()
rc, out = run(mem, sup_plan(dict(SUP, new_text=REPL)))
put(mem, "working/user-candidates.json", [qrow("kurz-wieder", id="UC31", observation="Antworten kurz halten", status="confirmed",
                                               occurrences=2, sessions=["a", "b"])])
rc1, out1 = run(mem, {"date": DATE, "user_candidates": []})
rc2, out2 = run(mem, {"date": DATE, "user_candidates": []})
check(rc == 0 and rc1 == 0 and rc2 == 0 and out1["tally"]["promotion_blocked_superseded"] == 1
      and one(mem, "kurz-wieder").get("blocked_by_supersede") == "UC3" and not out1["tally"]["promoted_ids"],
      f"W6 nach dem Supersede (kurz -> ausfuehrlich) ist 'Antworten kurz halten' gesperrt (blocked={out1['tally']['promotion_blocked_superseded']})")
REL = {"match": REPL, "new_text": "- Antworten kurz halten", "reason": "Dominic will die kurze Fassung wieder",
       "plain": PLAIN, "quote": REL_QUOTE}
rc3, out3 = run(mem, sup_plan(REL, user_candidates=[]))
t3, row = out3["tally"], one(mem, "kurz-wieder")
md = read(mem, "identity/user.md")
check(rc3 == 0 and t3["user_lines_superseded"] == 1 and t3["promotion_blocked_superseded"] == 0 and t3["promotion_skipped_duplicate"] == 1
      and row.get("status") == "promoted" and row.get("status_after_promotion") == "duplicate_of_existing"
      and "blocked_by_supersede" not in row and md.count("- Antworten kurz halten\n") == 1 and REPL not in md,
      f"W6 Freigabe: ein Supersede der ersetzenden Zeile mit verifiziertem Zitat schliesst den Kandidaten als Duplikat der neuen Zeile "
      f"(rc={rc3}, {out3.get('error')}, dup={t3['promotion_skipped_duplicate']}, row={row.get('status')})")
before = snapshot(mem)
rc4, out4 = run(mem, sup_plan(REL, user_candidates=[]))
check(rc4 == 0 and snapshot(mem) == before and out4["tally"]["promotion_blocked_superseded"] == 0, "W6 Wiederholung der Freigabe aendert nichts")
# W6b ohne verifiziertes Zitat bleibt die Sperre bestehen (der Agent gibt nichts auf eigenes Wort frei)
mem = make_mem()
run(mem, sup_plan(dict(SUP, new_text=REPL)))
put(mem, "working/user-candidates.json", [qrow("kurz-wieder", id="UC31", observation="Antworten kurz halten", status="confirmed",
                                               occurrences=2, sessions=["a", "b"])])
rc5, out5 = run(mem, sup_plan(dict(REL, quote="das hat er so nie gesagt, nicht einmal ansatzweise"), user_candidates=[]))
t5 = out5["tally"]
check(rc5 == 0 and t5["supersede_skipped_unverified"] == 1 and t5["promotion_blocked_superseded"] == 1 and REPL in read(mem, "identity/user.md")
      and one(mem, "kurz-wieder").get("status") == "confirmed",
      f"W6b Gegenprobe: ohne verifiziertes Zitat wird die Zeile nicht ersetzt und der Kandidat bleibt gesperrt (skipped={t5['supersede_skipped_unverified']})")

# ============================================================================= X  Negationsliste und Polaritaets-Hinweis (R3, Runde 3)

print("--- X: mehr Negationswoerter (auch ohne Apostroph, Akut, Gravis) und ein Hinweis, wenn Zitat und Aussage verschieden polar sind (R3, Runde 3)")
# X1 (Probe D1-D6) Luecken der Liste: das Zitat steht neben der Negation -> nicht verifiziert, Grund negation
for label, text, quote in (
        ("D1 'keinerlei' davor", "Ich will keinerlei lange Antworten mit Tabellen", "lange Antworten mit Tabellen"),
        ("D2 'keineswegs' davor", "Das will ich keineswegs: lange Antworten mit Tabellen", "lange Antworten mit Tabellen"),
        ("D3 'cant' ohne Apostroph", "I cant stand long answers with tables", "long answers with tables"),
        ("D4 'doesnt' ohne Apostroph", "he doesnt want long answers with tables", "long answers with tables"),
        ("D5 'Noe' dahinter", "Lange Antworten mit Tabellen? Noe.", "Lange Antworten mit Tabellen"),
        ("D5b 'Nö' dahinter (Umlaut)", "Lange Antworten mit Tabellen? Nö.", "Lange Antworten mit Tabellen"),
        ("D5c 'Nee' dahinter", "Lange Antworten mit Tabellen? Nee, danke.", "Lange Antworten mit Tabellen"),
        ("D6 Akut statt Apostroph (don´t)", "I don´t like long answers with tables", "like long answers with tables"),
        ("D6b Gravis statt Apostroph (don`t)", "I don`t like long answers with tables", "like long answers with tables"),
        ("D6c 'wont' ohne Apostroph", "I wont accept long answers with tables", "accept long answers with tables"),
        ("D6d 'isnt' ohne Apostroph", "Tables isnt something I want in long answers", "something I want in long answers")):
    rc, c, out, mem = one_quote(text, quote, "neg")
    check(rc == 0 and c.get("quote_verified") is False and out["tally"]["quote_reasons"] == {"neg": "negation"}
          and c.get("status") == "observed" and not out["tally"]["promoted_ids"],
          f"X1 {label}: nicht verifiziert, Grund negation (qv={c.get('quote_verified')}, {out['tally']['quote_reasons']})")
# X1b Gegenproben: Woerter, die nur AEHNLICH aussehen, sind keine Negation
for label, text, quote in (
        ("'Noel' ist kein 'noe'", "Lange Antworten mit Tabellen? Noel mag sie.", "Lange Antworten mit Tabellen"),
        ("'cantina' ist kein 'cant'", "he visited the cantina with long answers about tables", "long answers about tables"),
        ("'wonton' ist kein 'wont'", "I ordered wonton with long answers about tables", "long answers about tables")):
    rc, c, out, mem = one_quote(text, quote, "neg")
    check(rc == 0 and c.get("quote_verified") is True, f"X1b Gegenprobe {label}: das Zitat verifiziert (qv={c.get('quote_verified')})")
# X2 Einheit: jedes neue Negationswort greift im Abstand 1 bis 3 vor und hinter dem Zitat, im Abstand 4 nicht mehr
if iq is None or not hasattr(iq, "locate"):
    check(False, "X2 identity_quote.locate existiert")
else:
    NEGS3 = ("keinerlei", "keineswegs", "nö", "noe", "nee", "cant", "wont", "dont", "doesnt", "isnt", "didnt", "arent", "wasnt",
             "werent", "havent", "hasnt", "hadnt", "wouldnt", "couldnt", "shouldnt", "mustnt", "aint")
    Q3 = "jede antwort mit tabellen"
    FILL3 = ("alpha", "beta", "gamma", "delta")
    bad = []
    for n in NEGS3:
        for k in (1, 2, 3):
            if iq.locate(Q3, " ".join((n,) + FILL3[:k - 1] + (Q3,))) != "negation":
                bad.append(f"vor {n}/{k}")
            if iq.locate(Q3, " ".join((Q3,) + FILL3[:k - 1] + (n,))) != "negation":
                bad.append(f"nach {n}/{k}")
        if iq.locate(Q3, " ".join((n,) + FILL3[:3] + (Q3,))) is not True or iq.locate(Q3, " ".join((Q3,) + FILL3[:3] + (n,))) is not True:
            bad.append(f"{n}/4 zu weit")
    check(not bad, f"X2 alle {len(NEGS3)} neuen Negationswoerter greifen im Abstand 1-3 und nicht im Abstand 4 ({bad[:6]})")
    # Akut und Gravis werden wie der Apostroph normalisiert (vor dem Vergleich, in beiden Texten)
    check(iq.normalize("I don´t") == "i don't" and iq.normalize("I don`t") == "i don't" and iq.normalize("I donʼt") == "i don't"
          and iq.locate("want long answers", iq.normalize("I don´t want long answers")) == "negation"
          and iq.locate("want long answers", iq.normalize("I can`t want long answers")) == "negation",
          "X2 Akut, Gravis und U+02BC werden wie ein Apostroph normalisiert")

# X3 (Probe D10) Polaritaet: das Zitat traegt ein Negationswort, die Aussage nicht (oder umgekehrt) -> Hinweis, kein Herabstufen
def pol_case(text, quote, obs, key="pol", **kw):
    mem_ = make_mem()
    write_transcript(TR, [rec_user(text)])
    rc_, out_ = run(mem_, {"date": DATE, "user_candidates": [cand(key, observation=obs, confirmed=True, quote=quote, **kw)]}, "--transcript", TR)
    return rc_, out_["tally"], mem_


rc, t, mem = pol_case("Ich will keine langen Antworten mit Tabellen", "keine langen Antworten mit Tabellen", "Mag lange Antworten mit Tabellen")
c = one(mem, "pol")
check(rc == 0 and c.get("quote_verified") is True and t["quote_polarity"] == ["pol"] and t["quote_unrelated"] == []
      and not any("negation" in w for w in t["warnings"]) and c.get("status") == "promoted" and t["promoted_ids"],
      f"X3 (D10) das Zitat verneint, die Aussage nicht: nur der Tally-Zaehler quote_polarity (Kalibrierdaten), KEINE Warnzeile (Runde 4), "
      f"das Urteil bleibt true (qv={c.get('quote_verified')}, polarity={t['quote_polarity']}, warnings={t['warnings']}, status={c.get('status')})")
rc, t, mem = pol_case("Ich mag lange Antworten mit Tabellen sehr gern", "lange Antworten mit Tabellen sehr gern", "Mag keine langen Antworten mit Tabellen")
check(rc == 0 and one(mem, "pol").get("quote_verified") is True and t["quote_polarity"] == ["pol"] and t["quote_unrelated"] == [],
      f"X3 umgekehrt: die Aussage verneint, das Zitat nicht: Hinweis ({t['quote_polarity']})")
rc, t, mem = pol_case("Ich will nie wieder ungefragt pushen", "nie wieder ungefragt pushen", "Pusht nie ungefragt")
check(rc == 0 and one(mem, "pol").get("quote_verified") is True and t["quote_polarity"] == [] and not any("negation" in w for w in t["warnings"]),
      f"X3 Gegenprobe: beide verneinen (nie / nie): keine Meldung ({t['quote_polarity']})")
rc, t, mem = pol_case("Ich will immer eine kurze Zusammenfassung zuerst", "immer eine kurze Zusammenfassung zuerst", "Will kurze Zusammenfassung zuerst")
check(rc == 0 and one(mem, "pol").get("quote_verified") is True and t["quote_polarity"] == [], "X3 Gegenprobe: keiner verneint: keine Meldung")
rc, t, mem = pol_case("Ich will keine langen Antworten mit Tabellen", "das hat er nie gesagt, ganz sicher nicht", "Mag lange Antworten")
check(rc == 0 and t["quote_polarity"] == [], "X3 ein nicht verifiziertes Zitat steht nicht in quote_polarity")
# X3b bei einem bekannten Key zaehlt der gespeicherte Text (wie R1)
write_transcript(TR, [rec_user("Ich will keine langen Antworten mit Tabellen")])
mem = make_mem_q([qrow("pol", id="UC5", observation="Mag lange Antworten mit Tabellen", sessions=["frueher"])])
rc, out = run(mem, {"date": DATE, "user_candidates": [{"key": "pol", "confirmed": True, "quote": "keine langen Antworten mit Tabellen",
                                                       "observation": "Mag keine langen Antworten mit Tabellen",
                                                       "trust_source": "conversation"}]}, "--transcript", TR)
check(rc == 0 and out["tally"]["quote_polarity"] == ["pol"], "X3b bekannter Key: massgeblich ist die gespeicherte Beobachtung (der Plan verneint, die Zeile nicht)")
# X3c Einheit has_negation
check(iq is not None and hasattr(iq, "has_negation") and iq.has_negation("keine langen Antworten") and not iq.has_negation("lange Antworten")
      and iq.has_negation("I don’t know") and iq.has_negation("Mag Tabellen ohne Rueckfrage") and not iq.has_negation("Mag Noel und Cantina"),
      "X3c identity_quote.has_negation: Negationswort irgendwo im Text (auch ...n't und ohne)")
# X3d Supersede: das Zitat bestaetigt new_text mit anderer Polaritaet
write_transcript(TR, [rec_user(SUP_SAYS)])
mem = make_mem()
rc, out = run(mem, sup_plan(dict(SUP, new_text="- Keine ausfuehrlichen Antworten, auch nicht bei der Fehlersuche")))
t = out["tally"]
check(rc == 0 and t["user_lines_superseded"] == 1 and t["quote_polarity"] == ["supersede:" + OLD[:40]] and t["quote_unrelated"] == []
      and not any("negation" in w for w in t["warnings"]),
      f"X3d Supersede: new_text verneint, das Zitat nicht: angewendet, nur im Tally-Zaehler, keine Warnzeile (Runde 4) ({t['quote_polarity']}, {t['warnings']})")
mem = make_mem()
rc, out = run(mem, sup_plan(SUP))
check(rc == 0 and out["tally"]["quote_polarity"] == [] and not any("negation" in w for w in out["tally"]["warnings"]),
      "X3d Gegenprobe: gleiche Polaritaet, keine Meldung")

# ============================================================================= Y  Testluecken (R4, Runde 3)

print("--- Y: BOM direkt vor der ersten Ueberschrift (Supersede und Promotion); ein spaeteres unverifiziertes Zitat ueberschreibt kein verifiziertes (R4, Runde 3)")
write_transcript(TR, [rec_user(SUP_SAYS)])
BOM_B = b"\xef\xbb\xbf"
for label, nl in (("LF", b"\n"), ("CRLF", b"\r\n")):
    bom_md = BOM_B + b"## Preferences" + nl + b"- Antworten kurz halten (UC3, 2026-07-01)" + nl
    # Y1 (Mutant V21) die erste Sektion einer Datei, die mit BOM direkt vor '## ' beginnt, ist KEINE Praeambel
    mem = make_mem(bom_md)
    rc, out = run(mem, sup_plan(SUP))
    got = read_bytes(mem, "identity/user.md")
    check(rc == 0 and got == bom_md.replace(OLD.encode("utf-8"), NEW.encode("utf-8")) and out["tally"]["user_lines_superseded"] == 1,
          f"Y1 (V21) BOM direkt vor '## Preferences' ({label}): die Zeile der ersten Sektion wird ersetzt, alles andere Byte-gleich "
          f"(rc={rc}, {str(out.get('error'))[:70]})")
    # Y2 (Mutant V22) die Sektionssuche der Promotion kennt das BOM: keine zweite '## Preferences'
    mem = make_mem_q([ripe()], bom_md)
    rc, out = run(mem, {"date": DATE, "user_candidates": []})
    got = read_bytes(mem, "identity/user.md")
    check(rc == 0 and out["tally"]["promoted_ids"] == ["UC9"] and got.count(b"## Preferences") == 1 and got == bom_md + PROMO_LINE.encode("utf-8") + nl,
          f"Y2 (V22) BOM direkt vor '## Preferences' ({label}): die Zeile kommt in die vorhandene Sektion, keine doppelte Ueberschrift "
          f"(sections={got.count(b'## Preferences')}, rc={rc})")

# Y3 (Mutant V03) ein spaeteres unverifiziertes Zitat ueberschreibt ein verifiziertes nicht; ein verifiziertes ersetzt ein unverifiziertes.
# signal:mood wird nie promotet, die Zeile bleibt also in der Queue und man sieht, was aus dem Zitat wird.
write_transcript(TR, [rec_user("Ich bin heute wirklich sehr muede und gereizt")])
MOOD_Q, BAD_Q = "heute wirklich sehr muede", "das hat er so nie gesagt, ganz sicher nicht"


def mood_plan(quote):
    return {"date": DATE, "user_candidates": [cand("mood", signal_type="mood", observation="Ist heute muede", confirmed=True, quote=quote)]}


mem = make_mem()
rc1, out1 = run(mem, mood_plan(MOOD_Q), "--transcript", TR)
c1 = dict(one(mem, "mood"))
rc2, out2 = run(mem, mood_plan(BAD_Q), "--transcript", TR)
c2 = one(mem, "mood")
check(rc1 == 0 and rc2 == 0 and c1.get("quote") == MOOD_Q and c1.get("quote_verified") is True
      and c2.get("quote") == MOOD_Q and c2.get("quote_verified") is True and out2["tally"]["quotes_unverified"] == 1 and c2.get("status") == "confirmed",
      f"Y3 (V03) ein spaeteres unverifiziertes Zitat ueberschreibt das verifizierte nicht (quote={c2.get('quote')!r}, qv={c2.get('quote_verified')})")
mem = make_mem()
run(mem, mood_plan(BAD_Q), "--transcript", TR)
c1 = dict(one(mem, "mood"))
rc2, out2 = run(mem, mood_plan(MOOD_Q), "--transcript", TR)
c2 = one(mem, "mood")
check(c1.get("quote") == BAD_Q and c1.get("quote_verified") is False and rc2 == 0 and c2.get("quote") == MOOD_Q and c2.get("quote_verified") is True
      and c2.get("status") == "confirmed" and c2.get("confirmed_by") == "quote",
      f"Y3 Gegenrichtung: ein verifiziertes Zitat ersetzt das unverifizierte und bestaetigt (quote={c2.get('quote')!r}, qv={c2.get('quote_verified')})")
mem = make_mem()
run(mem, mood_plan(BAD_Q), "--transcript", TR)
rc2, out2 = run(mem, mood_plan("auch das hat er nie gesagt, nicht einmal ansatzweise"), "--transcript", TR)
check(one(mem, "mood").get("quote_verified") is False and one(mem, "mood").get("quote") == "auch das hat er nie gesagt, nicht einmal ansatzweise",
      "Y3 zwei unverifizierte Zitate nacheinander: das jeweils letzte steht in der Zeile")

# ============================================================================= Z  Nachbesserung nach dem Pruefer-Urteil (Runde 3)

print("--- Z: Faelle des Pruefers aus Runde 3 unter dem Beweisstufen-Vertrag (Runde 4): per Zweitnennung gesperrt, die Klammer des Kandidaten bleibt Inhalt, Vermerk-Id")
# Z1 (Probe N1a-c) der Kandidat nennt die widerlegte Aussage wieder - die Verfeinerungs-Ausnahme gibt es nicht mehr
d9_expect("Z1 (N1a) 'nicht auf Englisch': der Kandidat nennt die gestrichene Aussage 'Deutsch' wieder -> gesperrt",
          CM_OLD, CM_NEW, "Commits immer auf Deutsch schreiben, nicht auf Englisch", True)
d9_expect("Z1b (N1b) 'statt auf Englisch' -> gesperrt", CM_OLD, CM_NEW, "Commits auf Deutsch statt auf Englisch schreiben", True)
d9_expect("Z1c (N1c) 'kurz halten statt ausfuehrlich ...' (gestrichen: kurz, halten) -> gesperrt", "- Antworten kurz halten (UC3, 2026-07-01)",
          "- Antworten ausfuehrlich mit allen Details", "Antworten kurz halten statt ausfuehrlich mit allen Details", True)
d9_expect("Z1d (N1d) Runde 4: auch eine echte Verfeinerung der neuen Zeile (3 von 4 Woertern der widerlegten) braucht ein Zitat", CM_OLD, CM_NEW,
          "Commits immer auf Englisch schreiben, auch in PR-Texten", True)
d9_expect("Z1e (N1e) Kontrolle: der alte Wortlaut pur bleibt gesperrt", CM_OLD, CM_NEW, "Commits immer auf Deutsch schreiben", True)
d9_expect("Z1f das gestrichene Wort in anderer Beugung ('deutscher') zaehlt wie dasselbe Wort", CM_OLD, CM_NEW,
          "Commits nicht auf Englisch schreiben, sondern in deutscher Sprache", True)

# Z2 (Muss 2, Probe N2a-c) die Klammer am Ende des KANDIDATEN ist Inhalt (nur die alte Profilzeile wird von Vermerken befreit)
d9_expect("Z2 (N2a) Kern in der Schlussklammer des Kandidaten: 'Commits schreiben (immer auf Deutsch)' -> gesperrt",
          CM_OLD, CM_NEW, "Commits schreiben (immer auf Deutsch)", True)
d9_expect("Z2b (N2b) 'Antworten (kurz halten)' -> gesperrt", "- Antworten kurz halten (UC3, 2026-07-01)",
          "- Antworten ausfuehrlich mit allen Details", "Antworten (kurz halten)", True)
d9_expect("Z2c (N2c) 'Stil (ausfuehrliche Erklaerungen mit Beispielen)' -> gesperrt",
          "- Mag ausfuehrliche Erklaerungen mit Beispielen (UC3, 2026-07-01)", "- Mag knappe Antworten",
          "Stil (ausfuehrliche Erklaerungen mit Beispielen)", True)
d9_expect("Z2d Gegenprobe: eine Schlussklammer des Kandidaten ohne widerlegten Inhalt sperrt nicht", CM_OLD, CM_NEW,
          "Commits kurz fassen (siehe Review)", False)
d9_expect("Z2e Gegenprobe: auf der ALTEN Zeile wird der Vermerk weiter abgeschnitten (W2)", "- Antworten kurz halten (seit 2026-07)",
          OTHER_LINE, "Antworten kurz halten", True)

# Z3 (Empfehlung N6) die Zeilen-Id kommt aus der Zitierung am Zeilenende, nicht aus der ersten Klammer der Zeile
for label, old_line, want in (("Klammer '(py3, uv)' vor der Zitierung", "- Nutzt Python (py3, uv) fuer Skripte (UC3, 2026-07-01)", "UC3"),
                              ("Zitierung gefolgt von einem Vermerk", "- Nutzt Python (py3, uv) fuer Skripte (UC3, 2026-07-01) [v2]", "UC3"),
                              ("Zitierung ohne Klammer davor (Gegenprobe)", "- Nutzt Python fuer Skripte (UC3, 2026-07-01)", "UC3"),
                              ("eine andere Kennung als UC in der Zitierung", "- Nutzt Python (py3, uv) fuer Skripte (P12, 2026-07-01)", "P12")):
    rc, t1, mem = d9_case(old_line, "- Nutzt Node fuer Skripte", "Nutzt Python fuer Skripte")
    got = one(mem, "w-wieder").get("blocked_by_supersede")
    check(rc == 0 and t1["promotion_blocked_superseded"] == 1 and got == want and any("line " + want in w for w in t1["warnings"]),
          f"Z3 (N6) Zeilen-Id im Vermerk und in der Warnung, {label}: {want!r} (ist {got!r})")

# Z4 (Mutant MY-1) grosses Umlaut-Initial: erst casefold, dann Umschrift ('Aenderungen' = 'Änderungen')
d9_expect("Z4 (MY-1) grosser Umlaut am Wortanfang: 'Aenderungen' bringt die widerlegte 'Änderungen' zurueck",
          "- Änderungen immer sauber dokumentieren (UC3, 2026-07-01)", OTHER_LINE, "Aenderungen immer sauber dokumentieren", True)
d9_expect("Z4b (MY-1) Gegenrichtung, grosses Ü: 'Übersichten' = 'Uebersichten'", "- Uebersichten stets knapp halten (UC3, 2026-07-01)",
          OTHER_LINE, "Übersichten stets knapp halten", True)
# Z5 (Mutant MY-2) ö wird zu oe
d9_expect("Z5 (MY-2) o-Umlaut: 'Moeglichst kurze Antworten geben' bringt 'Möglichst kurze Antworten geben' zurueck",
          "- Möglichst kurze Antworten geben (UC3, 2026-07-01)", OTHER_LINE, "Moeglichst kurze Antworten geben", True)
# Z6 steht die ganze alte Aussage in Klammern, behaelt sie ihre Woerter (nur die Zitierung am Zeilenende wird abgeschnitten)
d9_expect("Z6 eine alte Zeile ganz in Klammern behaelt ihre Woerter", "- (Antworten kurz halten)", OTHER_LINE,
          "Antworten kurz halten", True)
# Z7 (Mutant MY-4) auf die Zitierung folgende Vermerke, auch mehrere und solche mit Inhaltswoertern, werden mit abgeschnitten
d9_expect("Z7 (MY-4) Zitierung gefolgt von zwei Vermerken ('(seit Juli) [Owner Telefonat]'): alle werden abgeschnitten, 3 von 3 Woertern",
          "- Antworten kurz halten (UC3, 2026-07-01) (seit Juli) [Owner Telefonat]", OTHER_LINE, "Antworten kurz halten", True)


# Z8 bei mehreren Widerrufen nennt der Vermerk den ersten Eintrag, den der Kandidat beruehrt - nicht irgendeinen
def d9_multi(entries, obs):
    mem_ = make_mem_q([qrow("w-wieder", id="UC30", observation=obs, status="confirmed", occurrences=2, sessions=["a", "b"])], T_USER_MD)
    put(mem_, "identity/user-changelog.json",
        [{"ts": "2026-10-20T00:00:00+00:00", "field": "user.md/supersede", "old_value": o, "new_value": n, "reason": "r", "plain": PLAIN}
         for o, n in entries])
    rc_, out_ = run(mem_, {"date": DATE, "user_candidates": []})
    return rc_, out_["tally"], mem_


ENTRY_A = (REF_OLD, REF_NEW)  # kurz -> kurz, ausser bei Fehlersuche
ENTRY_B = ("- Commits immer auf Deutsch schreiben (UC5, 2026-07-01)", CM_NEW)
for label, entries, obs, want in (("nur B beruehrt, Reihenfolge A, B", [ENTRY_A, ENTRY_B], "Commits immer auf Deutsch schreiben", "UC5"),
                                  ("nur B beruehrt, Reihenfolge B, A", [ENTRY_B, ENTRY_A], "Commits immer auf Deutsch schreiben", "UC5"),
                                  ("beide beruehrt, Reihenfolge A, B: der erste Eintrag", [ENTRY_A, ENTRY_B],
                                   "Antworten kurz halten; Commits immer auf Deutsch schreiben", "UC3"),
                                  ("beide beruehrt, Reihenfolge B, A: der erste Eintrag", [ENTRY_B, ENTRY_A],
                                   "Antworten kurz halten; Commits immer auf Deutsch schreiben", "UC5")):
    rc, t1, mem = d9_multi(entries, obs)
    check(rc == 0 and t1["promotion_blocked_superseded"] == 1 and not t1["promoted_ids"] and one(mem, "w-wieder").get("blocked_by_supersede") == want,
          f"Z8 ({label}): gesperrt, der Vermerk nennt {want} (blocked={t1['promotion_blocked_superseded']}, mark={one(mem, 'w-wieder').get('blocked_by_supersede')})")

# ============================================================================= AA  Beweisstufe fuer widerrufene Aussagen (Runde 4)

print("--- AA: eine Aussage, die per Supersede widerrufen wurde, kommt per Zweitnennung nie zurueck, mit dem verifizierten Zitat des Users immer (Runde 4)")
# Die Sperre urteilt nicht ueber die Bedeutung, sie verlangt nur die hoechste Beweisstufe (confirmed_by == "quote"). Jeder Fall des Pruefers
# (probe5 N1-N4, probe6 R, B, probe7 F, K) laeuft als Gesamtablauf: Promotion der Beobachtung -> Supersede der Zeile mit verifiziertem
# Zitat -> dieselbe oder eine umschriebene Aussage unter NEUEM Key, einmal per Zweitnennung (nicht promotet), einmal mit Zitat (promotet).
REF_BACK_TAIL = ", das ist mir wichtig."
KURZ_NEU = "- Antworten kurz halten, bei der Fehlersuche ausfuehrlich"
CM_NEU = "- Commits immer auf Englisch schreiben"
CM_OBS = "Commits immer auf Deutsch schreiben"
KURZ_OBS = "Antworten kurz halten"
AUSF_NEU = "- Antworten ausfuehrlich mit allen Details"
AUSSER_NEU = "- Antworten kurz halten, ausser bei Fehlersuche"
BRACKET_OBS = "Schreibt Commits (immer auf Deutsch)"
BRACKET_NEU = "- Schreibt Commits auf Englisch"
PROJ_OBS = "Schreibt Commit-Nachrichten fuer Projekte (auf Deutsch)"
PROJ_NEU = "- Schreibt Commit-Nachrichten fuer Projekte auf Englisch"


def flow(label, obs, new_text, back, touches=True, quote=None, reinstate=False):
    """Gesamtablauf: (1) `obs` wird als reifer Kandidat promotet, (2) der Owner ersetzt die Zeile per user_md_supersede mit verifiziertem
    Zitat (`reinstate`: und setzt sie danach mit verifiziertem Zitat wieder ein), (3) `back` kommt unter einem neuen Key - per
    Zweitnennung bestaetigt: nicht promotet, wenn es die widerrufene Aussage beruehrt (`touches`); mit verifiziertem Zitat: promotet."""
    quote = quote or back
    says = "Ja, genau so: " + quote + REF_BACK_TAIL
    write_transcript(TR, [rec_user(SUP_SAYS), rec_user(REL_SAYS), rec_user(says)])
    mem_ = make_mem_q([qrow("erst", id="UC1", observation=obs, status="confirmed", occurrences=2, sessions=["a", "b"])], T_USER_MD)
    rc1, out1 = run(mem_, {"date": DATE, "user_candidates": []})
    line = next((ln for ln in read(mem_, "identity/user.md").splitlines() if "(UC1," in ln), "")
    rc2, out2 = run(mem_, sup_plan({"match": line, "new_text": new_text, "reason": "r", "plain": PLAIN, "quote": SUP_QUOTE}))
    ok = rc1 == 0 and out1["tally"]["promoted_ids"] == ["UC1"] and rc2 == 0 and out2["tally"]["user_lines_superseded"] == 1
    if reinstate:
        rc3, out3 = run(mem_, sup_plan({"match": new_text, "new_text": "- " + obs, "reason": "r", "plain": PLAIN, "quote": REL_QUOTE}))
        ok = ok and rc3 == 0 and out3["tally"]["user_lines_superseded"] == 1
    check(ok, f"AA {label}: Vorlauf (Promotion von {obs!r}, Supersede mit verifiziertem Zitat) ist durchgelaufen")
    mem_q = mem_ + "-quote"
    shutil.copytree(mem_, mem_q)
    md_before = read_bytes(mem_, "identity/user.md")
    put(mem_, "working/user-candidates.json", queue_of(mem_) + [qrow("zurueck", id="UC2", observation=back, status="confirmed", occurrences=2,
                                                                       sessions=["a", "b"], confirmed_by="repeat")])
    rc4, out4 = run(mem_, {"date": DATE, "user_candidates": []})
    t4 = out4["tally"]
    if touches:
        check(rc4 == 0 and t4["promoted_ids"] == [] and t4["promotion_blocked_superseded"] == 1 and read_bytes(mem_, "identity/user.md") == md_before,
              f"AA {label}: {back!r} per Zweitnennung NICHT promotet, user.md unveraendert (blocked={t4['promotion_blocked_superseded']}, promoted={t4['promoted_ids']})")
        rc5, out5 = run(mem_q, {"date": DATE, "user_candidates": [cand("zurueck", observation=back, confirmed=True, quote=quote)]})
        t5, md_q = out5["tally"], read(mem_q, "identity/user.md")
        check(rc5 == 0 and t5["quotes_verified"] == 1 and len(t5["promoted_ids"]) == 1 and t5["promotion_blocked_superseded"] == 0
              and "- " + back + " (" in md_q and one(mem_q, "zurueck").get("confirmed_by") == "quote",
              f"AA {label}: mit dem verifizierten Zitat des Users promotet (verified={t5['quotes_verified']}, promoted={t5['promoted_ids']}, "
              f"blocked={t5['promotion_blocked_superseded']}, {str(out5.get('error'))[:60]})")
    else:
        check(rc4 == 0 and t4["promoted_ids"] == ["UC2"] and t4["promotion_blocked_superseded"] == 0 and "- " + back + " (" in read(mem_, "identity/user.md"),
              f"AA {label}: {back!r} beruehrt die widerrufene Aussage nicht - per Zweitnennung promotet (blocked={t4['promotion_blocked_superseded']}, "
              f"promoted={t4['promoted_ids']})")


for case in (
        ("N1a", CM_OBS, CM_NEU, "Commits immer auf Deutsch schreiben, nicht auf Englisch"),
        ("N1b", CM_OBS, CM_NEU, "Commits auf Deutsch statt auf Englisch schreiben"),
        ("N1c", KURZ_OBS, AUSF_NEU, "Antworten kurz halten statt ausfuehrlich mit allen Details"),
        ("N2a", CM_OBS, CM_NEU, "Commits schreiben (immer auf Deutsch)"),
        ("N2b", KURZ_OBS, AUSF_NEU, "Antworten (kurz halten)"),
        ("N2c", "Mag ausfuehrliche Erklaerungen mit Beispielen", "- Mag knappe Antworten", "Stil (ausfuehrliche Erklaerungen mit Beispielen)"),
        ("N3a", "Antwortet auf Deutsch", "- Antwortet auf Englisch", "Antwortet auf Deutsch, auch bei Code-Kommentaren"),
        ("N3b", "Mag Emojis in Antworten", "- Keine Emojis in Antworten", "Mag Emojis in Antworten und Commit-Texten"),
        ("N4a", "Antworten kurz (auch bei Fehlersuche und Reviews)", "- Antworten bei Fehlersuche ausfuehrlich",
         "Antworten kurz halten, auch bei Fehlersuche und Reviews"),
        ("R1 (= F3)", KURZ_OBS, KURZ_NEU, "Antworten kurz halten, auch bei der Fehlersuche nicht ausfuehrlich"),
        ("R2", KURZ_OBS, KURZ_NEU, "Antworten immer kurz halten, auch bei der Fehlersuche"),
        ("R3", KURZ_OBS, KURZ_NEU, "Antworten kurz halten, keine Ausnahme bei der Fehlersuche"),
        ("R4", KURZ_OBS, KURZ_NEU, "Antworten kurz halten"),
        ("R5", KURZ_OBS, AUSSER_NEU, "Antworten kurz halten, gerade bei Fehlersuche"),
        ("R6", KURZ_OBS, AUSSER_NEU, "Antworten kurz halten, ohne Ausnahme bei Fehlersuche"),
        ("R7", "Keep answers short", "- Keep answers short, except when debugging", "Keep answers short, even when debugging"),
        ("B1 (= F1)", BRACKET_OBS, BRACKET_NEU, BRACKET_OBS),
        ("B2", BRACKET_OBS, BRACKET_NEU, "Schreibt Commits immer auf Deutsch"),
        ("B3", PROJ_OBS, PROJ_NEU, "Schreibt Commit-Nachrichten fuer Projekte auf Deutsch, nicht auf Englisch"),
        ("B4", PROJ_OBS, PROJ_NEU, PROJ_OBS),
        ("B5", "Nutzt Linter (ruff, mypy)", "- Nutzt Linter (pylint)", "Nutzt Linter ruff und mypy")):
    flow(*case)
flow("K1", "Mag TDD", "- Schreibt Tests nach dem Code", "Mag TDD", quote="ich mag TDD wirklich sehr")
# eine echte Verfeinerung der neuen Zeile: seit Runde 4 per Zweitnennung gesperrt (bewusste Vertragsaenderung), mit Zitat frei
flow("A2 echte Verfeinerung der neuen Zeile", KURZ_OBS, KURZ_NEU, "Antworten kurz halten, bei der Fehlersuche ausfuehrlich mit Logs")
# ein anderer Gegenstand ohne gemeinsame Woerter: per Zweitnennung promotet
flow("A1 anderer Gegenstand ('PR-Texte kurz halten' nach dem Widerruf von 'Commits ...')", CM_OBS, CM_NEU, "PR-Texte kurz halten", touches=False)
# kurz -> ausfuehrlich -> kurz: kein Dauerverbot, der Owner setzt die Aussage mit seinem Zitat wieder ein; die Verfeinerung der wieder
# eingesetzten Aussage braucht weiter ein Zitat (die sichere Richtung), mit Zitat kommt sie
flow("N5 kurz -> ausfuehrlich -> kurz, Verfeinerung", KURZ_OBS, AUSF_NEU, "Antworten kurz halten, auch in Reviews", reinstate=True)

# AA2 derselbe Key: eine per Zweitnennung bestaetigte, gesperrte Zeile steigt mit dem verifizierten Zitat auf und wird promotet
CHG_KURZ = [{"ts": "2026-10-20T00:00:00+00:00", "field": "user.md/supersede", "old_value": REF_OLD, "new_value": AUSF_NEU,
             "reason": "r", "plain": PLAIN}]
write_transcript(TR, [rec_user("Ja, genau so: Antworten kurz halten" + REF_BACK_TAIL)])
for label, q_in, want_ok in (("verifiziertes Zitat", "Antworten kurz halten", True),
                             ("nicht verifiziertes Zitat", "das hat er so nie gesagt, nicht einmal ansatzweise", False)):
    mem = make_mem_q([qrow("kurz-wieder", id="UC31", observation="Antworten kurz halten", status="confirmed", occurrences=2,
                           sessions=["a", "b"], confirmed_by="repeat", blocked_by_supersede="UC3")], T_USER_MD)
    put(mem, "identity/user-changelog.json", CHG_KURZ)
    rc, out = run(mem, {"date": DATE, "user_candidates": [cand("kurz-wieder", observation="Antworten kurz halten", confirmed=True, quote=q_in)]})
    t, row = out["tally"], one(mem, "kurz-wieder")
    if want_ok:
        check(rc == 0 and t["promoted_ids"] == ["UC31"] and row.get("confirmed_by") == "quote" and "blocked_by_supersede" not in row,
              f"AA2 derselbe Key, {label}: die gesperrte Zeile steigt auf (confirmed_by quote) und wird promotet (promoted={t['promoted_ids']}, "
              f"confirmed_by={row.get('confirmed_by')})")
    else:
        check(rc == 0 and t["promoted_ids"] == [] and row.get("confirmed_by") == "repeat" and row.get("blocked_by_supersede") == "UC3"
              and t["promotion_blocked_superseded"] == 1,
              f"AA2 derselbe Key, {label}: bleibt gesperrt, Beweisstufe unveraendert (confirmed_by={row.get('confirmed_by')}, blocked={t['promotion_blocked_superseded']})")
# AA3 der inferred-Weg (zwei Nennungen, Konfidenz >= 0,6) ist keine Beweisstufe 'quote': auch er braucht das Zitat
mem = make_mem_q([qrow("inf", id="UC33", observation="Antworten kurz halten", status="inferred", confidence=0.7, occurrences=2,
                       sessions=["a", "b"])], T_USER_MD)
put(mem, "identity/user-changelog.json", CHG_KURZ)
rc, out = run(mem, {"date": DATE, "user_candidates": []})
check(rc == 0 and out["tally"]["promoted_ids"] == [] and out["tally"]["promotion_blocked_superseded"] == 1,
      f"AA3 der inferred-Weg (occ >= 2, conf >= 0,6) bringt eine widerrufene Aussage nicht zurueck (promoted={out['tally']['promoted_ids']})")
mem = make_mem_q([qrow("inf", id="UC33", observation="Etwas ganz Neues zum Testen", status="inferred", confidence=0.7, occurrences=2,
                       sessions=["a", "b"])], T_USER_MD)
put(mem, "identity/user-changelog.json", CHG_KURZ)
rc, out = run(mem, {"date": DATE, "user_candidates": []})
check(rc == 0 and out["tally"]["promoted_ids"] == ["UC33"], "AA3 Gegenprobe: eine nicht beruehrte Aussage geht den inferred-Weg wie bisher")

# ============================================================================= AB  Maß und Beweisstufe nachgeschaerft (Runde 5)

print("--- AB: kurze Zeilen und Schlussvermerke beruehren grosszuegig; die Beweisstufe kommt nur aus dem laufenden Lauf (Runde 5)")
# AB1 (Pruefer Runde 4, Q1-Q3/Q10) eine Zeile mit 1-2 Inhaltswoertern braucht alle INHALTSwoerter, ein weggelassenes Fuellwort hilft nicht
for label, old, obs in (("Q1 'immer' weggelassen", "- Antwortet immer auf Deutsch (UC3, 2026-07-01)", "Antwortet auf Deutsch"),
                        ("Q2 'Will' weggelassen", "- Will keine Emojis (UC3, 2026-07-01)", "Keine Emojis"),
                        ("Q3 'Mag' weggelassen, 'bitte' dazu", "- Mag keine Tabellen (UC3, 2026-07-01)", "Keine Tabellen, bitte"),
                        ("Q10 'nie pip' weggelassen", "- Nutzt uv und ruff, nie pip (UC3, 2026-07-01)", "Nutzt uv und ruff")):
    blocked_expect(f"AB1 {label}", old, obs, True)
blocked_expect("AB1 Gegenprobe: das einzige Inhaltswort fehlt ('Emojis' -> 'Tabellen')", "- Will keine Emojis (UC3, 2026-07-01)",
               "Will keine Tabellen", False)
# AB2 (Q4-Q6) die Zeile wird auch ohne JEDE Schlussklammer gelesen - ein Vermerk ohne Zitierung verduennt nicht mehr
blocked_expect("AB2 Q5 '(Owner, 2026-07-01)' ist keine Zitierung (UCn), derselbe Wortlaut kommt trotzdem nicht zurueck",
               "- Will keine Emojis (Owner, 2026-07-01)", "Will keine Emojis", True)
blocked_expect("AB2 Q4 '(seit 2026-07)' auf einer Zeile ohne Inhaltswort ('Mag TDD')", "- Mag TDD (seit 2026-07)", "Mag TDD", True)
blocked_expect("AB2 Q6 eine lange Notiz in Klammern verduennt nicht (ohne sie 3 von 3 Woertern)",
               "- Antworten kurz halten (Owner im Telefonat bestaetigt, gilt fuer Projekte und Kanaele und Reviews)", "Antworten kurz halten", True)
blocked_expect("AB2 Vermerk in eckigen Klammern und Schlusspunkt", "- Will keine Emojis [Owner, Telefonat].", "Will keine Emojis", True)
blocked_expect("AB2 Gegenprobe: ohne Schlussvermerk bleibt ein anderer Gegenstand frei", "- Will keine Emojis (Owner, 2026-07-01)",
               "Mag Tabellen mit Spalten", False)
# AB3 (Q11/Q12) unsichtbare Formatzeichen trennen kein Wort
blocked_expect("AB3 Q11 Nullbreite-Leerzeichen in zwei Woertern (ohne Faltung 1 von 3)", RET3, "Ant​worten kurz hal​ten", True)
blocked_expect("AB3 Q12 weiches Trennzeichen in zwei Woertern (ohne Faltung 1 von 3)", RET3, "Ant­worten kurz hal­ten", True)
# AB4 (Mutant MX1) die Sperre fragt nach der Beweisstufe des Kandidaten (confirmed_by), nicht nach einem frueher verifizierten Zitat:
# ein Zitat ohne Bestaetigungsanspruch aus einer frueheren Sitzung plus eine Zweitnennung ist 'repeat'
mem = make_mem_q([qrow("frueh-zitat", id="UC34", observation="Antworten kurz halten", status="confirmed", occurrences=2, sessions=["a", "b"],
                       confirmed_by="repeat", quote="Antworten kurz halten", quote_verified=True)], T_USER_MD)
put(mem, "identity/user-changelog.json", CHG_KURZ)
rc, out = run(mem, {"date": DATE, "user_candidates": []})
check(rc == 0 and out["tally"]["promoted_ids"] == [] and out["tally"]["promotion_blocked_superseded"] == 1,
      f"AB4 (MX1) frueher verifiziertes Zitat, bestaetigt per Zweitnennung: gesperrt (promoted={out['tally']['promoted_ids']})")
# AB5 (Mutant MX11) nur ein Plan-Eintrag MIT confirmed: true hebt die Beweisstufe; ein verifiziertes Zitat ohne Anspruch nicht
write_transcript(TR, [rec_user("Ja, genau so: Antworten kurz halten" + REF_BACK_TAIL)])
mem = make_mem_q([qrow("kurz-wieder", id="UC31", observation="Antworten kurz halten", status="confirmed", occurrences=2,
                       sessions=["a", "b"], confirmed_by="repeat", blocked_by_supersede="UC3")], T_USER_MD)
put(mem, "identity/user-changelog.json", CHG_KURZ)
rc, out = run(mem, {"date": DATE, "user_candidates": [cand("kurz-wieder", observation="Antworten kurz halten", quote="Antworten kurz halten")]})
row = one(mem, "kurz-wieder")
check(rc == 0 and out["tally"]["quotes_verified"] == 1 and out["tally"]["promoted_ids"] == [] and row.get("confirmed_by") == "repeat",
      f"AB5 (MX11) verifiziertes Zitat ohne confirmed: true hebt nicht (verified={out['tally']['quotes_verified']}, "
      f"promoted={out['tally']['promoted_ids']}, confirmed_by={row.get('confirmed_by')})")
# AB6 (Pruefer Z1/Z2) Beweisfelder im Plan zaehlen nicht - weder fuer einen neuen noch fuer einen bekannten, gesperrten Key
DECLARED = {"confirmed_by": "quote", "quote_verified": True, "status": "confirmed", "occurrences": 5, "sessions": ["x", "y", "z"]}
mem = make_mem_q([], T_USER_MD)
put(mem, "identity/user-changelog.json", CHG_KURZ)
rc, out = run(mem, {"date": DATE, "user_candidates": [cand("erklaert", observation="Antworten kurz halten", **DECLARED)]})
row = one(mem, "erklaert")
check(rc == 0 and out["tally"]["promoted_ids"] == [] and row.get("status") != "confirmed" and "confirmed_by" not in row
      and "quote_verified" not in row and row.get("occurrences") == 1,
      f"AB6 neuer Key mit erklaerten Beweisfeldern: verworfen (status={row.get('status')}, confirmed_by={row.get('confirmed_by')}, "
      f"occ={row.get('occurrences')})")
mem = make_mem_q([qrow("kurz-wieder", id="UC31", observation="Antworten kurz halten", status="confirmed", occurrences=2,
                       sessions=["a", "b"], confirmed_by="repeat", blocked_by_supersede="UC3")], T_USER_MD)
put(mem, "identity/user-changelog.json", CHG_KURZ)
rc, out = run(mem, {"date": DATE, "user_candidates": [cand("kurz-wieder", observation="Antworten kurz halten", **DECLARED)]})
row = one(mem, "kurz-wieder")
check(rc == 0 and out["tally"]["promoted_ids"] == [] and row.get("confirmed_by") == "repeat" and out["tally"]["promotion_blocked_superseded"] == 1,
      f"AB6 bekannter gesperrter Key mit erklaerten Beweisfeldern: bleibt repeat und gesperrt (confirmed_by={row.get('confirmed_by')})")

# ============================================================================= AC  Vergleich gegen ALLE Woerter des Kandidaten (Runde 6)

print("--- AC: ein Wort der widerrufenen Zeile steht auch dann im Kandidaten, wenn er es als Fuellwort schreibt; unsichtbare Zeichen (Runde 6)")
# AC1 (Pruefer Runde 5, R1/R2 - Regression gegen d72b1a2) 'nochmal' steht in 'noch einmal', 'mehrfach' in 'mehr'
blocked_expect("AC1 R1 'Prueft noch einmal nach' gegen 'Prüft nochmal nach'", "- Prüft nochmal nach (UC3, 2026-07-01)", "Prueft noch einmal nach", True)
blocked_expect("AC1 R2 'Fragt mehr nach' gegen 'Fragt mehrfach nach'", "- Fragt mehrfach nach (UC3, 2026-07-01)", "Fragt mehr nach", True)
blocked_expect("AC1 Gegenprobe: ein Kandidat ohne 'noch'/'nochmal' bleibt frei", "- Prüft nochmal nach (UC3, 2026-07-01)",
               "Prueft Quellen gruendlich", False)
# AC2 unsichtbare Zeichen: als einziger Worttrenner (Runde 5 klebte die Woerter zusammen) und als Kombinationszeichen (Kategorie Mn)
blocked_expect("AC2 Nullbreite-Leerzeichen als einziger Worttrenner", RET3, "Antworten​kurz​halten", True)
blocked_expect("AC2 Kombinationszeichen in zwei Woertern (U+034F, U+FE0F; ohne Entfernen 1 von 3)", RET3,
               "Ant͏worten kurz hal️ten", True)
# AC3 ein Ausrufe- oder Fragezeichen nach dem Schlussvermerk
blocked_expect("AC3 '!' nach dem Schlussvermerk", "- Will keine Emojis (Owner, 2026-07-01)!", "Will keine Emojis", True)
# AC4 (Pruefer Runde 6, L1-L3 - Regression gegen d72b1a2) ein unsichtbares Zeichen als einziger Trenner in der WIDERRUFENEN Zeile
blocked_expect("AC4 L1 Nullbreite-Leerzeichen zwischen zwei Woertern der alten Zeile", "- Antwortet auf​Deutsch (UC3, 2026-07-01)",
               "Antwortet auf Deutsch", True)
blocked_expect("AC4 L2 Nullbreite-Leerzeichen in einer Zeile ohne Inhaltswort", "- Mag​TDD (UC3, 2026-07-01)", "Mag TDD", True)
blocked_expect("AC4 L3 Kombinationszeichen U+034F als Trenner in der alten Zeile", "- Antwortet auf͏Deutsch (UC3, 2026-07-01)",
               "Antwortet auf Deutsch", True)
blocked_expect("AC4 Gegenprobe: ein anderer Gegenstand bleibt frei", "- Antwortet auf​Deutsch (UC3, 2026-07-01)",
               "Mag Tabellen mit Spalten", False)

# ============================================================================= AD  Codex-Verifier 2026-10-28 (P1-B2-01, P1-B2-02)

print("--- AD: unsichtbares Zeichen hinter dem Schlussvermerk; Verneinung hinter einem sehr langen Wort (Codex-Verifier)")
# AD1 (P1-B2-01) ein unsichtbares Zeichen direkt hinter der Schlussklammer: die Lesart ohne Anmerkung entsteht trotzdem
NOTE_LINE = "- Antworten kurz halten (alte historische Information bleibt hier dokumentiert)"
blocked_expect("AD1 Nullbreite-Leerzeichen hinter der Anmerkung", NOTE_LINE + "​", "Antworten kurz halten", True)
blocked_expect("AD1 Kombinationszeichen U+034F hinter der Anmerkung", NOTE_LINE + "͏", "Antworten kurz halten", True)
blocked_expect("AD1 Nullbreite-Leerzeichen zwischen zwei Anmerkungen",
               "- Antworten kurz halten (alte historische Information bleibt dokumentiert)​(seit Juli)", "Antworten kurz halten", True)
blocked_expect("AD1 Gegenprobe: ein anderer Gegenstand bleibt frei", NOTE_LINE + "​", "Mag Tabellen mit Spalten", False)
blocked_expect("AD1 Kombinationszeichen mitten in Woertern der alten Zeile: die Lesart der Zeile wie geschrieben bleibt noetig",
               "- Ant͏worten kurz hal️ten (UC3, 2026-07-01)", "Antworten kurz halten", True)
# AD2 (P1-B2-02) die Verneinung steht innerhalb von drei Woertern, aber mehr als 160 Zeichen vom Zitat entfernt
if iq is None or not hasattr(iq, "locate"):
    check(False, "AD2 identity_quote.locate existiert")
else:
    QD, LONG = "jede antwort mit tabellen", "a" * 161
    check(iq.locate(QD, f"nicht {LONG} b {QD}") == "negation" and iq.locate(QD, f"{QD} b {LONG} nicht") == "negation",
          "AD2 Verneinung hinter einem Wort von 161 Zeichen (vor und nach dem Zitat): erkannt")
    check(iq.locate(QD, f"nicht {LONG} b c {QD}") is True and iq.locate(QD, f"{QD} b c {LONG} nicht") is True,
          "AD2 Gegenprobe: im Abstand 4 hinter einem langen Wort greift die Verneinung nicht")
    cut = [(k, fill, side) for k in range(150, 170) for fill in (" ", " b ")
           for side, text in (("vor", "nicht " + "a" * k + fill + QD), ("nach", QD + fill + "a" * k + " nicht"))
           if iq.locate(QD, text) != "negation"]
    check(not cut, f"AD2 die Fenstergrenze faellt in das Verneinungswort: trotzdem erkannt (Laengen ohne Treffer: {cut})")

# ============================================================================= AE  Codex-Verifier Runde 2 (2026-10-28)

print("--- AE: Codex-Verifier Runde 2 - Block ueber mehrere Textteile, Plan-Fehler vor dem ersten Byte, Wortzahl, Sitzungs-ID")
# AE1 (P1-B2-R2-01) ein eingefuegter Block, dessen Tags in verschiedenen Textteilen derselben Nachricht stehen, zaehlt nicht
for label, parts, ok in (
        ("pasted_content ueber drei Textteile", ("<pasted_content>", USER_SAYS, "</pasted_content>"), False),
        ("system-reminder ueber zwei Textteile", ("<system-reminder>" + USER_SAYS, "</system-reminder>"), False),
        ("Gegenprobe: eigene Worte in einem zweiten Textteil", ("Kurz vorweg:", USER_SAYS), True),
        ("Gegenprobe: geschlossener Block im ersten Teil, eigene Worte im zweiten",
         ("<system-reminder>egal</system-reminder>", USER_SAYS), True)):
    mem = make_mem()
    write_transcript(TR, [rec_user(blocks(*parts))])
    rc, out = run(mem, {"date": DATE, "user_candidates": [cand("teile", confirmed=True, quote=QUOTE)]}, "--transcript", TR)
    c = one(mem, "teile")
    check(rc == 0 and (c.get("quote_verified") is True) == ok,
          f"AE1 {label}: {'verifiziert' if ok else 'nicht verifiziert'} (qv={c.get('quote_verified')}, {out['tally']['quote_reasons']})")
# AE2 (P1-B2-R2-02) ein confidence-Wert, der keine Zahl ist, verwirft den Plan vor dem ersten Byte
for label, bad in (("Text", "bad"), ("null", None), ("Liste", [0.5])):
    rejected(f"AE2 confidence als {label}", make_mem(),
             {"date": DATE, "iterations": [{"title": "Darf nicht geschrieben werden"}],
              "user_candidates": [cand("conf", confidence=bad)]}, needle="confidence")
# AE3 (P1-B2-R2-04) die Wortzahl zaehlt wie das Verneinungsfenster: "don't" ist ein Wort
rc, c, out, mem = one_quote("don't push", "don't push", "dp")
check(c.get("quote_verified") is False and out["tally"]["quote_reasons"] == {"dp": "too_few_words"},
      f"AE3 'don't push' sind zwei Woerter: nicht verifiziert ({out['tally']['quote_reasons']})")
rc, c, out, mem = one_quote("don't push to main", "don't push to main", "dp")
check(c.get("quote_verified") is True, f"AE3 Gegenprobe 'don't push to main' (vier Woerter): verifiziert ({out['tally']['quote_reasons']})")
# AE4 (P1-B2-R2-05) dasselbe Ziel, zuerst ohne Aenderung, dann per line_id: abgelehnt wie in der umgekehrten Reihenfolge (L3)
rejected("AE4 dasselbe Ziel: zuerst ohne Aenderung per match, dann per line_id", make_mem(),
         with_other(dict(SUP, new_text=OLD), {"line_id": "UC3", "new_text": "- anders", "reason": "r", "plain": PLAIN, "quote": SUP_QUOTE}),
         needle="twice")
# AE5 (P1-B2-R2-06) ein ausdruecklich leeres --session-id gewinnt gegen session_id im Plan: nichts wird hochgezaehlt
mem = make_mem_q([qrow("alt3", sessions=["frueher"])])
rc, out = run(mem, {"date": DATE, "session_id": "plan-sitzung", "user_candidates": [cand("alt3")]}, sid="")
c = one(mem, "alt3")
check(rc == 0 and c.get("occurrences") == 1 and c.get("status") == "observed" and not out["tally"]["promoted_ids"],
      f"AE5 --session-id '' und session_id im Plan: kein Hochzaehlen (occ={c.get('occurrences')}, status={c.get('status')})")
# AE6 (P1-B2-R2-11) Changelog zuerst auch bei der Promotion: scheitert das Schreiben von user.md, steht der Eintrag schon
aw = try_import("apply_wrapup")
mem = make_mem_q([qrow("reif6", status="confirmed", confirmed_by="repeat", occurrences=2, sessions=["a", "b"])])
md_before = read_bytes(mem, "identity/user.md")
if aw is not None and hasattr(aw, "promote_candidates") and hasattr(aw, "new_tally"):
    real_write = aw.write_atomic

    def failing_md(mem_, rel, text, dry, touched, via=None):
        if rel == "identity/user.md":
            raise OSError("simulierter Schreibfehler an user.md")
        return real_write(mem_, rel, text, dry, touched, via)

    aw.write_atomic = failing_md
    try:
        try:
            with inproc_env():
                aw.promote_candidates(mem, queue_of(mem), DATE, False, [], aw.new_tally())
            crashed = False
        except OSError:
            crashed = True
    finally:
        aw.write_atomic = real_write
    log = load(mem, "identity/user-changelog.json") or []
    check(crashed and read_bytes(mem, "identity/user.md") == md_before and len(log) == 1 and log[0].get("candidate_id") == "UC1",
          f"AE6 Schreibfehler an user.md bei der Promotion: der Changelog-Eintrag steht schon, die Datei ist unveraendert "
          f"(crashed={crashed}, log={len(log)})")
else:
    check(False, "AE6 apply_wrapup.promote_candidates existiert")
# AE7 (P1-B2-R2-13) der Anspruch ueber status: "confirmed" (ohne confirmed: true) bestaetigt mit verifiziertem Zitat
write_transcript(TR, [rec_user(USER_SAYS)])
mem = make_mem()
rc, out = run(mem, {"date": DATE, "user_candidates": [cand("st-conf", status="confirmed", quote=QUOTE)]}, "--transcript", TR)
c = one(mem, "st-conf")
check(rc == 0 and c.get("quote_verified") is True and c.get("confirmed_by") == "quote" and c.get("status") == "promoted",
      f"AE7 status confirmed mit verifiziertem Zitat: bestaetigt per quote und promotet "
      f"(qv={c.get('quote_verified')}, by={c.get('confirmed_by')}, status={c.get('status')})")

print(f"=== Results: {PASSED}/{TESTS} passed, {ERRORS} failures ===")
sys.exit(1 if ERRORS else 0)

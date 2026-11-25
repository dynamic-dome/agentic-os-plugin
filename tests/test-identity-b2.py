#!/usr/bin/env python3
"""Tests fuer P1 (B2, Qualitaet der Identitaetsdaten): Zitatpruefung, Supersede, Klartext-Pflicht.

Spec: docs/superpowers/specs/2026-10-27-identitaet-verdichtung-messbar-design.md, Abschnitt P1.
6.0.0 (T-090): die Identity-Ernte (user_candidates, Queue-Wachstum, Promotion nach user.md) ist entfernt; ihre Faelle
sind gestrichen. Die Zitatpruefung bleibt, weil user_md_supersede und user_md_retire ihr `quote` darueber verifizieren.

Was hier bewiesen wird:
  A  scripts/identity_quote.py.verify_quotes (die Pruefung beider Applier): nur ECHTE User-Nachrichten der Sitzung
     zaehlen (kein tool_result, keine system-reminder/command-Ausgabe, keine isMeta-Zeile, kein angenommener Vorschlag,
     kein eingefuegter Block), Normalisierung, Wortgrenzen, mindestens 8 Zeichen und 3 Woerter, Negations-Waechter
     (3 Woerter Reichweite), Sitzungsbindung (D2), Transkriptpfad nur unter <HOME>/.claude/projects/<dir>/ (D3, Q).
  B  scripts/identity_quote.py (Normalisierung, Auswahl echter User-Nachrichten, locate, has_negation) als Einheit.
  D  Plan-Sektion user_md_supersede: verlustfreie Zeilenersetzung (CRLF, BOM, Praeambel, Sektionsreihenfolge bleiben
     Byte fuer Byte), Changelog VOR der Datei, Ablehnung schreibt nichts (Learning L94), Wiederholung aendert nichts.
  E  Klartext-Pflicht (`plain`, 1..600 Zeichen) an bridge_decisions und rule_explanations; user_md_supersede siehe D.
  L  (D6) Supersede-Haerten: Praeambel, doppelte Eintraege, Zeilentrenner, Wiederholung mit line_id
  M  (D7) Supersede braucht ein verifiziertes Zitat
  S, X, Y  Bezug und Polaritaet zwischen Zitat und new_text, BOM direkt vor der ersten Ueberschrift

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


print("=== P1 / B2 tests (Zitatpruefung, Supersede, Klartext) ===")

# ============================================================================= A  Zitatpruefung (identity_quote) als Einheit
# 6.0.0: die Identity-Ernte (user_candidates) ist entfernt. Die Zitatpruefung bleibt, user_md_supersede und user_md_retire
# verifizieren ihr `quote` darueber. Die Faelle, die frueher ueber einen Kandidaten liefen, pruefen jetzt
# identity_quote.verify_quotes direkt - dieselbe Funktion, die beide Applier aufrufen (Abschnitt M zeigt den Weg ueber den Applier).

print("--- A: Zitatpruefung (identity_quote.verify_quotes), Quellen, Grenzen, Transkriptpfad, Sitzungsbindung")
iq = try_import("identity_quote")
check(iq is not None, "A0 scripts/identity_quote.py ist importierbar")
T_DIR = tempfile.mkdtemp(prefix="b2-transcripts-")  # ausserhalb des Homes: dort wird ein --transcript abgelehnt (D3)
atexit.register(shutil.rmtree, T_DIR, ignore_errors=True)
TR = os.path.join(PROJ_DIR, f"{SID}.jsonl")  # das Transkript der Standard-Sitzung SID, im Temp-Home
REAL_SESSION = {"CLAUDE_CODE_SESSION_ID": "die-echte-sitzung"}
SELF_WRITTEN = os.path.join(T_DIR, f"{SID}.jsonl")
write_transcript(SELF_WRITTEN, [rec_user(USER_SAYS)])
USE_TR = object()


def vq(records, quote, sid=SID, transcript=USE_TR, env=None, **kw):
    """Ein Zitat pruefen wie die Applier es tun. `records` (falls nicht None) wird vorher nach TR geschrieben.
    -> (Urteil, Grund, info)."""
    if records is not None:
        write_transcript(TR, records, **kw)
    with inproc_env():
        os.environ.update(env or {})
        res, info = iq.verify_quotes([quote], sid, TR if transcript is USE_TR else transcript)
    return res.get(quote), info["reasons"].get(quote), info


if iq is not None:
    # A1 Zitat steht in einer echten User-Nachricht: verifiziert, das benutzte Transkript wird genannt
    v, why, info = vq([rec_user("Hallo"), rec_assistant("Antwort"), rec_user(USER_SAYS)], QUOTE)
    check(v is True and info["transcript"] == TR, f"A1 Zitat in einer echten User-Nachricht: verifiziert ({v}, {info['transcript']})")

    # A2 Aufloesung ueber HOME/.claude/projects/*/<sid>.jsonl ohne --transcript (HOME-Umleitung wird beachtet)
    path = home_transcript("sess-home", [rec_user_old("nur zur Tarnung"), rec_user_old(USER_SAYS)])
    v, why, info = vq(None, QUOTE, sid="sess-home", transcript=None)
    check(v is True and os.path.normcase(str(info["transcript"])) == os.path.normcase(path),
          f"A2 Transkript im (Temp-)Home gefunden, Textbloecke im aelteren Format zaehlen ({v}, {info['transcript']})")
    drop_home_transcript("sess-home")

    # A3 Zitat nur in Quellen, die keine echte User-Nachricht sind: false
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
        v, why, _ = vq(recs, QUOTE)
        check(v is False and why == "not_found", f"A3 Zitat nur in '{name}': nicht verifiziert ({v}, {why})")

    # A4 Normalisierung: Gross/Klein, Whitespace, typografische Anfuehrungszeichen, aussen gesetzte Anfuehrungszeichen
    write_transcript(TR, [rec_user("Das gilt „immer“:   ERST eine KURZE\n  Zusammenfassung – it’s fine")])
    for label, q in (("Gross/Klein + Whitespace", "erst eine kurze   zusammenfassung"),
                     ("typografische Anfuehrungszeichen", "gilt \"immer\": erst eine kurze"),
                     ("Apostroph", "zusammenfassung – it's fine"),
                     ("aussen umschlossen", "„erst eine kurze Zusammenfassung“")):
        v, why, _ = vq(None, q)
        check(v is True, f"A4 Normalisierung: {label} ({v}, {why})")

    # A5 ein Zitat ueber zwei Nachrichten hinweg zaehlt nicht
    v, why, _ = vq([rec_user("Das ist der Anfang vom Satz bar"), rec_user("baz und danach das Ende vom Satz")],
                   "Anfang vom Satz bar baz und danach")
    check(v is False, "A5 Zitat ueber zwei Nachrichten -> false")

    # A6 leeres / zu kurzes Zitat
    for label, q, want in (("leeres Zitat", "", "too_short"), ("nur Whitespace", "  \n ", "too_short"), ("zu kurz", "ja", "too_short")):
        v, why, _ = vq([rec_user("ja, genau so. " + USER_SAYS)], q)
        check(v is False and why == want, f"A6 {label}: nicht verifiziert, Grund {want} ({v}, {why})")

    # A7 kein Transkript auffindbar: 'kein_transkript', kein Fehler
    v, why, info = vq(None, QUOTE, sid="sess-ohne-transkript", transcript=None)
    check(v == "kein_transkript" and info["transcript"] is None, f"A7 ohne Transkript -> 'kein_transkript' ({v})")
    v, why, info = vq(None, QUOTE, transcript=os.path.join(ISO_HOME, ".claude", "projects", "proj-leer", f"{SID}.jsonl"))
    check(v == "kein_transkript" and any("not found" in w.lower() for w in info["warnings"]) and not info["override_rejected"],
          "A7 ein zulaessiger, aber fehlender --transcript-Pfad ist kein Fehler, nur eine Warnung (und keine Ablehnung)")
    v, why, info = vq(None, QUOTE, sid="../evil", transcript=None)
    check(v == "kein_transkript", "A7 eine Session-ID mit Pfadzeichen loest nie ein Transkript auf")

    # A8 ein Transkript mit BOM, kaputter und leerer Zeile bleibt lesbar
    v, why, _ = vq([rec_user("x"), rec_user(USER_SAYS)], QUOTE, bom=True, garbage=True)
    check(v is True, "A8 BOM + kaputte Zeile im Transkript: Zitat wird gefunden")

    # H (D2) CLAUDE_CODE_SESSION_ID weicht von der Sitzungs-ID ab: die Evidenz ist nicht pruefbar
    write_transcript(TR, [rec_user(USER_SAYS)])
    v, why, info = vq(None, QUOTE, env=REAL_SESSION)
    check(v == "kein_transkript" and why == "session_mismatch" and info["session_mismatch"] is True
          and info["transcript"] is None and any("CLAUDE_CODE_SESSION_ID" in w for w in info["warnings"]),
          f"H1 Sitzungsbindung verletzt: nicht verifiziert, Warnung und Kennzeichen ({v}, {why})")
    v, why, info = vq(None, QUOTE, env={"CLAUDE_CODE_SESSION_ID": SID})
    check(v is True and not info["session_mismatch"], "H2 CLAUDE_CODE_SESSION_ID gleich der Sitzungs-ID: normal verifiziert")
    home_transcript("andere-sitzung", [rec_user(USER_SAYS, sessionId="andere-sitzung")])
    v, why, info = vq(None, QUOTE, sid="andere-sitzung", transcript=None, env={"CLAUDE_CODE_SESSION_ID": SID})
    check(v == "kein_transkript" and info["session_mismatch"] is True,
          f"H3 eine fremde Sitzungs-ID (Transkript vorhanden) verifiziert nichts ({v})")
    v, why, info = vq(None, QUOTE, sid="andere-sitzung", transcript=None)
    check(v is True, "H3 ohne CLAUDE_CODE_SESSION_ID gibt es keinen Vergleichswert: die genannte Sitzung gilt (Grenze, Codex/Tests)")
    os.remove(os.path.join(PROJ_DIR, "andere-sitzung.jsonl"))

    # I (D3) --transcript nur unter <HOME>/.claude/projects/<dir>/<session-id>.jsonl; fremde sessionId-Records zaehlen nicht
    write_transcript(TR, [rec_user("Nichts Passendes in der echten Sitzung")])

    def override_refused(label, path_):
        v_, why_, info_ = vq(None, QUOTE, transcript=path_)
        check(v_ == "kein_transkript" and info_["override_rejected"] is True and info_["transcript"] is None
              and any("refused" in w for w in info_["warnings"]),
              f"I1 {label}: abgelehnt, 'kein_transkript' ({v_}, {why_})")

    override_refused("selbst geschriebene Datei ausserhalb von projects (Befund Q3)", SELF_WRITTEN)
    write_transcript(os.path.join(PROJ_DIR, "egal.jsonl"), [rec_user(USER_SAYS)])
    override_refused("richtiges Verzeichnis, falscher Dateiname", os.path.join(PROJ_DIR, "egal.jsonl"))
    write_transcript(os.path.join(ISO_HOME, ".claude", "ausserhalb", f"{SID}.jsonl"), [rec_user(USER_SAYS)])
    override_refused("Pfad klettert per .. aus projects heraus", os.path.join(PROJ_DIR, "..", "..", "ausserhalb", f"{SID}.jsonl"))
    write_transcript(os.path.join(ISO_HOME, ".claude", "projects-evil", f"{SID}.jsonl"), [rec_user(USER_SAYS)])
    override_refused("Nachbarordner mit gleichem Namensanfang (projects-evil)", os.path.join(ISO_HOME, ".claude", "projects-evil", f"{SID}.jsonl"))
    override_refused("Datei direkt in projects (es fehlt das Projektverzeichnis)", os.path.join(ISO_HOME, ".claude", "projects", f"{SID}.jsonl"))
    write_transcript(os.path.join(PROJ_DIR, "tief", f"{SID}.jsonl"), [rec_user(USER_SAYS)])
    override_refused("zwei Ebenen unter projects", os.path.join(PROJ_DIR, "tief", f"{SID}.jsonl"))
    other = os.path.join(ISO_HOME, ".claude", "projects", "proj-zwei", f"{SID}.jsonl")
    write_transcript(other, [rec_user(USER_SAYS)])
    v, why, info = vq(None, QUOTE, transcript=other)
    check(v is True and info["transcript"] == other and not info["override_rejected"],
          "I2 ein zulaessiger Pfad (anderes Projektverzeichnis) wird angenommen")
    drop_home_transcript()
    v, why, _ = vq([rec_user(USER_SAYS, sessionId="ganz-fremd"), rec_user("Etwas anderes Harmloses hier", sessionId=SID)], QUOTE)
    check(v is False and why == "not_found", f"I3 ein Record mit fremder sessionId belegt nichts ({v}, {why})")
    v, why, _ = vq([rec_user(USER_SAYS, sessionId=SID)], QUOTE)
    check(v is True, "I3 derselbe Record mit der eigenen sessionId zaehlt")

    # J (D4) Modell-, Paste- und Harness-Texte sind keine Worte des Users
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
        ("turnOrigin sdk ohne origin und ohne promptSource", [raw_user(USER_SAYS, turnOrigin="sdk")], QUOTE),
        ("promptSource sdk ohne turnOrigin und ohne origin", [raw_user(USER_SAYS, promptSource="sdk")], QUOTE),
        ("promptSource system ohne turnOrigin und ohne origin", [raw_user(USER_SAYS, promptSource="system")], QUOTE),
        ("turnOrigin scheduled", [raw_user(USER_SAYS, turnOrigin="scheduled")], QUOTE),
        ("turnOrigin peer", [raw_user(USER_SAYS, turnOrigin="peer")], QUOTE),
        ("pasted_content ueber drei Textteile (AE1)", [rec_user(blocks("<pasted_content>", USER_SAYS, "</pasted_content>"))], QUOTE),
        ("system-reminder ueber zwei Textteile (AE1)", [rec_user(blocks("<system-reminder>" + USER_SAYS, "</system-reminder>"))], QUOTE),
    )
    for label, recs, quote in J_CASES:
        v, why, _ = vq([rec_user("Etwas ganz anderes"), *recs], quote)
        check(v is False, f"J1 {label}: nicht verifiziert ({v}, {why})")
    J_OK = (
        ("promptSource typed", [rec_user(USER_SAYS, promptSource="typed")]),
        ("promptSource queued (waehrend der Arbeit getippt)", [rec_user(USER_SAYS, promptSource="queued")]),
        ("Altformat ohne origin/promptSource/turnOrigin", [rec_user_old(USER_SAYS)]),
        ("eigener Text neben einem pasted_content-Block", [rec_user("schau mal <pasted_content>Fremdtext</pasted_content> " + USER_SAYS)]),
        ("eigener Text neben einem ide_selection-Block", [raw_user(blocks("<ide_selection>Auswahl</ide_selection>", USER_SAYS))]),
        ("eigene Worte in einem zweiten Textteil (AE1)", [rec_user(blocks("Kurz vorweg:", USER_SAYS))]),
        ("geschlossener Block im ersten Teil, eigene Worte im zweiten (AE1)",
         [rec_user(blocks("<system-reminder>egal</system-reminder>", USER_SAYS))]),
    )
    for label, recs in J_OK:
        v, why, _ = vq(recs, QUOTE)
        check(v is True, f"J2 Gegenprobe, zaehlt als Wort des Users: {label} ({v}, {why})")

    # K (D5) Wortgrenzen, mindestens 3 Woerter, Negations-Waechter
    K_CASES = (
        ("K1 Bruchstueck mitten im Wort (Q5)", "Ich arbeite niemals nachts gern an Code", "beite niemals nach", False, "word_boundary"),
        ("K1 ein Zitat, das nur rechts mitten im Wort endet", "Ich arbeite niemals nachts gern an Code", "arbeite niemals nac", False, None),
        ("K1 Gegenprobe: an Wortgrenzen", "Ich arbeite niemals nachts gern an Code", "arbeite niemals nachts gern", True, None),
        ("K1 Gegenprobe: bis zum Ende der Nachricht", "Ich arbeite niemals nachts gern an Code", "niemals nachts gern an Code", True, None),
        ("K2 Negation direkt vor dem Zitat (Q6)", "Ich mag keine langen Antworten mit vielen Tabellen", "langen Antworten mit vielen Tabellen", False, "negation"),
        ("K3 Negation direkt hinter dem Zitat", "Alle Antworten mit Tabellen sind nicht gewuenscht", "Alle Antworten mit Tabellen sind", False, "negation"),
        ("K4 Negation im Zitat selbst zaehlt", "Ich will nie wieder ungefragt pushen", "nie wieder ungefragt pushen", True, None),
        ("K5 zwei Woerter (Q7)", "ja, gerne", "ja, gerne", False, "too_few_words"),
        ("K5 drei Woerter reichen", "ja gerne bitte", "ja gerne bitte", True, None),
        ("K5 drei Woerter, aber unter 8 Zeichen", "a b c d e f", "a b c", False, "too_short"),
        ("K6 zwei Fundstellen, nur die erste negiert", "Keine Tabellen im Fliesstext. Tabellen im Fliesstext sind aber erlaubt", "Tabellen im Fliesstext", True, None),
        ("AE3 'don't push' sind zwei Woerter", "don't push", "don't push", False, "too_few_words"),
        ("AE3 Gegenprobe 'don't push to main'", "don't push to main", "don't push to main", True, None),
    )
    for label, text, quote, want, reason in K_CASES:
        v, why, _ = vq([rec_user(text)], quote)
        check(v is want and (reason is None or why == reason), f"{label}: {want} ({v}, {why})")

    # R (D5, Runde 2) und X (R3, Runde 3): Negation bis zu 3 Woerter vor/hinter dem Zitat, auch ohne Apostroph, Akut, Gravis
    for label, text, quote in (
            ("R1 C3 'Auf keinen Fall ...'", "Auf keinen Fall lange Antworten schreiben", "lange Antworten schreiben"),
            ("R1 C4 'Nie wieder ...'", "Nie wieder lange Antworten schreiben", "lange Antworten schreiben"),
            ("R1 C5 '... gut? Nein.'", "Lange Antworten sind gut? Nein.", "Lange Antworten sind gut"),
            ("R1 C6 'ohne ...'", "Bitte ohne lange Antworten und Tabellen", "lange Antworten und Tabellen"),
            ("R1 C7 \"won't\"", "I won't accept long answers here", "accept long answers here"),
            ("R1 weder ... noch", "Das will weder lange Antworten noch Tabellen", "lange Antworten noch Tabellen"),
            ("R1 nichts", "Davon halte ich nichts: lange Antworten schreiben", "lange Antworten schreiben"),
            ("X1 D1 'keinerlei' davor", "Ich will keinerlei lange Antworten mit Tabellen", "lange Antworten mit Tabellen"),
            ("X1 D2 'keineswegs' davor", "Das will ich keineswegs: lange Antworten mit Tabellen", "lange Antworten mit Tabellen"),
            ("X1 D3 'cant' ohne Apostroph", "I cant stand long answers with tables", "long answers with tables"),
            ("X1 D4 'doesnt' ohne Apostroph", "he doesnt want long answers with tables", "long answers with tables"),
            ("X1 D5 'Noe' dahinter", "Lange Antworten mit Tabellen? Noe.", "Lange Antworten mit Tabellen"),
            ("X1 D5b 'Nö' dahinter (Umlaut)", "Lange Antworten mit Tabellen? Nö.", "Lange Antworten mit Tabellen"),
            ("X1 D5c 'Nee' dahinter", "Lange Antworten mit Tabellen? Nee, danke.", "Lange Antworten mit Tabellen"),
            ("X1 D6 Akut statt Apostroph (don´t)", "I don´t like long answers with tables", "like long answers with tables"),
            ("X1 D6b Gravis statt Apostroph (don`t)", "I don`t like long answers with tables", "like long answers with tables"),
            ("X1 D6c 'wont' ohne Apostroph", "I wont accept long answers with tables", "accept long answers with tables"),
            ("X1 D6d 'isnt' ohne Apostroph", "Tables isnt something I want in long answers", "something I want in long answers")):
        v, why, _ = vq([rec_user(text)], quote)
        check(v is False and why == "negation", f"{label}: nicht verifiziert, Grund negation ({v}, {why})")
    for label, text, quote in (
            ("R1 ohne Negationswort in der Naehe", "Ich will gern lange Antworten schreiben lassen", "lange Antworten schreiben lassen"),
            ("X1b 'Noel' ist kein 'noe'", "Lange Antworten mit Tabellen? Noel mag sie.", "Lange Antworten mit Tabellen"),
            ("X1b 'cantina' ist kein 'cant'", "he visited the cantina with long answers about tables", "long answers about tables"),
            ("X1b 'wonton' ist kein 'wont'", "I ordered wonton with long answers about tables", "long answers about tables")):
        v, why, _ = vq([rec_user(text)], quote)
        check(v is True, f"{label}: Gegenprobe, das Zitat verifiziert ({v}, {why})")

    # J3 Einheit: eingefuegte Bloecke fallen bis zum Fixpunkt
if iq is None or not hasattr(iq, "locate"):
    check(False, "J3 identity_quote._strip_injected/locate vorhanden")
else:
    nested = iq._strip_injected("a <system-reminder>b <system-reminder>c</system-reminder> d</system-reminder> e")
    check(nested.split() == ["a", "e"], f"J3 _strip_injected: verschachtelte Bloecke fallen bis zum Fixpunkt ({nested!r})")
    check(iq._strip_injected("a <system-reminder>nie geschlossen").strip() == "a", "J3 ein nie geschlossener Block wird bis zum Ende entfernt")
    check(iq._strip_injected("vorher [Request interrupted by user] nachher").split() == ["vorher", "nachher"], "J3 die Unterbrechungszeile faellt weg")

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

# X3c Einheit has_negation
check(iq is not None and hasattr(iq, "has_negation") and iq.has_negation("keine langen Antworten") and not iq.has_negation("lange Antworten")
      and iq.has_negation("I don’t know") and iq.has_negation("Mag Tabellen ohne Rueckfrage") and not iq.has_negation("Mag Noel und Cantina"),
      "X3c identity_quote.has_negation: Negationswort irgendwo im Text (auch ...n't und ohne)")

# Q (D3, Runde 2) ein Junction/Symlink unter projects fuehrt aus projects hinaus -> die Datei zaehlt nicht
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
if iq is None:
    pass
elif not make_dir_link(JX_LINK, JX_OUTSIDE):
    print("  SKIP: Q - hier laesst sich kein Verzeichnis-Link anlegen (Junction/Symlink); die Faelle Q1-Q4 laufen nicht")
else:
    try:
        via_link = os.path.join(JX_LINK, f"{JX_SID}.jsonl")
        v, why, info = vq(None, QUOTE, sid=JX_SID, transcript=via_link)
        check(v == "kein_transkript" and info["override_rejected"] is True and info["transcript"] is None,
              f"Q1 --transcript ueber einen Link nach ausserhalb: abgelehnt ({v}, {why})")
        v, why, info = vq(None, QUOTE, sid=JX_SID, transcript=None)
        check(v == "kein_transkript" and info["transcript"] is None,
              f"Q2 Standardsuche: eine Datei hinter einem Link nach ausserhalb ist kein Transkript ({v}, {info['transcript']})")
        write_transcript(os.path.join(PROJ_DIR, f"{JX_SID}.jsonl"), [rec_user(USER_SAYS)])
        v, why, info = vq(None, QUOTE, sid=JX_SID, transcript=None)
        check(v is True and os.path.normcase(os.path.dirname(str(info["transcript"]))) == os.path.normcase(PROJ_DIR),
              "Q3 Gegenprobe: dasselbe Transkript in einem gewoehnlichen Projektverzeichnis wird gefunden und verifiziert")
        with inproc_env():
            os.remove(os.path.join(PROJ_DIR, f"{JX_SID}.jsonl"))
            check(iq.override_allowed(via_link, JX_SID) is False and iq.resolve_transcript(JX_SID) is None
                  and iq.resolve_transcript(JX_SID, via_link) is None,
                  "Q4 override_allowed und resolve_transcript lehnen den Pfad ueber den Link ab")
    finally:
        drop_dir_link(JX_LINK)
drop_home_transcript()

# ============================================================================= B  Einheit identity_quote

print("--- B: scripts/identity_quote.py")
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
    # __file__/__cached__ name where the module lies, not a resolved home path (the repo itself may sit under home)
    check(not [n for n, v in vars(iq).items()
               if not n.startswith("__") and isinstance(v, str) and home_now in os.path.normcase(v)],
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

# ============================================================================= S  Bezug zwischen Zitat und new_text (D5/D7, Runde 2)

print("--- S: ein verifiziertes Zitat ohne gemeinsames Inhaltswort mit new_text wird gemeldet (D5/D7, Runde 2)")
write_transcript(TR, [rec_user(SUP_SAYS)])
apply_mod = try_import("apply_wrapup")
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
# ============================================================================= X  Polaritaets-Hinweis am Supersede (R3, Runde 3)

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

print("--- Y: BOM direkt vor der ersten Ueberschrift (Supersede, R4, Runde 3)")
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

# ============================================================================= AE  Codex-Verifier Runde 2 (2026-10-28)

# AE4 (P1-B2-R2-05) dasselbe Ziel, zuerst ohne Aenderung, dann per line_id: abgelehnt wie in der umgekehrten Reihenfolge (L3)
rejected("AE4 dasselbe Ziel: zuerst ohne Aenderung per match, dann per line_id", make_mem(),
         with_other(dict(SUP, new_text=OLD), {"line_id": "UC3", "new_text": "- anders", "reason": "r", "plain": PLAIN, "quote": SUP_QUOTE}),
         needle="twice")

print(f"=== Results: {PASSED}/{TESTS} passed, {ERRORS} failures ===")
sys.exit(1 if ERRORS else 0)

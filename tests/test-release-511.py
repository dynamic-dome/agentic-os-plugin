#!/usr/bin/env python3
"""Tests fuer Release 5.11.0 (Owner-Go 2026-11-03): weniger schreiben, Identitaet verschlanken.

  R  Learnings unter importance 3 werden nicht mehr angelegt (Tally learnings_skipped_low_importance);
     rule_class-Learnings bleiben davon ausgenommen (sie gehen in den Regel-Gate, nicht in die Masse).
  E  Plan-Sektion identity_expire: Einmal-Beobachtungen (observed/inferred, occurrences <= 1), deren last_seen
     aelter als older_than_days ist, werden als "expired" markiert, nie geloescht. Eine spaetere Nennung desselben
     Keys holt sie zurueck (revived_at). Ablehnung schreibt nichts (L94), Wiederholung aendert nichts.
  T  Plan-Sektion user_md_retire: eine Profilzeile verlustfrei streichen. Changelog (field user.md/retire, alter
     Text) VOR der Datei, nur mit verifiziertem Zitat, Wiederholung erkannt, Ablehnung schreibt nichts. Eine
     gestrichene Aussage kehrt nicht per Wiederholung zurueck (wie nach user_md_supersede, D9).

Alles gegen apply_wrapup.py als Subprozess mit frischem Temp-Home (_isolated_home); echte Stores werden nie gelesen.
Exit-Codes: 0 = alles gruen, 1 = Fehler.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "apply_wrapup.py")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolated_home import process_env  # noqa: E402

ENV, ISO_HOME = process_env(PYTHONIOENCODING="utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

TESTS = PASSED = ERRORS = 0
DATE = "2026-11-03"
SID = "sess-511"
PLAIN = "Diese Zeile steht schon in den globalen Regeln. Sie wird im Profil gestrichen, die alte Fassung bleibt im Protokoll."
QUOTE = "ja zu Punkt 3 und parallel zum Pilot"

USER_MD = (
    "# User Profile\n\n"
    "*Initialized: 2026-07-01*\n\n"
    "## Preferences\n"
    "- Antworten kurz halten (UC3, 2026-07-01)\n"
    "- Klartext statt Fachjargon in jedem Bericht (UC4, 2026-07-02)\n\n"
    "## Work Style\n"
    "- (Will be populated through session observations)\n"
)


def check(cond, msg):
    global TESTS, PASSED, ERRORS
    TESTS += 1
    if cond:
        PASSED += 1
        print(f"  PASS: {msg}")
    else:
        ERRORS += 1
        print(f"  FAIL: {msg}")


MEM_ROOT = tempfile.mkdtemp(prefix="r511-run-")
atexit.register(shutil.rmtree, MEM_ROOT, ignore_errors=True)
PROJ_DIR = os.path.join(ISO_HOME, ".claude", "projects", "proj-511")


def write_bytes(path, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def put(mem, rel, data):
    write_bytes(os.path.join(mem, rel), (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def read(mem, rel):
    with open(os.path.join(mem, rel), "rb") as fh:
        return fh.read().decode("utf-8")


def load(mem, rel):
    return json.loads(read(mem, rel))


def snapshot(mem):
    out = {}
    for base, _d, names in os.walk(mem):
        for n in names:
            rel = os.path.relpath(os.path.join(base, n), mem).replace(os.sep, "/")
            if rel != "working/store.lock":
                with open(os.path.join(base, n), "rb") as fh:
                    out[rel] = fh.read()
    return out


def cand(cid, key, status="observed", occ=1, last_seen="2026-09-01", **extra):
    c = {"id": cid, "key": key, "observation": f"Beobachtung {key}", "status": status, "signal_type": "preference",
         "confidence": 0.5, "occurrences": occ, "sessions": ["alt"], "evidence": [], "first_seen": last_seen,
         "last_seen": last_seen, "trust_source": "conversation"}
    c.update(extra)
    return c


def make_mem(queue=None):
    mem = tempfile.mkdtemp(prefix="r511-", dir=MEM_ROOT)
    for d in ("learnings", "working", "identity", "context"):
        os.makedirs(os.path.join(mem, d), exist_ok=True)
    put(mem, "learnings/learnings.json", [])
    put(mem, "working/user-candidates.json", queue or [])
    put(mem, "identity/user-changelog.json", [])
    put(mem, "context/open-tasks.json", [])
    write_bytes(os.path.join(mem, "identity/user.md"), USER_MD.encode("utf-8"))
    return mem


def transcript(sid, *texts):
    recs = [{"type": "user", "isSidechain": False, "message": {"role": "user", "content": t},
             "origin": {"kind": "human"}, "promptSource": "typed", "turnOrigin": "human"} for t in texts]
    write_bytes(os.path.join(PROJ_DIR, f"{sid}.jsonl"),
                ("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n").encode("utf-8"))


def run(mem, plan, sid=SID):
    proc = subprocess.run([sys.executable, SCRIPT, mem, "--session-id", sid], input=json.dumps(plan),
                          capture_output=True, text=True, encoding="utf-8", env=dict(ENV))
    try:
        res = json.loads(proc.stdout)
    except json.JSONDecodeError:
        res = {"_stdout": proc.stdout, "_stderr": proc.stderr}
    res.setdefault("tally", {})
    return proc.returncode, res


transcript(SID, "Ja zu Punkt 1, ja zu Punkt 2, ja zu Punkt 3 und parallel zum Pilot und Go.")

# ----------------------------------------------------------------------------- R: Learning-Schwelle
print("== R  Learnings unter importance 3")
mem = make_mem()
rc, out = run(mem, {"date": DATE, "learnings": [
    {"text": "Trivia: die Datei heisst so", "importance": 1, "tags": []},
    {"text": "Workflow-Kleinigkeit zum Reihenfolge-Tausch", "importance": 2, "tags": []},
    {"text": "Nicht offensichtliches Verhalten der Projektion bei Caps", "importance": 3, "tags": []},
    {"text": "Regelaenderung mit niedriger Wichtigkeit", "importance": 2, "tags": [], "rule_class": True}]})
rows = load(mem, "learnings/learnings.json")
t = out["tally"]
check(rc == 0 and t.get("learnings_skipped_low_importance") == 2,
      f"R1 importance 1 und 2 werden nicht angelegt, Tally zaehlt 2 (ist {t.get('learnings_skipped_low_importance')})")
check(sorted(r["text"][:12] for r in rows) == sorted(["Nicht offens", "Regelaenderu"]),
      "R2 importance 3 und das rule_class-Learning stehen im Store")

# ----------------------------------------------------------------------------- E: identity_expire
print("== E  identity_expire")
QUEUE = [cand("UC1", "alt-einmal"), cand("UC2", "frisch-einmal", last_seen="2026-10-30"),
         cand("UC3", "alt-zweimal", occ=2), cand("UC4", "alt-inferred", status="inferred"),
         cand("UC5", "alt-promoted", status="promoted"), cand("UC6", "alt-confirmed", status="confirmed")]
mem = make_mem(QUEUE)
rc, out = run(mem, {"date": DATE, "identity_expire": {"older_than_days": 30, "reason": "Owner-Go 03.11.: Queue verschlanken"}})
q = {c["id"]: c for c in load(mem, "working/user-candidates.json")}
t = out["tally"]
check(rc == 0 and sorted(t.get("candidates_expired_ids") or []) == ["UC1", "UC4"],
      f"E1 nur alte Einmal-Beobachtungen verfallen (ist {t.get('candidates_expired_ids')})")
check(q["UC1"]["status"] == "expired" and q["UC1"].get("expired_from") == "observed"
      and q["UC1"].get("expired_at") == DATE and "verschlanken" in q["UC1"].get("expired_reason", ""),
      "E2 Vermerk am Kandidaten: expired_at, expired_from, expired_reason")
check(len(q) == 6 and q["UC2"]["status"] == "observed" and q["UC3"]["status"] == "observed"
      and q["UC5"]["status"] == "promoted" and q["UC6"]["status"] == "confirmed",
      "E3 nichts geloescht, frische, mehrfach gesehene, promotete und bestaetigte bleiben unveraendert")
check(t.get("queue_open") == 3, f"E4 verfallene zaehlen nicht als offen (queue_open {t.get('queue_open')}, erwartet 3)")
before = snapshot(mem)
rc, out = run(mem, {"date": DATE, "identity_expire": {"older_than_days": 30, "reason": "nochmal"}})
check(rc == 0 and (out["tally"].get("candidates_expired_ids") or []) == [] and snapshot(mem) == before,
      "E5 Wiederholung aendert nichts")
for bad, why in (({"older_than_days": 30}, "ohne reason"), ({"older_than_days": 7, "reason": "x"}, "unter 14 Tagen"),
                 ({"older_than_days": "30", "reason": "x"}, "Tage nicht ganzzahlig")):
    m2 = make_mem(QUEUE)
    before = snapshot(m2)
    rc, out = run(m2, {"date": DATE, "identity_expire": bad})
    check(rc == 2 and snapshot(m2) == before, f"E6 {why}: Plan abgelehnt, nichts geschrieben")
rc, out = run(mem, {"date": DATE, "user_candidates": [
    {"key": "alt-einmal", "observation": "Beobachtung alt-einmal", "signal_type": "preference", "confidence": 0.5,
     "evidence": ["neu"], "status": "observed", "trust_source": "conversation"}]}, sid="sess-andere")
q = {c["id"]: c for c in load(mem, "working/user-candidates.json")}
check(rc == 0 and q["UC1"]["status"] != "expired" and q["UC1"].get("revived_at") == DATE
      and q["UC1"]["occurrences"] == 2,
      f"E7 erneute Nennung aus anderer Sitzung holt den Kandidaten zurueck (status {q['UC1']['status']})")

# ----------------------------------------------------------------------------- T: user_md_retire
print("== T  user_md_retire")
mem = make_mem()
entry = {"line_id": "UC4", "reason": "steht wortgleich in ~/.claude/CLAUDE.md (L257)", "plain": PLAIN, "quote": QUOTE}
rc, out = run(mem, {"date": DATE, "user_md_retire": [entry]})
md, log = read(mem, "identity/user.md"), load(mem, "identity/user-changelog.json")
check(rc == 0 and out["tally"].get("user_lines_retired") == 1 and "Klartext statt Fachjargon" not in md
      and md == USER_MD.replace("- Klartext statt Fachjargon in jedem Bericht (UC4, 2026-07-02)\n", ""),
      "T1 die Zeile ist weg, der Rest der Datei Byte fuer Byte gleich")
check(len(log) == 1 and log[0]["field"] == "user.md/retire" and "Klartext statt Fachjargon" in log[0]["old_value"]
      and log[0].get("new_value") is None and log[0]["quote_verified"] is True and log[0]["plain"] == PLAIN,
      "T2 Changelog haelt den alten Text, Zitat und Klartext fest")
before = snapshot(mem)
rc, out = run(mem, {"date": DATE, "user_md_retire": [entry]})
check(rc == 0 and out["tally"].get("retire_skipped_unchanged") == 1 and snapshot(mem) == before,
      "T3 Wiederholung erkannt, nichts geschrieben")
rc, out = run(mem, {"date": DATE, "user_candidates": [
    {"key": "klartext-neu", "observation": "Klartext statt Fachjargon in jedem Bericht", "signal_type": "preference",
     "confidence": 0.8, "evidence": [], "status": "inferred", "trust_source": "conversation"}]}, sid="s-a")
rc, out = run(mem, {"date": DATE, "user_candidates": [
    {"key": "klartext-neu", "observation": "Klartext statt Fachjargon in jedem Bericht", "signal_type": "preference",
     "confidence": 0.8, "evidence": [], "status": "inferred", "trust_source": "conversation"}]}, sid="s-b")
check(rc == 0 and "Klartext statt Fachjargon" not in read(mem, "identity/user.md")
      and (out["tally"].get("promotion_blocked_superseded") or 0) >= 1,
      "T4 die gestrichene Aussage kehrt nicht per Wiederholung zurueck")

mem = make_mem()
rc, out = run(mem, {"date": DATE, "user_md_retire": [dict(entry, quote="das hat der User so nie gesagt")]})
check(rc == 0 and out["tally"].get("retire_skipped_unverified") == 1
      and read(mem, "identity/user.md") == USER_MD and load(mem, "identity/user-changelog.json") == [],
      "T5 unverifiziertes Zitat: Eintrag uebersprungen, nichts geschrieben")
for bad, why in ((dict(entry, reason=""), "ohne reason"), (dict(entry, plain=""), "ohne plain"),
                 (dict(entry, quote=""), "ohne quote"), ({**entry, "line_id": "UC99"}, "unbekannte Zeile"),
                 ({k: v for k, v in entry.items() if k != "line_id"} | {"match": "## Preferences"}, "Ueberschrift"),
                 ({k: v for k, v in entry.items() if k != "line_id"} | {"match": "*Initialized: 2026-07-01*"}, "Praeambel"),
                 ({k: v for k, v in entry.items() if k != "line_id"}
                  | {"match": "- (Will be populated through session observations)"}, "Platzhalter"),
                 (dict(entry, trust_source="web"), "fremde Quelle")):
    m2 = make_mem()
    before = snapshot(m2)
    rc, out = run(m2, {"date": DATE, "user_md_retire": [bad]})
    check(rc == 2 and snapshot(m2) == before, f"T6 {why}: Plan abgelehnt, nichts geschrieben")

# ----------------------------------------------------------------------------- Codex-Verifier Runde 1 (03.11.)
print("== C  Codex Runde 1")
m2 = make_mem()
before = snapshot(m2)
rc, out = run(m2, {"date": DATE, "user_md_retire": [entry],
                   "user_md_supersede": [{"line_id": "UC4", "new_text": "- Klartext immer (UC4, 2026-07-02)",
                                          "reason": "r", "plain": PLAIN, "quote": QUOTE}]})
check(rc == 2 and snapshot(m2) == before, "C1 (V1-01) retire und supersede im selben Plan: abgelehnt, nichts geschrieben")
m2 = make_mem()
before = snapshot(m2)
rc, out = run(m2, {"date": DATE, "learnings": [{"text": "Ein ganz normales Learning der Stufe vier", "importance": 4}],
                   "identity_expire": {"older_than_days": 10 ** 9, "reason": "zu gross"}})
check(rc == 2 and snapshot(m2) == before, "C2 (V1-02) unmoeglicher Zeitraum: abgelehnt vor dem ersten Byte")
m2 = make_mem()
rc, out = run(m2, {"date": DATE, "user_md_retire": [entry]})
write_bytes(os.path.join(m2, "identity/user.md"), USER_MD.encode("utf-8"))      # Zeile spaeter wieder aufgenommen
transcript("sess-511b", "Ja zu Punkt 1, ja zu Punkt 2, ja zu Punkt 3 und parallel zum Pilot und Go.")
rc, out = run(m2, {"date": DATE, "user_md_retire": [entry]}, sid="sess-511b")      # spaetere Sitzung
log = load(m2, "identity/user-changelog.json")
check(rc == 0 and sum(1 for e in log if e.get("field") == "user.md/retire") == 2
      and "Klartext statt Fachjargon" not in read(m2, "identity/user.md"),
      "C3 (V1-03) erneutes Streichen einer wieder aufgenommenen Zeile bekommt einen eigenen Protokolleintrag")
m2 = make_mem()
rc, out = run(m2, {"date": DATE, "user_md_retire": [entry]})
write_bytes(os.path.join(m2, "identity/user.md"), USER_MD.encode("utf-8"))      # Absturz: Protokoll da, Datei noch alt
rc, out = run(m2, {"date": DATE, "user_md_retire": [entry]})                    # gleiche Sitzung holt nach
log = load(m2, "identity/user-changelog.json")
check(rc == 0 and sum(1 for e in log if e.get("field") == "user.md/retire") == 1
      and "Klartext statt Fachjargon" not in read(m2, "identity/user.md"),
      "C4 Nachholen nach Absturz in derselben Sitzung: Zeile weg, kein doppelter Protokolleintrag")

m2 = make_mem(QUEUE)
before = snapshot(m2)
rc, out = run(m2, {"date": "0001-01-01", "identity_expire": {"older_than_days": 14, "reason": "zu frueh"},
                   "learnings": [{"text": "Ein ganz normales Learning der Stufe vier", "importance": 4}]})
check(rc == 2 and snapshot(m2) == before, "C5 (#10282) Plan-Datum nahe Jahr 1: abgelehnt vor dem ersten Byte")
m2 = make_mem()
rc, out = run(m2, {"date": DATE, "user_md_retire": [entry]})
write_bytes(os.path.join(m2, "identity/user.md"), USER_MD.encode("utf-8"))      # Zeile kam per supersede zurueck
log = load(m2, "identity/user-changelog.json")
log.append({"field": "user.md/supersede", "old_value": "B", "new_value": "Klartext statt Fachjargon", "session": SID})
put(m2, "identity/user-changelog.json", log)
zweite = dict(entry, line_id="UC3")
rc, out = run(m2, {"date": DATE, "user_md_retire": [entry, zweite]})            # dieselbe Sitzung streicht erneut
log = load(m2, "identity/user-changelog.json")
check(rc == 0 and sum(1 for e in log if e.get("field") == "user.md/retire") == 3,
      f"C6 (#10282) erneutes Streichen nach Wiederaufnahme in DERSELBEN Sitzung wird protokolliert (out {out})")

# ----------------------------------------------------------------------- K: corrections.profile_candidates
print("== K  corrections.profile_candidates (#10173)")
KAPUTT = "F\u00c3\u00bchrt Reviews gr\u00c3\u00bcndlich durch"
HEIL = "Führt Reviews gründlich durch"
GRUND = "Encoding-Reparatur"
m2 = make_mem([cand("UC1", "moji", observation=KAPUTT, evidence=[KAPUTT, "sauber"]), cand("UC2", "heil")])
rc, out = run(m2, {"date": DATE, "corrections": {"profile_candidates": [
    {"id": "UC1", "observation": HEIL, "evidence": [HEIL, "sauber"], "reason": GRUND}]}})
q = {c["id"]: c for c in load(m2, "working/user-candidates.json")}
check(rc == 0 and q["UC1"]["observation"] == HEIL and q["UC1"]["evidence"] == [HEIL, "sauber"]
      and len(q["UC1"]["corrections"]) == 2 and q["UC1"]["corrections"][0]["was"] == KAPUTT
      and "corrections" not in q["UC2"],
      f"K1 Kodierung repariert, alter Text in corrections[], Nachbar unberuehrt (out {out})")
vorher = snapshot(m2)
rc, out = run(m2, {"date": DATE, "corrections": {"profile_candidates": [
    {"id": "UC1", "observation": HEIL, "evidence": [HEIL, "sauber"], "reason": GRUND}]}})
stand = {c["id"]: c for c in load(m2, "working/user-candidates.json")}
check(rc == 0 and len(stand["UC1"]["corrections"]) == 2, "K2 Wiederholung aendert nichts")
for bad, why in (({"id": "UC1", "observation": "Ganz andere Aussage", "reason": GRUND}, "anderer Wortlaut"),
                 ({"id": "UC9", "observation": HEIL, "reason": GRUND}, "unbekannte id"),
                 ({"id": "UC1", "reason": GRUND}, "kein Feld"),
                 ({"id": "UC1", "observation": HEIL}, "ohne reason"),
                 ({"id": "UC1", "status": "confirmed", "reason": GRUND}, "fremdes Feld")):
    m3 = make_mem([cand("UC1", "moji", observation=KAPUTT)])
    before = snapshot(m3)
    rc, out = run(m3, {"date": DATE, "learnings": [{"text": "Ein ganz normales Learning der Stufe vier", "importance": 4}],
                       "corrections": {"profile_candidates": [bad]}})
    check(rc == 2 and snapshot(m3) == before, f"K3 {why}: Plan abgelehnt, nichts geschrieben")
m3 = make_mem([cand("UC1", "moji", observation=KAPUTT, evidence=["sauber"]), cand("UC2", "heil")])
proc = subprocess.run([sys.executable, "-X", "utf8", SCRIPT, m3, "--propose-encoding-corrections"], env=ENV,
                      capture_output=True, encoding="utf-8")
vorschlag = json.loads(proc.stdout)["corrections"]["profile_candidates"]
check(proc.returncode == 0 and [v["id"] for v in vorschlag] == ["UC1"] and vorschlag[0]["observation"] == HEIL
      and "evidence" not in vorschlag[0], f"K4 Vorschlag nennt nur die kaputte Zeile und das kaputte Feld ({vorschlag})")

m3 = make_mem([cand("UC1", "moji", observation=KAPUTT, corrections=None)])
before = snapshot(m3)
rc, out = run(m3, {"date": DATE, "learnings": [{"text": "Ein ganz normales Learning der Stufe vier", "importance": 4}],
                   "corrections": {"profile_candidates": [{"id": "UC1", "observation": HEIL, "reason": GRUND}]}})
check(rc == 2 and snapshot(m3) == before, f"K5 corrections ohne Listenform: abgelehnt vor dem ersten Byte (rc {rc})")

print(f"\n{PASSED}/{TESTS} bestanden, {ERRORS} Fehler")
sys.exit(1 if ERRORS else 0)

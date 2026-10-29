#!/usr/bin/env python3
"""Tests fuer P2 (B1 Release A, zentrales Profil): Zentralpfad, Modus, Guards, Zentral-Lock, Zentralmodus, Briefing.

Spec: docs/superpowers/specs/2026-10-27-identitaet-verdichtung-messbar-design.md, Abschnitt P2.
Plan: docs/superpowers/plans/2026-10-28-p2-zentrales-profil.md (Tasks 1, 2, 4, 5).

Was hier bewiesen wird:
  S  scripts/identity_store.py: das Zentral wird erst beim Aufruf aufgeloest (--identity-store, dann
     AGENTIC_OS_IDENTITY_STORE, dann das Home), es ist genau dann aktiv, wenn identity/profile.md existiert, und ein
     Temp-Store schreibt nie in ein Zentral, das nur ueber das Home gefunden wurde (E10).

Alle Stores liegen in Temp-Ordnern, Subprozesse laufen mit dem frischen Temp-Home aus _isolated_home. Echte Stores
und das echte Zentral werden nie gelesen oder geschrieben.
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
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

from _isolated_home import process_env  # noqa: E402

ENV, ISO_HOME = process_env(PYTHONIOENCODING="utf-8")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import identity_store as ids  # noqa: E402

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


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(text.encode("utf-8"))


# Alle Test-Ordner unter EINEM Elternordner, der am Ende (auch nach einem Absturz) geloescht wird
MEM_ROOT = tempfile.mkdtemp(prefix="central-run-")
atexit.register(shutil.rmtree, MEM_ROOT, ignore_errors=True)


def make_dir(prefix):
    return tempfile.mkdtemp(prefix=prefix, dir=MEM_ROOT)


def make_central(active=True):
    store = make_dir("central-")
    if active:
        write(os.path.join(store, ids.PROFILE_MD), "# Profil\n\n- Eine Zeile (PC1, 2026-10-28)\n")
    return store


# ============================================================================= S: Zentralpfad
print("--- S: Zentralpfad")

# S1 Reihenfolge der Quellen: --identity-store, dann Umgebungsvariable, dann das Home
check(ids.resolve_store("X", {ids.ENV_VAR: "Y"}) == (os.path.abspath("X"), "cli"),
      f"S1 --identity-store gewinnt gegen die Umgebungsvariable ({ids.resolve_store('X', {ids.ENV_VAR: 'Y'})})")
check(ids.resolve_store(None, {ids.ENV_VAR: "Y"}) == (os.path.abspath("Y"), "env"),
      "S1 ohne --identity-store gilt die Umgebungsvariable")
check(ids.resolve_store(None, {ids.ENV_VAR: ""})[1] == "home",
      "S1 eine leere Umgebungsvariable zaehlt nicht")
# der Home-Fall im Kindprozess: dort ist das Home das isolierte Temp-Home, nicht das echte
child_env = {**ENV, ids.ENV_VAR: ""}  # leer zaehlt wie nicht gesetzt (S1 oben)
probe = ("import json, os, sys; sys.path.insert(0, sys.argv[1]); import identity_store as s; "
         "print(json.dumps(s.resolve_store(None, os.environ)))")
r = subprocess.run([sys.executable, "-X", "utf8", "-c", probe, SCRIPTS], capture_output=True, encoding="utf-8",
                   env=child_env)
got = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
check(got == [os.path.join(ISO_HOME, ".agent-memory"), "home"],
      f"S1 ohne beides gilt <home>/.agent-memory, im Kindprozess das isolierte Home ({got}, rc={r.returncode} {r.stderr[-200:]})")

# S2 kein Pfad auf Modulebene: vor der ersten Funktion weder expanduser noch environ
with open(os.path.join(SCRIPTS, "identity_store.py"), "rb") as fh:
    src = fh.read().decode("utf-8-sig")
head = src.split("\ndef ", 1)[0]
check("expanduser" not in head and "environ" not in head,
      "S2 identity_store.py loest vor der ersten Funktion keinen Pfad auf (kein expanduser, kein environ)")

# S3 aktiv genau mit profile.md; select_paths waehlt danach
mem = make_dir("proj-")
inactive, active = make_central(False), make_central(True)
check(not ids.central_active(inactive) and ids.central_active(active),
      "S3 central_active: False ohne profile.md, True mit profile.md")
check(ids.select_paths(mem, inactive, {}) == ids.legacy_paths(mem),
      f"S3 ohne profile.md: legacy_paths(mem) ({ids.select_paths(mem, inactive, {})})")
check(ids.select_paths(mem, active, {}) == ids.central_paths(active),
      "S3 mit profile.md: central_paths(store)")
lp, cp = ids.legacy_paths(mem), ids.central_paths(active)
check((lp.md, lp.changelog, lp.queue, lp.id_prefix, lp.central)
      == ("identity/user.md", "identity/user-changelog.json", "working/user-candidates.json", "UC", False)
      and lp.root == mem,
      f"S3 legacy_paths: die heutigen Projektdateien, Prefix UC ({lp})")
check((cp.md, cp.changelog, cp.queue, cp.id_prefix, cp.central)
      == (ids.PROFILE_MD, ids.PROFILE_CHANGELOG, ids.PROFILE_QUEUE, "PC", True) and cp.root == active,
      f"S3 central_paths: profile.*, Prefix PC ({cp})")
check((ids.PROFILE_MD, ids.PROFILE_CHANGELOG, ids.PROFILE_QUEUE, ids.PROFILE_LOCK, ids.BRIEFING_LINES,
       ids.MIGRATION_LEDGER) == ("identity/profile.md", "identity/profile-changelog.json",
                                 "identity/profile-candidates.json", "identity/profile.lock",
                                 "identity/briefing-lines.txt", "identity/profile-migration-ledger.json"),
      "S3 Namensraum identity/profile.* wie in der Spec")

# S4 Guard (E10): ein Temp-Store schreibt nie in ein Zentral, das nur ueber das Home gefunden wurde
tmp_mem = make_dir("proj-")  # liegt unter tempfile.gettempdir()
home_dir = make_dir("home-")
write(os.path.join(home_dir, ".agent-memory", ids.PROFILE_MD), "# Profil\n")
fake_env = {"USERPROFILE": home_dir, "HOME": home_dir}
real_expand = os.path.expanduser
os.path.expanduser = lambda p: home_dir if p == "~" else real_expand(p)  # nur fuer diesen Abschnitt
try:
    src_home = ids.resolve_store(None, fake_env)
    try:
        ids.select_paths(tmp_mem, None, fake_env)
        err = None
    except ids.GuardError as exc:
        err = str(exc)
    check(src_home[1] == "home" and err is not None and "--identity-store" in err,
          f"S4 Temp-Store + Zentral nur ueber das Home aktiv: GuardError mit Hinweis auf --identity-store ({err})")
    try:
        ok_cli = ids.select_paths(tmp_mem, os.path.join(home_dir, ".agent-memory"), fake_env).central
        ok_env = ids.select_paths(tmp_mem, None, {ids.ENV_VAR: os.path.join(home_dir, ".agent-memory")}).central
        guard_ok = None
    except ids.GuardError as exc:
        ok_cli = ok_env = False
        guard_ok = str(exc)
    check(ok_cli and ok_env, f"S4 mit Quelle cli oder env kein Fehler, Zentralmodus ({guard_ok})")
    os.remove(os.path.join(home_dir, ".agent-memory", ids.PROFILE_MD))
    try:
        legacy = ids.select_paths(tmp_mem, None, fake_env)
        guard_legacy = None
    except ids.GuardError as exc:
        legacy, guard_legacy = None, str(exc)
    check(legacy == ids.legacy_paths(tmp_mem), f"S4 ohne aktives Zentral kein Fehler, Legacy ({guard_legacy})")
finally:
    os.path.expanduser = real_expand

# S5 same_store: realpath + normcase
a = make_dir("same-")
b = make_dir("same-")
check(ids.same_store(a, os.path.join(a, "..", os.path.basename(a))), "S5 X und X/../X sind derselbe Store")
check(not ids.same_store(a, b), "S5 zwei verschiedene Ordner sind verschiedene Stores")
if os.name == "nt":
    check(ids.same_store(a, a.upper()), "S5 Windows: Gross-/Kleinschreibung zaehlt nicht")

# S6 project_key: Elternordner relativ zum Home, "~" fuer den Home-Store, sonst absolut
hk = make_dir("pkhome-")
check(ids.project_key(os.path.join(hk, ".agent-memory"), hk) == "~", "S6 Home-Store -> ~")
check(ids.project_key(os.path.join(hk, "work", "tools", ".agent-memory"), hk) == "work/tools",
      f"S6 <home>/work/tools/.agent-memory -> work/tools ({ids.project_key(os.path.join(hk, 'work', 'tools', '.agent-memory'), hk)})")
outside = make_dir("pkout-")
key = ids.project_key(os.path.join(outside, ".agent-memory"), hk)
check(os.path.isabs(key) and ids.same_store(key, outside), f"S6 ausserhalb des Homes: absoluter Pfad ({key})")

# ============================================================================= L: Zentral-Lock
print("--- L: Zentral-Lock")
import time  # noqa: E402

import store_lock  # noqa: E402

# L1 eigenes Lock fuer das Zentral: identity/profile.lock, nicht working/store.lock
store = make_central()
with store_lock.store_lock(store, lock_rel=ids.PROFILE_LOCK) as held:
    check(os.path.isfile(os.path.join(store, *ids.PROFILE_LOCK.split("/")))
          and not os.path.exists(os.path.join(store, "working", "store.lock")),
          f"L1 store_lock(lock_rel=PROFILE_LOCK) legt identity/profile.lock an, nicht working/store.lock ({held})")

# L2 Home-Store als Projekt (mem == store): Projekt-Lock und Zentral-Lock verschachtelt, kein Selbst-Deadlock (E8)
t0 = time.time()
try:
    with store_lock.store_lock(store, timeout=2):
        with store_lock.store_lock(store, timeout=2, lock_rel=ids.PROFILE_LOCK):
            nested = True
except store_lock.LockTimeout:
    nested = False
check(nested and time.time() - t0 < 1, f"L2 mem == store: Projekt-Lock, dann Zentral-Lock, unter 1 s ({time.time() - t0:.2f}s)")

# L3 Stall-Probe (E8): ein Halter in einem anderen Prozess haelt profile.lock -> LockTimeout nennt profile.lock;
# das Projekt-Lock desselben Stores bleibt davon frei (zwei Dateien, zwei Sperren)
HOLD = ("import sys, time; sys.path.insert(0, sys.argv[1]); import store_lock\n"
        "with store_lock.store_lock(sys.argv[2], lock_rel=sys.argv[3]):\n"
        "    print('held', flush=True); time.sleep(60)\n")
holder = subprocess.Popen([sys.executable, "-c", HOLD, SCRIPTS, store, ids.PROFILE_LOCK], stdout=subprocess.PIPE,
                          text=True, encoding="utf-8", env=ENV)
try:
    holder.stdout.readline()
    t0 = time.time()
    try:
        with store_lock.store_lock(store, timeout=0.5, lock_rel=ids.PROFILE_LOCK):
            msg = None
    except store_lock.LockTimeout as exc:
        msg = str(exc)
    check(msg is not None and "profile.lock" in msg and time.time() - t0 >= 0.4,
          f"L3 Halter auf profile.lock: zweiter Zugriff wartet und meldet profile.lock ({msg})")
    t0 = time.time()
    with store_lock.store_lock(store, timeout=0.5):
        check(time.time() - t0 < 0.3, "L3 das Projekt-Lock desselben Stores bleibt frei")
finally:
    holder.kill()
    holder.wait()

# ============================================================================= C: Zentralmodus
print("--- C: Zentralmodus")

SCRIPT = os.path.join(SCRIPTS, "apply_wrapup.py")
DATE = "2026-10-28"
SID, SID2 = "sess-c1", "sess-c2"
PROJ_DIR = os.path.join(ISO_HOME, ".claude", "projects", "proj-c")
USER_SAYS = "Ich will das immer so: erst eine kurze Zusammenfassung, dann die Details."
QUOTE = "erst eine kurze Zusammenfassung, dann die Details"
SUP_SAYS = "Bei der Fehlersuche will ich lieber ausfuehrliche Antworten mit allen Details."
SUP_QUOTE = "lieber ausfuehrliche Antworten mit allen Details"
PLAIN = "Kurz gesagt: Die Profilzeile wird ersetzt. Ab jetzt gilt nur noch die neue Fassung."
PROJECT_USER_MD = ("# User Profile\n\n*Initialized: 2026-07-01*\n\n## Preferences\n"
                   "- Projektzeile bleibt hier (UC3, 2026-07-01)\n")
PROFILE_TEXT = "# Profil\n\n## Preferences\n- Antworten kurz halten\n\n## Work Style\n- Arbeitet gern abends\n"
LOCKS = ("working/store.lock", "identity/profile.lock")
PROJECT_IDENTITY = ("identity/user.md", "identity/user-changelog.json", "working/user-candidates.json")


def put(root, rel, data):
    write(os.path.join(root, rel), json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def read_rel(root, rel):
    with open(os.path.join(root, rel), "rb") as fh:
        return fh.read().decode("utf-8")


def load_rel(root, rel):
    return json.loads(read_rel(root, rel))


def make_project(user_md=PROJECT_USER_MD):
    mem = os.path.join(make_dir("proj-"), ".agent-memory")  # echtes Layout: der Projektordner ist der Elternordner
    put(mem, "learnings/learnings.json", [])
    put(mem, "working/user-candidates.json", [])
    put(mem, "identity/user-changelog.json", [])
    put(mem, "context/open-tasks.json", [])
    write(os.path.join(mem, "identity", "user.md"), user_md)
    return mem


def make_profile(text=PROFILE_TEXT, queue=None, changelog=None):
    store = make_dir("central-")
    write(os.path.join(store, ids.PROFILE_MD), text)
    put(store, ids.PROFILE_QUEUE, queue or [])
    put(store, ids.PROFILE_CHANGELOG, changelog or [])
    return store


def snapshot(root):
    files = {}
    for base, _dirs, names in os.walk(root):
        for n in names:
            p = os.path.join(base, n)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if rel in LOCKS:
                continue  # OS-Lock-Handle: Infrastruktur, keine Daten
            with open(p, "rb") as fh:
                files[rel] = fh.read()
    return files


def same_identity(before, after):
    return all(before.get(k) == after.get(k) for k in PROJECT_IDENTITY)


class Tally(dict):
    def __missing__(self, key):
        return None


def run(mem, plan, *extra, sid=SID, env_extra=None):
    run_env = dict(ENV)
    run_env.update(env_extra or {})
    proc = subprocess.run([sys.executable, SCRIPT, mem, "--session-id", sid, *extra], input=json.dumps(plan),
                          capture_output=True, text=True, encoding="utf-8", env=run_env)
    try:
        res = json.loads(proc.stdout)
    except json.JSONDecodeError:
        res = {"_stdout": proc.stdout, "_stderr": proc.stderr}
    res["tally"] = Tally(res.get("tally") or {})
    return proc.returncode, res


def rec_user(text):
    return {"type": "user", "isSidechain": False, "message": {"role": "user", "content": text},
            "origin": {"kind": "human"}, "promptSource": "typed", "turnOrigin": "human"}


def transcript(sid, *texts):
    lines = [json.dumps(rec_user(t), ensure_ascii=False) for t in texts]
    write(os.path.join(PROJ_DIR, f"{sid}.jsonl"), "\n".join(lines) + "\n")


def cand(key="kurz-zuerst", **kw):
    c = {"key": key, "observation": "Antworten beginnen mit einer kurzen Zusammenfassung", "signal_type": "preference",
         "confidence": 0.8, "evidence": [f"session {DATE}"], "trust_source": "conversation"}
    c.update(kw)
    return c


def qrow(key, **kw):
    r = {"id": "PC1", "key": key, "observation": "Antworten beginnen mit einer kurzen Zusammenfassung",
         "status": "observed", "signal_type": "preference", "confidence": 0.5, "occurrences": 1,
         "evidence": ["session 2026-10-01"], "first_seen": "2026-10-01", "last_seen": "2026-10-01",
         "trust_source": "conversation", "sessions": ["sess-alt"]}
    r.update(kw)
    return r


transcript(SID, "Hallo", USER_SAYS, SUP_SAYS)

# C1 Promotion mit verifiziertem Zitat ins Zentral; die Projektdateien bleiben byte-gleich
mem, store = make_project(), make_profile()
pb = snapshot(mem)
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, "--identity-store", store)
md = read_rel(store, ids.PROFILE_MD)
log = load_rel(store, ids.PROFILE_CHANGELOG)
q = load_rel(store, ids.PROFILE_QUEUE)
key = ids.project_key(mem, ISO_HOME)
check(rc == 0 and f"- Antworten beginnen mit einer kurzen Zusammenfassung (PC1, {DATE})" in md
      and "- Antworten kurz halten" in md,
      f"C1 die Zeile steht in profile.md mit (PC1, {DATE}), der Bestand bleibt (rc={rc}, {out.get('error')})")
hit = [e for e in log if e.get("candidate_id") == "PC1"]
check(len(hit) == 1 and (hit[0].get("origin") or {}).get("projekt") == key,
      f"C1 profile-changelog.json: Eintrag mit candidate_id PC1 und origin.projekt ({hit})")
check(len(q) == 1 and q[0].get("id") == "PC1" and q[0].get("status") == "promoted"
      and q[0].get("origin") == {"projekt": key, "id": None},
      f"C1 Zentral-Queue: PC1 promoted mit origin {{projekt, id: null}} ({q})")
check(same_identity(pb, snapshot(mem)), "C1 Projekt-user.md, -Changelog und -Queue sind byte-gleich")
check(out["tally"]["identity_target"] == "central" and out["tally"]["central_path"]
      and ids.same_store(out["tally"]["central_path"], store),
      f"C1 Tally: identity_target central, central_path ({out['tally']['identity_target']}, {out['tally']['central_path']})")
check("profile.md promotet" in str(out.get("identity_status_line")),
      f"C1 Statuszeile nennt profile.md ({out.get('identity_status_line')})")

# C2 Schreibreihenfolge: Zentral-Changelog, dann profile.md, dann Zentral-Queue
fw = out.get("files_written") or []
order = [ids.PROFILE_CHANGELOG, ids.PROFILE_MD, ids.PROFILE_QUEUE]
check(all(f in fw for f in order) and [fw.index(f) for f in order] == sorted(fw.index(f) for f in order),
      f"C2 files_written: Changelog, dann profile.md, dann Queue ({fw})")

# C3 ohne profile.md: wie heute, Ziel user.md, der Zentral-Ordner entsteht nicht
mem = make_project()
nowhere = os.path.join(MEM_ROOT, "kein-zentral")
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, "--identity-store", nowhere)
check(rc == 0 and f"(UC1, {DATE})" in read_rel(mem, "identity/user.md") and out["tally"]["identity_target"] == "project"
      and not os.path.exists(nowhere),
      f"C3 ohne profile.md: user.md, identity_target project, kein Zentral-Ordner (rc={rc}, {out['tally']['identity_target']})")

# C4 Zweitnennung aus anderer Sitzung UND anderem Projekt erhoeht occurrences desselben PC-Kandidaten
store = make_profile()
mem_a, mem_b = make_project(), make_project()
rc1, _ = run(mem_a, {"date": DATE, "user_candidates": [cand("zweimal")]}, "--identity-store", store, sid=SID)
rc2, out = run(mem_b, {"date": DATE, "user_candidates": [cand("zweimal")]}, "--identity-store", store, sid=SID2)
q = load_rel(store, ids.PROFILE_QUEUE)
row = q[0] if len(q) == 1 else {}
check(rc1 == 0 and rc2 == 0 and row.get("id") == "PC1" and row.get("occurrences") == 2
      and set(row.get("sessions") or []) == {SID, SID2},
      f"C4 ein PC-Kandidat, occurrences 2, sessions beide ({q})")
check((row.get("origin") or {}).get("projekt") == ids.project_key(mem_a, ISO_HOME),
      "C4 origin bleibt das Projekt der Erstnennung")

# C5 Supersede im Zentralmodus ersetzt die Zeile in profile.md, Changelog davor; D9 greift gegen profile-changelog.json
mem, store = make_project(), make_profile()
pb = snapshot(mem)
sup = {"match": "- Antworten kurz halten", "new_text": "- Antworten kurz halten, bei der Fehlersuche ausfuehrlich",
       "reason": "Owner hat die Regel eingeschraenkt", "plain": PLAIN, "quote": SUP_QUOTE}
rc, out = run(mem, {"date": DATE, "user_md_supersede": [sup]}, "--identity-store", store)
md = read_rel(store, ids.PROFILE_MD)
fw = out.get("files_written") or []
check(rc == 0 and md == PROFILE_TEXT.replace("- Antworten kurz halten\n", sup["new_text"] + "\n")
      and fw.index(ids.PROFILE_CHANGELOG) < fw.index(ids.PROFILE_MD) and same_identity(pb, snapshot(mem)),
      f"C5 profile.md: genau die Zielzeile ersetzt, Changelog davor, Projekt unberuehrt (rc={rc}, {out.get('error')}, {fw})")
log = load_rel(store, ids.PROFILE_CHANGELOG)
check(len(log) == 1 and log[0].get("field") == "user.md/supersede" and log[0].get("old_value") == "- Antworten kurz halten"
      and (log[0].get("origin") or {}).get("projekt") == ids.project_key(mem, ISO_HOME),
      f"C5 Changelog-Eintrag mit old_value und origin ({log})")
put(store, ids.PROFILE_QUEUE, [qrow("kurz-wieder", observation="Antworten kurz halten")])
md_before = read_rel(store, ids.PROFILE_MD)
rc, out = run(mem, {"date": DATE, "user_candidates": [cand("kurz-wieder", observation="Antworten kurz halten")]},
              "--identity-store", store, sid=SID2)
row = load_rel(store, ids.PROFILE_QUEUE)[0]
check(rc == 0 and row.get("status") == "confirmed" and row.get("blocked_by_supersede")
      and read_rel(store, ids.PROFILE_MD) == md_before,
      f"C5 D9: die widerrufene Aussage kommt per Zweitnennung nicht zurueck (status={row.get('status')}, "
      f"blocked={row.get('blocked_by_supersede')})")

# C6 Plan-Ablehnung: rc 2, Projekt und Zentral byte-gleich, profile.lock frei
mem, store = make_project(), make_profile()
pb, cb = snapshot(mem), snapshot(store)
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confidence="hoch")]}, "--identity-store", store)
try:
    with store_lock.store_lock(store, timeout=0.3, lock_rel=ids.PROFILE_LOCK):
        free = True
except store_lock.LockTimeout:
    free = False
check(rc == 2 and snapshot(mem) == pb and snapshot(store) == cb and free,
      f"C6 Ablehnung: rc 2, nichts geschrieben, profile.lock frei (rc={rc}, {str(out.get('error'))[:80]})")

# C7 Zentral unlesbar: rc 2 vor dem ersten Byte, kein Rueckfall auf die Projekt-user.md (Review Focus 1)
# Der Plan schreibt VOR den Kandidaten eine Iteration: ohne Vorabpruefung waere sie geschrieben, bevor die Queue
# gelesen wird (halb angewandter Plan) - so beweist der Test die Pruefung vor dem ERSTEN Byte
C7_PLAN = {"date": DATE, "iterations": [{"title": "darf nicht geschrieben werden", "type": "feature"}],
           "user_candidates": [cand(confirmed=True, quote=QUOTE)]}
mem, store = make_project(), make_profile()
write(os.path.join(store, ids.PROFILE_QUEUE), "{kaputt")
pb, cb = snapshot(mem), snapshot(store)
rc, out = run(mem, C7_PLAN, "--identity-store", store)
check(rc == 2 and snapshot(mem) == pb and snapshot(store) == cb and "profile-candidates.json" in str(out.get("error")),
      f"C7 kaputte Zentral-Queue: rc 2, nichts geschrieben ({str(out.get('error'))[:90]})")
mem, store = make_project(), make_profile()
with open(os.path.join(store, ids.PROFILE_MD), "wb") as fh:
    fh.write(b"# Profil\n\n## Preferences\n- kaputt \xff\xfe\n")
pb, cb = snapshot(mem), snapshot(store)
rc, out = run(mem, C7_PLAN, "--identity-store", store)
check(rc == 2 and snapshot(mem) == pb and snapshot(store) == cb and "profile.md" in str(out.get("error")),
      f"C7 profile.md kein UTF-8: rc 2, nichts geschrieben, auch nicht in user.md ({str(out.get('error'))[:90]})")

# C8 Supersede-Ziel steht nur noch in der Projekt-user.md: uebersprungen mit Warnung, Projektdatei byte-gleich
mem, store = make_project(), make_profile()
pb, cb = snapshot(mem), snapshot(store)
sup8 = {"match": "- Projektzeile bleibt hier (UC3, 2026-07-01)", "new_text": "- Projektzeile neu",
        "reason": "r", "plain": PLAIN, "quote": SUP_QUOTE}
rc, out = run(mem, {"date": DATE, "user_md_supersede": [sup8]}, "--identity-store", store)
warn = " ".join(out.get("warnings") or out["tally"]["warnings"] or [])
check(rc == 0 and "not in profile.md" in warn and snapshot(mem) == pb and snapshot(store) == cb,
      f"C8 Zeile nur im Projekt: uebersprungen, Warnung 'not in profile.md', nichts geschrieben (rc={rc}, "
      f"{str(out.get('error'))[:60]}, {warn[:120]})")

# C8b (T-050): dieselbe Lage per line_id einer migrierten Zeile - die Warnung nennt die neue PC-Id aus der Zentral-Queue
mem = make_project()
store = make_profile(queue=[{"id": "PC7", "key": "projektzeile", "origin": {"projekt": ids.project_key(mem, ISO_HOME),
                                                                             "id": "UC3"}},
                            {"id": "PC8", "key": "fremd", "origin": {"projekt": "anderes-projekt", "id": "UC3"}}])
pb, cb = snapshot(mem), snapshot(store)
sup8b = dict(sup8, line_id="UC3")
del sup8b["match"]
rc, out = run(mem, {"date": DATE, "user_md_supersede": [sup8b]}, "--identity-store", store)
warn = " ".join(out.get("warnings") or out["tally"]["warnings"] or [])
check(rc == 0 and "not in profile.md" in warn and "line_id PC7" in warn and "PC8" not in warn
      and snapshot(mem) == pb and snapshot(store) == cb,
      f"C8b migrierte UC-Id: uebersprungen, Warnung nennt PC7 (nicht die Zeile eines anderen Projekts), nichts geschrieben "
      f"(rc={rc}, {str(out.get('error'))[:60]}, {warn[:160]})")
rc, out = run(make_project(), {"date": DATE, "user_md_supersede": [sup8b]}, "--identity-store", make_profile())
warn = " ".join(out.get("warnings") or out["tally"]["warnings"] or [])
check(rc == 0 and "not in profile.md" in warn and "line_id PC" not in warn,
      f"C8b ohne Herkunft in der Queue: Warnung wie bisher, ohne erfundene Id ({warn[:120]})")

# C8b2 (Codex T-050-01): two central rows with the SAME origin are ambiguous - no id is recommended
mem = make_project()
origin = {"projekt": ids.project_key(mem, ISO_HOME), "id": "UC3"}
store = make_profile(queue=[{"id": "PC7", "key": "a", "origin": origin}, {"id": "PC8", "key": "b", "origin": origin}])
rc, out = run(mem, {"date": DATE, "user_md_supersede": [sup8b]}, "--identity-store", store)
warn = " ".join(out.get("warnings") or out["tally"]["warnings"] or [])
check(rc == 0 and "not in profile.md" in warn and "line_id PC" not in warn,
      f"C8b2 mehrdeutige Herkunft (PC7 und PC8): keine Id empfohlen ({warn[:160]})")

# C8c (T-050) `identity_store.py check`: rein lesender Gesundheitsblick auf die Zentraldateien (maintain Step 2, memory-audit)
CHECK = os.path.join(SCRIPTS, "identity_store.py")


def check_run(store):
    proc = subprocess.run([sys.executable, CHECK, "check", "--identity-store", store], capture_output=True, text=True,
                          encoding="utf-8", env=ENV)
    try:
        return proc.returncode, json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.returncode, {"_stdout": proc.stdout, "_stderr": proc.stderr}


inactive_store = make_dir("central-inaktiv-")
rc, rep = check_run(inactive_store)
check(rc == 0 and rep.get("active") is False and rep.get("problems") == [], f"C8c kein profile.md: inaktiv, nichts gemeldet ({rep})")
store = make_profile()
rc, out = run(make_project(), {"date": DATE, "user_candidates": [cand("check")]}, "--identity-store", store)
before = snapshot(store)
rc, rep = check_run(store)
check(rc == 0 and rep.get("active") is True and rep.get("problems") == [] and snapshot(store) == before,
      f"C8c sauberes Zentral nach einem Lauf: keine Befunde, nichts geschrieben ({rep.get('problems')})")
with open(os.path.join(store, ids.PROFILE_BRIEFING), "a", encoding="utf-8") as fh:
    fh.write("- untergeschoben\n")
rc, rep = check_run(store)
check(any("profile-briefing.txt" in p and "projection" in p for p in rep.get("problems", [])),
      f"C8c veraltete Projektion wird gemeldet ({rep.get('problems')})")
write(os.path.join(store, ids.PROFILE_QUEUE), "{kaputt")
os.remove(os.path.join(store, ids.PROFILE_BRIEFING))
rc, rep = check_run(store)
probs = " | ".join(rep.get("problems", []))
check(rc == 0 and "profile-candidates.json: not valid JSON" in probs and "profile-briefing.txt: missing" in probs,
      f"C8c kaputte Queue und fehlende Projektion werden gemeldet, Exit 0 ({probs})")
with open(os.path.join(store, ids.PROFILE_MD), "wb") as fh:
    fh.write(b"# Profil\n- kaputt \xff\n")
rc, rep = check_run(store)
check(any("profile.md: unreadable" in p for p in rep.get("problems", [])), f"C8c profile.md kein UTF-8 ({rep.get('problems')})")

# C9 zwei Projekte nacheinander: beide Kandidaten in der Zentral-Queue, PC1 und PC2 (Lost-Update-Probe ohne Last)
store = make_profile()
mem_a, mem_b = make_project(), make_project()
run(mem_a, {"date": DATE, "user_candidates": [cand("eins")]}, "--identity-store", store, sid=SID)
run(mem_b, {"date": DATE, "user_candidates": [cand("zwei", observation="Mag ausfuehrliche Fehlermeldungen")]},
    "--identity-store", store, sid=SID2)
q = load_rel(store, ids.PROFILE_QUEUE)
check([r.get("id") for r in q] == ["PC1", "PC2"] and [r.get("key") for r in q] == ["eins", "zwei"]
      and q[0]["origin"]["projekt"] != q[1]["origin"]["projekt"],
      f"C9 beide Kandidaten im Zentral, PC1/PC2, verschiedene origins ({[(r.get('id'), r.get('key')) for r in q]})")

# C10 Guard (E10): Temp-Store, Zentral nur ueber HOME aktiv, weder --identity-store noch Variable -> rc 2, nichts
home_store = os.path.join(ISO_HOME, ".agent-memory")
write(os.path.join(home_store, ids.PROFILE_MD), PROFILE_TEXT)
try:
    mem = make_project()
    pb, hb = snapshot(mem), snapshot(home_store)
    rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]},
                  env_extra={ids.ENV_VAR: ""})
    check(rc == 2 and "--identity-store" in str(out.get("error")) and snapshot(mem) == pb and snapshot(home_store) == hb,
          f"C10 Guard: rc 2 mit Hinweis auf --identity-store, nichts geschrieben (rc={rc}, {str(out.get('error'))[:90]})")
finally:
    shutil.rmtree(home_store, ignore_errors=True)

# C11 ein Plan nur mit iterations nimmt kein Zentral-Lock
mem, store = make_project(), make_profile()
rc, out = run(mem, {"date": DATE, "iterations": [{"title": "nur eine Iteration", "type": "feature"}]},
              "--identity-store", store)
check(rc == 0 and not os.path.exists(os.path.join(store, ids.PROFILE_LOCK)),
      f"C11 iterations-Plan: kein profile.lock im Zentral (rc={rc}, {out.get('error')})")

# C12 (E8, aus Task 2 L3) Halter auf profile.lock: apply_wrapup mit Identitaets-Plan endet mit rc 2, nichts geschrieben
mem, store = make_project(), make_profile()
pb, cb = snapshot(mem), snapshot(store)
holder = subprocess.Popen([sys.executable, "-c", HOLD, SCRIPTS, store, ids.PROFILE_LOCK], stdout=subprocess.PIPE,
                          text=True, encoding="utf-8", env=ENV)
try:
    holder.stdout.readline()
    rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]},
                  "--identity-store", store, "--lock-timeout", "0.5")
finally:
    holder.kill()
    holder.wait()
check(rc == 2 and "profile.lock" in str(out.get("error")) and snapshot(mem) == pb and snapshot(store) == cb,
      f"C12 profile.lock belegt: rc 2, Meldung nennt profile.lock, nichts geschrieben ({str(out.get('error'))[:90]})")

# ============================================================================= B: Briefing aus dem Profil
print("--- B: Briefing aus dem Profil")
import identity_briefing as ib  # noqa: E402

B_MD = ("# Profil\n\n*Initialized: 2026-07-01*\n\n## Preferences\n"
        "- Zeile eins (PC1, 2026-07-01)\n"
        "- (Will be populated through session observations)\n"
        "- Zeile fuenf (PC2, 2026-07-02)\n\n"
        "## Work Style\n- Zeile drei (PC3, 2026-07-03)\n")
B_QUEUE = [qrow("k1", id="PC1", occurrences=1, status="promoted"),
           qrow("k2", id="PC2", occurrences=5, status="promoted"),
           qrow("k3", id="PC3", occurrences=3, status="promoted")]

# B1 Rangfolge nach occurrences, Platzhalter faellt weg, hoechstens 6 Zeilen, Text ohne Zitierung
got = ib.select_lines(B_MD, B_QUEUE)
check(got == ["- Zeile fuenf", "- Zeile drei", "- Zeile eins"],
      f"B1 occurrences 5, 3, 1 in dieser Reihenfolge, ohne Platzhalter und Zitierung ({got})")
many = "# P\n\n## Preferences\n" + "".join(f"- Zeile {i}\n" for i in range(10))
check(len(ib.select_lines(many, [])) == ib.MAX_LINES == 6, "B1 hoechstens 6 Zeilen")

# B2 zitat-bestaetigt vor allem anderen
q2 = [qrow("k1", id="PC1", occurrences=1, status="promoted", confirmed_by="quote"),
      qrow("k2", id="PC2", occurrences=4, status="promoted", confirmed_by="repeat")]
md2 = "# P\n\n## Preferences\n- Viermal gesehen (PC2, 2026-07-02)\n- Mit Zitat (PC1, 2026-07-01)\n"
got = ib.select_lines(md2, q2)
check(got[:2] == ["- Mit Zitat", "- Viermal gesehen"], f"B2 Zitat (occurrences 1) vor Wiederholung (occurrences 4) ({got})")
q3 = [qrow("k1", id="PC1", occurrences=2, last_seen="2026-09-01"), qrow("k2", id="PC2", occurrences=2, last_seen="2026-10-01")]
md3 = "# P\n\n## Preferences\n- Aelter (PC1, 2026-07-01)\n- Neuer (PC2, 2026-07-02)\n- Ohne Kandidat\n"
got = ib.select_lines(md3, q3)
check(got == ["- Neuer", "- Aelter", "- Ohne Kandidat"],
      f"B2 Gleichstand: last_seen absteigend, Zeile ohne Kandidat zaehlt 1 ({got})")

# B4 literales \n, Tab, 600 Zeichen: eine Zeile, ohne Backslash und Steuerzeichen, gekuerzt mit … (Review Focus 4)
long_line = "- a\\nb\tc " + "x" * 600
got = ib.select_lines("# P\n\n## Preferences\n" + long_line + "\n", [])
line = got[0] if got else ""
check(len(got) == 1 and "\\" not in line and not any(ord(ch) < 32 or ord(ch) == 127 for ch in line)
      and len(line) <= ib.MAX_CHARS_LINE and line.endswith("…"),
      f"B4 eine Zeile, kein Backslash, keine Steuerzeichen, <= {ib.MAX_CHARS_LINE} Zeichen, endet auf … ({line[:40]!r}, {len(line)})")
wide = "# P\n\n## Preferences\n" + "".join(f"- {i} " + "y" * 300 + "\n" for i in range(6))
check(sum(len(x) for x in ib.select_lines(wide, [])) <= ib.MAX_CHARS_TOTAL, "B4 Gesamtlaenge hoechstens 900 Zeichen")

# B3 nach einer Promotion im Zentralmodus: briefing-lines.txt im Zentral, in files_written nach profile.md
mem, store = make_project(), make_profile()
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, "--identity-store", store)
fw = out.get("files_written") or []
bl = os.path.join(store, *ids.PROFILE_BRIEFING.split("/"))
lines = open(bl, encoding="utf-8").read().splitlines() if os.path.isfile(bl) else []
check(rc == 0 and ids.PROFILE_BRIEFING in fw and fw.index(ids.PROFILE_MD) < fw.index(ids.PROFILE_BRIEFING)
      and lines and lines[0] == "- Antworten beginnen mit einer kurzen Zusammenfassung",
      f"B3 Zentral: briefing-lines.txt nach profile.md geschrieben, Zitat-Zeile vorn ({fw}, {lines[:2]})")
check(not os.path.exists(os.path.join(mem, "identity", "briefing-lines.txt")),
      "B3 im Zentralmodus entsteht keine Projektion im Projekt")

# B5 Projektmodus: die Projektion entsteht im Projekt (der Hook liest sie, solange kein Zentral aktiv ist)
mem = make_project()
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, "--identity-store",
              os.path.join(MEM_ROOT, "kein-zentral"))
pl = os.path.join(mem, "identity", "briefing-lines.txt")
plines = open(pl, encoding="utf-8").read().splitlines() if os.path.isfile(pl) else []
check(rc == 0 and "identity/briefing-lines.txt" in (out.get("files_written") or [])
      and "- Antworten beginnen mit einer kurzen Zusammenfassung" in plines and "- Projektzeile bleibt hier" in plines,
      f"B5 Projektmodus: identity/briefing-lines.txt im Projekt ({plines})")
# ein Plan ohne Identitaetsschreibvorgang laesst die Projektion in Ruhe
mem = make_project()
rc, out = run(mem, {"date": DATE, "iterations": [{"title": "nur eine Iteration", "type": "feature"}]})
check(rc == 0 and "identity/briefing-lines.txt" not in (out.get("files_written") or []),
      "B5 iterations-Plan schreibt keine Projektion")

# ============================================================================= F: Sofort-Fixes DCO #10148
print("--- F: soul-Kandidaten (DCO #10148)")

# echtes Format der 6 betroffenen Stores: Leerstub direkt ueber echten Kandidaten
SOUL_STUB = ("# Soul Candidates\n\n*Keine offenen Kandidaten.*\n\n"
             "## Kandidat: Beispielvorschlag A (2026-07-20)\n"
             "**Vorschlag:** Beispieltext eins.\n"
             "**Evidence:** UC7 (2026-07-18)\n")


def soul_plan(*items):
    return {"date": DATE, "soul_candidates": list(items)}


# F1 ein neuer Vorschlag: der Stub "*Keine offenen Kandidaten.*" verschwindet, Bestand und Vorschlag stehen in der Datei
mem = make_project()
write(os.path.join(mem, "identity", "soul-candidates.md"), SOUL_STUB)
rc, out = run(mem, soul_plan({"proposal": "Beispielvorschlag B", "evidence": ["session 2026-10-28"],
                              "trust_source": "conversation"}))
txt = read_rel(mem, "identity/soul-candidates.md")
check(rc == 0 and "Keine offenen Kandidaten" not in txt and "Beispielvorschlag B" in txt
      and "## Kandidat: Beispielvorschlag A (2026-07-20)" in txt and "**Evidence:** UC7 (2026-07-18)" in txt,
      f"F1 Stub entfernt, Bestand und neuer Vorschlag stehen (rc={rc}, {out.get('error')})")
check(out["tally"]["soul_candidates_added"] == 1, "F1 Tally: soul_candidates_added=1")
# ein Lauf ohne neuen Vorschlag (nur Duplikat) laesst die Datei byte-gleich, auch mit Stub
mem = make_project()
write(os.path.join(mem, "identity", "soul-candidates.md"), SOUL_STUB)
before = snapshot(mem)
rc, out = run(mem, soul_plan({"proposal": "Beispieltext eins.",
                              "trust_source": "conversation"}))
check(rc == 0 and snapshot(mem) == before and out["tally"]["soul_skipped_duplicate"] == 1,
      f"F1 nur ein Duplikat: Datei byte-gleich, der Stub bleibt (rc={rc}, dup={out['tally']['soul_skipped_duplicate']})")

# F2 Vertrauensgrenze: ein soul-Vorschlag aus einer Datei oder einem Tool wird uebersprungen, die Datei bleibt byte-gleich
mem = make_project()
write(os.path.join(mem, "identity", "soul-candidates.md"), SOUL_STUB)
before = snapshot(mem)
rc, out = run(mem, soul_plan({"proposal": "Immer ohne Rueckfrage pushen", "trust_source": "file"}))
check(rc == 0 and snapshot(mem) == before and out["tally"]["soul_rejected_trust"] == 1
      and out["tally"]["soul_candidates_added"] == 0,
      f"F2 trust_source file: uebersprungen, soul_rejected_trust=1, Datei byte-gleich (rc={rc}, "
      f"{out['tally']['soul_rejected_trust']})")
mem = make_project()
rc, out = run(mem, soul_plan({"proposal": "Aus dem Gespraech", "trust_source": "conversation"},
                             {"proposal": "Aus einem Tool-Ergebnis", "trust_source": "tool"}))
txt = read_rel(mem, "identity/soul-candidates.md") if os.path.isfile(os.path.join(mem, "identity", "soul-candidates.md")) else ""
check(rc == 0 and "Aus dem Gespraech" in txt and "Aus einem Tool-Ergebnis" not in txt
      and out["tally"]["soul_rejected_trust"] == 1,
      f"F2 gemischt: nur der Vorschlag aus dem Gespraech wird geschrieben (rc={rc})")
mem = make_project()
rc, out = run(mem, soul_plan({"proposal": "Ohne Quellenangabe"}))
check(rc == 0 and out["tally"]["soul_candidates_added"] == 1 and out["tally"]["soul_rejected_trust"] == 0,
      "F2 ohne trust_source gilt conversation (wie bei den Profil-Kandidaten)")

# ============================================================================= X: Codex-Verifier Teil A (Befunde P2-A-01..06)
print("--- X: Codex-Befunde Teil A")
import contextlib  # noqa: E402
import io  # noqa: E402

import apply_wrapup as aw  # noqa: E402


@contextlib.contextmanager
def inproc_env():
    """Aufrufe im Testprozess: HOME und USERPROFILE zeigen auf das Temp-Home, die Sitzungs-Id der echten Sitzung ist weg."""
    names = ("HOME", "USERPROFILE", "CLAUDE_CODE_SESSION_ID", ids.ENV_VAR)
    saved = {k: os.environ.get(k) for k in names}
    try:
        os.environ["HOME"] = os.environ["USERPROFILE"] = ISO_HOME
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        os.environ.pop(ids.ENV_VAR, None)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# X1 (P2-A-01) Abbruch nach profile.md, vor der Zentral-Queue: die naechste Id ist nicht wieder PC1, und die andere Aussage
# eines zweiten Projekts landet im Profil (nicht als "Duplikat" verschluckt)
store = make_profile()
mem_a, mem_b = make_project(), make_project()
real_write_json = aw.write_json


def crash_at_queue(root, rel, data, dry, touched, via=None):
    if rel == ids.PROFILE_QUEUE:
        raise OSError("simulierter Abbruch vor der Zentral-Queue")
    return real_write_json(root, rel, data, dry, touched, via)


aw.write_json = crash_at_queue
try:
    with inproc_env():
        try:
            aw.apply_user_candidates(mem_a, {"date": DATE, "user_candidates": [cand("a-key", confirmed=True, quote=QUOTE)]},
                                     DATE, False, [], aw.new_tally(), SID, None, paths=ids.central_paths(store))
            crashed = False
        except OSError:
            crashed = True
finally:
    aw.write_json = real_write_json
check(crashed and f"(PC1, {DATE})" in read_rel(store, ids.PROFILE_MD) and load_rel(store, ids.PROFILE_QUEUE) == [],
      "X1 Vorbedingung: profile.md traegt PC1, die Zentral-Queue ist nach dem Abbruch leer")
OTHER = "Mag ausfuehrliche Fehlermeldungen mit Pfad"
rc, out = run(mem_b, {"date": DATE, "user_candidates": [cand("b-key", observation=OTHER, confirmed=True, quote=QUOTE)]},
              "--identity-store", store)
q = load_rel(store, ids.PROFILE_QUEUE)
row = next((r for r in q if r.get("key") == "b-key"), {})
check(rc == 0 and row.get("id") == "PC2" and f"- {OTHER} (PC2, {DATE})" in read_rel(store, ids.PROFILE_MD)
      and out["tally"]["promotion_skipped_duplicate"] in (0, None),
      f"X1 die naechste Id ueberspringt die im Changelog belegte PC1, die andere Aussage steht im Profil "
      f"(rc={rc}, id={row.get('id')}, dup={out['tally']['promotion_skipped_duplicate']})")

# X2 (P2-A-02) corrections mit falschem Typ im Zentralmodus: JSON-Ablehnung mit Exit 2, kein Absturz
mem, store = make_project(), make_profile()
pb, cb = snapshot(mem), snapshot(store)
rc, out = run(mem, {"date": DATE, "corrections": ["ungueltig"]}, "--identity-store", store)
check(rc == 2 and out.get("ok") is False and snapshot(mem) == pb and snapshot(store) == cb,
      f"X2 corrections als Liste im Zentralmodus: rc 2 mit JSON-Fehler, nichts geschrieben (rc={rc}, "
      f"{str(out.get('error') or out.get('_stderr'))[-120:]})")

# X3 (P2-A-03) ein Steuerzeichen, das splitlines als Zeilenende liest, teilt die Profilzeile nicht
got = ib.select_lines("# P\n\n## Preferences\n- Links\x0bRechts\n- Zweite Haelfte\n", [])
check(got == ["- Links Rechts", "- Zweite Haelfte"], f"X3 VT und LS werden zu Leerzeichen in derselben Zeile ({got})")

# X4 (P2-A-04) eine Zitierung vor einer weiteren Klammernotiz faellt weg, die Notiz bleibt
got = ib.select_lines("# P\n\n## Preferences\n- Nutzt Python (PC1, 2026-10-28) (seit Juli)\n", [])
check(got == ["- Nutzt Python (seit Juli)"], f"X4 Zitierung vor einer Klammernotiz entfernt ({got})")

# X5 (P2-A-05) der Stub ohne abschliessenden Zeilenumbruch verschwindet ebenfalls
mem = make_project()
write(os.path.join(mem, "identity", "soul-candidates.md"), "# Soul Candidates\n\n*Keine offenen Kandidaten.*")
rc, out = run(mem, {"date": DATE, "soul_candidates": [{"proposal": "Neuer Vorschlag", "trust_source": "conversation"}]})
txt = read_rel(mem, "identity/soul-candidates.md")
check(rc == 0 and "Keine offenen Kandidaten" not in txt and "Neuer Vorschlag" in txt,
      f"X5 Stub am Dateiende ohne Zeilenumbruch: entfernt ({txt[:80]!r})")

# X6 (P2-A-06) das Zentral-Lock ist beim Lesen UND beim Schreiben der Zentral-Queue gehalten (nicht nur beim Eintritt)
PROBE = ("import sys; sys.path.insert(0, sys.argv[1]); import store_lock\n"
         "try:\n"
         "    with store_lock.store_lock(sys.argv[2], timeout=0.3, lock_rel=sys.argv[3]):\n"
         "        sys.exit(0)\n"
         "except store_lock.LockTimeout:\n"
         "    sys.exit(3)\n")
seen = []


def lock_busy(store_):
    r = subprocess.run([sys.executable, "-c", PROBE, SCRIPTS, store_, ids.PROFILE_LOCK], env=ENV)
    return r.returncode == 3


store = make_profile()
mem = make_project()
real_load_json = aw.load_json


def probing_load(root, rel, default, via=None):
    if rel == ids.PROFILE_QUEUE and ids.same_store(root, store):
        seen.append(("lesen", lock_busy(store)))
    return real_load_json(root, rel, default, via)


def probing_write(root, rel, data, dry, touched, via=None):
    if rel == ids.PROFILE_QUEUE and ids.same_store(root, store):
        seen.append(("schreiben", lock_busy(store)))
    return real_write_json(root, rel, data, dry, touched, via)


real_read_queue = ib._read_queue


def probing_read_queue(path):
    # Codex P2-A-R2-02: the briefing projection reads the central queue with its own reader
    if ids.same_store(os.path.dirname(os.path.dirname(path)), store):
        seen.append(("projektion", lock_busy(store)))
    return real_read_queue(path)


aw.load_json, aw.write_json, ib._read_queue = probing_load, probing_write, probing_read_queue
argv, stdin = sys.argv, sys.stdin
try:
    with inproc_env(), contextlib.redirect_stdout(io.StringIO()):
        sys.argv = ["apply_wrapup.py", mem, "--session-id", SID, "--identity-store", store]
        sys.stdin = io.StringIO(json.dumps({"date": DATE, "user_candidates": [cand("x6", confirmed=True, quote=QUOTE)]}))
        rc6 = aw.main()
finally:
    aw.load_json, aw.write_json, ib._read_queue = real_load_json, real_write_json, real_read_queue
    sys.argv, sys.stdin = argv, stdin
phases = {p for p, busy in seen if busy}
check(rc6 == 0 and {"lesen", "schreiben", "projektion"} <= phases and all(busy for _p, busy in seen),
      f"X6 profile.lock ist bei jedem Lesen und Schreiben der Zentral-Queue belegt (rc={rc6}, {seen})")

# X7 (P2-A-R2-01) eine Sachklammer, die wie eine Id aussieht ("Python3"), bleibt; die LETZTE Zitierung faellt weg und
# bestimmt den Rang
q7 = [qrow("k1", id="PC1", occurrences=5, status="promoted"), qrow("k2", id="PC2", occurrences=2, status="promoted")]
md7 = ("# P\n\n## Preferences\n- Zweite Zeile (PC2, 2026-10-28)\n"
       "- Nutzt Python (Python3, lokal) (PC1, 2026-10-28)\n")
got = ib.select_lines(md7, q7)
check(got == ["- Nutzt Python (Python3, lokal)", "- Zweite Zeile"],
      f"X7 Sachklammer bleibt, PC1 (occurrences 5) bestimmt den Rang ({got})")

# ============================================================================= R: Befunde der Gesamtpruefung
print("--- R: Gesamtpruefung (Projektion, Encoding, Temp-Sperre)")

# R1 die Zentral-Projektion hat einen eigenen Namen: im Home-Store (= Zentral) kollidiert sie nie mit der Projekt-Projektion
check(ids.PROFILE_BRIEFING == "identity/profile-briefing.txt" and ids.central_paths("x").briefing == ids.PROFILE_BRIEFING
      and ids.legacy_paths("x").briefing == ids.BRIEFING_LINES,
      "R1 Projektion: Zentral identity/profile-briefing.txt, Projekt identity/briefing-lines.txt")
store = make_profile()
write(os.path.join(store, "identity", "briefing-lines.txt"), "- alte Projekt-Projektion des Home-Projekts\n")
mem = make_project()
rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, "--identity-store", store)
check(rc == 0 and ids.PROFILE_BRIEFING in (out.get("files_written") or [])
      and read_rel(store, "identity/briefing-lines.txt") == "- alte Projekt-Projektion des Home-Projekts\n"
      and read_rel(store, ids.PROFILE_BRIEFING).startswith("- Antworten beginnen"),
      f"R1 Zentralmodus schreibt profile-briefing.txt, die Projekt-Projektion im selben Ordner bleibt (rc={rc}, "
      f"{out.get('files_written')})")

# R3 der Encoding-Vorschlag liest im Zentralmodus profile.md, und der Vorschlag laesst sich anwenden
MOJI = "- GrÃ¼ÃŸe an alle (PC7, 2026-10-28)"
store = make_profile(text=PROFILE_TEXT + MOJI + "\n")
mem = make_project()
proc = subprocess.run([sys.executable, SCRIPT, mem, "--propose-encoding-corrections", "--identity-store", store],
                      capture_output=True, text=True, encoding="utf-8", env=ENV)
prop = json.loads(proc.stdout) if proc.returncode == 0 and proc.stdout.strip() else {}
lines = (prop.get("corrections") or {}).get("user_md") or []
check(len(lines) == 1 and lines[0]["old"] == MOJI and lines[0]["new"] == "- Grüße an alle (PC7, 2026-10-28)",
      f"R3 Vorschlag findet die Mojibake-Zeile in profile.md ({lines})")
rc, out = run(mem, {"date": DATE, "corrections": prop.get("corrections", {})}, "--identity-store", store)
check(rc == 0 and "- Grüße an alle (PC7, 2026-10-28)" in read_rel(store, ids.PROFILE_MD),
      f"R3 der Vorschlag laesst sich im Zentralmodus anwenden (rc={rc}, {str(out.get('error'))[:100]})")

# R4 die Temp-Sperre gilt nur fuer Plaene, die das Profil schreiben
home_store = os.path.join(ISO_HOME, ".agent-memory")
write(os.path.join(home_store, ids.PROFILE_MD), PROFILE_TEXT)
try:
    mem = make_project()
    rc, out = run(mem, {"date": DATE, "iterations": [{"title": "nur eine Iteration", "type": "feature"}]},
                  env_extra={ids.ENV_VAR: ""})
    check(rc == 0, f"R4 iterations-Plan in einem Temp-Store bei aktivem Home-Zentral: laeuft (rc={rc}, {str(out.get('error'))[:100]})")
    rc, out = run(mem, {"date": DATE, "user_candidates": [cand(confirmed=True, quote=QUOTE)]}, env_extra={ids.ENV_VAR: ""})
    check(rc == 2 and "--identity-store" in str(out.get("error")), f"R4 Identitaets-Plan dort weiter gesperrt (rc={rc})")
finally:
    shutil.rmtree(home_store, ignore_errors=True)

print(f"=== Results: {PASSED}/{TESTS} passed, {ERRORS} failures ===")
sys.exit(1 if ERRORS else 0)

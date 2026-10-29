#!/usr/bin/env python3
"""Tests fuer scripts/migrate_identity.py (P2 Teil B: Migration der Projekt-Profile ins Zentralprofil).

Spec: docs/superpowers/specs/2026-10-27-identitaet-verdichtung-messbar-design.md, Abschnitt P2 (Migration, E1-E11).
Plan: docs/superpowers/plans/2026-10-28-p2-zentrales-profil.md, Tasks 9-11.

Was hier bewiesen wird:
  M1-M6  `plan`: findet die Stores wie das Store-Manifest, liest jede Profilzeile verlustfrei (Klasse, Sektion, Rohtext,
         Zeilenende), vergibt globale Ids PCn mit origin, bildet Zitierungen (UCn, Datum) auf (PCm, Datum) ab, benennt
         Key-Kollisionen um, markiert Altbestand ohne Zitat (Q14: confirmed_by legacy), fuehrt NICHTS zusammen (Owner
         28.10.: 1:1), meldet aehnliche Paare nur, schreibt kein Byte und bricht bei einem nicht durchsuchbaren Ordner ab.

Alle Stores liegen in einem Fake-Home unter dem Temp-Ordner; Subprozesse laufen mit dem isolierten Home aus _isolated_home.
Echte Stores und das echte Zentral werden nie gelesen oder geschrieben.
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
SCRIPT = os.path.join(SCRIPTS, "migrate_identity.py")
sys.path.insert(0, SCRIPTS)

from _isolated_home import process_env  # noqa: E402

ENV, ISO_HOME = process_env(PYTHONIOENCODING="utf-8")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import migrate_identity as mi  # noqa: E402

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


RUN_ROOT = tempfile.mkdtemp(prefix="migrate-run-")
atexit.register(shutil.rmtree, RUN_ROOT, ignore_errors=True)


def write_bytes(path, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def put(path, data):
    write_bytes(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def snapshot(root):
    files = {}
    for base, _dirs, names in os.walk(root):
        for n in names:
            if n.endswith(".lock"):
                continue  # OS-Lock-Handle: Infrastruktur, keine Daten (und gesperrt nicht lesbar)
            p = os.path.join(base, n)
            with open(p, "rb") as fh:
                files[os.path.relpath(p, root).replace(os.sep, "/")] = fh.read()
    return files


def cand(cid, key, **kw):
    r = {"id": cid, "key": key, "observation": f"Beobachtung {key}", "status": "observed", "signal_type": "preference",
         "confidence": 0.5, "occurrences": 1, "evidence": ["session 2026-10-01"], "first_seen": "2026-10-01",
         "last_seen": "2026-10-01", "trust_source": "conversation"}
    r.update(kw)
    return r


# Projekt A: CRLF + BOM, Praeambel, Platzhalter, Fortsetzung, Sonder-Sektion, Zitierung (UC1, ...)
A_MD = ("﻿# User Profile\r\n\r\n*Initialized: 2026-07-01*\r\n\r\n## Preferences\r\n"
        "- Antworten kurz halten (UC1, 2026-07-01)\r\n"
        "  weil lange Antworten untergehen\r\n"
        "- (Will be populated through session observations)\r\n\r\n"
        "## Work Style\r\n- Arbeitet abends (UC2, 2026-07-02)\r\n\r\n"
        "## Eigene Sektion\r\n- Beispielzeile drei")
B_MD = ("# User Profile\n\n*Initialized: 2026-07-03*\n\n## Preferences\n"
        "- Will Quellen immer belegt haben (UC1, 2026-07-03)\n"
        "- Zitat ohne Kandidat (UC9, 2026-07-04)\n")
HOME_MD = "# User Profile\n\nEin eigener Satz vor der ersten Sektion.\n\n## Preferences\n- Home-Zeile\n"


def make_home():
    home = tempfile.mkdtemp(prefix="home-", dir=RUN_ROOT)
    a = os.path.join(home, "projA", ".agent-memory")
    b = os.path.join(home, "Desktop", "projB", ".agent-memory")
    h = os.path.join(home, ".agent-memory")
    write_bytes(os.path.join(a, "identity", "user.md"), A_MD.encode("utf-8"))
    put(os.path.join(a, "working", "user-candidates.json"), [
        cand("UC1", "kurz", status="promoted", observation="Antworten kurz halten"),
        cand("UC2", "abends", status="promoted", observation="Arbeitet abends", confirmed_by="quote"),
        cand("UC3", "offen")])
    put(os.path.join(a, "identity", "user-changelog.json"), [
        {"ts": "2026-07-01T10:00:00+02:00", "field": "user.md/Preferences", "old_value": None,
         "new_value": "Antworten kurz halten", "candidate_id": "UC1"}])
    write_bytes(os.path.join(b, "identity", "user.md"), B_MD.encode("utf-8"))
    put(os.path.join(b, "working", "user-candidates.json"), [
        cand("UC1", "kurz", status="confirmed", observation="Will Quellen immer belegt haben")])
    put(os.path.join(b, "identity", "user-changelog.json"), [])
    # verirrte Queue in identity/ (wie im DCO-Store): wird mit origin.datei uebernommen
    put(os.path.join(b, "identity", "user-candidates.json"), [cand("UC11", "verirrt", status="confirmed")])
    write_bytes(os.path.join(h, "identity", "user.md"), HOME_MD.encode("utf-8"))
    put(os.path.join(h, "working", "user-candidates.json"), [])
    # keine echten Stores: Kopien und uebersprungene Ordner
    write_bytes(os.path.join(home, "node_modules", "x", ".agent-memory", "identity", "user.md"), b"- nie\n")
    write_bytes(os.path.join(home, ".claude", "plugins", "cache", "p", ".agent-memory", "identity", "user.md"), b"- nie\n")
    return home


def run(*args):
    proc = subprocess.run([sys.executable, "-X", "utf8", SCRIPT, *args], capture_output=True, text=True,
                          encoding="utf-8", env=ENV)
    return proc.returncode, proc.stdout, proc.stderr


print("=== migrate_identity.py tests ===")
print("--- M: plan (nur lesend)")

# M1 verlustfreier Leser: Klassen, Sektion, Rohtext ohne BOM, Zeilenende je Zeile
rows = mi.parse_profile(A_MD, {"projekt": "projA", "datei": "identity/user.md"})
got = [(r["klasse"], r["sektion"], r["roh"], r["eol"]) for r in rows]
want = [("titel", None, "# User Profile", "\r\n"),
        ("praeambel", None, "*Initialized: 2026-07-01*", "\r\n"),
        ("sektion", "Preferences", "## Preferences", "\r\n"),
        ("bullet", "Preferences", "- Antworten kurz halten (UC1, 2026-07-01)", "\r\n"),
        ("fortsetzung", "Preferences", "  weil lange Antworten untergehen", "\r\n"),
        ("platzhalter", "Preferences", "- (Will be populated through session observations)", "\r\n"),
        ("sektion", "Work Style", "## Work Style", "\r\n"),
        ("bullet", "Work Style", "- Arbeitet abends (UC2, 2026-07-02)", "\r\n"),
        ("sektion", "Eigene Sektion", "## Eigene Sektion", "\r\n"),
        ("bullet", "Eigene Sektion", "- Beispielzeile drei", "")]
check(got == want, f"M1 Klassen, Sektionen, Rohtext und Zeilenenden exakt ({got[:3]} ...)")
check(rows[3]["origin"] == {"projekt": "projA", "datei": "identity/user.md", "zeile": 6}
      and rows[1]["vorlage"] is True and rows[3]["vorlage"] is False,
      f"M1 origin mit Zeilennummer, *Initialized* ist Vorlage ({rows[3]['origin']}, {rows[1]['vorlage']})")

home = make_home()
before = snapshot(home)
out = os.path.join(RUN_ROOT, "plan-1.json")
rc, so, se = run("plan", "--home", home, "--out", out)
plan = json.load(open(out, encoding="utf-8")) if rc == 0 and os.path.isfile(out) else {}
stores = [s["store"] for s in plan.get("stores", [])]
check(rc == 0 and stores == [".agent-memory", "Desktop/projB/.agent-memory", "projA/.agent-memory"],
      f"M1 Stores wie im Store-Manifest: sortiert, ohne node_modules und Plugin-Cache (rc={rc}, {stores}, {se[-200:]})")

# M2 UC1 in zwei Stores -> zwei PC-Ids mit verschiedenen origins; Zitierungen abgebildet; Key-Kollision umbenannt
ziel = plan.get("ziel", {})
kand = ziel.get("kandidaten", [])
by_origin = {(k["origin"]["projekt"], k["origin"]["id"], k["origin"].get("datei")): k for k in kand}
pa, pb = by_origin.get(("projA", "UC1", None), {}), by_origin.get(("Desktop/projB", "UC1", None), {})
check(pa.get("id", "").startswith("PC") and pb.get("id", "").startswith("PC") and pa["id"] != pb["id"],
      f"M2 UC1 aus zwei Stores: zwei PC-Ids ({pa.get('id')}, {pb.get('id')})")
ids = [k["id"] for k in kand]
check(ids == [f"PC{i}" for i in range(1, len(kand) + 1)] and len(kand) == 5,
      f"M2 PC-Ids lueckenlos in Store- und Dateireihenfolge, 5 Kandidaten ({ids})")
md = ziel.get("profile_md", "")
check(f"- Antworten kurz halten ({pa.get('id')}, 2026-07-01)" in md
      and f"- Will Quellen immer belegt haben ({pb.get('id')}, 2026-07-03)" in md,
      "M2 (UC1, Datum) wird je Store auf die eigene PC-Id abgebildet")
check("- Zitat ohne Kandidat (UC9, 2026-07-04)" in md
      and any(z.get("alt") == "UC9" and z.get("neu") is None for z in plan.get("zuordnung", [])),
      "M2 eine Zitierung ohne Kandidat bleibt woertlich und steht im Ledger (neu: null)")
# Store-Reihenfolge ist sortiert: "Desktop/projB" kommt vor "projA", also behaelt projB den Key
check(pb.get("key") == "kurz" and pa.get("key") == "kurz@projA"
      and any(c.get("neu_key") == "kurz@projA" for c in plan.get("kollisionen", [])),
      f"M2 Key-Kollision: der zweite Key wird zu <key>@<projekt> ({pb.get('key')}, {pa.get('key')})")
stray = by_origin.get(("Desktop/projB", "UC11", "identity/user-candidates.json"), {})
check(stray.get("key") == "verirrt" and any("identity/user-candidates.json" in h for h in plan.get("hinweise", [])),
      "M2 die verirrte identity/user-candidates.json wird mit origin.datei uebernommen und im Bericht genannt")
check("  weil lange Antworten untergehen\n" in md and md.index("- Antworten kurz halten") < md.index("  weil lange")
      and "- Beispielzeile drei" in md and "## Eigene Sektion" in md and "Ein eigener Satz vor der ersten Sektion." in md,
      "M2 Fortsetzung bleibt unter ihrem Bullet, Sonder-Sektion und eigene Praeambel werden uebernommen")
check("*Initialized" not in md and "Will be populated" not in md
      and sum(1 for z in ziel.get("ledger", {}).get("zeilen", []) if z.get("entscheidung") == "vorlage") == 3,
      "M2 Vorlage (*Initialized*, 2x) und Platzhalter (1x) landen nicht in profile.md, sondern im Ledger (vorlage)")
log = ziel.get("changelog", [])
src = [e for e in log if e.get("field") == "user.md/Preferences"]
check(len(src) == 1 and src[0].get("origin", {}).get("projekt") == "projA" and src[0].get("candidate_id") == "UC1",
      f"M2 Quell-Changelog-Eintrag mit origin uebernommen, sonst woertlich ({src})")

# M3 kein Zusammenfuehren (Owner 28.10.): aehnliche Paare werden nur gemeldet, beide Zeilen bleiben
P85 = "- eins zwei drei vier fuenf sechs"
Q85 = "- eins zwei drei vier fuenf sechs sieben"
P79 = "- a1 a2 a3 a4 a5 a6 a7 a8 a9 b10 b11 xx"
Q79 = "- a1 a2 a3 a4 a5 a6 a7 a8 a9 b10 b11 yy zz"
home3 = tempfile.mkdtemp(prefix="home-", dir=RUN_ROOT)
for proj, lines in (("p1", [P85, P79]), ("p2", [Q85, Q79])):
    write_bytes(os.path.join(home3, proj, ".agent-memory", "identity", "user.md"),
                ("# User Profile\n\n## Preferences\n" + "\n".join(lines) + "\n").encode("utf-8"))
out3 = os.path.join(RUN_ROOT, "plan-3.json")
rc, so, se = run("plan", "--home", home3, "--out", out3)
p3 = json.load(open(out3, encoding="utf-8")) if rc == 0 else {}
pairs = p3.get("aehnliche_paare", [])
md3 = p3.get("ziel", {}).get("profile_md", "")
check(rc == 0 and len(pairs) == 1 and pairs[0]["jaccard"] >= 0.8 and {pairs[0]["a"], pairs[0]["b"]} == {P85, Q85},
      f"M3 Jaccard 0,86 wird als aehnliches Paar gemeldet, 0,79 nicht ({pairs}, rc={rc}, {se[-150:]})")
check(all(x in md3 for x in (P85, Q85, P79, Q79)) and p3.get("zaehler", {}).get("zusammengefuehrt") == 0,
      "M3 keine Zeile wird zusammengefuehrt, alle vier stehen in profile.md")

# M4 Q14: promoted/confirmed ohne confirmed_by -> legacy; vorhandenes confirmed_by und offene bleiben
check(pa.get("confirmed_by") == "legacy" and pb.get("confirmed_by") == "legacy"
      and by_origin.get(("projA", "UC2", None), {}).get("confirmed_by") == "quote"
      and "confirmed_by" not in by_origin.get(("projA", "UC3", None), {"confirmed_by": 1}),
      "M4 Altbestand ohne Zitat wird als confirmed_by legacy markiert, quote und offene bleiben, wie sie sind")
check(pa.get("status") == "promoted" and pa.get("occurrences") == 1 and pa.get("evidence") == ["session 2026-10-01"]
      and "sessions" not in pa,
      "M4 Status, occurrences und evidence bleiben; keine kuenstliche Sitzung (A3: Adoptionsregel)")

# M5 plan schreibt kein Byte in die Stores
check(snapshot(home) == before, "M5 plan laesst das Fake-Home byte-gleich")
z = plan.get("zaehler", {})
check(z.get("stores") == 3 and z.get("kandidaten") == 5 and z.get("bullet") == 6 and z.get("fortsetzung") == 1
      and z.get("praeambel_eigen") == 1 and z.get("vorlage") == 2 and z.get("platzhalter") == 1
      and z.get("changelog") == 1,
      f"M5 Zaehler ({z})")

# M6 ein nicht durchsuchbarer Ordner bricht ab (ein Store darin waere unsichtbar)
home6 = make_home()
blocked = os.path.join(home6, "Desktop")
real_scandir = os.scandir


def failing_scandir(path="."):
    if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(blocked)):
        raise PermissionError(13, "Zugriff verweigert", path)
    return real_scandir(path)


out6 = os.path.join(RUN_ROOT, "plan-6.json")
os.scandir = failing_scandir
try:
    rc6 = mi.main(["plan", "--home", home6, "--out", out6])
finally:
    os.scandir = real_scandir
check(rc6 == 2 and not os.path.exists(out6), f"M6 nicht durchsuchbarer Ordner: Exit 2, kein Plan geschrieben (rc={rc6})")

print("--- M: backup und apply")
import identity_store as ids  # noqa: E402
import store_lock  # noqa: E402

TARGETS = (ids.PROFILE_CHANGELOG, ids.PROFILE_QUEUE, ids.MIGRATION_LEDGER, ids.PROFILE_BRIEFING, ids.PROFILE_MD)


def fresh(tag):
    """Fake-Home, Plan und Backup; das Zentral ist der Home-Store des Fake-Homes."""
    home_ = make_home()
    plan_ = os.path.join(RUN_ROOT, f"plan-{tag}.json")
    bak_ = os.path.join(RUN_ROOT, f"backup-{tag}")
    rc_p, _o, e_p = run("plan", "--home", home_, "--out", plan_)
    rc_b, _o, e_b = run("backup", "--plan", plan_, "--out", bak_)
    assert rc_p == 0 and rc_b == 0, (rc_p, e_p, rc_b, e_b)
    return home_, plan_, bak_, os.path.join(home_, ".agent-memory")


def apply_(plan_, bak_, central_, *extra, env_extra=None):
    run_env = dict(ENV)
    run_env.update(env_extra or {})
    proc = subprocess.run([sys.executable, "-X", "utf8", SCRIPT, "apply", "--plan", plan_, "--backup", bak_,
                           "--identity-store", central_, *extra], capture_output=True, text=True, encoding="utf-8",
                          env=run_env)
    return proc.returncode, proc.stdout, proc.stderr


def central_files(central_):
    return {t: open(os.path.join(central_, *t.split("/")), "rb").read()
            for t in TARGETS if os.path.isfile(os.path.join(central_, *t.split("/")))}


# backup: Bytekopie von identity/ und der Queue je Store, mit sha256-Liste; ausserhalb jedes Stores
home, plan_p, bak, central = fresh("b")
man = json.load(open(os.path.join(bak, "backup.json"), encoding="utf-8"))
a_md = os.path.join(bak, "kopie", "projA", "agent-memory.kopie", "identity", "user.md")
check(os.path.isfile(a_md) and open(a_md, "rb").read() == A_MD.encode("utf-8")
      and "projA/.agent-memory" in man.get("dateien", {}) and man.get("plan_sha256"),
      f"M7 backup: Bytekopie (Ordner agent-memory.kopie) samt sha256-Liste und Plan-Hash ({sorted(man)})")

# M7 (E1) --dry-run schreibt kein Byte, weder in Stores noch ins Zentral
before = snapshot(home)
rc, so, se = apply_(plan_p, bak, central, "--dry-run")
check(rc == 0 and snapshot(home) == before, f"M7 apply --dry-run: rc 0, Fake-Home byte-gleich (rc={rc}, {se[-150:]})")

# echter Lauf: Reihenfolge Changelog -> Queue -> Ledger -> Projektion -> profile.md, Quellen unveraendert
src_before = {k: v for k, v in snapshot(home).items() if not k.startswith(".agent-memory/identity/profile")
              and not k.startswith(".agent-memory/identity/briefing")}
rc, so, se = apply_(plan_p, bak, central)
plan = json.load(open(plan_p, encoding="utf-8"))
cf = central_files(central)
check(rc == 0 and set(cf) == set(TARGETS) and cf[ids.PROFILE_MD].decode("utf-8") == plan["ziel"]["profile_md"]
      and json.loads(cf[ids.PROFILE_QUEUE].decode("utf-8")) == plan["ziel"]["kandidaten"],
      f"M7 apply: alle fuenf Zieldateien, profile.md und Queue wie im Plan (rc={rc}, {sorted(cf)}, {se[-150:]})")
order = [ln.split(" ", 1)[1].strip() for ln in so.splitlines() if ln.startswith("geschrieben ")]
check(order == list(TARGETS), f"M7 Schreibreihenfolge, profile.md zuletzt ({order})")
after = snapshot(home)
check(all(after.get(k) == v for k, v in src_before.items()), "M7 alle Quelldateien byte-gleich (D-021)")
check(ids.central_active(central) and ids.select_paths(os.path.join(home, "projA", ".agent-memory"), central, {}).central,
      "M7 danach ist das Zentral aktiv")

# M8 (E6) ein zweiter apply erzeugt null Diff
snap1 = snapshot(home)
rc, so, se = apply_(plan_p, bak, central)
check(rc == 0 and snapshot(home) == snap1 and "bereits" in (so + se).lower(),
      f"M8 zweiter apply: No-op, null Diff (rc={rc}, {(so + se)[-120:]})")

# M9 (E7) Abbruch nach dem k-ten Schreibvorgang: der Rerun konvergiert zum selben Endzustand, ohne Duplikat
ref = central_files(central)
for k in range(0, len(TARGETS)):
    home_k, plan_k, bak_k, central_k = fresh(f"k{k}")
    rc1, so1, se1 = apply_(plan_k, bak_k, central_k, env_extra={"AGENTIC_OS_MIGRATE_FAIL_AFTER": str(k)})
    half = central_files(central_k)
    rc2, so2, se2 = apply_(plan_k, bak_k, central_k)
    done = central_files(central_k)
    plan_kk = json.load(open(plan_k, encoding="utf-8"))
    same = (done.get(ids.PROFILE_MD, b"").decode("utf-8") == plan_kk["ziel"]["profile_md"]
            and json.loads(done.get(ids.PROFILE_QUEUE, b"[]")) == plan_kk["ziel"]["kandidaten"]
            and json.loads(done.get(ids.PROFILE_CHANGELOG, b"[]")) == plan_kk["ziel"]["changelog"])
    check(rc1 == 2 and len(half) == k and ids.PROFILE_MD not in half and rc2 == 0 and set(done) == set(TARGETS) and same,
          f"M9 Abbruch nach {k} Schreibvorgaengen: Zentral inaktiv, Rerun konvergiert ohne Duplikat "
          f"(rc1={rc1}, halb={sorted(half)}, rc2={rc2}, {se2[-100:]})")

# M10 eine nach dem Backup geaenderte Quelle: Exit 2, nichts geschrieben
home, plan_p, bak, central = fresh("q")
with open(os.path.join(home, "projA", ".agent-memory", "identity", "user.md"), "ab") as fh:
    fh.write(b"\r\n- Nachtraeglich dazugekommen")
before = snapshot(home)
rc, so, se = apply_(plan_p, bak, central)
check(rc == 2 and snapshot(home) == before and "projA" in se,
      f"M10 geaenderte Quelle: Exit 2, nichts geschrieben, Meldung nennt den Store (rc={rc}, {se[-150:]})")

# M11 (E8) ein Halter auf profile.lock: Exit 2 nach dem Timeout, nichts geschrieben
home, plan_p, bak, central = fresh("l")
HOLD = ("import sys, time; sys.path.insert(0, sys.argv[1]); import store_lock\n"
        "with store_lock.store_lock(sys.argv[2], lock_rel=sys.argv[3]):\n"
        "    print('held', flush=True); time.sleep(60)\n")
holder = subprocess.Popen([sys.executable, "-c", HOLD, SCRIPTS, central, ids.PROFILE_LOCK], stdout=subprocess.PIPE,
                          text=True, encoding="utf-8", env=ENV)
try:
    holder.stdout.readline()
    before = {k: v for k, v in snapshot(home).items() if not k.endswith("profile.lock")}
    rc, so, se = apply_(plan_p, bak, central, "--lock-timeout", "0.5")
finally:
    holder.kill()
    holder.wait()
after = {k: v for k, v in snapshot(home).items() if not k.endswith("profile.lock")}
check(rc == 2 and after == before and "profile.lock" in se,
      f"M11 profile.lock belegt: Exit 2, nichts geschrieben (rc={rc}, {se[-120:]})")

# profile.md ohne passenden Ledger: Exit 2 statt Ueberschreiben
home, plan_p, bak, central = fresh("f")
os.makedirs(os.path.join(central, "identity"), exist_ok=True)
write_bytes(os.path.join(central, "identity", "profile.md"), b"# fremd\n")
rc, so, se = apply_(plan_p, bak, central)
check(rc == 2 and open(os.path.join(central, "identity", "profile.md"), "rb").read() == b"# fremd\n",
      f"M8 profile.md ohne passenden Ledger: Exit 2, nicht ueberschrieben (rc={rc}, {se[-120:]})")

print("--- V: verify (E2-E4, Quellen gleich Backup)")


def verify_(plan_, bak_, central_):
    rep = os.path.join(RUN_ROOT, f"verify-{len(os.listdir(RUN_ROOT))}.json")
    proc = subprocess.run([sys.executable, "-X", "utf8", SCRIPT, "verify", "--plan", plan_, "--backup", bak_,
                           "--identity-store", central_, "--json", rep], capture_output=True, text=True,
                          encoding="utf-8", env=ENV)
    data = json.load(open(rep, encoding="utf-8")) if os.path.isfile(rep) else {}
    return proc.returncode, data, proc.stdout + proc.stderr


def migrated(tag):
    home_, plan_, bak_, central_ = fresh(tag)
    rc_, _so, se_ = apply_(plan_, bak_, central_)
    assert rc_ == 0, se_
    return home_, plan_, bak_, central_


def edit_json(path, fn):
    data = json.load(open(path, encoding="utf-8"))
    put(path, fn(data))


home, plan_p, bak, central = migrated("v0")
rc, rep, txt = verify_(plan_p, bak, central)
check(rc == 0 and rep.get("ok") is True and not rep.get("verstoesse"),
      f"V0 saubere Migration: verify Exit 0, keine Verstoesse (rc={rc}, {str(rep.get('verstoesse'))[:150]})")
check(rep.get("zaehler", {}).get("bullet") == 6 and rep.get("zaehler", {}).get("kandidaten") == 5,
      f"V0 der Bericht zaehlt aus den Backup-Bytes nach ({rep.get('zaehler')})")

# V1 eine verschluckte Zeile (Fortsetzung) in profile.md
home, plan_p, bak, central = migrated("v1")
md_path = os.path.join(central, "identity", "profile.md")
txt_md = open(md_path, encoding="utf-8").read()
write_bytes(md_path, txt_md.replace("  weil lange Antworten untergehen\n", "").encode("utf-8"))
rc, rep, txt = verify_(plan_p, bak, central)
check(rc == 1 and any("weil lange Antworten untergehen" in v for v in rep.get("verstoesse", [])),
      f"V1 verschluckte Profilzeile: verify Exit 1 und nennt sie (rc={rc}, {str(rep.get('verstoesse'))[:150]})")

# V2 ein verschluckter Kandidat
home, plan_p, bak, central = migrated("v2")
edit_json(os.path.join(central, "identity", "profile-candidates.json"), lambda q: [c for c in q if c.get("key") != "offen"])
rc, rep, txt = verify_(plan_p, bak, central)
check(rc == 1 and any("UC3" in v and "projA" in v for v in rep.get("verstoesse", [])),
      f"V2 verschluckter Kandidat: Exit 1, Meldung nennt projA/UC3 (rc={rc}, {str(rep.get('verstoesse'))[:150]})")

# V3 ein verschluckter Changelog-Eintrag
home, plan_p, bak, central = migrated("v3")
edit_json(os.path.join(central, "identity", "profile-changelog.json"),
          lambda log: [e for e in log if e.get("field") != "user.md/Preferences"])
rc, rep, txt = verify_(plan_p, bak, central)
check(rc == 1 and any("changelog" in v.lower() for v in rep.get("verstoesse", [])),
      f"V3 verschluckter Changelog-Eintrag: Exit 1 (rc={rc}, {str(rep.get('verstoesse'))[:150]})")

# V4 eine nach der Migration veraenderte Quelle (Quellen muessen byte-gleich zum Backup bleiben)
home, plan_p, bak, central = migrated("v4")
with open(os.path.join(home, "Desktop", "projB", ".agent-memory", "working", "user-candidates.json"), "ab") as fh:
    fh.write(b" ")
rc, rep, txt = verify_(plan_p, bak, central)
check(rc == 1 and any("Desktop/projB" in v for v in rep.get("verstoesse", [])),
      f"V4 veraenderte Quelle: Exit 1, Meldung nennt den Store (rc={rc}, {str(rep.get('verstoesse'))[:150]})")

# V5 eine falsch abgebildete Zitierung (zwei PC-Ids im Profil vertauscht)
home, plan_p, bak, central = migrated("v5")
txt_md = open(md_path := os.path.join(central, "identity", "profile.md"), encoding="utf-8").read()
write_bytes(md_path, txt_md.replace("(PC3,", "(PCX,").replace("(PC1,", "(PC3,").replace("(PCX,", "(PC1,").encode("utf-8"))
rc, rep, txt = verify_(plan_p, bak, central)
check(rc == 1 and any("PC" in v and "Zitierung" in v for v in rep.get("verstoesse", [])),
      f"V5 vertauschte Zitierung: Exit 1 (rc={rc}, {str(rep.get('verstoesse'))[:150]})")

# V6 ein veraendertes Kandidatenfeld (occurrences) und ein unzulaessiges legacy
home, plan_p, bak, central = migrated("v6")
edit_json(os.path.join(central, "identity", "profile-candidates.json"),
          lambda q: [dict(c, occurrences=7) if c.get("key") == "offen" else
                     dict(c, confirmed_by="legacy") if c.get("key") == "abends" else c for c in q])
rc, rep, txt = verify_(plan_p, bak, central)
v = " ".join(rep.get("verstoesse", []))
check(rc == 1 and "UC3" in v and "UC2" in v, f"V6 veraenderte occurrences und falsches legacy: Exit 1 (rc={rc}, {v[:200]})")

# V7 (T-050) die Briefing-Projektion: veraendert oder geloescht -> verify meldet es
home, plan_p, bak, central = migrated("v7")
brief = os.path.join(central, "identity", "profile-briefing.txt")
with open(brief, "rb") as fh:
    write_bytes(brief, fh.read() + b"- untergeschobene Zeile\n")
rc, rep, txt = verify_(plan_p, bak, central)
check(rc == 1 and any("profile-briefing.txt" in v for v in rep.get("verstoesse", [])),
      f"V7 veraenderte Briefing-Projektion: Exit 1 (rc={rc}, {str(rep.get('verstoesse'))[:150]})")
os.remove(brief)
rc, rep, txt = verify_(plan_p, bak, central)
check(rc == 1 and any("profile-briefing.txt" in v and "fehlt" in v for v in rep.get("verstoesse", [])),
      f"V7 fehlende Briefing-Projektion: Exit 1 (rc={rc}, {str(rep.get('verstoesse'))[:150]})")

print("--- W: Codex-Befunde Teil B (F01-F17)")


def build_home(stores):
    """stores: {projekt: {"user_md": text, "queue": [...], "changelog": [...]}} -> Fake-Home."""
    h = tempfile.mkdtemp(prefix="home-", dir=RUN_ROOT)
    for proj, spec in stores.items():
        mem = os.path.join(h, *proj.split("/"), ".agent-memory")
        if "user_md" in spec:
            write_bytes(os.path.join(mem, "identity", "user.md"), spec["user_md"].encode("utf-8"))
        if "queue" in spec:
            put(os.path.join(mem, "working", "user-candidates.json"), spec["queue"])
        if "changelog" in spec:
            put(os.path.join(mem, "identity", "user-changelog.json"), spec["changelog"])
    return h


def migrate(h, tag, verify_too=True):
    p, b = os.path.join(RUN_ROOT, f"plan-{tag}.json"), os.path.join(RUN_ROOT, f"backup-{tag}")
    c = os.path.join(h, ".agent-memory")
    r1 = run("plan", "--home", h, "--out", p)
    r2 = run("backup", "--plan", p, "--out", b) if r1[0] == 0 else (9, "", "")
    r3 = apply_(p, b, c) if r2[0] == 0 else (9, "", "")
    out = {"plan": p, "backup": b, "central": c, "rc": (r1[0], r2[0], r3[0]), "err": r1[2] + r2[2] + r3[2]}
    if verify_too and r3[0] == 0:
        out["verify"] = verify_(p, b, c)
    return out


def central_md(c):
    return open(os.path.join(c, "identity", "profile.md"), encoding="utf-8").read()


def central_q(c):
    return json.load(open(os.path.join(c, "identity", "profile-candidates.json"), encoding="utf-8"))


# F01 ein beschaedigtes Backup (Kopie fehlt) laesst apply nicht scharf schalten
home, plan_p, bak, central = fresh("w1")
os.remove(os.path.join(bak, "kopie", "projA", "agent-memory.kopie", "identity", "user.md"))
rc, so, se = apply_(plan_p, bak, central)
check(rc == 2 and not os.path.exists(os.path.join(central, "identity", "profile.md")),
      f"W1 (F01) Backup-Kopie fehlt: apply Exit 2, Zentral nicht aktiv (rc={rc}, {se[-120:]})")

# F02 eine fremde profile.md, die WAEHREND des Wartens auf profile.lock entsteht, wird nicht ueberschrieben
home, plan_p, bak, central = fresh("w2")
HOLD_WRITE = ("import sys, time, os; sys.path.insert(0, sys.argv[1]); import store_lock\n"
              "with store_lock.store_lock(sys.argv[2], lock_rel=sys.argv[3]):\n"
              "    print('held', flush=True); time.sleep(2.5)\n"
              "    open(os.path.join(sys.argv[2], 'identity', 'profile.md'), 'wb').write(b'# fremd\\n')\n")
holder = subprocess.Popen([sys.executable, "-c", HOLD_WRITE, SCRIPTS, central, ids.PROFILE_LOCK], stdout=subprocess.PIPE,
                          text=True, encoding="utf-8", env=ENV)
try:
    holder.stdout.readline()
    rc, so, se = apply_(plan_p, bak, central, "--lock-timeout", "15")
finally:
    holder.wait(timeout=30)
md_now = open(os.path.join(central, "identity", "profile.md"), "rb").read()
check(rc == 2 and md_now == b"# fremd\n",
      f"W2 (F02) fremde profile.md waehrend des Lock-Wartens: Exit 2, fremde Bytes bleiben (rc={rc}, {se[-120:]})")

# F03 ein vorhandener Hardlink am alten festen Temp-Namen veraendert keine Quelle
home, plan_p, bak, central = fresh("w3")
src_md = os.path.join(home, "projA", ".agent-memory", "identity", "user.md")
src_before = open(src_md, "rb").read()
os.makedirs(os.path.join(central, "identity"), exist_ok=True)
try:
    os.link(src_md, os.path.join(central, "identity", "profile-changelog.json.tmp-migrate"))
    linked = True
except OSError:
    linked = False
rc, so, se = apply_(plan_p, bak, central)
check(linked and open(src_md, "rb").read() == src_before,
      f"W3 (F03) Hardlink am Temp-Namen: die Quelle bleibt byte-gleich (link={linked}, rc={rc})")


def cq(cid, key, **kw):
    return cand(cid, key, **kw)


# F04 doppelte Id in einem Store: eine Zeile, die sie zitiert, wird NICHT willkuerlich einem der beiden zugeordnet
r = migrate(build_home({"pd": {"user_md": "# User Profile\n\n## Preferences\n- B-Aussage (UC1, 2026-10-01)\n",
                                "queue": [cq("UC1", "a", observation="A-Aussage", status="promoted"),
                                          cq("UC1", "b", observation="B-Aussage", status="promoted")]}}), "w4")
pl = json.load(open(r["plan"], encoding="utf-8"))
check(r["rc"] == (0, 0, 0) and "- B-Aussage (UC1, 2026-10-01)" in central_md(r["central"])
      and any(z.get("alt") == "UC1" and z.get("neu") is None and z.get("grund") == "mehrdeutig" for z in pl["zuordnung"])
      and r["verify"][0] == 0,
      f"W4 (F04) mehrdeutige Id: Zeile bleibt woertlich, Ledger nennt 'mehrdeutig', verify OK ({r['rc']}, {r['err'][-120:]})")

# F05 eine Zitierung am Ende einer Fortsetzungszeile wird abgebildet; F06 eine spaetere Klammer (v1, final) stoert nicht
r = migrate(build_home({"pf": {"user_md": "# User Profile\n\n## Preferences\n- Hauptsatz\n  Zusatz (UC1, 2026-10-01)\n"
                                           "- Kurz antworten (UC2, 2026-10-02) (v1, final)\n",
                                "queue": [cq("UC1", "x", status="promoted"), cq("UC2", "y", status="promoted")]}}), "w5")
md = central_md(r["central"]) if r["rc"] == (0, 0, 0) else ""
check("  Zusatz (PC1, 2026-10-01)\n" in md, f"W5 (F05) Zitierung in einer Fortsetzung wird zu PC1 ({md[-160:]!r})")
check("- Kurz antworten (PC2, 2026-10-02) (v1, final)\n" in md,
      f"W6 (F06) Zitierung vor einer id-aehnlichen Klammer wird abgebildet, die Klammer bleibt ({md[-120:]!r})")
check(r.get("verify", (9,))[0] == 0, f"W5/W6 verify OK ({str(r.get('verify', ('-', {}))[1].get('verstoesse'))[:150]})")

# F07 Profil UND Ledger gemeinsam manipuliert: verify erkennt die falsche Zuordnung aus den Backup-Bytes
home, plan_p, bak, central = migrated("w7")
md_path = os.path.join(central, "identity", "profile.md")
q = central_q(central)
pa_id = next(c["id"] for c in q if c["origin"] == {"projekt": "projA", "id": "UC1"})
pb_id = next(c["id"] for c in q if c["origin"] == {"projekt": "projA", "id": "UC2"})
write_bytes(md_path, central_md(central).replace(f"- Antworten kurz halten ({pa_id},", f"- Antworten kurz halten ({pb_id},").encode("utf-8"))
led_path = os.path.join(central, "identity", "profile-migration-ledger.json")
edit_json(led_path, lambda L: dict(L, zeilen=[dict(z, ziel=z["ziel"].replace(f"({pa_id},", f"({pb_id},"),
                                                    roh=z["roh"].replace("(UC1,", "(UC2,"))
                                               if (z.get("ziel") or "").startswith("- Antworten kurz halten") else z
                                               for z in L["zeilen"]]))
rc, rep, txt = verify_(plan_p, bak, central)
check(rc == 1 and any("Antworten kurz halten" in v for v in rep.get("verstoesse", [])),
      f"W7 (F07) Profil+Ledger gemeinsam falsch: verify Exit 1 (rc={rc}, {str(rep.get('verstoesse'))[:150]})")

# F08 eine veraenderte Zentral-Id (falsches Format, doppelt) faellt auf
for label, new_id in (("Format", "UC3"), ("doppelt", "PC2")):
    home, plan_p, bak, central = migrated(f"w8{label}")
    edit_json(os.path.join(central, "identity", "profile-candidates.json"),
              lambda q, n=new_id: [dict(c, id=n) if c.get("key") == "offen" else c for c in q])
    rc, rep, txt = verify_(plan_p, bak, central)
    check(rc == 1, f"W8 (F08) Zentral-Id {label} ({new_id}): verify Exit 1 (rc={rc}, {str(rep.get('verstoesse'))[:120]})")

# F09 ein zurueckgedrehter Kollisions-Key faellt auf
home, plan_p, bak, central = migrated("w9")
edit_json(os.path.join(central, "identity", "profile-candidates.json"),
          lambda q: [dict(c, key="kurz") if c.get("key") == "kurz@projA" else c for c in q])
rc, rep, txt = verify_(plan_p, bak, central)
check(rc == 1, f"W9 (F09) Key-Kollision wieder eingefuehrt: verify Exit 1 (rc={rc}, {str(rep.get('verstoesse'))[:120]})")

# F10 Vorlage- und Platzhalterzeilen im Ledger werden inhaltlich geprueft, nicht nur gezaehlt
home, plan_p, bak, central = migrated("w10")
edit_json(os.path.join(central, "identity", "profile-migration-ledger.json"),
          lambda L: dict(L, zeilen=[dict(z, roh="") if z.get("entscheidung") == "vorlage" else z for z in L["zeilen"]]))
rc, rep, txt = verify_(plan_p, bak, central)
check(rc == 1, f"W10 (F10) Ledger-Vorlagen geleert: verify Exit 1 (rc={rc}, {str(rep.get('verstoesse'))[:120]})")

# F11 ein nach dem Backup neu entstandener Store wird gemeldet
home, plan_p, bak, central = migrated("w11")
write_bytes(os.path.join(home, "projC", ".agent-memory", "identity", "user.md"), b"# User Profile\n\n## Preferences\n- Neu\n")
rc, rep, txt = verify_(plan_p, bak, central)
check(rc == 1 and any("projC" in v for v in rep.get("verstoesse", [])),
      f"W11 (F11) neuer Store nach dem Backup: verify Exit 1 (rc={rc}, {str(rep.get('verstoesse'))[:120]})")

# F12 eine Quelle, deren Pruefung (stat) scheitert, stoppt plan statt still zu fehlen
home12 = make_home()
bad = os.path.normcase(os.path.join(home12, "projA", ".agent-memory", "identity", "user.md"))
real_stat = os.stat


def failing_stat(path, *a, **kw):
    if os.path.normcase(os.path.abspath(str(path))) == bad:
        raise PermissionError(13, "Zugriff verweigert", path)
    return real_stat(path, *a, **kw)


out12 = os.path.join(RUN_ROOT, "plan-w12.json")
os.stat = failing_stat
try:
    rc12 = mi.main(["plan", "--home", home12, "--out", out12])
finally:
    os.stat = real_stat
check(rc12 == 2 and not os.path.exists(out12), f"W12 (F12) stat-Fehler an einer Quelle: plan Exit 2, kein Plan (rc={rc12})")

# F13 ein Ausgabepfad im Store in anderer Schreibweise wird abgelehnt (Windows: Gross/Klein egal)
if os.name == "nt":
    home13 = make_home()
    out13 = os.path.join(home13, "projA", ".AGENT-MEMORY", "plan.json")
    rc, so, se = run("plan", "--home", home13, "--out", out13)
    check(rc == 2 and not os.path.exists(out13), f"W13 (F13) --out unter .AGENT-MEMORY: Exit 2 (rc={rc}, {se[-100:]})")

# F14 verify --json ueberschreibt nie eine vorhandene Datei und schreibt nie in einen Store
home, plan_p, bak, central = migrated("w14")
src_md = os.path.join(home, "projA", ".agent-memory", "identity", "user.md")
before14 = open(src_md, "rb").read()
proc = subprocess.run([sys.executable, "-X", "utf8", SCRIPT, "verify", "--plan", plan_p, "--backup", bak,
                       "--identity-store", central, "--json", src_md], capture_output=True, text=True, encoding="utf-8", env=ENV)
check(proc.returncode == 2 and open(src_md, "rb").read() == before14,
      f"W14 (F14) verify --json auf eine Quelle: Exit 2, Quelle byte-gleich (rc={proc.returncode})")

# F15 ein schon in der Quelle vorhandenes confirmed_by legacy ist kein Verstoss
r = migrate(build_home({"pl": {"queue": [cq("UC1", "l", status="promoted", confirmed_by="legacy")]}}), "w15")
check(r["rc"] == (0, 0, 0) and r["verify"][0] == 0,
      f"W15 (F15) Quelle mit confirmed_by legacy: verify OK ({r['rc']}, {str(r.get('verify', ('-', {}))[1].get('verstoesse'))[:120]})")

# F16 ein Quellfeld namens _datei bleibt erhalten
r = migrate(build_home({"pu": {"queue": [cq("UC1", "u", _datei="originaler Wert")]}}), "w16")
q16 = central_q(r["central"]) if r["rc"] == (0, 0, 0) else []
check(q16 and q16[0].get("_datei") == "originaler Wert" and r["verify"][0] == 0,
      f"W16 (F16) Feld _datei bleibt erhalten, verify OK ({q16[:1]})")

# F17 ein Quell-Changelog-Eintrag mit origin UND origin_quelle verliert nichts
r = migrate(build_home({"po": {"changelog": [{"ts": "2026-10-01T10:00:00+02:00", "field": "user.md/Preferences",
                                               "origin": {"alt": "A"}, "origin_quelle": {"behalten": "B"}}]}}), "w17")
log17 = json.load(open(os.path.join(r["central"], "identity", "profile-changelog.json"), encoding="utf-8")) \
    if r["rc"] == (0, 0, 0) else []
src17 = [e for e in log17 if e.get("field") == "user.md/Preferences"]
check(src17 and src17[0].get("origin_quelle") == {"behalten": "B"} and r["verify"][0] == 0
      and json.dumps(src17[0], ensure_ascii=False).count('"alt": "A"') == 1,
      f"W17 (F17) origin und origin_quelle der Quelle bleiben erhalten, verify OK ({src17})")

print("--- W: Codex-Befunde Teil B, Runde 2")
import contextlib  # noqa: E402

# R2-01 die LETZTE Kandidaten-Klammer entscheidet, auch wenn sie mehrdeutig ist (keine fruehere eindeutige gewinnt)
line, rec = mi.map_citations("- Aussage (UC2, 2026-10-01) (UC1, 2026-10-02)", {"UC2": "PC3"}, {"UC1"})
check(line == "- Aussage (UC2, 2026-10-01) (UC1, 2026-10-02)" and rec == {"alt": "UC1", "neu": None, "grund": "mehrdeutig"},
      f"W18 (R2-01) letzte Klammer mehrdeutig: Zeile bleibt, grund mehrdeutig ({line!r}, {rec})")
line, rec = mi.map_citations("- Aussage (UC1, 2026-10-02) (UC2, 2026-10-01)", {"UC2": "PC3"}, {"UC1"})
check(line == "- Aussage (UC1, 2026-10-02) (PC3, 2026-10-01)" and rec == {"alt": "UC2", "neu": "PC3"},
      f"W18 (R2-01) letzte Klammer eindeutig: wird abgebildet ({line!r}, {rec})")

# R2-02 eine neue Quelldatei im (von apply angelegten) Zentral-Store wird gemeldet
r = migrate(build_home({"nur": {"user_md": "# User Profile\n\n## Preferences\n- Eine Zeile\n"}}), "w19")
write_bytes(os.path.join(r["central"], "identity", "user.md"), b"# User Profile\n\n## Preferences\n- Unbemerkt\n")
rc, rep, txt = verify_(r["plan"], r["backup"], r["central"])
check(r["rc"] == (0, 0, 0) and rc == 1 and any("identity/user.md" in v for v in rep.get("verstoesse", [])),
      f"W19 (R2-02) neue Quelle im neu angelegten Zentral-Store: verify Exit 1 (rc={rc}, {str(rep.get('verstoesse'))[:150]})")

# R2-03 deterministisch: eine fremde profile.md entsteht genau zwischen der ersten Pruefung und der Lock-Uebernahme
home, plan_p, bak, central = fresh("w20")
real_lock = store_lock.store_lock


@contextlib.contextmanager
def lock_after_foreign_write(mem, timeout=10.0, lock_rel=None):
    with open(os.path.join(mem, "identity", "profile.md"), "wb") as fh:  # ein anderer Schreiber war zuerst im Lock
        fh.write(b"# fremd\n")
    with real_lock(mem, timeout=timeout, lock_rel=lock_rel) as held:
        yield held


os.makedirs(os.path.join(central, "identity"), exist_ok=True)
store_lock.store_lock = lock_after_foreign_write
try:
    rc20 = mi.main(["apply", "--plan", plan_p, "--backup", bak, "--identity-store", central])
finally:
    store_lock.store_lock = real_lock
check(rc20 == 2 and open(os.path.join(central, "identity", "profile.md"), "rb").read() == b"# fremd\n",
      f"W20 (R2-03) fremde profile.md zwischen erster Pruefung und Lock: Exit 2, fremde Bytes bleiben (rc={rc20})")

# R2-04 verify --json auf eine NEUE Datei in einem Store wird abgelehnt
home, plan_p, bak, central = migrated("w21")
target = os.path.join(home, "projA", ".agent-memory", "verify-neu.json")
proc = subprocess.run([sys.executable, "-X", "utf8", SCRIPT, "verify", "--plan", plan_p, "--backup", bak,
                       "--identity-store", central, "--json", target], capture_output=True, text=True, encoding="utf-8", env=ENV)
check(proc.returncode == 2 and not os.path.exists(target),
      f"W21 (R2-04) verify --json auf neue Datei im Store: Exit 2, keine Datei (rc={proc.returncode})")

# R1 eine vorhandene Projekt-Projektion des Home-Projekts blockiert die Migration nicht und bleibt unberuehrt
home22 = make_home()
legacy_proj = os.path.join(home22, ".agent-memory", "identity", "briefing-lines.txt")
write_bytes(legacy_proj, b"- Home-Projekt-Projektion aus 5.9.0\n")
r = migrate(home22, "w22")
check(r["rc"] == (0, 0, 0) and open(legacy_proj, "rb").read() == b"- Home-Projekt-Projektion aus 5.9.0\n"
      and os.path.isfile(os.path.join(r["central"], *ids.PROFILE_BRIEFING.split("/"))) and r["verify"][0] == 0,
      f"W22 (R1) Home-Store mit Projekt-Projektion: Migration laeuft, Projektion bleibt, verify OK ({r['rc']}, {r['err'][-150:]})")

print("--- SI: soul-Inventar (nur lesend, E5)")
SOUL_CAND_STUB = ("# Soul Candidates\n\n*Keine offenen Kandidaten.*\n\n"
                  "## Kandidat: Beispielvorschlag A (2026-07-20)\n"
                  "**Vorschlag:** Beispieltext eins.\n"
                  "**Evidence:** UC7 (2026-07-18)\n\n"
                  "## 2026-10-22 — Beispielregel B\n\n"
                  "- **Evidence:** 2026-10-22 Beispielbeleg\n")
SOUL_CAND_BULLET = ("# Soul Candidates\n\n- **Vorschlag (2026-07-19):** Beispielvorschlag C, erste Zeile,\n"
                    "  Fortsetzung des Vorschlags.\n")
tpl_text = open(os.path.join(SCRIPTS, "soul-template.txt"), encoding="utf-8").read()
SOUL_TEMPLATE_ONLY = tpl_text.split("<!-- earlier")[0]
SOUL_OWN = ("# Agent Identity\r\n\r\n## Communication\r\n- Language: en (switch to de if user writes in German)\r\n"
            "- Brevity: 3/5 (balanced)\r\n- Beispielzeile eins\r\n\r\n"
            "## Priorities\r\n1. Correctness over speed\r\n\r\n"
            "## Eigene Sektion\r\n- Beispielzeile zwei\r\n  Fortsetzung der Beispielzeile\r\n")
home_si = tempfile.mkdtemp(prefix="home-", dir=RUN_ROOT)
for proj, name, text in (("s1", "soul-candidates.md", SOUL_CAND_STUB), ("s2", "soul-candidates.md", SOUL_CAND_BULLET),
                         ("s3", "soul.md", SOUL_TEMPLATE_ONLY), ("s4", "soul.md", SOUL_OWN),
                         ("s5", "soul-candidates.md", "# Soul Candidates\n\n*Keine offenen Kandidaten.*\n")):
    write_bytes(os.path.join(home_si, proj, ".agent-memory", "identity", name), text.encode("utf-8"))
before = snapshot(home_si)
out_si = os.path.join(RUN_ROOT, "soul.json")
rc, so, se = run("soul", "--home", home_si, "--out", out_si)
inv = json.load(open(out_si, encoding="utf-8")) if rc == 0 and os.path.isfile(out_si) else {}
items = inv.get("elemente", [])
kand = [i for i in items if i["art"] == "kandidat"]
own = [i for i in items if i["art"] == "soul_zeile"]
check(rc == 0 and snapshot(home_si) == before, f"SI1 soul schreibt kein Byte in die Stores (rc={rc}, {se[-150:]})")
check([(k["origin"]["projekt"], k["titel"]) for k in kand]
      == [("s1", "Kandidat: Beispielvorschlag A (2026-07-20)"),
          ("s1", "2026-10-22 — Beispielregel B"),
          ("s2", "- **Vorschlag (2026-07-19):** Beispielvorschlag C, erste Zeile,")],
      f"SI1 drei Kandidaten: je ## -Abschnitt einer, ein Aufzaehlungspunkt vor dem ersten Abschnitt einer, der Stub keiner "
      f"({[(k['origin']['projekt'], k['titel'][:30]) for k in kand]})")
check("UC7 (2026-07-18)" in kand[0]["text"] and "Fortsetzung des Vorschlags." in kand[2]["text"],
      "SI1 der Kandidatentext enthaelt Vorschlag, Evidenz und Fortsetzungszeilen")
check([(o["origin"]["projekt"], o["sektion"], o["text"]) for o in own]
      == [("s4", "Communication", "- Beispielzeile eins"),
          ("s4", "Eigene Sektion", "- Beispielzeile zwei\n  Fortsetzung der Beispielzeile")],
      f"SI1 soul.md: nur eigene Zeilen (Vorlage in allen Fassungen und nummerierte Vorlagenzeilen fallen weg), "
      f"Fortsetzung beim Bullet ({[(o['origin']['projekt'], o['text'][:30]) for o in own]})")
z = inv.get("zaehler", {})
check(z.get("kandidaten") == 3 and z.get("kandidaten_stores") == 2 and z.get("soul_bloecke") == 2
      and z.get("soul_stores") == 1 and z.get("soul_zeilen") == 3,
      f"SI1 Zaehler: 3 Kandidaten in 2 Stores, 2 eigene Bloecke (3 Zeilen) in 1 Store ({z})")
check(len({json.dumps(i["origin"], sort_keys=True) for i in items}) == len(items),
      "SI1 jede origin genau einmal (Grundlage der Lueckenlosigkeit in Task 14)")

print(f"=== Results: {PASSED}/{TESTS} passed, {ERRORS} failures ===")
sys.exit(1 if ERRORS else 0)

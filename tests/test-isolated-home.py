#!/usr/bin/env python3
"""Tests für tests/_isolated_home.py (P0 Messgrundlage, Spec 2026-10-27 Abschnitt 2).

Der Helfer gibt Testprozessen eine Umgebung mit frischem Temp-Home, damit ein Skript-Subprozess nie das echte Home
(`~/AI`, `~/.agent-memory/identity`, `~/.agentic-os`) sieht. Dieser Test beweist das an drei Stellen:

 (a) der Helfer setzt alle Variablen, entfernt die Sitzungsvariablen, und das Home liegt weder unter dem echten Home
     noch unter einem `.agent-memory`;
 (b) ein Kindprozess sieht `os.path.expanduser('~')` == Temp-Home (die Wirkung, nicht nur die Variable);
 (c) das Home wird nach einer Ausnahme, bei normalem Ende und beim Prozessende (atexit) entfernt;
 (d) geerbte Variablen (AGENTIC_OS_*, CLAUDE_CODE_SESSION_ID, CLAUDE_PLUGIN_DATA, NIGHTLY_CONSOLIDATION_REPORT)
     erreichen den Kindprozess nicht, nur die beiden gesetzten AGENTIC_OS_*-Umleitungen bleiben;
 (e) Sperren: irgendein Vorfahr mit einem Ordner .agent-memory bricht ab, ein nicht beschreibbarer Laufwerks-Root
     bricht mit klarer Meldung ab und fällt nie still auf %TEMP% zurück;
 (f) statische Pflicht (per ast, keine Regex): in den umgestellten Testdateien hat jeder Subprozess-Aufruf ein env=,
     das aus ENV abgeleitet ist, und ENV selbst stammt aus process_env().

Gegenproben (Pflicht, L86): Lässt man im Helfer USERPROFILE weg, müssen (a) und (b) rot werden; fehlt die
Bereinigung, die Vorfahren-Sperre oder entsteht ein Rückfall auf %TEMP%, werden (d) bzw. (e) rot; (f) wird an
Kopien der echten Dateien gegengeprüft: pro Subprozess-Aufruf wird sein env= entfernt, und genau diese Stelle
muss gemeldet werden.

Es wird kein echter Store gelesen oder geschrieben; alles liegt in Temp-Homes, die der Helfer selbst entfernt.
Exit-Codes: 0 = alles grün, 1 = Fehler.
"""

from __future__ import annotations

import ast
import contextlib
import getpass
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
import _isolated_home as h  # noqa: E402

TESTS = PASSED = ERRORS = 0
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def check(cond, msg):
    global TESTS, PASSED, ERRORS
    TESTS += 1
    if cond:
        PASSED += 1
        print(f"  PASS: {msg}")
    else:
        ERRORS += 1
        print(f"  FAIL: {msg}")


def norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def is_under(path: str, parent: str) -> bool:
    p, q = norm(path), norm(parent)
    return p == q or p.startswith(q.rstrip("\\/") + os.sep)


def real_home_candidates() -> list:
    """Das echte Home so unabhängig vom aktuellen Prozess wie möglich: läuft dieser Test im Isolations-Runner, zeigt
    `~` schon auf ein Temp-Home, deshalb kommt der Benutzername aus dem Betriebssystem dazu."""
    names = {getpass.getuser()}
    cands = [os.path.expanduser("~"), os.environ.get("USERPROFILE", ""), os.environ.get("HOME", "")]
    drive = os.path.splitdrive(os.path.realpath(tempfile.gettempdir()))[0] + os.sep
    for name in names:
        cands += [os.path.join(drive, "Users", name), os.path.join(os.sep, "home", name),
                  os.path.join(os.sep, "Users", name)]
    return [c for c in cands if c]


def child_view(env: dict) -> dict:
    """Was ein Kindprozess mit dieser Umgebung sieht (reiner ASCII-Code, kein Sonderzeichen)."""
    code = ("import json, os, pathlib; "
            "print(json.dumps({'tilde': os.path.expanduser('~'), 'path_home': str(pathlib.Path.home()), "
            "'session': [k for k in ('CLAUDE_PROJECT_DIR', 'CLAUDE_PLUGIN_ROOT', 'CLAUDE_ENV_FILE', "
            "'CLAUDE_MEMORY_GLOBAL', 'BASH_ENV', 'ENV') if k in os.environ], "
            "'snap': os.environ.get('AGENTIC_OS_SNAPSHOT_DIR'), 'ident': os.environ.get('AGENTIC_OS_IDENTITY_STORE')}))")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", env=env)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {"_err": p.stderr, "_out": p.stdout}


PROBE = f"isohome-probe-{os.getpid()}-"  # eigener Präfix: parallele Läufe anderer Sitzungen stören den Zählvergleich nicht


def expected_base() -> str:
    """Unabhängiges Orakel für den Ort des Homes: Wurzelordner des Laufwerks von %TEMP% (POSIX: der Temp-Ordner)."""
    tmp = os.path.realpath(tempfile.gettempdir())
    drive = os.path.splitdrive(tmp)[0]
    return drive + os.sep if drive else tmp


def probe_leftovers() -> list:
    found = set()
    for d in {expected_base(), os.path.realpath(tempfile.gettempdir())}:
        found.update(glob.glob(os.path.join(glob.escape(d), PROBE + "*")))
    return sorted(found)


@contextlib.contextmanager
def patched_base(path: str):
    """Schiebt dem Helfer einen Basisordner unter (egal ob er ihn als Liste oder als Wert abfragt)."""
    saved = {}
    for name, fn in (("_base_dirs", lambda: [path]), ("_base_dir", lambda: path)):
        if hasattr(h, name):
            saved[name] = getattr(h, name)
            setattr(h, name, fn)
    try:
        yield
    finally:
        for name, fn in saved.items():
            setattr(h, name, fn)


@contextlib.contextmanager
def root_not_writable(blocked: str):
    """Simuliert einen Basisordner ohne Schreibrecht: mkdtemp dort scheitert mit PermissionError, überall sonst
    funktioniert es echt. So wird ein stiller Rückfall auf %TEMP% als Erfolg sichtbar, statt zu crashen."""
    real = tempfile.mkdtemp

    def picky(suffix=None, prefix=None, dir=None):
        if dir is not None and norm(dir) == norm(blocked):
            raise PermissionError(13, "Zugriff verweigert (simuliert)", dir)
        return real(suffix=suffix, prefix=prefix, dir=dir)
    tempfile.mkdtemp = picky
    try:
        yield
    finally:
        tempfile.mkdtemp = real


def try_make_home(base_path=None):
    """make_home mit untergeschobenem Basisordner (None = echter Ort); liefert (home, ausnahme). Ein entstandenes Home
    muss der Aufrufer entfernen."""
    try:
        if base_path is None:
            return h.make_home(prefix=PROBE), None
        with patched_base(base_path):
            return h.make_home(prefix=PROBE), None
    except Exception as exc:  # noqa: BLE001 - die Art der Ausnahme ist Teil der Prüfung
        return None, exc


print("=== _isolated_home tests ===")

# --- (a) alle Variablen gesetzt, Sitzungsvariablen entfernt, Home am richtigen Ort -----------------------------
print("--- (a) Variablen und Ort")
SENTINEL = "bleibt-unveraendert"
base = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "SENTINEL_VAR": SENTINEL,
        "HOME": "X:/falsches-home", "USERPROFILE": "X:/falsches-profil",
        "CLAUDE_PROJECT_DIR": "X:/p", "CLAUDE_PLUGIN_ROOT": "X:/r", "CLAUDE_ENV_FILE": "X:/e",
        "CLAUDE_MEMORY_GLOBAL": "X:/g", "BASH_ENV": "X:/b", "ENV": "X:/v",
        "AGENTIC_OS_SNAPSHOT_DIR": "X:/echte-snaps", "AGENTIC_OS_IDENTITY_STORE": "X:/echtes-zentral"}
base_copy = dict(base)
with h.isolated_env(base, PYTHONIOENCODING="utf-8") as (env, home):
    check(os.path.isdir(home), "das Temp-Home existiert während des Blocks")
    check(env["HOME"] == home and env["USERPROFILE"] == home, "HOME und USERPROFILE zeigen auf das Temp-Home")
    check(env["AGENTIC_OS_SNAPSHOT_DIR"] == os.path.join(home, "snapshots"),
          "AGENTIC_OS_SNAPSHOT_DIR ist ein Unterordner des Homes")
    check(os.path.isdir(env["AGENTIC_OS_SNAPSHOT_DIR"]), "der Snapshot-Ordner ist angelegt (wie bisher in den Tests)")
    check(env["AGENTIC_OS_IDENTITY_STORE"] == os.path.join(home, "identity-store"),
          "AGENTIC_OS_IDENTITY_STORE ist ein Unterordner des Homes")
    check(not os.path.exists(env["AGENTIC_OS_IDENTITY_STORE"]),
          "der Identitäts-Ordner ist nicht angelegt (ein Schreibzugriff darauf bleibt an seiner Abwesenheit sichtbar)")
    for name in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT", "CLAUDE_ENV_FILE", "CLAUDE_MEMORY_GLOBAL", "BASH_ENV", "ENV"):
        check(name not in env, f"Sitzungsvariable {name} ist entfernt")
    check(env["SENTINEL_VAR"] == SENTINEL and env["PATH"] == base["PATH"],
          "übrige Variablen bleiben unverändert (PATH, Sentinel)")
    check(env["PYTHONIOENCODING"] == "utf-8", "Overrides werden zuletzt gesetzt")
    check(base == base_copy, "base_env selbst bleibt unverändert")
    if os.name == "nt":
        mixed = dict(base, Userprofile="X:/gemischte-schreibweise")
        with h.isolated_env(mixed) as (env2, _home2):
            check([k for k in env2 if k.upper() == "USERPROFILE"] == ["USERPROFILE"],
                  "Windows: kein zweiter USERPROFILE-Eintrag in anderer Schreibweise")
    else:
        print("  SKIP: Windows-Schreibweise der Umgebungsvariablen (nur unter Windows relevant)")
    bad = [c for c in real_home_candidates() if is_under(home, c)]
    check(not bad, f"das Temp-Home liegt nicht unter dem echten Home ({bad})")
    check(h.STORE_DIR_NAME not in norm(home).replace("\\", "/").split("/"),
          "das Temp-Home liegt nicht unter einem .agent-memory")

# Harte Sperre: ein Basisordner unter einem Store wird abgelehnt und räumt hinter sich auf
print("--- (a) Sperre gegen ein Home unter einem Store")
td = h.make_home(prefix=PROBE)
try:
    store_like = os.path.join(td, ".agent-memory", "sub")
    os.makedirs(store_like)
    home_x, exc = try_make_home(store_like)
    if home_x:
        h.remove_home(home_x)
    check(isinstance(exc, RuntimeError) and ".agent-memory" in str(exc),
          f"make_home unter einem .agent-memory wird abgelehnt ({exc!r})")
    check(os.listdir(store_like) == [], "das abgelehnte Home wurde nicht angelegt (nichts bleibt im Store zurück)")
finally:
    h.remove_home(td)

# --- (b) Wirkung im Kindprozess ------------------------------------------------------------------------------
print("--- (b) Kindprozess")
with h.isolated_env(base) as (env, home):
    seen = child_view(env)
    check("_err" not in seen, f"Kindprozess lief ({seen.get('_err', '')[:120]})")
    check(seen.get("tilde") and norm(seen["tilde"]) == norm(home),
          f"Kindprozess: os.path.expanduser('~') == Temp-Home ({seen.get('tilde')})")
    check(seen.get("path_home") and norm(seen["path_home"]) == norm(home), "Kindprozess: Path.home() == Temp-Home")
    check(seen.get("session") == [], f"Kindprozess sieht keine Sitzungsvariable ({seen.get('session')})")
    check(seen.get("snap") == env["AGENTIC_OS_SNAPSHOT_DIR"] and seen.get("ident") == env["AGENTIC_OS_IDENTITY_STORE"],
          "Kindprozess sieht die Umleitungen für Snapshots und Zentralprofil")
    # Kontrolle: ohne den Helfer sieht derselbe Kindprozess ein anderes Home, der Test unterscheidet also etwas
    raw = child_view(dict(base, HOME=os.path.dirname(home), USERPROFILE=os.path.dirname(home)))
    check(raw.get("tilde") and norm(raw["tilde"]) != norm(home),
          "Kontrolle: mit der Roh-Umgebung sieht das Kind nicht das Temp-Home")

# --- (c) Aufräumen --------------------------------------------------------------------------------------------
print("--- (c) Aufräumen")
seen_home = []
try:
    with h.isolated_env() as (env, home):
        seen_home.append(home)
        os.makedirs(os.path.join(home, "tief", "verschachtelt"))
        with open(os.path.join(home, "tief", "verschachtelt", "datei.txt"), "w", encoding="utf-8") as fh:
            fh.write("Inhalt")
        raise KeyError("absichtlicher Abbruch")
except KeyError:
    check(True, "die Ausnahme aus dem Block läuft nach außen weiter")
else:
    check(False, "die Ausnahme aus dem Block läuft nach außen weiter")
check(len(seen_home) == 1 and not os.path.exists(seen_home[0]),
      "nach einer Ausnahme ist das Home samt Unterordnern und Dateien entfernt")

with h.isolated_env() as (env, home):
    normal_home = home
check(not os.path.exists(normal_home), "nach normalem Ende ist das Home entfernt")

# process_env (für Testdateien mit modulweitem ENV) räumt per atexit auf, auch wenn der Prozess mit sys.exit endet
code = ("import sys; sys.path.insert(0, sys.argv[1]); import _isolated_home as h; "
        "env, home = h.process_env(PYTHONIOENCODING='utf-8'); print(home, flush=True); "
        "print(env['USERPROFILE'] == home and env['PYTHONIOENCODING'] == 'utf-8', flush=True); sys.exit(3)")
p = subprocess.run([sys.executable, "-c", code, TESTS_DIR], capture_output=True, text=True, encoding="utf-8")
lines = p.stdout.split("\n")
check(p.returncode == 3 and len(lines) >= 2 and lines[1].strip() == "True",
      f"process_env liefert dasselbe Home und dieselben Variablen (rc={p.returncode}, {p.stderr.strip()[:120]})")
check(bool(lines[0]) and not os.path.exists(lines[0].strip()),
      f"process_env: das Home ist nach sys.exit(3) per atexit entfernt ({lines[0].strip()})")

# --- (d) geerbte Variablen -------------------------------------------------------------------------------------
print("--- (d) geerbte Variablen")
# Namen unabhängig vom Helfer festgeschrieben: der Test darf nicht dieselbe Liste wie der Helfer benutzen
DROPPED_EXACT = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PLUGIN_DATA", "NIGHTLY_CONSOLIDATION_REPORT")
KEPT_AGENTIC = ("AGENTIC_OS_SNAPSHOT_DIR", "AGENTIC_OS_IDENTITY_STORE")
inherited = dict(base, AGENTIC_OS_ACTIVE="true", AGENTIC_OS_MEMORY_DIR="X:/echter-store",
                 AGENTIC_OS_CENTRAL_HANDOFF="X:/zentral/handoff.md", AGENTIC_OS_CODEX_HEADLESS="1",
                 CLAUDE_CODE_SESSION_ID="sitzung-1", CLAUDE_PLUGIN_DATA="X:/plugin-daten",
                 NIGHTLY_CONSOLIDATION_REPORT="X:/bericht.jsonl",
                 AGENTIC_OSX_BLEIBT="1", MEIN_AGENTIC_OS_BLEIBT="1")  # nur der Präfix AGENTIC_OS_ ist gemeint
if os.name == "nt":
    inherited["agentic_os_klein"] = "1"  # Windows: Groß-/Kleinschreibung zählt nicht
inherited_copy = dict(inherited)


def child_names(env: dict) -> list:
    code = ("import json, os; print(json.dumps(sorted(k for k in os.environ if k.upper().startswith('AGENTIC_OS') "
            "or k.upper().startswith('MEIN_AGENTIC') or k.upper() in ('CLAUDE_CODE_SESSION_ID', 'CLAUDE_PLUGIN_DATA', "
            "'NIGHTLY_CONSOLIDATION_REPORT'))))")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", env=env)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return [f"_err: {p.stderr[:120]}"]


with h.isolated_env(inherited, AGENTIC_OS_EXPLIZIT="ja") as (env, home):
    upper = {k.upper() for k in env}
    for name in ("AGENTIC_OS_ACTIVE", "AGENTIC_OS_MEMORY_DIR", "AGENTIC_OS_CENTRAL_HANDOFF", "AGENTIC_OS_CODEX_HEADLESS",
                 *DROPPED_EXACT):
        check(name not in upper, f"geerbte Variable {name} ist entfernt")
    if os.name == "nt":
        check("AGENTIC_OS_KLEIN" not in upper, "Windows: auch die kleingeschriebene Schreibweise agentic_os_klein ist entfernt")
    check(env["AGENTIC_OS_SNAPSHOT_DIR"] == os.path.join(home, "snapshots")
          and env["AGENTIC_OS_IDENTITY_STORE"] == os.path.join(home, "identity-store"),
          "die beiden gesetzten AGENTIC_OS_*-Umleitungen zeigen auf das Temp-Home (nicht auf die geerbten Werte)")
    check(env.get("AGENTIC_OSX_BLEIBT") == "1" and env.get("MEIN_AGENTIC_OS_BLEIBT") == "1",
          "nur der Präfix AGENTIC_OS_ wird entfernt (AGENTIC_OSX_..., MEIN_AGENTIC_OS_... bleiben)")
    check(env.get("AGENTIC_OS_EXPLIZIT") == "ja", "ein ausdrücklicher Override mit Präfix AGENTIC_OS_ bleibt erhalten")
    check(env["SENTINEL_VAR"] == SENTINEL, "übrige Variablen bleiben unverändert")
    names = child_names(env)
    check(sorted(names) == sorted([*KEPT_AGENTIC, "AGENTIC_OS_EXPLIZIT", "AGENTIC_OSX_BLEIBT", "MEIN_AGENTIC_OS_BLEIBT"]),
          f"Kindprozess sieht von den geerbten Namen nur die zwei Umleitungen, den Override und die Nicht-Treffer ({names})")
check(inherited == inherited_copy, "base_env selbst bleibt unverändert (auch mit geerbten Variablen)")

# --- (e) Sperren ---------------------------------------------------------------------------------------------
print("--- (e) Sperren: Vorfahren, nicht beschreibbarer Root, kein Rückfall")
got_base = getattr(h, "_base_dir", lambda: "<_base_dir fehlt>")()
check(norm(got_base) == norm(expected_base()),
      f"der Basisordner ist der Laufwerks-Root des Temp-Ordners, nicht der Temp-Ordner selbst ({got_base})")

# Vorfahr mit einem Ordner .agent-memory (Geschwister, nicht Pfadbestandteil) - egal wie weit oben
td = h.make_home(prefix=PROBE)
try:
    proj = os.path.join(td, "proj")
    deep = os.path.join(proj, "a", "b")
    os.makedirs(deep)
    store_dir = os.path.join(proj, ".agent-memory")
    os.makedirs(store_dir)
    home_x, exc = try_make_home(deep)
    if home_x:
        h.remove_home(home_x)
    check(isinstance(exc, RuntimeError) and ".agent-memory" in str(exc)
          and os.path.normcase(proj) in os.path.normcase(str(exc)),
          f"ein Vorfahr (zwei Ebenen höher) mit Ordner .agent-memory bricht ab und wird genannt ({exc!r})"[:200])
    check(os.listdir(deep) == [], "bei der Ablehnung wurde nichts angelegt")
    shutil.rmtree(store_dir)
    home_x, exc = try_make_home(deep)
    check(exc is None and home_x is not None and os.path.isdir(home_x),
          f"Kontrolle: ohne Ordner .agent-memory im Vorfahren wird das Home angelegt ({exc!r})")
    if home_x:
        h.remove_home(home_x)
    with open(store_dir, "w", encoding="utf-8") as fh:  # eine Datei gleichen Namens ist kein Store
        fh.write("keine Ordnerstruktur")
    home_x, exc = try_make_home(deep)
    check(exc is None and home_x is not None, f"eine Datei namens .agent-memory zählt nicht ({exc!r})")
    if home_x:
        h.remove_home(home_x)
finally:
    h.remove_home(td)

# Laufwerks-Root nicht beschreibbar: Abbruch mit klarer Meldung, kein Rückfall auf den Temp-Ordner
before = probe_leftovers()
with root_not_writable(expected_base()):
    home_x, exc = try_make_home()
if home_x:
    h.remove_home(home_x)
check(home_x is None, "nicht beschreibbarer Laufwerks-Root: es entsteht kein Home (kein Rückfall auf den Temp-Ordner)")
check(isinstance(exc, RuntimeError) and norm(expected_base()) in norm(str(exc)) and "Rückfall" in str(exc),
      f"nicht beschreibbarer Laufwerks-Root: Ausnahme nennt den Ordner und den ausgeschlossenen Rückfall ({exc!r})"[:220])
check(probe_leftovers() == before, "nicht beschreibbarer Laufwerks-Root: nirgends bleibt ein Home zurück")

missing = os.path.join(expected_base(), f"gibt-es-nicht-{os.getpid()}", "x")  # unter dem Root, nicht unter %TEMP% (Store-Vorfahr)
home_x, exc = try_make_home(missing)
if home_x:
    h.remove_home(home_x)
check(home_x is None and isinstance(exc, RuntimeError) and "beschreibbar" in str(exc),
      f"ein nicht vorhandener Basisordner bricht mit klarer Meldung ab ({exc!r})"[:200])
check(probe_leftovers() == before, "nicht vorhandener Basisordner: nirgends bleibt ein Home zurück")


# --- (f) env-Pflicht per ast -----------------------------------------------------------------------------------
print("--- (f) jeder Subprozess-Aufruf in den umgestellten Testdateien hat ein env= aus ENV (ast)")
SPAWN_FUNCS = ("run", "Popen", "check_output", "check_call", "call")
ENV_BLIND = (("subprocess", "getoutput"), ("subprocess", "getstatusoutput"), ("os", "system"), ("os", "popen"))
CHECKED_FILES = ("test-apply-wrapup.py", "test-corrections.py", "test-rule-release.py", "test-principles.py",
                 "test-restore.py", "test-wrapup-core.py", "test-identity-b2.py", "test-preprocess-state.py",
                 "test-identity-central.py", "test-migrate-identity.py")


def _is_blind(kind) -> bool:
    return kind in ENV_BLIND or (kind[0] == "os" and kind[1].startswith(("spawn", "exec")))


def _spawn_kind(func, modules: dict, funcs: dict):
    """('subprocess', 'run') o. ä., wenn `func` einen Prozess startet, sonst None. Löst Aliase auf."""
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in modules:
        kind = (modules[func.value.id], func.attr)
    elif isinstance(func, ast.Name) and func.id in funcs:
        kind = funcs[func.id]
    else:
        return None
    if (kind[0] == "subprocess" and kind[1] in SPAWN_FUNCS) or _is_blind(kind):
        return kind
    return None


def _spawn_calls(tree):
    """(Aufrufe, Probleme): alle Prozessstarts im Baum samt Art; Probleme sind nicht prüfbare Konstruktionen."""
    modules, funcs, problems = {}, {}, []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname is None and a.name.split(".")[0] in ("subprocess", "os"):
                    modules[a.name.split(".")[0]] = a.name.split(".")[0]
                elif a.asname and a.name in ("subprocess", "os"):
                    modules[a.asname] = a.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in ("subprocess", "os"):
            for a in node.names:
                if a.name == "*":
                    problems.append((node.lineno, f"from {node.module} import * verdeckt Prozessaufrufe"))
                else:
                    funcs[a.asname or a.name] = (node.module, a.name)
    calls, call_funcs = [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            kind = _spawn_kind(node.func, modules, funcs)
            if kind:
                calls.append((node, kind))
                call_funcs.add(id(node.func))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Attribute, ast.Name)) and id(node) not in call_funcs and _spawn_kind(node, modules, funcs):
            problems.append((node.lineno, "Prozessfunktion wird als Wert weitergereicht und ist nicht als Aufruf prüfbar"))
    return calls, problems


def spawn_sites(source: str) -> list:
    """Alle subprocess-Aufrufe (run, Popen, ...) eines Quelltexts als ast.Call-Knoten."""
    calls, _ = _spawn_calls(ast.parse(source))
    return [c for c, kind in calls if kind[0] == "subprocess" and kind[1] in SPAWN_FUNCS]


def _violations(source: str, filename: str = "<quelle>") -> list:
    tree = ast.parse(source, filename=filename)
    calls, problems = _spawn_calls(tree)
    stores, plain, params, root_line = {}, {}, set(), None
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            stores[node.id] = stores.get(node.id, 0) + 1
        elif isinstance(node, ast.arg):
            params.add(node.arg)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    plain.setdefault(target.id, []).append(node.value)
    for stmt in tree.body:  # die einzige zugelassene Quelle von ENV: `ENV, HOME = process_env(...)` auf Modulebene
        if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Tuple)
                and stmt.targets[0].elts and isinstance(stmt.targets[0].elts[0], ast.Name)
                and stmt.targets[0].elts[0].id == "ENV" and isinstance(stmt.value, ast.Call)
                and ((isinstance(stmt.value.func, ast.Name) and stmt.value.func.id == "process_env")
                     or (isinstance(stmt.value.func, ast.Attribute) and stmt.value.func.attr == "process_env"))):
            root_line = stmt.lineno
    if root_line is None or stores.get("ENV", 0) != 1 or "ENV" in params:
        problems.append((root_line or 1, "ENV stammt nicht allein aus `ENV, HOME = process_env(...)` auf Modulebene"))

    def derived(node, seen=()) -> bool:
        if isinstance(node, ast.Name):
            if node.id == "ENV":
                return True  # seine Herkunft wird oben einmal geprüft
            if node.id in seen or node.id in params or len(plain.get(node.id, [])) != stores.get(node.id, 0):
                return False
            values = plain.get(node.id, [])
            return bool(values) and all(derived(v, seen + (node.id,)) for v in values)
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "dict":
                return (bool(node.args) and all(derived(a, seen) for a in node.args)
                        and all(k.arg is not None or derived(k.value, seen) for k in node.keywords))
            if isinstance(f, ast.Attribute) and f.attr == "copy" and not node.args and not node.keywords:
                return derived(f.value, seen)
            return False
        if isinstance(node, ast.Dict):
            unpacked = [v for k, v in zip(node.keys, node.values) if k is None]
            return bool(unpacked) and all(derived(v, seen) for v in unpacked)
        return False

    for call, kind in calls:
        if _is_blind(kind):
            problems.append((call.lineno, f"{kind[0]}.{kind[1]} erbt die Umgebung und lässt kein env= zu"))
            continue
        env_kws = [k for k in call.keywords if k.arg == "env"]
        if not env_kws:
            hint = " (**kwargs kann es verstecken)" if any(k.arg is None for k in call.keywords) else ""
            problems.append((call.lineno, f"subprocess.{kind[1]} ohne env={hint}"))
        elif not derived(env_kws[0].value):
            problems.append((call.lineno,
                             f"subprocess.{kind[1]}: env={ast.unparse(env_kws[0].value)} ist nicht aus ENV abgeleitet"))
    return sorted(problems)


def subprocess_env_violations(path: str) -> list:
    """Verstöße gegen die env-Pflicht in der Python-Datei `path`: [(Zeile, Text)], leer = in Ordnung.

    Jeder subprocess-Aufruf (run, Popen, check_output, check_call, call, auch über Aliase) braucht env=, das aus ENV
    abgeleitet ist (ENV, dict(ENV, ...), {**ENV, ...}, ENV.copy() oder eine Variable, die ausschließlich daraus
    gebildet wird). ENV selbst muss aus `ENV, HOME = process_env(...)` auf Modulebene stammen. os.system, os.popen,
    subprocess.getoutput u. ä. erben die Umgebung und sind verboten."""
    with open(path, "rb") as fh:
        source = fh.read().decode("utf-8-sig")
    return _violations(source, path)


def splice(source: str, node, text: str) -> str:
    """Ersetzt den Quelltext von `node` (ast-Position in UTF-8-Bytes) durch `text`; sonst bleibt alles byte-gleich."""
    data = source.encode("utf-8")
    starts, pos = [], 0
    for line in data.splitlines(keepends=True):
        starts.append(pos)
        pos += len(line)
    a = starts[node.lineno - 1] + node.col_offset
    b = starts[node.end_lineno - 1] + node.end_col_offset
    return (data[:a] + text.encode("utf-8") + data[b:]).decode("utf-8")


PROBE_DIR = tempfile.mkdtemp(prefix=f"isohome-ast-{os.getpid()}-")  # nur Quelltext-Kopien, nie ausgeführt
try:
    def violations_of(source: str) -> list:
        path = os.path.join(PROBE_DIR, "probe.py")
        with open(path, "wb") as fh:
            fh.write(source.encode("utf-8"))
        return subprocess_env_violations(path)

    HEAD = "import os\nimport subprocess\nfrom _isolated_home import process_env\n\nENV, HOME = process_env()\n"
    NOPE = "import os\nimport subprocess\nENV = dict(os.environ)\n"
    CASES = [
        # (Name, Quelltext, erwartete Zahl Verstöße)
        ("env=ENV", HEAD + "subprocess.run(['x'], env=ENV)\n", 0),
        ("env=dict(ENV, ...)", HEAD + "subprocess.run(['x'], env=dict(ENV, A='1'))\n", 0),
        ("env={**ENV, ...}", HEAD + "subprocess.Popen(['x'], env={**ENV, 'A': '1'})\n", 0),
        ("env=ENV.copy()", HEAD + "subprocess.check_output(['x'], env=ENV.copy())\n", 0),
        ("Variable aus ENV", HEAD + "e = dict(ENV, A='1')\nsubprocess.check_call(['x'], env=e)\n", 0),
        ("Kette von Variablen aus ENV", HEAD + "a = dict(ENV)\nb = dict(a, X='1')\nsubprocess.call(['x'], env=b)\n", 0),
        ("Alias import subprocess as sp", HEAD + "import subprocess as sp\nsp.run(['x'], env=ENV)\n", 0),
        ("from subprocess import run as r", HEAD + "from subprocess import run as r\nr(['x'], env=ENV)\n", 0),
        ("kein Subprozess", HEAD + "print('x')\n", 0),
        ("kein env=", HEAD + "subprocess.run(['x'])\n", 1),
        ("alle fünf Funktionen ohne env=",
         HEAD + "subprocess.run(['x'])\nsubprocess.Popen(['x'])\nsubprocess.check_output(['x'])\n"
                "subprocess.check_call(['x'])\nsubprocess.call(['x'])\n", 5),
        ("env=os.environ", HEAD + "subprocess.run(['x'], env=os.environ)\n", 1),
        ("env=dict(os.environ)", HEAD + "subprocess.run(['x'], env=dict(os.environ))\n", 1),
        ("env=None", HEAD + "subprocess.run(['x'], env=None)\n", 1),
        ("env={} (leer)", HEAD + "subprocess.run(['x'], env={})\n", 1),
        ("env=dict(PATH=...) ohne ENV", HEAD + "subprocess.run(['x'], env=dict(PATH='x'))\n", 1),
        ("Variable aus os.environ", HEAD + "e = dict(os.environ)\nsubprocess.run(['x'], env=e)\n", 1),
        ("Variable zweimal gebunden, einmal nicht aus ENV",
         HEAD + "e = dict(ENV)\ne = dict(os.environ)\nsubprocess.run(['x'], env=e)\n", 1),
        ("Funktionsparameter als env", HEAD + "def f(env):\n    subprocess.run(['x'], env=env)\n", 1),
        ("**kwargs statt env=", HEAD + "kw = {}\nsubprocess.run(['x'], **kw)\n", 1),
        ("{**ENV, **os.environ}", HEAD + "subprocess.run(['x'], env={**ENV, **os.environ})\n", 1),
        ("dict(ENV, **os.environ)", HEAD + "subprocess.run(['x'], env=dict(ENV, **os.environ))\n", 1),
        ("Alias ohne env", HEAD + "import subprocess as sp\nsp.run(['x'])\n", 1),
        ("from-Import ohne env", HEAD + "from subprocess import Popen\nPopen(['x'])\n", 1),
        ("from subprocess import *", HEAD + "from subprocess import *\n", 1),
        ("Funktion als Wert weitergereicht", HEAD + "r = subprocess.run\nr(['x'])\n", 1),
        ("os.system", HEAD + "os.system('x')\n", 1),
        ("os.popen", HEAD + "os.popen('x')\n", 1),
        ("subprocess.getoutput", HEAD + "subprocess.getoutput('x')\n", 1),
        ("ENV = dict(os.environ) statt process_env", NOPE + "subprocess.run(['x'], env=ENV)\n", 1),
        ("ENV zusätzlich neu gebunden", HEAD + "ENV = dict(os.environ)\nsubprocess.run(['x'], env=ENV)\n", 1),
        ("ENV nirgends gebunden", "import subprocess\nsubprocess.run(['x'], env=ENV)\n", 1),
    ]
    for name, source, expected in CASES:
        got = violations_of(source)
        check(len(got) == expected, f"Pflicht-Prüfung an Quelltext '{name}': {expected} Verstöße erwartet, {len(got)} gefunden {got}")

    # die echten Dateien: kein Verstoß, und die Prüfung hat tatsächlich Aufrufe gesehen (keine leere Wahrheit)
    for fname in CHECKED_FILES:
        path = os.path.join(TESTS_DIR, fname)
        with open(path, "rb") as fh:
            source = fh.read().decode("utf-8-sig")
        sites = spawn_sites(source)
        found = subprocess_env_violations(path)
        check(len(sites) > 0 and not found,
              f"{fname}: {len(sites)} Subprozess-Aufrufe, alle mit env= aus ENV {found[:3]}")

        # Gegenprobe an Kopien: pro Aufrufstelle einzeln env= entfernen bzw. durch os.environ ersetzen -> genau diese
        # Stelle muss gemeldet werden (die Prüfung ist an jeder Stelle tragend, nicht nur im Mittel)
        if found:  # schon oben als Verstoß gemeldet: Kopien einer roten Datei sind nicht aussagekräftig
            continue
        missed = []
        for site in sites:
            env_kw = next((k for k in site.keywords if k.arg == "env"), None)
            if env_kw is None:  # schon oben als Verstoß gemeldet, es gibt nichts zu mutieren
                continue
            for label, mutated in (("env= entfernt", splice(source, env_kw, "bufsize=-1")),
                                   ("env=os.environ", splice(source, env_kw.value, "os.environ"))):
                got = violations_of(mutated)
                if not (len(got) == 1 and got[0][0] == site.lineno):
                    missed.append((site.lineno, label, got[:2]))
        check(not missed, f"{fname}: Gegenprobe an Kopien, jede der {len(sites)} Aufrufstellen wird bei entferntem "
                          f"env= und bei env=os.environ einzeln gemeldet {missed[:3]}")
finally:
    shutil.rmtree(PROBE_DIR, ignore_errors=True)

print(f"=== Results: {PASSED}/{TESTS} passed, {ERRORS} failures ===")
sys.exit(1 if ERRORS else 0)

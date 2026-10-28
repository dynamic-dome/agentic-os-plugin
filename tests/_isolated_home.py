"""Gemeinsamer Testhelfer: Subprozess-Umgebung mit frischem Temp-Home (P0, Messgrundlage).

Warum: Ein Test, der einen Plugin-Skript-Subprozess mit der geerbten Umgebung startet, sieht das echte Home. Jeder
Pfad, der mit `~` beginnt (Zentral-Handoff `~/AI`, Zentralprofil `~/.agent-memory/identity`, Snapshots
`~/.agentic-os`), landet dann außerhalb des Temp-Stores im echten Bestand. Dieser Helfer baut die Umgebung so, dass
nichts davon möglich ist:

- HOME und USERPROFILE zeigen auf ein frisches Temp-Home. Unter Windows richtet sich `os.path.expanduser` nur nach
  USERPROFILE, Git Bash nach HOME, deshalb braucht es beide.
- AGENTIC_OS_SNAPSHOT_DIR und AGENTIC_OS_IDENTITY_STORE zeigen auf Unterordner dieses Homes. Der Snapshot-Ordner wird
  angelegt (so war es bisher in den Tests), der Identitäts-Ordner nicht: ihn legt erst der Code an, der ihn schreibt,
  damit ein Test "es wurde nichts ins Zentral geschrieben" an seiner Abwesenheit festmachen kann.
- Sitzungsvariablen (CLAUDE_PROJECT_DIR, CLAUDE_PLUGIN_ROOT, CLAUDE_ENV_FILE, CLAUDE_MEMORY_GLOBAL, BASH_ENV, ENV)
  werden entfernt. Sie zeigen auf echte Orte, und BASH_ENV/ENV würden bash vor dem Test ein fremdes Skript laden lassen.
- Weitere geerbte Variablen werden ebenfalls entfernt: CLAUDE_CODE_SESSION_ID, CLAUDE_PLUGIN_DATA,
  NIGHTLY_CONSOLIDATION_REPORT und jede AGENTIC_OS_* außer den beiden gesetzten (AGENTIC_OS_MEMORY_DIR zeigt in einer
  Sitzung auf den echten Store, AGENTIC_OS_CENTRAL_HANDOFF auf das echte Zentral). Ein ausdrücklicher Override des
  Aufrufers (`**overrides`) wird zuletzt gesetzt und bleibt erhalten.

Das Home liegt im Wurzelordner des Laufwerks, nicht unter %TEMP%: %TEMP% liegt auf dieser Maschine unter einem Store
(`.agent-memory`), und ein Test, der "außerhalb jedes Stores" braucht, dürfte dort nie landen. Es gibt keinen stillen
Rückfall: Ist der Wurzelordner nicht beschreibbar, bricht make_home mit IsolationError ab. Ebenso bei jedem Vorfahren
des Basisordners, der einen Ordner `.agent-memory` enthält (oder selbst einer ist); geprüft wird vor dem Anlegen, damit
bei einer Ablehnung nichts entsteht. Unter POSIX gibt es keinen Laufwerks-Wurzelordner mit Schreibrecht, dort ist der
Temp-Ordner die Basis (auch dort gilt die Vorfahren-Sperre). Aufgeräumt wird immer (finally bzw. atexit). Alles wird
erst beim Aufruf aufgelöst, es gibt keine Modulwerte mit Zustand.

Benutzung in einem Test mit modulweitem ENV (Skript endet mit sys.exit, atexit räumt auf):
    from _isolated_home import process_env
    ENV, HOME = process_env(PYTHONIOENCODING="utf-8")

Benutzung mit begrenzter Lebensdauer:
    from _isolated_home import isolated_env
    with isolated_env() as (env, home):
        subprocess.run([...], env=env)
"""

from __future__ import annotations

import atexit
import contextlib
import os
import shutil
import sys
import tempfile

# Namen statt Werte: die Pfade entstehen erst beim Aufruf
HOME_VARS = ("HOME", "USERPROFILE")
SNAPSHOT_VAR = "AGENTIC_OS_SNAPSHOT_DIR"
IDENTITY_VAR = "AGENTIC_OS_IDENTITY_STORE"
SESSION_VARS = ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT", "CLAUDE_ENV_FILE", "CLAUDE_MEMORY_GLOBAL",
                "BASH_ENV", "ENV")
# Außerdem entfernt: weitere geerbte Variablen, die auf echte Orte oder die laufende Sitzung zeigen
INHERITED_VARS = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PLUGIN_DATA", "NIGHTLY_CONSOLIDATION_REPORT")
INHERITED_PREFIX = "AGENTIC_OS_"  # alle bis auf die beiden unten gesetzten (SNAPSHOT_VAR, IDENTITY_VAR)
STORE_DIR_NAME = ".agent-memory"


class IsolationError(RuntimeError):
    """Die Isolation lässt sich nicht herstellen: der Test darf nicht weiterlaufen (und nicht ausweichen)."""


def _same_name(a: str, b: str) -> bool:
    # Windows kennt Umgebungsvariablen ohne Groß-/Kleinschreibung, POSIX mit
    return a.upper() == b.upper() if os.name == "nt" else a == b


def _has_prefix(key: str, prefix: str) -> bool:
    return key.upper().startswith(prefix.upper()) if os.name == "nt" else key.startswith(prefix)


def _drop(env: dict, name: str) -> None:
    for key in [k for k in env if _same_name(k, name)]:
        del env[key]


def _set(env: dict, name: str, value: str) -> None:
    _drop(env, name)  # sonst bliebe unter Windows ein zweiter Eintrag in anderer Schreibweise stehen
    env[name] = value


def _base_dir() -> str:
    """Ort des Homes: der Wurzelordner des Laufwerks, auf dem %TEMP% liegt (dort liegt kein Store). Unter POSIX gibt
    es kein Laufwerk, dort ist es der Temp-Ordner. Genau ein Kandidat, kein Rückfall."""
    temp = os.path.realpath(tempfile.gettempdir())
    drive = os.path.splitdrive(temp)[0]
    return drive + os.sep if drive else temp


def _store_ancestor(path: str):
    """Der erste Ordner von `path` aufwärts (path eingeschlossen), der selbst `.agent-memory` heißt oder einen Ordner
    `.agent-memory` enthält, sonst None. Eine Datei dieses Namens zählt nicht."""
    current = os.path.realpath(path)
    while True:
        if (os.path.normcase(os.path.basename(current)) == os.path.normcase(STORE_DIR_NAME)
                or os.path.isdir(os.path.join(current, STORE_DIR_NAME))):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def make_home(prefix: str = "aos-testhome-") -> str:
    """Legt ein frisches Temp-Home an und gibt den Pfad zurück. Der Aufrufer räumt mit remove_home() auf.

    Bricht mit IsolationError ab (und legt dann nichts an), wenn ein Vorfahr des Basisordners einen Ordner
    `.agent-memory` enthält oder der Basisordner nicht beschreibbar ist. Es gibt keinen Ausweichort."""
    base = _base_dir()
    store = _store_ancestor(base)
    if store:  # harte Sperre: ein Home unter einem Store wäre selbst ein Leck
        raise IsolationError(f"Temp-Home läge unter einem {STORE_DIR_NAME}: {store} ist oder enthält einen "
                             f"(Basisordner {base})")
    try:
        return tempfile.mkdtemp(prefix=prefix, dir=base)
    except OSError as exc:
        raise IsolationError(f"Basisordner {base} nicht beschreibbar ({exc}); bewusst kein Rückfall auf den "
                             f"Temp-Ordner, weil der unter einem {STORE_DIR_NAME} liegen kann") from exc


def remove_home(home: str) -> None:
    shutil.rmtree(home, ignore_errors=True)
    if os.path.exists(home):  # nicht still schlucken: ein Rest wäre ein Leck in der Ablage
        print(f"WARNUNG: Temp-Home nicht vollständig entfernt: {home}", file=sys.stderr)


def build_env(home: str, base_env: dict | None = None, **overrides: str) -> dict:
    """Kopie von base_env (Default os.environ) mit Home, Zentral-Variablen und ohne Sitzungsvariablen.

    `overrides` werden zuletzt gesetzt (z. B. PYTHONIOENCODING); base_env selbst bleibt unverändert."""
    env = dict(os.environ if base_env is None else base_env)
    for name in SESSION_VARS + INHERITED_VARS:
        _drop(env, name)
    for key in [k for k in env if _has_prefix(k, INHERITED_PREFIX)]:
        del env[key]
    for name in HOME_VARS:
        _set(env, name, home)
    _set(env, SNAPSHOT_VAR, os.path.join(home, "snapshots"))
    _set(env, IDENTITY_VAR, os.path.join(home, "identity-store"))
    os.makedirs(env[SNAPSHOT_VAR], exist_ok=True)
    for name, value in overrides.items():
        _set(env, name, value)
    return env


@contextlib.contextmanager
def isolated_env(base_env: dict | None = None, **overrides: str):
    """Kontextmanager: liefert (env, home) und entfernt das Home im finally, auch nach einer Ausnahme."""
    home = make_home()
    try:
        yield build_env(home, base_env, **overrides), home
    finally:
        remove_home(home)


def process_env(base_env: dict | None = None, **overrides: str) -> tuple[dict, str]:
    """Wie isolated_env, aber für die ganze Lebensdauer des Testprozesses: räumt per atexit auf.

    Für Testdateien, die ihr ENV auf Modulebene brauchen. atexit läuft auch bei sys.exit()."""
    home = make_home()
    atexit.register(remove_home, home)
    return build_env(home, base_env, **overrides), home

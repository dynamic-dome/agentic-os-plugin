#!/usr/bin/env python3
"""identity_store.py — where the user profile lives: per project (legacy) or central (P2, Release A).

WHY: dozens of project stores each grew their own `identity/user.md`; one and the same statement sits in many of
them in as many wordings, and a candidate id like UC1 exists in most stores. P2 moves the profile into ONE central namespace
`identity/profile.*` with global ids (prefix PC) and an `origin` per row.

Contract:
  - The central store is resolved only when a function is called, never on import, in this order:
    `--identity-store`, then the variable named by ENV_VAR, then the home store. Tests and the release isolation
    rely on that: a module-level path would point at the real home before a test can redirect it.
  - The central store is ACTIVE exactly when `identity/profile.md` exists. The migration writes that file last, so
    a half-finished migration stays inactive, and renaming the file is the way back.
  - Guard (E10): a store under the system temp directory never writes into a central store that was found only via
    the home default - that is a test or a probe that forgot `--identity-store`. Explicit sources (cli, env) pass.
  - Two paths name the same store when realpath + normcase agree.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass

ENV_VAR = "AGENTIC_OS_IDENTITY_STORE"
PROFILE_MD = "identity/profile.md"
PROFILE_CHANGELOG = "identity/profile-changelog.json"
PROFILE_QUEUE = "identity/profile-candidates.json"
PROFILE_LOCK = "identity/profile.lock"
BRIEFING_LINES = "identity/briefing-lines.txt"      # projection of a PROJECT profile (user.md)
PROFILE_BRIEFING = "identity/profile-briefing.txt"  # projection of the CENTRAL profile - the home store is both a
                                                    # project and the central store, so the names must differ
MIGRATION_LEDGER = "identity/profile-migration-ledger.json"
STORE_DIR_NAME = ".agent-memory"


@dataclass(frozen=True)
class IdentityPaths:
    """Where one run reads and writes identity data. `md`, `changelog` and `queue` are relative to `root`."""
    root: str
    md: str
    changelog: str
    queue: str
    id_prefix: str
    central: bool
    briefing: str = BRIEFING_LINES


class GuardError(Exception):
    """The run would write into a central store it must not touch; nothing has been written."""


def resolve_store(cli: str | None, env: Mapping[str, str]) -> tuple[str, str]:
    """(absolute path of the central store, source) with source "cli" | "env" | "home"."""
    if cli:
        return os.path.abspath(cli), "cli"
    if env.get(ENV_VAR):
        return os.path.abspath(env[ENV_VAR]), "env"
    return os.path.join(os.path.expanduser("~"), STORE_DIR_NAME), "home"


def central_active(store: str) -> bool:
    return os.path.isfile(os.path.join(store, PROFILE_MD))


def _canon(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def same_store(a: str, b: str) -> bool:
    return _canon(a) == _canon(b)


def _under(path: str, root: str) -> bool:
    p, r = _canon(path), _canon(root)
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def legacy_paths(mem: str) -> IdentityPaths:
    return IdentityPaths(mem, "identity/user.md", "identity/user-changelog.json", "working/user-candidates.json",
                         "UC", False, BRIEFING_LINES)


def central_paths(store: str) -> IdentityPaths:
    return IdentityPaths(store, PROFILE_MD, PROFILE_CHANGELOG, PROFILE_QUEUE, "PC", True, PROFILE_BRIEFING)


def select_paths(mem: str, cli: str | None, env: Mapping[str, str]) -> IdentityPaths:
    """Central paths when the central store is active, else the project's own files. Raises GuardError when a temp
    store would write into a central store found only via the home default."""
    store, source = resolve_store(cli, env)
    if not central_active(store):
        return legacy_paths(mem)
    if source == "home" and _under(mem, tempfile.gettempdir()):
        raise GuardError(f"store {mem} lies in the temp directory and the central profile {store} was found only via "
                         f"the home directory - pass --identity-store (or {ENV_VAR}) to name the central store "
                         f"explicitly; nothing was written")
    return central_paths(store)


def project_key(mem: str, home: str) -> str:
    """Origin key of a store: its project folder relative to `home` with "/", "~" for the home store itself, an
    absolute path for a store outside home."""
    parent = os.path.dirname(os.path.abspath(mem).rstrip("\\/"))
    if same_store(parent, home):
        return "~"
    if _under(parent, home):
        return os.path.relpath(os.path.realpath(parent), os.path.realpath(home)).replace(os.sep, "/")
    return os.path.normpath(parent)

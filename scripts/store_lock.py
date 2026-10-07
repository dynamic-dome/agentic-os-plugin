#!/usr/bin/env python3
"""store_lock.py — one lock per memory store for every mutating script (5.3.0).

WHY: the writers load a store file, change it in memory and write it back. Two
of them at once (a wrap-up and the nightly run, or apply_wrapup and
extract_patterns) lose one side's rows - a lost update, no error anywhere
(plan phase 1, red-team finding #7). The lock covers the whole store, not
single files, because one run reads several files and decides on all of them.

Contract:
  - `working/store.lock` is held through an OPERATING-SYSTEM lock on the open
    file (msvcrt.locking on Windows, fcntl.flock elsewhere). The OS releases it
    when the holder exits or crashes, so there is no stale-lock logic and the
    file is never deleted. (The first version used an O_EXCL file plus "break
    if older than 30 s"; Codex verifier round 2 showed a rename/delete race in
    which two writers could enter at once - with an OS lock that race cannot
    exist.)
  - Held only from the first read to the last write of ONE run - never nested,
    never across a subprocess call (the child would wait for its own parent).
  - Waiting longer than `timeout` raises LockTimeout. It is an OSError on
    purpose: every writer already reports OSError as "io error", exit 2, no
    marker - a busy store is a visible failure, never a silent skip.
"""

from __future__ import annotations

import contextlib
import os
import time

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None
    import fcntl

TIMEOUT_SECONDS = 10.0
LOCK_REL = os.path.join("working", "store.lock")


class LockTimeout(OSError):
    """The store stayed locked longer than the timeout."""


def _try_lock(fd: int) -> bool:
    try:
        if msvcrt is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    with contextlib.suppress(OSError):
        if msvcrt is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def store_lock(mem: str, timeout: float = TIMEOUT_SECONDS):
    path = os.path.join(mem, LOCK_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.time() + timeout
        while not _try_lock(fd):
            if time.time() >= deadline:
                raise LockTimeout(f"store lock {path} held by another process for more than {timeout:g}s")
            time.sleep(0.05)
        try:
            yield path
        finally:
            _unlock(fd)
    finally:
        os.close(fd)

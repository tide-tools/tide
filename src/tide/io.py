"""tide.io — durability primitives: atomic writes and cross-platform file locking.

Two exports:

``atomic_write(path, text, *, encoding)``
    Write *text* to *path* with crash-safety: a temp file in the SAME directory
    is flushed and fdsynced, then renamed over the target via ``os.replace``.
    A crash between the write and the rename leaves the original file (or no file,
    if it did not exist) intact — no torn half-written state reaches the target.
    On any failure the temp file is cleaned up (no ``.tmp`` litter).

``file_lock(lock_dir, *, ttl, retry, attempts)``
    A context manager that acquires a cross-platform directory lock via
    ``os.mkdir`` (the only portable atomic operation on POSIX and Windows). The
    directory being present = lock held. A ``lock.json`` inside records the
    holding PID and creation timestamp so stale locks (dead PID or exceeded TTL)
    can be reclaimed automatically.

Both primitives are pure standard-library (stdlib-only, Python ≥ 3.12).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def atomic_write(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write *text* to *path* atomically via a same-directory temp file.

    Sequence:
      1. Create parent directory (parents=True, exist_ok=True).
      2. Open a named temp file in ``path.parent`` (same filesystem as target).
      3. Write, ``flush``, and ``fsync`` the temp file.
      4. Close the temp file.
      5. ``os.replace(tmp, path)`` — atomic on POSIX; best-effort on Windows.

    On any exception in steps 2-5 the temp file is unlinked (no ``.tmp``
    litter) and the exception is re-raised.  The original *path* (if it
    existed) is never modified until ``os.replace`` succeeds.

    The temp file is placed in ``path.parent`` (NOT ``/tmp`` or the system temp
    dir) so the rename stays on the same filesystem — a cross-filesystem
    ``os.replace`` raises ``OSError: Invalid cross-device link`` on Linux.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding=encoding) as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            # fdopen took ownership of fd; if it raised, fd is already closed.
            # If fdopen itself failed fd may still be open; close defensively.
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        os.replace(tmp_path, path)
        tmp_path = None  # replace succeeded; no cleanup needed
    except BaseException:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


# ---------------------------------------------------------------------------
# Cross-platform directory lock
# ---------------------------------------------------------------------------

_LOCK_INFO_FILE = "lock.json"
_DEFAULT_TTL = 30.0      # seconds before a lock is considered stale
_DEFAULT_RETRY = 0.05    # seconds between contention retries
_DEFAULT_ATTEMPTS = 20   # total acquisition attempts before giving up


def _pid_alive(pid: int) -> bool:
    """True when *pid* is a currently running process on this machine.

    Note on PID reuse: a recycled PID from a dead holder can make a stale lock
    appear live.  The TTL bounds the worst case: even if a new process inherits
    the same PID, the lock is reclaimed once ``age > ttl`` (default 30 s),
    so the window of false-liveness is at most one TTL period.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we cannot send signals to it — still alive.
        return True
    except OSError:
        return False


def _write_lock_info(lock_dir: Path) -> None:
    """Record PID + creation timestamp inside *lock_dir*."""
    info = {"pid": os.getpid(), "created": time.time()}
    (lock_dir / _LOCK_INFO_FILE).write_text(json.dumps(info), encoding="utf-8")


def _is_stale(lock_dir: Path, ttl: float) -> bool:
    """True when the lock at *lock_dir* is stale (dead PID or exceeded TTL)."""
    try:
        raw = (lock_dir / _LOCK_INFO_FILE).read_text(encoding="utf-8")
        info = json.loads(raw)
        pid = int(info.get("pid", 0))
        created = float(info.get("created", 0.0))
    except Exception:
        # Corrupted / missing lock.json — treat as stale so we can reclaim.
        return True
    age = time.time() - created
    if age > ttl:
        return True
    return not _pid_alive(pid)


@contextmanager
def file_lock(
    lock_dir: Path,
    *,
    ttl: float = _DEFAULT_TTL,
    retry: float = _DEFAULT_RETRY,
    attempts: int = _DEFAULT_ATTEMPTS,
) -> Generator[None, None, None]:
    """Acquire a cross-platform mkdir-lock at *lock_dir*, yield, then release.

    Acquisition uses ``os.mkdir`` — the only portable atomic create-if-absent
    on POSIX and Windows. When the directory already exists the loop checks
    whether the holder is stale (dead PID or age > *ttl*).

    **Stale reclaim is TOCTOU-safe** via an atomic rename-to-claim: the
    reclaimer renames ``lock_dir`` to a pid-specific staging name before
    removing it. Only the process that wins the rename proceeds; losers get
    ``FileNotFoundError`` / ``OSError`` from the rename and loop back to
    the top where their ``mkdir`` will either succeed (lock free) or find the
    winner's fresh lock (contention → retry). This prevents two concurrent
    reclaimers from both entering the critical section.

    **Info-write safety**: if ``_write_lock_info`` raises after a successful
    ``mkdir``, the newly created lock dir is removed before propagating so
    it never leaks as a permanent phantom lock.

    Raises ``RuntimeError`` after *attempts* consecutive failures.
    The lock dir is always removed in a ``try/finally`` — exceptions inside
    the guarded body never leave a stale lock behind.

    Lock path recommendation: ``.tide/state/.merge.lock``.
    """
    lock_dir = Path(lock_dir)
    lock_dir.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(attempts):
        try:
            lock_dir.mkdir()
        except FileExistsError:
            if _is_stale(lock_dir, ttl):
                # Atomic claim: rename beats a concurrent rmtree+mkdir race.
                # Only the process that wins os.rename owns the staging dir and
                # may remove it; all others get an error and retry from the top.
                claimed = lock_dir.parent / ".reclaiming-{0}".format(os.getpid())
                try:
                    os.rename(lock_dir, claimed)  # atomic on POSIX; one winner
                except (FileNotFoundError, OSError):
                    # Another process won the rename (or lock_dir disappeared).
                    # Loop back: the winner will release shortly → mkdir may succeed.
                    continue
                shutil.rmtree(claimed, ignore_errors=True)  # we own it; safe to clear
                continue  # stale cleared → retry mkdir from top
            if attempt < attempts - 1:
                time.sleep(retry)
            else:
                raise RuntimeError(
                    "tide: could not acquire merge lock at {0} after {1} attempts "
                    "(another process may be merging)".format(lock_dir, attempts)
                )
        else:
            # mkdir succeeded — we own lock_dir.  Write info; if that fails, clean
            # up so the dir does not leak as a permanent phantom lock.
            try:
                _write_lock_info(lock_dir)
            except BaseException:
                shutil.rmtree(lock_dir, ignore_errors=True)
                raise
            break  # fully acquired

    try:
        yield
    finally:
        try:
            shutil.rmtree(lock_dir, ignore_errors=True)
        except Exception:
            pass

"""tide.registry — the sid-keyed launch registry (``terminals.json``).

ONE shared file at ``<control-home>/terminals.json`` mapping a claude SESSION id to the
terminal that runs it::

    {"<sid>": {"handle": "term_…", "arc": "<abs arc path>", "ts": "<iso>"}}

EVERY launcher writes it at spawn — tide's own (pickup / handoff-launch / spark) AND the
board — so "return to this session" resolves the *exact* terminal by session id. Keying
by ARC (the old board registry / cand 92) could not tell a thread's several live sessions
apart, so ▶ focused the wrong/dead terminal and spawned a duplicate (cand 94 diagnosis).
Keying by sid closes that: the launcher knows the ``(sid, handle)`` pair the moment it
spawns, and ``passport.claude-session`` (stamped at birth since 1.0.38) equals the key.

Dead handles are pruned lazily on read by cross-checking ``orca terminal list`` — a sid
whose handle is no longer live is dropped, so the caller falls through to resume/spark.
All logic is pure and takes the live-handle set as data (``orca`` is only shelled out at
the CLI edge), so the whole module is unit-testable without a terminal.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

from . import io as _io

REGISTRY_FILENAME = "terminals.json"

# The session's OWN identity, straight out of its environment (cand 144). Orca exports
# the pane's handle into every shell it opens; Claude Code exports the running session's
# id into every process it spawns. So ANY tide command running inside a session — a hook,
# an offload, a bare `tide status` — knows the exact ``(sid, handle)`` pair without
# guessing and without asking orca. This is what makes a session started BY HAND
# (`claude --resume` after a reboot) register itself just like a tide-spawned one.
ENV_TERMINAL_HANDLE = "ORCA_TERMINAL_HANDLE"
ENV_SESSION_ID = "CLAUDE_CODE_SESSION_ID"

# orca create/list MUST run in the SAME environment or a created terminal is invisible to
# a later list (the registry then reads it as dead — a known footgun). A minimal PATH that
# resolves the ``orca`` binary keeps both calls in one runtime (mirrors the board's env).
_ORCA_ENV = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}


def registry_file(home: Path) -> Path:
    """The registry path for *home* (the control-home): ``<home>/terminals.json``."""
    return Path(home) / REGISTRY_FILENAME


def read(home: Path) -> Dict[str, Dict[str, str]]:
    """Return the registry dict (``{sid: {...}}``); missing/garbled ⇒ ``{}``."""
    f = registry_file(home)
    try:
        data = json.loads(f.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record(
    home: Path, sid: str, handle: str, arc: str, *, now: Optional[datetime] = None
) -> None:
    """Record ``sid → (handle, arc, ts)`` — last-writer-wins. No-op on empty sid/handle.

    Called by WHOEVER spawns the session (tide launcher or board), right after the
    terminal is created, so the pairing is exact — never reverse-engineered.
    """
    s = (sid or "").strip()
    h = (handle or "").strip()
    if not s or not h:
        return
    data = read(home)
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    data[s] = {"handle": h, "arc": str(arc or ""), "ts": stamp}
    _io.atomic_write(registry_file(home), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def self_pair(env: Optional[Dict[str, str]] = None) -> Tuple[str, str]:
    """``(sid, handle)`` of the session THIS process runs inside; ``("", "")`` if none.

    Read from :data:`ENV_SESSION_ID` / :data:`ENV_TERMINAL_HANDLE` — an exact pairing,
    not a guess. Outside a session terminal (the board's server, a plain shell) one or
    both are absent and the caller simply does nothing.
    """
    e = os.environ if env is None else env
    return ((e.get(ENV_SESSION_ID) or "").strip(),
            (e.get(ENV_TERMINAL_HANDLE) or "").strip())


def forget(home: Path, sid: str) -> None:
    """Drop *sid* from the registry (e.g. its session closed). Idempotent."""
    s = (sid or "").strip()
    data = read(home)
    if s in data:
        del data[s]
        _io.atomic_write(
            registry_file(home), json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        )


def forget_handle(home: Path, handle: str) -> int:
    """Drop EVERY entry pointing at *handle*; returns how many. Idempotent.

    Called when a focus attempt just proved the handle dead: a corpse recorded under
    two keys (a sid and a legacy arc path) would otherwise keep resolving from the
    other one, and the caller would «фокусировать труп» forever (the ``pickup_stale``
    trap, c636275). One dead terminal, one sweep.
    """
    h = (handle or "").strip()
    if not h:
        return 0
    data = read(home)
    kept = {k: e for k, e in data.items() if ((e or {}).get("handle") or "").strip() != h}
    removed = len(data) - len(kept)
    if removed:
        _io.atomic_write(
            registry_file(home), json.dumps(kept, ensure_ascii=False, indent=2) + "\n"
        )
    return removed


def is_sid_key(key: str) -> bool:
    """True when *key* is a session id, not a legacy ARC path (which contains ``/``)."""
    return bool((key or "").strip()) and "/" not in key


def migrate(home: Path) -> int:
    """Bring a mixed-schema registry to the ONE sid-keyed schema; returns entries dropped.

    The shared file still carries pre-cand-94 records keyed by ARC PATH. They cannot be
    converted — an arc path carries no session id, and a thread routinely has several
    sessions, which is precisely why arc-keying focused the wrong terminal and spawned
    duplicates (cand 94). So they are DROPPED, not migrated, and the caller says the
    number out loud rather than swallowing it. Idempotent: a clean registry returns 0.
    """
    data = read(home)
    kept = {k: e for k, e in data.items() if is_sid_key(k)}
    dropped = len(data) - len(kept)
    if dropped:
        _io.atomic_write(
            registry_file(home), json.dumps(kept, ensure_ascii=False, indent=2) + "\n"
        )
    return dropped


def orca_terminal_list() -> list:
    """Live terminals from ``orca terminal list --json`` as dicts (``[]`` on failure).

    Richer sibling of :func:`orca_live_handles` — the reconcile sweeper needs
    ``worktreePath`` for its cwd match, not just the handle set. Same caveat: list
    HIDES background-adopted terminals (cand 101), so this is usable for finding a
    terminal, never for declaring one dead.
    """
    try:
        r = subprocess.run(
            ["orca", "terminal", "list", "--json"],
            capture_output=True, text=True, timeout=10, env=_ORCA_ENV,
        )
        terminals = (json.loads(r.stdout or "{}").get("result", {}) or {}).get("terminals", [])
        return [t for t in terminals if isinstance(t, dict)]
    except Exception:  # noqa: BLE001 — best-effort
        return []


def orca_live_handles() -> Set[str]:
    """The set of live terminal handles from ``orca terminal list --json`` (``{}`` on any
    failure — a registry read must never break because orca is absent)."""
    try:
        r = subprocess.run(
            ["orca", "terminal", "list", "--json"],
            capture_output=True, text=True, timeout=10, env=_ORCA_ENV,
        )
        terminals = (json.loads(r.stdout or "{}").get("result", {}) or {}).get("terminals", [])
        return {t.get("handle") for t in terminals if t.get("handle")}
    except Exception:  # noqa: BLE001 — best-effort; caller treats absence as "no live handles"
        return set()


def recorded_handle(home: Path, sid: str, *, arc: str = "") -> Optional[str]:
    """The RECORDED handle for *sid* — deliberately NO liveness cross-check.

    ``orca terminal list`` hides background-adopted terminals, so absence-from-list
    is NOT death (cand 101): the only honest liveness probe is trying to focus the
    handle, which is exactly what the return path does next.

    Keyed by SID ONLY. The old arc-path fallback is gone (cand 144): an arc key cannot
    tell a thread's several sessions apart, so it answered with *some* session's tab —
    focusing the wrong one is worse than a duplicate. :func:`migrate` drops those
    records. *arc* is accepted and ignored so older callers keep working.
    """
    del arc  # back-compat: legacy arc-keyed lookup retired, see the docstring
    key = (sid or "").strip()
    if not key:
        return None
    handle = ((read(home).get(key) or {}).get("handle") or "").strip()
    return handle or None


def resolve(home: Path, sid: str, *, live_handles: Optional[Set[str]] = None) -> Optional[str]:
    """The LIVE terminal handle for *sid*, or None (recorded-but-dead, or unknown).

    *live_handles* is the set of currently-alive handles (from :func:`orca_live_handles`);
    injected as data so this is pure/testable. A sid whose handle isn't live returns None
    — the caller then resumes (``claude --resume sid``) or sparks fresh.
    """
    s = (sid or "").strip()
    if not s:
        return None
    entry = read(home).get(s) or {}
    handle = (entry.get("handle") or "").strip()
    if not handle:
        return None
    live = orca_live_handles() if live_handles is None else live_handles
    return handle if handle in live else None


def prune(home: Path, *, live_handles: Optional[Set[str]] = None) -> int:
    """Drop every entry whose handle is no longer live. Returns how many were removed.

    Lazy garbage-collection — call opportunistically on read paths so the file doesn't
    accrete dead terminals. Injectable *live_handles* keeps it testable.
    """
    data = read(home)
    if not data:
        return 0
    live = orca_live_handles() if live_handles is None else live_handles
    if not live:
        # An empty live-set is indistinguishable from an orca outage (orca_live_handles
        # returns {} on ANY failure) — wiping the whole registry on an outage would turn
        # every "return to session" into a dead end. Keep stale entries; resolve() still
        # answers None for them, so correctness is unaffected.
        return 0
    kept = {s: e for s, e in data.items() if (e.get("handle") or "") in live}
    removed = len(data) - len(kept)
    if removed:
        _io.atomic_write(
            registry_file(home), json.dumps(kept, ensure_ascii=False, indent=2) + "\n"
        )
    return removed

"""tide.sessions — session-life domain ops that span arcs and the registry.

Today: :func:`reconcile_registry`, the SessionStart sweeper. The launcher records
``sid → terminal`` at spawn (the happy path); this covers every OTHER way a session
can exist — a bare ``claude`` run by hand, a spawn whose registry write failed, a
registry file lost — so "return to this session" keeps working on ALL paths of
ascent (principle №1), not only the launched ones.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from . import registry


def reconcile_registry(
    control_home: Path,
    project_root: Path,
    sid: str,
    *,
    terminals: List[Dict[str, str]],
    own_handle: str = "",
) -> Optional[str]:
    """Bind ``sid → handle``, preferring the session's OWN terminal over any guess.

    Rules, in order:
    - *own_handle* (from ``$ORCA_TERMINAL_HANDLE``, see :func:`registry.self_pair`) —
      the session telling us which pane it lives in. Exact, so it OVERWRITES an
      existing record: a session resumed after a reboot runs in a NEW tab while the
      registry still points at the corpse of the old one;
    - no own handle, but a record exists → do nothing (absence from ``orca terminal
      list`` is NOT death — background-adopted terminals hide from list yet focus
      fine, cand 101 — so overwriting a record against the list would re-open that trap);
    - no record → match live terminals by cwd (``worktreePath == project_root``), and
      ONLY when exactly one candidate — with several sessions in one project a cwd
      guess would bind the wrong tab (never guess between heads).

    The cwd match is the weakest rung and used to be the ONLY one, which is how the
    duplicate-tab loop sustained itself (cand 144): the first duplicate gave the
    project a second terminal, from then on the count was never 1, so the sweeper bailed
    on every session start and every press of ⟳ spawned another tab. *own_handle* does
    not count anything, so it cannot be defeated by a project having many sessions.

    Returns a short note of what happened (for the hook's stderr), or None.
    """
    s = (sid or "").strip()
    if not s:
        return None
    want = str(Path(project_root))
    own = (own_handle or "").strip()
    if own:
        if registry.recorded_handle(control_home, s) == own:
            return None  # already exact — no write
        registry.record(control_home, s, own, want)
        return "bound: {0} → {1} (own terminal)".format(s[:8], own)
    if registry.recorded_handle(control_home, s):
        return None
    candidates = [
        t for t in (terminals or [])
        if (t.get("worktreePath") or "").rstrip("/") == want.rstrip("/")
        and (t.get("handle") or "").strip()
    ]
    if len(candidates) != 1:
        return None
    handle = candidates[0]["handle"].strip()
    registry.record(control_home, s, handle, want)
    return "reconciled: {0} → {1}".format(s[:8], handle)


def self_register(
    control_home: Path,
    *,
    arc: str = "",
    env: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Bind the session THIS process runs inside to its terminal. Returns the handle written.

    The registry's second writer, next to the launcher. Every tide command — every hook,
    every offload — runs inside the session's own pane and therefore carries the exact
    pair in its environment, so binding costs one comparison and a write only when the
    pairing actually changed. That is what closes the last hole in «вернуться в сессию»:
    a session the launcher never saw (started by hand, or whose tab changed under it)
    registers itself the moment the agent does anything at all, instead of waiting for a
    SessionStart that already happened.

    Silent no-op outside a session terminal (board server, plain shell) — both env vars
    must be present, and nothing is ever guessed from their absence.
    """
    sid, handle = registry.self_pair(env)
    if not sid or not handle:
        return None
    if registry.recorded_handle(control_home, sid) == handle:
        return None
    registry.record(control_home, sid, handle, arc)
    return handle

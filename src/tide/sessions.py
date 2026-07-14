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
) -> Optional[str]:
    """Backfill a MISSING ``sid → handle`` registry record; never overwrite one.

    Rules, in order:
    - a recorded handle exists → do nothing (absence from ``orca terminal list`` is
      NOT death — background-adopted terminals hide from list yet focus fine, cand
      101 — so overwriting a record against the list would re-open that trap);
    - no record → match live terminals by cwd (``worktreePath == project_root``),
      and ONLY when exactly one candidate — with several sessions in one project a
      cwd guess would bind the wrong tab (never guess between heads);
    - zero or many candidates → leave it; ``tide return`` falls back to
      ``claude --resume`` and ``tide doctor`` surfaces the drift.

    Returns a short note of what happened (for the hook's stderr), or None.
    """
    s = (sid or "").strip()
    if not s:
        return None
    if registry.recorded_handle(control_home, s):
        return None
    want = str(Path(project_root))
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

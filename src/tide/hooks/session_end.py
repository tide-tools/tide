"""tide.hooks.session_end — SessionEnd: stamp ``ended:`` on the session's passport.

The closing bookend of the session-life mechanics: birth builds the passport floor,
SessionStart links and reconciles, and the END is recorded by the harness too — the
board can tell «conversation finished» from «went quiet» without pulse forensics.
The terminal link is deliberately KEPT (the tab may still exist; ``tide return``
falls back to ``claude --resume`` by itself when it doesn't).

Fully defensive: any hiccup prints nothing and exits 0 — a hook must never break a
session, least of all on its way out.
"""

from __future__ import annotations

import json
import sys
from typing import Optional

from .. import fields, paths
from ..offload import find_session_by_claude_id


def _hook_session() -> Optional[str]:
    """Best-effort session id from the hook's stdin JSON (TTY-guarded)."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return None
        payload = json.loads(sys.stdin.read() or "{}")
        return payload.get("session_id") or payload.get("session")
    except Exception:  # noqa: BLE001 — a hook must never raise
        return None


def cmd_session_end(args) -> int:
    """``tide hook session-end`` — stamp ``ended:`` on this sid's session arc."""
    try:
        root = paths.find_tide_root()
        if root is None:
            return 0
        sid = _hook_session()
        if not sid:
            return 0
        arc = find_session_by_claude_id(root, sid)
        if arc is None:
            return 0
        pp = arc / "arc.md"
        if pp.is_file() and not (fields.read_field(pp, "ended") or "").strip():
            from datetime import datetime

            fields.set_field(pp, "ended", datetime.now().isoformat(timespec="seconds"))
    except Exception:  # noqa: BLE001 — defensive to the end
        pass
    return 0

"""tide.api — the ONE door into tide's domain layer.

Everything OUTSIDE the domain (cli, hooks, launcher, the board over subprocess) reaches
domain operations through this flat facade; nothing outside imports ``tide.arc.*`` /
``tide.handoff_queue`` / … directly. The layering rule (enforced by
``tests/test_layers.py``)::

    cli / hooks / board(external)  →  launch  →  api  →  domain  →  store

Domain modules stay pure: paths + strings in, file writes through the store primitives
(``fields`` / ``io`` / ``paths``) out — no argparse, no HTTP, no subprocess to terminals.
The facade adds NO logic — re-exports only. If an operation needs glue, the glue belongs
in the domain module, not here.
"""

from __future__ import annotations

# --- arcs / threads / sessions (lifecycle of .tide/arcs/) ----------------------------
from .arc.stream import (
    close,
    close_thread,
    effective_status,
    entry_kind,
    last_session,
    new_arc,
    new_goal,
    new_routine,
    new_session,
    new_thread,
    open_arc,
    passport_path,
    record_birth_and_guard,
    reopen,
    rm,
    session_entries,
    set_goal,
    stamp_rev,
    supersede,
    thread_entries,
)

# --- candidates ----------------------------------------------------------------------
from .arc.candidate import (
    archive,
    archive_resolved,
    is_resolved,
    list_candidates,
    new_candidate,
    promote,
)

# --- the human's hand-gestures (board clicks → domain ops) ----------------------------
from .arc.curate import (
    dismiss,
    drop_candidate,
    drop_thread,
    hold,
    retire_sessions,
    validate_step,
)

# --- handoffs (two-stage offer → take) -----------------------------------------------
from .handoff_queue import (
    confirm_for_session,
    drop,
    is_dissolved,
    list_offers,
    multiples,
    offer,
    reserve,
    take,
    validate_target,
)

# --- pulse / offload -----------------------------------------------------------------
from .offload import (
    find_session,
    find_session_by_claude_id,
    nudge_reason,
    offload,
    write_pulse,
)

# --- terminal registry (sid → live handle cache) -------------------------------------
from . import registry

__all__ = [
    # arcs
    "close", "close_thread", "effective_status", "entry_kind", "last_session",
    "new_arc", "new_goal", "new_routine", "new_session", "new_thread", "open_arc",
    "passport_path", "record_birth_and_guard", "reopen", "rm", "session_entries",
    "set_goal", "stamp_rev", "supersede", "thread_entries",
    # candidates
    "archive", "archive_resolved", "is_resolved", "list_candidates", "new_candidate",
    "promote",
    # curate (hand gestures)
    "dismiss", "drop_candidate", "drop_thread", "hold", "retire_sessions",
    "validate_step",
    # handoffs
    "confirm_for_session", "drop", "is_dissolved", "list_offers", "multiples", "offer",
    "reserve", "take", "validate_target",
    # pulse
    "find_session", "find_session_by_claude_id", "nudge_reason", "offload",
    "write_pulse",
    # registry (module — sid-keyed launch registry)
    "registry",
]

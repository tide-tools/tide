"""tide.update — self-update: keep one tide current on any machine.

Three pieces, kept small + separable:

* :mod:`tide.update.source` — the pluggable VERSION SOURCE (the hard question:
  "is the installed tide stale vs the source-of-truth?"). Today the only source
  is the LOCAL checkout the install points at; crit E adds a published channel.
* :mod:`tide.update.core` — detect → REGRESSION GATE (verify --portable + suite)
  → (re)install → stamp. A red gate refuses: a self-update must never ship a
  broken tide.
* :mod:`tide.update.commands` — the thin ``tide self-update`` CLI handler.

:func:`tide.update.core.session_note` is the non-blocking probe the SessionStart
hook calls to SURFACE (never auto-apply) an available update — per the canon
principle that updates are supervised, not silently autonomous.
"""

from __future__ import annotations

__all__ = ["source", "core", "commands"]

"""tide.adapters — the pluggable terminal-adapter registry.

One ABC (:class:`tide.adapters.base.TerminalAdapter`), four shipped
implementations — ``orca`` (drives Orca via osascript), ``macos`` (macOS
Terminal.app via osascript ``do script``), ``tmux`` (headless fallback), ``ao``
(desktop Agent Orchestrator; never auto-detected, pin it — globally or per project) — and a
tiny name→adapter registry. The menu / handoff ask :func:`get_adapter` for an
adapter; an unknown name raises a clear error that *lists the available ones*.
The chosen adapter can be pinned in the project ``.claude/settings.json`` under
``terminal_adapter`` (resolved by :func:`resolve_from_settings`); absent ⇒
auto-detect via :func:`default_adapter_name`.

**Layers** (first hit wins — :func:`configured_adapter_name`):

1. ``--adapter`` on the command line (callers pass it as *override*);
2. ``$TIDE_ADAPTER`` (env);
3. ``terminal_adapter`` in the project / control-home ``.claude/settings.json``;
4. ``terminal_adapter`` in the GLOBAL ``~/.claude/settings.json``
   (``$TIDE_GLOBAL_SETTINGS`` overrides the path) — ``tide terminal-adapter --set
   ao --global``. A global ``ao`` whose app is not installed is skipped (auto);
5. auto-detect, below.

**Auto-detect contract** (when no adapter is pinned or an empty/None name is
passed to :func:`get_adapter`):

1. ``orca``  — if the ``orca`` binary is on PATH (Orca Helper.app installed).
2. ``macos`` — if running on Darwin (any standard Mac without Orca).
3. ``tmux``  — otherwise (Linux / CI headless fallback).

An **explicit** name (via ``--adapter`` or settings) always wins and bypasses
auto-detect. An **unknown** explicit name always raises :class:`AdapterError`.

Keeping the registry here (not in ``base``) lets ``base`` stay dependency-free
and each adapter import only ``base``.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Type

from ..arc.stream import StreamError
from .ao import AoAdapter, ao_available
from .base import SpawnResult, TerminalAdapter, safe_title
from .orca import OrcaAdapter
from .terminal_app import TerminalAppAdapter
from .tmux import TmuxAdapter

# Kept for backward-compat; callers that read it directly still see a sensible
# fallback string. The live default now comes from default_adapter_name().
DEFAULT_ADAPTER = "orca"
SETTINGS_KEY = "terminal_adapter"
ENV_ADAPTER = "TIDE_ADAPTER"
ENV_GLOBAL_SETTINGS = "TIDE_GLOBAL_SETTINGS"

# name → adapter class. Insertion order is the "available" listing order.
_REGISTRY: Dict[str, Type[TerminalAdapter]] = {
    "orca": OrcaAdapter,
    "macos": TerminalAppAdapter,
    "tmux": TmuxAdapter,
    "ao": AoAdapter,
}


class AdapterError(StreamError):
    """An unknown / unresolvable terminal adapter.

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on
    the same ``except`` arm (prints ``tide: …``, exits nonzero).
    """


def available_adapters() -> List[str]:
    """The registered adapter names, in listing order (``orca``, ``macos``, ``tmux``, ``ao``)."""
    return list(_REGISTRY.keys())


def global_settings_path() -> Path:
    """The user-wide Claude settings file carrying the global ``terminal_adapter``."""
    env = (os.environ.get(ENV_GLOBAL_SETTINGS) or "").strip()
    return Path(env).expanduser() if env else Path.home() / ".claude" / "settings.json"


def _pin_in(path: Path) -> Optional[str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return None
    value = data.get(SETTINGS_KEY) if isinstance(data, dict) else None
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


def global_adapter_name() -> Optional[str]:
    """The global pin, or None (unset, or ``ao`` pinned but the app is missing)."""
    name = _pin_in(global_settings_path())
    if name == "ao" and not ao_available():
        return None
    return name


def configured_adapter_name(*roots) -> Optional[str]:
    """env ``TIDE_ADAPTER`` > first *roots* project pin > global pin > None (auto)."""
    env = (os.environ.get(ENV_ADAPTER) or "").strip()
    if env:
        return env.lower()
    for root in roots:
        if root is None:
            continue
        name = _pin_in(Path(root) / ".claude" / "settings.json")
        if name:
            return name
    return global_adapter_name()


def default_adapter_name() -> str:
    """The adapter when nothing project-level is pinned: env > global pin > auto.

    Auto-detect order (:func:`auto_adapter_name`):
    1. ``orca``  — on Darwin with the ``orca`` binary on PATH (Orca Helper.app).
       Darwin-gated so an unrelated Linux ``orca`` (GNOME screen-reader) is not
       mistaken for it.
    2. ``macos`` — running on Darwin (standard Mac; osascript present).
    3. ``tmux``  — fallback (Linux / CI / any non-Darwin host).
    """
    pinned = configured_adapter_name()
    if pinned:
        return pinned
    return auto_adapter_name()


def auto_adapter_name() -> str:
    """Pure environment auto-detect (orca → macos → tmux)."""
    if sys.platform == "darwin" and shutil.which("orca") is not None:
        return "orca"
    if sys.platform == "darwin":
        return "macos"
    return "tmux"


def get_adapter(name: Optional[str] = None) -> TerminalAdapter:
    """Resolve *name* to a fresh adapter instance.

    When *name* is ``None`` or blank (empty / whitespace), :func:`default_adapter_name`
    is called to auto-detect the best adapter for the current environment.
    An explicit non-blank *name* is normalised to lowercase and looked up
    directly — it always wins over auto-detect.

    An unknown name raises :class:`AdapterError` naming the available adapters,
    so a typo fails loud rather than silently falling back.
    """
    if name is None or not name.strip():
        key = default_adapter_name()
    else:
        key = name.strip().lower()

    cls = _REGISTRY.get(key)
    if cls is None:
        raise AdapterError(
            "unknown terminal adapter {0!r} — available: {1}".format(
                name, ", ".join(available_adapters())
            )
        )
    return cls()


def resolve_from_settings(settings: Optional[dict]) -> TerminalAdapter:
    """Resolve the adapter pinned in a settings dict (``terminal_adapter``) or auto-detect."""
    name = None
    if isinstance(settings, dict):
        value = settings.get(SETTINGS_KEY)
        if isinstance(value, str) and value.strip():
            name = value
    return get_adapter(name)


__all__ = [
    "AdapterError",
    "AoAdapter",
    "DEFAULT_ADAPTER",
    "SETTINGS_KEY",
    "SpawnResult",
    "TerminalAdapter",
    "TerminalAppAdapter",
    "auto_adapter_name",
    "available_adapters",
    "configured_adapter_name",
    "global_adapter_name",
    "global_settings_path",
    "default_adapter_name",
    "get_adapter",
    "resolve_from_settings",
    "safe_title",
]

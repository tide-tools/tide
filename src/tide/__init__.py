"""tide — simplified orchestration machine.

Pure CLI + markdown files. Synchronous, human-driven, NO autonomy: no Telegram,
no background decisions. The one web surface is the read-only localhost board
(``tide board`` — :mod:`tide.board_server`). One binary, namespaced subcommands
(arc / canon / contract / candidate / roster) wired by ``cli.py``.

Every command module follows the same ``register(subparsers)`` / thin-handler
pattern (see ``cli.py``); on-disk state lives per-project in
``.tide/{canon,arcs,state}`` (see ``paths.py``).
"""

from __future__ import annotations

_FALLBACK_VERSION = "1.0.31"

try:  # installed package → read from metadata
    from importlib.metadata import PackageNotFoundError, version

    try:
        __version__ = version("tide")
    except PackageNotFoundError:  # running from a source checkout, not installed
        __version__ = _FALLBACK_VERSION
except ImportError:  # pragma: no cover - importlib.metadata always present on 3.9+
    __version__ = _FALLBACK_VERSION

__all__ = ["__version__"]

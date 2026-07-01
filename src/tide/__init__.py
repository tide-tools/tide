"""tide — simplified orchestration machine.

Pure CLI + markdown files. Synchronous, human-driven, NO autonomy:
no web surface, no Telegram, no background daemon. One binary, namespaced
subcommands (arc / canon / contract / candidate / roster) wired by ``cli.py``.

See README.md "## build conventions" for the handler pattern every module
follows and where on-disk state lives (per-project ``.tide/{canon,arcs,state}``).
"""

from __future__ import annotations

_FALLBACK_VERSION = "1.0.30"

try:  # installed package → read from metadata
    from importlib.metadata import PackageNotFoundError, version

    try:
        __version__ = version("tide")
    except PackageNotFoundError:  # running from a source checkout, not installed
        __version__ = _FALLBACK_VERSION
except ImportError:  # pragma: no cover - importlib.metadata always present on 3.9+
    __version__ = _FALLBACK_VERSION

__all__ = ["__version__"]

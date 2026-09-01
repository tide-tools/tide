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

import re
from pathlib import Path
from typing import Optional

# Last resort only: neither a checkout nor an installed distribution answered.
_FALLBACK_VERSION = "1.0.31"

_PYPROJECT_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.M)
_PYPROJECT_NAME_RE = re.compile(r'^name\s*=\s*"tide"', re.M)


def _checkout_version() -> Optional[str]:
    """Version from the pyproject of the checkout we are RUNNING FROM, or None.

    This wins over installed metadata, and that ordering is the whole fix. An
    editable install (`pip install -e .`, how every tide developer runs) stamps a
    ``tide-X.Y.Z.dist-info`` once and never touches it again; later version bumps
    land in ``pyproject.toml`` alone. That is how ``tide --version`` answered
    1.0.43 for five releases while the files actually executing were 1.0.48 —
    and with them ``tide doctor``, the install marker and the update smoke, so
    neither the author nor a user could read off what they were running.

    There is still ONE source of truth: ``pyproject.toml``. Installed metadata is
    a copy of it, and this prefers the original whenever the original is right
    there next to the code being imported. A real wheel has no pyproject two
    levels up, so it falls through to metadata untouched; the name guard keeps an
    unrelated pyproject that happens to sit there from answering for us.
    """
    try:
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        if not pyproject.is_file():
            return None
        text = pyproject.read_text(encoding="utf-8")
        if not _PYPROJECT_NAME_RE.search(text):
            return None
        m = _PYPROJECT_VERSION_RE.search(text)
        return m.group(1) if m else None
    except OSError:
        return None


def _metadata_version() -> Optional[str]:
    """Version of the installed ``tide`` distribution, or None when not installed."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("tide")
        except PackageNotFoundError:  # a source checkout that was never installed
            return None
    except ImportError:  # pragma: no cover - importlib.metadata is always present
        return None


__version__ = _checkout_version() or _metadata_version() or _FALLBACK_VERSION

__all__ = ["__version__"]

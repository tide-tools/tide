"""tide.strictness — the per-project ``strict|loose`` dispatch dial.

A single value persisted at ``<project>/.tide/state/strictness`` decides how the
contract ``sign`` gate behaves (see build-blueprint decision 7):

* ``strict`` — the **human** signs the contract before the worker runs. This is
  the **safe default**: a missing or unreadable dial reads back as ``strict``, so
  the absence of a decision never silently enables auto-dispatch.
* ``loose`` — the live **orchestrator** stamps the signature synchronously and
  auto-dispatches; the human reviews after. ``loose`` is NOT headless autonomy —
  it only shifts who signs inside the running session (the canon ``gate_owner``
  autonomy semantics are intentionally dropped, the dial is kept).

Logic is plain functions (argparse-free, unit-testable); :func:`register` wires
the thin ``tide strictness [strict|loose]`` handler that ``cli.py`` calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from . import io as _io, paths
from .arc.stream import StreamError

STRICT = "strict"
LOOSE = "loose"
DEFAULT = STRICT
VALID: List[str] = [STRICT, LOOSE]


class StrictnessError(StreamError):
    """An invalid strictness value (anything but ``strict``/``loose``).

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on
    the same ``except`` arm (prints ``tide: …``, exits nonzero).
    """


def _normalise(value: str) -> str:
    """Lower-case + strip a raw dial value, validating it against :data:`VALID`."""
    v = (value or "").strip().lower()
    if v not in VALID:
        raise StrictnessError(
            "strictness: invalid value {0!r} (want one of {1})".format(
                value, "/".join(VALID)
            )
        )
    return v


def read_strictness(root: Path) -> str:
    """Return the project's dial, defaulting to ``strict`` (safe default).

    A missing file, an empty file, or any unrecognised content all read back as
    :data:`DEFAULT` — the absence of an explicit ``loose`` never enables it.
    """
    f = paths.strictness_file(root)
    if not f.is_file():
        return DEFAULT
    raw = f.read_text(encoding="utf-8").strip().lower()
    return raw if raw in VALID else DEFAULT


def read_strictness_explicit(root: Path) -> str:
    """Return the dial ONLY when explicitly set, else ``""`` (no inferred default).

    Unlike :func:`read_strictness` (which folds a missing/garbage dial into the
    safe ``strict`` default), this returns the empty string when the project has
    made no decision. The ``land`` axis needs the distinction: a project that has
    never run ``tide strictness`` lands ``loose`` by default (speed), while one
    that deliberately ran ``tide strictness strict`` lands ``strict`` by default —
    a difference :func:`read_strictness` cannot express.
    """
    f = paths.strictness_file(root)
    if not f.is_file():
        return ""
    raw = f.read_text(encoding="utf-8").strip().lower()
    return raw if raw in VALID else ""


def set_strictness(root: Path, value: str) -> str:
    """Persist *value* (``strict``/``loose``) to ``.tide/state/strictness``.

    Validates and normalises first (raising :class:`StrictnessError` on garbage),
    creates ``state/`` if needed, and writes a single newline-terminated line.
    Returns the normalised value that was written.
    """
    v = _normalise(value)
    f = paths.strictness_file(root)
    _io.atomic_write(f, "{0}\n".format(v))
    return v


def is_strict(root: Path) -> bool:
    """True when the project is on the ``strict`` (human-signs-first) dial."""
    return read_strictness(root) == STRICT


# --- CLI wiring ------------------------------------------------------------

def _cmd_strictness(args) -> int:
    root = paths.require_tide_root()
    if getattr(args, "value", None):
        v = set_strictness(root, args.value)
        print("tide: strictness → {0}".format(v))
    else:
        print(read_strictness(root))
    return 0


def register(subparsers) -> None:
    """Add the top-level ``strictness`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "strictness", help="show/set the dispatch dial (strict|loose)"
    )
    p.add_argument("value", nargs="?", choices=VALID, help="set the dial (omit to show)")
    p.set_defaults(func=_cmd_strictness, _cmd="strictness")

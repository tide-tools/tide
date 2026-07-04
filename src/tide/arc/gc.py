"""tide.arc.gc — sweep abandoned template shells out of the stream (cand 04).

The mite incident left seven untouched template arcs (болванки) posing as live
work. ``tide arc gc`` finds entries that are BOTH:

* **drafts** — open top-stream entries whose formulation is still template
  placeholders (:func:`tide.arc.stream.draft_entries`), and
* **contentless** — nothing real inside: the passport is the only file, the
  input/workspace/output triad (and a container's nested ``arcs/``) hold no
  files at all.

Dry-run by default (prints the list, touches nothing). ``--apply`` MOVES the
shells into ``.tide/gc-trash/`` instead of deleting — a sweep must be
reversible; emptying the trash is the human's explicit act.

Plain functions (argparse-free, unit-testable); :func:`register` wires the thin
CLI handler under ``tide arc``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List

from .. import paths
from . import stream

GC_TRASH_DIRNAME = "gc-trash"


def _has_any_file(d: Path) -> bool:
    """True when dir *d* contains at least one FILE anywhere below it."""
    if not d.is_dir():
        return False
    return any(p.is_file() for p in d.rglob("*"))


def contentless(entry_dir: Path) -> bool:
    """True when nothing real ever landed in *entry_dir* — passport-only scaffold.

    Any extra file (a delta.md, a seed in ``input/``, a workspace note, a nested
    run) is a sign of life: the entry is somebody's work, not a болванка, and gc
    must keep its hands off.
    """
    entry_dir = Path(entry_dir)
    passport = stream.passport_path(entry_dir)
    for p in entry_dir.rglob("*"):
        if p.is_file() and p != passport:
            return False
    return True


def sweepable(root: Path) -> List[Path]:
    """The gc candidates for *root*: draft AND contentless AND goal-less.

    A stated ``goal:`` is somebody's intent — even a steps-less routine (draft on
    the board) is never swept once a human wrote what it is for.
    """
    return [
        e
        for e in stream.draft_entries(root)
        if contentless(e) and not stream.goal_filled(e)
    ]


def trash_dir(root: Path) -> Path:
    """Where swept shells go: ``.tide/gc-trash/`` (created on first sweep)."""
    return paths.tide_dir(Path(root)) / GC_TRASH_DIRNAME


def sweep(root: Path, *, apply: bool = False) -> "tuple[List[Path], List[Path]]":
    """Find (and with *apply* move to trash) the sweepable shells of *root*.

    Returns ``(found, moved)`` — *moved* is empty on a dry run. A name collision
    in the trash gets a numeric suffix so nothing is ever overwritten.
    """
    found = sweepable(root)
    if not apply or not found:
        return found, []
    tdir = trash_dir(root)
    tdir.mkdir(parents=True, exist_ok=True)
    moved: List[Path] = []
    for e in found:
        target = tdir / e.name
        n = 1
        while target.exists():
            target = tdir / "{0}~{1}".format(e.name, n)
            n += 1
        shutil.move(str(e), str(target))
        moved.append(target)
    return found, moved


# --- CLI wiring ------------------------------------------------------------

def cmd_gc(args) -> int:
    """``tide arc gc [--apply]`` — list (or sweep) abandoned template shells."""
    root = paths.require_tide_root()
    apply = bool(getattr(args, "apply", False))
    found, moved = sweep(root, apply=apply)
    if not found:
        print("tide: gc — no abandoned template shells ✓")
        return 0
    if apply:
        print("tide: gc — swept {0} shell(s) into {1}:".format(
            len(moved), trash_dir(root)))
        for t in moved:
            print("  {0}".format(t.name))
        print("  (reversible — move a dir back to restore; empty the trash yourself)")
    else:
        print("tide: gc — {0} abandoned template shell(s) (dry-run, nothing touched):".format(
            len(found)))
        for e in found:
            print("  {0}".format(e.name))
        print("  fill their goal to keep them, or sweep with 'tide arc gc --apply'")
    return 0


def register(arc_subparsers) -> None:
    """Add the ``gc`` command under ``tide arc`` (called by cli.py)."""
    p = arc_subparsers.add_parser(
        "gc",
        help="sweep abandoned template shells (drafts with no content) to .tide/gc-trash/",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="actually move the shells to trash (default: dry-run list)",
    )
    p.set_defaults(func=cmd_gc, _cmd="arc gc")

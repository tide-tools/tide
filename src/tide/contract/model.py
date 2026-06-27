"""tide.contract.model — the ``contract.md`` passport + its 5-state machine.

A contract is the lightweight binding ``worker → arc`` (``goal + criteria``).
It lives as ``<arc_dir>/contract.md`` — **one contract per arc** (the guard is a
plain file-exists check, since the file IS the contract). Ported from the canon
6-state model, collapsed to tide's 5 (build-blueprint decision; ``НА АПРУВ``
folds into the sign gate, ``ГЕЙТ``/``НА ВЕРИФИКАЦИИ`` collapse into the in-session
report+proof review):

    draft → (sign) → running → output → close

Passport fields (architect ``contract.md passport``, autonomy ``backend:`` dropped,
``canon-rev`` added): ``slug · goal · criteria · project · state · sign ·
supersedes (optional) · canon-rev``. Body: ``## IS → TO-BE`` + ``## where we are``.

All functions are pure helpers or thin file wrappers (argparse-free, unit-testable);
the CLI surface lives in :mod:`tide.contract.lifecycle`.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import List, Optional

from .. import fields, paths, slug
from ..arc.stream import StreamError

CONTRACT_FILE = "contract.md"
DELTA_FILE = "delta.md"
ASKS_DIRNAME = "asks"

# The 5 collapsed states, in lifecycle order.
DRAFT = "draft"
SIGN = "sign"
RUNNING = "running"
OUTPUT = "output"
CLOSE = "close"
STATES: List[str] = [DRAFT, SIGN, RUNNING, OUTPUT, CLOSE]


class ContractError(StreamError):
    """A user-facing contract error (no arc, one-per-arc, bad state, guard …).

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on
    the same ``except`` arm (prints ``tide: …``, exits nonzero).
    """


def today() -> str:
    """Today's date as ``YYYY-MM-DD`` (callers may inject *date* to override)."""
    return datetime.date.today().isoformat()


# --- passport paths --------------------------------------------------------

def contract_path(arc_dir: Path) -> Path:
    """Path to an arc's ``contract.md`` (the one-per-arc passport file)."""
    return Path(arc_dir) / CONTRACT_FILE


def delta_path(arc_dir: Path) -> Path:
    """Path to an arc's staged canon ``delta.md`` (merged into CANON.md on close)."""
    return Path(arc_dir) / DELTA_FILE


def asks_dir(arc_dir: Path) -> Path:
    """Path to an arc's durable ``asks/`` open-question home."""
    return Path(arc_dir) / ASKS_DIRNAME


def has_contract(arc_dir: Path) -> bool:
    """True when *arc_dir* already carries a ``contract.md`` (one-per-arc guard)."""
    return contract_path(arc_dir).is_file()


# --- template --------------------------------------------------------------

def contract_md(
    contract_slug: str,
    *,
    goal: Optional[str] = None,
    criteria: Optional[str] = None,
    project: Optional[str] = None,
    canon_rev: str = "",
) -> str:
    """Seed text for a fresh ``contract.md`` (state ``draft``, empty ``sign:``).

    Field KEYS stay English so parsing is language-agnostic. The ``# supersedes:``
    placeholder is a *comment* (a key with a leading ``# `` is not a real field);
    a real intent pivot writes ``supersedes:`` via :func:`set_field`.
    """
    g = (goal or "").strip() or "<one line — what this contract closes>"
    c = (criteria or "").strip() or "<done-when — the acceptance criteria>"
    p = (project or "").strip() or "<project path>"
    return (
        "# contract — {slug}\n"
        "\n"
        "slug: {slug}\n"
        "goal: {goal}\n"
        "criteria: {criteria}\n"
        "project: {project}\n"
        "state: {draft}\n"
        "sign:\n"
        "# supersedes: <slug of the contract this one pivots from — optional; alias: prev:>\n"
        "canon-rev: {rev}\n"
        "\n"
        "## IS → TO-BE\n"
        "<where it is now → where this contract takes it>\n"
        "\n"
        "## where we are\n"
        "<current step / bottleneck>\n"
    ).format(slug=contract_slug, goal=g, criteria=c, project=p, draft=DRAFT, rev=canon_rev)


# --- field read / write ----------------------------------------------------

def read_field(arc_dir: Path, key: str) -> Optional[str]:
    """Read a passport field from an arc's ``contract.md`` (None if absent)."""
    return fields.read_field(contract_path(arc_dir), key)


def set_field(arc_dir: Path, key: str, value: str) -> None:
    """Write a passport field into an arc's ``contract.md`` (order-preserving)."""
    fields.set_field(contract_path(arc_dir), key, value)


def read_state(arc_dir: Path) -> Optional[str]:
    """The contract's current ``state:`` (None when there is no contract)."""
    return read_field(arc_dir, "state")


def set_state(arc_dir: Path, state: str) -> str:
    """Validate *state* against :data:`STATES` and write it; return the value.

    Raises :class:`ContractError` on an unknown state key so a bad manual
    transition fails fast with a clear message.
    """
    s = (state or "").strip().lower()
    if s not in STATES:
        raise ContractError(
            "contract state: invalid value {0!r} (want one of {1})".format(
                state, "/".join(STATES)
            )
        )
    set_field(arc_dir, "state", s)
    return s


def contract_slug(arc_dir: Path) -> str:
    """The contract's slug — its ``slug:`` field, falling back to the arc's slug."""
    s = read_field(arc_dir, "slug")
    if s:
        return s
    return slug.entry_slug(Path(arc_dir).name)


# --- arc resolution --------------------------------------------------------

def resolve_arc_dir(root: Path, ref: str, goal_slug: Optional[str] = None) -> Path:
    """Resolve the arc dir a contract binds to (open OR closed, top or substream).

    Accepts an exact entry dir name (``03-fix-leak`` / ``__03-fix-leak__``) or a
    bare slug. With *goal_slug* the lookup runs inside that goal's ``arcs/``
    substream. Raises :class:`ContractError` when nothing matches.
    """
    # Local import avoids an import cycle (stream → nothing in contract, but keep tidy).
    from ..arc import stream

    if goal_slug:
        stream_dir = stream._search_dir(root, goal_slug)
    else:
        stream_dir = paths.arcs_dir(root)

    exact = Path(stream_dir) / ref
    if exact.is_dir() and exact.name != paths.CANDIDATES_DIRNAME:
        return exact
    closed_exact = Path(stream_dir) / "__{0}__".format(ref)
    if closed_exact.is_dir():
        return closed_exact

    entry = stream._resolve(stream_dir, ref, closed=False) or stream._resolve(
        stream_dir, ref, closed=True
    )
    if entry is None:
        raise ContractError(
            "no arc matching {0!r} in {1} (create it first?)".format(ref, stream_dir)
        )
    return entry

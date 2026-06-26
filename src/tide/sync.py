"""tide.sync — the cannon-rev drift engine (stamp · bump · drift-check · block).

This is tide's load-bearing **net-new** discipline over the arcs/canon bash tools
(they had no content-hash, no drift anchor). It turns the cannon-rev (sha256 of
``CANON.md`` ONLY — :mod:`tide.cannon.rev`) into a synchronisation barrier so
parallel workers never share writes and divergence surfaces one delta at a time
at the human-gated merge.

Four pure operations (build-blueprint ``sync_hook`` / package-layout U7), each
consumed by the agent CLI and the SessionStart / edit-gate hooks:

* **stamp(arc, root)** — write the current cannon-rev into an arc's passport.
  The on-open stamp; mirrors :func:`tide.arc.stream.stamp_rev` (which the stream
  calls directly on new/open) so the whole stamp/bump/drift/block vocabulary
  lives in one module.
* **bump(root)** — recompute the cannon-rev after a merge (close = delta-merge).
  So any later arc comparing against an older stamp detects movement.
* **drift_check(arc, root)** — compare an arc's stamped cannon-rev against the
  current one; if cannon moved, the arc has drifted and must reconcile before it
  proceeds (checked on worker dispatch + on arc close).
* **block_new_arc_if_unmerged_delta(root)** — refuse to open a NEW arc while ANY
  arc — ACTIVE or CLOSED — still carries a non-empty, unmerged ``delta.md``.
  Extends canon's close guard into a between-arcs barrier so deltas funnel through
  the merge gate one at a time (decision 9 / dogfood fix F1). The active-arc scan
  closes the happy-path hole where the current arc is still open.

All functions are pure (read-only) except ``stamp`` (a single field write) and
``block_…`` (raises). They are wired into :mod:`tide.arc.stream` (``new``/``open``)
and re-used by the U8 board + U10 hooks.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, NamedTuple, Optional

from . import fields, paths, slug
from .cannon import merge, rev

# stamp_rev/passport_path are the single on-disk-passport implementation; reuse
# them so sync never re-derives the goal-doc-vs-arc.md resolution (DRY). Imported
# at module top is safe: tide.arc.stream imports `tide.sync` only *lazily* (inside
# new_arc/open_arc), so there is no import cycle at load time.
from .arc.stream import StreamError, passport_path, stamp_rev

DELTA_FILE = "delta.md"
MERGED_KEY = "merged"
MERGED_YES = "yes"


class SyncError(StreamError):
    """A cannon-sync barrier error (unmerged delta blocks a new arc).

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on
    the same ``except`` arm (prints ``tide: …``, exits nonzero).
    """


class DriftResult(NamedTuple):
    """Outcome of :func:`drift_check`.

    ``drifted`` is True only when the arc carries a stamp AND it differs from the
    current cannon-rev. A never-stamped arc (``stamped is None``) is *not* drift —
    there is nothing to compare — so callers can flag "unstamped" separately.
    """

    drifted: bool
    stamped: Optional[str]
    current: str


# --- stamp / bump ----------------------------------------------------------

def stamp(arc_dir: Path, root: Path) -> str:
    """Write the current cannon-rev into *arc_dir*'s passport; return the rev.

    The on-open stamp (decision 9/10). Delegates to
    :func:`tide.arc.stream.stamp_rev` so there is exactly one writer of the
    ``cannon-rev:`` field.
    """
    return stamp_rev(Path(arc_dir), Path(root))


def bump(root: Path) -> str:
    """Recompute the cannon-rev after a merge — the post-merge drift anchor.

    Pure read of ``CANON.md``; identical to what :func:`tide.cannon.merge.merge_delta`
    returns, named here so the merge path and the hook share one vocabulary.
    """
    return rev.compute(Path(root))


# --- drift check -----------------------------------------------------------

def drift_check(arc_dir: Path, root: Path) -> DriftResult:
    """Compare *arc_dir*'s stamped cannon-rev against the current one.

    Reads the stamp from the arc's passport (``cannon-rev:``) and the live rev
    from ``CANON.md``. Returns a :class:`DriftResult`; ``drifted`` is True when a
    stamp exists and the cannon has since moved.
    """
    stamped = fields.read_field(passport_path(Path(arc_dir)), "cannon-rev")
    current = rev.compute(Path(root))
    drifted = stamped is not None and stamped != current
    return DriftResult(drifted=drifted, stamped=stamped, current=current)


def has_drifted(arc_dir: Path, root: Path) -> bool:
    """Convenience boolean wrapper over :func:`drift_check`."""
    return drift_check(arc_dir, root).drifted


# --- unmerged-delta barrier ------------------------------------------------

def is_unmerged_delta(delta_path: Path) -> bool:
    """True when *delta_path* is a non-empty delta NOT yet marked ``merged: yes``.

    "Non-empty" means it has a merge-worthy body — exactly what
    :func:`tide.cannon.merge.merge_delta` would fold into the journal (frontmatter
    + heading stripped) — so a delta carrying only a ``merged:`` line counts as
    empty. A merged delta (``merged: yes``) is done and never an offender.
    """
    p = Path(delta_path)
    if not p.is_file():
        return False
    text = p.read_text(encoding="utf-8")
    if fields.read_field_text(text, MERGED_KEY) == MERGED_YES:
        return False
    return bool(merge._delta_body(text))


def unmerged_deltas(root: Path, *, include_active: bool = False) -> List[Path]:
    """Arc dirs under ``.tide/arcs/`` that still hold a non-empty, unmerged delta.

    Walks the whole stream (top + nested goal substreams) for ``delta.md`` files,
    keeping those whose entry is a real arc/goal dir and whose delta is non-empty
    + unmerged. By default only CLOSED (``__…__``) arcs count — the post-close
    merge-gate offenders the board + hooks report. With ``include_active=True`` an
    OPEN arc carrying a written-but-unmerged delta is an offender too; the
    between-arcs barrier (:func:`block_new_arc_if_unmerged_delta`) uses this so the
    one-at-a-time invariant also holds on the happy path, where the current arc is
    still active. Returns the offending entry dirs, name-sorted.
    """
    arcs = paths.arcs_dir(Path(root))
    offenders: List[Path] = []
    if not arcs.is_dir():
        return offenders
    for delta in sorted(arcs.rglob(DELTA_FILE)):
        entry = delta.parent
        if not slug.is_entry(entry.name):
            continue
        if not include_active and not slug.is_closed_entry(entry.name):
            continue
        if is_unmerged_delta(delta):
            offenders.append(entry)
    return sorted(offenders, key=lambda p: p.name)


def block_new_arc_if_unmerged_delta(root: Path) -> None:
    """Refuse to open a new arc while ANY arc — active OR closed — owes a merge.

    The between-arcs barrier (decision 9 / dogfood fix F1): deltas must funnel
    through the orchestrator-only ``cannon merge`` gate one feature at a time. The
    old scan only saw CLOSED arcs, so the common happy path (the current arc still
    active with a written delta) let a 2nd concurrent arc open silently. Scanning
    active + closed (``include_active=True``) closes that hole. Raises
    :class:`SyncError` naming the offender(s) + how to resolve; a no-op when the
    stream is clean.
    """
    offenders = unmerged_deltas(root, include_active=True)
    # Arcs landed `loose` carry their unmerged delta as DELIBERATE deferred debt
    # (tracked in the ledger, surfaced at session-start) — they don't block
    # dispatching the next arc. That is the whole point of the loose dial:
    # discipline without slowness. `tide reconcile` pays the debt down later.
    from . import ledger  # lazy: ledger imports paths/slug only, no cycle

    deferred = {e.ref for e in ledger.entries(root)}
    offenders = [o for o in offenders if slug.entry_slug(o.name) not in deferred]
    if not offenders:
        return
    names = ", ".join(o.name for o in offenders)
    raise SyncError(
        "cannot open a new arc — {n} arc(s) carry an unmerged cannon-delta "
        "({names}); merge into cannon first (tide cannon merge <arc>)".format(
            n=len(offenders), names=names
        )
    )

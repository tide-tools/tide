"""tide.ledger — the deferred-reconciliation debt ledger (``.tide/deferred.md``).

When an arc lands ``loose`` (the fast default — *discipline without slowness*),
the strict reconciliation guards (a non-empty ``delta.md`` merged into CANON, an
accepted ``report.md`` + ``proof.md``) are SKIPPED so the head can dispatch the
next arc immediately. The skipped work is not lost — it is written here as a debt
line so a later ``tide reconcile`` / ``tide arc land --strict <arc>`` pays it down.

One human-readable, git-trackable file at the ``.tide/`` root; each owed arc is a
single list line carrying the three things reconciliation needs to find it again:

    - arc: <entry-dir-name>  deferred: <guards>  cannon-rev: <rev>

``<guards>`` is a comma-joined subset of ``delta``/``report``/``proof`` (the
guards that were not satisfied at land time). The ledger is the SINGLE source of
"canon is behind"; the board, SessionStart, and ``tide go`` all read :func:`count`
/ :func:`entries` to surface it, and ``tide reconcile`` walks :func:`entries`.

All functions are pure reads or single-file writes (argparse-free, unit-testable);
``append`` is idempotent per-arc (re-landing replaces that arc's line in place).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import io as _io, paths, slug

# The three strict-reconciliation guards a loose land may defer, canonical order.
GUARD_DELTA = "delta"
GUARD_REPORT = "report"
GUARD_PROOF = "proof"
GUARDS: List[str] = [GUARD_DELTA, GUARD_REPORT, GUARD_PROOF]

_HEADER = (
    "# deferred — reconciliation debt\n"
    "\n"
    "Arcs landed `loose` that still owe a `strict` reconciliation (a non-empty\n"
    "delta merged into CANON + an accepted report.md & proof.md). Pay down with\n"
    "`tide reconcile` (all) or `tide arc land --strict <arc>` (one). Auto-managed —\n"
    "lines are added on a loose land and removed on reconciliation.\n"
    "\n"
)

# Parses one debt line: `- arc: NAME  deferred: a, b  cannon-rev: REV`.
_LINE_RE = re.compile(
    r"^-\s*arc:\s*(?P<arc>\S+)\s+deferred:\s*(?P<deferred>.*?)\s+cannon-rev:\s*(?P<rev>\S*)\s*$"
)


@dataclass(frozen=True)
class LedgerEntry:
    """One owed arc: its sealed dir name, the deferred guards, the land-time rev."""

    arc: str
    deferred: List[str]
    cannon_rev: str

    @property
    def ref(self) -> str:
        """The bare slug used to resolve this arc again (markers stripped)."""
        return slug.entry_slug(self.arc)


def _format_line(entry: LedgerEntry) -> str:
    """Render one debt line (the inverse of :data:`_LINE_RE`)."""
    guards = ", ".join(entry.deferred) if entry.deferred else "-"
    return "- arc: {arc}  deferred: {guards}  cannon-rev: {rev}".format(
        arc=entry.arc, guards=guards, rev=entry.cannon_rev
    )


def _parse_line(line: str) -> Optional[LedgerEntry]:
    """Parse one debt line into a :class:`LedgerEntry`, or None when it is not one."""
    m = _LINE_RE.match(line.strip())
    if not m:
        return None
    raw = m.group("deferred").strip()
    deferred = [g.strip() for g in raw.split(",") if g.strip() and g.strip() != "-"]
    return LedgerEntry(arc=m.group("arc"), deferred=deferred, cannon_rev=m.group("rev"))


# --- reads -----------------------------------------------------------------

def entries(root: Path) -> List[LedgerEntry]:
    """Every debt entry in the ledger, in file order (empty list when none/absent)."""
    f = paths.deferred_file(Path(root))
    if not f.is_file():
        return []
    out: List[LedgerEntry] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        entry = _parse_line(line)
        if entry is not None:
            out.append(entry)
    return out


def count(root: Path) -> int:
    """Number of arcs currently owing a strict reconciliation."""
    return len(entries(root))


def find(root: Path, ref: str) -> Optional[LedgerEntry]:
    """The debt entry matching *ref* (by bare slug), or None when not owed."""
    target = slug.entry_slug(ref)
    for e in entries(root):
        if e.ref == target:
            return e
    return None


# --- writes ----------------------------------------------------------------

def _write(root: Path, items: List[LedgerEntry]) -> None:
    """Persist *items* to the ledger; delete the file when the debt is fully paid."""
    f = paths.deferred_file(Path(root))
    if not items:
        if f.is_file():
            f.unlink()
        return
    body = "\n".join(_format_line(e) for e in items)
    _io.atomic_write(f, _HEADER + body + "\n")


def append(root: Path, arc: str, deferred: List[str], cannon_rev: str) -> LedgerEntry:
    """Record (or refresh) *arc*'s reconciliation debt; idempotent per-arc.

    Re-landing an arc that is already owed replaces its line in place (latest
    guards + rev win) rather than duplicating it, so the ledger holds at most one
    line per arc. Returns the entry written.
    """
    new = LedgerEntry(arc=arc, deferred=list(deferred), cannon_rev=cannon_rev)
    target = slug.entry_slug(arc)
    items = [e for e in entries(root) if e.ref != target]
    items.append(new)
    _write(root, items)
    return new


def remove(root: Path, ref: str) -> bool:
    """Drop *ref*'s debt line (it has been reconciled); True when one was removed."""
    target = slug.entry_slug(ref)
    items = entries(root)
    kept = [e for e in items if e.ref != target]
    if len(kept) == len(items):
        return False
    _write(root, kept)
    return True

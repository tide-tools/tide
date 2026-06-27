"""tide.canon.migrate — atomic ``.tide/cannon/`` → ``.tide/canon/`` folder migration.

The cannon → canon rename shipped read-both back-compat (``paths.canon_dir`` falls
back to a legacy ``.tide/cannon/``; ``paths.migrate_canon_dir`` flips it on the next
write). But many on-disk instances still physically carry ``.tide/cannon/`` and there
is no explicit, auditable command to move them — so a rename-critic flagged the silent
case where BOTH ``.tide/cannon/`` and ``.tide/canon/`` coexist. This module is that
command's engine: ``tide canon migrate``.

It does TWO things and nothing else:

1. **Rename** ``.tide/cannon/`` → ``.tide/canon/`` (the legacy dir → the canonical one).
2. **Rewrite** the legacy *stamps* that live INSIDE that dir — exactly the spellings
   that occur on real disk: the journal heading ``## Cannon journal`` (and its ``###``
   sub-entry form) → ``Canon journal``, and the ``cannon-rev`` field → ``canon-rev``.
   The standalone word ``cannon`` in prose / paths is deliberately left alone — only
   the two structural stamps are touched.

Plan-then-apply, mirroring :mod:`tide.migrate`: :func:`plan` is a pure read returning a
:class:`CanonMigratePlan`; :func:`apply` is the only mutator. ``--dry-run`` prints the
plan and changes nothing.

Crash-safety (mirrors :mod:`tide.io` discipline): stamp rewrites happen IN the legacy
dir first, each via :func:`tide.io.atomic_write` (same-dir temp + fsync + ``os.replace``)
and each idempotent; the directory ``os.rename`` is the single, atomic commit point.
A crash before the rename leaves the legacy dir in place — a retry re-runs cleanly
(the rewrites are no-ops the second time) and then renames. ``os.rename`` of a dir
within the same ``.tide/`` parent is atomic on POSIX: never both names, never neither.

LOUD on coexistence: if BOTH dirs exist :func:`apply` refuses with an actionable error
and touches nothing — two ``CANON.md`` files cannot be merged unambiguously, so the
human must resolve it. This closes the rename-critic MEDIUM.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

from .. import io as _io, paths
from ..arc.stream import StreamError

# Legacy → canonical text rewrites. ONLY the spellings that actually occur inside a
# real ``.tide/cannon/`` dir (CANON.md / config) on the roster:
#   * "Cannon journal"  — the append-only journal heading (## and ### forms);
#   * "cannon-rev"      — the legacy drift-stamp field.
# Replacing "## Cannon journal" is covered by the substring "Cannon journal" (which
# also fixes the "### Cannon journal" sub-entry form), and neither replacement is a
# substring of the other, so order is irrelevant. The bare word "cannon" in prose or
# a ".tide/cannon" path is intentionally NOT rewritten.
_TEXT_REWRITES: Tuple[Tuple[str, str], ...] = (
    ("Cannon journal", "Canon journal"),
    ("cannon-rev", "canon-rev"),
)


class CanonMigrateError(StreamError):
    """A canon-migrate refusal (coexisting cannon/ + canon/ dirs).

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on the
    same ``except`` arm (prints ``tide: …`` to stderr and exits nonzero).
    """


# ---------------------------------------------------------------------------
# Pure stamp rewrite
# ---------------------------------------------------------------------------

def rewrite_stamps(text: str) -> Tuple[str, int]:
    """Return ``(rewritten_text, n_replacements)`` for one file's *text*.

    Applies every legacy → canonical stamp rewrite. ``n_replacements`` is the total
    number of legacy occurrences replaced (0 ⇒ the text was already canonical, so the
    file needs no write). Deterministic and idempotent: feeding the output back in
    yields the same text and ``0``.
    """
    out = text
    total = 0
    for legacy, canonical in _TEXT_REWRITES:
        total += out.count(legacy)
        out = out.replace(legacy, canonical)
    return out, total


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

@dataclass
class CanonMigratePlan:
    """A pure, immutable description of what a canon migration WOULD do."""

    root: Path
    legacy_dir: Path                                   # .tide/cannon
    target_dir: Path                                   # .tide/canon
    needs_rename: bool                                 # legacy present, target absent
    coexist: bool                                      # BOTH present → must refuse
    # Files (by their CURRENT legacy path) that carry a legacy stamp, with the count.
    rewrites: List[Tuple[Path, int]] = field(default_factory=list)


def plan(root: Path) -> CanonMigratePlan:
    """Compute the deterministic migration plan for *root* (pure — no disk writes).

    Resolution mirrors :func:`tide.paths.canon_dir`'s back-compat detection:
      * legacy ``.tide/cannon/`` present, ``.tide/canon/`` absent → a real migration;
      * neither / only ``.tide/canon/`` present → nothing to do (no-op);
      * BOTH present → ``coexist`` (the caller must refuse loudly).

    When a rename is warranted, every readable file under the legacy dir is scanned for
    legacy stamps so :func:`apply` knows exactly which files to rewrite (and dry-run can
    report them).
    """
    root = Path(root)
    td = paths.tide_dir(root)
    legacy_dir = td / paths.CANNON_DIRNAME
    target_dir = td / paths.CANON_DIRNAME

    legacy_present = legacy_dir.is_dir()
    target_present = target_dir.is_dir()
    coexist = legacy_present and target_present
    needs_rename = legacy_present and not target_present

    p = CanonMigratePlan(
        root=root,
        legacy_dir=legacy_dir,
        target_dir=target_dir,
        needs_rename=needs_rename,
        coexist=coexist,
    )

    if needs_rename:
        for child in sorted(legacy_dir.rglob("*")):
            if not child.is_file():
                continue
            try:
                text = child.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # non-text / unreadable file — carried as-is by the rename
            _, n = rewrite_stamps(text)
            if n:
                p.rewrites.append((child, n))
    return p


def coexist_message(p: CanonMigratePlan) -> str:
    """The loud, actionable refusal printed when both cannon/ and canon/ exist."""
    return (
        "REFUSING to migrate — BOTH {legacy} and {target} exist.\n"
        "  This is ambiguous: each may hold a different CANON.md, and tide will not\n"
        "  silently pick one. Resolve by hand:\n"
        "    1. diff {legacy}/CANON.md  {target}/CANON.md\n"
        "    2. keep the correct truth in {target}/ , then remove {legacy}/\n"
        "  Nothing was touched.".format(
            legacy=p.legacy_dir,
            target=p.target_dir,
        )
    )


# ---------------------------------------------------------------------------
# Result + apply
# ---------------------------------------------------------------------------

@dataclass
class CanonMigrateResult:
    """What :func:`apply` actually did (for the CLI summary)."""

    migrated: bool                                     # True ⇒ a rename happened
    legacy_dir: Path
    target_dir: Path
    files_rewritten: List[Tuple[str, int]] = field(default_factory=list)


def apply(p: CanonMigratePlan) -> CanonMigrateResult:
    """Execute *plan* — the ONLY mutator. Atomic, crash-safe, idempotent.

    Refuses (touching nothing) when both dirs coexist. When there is no legacy dir the
    call is a clean no-op (``migrated=False``). Otherwise: rewrite legacy stamps in the
    legacy dir (each write atomic + idempotent), then atomically ``os.rename`` the dir
    to its canonical name — the single commit point.
    """
    if p.coexist:
        raise CanonMigrateError(coexist_message(p))

    if not p.needs_rename:
        # Already on .tide/canon/ (or no canon home at all) — nothing to migrate.
        return CanonMigrateResult(
            migrated=False,
            legacy_dir=p.legacy_dir,
            target_dir=p.target_dir,
        )

    # 1. Rewrite legacy stamps IN the legacy dir (atomic per file, idempotent). Doing
    #    this before the rename keeps the rename the single commit point: a crash here
    #    leaves the legacy dir in place for an idempotent retry.
    rewritten: List[Tuple[str, int]] = []
    for file_path, _predicted in p.rewrites:
        text = file_path.read_text(encoding="utf-8")
        new_text, n = rewrite_stamps(text)
        if n:
            _io.atomic_write(file_path, new_text)
            rewritten.append((file_path.name, n))

    # 2. Atomic commit: rename the dir within the same .tide/ parent (POSIX-atomic —
    #    never leaves both names or neither). Back-compat resolution is now obsolete
    #    for this project; future writes land directly in .tide/canon/.
    os.rename(str(p.legacy_dir), str(p.target_dir))

    return CanonMigrateResult(
        migrated=True,
        legacy_dir=p.legacy_dir,
        target_dir=p.target_dir,
        files_rewritten=rewritten,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_plan(p: CanonMigratePlan) -> str:
    """Human-readable ``--dry-run`` view: the rename + every file that would be rewritten."""
    if not p.needs_rename:
        return "canon migrate: nothing to migrate (already on {0})".format(p.target_dir)

    lines = ["canon migrate plan — {0}".format(p.root)]
    lines.append("  rename:  {0} → {1}".format(p.legacy_dir.name, p.target_dir.name))
    if p.rewrites:
        lines.append("  stamp rewrites (legacy → canonical):")
        for file_path, n in p.rewrites:
            lines.append("    {0}: {1} occurrence(s)".format(file_path.name, n))
    else:
        lines.append("  stamp rewrites: none (no legacy stamps inside)")
    return "\n".join(lines)


def render_result(result: CanonMigrateResult) -> str:
    """Human-readable post-migration summary."""
    if not result.migrated:
        return "canon migrate: nothing to migrate (already on {0})".format(result.target_dir)

    lines = ["canon migrate: done"]
    lines.append("  renamed:  {0} → {1}".format(result.legacy_dir.name, result.target_dir.name))
    if result.files_rewritten:
        lines.append("  rewrote stamps:")
        for name, n in result.files_rewritten:
            lines.append("    {0}: {1} occurrence(s)".format(name, n))
    else:
        lines.append("  rewrote stamps: none")
    return "\n".join(lines)

"""tide.canon.commands — wire the ``tide canon …`` subcommands.

Follows the build convention: this module owns the argparse surface and thin
handlers; all logic lives in :mod:`store` / :mod:`rev` / :mod:`merge` so it stays
unit-testable without argparse. ``cli.py`` calls :func:`register`.

``canon merge`` is the single serialization point and is ORCHESTRATOR-ONLY —
the handler hard-refuses a worker via ``cli.require_orchestrator``. ``cannon``
is kept as a hidden CLI alias for back-compat so ``tide cannon …`` still works.
"""

from __future__ import annotations

import argparse
import sys

from .. import paths, slug
from . import merge, rev, store


def _cmd_init(args: argparse.Namespace) -> int:
    root = paths.require_tide_root()
    canon_directory = store.init(root, name=args.name, lang=args.lang, force=args.force)
    print("canon ready: {0}".format(canon_directory))
    return 0


def _cmd_rev(args: argparse.Namespace) -> int:
    root = paths.require_tide_root()
    print(rev.compute(root))
    return 0


def _resolve_arc_dir(root, ref: str):
    """Find the arc entry dir under ``arcs/`` whose slug matches *ref*."""
    arcs = paths.arcs_dir(root)
    if not arcs.is_dir():
        return None
    # Exact dir name first, then __…__-tolerant slug match.
    exact = arcs / ref
    if exact.is_dir():
        return exact
    for entry in arcs.iterdir():
        if entry.is_dir() and entry.name != "candidates" and slug.ref_matches(ref, entry.name):
            return entry
    return None


def _cmd_merge(args: argparse.Namespace) -> int:
    # cli.main wraps RoleError → exit 1; import lazily to avoid a cycle.
    from ..cli import require_orchestrator

    root = paths.require_tide_root()
    arc_dir = _resolve_arc_dir(root, args.arc)
    if arc_dir is None:
        print("tide: no arc matching {0!r}".format(args.arc), file=sys.stderr)
        return 1
    arc_slug = slug.entry_slug(arc_dir.name)

    # --preview is a read-only dry-run: show the prospective CANON.md diff and
    # commit nothing. Allowed for any role (review-then-commit); the actual merge
    # below stays orchestrator-only.
    if getattr(args, "preview", False):
        try:
            current, prospective = merge.preview_delta(root, arc_dir, slug=arc_slug)
        except FileNotFoundError as exc:
            print("tide: {0}".format(exc), file=sys.stderr)
            return 1
        diff = merge.unified_diff(current, prospective)
        if not diff.strip():
            print(
                "tide: preview {0} — no change to CANON.md "
                "(already merged / empty delta)".format(arc_slug)
            )
        else:
            print("tide: preview merge {0} → CANON.md (NOT committed):".format(arc_slug))
            print(diff)
        return 0

    require_orchestrator("canon merge")
    try:
        new_rev = merge.merge_delta(root, arc_dir, slug=arc_slug)
    except FileNotFoundError as exc:
        print("tide: {0}".format(exc), file=sys.stderr)
        return 1
    print("merged {0} → canon-rev {1}".format(arc_slug, new_rev))
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    """Rename a legacy ``.tide/cannon/`` → ``.tide/canon/`` (atomic, idempotent).

    Refuses loudly (via :class:`CanonMigrateError`, caught by ``cli.main``) when both
    dirs coexist. ``--dry-run`` prints the plan and changes nothing.
    """
    from . import migrate as canon_migrate

    root = paths.require_tide_root()
    plan = canon_migrate.plan(root)
    if plan.coexist:
        # Loud refusal — single source of the message; cli.main prints + exits 1.
        raise canon_migrate.CanonMigrateError(canon_migrate.coexist_message(plan))
    if getattr(args, "dry_run", False):
        print(canon_migrate.render_plan(plan))
        return 0
    result = canon_migrate.apply(plan)
    print(canon_migrate.render_result(result))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    from . import board

    return board.cmd_status(args)


def _cmd_gate(args: argparse.Namespace) -> int:
    """Run the M1 tri-state canon-gate oracle and return a POSIX exit code.

    Exit codes: 0 = current, 1 = stale, 2 = oracle-error.  Code 2 is
    FAIL-LOUD: callers (shells, hooks, CI, Orca --precheck) MUST treat it as
    an alert, never as a silent skip.
    """
    from .. import gate

    try:
        root = paths.require_tide_root()
    except FileNotFoundError as exc:
        print("canon gate: oracle-error", file=sys.stderr)
        print("  oracle-error: {0}".format(exc), file=sys.stderr)
        return 2

    code, reasons = gate.decide(root)

    if code == 0:
        print("canon gate: current")
    elif code == 1:
        print("canon gate: stale ({0} issue(s))".format(len(reasons)))
        for r in reasons:
            print("  - {0}".format(r))
    else:  # code == 2
        print("canon gate: oracle-error (code 2)", file=sys.stderr)
        for r in reasons:
            print("  {0}".format(r), file=sys.stderr)

    return code


def register(subparsers) -> None:
    """Add the ``canon`` command group to *subparsers* (called by cli.py).

    ``cannon`` is registered as a hidden alias so ``tide cannon …`` keeps working
    for existing scripts and muscle memory.
    """
    p = subparsers.add_parser(
        "canon",
        aliases=["cannon"],  # back-compat alias; hidden from help
        help="durable truth: init/status/merge/rev/gate",
    )
    nsub = p.add_subparsers(dest="canon_cmd")

    ip = nsub.add_parser("init", help="seed a project's canon/ (CANON.md + config)")
    ip.add_argument("--name", help="project name in the CANON.md header (default: dir name)")
    ip.add_argument("--lang", default=store.DEFAULT_LANG, help="canon/config lang (default: en)")
    ip.add_argument("--force", action="store_true", help="overwrite existing CANON.md/config")
    ip.set_defaults(func=_cmd_init, _cmd="canon init")

    sp = nsub.add_parser("status", help="scan per-arc homes, group by state")
    sp.set_defaults(func=_cmd_status, _cmd="canon status")

    migp = nsub.add_parser(
        "migrate",
        help="rename a legacy .tide/cannon/ → .tide/canon/ (atomic, idempotent, loud on coexistence)",
    )
    migp.add_argument(
        "--dry-run",
        action="store_true",
        help="print the rename + stamp-rewrite plan and change nothing",
    )
    migp.set_defaults(func=_cmd_migrate, _cmd="canon migrate")

    mp = nsub.add_parser("merge", help="ORCHESTRATOR-ONLY: merge an arc delta into CANON.md")
    mp.add_argument("arc", help="arc slug (or dir name) whose delta.md to merge")
    mp.add_argument(
        "--preview",
        action="store_true",
        help="dry-run: print the prospective CANON.md diff, commit nothing (any role)",
    )
    mp.set_defaults(func=_cmd_merge, _cmd="canon merge")

    rp = nsub.add_parser("rev", help="print the current canon-rev (sha256 of CANON.md)")
    rp.set_defaults(func=_cmd_rev, _cmd="canon rev")

    gp = nsub.add_parser(
        "gate",
        help="M1 tri-state oracle: 0=current 1=stale 2=oracle-error (POSIX exit code)",
    )
    gp.set_defaults(func=_cmd_gate, _cmd="canon gate")

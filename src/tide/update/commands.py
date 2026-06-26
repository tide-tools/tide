"""tide.update.commands — the thin ``tide self-update`` CLI handler.

Modes (mutually-light; the default is detect→gate→apply):

* ``tide self-update``            detect staleness → if stale, run the regression
                                  gate → on GREEN reinstall + stamp; on RED refuse.
* ``tide self-update --check``    report staleness only (no gate, no install).
                                  Exit 0 = current, 1 = update available, 2 = no
                                  source (POSIX-ish, mirrors tide.gate's tri-state).
* ``tide self-update --force``    reinstall even when already current (still gated).
* ``tide self-update --no-suite`` portable-only gate (skip the suite — weaker).
* ``tide self-update --dry-run``  show the resolved source + install command; act not.

Logic lives in :mod:`tide.update.core` / :mod:`tide.update.source`; this file is
argparse + printing only.
"""

from __future__ import annotations

from . import core
from .source import resolve_source

NO_SOURCE_MSG = (
    "tide self-update: no local source resolvable (not a local/editable install). "
    "A published update channel is not built yet (crit E) — nothing to update against."
)


def _cmd_self_update(args) -> int:
    source = resolve_source()
    if source is None:
        print(NO_SOURCE_MSG)
        return 2

    if getattr(args, "check", False):
        return _cmd_check(source)

    if getattr(args, "dry_run", False):
        return _cmd_dry_run(source, args)

    result = core.self_update(
        source,
        force=getattr(args, "force", False),
        run_suite=not getattr(args, "no_suite", False),
    )
    print("tide self-update [{0}]".format(result.source_name))
    for line in result.messages:
        print("  " + line)
    if result.accepted:
        return 0
    return 1


def _cmd_check(source) -> int:
    status = core.check_for_update(source)
    print("tide self-update --check [{0}]".format(status.source_name))
    print("  installed: {0}".format(status.installed))
    print("  available: {0}".format(status.available))
    if status.stale:
        print("  → UPDATE AVAILABLE (run 'tide self-update' to gate + apply)")
        return 1
    print("  → current")
    return 0


def _cmd_dry_run(source, args) -> int:
    status = core.check_for_update(source)
    print("tide self-update --dry-run [{0}] (nothing applied)".format(status.source_name))
    print("  source:    {0}".format(getattr(source, "source_dir", "?")))
    print("  installed: {0}".format(status.installed))
    print("  available: {0}".format(status.available))
    print("  stale:     {0}".format(status.stale))
    suite = "skipped (--no-suite)" if getattr(args, "no_suite", False) else "yes"
    print("  would gate: verify --portable + suite={0}".format(suite))
    print("  would run:  {0}".format(" ".join(source.install_command())))
    return 0


def register(subparsers) -> None:
    """Add the top-level ``self-update`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "self-update",
        help="keep tide current: detect a stale install vs source, gate, reinstall",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="report staleness only (exit 1 if an update is available); no install",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="reinstall even when already current (still gated)",
    )
    p.add_argument(
        "--no-suite",
        action="store_true",
        dest="no_suite",
        help="run a portable-only gate (skip the test suite — weaker, say so)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="show the resolved source + install command without acting",
    )
    p.set_defaults(func=_cmd_self_update, _cmd="self-update")

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
from .source import (
    PublishedChannelSource,
    default_rollback_path,
    is_clone_install,
    is_editable_install,
    read_rollback,
    resolve_source,
)

NO_SOURCE_MSG = (
    "tide self-update: no update source resolvable (neither a local/editable "
    "install nor a published channel) — nothing to update against."
)


def _cmd_self_update(args) -> int:
    source = resolve_source()

    if getattr(args, "rollback", False):
        return _cmd_rollback(source, dry_run=getattr(args, "dry_run", False))

    if source is None:
        print(NO_SOURCE_MSG)
        return 2

    if getattr(args, "stamp", False):
        return _cmd_stamp(source)

    if getattr(args, "check", False):
        return _cmd_check(source)

    if getattr(args, "dry_run", False):
        return _cmd_dry_run(source, args)

    force = getattr(args, "force", False)
    run_suite = not getattr(args, "no_suite", False)
    if isinstance(source, PublishedChannelSource):
        result = core.self_update_published(source, force=force, run_suite=run_suite)
    else:
        result = core.self_update(
            source, force=force, run_suite=run_suite,
            pull=not getattr(args, "no_pull", False),
        )
    print("tide self-update [{0}]".format(result.source_name))
    for line in result.messages:
        print("  " + line)
    if result.accepted:
        return 0
    return 1


def _cmd_rollback(source, *, dry_run: bool = False) -> int:
    """``--rollback`` (and ``--rollback --dry-run``): go back one version.

    The dry run is what makes rollback SHOWABLE: it prints the recorded recovery
    point and the exact command that would replay it, without touching anything.
    """
    path = default_rollback_path()

    if source is not None and is_editable_install(source):
        source_dir = getattr(source, "source_dir", "?")
        print("tide self-update --rollback")
        print("  editable install — there is no package to roll back")
        print("  your versions are git history; go back with:")
        print("    git -C {0} log --oneline    # find the good commit".format(source_dir))
        print("    git -C {0} checkout <commit>".format(source_dir))
        return 2

    recorded = read_rollback(path)
    if dry_run:
        print("tide self-update --rollback --dry-run (nothing applied)")
        print("  marker:    {0}".format(path))
        if not recorded or not recorded.get("command"):
            print("  → no rollback point recorded yet — nothing to roll back to")
            print("     (one is written automatically just before the next update lands)")
            return 2
        print("  would return to: {0}".format(recorded.get("version", "?")))
        print("  would run:       {0}".format(" ".join(recorded["command"])))
        print("  then smoke-test:  tide version")
        return 0

    result = core.rollback(path)
    print("tide self-update --rollback")
    for line in result.messages:
        print("  " + line)
    if not result.ok and result.target is None:
        return 2  # no marker — nothing to roll back to
    return 0 if result.ok else 1


def _cmd_stamp(source) -> int:
    """``--stamp``: record the CURRENT source as what is installed. Installs nothing.

    Called by ``install.sh`` right after it installs. Without it a fresh clone
    install has no marker, so ``installed()`` falls back to the bare metadata
    version while ``available()`` carries the checkout's commit — the two never
    match and tide greets a brand-new install with "update available", pointing at
    the very commit it was just installed from. Nagging someone about an update
    they already have is how people learn to ignore the nag.
    """
    if not hasattr(source, "record_install"):
        print("tide self-update --stamp: this source keeps no install marker")
        return 2
    try:
        rev = source.record_install()
    except OSError as exc:
        print("tide self-update --stamp: could not write the marker: {0}".format(exc))
        return 1
    print("tide self-update --stamp: recorded {0} as installed".format(rev))
    return 0


def _cmd_check(source) -> int:
    status = core.check_for_update(source)
    print("tide self-update --check [{0}]".format(status.source_name))
    print("  installed: {0}".format(status.installed))
    print("  available: {0}".format(status.available))
    if is_editable_install(source):
        for line in core.editable_noop_messages(source):
            print("  " + line)
        return 0
    if status.stale:
        print("  → UPDATE AVAILABLE (run 'tide self-update' to gate + apply)")
        return 1
    if is_clone_install(source):
        # Say what "current" was measured against. For a clone install the answer
        # is the LOCAL checkout, which only moves when something pulls it — so
        # "current" here does not yet mean "current with the release repo".
        upstream = source.upstream()
        if upstream:
            print("  → current with your local checkout; 'tide self-update' pulls "
                  "{0} first, then reinstalls".format(upstream))
        else:
            print("  → current with your local checkout — which tracks no remote, "
                  "so nothing can bring a newer tide in")
        return 0
    print("  → current")
    return 0


def _cmd_dry_run(source, args) -> int:
    status = core.check_for_update(source)
    print("tide self-update --dry-run [{0}] (nothing applied)".format(status.source_name))
    print("  source:    {0}".format(getattr(source, "source_dir", "?")))
    print("  installed: {0}".format(status.installed))
    print("  available: {0}".format(status.available))
    print("  stale:     {0}".format(status.stale))
    if is_editable_install(source):
        for line in core.editable_noop_messages(source, force=getattr(args, "force", False)):
            print("  " + line)
        return 0
    if is_clone_install(source) and not getattr(args, "no_pull", False):
        upstream = source.upstream()
        if upstream:
            print("  would pull:  {0}  (from {1})".format(
                " ".join(source.pull_command()), upstream))
        else:
            print("  would pull:  nothing — this checkout tracks no remote, so there "
                  "is nowhere to get a newer tide from")
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
        "--stamp",
        action="store_true",
        help=(
            "record the current source as installed and stop (no gate, no install) "
            "— what install.sh calls so a fresh install does not report itself stale"
        ),
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
    p.add_argument(
        "--no-pull",
        action="store_true",
        dest="no_pull",
        help=(
            "for a clone install: reinstall from the checkout AS IT IS, without "
            "fast-forwarding it first"
        ),
    )
    p.add_argument(
        "--rollback",
        action="store_true",
        help=(
            "reinstall the previous pinned version recorded before the last update "
            "(add --dry-run to see the recovery point without applying it)"
        ),
    )
    p.set_defaults(func=_cmd_self_update, _cmd="self-update")

"""tide.release.commands — the ``tide release`` CLI handler (argparse + printing).

    tide release --dry-run          resolve + gate + build, print every step, act not
    tide release                    the same plan, applied (asks first)
    tide release --version 1.1.0    cut a specific version (bumps pyproject)
    tide release --no-tap           tag + GitHub release only, leave brew for later
    tide release --yes              skip the confirmation (for a scripted cut)

What a release IS: a pushed tag plus a GitHub release carrying the artifact. That
is the whole front door, because people install tide with ``git clone`` +
``./install.sh``. Bumping the Homebrew formula is a SECONDARY channel that rides
along when a tap checkout is available and is skipped without ceremony when not.

The default is NOT the dry run — a command whose safe mode is the default trains
people to type ``--force``. Instead the real run resolves the whole plan, prints
it exactly as the dry run would, and THEN asks. You always see the release before
you agree to it.
"""

from __future__ import annotations

from pathlib import Path

from . import core


def _print_plan(plan: core.ReleasePlan, *, show_formula: bool) -> None:
    print("tide release {0}  [{1}]".format(plan.version, plan.github_repo))
    print("  repo:     {0}".format(plan.repo))
    print("  ref:      {0}".format(plan.ref))
    print("  asset:    {0}".format(plan.asset_url))
    if plan.artifact is not None:
        print("  artifact: {0}".format(plan.artifact))
    if plan.sha256:
        print("  sha256:   {0}".format(plan.sha256))
    print("  tap:      {0}".format(plan.tap_dir or "— not tapped locally —"))

    print("\n  preflight")
    for c in plan.checks:
        print("    {0} {1:<18} {2}".format("✓" if c.ok else "✗", c.name, c.detail))

    if plan.gate is not None:
        print("\n  regression gate: {0}".format("GREEN" if plan.gate.ok else "RED"))
        for line in plan.gate.messages:
            print("    " + line)

    for note in plan.notes:
        print("\n  note: {0}".format(note))

    print("\n  steps ({0})".format(len(plan.steps)))
    for i, step in enumerate(plan.steps, 1):
        print("    {0}. {1:<18} {2}".format(i, step.name, step.detail))
        rendered = step.render()
        if rendered != step.detail:
            print("       $ {0}".format(rendered))

    if show_formula:
        preview = core.formula_preview(plan)
        if preview:
            print("\n  formula as it would be written")
            for line in preview.splitlines():
                if line.strip().startswith(("url ", "sha256 ", "assert_match")):
                    print("    | " + line.strip())


def _cmd_release(args) -> int:
    repo = Path(args.repo).expanduser() if args.repo else Path.cwd()
    plan = core.plan_release(
        repo,
        version=args.version,
        ref=args.ref,
        branch=args.branch,
        github_repo=args.github_repo,
        tap=args.tap,
        tap_dir=Path(args.tap_dir).expanduser() if args.tap_dir else None,
        update_tap=not args.no_tap,
        allow_dirty=args.allow_dirty,
        run_gate=not args.no_gate,
        run_suite=not args.no_suite,
    )

    dry = bool(args.dry_run)
    _print_plan(plan, show_formula=not args.no_tap)

    if not plan.ok:
        print("\n  ✗ REFUSED — {0}".format(
            "; ".join(c.name for c in plan.blockers) or "the regression gate is red"
        ))
        print("    nothing was published. Fix the above and re-run.")
        return 1

    if dry:
        print("\n  ✓ dry run — the plan is green; NOTHING was published.")
        print("    the artifact above is real and its sha256 is the one the formula would pin.")
        print("    to cut it for real: tide release{0}".format(
            "" if args.version is None else " --version " + args.version
        ))
        return 0

    if not args.yes:
        tail = "" if (args.no_tap or plan.tap_dir is None) else " and push the tap formula"
        print("\n  this WILL push a tag, create a GitHub release{0}.".format(tail))
        try:
            answer = input("  type the version to confirm ({0}): ".format(plan.version)).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  aborted — nothing was published.")
            return 1
        if answer != plan.version:
            print("  aborted — nothing was published.")
            return 1

    result = core.apply_release(plan)
    print()
    for name in result.done:
        print("  ✓ {0}".format(name))
    for line in result.messages:
        print("  {0}".format(line))
    if result.ok:
        print("\n  the front door — anyone can now install or update with:")
        print("    git clone https://github.com/{0}.git && cd tide && ./install.sh".format(
            args.github_repo
        ))
        if not args.no_tap and plan.tap_dir is not None:
            print("\n  and on the brew channel:")
            print("    brew install {0}/tide".format(args.tap))
        return 0
    return 1


def register(subparsers) -> None:
    """Add the top-level ``release`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "release",
        help="cut a release: gate, build the artifact, tag + publish it (brew formula rides along)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="resolve + gate + build and print the whole plan, publishing nothing",
    )
    p.add_argument("--version", default=None, help="version to cut (default: pyproject's)")
    p.add_argument("--repo", default=None, help="the tide checkout to release (default: cwd)")
    p.add_argument("--ref", default="HEAD", help="git ref to build the artifact from")
    p.add_argument(
        "--branch", default=core.DEFAULT_BRANCH, help="branch a release must be cut from"
    )
    p.add_argument(
        "--github-repo", default="tide-tools/tide", dest="github_repo",
        help="owner/name the release is published to",
    )
    p.add_argument("--tap", default="tide-tools/tide", help="the Homebrew tap to bump")
    p.add_argument(
        "--tap-dir", default=None, dest="tap_dir",
        help="local checkout of the tap (default: the one brew already has)",
    )
    p.add_argument(
        "--no-tap", action="store_true", dest="no_tap",
        help=(
            "do not touch the brew formula — the tag and the GitHub release ARE the "
            "release; Homebrew is a secondary channel you can bump later"
        ),
    )
    p.add_argument(
        "--allow-dirty", action="store_true", dest="allow_dirty",
        help="release with an uncommitted tree (the artifact still comes from the commit)",
    )
    p.add_argument(
        "--no-gate", action="store_true", dest="no_gate",
        help="skip the regression gate entirely (weaker — say so)",
    )
    p.add_argument(
        "--no-suite", action="store_true", dest="no_suite",
        help="gate on verify --portable only, without the test suite (weaker)",
    )
    p.add_argument(
        "--yes", action="store_true", help="skip the interactive confirmation"
    )
    p.set_defaults(func=_cmd_release, _cmd="release")

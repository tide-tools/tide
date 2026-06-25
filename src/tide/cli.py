"""tide — single binary, namespaced subcommands.

This is the CLI root. It wires every subcommand group via a uniform
``register(subparsers)`` / handler pattern (see README "## build conventions").

SCAFFOLD STATE: the groups below are registered as STUBS. Each later build unit
replaces its ``_register_*`` body with a call into the real module's
``register(subparsers)`` (the module owns the parser + thin handler; logic lives
in plain, argparse-free module functions so it stays unit-testable).

Roles: commands that mutate shared truth (``cannon merge``, ``candidate
promote``) are ORCHESTRATOR-ONLY and hard-refuse unless TIDE_ROLE=orchestrator.
The role check lives in ``require_orchestrator`` so every gated handler reuses it.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import __version__
from .arc.stream import StreamError

ROLE_ENV = "TIDE_ROLE"
ROLE_ORCHESTRATOR = "orchestrator"
ROLE_WORKER = "worker"


class RoleError(SystemExit):
    """Raised (as a nonzero exit) when a worker attempts an orchestrator-only op."""


def current_role() -> str:
    """Return the active role from $TIDE_ROLE; default 'worker' (least privilege)."""
    return os.environ.get(ROLE_ENV, ROLE_WORKER).strip().lower() or ROLE_WORKER


def require_orchestrator(action: str) -> None:
    """Hard-refuse an orchestrator-only action unless TIDE_ROLE=orchestrator.

    Used by ``cannon merge`` and ``candidate promote``. Raises a nonzero
    SystemExit with a clear message — workers literally cannot run these.
    """
    role = current_role()
    if role != ROLE_ORCHESTRATOR:
        raise RoleError(
            "refused: '{action}' is orchestrator-only "
            "(TIDE_ROLE={role!r}; set TIDE_ROLE={orch} to run it)".format(
                action=action, role=role, orch=ROLE_ORCHESTRATOR
            )
        )


# --- stub plumbing ---------------------------------------------------------
# Every group registers subparsers whose default handler is _stub. As units land
# they swap _stub for the module's real handler. The unit tag tells the reader
# (and the next builder) exactly which unit owns the wiring.

def _stub(unit: str):
    def _handler(args: argparse.Namespace) -> int:
        print(
            "tide: '{cmd}' is not implemented yet (build unit {unit})".format(
                cmd=getattr(args, "_cmd", "?"), unit=unit
            ),
            file=sys.stderr,
        )
        return 2

    return _handler


def _add_stub(sub, name: str, help_text: str, unit: str) -> argparse.ArgumentParser:
    p = sub.add_parser(name, help=help_text)
    p.set_defaults(func=_stub(unit), _cmd=name)
    return p


# --- group registration points --------------------------------------------
# TODO(U9): replace each _register_* below with `from .<module> import register;
# register(subparsers)` once the owning unit lands. Keep the handler thin.

def _register_init(sub) -> None:
    # U9: real control-home unfold (+ dogfood .tide + roster + README + optional git).
    from .init_home import register as register_init

    register_init(sub)


def _register_status(sub) -> None:
    # U8: real STREAM board (computed N/M + CANDIDATES + drift/unmerged flags).
    from .arc.board import cmd_status

    p = sub.add_parser("status", help="render the STREAM board for the current project")
    p.add_argument("--all", action="store_true", help="roster-wide status")
    p.set_defaults(func=cmd_status, _cmd="status")


def _register_strictness(sub) -> None:
    # U5: real per-project strict|loose dial (show/set in .tide/state/strictness).
    from .strictness import register as register_strictness

    register_strictness(sub)


def _register_install_hooks(sub) -> None:
    # U10: real install-hooks (SessionStart + PreToolUse, merge-not-clobber).
    from .hooks.install import register as register_install

    register_install(sub)


def _register_hook(sub) -> None:
    # U10: internal dispatch group the installed settings.json calls
    # (`tide hook session-start` / `tide hook edit-gate`).
    from .hooks.install import register_hook_group

    register_hook_group(sub)


def _register_roster(sub) -> None:
    # U5: real control-home 'name | path' registry (add/rm/ls).
    from .roster import register as register_roster

    register_roster(sub)


def _register_menu(sub) -> None:
    # U11: the launcher menu (pick N roster projects → seeded orchestrator sessions).
    # Bare `tide` still prints help; the menu lives under the explicit `tide menu`.
    from .launcher.menu import register as register_menu

    register_menu(sub)


def _register_context(sub) -> None:
    # U13: per-project launch context profile — show the resolved scoped command
    # (strict MCP scoping; lean default loads no global MCP servers).
    from .launcher.context import register as register_context

    register_context(sub)


def _register_terminal(sub) -> None:
    # tide terminal — native absorption of ~/.local/bin/tide-go: exec a clean,
    # logged-in, seeded claude session IN the current terminal (os.execvp, no
    # spawn). Reuses the scoped builder; adds --disable-slash-commands; never
    # --bare (that would drop OAuth auth living in ~/.claude.json).
    from .launcher.terminal import register as register_terminal

    register_terminal(sub)


def _register_go(sub) -> None:
    # tide go — the light ENTRY dispatcher (symmetric mirror of `tide handoff`, the
    # exit). Asks "resume prior work or start new?", resolves a seed, and delegates
    # the launch to `tide terminal` (the single scoped+skip-perms in-place path).
    # ~/.local/bin/tide-go is a thin alias onto this, preserving the loved name.
    from .launcher.go import register as register_go

    register_go(sub)


def _register_handoff(sub) -> None:
    # U12: warm-handoff (`tide handoff <arc>`) — distil chat → arc workspace, remind
    # candidates, fork continue|new|close, auto-spawn a fresh session (toggle ON).
    # This is the CLI the /tide-handoff skill drives.
    from .launcher.handoff import register as register_handoff

    register_handoff(sub)


def _register_verify(sub) -> None:
    # F7: isolated verification affordance — stage a built artifact into a temp dir,
    # serve it on an OS-assigned ephemeral port, and check it (HTTP 200 + optional
    # node inline-script syntax smoke). stdlib-only; no fixed-port collisions.
    from .verify import register as register_verify

    register_verify(sub)


def _register_arc(sub) -> None:
    # U3: real arc-stream verbs (new/new-goal/open/resume/close/reopen/supersede).
    # 11-arc-worktree-isolation: adds work/land verbs via worktree.register.
    # TODO(U4): tide.arc.candidate is a separate top-level group; status is U8.
    from .arc.board import cmd_status as arc_status
    from .arc.stream import register as register_stream
    from .arc.worktree import register as register_worktree

    p = sub.add_parser("arc", help="work stream: new/open/close/reopen/supersede/work/land")
    asub = p.add_subparsers(dest="arc_cmd")
    register_stream(asub)
    register_worktree(asub)
    sp = asub.add_parser("status", help="render the STREAM board")
    sp.set_defaults(func=arc_status, _cmd="arc status")


def _register_candidate(sub) -> None:
    # U4: real candidate group (add/list/promote; promote=orchestrator-only).
    from .arc.candidate import register as register_candidate

    register_candidate(sub)


def _register_cannon(sub) -> None:
    # U2: real cannon group (init/rev/merge live; status stubbed for U8).
    from .cannon.commands import register as register_cannon

    register_cannon(sub)


def _register_contract(sub) -> None:
    # U6: real contract group (lifecycle new/sign/report/proof/accept/close/
    # reopen/state/list + ask/answer; close=orchestrator-only via the handler).
    from .contract.lifecycle import register as register_contract

    register_contract(sub)


def _cmd_version(args: argparse.Namespace) -> int:
    """``tide version`` — the command form of ``--version`` (same string)."""
    print("tide {0}".format(__version__))
    return 0


def _cmd_help(args: argparse.Namespace) -> int:
    """``tide help`` — print the root help (the command form of ``-h``)."""
    build_parser().print_help()
    return 0


def _register_version(sub) -> None:
    p = sub.add_parser("version", help="print the tide version")
    p.set_defaults(func=_cmd_version, _cmd="version")


def _register_help(sub) -> None:
    p = sub.add_parser("help", help="show this help (every command group)")
    p.set_defaults(func=_cmd_help, _cmd="help")


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse root with every (currently stubbed) group wired."""
    parser = argparse.ArgumentParser(
        prog="tide",
        description="tide — simplified, synchronous orchestration machine (no autonomy).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="tide {0}".format(__version__),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # Human launcher surface
    _register_init(subparsers)
    _register_status(subparsers)
    _register_strictness(subparsers)
    _register_install_hooks(subparsers)
    _register_roster(subparsers)
    _register_menu(subparsers)
    _register_context(subparsers)
    _register_terminal(subparsers)
    _register_go(subparsers)
    _register_handoff(subparsers)
    _register_verify(subparsers)

    _register_version(subparsers)
    _register_help(subparsers)

    # Agent module surface
    _register_arc(subparsers)
    _register_candidate(subparsers)
    _register_cannon(subparsers)
    _register_contract(subparsers)

    # Internal hook-dispatch group (the commands install-hooks writes into settings.json)
    _register_hook(subparsers)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point for the ``tide`` console_script."""
    parser = build_parser()
    args = parser.parse_args(argv)

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0

    try:
        return int(func(args) or 0)
    except RoleError as exc:
        print("tide: {0}".format(exc), file=sys.stderr)
        return 1
    except (StreamError, FileNotFoundError) as exc:
        print("tide: {0}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

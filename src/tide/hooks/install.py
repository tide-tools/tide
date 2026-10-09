"""tide.hooks.install — ``tide install-hooks``: wire the Claude Code hooks.

The merge-safe wiring itself lives DOWN in :mod:`tide.harness` (domain) — the
launcher ensures it on every spawn, so a project can't strand a session half-alive
just because nobody ran the one-shot command (live 14.07: forge had no hooks, the
handoff flip physically couldn't fire, the board kept painting «поднимается»).
This module is the thin adapter above it: the ``tide install-hooks`` CLI verb and
the internal ``tide hook …`` dispatch group that settings.json entries call back
into. The wiring names are re-exported so existing imports keep working.
"""

from __future__ import annotations

# Re-exports: the wiring machinery moved down to tide.harness (domain) so the
# launcher may ensure it on spawn without importing an edge module upward.
from ..harness import (  # noqa: F401 — public re-exports
    ASK_GATE_CMD,
    ASK_GATE_MATCHER,
    PERM_NOTIFY_CMD,
    PERMISSION_REQUEST_EVENT,
    CLAUDE_DIRNAME,
    CONTEXT_WATCH_CMD,
    EDIT_GATE_CMD,
    EDIT_MATCHER,
    HANDOFF_CONFIRM_CMD,
    HOOKS_KEY,
    InstallError,
    OFFLOAD_NUDGE_CMD,
    PRE_TOOL_USE_EVENT,
    ROLE_GATE_CMD,
    ROLE_GATE_MATCHER,
    SESSION_END_CMD,
    SESSION_END_EVENT,
    SESSION_START_CMD,
    SESSION_START_EVENT,
    SETTINGS_FILE,
    STOP_EVENT,
    USER_PROMPT_EVENT,
    _command_present,
    _hook_block,
    TIDE_HOOKS,
    _load,
    install_hooks,
    install_hooks_in_worktree,
    merge_ask_gate,
    merge_perm_notify,
    merge_hooks,
    merge_pre_tool_use,
    merge_role_gate,
    merge_session_end,
    merge_session_start,
    merge_stop_context_watch,
    merge_stop_nudge,
    merge_user_prompt,
    missing_hooks,
    missing_hooks_at,
    settings_path,
)
from . import edit_gate, role_gate, session_start


# --- CLI wiring ------------------------------------------------------------

def _cmd_install_hooks(args) -> int:
    from .. import paths

    if getattr(args, "worktree", False):
        return _cmd_install_worktree()
    root = paths.require_tide_root()
    path, notes = install_hooks(root)
    print("tide: hooks wired in {0}".format(path))
    if notes:
        for note in notes:
            print("  + {0}".format(note))
    else:
        print("  (already wired — nothing to change)")
    _ensure_ao(root)
    return 0


def _ensure_ao(root) -> None:
    """Main tree registered in AO → make its AO sessions get the hooks too
    (``tide ao ensure`` for this project). Best-effort: never fails install-hooks."""
    try:
        from ..adapters import ao

        res = ao.ensure_hooks_for_tree(root)
    except Exception as exc:  # noqa: BLE001 — AO trouble must not fail the hooks
        print("  ! AO config not checked: {0}".format(exc))
        return
    if res is not None and (res.changed or res.notes):
        print("  + AO {0}: {1}".format(res.pid, "; ".join(res.added) or "checked"))
        for note in res.notes:
            print("  ! {0}".format(note))


def _cmd_install_worktree() -> int:
    """``--worktree``: the AO ``postCreate`` step — wire THIS directory, as is.

    No climb to the main tree (that mapping is exactly what strands the worktree),
    and always exit 0: a failed postCreate fails AO's whole spawn."""
    import os
    import sys
    from pathlib import Path

    try:
        path, notes = install_hooks_in_worktree(Path(os.getcwd()))
    except Exception as exc:  # noqa: BLE001 — never fail the spawn over hooks
        print("tide: worktree hooks skipped: {0}".format(exc), file=sys.stderr)
        return 0
    if (Path(os.getcwd()) / CLAUDE_DIRNAME).is_symlink():
        print("tide: .claude is a link to the main tree — {0}{1}".format(
            "hooks merged into " + str(path) if notes else "all hooks already in " + str(path),
            "".join("\n  + " + n for n in notes)))
    else:
        print("tide: hooks wired in {0}{1}".format(
            path, "".join("\n  + " + n for n in notes) or " (already wired)"))
    return 0


def register(subparsers) -> None:
    """Add the top-level ``install-hooks`` command (called by cli.py)."""
    p = subparsers.add_parser(
        "install-hooks",
        help="wire the Claude Code hooks into .claude/settings.json (merge-safe)",
    )
    p.add_argument(
        "--worktree", action="store_true",
        help="wire the current directory itself (AO postCreate in a session worktree)",
    )
    p.set_defaults(func=_cmd_install_hooks, _cmd="install-hooks")


def register_hook_group(subparsers) -> None:
    """Add the internal ``hook`` dispatch group (the commands settings.json calls)."""
    p = subparsers.add_parser(
        "hook", help="internal: dispatched Claude Code hooks (session-start/edit-gate)"
    )
    hsub = p.add_subparsers(dest="hook_cmd", metavar="<hook>")

    ss = hsub.add_parser("session-start", help="SessionStart: board + role + warnings")
    ss.set_defaults(func=session_start.cmd_session_start, _cmd="hook session-start")

    eg = hsub.add_parser("edit-gate", help="PreToolUse: block edits with no open arc")
    eg.set_defaults(func=edit_gate.cmd_edit_gate, _cmd="hook edit-gate")

    rg = hsub.add_parser(
        "role-gate",
        help="PreToolUse: deny orchestrator from doing worker-work (Write/Edit/mutating Bash)",
    )
    # The manikin: ask the gate how it would judge a command instead of running
    # it to find out. --explain-file keeps the probe string off the shell's hands
    # entirely, which is the safe way to test a gate (candidate 166).
    rg.add_argument(
        "--explain", metavar="COMMAND",
        help="dry-run: print how the gate would judge COMMAND, execute nothing",
    )
    rg.add_argument(
        "--explain-file", metavar="PATH",
        help="same, reading the command from PATH (safest — the shell never sees it)",
    )
    rg.set_defaults(func=role_gate.cmd_role_gate, _cmd="hook role-gate")

    ag = hsub.add_parser(
        "ask-gate",
        help="PreToolUse: in an AO session with no human in the window, "
             "AskUserQuestion goes to the manager, not a dialog",
    )
    from .ask_gate import cmd_ask_gate  # lazy: keep hook wiring import-light
    ag.set_defaults(func=cmd_ask_gate, _cmd="hook ask-gate")

    tg = hsub.add_parser(
        "truth-gate",
        help="PreToolUse/PostToolUse: decisions-tg.jsonl / facts.md / decisions-skip.jsonl "
             "of topics — only the context-keeper holder edits them, every edit journaled",
    )
    from .truth_gate import cmd_truth_gate  # lazy: keep hook wiring import-light
    tg.set_defaults(func=cmd_truth_gate, _cmd="hook truth-gate")

    pn = hsub.add_parser(
        "perm-notify",
        help="PermissionRequest: report a pending permission dialog to the human's "
             "Telegram; never allows or denies (SOC-108)",
    )
    from .perm_notify import cmd_perm_notify  # lazy: keep hook wiring import-light
    pn.set_defaults(func=cmd_perm_notify, _cmd="hook perm-notify")

    hc = hsub.add_parser(
        "handoff-confirm",
        help="UserPromptSubmit: first message confirms a picked-up handoff offer",
    )
    # confirm + (у старой, отдавшей нить) пересылка входящего новой — SOC-101 C14
    # + строка «почта: N непрочитанных» сессии нити — SOC-143
    from ..mail import cmd_prompt_hook  # lazy: avoid import cycle
    hc.set_defaults(func=cmd_prompt_hook, _cmd="hook handoff-confirm")

    on = hsub.add_parser(
        "offload-nudge",
        help="Stop: block once when the arc's workspace moved but its passport is stale",
    )
    from ..offload import cmd_offload_nudge  # lazy: keep hook wiring import-light
    on.set_defaults(func=cmd_offload_nudge, _cmd="hook offload-nudge")

    cw = hsub.add_parser(
        "context-watch",
        help="Stop: past the context thresholds block with "
             "`tide handoff continue --auto`",
    )
    from .context_watch import cmd_context_watch  # lazy: keep hook wiring import-light
    cw.set_defaults(func=cmd_context_watch, _cmd="hook context-watch")

    se = hsub.add_parser(
        "session-end",
        help="SessionEnd: stamp ended: on this sid's session passport (closing bookend)",
    )
    from . import session_end  # lazy: keep hook wiring import-light
    se.set_defaults(func=session_end.cmd_session_end, _cmd="hook session-end")

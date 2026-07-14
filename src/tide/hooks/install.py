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
    CLAUDE_DIRNAME,
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
    _load,
    install_hooks,
    merge_hooks,
    merge_pre_tool_use,
    merge_role_gate,
    merge_session_end,
    merge_session_start,
    merge_stop_nudge,
    merge_user_prompt,
    settings_path,
)
from . import edit_gate, role_gate, session_start


# --- CLI wiring ------------------------------------------------------------

def _cmd_install_hooks(args) -> int:
    from .. import paths

    root = paths.require_tide_root()
    path, notes = install_hooks(root)
    print("tide: hooks wired in {0}".format(path))
    if notes:
        for note in notes:
            print("  + {0}".format(note))
    else:
        print("  (already wired — nothing to change)")
    return 0


def register(subparsers) -> None:
    """Add the top-level ``install-hooks`` command (called by cli.py)."""
    p = subparsers.add_parser(
        "install-hooks",
        help="wire the Claude Code hooks into .claude/settings.json (merge-safe)",
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
    rg.set_defaults(func=role_gate.cmd_role_gate, _cmd="hook role-gate")

    hc = hsub.add_parser(
        "handoff-confirm",
        help="UserPromptSubmit: first message confirms a picked-up handoff offer",
    )
    from ..handoff_queue import cmd_handoff_confirm  # lazy: avoid import cycle
    hc.set_defaults(func=cmd_handoff_confirm, _cmd="hook handoff-confirm")

    on = hsub.add_parser(
        "offload-nudge",
        help="Stop: block once when the arc's workspace moved but its passport is stale",
    )
    from ..offload import cmd_offload_nudge  # lazy: keep hook wiring import-light
    on.set_defaults(func=cmd_offload_nudge, _cmd="hook offload-nudge")

    se = hsub.add_parser(
        "session-end",
        help="SessionEnd: stamp ended: on this sid's session passport (closing bookend)",
    )
    from . import session_end  # lazy: keep hook wiring import-light
    se.set_defaults(func=session_end.cmd_session_end, _cmd="hook session-end")

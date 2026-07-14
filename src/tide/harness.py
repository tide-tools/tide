"""tide.harness — the hook floor every tide project must carry (merge-safe wiring).

The session-life mechanics (start-gate floor, handoff flip on first prompt, pulse
nudge, ``ended:`` bookend) all ride Claude Code hooks in the PROJECT's
``.claude/settings.json``. A project that never got them leaves a session
half-alive: the seam's flip physically cannot happen (live 14.07 — forge had no
hooks, the picked-up session worked for 20 minutes while the board honestly kept
painting «поднимается»). So the wiring is DOMAIN mechanics, not a one-shot human
chore: ``tide install-hooks`` calls it, and the launcher ensures it on every spawn.

The single load-bearing rule is **MERGE-not-clobber**: a real ``.claude/
settings.json`` already carries the human's own hooks (e.g. rtk), permissions,
env. We parse the whole document, append our entries to the relevant hook
**event lists** (never replacing them), and re-serialise — so every pre-existing
key and hook survives. Re-running is idempotent: an entry already pointing at our
command is detected and not duplicated.

Pure helpers operate on a settings ``dict``; :func:`install_hooks` does the I/O.
The CLI wiring (``tide install-hooks``, the ``tide hook …`` dispatch group) stays
in :mod:`tide.hooks.install` — the thin adapter above this module.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

from . import io as _io
from .arc.stream import StreamError

# settings.json location + the exact commands/matcher we install.
CLAUDE_DIRNAME = ".claude"
SETTINGS_FILE = "settings.json"
HOOKS_KEY = "hooks"

SESSION_START_EVENT = "SessionStart"
SESSION_END_EVENT = "SessionEnd"
PRE_TOOL_USE_EVENT = "PreToolUse"
USER_PROMPT_EVENT = "UserPromptSubmit"
STOP_EVENT = "Stop"

SESSION_START_CMD = "tide hook session-start"
EDIT_GATE_CMD = "tide hook edit-gate"
EDIT_MATCHER = "Edit|Write|MultiEdit"
ROLE_GATE_CMD = "tide hook role-gate"
ROLE_GATE_MATCHER = "Write|Edit|NotebookEdit|Bash"
HANDOFF_CONFIRM_CMD = "tide hook handoff-confirm"
OFFLOAD_NUDGE_CMD = "tide hook offload-nudge"
SESSION_END_CMD = "tide hook session-end"


class InstallError(StreamError):
    """A hook-install error (e.g. a settings.json that is not valid JSON).

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on
    the same ``except`` arm (prints ``tide: …``, exits nonzero).
    """


def settings_path(root: Path) -> Path:
    """Path to the project ``.claude/settings.json`` for *root*."""
    return Path(root) / CLAUDE_DIRNAME / SETTINGS_FILE


# --- pure merge helpers ----------------------------------------------------

def _load(path: Path) -> dict:
    """Parse an existing settings.json (``{}`` when absent); raise on bad JSON."""
    p = Path(path)
    if not p.is_file():
        return {}
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise InstallError(
            "install-hooks: {0} is not valid JSON ({1}) — fix it before "
            "installing hooks".format(p, exc)
        )
    if not isinstance(data, dict):
        raise InstallError(
            "install-hooks: {0} is not a JSON object".format(p)
        )
    return data


def _command_present(groups: List[dict], command: str) -> bool:
    """True when any hook in *groups* already points at *command* (idempotency)."""
    for group in groups:
        if not isinstance(group, dict):
            continue
        for hook in group.get(HOOKS_KEY, []) or []:
            if isinstance(hook, dict) and hook.get("command") == command:
                return True
    return False


def _hook_block(command: str) -> dict:
    """A single command-hook block ``{"type": "command", "command": …}``."""
    return {"type": "command", "command": command}


def merge_session_start(hooks: dict) -> bool:
    """Append the SessionStart entry to *hooks* (merge-not-clobber); return changed.

    No-op (returns False) when our command is already wired.
    """
    groups = hooks.setdefault(SESSION_START_EVENT, [])
    if _command_present(groups, SESSION_START_CMD):
        return False
    groups.append({HOOKS_KEY: [_hook_block(SESSION_START_CMD)]})
    return True


def merge_pre_tool_use(hooks: dict) -> bool:
    """Append the PreToolUse edit-gate entry to *hooks*; return whether it changed.

    Adds a matcher-scoped group (``Edit|Write|MultiEdit``) alongside any existing
    PreToolUse groups (e.g. the human's own). No-op when already wired.
    """
    groups = hooks.setdefault(PRE_TOOL_USE_EVENT, [])
    if _command_present(groups, EDIT_GATE_CMD):
        return False
    groups.append(
        {"matcher": EDIT_MATCHER, HOOKS_KEY: [_hook_block(EDIT_GATE_CMD)]}
    )
    return True


def merge_role_gate(hooks: dict) -> bool:
    """Append the PreToolUse role-gate entry to *hooks*; return whether it changed.

    Adds a matcher-scoped group (``Write|Edit|NotebookEdit|Bash``) that enforces
    the orchestrator/worker role split. No-op when already wired.
    """
    groups = hooks.setdefault(PRE_TOOL_USE_EVENT, [])
    if _command_present(groups, ROLE_GATE_CMD):
        return False
    groups.append(
        {"matcher": ROLE_GATE_MATCHER, HOOKS_KEY: [_hook_block(ROLE_GATE_CMD)]}
    )
    return True


def merge_user_prompt(hooks: dict) -> bool:
    """Append the UserPromptSubmit handoff-confirm entry to *hooks*; return changed.

    The first human message in a freshly-picked-up session is what confirms a
    pending handoff (status offered → taken). Fires on every prompt but is a no-op
    once nothing is offered for the project. No-op (False) when already wired.
    """
    groups = hooks.setdefault(USER_PROMPT_EVENT, [])
    if _command_present(groups, HANDOFF_CONFIRM_CMD):
        return False
    groups.append({HOOKS_KEY: [_hook_block(HANDOFF_CONFIRM_CMD)]})
    return True


def merge_session_end(hooks: dict) -> bool:
    """Append the SessionEnd entry to *hooks*; return whether it changed.

    The closing bookend: ``ended:`` lands on the session's passport by mechanics,
    so the board tells "conversation finished" from "went quiet". No-op when wired.
    """
    groups = hooks.setdefault(SESSION_END_EVENT, [])
    if _command_present(groups, SESSION_END_CMD):
        return False
    groups.append({HOOKS_KEY: [_hook_block(SESSION_END_CMD)]})
    return True


def merge_stop_nudge(hooks: dict) -> bool:
    """Append the Stop offload-nudge entry to *hooks*; return whether it changed.

    The по-ходовая выгрузка enforcer (cand 40): at end-of-turn, when the arc's
    workspace moved but its passport went untouched past the window, the stop is
    blocked ONCE with the exact ``tide offload`` command. ``stop_hook_active``
    is the anti-loop. No-op (False) when already wired.
    """
    groups = hooks.setdefault(STOP_EVENT, [])
    if _command_present(groups, OFFLOAD_NUDGE_CMD):
        return False
    groups.append({HOOKS_KEY: [_hook_block(OFFLOAD_NUDGE_CMD)]})
    return True


def merge_hooks(data: dict) -> List[str]:
    """Merge both tide hook entries into a settings *data* dict; return notes.

    Mutates *data* in place (creating ``hooks`` if absent) and returns a list of
    human-readable "installed …" notes — empty when everything was already wired.
    """
    hooks = data.setdefault(HOOKS_KEY, {})
    if not isinstance(hooks, dict):
        raise InstallError(
            "install-hooks: existing '{0}' key is not a JSON object".format(HOOKS_KEY)
        )
    notes: List[str] = []
    if merge_session_start(hooks):
        notes.append("{0} → {1}".format(SESSION_START_EVENT, SESSION_START_CMD))
    if merge_pre_tool_use(hooks):
        notes.append(
            "{0} [{1}] → {2}".format(PRE_TOOL_USE_EVENT, EDIT_MATCHER, EDIT_GATE_CMD)
        )
    if merge_role_gate(hooks):
        notes.append(
            "{0} [{1}] → {2}".format(PRE_TOOL_USE_EVENT, ROLE_GATE_MATCHER, ROLE_GATE_CMD)
        )
    if merge_user_prompt(hooks):
        notes.append("{0} → {1}".format(USER_PROMPT_EVENT, HANDOFF_CONFIRM_CMD))
    if merge_stop_nudge(hooks):
        notes.append("{0} → {1}".format(STOP_EVENT, OFFLOAD_NUDGE_CMD))
    if merge_session_end(hooks):
        notes.append("{0} → {1}".format(SESSION_END_EVENT, SESSION_END_CMD))
    return notes


# --- I/O -------------------------------------------------------------------

def install_hooks(root: Path) -> Tuple[Path, List[str]]:
    """Write the hook entries into ``<root>/.claude/settings.json`` (merge-safe).

    Returns ``(settings_path, notes)``; *notes* is empty on a re-run (idempotent).
    The whole document is preserved — only our entries are appended.
    """
    path = settings_path(root)
    data = _load(path)
    notes = merge_hooks(data)
    if notes:
        _io.atomic_write(path, json.dumps(data, indent=2) + "\n")
    return path, notes

"""tide.hooks.install — ``tide install-hooks``: wire the Claude Code hooks.

One-shot human command (build-blueprint resolved-risk #7). It writes two entries
into the **project** ``.claude/settings.json``:

* **SessionStart** → ``tide hook session-start`` (board + role reminder + warnings).
* **PreToolUse** (matcher ``Edit|Write|MultiEdit``) → ``tide hook edit-gate``
  (block project edits until a worker arc is open).

The single load-bearing rule is **MERGE-not-clobber**: a real ``.claude/
settings.json`` already carries the human's own hooks (e.g. rtk), permissions,
env. We parse the whole document, append our two entries to the relevant hook
**event lists** (never replacing them), and re-serialise — so every pre-existing
key and hook survives. Re-running is idempotent: an entry already pointing at our
command is detected and not duplicated.

Pure helpers operate on a settings ``dict``; :func:`install_hooks` does the I/O;
:func:`register` / :func:`register_hook_group` wire the thin CLI handlers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

from .. import io as _io
from ..arc.stream import StreamError
from . import edit_gate, role_gate, session_start

# settings.json location + the exact commands/matcher we install.
CLAUDE_DIRNAME = ".claude"
SETTINGS_FILE = "settings.json"
HOOKS_KEY = "hooks"

SESSION_START_EVENT = "SessionStart"
PRE_TOOL_USE_EVENT = "PreToolUse"

SESSION_START_CMD = "tide hook session-start"
EDIT_GATE_CMD = "tide hook edit-gate"
EDIT_MATCHER = "Edit|Write|MultiEdit"
ROLE_GATE_CMD = "tide hook role-gate"
ROLE_GATE_MATCHER = "Write|Edit|NotebookEdit|Bash"


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
    return notes


# --- I/O -------------------------------------------------------------------

def install_hooks(root: Path) -> Tuple[Path, List[str]]:
    """Write both hook entries into ``<root>/.claude/settings.json`` (merge-safe).

    Returns ``(settings_path, notes)``; *notes* is empty on a re-run (idempotent).
    The whole document is preserved — only our two entries are appended.
    """
    path = settings_path(root)
    data = _load(path)
    notes = merge_hooks(data)
    if notes:
        _io.atomic_write(path, json.dumps(data, indent=2) + "\n")
    return path, notes


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

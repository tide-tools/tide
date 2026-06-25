"""tide.hooks.role_gate — PreToolUse role-capability gate.

Enforces the HEAD/worker role split at the tool level: when TIDE_ROLE=orchestrator
the hook PHYSICALLY FORBIDS the tools that belong to worker-sessions (Write, Edit,
NotebookEdit, mutating Bash). The orchestrator only reads, talks, and runs the
tide CLI; all build-work is dispatched via the Agent tool.

When TIDE_ROLE is anything other than ``orchestrator`` (worker, unset, empty) the
hook is a **pure no-op** — workers keep full Write/Edit/Bash capability.

Protocol (same as ``edit_gate``):

* Reads the Claude Code PreToolUse JSON payload from stdin.
* Exits ``0`` (allow) or ``2`` (block + reason on stderr).
* A garbled / missing payload is treated as "allow" — the gate never wedges a
  session shut on a parse error.

Decision logic lives in :func:`decide` (pure, argparse-free, unit-testable);
:func:`cmd_role_gate` is the thin CLI handler.

Bash allowlist (conservative — unrecognised patterns are DENIED):

* ``tide <anything>`` or bare ``tide`` → ALLOW (orchestration work).
* Read-only git: ``git status``, ``git log``, ``git diff``, ``git show``,
  ``git rev-parse``, ``git branch`` (without ``-D/--delete/-d``),
  ``git worktree list``, ``git remote``, ``git remote -v``.
  NOT: ``git commit``, ``git push``, ``git merge``, ``git branch -D``,
  ``git worktree add/remove``.
* Commands containing ``>`` or ``>>`` (redirects) are DENIED.
* Commands containing ``|`` (pipes) are DENIED (can't reliably prove safe).
* Plain read-only builtins WITHOUT redirects/pipes: ``ls``, ``cat``, ``pwd``,
  ``find``, ``grep``, ``echo``.
* Everything else → DENY.
"""

from __future__ import annotations

import json
import shlex
import sys
from typing import Tuple

# PreToolUse exit-code protocol (Claude Code): 0 = allow, 2 = block + stderr reason.
ALLOW_EXIT = 0
BLOCK_EXIT = 2

DENY_MESSAGE = (
    "tide: you are the HEAD (orchestrator) — this is worker-work. "
    "Dispatch it via the Agent tool; the head only reads, talks, and runs the tide CLI."
)

# Tools unconditionally blocked for the orchestrator role.
_BLOCKED_TOOLS = frozenset({"Write", "Edit", "NotebookEdit"})

# Read-only git subcommands (first non-flag token after ``git``).
_READONLY_GIT_SUBCOMMANDS = frozenset({
    "status", "log", "diff", "show", "rev-parse",
})

# Shell builtins safe without redirects or pipes.
_READONLY_BUILTINS = frozenset({"ls", "cat", "pwd", "find", "grep", "echo"})

# git branch flags that mean destructive deletion.
_GIT_BRANCH_DELETE_FLAGS = frozenset({"-D", "--delete", "-d"})


# --- bash allowlist -----------------------------------------------------------

def _is_git_subcommand_allowed(cmd: str) -> bool:
    """Return True when *cmd* (a ``git …`` string) is a read-only git operation."""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return False

    if len(parts) < 2:
        return True  # bare ``git`` → shows help, read-only

    # Skip global flags (e.g. -C /dir, --git-dir=…) to find the subcommand.
    idx = 1
    while idx < len(parts) and parts[idx].startswith("-"):
        idx += 1

    if idx >= len(parts):
        return True  # only global flags, no subcommand

    subcmd = parts[idx]
    rest = parts[idx + 1:]

    if subcmd in _READONLY_GIT_SUBCOMMANDS:
        return True

    if subcmd == "branch":
        # Allow listing (``git branch``, ``-a``, ``-r``); deny any delete flag.
        return not any(arg in _GIT_BRANCH_DELETE_FLAGS for arg in rest)

    if subcmd == "remote":
        # Allow bare ``git remote`` and ``git remote -v`` only.
        return rest in ([], ["-v"])

    if subcmd == "worktree":
        # Allow only ``git worktree list``.
        return bool(rest) and rest[0] == "list"

    return False


def _is_bash_allowed(command: str) -> bool:
    """Return True when *command* is safe for an orchestrator to run directly.

    Conservative: unknown commands and anything with redirects/pipes (outside of
    tide commands) are denied.
    """
    cmd = command.strip()
    if not cmd:
        return True  # empty → allow; nothing happens

    # Tide commands are always allowed (orchestration surface, not worker-work).
    if cmd == "tide" or cmd.startswith("tide "):
        return True

    # Redirects are always a signal of mutation — deny immediately.
    if ">" in cmd:
        return False

    # Pipes can mask mutations; deny conservatively.
    if "|" in cmd:
        return False

    # Git: apply the read-only subcommand allowlist.
    if cmd == "git" or cmd.startswith("git "):
        return _is_git_subcommand_allowed(cmd)

    # Plain read-only builtins (no redirects/pipes already confirmed above).
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return False

    if parts and parts[0] in _READONLY_BUILTINS:
        return True

    return False


# --- pure decision ------------------------------------------------------------

def decide(tool_name: str, tool_input: dict, role: str) -> Tuple[bool, str]:
    """Decide whether the tool call is permitted for *role*.

    Returns ``(allow, reason)``.  When *allow* is ``True`` *reason* is empty.
    When *allow* is ``False`` *reason* carries the re-teaching denial message.

    Non-orchestrator roles (worker, unset) are always allowed — the gate is a
    pure no-op for them.
    """
    # Workers (and unset / any other role) have full tool capability.
    if role != "orchestrator":
        return True, ""

    # Write / Edit / NotebookEdit are unconditionally worker-work.
    if tool_name in _BLOCKED_TOOLS:
        return False, DENY_MESSAGE

    # Bash: check against the conservative allowlist.
    if tool_name == "Bash":
        command = ""
        if isinstance(tool_input, dict):
            raw = tool_input.get("command", "")
            if isinstance(raw, str):
                command = raw
        if _is_bash_allowed(command):
            return True, ""
        return False, DENY_MESSAGE

    # Read, Grep, Glob, Agent, Task and anything else → always allow.
    return True, ""


# --- payload parsing ----------------------------------------------------------

def _read_payload(stream_in) -> dict:
    """Parse the Claude Code PreToolUse JSON payload from *stream_in* (lenient)."""
    try:
        raw = stream_in.read()
    except (OSError, ValueError):
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# --- CLI handler --------------------------------------------------------------

def cmd_role_gate(args) -> int:
    """``tide hook role-gate`` — the dispatched PreToolUse handler.

    Reads the tool payload from stdin, decides based on TIDE_ROLE, and exits 0
    (allow) or 2 (block, reason on stderr). A missing/garbled payload is treated
    as "allow" so the gate never wedges a session shut on a parse hiccup.
    """
    from ..cli import current_role

    payload = _read_payload(sys.stdin)
    tool_name = payload.get("tool_name", "") if payload else ""
    tool_input = payload.get("tool_input", {}) if payload else {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    if not isinstance(tool_name, str):
        tool_name = ""

    role = current_role()
    allow, reason = decide(tool_name, tool_input, role)

    if not allow:
        print(reason, file=sys.stderr)
        return BLOCK_EXIT
    return ALLOW_EXIT

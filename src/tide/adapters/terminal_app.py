"""tide.adapters.terminal_app — macOS Terminal.app adapter.

Opens a NEW Terminal.app window and runs the launcher's scoped Claude command
inside it. Targets users on a standard macOS install who do NOT have Orca
installed — the fallback before tmux, and the default on Darwin when orca is
absent from PATH.

The implementation drives Terminal.app via ``osascript``, which is present on
every macOS installation (no extra install required). The AppleScript used is the
simplest that works reliably:

    tell application "Terminal" to do script "<shell_cmd>"

The ``do script`` verb opens a **new** Terminal window and executes the shell
command. This is simpler and more reliable than keystroke-based approaches
(which require Accessibility grants and are sensitive to timing).

Escaping — two layers, applied in order
---------------------------------------
Layer 1 — POSIX shell (``shlex``):
    Each token in ``command`` is passed through ``shlex.quote`` (via
    ``shlex.join``); the ``cwd`` is wrapped with ``shlex.quote``. This ensures
    paths with spaces and flags with special chars are treated as single shell
    tokens when the shell evaluates the ``cd … && …`` line.

Layer 2 — AppleScript string literal:
    The ``do script "…"`` argument is an AppleScript double-quoted string.
    Inside it, ``\\`` must be written as ``\\\\`` and ``"`` as ``\\"``.
    We apply this after shlex-quoting, so any double-quotes introduced by
    shlex (e.g. for paths containing single-quotes, which shlex escapes as
    ``'"'"'``) are also covered.

Why ``osascript`` over ``open -a Terminal``?
    ``open -a Terminal`` activates Terminal but doesn't let us pass a command
    to run in the new window without resorting to a shell script file. The
    ``osascript`` ``do script`` verb gives us a direct, reliable one-liner with
    no temp-file indirection and no Accessibility grant.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from typing import List

from .base import SpawnResult, TerminalAdapter, safe_title

# AppleScript one-liner: open a new Terminal.app window and run shell_cmd in it.
# {shell_cmd} is the POSIX-shell command already escaped by _escape_for_applescript.
_APPLESCRIPT_TEMPLATE = 'tell application "Terminal" to do script "{shell_cmd}"'


def _build_shell_command(cwd: str, command: List[str]) -> str:
    """Return ``cd '<cwd>' && <command…>`` with each token POSIX-quoted.

    ``shlex.quote`` wraps tokens in single-quotes, so spaces and most special
    chars survive the shell parse without interpretation. ``shlex.join`` applies
    it to every element of *command*.
    """
    return "cd {cwd} && {cmd}".format(
        cwd=shlex.quote(cwd),
        cmd=shlex.join(command),
    )


def _escape_for_applescript(s: str) -> str:
    """Escape *s* for safe embedding inside an AppleScript double-quoted string.

    Apply in this order to avoid double-escaping:
    1. ``\\`` → ``\\\\``  (backslash must be doubled)
    2. ``"``  → ``\\"``   (double-quote must be escaped)
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


class TerminalAppAdapter(TerminalAdapter):
    """macOS Terminal.app adapter — ``osascript do script`` in a new window.

    Registry key: ``macos``. Auto-selected on Darwin when the ``orca`` binary
    is absent from PATH. No Accessibility grant required (unlike keystroke-based
    Orca control); needs only ``osascript``, which ships with every macOS.
    """

    name = "macos"

    def build_commands(self, *, command: List[str], cwd: str, title: str) -> List[List[str]]:
        """Build the single ``osascript -e <script>`` command — pure, no side effects.

        The returned list always has exactly one element: the osascript invocation.
        *command* is carried verbatim (shlex-quoted) inside the AppleScript string.
        """
        shell_cmd = _build_shell_command(cwd=cwd, command=command)
        escaped = _escape_for_applescript(shell_cmd)
        script = _APPLESCRIPT_TEMPLATE.format(shell_cmd=escaped)
        return [["osascript", "-e", script]]

    def spawn(
        self,
        *,
        command: List[str],
        cwd: str,
        title: str = "tide",
        dry_run: bool = False,
    ) -> SpawnResult:
        """Open a new Terminal.app window at *cwd* running *command*.

        With ``dry_run=True``: build but do NOT execute; return commands for
        inspection. On a real spawn: run ``osascript``; degrade gracefully on
        failure (return ``ok=False`` with human-readable instructions, never raise).
        """
        commands = self.build_commands(command=command, cwd=cwd, title=title)

        if dry_run:
            return SpawnResult(
                ok=True,
                ref=safe_title(title),
                detail="dry-run (osascript not executed)",
                commands=commands,
            )

        if shutil.which("osascript") is None:
            return SpawnResult(
                ok=False,
                detail=(
                    "osascript not found — Terminal.app adapter requires macOS; "
                    "try '--adapter tmux'"
                ),
                commands=commands,
            )

        try:
            subprocess.run(commands[0], check=True)
        except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover
            return SpawnResult(
                ok=False,
                detail=(
                    "Terminal.app spawn failed ({exc}); "
                    "try '--adapter tmux'".format(exc=exc)
                ),
                commands=commands,
            )

        return SpawnResult(
            ok=True,
            ref=safe_title(title),
            detail="opened a new Terminal.app window",
            commands=commands,
        )

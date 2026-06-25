"""tide.adapters.tmux — the swappable fallback adapter (proves pluggability).

tmux is the headless-friendly alternative to the default Orca adapter: it needs
no Accessibility grant and no GUI, so it doubles as the "interface is genuinely
pluggable" proof and as a usable fallback on a server. It opens a new window in
the running tmux server, ``cd``s into the project, and runs the launcher's scoped
Claude command directly as the window's program.

A single command is built (and, on a real spawn, executed):

    ``tmux new-window -c <cwd> -n <title> <command…>``

— where ``<command…>`` is the launcher's scoped ``claude …`` argv (its scoping
flags + a ``--append-system-prompt @<seed_file>`` reference to the persisted
seed). No ``send-keys`` is needed: the seed rides in the command itself, so a
multi-KB payload is never keystroked.

The dry-run path returns the command on :attr:`SpawnResult.commands` WITHOUT
executing — that is the unit test the build-blueprint asks for.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import List

from .base import SpawnResult, TerminalAdapter, safe_title


class TmuxAdapter(TerminalAdapter):
    """Fallback adapter: ``tmux new-window`` running the launcher's scoped command."""

    name = "tmux"

    def build_commands(self, *, command: List[str], cwd: str, title: str) -> List[List[str]]:
        """Build the single new-window command (carries *command* verbatim) — pure."""
        window = safe_title(title)
        new_window = [
            "tmux", "new-window",
            "-c", cwd,
            "-n", window,
            *command,
        ]
        return [new_window]

    def spawn(
        self,
        *,
        command: List[str],
        cwd: str,
        title: str = "tide",
        dry_run: bool = False,
    ) -> SpawnResult:
        commands = self.build_commands(command=command, cwd=cwd, title=title)
        if dry_run:
            return SpawnResult(
                ok=True,
                ref=safe_title(title),
                detail="dry-run (tmux not executed)",
                commands=commands,
            )

        if shutil.which("tmux") is None:
            return SpawnResult(
                ok=False,
                detail="tmux not found on PATH — install tmux or pick another adapter",
                commands=commands,
            )

        try:
            for cmd in commands:
                subprocess.run(cmd, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover
            return SpawnResult(
                ok=False,
                detail="tmux spawn failed: {0}".format(exc),
                commands=commands,
            )
        return SpawnResult(
            ok=True,
            ref=safe_title(title),
            detail="opened tmux window {0!r}".format(safe_title(title)),
            commands=commands,
        )

"""tide.adapters.orca — the DEFAULT adapter, opens a tab via the ``orca`` CLI.

Orca is the terminal the orchestrator already lives in on this machine. This
adapter opens a NEW Orca terminal tab in the project directory and runs the
launcher's scoped Claude command — using ``orca terminal create`` (the native CLI
the handoff flow uses), NOT AppleScript ``keystroke``.

Why not osascript: ``keystroke "<cmd>"`` types the command through the active
keyboard layout, so a non-Latin input source (Greek/Russian/…) mangles
``claude … /Users/…`` into garbage, and the keystrokes can land in the wrong
window entirely. ``orca terminal create`` runs the command directly — no typing,
layout-independent, targets the right tab.

The launch *command* is built upstream (:mod:`tide.launcher.context`) and already
points the fresh session at the persisted seed file via ``--append-system-prompt
@<seed_file>``. The adapter degrades gracefully: when the ``orca`` CLI is missing
or errors it returns ``ok=False`` with instructions instead of raising — the
caller can then suggest ``--adapter tmux``.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from typing import List

from .base import SpawnResult, TerminalAdapter, safe_title


class OrcaAdapter(TerminalAdapter):
    """Default adapter: ``orca terminal create`` opens a tab running the scoped command."""

    name = "orca"

    def build_command(self, *, cwd: str, command: List[str], title: str = "tide") -> List[str]:
        """The ``orca terminal create`` argv that opens *cwd* and runs *command*."""
        return [
            "orca",
            "terminal",
            "create",
            "--worktree",
            "path:{0}".format(cwd),
            "--command",
            shlex.join(command),
            "--title",
            safe_title(title),
            "--focus",
        ]

    def spawn(
        self,
        *,
        command: List[str],
        cwd: str,
        title: str = "tide",
        dry_run: bool = False,
    ) -> SpawnResult:
        argv = self.build_command(cwd=cwd, command=command, title=title)
        if dry_run:
            return SpawnResult(
                ok=True,
                ref=safe_title(title),
                detail="dry-run (orca not executed)",
                commands=[argv],
            )

        if shutil.which("orca") is None:
            return SpawnResult(
                ok=False,
                detail="orca CLI not found — install Orca or use '--adapter tmux'",
            )

        try:
            subprocess.run(argv, check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover
            hint = ""
            stderr = getattr(exc, "stderr", "") or ""
            if "worktree" in stderr.lower() or "path" in stderr.lower():
                hint = " (is this project registered with Orca? `orca repo add <path>`)"
            return SpawnResult(
                ok=False,
                detail="Orca spawn failed ({0}){1}; or use '--adapter tmux'".format(exc, hint),
                commands=[argv],
            )
        return SpawnResult(
            ok=True,
            ref=safe_title(title),
            detail="opened a new Orca tab",
            commands=[argv],
        )

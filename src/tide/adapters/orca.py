"""tide.adapters.orca — the DEFAULT adapter, drives Orca via ``osascript``.

Orca Helper.app is the terminal the orchestrator already lives in on this
machine; this adapter (ported from the focus handoff skill's Orca control) opens
a NEW Orca terminal tab, ``cd``s into the project, and runs the launcher's scoped
Claude command. It needs an Accessibility grant + Orca installed, so it **degrades
gracefully**: when ``osascript`` is missing or errors it returns ``ok=False`` with
instructions instead of raising — the caller can then suggest ``--adapter tmux``.

The launch *command* is built upstream (:mod:`tide.launcher.context`) and already
points the fresh session at the persisted seed file via ``--append-system-prompt
@<seed_file>`` — so the adapter only keystrokes ``cd <cwd> && <command>`` (a short,
flag-only line; the multi-KB seed lives in the referenced file, never typed). The
dry-run path builds the ``osascript`` command WITHOUT executing anything.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from typing import List

from .base import SpawnResult, TerminalAdapter, safe_title

# AppleScript: activate Orca, open a new tab (Cmd-T), then type the launch line.
# Kept as a template so the dry-run can show the exact script that would run.
_OSASCRIPT_TEMPLATE = """tell application "Orca" to activate
tell application "System Events"
    keystroke "t" using command down
    delay 0.4
    keystroke "cd {cwd} && {launch_line}"
    key code 36
end tell"""


class OrcaAdapter(TerminalAdapter):
    """Default adapter: ``osascript`` opens a new Orca tab and runs the scoped command."""

    name = "orca"

    def build_script(self, *, cwd: str, command: List[str]) -> str:
        """Render the AppleScript that opens the tab and types ``cd <cwd> && <command>``."""
        return _OSASCRIPT_TEMPLATE.format(
            cwd=shlex.quote(cwd), launch_line=shlex.join(command)
        )

    def spawn(
        self,
        *,
        command: List[str],
        cwd: str,
        title: str = "tide",
        dry_run: bool = False,
    ) -> SpawnResult:
        script = self.build_script(cwd=cwd, command=command)
        if dry_run:
            return SpawnResult(
                ok=True,
                ref=safe_title(title),
                detail="dry-run (osascript not executed)",
                commands=[["osascript", "-e", script]],
            )

        if shutil.which("osascript") is None:
            return SpawnResult(
                ok=False,
                detail=(
                    "osascript not found — Orca control needs macOS + an "
                    "Accessibility grant; try '--adapter tmux'"
                ),
            )

        try:
            subprocess.run(["osascript", "-e", script], check=True)
        except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover
            return SpawnResult(
                ok=False,
                detail=(
                    "Orca spawn failed ({0}); grant Accessibility to Orca or "
                    "use '--adapter tmux'".format(exc)
                ),
                commands=[["osascript", "-e", script]],
            )
        return SpawnResult(
            ok=True,
            ref=safe_title(title),
            detail="opened a new Orca tab",
            commands=[["osascript", "-e", script]],
        )

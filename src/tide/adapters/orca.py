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

# A spawn against a path Orca does not know about fails nonzero with this code in
# its JSON error payload (carried on stdout/stderr). It is the ONE failure we
# can self-heal — register the repo, then retry the create.
_SELECTOR_NOT_FOUND = "selector_not_found"


def _worktree_blocker(cwd: str) -> str:
    """'' when *cwd* is a git repo with a commit; else the one-line human diagnosis.

    ``orca terminal create --worktree`` runs ``git worktree add``, which needs a
    repo with HEAD — a bare or freshly-init'ed project dir dies with a raw orca
    trace otherwise (cand 32). Preflight it and say the CAUSE + the fix, not the
    failed command line. A missing git binary returns '' — let orca speak then.
    """
    try:
        probe = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--verify", "--quiet", "HEAD"],
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    if probe.returncode == 0:
        return ""
    return (
        "{0} is not a git repo with a commit — Orca worktrees need one. "
        "Fix: `tide adopt {0}` (git init + first commit + scaffold), then retry; "
        "or use '--adapter tmux'".format(cwd)
    )


def _is_unregistered_repo(exc: BaseException) -> bool:
    """True when *exc* is an Orca failure caused by an unregistered repo path.

    Inspects the failed process's stdout+stderr for the ``selector_not_found``
    error code Orca emits when ``--worktree path:<cwd>`` points at a path it has
    never seen. Any other failure (bad command, app down, …) returns False so we
    never blindly register + retry on an unrelated error.
    """
    blob = "{0}{1}".format(
        getattr(exc, "stdout", "") or "", getattr(exc, "stderr", "") or ""
    )
    return _SELECTOR_NOT_FOUND in blob.lower()


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

        blocker = _worktree_blocker(cwd)
        if blocker:
            return SpawnResult(ok=False, detail=blocker, commands=[argv])

        try:
            subprocess.run(argv, check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            # Self-heal: the ONLY failure we recover from is an unregistered repo
            # path. Register it once, then retry the create exactly once. Any
            # other failure (or a still-failing retry) degrades gracefully below.
            if _is_unregistered_repo(exc) and self._register_repo(cwd):
                try:
                    subprocess.run(argv, check=True, capture_output=True, text=True)
                except (OSError, subprocess.CalledProcessError) as retry_exc:
                    return self._spawn_failure(retry_exc, argv)
                return SpawnResult(
                    ok=True,
                    ref=safe_title(title),
                    detail="opened a new Orca tab (after registering repo with Orca)",
                    commands=[argv],
                )
            return self._spawn_failure(exc, argv)
        return SpawnResult(
            ok=True,
            ref=safe_title(title),
            detail="opened a new Orca tab",
            commands=[argv],
        )

    def _register_repo(self, cwd: str) -> bool:
        """Register *cwd* with Orca via ``orca repo add --path <cwd>``; True on attempt.

        Best-effort + idempotent-ish: ``orca repo add`` tolerates an already-known
        path, so a nonzero exit is not treated as fatal here — we still retry the
        create. Returns False only when the ``orca`` binary itself cannot be run
        (so the caller skips the pointless retry).
        """
        try:
            subprocess.run(
                ["orca", "repo", "add", "--path", cwd],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return False
        return True

    def _spawn_failure(self, exc: BaseException, argv: List[str]) -> SpawnResult:
        """Build the graceful ``ok=False`` result for a failed ``orca terminal create``."""
        hint = ""
        stderr = getattr(exc, "stderr", "") or ""
        if "worktree" in stderr.lower() or "path" in stderr.lower():
            hint = " (is this project registered with Orca? `orca repo add <path>`)"
        return SpawnResult(
            ok=False,
            detail="Orca spawn failed ({0}){1}; or use '--adapter tmux'".format(exc, hint),
            commands=[argv],
        )

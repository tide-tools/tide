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

import json
import shlex
import shutil
import subprocess
from typing import List, Optional

from .base import SpawnResult, TerminalAdapter, safe_title


def _parse_handle(stdout: str) -> Optional[str]:
    """The created terminal's handle from ``orca terminal create --json`` output, or None.

    Shape: ``{"result": {"terminal": {"handle": "term_…"}}}``. The handle is what the
    sid-keyed launch registry (:mod:`tide.registry`, cand 94) records so ▶ can later
    focus THIS exact terminal — any parse failure just yields None (registry write skipped).
    """
    try:
        term = (json.loads(stdout or "{}").get("result", {}) or {}).get("terminal", {}) or {}
        handle = (term.get("handle") or "").strip()
        return handle or None
    except (ValueError, AttributeError):
        return None

# A spawn against a path Orca does not know about fails nonzero with this code in
# its JSON error payload (carried on stdout/stderr). It is the ONE failure we
# can self-heal — register the repo, then retry the create.
_SELECTOR_NOT_FOUND = "selector_not_found"


def screen_locked() -> bool:
    """True when the mac's screen is locked (lock screen, or the display asleep).

    A locked mac brings NO app to the front, so a return that "succeeded" shows the
    human nothing: proven 29.07 — the switch answered ``ok:true`` while
    ``lsappinfo front`` sat on the login window for a solid twenty minutes of
    head-scratching. Callers use this to say so out loud instead of promising a window
    that cannot appear (``screen_locked`` in the ``tide return --json`` contract).

    Read from ``ioreg``: no Apple Events, no TCC prompt, so it answers the same inside
    the board's launchd service. Any failure reads as "not locked" — a warning we are
    not sure about is worse than no warning.
    """
    try:
        out = subprocess.run(
            ["/usr/sbin/ioreg", "-n", "Root", "-d1"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return 'CGSSessionScreenIsLocked"=Yes' in out.replace(" ", "")


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
        """The ``orca terminal create`` argv that opens *cwd* and runs *command*.

        ``--json`` so the created terminal's handle comes back on stdout — the launcher
        records it in the sid-keyed registry (cand 94) to make ▶ resolve this exact tab.
        """
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
            "--json",
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
            proc = subprocess.run(argv, check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            # Self-heal: the ONLY failure we recover from is an unregistered repo
            # path. Register it once, then retry the create exactly once. Any
            # other failure (or a still-failing retry) degrades gracefully below.
            if _is_unregistered_repo(exc) and self._register_repo(cwd):
                try:
                    proc = subprocess.run(argv, check=True, capture_output=True, text=True)
                except (OSError, subprocess.CalledProcessError) as retry_exc:
                    return self._spawn_failure(retry_exc, argv)
                self._raise_app()
                return SpawnResult(
                    ok=True,
                    ref=_parse_handle(proc.stdout) or safe_title(title),
                    detail="opened a new Orca tab (after registering repo with Orca)",
                    commands=[argv],
                )
            return self._spawn_failure(exc, argv)
        # A fresh tab is no more visible than a switched one: `create --focus` also
        # leaves the app where it was in the z-order (see _raise_app).
        self._raise_app()
        # ref is the orca terminal handle when --json parses (registry key), else the
        # title as a legible fallback — a failed parse must never fail the spawn.
        return SpawnResult(
            ok=True,
            ref=_parse_handle(proc.stdout) or safe_title(title),
            detail="opened a new Orca tab",
            commands=[argv],
        )

    def focus(self, handle: str) -> bool:
        """``orca terminal switch --terminal <handle> --json`` → ok:true means alive.

        This IS the liveness probe (cand 101) — never gate it on ``terminal list``,
        which hides background-adopted terminals that focus perfectly well.

        ``switch`` is the verb Orca's CLI documents (``orca terminal --help``); the
        ``focus`` spelling we used to send is only an undocumented alias for it — same
        request, one rename away from breaking silently.

        A successful switch is only HALF a return: it changes the active tab INSIDE the
        app and leaves the app itself wherever it was in the z-order, so it is followed
        by :meth:`_raise_app`.
        """
        h = (handle or "").strip()
        if not h or shutil.which("orca") is None:
            return False
        try:
            proc = subprocess.run(
                ["orca", "terminal", "switch", "--terminal", h, "--json"],
                capture_output=True, text=True, timeout=10,
            )
            if json.loads(proc.stdout or "{}").get("ok") is not True:
                return False
        except Exception:  # noqa: BLE001 — a failed probe just means "not focusable"
            return False
        self._raise_app()
        return True

    def say(self, handle: str, text: str) -> bool:
        """``orca terminal send --terminal <handle> --text … --enter`` → the session
        gets a real turn.

        Orca's own CLI is the channel: the text lands in that terminal's stdin and the
        ``--enter`` submits it, exactly as if the human had typed it there. No Apple
        Events, no keystroke through the active keyboard layout (the reason
        :meth:`spawn` avoids osascript) — so a Russian input source cannot mangle it,
        and it works from the board's launchd service.

        ONE LINE only, and the caller owes that: a newline inside *text* would submit
        early and leave the tail typed into the next prompt.

        False when there is no orca, no handle, no text, or the send did not answer
        ``ok`` — the caller must then tell the human the words did NOT arrive rather
        than promise a turn that never happened.
        """
        h = (handle or "").strip()
        t = (text or "").strip()
        if not h or not t or shutil.which("orca") is None:
            return False
        try:
            proc = subprocess.run(
                ["orca", "terminal", "send", "--terminal", h,
                 "--text", t, "--enter", "--json"],
                capture_output=True, text=True, timeout=20,
            )
            return json.loads(proc.stdout or "{}").get("ok") is True
        except Exception:  # noqa: BLE001 — a failed send is "not delivered", not a crash
            return False

    def _raise_app(self) -> None:
        """Bring the Orca window to the FRONT — best-effort, never fatal.

        ``orca terminal switch`` (and ``create --focus``) picks the tab inside the app
        but does not activate the app itself. Proven 29.07: with Board.app frontmost,
        the switch answered ``ok:true`` while ``lsappinfo front`` stayed on
        the board app's own bundle id. From a terminal you cannot see the gap — Orca is
        already in front — but from the board it read as "the button does nothing": the
        verdict said "switched", no window ever came up. So the return verb owes the
        human the window, not just the tab.

        ``orca open`` is the CLI's own door (launch + wait for the runtime) and it
        activates the app in ~0.1s. It goes through LaunchServices, not Apple Events,
        so it also works from the board's launchd service, which holds no Automation
        right and cannot be granted one non-interactively.

        A locked screen is the one case we skip: nothing can come forward there, so the
        call would only burn 0.1s to change nothing — :func:`screen_locked` is what the
        surfaces read to TELL the human that instead.
        """
        if screen_locked():
            return
        try:
            subprocess.run(["orca", "open"], check=False,
                           capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass  # no window came up, but the tab did switch — the verdict stays honest

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

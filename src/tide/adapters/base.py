"""tide.adapters.base — the terminal-adapter contract.

An adapter's one job: open a NEW terminal window/tab, ``cd`` into a project, and
run a FRESH Claude session with the exact launch command the launcher built. That
command (a scoped ``claude …`` argv) is assembled adapter-agnostically by
:mod:`tide.launcher.context` from the project's context profile and the persisted
seed file; the adapter only *carries it verbatim*. That split keeps adapters thin
and genuinely swappable (Orca default, tmux fallback, more later) behind one ABC —
and it means WHAT context loads (strict MCP scoping, allow-lists) is decided once,
centrally, not re-derived per adapter.

:class:`TerminalAdapter` is the contract; :class:`SpawnResult` is the uniform
return so the caller (the menu, later the handoff skill) can report success or a
graceful failure with instructions. Every adapter supports ``dry_run=True``:
build the exact command(s) WITHOUT executing — used by the tests and by a
"show me what you'd run" mode.
"""

from __future__ import annotations

import re
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# The base CLI that starts a fresh Claude session inside the new terminal. The
# launcher (tide.launcher.context) wraps this in scoping flags to build the full
# launch argv; adapters carry that argv verbatim.
SESSION_PROGRAM = "claude"


@dataclass
class SpawnResult:
    """Outcome of an adapter ``spawn`` (success, a failure, or a dry-run preview).

    * ``ok`` — True on a real spawn (or a dry-run, which always "succeeds" at the
      build step); False when the underlying tool was missing or errored.
    * ``ref`` — an opaque handle (pid / window-id / tab title) when known.
    * ``detail`` — a human-readable note (e.g. degraded-mode instructions).
    * ``commands`` — the exact command list(s) built; populated on dry-run so a
      test (or the human) can inspect what would run without executing it.
    """

    ok: bool
    ref: Optional[str] = None
    detail: str = ""
    commands: List[List[str]] = field(default_factory=list)


def safe_title(title: str) -> str:
    """A filesystem-/window-safe slug of *title* (alnum + dash), never empty."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (title or "").strip()).strip("-")
    return cleaned or "tide"


def persist_seed(seed: str, title: str) -> Path:
    """Write *seed* to a temp file and return its path (for adapters that pass a file).

    A new terminal can be slow to accept a multi-KB pasted prompt; writing the seed
    to a stable file lets an adapter hand the session a path to read instead. Lives
    under the OS temp dir so it never pollutes a project.
    """
    path = Path(tempfile.gettempdir()) / "tide-seed-{0}.md".format(safe_title(title))
    path.write_text(seed, encoding="utf-8")
    return path


class TerminalAdapter(ABC):
    """Open a new terminal and run a fresh, scoped Claude session.

    Subclasses set :attr:`name` (the registry key) and implement :meth:`spawn`.
    """

    name: str = "base"

    @abstractmethod
    def spawn(
        self,
        *,
        command: List[str],
        cwd: str,
        title: str = "tide",
        dry_run: bool = False,
    ) -> SpawnResult:
        """Open a new terminal at *cwd* and run *command* (the launcher's scoped argv).

        *command* is the full ``claude …`` invocation (scoping flags + a reference
        to the persisted seed file) built by :mod:`tide.launcher.context`; the
        adapter carries it verbatim and never re-derives what context loads.

        With ``dry_run=True`` the adapter MUST build but NOT execute the command(s),
        returning them on :attr:`SpawnResult.commands`.
        """
        raise NotImplementedError  # pragma: no cover

"""tide.launcher.menu — the ``tide menu`` launcher: pick N projects, spawn sessions.

The human entry point into a work session: from a control-home, list the roster,
let the human pick one or more projects, then for EACH picked project build a seed
(:mod:`tide.launcher.seed`) and open a fresh **orchestrator** session in a new
terminal via the configured adapter (:mod:`tide.adapters`).

(The bare ``tide`` command keeps printing help — see ``cli.py``; the menu lives
under the explicit ``tide menu`` subcommand so it never blocks an empty
invocation on stdin.)

The pure pieces — :func:`render_menu`, :func:`parse_selection`,
:func:`select_entries`, :func:`launch_entries` — are argparse-free and unit-
testable (a dry-run adapter proves the wiring without opening a terminal);
:func:`cmd_menu` is the thin interactive handler.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from .. import paths, roster
from ..adapters import SETTINGS_KEY, SpawnResult, get_adapter, resolve_from_settings
from ..adapters.base import persist_seed
from ..arc.stream import StreamError
from . import context, seed

# Placeholder seed-file token used in dry-run (nothing is persisted to disk then),
# so the printed command shows the @<seed-file> shape without a real temp path.
DRY_RUN_SEED_FILE = "<seed-file>"

DEFAULT_ROLE = seed.ROLE_ORCHESTRATOR


class MenuError(StreamError):
    """A menu/selection error (empty roster, out-of-range or unparsable pick).

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on
    the same ``except`` arm (prints ``tide: …``, exits nonzero).
    """


# --- listing + selection (pure) --------------------------------------------

def list_entries(root: Path) -> List[Dict[str, str]]:
    """The control-home roster as ``[{'name','path'}, …]`` (file order)."""
    return roster.read_roster(root)


def render_menu(entries: List[Dict[str, str]]) -> str:
    """Render the numbered pick-list (``1) name → path``) or an empty-roster note."""
    if not entries:
        return "(roster is empty — add a project: tide roster add <name> <path>)"
    lines = ["Pick project(s) to lead this session:"]
    for i, e in enumerate(entries, start=1):
        lines.append("  {0}) {1} → {2}".format(i, e["name"], e["path"]))
    return "\n".join(lines)


def parse_selection(raw: str, count: int) -> List[int]:
    """Parse a pick string into sorted unique 1-based indices, validated to *count*.

    Accepts comma- and/or space-separated numbers (``"1,3"`` / ``"1 3"``) and the
    keyword ``all``. Raises :class:`MenuError` on an empty, non-numeric, or
    out-of-range pick.
    """
    s = (raw or "").strip().lower()
    if not s:
        raise MenuError("menu: empty selection (pick e.g. '1' or '1,3' or 'all')")
    if s == "all":
        return list(range(1, count + 1))

    picks: List[int] = []
    for tok in re.split(r"[,\s]+", s):
        if not tok:
            continue
        if not tok.isdigit():
            raise MenuError("menu: invalid pick {0!r} (want numbers or 'all')".format(tok))
        n = int(tok)
        if not (1 <= n <= count):
            raise MenuError(
                "menu: pick {0} out of range (1..{1})".format(n, count)
            )
        if n not in picks:
            picks.append(n)
    if not picks:
        raise MenuError("menu: no projects selected")
    return sorted(picks)


def select_entries(entries: List[Dict[str, str]], raw: str) -> List[Dict[str, str]]:
    """Resolve a pick string against *entries* into the chosen entry dicts."""
    if not entries:
        raise MenuError("menu: roster is empty — nothing to pick")
    picks = parse_selection(raw, len(entries))
    return [entries[i - 1] for i in picks]


# --- launch ----------------------------------------------------------------

def build_launch(
    project: Path,
    *,
    control_home: Path,
    role: str = DEFAULT_ROLE,
    dry_run: bool = False,
) -> List[str]:
    """Resolve seed + context profile into the scoped ``claude …`` argv for *project*.

    On a real launch the seed is persisted (so the new session can read it by path);
    on a dry-run nothing is written and a placeholder seed-file token is used, so the
    printed command still shows the exact scoped shape.
    """
    s = seed.seed_for_project(project, role=role, control_home=control_home)
    title = "tide-{0}".format(project.name)
    seed_file = DRY_RUN_SEED_FILE if dry_run else str(persist_seed(s, title))
    profile = context.load_profile(project)
    return context.build_launch_command(seed_file, profile)


def launch_entry(
    entry: Dict[str, str],
    *,
    adapter,
    control_home: Path,
    role: str = DEFAULT_ROLE,
    dry_run: bool = False,
) -> SpawnResult:
    """Build the scoped launch command for one rostered project and spawn it."""
    project = Path(entry["path"]).expanduser()
    command = build_launch(
        project, control_home=control_home, role=role, dry_run=dry_run
    )
    title = "tide-{0}".format(entry["name"])
    return adapter.spawn(
        command=command, cwd=str(project), title=title, dry_run=dry_run
    )


def launch_entries(
    entries: List[Dict[str, str]],
    *,
    control_home: Path,
    adapter_name: Optional[str] = None,
    role: str = DEFAULT_ROLE,
    dry_run: bool = False,
) -> List[SpawnResult]:
    """Spawn a seeded session for each chosen project; return one result per project."""
    adapter = get_adapter(adapter_name)
    return [
        launch_entry(
            e,
            adapter=adapter,
            control_home=control_home,
            role=role,
            dry_run=dry_run,
        )
        for e in entries
    ]


# --- settings (adapter pin) ------------------------------------------------

def _read_settings(root: Path) -> Optional[dict]:
    """Best-effort read of the project ``.claude/settings.json`` (None on any issue)."""
    path = Path(root) / ".claude" / "settings.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (json.JSONDecodeError, ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def resolve_adapter_name(root: Path, override: Optional[str]) -> Optional[str]:
    """Pick the adapter name: explicit *override* wins, else the settings pin, else None."""
    if override:
        return override
    settings = _read_settings(root)
    if isinstance(settings, dict):
        value = settings.get(SETTINGS_KEY)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# --- CLI handler -----------------------------------------------------------

def cmd_menu(args) -> int:
    """``tide menu`` — list the roster, pick N projects, launch seeded sessions."""
    root = paths.require_tide_root()
    entries = list_entries(root)
    if not entries:
        print(render_menu(entries))
        return 0

    raw = getattr(args, "pick", None)
    if not raw:
        print(render_menu(entries))
        try:
            raw = input("pick> ")
        except EOFError:
            raw = ""

    chosen = select_entries(entries, raw)
    adapter_name = resolve_adapter_name(root, getattr(args, "adapter", None))
    dry_run = bool(getattr(args, "dry_run", False))
    role = getattr(args, "role", None) or DEFAULT_ROLE

    results = launch_entries(
        chosen,
        control_home=root,
        adapter_name=adapter_name,
        role=role,
        dry_run=dry_run,
    )
    for entry, res in zip(chosen, results):
        flag = "ok" if res.ok else "FAILED"
        print("tide: {0} [{1}] {2}".format(entry["name"], flag, res.detail))
        if dry_run:
            # Show the human EXACTLY what scoped session would launch — the resolved
            # claude argv (strict MCP scoping visible, no global MCP loaded).
            project = Path(entry["path"]).expanduser()
            command = build_launch(
                project, control_home=root, role=role, dry_run=True
            )
            print("  scoped command: {0}".format(" ".join(command)))
    return 0 if all(r.ok for r in results) else 1


def register(subparsers) -> None:
    """Add the top-level ``menu`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "menu", help="pick N projects from the roster and launch seeded sessions"
    )
    p.add_argument("--pick", help="non-interactive selection (e.g. '1,3' or 'all')")
    p.add_argument("--adapter", help="terminal adapter (orca|tmux; default from settings)")
    p.add_argument("--role", help="session role (default: orchestrator)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="build seeds + adapter commands without opening a terminal",
    )
    p.set_defaults(func=cmd_menu, _cmd="menu")

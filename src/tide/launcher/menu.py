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

from .. import fields, paths, roster, slug
from ..adapters import SETTINGS_KEY, SpawnResult, get_adapter, resolve_from_settings
from ..adapters.base import persist_seed
from ..arc import stream
from ..arc.stream import StreamError
from . import context, seed

# Placeholder seed-file token used in dry-run (nothing is persisted to disk then),
# so the printed command shows the @<seed-file> shape without a real temp path.
DRY_RUN_SEED_FILE = "<seed-file>"

DEFAULT_ROLE = seed.ROLE_ORCHESTRATOR

# Picker sessions are head/orchestrator sessions (like `tide terminal`), so they
# default to skipping the permission prompts. Spliced right after the program
# name; toggled off with `tide menu --no-skip-permissions`.
SKIP_PERMISSIONS = "--dangerously-skip-permissions"


class MenuError(StreamError):
    """A menu/selection error (empty roster, out-of-range or unparsable pick).

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on
    the same ``except`` arm (prints ``tide: …``, exits nonzero).
    """


# --- listing + selection (pure) --------------------------------------------

def list_entries(root: Path) -> List[Dict[str, str]]:
    """The control-home roster as ``[{'name','path'}, …]`` (file order)."""
    return roster.read_roster(root)


def is_active(entry: Dict[str, str]) -> bool:
    """True unless the entry carries ``status=archived`` (default is active)."""
    return entry.get("status", roster.STATUS_ACTIVE) != roster.STATUS_ARCHIVED


def active_entries(entries: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """The subset of *entries* that are active (archived projects filtered out)."""
    return [e for e in entries if is_active(e)]


def render_menu(entries: List[Dict[str, str]]) -> str:
    """Render the numbered pick-list (``1) name → path``) or an empty-roster note.

    Archived entries (only ever shown via ``--all``) are tagged ``[archived]`` so
    the human sees why a normally-hidden project is in the list.
    """
    if not entries:
        return "(roster is empty — add a project: tide roster add <name> <path>)"
    lines = ["Pick project(s) to lead this session:"]
    for i, e in enumerate(entries, start=1):
        tag = "" if is_active(e) else "  [archived]"
        lines.append("  {0}) {1} → {2}{3}".format(i, e["name"], e["path"], tag))
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


# --- thread (нить) selection -----------------------------------------------
# After a project is picked, the human binds the session to a thread: continue an
# open one or start new. A thread is a kind: thread arc (one session's memory);
# the bound slug becomes the seed's arc_ref. See tide.arc.stream.

THREAD_NEW = "new"  # sentinel pick for the "+ new thread" row


def list_threads(project: Path) -> List[Dict[str, str]]:
    """A project's open threads for the picker: ``[{slug, goal, path}, …]`` in order."""
    out = []
    for entry in stream.thread_entries(project):
        goal = (fields.read_field(stream.passport_path(entry), "goal") or "").strip()
        out.append(
            {"slug": slug.entry_slug(entry.name), "goal": goal, "path": str(entry)}
        )
    return out


def render_thread_menu(project_name: str, threads: List[Dict[str, str]]) -> str:
    """Numbered thread pick-list for *project_name*, ending with a '+ new thread' row."""
    lines = ["Thread (нить) for {0} — continue one or start new:".format(project_name)]
    for i, t in enumerate(threads, start=1):
        goal = t.get("goal") or ""
        suffix = " — {0}".format(goal) if goal and not goal.startswith("<") else ""
        lines.append("  {0}) {1}{2}".format(i, t["slug"], suffix))
    lines.append("  {0}) + new thread".format(len(threads) + 1))
    return "\n".join(lines)


def parse_thread_choice(raw: str, count: int):
    """Map a thread pick to a 1-based index (``1..count``) or :data:`THREAD_NEW`.

    Empty / ``n`` / ``new`` / the ``count+1`` row → new; a number in ``1..count``
    → that thread. Raises :class:`MenuError` on a garbage or out-of-range pick.
    """
    s = (raw or "").strip().lower()
    if s in ("", "n", "new", "+"):
        return THREAD_NEW
    if not s.isdigit():
        raise MenuError("thread: invalid pick {0!r} (a number or 'new')".format(raw))
    n = int(s)
    if n == count + 1:
        return THREAD_NEW
    if 1 <= n <= count:
        return n
    raise MenuError("thread: pick {0} out of range (1..{1})".format(n, count + 1))


def create_thread(project: Path, name: str) -> Optional[str]:
    """Create a new thread in *project*; return its bound arc_ref (slug), or None
    when *name* is blank (the human skipped)."""
    name = (name or "").strip()
    if not name:
        return None
    entry = stream.new_thread(project, name)
    return slug.entry_slug(entry.name)


def touch_thread(project: Path, thread_path: str) -> None:
    """Re-stamp a continued thread's canon-rev — links the re-entry, no work-gate."""
    stream.stamp_rev(Path(thread_path), project)


def prompt_thread(project: Path, project_name: str) -> Optional[str]:
    """Interactively bind a thread for *project*: continue one or start new.

    Returns the bound arc_ref (thread slug), or None when the human skips (an
    empty new-thread name).
    """
    threads = list_threads(project)
    print(render_thread_menu(project_name, threads))
    try:
        raw = input("thread> ")
    except EOFError:
        raw = ""
    choice = parse_thread_choice(raw, len(threads))
    if choice == THREAD_NEW:
        try:
            name = input("new thread name> ")
        except EOFError:
            name = ""
        return create_thread(project, name)
    chosen = threads[choice - 1]
    touch_thread(project, chosen["path"])
    return chosen["slug"]


def resolve_thread(
    project: Path,
    project_name: str,
    *,
    thread_ref: Optional[str] = None,
    new_thread: Optional[str] = None,
    interactive: bool = False,
) -> Optional[str]:
    """Bind a thread for one chosen project. A flag wins; else prompt when
    interactive; else None (no thread). ``--new-thread`` creates; ``--thread``
    continues a named one."""
    if new_thread:
        return create_thread(project, new_thread)
    if thread_ref:
        return thread_ref
    if interactive:
        return prompt_thread(project, project_name)
    return None


# --- launch ----------------------------------------------------------------

def build_launch(
    project: Path,
    *,
    control_home: Path,
    role: str = DEFAULT_ROLE,
    arc_ref: Optional[str] = None,
    skip_permissions: bool = True,
    dry_run: bool = False,
) -> List[str]:
    """Resolve seed + context profile into the scoped ``claude …`` argv for *project*.

    When *arc_ref* names a thread (нить), the seed carries that thread's passport
    so the session opens already bound to it. *skip_permissions* (default True)
    splices ``--dangerously-skip-permissions`` right after the program — a head
    session runs unattended. On a real launch the seed is persisted (so the new
    session can read it by path); on a dry-run nothing is written and a
    placeholder seed-file token is used, so the printed command still shows the
    exact scoped shape.
    """
    s = seed.seed_for_project(
        project, role=role, control_home=control_home, arc_ref=arc_ref
    )
    title = "tide-{0}".format(project.name)
    seed_file = DRY_RUN_SEED_FILE if dry_run else str(persist_seed(s, title))
    profile = context.load_profile(project)
    command = context.build_launch_command(seed_file, profile)
    if skip_permissions and SKIP_PERMISSIONS not in command:
        command[1:1] = [SKIP_PERMISSIONS]  # after the program, before the flags
    return command


def launch_entry(
    entry: Dict[str, str],
    *,
    adapter,
    control_home: Path,
    role: str = DEFAULT_ROLE,
    skip_permissions: bool = True,
    dry_run: bool = False,
) -> SpawnResult:
    """Build the scoped launch command for one rostered project and spawn it.

    A resolved thread is carried on the entry under the ``"thread"`` key (the
    arc_ref the seed binds to); absent ⇒ no thread binding. The session is
    spawned with ``cwd`` = the project dir (so its ``CLAUDE.md`` loads).
    """
    project = Path(entry["path"]).expanduser()
    command = build_launch(
        project,
        control_home=control_home,
        role=role,
        arc_ref=entry.get("thread"),
        skip_permissions=skip_permissions,
        dry_run=dry_run,
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
    skip_permissions: bool = True,
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
            skip_permissions=skip_permissions,
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


# --- launch preview (pure) -------------------------------------------------

def launch_preview(
    chosen: List[Dict[str, str]],
    *,
    control_home: Path,
    role: str = DEFAULT_ROLE,
    skip_permissions: bool = True,
) -> List[tuple]:
    """The scoped command(s) the human would enter with — for ``--dry-run`` /
    ``--debug`` display.

    Pure: builds each project's resolved ``claude …`` argv WITHOUT spawning (and
    without persisting a seed — the placeholder ``@<seed-file>`` token stands in
    for the temp path). Returns ``[(name, command_string), …]`` in pick order.
    """
    out = []
    for entry in chosen:
        project = Path(entry["path"]).expanduser()
        command = build_launch(
            project,
            control_home=control_home,
            role=role,
            arc_ref=entry.get("thread"),
            skip_permissions=skip_permissions,
            dry_run=True,
        )
        out.append((entry["name"], " ".join(command)))
    return out


# --- CLI handler -----------------------------------------------------------

def cmd_menu(args) -> int:
    """``tide menu`` — list the roster, pick N projects, launch seeded sessions."""
    root = paths.control_home()
    all_entries = list_entries(root)
    include_archived = bool(getattr(args, "all", False))
    entries = all_entries if include_archived else active_entries(all_entries)
    if not entries:
        if not all_entries:
            print(render_menu([]))  # truly empty roster
        else:
            print("(no active projects — use `tide menu --all` to include archived)")
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
    debug = bool(getattr(args, "debug", False))
    skip_permissions = not getattr(args, "no_skip_permissions", False)
    role = getattr(args, "role", None) or DEFAULT_ROLE

    # Bind a thread (нить) to each chosen project: continue an open one or start
    # new. A flag wins; else prompt when the project pick itself was interactive.
    interactive = not getattr(args, "pick", None)
    thread_ref = getattr(args, "thread", None)
    new_thread = getattr(args, "new_thread", None)
    chosen = [
        {**entry, "thread": resolve_thread(
            Path(entry["path"]).expanduser(),
            entry["name"],
            thread_ref=thread_ref,
            new_thread=new_thread,
            interactive=interactive,
        )}
        for entry in chosen
    ]

    # --debug (real launch) and --dry-run (no launch) both show the human EXACTLY
    # what scoped session will run — the resolved claude argv (strict MCP scoping
    # visible, no global MCP loaded) — BEFORE the terminal opens.
    if dry_run or debug:
        for name, command in launch_preview(
            chosen, control_home=root, role=role, skip_permissions=skip_permissions
        ):
            print("tide: {0} scoped command:".format(name))
            print("  {0}".format(command))

    results = launch_entries(
        chosen,
        control_home=root,
        adapter_name=adapter_name,
        role=role,
        skip_permissions=skip_permissions,
        dry_run=dry_run,
    )
    for entry, res in zip(chosen, results):
        flag = "ok" if res.ok else "FAILED"
        print("tide: {0} [{1}] {2}".format(entry["name"], flag, res.detail))
    return 0 if all(r.ok for r in results) else 1


def register(subparsers) -> None:
    """Add the top-level ``menu`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "menu", help="pick N projects from the roster and launch seeded sessions"
    )
    p.add_argument("--pick", help="non-interactive selection (e.g. '1,3' or 'all')")
    p.add_argument(
        "--all",
        action="store_true",
        help="include archived projects in the pick-list (default: active only)",
    )
    p.add_argument("--adapter", help="terminal adapter (orca|tmux; default from settings)")
    p.add_argument("--role", help="session role (default: orchestrator)")
    p.add_argument(
        "--thread",
        metavar="SLUG",
        help="continue an existing thread (нить) by slug — bind the session to it",
    )
    p.add_argument(
        "--new-thread",
        dest="new_thread",
        metavar="NAME",
        help="start a fresh thread (нить) with this name and bind the session to it",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="build seeds + adapter commands without opening a terminal",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="print the full scoped launch command before opening the terminal",
    )
    p.add_argument(
        "--no-skip-permissions",
        action="store_true",
        dest="no_skip_permissions",
        help="keep permission prompts on (default: --dangerously-skip-permissions, like tide terminal)",
    )
    p.set_defaults(func=cmd_menu, _cmd="menu")

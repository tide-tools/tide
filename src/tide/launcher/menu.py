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
import shlex
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from .. import fields, paths, roster, slug
from ..adapters import SETTINGS_KEY, SpawnResult, get_adapter, resolve_from_settings
from ..adapters.base import persist_seed
from ..arc import stream
from ..arc.stream import StreamError
from . import context, seed, select

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


# --- prism (призма) + session selection ------------------------------------
# After a project is picked, the human binds the session in TWO steps: pick a
# PRISM (призма — the arc through which a work-line is managed), then a SESSION
# inside it (continue one, or start new). At each step `0` ALWAYS means "+ new".
# The chosen session's passport becomes the seed's arc_text (sessions live in a
# prism substream that the top-stream read_arc_passport would miss); the prism
# name frames the seed. See tide.arc.stream.

PICK_NEW = "0"  # the universal "new" pick — 0 is always new


def _ask(prompt: str) -> str:
    """input() that treats EOF (piped/empty stdin) as an empty answer."""
    try:
        return input(prompt)
    except EOFError:
        return ""


def list_prisms(project: Path) -> List[Dict[str, str]]:
    """A project's open prisms for the picker: ``[{slug, name, goal, path}, …]``."""
    out = []
    for entry in stream.prism_entries(project):
        goal = (fields.read_field(stream.passport_path(entry), "goal") or "").strip()
        out.append({
            "slug": slug.entry_slug(entry.name),
            "name": entry.name,
            "goal": goal,
            "path": str(entry),
        })
    return out


def list_sessions(project: Path, prism_slug: str) -> List[Dict[str, str]]:
    """A prism's open sessions: ``[{slug, name, title, from, path}, …]`` in order."""
    out = []
    for entry in stream.session_entries(project, prism_slug):
        pp = entry / "arc.md"
        frm = (fields.read_field(pp, "from") or "").strip()
        title = (fields.read_field(pp, "title") or "").strip()
        out.append({
            "slug": slug.entry_slug(entry.name),
            "name": entry.name,
            "title": "" if title.startswith("<") else title,
            "from": frm,
            "path": str(entry),
        })
    return out


def render_prism_menu(project_name: str, prisms: List[Dict[str, str]]) -> str:
    """Numbered prism pick-list for *project_name*; ``0`` is the '+ new' row."""
    lines = ["Prism (призма) for {0} — 0 = new prism, or continue one:".format(project_name)]
    lines.append("  0) + new prism")
    for i, p in enumerate(prisms, start=1):
        goal = p.get("goal") or ""
        suffix = " — {0}".format(goal) if goal and not goal.startswith("<") else ""
        lines.append("  {0}) {1}{2}".format(i, p["slug"], suffix))
    return "\n".join(lines)


def render_session_menu(prism_slug: str, sessions: List[Dict[str, str]]) -> str:
    """Numbered session pick-list inside *prism_slug*, with from→ lineage; ``0`` = new."""
    lines = ["Session in prism {0} — 0 = new session, or continue one:".format(prism_slug)]
    lines.append("  0) + new session")
    for i, s in enumerate(sessions, start=1):
        title = " — {0}".format(s["title"]) if s.get("title") else ""
        lineage = " (from {0})".format(s["from"]) if s.get("from") else ""
        lines.append("  {0}) {1}{2}{3}".format(i, s["slug"], title, lineage))
    return "\n".join(lines)


def _prism_label(p: Dict[str, str]) -> str:
    """One prism row's label for the arrow picker — numeric index first: ``NN  slug — goal``."""
    index = p["name"].split("-", 1)[0] if p.get("name") else ""
    goal = p.get("goal") or ""
    suffix = " — {0}".format(goal) if goal and not goal.startswith("<") else ""
    head = "{0}  ".format(index) if index else ""
    return "{0}{1}{2}".format(head, p["slug"], suffix)


def _session_label(s: Dict[str, str]) -> str:
    """One session row's label for the arrow picker — numeric index first.

    ``NN  title (from …)`` once a title exists (set by offload/handoff), else
    ``NN  slug`` for a still-unnamed session.
    """
    index = s["name"].split("-", 1)[0]  # the NN prefix of NN-slug
    label = s["title"] if s.get("title") else s["slug"]
    lineage = " (from {0})".format(s["from"]) if s.get("from") else ""
    return "{0}  {1}{2}".format(index, label, lineage)


def parse_pick(raw: str, count: int):
    """Map a pick to :data:`PICK_NEW` or a 1-based index.

    ``0`` (also empty / ``new``) → new; a number in ``1..count`` → that row.
    Raises :class:`MenuError` on garbage or out-of-range.
    """
    s = (raw or "").strip().lower()
    if s in ("", "0", "n", "new", "+"):
        return PICK_NEW
    if not s.isdigit():
        raise MenuError("pick: invalid {0!r} (0 for new, or a number)".format(raw))
    n = int(s)
    if 1 <= n <= count:
        return n
    raise MenuError("pick: {0} out of range (0 for new, 1..{1})".format(n, count))


def _create_prism(project: Path, name: str) -> Optional[str]:
    name = (name or "").strip()
    if not name:
        return None
    return slug.entry_slug(stream.new_prism(project, name).name)


def _create_session(project: Path, prism_slug: str, name: str):
    name = (name or "").strip() or "session"
    entry = stream.new_session(project, prism_slug, name)
    return slug.entry_slug(entry.name), str(entry)


def _resolve_prism(project, project_name, *, prism_ref, new_prism, interactive):
    """Continue/create a prism. A flag wins; else interactive 0=new pick; else None."""
    if new_prism:
        return _create_prism(project, new_prism)
    if prism_ref:
        return prism_ref
    if not interactive:
        return None
    prisms = list_prisms(project)
    choice = select.select(
        "Prism (призма) for {0} — continue one, or start new:".format(project_name),
        [_prism_label(p) for p in prisms],
        allow_new=True,
        new_label="+ new prism",
    )
    if choice == select.NEW:
        return _create_prism(project, _ask("new prism name> "))
    return prisms[choice]["slug"]


def _resolve_session(project, prism_slug, *, session_ref, new_session, interactive):
    """Continue/create a session inside *prism_slug*. Returns (slug, path, is_new).

    ``is_new`` is True when the session was just created (so it gets a fresh pinned
    claude session-id); False when continuing an existing one (so it resumes).
    """
    if new_session:
        slug_, path_ = _create_session(project, prism_slug, new_session)
        return slug_, path_, True
    if session_ref:
        for s in list_sessions(project, prism_slug):
            if s["slug"] == session_ref:
                return session_ref, s["path"], False
        return session_ref, None, False
    if not interactive:
        # entering a prism non-interactively means a fresh session in it
        slug_, path_ = _create_session(project, prism_slug, "session")
        return slug_, path_, True
    sessions = list_sessions(project, prism_slug)
    choice = select.select(
        "Session in prism {0} — continue one, or start new:".format(prism_slug),
        [_session_label(s) for s in sessions],
        allow_new=True,
        new_label="+ new session",
    )
    if choice == select.NEW:
        # No name prompt — sessions are auto-named (NN-session); the human title
        # is written later by offload/handoff (the `title:` field).
        slug_, path_ = _create_session(project, prism_slug, "")
        return slug_, path_, True
    chosen = sessions[choice]
    return chosen["slug"], chosen["path"], False


def _bind_claude_session(session_path, *, is_new):
    """Resolve the pinned claude session-id for a session → (session_id, resume).

    Continuing an existing session that already carries a ``claude-session:`` id →
    resume that exact claude conversation. A new session (or a legacy one with no
    id) → mint/keep an id, launch fresh, and persist it so the NEXT entry resumes.
    """
    pp = Path(session_path) / "arc.md"
    stored = (fields.read_field(pp, "claude-session") or "").strip()
    has_id = bool(stored) and not stored.startswith("<")
    if has_id and not is_new:
        return stored, True  # return to the same conversation
    sid = stored if has_id else str(uuid.uuid4())
    fields.set_field(pp, "claude-session", sid)
    return sid, False  # fresh launch, but pinned so the next entry resumes


def resolve_session(
    project: Path,
    project_name: str,
    *,
    prism_ref: Optional[str] = None,
    new_prism: Optional[str] = None,
    session_ref: Optional[str] = None,
    new_session: Optional[str] = None,
    interactive: bool = False,
) -> Optional[Dict[str, Optional[str]]]:
    """Bind a session for one project: resolve a prism, then a session inside it.

    Returns ``{"arc_ref", "arc_text", "prism", "session_id", "resume"}`` — the
    session slug, its passport text for the seed, the owning prism, the pinned
    claude session-id, and whether to ``--resume`` that conversation (continuing an
    existing session) vs launch fresh (new session). None when nothing is bound.
    """
    prism = _resolve_prism(
        project, project_name, prism_ref=prism_ref, new_prism=new_prism, interactive=interactive
    )
    if prism is None:
        return None
    sess_slug, sess_path, is_new = _resolve_session(
        project, prism, session_ref=session_ref, new_session=new_session, interactive=interactive
    )
    if sess_slug is None:
        return None
    arc_text = None
    session_id = None
    resume = False
    if sess_path:
        try:
            arc_text = (Path(sess_path) / "arc.md").read_text(encoding="utf-8")
        except OSError:
            arc_text = None
        session_id, resume = _bind_claude_session(sess_path, is_new=is_new)
    return {
        "arc_ref": sess_slug,
        "arc_text": arc_text,
        "prism": prism,
        "session_id": session_id,
        "resume": resume,
    }


# --- launch ----------------------------------------------------------------

def build_launch(
    project: Path,
    *,
    control_home: Path,
    role: str = DEFAULT_ROLE,
    arc_ref: Optional[str] = None,
    arc_text: Optional[str] = None,
    prism_name: Optional[str] = None,
    session_id: Optional[str] = None,
    resume: bool = False,
    skip_permissions: bool = True,
    dry_run: bool = False,
) -> List[str]:
    """Resolve the scoped ``claude …`` argv for *project*.

    Two shapes, both scoped + (by default) ``--dangerously-skip-permissions``:

    * **resume** (``resume`` + *session_id*): ``claude --resume <id> || <fresh>`` —
      return to the SAME conversation; but claude only persists a session after a
      first turn, so a pinned-but-never-used id has no conversation and ``--resume``
      errors. The ``|| <fresh>`` fallback then launches a fresh seeded session under
      the same id, so re-entry is always forgiving (returned as ``sh -c``).
    * **fresh** (otherwise): a seeded launch (*arc_ref*/*arc_text* carry the bound
      session's passport, *prism_name* frames it). *session_id*, when given, is
      pinned via ``--session-id`` so a later entry can ``--resume`` this exact
      conversation. On dry-run a placeholder seed-file token is used.
    """
    fresh = _fresh_command(
        project,
        control_home=control_home,
        role=role,
        arc_ref=arc_ref,
        arc_text=arc_text,
        prism_name=prism_name,
        session_id=session_id,
        skip_permissions=skip_permissions,
        dry_run=dry_run,
    )
    if resume and session_id:
        resume_cmd = [context.SESSION_PROGRAM]
        if skip_permissions:
            resume_cmd.append(SKIP_PERMISSIONS)
        resume_cmd += ["--resume", session_id, "--strict-mcp-config"]
        shell = "{0} || {1}".format(shlex.join(resume_cmd), shlex.join(fresh))
        return ["sh", "-c", shell]
    return fresh


def _fresh_command(
    project: Path,
    *,
    control_home: Path,
    role: str,
    arc_ref: Optional[str],
    arc_text: Optional[str],
    prism_name: Optional[str],
    session_id: Optional[str],
    skip_permissions: bool,
    dry_run: bool,
) -> List[str]:
    """The seeded fresh-launch argv (with ``--session-id`` pinned when given)."""
    s = seed.seed_for_project(
        project,
        role=role,
        control_home=control_home,
        arc_ref=arc_ref,
        arc_text=arc_text,
        prism_name=prism_name,
    )
    title = "tide-{0}".format(project.name)
    seed_file = DRY_RUN_SEED_FILE if dry_run else str(persist_seed(s, title))
    profile = context.load_profile(project)
    command = context.build_launch_command(seed_file, profile)
    if session_id:
        command[1:1] = ["--session-id", session_id]  # pin for a future --resume
    if skip_permissions and SKIP_PERMISSIONS not in command:
        command[1:1] = [SKIP_PERMISSIONS]  # after the program, before the flags
    return command


def _session_launch_kwargs(entry: Dict) -> Dict:
    """Pull the bound-session launch kwargs off an entry (``{}`` when none bound)."""
    s = entry.get("session") or {}
    return {
        "arc_ref": s.get("arc_ref"),
        "arc_text": s.get("arc_text"),
        "prism_name": s.get("prism"),
        "session_id": s.get("session_id"),
        "resume": bool(s.get("resume")),
    }


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

    A resolved session is carried on the entry under the ``"session"`` key (the
    prism + session passport the seed binds to); absent ⇒ no binding. The session
    is spawned with ``cwd`` = the project dir (so its ``CLAUDE.md`` loads).
    """
    project = Path(entry["path"]).expanduser()
    command = build_launch(
        project,
        control_home=control_home,
        role=role,
        skip_permissions=skip_permissions,
        dry_run=dry_run,
        **_session_launch_kwargs(entry),
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
            skip_permissions=skip_permissions,
            dry_run=True,
            **_session_launch_kwargs(entry),
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
        if select.is_interactive_tty():
            # TTY: navigate the roster with arrows (single pick). The downstream
            # parse_selection still drives off a pick string, so map the chosen
            # 0-based index back to its 1-based row (no "+ new" for projects).
            idx = select.select(
                "Pick a project to lead this session:",
                ["{0} → {1}".format(e["name"], e["path"]) for e in entries],
                allow_new=False,
            )
            raw = str(int(idx) + 1)
        else:
            # non-TTY: keep the typed multi-pick ('1,3' / 'all') behavior intact.
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

    # Bind a session to each chosen project: pick a prism (призма), then a session
    # inside it (continue or 0=new). Flags win; else prompt when the project pick
    # itself was interactive; else no binding (back-compat with scripted --pick).
    interactive = not getattr(args, "pick", None)
    chosen = [
        {**entry, "session": resolve_session(
            Path(entry["path"]).expanduser(),
            entry["name"],
            prism_ref=getattr(args, "prism", None),
            new_prism=getattr(args, "new_prism", None),
            session_ref=getattr(args, "session", None),
            new_session=getattr(args, "new_session", None),
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
        "--prism",
        metavar="SLUG",
        help="continue an existing prism (призма) by slug (else 0=new in the picker)",
    )
    p.add_argument(
        "--new-prism",
        dest="new_prism",
        metavar="NAME",
        help="start a fresh prism (призма) with this name",
    )
    p.add_argument(
        "--session",
        metavar="SLUG",
        help="continue an existing session by slug inside the chosen prism",
    )
    p.add_argument(
        "--new-session",
        dest="new_session",
        metavar="NAME",
        help="start a fresh session with this name inside the chosen prism",
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

"""tide.launcher.handoff — the ``/tide-handoff`` engine (warm chat → fresh session).

The handoff turns a bloated live chat into a clean, already-working session on an
arc. Per design §12 it does four things, in order:

1. **distill → workspace** — write the conversation summary into the arc's
   ``workspace/handoff-<date>.md`` (handoff is *continuation*, not an ending, so
   it lands in ``workspace/`` — never ``output/``, which is reserved for the arc's
   durable finish).
2. **remind candidates** — surface the candidates backlog so anything worth
   keeping for cannon/method gets dropped via ``tide candidate add`` before the
   chat is abandoned.
3. **offer a fork** — ``continue`` (resume THIS arc in a fresh session) ·
   ``new`` (a fresh orchestrator session to pick a candidate) · ``close`` (just
   distil, no spawn).
4. **auto-spawn (toggle, default ON)** — for ``continue``/``new`` build the seed
   (:mod:`tide.launcher.seed`) and hand it to the configured terminal adapter
   (:mod:`tide.adapters`, Orca default). ``close`` never spawns.

Two layers as everywhere else: pure functions (``build_summary``, ``fork_offer``,
``autospawn_enabled`` …) are argparse- and disk-free and snapshot-testable;
:func:`run_handoff` is the disk+adapter orchestration; :func:`cmd_handoff` is the
thin CLI handler wired by ``cli.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import List, Optional

from .. import io as _io, paths, slug
from ..adapters import SpawnResult, get_adapter
from ..adapters.base import persist_seed
from ..arc import candidate
from ..arc.stream import StreamError
from . import context, seed

WORKSPACE_DIRNAME = "workspace"
SUMMARY_PREFIX = "handoff-"

# settings.json toggle — the auto-spawn default is ON; only an explicit false off.
SETTINGS_AUTOSPAWN_KEY = "handoff_autospawn"

FORK_CONTINUE = "continue"
FORK_NEW = "new"
FORK_CLOSE = "close"
FORK_MODES = (FORK_CONTINUE, FORK_NEW, FORK_CLOSE)


class HandoffError(StreamError):
    """A user-facing handoff error (unknown mode, no such open arc …).

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on
    the same ``except`` arm (prints ``tide: …``, exits nonzero).
    """


# --- summary assembly (pure) -----------------------------------------------

def _today(date: Optional[str]) -> str:
    return date or _date.today().isoformat()


def summary_filename(date: Optional[str] = None) -> str:
    """The workspace filename for a handoff distil (``handoff-<date>.md``)."""
    return "{0}{1}.md".format(SUMMARY_PREFIX, _today(date))


def _bullets(title: str, items: Optional[List[str]]) -> List[str]:
    """A ``## title`` section rendered as a bullet list, or [] when empty."""
    kept = [i.strip() for i in (items or []) if i and i.strip()]
    if not kept:
        return []
    return ["", "## {0}".format(title), *["- {0}".format(i) for i in kept]]


def build_summary(
    *,
    mode: str,
    arc_ref: str,
    state: str = "",
    decisions: Optional[List[str]] = None,
    artifacts: Optional[List[str]] = None,
    next_step: str = "",
    open_questions: Optional[List[str]] = None,
    date: Optional[str] = None,
) -> str:
    """Assemble the handoff distil markdown from already-extracted pieces (pure).

    Sections, in order: a frontmatter-ish header (mode/arc/date), **Where we are**
    (always present — placeholder when empty), then **Decisions**, **Artifacts**,
    **Next step**, and **Open questions** (each omitted when empty). The shape is
    fixed so a fresh session — and the snapshot tests — can rely on it.
    """
    lines: List[str] = [
        "# tide handoff — {0}".format(arc_ref),
        "",
        "mode: {0}".format(mode),
        "arc: {0}".format(arc_ref),
        "date: {0}".format(_today(date)),
        "",
        "## Where we are",
        state.strip() if state.strip() else "(state not distilled — fill before spawning)",
    ]
    lines += _bullets("Decisions", decisions)
    lines += _bullets("Artifacts", artifacts)
    if next_step.strip():
        lines += ["", "## Next step", next_step.strip()]
    lines += _bullets("Open questions", open_questions)
    return "\n".join(lines) + "\n"


# --- arc resolution + workspace write --------------------------------------

def resolve_open_entry(root: Path, arc_ref: str) -> Optional[Path]:
    """First OPEN top-stream entry whose slug matches *arc_ref* (goal preferred)."""
    arcs = paths.arcs_dir(root)
    if not arcs.is_dir():
        return None
    want = slug.slugify(arc_ref)
    matches = [
        p
        for p in arcs.iterdir()
        if p.is_dir()
        and p.name != paths.CANDIDATES_DIRNAME
        and not slug.is_closed_entry(p.name)
        and slug.entry_slug(p.name) == want
    ]
    if not matches:
        return None
    matches.sort(key=lambda p: (not slug.is_goal_entry(p.name), p.name))
    return matches[0]


def write_summary(
    root: Path, arc_ref: str, summary: str, *, date: Optional[str] = None
) -> Path:
    """Write *summary* into the open arc's ``workspace/handoff-<date>.md``.

    Raises :class:`HandoffError` when *arc_ref* names no open arc — a handoff must
    anchor to a real arc (so the fresh session has somewhere to continue from).
    """
    entry = resolve_open_entry(root, arc_ref)
    if entry is None:
        raise HandoffError(
            "handoff: no open arc matching {0!r} — open or create one first "
            "('tide arc new {0}')".format(arc_ref)
        )
    ws = entry / WORKSPACE_DIRNAME
    ws.mkdir(parents=True, exist_ok=True)
    path = ws / summary_filename(date)
    _io.atomic_write(path, summary)
    return path


# --- candidate reminder + fork offer (pure-ish) ----------------------------

def candidate_reminder(root: Path) -> str:
    """A reminder block listing the candidates backlog before the chat is dropped."""
    backlog = candidate.render_list(root)
    return (
        "Candidates backlog (drop anything worth keeping with "
        "'tide candidate add <slug>'):\n{0}".format(backlog)
    )


def fork_offer(arc_ref: str) -> str:
    """The three-way fork prompt (continue / new / close) for *arc_ref*."""
    return "\n".join(
        [
            "Fork — how to carry the thread:",
            "  continue → resume arc {0} in a fresh seeded session".format(arc_ref),
            "  new      → a fresh orchestrator session to promote a candidate",
            "  close    → stop here; thread distilled, no spawn",
        ]
    )


# --- auto-spawn toggle ------------------------------------------------------

def autospawn_enabled(settings: Optional[dict]) -> bool:
    """Resolve the auto-spawn toggle (default ON; only an explicit false disables)."""
    if isinstance(settings, dict):
        value = settings.get(SETTINGS_AUTOSPAWN_KEY)
        if isinstance(value, bool):
            return value
    return True


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


def read_autospawn(root: Path) -> bool:
    """The effective auto-spawn toggle for project *root* (settings, default ON)."""
    return autospawn_enabled(_read_settings(root))


# --- orca workspace focus (best-effort) -------------------------------------

def _maybe_activate_orca(arc_dir: Path) -> bool:
    """Best-effort focus of the arc's Orca workspace (never raises / blocks entry)."""
    try:
        from ..adapters import orca_worktree
        return orca_worktree.activate_workspace(Path(arc_dir))
    except Exception:  # noqa: BLE001  a failed focus must never block the handoff
        return False


# --- orchestration ----------------------------------------------------------

@dataclass
class HandoffResult:
    """Outcome of a handoff: where the distil landed + whether a session spawned.

    * ``mode`` — the chosen fork (continue / new / close).
    * ``summary_path`` — the workspace file the distil was written to.
    * ``candidate_reminder`` — the backlog reminder text (always computed).
    * ``fork_offer`` — the three-way fork prompt text.
    * ``autospawn`` — the effective toggle value used.
    * ``spawn`` — the adapter :class:`SpawnResult` (None for ``close`` / toggle-off).
    """

    mode: str
    summary_path: Path
    candidate_reminder: str
    fork_offer: str
    autospawn: bool
    spawn: Optional[SpawnResult] = None
    notes: List[str] = field(default_factory=list)


def run_handoff(
    root: Path,
    *,
    arc_ref: str,
    mode: str = FORK_CONTINUE,
    summary: Optional[str] = None,
    autospawn: Optional[bool] = None,
    adapter_name: Optional[str] = None,
    dry_run: bool = False,
    date: Optional[str] = None,
) -> HandoffResult:
    """Run a handoff: distil → workspace, remind candidates, offer fork, maybe spawn.

    *summary* is the distilled markdown (the caller prepares it); when absent a
    minimal stub is built from *mode*/*arc_ref* so the call never silently writes
    nothing. For ``continue``/``new`` and an effective auto-spawn toggle a fresh
    session is spawned via the adapter — ``continue`` seeds THIS arc, ``new`` seeds
    a project-level orchestrator (no arc) so it can pick a candidate. ``close``
    never spawns. ``dry_run`` builds the adapter command without driving any UI.
    """
    root = Path(root)
    if mode not in FORK_MODES:
        raise HandoffError(
            "handoff: unknown mode {0!r} — pick one of {1}".format(
                mode, ", ".join(FORK_MODES)
            )
        )

    # Resolve which project owns the arc (cwd-project first, then the roster) so a
    # handoff fired from the control-home anchors to the RIGHT project's arc, and
    # the distil lands in that project's arc workspace — not the control-home root.
    from ..arc import worktree
    owner_root, arc_entry = worktree.resolve_project_and_arc(root, arc_ref)

    text = summary if summary is not None else build_summary(
        mode=mode, arc_ref=arc_ref, date=date
    )
    summary_path = write_summary(owner_root, arc_ref, text, date=date)

    effective_spawn = read_autospawn(owner_root) if autospawn is None else bool(autospawn)
    result = HandoffResult(
        mode=mode,
        summary_path=summary_path,
        candidate_reminder=candidate_reminder(root),
        fork_offer=fork_offer(arc_ref),
        autospawn=effective_spawn,
    )

    if mode == FORK_CLOSE:
        result.notes.append("close: thread distilled to {0}; no spawn".format(summary_path))
        return result
    if not effective_spawn:
        result.notes.append(
            "auto-spawn off ({0}=false) — run 'tide {1}' by hand to resume".format(
                SETTINGS_AUTOSPAWN_KEY, root.resolve().name
            )
        )
        return result

    # continue seeds THIS arc in its OWNING project (land in its worktree); new seeds
    # a project-level orchestrator at the control-home (pick a candidate, no arc).
    spawn_arc = arc_ref if mode == FORK_CONTINUE else None
    seed_root = owner_root if mode == FORK_CONTINUE else root
    control_home = seed_root if paths.is_control_home(seed_root) else None
    seed_text = seed.seed_for_project(
        seed_root, arc_ref=spawn_arc, role=seed.ROLE_ORCHESTRATOR, control_home=control_home
    )
    adapter = get_adapter(adapter_name)
    title = "tide-handoff-{0}".format(slug.slugify(arc_ref) or "session")
    seed_file = "<seed-file>" if dry_run else str(persist_seed(seed_text, title))
    command = context.build_launch_command(seed_file, context.load_profile(seed_root))

    if mode == FORK_CONTINUE:
        spawn_cwd = worktree.resolve_cwd(owner_root, arc_entry)
        if not dry_run and arc_entry is not None:
            _maybe_activate_orca(arc_entry)
    else:
        spawn_cwd = root.resolve()

    result.spawn = adapter.spawn(
        command=command, cwd=str(spawn_cwd), title=title, dry_run=dry_run
    )
    result.notes.append(
        "{0}: {1}".format(
            "spawned" if result.spawn.ok and not dry_run else
            ("dry-run" if dry_run else "spawn FAILED"),
            result.spawn.detail,
        )
    )
    return result


# --- CLI wiring ------------------------------------------------------------

def _read_summary_arg(args) -> Optional[str]:
    """Resolve the distil text from ``--summary-file`` (None ⇒ build a stub)."""
    sf = getattr(args, "summary_file", None)
    if not sf:
        return None
    p = Path(sf).expanduser()
    if not p.is_file():
        raise HandoffError("handoff: --summary-file {0} not found".format(p))
    return p.read_text(encoding="utf-8")


def cmd_handoff(args) -> int:
    """``tide handoff <arc>`` — distil to workspace, remind, offer fork, maybe spawn."""
    root = paths.require_tide_root()
    summary = _read_summary_arg(args)
    autospawn = False if getattr(args, "no_spawn", False) else None
    result = run_handoff(
        root,
        arc_ref=args.arc,
        mode=getattr(args, "mode", None) or FORK_CONTINUE,
        summary=summary,
        autospawn=autospawn,
        adapter_name=getattr(args, "adapter", None),
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    print("tide: handoff [{0}] → {1}".format(result.mode, result.summary_path))
    print(result.candidate_reminder)
    print(result.fork_offer)
    for note in result.notes:
        print("  {0}".format(note))
    spawned_ok = result.spawn is None or result.spawn.ok
    return 0 if spawned_ok else 1


def register(subparsers) -> None:
    """Add the ``handoff`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "handoff",
        help="warm-handoff: distil chat → arc workspace, then fork (continue|new|close)",
    )
    p.add_argument("arc", help="the open arc to anchor the handoff on")
    p.add_argument(
        "--mode",
        choices=FORK_MODES,
        default=FORK_CONTINUE,
        help="fork: continue (resume this arc) | new (pick a candidate) | close",
    )
    p.add_argument(
        "--summary-file",
        dest="summary_file",
        help="path to the prepared distil markdown (default: a minimal stub)",
    )
    p.add_argument(
        "--no-spawn",
        action="store_true",
        dest="no_spawn",
        help="force the auto-spawn toggle off for this run",
    )
    p.add_argument("--adapter", help="terminal adapter (orca|tmux; default from settings)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="build the seed + adapter command without opening a terminal",
    )
    p.set_defaults(func=cmd_handoff, _cmd="handoff")

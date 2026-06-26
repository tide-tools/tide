"""tide.arc.worktree — per-arc git worktree isolation (the FILE axis).

Each arc can have an isolated git worktree so worker edits never touch main or
sibling worktrees. This is the FILE-axis gate: contradictions surface as merge
conflicts on land (mirroring cannon-merge on the truth axis).

Public API
----------
create(root, arc_dir)     → Path | None   create a worktree for the arc
land(root, arc_dir)       → LandResult    merge the branch back to base
remove(root, arc_dir)     → bool          remove worktree + delete branch
has_worktree(root, arc_dir) → bool        True when arc has a recorded branch
is_git_repo(root)         → bool          True when root/.git exists

CLI: ``tide arc work <slug>``  (calls create). ``tide arc land`` lives in
:mod:`tide.arc.land` (the atomic, strictness-gated land) and calls :func:`land` /
:func:`remove` here.

Non-git projects fall back gracefully: create returns None, land/remove are no-ops.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .. import fields, paths, slug

# --- constants ---------------------------------------------------------------

WT_DIRNAME = "worktrees"
BRANCH_FIELD = "worktree-branch"
BRANCH_PREFIX = "arc/"


# --- errors ------------------------------------------------------------------

class WorktreeError(Exception):
    """A user-facing worktree error (no commit, worktree exists, git failed …)."""


# --- result type -------------------------------------------------------------

@dataclass(frozen=True)
class LandResult:
    """Outcome of a land operation."""
    landed: bool
    conflict: bool
    branch: str
    detail: str


# --- git helpers -------------------------------------------------------------

def is_git_repo(root: Path) -> bool:
    """True when *root* contains a ``.git`` entry (dir or file for worktrees)."""
    return (Path(root) / ".git").exists()


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in *root*; raise on failure unless ``check=False``."""
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _has_commit(root: Path) -> bool:
    """True when the repo has at least one commit (HEAD is resolvable)."""
    return _git(root, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0


def current_branch(root: Path) -> str:
    """Return the current branch name in *root*."""
    return _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


# --- arc/worktree path helpers -----------------------------------------------

def branch_for(arc_dir: Path) -> str:
    """Canonical branch name for an arc, e.g. ``arc/fix-the-leak``."""
    return BRANCH_PREFIX + slug.entry_slug(Path(arc_dir).name)


def worktree_path(root: Path, arc_dir: Path) -> Path:
    """Where the arc's worktree lives: ``.tide/worktrees/<slug>``."""
    return paths.tide_dir(root) / WT_DIRNAME / slug.entry_slug(Path(arc_dir).name)


def _passport(arc_dir: Path) -> Path:
    """The arc passport (arc.md or goal doc) for *arc_dir*."""
    from . import stream
    return stream.passport_path(Path(arc_dir))


# --- re-entry cwd resolution (DRY for go + handoff) ---------------------------

def resolve_cwd(root: Path, arc_dir: Optional[Path]) -> Path:
    """The dir a session should land in for an arc: orca-workspace > git worktree > root.

    Reads the passport's ``orca-workspace`` (an absolute path) first, then the raw
    git worktree path — each used ONLY when it still exists on disk — else falls
    back to the project *root*. A pure read: it never creates anything, and a stale
    field (worktree removed but field lingers) degrades to the next option rather
    than handing a caller a missing dir to ``chdir`` into.
    """
    root = Path(root)
    if arc_dir is None:
        return root
    arc_dir = Path(arc_dir)

    # 1. Orca-managed workspace (absolute path recorded in the passport).
    from ..adapters.orca_worktree import WORKSPACE_FIELD  # lazy: stable field name
    ws = fields.read_field(_passport(arc_dir), WORKSPACE_FIELD)
    if ws:
        ws_path = Path(ws).expanduser()
        if ws_path.exists():
            return ws_path

    # 2. Raw-git worktree path (only when present on disk).
    wt = worktree_path(root, arc_dir)
    if wt.exists():
        return wt

    # 3. Fall back to the project root.
    return root


def _find_open_arc(root: Path, arc_ref: str) -> Optional[Path]:
    """First OPEN top-stream entry in *root* matching *arc_ref* (goal preferred)."""
    from . import stream
    return stream._resolve(paths.arcs_dir(Path(root)), arc_ref, closed=False)


def resolve_project_and_arc(root: Path, arc_ref: str) -> Tuple[Path, Optional[Path]]:
    """Resolve which project owns *arc_ref* and its open arc dir (cross-project).

    Looks in *root* (the cwd project / control-home) first; when *root* is a
    control-home and the arc is not there, walks the roster and returns the first
    registered project that holds an open arc matching *arc_ref*. Returns
    ``(root, None)`` when nothing matches anywhere, so the caller falls back to the
    bare project root. A pure read — no disk mutation.
    """
    root = Path(root)
    entry = _find_open_arc(root, arc_ref)
    if entry is not None:
        return root, entry

    if paths.is_control_home(root):
        from .. import roster  # lazy: avoid a launcher/arc import cycle
        for item in roster.read_roster(root):
            proj = Path(item["path"]).expanduser()
            if proj == root or not (proj / paths.TIDE_DIR).is_dir():
                continue
            sub = _find_open_arc(proj, arc_ref)
            if sub is not None:
                return proj, sub

    return root, None


# --- .gitignore helper -------------------------------------------------------

def _ensure_wt_ignored(root: Path) -> None:
    """Ensure ``.tide/worktrees/`` exists with a ``*`` .gitignore (never commit wt refs)."""
    home = paths.tide_dir(root) / WT_DIRNAME
    home.mkdir(parents=True, exist_ok=True)
    ig = home / ".gitignore"
    if not ig.exists():
        ig.write_text("*\n", encoding="utf-8")


# --- core operations ---------------------------------------------------------

def create(root: Path, arc_dir: Path) -> Optional[Path]:
    """Create an isolated git worktree for the arc.

    Returns the worktree path on success, **None** when *root* is not a git repo
    (graceful non-git fallback). Raises :class:`WorktreeError` when the repo has
    no commit or the worktree already exists.
    """
    root, arc_dir = Path(root), Path(arc_dir)
    if not is_git_repo(root):
        return None
    if not _has_commit(root):
        raise WorktreeError("repo has no commit to branch from — make an initial commit first")
    wt = worktree_path(root, arc_dir)
    branch = branch_for(arc_dir)
    if wt.exists():
        raise WorktreeError("worktree already exists at {0}".format(wt))
    _ensure_wt_ignored(root)
    p = _git(root, "worktree", "add", "-b", branch, str(wt), check=False)
    if p.returncode != 0:
        raise WorktreeError("git worktree add failed: {0}".format(p.stderr.strip()))
    fields.set_field(_passport(arc_dir), BRANCH_FIELD, branch)
    return wt


def has_worktree(root: Path, arc_dir: Path) -> bool:
    """True when the arc passport records a ``worktree-branch`` field.

    Intentionally reads only the passport field (no git call) so it is safe to
    call on non-git projects — returns False (no worktree) rather than crashing.
    """
    if not is_git_repo(root):
        return False
    return bool(fields.read_field(_passport(arc_dir), BRANCH_FIELD))


def land(root: Path, arc_dir: Path, base: Optional[str] = None) -> LandResult:
    """Merge the arc branch back to *base* (default: current branch).

    Returns a :class:`LandResult`.  When the merge conflicts, aborts cleanly and
    returns ``conflict=True`` — the repo is left in a clean state with no
    lingering ``MERGE_HEAD``.  Pure no-op (landed=False, conflict=False) for
    non-git repos or arcs without a recorded branch.
    """
    root, arc_dir = Path(root), Path(arc_dir)
    branch = fields.read_field(_passport(arc_dir), BRANCH_FIELD) or ""
    if not is_git_repo(root) or not branch:
        return LandResult(landed=False, conflict=False, branch=branch, detail="no worktree to land")
    target = base or current_branch(root)
    if current_branch(root) != target:
        _git(root, "checkout", target)
    p = _git(root, "merge", "--no-ff", "--no-edit", branch, check=False)
    if p.returncode != 0:
        _git(root, "merge", "--abort", check=False)
        return LandResult(
            landed=False,
            conflict=True,
            branch=branch,
            detail="conflict landing {0} onto {1}".format(branch, target),
        )
    return LandResult(
        landed=True,
        conflict=False,
        branch=branch,
        detail="landed {0} onto {1}".format(branch, target),
    )


def remove(root: Path, arc_dir: Path, *, delete_branch: bool = True) -> bool:
    """Remove the worktree directory and optionally the branch; clear the passport field.

    Returns True if the worktree directory was present and removed, False otherwise.
    No-op for non-git projects.
    """
    root, arc_dir = Path(root), Path(arc_dir)
    if not is_git_repo(root):
        return False
    wt = worktree_path(root, arc_dir)
    branch = fields.read_field(_passport(arc_dir), BRANCH_FIELD) or ""
    removed = False
    if wt.exists():
        _git(root, "worktree", "remove", "--force", str(wt), check=False)
        removed = True
    _git(root, "worktree", "prune", check=False)
    if delete_branch and branch:
        _git(root, "branch", "-D", branch, check=False)
    if branch:
        fields.set_field(_passport(arc_dir), BRANCH_FIELD, "")
    return removed


# --- CLI helpers -------------------------------------------------------------

def _resolve_arc(root: Path, ref: str, goal_slug: Optional[str] = None) -> Path:
    """Resolve *ref* to an open arc dir (raises WorktreeError when not found)."""
    from . import stream
    stream_dir = stream._search_dir(root, goal_slug)
    entry = stream._resolve(stream_dir, ref, closed=False)
    if entry is None:
        raise WorktreeError(
            "open arc/goal {0!r} not found (closed or missing?)".format(ref)
        )
    return entry


def _cmd_work(args) -> int:
    """Handler for ``tide arc work <slug>``.

    Routes to the Orca-native path (GitHub issue + orca worktree + Claude agent)
    when ``orca_available()`` is True; falls back to raw-git worktree isolation
    when Orca is unavailable.
    """
    root = paths.require_tide_root()
    try:
        arc_dir = _resolve_arc(root, args.slug, goal_slug=getattr(args, "goal", None))
    except WorktreeError as exc:
        print("tide: {0}".format(exc), file=sys.stderr)
        return 1

    # --- Orca-native path ---
    from ..adapters import orca_worktree as _ow  # lazy: avoid circular import
    if _ow.orca_available():
        try:
            workspace = _ow.orca_work(root, arc_dir)
            issue_num = fields.read_field(_passport(arc_dir), _ow.ISSUE_FIELD) or "?"
            print(
                "tide: orca worktree created at {ws} "
                "(issue #{n}, agent: claude)".format(ws=workspace, n=issue_num)
            )
            return 0
        except _ow.OrcaWorkError as exc:
            print("tide: orca: {0}".format(exc), file=sys.stderr)
            return 1

    # --- Headless raw-git fallback ---
    try:
        wt = create(root, arc_dir)
        if wt is None:
            print(
                "tide: not a git repo — worktree isolation skipped "
                "(arc {0} is open for editing in place)".format(args.slug),
                file=sys.stderr,
            )
            return 0
        print("tide: created worktree {0} on branch {1}".format(wt, branch_for(arc_dir)))
        return 0
    except WorktreeError as exc:
        print("tide: {0}".format(exc), file=sys.stderr)
        return 1


def _add_goal_opt(p) -> None:
    p.add_argument("-g", "--goal", help="operate inside this goal's substream")


def register(arc_subparsers) -> None:
    """Add ``tide arc work`` to the ``tide arc`` subparser group.

    ``tide arc land`` is no longer registered here — the atomic, strictness-gated
    land lives in :mod:`tide.arc.land` (``register_land``), which still calls the
    low-level :func:`land` / :func:`remove` primitives this module owns.
    """
    wp = arc_subparsers.add_parser(
        "work",
        help="create an isolated git worktree for the arc (FILE-axis isolation)",
    )
    wp.add_argument("slug")
    _add_goal_opt(wp)
    wp.set_defaults(func=_cmd_work, _cmd="arc work")

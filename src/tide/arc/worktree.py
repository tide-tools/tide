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

CLI: ``tide arc work <slug>``  (calls create)
     ``tide arc land <slug>``  (calls land; on conflict prints warning, exits 1)

Non-git projects fall back gracefully: create returns None, land/remove are no-ops.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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
    """Handler for ``tide arc work <slug>``."""
    root = paths.require_tide_root()
    try:
        arc_dir = _resolve_arc(root, args.slug, goal_slug=getattr(args, "goal", None))
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


def _cmd_land(args) -> int:
    """Handler for ``tide arc land <slug>``."""
    root = paths.require_tide_root()
    try:
        arc_dir = _resolve_arc(root, args.slug, goal_slug=getattr(args, "goal", None))
    except WorktreeError as exc:
        print("tide: {0}".format(exc), file=sys.stderr)
        return 1

    result = land(root, arc_dir)
    if result.conflict:
        print(
            "tide: CONFLICT — {0}  (worktree branch NOT merged; resolve manually)".format(
                result.detail
            ),
            file=sys.stderr,
        )
        return 1
    if not result.landed:
        print("tide: {0}".format(result.detail))
        return 0
    # Landed cleanly → clean up worktree.
    remove(root, arc_dir)
    print("tide: {0} (worktree removed)".format(result.detail))
    return 0


def _add_goal_opt(p) -> None:
    p.add_argument("-g", "--goal", help="operate inside this goal's substream")


def register(arc_subparsers) -> None:
    """Add ``tide arc work`` and ``tide arc land`` to the ``tide arc`` subparser group."""
    wp = arc_subparsers.add_parser(
        "work",
        help="create an isolated git worktree for the arc (FILE-axis isolation)",
    )
    wp.add_argument("slug")
    _add_goal_opt(wp)
    wp.set_defaults(func=_cmd_work, _cmd="arc work")

    lp = arc_subparsers.add_parser(
        "land",
        help="merge the arc worktree branch back to base (gate: conflict surfaces and blocks)",
    )
    lp.add_argument("slug")
    _add_goal_opt(lp)
    lp.set_defaults(func=_cmd_land, _cmd="arc land")

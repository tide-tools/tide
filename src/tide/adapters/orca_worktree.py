"""tide.adapters.orca_worktree — Orca-native arc execution adapter.

Routes ``tide arc work`` through the Orca CLI when Orca is running: creates a
GitHub issue (the durable commitment), then an Orca-managed worktree with an
attached Claude agent.  Falls back to the existing raw-git worktree when Orca
is unavailable (transparent degradation).

gh-first PR flow (``tide arc land``): push branch → ``gh pr create`` with
"Closes #N" → ``orca worktree set --workspace-status in-review``.  The PR is
merged through GitHub; GitHub auto-closes the issue via "Closes #N".

ABANDON-GATE (``tide arc close``): refuses unless the linked GitHub issue is
CLOSED.  The open issue IS the durable commitment — the arc cannot be sealed
while work is still in flight.

All external calls go through thin, mockable module-level functions:
    _run_orca, _run_gh, _run_git
Patch those in tests; never call real Orca/gh/git from the test suite.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .. import fields, slug
from ..arc.stream import StreamError, passport_path

# --- passport field names ---------------------------------------------------

ISSUE_FIELD = "orca-issue"          # GitHub issue number (string)
WORKSPACE_FIELD = "orca-workspace"  # absolute path to orca workspace
BASE_BRANCH_FIELD = "orca-base-branch"  # base branch used at worktree creation
# branch is stored under arc.worktree.BRANCH_FIELD ("worktree-branch")
_WORKTREE_BRANCH_FIELD = "worktree-branch"


# --- error types ------------------------------------------------------------

class OrcaWorkError(Exception):
    """Base for orca-worktree errors (non-StreamError — callers catch explicitly)."""


class OrcaLandError(OrcaWorkError):
    """Raised when gh-first land cannot proceed (no commits, push/PR failure)."""


class AbandonGateError(StreamError):
    """Raised when arc close is blocked by an open GitHub issue.

    Inherits :class:`~tide.arc.stream.StreamError` so ``cli.main`` catches and
    prints it without a traceback.
    """


# --- thin subprocess layer (monkeypatch these in tests) ---------------------

def _run_orca(args: list, *, check: bool = True) -> subprocess.CompletedProcess:
    """Run ``orca <args>``; capture stdout/stderr."""
    return subprocess.run(
        ["orca"] + list(args),
        check=check,
        capture_output=True,
        text=True,
    )


def _run_gh(
    args: list,
    *,
    check: bool = True,
    cwd: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """Run ``gh <args>``; capture stdout/stderr."""
    return subprocess.run(
        ["gh"] + list(args),
        check=check,
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def _run_git(root: Path, args: list, *, check: bool = True) -> subprocess.CompletedProcess:
    """Run ``git -C <root> <args>``; capture stdout/stderr."""
    return subprocess.run(
        ["git", "-C", str(root)] + list(args),
        check=check,
        capture_output=True,
        text=True,
    )


# --- availability -----------------------------------------------------------

def orca_available() -> bool:
    """True when the Orca binary exists AND the Orca app is running+reachable.

    Checks ``orca status --json``.  Returns False for any error (missing binary,
    app not running, JSON parse failure, unexpected schema).
    """
    if shutil.which("orca") is None:
        return False
    try:
        p = _run_orca(["status", "--json"], check=False)
        data = json.loads(p.stdout)
        return bool(
            data.get("app", {}).get("running")
            and data.get("runtime", {}).get("reachable")
        )
    except Exception:  # noqa: BLE001
        return False


# --- helpers ----------------------------------------------------------------

def _orca_name(arc_dir: Path) -> str:
    """Orca workspace/branch name for an arc: ``arc-<slug>`` (hyphen, not slash)."""
    return "arc-{0}".format(slug.entry_slug(Path(arc_dir).name))


def _arc_goal(arc_dir: Path) -> str:
    """The arc's goal field (or its directory name as fallback)."""
    val = fields.read_field(passport_path(arc_dir), "goal") or ""
    val = val.strip()
    # Skip unfilled template placeholders.
    if not val or val.startswith("<"):
        return Path(arc_dir).name
    return val


def _arc_passport_text(arc_dir: Path) -> str:
    """Full text of the arc's passport file (for the GitHub issue body)."""
    p = passport_path(arc_dir)
    if p.is_file():
        return p.read_text(encoding="utf-8")
    return Path(arc_dir).name


# --- create (gh issue + orca worktree) -------------------------------------

def create_issue(root: Path, arc_dir: Path) -> str:
    """Create a GitHub issue for the arc; return the issue number string.

    Calls ``gh issue create --label tide-arc --title <goal> --body <passport>``.
    Parses the issue number from the returned URL.
    """
    goal = _arc_goal(arc_dir)
    body = _arc_passport_text(arc_dir)
    p = _run_gh(
        ["issue", "create", "--label", "tide-arc", "--title", goal, "--body", body],
        cwd=str(root),
    )
    # gh prints: https://github.com/org/repo/issues/123
    url = p.stdout.strip()
    return url.rstrip("/").split("/")[-1]


def create_orca_worktree(
    root: Path,
    arc_dir: Path,
    issue_num: str,
    base_branch: str,
) -> str:
    """Create an Orca-managed worktree with a Claude agent; return the workspace path.

    Calls ``orca worktree create --repo path:<root> --name arc-<slug>
    --base-branch <base> --issue <N> --agent claude --prompt <goal> --activate --json``.
    """
    name = _orca_name(arc_dir)
    goal = _arc_goal(arc_dir)
    p = _run_orca([
        "worktree", "create",
        "--repo", "path:{0}".format(root),
        "--name", name,
        "--base-branch", base_branch,
        "--issue", issue_num,
        "--agent", "claude",
        "--prompt", goal,
        "--activate",
        "--json",
    ])
    try:
        data = json.loads(p.stdout)
        return data.get("path", "")
    except (json.JSONDecodeError, AttributeError):
        return ""


def orca_work(root: Path, arc_dir: Path) -> str:
    """Create a GitHub issue + Orca worktree for the arc; record both in the passport.

    Returns the workspace path.  Raises :class:`OrcaWorkError` on failure.
    """
    # Discover the base branch from the main worktree.
    p_base = _run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    base_branch = p_base.stdout.strip() or "main"

    issue_num = create_issue(root, arc_dir)
    workspace = create_orca_worktree(root, arc_dir, issue_num, base_branch)

    passport = passport_path(arc_dir)
    fields.set_field(passport, ISSUE_FIELD, issue_num)
    fields.set_field(passport, WORKSPACE_FIELD, workspace)
    fields.set_field(passport, BASE_BRANCH_FIELD, base_branch)
    fields.set_field(passport, _WORKTREE_BRANCH_FIELD, _orca_name(arc_dir))
    return workspace


# --- gh-first land ----------------------------------------------------------

def _commits_ahead(root: Path, base: str, branch: str) -> bool:
    """True when *branch* has at least one commit not in *base*."""
    p = _run_git(root, ["rev-list", "--count", "{0}..{1}".format(base, branch)], check=False)
    try:
        return int(p.stdout.strip()) > 0
    except (ValueError, AttributeError):
        return False


def orca_land(root: Path, arc_dir: Path) -> str:
    """gh-first land: push branch → PR create → orca in-review.  Returns PR URL.

    Guards: at least one commit ahead of base.
    Raises :class:`OrcaLandError` when the guard fails.
    """
    passport = passport_path(arc_dir)
    issue_num = fields.read_field(passport, ISSUE_FIELD) or ""
    branch = fields.read_field(passport, _WORKTREE_BRANCH_FIELD) or _orca_name(arc_dir)
    base_branch = fields.read_field(passport, BASE_BRANCH_FIELD) or "main"

    if not _commits_ahead(root, base_branch, branch):
        raise OrcaLandError(
            "nothing to push: {b} has no commits ahead of {base} — "
            "commit your work in the Orca worktree first".format(b=branch, base=base_branch)
        )

    _run_git(root, ["push", "-u", "origin", branch])

    goal = _arc_goal(arc_dir)
    body = "Closes #{n}".format(n=issue_num) if issue_num else goal
    p_pr = _run_gh(
        ["pr", "create", "--title", goal, "--body", body, "--head", branch],
        cwd=str(root),
    )
    pr_url = p_pr.stdout.strip()

    _run_orca([
        "worktree", "set",
        "--worktree", "branch:{0}".format(branch),
        "--workspace-status", "in-review",
    ])

    return pr_url


# --- abandon gate -----------------------------------------------------------

def issue_state(issue_num: str) -> str:
    """Return the GitHub issue state (e.g. ``'OPEN'`` or ``'CLOSED'``)."""
    p = _run_gh(["issue", "view", issue_num, "--json", "state", "-q", ".state"])
    return p.stdout.strip().upper()


def abandon_gate(arc_dir: Path) -> None:
    """Raise :class:`AbandonGateError` if the arc has an open GitHub issue.

    No-op when the arc has no ``orca-issue`` field (headless arcs are unaffected).
    """
    num = fields.read_field(passport_path(arc_dir), ISSUE_FIELD)
    if not num:
        return
    try:
        state = issue_state(num)
    except Exception:  # noqa: BLE001  gh not available / network error
        return
    if state != "CLOSED":
        raise AbandonGateError(
            "tide arc close blocked: GitHub issue #{n} is still {s}.\n"
            "The arc cannot be sealed while its issue is open — "
            "merge the PR first (GitHub auto-closes the issue via 'Closes #{n}').\n"
            "To check progress: gh issue view {n}".format(n=num, s=state)
        )

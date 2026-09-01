"""tide.adopt — one-command project onboarding (``tide adopt [path]``).

A single, idempotent verb that takes any directory and makes it a fully working
tide project. It composes the pieces that already exist — git, the per-project
``.tide/`` scaffold, Orca repo registration, the control-home roster — into one
runnable command so a human (or the orchestrator) never has to remember the
four-step recipe:

1. Resolve an absolute path; the project *name* defaults to the dir's basename.
2. ``git init`` when the dir is not already a repo (tolerates a missing ``git``).
3. Scaffold ``.tide/`` when absent (reuses :func:`tide.init_home.scaffold_project`),
   seeding the canon's intent from ``--goal`` when the human gave one.
4. Project that canon into a ``README.md`` — only when a goal was seeded, so the
   user door opens on something real instead of a generated stub.
5. Register with Orca (``orca repo add --path <abs> --json``) when the CLI is on
   PATH — an already-registered path is success, not a failure.
6. Add to the control-home roster (skipped gracefully when none resolves).

Birth without ``--goal`` is unchanged: a four-heading canon skeleton and no
README, which the SessionStart newborn guard is deliberately silent about
(``tide.hooks.session_start._is_newborn``). Nothing is ever demanded of the human.

Every step is non-destructive + re-runnable: a second ``tide adopt`` on the same
dir is a no-op-ish success. Logic is plain functions returning an
:class:`AdoptReport` (argparse-free, unit-testable); :func:`register` wires the
thin CLI handler that ``cli.py`` calls.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import (init_home, layer as _layer, paths, quickstart,
               readme as _readme, roster, terminal_choice)

# Per-step outcome markers (rendered in the summary).
DONE = "done"
SKIPPED = "skipped"
WARN = "warn"

_MARK = {DONE: "✓", SKIPPED: "·", WARN: "!"}


@dataclass
class AdoptStep:
    """One step of an adopt run: its name, outcome, and a human-readable note."""

    name: str
    status: str
    detail: str = ""


@dataclass
class AdoptReport:
    """The full result of :func:`adopt` — the resolved project + per-step outcomes."""

    path: Path
    name: str
    steps: List[AdoptStep] = field(default_factory=list)

    def step(self, name: str) -> Optional[AdoptStep]:
        """Return the recorded step named *name*, or None when it did not run."""
        for s in self.steps:
            if s.name == name:
                return s
        return None


# --- individual steps (each returns an AdoptStep) --------------------------

def _git_step(path: Path, do_git: bool) -> AdoptStep:
    """``git init`` *path* unless it is already a repo / opted out / git missing."""
    if not do_git:
        return AdoptStep("git", SKIPPED, "skipped (--no-git)")
    if (path / ".git").exists():
        return AdoptStep("git", SKIPPED, "already a git repo")
    try:
        subprocess.run(
            ["git", "init", "--quiet", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return AdoptStep("git", WARN, "git not found — left un-versioned")
    except (OSError, subprocess.CalledProcessError) as exc:
        return AdoptStep("git", WARN, "git init failed ({0})".format(exc))
    return AdoptStep("git", DONE, "git init")


def _first_commit_step(path: Path, do_git: bool, repo_is_new: bool) -> AdoptStep:
    """Give a repo tide just created an EMPTY first commit — and commit nowhere else.

    Two rules meet here. ``git worktree add`` — the spawn path under ``tide
    menu`` — refuses a repo without HEAD, so a project with no commit shows up in
    the picker and dies with a raw trace at pickup (cand 32). And a repository
    belongs to the person who owns it: tide writes nothing into the history of a
    repo it did not create (decision 15, работа 60).

    So the commit happens only when *repo_is_new* — this very run ran ``git
    init`` — and even then it carries **no files** (``--allow-empty``): HEAD
    exists, the index is untouched, and every file in the dir is still the
    person's to commit as they see fit. A repo that already existed is left
    exactly as found, HEAD or no HEAD; when it has no HEAD the skip note says
    what to run, instead of running it for them.
    """
    if not do_git:
        return AdoptStep("commit", SKIPPED, "skipped (--no-git)")
    if not (path / ".git").exists():
        return AdoptStep("commit", SKIPPED, "not a git repo")
    try:
        probe = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--verify", "--quiet", "HEAD"],
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return AdoptStep("commit", SKIPPED, "your repo has history — tide commits nothing in it")
        if not repo_is_new:
            return AdoptStep(
                "commit", SKIPPED,
                "your repo, no commit yet — tide makes none; threads need one "
                "(git commit --allow-empty -m init)")
        subprocess.run(
            ["git", "-C", str(path), "commit", "--quiet", "--allow-empty",
             "-m", "chore: tide adopt — empty first commit so threads can spawn"],
            check=True, capture_output=True, text=True,
        )
    except FileNotFoundError:
        return AdoptStep("commit", WARN, "git not found — left without a commit")
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        return AdoptStep(
            "commit", WARN,
            "first commit failed ({0}) — make one by hand; worktree spawn needs it".format(
                " ".join(detail.split())[:120]
            ),
        )
    return AdoptStep("commit", DONE, "empty first commit (worktree-ready; none of your files in it)")


def _scaffold_step(path: Path, name: str, intent: str = "") -> AdoptStep:
    """Lay down ``.tide/`` when absent (idempotent via scaffold_project)."""
    existed = paths.tide_dir(path).is_dir()
    init_home.scaffold_project(path, name=name, intent=intent)
    if existed:
        return AdoptStep("tide", SKIPPED, ".tide/ already present")
    if intent.strip():
        return AdoptStep("tide", DONE, "scaffolded .tide/ (canon seeded with the goal)")
    return AdoptStep("tide", DONE, "scaffolded .tide/")


def _readme_step(path: Path, name: str, intent: str, canon_was_born: bool) -> AdoptStep:
    """Project the freshly-seeded canon into README.md — the project's user door.

    Runs only when this very run seeded the canon from ``--goal``. Without a goal
    the canon is a blank skeleton and a README off it would be the stub this step
    exists to prevent; over a canon that already existed, the goal was not applied
    (scaffold is non-destructive), so writing a README would be a surprise edit of
    someone else's project.
    """
    if not intent.strip():
        return AdoptStep("readme", SKIPPED, "no --goal — canon has nothing to project yet")
    if not canon_was_born:
        return AdoptStep("readme", SKIPPED, "canon already written — --goal not applied")
    try:
        _text, status = _readme.generate(path, fallback_name=name)
    except (OSError, FileNotFoundError, UnicodeDecodeError) as exc:
        return AdoptStep("readme", WARN, "README not generated ({0})".format(exc))
    return AdoptStep("readme", DONE, "README.md {0} from canon".format(status))


def _layer_step(path: Path, shared: bool) -> AdoptStep:
    """Keep ``.tide/`` out of the project's history — the default (работа 60).

    The exclusion goes into ``.git/info/exclude``: same syntax as ``.gitignore``,
    same effect, but git does not track that file, so the line stays on this
    machine. ``.gitignore`` is deliberately never touched — it is committed and
    reviewed, and one person's "ignore my tool" would ride into a colleague's
    pull request.
    """
    if shared:
        _layer.set_mode(path, _layer.SHARED)
        return AdoptStep("layer", SKIPPED,
                         "--shared — .tide/ is committed with this project")
    verdict = _layer.ensure_local(path)
    if verdict == "excluded":
        return AdoptStep("layer", DONE,
                         ".tide/ stays on this machine (.git/info/exclude, not .gitignore)")
    if verdict == "already":
        return AdoptStep("layer", SKIPPED, ".git/info/exclude already keeps .tide/ local")
    if verdict == "shared":
        return AdoptStep("layer", SKIPPED, "this project shares its .tide/ (tide layer local)")
    if verdict == "tracked":
        return AdoptStep("layer", WARN,
                         ".tide/ is already tracked in this repo — 'tide layer untrack' takes it out")
    return AdoptStep("layer", SKIPPED, "no git repo — nothing to keep out of")


def _orca_step(abs_path: str, do_orca: bool) -> AdoptStep:
    """Register *abs_path* with Orca; an already-known path counts as success.

    Darwin-gated exactly like :func:`tide.adapters.default_adapter_name`: on
    Linux an ``orca`` on PATH is the GNOME screen-reader, and calling it with
    ``repo add`` would launch a screen-reader with junk arguments (finding 5,
    release panel 2026-08). Orca the terminal manager is a Mac app.
    """
    if not do_orca:
        return AdoptStep("orca", SKIPPED, "skipped (--no-orca)")
    if sys.platform != "darwin":
        return AdoptStep(
            "orca", SKIPPED,
            "non-macOS — Orca (optional Mac terminal manager) not called")
    if shutil.which("orca") is None:
        return AdoptStep(
            "orca", SKIPPED,
            "orca CLI not on PATH — optional terminal manager, tide works without it")
    try:
        subprocess.run(
            ["orca", "repo", "add", "--path", abs_path, "--json"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        # `orca repo add` is idempotent-ish: a path that already exists exits
        # nonzero but is, for our purpose, registered. Treat that as success.
        return AdoptStep("orca", DONE, "already registered with Orca")
    except OSError as exc:
        return AdoptStep("orca", WARN, "orca repo add failed ({0})".format(exc))
    return AdoptStep("orca", DONE, "registered with Orca")


def _roster_step(name: str, abs_path: str) -> AdoptStep:
    """Add *name*→*abs_path* to the control-home roster (skip when none resolves)."""
    try:
        home = paths.control_home()
    except FileNotFoundError:
        return AdoptStep(
            "roster",
            SKIPPED,
            "no control-home — set $TIDE_HOME or run 'tide init' somewhere",
        )
    roster.add(home, name, abs_path)
    return AdoptStep("roster", DONE, "rostered → {0}".format(home))


# --- orchestration ---------------------------------------------------------

def adopt(
    path: Path,
    *,
    name: Optional[str] = None,
    do_git: bool = True,
    do_orca: bool = True,
    intent: str = "",
    shared: bool = False,
) -> AdoptReport:
    """Make *path* a working tide project, idempotently; return an :class:`AdoptReport`.

    *name* overrides the project name (default: the dir's basename). *do_git* /
    *do_orca* opt out of the git-init / Orca-registration steps. *intent* is the
    human's one-line goal (``--goal``): it seeds the canon's "What it is" and the
    README projected from it, so the project is born already saying what it is.
    *shared* opts this project into a committed ``.tide/`` (a team running one
    thread together); the default keeps the layer on this machine.
    Re-running on an already-adopted dir is a no-op-ish success (every step
    reports ``skipped``).
    """
    abs_path = Path(path).expanduser().resolve()
    proj_name = (name or "").strip() or abs_path.name
    abs_str = str(abs_path)

    report = AdoptReport(path=abs_path, name=proj_name)
    git_step = _git_step(abs_path, do_git)
    report.steps.append(git_step)
    # Whether the canon is born in THIS run decides if --goal reached it — read
    # before the scaffold writes, since scaffold_project preserves an existing one.
    canon_was_born = not paths.canon_file(abs_path).exists()
    report.steps.append(_scaffold_step(abs_path, proj_name, intent))
    report.steps.append(_layer_step(abs_path, shared))
    report.steps.append(_readme_step(abs_path, proj_name, intent, canon_was_born))
    # Only a repo THIS run created gets a commit, and an empty one — see
    # _first_commit_step.
    report.steps.append(_first_commit_step(abs_path, do_git, git_step.status == DONE))
    report.steps.append(_orca_step(abs_str, do_orca))
    report.steps.append(_roster_step(proj_name, abs_str))
    return report


def render_report(report: AdoptReport) -> str:
    """A step-by-step summary (✓/·/! per step) ending in the ready line."""
    lines = ["tide: adopted {0} at {1}".format(report.name, report.path)]
    for s in report.steps:
        mark = _MARK.get(s.status, "?")
        lines.append("  {0} {1:<7} {2}".format(mark, s.name, s.detail))
    lines.append("ready: tide menu → {0}".format(report.name))
    return "\n".join(lines)


# --- CLI wiring ------------------------------------------------------------

def _cmd_adopt(args) -> int:
    report = adopt(
        Path(getattr(args, "path", None) or "."),
        name=getattr(args, "name", None),
        do_git=not getattr(args, "no_git", False),
        do_orca=not getattr(args, "no_orca", False),
        intent=getattr(args, "intent", None) or "",
        shared=getattr(args, "shared", False),
    )
    print(render_report(report))
    # The terminal was chosen silently and met, if at all, as a grey `orca` line
    # in the steps above. Say it plainly (работа 59 п.5) — and end on the route,
    # because this is where a person asks "and now what?".
    print()
    for line in terminal_choice.announce_lines(Path(getattr(args, "path", None) or ".")):
        print(line)
    print()
    print("next: open the board and watch it:")
    print("      tide board --open")
    print(quickstart.next_step_line("adopt"))
    return 0


def register(subparsers) -> None:
    """Add the top-level ``adopt`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "adopt", help="make a directory a working tide project (git + .tide + orca + roster)"
    )
    p.add_argument("path", nargs="?", default=".", help="directory to adopt (default: cwd)")
    p.add_argument("--name", help="project name (default: dir basename)")
    p.add_argument(
        "--goal",
        dest="intent",
        metavar="TEXT",
        help=(
            "one line saying what this project is — seeds the canon at birth and "
            "the README projected from it (omit and the canon is born blank)"
        ),
    )
    p.add_argument(
        "--shared",
        action="store_true",
        help=(
            "commit .tide/ with the project — for a team running one thread "
            "together (default: the layer stays on this machine)"
        ),
    )
    p.add_argument("--no-git", action="store_true", help="do not 'git init' the directory")
    p.add_argument("--no-orca", action="store_true", help="do not register the repo with Orca")
    p.set_defaults(func=_cmd_adopt, _cmd="adopt")

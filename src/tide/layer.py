"""tide.layer — where the operator layer lives: on this machine, or in the repo.

The rule (thread release, decision 15): the tide layer sits ON TOP of a working
repository, not inside it. ``.tide/`` is how *you* run the work — arcs, pulses,
handoffs, the board's state. It is not the project. By default it must never
arrive in a colleague's pull request.

Two ways to say "do not track this", and only one of them is honest here:

* ``.gitignore`` is the project's file. It is committed, reviewed, and shipped
  to everyone who clones. A line there saying "ignore my tool" is one person's
  opinion travelling to a whole team.
* ``.git/info/exclude`` is the same syntax, same effect — and it never leaves
  this machine, because git itself does not track it. That is where the
  exclusion belongs, and this module writes nothing anywhere else.

The exception is explicit (:data:`SHARED`): a team that runs one thread together
wants ``.tide/`` committed. Then the mode is recorded in ``.tide/layer``, inside
the layer itself, so it travels with the very files it is talking about.

The control-home is shared by default and always has been: there ``.tide/`` *is*
the content — the arcs, the candidates, the decisions. Excluding it there would
mean the home's own history stopped recording the home.

Everything is a plain function over a project root; :func:`register` wires the
``tide layer`` group (status / local / shared / untrack).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import io as _io, paths

# The two modes.
LOCAL = "local"     # .tide/ stays on this machine (default for a project)
SHARED = "shared"   # .tide/ is committed with the project (explicit opt-in)

# Where the opt-in is recorded — inside the layer, so it travels with it.
MODE_FILE = "layer"

# What goes into .git/info/exclude, with the line that says who wrote it and why.
EXCLUDE_PATTERN = "/{0}/".format(paths.TIDE_DIR)
EXCLUDE_NOTE = (
    "# tide: the operator layer lives on this machine, not in this repo's "
    "history (tide layer shared — to commit it with the project)"
)


# --- the mode ---------------------------------------------------------------

def mode_file(root: Path) -> Path:
    """The ``.tide/layer`` file for *root* (may not exist — absence means default)."""
    return paths.tide_dir(Path(root)) / MODE_FILE


def default_mode(root: Path) -> str:
    """The mode when nothing was written: shared in a control-home, local elsewhere."""
    return SHARED if paths.is_control_home(Path(root)) else LOCAL


def mode(root: Path) -> str:
    """The layer mode of *root* — :data:`LOCAL` or :data:`SHARED`.

    An unreadable or garbled file reads as the default rather than as an error:
    nothing about where files live is worth killing a command over.
    """
    try:
        text = mode_file(root).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return default_mode(root)
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "mode" and value.strip().lower() in (LOCAL, SHARED):
            return value.strip().lower()
    return default_mode(root)


def set_mode(root: Path, value: str) -> Path:
    """Record *value* in ``.tide/layer``; return the file path.

    The default mode is written down too rather than left implicit — a person
    who switched back to local should be able to see that they did.
    """
    if value not in (LOCAL, SHARED):
        raise ValueError("unknown layer mode {0!r} — use {1} or {2}".format(
            value, LOCAL, SHARED))
    path = mode_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _io.atomic_write(path, "mode={0}\n".format(value))
    return path


# --- git plumbing (all read-only except the exclude file) -------------------

def _git(root: Path, *argv: str) -> Optional[subprocess.CompletedProcess]:
    """Run ``git -C root <argv>``; None when git is missing or unusable."""
    try:
        return subprocess.run(
            ["git", "-C", str(root), *argv], capture_output=True, text=True)
    except (OSError, ValueError):
        return None


def is_git_repo(root: Path) -> bool:
    """True when *root* carries a ``.git`` (dir in a clone, file in a worktree)."""
    return (Path(root) / ".git").exists()


def git_common_dir(root: Path) -> Optional[Path]:
    """The repo's COMMON git dir — the one whose ``info/exclude`` applies repo-wide.

    Asked of git rather than guessed, because inside a linked worktree ``.git``
    is a file pointing elsewhere and the per-worktree git dir has an
    ``info/exclude`` that only that worktree reads.
    """
    root = Path(root)
    probe = _git(root, "rev-parse", "--git-common-dir")
    if probe is not None and probe.returncode == 0 and probe.stdout.strip():
        found = Path(probe.stdout.strip())
        if not found.is_absolute():
            found = root / found
        if found.is_dir():
            return found.resolve()
    # No git binary (or an old one): the plain layout is still right in a clone.
    plain = root / ".git"
    return plain.resolve() if plain.is_dir() else None


def exclude_file(root: Path) -> Optional[Path]:
    """The repo-local ``info/exclude`` path for *root*; None when there is no repo."""
    common = git_common_dir(root)
    return None if common is None else common / "info" / "exclude"


def _exclude_lines(path: Path) -> List[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def is_excluded(root: Path) -> bool:
    """True when this machine's exclude file already carries the ``.tide/`` pattern."""
    path = exclude_file(root)
    if path is None or not path.is_file():
        return False
    wanted = {EXCLUDE_PATTERN, paths.TIDE_DIR + "/", paths.TIDE_DIR}
    return any(line.strip() in wanted for line in _exclude_lines(path))


def exclude(root: Path) -> str:
    """Add the ``.tide/`` pattern to ``.git/info/exclude``; return what happened.

    Returns ``"added"``, ``"already"``, or ``"no-git"``. Appends — the file may
    hold the person's own patterns, and this one is a guest in it.
    """
    path = exclude_file(root)
    if path is None:
        return "no-git"
    if is_excluded(root):
        return "already"
    lines = _exclude_lines(path)
    body = list(lines)
    if body and body[-1].strip():
        body.append("")
    body.extend([EXCLUDE_NOTE, EXCLUDE_PATTERN])
    path.parent.mkdir(parents=True, exist_ok=True)
    _io.atomic_write(path, "\n".join(body) + "\n")
    return "added"


def unexclude(root: Path) -> str:
    """Drop tide's lines from ``.git/info/exclude``; return ``removed``/``absent``/``no-git``."""
    path = exclude_file(root)
    if path is None:
        return "no-git"
    if not path.is_file() or not is_excluded(root):
        return "absent"
    wanted = {EXCLUDE_PATTERN, paths.TIDE_DIR + "/", paths.TIDE_DIR}
    kept = [ln for ln in _exclude_lines(path)
            if ln.strip() not in wanted and ln.strip() != EXCLUDE_NOTE.strip()]
    while kept and not kept[-1].strip():
        kept.pop()
    _io.atomic_write(path, ("\n".join(kept) + "\n") if kept else "")
    return "removed"


def tracked(root: Path) -> List[str]:
    """Paths under ``.tide/`` that this repo's index currently tracks (may be empty)."""
    probe = _git(root, "ls-files", "--", "{0}/".format(paths.TIDE_DIR))
    if probe is None or probe.returncode != 0:
        return []
    return [ln for ln in probe.stdout.splitlines() if ln.strip()]


# --- the two operations -----------------------------------------------------

@dataclass
class LayerReport:
    """What a layer operation did, as lines a person can read back."""

    mode: str
    lines: List[str] = field(default_factory=list)
    tracked_before: int = 0
    staged_removal: bool = False


def ensure_local(root: Path) -> str:
    """Keep ``.tide/`` out of this project's history — the default at adoption.

    Returns a short outcome word for the caller to render: ``excluded`` (the
    pattern was just written), ``already`` (it was there), ``shared`` (this
    project opted into a committed layer), ``tracked`` (the layer is already in
    the index — an exclude would be a lie, :func:`untrack` is the way out), or
    ``no-git`` (nothing to exclude from).
    """
    root = Path(root)
    if mode(root) == SHARED:
        return "shared"
    if not is_git_repo(root):
        return "no-git"
    if tracked(root):
        return "tracked"
    return "excluded" if exclude(root) == "added" else "already"


def untrack(root: Path) -> LayerReport:
    """Take an ALREADY COMMITTED ``.tide/`` out of the index, files left on disk.

    Two moves, both named out loud before they run: ``git rm -r --cached .tide``
    (drops it from the index, touches no file on disk) and the exclude pattern
    so it does not come back. What this canNOT do is remove the layer from
    commits that already exist — that is a history rewrite, it is not something
    a tool should do behind a person's back, and the report says so plainly.

    The removal is left STAGED, not committed: the whole point of the rule is
    that tide does not commit into someone's repository.
    """
    root = Path(root)
    report = LayerReport(mode=LOCAL)
    if not is_git_repo(root):
        report.lines.append("not a git repo — nothing is tracked, nothing to take out")
        return report

    before = tracked(root)
    report.tracked_before = len(before)
    if before:
        removed = _git(root, "rm", "-r", "--cached", "--quiet", "--",
                       "{0}/".format(paths.TIDE_DIR))
        if removed is None or removed.returncode != 0:
            detail = "" if removed is None else " ".join(removed.stderr.split())[:120]
            report.lines.append(
                "could not drop {0}/ from the index ({1}) — nothing was changed".format(
                    paths.TIDE_DIR, detail or "git unavailable"))
            return report
        report.staged_removal = True
        report.lines.append(
            "removed {0} file(s) under {1}/ from the index — every one of them is "
            "still on disk".format(len(before), paths.TIDE_DIR))
    else:
        report.lines.append(
            "{0}/ was not tracked here — nothing had to be taken out".format(
                paths.TIDE_DIR))

    verdict = exclude(root)
    report.lines.append({
        "added": "wrote {0} into .git/info/exclude — a local file, never committed "
                 "itself".format(EXCLUDE_PATTERN),
        "already": ".git/info/exclude already had {0}".format(EXCLUDE_PATTERN),
        "no-git": "no git dir — the exclude was not written",
    }[verdict])

    if mode(root) == SHARED:
        set_mode(root, LOCAL)
        report.lines.append("layer mode: shared → local")

    if report.staged_removal:
        report.lines.append(
            "the removal is STAGED, not committed — tide does not commit in your "
            "repo; run `git commit` when you want it in")
        report.lines.append(
            "history is untouched: commits that already carry {0}/ still carry it, "
            "and only rewriting them would change that".format(paths.TIDE_DIR))
    report.lines.append("your .gitignore was not touched")
    return report


# --- rendering --------------------------------------------------------------

def status_lines(root: Path) -> List[str]:
    """Where this project's layer lives right now, in a person's words."""
    root = Path(root)
    current = mode(root)
    out = []
    if current == SHARED:
        out.append("layer: shared — {0}/ is committed with this project".format(
            paths.TIDE_DIR))
    else:
        out.append("layer: local — {0}/ stays on this machine".format(paths.TIDE_DIR))
    if not is_git_repo(root):
        out.append("  no git repo here, so nothing is tracked either way")
        return out
    count = len(tracked(root))
    if count:
        out.append("  git: {0} file(s) under {1}/ are tracked in this repo".format(
            count, paths.TIDE_DIR))
        if current == LOCAL:
            out.append("  take them out: tide layer untrack")
    else:
        out.append("  git: nothing under {0}/ is tracked".format(paths.TIDE_DIR))
    out.append("  .git/info/exclude: {0}".format(
        "excludes {0}".format(EXCLUDE_PATTERN) if is_excluded(root)
        else "no tide pattern"))
    return out


# --- CLI --------------------------------------------------------------------

def _root(args) -> Path:
    """The project this command is about: an explicit path, else the tide root, else cwd."""
    given = getattr(args, "path", None)
    if given:
        return Path(given).expanduser().resolve()
    return paths.find_tide_root() or Path.cwd()


def _cmd_status(args) -> int:
    for line in status_lines(_root(args)):
        print(line)
    return 0


def _cmd_local(args) -> int:
    root = _root(args)
    set_mode(root, LOCAL)
    verdict = ensure_local(root)
    print("tide layer: local — {0}/ stays on this machine".format(paths.TIDE_DIR))
    if verdict == "excluded":
        print("  wrote {0} into .git/info/exclude (local file, never committed)".format(
            EXCLUDE_PATTERN))
    elif verdict == "already":
        print("  .git/info/exclude already had {0}".format(EXCLUDE_PATTERN))
    elif verdict == "tracked":
        print("  but {0}/ is already tracked here — tide layer untrack takes it out".format(
            paths.TIDE_DIR))
    elif verdict == "no-git":
        print("  no git repo here — nothing to exclude from")
    print("  your .gitignore was not touched")
    return 0


def _cmd_shared(args) -> int:
    root = _root(args)
    set_mode(root, SHARED)
    verdict = unexclude(root)
    print("tide layer: shared — {0}/ is committed with this project".format(
        paths.TIDE_DIR))
    if verdict == "removed":
        print("  dropped the tide pattern from .git/info/exclude")
    print("  {0}/ now shows up in `git status`; commit it when you want to".format(
        paths.TIDE_DIR))
    return 0


def _cmd_untrack(args) -> int:
    root = _root(args)
    print("tide layer untrack — {0} — this will:".format(root))
    print("  1. git rm -r --cached {0}/   (index only; files stay on disk)".format(
        paths.TIDE_DIR))
    print("  2. add {0} to .git/info/exclude   (local file, never committed)".format(
        EXCLUDE_PATTERN))
    print("  it does NOT rewrite history and does NOT touch your .gitignore.")
    print()
    if getattr(args, "dry_run", False):
        count = len(tracked(root)) if is_git_repo(root) else 0
        print("--dry-run: nothing was changed ({0} tracked file(s) would be "
              "removed from the index)".format(count))
        return 0
    for line in untrack(root).lines:
        print("  " + line)
    return 0


def register(subparsers) -> None:
    """Add the ``layer`` group to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "layer",
        help="where the tide layer lives: on this machine (default) or in the repo",
    )
    p.add_argument("--path", metavar="DIR",
                   help="the project to act on (default: the current one)")
    p.set_defaults(func=_cmd_status, _cmd="layer")
    sub = p.add_subparsers(dest="layer_cmd", metavar="<subcommand>")

    s = sub.add_parser("status", help="say where the layer lives right now")
    s.add_argument("--path", metavar="DIR", help="the project to act on")
    s.set_defaults(func=_cmd_status, _cmd="layer status")

    s = sub.add_parser(
        "local", help="default: keep .tide/ out of this project's history")
    s.add_argument("--path", metavar="DIR", help="the project to act on")
    s.set_defaults(func=_cmd_local, _cmd="layer local")

    s = sub.add_parser(
        "shared", help="commit .tide/ with the project (a team running one thread)")
    s.add_argument("--path", metavar="DIR", help="the project to act on")
    s.set_defaults(func=_cmd_shared, _cmd="layer shared")

    s = sub.add_parser(
        "untrack",
        help="take an already committed .tide/ out of the index (files stay on disk)")
    s.add_argument("--path", metavar="DIR", help="the project to act on")
    s.add_argument("--dry-run", action="store_true",
                   help="say what would happen, change nothing")
    s.set_defaults(func=_cmd_untrack, _cmd="layer untrack")

"""tide.init_home — unfold a control-home (and scaffold a per-project ``.tide/``).

``tide init`` is the one human command that *creates* state. Two shapes share one
implementation (build-blueprint ``tide_dir_format``):

* **control-home** (default) — the dir where the human leads ALL projects. Gets the
  per-project ``.tide/{cannon,arcs,state}`` skeleton (tide **dogfoods itself**, so
  the control-home is also a tide project) PLUS a top-level ``roster.md`` registry,
  a short ``README.md`` orientation, and an optional ``git init``.
* **plain project** (``--project``) — just the per-project ``.tide/`` skeleton, no
  roster/README (a dispatched project that the orchestrator will lead from afar).

Everything is **non-destructive + re-runnable**: an existing CANON.md / config /
roster.md / README.md is preserved unless ``force`` is set, so re-running ``tide
init`` in a live home never clobbers real content. Logic is plain functions
(argparse-free, unit-testable); :func:`register` wires the thin handler ``cli.py``
calls.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional

from . import io as _io, paths, roster
from .arc.stream import StreamError
from .cannon import store
from .strictness import DEFAULT as DEFAULT_STRICTNESS

README_TEMPLATE = """# {name} — tide control-home

This dir is a **tide control-home**: where you lead every project from one place.

## Layout
- `roster.md` — the project registry (`name | path` per line); edit via `tide roster`.
- `.tide/` — this home's own work stream (tide dogfoods itself as a tide project).
  - `cannon/CANON.md` — durable living-IS truth.
  - `arcs/` — the numbered work stream (`NN-<slug>/`) + `candidates/`.
  - `state/` — the strictness dial + cannon-rev stamps.

## Daily use
- `tide roster add <name> <path>` — register a project.
- `tide status [--all]` — render the work-stream board (`--all` = every rostered project).
- `tide strictness [strict|loose]` — the dispatch dial.
- `tide help` — full command list.
"""


class InitError(StreamError):
    """A control-home / scaffold init error.

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on
    the same ``except`` arm (prints ``tide: …``, exits nonzero).
    """


# --- per-project scaffold --------------------------------------------------

def scaffold_project(
    root: Path,
    name: Optional[str] = None,
    lang: str = store.DEFAULT_LANG,
    force: bool = False,
) -> List[str]:
    """Lay down the per-project ``.tide/{cannon,arcs/candidates,state}`` skeleton.

    Seeds ``cannon/`` (CANON.md + config via :func:`tide.cannon.store.init`),
    creates the ``arcs/candidates/`` backlog dir and ``state/``, and writes the
    default ``strict`` dial. Non-destructive: existing files survive unless
    *force*. Returns a list of human-readable "created …" notes (idempotent ⇒ may
    be empty on a re-run).
    """
    root = Path(root)
    name = name if name else root.resolve().name
    created: List[str] = []

    tide_existed = paths.tide_dir(root).is_dir()

    # cannon/ — CANON.md + config (store.init is itself non-destructive).
    canon_existed = paths.canon_file(root).exists()
    store.init(root, name=name, lang=lang, force=force)
    if force or not canon_existed:
        created.append("cannon/CANON.md")

    # arcs/ + candidates/ backlog.
    paths.candidates_dir(root).mkdir(parents=True, exist_ok=True)

    # state/ + the default strictness dial (safe default; never downgrades).
    sf = paths.strictness_file(root)
    sf.parent.mkdir(parents=True, exist_ok=True)
    if force or not sf.exists():
        _io.atomic_write(sf, "{0}\n".format(DEFAULT_STRICTNESS))
        created.append("state/strictness")

    if not tide_existed:
        created.append(".tide/")
    return created


# --- control-home unfold ---------------------------------------------------

def unfold_control_home(
    root: Path,
    name: Optional[str] = None,
    lang: str = store.DEFAULT_LANG,
    git: bool = False,
    force: bool = False,
) -> List[str]:
    """Unfold a full control-home at *root* (dogfood ``.tide/`` + roster + README).

    Runs :func:`scaffold_project` (the home is itself a tide project), then adds the
    ``roster.md`` registry, a ``README.md`` orientation, and an optional
    ``git init``. Non-destructive + re-runnable. Returns the "created …" notes.
    """
    root = Path(root)
    name = name if name else root.resolve().name
    created = scaffold_project(root, name=name, lang=lang, force=force)

    # roster.md — the control-home registry (header-only when fresh).
    rf = paths.roster_file(root)
    if force or not rf.is_file():
        _io.atomic_write(rf, roster.HEADER + "\n")
        created.append("roster.md")

    # README.md — orientation for a human opening the dir.
    readme = root / "README.md"
    if force or not readme.exists():
        _io.atomic_write(readme, README_TEMPLATE.format(name=name))
        created.append("README.md")

    if git:
        if _git_init(root):
            created.append("git repo")

    return created


def _git_init(root: Path) -> bool:
    """``git init`` *root* when it is not already a repo; return True if created.

    A best-effort convenience: a missing/failing ``git`` is swallowed (the
    control-home is fully usable without version control), so init never hard-fails
    on a machine without git.
    """
    root = Path(root)
    if (root / ".git").exists():
        return False
    try:
        subprocess.run(
            ["git", "init", "--quiet", str(root)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


# --- CLI wiring ------------------------------------------------------------

def _cmd_init(args) -> int:
    root = Path.cwd()
    if getattr(args, "project", False):
        created = scaffold_project(root, name=args.name, force=args.force)
        what = "tide project scaffold"
    else:
        created = unfold_control_home(
            root, name=args.name, git=args.git, force=args.force
        )
        what = "tide control-home"

    print("tide: {0} ready at {1}".format(what, root))
    if created:
        for note in created:
            print("  + {0}".format(note))
    else:
        print("  (already unfolded — nothing to create)")
    return 0


def register(subparsers) -> None:
    """Add the top-level ``init`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "init", help="unfold a tide control-home (roster + dogfood .tide/)"
    )
    p.add_argument("--name", help="project name in CANON.md / README (default: dir name)")
    p.add_argument(
        "--project",
        action="store_true",
        help="scaffold only a per-project .tide/ (no roster/README)",
    )
    p.add_argument("--git", action="store_true", help="also 'git init' the control-home")
    p.add_argument("--force", action="store_true", help="overwrite existing CANON/roster/README")
    p.set_defaults(func=_cmd_init, _cmd="init")

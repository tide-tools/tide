"""tide.init_home — unfold a control-home (and scaffold a per-project ``.tide/``).

``tide init`` is the one human command that *creates* state. Two shapes share one
implementation (build-blueprint ``tide_dir_format``):

* **control-home** (default) — the dir where the human leads ALL projects. Gets the
  per-project ``.tide/{canon,arcs,state}`` skeleton (tide **dogfoods itself**, so
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

from . import io as _io, paths, plugins, roster
from .arc.stream import StreamError
from .canon import store
from .strictness import DEFAULT as DEFAULT_STRICTNESS

README_TEMPLATE = """# {name} — tide control-home

This dir is a **tide control-home**: where you lead every project from one place.

## Layout
- `roster.md` — the project registry (`name | path` per line); edit via `tide roster`.
- `.tide/` — this home's own work stream (tide dogfoods itself as a tide project).
  - `canon/CANON.md` — durable living-IS truth.
  - `arcs/` — the numbered work stream (`NN-<slug>/`) + `candidates/`.
  - `state/` — the strictness dial + canon-rev stamps.

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
    intent: str = "",
) -> List[str]:
    """Lay down the per-project ``.tide/{canon,arcs/candidates,state}`` skeleton.

    Seeds ``canon/`` (CANON.md + config via :func:`tide.canon.store.init`),
    creates the ``arcs/candidates/`` backlog dir and ``state/``, and writes the
    default ``strict`` dial. Non-destructive: existing files survive unless
    *force*. Returns a list of human-readable "created …" notes (idempotent ⇒ may
    be empty on a re-run).

    *intent* is the birth seed for the canon's "What it is" section (``tide adopt
    --goal``): passed straight through to :func:`tide.canon.store.init`, so it
    lands only in a canon actually being written.
    """
    root = Path(root)
    name = name if name else root.resolve().name
    created: List[str] = []

    tide_existed = paths.tide_dir(root).is_dir()

    # canon/ — CANON.md + config (store.init is itself non-destructive).
    canon_existed = paths.canon_file(root).exists()
    store.init(root, name=name, lang=lang, force=force, intent=intent)
    if force or not canon_existed:
        created.append("canon/CANON.md")

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

    # .tide/plugins — the core/plugin switchboard (работа 48). Seeded CORE-ONLY,
    # and ONLY for a home that is genuinely fresh (its roster.md did not exist a
    # moment ago). An existing home must never gain a file full of ``off`` on a
    # re-run of `tide init`: that would silently take working surfaces away from
    # someone whose board was fine. No file at all reads as "everything on".
    if "roster.md" in created and not plugins.registry_file(root).is_file():
        plugins.seed_new_install(root)
        created.append(".tide/plugins (core only)")

    # README.md — orientation for a human opening the dir.
    readme = root / "README.md"
    if force or not readme.exists():
        _io.atomic_write(readme, README_TEMPLATE.format(name=name))
        created.append("README.md")

    if git:
        if _git_init(root):
            created.append("git repo (birth commit)")

    return created


def _git_init(root: Path) -> bool:
    """Make *root* a git repo WITH a first commit; return True when anything was done.

    ``git init`` alone leaves a mine: the repo exists but ``git worktree add``
    (the thread-spawn path) refuses a HEAD-less repo — the project sits in the
    picker and dies at pickup (mitehq, 2026-07-05). So init here means
    worktree-ready: init when missing, then a birth commit when HEAD is absent.
    Best-effort as before: a missing/failing ``git`` is swallowed.
    """
    root = Path(root)
    did = False
    try:
        if not (root / ".git").exists():
            subprocess.run(
                ["git", "init", "--quiet", str(root)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            did = True
        if not is_worktree_ready(root):
            subprocess.run(
                ["git", "-C", str(root), "add", "-A"],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "commit", "--quiet",
                 "-m", "chore: tide init — birth commit"],
                check=True, capture_output=True,
            )
            did = True
    except (OSError, subprocess.CalledProcessError):
        return did
    return did


def is_worktree_ready(root: Path) -> bool:
    """True when *root* is a git repo with HEAD — i.e. thread spawn can worktree it."""
    try:
        probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "HEAD"],
            capture_output=True,
        )
    except OSError:
        return False
    return probe.returncode == 0


# --- CLI wiring ------------------------------------------------------------

def _wire_harness(root: Path) -> List[str]:
    """Wire the Claude hooks + deliver the skills for a freshly-init'd home.

    Work 49: "хуки встают сами при установке". The wiring itself has been
    merge-safe and idempotent since U10, and the launcher ensures it on every
    spawn — but a person who ran ``tide init`` and then opened ``claude`` by hand
    got a session with no start-gate, no handoff flip, no ``ended:`` bookend, and
    no idea that a json needed editing. So init does the one gesture for them.

    Both halves are best-effort: a project that cannot take hooks, or a machine
    with no ``~/.claude``, still gets its ``.tide/`` — the init must not die on
    the harness. Returns human-readable notes for the init printout.
    """
    notes: List[str] = []
    try:
        from .harness import install_hooks

        path, wired = install_hooks(root)
        if wired:
            notes.append("Claude hooks → {0} ({1})".format(path, len(wired)))
    except Exception:  # noqa: BLE001 — the harness must not kill an init
        pass
    try:
        from .skills_install import default_target_dir, install_skills

        done = [n for n, verdict in install_skills()
                if verdict in ("linked", "copied", "replaced")]
        if done:
            notes.append("skills → {0}: {1}".format(
                default_target_dir(), ", ".join(done)))
    except ValueError as exc:
        # No source checkout (published-channel install). Say so — a silent skip
        # would leave the person wondering why their agent knows no handoff.
        notes.append("skills NOT installed: {0}".format(exc))
    except Exception:  # noqa: BLE001 — no ~/.claude at all: skip quietly
        pass
    return notes


def _cmd_init(args) -> int:
    root = Path.cwd()
    if getattr(args, "project", False):
        created = scaffold_project(root, name=args.name, force=args.force)
        what = "tide project scaffold"
        if getattr(args, "git", False) and _git_init(root):
            created.append("git repo (birth commit)")
    else:
        created = unfold_control_home(
            root, name=args.name, git=args.git, force=args.force
        )
        what = "tide control-home"

    created.extend(_wire_harness(root))

    print("tide: {0} ready at {1}".format(what, root))
    if created:
        for note in created:
            print("  + {0}".format(note))
    else:
        print("  (already unfolded — nothing to create)")
    if not is_worktree_ready(root):
        print(
            "  ⚠ not a git repo with a commit — thread spawn (worktree) will FAIL "
            "here.\n    Fix now: `tide adopt {0}` or re-run with --git".format(root)
        )
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
    p.add_argument("--git", action="store_true",
                   help="also make it a git repo WITH a birth commit (worktree-ready)")
    p.add_argument("--force", action="store_true", help="overwrite existing CANON/roster/README")
    p.set_defaults(func=_cmd_init, _cmd="init")

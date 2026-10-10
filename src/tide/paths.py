"""tide.paths — resolve the per-project ``.tide/`` home and name the install dirs.

Every module that touches disk routes through here so the on-disk layout
(`build-blueprint.md` ``tide_dir_format``) lives in exactly one place:

    <project>/.tide/
      canon/    CANON.md + config            durable truth
      arcs/     NN-<slug>/ work stream        + candidates/ (separate seq)
      state/    strictness + canon-rev stamps + contract index

The control-home (where ``tide init`` ran) additionally carries a top-level
``roster.md`` and its own dogfood ``.tide/``.

Resolution is ancestor-walking (like git finding ``.git``): from a start dir we
climb until we find one that contains ``.tide/``. That dir is the *project root*;
``.tide`` is its meta dir.

Global install dirs (canonical ``prompts/`` + ``rules/`` shipped with the tool)
are computed relative to this package so a source checkout works without install.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# --- on-disk names (single source of truth) --------------------------------
TIDE_DIR = ".tide"
ROSTER_FILE = "roster.md"

# Env var pointing at the control-home, so `tide` resolves the roster from any
# cwd (not only when run inside the control-home tree). See :func:`control_home`.
TIDE_HOME_ENV = "TIDE_HOME"

CANON_DIRNAME = "canon"          # new canonical spelling
CANNON_DIRNAME = "cannon"        # legacy spelling — for back-compat detection only
ARCS_DIRNAME = "arcs"
STATE_DIRNAME = "state"
CANDIDATES_DIRNAME = "candidates"

CANON_FILE = "CANON.md"
CONFIG_FILE = "config"
STRICTNESS_FILE = "strictness"
CONTEXT_FILE = "context.json"
MCP_FILE = "mcp.json"
DEFERRED_FILE = "deferred.md"


# --- project-root resolution -----------------------------------------------

def find_tide_root(start: Optional[Path] = None) -> Optional[Path]:
    """Return the nearest ancestor of *start* that contains ``.tide/``, or None.

    The search is inclusive of *start* itself and climbs to the filesystem root.
    The returned path is the **project root** (the dir holding ``.tide``), not
    the ``.tide`` dir. *start* defaults to the current working directory.
    """
    here = Path(start).resolve() if start is not None else Path.cwd().resolve()
    mapped = ao_main_path(here)
    if mapped is not None:
        here = mapped
    for candidate in (here, *here.parents):
        if not_project_root(candidate):
            # Дом и всё выше — не проект: ~/.tide держит глобальные файлы
            # (manager.json пульта, state/), и подъём до него делал «проектом»
            # любой репо без своей .tide (edit-gate блокировал «no open arc»).
            return None
        if (candidate / TIDE_DIR).is_dir():
            return candidate
    return None


def _home_dir() -> Optional[Path]:
    try:
        return Path.home().resolve()
    except (RuntimeError, KeyError, OSError):
        return None


def not_project_root(candidate: Path) -> bool:
    """True when *candidate* is ``$HOME`` or one of its ancestors — never a project.

    The home dir may carry a ``~/.tide`` of global files (the TG pult's
    ``manager.json``, ``state/``); treating it as a project root would make every
    repo under ``$HOME`` without its own ``.tide`` look like a tide project.
    """
    home = _home_dir()
    if home is None:
        return False
    cand = Path(candidate)
    return cand == home or cand in home.parents


# --- Agent Orchestrator worktrees ------------------------------------------
# The desktop AO runs every session in a fresh git worktree under
# ``$AO_DATA_DIR/worktrees`` (default ``~/.ao/data/worktrees``). A repo that commits
# its ``.tide/`` hands that worktree a STALE copy of every arc — a session there
# would read and write the wrong ``.tide``. So a path inside an AO worktree is
# mapped to the same relative path inside the repo's MAIN tree before the climb.
# The main tree comes from git itself (the worktree's ``.git`` file → its gitdir →
# ``commondir``); ``$TIDE_PROJECT_ROOT`` (set per AO project by the ao adapter) is
# the fallback when the git pointers can't be read.

AO_DATA_ENV = "AO_DATA_DIR"
PROJECT_ROOT_ENV = "TIDE_PROJECT_ROOT"


def ao_worktrees_root() -> Path:
    base = os.environ.get(AO_DATA_ENV) or str(Path.home() / ".ao" / "data")
    return Path(base).expanduser() / "worktrees"


def _git_main_tree(worktree: Path) -> Optional[Path]:
    """Main tree of a LINKED worktree (``.git`` is a file), else None."""
    try:
        text = (worktree / ".git").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    gitdir = Path(text[len("gitdir:"):].strip())
    if not gitdir.is_absolute():
        gitdir = (worktree / gitdir)
    try:
        rel = (gitdir / "commondir").read_text(encoding="utf-8").strip()
        common = (gitdir / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
    except OSError:
        common = gitdir.parent.parent  # <main>/.git/worktrees/<name> → <main>/.git
    return common.parent if common.name == ".git" else None


def ao_main_path(here: Path) -> Optional[Path]:
    """*here* inside an AO worktree → the same spot in the main tree; else None."""
    try:
        root = ao_worktrees_root().resolve()
        here.relative_to(root)
    except (ValueError, OSError):
        return None
    for cand in (here, *here.parents):
        if cand == root:
            break
        if (cand / ".git").is_file():
            main = _git_main_tree(cand)
            if main is None:
                env = (os.environ.get(PROJECT_ROOT_ENV) or "").strip()
                main = Path(env).expanduser() if env else None
            if main is None or not main.is_dir():
                return None
            return main.resolve() / here.relative_to(cand)
    return None


def require_tide_root(start: Optional[Path] = None) -> Path:
    """Like :func:`find_tide_root` but raise a clear error when none is found."""
    root = find_tide_root(start)
    if root is None:
        where = Path(start).resolve() if start is not None else Path.cwd().resolve()
        raise FileNotFoundError(
            "no .tide/ found in {0} or any parent "
            "(run 'tide init' to unfold a control-home)".format(where)
        )
    return root


def control_home(start: Optional[Path] = None) -> Path:
    """Resolve the control-home, preferring ``$TIDE_HOME`` over the cwd climb.

    This is the entry point for commands that operate on the **roster** (``tide
    menu``, ``tide roster``): they must work from any directory, not only from
    inside the control-home tree. Resolution order:

    1. ``$TIDE_HOME`` — if set, it must point at an existing directory (the
       control-home); a stale/missing path is a clear error, not a silent
       fall-through to the cwd climb.
    2. Otherwise climb from *start* for a REAL control-home — a dir carrying
       ``roster.md`` (:func:`is_control_home`), NOT just any ``.tide/``. A plain
       project (which has its own ``.tide/``) must never be mistaken for the
       control-home, or it would scatter a stray roster there.
    """
    env = os.environ.get(TIDE_HOME_ENV)
    if env and env.strip():
        home = Path(env).expanduser()
        if not home.is_dir():
            raise FileNotFoundError(
                "{0} points at {1}, which is not a directory".format(
                    TIDE_HOME_ENV, home
                )
            )
        return home.resolve()
    here = Path(start).resolve() if start is not None else Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if is_control_home(candidate):
            return candidate
    raise FileNotFoundError(
        "no control-home (a dir with roster.md) found in {0} or any parent — "
        "set $TIDE_HOME to your control-home".format(here)
    )


def tide_dir(root: Path) -> Path:
    """The ``.tide`` meta dir for a project *root*."""
    return Path(root) / TIDE_DIR


# --- per-project subdir helpers (take a project root) ----------------------

def canon_dir(root: Path) -> Path:
    """The ``.tide/canon/`` dir for *root*, with back-compat legacy resolution.

    Resolution order (read-both back-compat):
    1. ``.tide/canon/``  exists → return it (new canonical path).
    2. ``.tide/cannon/`` exists and ``.tide/canon/`` does NOT → return the
       legacy path so existing instances read correctly without migration.
    3. Neither exists → return ``.tide/canon/`` (new instances / fresh init).

    The first WRITE that creates the dir should call :func:`migrate_canon_dir`
    first (atomic rename ``cannon/`` → ``canon/``) so we never end up with two
    dirs and legacy instances are migrated on the next write.
    """
    td = tide_dir(Path(root))
    new_path = td / CANON_DIRNAME
    if new_path.is_dir():
        return new_path
    legacy_path = td / CANNON_DIRNAME
    if legacy_path.is_dir():
        return legacy_path
    return new_path


def migrate_canon_dir(root: Path) -> bool:
    """Atomically rename ``.tide/cannon/`` → ``.tide/canon/`` when needed.

    Called before the first write to the canon dir so legacy instances are
    migrated in place. No-op when ``.tide/canon/`` already exists or no legacy
    dir is present. Returns True when a migration actually happened.
    """
    td = tide_dir(Path(root))
    new_path = td / CANON_DIRNAME
    legacy_path = td / CANNON_DIRNAME
    if new_path.is_dir():
        return False
    if not legacy_path.is_dir():
        return False
    os.rename(str(legacy_path), str(new_path))
    return True


def canon_file(root: Path) -> Path:
    return canon_dir(root) / CANON_FILE


def canon_config(root: Path) -> Path:
    return canon_dir(root) / CONFIG_FILE


def arcs_dir(root: Path) -> Path:
    return tide_dir(root) / ARCS_DIRNAME


def candidates_dir(root: Path) -> Path:
    return arcs_dir(root) / CANDIDATES_DIRNAME


def state_dir(root: Path) -> Path:
    return tide_dir(root) / STATE_DIRNAME


def handoffs_dir(root: Path) -> Path:
    """Path to the two-stage handoff queue (``.tide/handoffs/``).

    A control-home holds the pending-handoff offers here: each is a small markdown
    record an offering session writes (status: offered) and a picking-up session
    confirms (status: taken) — decoupling "chat offered a handoff" from "another
    chat actually took it" (no reliance on a fragile spawn).
    """
    return tide_dir(root) / "handoffs"


def strictness_file(root: Path) -> Path:
    return state_dir(root) / STRICTNESS_FILE


def context_file(root: Path) -> Path:
    """Path to the per-project launch context profile (``state/context.json``)."""
    return state_dir(root) / CONTEXT_FILE


def mcp_file(root: Path) -> Path:
    """Path to the per-project scoped MCP config (``state/mcp.json``).

    Shape ``{"mcpServers": {<name>: <serverdef>}, "_disabled": {…}}`` — the
    launch profile's ``mcp_config`` points here when any server is enabled.
    """
    return state_dir(root) / MCP_FILE


def deferred_file(root: Path) -> Path:
    """Path to the deferred-reconciliation debt ledger (``.tide/deferred.md``).

    Lives at the ``.tide/`` root (not under ``state/``) so it is a human-visible,
    git-trackable record of every arc landed ``loose`` that still owes a ``strict``
    reconciliation — see :mod:`tide.ledger`.
    """
    return tide_dir(root) / DEFERRED_FILE


# --- control-home --------------------------------------------------------

def roster_file(root: Path) -> Path:
    """Path to the control-home roster (only the install dir has a real one)."""
    return Path(root) / ROSTER_FILE


def is_control_home(root: Path) -> bool:
    """True when *root* is a tide control-home (carries ``roster.md``)."""
    return roster_file(root).is_file()


# --- global install dirs (canonical prompts/rules shipped with the tool) ---
# paths.py lives at <repo>/src/tide/paths.py; the shipped prompts/ and rules/
# sit at the repo root. parents[2] climbs src/tide → src → <repo>.

def install_root() -> Path:
    """Repo/install root that ships canonical ``prompts/`` and ``rules/``."""
    return Path(__file__).resolve().parents[2]


def global_prompts_dir() -> Path:
    """Canonical prompts dir (orchestrator.md / worker.md / user-playbook.md)."""
    return install_root() / "prompts"


def global_rules_dir() -> Path:
    """Canonical rules dir (subagents.md / canon-sync.md / contract.md)."""
    return install_root() / "rules"

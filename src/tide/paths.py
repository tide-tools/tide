"""tide.paths — resolve the per-project ``.tide/`` home and name the install dirs.

Every module that touches disk routes through here so the on-disk layout
(`build-blueprint.md` ``tide_dir_format``) lives in exactly one place:

    <project>/.tide/
      cannon/   CANON.md + config            durable truth
      arcs/     NN-<slug>/ work stream        + candidates/ (separate seq)
      state/    strictness + cannon-rev stamps + contract index

The control-home (where ``tide init`` ran) additionally carries a top-level
``roster.md`` and its own dogfood ``.tide/``.

Resolution is ancestor-walking (like git finding ``.git``): from a start dir we
climb until we find one that contains ``.tide/``. That dir is the *project root*;
``.tide`` is its meta dir.

Global install dirs (canonical ``prompts/`` + ``rules/`` shipped with the tool)
are computed relative to this package so a source checkout works without install.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

# --- on-disk names (single source of truth) --------------------------------
TIDE_DIR = ".tide"
ROSTER_FILE = "roster.md"

CANNON_DIRNAME = "cannon"
ARCS_DIRNAME = "arcs"
STATE_DIRNAME = "state"
CANDIDATES_DIRNAME = "candidates"

CANON_FILE = "CANON.md"
CONFIG_FILE = "config"
STRICTNESS_FILE = "strictness"
CONTEXT_FILE = "context.json"
DEFERRED_FILE = "deferred.md"


# --- project-root resolution -----------------------------------------------

def find_tide_root(start: Optional[Path] = None) -> Optional[Path]:
    """Return the nearest ancestor of *start* that contains ``.tide/``, or None.

    The search is inclusive of *start* itself and climbs to the filesystem root.
    The returned path is the **project root** (the dir holding ``.tide``), not
    the ``.tide`` dir. *start* defaults to the current working directory.
    """
    here = Path(start).resolve() if start is not None else Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / TIDE_DIR).is_dir():
            return candidate
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


def tide_dir(root: Path) -> Path:
    """The ``.tide`` meta dir for a project *root*."""
    return Path(root) / TIDE_DIR


# --- per-project subdir helpers (take a project root) ----------------------

def cannon_dir(root: Path) -> Path:
    return tide_dir(root) / CANNON_DIRNAME


def canon_file(root: Path) -> Path:
    return cannon_dir(root) / CANON_FILE


def cannon_config(root: Path) -> Path:
    return cannon_dir(root) / CONFIG_FILE


def arcs_dir(root: Path) -> Path:
    return tide_dir(root) / ARCS_DIRNAME


def candidates_dir(root: Path) -> Path:
    return arcs_dir(root) / CANDIDATES_DIRNAME


def state_dir(root: Path) -> Path:
    return tide_dir(root) / STATE_DIRNAME


def strictness_file(root: Path) -> Path:
    return state_dir(root) / STRICTNESS_FILE


def context_file(root: Path) -> Path:
    """Path to the per-project launch context profile (``state/context.json``)."""
    return state_dir(root) / CONTEXT_FILE


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
    """Canonical rules dir (subagents.md / cannon-sync.md / contract.md)."""
    return install_root() / "rules"

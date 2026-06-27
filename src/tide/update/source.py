"""tide.update.source — pluggable version sources (is there a newer tide?).

The hard question behind self-update is the VERSION SOURCE: "is the installed
``tide`` stale relative to the source-of-truth?" There is NO published channel
yet (that is crit E), so the only source-of-truth today is the LOCAL checkout
the ``tide`` on PATH was installed from — the origin recorded in the install's
``direct_url.json`` (an editable / local ``pip install <path>``). This module
makes that source PLUGGABLE behind a small protocol so crit E can add a
``PublishedChannelSource`` (PyPI / a release feed) without touching the gate or
the CLI.

A :class:`VersionSource` answers two questions and offers one action::

    installed() -> Revision        what `tide` on PATH is running RIGHT NOW
    available() -> Revision        the source-of-truth's latest
    install_command() -> [argv]    how to (re)install from this source

and :func:`is_stale` is the pure comparison over the two revisions.

The INSTALLED side is recorded by an **install marker** (written by
:meth:`LocalSourceCheckout.record_install` on every accepted self-update) — so
"installed" means "the revision we last actually installed", which is the right
notion even for an editable checkout whose working tree has since moved on. When
no marker exists yet (a fresh install.sh run) we fall back to the package
metadata version + (for an editable install) the source HEAD, which is honest
but coarser; the first ``tide self-update`` lays down the marker.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol

from .. import __version__ as _PKG_FALLBACK_VERSION
from .. import io as _io

# --- revision identity ------------------------------------------------------


@dataclass(frozen=True)
class Revision:
    """A comparable identity for one tide build.

    ``version`` is the pyproject/metadata version; ``commit`` the source git
    short-sha when known (None for a non-git or metadata-only source). ``dirty``
    is advisory only — it is reported but does NOT drive staleness (an editable
    dev tree is routinely dirty, and that alone is not "a newer tide").
    """

    version: str
    commit: Optional[str] = None
    dirty: bool = False

    @property
    def identity(self) -> str:
        """The staleness key: ``version+commit`` when a commit is known, else version."""
        if self.commit:
            return "{0}+{1}".format(self.version, self.commit)
        return self.version

    def __str__(self) -> str:
        base = self.version
        if self.commit:
            base += " ({0}{1})".format(self.commit, "-dirty" if self.dirty else "")
        return base


# --- the pluggable interface ------------------------------------------------


class VersionSource(Protocol):
    """A source-of-truth for "is there a newer tide, and how do I install it?"."""

    def name(self) -> str:
        """Short identifier for the source (e.g. ``local-source``)."""

    def installed(self) -> Revision:
        """The revision the ``tide`` on PATH is currently running."""

    def available(self) -> Revision:
        """The latest revision this source can offer."""

    def install_command(self) -> List[str]:
        """The argv that (re)installs ``tide`` from this source."""


def is_stale(source: VersionSource) -> bool:
    """Pure comparison: True when the source offers a revision != the installed one."""
    return source.installed().identity != source.available().identity


# --- git / pyproject / metadata probes --------------------------------------


def _git(source_dir: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command in *source_dir* (never raises; callers read returncode)."""
    return subprocess.run(
        ["git", "-C", str(source_dir), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def git_head(source_dir: Path) -> tuple[Optional[str], bool]:
    """Return ``(short_sha, dirty)`` for *source_dir*, or ``(None, False)`` if non-git.

    ``dirty`` is True when ``git status --porcelain`` reports any change. Both are
    best-effort: a missing git binary or a non-repo dir yields ``(None, False)``.
    """
    head = _git(source_dir, "rev-parse", "--short", "HEAD")
    if head.returncode != 0:
        return None, False
    sha = head.stdout.strip() or None
    status = _git(source_dir, "status", "--porcelain")
    dirty = bool(status.stdout.strip()) if status.returncode == 0 else False
    return sha, dirty


_VERSION_RE = re.compile(r"""^\s*version\s*=\s*["']([^"']+)["']""", re.MULTILINE)


def read_pyproject_version(source_dir: Path) -> Optional[str]:
    """Read ``[project] version`` from *source_dir*/pyproject.toml, or None."""
    pyproject = Path(source_dir) / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        version = data.get("project", {}).get("version")
        if isinstance(version, str):
            return version
    except (tomllib.TOMLDecodeError, OSError):
        pass
    # last-ditch regex (a malformed-but-readable toml still tells us the version)
    m = _VERSION_RE.search(pyproject.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def installed_metadata_version() -> str:
    """The installed ``tide`` distribution version (falls back to the package's)."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("tide")
        except PackageNotFoundError:
            return _PKG_FALLBACK_VERSION
    except ImportError:  # pragma: no cover - importlib.metadata always present
        return _PKG_FALLBACK_VERSION


# --- the install marker (what we last actually installed) --------------------


def default_marker_path(env: Optional[dict] = None) -> Path:
    """Where the install marker lives (``$TIDE_HOME/install-marker.json``).

    Mirrors ``install.sh``'s ``TIDE_HOME`` (default ``~/.local/share/tide``). The
    home dir is resolved at RUNTIME via ``Path.home()`` — never baked into source
    — so the shipped package stays portable (no abs-home leak; see verify.py).
    """
    env = env if env is not None else os.environ
    home = env.get("TIDE_HOME")
    base = Path(home) if home else Path.home() / ".local" / "share" / "tide"
    return base / "install-marker.json"


def read_marker(path: Path) -> Optional[dict]:
    """Parse the install marker JSON (None when absent or unreadable)."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def write_marker(path: Path, revision: Revision, source_dir: Path) -> None:
    """Write the install marker recording *revision* + its *source_dir*."""
    p = Path(path)
    payload = {
        "version": revision.version,
        "commit": revision.commit,
        "dirty": revision.dirty,
        "source": str(source_dir),
    }
    _io.atomic_write(p, json.dumps(payload, indent=2) + "\n")


# --- the local-source implementation ----------------------------------------


@dataclass
class LocalSourceCheckout:
    """The source-of-truth = the local checkout the install points at (today's only source).

    ``available()`` reads the checkout's pyproject version + git HEAD;
    ``installed()`` prefers the install marker, falling back to package metadata
    (+ source HEAD when editable, since editable code runs straight from source).
    ``install_command()`` is a ``pip install --upgrade`` of the checkout,
    preserving ``-e`` when the current install is editable.
    """

    source_dir: Path
    python_exe: str
    editable: bool
    marker_path: Path

    def name(self) -> str:
        return "local-source"

    def available(self) -> Revision:
        version = read_pyproject_version(self.source_dir) or installed_metadata_version()
        commit, dirty = git_head(self.source_dir)
        return Revision(version=version, commit=commit, dirty=dirty)

    def installed(self) -> Revision:
        marker = read_marker(self.marker_path)
        if marker and isinstance(marker.get("version"), str):
            return Revision(
                version=marker["version"],
                commit=marker.get("commit"),
                dirty=bool(marker.get("dirty", False)),
            )
        # No marker yet: metadata version; commit only knowable for an editable
        # install (its running code == the source checkout).
        commit = None
        if self.editable:
            commit, _ = git_head(self.source_dir)
        return Revision(version=installed_metadata_version(), commit=commit)

    def install_command(self) -> List[str]:
        cmd = [self.python_exe, "-m", "pip", "install", "--upgrade"]
        if self.editable:
            cmd.append("-e")
        cmd.append(str(self.source_dir))
        return cmd

    def record_install(self) -> Revision:
        """Stamp the marker with ``available()`` (call AFTER an accepted install)."""
        rev = self.available()
        write_marker(self.marker_path, rev, self.source_dir)
        return rev


# --- resolution: find the local source the install points at ----------------


def editable_origin() -> Optional[tuple[Path, bool]]:
    """Read the installed dist's ``direct_url.json``: ``(source_dir, editable)`` or None.

    A local ``pip install`` (editable or not) records the origin as a ``file://``
    URL plus an ``dir_info.editable`` flag. A PyPI install records no ``file://``
    url — so this returns None there (that is crit E's published-channel case).
    """
    try:
        from importlib.metadata import PackageNotFoundError, distribution

        try:
            dist = distribution("tide")
        except PackageNotFoundError:
            return None
        raw = dist.read_text("direct_url.json")
    except Exception:  # pragma: no cover - defensive: metadata layout varies
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    url = data.get("url", "")
    if not isinstance(url, str) or not url.startswith("file://"):
        return None
    source_dir = Path(url[len("file://"):])
    editable = bool(data.get("dir_info", {}).get("editable", False))
    return source_dir, editable


def _walk_up_to_checkout(start: Path) -> Optional[Path]:
    """Climb from *start* to the nearest dir holding both ``.git`` and pyproject.toml."""
    here = Path(start).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists() and (candidate / "pyproject.toml").is_file():
            return candidate
    return None


def resolve_source(
    *,
    env: Optional[dict] = None,
    python_exe: Optional[str] = None,
    marker_path: Optional[Path] = None,
) -> Optional[VersionSource]:
    """Resolve the active :class:`VersionSource`, or None when none is available.

    Resolution order for the source-of-truth checkout:

    1. ``$TIDE_SOURCE`` (explicit override) — treated as editable iff the install
       reports editable (so a reinstall preserves the current install shape).
    2. the install's ``direct_url.json`` origin (the normal case).
    3. a walk-up from this package's location to an enclosing git checkout
       (covers an editable install whose metadata is missing direct_url).

    Returns None when there is no local source — that is the seam crit E fills
    (a published channel becomes the source instead).
    """
    import sys

    env = env if env is not None else os.environ
    python_exe = python_exe or sys.executable
    marker_path = marker_path or default_marker_path(env)

    origin = editable_origin()
    origin_editable = origin[1] if origin else True  # default editable: dev shape

    override = env.get("TIDE_SOURCE")
    if override:
        source_dir = Path(override).expanduser()
    elif origin is not None:
        source_dir = origin[0]
    else:
        walked = _walk_up_to_checkout(Path(__file__))
        if walked is None:
            return None
        source_dir = walked

    if not source_dir.is_dir():
        return None

    return LocalSourceCheckout(
        source_dir=source_dir,
        python_exe=python_exe,
        editable=origin_editable,
        marker_path=marker_path,
    )

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
import tarfile
import time
import tomllib
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Protocol

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


def tide_home_dir(env: Optional[dict] = None) -> Path:
    """The ``$TIDE_HOME`` base (default ``~/.local/share/tide``), resolved at RUNTIME.

    Mirrors ``install.sh``'s ``TIDE_HOME``. The home dir is resolved via
    ``Path.home()`` — never baked into source — so the shipped package stays
    portable (no abs-home leak; see verify.py). Shared by every state file we keep
    next to the install (marker, published-channel cache, rollback marker).
    """
    env = env if env is not None else os.environ
    home = env.get("TIDE_HOME")
    return Path(home) if home else Path.home() / ".local" / "share" / "tide"


def default_marker_path(env: Optional[dict] = None) -> Path:
    """Where the install marker lives (``$TIDE_HOME/install-marker.json``)."""
    return tide_home_dir(env) / "install-marker.json"


def default_cache_path(env: Optional[dict] = None) -> Path:
    """Where the published-channel feed cache lives (``$TIDE_HOME/published-channel-cache.json``)."""
    return tide_home_dir(env) / "published-channel-cache.json"


def default_rollback_path(env: Optional[dict] = None) -> Path:
    """Where the rollback marker lives (``$TIDE_HOME/rollback-marker.json``)."""
    return tide_home_dir(env) / "rollback-marker.json"


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


# --- the rollback marker (how to reinstall the PREVIOUS version) -------------


def read_rollback(path: Path) -> Optional[dict]:
    """Parse the rollback marker JSON (None when absent or unreadable)."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def write_rollback(path: Path, version: str, command: List[str]) -> None:
    """Record the PREVIOUS install: its *version* and the *command* that reinstalls it.

    Written just BEFORE an update is applied, so a regression has a pinned recovery
    path (``tide self-update --rollback`` replays ``command``).
    """
    payload = {"version": version, "command": list(command)}
    _io.atomic_write(Path(path), json.dumps(payload, indent=2) + "\n")


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


# --- the published-channel implementation (brew / pip-from-git) -------------

# The release repo is DISCOVERED from the package's own declared source URL (the
# pyproject ``[project.urls]`` → dist ``Project-URL`` metadata), never hardcoded:
# hardcoding the ``owner/name`` literal would (a) bake an instance token into
# shipped source — which ``tide verify --portable`` rightly forbids when the org
# name collides with a dev's username — and (b) drift from the canonical home. The
# fallback below is portable-safe (carries no instance token) and matches the
# pyproject-declared org; the HEAD reconciles it with the real release org when it
# cuts the release (the published channel then follows automatically).
_FALLBACK_REPO = "tide-cli/tide"
_GITHUB_REPO_RE = re.compile(r"github\.com[/:]([^/\s,]+)/([^/\s,#]+)")
CACHE_TTL_S = 24 * 60 * 60  # 24h: session start does NOT hit the network every time
NETWORK_TIMEOUT_S = 5  # short: a stale/offline feed must never hang a session
_USER_AGENT = "tide-self-update"

# A urlopen-shaped callable, injectable so tests never touch the real network.
Opener = Callable[..., object]


def discover_repo() -> str:
    """The release repo ``owner/name``, read from the dist's declared GitHub URL.

    Probes the installed ``tide`` distribution metadata (``Project-URL`` /
    ``Home-page``) for a ``github.com/<owner>/<name>`` and returns ``owner/name``.
    Falls back to :data:`_FALLBACK_REPO` when no metadata / URL is resolvable — so
    a non-dev install still has a channel to point at, with NO literal baked into
    shipped source.
    """
    try:
        from importlib.metadata import metadata

        md = metadata("tide")
        candidates: List[str] = list(md.get_all("Project-URL") or [])
        home = md.get("Home-page")
        if home:
            candidates.append(home)
        for entry in candidates:
            m = _GITHUB_REPO_RE.search(entry)
            if m:
                owner, name = m.group(1), m.group(2)
                if name.endswith(".git"):
                    name = name[: -len(".git")]
                return "{0}/{1}".format(owner, name)
    except Exception:  # pragma: no cover - metadata layout varies / absent
        pass
    return _FALLBACK_REPO


def _detect_homebrew(python_exe: str) -> bool:
    """True when the running ``tide`` lives in a Homebrew keg (a formula install).

    Homebrew's keg layout is ``<prefix>/Cellar/tide/<version>/…``; both the
    formula venv interpreter and the installed package files sit under it. We probe
    the interpreter path first (cheap), then the distribution location as a
    fallback. Explicit + testable: callers may also set ``homebrew=`` directly.
    """
    if "/Cellar/tide/" in str(python_exe or ""):
        return True
    try:
        from importlib.metadata import distribution

        loc = str(distribution("tide").locate_file(""))
        return "/Cellar/tide/" in loc
    except Exception:  # pragma: no cover - metadata layout varies
        return False


def safe_extract(tarball: Path, dest: Path) -> Path:
    """Extract *tarball* into *dest* (path-traversal-guarded) and return the source root.

    A GitHub release/source tarball extracts to a single top-level dir holding the
    project (pyproject + tests). We reject any member that would escape *dest*
    (defence-in-depth on top of the 3.12 ``data`` filter) and return the first
    extracted dir carrying a ``pyproject.toml``.
    """
    dest = Path(dest).resolve()
    with tarfile.open(tarball, "r:gz") as tf:
        for member in tf.getmembers():
            target = (dest / member.name).resolve()
            if target != dest and dest not in target.parents:
                raise RuntimeError("unsafe tar member escapes dest: {0}".format(member.name))
        tf.extractall(dest, filter="data")
    for child in sorted(dest.iterdir()):
        if child.is_dir() and (child / "pyproject.toml").is_file():
            return child
    for found in dest.rglob("pyproject.toml"):
        return found.parent
    raise RuntimeError("extracted archive has no pyproject.toml")


@dataclass
class PublishedChannelSource:
    """The source-of-truth = a published GitHub release (brew / pip-from-git install).

    Fills the seam ``LocalSourceCheckout`` could not: a ``tide`` put on PATH by
    ``brew`` / ``pip install git+…`` / ``install.sh`` has NO in-place source
    checkout. ``installed()`` reads package metadata (+ marker); ``available()``
    asks the GitHub *releases/latest* feed for the newest tag — CACHED ~24h under
    ``$TIDE_HOME`` and fully network-defensive: any error (offline, parse, rate
    limit) is swallowed and reported as "no newer version" (``available == installed``),
    so a session start never blocks, never raises, never falsely nudges.

    ``install_command()`` picks the channel: ``brew upgrade`` for a Homebrew keg,
    else ``pip install --upgrade git+…@<tag>``. The published artifact has no local
    suite to gate, so :meth:`materialize_source` downloads + extracts the release
    tarball; the gate runs against THAT (see :func:`tide.update.core.self_update_published`).
    """

    python_exe: str
    marker_path: Path
    cache_path: Path
    rollback_path: Path
    repo: str = _FALLBACK_REPO
    homebrew: bool = False
    opener: Opener = urllib.request.urlopen

    def name(self) -> str:
        return "published-channel"

    # -- staleness sides -----------------------------------------------------

    def installed(self) -> Revision:
        marker = read_marker(self.marker_path)
        if marker and isinstance(marker.get("version"), str):
            # A published install is keyed on version only (commit is meaningless
            # against a release tag) — so the marker is stamped with commit=None.
            return Revision(version=marker["version"])
        return Revision(version=installed_metadata_version())

    def available(self) -> Revision:
        """The latest published version, or the installed one when unknowable.

        Reads the cached feed (network only on a cold/expired cache, short timeout,
        all errors swallowed). When nothing is resolvable we return *installed* so
        the source reads as "current" — never a crash, never a false nudge.
        """
        tag = self._latest_tag()
        if not tag:
            return self.installed()
        return Revision(version=tag.lstrip("v"))

    def install_command(self) -> List[str]:
        if self.homebrew:
            return ["brew", "upgrade", "{0}/tide".format(self.repo)]
        tag = self._latest_tag() or ("v" + self.available().version)
        return [
            self.python_exe, "-m", "pip", "install", "--upgrade",
            "git+https://github.com/{0}@{1}".format(self.repo, tag),
        ]

    def record_install(self) -> Revision:
        """Stamp the marker with ``available()`` (call AFTER an accepted install)."""
        rev = self.available()
        write_marker(self.marker_path, rev, Path("published:" + self.repo))
        return rev

    def rollback_command(self) -> List[str]:
        """Pin a reinstall of the CURRENTLY-installed version (recorded pre-upgrade).

        Always via pip-from-git@<tag>: it is the only version-pinned reinstall that
        works regardless of channel (a brew keg can't downgrade to an exact past
        version, but pip-from-git can pin the tag).
        """
        version = self.installed().version
        return [
            self.python_exe, "-m", "pip", "install", "--upgrade",
            "git+https://github.com/{0}@v{1}".format(self.repo, version),
        ]

    # -- release artifact (the gated published install) ----------------------

    def tarball_url(self, tag: str) -> str:
        """The GitHub source tarball for *tag* (carries pyproject + tests to gate)."""
        return "https://github.com/{0}/archive/refs/tags/{1}.tar.gz".format(self.repo, tag)

    def materialize_source(self, workdir: Path) -> Path:
        """Download + extract the release tarball into *workdir*; return its source root.

        Raises on any failure. Unlike :meth:`available`, this is fail-LOUD: it runs
        only under an EXPLICIT ``tide self-update`` (never at session start), and a
        gate cannot run without the source — so a failed fetch must REFUSE the
        update, not silently proceed.
        """
        tag = self._latest_tag()
        if not tag:
            raise RuntimeError("no published release tag resolvable (cannot fetch artifact)")
        workdir = Path(workdir)
        tarball = workdir / "tide-{0}.tar.gz".format(tag.lstrip("v"))
        req = urllib.request.Request(self.tarball_url(tag), headers={"User-Agent": _USER_AGENT})
        with self.opener(req, timeout=NETWORK_TIMEOUT_S) as resp:
            tarball.write_bytes(resp.read())
        return safe_extract(tarball, workdir)

    # -- the 24h feed cache --------------------------------------------------

    def _latest_tag(self) -> Optional[str]:
        """Latest release tag (e.g. ``v1.0.1``) — cache-first, network-defensive."""
        cached = self._read_cache()
        if cached and self._cache_fresh(cached):
            return _cache_tag(cached)
        fetched = self._fetch_latest_tag()
        if fetched:
            self._write_cache(fetched)
            return fetched
        # Fetch failed (offline / rate-limited): fall back to a stale cache if any,
        # else report nothing (→ available()==installed() → no nudge).
        return _cache_tag(cached) if cached else None

    def _fetch_latest_tag(self) -> Optional[str]:
        """Query GitHub ``releases/latest`` for ``tag_name`` (None on ANY error)."""
        url = "https://api.github.com/repos/{0}/releases/latest".format(self.repo)
        req = urllib.request.Request(
            url, headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"}
        )
        try:
            with self.opener(req, timeout=NETWORK_TIMEOUT_S) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            tag = data.get("tag_name")
            return tag if isinstance(tag, str) and tag else None
        except Exception:
            return None

    def _read_cache(self) -> Optional[dict]:
        p = Path(self.cache_path)
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, OSError):
            return None

    def _cache_fresh(self, data: dict) -> bool:
        ts = data.get("fetched_at")
        return isinstance(ts, (int, float)) and (time.time() - ts) < CACHE_TTL_S

    def _write_cache(self, tag: str) -> None:
        try:
            _io.atomic_write(
                Path(self.cache_path),
                json.dumps({"tag": tag, "fetched_at": time.time()}, indent=2) + "\n",
            )
        except OSError:  # pragma: no cover - cache is best-effort, never fatal
            pass


def _cache_tag(data: Optional[dict]) -> Optional[str]:
    """Read the ``tag`` out of a cache dict (None when absent/odd)."""
    if not data:
        return None
    tag = data.get("tag")
    return tag if isinstance(tag, str) and tag else None


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
    """Resolve the active :class:`VersionSource` — local source first, else published.

    Resolution order for the source-of-truth checkout:

    1. ``$TIDE_SOURCE`` (explicit override) — treated as editable iff the install
       reports editable (so a reinstall preserves the current install shape).
    2. the install's ``direct_url.json`` origin (the normal dev case).
    3. a walk-up from this package's location to an enclosing git checkout
       (covers an editable install whose metadata is missing direct_url).

    When NONE of those yields a real local checkout, fall back to a
    :class:`PublishedChannelSource` (the brew / pip-from-git / install.sh case) —
    so a non-dev install still gets gated, supervised self-update. (Returns None
    only in the degenerate case the published source itself can't be constructed,
    which is effectively never — it needs only metadata, not a checkout.)
    """
    import sys

    env = env if env is not None else os.environ
    python_exe = python_exe or sys.executable
    marker_path = marker_path or default_marker_path(env)

    origin = editable_origin()
    origin_editable = origin[1] if origin else True  # default editable: dev shape

    override = env.get("TIDE_SOURCE")
    source_dir: Optional[Path] = None
    if override:
        cand = Path(override).expanduser()
        if cand.is_dir():
            source_dir = cand
    elif origin is not None and origin[0].is_dir():
        source_dir = origin[0]
    else:
        source_dir = _walk_up_to_checkout(Path(__file__))

    if source_dir is not None and source_dir.is_dir():
        return LocalSourceCheckout(
            source_dir=source_dir,
            python_exe=python_exe,
            editable=origin_editable,
            marker_path=marker_path,
        )

    # No local source → the published channel becomes the source-of-truth.
    return PublishedChannelSource(
        python_exe=python_exe,
        marker_path=marker_path,
        cache_path=default_cache_path(env),
        rollback_path=default_rollback_path(env),
        repo=discover_repo(),
        homebrew=_detect_homebrew(python_exe),
    )

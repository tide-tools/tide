"""tide.cannon.reality — M2 reality-rev: API-surface fingerprint over covered paths.

The reality-rev is the second freshness axis: while cannon-rev tracks whether
CANON.md itself has changed, reality-rev tracks whether the *interface CANON
claims to describe* has changed. When an open arc's stamped reality-rev
disagrees with the current reality-rev, the gate trips STALE — "the API shipped,
canon didn't."

Why a fingerprint, not a content hash (kill gate fatigue)
---------------------------------------------------------
A naive content hash over covered code files trips on ANY edit — comments,
whitespace, formatting, a function-body tweak, an added test, a lockfile bump.
In a real repo 30–60% of commits touch covered files without changing the API
surface, so every one would force a canon delta or a noop+debt. ``noop`` then
becomes reflexive and the gate degrades into documentation-theater.

So for **code files** reality-rev hashes only the NORMALIZED API SURFACE —
signature-bearing lines (``class`` / ``def`` / ``async def`` / ``export`` /
``func`` / ``type`` / ``interface``), stripped and sorted per file. Cosmetic
edits, body-only changes, and added tests do NOT move the rev; adding, removing,
or changing a signature DOES. (Prior art: Fiberplane Drift — AST API-surface
fingerprinting; here a stdlib regex approximation.)

For **non-code files** (``.md`` / ``.json`` / ``.txt`` / config — no recognisable
signatures) reality-rev falls back to a full-content hash for that file, so docs
and config are still tracked verbatim.

**Deliberate tradeoff:** API-surface fingerprinting can MISS pure behavioural
(body-only) changes — a bug fix that keeps the same signatures will not move
reality-rev. This is the Drift tradeoff: avoiding gate-fatigue is worth the
occasional false-negative. Behavioural coverage is M3 / substance-check
territory (a merged delta must encode what the work *taught*), not this axis.

``canon-covers:`` / ``canon-covers-exclude:`` manifest
------------------------------------------------------
A project declares covered paths in one of two places (checked in order):

1. **CANON.md preamble** — everything before the first ``## `` heading. A bare
   ``canon-covers:`` (or ``canon-covers-exclude:``) line starts a block;
   subsequent lines indented with whitespace OR prefixed with ``- `` are path
   globs relative to the project root. A non-indented non-blank line (or the
   first ``## `` heading) ends the block.

2. **.tide/state/canon-covers** and **.tide/state/canon-covers-exclude** — one
   glob per line; ``#``-led lines are comments and are stripped.

Exclude globs (candidate 32) drop matching paths from reality-rev entirely — for
lockfiles, generated code, vendored dirs. A project with no ``canon-covers:``
manifest degrades gracefully: :func:`reality_rev` returns ``None`` and no
``reality-rev:`` field is stamped. This is not an error — it simply has no
reality axis.

In a git repo, ``git ls-files`` is used so only *tracked* files count
(untracked/ignored files are invisible). Outside git (e.g. test fixtures without
a repo), pathlib glob is the fallback.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Set

from .. import fields, paths

# Match cannon.rev.REV_LEN for consistency.
REV_LEN = 12

# Manifest markers (declared in CANON.md preamble or .tide/state/<file>).
COVERS_MARKER = "canon-covers:"
EXCLUDE_MARKER = "canon-covers-exclude:"
COVERS_STATE_FILE = "canon-covers"
EXCLUDE_STATE_FILE = "canon-covers-exclude"

# File extensions treated as CODE — these get an API-surface fingerprint instead
# of a full-content hash. Everything else (docs, config, data) falls back to a
# full-content hash so it is still tracked verbatim.
CODE_EXTENSIONS: Set[str] = {
    ".py", ".pyi",
    ".js", ".jsx", ".mjs", ".cjs",
    ".ts", ".tsx",
    ".go",
    ".rs",
    ".java", ".kt",
    ".c", ".h", ".cc", ".cpp", ".hpp",
    ".rb",
    ".swift",
    ".scala",
}

# Signature-bearing line prefixes (after leading whitespace). A regex over the
# given keywords; covers Python (class/def/async def), JS/TS (export/class/
# function via class+export+interface+type), Go (func/type), and others that
# reuse these keywords. The trailing space/paren guards against matching a bare
# identifier that merely starts with the keyword (e.g. a variable named
# ``classifier``).
import re

_SIGNATURE_RE = re.compile(
    r"^\s*("
    r"class[\s(]"          # class Foo / class Foo(
    r"|def\s"              # def foo
    r"|async\s+def\s"      # async def foo
    r"|export\s"           # export …
    r"|func[\s(]"          # func foo / func (recv)
    r"|type\s"             # type Foo …
    r"|interface\s"        # interface Foo …
    r")"
)


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------

def _parse_canon_block(text: str, marker: str) -> Optional[List[str]]:
    """Extract the *marker* glob block from the preamble of *text* (before first ``## ``).

    A bare *marker* line (e.g. ``canon-covers:``) starts the block. Subsequent
    lines that are indented (start with whitespace) or prefixed with ``- `` are
    glob patterns. A non-indented non-blank line or the first ``## `` heading
    ends the block. Returns ``None`` when the marker is absent or the block empty.
    """
    in_block = False
    globs: List[str] = []

    for line in text.splitlines():
        if line.startswith("## "):
            break  # end of preamble
        stripped = line.strip()
        if stripped == marker:
            in_block = True
            continue
        if in_block:
            if stripped.startswith("- "):
                globs.append(stripped[2:].strip())
            elif line and line[0].isspace() and stripped:
                globs.append(stripped)
            elif stripped:
                # non-indented non-blank line → end of this block
                in_block = False
            # blank lines inside the block are allowed (ignored)

    return globs if globs else None


def _parse_canon_text(text: str) -> Optional[List[str]]:
    """Back-compat shim: parse the ``canon-covers:`` block from *text*."""
    return _parse_canon_block(text, COVERS_MARKER)


def _parse_state_file(root: Path, filename: str) -> Optional[List[str]]:
    """Parse a one-glob-per-line ``.tide/state/<filename>`` (``#``-comments stripped)."""
    state_file = paths.state_dir(Path(root)) / filename
    if not state_file.is_file():
        return None
    lines = state_file.read_text(encoding="utf-8").splitlines()
    globs = [
        ln.strip()
        for ln in lines
        if ln.strip() and not ln.strip().startswith("#")
    ]
    return globs if globs else None


def parse_manifest(root: Path) -> Optional[List[str]]:
    """Return the ``canon-covers:`` globs for *root*, or ``None`` when absent.

    Checks (1) the ``canon-covers:`` block in CANON.md's preamble, then (2)
    ``.tide/state/canon-covers``. Returns ``None`` when neither is present so the
    caller can degrade gracefully.
    """
    canon = paths.canon_file(Path(root))
    if canon.is_file():
        globs = _parse_canon_block(canon.read_text(encoding="utf-8"), COVERS_MARKER)
        if globs is not None:
            return globs
    return _parse_state_file(Path(root), COVERS_STATE_FILE)


def parse_exclude(root: Path) -> List[str]:
    """Return the ``canon-covers-exclude:`` globs for *root* (``[]`` when absent).

    Same two-source resolution as :func:`parse_manifest`: CANON.md preamble block
    first, then ``.tide/state/canon-covers-exclude``. Excludes are additive
    filters dropped from reality-rev (lockfiles, generated code, vendored dirs);
    an absent exclude manifest is simply no filtering.
    """
    canon = paths.canon_file(Path(root))
    if canon.is_file():
        globs = _parse_canon_block(canon.read_text(encoding="utf-8"), EXCLUDE_MARKER)
        if globs is not None:
            return globs
    return _parse_state_file(Path(root), EXCLUDE_STATE_FILE) or []


# ---------------------------------------------------------------------------
# Per-file fingerprint (API surface for code, full content otherwise)
# ---------------------------------------------------------------------------

def _api_surface(text: str) -> str:
    """Return the normalized API-surface fingerprint text of code *text*.

    Keeps only signature-bearing lines (see :data:`_SIGNATURE_RE`), strips each
    to remove leading/trailing whitespace (so indentation and trailing-space
    churn are invisible), and sorts them so line-reordering does not move the
    fingerprint. Body-only edits, comments, and blank-line churn drop out.
    """
    sigs = [
        line.strip()
        for line in text.splitlines()
        if _SIGNATURE_RE.match(line)
    ]
    return "\n".join(sorted(sigs))


def _file_fingerprint(abs_path: Path) -> str:
    """Return the sha256 fingerprint of one file.

    Code files (suffix in :data:`CODE_EXTENSIONS`) are fingerprinted by their
    normalized API surface; all other files fall back to a full-content hash so
    docs/config are tracked verbatim. A binary/undecodable code file falls back
    to full-content hashing too (its "surface" is undefined).
    """
    p = Path(abs_path)
    if p.suffix.lower() in CODE_EXTENSIONS:
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return hashlib.sha256(p.read_bytes()).hexdigest()
        return hashlib.sha256(_api_surface(text).encode("utf-8")).hexdigest()
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Path collection (git-tracked or filesystem glob), with excludes applied
# ---------------------------------------------------------------------------

def _is_git_repo(root: Path) -> bool:
    """True when *root* contains a ``.git`` entry (dir or worktree file)."""
    return (Path(root) / ".git").exists()


def _ls_files(root: Path, globs: List[str]) -> Set[str]:
    """Git-tracked relative paths matching *globs* (empty set on any git error)."""
    if not globs:
        return set()
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--", *globs],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()
    return {ln.strip() for ln in result.stdout.splitlines() if ln.strip()}


def _glob_files(root: Path, globs: List[str]) -> Set[str]:
    """Filesystem relative paths matching *globs* via pathlib glob (non-git mode)."""
    found: Set[str] = set()
    for pattern in globs:
        for abs_path in Path(root).glob(pattern):
            if abs_path.is_file():
                found.add(str(abs_path.relative_to(root)))
    return found


def _covered_paths(root: Path, covers: List[str], exclude: List[str]) -> Set[str]:
    """Relative paths covered by *covers* minus those matched by *exclude*.

    Uses git-tracked enumeration in a git repo (untracked/ignored files invisible)
    and pathlib glob otherwise. Excludes are computed with the SAME engine so the
    drop is symmetric with the coverage.
    """
    if _is_git_repo(Path(root)):
        covered = _ls_files(Path(root), covers)
        excluded = _ls_files(Path(root), exclude)
    else:
        covered = _glob_files(Path(root), covers)
        excluded = _glob_files(Path(root), exclude)
    return covered - excluded


# ---------------------------------------------------------------------------
# Reality-rev computation
# ---------------------------------------------------------------------------

def reality_rev(root: Path) -> Optional[str]:
    """Return the reality-rev for *root*: an API-surface fingerprint over covered files.

    Returns ``None`` when no ``canon-covers:`` manifest exists — graceful
    degradation, never an error. Code files contribute their normalized API
    surface; non-code files their full content; ``canon-covers-exclude:`` paths
    are dropped entirely. Uses ``git ls-files`` in git repos; pathlib glob
    otherwise.

    An empty match (manifest present but no covered files) returns a stable
    "empty" rev — so the rev is defined and can detect files being *added* to
    coverage.
    """
    covers = parse_manifest(Path(root))
    if covers is None:
        return None
    exclude = parse_exclude(Path(root))

    rel_paths = _covered_paths(Path(root), covers, exclude)

    file_fps: Dict[str, str] = {}
    for rel in rel_paths:
        abs_path = Path(root) / rel
        if abs_path.is_file():
            file_fps[rel] = _file_fingerprint(abs_path)

    if not file_fps:
        return hashlib.sha256(b"").hexdigest()[:REV_LEN]

    digest = hashlib.sha256()
    for rel_path, fp in sorted(file_fps.items()):
        digest.update("{0}\0{1}\n".format(rel_path, fp).encode("utf-8"))
    return digest.hexdigest()[:REV_LEN]


# ---------------------------------------------------------------------------
# Passport stamp
# ---------------------------------------------------------------------------

def stamp_reality_rev(passport_doc: Path, root: Path) -> Optional[str]:
    """Stamp the current ``reality-rev`` into *passport_doc* and return it.

    *passport_doc* is the actual passport file path (``arc.md`` or
    ``<slug>-goal.md``), not the entry dir.  This avoids importing
    ``arc.stream`` here and keeps the module cycle-free.

    A no-op (returns ``None``) when the project has no ``canon-covers:``
    manifest.  When a manifest exists, writes ``reality-rev: <rev>`` via
    :func:`tide.fields.set_field` and returns the rev.
    """
    rr = reality_rev(Path(root))
    if rr is not None:
        fields.set_field(Path(passport_doc), "reality-rev", rr)
    return rr

"""tide.arc.candidate — the candidates backlog and its promotion into the stream.

Ported from the arcs CLI (``arcs candidate`` / ``arcs promote``), retargeted to
``<project>/.tide/arcs/candidates/``. A candidate is a *surfaced future-work
idea* that doesn't belong to the current arc — a worker or the human drops it
here, an orchestrator later promotes it into a real arc.

Three operations, matching the design (build-blueprint "candidates"):

* **capture** — :func:`new_candidate` writes ``candidates/NN-<slug>.md`` on the
  candidates' OWN numbering sequence (:func:`tide.numbering.next_num_file`,
  decoupled from the work-stream counter), with a ``from:`` origin field and a
  free-form body.
* **list** — :func:`list_candidates` / :func:`render_list` show the backlog
  (slug + from-origin); also surfaced in the U8 status board.
* **promote** — :func:`promote` turns a candidate into a real arc: it creates
  the arc, **MOVES** the candidate file into the arc's ``input/`` as its seed
  (body + origin preserved), and clears it from ``candidates/``. Decision 8 +
  resolved-risk #3: a worker may *surface* a candidate but **only an
  orchestrator promotes it** — the CLI handler hard-refuses via
  ``cli.require_orchestrator`` before this logic ever runs.

All logic is plain functions (argparse-free, unit-testable); :func:`register`
wires the thin CLI handlers and owns the role gate on ``promote``.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# The honest "when it was dropped" stamp (candidate 89). The deck reads this field
# first and only falls back to FS birthtime/mtime when it's absent — and FS times
# lie, because sharpening a candidate in a rename-editor resets both. Minute
# precision, matching the deck's primary parse format (``%Y-%m-%d %H:%M``).
DROPPED_FIELD = "dropped"
_DROPPED_FMT = "%Y-%m-%d %H:%M"

from .. import fields, io as _io, numbering, paths, slug
from . import stream

# A candidate file: NN-<slug>.md (2+ digit number, base-10 padding).
_CAND_RE = re.compile(r"^(\d{2,})-(.+)\.md$")


class CandidateError(stream.StreamError):
    """A user-facing candidates error (empty slug, unknown promote key …).

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it
    with the same ``except`` arm (prints ``tide: …``, exits nonzero).
    """


# --- seed template ---------------------------------------------------------

def _candidate_md(
    name: str, from_arc: Optional[str], body: Optional[str], dropped: str
) -> str:
    """Seed text for a ``candidates/NN-<slug>.md`` backlog entry.

    *name* is the file stem (``NN-<slug>``) used as the H1; ``from:`` records the
    origin arc (``-`` when none); ``dropped:`` stamps *when* it was dropped so the
    board's age stays honest across later sharpening (candidate 89); the body is
    the free-form idea — the slug is only a short handle, so the full surfaced idea
    is persisted here (fix F6). On promote this whole file moves into the new arc's
    ``input/`` as the seed, so origin, stamp and body travel with it untouched.
    """
    origin = (from_arc or "").strip() or "-"
    text = (body or "").strip() or "<one line — the surfaced idea>"
    return (
        "# {name}\n"
        "\n"
        "from: {origin}\n"
        "dropped: {dropped}\n"
        "\n"
        "{body}\n"
    ).format(name=name, origin=origin, dropped=dropped, body=text)


# --- capture ---------------------------------------------------------------

def new_candidate(
    root: Path,
    raw_slug: str,
    from_arc: Optional[str] = None,
    body: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Path:
    """Capture ``candidates/NN-<slug>.md`` on the candidates' OWN number sequence.

    The number comes from :func:`tide.numbering.next_num_file` (separate from the
    work-stream counter — capturing a candidate never consumes an arc number, and
    vice-versa). Records *from_arc* as the ``from:`` origin and stamps ``dropped:``
    with *now* (default: wall clock) so the board's age is honest from birth and
    survives later sharpening (candidate 89). The slug is a SHORT handle
    (:func:`slug.short_slug`, capped) so a pasted idea doesn't become a 200-char
    filename; the full idea is persisted in the BODY — *body* when given, else the
    raw title text (fix F6). Returns the new file path.
    """
    s = slug.short_slug(raw_slug)
    if not s:
        raise CandidateError("candidate: empty slug after slugify")
    # Keep the full idea in the body even when only a title was passed: the slug
    # is a capped handle and would otherwise be the only record of the idea.
    idea = (body or "").strip() or (raw_slug or "").strip()
    dropped = (now or datetime.now()).strftime(_DROPPED_FMT)
    cdir = paths.candidates_dir(root)
    cdir.mkdir(parents=True, exist_ok=True)
    nn = numbering.next_num_file(cdir)
    name = "{0}-{1}".format(nn, s)
    path = cdir / "{0}.md".format(name)
    _io.atomic_write(path, _candidate_md(name, from_arc, idea, dropped))
    return path


# --- list ------------------------------------------------------------------

def _parse(name: str):
    """Return ``(num, slug)`` for a candidate filename, or None if it isn't one."""
    m = _CAND_RE.match(name)
    if not m:
        return None
    return m.group(1), m.group(2)


def list_candidates(root: Path) -> List[Dict[str, object]]:
    """Return the candidates backlog as dicts, ordered by filename (NN ascending).

    Each entry carries ``path``/``name``/``stem``/``num``/``slug`` plus the
    ``from`` origin (read from frontmatter; None when absent). Non-candidate files
    in the dir are ignored.
    """
    cdir = paths.candidates_dir(root)
    out: List[Dict[str, object]] = []
    if not cdir.is_dir():
        return out
    for p in sorted(cdir.glob("*.md")):
        parsed = _parse(p.name)
        if not parsed:
            continue
        num, cslug = parsed
        out.append(
            {
                "path": p,
                "name": p.name,
                "stem": p.stem,
                "num": num,
                "slug": cslug,
                "from": fields.read_field(p, "from"),
            }
        )
    return out


def render_list(root: Path) -> str:
    """One-line-per-candidate backlog rendering (``NN-slug ← from <origin>``)."""
    items = list_candidates(root)
    if not items:
        return "(no candidates)"
    lines: List[str] = []
    for it in items:
        origin = it["from"] or "-"
        lines.append("{stem}  ← from {origin}".format(stem=it["stem"], origin=origin))
    return "\n".join(lines)


# --- promote ---------------------------------------------------------------

def _resolve(cdir: Path, key: str) -> Optional[Path]:
    """Resolve a candidate file by *key* — matches ``NN``, ``NN-slug`` or ``slug``.

    Tolerates a trailing ``.md`` and a leading ``-`` on the key (so ``-slug``
    works). Returns the first matching file in filename order, or None.
    """
    raw = (key or "").strip()
    stem = raw[:-3] if raw.lower().endswith(".md") else raw
    bare = stem.lstrip("-")
    want_slug = slug.slugify(bare)
    for p in sorted(cdir.glob("*.md")):
        parsed = _parse(p.name)
        if not parsed:
            continue
        num, cslug = parsed
        full = "{0}-{1}".format(num, cslug)
        if bare.isdigit() and int(bare) == int(num):
            return p
        if stem == full:
            return p
        if want_slug and want_slug == cslug:
            return p
    return None


def promote(
    root: Path,
    key: str,
    new_slug: Optional[str] = None,
    goal_slug: Optional[str] = None,
) -> Path:
    """Promote a candidate into a real arc, MOVING its file into the arc ``input/``.

    Resolves the candidate by *key* (NN / NN-slug / slug), creates an arc named
    *new_slug* (or the candidate's own slug), MOVES the candidate file into that
    arc's ``input/`` (seed = body, origin preserved), and removes it from
    ``candidates/``. ``-g goal`` promotes into a goal's substream. Returns the new
    arc dir.

    NOTE: orchestrator-only — the CLI handler calls ``require_orchestrator``
    *before* this runs (mirrors ``canon merge``); this logic stays gate-free so
    it is unit-testable.
    """
    cdir = paths.candidates_dir(root)
    cand = _resolve(cdir, key)
    if cand is None:
        raise CandidateError(
            "promote: no candidate matching {0!r} in {1}".format(key, cdir)
        )
    parsed = _parse(cand.name)
    assert parsed is not None  # _resolve only returns parseable files
    _num, cslug = parsed

    target = slug.slugify(new_slug) if new_slug else cslug
    if not target:
        raise CandidateError("promote: empty target slug after slugify")

    entry = stream.new_arc(root, target, goal_slug=goal_slug)
    dest = entry / "input" / cand.name
    cand.rename(dest)  # MOVE: seeds input/, clears candidates/ in one step
    return entry


# --- archive (retire a resolved candidate off the shelf) -------------------

DONE_DIRNAME = "__done__"

# A candidate is RESOLVED when a line OPENS with one of these markers — the house
# convention for a closed candidate (``РЕШЕНО (13.07): …`` / ``Сделано в 6/6 …``).
# Anchored at line start so a bug whose DESCRIPTION merely says "уже закрытое" or
# "закрывается гейтом" is never mis-swept (those words sit mid-sentence).
_RESOLVED_LINE = re.compile(r"^\s*(РЕШЕНО|СДЕЛАНО|Сделано)\b")


def done_dir(root: Path) -> Path:
    """The grave for retired candidates: ``candidates/__done__/`` (kept, reversible)."""
    return paths.candidates_dir(root) / DONE_DIRNAME


def is_resolved(path: Path) -> bool:
    """True when a candidate carries a resolution note (a ``РЕШЕНО``/``Сделано`` line)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(_RESOLVED_LINE.match(ln) for ln in text.splitlines())


def _archive_file(cand: Path, ddir: Path) -> Path:
    """Move *cand* into *ddir*, suffixing on a name collision (never overwrite)."""
    ddir.mkdir(parents=True, exist_ok=True)
    dest = ddir / cand.name
    n = 1
    while dest.exists():
        dest = ddir / "{0}~{1}".format(cand.name, n)
        n += 1
    cand.rename(dest)
    return dest


def archive(root: Path, key: str) -> Path:
    """Retire ONE candidate to ``candidates/__done__/`` — off the list + board, kept.

    The list and the deck both read only the top-level ``*.md`` (non-recursive), so a
    file under ``__done__/`` drops out of view while staying on disk (and in git) — the
    reversible retirement the backlog lacked (promote is for FUTURE work, drop is for
    junk; this is for DONE). Returns the archived path.
    """
    cdir = paths.candidates_dir(root)
    cand = _resolve(cdir, key)
    if cand is None:
        raise CandidateError("archive: no candidate matching {0!r} in {1}".format(key, cdir))
    return _archive_file(cand, done_dir(root))


def resolved_candidates(root: Path) -> List[Path]:
    """The active candidates carrying a resolution note — the ``--resolved`` sweep set."""
    return [it["path"] for it in list_candidates(root) if is_resolved(it["path"])]


def archive_resolved(root: Path, *, apply: bool = False) -> "tuple[List[Path], List[Path]]":
    """Find (and with *apply*, retire) every RESOLVED candidate. Returns ``(found, moved)``.

    Dry-run by default (``moved`` empty) — the caller lists what WOULD move so a
    mis-detected candidate is caught before anything is touched (mirrors ``arc gc``).
    """
    found = resolved_candidates(root)
    if not apply or not found:
        return found, []
    ddir = done_dir(root)
    moved = [_archive_file(p, ddir) for p in found]
    return found, moved


# --- CLI wiring ------------------------------------------------------------

def _root() -> Path:
    return paths.require_tide_root()


def _resolve_target_root(project: str) -> Path:
    """Resolve a sibling project's tide root by its roster name (cross-project capture).

    Looks *project* up in the control-home roster (``$TIDE_HOME`` or the cwd climb),
    so an agent working in one project can drop a candidate into ANY rostered
    neighbour. Raises a user-facing error when the name is unknown or its directory
    is not a tide project (so we never scaffold a stray ``.tide/`` somewhere).
    """
    from .. import roster  # lazy: avoid an import cycle at module load

    home = paths.control_home()
    for entry in roster.read_roster(home):
        if entry.get("name") == project:
            root = Path(entry["path"]).expanduser()
            if not (root / ".tide").is_dir():
                raise CandidateError(
                    "candidate: project {0!r} ({1}) is not a tide project".format(project, root)
                )
            return root
    raise CandidateError(
        "candidate: no project named {0!r} in the roster ({1})".format(
            project, paths.roster_file(home)
        )
    )


def _cmd_add(args) -> int:
    body = " ".join(args.text) if args.text else None
    project = getattr(args, "project", None)
    from_arc = args.from_arc
    if project:
        root = _resolve_target_root(project)
        # Default from: to the ORIGIN project so the target can trace where the
        # cross-project candidate came from (unless an explicit --from was given).
        if not from_arc:
            origin = paths.find_tide_root()
            if origin is not None:
                from_arc = "↗ {0}".format(origin.name)
    else:
        root = _root()
    path = new_candidate(root, args.slug, from_arc=from_arc, body=body)
    if project:
        print("tide: captured candidate {0} → project {1}".format(path.name, project))
    else:
        print("tide: captured candidate {0}".format(path.name))
    return 0


def _cmd_list(args) -> int:
    print(render_list(_root()))
    return 0


def _cmd_archive(args) -> int:
    root = _root()
    if getattr(args, "resolved", False):
        found, moved = archive_resolved(root, apply=getattr(args, "apply", False))
        if not found:
            print("tide: candidate archive — no resolved candidates on the shelf ✓")
            return 0
        if moved:
            print("tide: archived {0} resolved candidate(s) → {1}/ (reversible):".format(
                len(moved), DONE_DIRNAME))
            for m in moved:
                print("  {0}".format(m.name))
        else:
            print("tide: {0} resolved candidate(s) — dry-run, add --apply to retire:".format(
                len(found)))
            for p in found:
                print("  {0}".format(p.name))
        return 0
    if not getattr(args, "key", None):
        raise CandidateError(
            "archive: give a candidate key, or --resolved to sweep every done one"
        )
    dest = archive(root, args.key)
    print("tide: archived candidate {0} → {1}/".format(dest.name, DONE_DIRNAME))
    return 0


def _cmd_drop(args) -> int:
    from .curate import drop_candidate  # local: sibling domain module

    dest = drop_candidate(_root(), args.key)
    print("tide: candidate dropped → {0} (restorable)".format(dest))
    return 0


def _cmd_promote(args) -> int:
    # cli.main wraps RoleError → exit 1; import lazily to avoid an import cycle.
    from ..cli import require_orchestrator

    require_orchestrator("candidate promote")
    entry = promote(_root(), args.key, new_slug=args.new_slug, goal_slug=args.goal)
    print("tide: promoted {0} → arc {1}".format(args.key, entry.name))
    return 0


def register(subparsers) -> None:
    """Add the ``candidate`` command group to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser("candidate", help="capture/list/promote future-work ideas")
    csub = p.add_subparsers(dest="candidate_cmd")

    ap = csub.add_parser("add", help="capture a candidate (own NN sequence)")
    ap.add_argument("slug")
    ap.add_argument("--from", dest="from_arc", help="origin arc slug (recorded as from:)")
    ap.add_argument(
        "--project",
        help="capture into ANOTHER rostered project (by roster name), not the cwd one",
    )
    ap.add_argument("text", nargs="*", help="free-form body (the surfaced idea)")
    ap.set_defaults(func=_cmd_add, _cmd="candidate add")

    lp = csub.add_parser("list", help="list the candidates backlog")
    lp.set_defaults(func=_cmd_list, _cmd="candidate list")

    drp = csub.add_parser(
        "drop",
        help="✕ soft-drop an idea into candidates/__dropped__/ (off the shelf, restorable)",
    )
    drp.add_argument("key", help="candidate file name without .md (e.g. 42-some-idea)")
    drp.set_defaults(func=_cmd_drop, _cmd="candidate drop")

    arp = csub.add_parser(
        "archive",
        help="retire a resolved candidate to __done__/ (off the list + board; reversible)",
    )
    arp.add_argument("key", nargs="?", help="candidate NN, NN-slug, or slug (omit with --resolved)")
    arp.add_argument(
        "--resolved",
        action="store_true",
        help="sweep EVERY candidate carrying a РЕШЕНО/СДЕЛАНО note (dry-run unless --apply)",
    )
    arp.add_argument(
        "--apply",
        action="store_true",
        help="with --resolved: actually move them (default: list what would move)",
    )
    arp.set_defaults(func=_cmd_archive, _cmd="candidate archive")

    pp = csub.add_parser("promote", help="ORCHESTRATOR-ONLY: turn a candidate into a real arc")
    pp.add_argument("key", help="candidate NN, NN-slug, or slug")
    pp.add_argument("new_slug", nargs="?", help="optional new arc slug (default: candidate slug)")
    pp.add_argument("-g", "--goal", help="promote into this goal's substream")
    pp.set_defaults(func=_cmd_promote, _cmd="candidate promote")

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
from pathlib import Path
from typing import Dict, List, Optional

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

def _candidate_md(name: str, from_arc: Optional[str], body: Optional[str]) -> str:
    """Seed text for a ``candidates/NN-<slug>.md`` backlog entry.

    *name* is the file stem (``NN-<slug>``) used as the H1; ``from:`` records the
    origin arc (``-`` when none); the body is the free-form idea — the slug is
    only a short handle, so the full surfaced idea is persisted here (fix F6). On
    promote this whole file moves into the new arc's ``input/`` as the seed, so the
    origin and body travel with it untouched.
    """
    origin = (from_arc or "").strip() or "-"
    text = (body or "").strip() or "<one line — the surfaced idea>"
    return (
        "# {name}\n"
        "\n"
        "from: {origin}\n"
        "\n"
        "{body}\n"
    ).format(name=name, origin=origin, body=text)


# --- capture ---------------------------------------------------------------

def new_candidate(
    root: Path,
    raw_slug: str,
    from_arc: Optional[str] = None,
    body: Optional[str] = None,
) -> Path:
    """Capture ``candidates/NN-<slug>.md`` on the candidates' OWN number sequence.

    The number comes from :func:`tide.numbering.next_num_file` (separate from the
    work-stream counter — capturing a candidate never consumes an arc number, and
    vice-versa). Records *from_arc* as the ``from:`` origin. The slug is a SHORT
    handle (:func:`slug.short_slug`, capped) so a pasted idea doesn't become a
    200-char filename; the full idea is persisted in the BODY — *body* when given,
    else the raw title text (fix F6). Returns the new file path.
    """
    s = slug.short_slug(raw_slug)
    if not s:
        raise CandidateError("candidate: empty slug after slugify")
    # Keep the full idea in the body even when only a title was passed: the slug
    # is a capped handle and would otherwise be the only record of the idea.
    idea = (body or "").strip() or (raw_slug or "").strip()
    cdir = paths.candidates_dir(root)
    cdir.mkdir(parents=True, exist_ok=True)
    nn = numbering.next_num_file(cdir)
    name = "{0}-{1}".format(nn, s)
    path = cdir / "{0}.md".format(name)
    _io.atomic_write(path, _candidate_md(name, from_arc, idea))
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
    *before* this runs (mirrors ``cannon merge``); this logic stays gate-free so
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


# --- CLI wiring ------------------------------------------------------------

def _root() -> Path:
    return paths.require_tide_root()


def _cmd_add(args) -> int:
    body = " ".join(args.text) if args.text else None
    path = new_candidate(_root(), args.slug, from_arc=args.from_arc, body=body)
    print("tide: captured candidate {0}".format(path.name))
    return 0


def _cmd_list(args) -> int:
    print(render_list(_root()))
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
    ap.add_argument("text", nargs="*", help="free-form body (the surfaced idea)")
    ap.set_defaults(func=_cmd_add, _cmd="candidate add")

    lp = csub.add_parser("list", help="list the candidates backlog")
    lp.set_defaults(func=_cmd_list, _cmd="candidate list")

    pp = csub.add_parser("promote", help="ORCHESTRATOR-ONLY: turn a candidate into a real arc")
    pp.add_argument("key", help="candidate NN, NN-slug, or slug")
    pp.add_argument("new_slug", nargs="?", help="optional new arc slug (default: candidate slug)")
    pp.add_argument("-g", "--goal", help="promote into this goal's substream")
    pp.set_defaults(func=_cmd_promote, _cmd="candidate promote")

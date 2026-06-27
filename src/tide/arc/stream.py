"""tide.arc.stream — the on-disk arc/goal work stream and its lifecycle.

Ported from the arcs CLI (``new-arc``/``new-goal``/``close``/``reopen``/
``supersede``), retargeted to ``<project>/.tide/arcs/`` and extended with tide's
``cannon-rev`` stamp. The stream is ONE continuous numbered sequence holding two
kinds of entry:

* **arc** — ``NN-<slug>/`` : work without a standing purpose.
* **goal** — ``NN-@<slug>/`` : an arc WITH a purpose, carrying its own nested
  ``arcs/`` substream (local ``01,02…`` numbering) and an immutable
  ``<slug>-goal.md`` passport.

Each entry is the triad ``input/`` → ``workspace/`` (disposable) → ``output/``;
outside reads ``output/`` only. The load-bearing invariants this module owns:

* **Continuous numbering** — :func:`tide.numbering.next_num` counts open AND
  closed entries; closing renames but never frees a number.
* **Dual done-marking** — close renames to ``__NN-<slug>__`` AND sets
  ``status: done``; reopen reverses BOTH. Folder name and doc status never disagree.
* **Empty-output guard** — close refuses an empty ``output/`` (``-f`` overrides).
* **Arc-vs-goal disambiguation** — close/reopen/supersede PREFER the goal when a
  slug names one, else the plain arc (deterministic, never a coin-flip).
* **Immutable intent** — a meaning pivot is a *supersede*: close old (no output
  guard), create new same-kind, write ``supersedes:`` after ``status:``, seed
  ``input/from-<old>.md``. Old and new both stay on disk, linked.
* **cannon-rev stamp** — opening (or creating) an arc stamps the current
  ``cannon-rev`` (sha256 of CANON.md) into its passport for drift detection.
* **Safe removal** — ``rm``/``abort`` deletes a stray/unwanted entry but refuses
  to drop one with a merged delta or one referenced by a ``supersedes:`` chain
  (integrity guards, never ``-f``-overridable); a non-empty ``output/`` (or a
  goal with sub-arcs) needs ``-f`` (dogfood fix F8 — kills the manual ``rm -rf``).

All logic is plain functions (argparse-free, unit-testable); :func:`register`
wires the thin CLI handlers.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import List, Optional

from .. import fields, io as _io, numbering, paths, placeholders, slug
from ..cannon import rev
from . import templates

TRIAD = ("input", "workspace", "output")


class StreamError(Exception):
    """A user-facing arc-stream error (bad ref, closed goal, empty output …)."""


# --- entry resolution ------------------------------------------------------

def _entries(stream_dir: Path) -> List[Path]:
    """Child entry dirs of *stream_dir* (excludes the candidates/ backlog)."""
    if not Path(stream_dir).is_dir():
        return []
    return [
        p
        for p in Path(stream_dir).iterdir()
        if p.is_dir() and p.name != paths.CANDIDATES_DIRNAME
    ]


def _find(stream_dir: Path, want: str, *, goal: bool, closed: bool) -> Optional[Path]:
    """First entry in *stream_dir* matching slug *want* and the goal/closed flags."""
    target = slug.slugify(want)
    for p in _entries(stream_dir):
        if slug.entry_slug(p.name) != target:
            continue
        if slug.is_goal_entry(p.name) != goal:
            continue
        if slug.is_closed_entry(p.name) != closed:
            continue
        return p
    return None


def _resolve(stream_dir: Path, want: str, *, closed: bool) -> Optional[Path]:
    """Resolve an entry preferring the GOAL when *want* names one, else the arc."""
    g = _find(stream_dir, want, goal=True, closed=closed)
    if g is not None:
        return g
    return _find(stream_dir, want, goal=False, closed=closed)


def _resolve_present(stream_dir: Path, want: str) -> Optional[Path]:
    """Resolve an entry whether OPEN or CLOSED (open preferred), goal over arc."""
    return _resolve(stream_dir, want, closed=False) or _resolve(stream_dir, want, closed=True)


def passport_path(entry_dir: Path) -> Path:
    """The status-bearing doc for an entry: the goal doc if present, else arc.md."""
    goals = sorted(Path(entry_dir).glob("*-goal.md"))
    if goals:
        return goals[-1]
    return Path(entry_dir) / "arc.md"


# --- search-dir / goal-substream resolution --------------------------------

def _search_dir(root: Path, goal_slug: Optional[str]) -> Path:
    """The stream dir to operate in: a goal's substream (``-g``) or the top stream.

    For ``-g`` we accept an open OR closed goal so close/reopen/supersede can
    reach sub-arcs of a closed goal (matches arcs' ``_arc_searchdir``).
    """
    arcs = paths.arcs_dir(root)
    if not goal_slug:
        return arcs
    g = slug.slugify(goal_slug)
    gdir = _find(arcs, g, goal=True, closed=False) or _find(arcs, g, goal=True, closed=True)
    if gdir is None:
        raise StreamError("goal {0!r} not found in {1}".format(goal_slug, arcs))
    return gdir / paths.ARCS_DIRNAME


def _open_goal_substream(root: Path, goal_slug: str) -> Path:
    """The ``arcs/`` substream of an OPEN goal; new sub-arcs need a live goal."""
    arcs = paths.arcs_dir(root)
    g = slug.slugify(goal_slug)
    gdir = _find(arcs, g, goal=True, closed=False)
    if gdir is None:
        if _find(arcs, g, goal=True, closed=True) is not None:
            raise StreamError(
                "goal {0!r} is closed — reopen it first".format(goal_slug)
            )
        raise StreamError("goal {0!r} not found in {1}".format(goal_slug, arcs))
    return gdir / paths.ARCS_DIRNAME


# --- cannon-rev stamp ------------------------------------------------------

def stamp_rev(entry_dir: Path, root: Path) -> str:
    """Stamp cannon-rev (and reality-rev when a manifest exists) into the passport.

    M2 extension: also stamps ``reality-rev:`` via
    :func:`tide.cannon.reality.stamp_reality_rev` when the project carries a
    ``canon-covers:`` manifest. The lazy import keeps the load-time import
    graph cycle-free (``cannon.reality`` does not import ``arc.stream`` at its
    module top).
    """
    r = rev.compute(root)
    pp = passport_path(entry_dir)
    fields.set_field(pp, "cannon-rev", r)
    from ..cannon import reality as _reality  # lazy: avoids load-time cycle
    _reality.stamp_reality_rev(pp, root)
    return r


# --- create ----------------------------------------------------------------

def new_arc(root: Path, raw_slug: str, goal_slug: Optional[str] = None) -> Path:
    """Create a standalone arc ``NN-<slug>/`` (or a sub-arc under ``-g goal``).

    Builds the input/workspace/output triad + a templated ``arc.md`` and stamps
    the current cannon-rev. Returns the new entry dir.
    """
    s = slug.slugify(raw_slug)
    if not s:
        raise StreamError("new arc: empty slug after slugify")
    # Between-arcs barrier (U7): no new arc while a closed arc's delta is unmerged.
    from .. import sync  # lazy: tide.sync imports this module at top.

    sync.block_new_arc_if_unmerged_delta(root)
    stream_dir = _open_goal_substream(root, goal_slug) if goal_slug else paths.arcs_dir(root)
    stream_dir.mkdir(parents=True, exist_ok=True)
    nn = numbering.next_num(stream_dir)
    entry = stream_dir / "{0}-{1}".format(nn, s)
    for sub in TRIAD:
        (entry / sub).mkdir(parents=True, exist_ok=True)
    _io.atomic_write(entry / "arc.md", templates.arc_md(entry.name))
    stamp_rev(entry, root)
    return entry


def new_goal(root: Path, raw_slug: str) -> Path:
    """Create a goal ``NN-@<slug>/`` with the triad + nested ``arcs/`` + goal doc.

    Goals always live in the top stream (never inside another goal). Returns the
    new goal dir. Stamps cannon-rev into the goal doc.
    """
    s = slug.slugify(raw_slug)
    if not s:
        raise StreamError("new goal: empty slug after slugify")
    arcs = paths.arcs_dir(root)
    arcs.mkdir(parents=True, exist_ok=True)
    nn = numbering.next_num(arcs)
    entry = arcs / "{0}-@{1}".format(nn, s)
    for sub in (*TRIAD, paths.ARCS_DIRNAME):
        (entry / sub).mkdir(parents=True, exist_ok=True)
    _io.atomic_write(entry / "{0}-goal.md".format(s), templates.goal_md(s))
    stamp_rev(entry, root)
    return entry


# --- open / resume ---------------------------------------------------------

def open_arc(root: Path, ref: str, goal_slug: Optional[str] = None) -> Path:
    """Select an OPEN arc/goal as the active worker entry and stamp cannon-rev.

    Resolves preferring the goal; raises :class:`StreamError` if no open entry
    matches *ref*. Returns the entry dir. ``resume`` is an alias of this.
    """
    # Between-arcs barrier (U7): no entering the next arc while a closed arc's
    # delta is unmerged — reconcile through the merge gate first.
    from .. import sync  # lazy: tide.sync imports this module at top.

    sync.block_new_arc_if_unmerged_delta(root)
    stream_dir = _search_dir(root, goal_slug)
    entry = _resolve(stream_dir, ref, closed=False)
    if entry is None:
        raise StreamError(
            "open arc/goal {0!r} not found in {1} (closed?)".format(ref, stream_dir)
        )
    stamp_rev(entry, root)
    return entry


# --- close / reopen --------------------------------------------------------

def _output_empty(entry_dir: Path) -> bool:
    out = Path(entry_dir) / "output"
    if not out.is_dir():
        return True
    return not any(out.iterdir())


def close(
    root: Path,
    ref: str,
    goal_slug: Optional[str] = None,
    force: bool = False,
) -> Path:
    """Close an arc/goal: empty-output + placeholder guards (``-f`` overrides), then dual-mark done.

    Guards (skipped with *force*): an empty ``output/`` AND any leftover scaffold
    placeholder in the passport (``<…>`` template spans / the ``# supersedes:``
    hint — dogfood fix F5, so a closed passport never reads like a fill-in form).
    Then sets ``status: done`` in the passport AND renames the dir to ``__…__``.
    Returns the closed dir path.
    """
    stream_dir = _search_dir(root, goal_slug)
    entry = _resolve(stream_dir, ref, closed=False)
    if entry is None:
        raise StreamError(
            "open arc/goal {0!r} not found in {1} (already closed?)".format(ref, stream_dir)
        )
    if not force and _output_empty(entry):
        raise StreamError(
            "arc {0!r} has an empty output/ — write the result there first "
            "(a closed arc must carry a self-contained output). override: close -f".format(ref)
        )
    if not force:
        doc = passport_path(entry)
        leftovers = placeholders.find_in_file(doc)
        if leftovers:
            raise StreamError(placeholders.refuse_message(doc.name, ref, leftovers))

    # Worktree gate (11-arc-worktree-isolation): land the arc branch before sealing.
    # Gated so non-git projects and arcs without a branch are a pure no-op.
    from . import worktree as _wt  # lazy: avoid import cycle at module load
    if _wt.is_git_repo(root) and _wt.has_worktree(root, entry):
        if not force:
            result = _wt.land(root, entry)
            if result.conflict:
                raise StreamError(
                    "cannot close arc {0!r}: {1} "
                    "(resolve the conflict, then close)".format(ref, result.detail)
                )
            _wt.remove(root, entry)
        else:
            # force (supersede path) — discard the worktree without landing.
            _wt.remove(root, entry)

    fields.set_field(passport_path(entry), "status", "done")
    closed = entry.parent / "__{0}__".format(entry.name)
    entry.rename(closed)
    return closed


def reopen(root: Path, ref: str, goal_slug: Optional[str] = None) -> Path:
    """Undo a close: strip the ``__…__`` wrapper AND set ``status: active``.

    Resolves preferring the goal. Returns the reopened (un-wrapped) dir path.
    """
    stream_dir = _search_dir(root, goal_slug)
    entry = _resolve(stream_dir, ref, closed=True)
    if entry is None:
        raise StreamError(
            "closed arc/goal {0!r} not found in {1}".format(ref, stream_dir)
        )
    open_name = slug.strip_marker(entry.name)
    opened = entry.parent / open_name
    entry.rename(opened)
    fields.set_field(passport_path(opened), "status", "active")
    return opened


# --- supersede -------------------------------------------------------------

def _is_goal_ref(root: Path, ref: str) -> bool:
    """True when *ref* names a goal (open or closed) in the TOP stream."""
    arcs = paths.arcs_dir(root)
    return (
        _find(arcs, ref, goal=True, closed=False) is not None
        or _find(arcs, ref, goal=True, closed=True) is not None
    )


def _write_supersedes(doc_path: Path, old: str) -> None:
    """Insert ``supersedes: <old>`` right AFTER ``status:`` and drop the comment.

    The fresh template carries a ``# supersedes:`` placeholder comment; we remove
    it and write the real field, keeping the canonical position (after status:).
    """
    bare = slug.strip_marker(old)
    text = doc_path.read_text(encoding="utf-8")
    had_trailing_nl = text.endswith("\n")
    lines = text.split("\n")
    if had_trailing_nl and lines and lines[-1] == "":
        lines = lines[:-1]

    out: List[str] = []
    inserted = False
    for line in lines:
        if line.strip().startswith("# supersedes:"):
            continue  # drop the template placeholder
        out.append(line)
        if not inserted and fields._line_key(line) == "status":
            out.append("supersedes: {0}".format(bare))
            inserted = True
    if not inserted:
        # No status: line (unexpected) — fall back to the order-preserving setter.
        fields.set_field(doc_path, "supersedes", bare)
        return
    body = "\n".join(out)
    _io.atomic_write(doc_path, body + "\n" if had_trailing_nl else body)


def supersede(
    root: Path,
    old: str,
    new: str,
    goal_slug: Optional[str] = None,
) -> Path:
    """Pivot ``old`` → ``new``: close old (no guard), create new same-kind, link.

    Closes ``old`` with the output guard skipped (a superseded unit may carry no
    result), creates ``new`` preserving kind (goal→goal, arc→arc), writes
    ``supersedes: <old>`` after ``status:`` in the new passport, and seeds
    ``input/from-<old>.md``. Returns the new entry dir.
    """
    old_s = slug.slugify(slug.strip_marker(old))
    new_s = slug.slugify(new)
    if not old_s or not new_s:
        raise StreamError("supersede needs <old> and <new> slugs")

    was_goal = _is_goal_ref(root, old_s)

    # 1. close old, force (goals live top-level → ignore -g when old is a goal).
    close(root, old_s, goal_slug=None if was_goal else goal_slug, force=True)

    # 2. create new, preserving kind.
    if was_goal:
        entry = new_goal(root, new_s)
        doc = entry / "{0}-goal.md".format(new_s)
        kind = "goal"
    else:
        entry = new_arc(root, new_s, goal_slug=goal_slug)
        doc = entry / "arc.md"
        kind = "arc"

    # 3. link the intent chain (supersedes: after status:).
    _write_supersedes(doc, old_s)

    # 4. seed the back-pointer into input/.
    _io.atomic_write(
        entry / "input" / "from-{0}.md".format(old_s),
        templates.from_seed(old_s, kind),
    )
    return entry


# --- rm / abort ------------------------------------------------------------

def _all_entry_dirs(root: Path) -> List[Path]:
    """Every real stream entry (top-level + each goal's nested sub-arcs)."""
    arcs = paths.arcs_dir(root)
    out: List[Path] = []
    for p in _entries(arcs):
        if not slug.is_entry(p.name):
            continue
        out.append(p)
        if slug.is_goal_entry(p.name):
            out.extend(
                c for c in _entries(p / paths.ARCS_DIRNAME) if slug.is_entry(c.name)
            )
    return out


def _subtree_has_merged_delta(entry_dir: Path) -> bool:
    """True when *entry_dir* (or, for a goal, any sub-arc) carries a merged delta.

    A merged delta (``merged: yes``) is folded into CANON.md — its source is part
    of cannon history, so deleting the arc would orphan a contribution the canon
    journal already cites. Walks the whole subtree so a goal isn't emptied of a
    sub-arc whose work is already merged.
    """
    from .. import sync  # lazy: tide.sync imports this module at top.

    for delta in Path(entry_dir).rglob(sync.DELTA_FILE):
        if fields.read_field(delta, sync.MERGED_KEY) == sync.MERGED_YES:
            return True
    return False


def _referencing_entries(root: Path, entry: Path) -> List[Path]:
    """Entries OUTSIDE *entry*'s subtree whose passport ``supersedes:`` names it.

    Removing a superseded arc would orphan the ``supersedes:`` pointer (and the
    ``input/from-…`` seed) in its successor. Referrers inside the subtree being
    removed don't count — they vanish with it.
    """
    target = slug.normalize_ref(slug.entry_slug(entry.name))
    refs: List[Path] = []
    for e in _all_entry_dirs(root):
        if e == entry or _is_within(e, entry):
            continue
        sup = fields.read_field(passport_path(e), "supersedes")
        if sup and slug.normalize_ref(sup) == target:
            refs.append(e)
    return sorted(refs, key=lambda p: p.name)


def _is_within(child: Path, parent: Path) -> bool:
    """True when *child* lives under *parent* (or is *parent* itself)."""
    try:
        Path(child).relative_to(Path(parent))
        return True
    except ValueError:
        return False


def _needs_force_to_remove(entry_dir: Path) -> bool:
    """True when an entry carries auditable content that ``rm`` won't drop sans ``-f``.

    A non-empty ``output/`` (the arc's self-contained result) or — for a goal —
    any nested sub-arc both count as real work worth a deliberate ``-f``.
    """
    if not _output_empty(entry_dir):
        return True
    if slug.is_goal_entry(entry_dir.name):
        return bool(_entries(entry_dir / paths.ARCS_DIRNAME))
    return False


def rm(
    root: Path,
    ref: str,
    goal_slug: Optional[str] = None,
    force: bool = False,
) -> Path:
    """Delete a stray/unwanted arc or goal dir (open OR closed) with sane guards.

    The escape hatch for probe/throwaway entries that used to need a manual
    ``rm -rf`` (dogfood fix F8). Resolves *ref* preferring an open entry then a
    closed one, goal over arc, and refuses in three cases:

    * **merged delta** — the entry (or, for a goal, a sub-arc) carries a
      ``merged: yes`` delta folded into CANON.md; its source is cannon history,
      so removal is refused outright (``-f`` does NOT override — reopen/supersede
      instead).
    * **referenced** — another entry's ``supersedes:`` names it; removing it would
      orphan that chain. Refused outright; remove the referrer first.
    * **non-empty output / nested sub-arcs** — auditable content; refused UNLESS
      *force* (the one guard ``-f`` overrides).

    Returns the removed dir path. The two integrity guards (merged / referenced)
    are deliberately not force-overridable so a single ``-f`` can't silently drop
    cannon-anchored work — that path stays a manual ``rm -rf``.
    """
    stream_dir = _search_dir(root, goal_slug)
    entry = _resolve_present(stream_dir, ref)
    if entry is None:
        raise StreamError("arc/goal {0!r} not found in {1}".format(ref, stream_dir))

    if _subtree_has_merged_delta(entry):
        raise StreamError(
            "refuse to remove {0}: it carries a merged cannon-delta (its work is "
            "part of cannon history) — reopen/supersede instead of deleting".format(
                entry.name
            )
        )

    referrers = _referencing_entries(root, entry)
    if referrers:
        names = ", ".join(r.name for r in referrers)
        raise StreamError(
            "refuse to remove {0}: referenced by {1} (supersedes chain) — "
            "remove the referrer first".format(entry.name, names)
        )

    if not force and _needs_force_to_remove(entry):
        raise StreamError(
            "{0} carries auditable output/nested work — refusing to delete it "
            "without -f (override: arc rm -f)".format(entry.name)
        )

    shutil.rmtree(entry)
    return entry


# --- CLI wiring ------------------------------------------------------------

def _root() -> Path:
    return paths.require_tide_root()


def _cmd_new(args) -> int:
    entry = new_arc(_root(), args.slug, goal_slug=args.goal)
    print("tide: created arc {0}".format(entry))
    return 0


def _cmd_new_goal(args) -> int:
    entry = new_goal(_root(), args.slug)
    print("tide: created goal {0}".format(entry))
    return 0


def _cmd_open(args) -> int:
    entry = open_arc(_root(), args.slug, goal_slug=args.goal)
    print("tide: opened {0} (cannon-rev stamped)".format(entry))
    return 0


def _cmd_close(args) -> int:
    root = _root()
    # Orca abandon-gate: refuse close if the arc's linked GitHub issue is still
    # open.  The open issue IS the durable commitment — the arc cannot be sealed
    # while its PR is unmerged.  No-op for headless arcs (no orca-issue field).
    from ..adapters import orca_worktree as _ow  # lazy: avoid import at module load
    stream_dir = _search_dir(root, args.goal)
    arc_dir = _resolve(stream_dir, args.slug, closed=False)
    if arc_dir is not None:
        try:
            _ow.abandon_gate(arc_dir)
        except StreamError as exc:  # AbandonGateError is a StreamError subclass
            print("tide: {0}".format(exc), file=sys.stderr)
            return 1

    closed = close(root, args.slug, goal_slug=args.goal, force=args.force)
    print("tide: closed {0} (status: done)".format(closed.name))
    return 0


def _cmd_reopen(args) -> int:
    opened = reopen(_root(), args.slug, goal_slug=args.goal)
    print("tide: reopened {0}".format(opened.name))
    return 0


def _cmd_supersede(args) -> int:
    entry = supersede(_root(), args.old, args.new, goal_slug=args.goal)
    print("tide: superseded {0} → {1}".format(args.old, entry.name))
    return 0


def _cmd_rm(args) -> int:
    removed = rm(_root(), args.slug, goal_slug=args.goal, force=args.force)
    print("tide: removed {0}".format(removed.name))
    return 0


def _add_goal_opt(p) -> None:
    p.add_argument("-g", "--goal", help="operate inside this goal's substream")


def register(arc_subparsers) -> None:
    """Add the U3 arc-stream verbs to the ``tide arc`` subparser group."""
    np = arc_subparsers.add_parser("new", help="create an arc NN-<slug>/ (-g goal to nest)")
    np.add_argument("slug")
    _add_goal_opt(np)
    np.set_defaults(func=_cmd_new, _cmd="arc new")

    gp = arc_subparsers.add_parser("new-goal", help="create a goal NN-@<slug>/ with nested substream")
    gp.add_argument("slug")
    gp.set_defaults(func=_cmd_new_goal, _cmd="arc new-goal")

    op = arc_subparsers.add_parser("open", help="select an open arc as active (stamps cannon-rev)")
    op.add_argument("slug")
    _add_goal_opt(op)
    op.set_defaults(func=_cmd_open, _cmd="arc open")

    rp = arc_subparsers.add_parser("resume", help="re-enter an open arc (re-stamp cannon-rev)")
    rp.add_argument("slug")
    _add_goal_opt(rp)
    rp.set_defaults(func=_cmd_open, _cmd="arc resume")

    cp = arc_subparsers.add_parser("close", help="dual-mark done (__…__ + status:done), empty-output guard")
    cp.add_argument("slug")
    cp.add_argument("-f", "--force", action="store_true", help="skip the empty-output guard")
    _add_goal_opt(cp)
    cp.set_defaults(func=_cmd_close, _cmd="arc close")

    rop = arc_subparsers.add_parser("reopen", help="undo a close (strip __…__ + status:active)")
    rop.add_argument("slug")
    _add_goal_opt(rop)
    rop.set_defaults(func=_cmd_reopen, _cmd="arc reopen")

    sp = arc_subparsers.add_parser("supersede", help="pivot: close old + create new with supersedes:")
    sp.add_argument("old")
    sp.add_argument("new")
    _add_goal_opt(sp)
    sp.set_defaults(func=_cmd_supersede, _cmd="arc supersede")

    mp = arc_subparsers.add_parser(
        "rm",
        aliases=["abort"],
        help="delete a stray arc/goal dir (guards: merged delta / referenced; -f for non-empty output)",
    )
    mp.add_argument("slug")
    mp.add_argument("-f", "--force", action="store_true", help="remove even with non-empty output/ or nested sub-arcs")
    _add_goal_opt(mp)
    mp.set_defaults(func=_cmd_rm, _cmd="arc rm")

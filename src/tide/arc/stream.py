"""tide.arc.stream — the on-disk arc/goal work stream and its lifecycle.

Ported from the arcs CLI (``new-arc``/``new-goal``/``close``/``reopen``/
``supersede``), retargeted to ``<project>/.tide/arcs/`` and extended with tide's
``canon-rev`` stamp. The stream is ONE continuous numbered sequence holding two
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
* **canon-rev stamp** — opening (or creating) an arc stamps the current
  ``canon-rev`` (sha256 of CANON.md) into its passport for drift detection.
* **Safe removal** — ``rm``/``abort`` deletes a stray/unwanted entry but refuses
  to drop one with a merged delta or one referenced by a ``supersedes:`` chain
  (integrity guards, never ``-f``-overridable); a non-empty ``output/`` (or a
  goal with sub-arcs) needs ``-f`` (dogfood fix F8 — kills the manual ``rm -rf``).

All logic is plain functions (argparse-free, unit-testable); :func:`register`
wires the thin CLI handlers.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .. import fields, io as _io, numbering, paths, placeholders, slug
from .. import resolve as _shared_resolve
from ..canon import rev
from . import templates

TRIAD = ("input", "workspace", "output")

# The three arc kinds. ``goal`` is detected by its ``*-goal.md`` passport; a
# ``thread`` (тред — one session's memory) is a normal arc tagged ``kind: thread``
# in its passport; everything else is a plain work ``arc``.
KIND_ARC = "arc"
KIND_GOAL = "goal"
KIND_THREAD = "thread"
# Legacy on-disk kind value — a thread used to be a ``prism`` (renamed). Old
# containers still carry ``kind: prism`` in their passport; read-compat maps it to
# KIND_THREAD so pre-rename projects keep working (migrate.py rewrites on demand).
KIND_THREAD_LEGACY = "prism"
# A ``routine`` (рутина) is a reusable-procedure container: goal-shaped, tagged
# ``kind: routine`` in its passport, holding its **runs** as sub-arcs in nested
# ``arcs/`` (exactly like a thread holds sessions). See :data:`KIND_ROUTINE`.
KIND_ROUTINE = "routine"


class StreamError(Exception):
    """A user-facing arc-stream error (bad ref, closed goal, empty output …)."""


# --- entry resolution ------------------------------------------------------

def _entries(stream_dir: Path) -> List[Path]:
    """Child entry dirs of *stream_dir* — delegated to :mod:`tide.resolve`."""
    return _shared_resolve.child_entries(stream_dir)


def _find(stream_dir: Path, want: str, *, goal: bool, closed: bool) -> Optional[Path]:
    """First entry matching *want* + flags — THE matcher lives in :mod:`tide.resolve`.

    Both-form matching (displayed name ``04-@slug`` AND bare slug, cands 43 +
    agent report 2026-07-07) is the shared resolver's load-bearing rule; this
    thin alias keeps stream's internal call-sites unchanged.
    """
    return _shared_resolve.find_entry(stream_dir, want, goal=goal, closed=closed)


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


# --- entry kind (arc / goal / thread) --------------------------------------

def entry_kind(entry_dir: Path) -> str:
    """Classify an entry: ``thread`` (``kind: thread`` — a container of sessions),
    ``routine`` (``kind: routine`` — a container of runs), ``goal`` (a goal doc
    without either mark), else ``arc``.

    Kind is read from the on-disk passport, so the ``kind: thread``/``kind:
    routine`` mark wins even on a goal-shaped container — a thread (and a routine)
    IS a goal-with-nested-children, just tagged. See :data:`KIND_THREAD` /
    :data:`KIND_ROUTINE`.
    """
    pp = passport_path(entry_dir)
    k = (fields.read_field(pp, "kind") or "").strip().lower()
    if k in (KIND_THREAD, KIND_THREAD_LEGACY):  # read-compat: old ``kind: prism``
        return KIND_THREAD
    if k == KIND_ROUTINE:
        return KIND_ROUTINE
    if pp.name.endswith("-goal.md"):
        return KIND_GOAL
    return KIND_ARC


def is_thread(entry_dir: Path) -> bool:
    """True when *entry_dir* is a thread (a kind: thread container of sessions)."""
    return entry_kind(entry_dir) == KIND_THREAD


# --- draft classification (gates: cand 04) ----------------------------------
# A DRAFT is COMPUTED, never stored: an open entry whose formulation is still the
# template placeholder is a болванка regardless of what ``status:`` says. Computing
# it (instead of a new on-disk status) means every existing project's abandoned
# shells classify correctly with zero migration, and there is no promotion write
# to forget — fill the goal and the entry IS active on the next read.

STATUS_DRAFT = "draft"

_SECTION_RE_TMPL = r"^##\s+{0}\s*\n(.*?)(?=^##\s|\Z)"


def _section_body(text: str, title: str) -> str:
    """The body of the ``## <title>`` section in *text* ('' when absent)."""
    m = re.search(_SECTION_RE_TMPL.format(re.escape(title)), text, re.M | re.S)
    return m.group(1).strip() if m else ""


def goal_filled(entry_dir: Path) -> bool:
    """True when the entry's ``goal:`` line is REAL (non-empty, placeholder-free).

    The picker's bar: a container with a stated goal is somebody's intent and must
    stay reachable, even while (say) a routine's steps are still being written.
    """
    goal = (fields.read_field(passport_path(entry_dir), "goal") or "").strip()
    return bool(goal) and not placeholders.find_in_text("goal: {0}".format(goal))


def passport_filled(entry_dir: Path) -> bool:
    """True when the entry's formulation is REAL — not template scaffolding.

    ``goal:`` must be non-empty and placeholder-free; a routine must also carry a
    real ``## steps`` runbook (a routine without steps cannot be run, so it is
    still a draft even with a goal line).
    """
    pp = passport_path(entry_dir)
    try:
        text = pp.read_text(encoding="utf-8")
    except OSError:
        return False
    if not goal_filled(entry_dir):
        return False
    if entry_kind(entry_dir) == KIND_ROUTINE:
        steps = _section_body(text, "steps")
        if not steps or placeholders.find_in_text(steps):
            return False
    return True


def _is_nested_item(entry_dir: Path) -> bool:
    """True for an entry INSIDE a container (a session/run/goal sub-arc).

    Nested items are exempt from draft classification: a fresh session is born by
    the handoff machinery and works before its goal line is polished — hiding it
    from the picker would break pickup. The болванка problem lives in the TOP
    stream (abandoned thread/routine/goal shells).
    """
    grandparent = Path(entry_dir).parent.parent
    return passport_path(grandparent).is_file()


def effective_status(entry_dir: Path) -> str:
    """The status surfaces should show — ``draft`` for an unfilled open entry.

    Reads ``status:`` from the passport; an ``active`` TOP-STREAM entry whose
    formulation is still template placeholders classifies as :data:`STATUS_DRAFT`.
    Everything else passes through unchanged.
    """
    raw = (fields.read_field(passport_path(entry_dir), "status") or "active").strip()
    raw = raw or "active"
    if raw != "active":
        return raw
    if _is_nested_item(entry_dir) or passport_filled(entry_dir):
        return raw
    return STATUS_DRAFT


def draft_entries(root: Path) -> List[Path]:
    """Open top-stream entries that classify as drafts (болванки), numeric order."""
    arcs = paths.arcs_dir(root)
    out = [
        p
        for p in _entries(arcs)
        if not slug.is_closed_entry(p.name) and effective_status(p) == STATUS_DRAFT
    ]
    return sorted(out, key=lambda p: p.name)


def is_routine(entry_dir: Path) -> bool:
    """True when *entry_dir* is a routine (a kind: routine container of runs)."""
    return entry_kind(entry_dir) == KIND_ROUTINE


def thread_entries(root: Path, *, closed: bool = False) -> List[Path]:
    """Project threads (треды) in the top stream, open by default, in numeric order.

    A thread is a goal-shaped container tagged ``kind: thread``; its sessions are
    the sub-arcs in its nested ``arcs/``. Pass ``closed=True`` for sealed threads.
    Used by the picker to offer threads (not all arcs) for continue/new.
    """
    arcs = paths.arcs_dir(root)
    out = [
        p
        for p in _entries(arcs)
        if slug.is_closed_entry(p.name) == closed and is_thread(p)
    ]
    # NN prefixes are zero-padded, so a lexical name sort is numeric order.
    return sorted(out, key=lambda p: p.name)


def routine_entries(root: Path, *, closed: bool = False) -> List[Path]:
    """Project routines (рутины) in the top stream, open by default, numeric order.

    A routine is a goal-shaped container tagged ``kind: routine``; its **runs** are
    the sub-arcs in its nested ``arcs/`` (a run IS a session inside the routine).
    Mirrors :func:`thread_entries` but filters ``kind == routine`` — used by the
    picker to offer routines (not threads/goals/arcs) for run/continue.
    """
    arcs = paths.arcs_dir(root)
    out = [
        p
        for p in _entries(arcs)
        if slug.is_closed_entry(p.name) == closed and is_routine(p)
    ]
    # NN prefixes are zero-padded, so a lexical name sort is numeric order.
    return sorted(out, key=lambda p: p.name)


# --- sessions (sub-arcs inside a thread) -----------------------------------

def session_entries(root: Path, thread_slug: str, *, closed: bool = False) -> List[Path]:
    """The sessions (sub-arcs) of a thread, open by default, in numeric order.

    Sessions live in the thread container's nested ``arcs/`` substream; their
    ``from:`` field chains one to the next so the picker can show the lineage.
    """
    sub = _search_dir(root, thread_slug)  # the thread's arcs/ substream
    out = [
        p
        for p in _entries(sub)
        if slug.is_closed_entry(p.name) == closed
    ]
    return sorted(out, key=lambda p: p.name)


def last_session(root: Path, thread_slug: str) -> Optional[Path]:
    """The newest session of a thread (open or closed), or None when it has none.

    Used to chain a new session's ``from:`` to its predecessor.
    """
    sub = _search_dir(root, thread_slug)
    everything = sorted(_entries(sub), key=lambda p: p.name)
    return everything[-1] if everything else None


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


# --- canon-rev stamp ------------------------------------------------------

def stamp_rev(entry_dir: Path, root: Path) -> str:
    """Stamp canon-rev (and reality-rev when a manifest exists) into the passport.

    M2 extension: also stamps ``reality-rev:`` via
    :func:`tide.canon.reality.stamp_reality_rev` when the project carries a
    ``canon-covers:`` manifest. The lazy import keeps the load-time import
    graph cycle-free (``canon.reality`` does not import ``arc.stream`` at its
    module top).
    """
    r = rev.compute(root)
    pp = passport_path(entry_dir)
    fields.set_field(pp, "canon-rev", r)
    from ..canon import reality as _reality  # lazy: avoids load-time cycle
    _reality.stamp_reality_rev(pp, root)
    return r


# --- anti-runaway backpressure (gates: cand 04) ------------------------------
# The mite incident: an automated loop created SEVEN empty arcs in two minutes —
# no human works at that rate. Every birth is stamped into ``.tide/state/births``;
# when the window overflows, creation REFUSES with an escalation message instead
# of silently flooding the tree. Env-tunable; a limit of 0 disables the gate.

SPAWN_LIMIT_ENV = "TIDE_SPAWN_LIMIT"
SPAWN_WINDOW_ENV = "TIDE_SPAWN_WINDOW"
DEFAULT_SPAWN_LIMIT = 8        # births allowed per window per project
DEFAULT_SPAWN_WINDOW = 600     # seconds
BIRTHS_FILE = "births"


def _spawn_tuning() -> "tuple[int, int]":
    """The (limit, window-seconds) pair, env-overridable; bad values fall back."""
    try:
        limit = int(os.environ.get(SPAWN_LIMIT_ENV, DEFAULT_SPAWN_LIMIT))
    except ValueError:
        limit = DEFAULT_SPAWN_LIMIT
    try:
        window = int(os.environ.get(SPAWN_WINDOW_ENV, DEFAULT_SPAWN_WINDOW))
    except ValueError:
        window = DEFAULT_SPAWN_WINDOW
    return limit, window


def record_birth_and_guard(root: Path) -> None:
    """Stamp one arc birth for *root*; REFUSE when the window is already full.

    Raises :class:`StreamError` (the escalation) when *limit* births already
    happened inside *window* seconds — a rate only a runaway loop produces. The
    refused birth is NOT stamped, so a human retry after cleanup passes.
    """
    limit, window = _spawn_tuning()
    if limit <= 0:
        return
    f = paths.state_dir(Path(root)) / BIRTHS_FILE
    now = time.time()
    try:
        stamps = [float(x) for x in f.read_text(encoding="utf-8").split()]
    except (OSError, ValueError):
        stamps = []
    stamps = [t for t in stamps if now - t < window]
    if len(stamps) >= limit:
        raise StreamError(
            "arc spawn RUNAWAY: {n} arcs born in the last {w}s (limit {lim}) — "
            "that rate is a loop, not a human. STOP and escalate to the human; "
            "fill or sweep the drafts first ('tide arc gc'). If this volume is "
            "truly intended, raise ${env} for this run.".format(
                n=len(stamps), w=window, lim=limit, env=SPAWN_LIMIT_ENV
            )
        )
    stamps.append(now)
    f.parent.mkdir(parents=True, exist_ok=True)
    _io.atomic_write(f, "\n".join("{0:.3f}".format(t) for t in stamps) + "\n")


# --- create ----------------------------------------------------------------

def new_arc(root: Path, raw_slug: str, goal_slug: Optional[str] = None) -> Path:
    """Create a standalone arc ``NN-<slug>/`` (or a sub-arc under ``-g goal``).

    Builds the input/workspace/output triad + a templated ``arc.md`` and stamps
    the current canon-rev. Returns the new entry dir.
    """
    s = slug.slugify(raw_slug)
    if not s:
        raise StreamError("new arc: empty slug after slugify")
    # Between-arcs barrier (U7): no new arc while a closed arc's delta is unmerged.
    from .. import sync  # lazy: tide.sync imports this module at top.

    sync.block_new_arc_if_unmerged_delta(root)
    record_birth_and_guard(root)
    stream_dir = _open_goal_substream(root, goal_slug) if goal_slug else paths.arcs_dir(root)
    stream_dir.mkdir(parents=True, exist_ok=True)
    nn = numbering.next_num(stream_dir)
    entry = stream_dir / "{0}-{1}".format(nn, s)
    for sub in TRIAD:
        (entry / sub).mkdir(parents=True, exist_ok=True)
    _io.atomic_write(entry / "arc.md", templates.arc_md(entry.name))
    stamp_rev(entry, root)
    return entry


def _refuse_duplicate_container(root: Path, slug_s: str, *, kind: str, force: bool) -> None:
    """Refuse a new thread/routine when an OPEN one of the same slug already exists.

    The anti-mess gate for candidate 05 (``arc-spawn-runaway-empty-dups``): a
    spawner re-created the same ``@invite-codes`` routine / ``@kickoff`` thread over
    and over instead of reusing the live one, flooding the tree with empty dups.
    Refuse when an OPEN container of the same *kind* + *slug* already exists, and
    point the caller at REUSE (add a run/session) rather than a duplicate. *force*
    overrides for the rare legitimate second container.
    """
    if force:
        return
    existing = routine_entries(root) if kind == KIND_ROUTINE else thread_entries(root)
    for e in existing:
        if slug.entry_slug(e.name) == slug_s:
            run = "run" if kind == KIND_ROUTINE else "session"
            raise StreamError(
                "{0} '@{1}' already exists ({2}) — reuse it: add a {3} with "
                "`tide arc new-session <slug> -p {1}`, don't spawn a duplicate "
                "(pass --force only if you truly mean a second one).".format(
                    kind, slug_s, e.name, run
                )
            )


def new_thread(root: Path, raw_slug: str, *, force: bool = False,
               goal: Optional[str] = None) -> Path:
    """Create a thread ``NN-@<slug>/`` — a goal-shaped container of sessions.

    A thread (тред) is a durable work-line: it holds its sessions as sub-arcs in a
    nested ``arcs/`` (exactly like a goal), and is tagged ``kind: thread`` in its
    passport so the picker can tell threads from work-goals. Lives in the top
    stream. Stamps canon-rev. Sessions are added with :func:`new_session`. Refuses a
    duplicate of an OPEN same-slug thread (anti-mess gate); *force* overrides.

    *goal* fills the passport's ``goal:`` line at birth — a thread created for an
    immediate handoff offer must NOT be born a draft placeholder, or the picker
    hides it while the offer hangs inside (cand 28).
    """
    s = slug.slugify(raw_slug)
    if not s:
        raise StreamError("new thread: empty slug after slugify")
    _refuse_duplicate_container(root, s, kind=KIND_THREAD, force=force)
    record_birth_and_guard(root)
    arcs = paths.arcs_dir(root)
    arcs.mkdir(parents=True, exist_ok=True)
    nn = numbering.next_num(arcs)
    entry = arcs / "{0}-@{1}".format(nn, s)
    for sub in (*TRIAD, paths.ARCS_DIRNAME):
        (entry / sub).mkdir(parents=True, exist_ok=True)
    _io.atomic_write(entry / "{0}-goal.md".format(s), templates.thread_goal_md(s))
    if goal and goal.strip():
        fields.set_field(entry / "{0}-goal.md".format(s), "goal", goal.strip())
    stamp_rev(entry, root)
    return entry


def new_routine(root: Path, raw_slug: str, *, force: bool = False,
                goal: Optional[str] = None) -> Path:
    """Create a routine ``NN-@<slug>/`` — a goal-shaped container of runs.

    A routine (рутина) is a reusable procedure: it holds its **runs** as sub-arcs
    in a nested ``arcs/`` (exactly like a thread holds sessions), and is tagged
    ``kind: routine`` in its passport so the picker can tell routines from threads
    and work-goals. The passport carries the runbook (``## steps``) + accruing
    ``## experience``. Lives in the top stream. Stamps canon-rev. Runs are added
    with :func:`new_session` (a run IS a session inside the routine). Refuses a
    duplicate of an OPEN same-slug routine (anti-mess gate); *force* overrides.
    """
    s = slug.slugify(raw_slug)
    if not s:
        raise StreamError("new routine: empty slug after slugify")
    _refuse_duplicate_container(root, s, kind=KIND_ROUTINE, force=force)
    record_birth_and_guard(root)
    arcs = paths.arcs_dir(root)
    arcs.mkdir(parents=True, exist_ok=True)
    nn = numbering.next_num(arcs)
    entry = arcs / "{0}-@{1}".format(nn, s)
    for sub in (*TRIAD, paths.ARCS_DIRNAME):
        (entry / sub).mkdir(parents=True, exist_ok=True)
    _io.atomic_write(entry / "{0}-goal.md".format(s), templates.routine_md(s))
    if goal and goal.strip():
        fields.set_field(entry / "{0}-goal.md".format(s), "goal", goal.strip())
    stamp_rev(entry, root)
    return entry


def new_session(
    root: Path, thread_slug: str, raw_slug: str, from_ref: Optional[str] = None,
    goal: Optional[str] = None, claude_session: Optional[str] = None,
) -> Path:
    """Create a session ``NN-<slug>/`` inside *thread_slug*'s substream.

    A session is one orchestrator run within a thread. ``from:`` records its
    lineage so the picker shows how one session led to the next: *from_ref* sets
    it explicitly (branch/handoff fork from a chosen session); when omitted it
    chains to the thread's previous session. Carries a ``## cursor`` resume slot.
    Deliberately skips the unmerged-delta barrier — a session is not a work delta.
    The thread must be open.

    *claude_session* binds the head id at BIRTH — for the board-spark flow where claude
    is launched in the project root and creates its OWN session via this command, AFTER
    SessionStart already fired (so the start-hook had nothing to bind). The CLI handler
    passes ``$CLAUDE_CODE_SESSION_ID`` (the caller's own id); the board then sees the
    head immediately instead of leaving the nit stuck on 'launching' (cand 93-board-spark).

    The passport FLOOR is mechanics, not seed-text (cands 102/105): every session is
    born with a real ``title:`` (default ``<thread> · <slug>``) and — when the thread's
    own goal is live words — an inherited ``goal:``. A blind thread goal is fine (the
    thread may be a draft); the session then keeps its placeholder for the agent to
    fill. Living here means EVERY birth path (menu, spark, pickup, bare CLI) gets the
    floor without remembering to build it.
    """
    s = slug.slugify(raw_slug)
    if not s:
        raise StreamError("new session: empty slug after slugify")
    sub = _open_goal_substream(root, thread_slug)  # raises if the thread is closed/absent
    record_birth_and_guard(root)
    sub.mkdir(parents=True, exist_ok=True)
    if from_ref:
        from_slug = slug.entry_slug(from_ref) if "-" in from_ref else slug.slugify(from_ref)
    else:
        prev = last_session(root, thread_slug)
        from_slug = slug.entry_slug(prev.name) if prev is not None else None
    nn = numbering.next_num(sub)
    entry = sub / "{0}-{1}".format(nn, s)
    for t in TRIAD:
        (entry / t).mkdir(parents=True, exist_ok=True)
    _io.atomic_write(entry / "arc.md", templates.session_md(entry.name))
    if from_slug:
        fields.set_field(entry / "arc.md", "from", from_slug)
    session_goal = (goal or "").strip()
    if not session_goal:
        thread_entry = _find(paths.arcs_dir(root), thread_slug, goal=True, closed=False)
        if thread_entry is not None:
            thread_goal = fields.read_field(passport_path(thread_entry), "goal") or ""
            if not placeholders.is_blind_goal(thread_goal, slug.entry_slug(thread_entry.name)):
                session_goal = thread_goal.strip()
    if session_goal:
        fields.set_field(entry / "arc.md", "goal", session_goal)
    thread_s = slug.entry_slug(thread_slug) if "-" in thread_slug else slug.slugify(thread_slug)
    fields.set_field(entry / "arc.md", "title", "{0} · {1}".format(thread_s, s))
    if claude_session and claude_session.strip():
        fields.set_field(entry / "arc.md", "claude-session", claude_session.strip())
    stamp_rev(entry, root)
    return entry


def new_goal(root: Path, raw_slug: str) -> Path:
    """Create a goal ``NN-@<slug>/`` with the triad + nested ``arcs/`` + goal doc.

    Goals always live in the top stream (never inside another goal). Returns the
    new goal dir. Stamps canon-rev into the goal doc.
    """
    s = slug.slugify(raw_slug)
    if not s:
        raise StreamError("new goal: empty slug after slugify")
    record_birth_and_guard(root)
    arcs = paths.arcs_dir(root)
    arcs.mkdir(parents=True, exist_ok=True)
    nn = numbering.next_num(arcs)
    entry = arcs / "{0}-@{1}".format(nn, s)
    for sub in (*TRIAD, paths.ARCS_DIRNAME):
        (entry / sub).mkdir(parents=True, exist_ok=True)
    _io.atomic_write(entry / "{0}-goal.md".format(s), templates.goal_md(s))
    stamp_rev(entry, root)
    return entry


def set_goal(root: Path, ref: str, goal_text: str,
             *, thread_slug: Optional[str] = None, title: Optional[str] = None) -> Path:
    """Set an OPEN entry's ``goal:`` to REAL words — the start gate's setter (cand 81/87).

    Resolves *ref* as a top-stream thread/goal/arc, or (with *thread_slug*) a session
    inside that thread's substream, and rewrites its passport ``goal:`` line. REFUSES a
    blind goal — empty, a ``<…>`` placeholder, or just the entry's own slug — because
    the whole point of the gate is that the board shows a real purpose: re-stamping the
    slug as the goal is exactly the "there's no goal there" lie this closes. Returns the
    passport path written.

    *title* stamps ``title:`` in the same gesture (cand 105) — before it, the gate was
    half machine (goal via CLI) and half hand-edit (title), so on another session the
    title half simply never happened. A ``<…>`` placeholder title is refused the same
    way a blind goal is.
    """
    g = (goal_text or "").strip()
    if thread_slug:
        stream_dir = _open_goal_substream(root, thread_slug)  # raises if closed/absent
    else:
        stream_dir = paths.arcs_dir(root)
    entry = _resolve(stream_dir, ref, closed=False)
    if entry is None:
        where = "thread {0!r}".format(thread_slug) if thread_slug else "the stream"
        raise StreamError("set-goal: no open entry matching {0!r} in {1}".format(ref, where))
    if placeholders.is_blind_goal(g, slug.entry_slug(entry.name)):
        raise StreamError(
            "set-goal: {0!r} is not a real goal (empty, a <…> placeholder, or just the "
            "slug) — give the nit a purpose in plain words.".format(g)
        )
    pp = passport_path(entry)
    fields.set_field(pp, "goal", g)
    t = (title or "").strip()
    if t:
        if placeholders.find_in_text("title: " + t):
            raise StreamError(
                "set-goal: {0!r} is not a real title (a <…> placeholder) — "
                "give the picker human words.".format(t)
            )
        fields.set_field(pp, "title", t)
    return pp


# --- open / resume ---------------------------------------------------------

def open_arc(root: Path, ref: str, goal_slug: Optional[str] = None) -> Path:
    """Select an OPEN arc/goal as the active worker entry and stamp canon-rev.

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

    return _seal_entry(root, entry, force=force)


def _seal_entry(root: Path, entry: Path, *, force: bool) -> Path:
    """Land any worktree, mark ``status: done``, rename to ``__…__``. The sealing half
    of :func:`close` — extracted so :func:`close_thread` can seal a session directly
    (guards live in the callers). Returns the closed dir path.
    """
    # Worktree gate (11-arc-worktree-isolation): land the arc branch before sealing.
    # Gated so non-git projects and arcs without a branch are a pure no-op.
    from . import worktree as _wt  # lazy: avoid import cycle at module load
    if _wt.is_git_repo(root) and _wt.has_worktree(root, entry):
        if not force:
            result = _wt.land(root, entry)
            if result.conflict:
                raise StreamError(
                    "cannot close arc {0!r}: {1} "
                    "(resolve the conflict, then close)".format(entry.name, result.detail)
                )
            _wt.remove(root, entry)
        else:
            # force (supersede path) — discard the worktree without landing.
            _wt.remove(root, entry)

    fields.set_field(passport_path(entry), "status", "done")
    closed = entry.parent / "__{0}__".format(entry.name)
    entry.rename(closed)
    return closed


# A session whose last pulse is newer than this is LIVE — closing its thread must
# NOT seal it, or a working agent is buried under a done passport: the Mickey-17
# inverse (cand 79, caught live when closing 06-@operator killed the alive 01-frame).
# The pulse is the honest ``offloaded-at`` stamp, never mtime (unrelated edits bump
# mtime and would fake liveness — cand 88). Generous by design: sealing a live head
# is a real harm; leaving a quiet session for a hand-close is how sessions are meant
# to die anyway (silence / ✕), so we err toward NOT sealing.
LIVE_PULSE_SECONDS = 6 * 60 * 60  # 6h


def _pulse_age_seconds(session_dir: Path, *, now: Optional[float] = None) -> Optional[float]:
    """Seconds since a session's last offload pulse, or None if it never pulsed.

    Reads the ``offloaded-at`` ISO stamp — the honest liveness signal (cand 88:
    activity by pulses, not mtime). ``0`` / absent / unparseable ⇒ no pulse ⇒ None.
    """
    raw = (fields.read_field(session_dir / "arc.md", "offloaded-at") or "").strip()
    if not raw or raw == "0":
        return None
    try:
        ts = datetime.fromisoformat(raw).timestamp()
    except (ValueError, TypeError):
        return None
    now_ts = now if now is not None else datetime.now().timestamp()
    return max(0.0, now_ts - ts)


def _session_is_live(session_dir: Path, *, now: Optional[float] = None) -> bool:
    """True when a session pulsed within :data:`LIVE_PULSE_SECONDS` (cand 79)."""
    age = _pulse_age_seconds(session_dir, now=now)
    return age is not None and age < LIVE_PULSE_SECONDS


def close_thread(root: Path, ref: str, *, force: bool = False,
                 now: Optional[float] = None) -> "Dict[str, object]":
    """Close a whole container (thread OR routine): seal every DEAD nested
    session/run, then the container itself.

    ``close`` seals ONE entry, so closing a thread left its sessions ``[active]`` and
    the board reading ``0/N ✓`` on a done thread (cand 74, caught live by the greet
    dogfood). This cascades. A routine is the symmetric container (runs instead of
    sessions) and closes the same way — gating it out left дежурки unclosable from
    the desk (Гриша, live 14.07). Guards run on the CONTAINER first (empty
    ``output/`` + leftover placeholders, ``-f`` overrides) — it must still carry a
    self-contained result. Sessions then seal WITHOUT the output guard: a session is
    not a work delta (its work lives in ``workspace/``), so an empty ``output/`` is
    normal and must not block the nit's close.

    A session with a LIVE pulse is SKIPPED, never sealed — even under ``-f`` (cand 79):
    structure and attention are perpendicular axes, so the death of a nit must not bury
    a working head; a live session outlives its thread and closes by its own death
    (silence / ✕ by hand). Returns ``{thread, sessions (sealed), skipped_live}``.
    """
    stream_dir = paths.arcs_dir(root)
    entry = _resolve(stream_dir, ref, closed=False)
    if entry is None:
        raise StreamError(
            "open thread {0!r} not found in {1} (already closed?)".format(ref, stream_dir)
        )
    if not (is_thread(entry) or is_routine(entry)):
        raise StreamError(
            "{0!r} is not a thread/routine — use 'tide arc close' for a plain arc".format(ref)
        )
    if not force:
        if _output_empty(entry):
            raise StreamError(
                "thread {0!r} has an empty output/ — write the nit's result there first "
                "(a closed thread must carry a self-contained output). override: close -f".format(ref)
            )
        doc = passport_path(entry)
        leftovers = placeholders.find_in_file(doc)
        if leftovers:
            raise StreamError(placeholders.refuse_message(doc.name, ref, leftovers))

    sub = entry / paths.ARCS_DIRNAME
    open_sessions = (
        [e for e in sorted(_entries(sub), key=lambda p: p.name)
         if not slug.is_closed_entry(e.name)]
        if sub.is_dir() else []
    )
    # Live-pulse sessions survive the thread (cand 79) — seal the rest.
    live = [s for s in open_sessions if _session_is_live(s, now=now)]
    sealed_sessions = [
        _seal_entry(root, s, force=force).name for s in open_sessions if s not in live
    ]
    closed_thread = _seal_entry(root, entry, force=force)
    return {
        "thread": closed_thread.name,
        "sessions": sealed_sessions,
        "skipped_live": [s.name for s in live],
    }


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
    # F6 ordering fix: write "status: active" on the CURRENT (closed) passport
    # path BEFORE renaming.  A crash between the write and the rename leaves the
    # entry closed by name but with status=active — that is recoverable because
    # reopen resolves by closed=True name, finds the entry, and renames it on
    # retry.  Writing AFTER the rename risks a crash that leaves an open-named
    # dir with status=done, causing has_worktree and land to misread the state.
    fields.set_field(passport_path(entry), "status", "active")
    entry.rename(opened)
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
    of canon history, so deleting the arc would orphan a contribution the canon
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
      ``merged: yes`` delta folded into CANON.md; its source is canon history,
      so removal is refused outright (``-f`` does NOT override — reopen/supersede
      instead).
    * **referenced** — another entry's ``supersedes:`` names it; removing it would
      orphan that chain. Refused outright; remove the referrer first.
    * **non-empty output / nested sub-arcs** — auditable content; refused UNLESS
      *force* (the one guard ``-f`` overrides).

    Returns the removed dir path. The two integrity guards (merged / referenced)
    are deliberately not force-overridable so a single ``-f`` can't silently drop
    canon-anchored work — that path stays a manual ``rm -rf``.
    """
    stream_dir = _search_dir(root, goal_slug)
    entry = _resolve_present(stream_dir, ref)
    if entry is None:
        raise StreamError("arc/goal {0!r} not found in {1}".format(ref, stream_dir))

    if _subtree_has_merged_delta(entry):
        raise StreamError(
            "refuse to remove {0}: it carries a merged canon-delta (its work is "
            "part of canon history) — reopen/supersede instead of deleting".format(
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


def _cmd_new_thread(args) -> int:
    entry = new_thread(_root(), args.slug, force=getattr(args, "force", False),
                       goal=getattr(args, "goal_text", None))
    print("tide: created thread {0}".format(entry))
    return 0


def _cmd_new_routine(args) -> int:
    entry = new_routine(_root(), args.slug, force=getattr(args, "force", False),
                        goal=getattr(args, "goal_text", None))
    print("tide: created routine {0}".format(entry))
    return 0


def _sid_holds_some_session(root: Path, sid: str) -> bool:
    """True when *sid* is already pinned to ANY session arc across the stream.

    The dup-id guard's lookup (cand 103) — cross-thread on purpose: the collision
    that bites is an orchestrator creating a session for someone ELSE from its own
    terminal, and its own session lives in a DIFFERENT thread. Closed (``__``)
    containers count too — a sid's history is still its identity.
    """
    want = (sid or "").strip()
    if not want:
        return False
    try:
        for ap in paths.arcs_dir(root).glob("*/arcs/*/arc.md"):
            if (fields.read_field(ap, "claude-session") or "").strip() == want:
                return True
    except OSError:
        pass
    return False


def _cmd_new_session(args) -> int:
    # When claude itself runs this (the board-spark flow: the head starts, THEN creates
    # its own session), bind the caller's own session id at birth so the board sees the
    # head at once — no waiting for the first offload (cand 93-board-spark). Absent env
    # (a non-claude caller / test) → None → no stamp.
    root = _root()
    claude_session = os.environ.get("CLAUDE_CODE_SESSION_ID") or None
    # ДУБЛЬ-ID ГАРД (cand 103): штамп корректен только для self-register — когда claude
    # заводит СВОЮ первую сессию. Если этот id УЖЕ держит другую сессию — В ЛЮБОЙ нити,
    # не только этой (оркестратор заводит сессию для чужого подъёма из СВОЕЙ: e2e 14.07 —
    # гард по одной нити пропустил, спавн умер на «Session ID already in use») — НЕ
    # штампуем: иначе два арка на один claude-id, «вернуться» ведёт в один терминал,
    # доска не различает сессии, родословная ломается.
    if claude_session and _sid_holds_some_session(root, claude_session):
        claude_session = None
    entry = new_session(root, args.thread, args.slug,
                        from_ref=getattr(args, "from_ref", None),
                        goal=getattr(args, "goal_text", None),
                        claude_session=claude_session)
    print("tide: created session {0}".format(entry))
    return 0


def _cmd_open(args) -> int:
    entry = open_arc(_root(), args.slug, goal_slug=args.goal)
    print("tide: opened {0} (canon-rev stamped)".format(entry))
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

    # Closing by hand from the board arrives with the hand's extras: a one-line
    # result (a closed thread carries a self-sufficient output) and the retire of
    # the live head (dismissed: on sessions, so the trophy doesn't shine a stub).
    result = (getattr(args, "result", "") or "").strip()
    if arc_dir is not None and result:
        out_dir = arc_dir / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        _io.atomic_write(out_dir / "result.md",
                         "# итог нити (закрыта рукой с доски)\n\n{0}\n".format(result))
    if arc_dir is not None and getattr(args, "retire_head", False):
        from . import curate as _curate  # local: sibling domain module

        _curate.retire_sessions(arc_dir)

    # A container (thread/routine) closes as a WHOLE nit — cascade to its open
    # sessions/runs, else the board reads '0/N ✓' on a done container with sessions
    # still active (cand 74; routines joined 14.07 — дежурки were unclosable).
    if arc_dir is not None and args.goal is None and (is_thread(arc_dir) or is_routine(arc_dir)):
        summary = close_thread(root, args.slug, force=args.force)
        n = len(summary["sessions"])
        print("tide: closed thread {0} (status: done) + {1} session{2} sealed".format(
            summary["thread"], n, "" if n == 1 else "s"))
        live = summary.get("skipped_live") or []
        if live:
            print(
                "tide: ⚠ оставил {0} живую сессию открытой ({1}) — у неё свежий пульс, "
                "нить её не хоронит; закроется своей смертью (тишина/✕ рукой).".format(
                    len(live), ", ".join(live)),
                file=sys.stderr,
            )
        return 0

    closed = close(root, args.slug, goal_slug=args.goal, force=args.force)
    print("tide: closed {0} (status: done)".format(closed.name))
    return 0


def _cmd_set_goal(args) -> int:
    pp = set_goal(_root(), args.ref, args.goal_text,
                  thread_slug=getattr(args, "thread", None),
                  title=getattr(args, "title", None))
    print("tide: goal set → {0}".format(pp))
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

    tp = arc_subparsers.add_parser(
        "new-thread",
        aliases=["new-prism"],  # back-compat: 'thread' was once 'prism' (old sessions/muscle memory)
        help="create a thread NN-@<slug>/ (kind: thread — a container of sessions)",
    )
    tp.add_argument("slug")
    tp.add_argument("-f", "--force", action="store_true", help="allow a duplicate of an existing open same-slug thread")
    tp.add_argument("--goal", dest="goal_text", metavar="TEXT", help="fill the passport's goal: at birth (no draft placeholder — cand 28)")
    tp.set_defaults(func=_cmd_new_thread, _cmd="arc new-thread")

    rtp = arc_subparsers.add_parser("new-routine", help="create a routine NN-@<slug>/ (kind: routine — a reusable procedure whose runs are sessions)")
    rtp.add_argument("slug")
    rtp.add_argument("-f", "--force", action="store_true", help="allow a duplicate of an existing open same-slug routine")
    rtp.add_argument("--goal", dest="goal_text", metavar="TEXT", help="fill the passport's goal: at birth (steps still make it runnable or not)")
    rtp.set_defaults(func=_cmd_new_routine, _cmd="arc new-routine")

    snp = arc_subparsers.add_parser("new-session", help="create a session NN-<slug>/ inside a thread (-p thread), chained from the last (or --from)")
    snp.add_argument("slug")
    snp.add_argument("-p", "--thread", required=True, help="the thread (тред) to add the session to")
    snp.add_argument("--from", dest="from_ref", metavar="REF", help="fork lineage from this session (branch/handoff); default = previous session")
    snp.add_argument("--goal", dest="goal_text", metavar="TEXT", help="fill the session's goal: at birth")
    snp.set_defaults(func=_cmd_new_session, _cmd="arc new-session")

    sgp = arc_subparsers.add_parser(
        "set-goal",
        help="set an open nit's goal: to real words (start gate — refuses slug/placeholder; cand 81/87)",
    )
    sgp.add_argument("ref", help="thread/goal/arc slug (or a session slug with -p)")
    sgp.add_argument("goal_text", metavar="GOAL", help="the goal in plain words (≤12 words reads best)")
    sgp.add_argument("-p", "--thread", help="target a session INSIDE this thread's substream")
    sgp.add_argument("--title", metavar="TEXT", help="stamp title: in the same gesture — the whole start gate through one machine (cand 105)")
    sgp.set_defaults(func=_cmd_set_goal, _cmd="arc set-goal")

    op = arc_subparsers.add_parser("open", help="select an open arc as active (stamps canon-rev)")
    op.add_argument("slug")
    _add_goal_opt(op)
    op.set_defaults(func=_cmd_open, _cmd="arc open")

    rp = arc_subparsers.add_parser("resume", help="re-enter an open arc (re-stamp canon-rev)")
    rp.add_argument("slug")
    _add_goal_opt(rp)
    rp.set_defaults(func=_cmd_open, _cmd="arc resume")

    cp = arc_subparsers.add_parser("close", help="dual-mark done (__…__ + status:done), empty-output guard")
    cp.add_argument("slug")
    cp.add_argument("-f", "--force", action="store_true", help="skip the empty-output guard")
    cp.add_argument("--result", metavar="TEXT", help="one-line result by hand → output/result.md (board's ✓)")
    cp.add_argument("--retire-head", action="store_true", dest="retire_head",
                    help="dismissed: on the thread's live sessions before sealing (no head stub on a trophy)")
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

    # The human's hand-gestures (hold/dismiss/drop/validate) — domain ops the board
    # calls through the subprocess door instead of patching passports with regexes.
    from . import curate as _curate  # local: keep stream import-light at module load

    _curate.register_arc_verbs(arc_subparsers)

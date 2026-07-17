"""tide.resolve — THE slug→entry resolver (one matcher, four former copies).

Slug resolution used to live per-caller — ``stream._find``, launcher
``handoff.resolve_open_entry``, launcher ``seed._find_open_entry``,
``offload.find_session`` — each with its own matching. The copies drifted:
``seed`` still matched only the bare slug (the one-form trap of cand 43 that
sent agents to ``tide arc new`` and duplicated arcs), and every new surface had
to pick which copy to imitate. This module is the one place that knows how a
human-typed ref matches an on-disk entry; the former hosts delegate here.

The load-bearing rule (cand 43 + agent report 2026-07-07): a ref matches in
BOTH forms — the displayed entry name (``04-@slug``, peeled by
:func:`tide.slug.entry_slug`) and the bare slug (``slugify``, which keeps a
leading ``NN-`` that is genuinely part of the slug, e.g. ``01-mvp``).

Ambiguity across threads RAISES (:class:`AmbiguousRefError`) instead of
resolving to the first match — silently picking one wrote a pulse into a
stranger's passport in the wild (cand 85, data corruption).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Set

from . import paths, slug


class AmbiguousRefError(Exception):
    """A ref matches several entries and picking one silently would be a lie."""


def ref_forms(ref: str) -> Set[str]:
    """Both match-forms of a human-typed ref (bare slug + displayed-name slug)."""
    return {slug.slugify(ref), slug.entry_slug(ref)}


def entry_matches(ref: str, entry_name: str) -> bool:
    """True when *ref* names the on-disk *entry_name* (both-form matching)."""
    return slug.entry_slug(entry_name) in ref_forms(ref)


def child_entries(stream_dir: Path) -> List[Path]:
    """Child entry dirs of *stream_dir* (excludes the candidates/ backlog).

    Sorted by name so duplicate-slug resolution is deterministic (the former
    ``stream._entries`` used raw ``iterdir`` order — filesystem roulette).
    """
    if not Path(stream_dir).is_dir():
        return []
    return sorted(
        (p for p in Path(stream_dir).iterdir()
         if p.is_dir() and p.name != paths.CANDIDATES_DIRNAME),
        key=lambda p: p.name,
    )


def find_entry(stream_dir: Path, ref: str, *, goal: bool, closed: bool) -> Optional[Path]:
    """First entry in *stream_dir* matching *ref* and the goal/closed flags."""
    for p in child_entries(stream_dir):
        if not entry_matches(ref, p.name):
            continue
        if slug.is_goal_entry(p.name) != goal:
            continue
        if slug.is_closed_entry(p.name) != closed:
            continue
        return p
    return None


def resolve_entry(stream_dir: Path, ref: str, *, closed: bool) -> Optional[Path]:
    """Resolve an entry preferring the GOAL when *ref* names one, else the arc."""
    return (find_entry(stream_dir, ref, goal=True, closed=closed)
            or find_entry(stream_dir, ref, goal=False, closed=closed))


def open_top_entry(root: Path, ref: str) -> Optional[Path]:
    """First OPEN top-stream entry whose slug matches *ref* (goal preferred).

    The launcher's resolution (handoff anchors, seed passports): open entries
    only, the goal wins when a slug names both a goal and a plain arc.
    """
    return resolve_entry(paths.arcs_dir(Path(root)), ref, closed=False)


def open_session_dirs(root: Path) -> List[Path]:
    """Every OPEN nested session dir across all thread containers."""
    arcs = paths.arcs_dir(Path(root))
    out: List[Path] = []
    if not arcs.is_dir():
        return out
    for container in sorted(arcs.iterdir()):
        sub = container / paths.ARCS_DIRNAME
        if not container.is_dir() or slug.is_closed_entry(container.name) or not sub.is_dir():
            continue
        for entry in sorted(sub.iterdir()):
            if entry.is_dir() and slug.is_entry(entry.name) and not slug.is_closed_entry(entry.name):
                out.append(entry)
    return out


def closed_session_dirs(root: Path) -> List[Path]:
    """Every CLOSED session dir — закрытые сессии открытых нитей И все сессии
    закрытых нитей. Нужен sid-роутингу пульса (cand 109): чат живёт дольше своей
    арки, и его записи должны находить паспорт, а не падать в никуда."""
    arcs = paths.arcs_dir(Path(root))
    out: List[Path] = []
    if not arcs.is_dir():
        return out
    for container in sorted(arcs.iterdir()):
        sub = container / paths.ARCS_DIRNAME
        if not container.is_dir() or not sub.is_dir():
            continue
        container_closed = slug.is_closed_entry(container.name)
        for entry in sorted(sub.iterdir()):
            if not (entry.is_dir() and slug.is_entry(entry.name)):
                continue
            if container_closed or slug.is_closed_entry(entry.name):
                out.append(entry)
    return out


def find_session(root: Path, ref: str) -> Optional[Path]:
    """Resolve the open nested session *ref* names, or None if absent.

    *ref* forms: an exact dir name (``03-pickup`` — unique), a bare slug
    (``pickup``/``01-mvp``, cand 43), or a thread-qualified ``<thread>/<session>``.

    A bare slug shared by open sessions in MULTIPLE threads is AMBIGUOUS and
    RAISES :class:`AmbiguousRefError` with thread-qualified options (cand 85).
    Exact dir-name and thread-qualified refs are never ambiguous... except an
    exact dir name colliding across threads (01-work in two threads) — checked
    too.
    """
    dirs = open_session_dirs(root)

    # thread-qualified: '<thread>/<session>' (either part in dir-name or bare-slug form)
    parts = [p for p in str(ref).split("/") if p and p != paths.ARCS_DIRNAME]
    if len(parts) >= 2:
        for entry in dirs:
            thread = entry.parent.parent
            if (thread.name == parts[0] or entry_matches(parts[0], thread.name)) and \
               (entry.name == parts[1] or entry_matches(parts[1], entry.name)):
                return entry
        return None

    def _ambiguous(cands: List[Path]) -> AmbiguousRefError:
        opts = ", ".join("{0}/{1}".format(e.parent.parent.name, e.name) for e in cands)
        return AmbiguousRefError(
            "session {0!r} is ambiguous — {1} open sessions match across "
            "threads. Qualify it as <thread>/<session>: {2}".format(ref, len(cands), opts))

    exact = [e for e in dirs if e.name == ref]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise _ambiguous(exact)

    matches = [e for e in dirs if entry_matches(ref, e.name)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise _ambiguous(matches)
    return None

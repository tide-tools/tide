"""tide.arc.board — render the single STREAM board (``tide status`` / ``tide arc status``).

Ported from the arcs ``status`` renderer (architect ``arcs status``), retargeted to
``<project>/.tide/arcs/`` and extended with tide's two net-new flags. The board is
a *rendered* projection — never stored — of the work stream:

* **STREAM** — every top-level entry in numeric order. An arc is
  ``NN-slug  [status]  goal-line``; a goal is the same PLUS a computed ``(N/M ✓)``
  progress badge and its indented sub-arcs (``✓`` closed / ``○`` open).
* **N/M badge** — closed/total of the goal's on-disk sub-arcs (``__…__`` counts as
  closed); **never hand-ticked**. A goal with **zero** sub-arcs shows an EMPTY
  badge (no ``0/0``) — the badge only means something once there is a substream.
* **drift flag** — tide invention: an OPEN entry whose stamped ``cannon-rev``
  differs from the current one has drifted (cannon moved under it) and is flagged.
* **unmerged-delta flag** — tide invention: a CLOSED arc still carrying an
  unmerged ``delta.md`` is the between-arcs barrier offender (decision 9); listed
  so the orchestrator merges it through the gate.
* **CANDIDATES** — the separately-numbered backlog (``NN-slug  from <arc>``);
  ``from`` carries the surfaced-idea provenance (the origin arc, ``-`` when none).
* **HEALTH** — tide net-new (dogfood fix F4): an always-on merge-health footer
  showing the current ``cannon-rev``, the unmerged-delta count (with which arcs),
  and the drift line (``none`` or the drifted open arcs). Rendered even when
  clean — an explicit ``none`` beats ambiguous silence.

All rendering is pure (argparse-free, snapshot-testable); :func:`cmd_status` is the
thin CLI handler ``cli.py`` wires for both ``tide status`` and ``tide arc status``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import fields, paths, slug
from ..cannon import rev
from . import candidate, stream

# Numeric prefix of an entry dir (tolerates the closed ``__…__`` wrapper) — used to
# order open and closed entries on ONE continuous numeric axis (name-sort would put
# ``__03__`` after ``01`` because '_' > digits in ASCII).
_NUM_RE = re.compile(r"^_{0,2}(\d{2,})-")

TICK_CLOSED = "✓"
TICK_OPEN = "○"
BADGE_MARK = "✓"
DRIFT_FLAG = "⚠ drift"


# --- entry primitives ------------------------------------------------------

def _entry_num(name: str) -> int:
    """Numeric prefix of an entry dir name (0 when it has none) for stable sort."""
    m = _NUM_RE.match(name)
    return int(m.group(1), 10) if m else 0


def _stream_entries(stream_dir: Path) -> List[Path]:
    """Child entry dirs of *stream_dir* (excludes ``candidates/``), numeric order."""
    if not Path(stream_dir).is_dir():
        return []
    entries = [
        p
        for p in Path(stream_dir).iterdir()
        if p.is_dir() and p.name != paths.CANDIDATES_DIRNAME
    ]
    return sorted(entries, key=lambda p: (_entry_num(p.name), p.name))


def _field(entry_dir: Path, key: str) -> Optional[str]:
    """Read a passport field (goal doc if present, else arc.md) for an entry."""
    return fields.read_field(stream.passport_path(entry_dir), key)


def _status(entry_dir: Path) -> str:
    """The entry's ``status:`` (``done`` for a closed dir whose field is missing)."""
    s = _field(entry_dir, "status")
    if s:
        return s
    return "done" if slug.is_closed_entry(entry_dir.name) else "active"


def _goal_line(entry_dir: Path) -> str:
    """The entry's one-line ``goal:`` text (empty string when unset)."""
    return (_field(entry_dir, "goal") or "").strip()


def _supersedes_suffix(entry_dir: Path) -> str:
    """``(supersedes <x>)`` suffix when the entry carries a ``supersedes:`` link."""
    prev = _field(entry_dir, "supersedes")
    return "  (supersedes {0})".format(prev) if prev else ""


def _is_drifted(entry_dir: Path, current_rev: str) -> bool:
    """True for an OPEN entry whose stamped cannon-rev differs from *current_rev*.

    Closed entries are done (and may legitimately carry an older stamp), so drift
    is only flagged on still-open work — the case the sync barrier cares about. A
    never-stamped open entry is not drift (nothing to compare).
    """
    if slug.is_closed_entry(entry_dir.name):
        return False
    stamped = _field(entry_dir, "cannon-rev")
    return bool(stamped) and stamped != current_rev


def _drift_suffix(entry_dir: Path, current_rev: str) -> str:
    """Inline ``⚠ drift`` flag for an OPEN drifted entry (see :func:`_is_drifted`)."""
    return "  {0}".format(DRIFT_FLAG) if _is_drifted(entry_dir, current_rev) else ""


# --- goal badge ------------------------------------------------------------

def goal_badge(goal_dir: Path) -> Optional[Tuple[int, int]]:
    """Computed ``(closed, total)`` of a goal's sub-arcs, or None for zero sub-arcs.

    Counts the goal's ``arcs/`` substream entries; ``__…__`` ones are closed. A
    goal with NO sub-arcs returns None → an EMPTY badge (never ``0/0``), since the
    progress fraction is meaningless before a substream exists.
    """
    sub = Path(goal_dir) / paths.ARCS_DIRNAME
    if not sub.is_dir():
        return None
    entries = [p for p in sub.iterdir() if p.is_dir() and p.name != paths.CANDIDATES_DIRNAME]
    if not entries:
        return None
    closed = sum(1 for p in entries if slug.is_closed_entry(p.name))
    return (closed, len(entries))


def _badge_suffix(goal_dir: Path) -> str:
    """``  (N/M ✓)`` badge suffix for a goal, or ``""`` when it has no sub-arcs."""
    badge = goal_badge(goal_dir)
    if badge is None:
        return ""
    closed, total = badge
    return "  ({0}/{1} {2})".format(closed, total, BADGE_MARK)


# --- line rendering --------------------------------------------------------

def _top_line(entry_dir: Path, current_rev: str) -> str:
    """Render one top-level entry line (arc or goal, with badge/drift/supersedes)."""
    is_goal = slug.is_goal_entry(entry_dir.name)
    line = "  {name}  [{status}]".format(name=entry_dir.name, status=_status(entry_dir))
    goal_line = _goal_line(entry_dir)
    if goal_line:
        line += "  {0}".format(goal_line)
    if is_goal:
        line += _badge_suffix(entry_dir)
    line += _supersedes_suffix(entry_dir)
    line += _drift_suffix(entry_dir, current_rev)
    return line


def _sub_line(sub_dir: Path, current_rev: str) -> str:
    """Render one indented sub-arc line (``✓`` closed without status / ``○`` open)."""
    closed = slug.is_closed_entry(sub_dir.name)
    tick = TICK_CLOSED if closed else TICK_OPEN
    line = "    {tick} {name}".format(tick=tick, name=sub_dir.name)
    if not closed:
        line += "  [{0}]".format(_status(sub_dir))
    goal_line = _goal_line(sub_dir)
    if goal_line:
        line += "  {0}".format(goal_line)
    line += _supersedes_suffix(sub_dir)
    line += _drift_suffix(sub_dir, current_rev)
    return line


# --- merge-health footer (tide net-new, fix F4) ----------------------------

def _drifted_entries(root: Path, current_rev: str) -> List[Path]:
    """Every OPEN stream entry (top + goal sub-arcs) that has drifted, in order."""
    drifted: List[Path] = []
    for entry in _stream_entries(paths.arcs_dir(Path(root))):
        if _is_drifted(entry, current_rev):
            drifted.append(entry)
        if slug.is_goal_entry(entry.name):
            for sub in _stream_entries(entry / paths.ARCS_DIRNAME):
                if _is_drifted(sub, current_rev):
                    drifted.append(sub)
    return drifted


def _health_lines(root: Path, current_rev: str, offenders: List[Path]) -> List[str]:
    """The always-on HEALTH footer (fix F4): cannon-rev + unmerged + drift.

    Rendered even when everything is clean — an explicit ``none`` beats silence,
    which is ambiguous (clean vs. un-checked). *offenders* is the closed-arc
    unmerged-delta list already computed by :func:`render_board` (reused so the
    count and the ``UNMERGED DELTAS`` section can never disagree).
    """
    lines = ["HEALTH", "  cannon-rev: {0}".format(current_rev)]
    if offenders:
        names = ", ".join(o.name for o in offenders)
        lines.append("  unmerged: {0} delta(s) ({1})".format(len(offenders), names))
    else:
        lines.append("  unmerged: none")
    drifted = _drifted_entries(root, current_rev)
    if drifted:
        lines.append("  drift: {0}".format(", ".join(d.name for d in drifted)))
    else:
        lines.append("  drift: none")

    # Deferred-reconciliation debt (arc-land-strictness-dial): arcs landed `loose`
    # that owe a `strict` reconciliation. Surfaced here AND in the SessionStart
    # warnings so the head sees "канон отстал" on entry, with the one catch-up cmd.
    from .. import ledger  # lazy: ledger imports paths/slug only, no cycle

    debt = ledger.entries(root)
    if debt:
        names = ", ".join(e.arc for e in debt)
        lines.append(
            "  deferred: {0} arc(s) await strict reconciliation ({1})"
            " → tide reconcile".format(len(debt), names)
        )
    else:
        lines.append("  deferred: none")
    return lines


# --- whole-board render ----------------------------------------------------

def render_board(root: Path) -> str:
    """Render the full STREAM board for project *root* (pure, snapshot-testable)."""
    root = Path(root)
    current_rev = rev.compute(root)
    arcs = paths.arcs_dir(root)
    lines: List[str] = ["STREAM"]

    entries = _stream_entries(arcs)
    if not entries:
        lines.append("  (empty stream)")
    for entry in entries:
        lines.append(_top_line(entry, current_rev))
        if slug.is_goal_entry(entry.name):
            for sub in _stream_entries(entry / paths.ARCS_DIRNAME):
                lines.append(_sub_line(sub, current_rev))

    # tide net-new: between-arcs barrier offenders (closed arcs w/ unmerged delta).
    from .. import sync  # lazy: sync imports arc.stream at top, not arc.board.

    offenders = sync.unmerged_deltas(root)
    if offenders:
        lines.append("")
        lines.append("UNMERGED DELTAS")
        for off in offenders:
            lines.append(
                "  ! {name}  → tide cannon merge {slug}".format(
                    name=off.name, slug=slug.entry_slug(off.name)
                )
            )

    # CANDIDATES backlog (separately numbered); ``from`` = surfaced-idea provenance.
    cands = candidate.list_candidates(root)
    if cands:
        lines.append("")
        lines.append("CANDIDATES")
        for c in cands:
            lines.append("  {stem}  from {origin}".format(stem=c["stem"], origin=c["from"] or "-"))

    # tide net-new (fix F4): always-on merge-health footer — explicit even at zero.
    lines.append("")
    lines.extend(_health_lines(root, current_rev, offenders))

    return "\n".join(lines)


# --- compact on-entry summary (consumed by launcher.context) ---------------

def open_entries(root: Path) -> List[Path]:
    """Open (not-closed) top-level stream entries of *root*, numeric order.

    "Open" = not wrapped in the ``__…__`` closed marker. Goals and plain arcs alike
    are returned (a goal is open until its dir is closed). This is the work a fresh
    session should know about on entry — distinct from the full board, which also
    renders closed history and health.
    """
    entries = _stream_entries(paths.arcs_dir(Path(root)))
    return [e for e in entries if not slug.is_closed_entry(e.name)]


def open_questions(root: Path) -> List[Tuple[Path, str]]:
    """Unanswered contract asks across all open entries — ``(arc_dir, question_line)``.

    Each open arc may carry a ``asks/NN-slug.md`` durable open-question; an ask whose
    ``state:`` is not ``answered`` is still waiting on the human/orchestrator. These
    are the project's open *questions* (the third thing a session entering should
    see, alongside open arcs and candidates). Pure read of the on-disk records.
    """
    from ..contract import model  # lazy: contract imports arc, avoid a cycle

    out: List[Tuple[Path, str]] = []
    for entry in open_entries(root):
        adir = model.asks_dir(entry)
        if not adir.is_dir():
            continue
        for p in sorted(adir.glob("*.md")):
            state = (fields.read_field(p, "state") or "open").strip()
            if state == "answered":
                continue
            out.append((entry, p.stem))
    return out


def render_entry_summary(root: Path) -> str:
    """A compact ``open arcs`` + ``candidates`` + ``open questions`` on-entry block.

    Pure projection of ``.tide/arcs/`` — open entries (with their kind + goal line),
    the candidate backlog, and unanswered contract asks — kept deliberately terse so
    a session entering a project sees *what is live here* at a glance. Empty states
    say ``none`` (never silence). Reused by :func:`tide.launcher.context.render_enter`.
    """
    root = Path(root)
    lines: List[str] = []

    opens = open_entries(root)
    if opens:
        lines.append("open arcs ({0}):".format(len(opens)))
        for e in opens:
            kind = "goal" if slug.is_goal_entry(e.name) else "arc"
            goal_line = _goal_line(e)
            line = "  {name}  [{kind}]".format(name=e.name, kind=kind)
            if goal_line:
                line += "  {0}".format(goal_line)
            lines.append(line)
    else:
        lines.append("open arcs: none")

    cands = candidate.list_candidates(root)
    if cands:
        lines.append("candidates ({0}):".format(len(cands)))
        for c in cands:
            lines.append("  {stem}  ← from {origin}".format(stem=c["stem"], origin=c["from"] or "-"))
    else:
        lines.append("candidates: none")

    asks = open_questions(root)
    if asks:
        lines.append("open questions ({0}):".format(len(asks)))
        for entry, stem in asks:
            lines.append("  {stem}  (on {arc})".format(stem=stem, arc=entry.name))
    else:
        lines.append("open questions: none")

    return "\n".join(lines)


# --- JSON projection (same data render_board computes; for canon/drift dashboards) --

def _entry_dict(entry_dir: Path, current_rev: str, *, include_sub: bool) -> Dict[str, object]:
    """Pure dict projection of one stream entry — the SAME fields the line renderers read.

    Mirrors :func:`_top_line` (top entries, ``include_sub=True`` → badge + ``sub_arcs``)
    and :func:`_sub_line` (sub-arcs, ``include_sub=False`` → no badge/substream, exactly
    as the renderer descends only one level). No FS scan is re-derived: every field
    routes through the same ``_status``/``_goal_line``/``_field``/``goal_badge``/
    ``_is_drifted`` helpers the text board uses.
    """
    name = entry_dir.name
    is_goal = slug.is_goal_entry(name)
    badge = goal_badge(entry_dir) if (is_goal and include_sub) else None
    sub_arcs = (
        [
            _entry_dict(sub, current_rev, include_sub=False)
            for sub in _stream_entries(entry_dir / paths.ARCS_DIRNAME)
        ]
        if (is_goal and include_sub)
        else []
    )
    return {
        "name": name,
        "status": _status(entry_dir),
        "goal": _goal_line(entry_dir),
        "is_goal": is_goal,
        "is_closed": slug.is_closed_entry(name),
        "cannon_rev_stamped": _field(entry_dir, "cannon-rev"),
        "drifted": _is_drifted(entry_dir, current_rev),
        "supersedes": _field(entry_dir, "supersedes"),
        "badge": {"closed": badge[0], "total": badge[1]} if badge else None,
        "sub_arcs": sub_arcs,
    }


def project_status_dict(root: Path) -> Dict[str, object]:
    """Structured projection of one project's STREAM board (canon/drift/unmerged/health).

    The machine-readable twin of :func:`render_board`: same data source, same helpers,
    same ``offenders`` reuse (so the dict and the text board can never disagree). Pure.
    """
    from .. import sync  # lazy: sync imports arc.stream at top, not arc.board.

    root = Path(root)
    current_rev = rev.compute(root)
    offenders = sync.unmerged_deltas(root)
    drifted = _drifted_entries(root, current_rev)
    return {
        "name": root.name,
        "path": str(root),
        "cannon_rev": current_rev,
        "stream": [
            _entry_dict(e, current_rev, include_sub=True)
            for e in _stream_entries(paths.arcs_dir(root))
        ],
        "unmerged_deltas": [
            {"name": o.name, "slug": slug.entry_slug(o.name)} for o in offenders
        ],
        "candidates": [
            {"stem": c["stem"], "from": c["from"]} for c in candidate.list_candidates(root)
        ],
        "health": {
            "cannon_rev": current_rev,
            "unmerged_count": len(offenders),
            "unmerged_arcs": [o.name for o in offenders],
            "drifted_entries": [d.name for d in drifted],
        },
    }


def all_status_dict(control_home: Path) -> List[Dict[str, object]]:
    """Roster-wide list of :func:`project_status_dict`, one entry per registered project.

    Mirrors :func:`_render_all`: non-``.tide`` projects yield ``{tide_project: False}``;
    tide projects carry the full dict, with name/path overridden from the roster line.
    """
    from .. import roster

    control_home = Path(control_home)
    out: List[Dict[str, object]] = []
    for entry in roster.read_roster(control_home):
        proj = Path(entry["path"]).expanduser()
        if not paths.tide_dir(proj).is_dir():
            out.append({"name": entry["name"], "path": entry["path"], "tide_project": False})
            continue
        out.append(
            {
                **project_status_dict(proj),
                "name": entry["name"],
                "path": entry["path"],
                "tide_project": True,
            }
        )
    return out


# --- CLI handler -----------------------------------------------------------

def _render_all(root: Path) -> str:
    """Roster-wide STREAM boards from a control-home (one block per project)."""
    from .. import roster

    blocks: List[str] = []
    for entry in roster.read_roster(root):
        proj = Path(entry["path"]).expanduser()
        header = "=== {name}  ({path}) ===".format(name=entry["name"], path=entry["path"])
        if not paths.tide_dir(proj).is_dir():
            blocks.append("{0}\n  (no .tide/ — not a tide project)".format(header))
            continue
        blocks.append("{0}\n{1}".format(header, render_board(proj)))
    if not blocks:
        return "(roster is empty)"
    return "\n\n".join(blocks)


def cmd_status(args) -> int:
    """Print the STREAM board (``tide status`` / ``tide arc status``); ``--all`` = roster-wide."""
    root = paths.require_tide_root()
    if getattr(args, "json", False):
        data = all_status_dict(root) if getattr(args, "all", False) else project_status_dict(root)
        print(json.dumps(data, default=str, indent=2))
        return 0
    if getattr(args, "all", False):
        print(_render_all(root))
    else:
        print(render_board(root))
    return 0

"""tide.launcher.handoff — the ``/tide-handoff`` engine (warm chat → fresh session).

The handoff turns a bloated live chat into a clean continuation. ONE path into
the loop (cand 05 consolidated the CLI): distil the chat, then hang an offer in
the control-home queue — the same queue ``tide handoffs`` manages and the
``/handoff`` skill drives. Three things, in order:

1. **distill → workspace** — write the conversation summary into the arc's
   ``workspace/handoff-<date>.md`` (handoff is *continuation*, not an ending, so
   it lands in ``workspace/`` — never ``output/``, which is reserved for the arc's
   durable finish).
2. **remind candidates** — surface the candidates backlog so anything worth
   keeping for canon/method gets dropped via ``tide candidate add`` before the
   chat is abandoned.
3. **hang the offer** — ``continue`` (resume THIS arc) / ``new`` (fresh
   orchestrator to pick a candidate) land a record in the queue
   (:mod:`tide.handoff_queue`); ``close`` just distils. This command NEVER opens
   a terminal — the fresh session is pulled from ``tide menu`` (offer → take),
   which is what keeps one holder per thread (no Mickey-17 multiples).

Two layers as everywhere else: pure functions (``build_summary`` …) are argparse-
and disk-free and snapshot-testable; :func:`run_handoff` is the disk
orchestration; :func:`cmd_handoff` is the thin CLI handler wired by ``cli.py``.
"""

from __future__ import annotations

import argparse

from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import List, Optional

from .. import io as _io, paths, slug
from ..arc import candidate
from ..arc.stream import StreamError

WORKSPACE_DIRNAME = "workspace"
SUMMARY_PREFIX = "handoff-"

FORK_CONTINUE = "continue"
FORK_NEW = "new"
FORK_CLOSE = "close"
FORK_MODES = (FORK_CONTINUE, FORK_NEW, FORK_CLOSE)


class HandoffError(StreamError):
    """A user-facing handoff error (unknown mode, no such open arc …).

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on
    the same ``except`` arm (prints ``tide: …``, exits nonzero).
    """


# --- summary assembly (pure) -----------------------------------------------

def _today(date: Optional[str]) -> str:
    return date or _date.today().isoformat()


def _roster_project_name(home: Path, owner_root: Path) -> str:
    """The ROSTER name for *owner_root*, falling back to its dir name.

    The offer record must carry the roster name: pickup resolves the project
    through the roster, so a dev dir-name alias dies there — offered as
    ``ai-hot``, rostered as ``x`` (cand 17). Path-match the roster first; the
    dir name is only the last resort for a project the roster doesn't know.
    """
    from .. import roster as _roster
    try:
        want = Path(owner_root).expanduser().resolve()
        for e in _roster.read_roster(Path(home)):
            if Path(e.get("path", "")).expanduser().resolve() == want:
                return e["name"]
    except OSError:
        pass
    return Path(owner_root).name


def summary_filename(date: Optional[str] = None) -> str:
    """The workspace filename for a handoff distil (``handoff-<date>.md``)."""
    return "{0}{1}.md".format(SUMMARY_PREFIX, _today(date))


def _bullets(title: str, items: Optional[List[str]]) -> List[str]:
    """A ``## title`` section rendered as a bullet list, or [] when empty."""
    kept = [i.strip() for i in (items or []) if i and i.strip()]
    if not kept:
        return []
    return ["", "## {0}".format(title), *["- {0}".format(i) for i in kept]]


def build_summary(
    *,
    mode: str,
    arc_ref: str,
    state: str = "",
    decisions: Optional[List[str]] = None,
    artifacts: Optional[List[str]] = None,
    next_step: str = "",
    open_questions: Optional[List[str]] = None,
    date: Optional[str] = None,
) -> str:
    """Assemble the handoff distil markdown from already-extracted pieces (pure).

    Sections, in order: a frontmatter-ish header (mode/arc/date), **Where we are**
    (always present — placeholder when empty), then **Decisions**, **Artifacts**,
    **Next step**, and **Open questions** (each omitted when empty). The shape is
    fixed so a fresh session — and the snapshot tests — can rely on it.
    """
    lines: List[str] = [
        "# tide handoff — {0}".format(arc_ref),
        "",
        "mode: {0}".format(mode),
        "arc: {0}".format(arc_ref),
        "date: {0}".format(_today(date)),
        "",
        "## Where we are",
        state.strip() if state.strip() else "(state not distilled — fill before spawning)",
    ]
    lines += _bullets("Decisions", decisions)
    lines += _bullets("Artifacts", artifacts)
    if next_step.strip():
        lines += ["", "## Next step", next_step.strip()]
    lines += _bullets("Open questions", open_questions)
    return "\n".join(lines) + "\n"


# --- arc resolution + workspace write --------------------------------------

def resolve_open_entry(root: Path, arc_ref: str) -> Optional[Path]:
    """First OPEN top-stream entry whose slug matches *arc_ref* (goal preferred).

    *arc_ref* is normalised with :func:`slug.entry_slug` (not bare ``slugify``) so
    the entry name that ``tide status`` prints — ``04-@ai-hot-companion``, prefix
    and ``@`` and all — matches the same as the bare slug ``ai-hot-companion``.
    (``slugify`` keeps the ``NN-`` prefix, so it silently missed the displayed
    name — the trap that sent agents to ``tide arc new`` and duplicated arcs.)
    """
    arcs = paths.arcs_dir(root)
    if not arcs.is_dir():
        return None
    wants = {slug.slugify(arc_ref), slug.entry_slug(arc_ref)}
    matches = [
        p
        for p in arcs.iterdir()
        if p.is_dir()
        and p.name != paths.CANDIDATES_DIRNAME
        and not slug.is_closed_entry(p.name)
        and slug.entry_slug(p.name) in wants
    ]
    if not matches:
        return None
    matches.sort(key=lambda p: (not slug.is_goal_entry(p.name), p.name))
    return matches[0]


def _open_arc_slugs(root: Path) -> List[str]:
    """Slugs of OPEN top-stream entries — the valid handoff anchors (for hints)."""
    arcs = paths.arcs_dir(root)
    if not arcs.is_dir():
        return []
    return sorted(
        slug.entry_slug(p.name)
        for p in arcs.iterdir()
        if p.is_dir()
        and p.name != paths.CANDIDATES_DIRNAME
        and not slug.is_closed_entry(p.name)
    )


def write_summary(
    root: Path, arc_ref: str, summary: str, *, date: Optional[str] = None
) -> Path:
    """Write *summary* into the open arc's ``workspace/handoff-<date>.md``.

    Raises :class:`HandoffError` when *arc_ref* names no open arc — a handoff must
    anchor to a real arc (so the fresh session has somewhere to continue from).
    """
    entry = resolve_open_entry(root, arc_ref)
    if entry is None:
        raise HandoffError(
            "handoff: no open top-stream arc matching {0!r}. Anchor the handoff "
            "on an OPEN thread/goal (a session slug like '09-09-…' is NOT a "
            "top-stream arc). Open now: {1}".format(
                arc_ref, ", ".join(_open_arc_slugs(root)) or "(none open)"
            )
        )
    ws = entry / WORKSPACE_DIRNAME
    ws.mkdir(parents=True, exist_ok=True)
    path = ws / summary_filename(date)
    _io.atomic_write(path, summary)
    return path


# --- candidate reminder + fork offer (pure-ish) ----------------------------

def candidate_reminder(root: Path) -> str:
    """A reminder block listing the candidates backlog before the chat is dropped."""
    backlog = candidate.render_list(root)
    return (
        "Candidates backlog (drop anything worth keeping with "
        "'tide candidate add <slug>'):\n{0}".format(backlog)
    )


# --- orchestration ----------------------------------------------------------

@dataclass
class HandoffResult:
    """Outcome of a handoff: where the distil landed + the offer hung in the queue.

    * ``mode`` — the chosen fork (continue / new / close).
    * ``summary_path`` — the workspace file the distil was written to.
    * ``candidate_reminder`` — the backlog reminder text (always computed).
    * ``offer_path`` — the queue record (None for ``close`` and ``dry_run``).
    """

    mode: str
    summary_path: Path
    candidate_reminder: str
    offer_path: Optional[Path] = None
    notes: List[str] = field(default_factory=list)


def _with_throughline(thread_entry: Path, session_born: Path, distil: str) -> str:
    """Prepend the thread's THROUGHLINE to a seed so the idea survives the chain.

    A seed lands the ONE next step — deliberately, not a survey. But across a chain
    of handoffs (A→B→C…) each session then sees only its local step, and the ORIGINAL
    idea erodes: proved by a 2-hop probe where neither seed carried the thread goal,
    while it sat intact on disk (the same agent-memory dependency we mechanized away
    for the reception seam). The offering agent is *told* to write a "нить двумя шагами
    вперёд" line — but told, not enforced. So we stamp it mechanically: every thread
    seed opens with where the nit is HEADED (thread goal) and which session it
    CONTINUES (lineage), above the agent's distil (which still leads with the step).

    Best-effort: no goal / any read error ⇒ return the distil unchanged (the header is
    a safety net, never a new way to break a handoff).
    """
    try:
        from .. import fields, placeholders as _ph
        from ..arc import stream as _stream
        # A REAL goal only. An auto goal (goal == the thread's own slug/tag, e.g.
        # goal:=debug_deck on thread 01-@debug-deck) is not a goal — printing
        # "идёт к: debug_deck" is noise that reads as a lie (cand 83). Same filter the
        # board uses: empty / placeholder / equals-slug ⇒ no goal line.
        raw = (fields.read_field(_stream.passport_path(thread_entry), "goal") or "").strip()
        tslug = slug.entry_slug(thread_entry.name)
        goal = "" if _ph.is_blind_goal(raw, tslug) else raw

        from_slug = (fields.read_field(session_born / "arc.md", "from") or "").strip()
        # Three iron rules so a pickup needn't round-trip into the tide-flow skill —
        # the seed already covers ~90% (cand 84).
        rules = ("правила: работа → в workspace/ своей арки · "
                 "пульс по ходу → tide offload · передать дальше → /handoff")
        lines = ["## нить (throughline — держи за шагом)"]
        if goal:
            lines.append("идёт к: {0}".format(goal))
        if from_slug and from_slug != "-":
            lines.append("продолжаешь сессию: {0}".format(from_slug))
        lines.append(rules)
        return "{0}\n---\n\n{1}".format("\n".join(lines), distil)
    except Exception:  # noqa: BLE001  the header must never break a handoff
        return distil


def _unique_pickup_slug(thread_entry: Path, base: str = "pickup") -> str:
    """A pickup slug UNIQUE within the thread, so no two sessions share a slug.

    Every handoff naming its session ``pickup`` made the slug ambiguous: slug-based
    resolution (``offload``) and the lineage ``from:`` pointer could no longer tell
    the pickups apart — a live 6-hop dogfood landed a fresh session's pulse on an
    older sibling (cand 66/78). Numbering the DIR (``NN-``) isn't enough; the SLUG
    must differ. Returns ``pickup``, then ``pickup-2``, ``pickup-3``, … .
    """
    arcs = thread_entry / "arcs"
    existing = set()
    if arcs.is_dir():
        existing = {slug.entry_slug(d.name) for d in arcs.iterdir() if d.is_dir()}
    if base not in existing:
        return base
    n = 2
    while "{0}-{1}".format(base, n) in existing:
        n += 1
    return "{0}-{1}".format(base, n)


def _pickup_goal(thread_entry: Path, distil: str) -> Optional[str]:
    """A real goal for the born pickup session — never the template placeholder (cand 84).

    A pickup born with no goal shows ``<one line — what this session is for>`` on the
    board, so ``tide status`` can't orient — the seed carries all the meaning while the
    board reads empty. Derive it: prefer the distil's stated NEXT STEP (the session's
    actual job), else fall back to the thread goal. Best-effort ⇒ None keeps the old
    placeholder rather than breaking the handoff.
    """
    for line in (distil or "").splitlines():
        low = line.lower()
        if "следующий шаг" in low or "next step" in low:
            g = line.split(":", 1)[1] if ":" in line else line
            g = g.strip().strip("*#-— ").strip()
            if g:
                return g[:120]
    try:
        from .. import fields
        from ..arc import stream as _stream
        goal = (fields.read_field(_stream.passport_path(thread_entry), "goal") or "").strip()
        return goal or None
    except Exception:  # noqa: BLE001
        return None


def _new_fork_plan_preamble(thread_entry: Path) -> str:
    """First-step banner for a NEW-thread handoff: build the nit's plan (cand 86).

    A spark seed bakes in law 47 (plan the nit by waves); a handoff-``new`` seed did
    not — so a fresh thread could be born planless, and a planless nit has NO close
    path (the board's ✓ gate needs a plan with every wave ``[x]``). Make building
    ``plan.md`` the explicit first step so the thread is closeable from birth.
    """
    return (
        "## первый шаг — ПЛАН НИТИ (закон 47)\n"
        "Это НОВАЯ нить. ПЕРВЫМ ДЕЛОМ построй `{0}/plan.md` шагами/волнами (что и в "
        "каком порядке до цели), и только потом выполняй. Без плана нить нельзя "
        "закрыть — кнопка ✓ на доске гейтится планом со всеми шагами [x].\n\n"
    ).format(thread_entry.name)


def _thread_origin_session(stream_mod, owner_root: Path, thread_slug: str) -> Optional[str]:
    """The ``claude-session`` id of the thread's current holder (newest session), or None.

    Auto-fills the offer's origin id for the multiples detector when the caller
    didn't pass ``--from-session`` — the 'one holder per thread' guard must not go
    blind just because a flag was forgotten (cand 78). Read BEFORE the pickup is born.
    """
    try:
        from .. import fields
        prev = stream_mod.last_session(owner_root, thread_slug)
        if prev is None:
            return None
        sid = (fields.read_field(prev / "arc.md", "claude-session") or "").strip()
        return sid or None
    except Exception:  # noqa: BLE001  best-effort — never break a handoff over lineage
        return None


def run_handoff(
    root: Path,
    *,
    arc_ref: str,
    mode: str = FORK_CONTINUE,
    summary: Optional[str] = None,
    from_session: Optional[str] = None,
    dry_run: bool = False,
    date: Optional[str] = None,
) -> HandoffResult:
    """Run a handoff: distil → workspace, remind candidates, hang the offer.

    *summary* is the distilled markdown (the caller prepares it); when absent a
    minimal stub is built from *mode*/*arc_ref* so the call never silently writes
    nothing. For ``continue``/``new`` the offer lands in the control-home queue
    (:func:`tide.handoff_queue.offer`) and is picked up from ``tide menu`` /
    ``tide handoffs take`` — this function NEVER opens a terminal (pull model;
    one holder per thread). ``close`` just distils. ``dry_run`` distils but
    leaves the queue untouched.
    """
    root = Path(root)
    if mode not in FORK_MODES:
        raise HandoffError(
            "handoff: unknown mode {0!r} — pick one of {1}".format(
                mode, ", ".join(FORK_MODES)
            )
        )

    # Resolve which project owns the arc (cwd-project first, then the roster) so a
    # handoff fired from the control-home anchors to the RIGHT project's arc, and
    # the distil lands in that project's arc workspace — not the control-home root.
    from ..arc import worktree
    owner_root, _arc_entry = worktree.resolve_project_and_arc(root, arc_ref)

    text = summary if summary is not None else build_summary(
        mode=mode, arc_ref=arc_ref, date=date
    )

    # Fix B (cands 38 + agent report 2026-07-07): a live continue/new handoff
    # into a THREAD must anchor on a real SESSION — the menu surfaces pickups
    # only through ``<thread>/<session>`` (+seed in the session's input/), so a
    # thread-anchored offer is INVISIBLE (the bite that stranded ai-hot and
    # design). Create the pickup session here, land the distil as its seed.
    # ``close`` and ``dry_run`` stay side-effect-light (workspace distil only);
    # a plain arc (no thread) keeps the legacy anchoring.
    offer_arc = arc_ref
    session_born = None
    if mode in (FORK_CONTINUE, FORK_NEW) and not dry_run:
        from ..arc import stream as _stream
        entry = resolve_open_entry(owner_root, arc_ref)
        if entry is not None and _stream.is_thread(entry):
            tslug = slug.entry_slug(entry.name)
            # Origin id for the multiples detector: auto-fill from the thread's
            # current holder when the caller didn't pass it (cand 78). Explicit
            # --from-session still wins. Read BEFORE the pickup becomes the newest.
            if not from_session:
                from_session = _thread_origin_session(_stream, owner_root, tslug)
            # Name the pickup after the THREAD (03-debug-deck), not a generic 'pickup'
            # (cand 83): readable on the board, still unique via -N. The dir NN keeps
            # siblings apart; the slug now carries meaning instead of noise.
            session_born = _stream.new_session(
                owner_root, tslug, _unique_pickup_slug(entry, base=tslug),
                goal=_pickup_goal(entry, text),
            )
            summary_path = session_born / "input" / "handoff-seed.md"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            seed_text = text
            if mode == FORK_NEW:
                seed_text = _new_fork_plan_preamble(entry) + text
            _io.atomic_write(summary_path, _with_throughline(entry, session_born, seed_text))
            offer_arc = "{0}/{1}".format(entry.name, session_born.name)
    if session_born is None:
        summary_path = write_summary(owner_root, arc_ref, text, date=date)

    result = HandoffResult(
        mode=mode,
        summary_path=summary_path,
        candidate_reminder=candidate_reminder(root),
    )
    if session_born is not None:
        result.notes.append(
            "session born for pickup: {0} (seed in its input/)".format(offer_arc)
        )

    if mode == FORK_CLOSE:
        result.notes.append("close: thread distilled to {0}; no offer".format(summary_path))
        return result
    if dry_run:
        result.notes.append("dry-run: distil written; queue untouched")
        return result

    from .. import handoff_queue  # lazy: keep module import-light
    try:
        home = paths.control_home(root)
    except FileNotFoundError as exc:
        raise HandoffError(
            "handoff: no control-home for the offer queue — {0}".format(exc)
        ) from exc
    result.offer_path = handoff_queue.offer(
        home,
        arc_ref,
        arc=offer_arc,
        project=_roster_project_name(home, owner_root),
        seed=str(summary_path),
        mode=mode,
        from_session=from_session,
    )
    result.notes.append(
        "offer hung: {0} — pick it up from 'tide menu' "
        "(or 'tide handoffs take {1}')".format(
            result.offer_path.name, result.offer_path.stem
        )
    )
    return result


# --- CLI wiring ------------------------------------------------------------

def _read_summary_arg(args) -> Optional[str]:
    """Resolve the distil text from ``--summary-file`` (None ⇒ build a stub)."""
    sf = getattr(args, "summary_file", None)
    if not sf:
        return None
    p = Path(sf).expanduser()
    if not p.is_file():
        raise HandoffError("handoff: --summary-file {0} not found".format(p))
    return p.read_text(encoding="utf-8")


def cmd_handoff(args) -> int:
    """``tide handoff <arc>`` — distil to workspace, remind, hang the offer."""
    root = paths.require_tide_root()
    summary = _read_summary_arg(args)
    retired = [
        "--{0}".format(flag.replace("_", "-"))
        for flag in ("no_spawn", "adapter")
        if getattr(args, flag, None)
    ]
    result = run_handoff(
        root,
        arc_ref=args.arc,
        mode=getattr(args, "mode", None) or FORK_CONTINUE,
        summary=summary,
        from_session=getattr(args, "from_session", None),
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    print("tide: handoff [{0}] → {1}".format(result.mode, result.summary_path))
    print(result.candidate_reminder)
    for flag in retired:
        print(
            "  note: {0} is retired — handoff never spawns; the queue "
            "('tide handoffs') is the one path in".format(flag)
        )
    for note in result.notes:
        print("  {0}".format(note))
    return 0


def register(subparsers) -> None:
    """Add the ``handoff`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "handoff",
        help="warm-handoff: distil chat → arc workspace, hang an offer in the queue",
    )
    p.add_argument("arc", help="the open arc to anchor the handoff on")
    p.add_argument(
        "--mode",
        choices=FORK_MODES,
        default=FORK_CONTINUE,
        help="fork: continue (resume this arc) | new (pick a candidate) | close",
    )
    p.add_argument(
        "--summary-file",
        dest="summary_file",
        help="path to the prepared distil markdown (default: a minimal stub)",
    )
    p.add_argument(
        "--from-session",
        dest="from_session",
        help="origin session id — recorded on the offer for the multiples detector",
    )
    # retired flags (cand 05): accepted so old invocations don't crash, but inert —
    # the handoff never opens a terminal any more
    p.add_argument("--no-spawn", action="store_true", dest="no_spawn",
                   help=argparse.SUPPRESS)
    p.add_argument("--adapter", help=argparse.SUPPRESS)
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="distil only — leave the offer queue untouched",
    )
    p.set_defaults(func=cmd_handoff, _cmd="handoff")

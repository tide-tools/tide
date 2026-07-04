"""tide.arc.plan — the planning board (доска wake) bound to a goal-arc.

The board is tide's *focus surface*: a single ``board.json`` living in a
goal-arc's ``workspace/`` that holds what we're holding RIGHT NOW (≤7 focus
cards), the next ≤3 steps to the arc's goal, and a compression axis (distill a
vitok → the next level). It is the durable half of *wake* (arc 11 prototype):
files are the truth, a template + build render the two projections (map +
board). This module owns the truth-and-logic; rendering is a separate seam.

Design (candidate 27 ``tide-board-mode-planning-surface-on-arc-goal``):

* **one board per goal-arc** — ``<entry>/workspace/board.json`` (the entry is
  resolved by slug like every other arc verb; goal/thread preferred, open first).
* **focus ≤7** — the hard limit of what a head holds; overflow is refused, drops
  go to ``backlog`` (never deleted, restorable).
* **plan ≤3** — the explicit near-path to the arc's goal.
* **compression** — ``distill`` records a vitok (choice → formula) in
  ``history`` and reseeds focus from the formula, so the board climbs levels.
* **channel** — the page is a static artifact; the web side "commits" via a
  sync-code, the agent "pushes" via redeploy. :func:`apply_sync` decodes the
  tokens ``<card text>`` (add), ``DROP:<id>`` (to backlog).

All logic is plain, argparse-free functions that take/return an immutable board
dict (never mutated in place); :func:`register` wires the thin CLI handlers.
The CLI surface name is provisional (``tide plan``) — the final name is the
human's call (see candidate 27); only the wiring in :func:`register` changes.
"""

from __future__ import annotations

import copy
import datetime
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import fields, io as _io, paths, slug
from . import stream

FOCUS_LIMIT = 7
PLAN_LIMIT = 3
_CARD_ID_RE = re.compile(r"^c(\d+)$")
_TEMPLATE_PATH = Path(__file__).with_name("plan_template.html")
_SURFACE_TEMPLATE_PATH = Path(__file__).with_name("plan_surface_template.html")
PLAN_DATA_FILE = "plan-data.json"


class PlanError(stream.StreamError):
    """A user-facing board error (focus full, unknown card, unresolved goal …).

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it in
    the same arm (prints ``tide: …``, exits nonzero).
    """


# --- default shape ---------------------------------------------------------

def _empty_board() -> Dict[str, object]:
    """A fresh board: empty focus (limit 7), no plan, no compression, no threads."""
    return {
        "focus": {"limit": FOCUS_LIMIT, "cards": [], "backlog": []},
        "plan": [],
        "compression": {"levels": [], "history": []},
        "threads": [],
    }


# --- goal-arc + file resolution --------------------------------------------

def _resolve_entry(root: Path, goal: str) -> Path:
    """Resolve the goal-arc dir the board hangs on (goal/thread preferred, open first).

    Reuses the stream resolver so ``tide plan <goal>`` accepts the same slug forms
    as every other arc verb. Raises :class:`PlanError` when nothing matches, so we
    never scaffold a board on a stray path.
    """
    entry = stream._resolve_present(paths.arcs_dir(root), goal)
    if entry is None:
        raise PlanError("plan: no arc matching {0!r} in the stream".format(goal))
    return entry


def board_file(root: Path, goal: str) -> Path:
    """Path to the board's source of truth: ``<goal-arc>/workspace/board.json``."""
    return _resolve_entry(root, goal) / "workspace" / "board.json"


def load_board(root: Path, goal: str) -> Dict[str, object]:
    """Read the goal-arc's ``board.json``, or an empty board when none exists yet.

    Back-fills any missing top-level section so an older/partial file still loads
    with the full shape (focus/plan/compression/threads).
    """
    path = board_file(root, goal)
    board = _empty_board()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise PlanError("plan: cannot read {0}: {1}".format(path, exc))
        if isinstance(data, dict):
            board.update(data)
            # normalise nested defaults without clobbering present values
            focus = dict(_empty_board()["focus"])
            focus.update(board.get("focus") or {})
            board["focus"] = focus
            comp = dict(_empty_board()["compression"])
            comp.update(board.get("compression") or {})
            board["compression"] = comp
    return board


def save_board(root: Path, goal: str, board: Dict[str, object]) -> Path:
    """Atomically write *board* to the goal-arc's ``board.json`` (mkdir workspace)."""
    path = board_file(root, goal)
    path.parent.mkdir(parents=True, exist_ok=True)
    _io.atomic_write(path, json.dumps(board, ensure_ascii=False, indent=2) + "\n")
    return path


# --- pure board operations (immutable: take a board, return a NEW board) ----

def _next_card_id(board: Dict[str, object]) -> str:
    """Deterministic next card id (``c1``, ``c2`` …) — max existing +1, focus∪backlog.

    Scans both live focus cards and the backlog so a restored/dropped id is never
    reused. No timestamps/randomness — ids stay reproducible for tests.
    """
    focus = board.get("focus") or {}
    ids = [c.get("id", "") for c in focus.get("cards", [])]
    ids += [c.get("id", "") for c in focus.get("backlog", [])]
    nums = [int(m.group(1)) for m in (_CARD_ID_RE.match(i or "") for i in ids) if m]
    return "c{0}".format((max(nums) + 1) if nums else 1)


def add_card(board: Dict[str, object], text: str) -> Tuple[Dict[str, object], Dict[str, object]]:
    """Add a focus card, gating on the ≤7 limit. Returns ``(new_board, new_card)``.

    Refuses with :class:`PlanError` when focus is full — the point of the board is
    that a head can't hold more than the limit; overflow is a signal to distill or
    drop, not to grow the list.
    """
    text = (text or "").strip()
    if not text:
        raise PlanError("plan: empty card text")
    b = copy.deepcopy(board)
    focus = b["focus"]
    limit = int(focus.get("limit", FOCUS_LIMIT))
    if len(focus["cards"]) >= limit:
        raise PlanError(
            "plan: focus is full ({0}/{0}) — drop or distill before adding".format(limit)
        )
    card = {"id": _next_card_id(b), "text": text}
    focus["cards"].append(card)
    return b, card


def drop_card(board: Dict[str, object], card_id: str) -> Dict[str, object]:
    """Move a focus card to the backlog (never deleted — restorable). Returns new board."""
    b = copy.deepcopy(board)
    focus = b["focus"]
    keep, dropped = [], None
    for c in focus["cards"]:
        if c.get("id") == card_id and dropped is None:
            dropped = c
        else:
            keep.append(c)
    if dropped is None:
        raise PlanError("plan: no focus card with id {0!r}".format(card_id))
    focus["cards"] = keep
    focus["backlog"].append(dropped)
    return b


def add_step(board: Dict[str, object], text: str) -> Dict[str, object]:
    """Append a next-step to the ≤3 path toward the arc's goal. Returns new board."""
    text = (text or "").strip()
    if not text:
        raise PlanError("plan: empty step text")
    b = copy.deepcopy(board)
    if len(b["plan"]) >= PLAN_LIMIT:
        raise PlanError(
            "plan: path is full ({0} steps) — the board holds the NEAR path, not a "
            "backlog; complete or replace a step".format(PLAN_LIMIT)
        )
    b["plan"].append({"text": text})
    return b


def distill(board: Dict[str, object], card_id: str, formula: str) -> Dict[str, object]:
    """Compress a vitok: pick one card → a formula → the next level. Returns new board.

    Records the vitok in ``compression.history`` (the chosen card + formula + a
    snapshot of the focus it collapses), appends the formula to
    ``compression.levels``, and reseeds focus with a single card = the formula, so
    the board climbs to the next level without losing the prior focus (it lives in
    history). The backlog is untouched.
    """
    formula = (formula or "").strip()
    if not formula:
        raise PlanError("plan: empty distill formula")
    b = copy.deepcopy(board)
    focus = b["focus"]
    chosen = next((c for c in focus["cards"] if c.get("id") == card_id), None)
    if chosen is None:
        raise PlanError("plan: no focus card with id {0!r} to distill".format(card_id))
    level = len(b["compression"]["levels"]) + 1
    b["compression"]["history"].append(
        {
            "level": level,
            "choice": chosen,
            "formula": formula,
            "collapsed": list(focus["cards"]),
        }
    )
    b["compression"]["levels"].append(formula)
    seed = {"id": _next_card_id(b), "text": formula, "level": level}
    focus["cards"] = [seed]
    return b


_DROP_RE = re.compile(r"^DROP:\s*(\S+)$", re.IGNORECASE)


def apply_sync(board: Dict[str, object], code: str) -> Tuple[Dict[str, object], str]:
    """Decode a web sync-code into a board mutation. Returns ``(new_board, note)``.

    Tokens (candidate 27 channel): ``DROP:<id>`` → drop to backlog; anything else
    is treated as ``<card text>`` → add a focus card. ``DISTILL`` needs a chosen
    card + formula, so it is refused here (use ``tide plan distill``) rather than
    guessed.
    """
    code = (code or "").strip()
    if not code:
        raise PlanError("plan: empty sync code")
    if code.upper() == "DISTILL":
        raise PlanError("plan: DISTILL needs a card + formula — use 'tide plan distill'")
    m = _DROP_RE.match(code)
    if m:
        b = drop_card(board, m.group(1))
        return b, "dropped {0} → backlog".format(m.group(1))
    b, card = add_card(board, code)
    return b, "added {0}: {1}".format(card["id"], card["text"])


# --- rendering (text; the HTML projection is a separate build seam) ---------

def render_board(board: Dict[str, object]) -> str:
    """One-glance text projection of the board (focus / path / compression / backlog)."""
    focus = board.get("focus") or {}
    cards = focus.get("cards", [])
    limit = focus.get("limit", FOCUS_LIMIT)
    lines: List[str] = []
    lines.append("FOCUS ({0}/{1})".format(len(cards), limit))
    if cards:
        for c in cards:
            tag = "  ·L{0}".format(c["level"]) if c.get("level") else ""
            lines.append("  [{0}] {1}{2}".format(c.get("id", "?"), c.get("text", ""), tag))
    else:
        lines.append("  (empty — add with 'tide plan add')")
    plan = board.get("plan") or []
    lines.append("")
    lines.append("PATH -> goal ({0}/{1})".format(len(plan), PLAN_LIMIT))
    if plan:
        for i, s in enumerate(plan, 1):
            lines.append("  {0}. {1}".format(i, s.get("text", "")))
    else:
        lines.append("  (no steps — add with 'tide plan step')")
    levels = (board.get("compression") or {}).get("levels") or []
    if levels:
        lines.append("")
        lines.append("COMPRESSION: " + " -> ".join(levels))
    backlog = focus.get("backlog") or []
    if backlog:
        lines.append("")
        lines.append("backlog ({0}): ".format(len(backlog)) + ", ".join(
            "{0}".format(c.get("id", "?")) for c in backlog
        ))
    return "\n".join(lines)


# --- threads: mirror the project's REAL tide stream (not seed values) -------

def _threads_of(root: Path) -> List[Dict[str, object]]:
    """The open tide threads of ONE project as ``{slug, name, weight}`` dicts.

    A нить IS a ``kind: thread`` entry; ``weight`` = its open-session count (a real
    "pull" signal, floored at 1); the name is the thread's ``goal:`` line or slug.
    """
    out: List[Dict[str, object]] = []
    for t in stream.thread_entries(root, closed=False):
        tslug = slug.entry_slug(t.name)
        try:
            weight = max(len(stream.session_entries(root, tslug, closed=False)), 1)
        except Exception:
            weight = 1
        goal_line = (fields.read_field(stream.passport_path(t), "goal") or "").strip()
        out.append({"slug": tslug, "name": goal_line or tslug, "weight": weight})
    return out


def refresh_threads(root: Path, board: Dict[str, object], *, roster_wide: bool = False) -> Dict[str, object]:
    """Replace ``board['threads']`` with live tide threads. Returns a new board.

    The map projection's нити are not hand-seeded — they ARE real ``kind: thread``
    entries. By default the current project's threads; with *roster_wide* the board
    becomes the shared surface it is meant to be — every ACTIVE rostered project's
    threads, each tagged with its ``project`` so one glance covers all живые нити.
    Roster resolution is fully defensive: any failure degrades to this project's
    threads rather than raising (the board must always build).
    """
    b = copy.deepcopy(board)
    threads: List[Dict[str, object]] = []
    if roster_wide:
        try:
            from .. import roster  # lazy: avoid an import cycle

            home = paths.control_home()
            for entry in roster.read_roster(home):
                if str(entry.get("status") or "").strip().lower() == "archived":
                    continue
                proot = Path(str(entry.get("path") or "")).expanduser()
                if not (proot / ".tide").is_dir():
                    continue
                pname = entry.get("name") or proot.name
                for t in _threads_of(proot):
                    threads.append({**t, "project": pname})
            b["threads"] = threads
            return b
        except Exception:
            threads = []  # fall through to the local view — the board must build
    b["threads"] = _threads_of(root)
    return b


# --- HTML projection (self-contained; the phone/Artifact surface) ----------

_STANDALONE_HEAD = (
    "<!doctype html>\n<html lang=\"ru\">\n<head>\n<meta charset=\"utf-8\">\n"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
    "<title>{title}</title>\n</head>\n<body style=\"margin:0\">\n"
)
_STANDALONE_TAIL = "\n</body>\n</html>\n"


def _meta_for(root: Path, goal: str) -> Dict[str, object]:
    """Assemble the display META for the board page from the resolved goal-arc.

    Reads the arc's ``goal:`` line off its passport for the goal line, names the
    thread by the entry slug, and stamps the project + build date. Kept separate
    from :func:`build_html` so the pure builder takes plain data (testable).
    """
    entry = _resolve_entry(root, goal)
    passport = stream.passport_path(entry)
    goal_line = (fields.read_field(passport, "goal") or "").strip()
    return {
        "name": "wake",
        "project": root.name,
        "thread": entry.name,
        "goal": goal_line,
        "built": datetime.date.today().isoformat(),
    }


def build_html(board: Dict[str, object], meta: Dict[str, object], *, standalone: bool = True) -> str:
    """Render the board to a self-contained page by injecting JSON into the template.

    The template is a fragment (``<style>`` + markup + ``<script>``) so the same
    source feeds both surfaces: *standalone* wraps it in a minimal HTML document
    (for ``verify.serve`` / opening the file), while the un-wrapped fragment is
    what the Artifact publish path uploads (the Artifact skeleton supplies the
    head/body). Injection is literal — the placeholders are replaced with compact
    JSON, so no external requests are needed (works under the artifact CSP).
    """
    fragment = _TEMPLATE_PATH.read_text(encoding="utf-8")
    fragment = fragment.replace(
        "__BOARD_JSON__", json.dumps(board, ensure_ascii=False)
    ).replace(
        "__META_JSON__", json.dumps(meta, ensure_ascii=False)
    )
    if not standalone:
        return fragment
    title = "wake · {0}".format(meta.get("thread") or "board")
    return _STANDALONE_HEAD.format(title=title) + fragment + _STANDALONE_TAIL


# --- planning surface (deep plan by topic; the phone planning tool) ---------

def plan_data_file(root: Path, goal: str) -> Path:
    """Path to a goal-arc's deep-plan source: ``<goal-arc>/workspace/plan-data.json``.

    This is a SEPARATE surface from ``board.json`` (focus/path/compression): the
    plan-data holds ONE topic's plan-in-depth — steps with sub-points + status,
    open decisions, and the next move. Keeping it its own file is deliberate — it
    adds the planning surface without touching the board schema or its tests.
    """
    return _resolve_entry(root, goal) / "workspace" / PLAN_DATA_FILE


def show_on_board(root: Path, goal: str, title: str, *, kind: str = "заметка",
                  body: Optional[str] = None) -> Dict[str, object]:
    """Append a SHOW item to the goal-arc's ``plan-data.json`` — the agent's gesture.

    This is the *channel of showing*: the agent puts a thing (a note; richer kinds
    like an inline SVG diagram are authored directly in the data) into ``shows`` so
    the human sees it on the board at the next render/redeploy — the symmetric
    counterpart to the agent reading files. Creates a minimal plan-data when none
    exists yet. Returns the appended item.
    """
    title = (title or "").strip()
    if not title:
        raise PlanError("plan: empty show title")
    path = plan_data_file(root, goal)
    data: Dict[str, object] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise PlanError("plan: cannot read {0}: {1}".format(path, exc))
    if not isinstance(data, dict):
        data = {}
    shows = data.get("shows")
    if not isinstance(shows, list):
        shows = []
    item: Dict[str, object] = {"kind": kind or "заметка", "title": title}
    if body:
        item["body"] = body
    shows.append(item)
    data["shows"] = shows
    path.parent.mkdir(parents=True, exist_ok=True)
    _io.atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return item


def build_surface(root: Path, goal: str, *, standalone: bool = True) -> str:
    """Render the deep-plan surface for *goal* from its ``plan-data.json``.

    Reads the goal-arc's plan-data (raises :class:`PlanError` with a clear hint when
    absent) and injects it into the shipped surface template — a fragment, so the
    same source feeds a standalone doc (``verify.serve``) or the Artifact publish
    (un-wrapped). Injection is literal JSON, so it needs no external requests.
    """
    path = plan_data_file(root, goal)
    if not path.is_file():
        raise PlanError(
            "plan: no {0} on arc {1!r} — author the deep plan first".format(PLAN_DATA_FILE, goal)
        )
    try:
        data = path.read_text(encoding="utf-8")
        json.loads(data)  # validate before injecting
    except (ValueError, OSError) as exc:
        raise PlanError("plan: cannot read {0}: {1}".format(path, exc))
    fragment = _SURFACE_TEMPLATE_PATH.read_text(encoding="utf-8").replace("__PLAN_JSON__", data)
    if not standalone:
        return fragment
    # the surface template already carries its own <meta>/<title>/<style> head —
    # only the doctype/html/body wrapper is added for a standalone document.
    return "<!doctype html>\n<html lang=\"ru\">\n<body style=\"margin:0\">\n" + fragment + "\n</body>\n</html>\n"


# --- CLI wiring ------------------------------------------------------------

def _root() -> Path:
    return paths.require_tide_root()


def _cmd_open(args) -> int:
    root = _root()
    board = load_board(root, args.goal)
    save_board(root, args.goal, board)  # ensure the file exists on first open
    print(render_board(board))
    return 0


def _cmd_add(args) -> int:
    root = _root()
    board, card = add_card(load_board(root, args.goal), " ".join(args.text))
    save_board(root, args.goal, board)
    print("tide: + focus [{0}] {1}".format(card["id"], card["text"]))
    return 0


def _cmd_drop(args) -> int:
    root = _root()
    board = drop_card(load_board(root, args.goal), args.id)
    save_board(root, args.goal, board)
    print("tide: dropped {0} -> backlog".format(args.id))
    return 0


def _cmd_step(args) -> int:
    root = _root()
    board = add_step(load_board(root, args.goal), " ".join(args.text))
    save_board(root, args.goal, board)
    print("tide: + step: {0}".format(" ".join(args.text)))
    return 0


def _cmd_distill(args) -> int:
    root = _root()
    board = distill(load_board(root, args.goal), args.id, " ".join(args.formula))
    save_board(root, args.goal, board)
    print("tide: distilled {0} -> L{1}: {2}".format(
        args.id, len(board["compression"]["levels"]), " ".join(args.formula)
    ))
    return 0


def _cmd_sync(args) -> int:
    root = _root()
    board, note = apply_sync(load_board(root, args.goal), " ".join(args.code))
    save_board(root, args.goal, board)
    print("tide: sync — {0}".format(note))
    return 0


def _cmd_build(args) -> int:
    root = _root()
    # Mirror real tide threads into the board before rendering, so the map shows
    # the actual stream (not stale/seed threads) and persists it. --all makes it
    # the cross-project shared surface: every active rostered project's threads.
    board = refresh_threads(root, load_board(root, args.goal), roster_wide=args.all)
    save_board(root, args.goal, board)
    meta = _meta_for(root, args.goal)
    html = build_html(board, meta, standalone=not args.fragment)
    out = _resolve_entry(root, args.goal) / "workspace" / (
        "board.fragment.html" if args.fragment else "board.html"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    _io.atomic_write(out, html)
    print("tide: built board -> {0}".format(out))
    return 0


def _cmd_show(args) -> int:
    root = _root()
    item = show_on_board(root, args.goal, " ".join(args.title), kind=args.kind,
                         body=" ".join(args.body) if args.body else None)
    print("tide: shown on board [{0}] {1}".format(item["kind"], item["title"]))
    return 0


def _cmd_surface(args) -> int:
    root = _root()
    html = build_surface(root, args.goal, standalone=not args.fragment)
    out = _resolve_entry(root, args.goal) / "workspace" / (
        "plan-surface.fragment.html" if args.fragment else "plan-surface.html"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    _io.atomic_write(out, html)
    print("tide: built plan surface -> {0}".format(out))
    return 0


def register(subparsers) -> None:
    """Add the ``plan`` command group to *subparsers* (called by cli.py).

    Provisional surface name (``tide plan``); the final name is the human's — only
    this wiring changes if it moves (e.g. ``tide arc board``).
    """
    p = subparsers.add_parser("plan", help="focus board on an arc's goal (<=7 focus + path + distill)")
    psub = p.add_subparsers(dest="plan_cmd")

    op = psub.add_parser("open", help="open/create the board and print it")
    op.add_argument("goal", help="the arc/goal slug the board hangs on")
    op.set_defaults(func=_cmd_open, _cmd="plan open")

    ap = psub.add_parser("add", help="add a focus card (gate: <=7)")
    ap.add_argument("goal")
    ap.add_argument("text", nargs="+", help="the card text")
    ap.set_defaults(func=_cmd_add, _cmd="plan add")

    dp = psub.add_parser("drop", help="move a focus card to the backlog")
    dp.add_argument("goal")
    dp.add_argument("id", help="card id (e.g. c3)")
    dp.set_defaults(func=_cmd_drop, _cmd="plan drop")

    sp = psub.add_parser("step", help="add a next-step to the path (gate: <=3)")
    sp.add_argument("goal")
    sp.add_argument("text", nargs="+", help="the next-step text")
    sp.set_defaults(func=_cmd_step, _cmd="plan step")

    dsp = psub.add_parser("distill", help="compress a vitok: card + formula -> next level")
    dsp.add_argument("goal")
    dsp.add_argument("id", help="the chosen focus card id")
    dsp.add_argument("formula", nargs="+", help="the compressed formula")
    dsp.set_defaults(func=_cmd_distill, _cmd="plan distill")

    syp = psub.add_parser("sync", help="apply a web sync-code (<card> | DROP:<id>)")
    syp.add_argument("goal")
    syp.add_argument("code", nargs="+", help="the sync token")
    syp.set_defaults(func=_cmd_sync, _cmd="plan sync")

    bp = psub.add_parser("build", help="render the board to a self-contained HTML page")
    bp.add_argument("goal")
    bp.add_argument("--fragment", action="store_true",
                    help="emit the head/body-less fragment (for Artifact publish) instead of a standalone doc")
    bp.add_argument("--all", action="store_true",
                    help="shared surface: pull threads from ALL active rostered projects, not just this one")
    bp.set_defaults(func=_cmd_build, _cmd="plan build")

    up = psub.add_parser("surface", help="render the deep-plan surface (steps+depth+decisions) from plan-data.json")
    up.add_argument("goal")
    up.add_argument("--fragment", action="store_true",
                    help="emit the head/body-less fragment (for Artifact publish) instead of a standalone doc")
    up.set_defaults(func=_cmd_surface, _cmd="plan surface")

    shp = psub.add_parser("show", help="put a note on the board for the human to see (the show channel)")
    shp.add_argument("goal")
    shp.add_argument("title", nargs="+", help="what you're showing (short title)")
    shp.add_argument("--kind", default="заметка", help="label chip (e.g. заметка / схема / скрин)")
    shp.add_argument("--body", nargs="*", help="optional longer body")
    shp.set_defaults(func=_cmd_show, _cmd="plan show")

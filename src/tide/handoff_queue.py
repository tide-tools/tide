"""tide.handoff_queue — the two-stage handoff queue (offer → confirmed pickup).

The old handoff PUSHED: a chat distilled itself and tried to spawn a new session
(osascript keystroke into Orca). That spawn lies — it reports success even when the
keystroke never lands, and the offering chat never learns whether anyone actually
took over.

This decouples the two halves into a small on-disk QUEUE in the control-home
(``.tide/handoffs/``):

* **offer** (stage 1) — the offering chat writes a pending record (``status:
  offered``) pointing at its seed. No spawn dependency: the offer just *hangs*.
* **confirm** (stage 2) — when a fresh session gets its FIRST human message, a
  ``UserPromptSubmit`` hook (:func:`cmd_handoff_confirm`) claims the matching
  pending offer and flips it to ``status: taken`` (stamping who/when). The first
  human message is the proof a real pickup happened — not a spawn's say-so.

``tide handoffs`` lists what is hanging; ``tide handoffs take`` is the manual
equivalent of the confirm hook. Pure functions do the JSON/markdown I/O
(argparse-free, unit-testable); :func:`register` / :func:`cmd_handoff_confirm`
wire the thin CLI + hook handlers.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from . import io as _io, numbering, paths, slug
from .arc.stream import StreamError

STATUS_OFFERED = "offered"
STATUS_TAKEN = "taken"
STATUS_DROPPED = "dropped"  # soft-archived: dismissed without pickup (record kept)
DEFAULT_MODE = "continue"

# A handoff record file: NN-<slug>.md (2+ digit number, base-10 padding).
_HANDOFF_RE = re.compile(r"^(\d{2,})-(.+)\.md$")


# A handoff record is OUR own simple `key: value` format — read/written with these
# local helpers (NOT the shared tide.fields, whose whitelist excludes handoff keys).
def _get(text: str, key: str, default: str = "-") -> str:
    """First ``key: value`` line's value in *text* (stripped), or *default*."""
    prefix = key + ":"
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip() or default
    return default


def _set_field(path: Path, key: str, value: str) -> None:
    """Replace the ``key: …`` line in *path* with ``key: value`` (atomic write)."""
    prefix = key + ":"
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = "{0} {1}".format(prefix, value)
            break
    _io.atomic_write(path, "\n".join(lines) + "\n")


class HandoffError(StreamError):
    """A user-facing handoff-queue error (empty slug, unknown id, no home)."""


def _now() -> str:
    """Current local timestamp to seconds (own helper so tests can monkeypatch)."""
    return datetime.now().isoformat(timespec="seconds")


# --- record construction / parsing -----------------------------------------

def _record_md(name: str, *, mode: str, arc: str, project: str, seed: str,
               from_session: str, created: str, note: str) -> str:
    """The markdown body of a fresh OFFERED handoff record."""
    return (
        "# handoff {name}\n\n"
        "status: {status}\n"
        "mode: {mode}\n"
        "arc: {arc}\n"
        "project: {project}\n"
        "seed: {seed}\n"
        "from-session: {frm}\n"
        "created: {created}\n"
        "pickup-session: -\n"
        "taken-by: -\n"
        "taken-at: -\n\n"
        "## note\n{note}\n"
    ).format(
        name=name, status=STATUS_OFFERED, mode=mode or DEFAULT_MODE, arc=arc or "-",
        project=project or "-", seed=seed or "-", frm=from_session or "-",
        created=created, note=(note or "(none)"),
    )


def _parse(path: Path) -> Optional[Dict[str, object]]:
    """Parse a handoff record file into a dict, or None if it isn't one."""
    m = _HANDOFF_RE.match(path.name)
    if not m:
        return None
    text = path.read_text(encoding="utf-8")
    return {
        "path": path,
        "name": path.stem,
        "num": m.group(1),
        "slug": m.group(2),
        "status": _get(text, "status", STATUS_OFFERED),
        "mode": _get(text, "mode", DEFAULT_MODE),
        "arc": _get(text, "arc"),
        "project": _get(text, "project"),
        "seed": _get(text, "seed"),
        "from_session": _get(text, "from-session"),
        "created": _get(text, "created"),
        "pickup_session": _get(text, "pickup-session"),
        "taken_by": _get(text, "taken-by"),
        "taken_at": _get(text, "taken-at"),
    }


# --- operations (pure-ish: read/write the queue dir) -----------------------

def offer(home: Path, raw_slug: str, *, arc: str, project: str, seed: str,
          mode: str = DEFAULT_MODE, from_session: Optional[str] = None,
          note: Optional[str] = None) -> Path:
    """Hang a pending handoff offer in *home*'s queue; return the record path."""
    s = slug.short_slug(raw_slug)
    if not s:
        raise HandoffError("handoff: empty slug after slugify")
    cdir = paths.handoffs_dir(Path(home))
    cdir.mkdir(parents=True, exist_ok=True)
    nn = numbering.next_num_file(cdir)
    name = "{0}-{1}".format(nn, s)
    path = cdir / "{0}.md".format(name)
    _io.atomic_write(path, _record_md(
        name, mode=mode, arc=arc, project=project, seed=seed,
        from_session=from_session or "-", created=_now(), note=note or "",
    ))
    return path


def list_offers(home: Path, *, status: Optional[str] = None) -> List[Dict[str, object]]:
    """Return queue records (filename order), optionally filtered by *status*."""
    cdir = paths.handoffs_dir(Path(home))
    if not cdir.is_dir():
        return []
    out: List[Dict[str, object]] = []
    for p in sorted(cdir.glob("*.md")):
        rec = _parse(p)
        if rec and (status is None or rec["status"] == status):
            out.append(rec)
    return out


def _resolve(home: Path, key: str) -> Dict[str, object]:
    """Find a record by NN, NN-slug, or slug; raise if absent/ambiguous-miss."""
    want = (key or "").strip()
    recs = list_offers(home)
    for r in recs:
        if want in (r["num"], r["name"], r["slug"]):
            return r
    raise HandoffError("handoff: no offer matching {0!r}".format(key))


def _mark_taken(rec: Dict[str, object], *, session: Optional[str]) -> Dict[str, object]:
    """Flip a record to taken, stamping who/when (mutates the file)."""
    path = rec["path"]
    _set_field(path, "status", STATUS_TAKEN)
    _set_field(path, "taken-by", session or "-")
    _set_field(path, "taken-at", _now())
    return _parse(path)


def take(home: Path, key: str, *, session: Optional[str] = None) -> Dict[str, object]:
    """Explicitly confirm pickup of offer *key* (manual equivalent of the hook)."""
    return _mark_taken(_resolve(home, key), session=session)


def _prune_untouched_session(rec: Dict[str, object]) -> bool:
    """Remove the offer's seeded session dir IFF it was never engaged. Best-effort.

    The session lives at ``<session>/`` and the handoff seed at
    ``<session>/input/<seed>.md`` (so ``Path(seed).parent.parent`` is the session
    dir, exactly as :func:`tide.launcher.menu.launch_handoff` resolves it). It
    counts as **untouched** when ``workspace/`` and ``output/`` are both empty — only
    the seed sits in ``input/``. A session with real work is left intact (the drop
    then degrades to "just dismiss the offer"). Returns True when a dir was removed.
    """
    seed = rec.get("seed")
    if not seed or seed == "-":
        return False
    session_dir = Path(str(seed)).parent.parent
    if not (session_dir / "input").is_dir():
        return False  # seed path doesn't look like <session>/input/<file> — don't touch
    for sub in ("workspace", "output"):
        d = session_dir / sub
        if d.is_dir() and any(d.iterdir()):
            return False  # real work present — keep the session
    shutil.rmtree(session_dir, ignore_errors=True)
    return True


def drop(home: Path, key: str, *, prune_untouched: bool = True) -> "tuple[Dict[str, object], bool]":
    """Soft-archive offer *key* (status → dropped); optionally prune its dead session.

    The dismiss path the queue was missing: an offer you decide NOT to pick up.
    The record is KEPT (flipped to ``dropped``) so it stops surfacing in the menu /
    pending list but stays auditable — never a hard delete (the distil pointer is
    preserved). When *prune_untouched* and the seeded session was never engaged
    (empty ``workspace/`` + ``output/``), its dir is removed too so a thread doesn't
    accrue ghost tips (B-with-guard). Refuses a TAKEN offer (nothing to dismiss).
    Returns ``(record, pruned)``.
    """
    rec = _resolve(home, key)
    if rec["status"] == STATUS_TAKEN:
        raise HandoffError(
            "handoff: {0} already taken — nothing to drop".format(rec["name"])
        )
    _set_field(rec["path"], "status", STATUS_DROPPED)
    rec = _parse(rec["path"])
    pruned = _prune_untouched_session(rec) if prune_untouched else False
    return rec, pruned


def reserve(home: Path, key: str, *, session: str) -> Dict[str, object]:
    """Reserve an OFFERED handoff for a specific *session* (set ``pickup-session``).

    Called at menu-pickup launch with the pinned ``--session-id``. Status STAYS
    offered — the reservation just records WHICH session is allowed to confirm it,
    so a confirm hook in any OTHER session of the same project won't vacuum it. The
    real flip to ``taken`` happens on that session's first message
    (:func:`confirm_for_session`).
    """
    rec = _resolve(home, key)
    _set_field(rec["path"], "pickup-session", session)
    return _parse(rec["path"])


def confirm_for_session(home: Path, session: str) -> Optional[Dict[str, object]]:
    """Claim the offered handoff RESERVED for *session* (the confirm hook's core).

    Matches strictly on ``pickup-session`` — only the session that was actually
    launched from the offer confirms it. Returns the claimed record, or None when
    nothing is reserved for this session (so the hook is a silent no-op in any
    ordinary session — no more project-wide vacuuming).
    """
    if not session:
        return None
    for r in list_offers(home, status=STATUS_OFFERED):
        if r["pickup_session"] == session:
            return _mark_taken(r, session=session)
    return None


# --- render ----------------------------------------------------------------

def render_list(home: Path) -> str:
    """Human view: pending offers first, then recently taken ones."""
    recs = list_offers(home)
    if not recs:
        return "(no handoffs)"
    lines: List[str] = []
    for r in recs:
        if r["status"] == STATUS_OFFERED:
            lines.append("  ⌛ {0}  [{1}]  {2} · arc {3}  (offered {4})".format(
                r["name"], r["mode"], r["project"], r["arc"], r["created"]))
    for r in recs:
        if r["status"] == STATUS_TAKEN:
            lines.append("  ✓ {0}  [{1}]  {2}  (taken {3} by {4})".format(
                r["name"], r["mode"], r["project"], r["taken_at"], r["taken_by"]))
    for r in recs:
        if r["status"] == STATUS_DROPPED:
            lines.append("  ✗ {0}  [{1}]  {2}  (dropped)".format(
                r["name"], r["mode"], r["project"]))
    return "\n".join(lines)


# --- CLI + hook wiring ------------------------------------------------------

def _home() -> Path:
    return paths.control_home()


def _cmd_offer(args) -> int:
    note = " ".join(args.note) if getattr(args, "note", None) else None
    path = offer(
        _home(), args.slug, arc=args.arc or "-", project=args.project or "-",
        seed=args.seed or "-", mode=getattr(args, "mode", DEFAULT_MODE) or DEFAULT_MODE,
        from_session=getattr(args, "from_session", None), note=note,
    )
    print("tide: handoff offered {0} (status: offered)".format(path.name))
    return 0


def _cmd_list(args) -> int:
    print(render_list(_home()))
    return 0


def _cmd_take(args) -> int:
    rec = take(_home(), args.key, session=getattr(args, "session", None))
    print("tide: handoff {0} → taken".format(rec["name"]))
    return 0


def _cmd_drop(args) -> int:
    rec, pruned = drop(_home(), args.key)
    tail = " (+ empty session removed)" if pruned else ""
    print("tide: handoff {0} → dropped{1}".format(rec["name"], tail))
    return 0


def cmd_handoff_confirm(args) -> int:
    """``tide hook handoff-confirm`` — UserPromptSubmit: confirm THIS session's handoff.

    Fired on every user message; the FIRST one in a picked-up session is what
    confirms. Claims STRICTLY the offer reserved for this session's id (read from the
    hook's stdin JSON — the menu pickup pinned that id via ``--session-id`` and
    reserved the offer with it). A session that wasn't launched from a handoff (no
    matching reservation) is a silent no-op — so ordinary sessions never vacuum
    pending offers. Fully defensive: any error prints nothing and exits 0 (a hook
    must never break a session); re-firing on later messages is a harmless no-op.
    """
    try:
        if paths.find_tide_root() is None:
            return 0
        try:
            payload = json.loads(sys.stdin.read() or "{}")
            session = payload.get("session_id") or payload.get("session")
        except (ValueError, OSError):
            session = None
        if not session:
            return 0
        claimed = confirm_for_session(paths.control_home(), session)
        if claimed:
            print("tide: handoff {0} confirmed — picked up here".format(claimed["name"]))
    except Exception as exc:  # noqa: BLE001  a hook must never raise
        print("tide: [handoff-confirm] skipped: {0}".format(exc), file=sys.stderr)
    return 0


def register(subparsers) -> None:
    """Add the top-level ``handoffs`` command group (called by cli.py)."""
    p = subparsers.add_parser("handoffs", help="two-stage handoff queue (offer/list/take)")
    hsub = p.add_subparsers(dest="handoffs_cmd")

    op = hsub.add_parser("offer", help="hang a pending handoff offer")
    op.add_argument("slug")
    op.add_argument("--arc", help="target arc ref the handoff anchors on")
    op.add_argument("--project", help="project the work belongs to (roster name)")
    op.add_argument("--seed", help="path to the prepared handoff seed file")
    op.add_argument("--mode", default=DEFAULT_MODE, help="continue|execution|close (default: continue)")
    op.add_argument("--from", dest="from_session", help="origin session id")
    op.add_argument("note", nargs="*", help="free-form note")
    op.set_defaults(func=_cmd_offer, _cmd="handoffs offer")

    lp = hsub.add_parser("list", help="list pending + recently-taken handoffs")
    lp.set_defaults(func=_cmd_list, _cmd="handoffs list")

    tp = hsub.add_parser("take", help="confirm pickup of an offer (NN/slug)")
    tp.add_argument("key")
    tp.add_argument("--session", help="claiming session id (recorded as taken-by)")
    tp.set_defaults(func=_cmd_take, _cmd="handoffs take")

    dp = hsub.add_parser("drop", help="dismiss an offer (soft-archive; prune its dead session)")
    dp.add_argument("key")
    dp.set_defaults(func=_cmd_drop, _cmd="handoffs drop")

    # bare `tide handoffs` behaves like `tide handoffs list`
    p.set_defaults(func=_cmd_list, _cmd="handoffs")

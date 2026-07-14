"""tide.arc.curate — the human's hand-gestures over the stream, as DOMAIN operations.

Everything here used to live as regex writers inside the board's ``serve_live.py``
(``_hold`` / ``_dismiss`` / ``_validate`` / ``_drop_cand`` / ``_drop_thread`` and the
retire-cascade inside ``_close``) — the board patched passports by hand, in its own
copy, bypassing the domain. These are pull-gestures (a click IS the human's hand), but
the MECHANICS belong to tide: one implementation, every surface calls the door.

All operations are pure file work over ``.tide/`` through the store primitives — no
terminals, no HTTP. The board keys its UI by absolute entry dirs, so the functions
take dirs; the CLI wrappers accept ``--dir`` (the board's contract) and validate the
path shape before acting.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .. import fields
from .stream import StreamError, passport_path

DROPPED_DIRNAME = "__dropped__"


def _stamp(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).isoformat(timespec="seconds")


def _require_arcs_entry(entry_dir: Path) -> Path:
    """An existing dir inside a ``.tide/arcs/`` tree — the shape every gesture expects."""
    d = Path(entry_dir)
    if ".." in str(entry_dir):
        raise StreamError("curate: path traversal refused: {0}".format(entry_dir))
    if "/.tide/arcs/" not in str(d):
        raise StreamError("curate: not inside a .tide/arcs tree: {0}".format(d))
    if not d.is_dir():
        raise StreamError("curate: no such entry: {0}".format(d))
    return d


def hold(entry_dir: Path, *, on: bool = True, now: Optional[datetime] = None) -> Path:
    """☾ set a thread aside / ↑ bring it back: stamp or remove ``held:`` (idempotent).

    A held thread moves to its own board category (nearby, not nagging); reversible,
    lightweight. Returns the passport written.
    """
    d = _require_arcs_entry(entry_dir)
    pp = passport_path(d)
    if not pp.is_file():
        raise StreamError("hold: no passport in {0}".format(d))
    if on:
        if not fields.read_field(pp, "held"):
            fields.set_field(pp, "held", _stamp(now))
    else:
        fields.remove_field(pp, "held")
    return pp


def _stamp_dismissed(session_dir: Path, now: Optional[datetime]) -> Optional[Path]:
    """``dismissed:`` on one session's arc.md; None when absent/already stamped."""
    pp = Path(session_dir) / "arc.md"
    if not pp.is_file() or fields.read_field(pp, "dismissed"):
        return None
    fields.set_field(pp, "dismissed", _stamp(now))
    return pp


def dismiss(session_dir: Path, *, now: Optional[datetime] = None) -> List[Path]:
    """✕ retire a head by hand: ``dismissed:`` on the session (death of attention is
    ONLY ever a human gesture — an agent never buries itself).

    The session stays in the thread's visit journal (⟳ keeps working, nothing is
    moved); it just stops counting as the head in focus. On a CLOSED (``__``) thread
    the gesture frees the WHOLE head-chain — every live session at once (one lingering
    sibling used to keep a closed thread in focus). Returns the passports stamped.
    """
    d = _require_arcs_entry(session_dir)
    if not (d / "arc.md").is_file():
        raise StreamError("dismiss: no session passport in {0}".format(d))
    thread = d.parents[1]
    targets = [d]
    if thread.name.startswith("__") and (thread / "arcs").is_dir():
        targets = sorted(p for p in (thread / "arcs").iterdir() if (p / "arc.md").is_file())
    return [pp for s in targets if (pp := _stamp_dismissed(s, now)) is not None]


def retire_sessions(thread_dir: Path, *, now: Optional[datetime] = None) -> List[Path]:
    """``dismissed:`` on every live session of *thread_dir* — the retire-the-head half
    of closing a thread by hand (a closed thread must not shine a live head stub)."""
    d = _require_arcs_entry(thread_dir)
    sub = d / "arcs"
    if not sub.is_dir():
        return []
    return [pp for s in sorted(sub.iterdir())
            if (pp := _stamp_dismissed(s, now)) is not None]


def _moved_aside(target: Path, grave: Path) -> Path:
    """A collision-free destination in *grave* (NN may be reused after a drop)."""
    dest = grave / target.name
    n = 2
    while dest.exists():
        stem, suffix = target.stem, target.suffix
        dest = grave / "{0}-{1}{2}".format(stem, n, suffix)
        n += 1
    return dest


def drop_candidate(root: Path, key: str) -> Path:
    """✕ soft-drop an idea: MOVE ``candidates/<key>.md`` → ``candidates/__dropped__/``.

    "Nothing drowns": the file leaves the shelf and ``tide candidate list`` (both read
    only top-level ``*.md``) but stays on disk, restorable. Returns the new path.
    """
    if not (re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,120}", key or "") and ".." not in key):
        raise StreamError("candidate drop: bad key {0!r}".format(key))
    cdir = (Path(root) / ".tide" / "arcs" / "candidates").resolve()
    cfile = (cdir / "{0}.md".format(key)).resolve()
    if not (cfile.is_file() and cfile.parent == cdir):
        raise StreamError("candidate drop: no such candidate {0!r}".format(key))
    grave = cdir / DROPPED_DIRNAME
    grave.mkdir(exist_ok=True)
    dest = _moved_aside(cfile, grave)
    shutil.move(str(cfile), str(dest))
    return dest


def drop_thread(thread_dir: Path) -> Path:
    """✕ soft-drop an EMPTY plan-less thread: MOVE it → ``arcs/__dropped__/``.

    NOT ``__NN@__`` (that's a trophy on the closed shelf — a dropped blank never was
    one). SAFETY GATE: only a truly empty thread moves — zero open sessions AND no
    plan.md; live work is untouchable even through a forged path. Returns the new path.
    """
    d = _require_arcs_entry(thread_dir)
    if d.name.startswith("__"):
        raise StreamError("drop-thread: {0} is closed (a trophy) — reopen first".format(d.name))
    if d.parent.name != "arcs":
        raise StreamError("drop-thread: {0} is not a top-stream thread".format(d))
    sub = d / "arcs"
    has_session = sub.is_dir() and any(
        c.is_dir() and not c.name.startswith("__") for c in sub.iterdir())
    if has_session or (d / "plan.md").is_file():
        raise StreamError("drop-thread: thread is not empty — close it by hand instead")
    grave = d.parent / DROPPED_DIRNAME
    grave.mkdir(exist_ok=True)
    dest = _moved_aside(d, grave)
    shutil.move(str(d), str(dest))
    return dest


def validate_step(thread_dir: Path, step: str, *, who: str = "Гриша (с доски)",
                  now: Optional[datetime] = None) -> None:
    """✓ the human validates a step's gate in the thread's ``plan.md``.

    Marks the step done (``[x]``), writes/updates ``гейт-пройден: <date> · <who>`` in
    the step's sub-block, and promotes the next todo step to current (``[>]``) so the
    board always has a "now". Touches ONLY the gate — tasks are the agent's to fill.
    """
    d = _require_arcs_entry(thread_dir)
    plan = d / "plan.md"
    if not re.fullmatch(r"\d{1,3}", step or ""):
        raise StreamError("validate: bad step {0!r}".format(step))
    if not plan.is_file():
        raise StreamError("validate: no plan.md in {0}".format(d))
    lines = plan.read_text(encoding="utf-8", errors="ignore").splitlines()
    step_re = re.compile(r"^- \[[x> ]\]\s*" + step + r"\.\s")
    i = next((k for k, ln in enumerate(lines) if step_re.match(ln)), None)
    if i is None:
        raise StreamError("validate: no step {0} in {1}".format(step, plan))
    stamp = "  гейт-пройден: {0} · {1}".format(
        (now or datetime.now()).strftime("%d.%m"), who)
    lines[i] = re.sub(r"^- \[[x> ]\]", "- [x]", lines[i], count=1)
    j = i + 1
    gate_idx = passed_idx = None
    while j < len(lines) and not lines[j].startswith("- [") \
            and not lines[j].startswith("## "):
        if re.match(r"^\s+гейт-пройден:", lines[j]):
            passed_idx = j
        elif re.match(r"^\s+гейт:", lines[j]):
            gate_idx = j
        j += 1
    if passed_idx is not None:
        lines[passed_idx] = stamp
    elif gate_idx is not None:
        lines.insert(gate_idx + 1, stamp)
    else:
        lines.insert(j, stamp)
    if not any(re.match(r"^- \[>\]", ln) for ln in lines):
        for k in range(i + 1, len(lines)):
            if re.match(r"^- \[ \]", lines[k]):
                lines[k] = re.sub(r"^- \[ \]", "- [>]", lines[k], count=1)
                break
    plan.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- CLI (thin wrappers; the board calls these through the subprocess door) ---------

def _cmd_hold(args) -> int:
    pp = hold(Path(args.dir), on=not args.off)
    print("tide: {0} → {1}".format("held" if not args.off else "held removed", pp))
    return 0


def _cmd_dismiss(args) -> int:
    stamped = dismiss(Path(args.dir))
    print("tide: dismissed {0} session(s)".format(len(stamped)))
    return 0


def _cmd_drop_thread(args) -> int:
    dest = drop_thread(Path(args.dir))
    print("tide: thread dropped → {0}".format(dest))
    return 0


def _cmd_validate(args) -> int:
    validate_step(Path(args.dir), args.step, who=args.who)
    print("tide: step {0} validated".format(args.step))
    return 0


def register_arc_verbs(arc_subparsers) -> None:
    """Attach the curate gestures under ``tide arc …`` (called from stream.register)."""
    hp = arc_subparsers.add_parser(
        "hold", help="☾ set a thread aside (stamps held:) / --off brings it back — reversible")
    hp.add_argument("--dir", required=True, help="the thread's entry dir (board contract)")
    hp.add_argument("--off", action="store_true", help="remove held: — back to the table")
    hp.set_defaults(func=_cmd_hold, _cmd="arc hold")

    dp = arc_subparsers.add_parser(
        "dismiss", help="✕ retire a head by hand (dismissed: on the session; closed thread → whole chain)")
    dp.add_argument("--dir", required=True, help="the session's entry dir (board contract)")
    dp.set_defaults(func=_cmd_dismiss, _cmd="arc dismiss")

    tp = arc_subparsers.add_parser(
        "drop", help="✕ soft-drop an EMPTY plan-less thread into arcs/__dropped__/ (guarded)")
    tp.add_argument("--dir", required=True, help="the thread's entry dir (board contract)")
    tp.set_defaults(func=_cmd_drop_thread, _cmd="arc drop")

    vp = arc_subparsers.add_parser(
        "validate", help="✓ validate a plan step's gate by hand ([x] + гейт-пройден + promote next [>])")
    vp.add_argument("--dir", required=True, help="the thread's entry dir (board contract)")
    vp.add_argument("--step", required=True, help="the step number in plan.md")
    vp.add_argument("--who", default="Гриша (с доски)", help="whose hand passed the gate")
    vp.set_defaults(func=_cmd_validate, _cmd="arc validate")

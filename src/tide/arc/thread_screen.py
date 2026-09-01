"""tide.arc.thread_screen — ``tide thread``: one screen a thread is understood from.

A thread's state was never anywhere. It was in ``plan.md`` (the steps), in
``decisions.md`` (what was concluded), in ``works/*/work.md`` (what is being done),
in ``artifacts/`` (what waits for the human) and in the nested session passports
(where the material of the last few days actually sits — read by nobody). A fresh
session got a handoff seed and no way to reach any of it, so it asked the human to
retell the thread, and the human retold it wrong. This module is the answer to
"покажи нить": ONE command, one screen, half a minute to read.

It is deliberately a projection, not a store — every line here is read off files
some other verb owns, so there is nothing to keep in sync and nothing new to write.
That is what makes it cheap enough to open by reflex: no network, no LLM, no index
(release decision 27 — точечно по запросу, структура задана заранее, не тормозит
оркестратора; the lore reports killed vector stores and graph layers on the same
requirement).

Alongside it lives the ONE check (release decision 28 — правило без машинной
проверки не правило), ``tide thread --check``, over the three rules this build
introduced:

1. a live promise has an owner — in force, not carried out, no work linked;
2. a live session says in one line what it holds (``## summary``);
3. a live record is filed where it says it is (its ``thread:`` names a real thread).

The check is scoped hard on purpose. It looks at LIVE things only — open threads,
open sessions, unclosed works — because a check that also scolds four years of
closed history prints a wall, and a wall gets switched off, which is worse than no
check at all. Rule 3 skips cross-project addresses (``<project>/<thread>``): they
resolve only through the roster of another home, and a check that cries when a
neighbour's roster is merely absent would be crying about our own ignorance.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from .. import fields, paths, resolve, slug
from . import decision, stream

# Section names read off the passports/goal docs.
SUMMARY_SECTION = "## summary"
CONTEXT_SECTION = "## context"
CURSOR_SECTION = "## cursor — resume here"

# How many rows a group may print before it collapses into "+N more". The screen
# is a doorway, not the archive — a list that scrolls has already failed at being
# one screen, and every group names the verb that shows the rest.
CAP = 7


class ThreadError(stream.StreamError):
    """A user-facing ``tide thread`` error (no thread, ambiguous ref, …)."""


class Finding(NamedTuple):
    """One line of the check: *where* it is, *what* is wrong, *how* to fix it."""

    rule: str       # decision-owner | arc-summary | filing
    where: str      # the record, human-addressable
    what: str       # what is missing or wrong
    fix: str        # the exact gesture that closes it


# --- thread resolution -------------------------------------------------------

def open_threads(root: Path) -> List[Path]:
    """Every OPEN top-level thread container, in stream order."""
    arcs = paths.arcs_dir(Path(root))
    if not arcs.is_dir():
        return []
    return [p for p in resolve.child_entries(arcs)
            if slug.is_entry(p.name) and not slug.is_closed_entry(p.name)]


def caller_thread(root: Path) -> Optional[Path]:
    """The thread container of the session that invoked us, or None.

    Same lookup ``decision`` uses ($CLAUDE_CODE_SESSION_ID → the pinned session
    arc → its container), so ``tide thread`` with no argument means the same
    thread as ``tide decision add`` with no argument.
    """
    return decision._caller_thread_dir(Path(root))


def resolve_thread(root: Path, ref: Optional[str] = None) -> Path:
    """The thread the screen is about: *ref*, else the caller's, else the only one.

    Falling back to "the only open thread" matters for the fresh install and for
    the human at a terminal that is not a tide session — with one thread in the
    home there is no ambiguity to protect, and demanding a name there is just a
    step between a person and their own state.
    """
    root = Path(root)
    if ref:
        entry = resolve.open_top_entry(root, ref)
        if entry is None:
            names = ", ".join(p.name for p in open_threads(root)) or "(none)"
            raise ThreadError(
                "thread: no open thread matching {0!r}. Open threads: {1}".format(ref, names))
        return entry
    caller = caller_thread(root)
    if caller is not None:
        return caller
    live = open_threads(root)
    if len(live) == 1:
        return live[0]
    if not live:
        raise ThreadError("thread: no open thread in {0}".format(paths.arcs_dir(root)))
    raise ThreadError(
        "thread: which one? name it — {0}".format(", ".join(p.name for p in live)))


# --- reading the pieces ------------------------------------------------------

def _section_body(text: str, header: str) -> str:
    """Body of *header*'s section with template placeholders dropped.

    A placeholder is an angle-bracketed blob, and in the session template it
    SPANS LINES (``<a few plain sentences: …\\n written on handoff…>``). Filtering
    only lines that begin with ``<`` therefore leaves the tail behind, and the
    screen then reports "written on handoff; longer if the session is large" as
    the summary of every arc — a passport that has said nothing looking exactly
    like one that has. So the skip runs from the opening ``<`` to the line that
    closes it.
    """
    if header not in text:
        return ""
    body = text.partition(header)[2].split("\n## ", 1)[0]
    keep: List[str] = []
    inside = False
    for raw in body.splitlines():
        ln = raw.strip()
        if inside:
            inside = not ln.endswith(">")
            continue
        if not ln:
            continue
        if ln.startswith("<"):
            inside = not ln.endswith(">")
            continue
        keep.append(ln)
    return " ".join(keep).strip()


def thread_goal(tdir: Path) -> str:
    """The thread's one-line goal, off its ``*-goal.md`` (or ``arc.md``)."""
    for f in sorted(Path(tdir).glob("*-goal.md")) + [Path(tdir) / "arc.md"]:
        if f.is_file():
            got = (fields.read_field(f, "goal") or "").strip()
            if got and not got.startswith("<"):
                return got
    return ""


def sessions(tdir: Path) -> List[Dict[str, object]]:
    """The thread's sessions, newest first, with the line each one says about itself.

    Newest first because that is where the thread actually is; the older ones are
    what "where to look" points at. ``summary`` is the whole point of the row — an
    orchestrator picks which arc to walk into by reading these and nothing else —
    so a passport still carrying the template placeholder comes back with ``""``
    rather than the placeholder, and the screen says so out loud.
    """
    sub = Path(tdir) / paths.ARCS_DIRNAME
    out: List[Dict[str, object]] = []
    for p in sorted(sub.iterdir(), reverse=True) if sub.is_dir() else []:
        f = p / "arc.md"
        if not p.is_dir() or not slug.is_entry(p.name) or not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        out.append({
            "dir": p,
            "name": p.name,
            "closed": slug.is_closed_entry(p.name),
            "summary": _section_body(text, SUMMARY_SECTION),
            "cursor": _section_body(text, CURSOR_SECTION),
            "goal": (fields.read_field(f, "goal") or "").strip(),
            "pulses": len([ln for ln in
                           text.partition(CONTEXT_SECTION)[2].split("\n## ", 1)[0].splitlines()
                           if ln.strip().startswith("- ")]),
            "session_id": (fields.read_field(f, "claude-session") or "").strip(),
        })
    return out


def plan_state(root: Path, tdir: Path) -> Tuple[List[int], List[Tuple[int, str, str]]]:
    """``(current step numbers, all steps)`` off the thread's ``plan.md``.

    Reuses the reader ``work`` already owns — the plan is one file with one
    format, and a second parser for it would be a second truth.
    """
    from . import work

    try:
        return (work.current_plan_steps(Path(root), tdir.name),
                work.plan_steps(Path(root), tdir.name))
    except Exception:       # noqa: BLE001 — a malformed plan must not eat the screen
        return [], []


def thread_final(tdir: Path) -> str:
    """The thread's ``final:`` line off ``plan.md`` — what "finished" means here.

    Separate from the goal: the goal says what the thread is about, the final says
    what state the world is in when it can be closed. "What is left between today
    and the end" is unanswerable without it, and it sits one line into a file
    nobody was reading.
    """
    plan = Path(tdir) / "plan.md"
    if not plan.is_file():
        return ""
    try:
        m = re.search(r"^final:\s*(.+?)(?=\n\n|\n##|\Z)",
                      plan.read_text(encoding="utf-8", errors="ignore"), re.M | re.S)
    except OSError:
        return ""
    return " ".join(m.group(1).split()) if m else ""


def thread_works(root: Path, tdir: Path) -> List[Dict[str, object]]:
    """The thread's live works — ``[]`` when there are none or they don't read.

    Reads the directory rather than asking the plugin registry whether works are
    switched on: a projection reports what is ON DISK. A home with the plugin off
    has no cards and gets an empty list for free, and a home that has cards but
    switched the plugin off is better told they exist than left to wonder.
    """
    from . import work

    try:
        return work.thread_works(Path(root), tdir.name, live_only=True)
    except Exception:       # noqa: BLE001
        return []


def desk(root: Path) -> List[Tuple[str, str]]:
    """Artifacts still on the human's table as ``(name, caption)`` — the ask queue.

    Home-wide rather than per-thread: the table is one place by design (release
    decision 07 — гейты кладутся на стол, а не в карточку), so a thread screen
    that filtered it would hide the very question the человек is being waited on for.
    """
    from . import artifact

    adir = artifact.artifacts_dir(Path(root))
    out: List[Tuple[str, str]] = []
    for p in sorted(adir.iterdir(), reverse=True) if adir.is_dir() else []:
        f = p / "artifact.md"
        if not p.is_dir() or not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            if artifact._status_of(text) == artifact.TAKEN:
                continue
            out.append((p.name, artifact._head_caption(text) or p.name))
        except Exception:   # noqa: BLE001
            continue
    return out


# --- the screen --------------------------------------------------------------

def _clip(text: str, limit: int) -> str:
    """One line of at most *limit* chars, ellipsis when it had more to say."""
    one = " ".join((text or "").split())
    return one if len(one) <= limit else one[:limit - 1].rstrip() + "…"


def _capped(rows: List[str], more_hint: str) -> List[str]:
    """*rows* truncated to :data:`CAP`, with a line naming the verb for the rest."""
    if len(rows) <= CAP:
        return rows
    return rows[:CAP] + ["    (+{0} more — {1})".format(len(rows) - CAP, more_hint)]


def render(root: Path, ref: Optional[str] = None) -> str:
    """The whole screen for one thread, as text.

    Order is the order the questions get asked: where are we going · where are we
    on the way · what has been concluded and what of it is still only a promise ·
    what is moving right now · what is stuck on the human · where the material is.
    The check summary is the last line because it is hygiene, not news.
    """
    root = Path(root)
    tdir = resolve_thread(root, ref)
    name = slug.entry_slug(tdir.name) or tdir.name
    out: List[str] = ["{0} — thread {1}".format(name, tdir.name)]

    goal = thread_goal(tdir)
    out.append("goal: {0}".format(goal or "— (not set)"))
    final = thread_final(tdir)
    if final:
        out.append("final: {0}".format(_clip(final, 200)))

    # --- plan
    current, steps = plan_state(root, tdir)
    if steps:
        from . import work

        closed = [s for s in steps if s[1] == "x"]
        retired = [s for s in steps if s[1] == work.RETIRED_STEP]
        left = [s for s in steps if s[1] not in ("x", work.RETIRED_STEP)]
        head = "plan · {0} steps, {1} closed".format(len(steps), len(closed))
        if retired:
            head += ", {0} retired".format(len(retired))
        head += " · {0} left".format(len(left)) if left else " · all closed"
        out += ["", head]
        for num, _state, title in left:
            mark = "▸" if num in current else " "
            out.append("  {0} {1:<3}{2}".format(mark, num, _clip(title, 72)))
        if not current:
            out.append("    ▸ none — plan.md marks no step [>] and has no "
                       "'## текущий шаг' line, so nobody has said where we are")
    else:
        out += ["", "plan · none ({0}/plan.md)".format(tdir.name)]

    # --- decisions
    out += [""] + _decisions_block(root, tdir)

    # --- what is happening right now, in the newest live session's own words
    live = [s for s in sessions(tdir) if not s["closed"]]
    if live and str(live[0]["cursor"]):
        out += ["", "now · {0}".format(_clip(str(live[0]["cursor"]), 96)),
                "    ({0}, its own cursor)".format(live[0]["name"])]

    # --- works in flight / waiting on the human
    out += [""] + _works_block(root, tdir)

    # --- sessions: which arc holds what
    out += [""] + _sessions_block(tdir)

    # --- where the material is: the real paths, not the shape of a path
    out += ["", "where to look", "  {0}/plan.md · decisions.md".format(tdir)]
    for s in sessions(tdir)[:3]:
        ws = Path(str(s["dir"])) / "workspace"
        files = sorted(p.name for p in ws.iterdir() if p.is_file()) if ws.is_dir() else []
        if not files:
            continue
        out.append("  {0}/workspace/ — {1}".format(
            s["name"], _clip(" · ".join(files), 84)))
    out.append("  full paths under {0}/{1}/".format(tdir, paths.ARCS_DIRNAME))

    # --- hygiene, last
    found = check(root, [tdir])
    out += ["", "check · {0}".format(
        "clean" if not found else "{0} finding{1} — tide thread --check".format(
            len(found), "" if len(found) == 1 else "s"))]
    return "\n".join(out)


def _decisions_block(root: Path, tdir: Path) -> List[str]:
    """Decisions grouped by state — accepted-but-not-done first, because that hurts."""
    try:
        recs = decision.list_decisions(root, thread_ref=tdir.name)
    except decision.DecisionError:
        recs = []
    if not recs:
        return ["decisions · none"]
    live = [d for d in recs if decision.in_force(d)]
    done = [d for d in live if decision.is_done(d)]
    rules = [d for d in live if decision.is_rule(d) and not decision.is_done(d)]
    owing = [d for d in live if not decision.is_done(d) and not decision.is_rule(d)]
    retired = [d for d in recs if not decision.in_force(d)]
    out = ["decisions · {0} total: {1} in force ({2} carried out, {3} not, "
           "{4} standing rules){5}".format(
               len(recs), len(live), len(done), len(owing), len(rules),
               " · {0} retired".format(len(retired)) if retired else "")]
    # The painful list first, and it is the one the whole build exists for: signed,
    # binding, and nobody has said it was done. Each line names its owner, because
    # "who is carrying this" is the next question every time.
    if owing:
        out.append("  in force, NOT carried out:")
        out += _capped(
            ["    {0} {1}{2}".format(
                d["num"], _clip(str(d.get("what") or d["slug"]), 62),
                "  → work {0}".format(decision.owner(d)) if decision.owner(d)
                else "  → no work")
             for d in owing],
            "tide decision list --state not-done")
    if rules:
        out.append("  standing rules — {0}: {1}".format(
            len(rules), ", ".join(str(d["num"]) for d in rules)))
    if done:
        out.append("  carried out — {0}, each says what shows it: tide decision "
                   "list --state done".format(len(done)))
    for state in (decision.SUPERSEDED, decision.DROPPED):
        same = [d for d in retired if d.get("status") == state]
        if same:
            out.append("  {0} — {1}: {2}".format(
                state, len(same), ", ".join(str(d["num"]) for d in same)))
    # What the thread already REJECTED lives in each record's `closes:` line — the
    # anti-re-litigation body. There are as many of those as there are decisions,
    # so the screen names the door instead of printing thirty-one of them.
    closed_off = sum(1 for d in recs if str(d.get("closes") or "").strip() not in ("", "—", "-"))
    if closed_off:
        out.append("  already rejected — {0} settled arguments, don't re-open: "
                   "tide decision list --closes".format(closed_off))
    return out


def _works_block(root: Path, tdir: Path) -> List[str]:
    """What is moving, and what is standing on the human's word."""
    works = thread_works(root, tdir)
    if not works:
        return ["works · none live in this thread"]
    waiting = [w for w in works if w.get("status") == "review"]
    moving = [w for w in works if w.get("status") != "review"]
    out: List[str] = []
    if moving:
        out.append("in flight · {0}".format(len(moving)))
        out += _capped(
            ["    {0} {1:<8} {2}/{3}{4}  {5}".format(
                w["num"], w["status"], w["done"], w["total"],
                "  step " + str(w["step"]) if w["step"] else "",
                _clip(str(w["title"]), 52)) for w in moving],
            "tide work list")
    if waiting:
        out.append("waiting on you · {0} work{1} in review".format(
            len(waiting), "" if len(waiting) == 1 else "s"))
        out += _capped(
            ["    {0} {1}/{2}  {3}".format(w["num"], w["done"], w["total"],
                                           _clip(str(w["title"]), 60)) for w in waiting],
            "tide work list")
        out.append("    close with: tide work close <NN> --word \"…\"")
    table = desk(root)
    if table:
        out.append("  on your table · {0}".format(len(table)))
        out += _capped(["    {0}  {1}".format(n, _clip(c, 56)) for n, c in table],
                       "tide artifact list")
    return out


def _sessions_block(tdir: Path) -> List[str]:
    """One line per session saying what it holds — the orchestrator's index."""
    ss = sessions(tdir)
    if not ss:
        return ["sessions · none"]
    live = [s for s in ss if not s["closed"]]
    out = ["sessions · {0} ({1} open, newest first)".format(len(ss), len(live))]
    rows = []
    for s in ss:
        n = int(s["pulses"] or 0)
        line = str(s["summary"]) or "no summary — {0} pulse{1} in ## context".format(
            n, "" if n == 1 else "s")
        rows.append("    {0:<16}{1}{2}".format(
            s["name"], "(closed) " if s["closed"] else "", _clip(line, 62)))
    return out + _capped(rows, "ls {0}/{1}".format(tdir, paths.ARCS_DIRNAME))


# --- the one check -----------------------------------------------------------

def _thread_key(ref: str) -> str:
    """Comparable key of a thread address — number and closed-marker peeled off."""
    tail = (ref or "").strip().rstrip("/").rsplit("/", 1)[-1]
    return slug.entry_slug(tail) or tail


def _known_thread_keys(root: Path) -> set:
    """Every thread in this home by comparable key — closed ones included.

    Closed threads count as known: a done work that names the thread it was done
    in is filed correctly, and calling that an error would be the check inventing
    a problem out of the passage of time.
    """
    arcs = paths.arcs_dir(Path(root))
    if not arcs.is_dir():
        return set()
    return {_thread_key(p.name) for p in resolve.child_entries(arcs) if slug.is_entry(p.name)}


def _check_decisions(root: Path, tdir: Path) -> List[Finding]:
    """Rule 1 — a decision has a state, and a claimed state says what proves it."""
    try:
        recs = decision.list_decisions(root, thread_ref=tdir.name)
    except decision.DecisionError:
        return []
    home = _thread_key(tdir.name)
    out: List[Finding] = []
    for d in recs:
        where = "decision {0} {1}".format(d["num"], d["slug"])
        if decision.unowned(d):
            out.append(Finding(
                "decision-owner", where,
                "in force, not carried out, and no work is carrying it",
                "tide decision accept {0} --work <NN>   (a standing criterion "
                "instead? --rule)".format(d["num"])))
        elif decision.unproved(d):
            out.append(Finding(
                "decision-owner", where,
                "marked {0} but nothing shows it — no work, no proof".format(
                    "carried out" if decision.is_done(d) else str(d.get("status"))),
                "tide decision done {0} --work <NN> | --proof \"…\"".format(d["num"])))
        # Rule 3, decisions half: a record's thread: must name its own home.
        told = str(d.get("thread") or "").strip()
        if told and told not in ("—", "-") and "/" not in told \
                and _thread_key(told) != home:
            out.append(Finding(
                "filing", where,
                "filed in {0} but says thread: {1}".format(tdir.name, told),
                "move the record to the thread it names, or fix its thread: line"))
    return out


def _check_sessions(tdir: Path) -> List[Finding]:
    """Rule 2 — a LIVE session says in one line what it holds.

    Open sessions only, and only ones that have done something (a pulse or a
    cursor). A session that never wrote a line owes no summary, and a closed one
    is history — demanding either is how a check becomes noise and gets muted.
    """
    out: List[Finding] = []
    for s in sessions(tdir):
        if s["closed"] or s["summary"]:
            continue
        if not s["pulses"] and not s["cursor"]:
            continue
        n = int(s["pulses"] or 0)
        out.append(Finding(
            "arc-summary", "arc {0}/{1}".format(tdir.name, s["name"]),
            "nothing says what this arc holds{0}".format(
                " ({0} pulse{1} inside)".format(n, "" if n == 1 else "s") if n else ""),
            "tide offload {0} --summary \"…\"".format(slug.entry_slug(str(s["name"])))))
    return out


def _check_filing(root: Path) -> List[Finding]:
    """Rule 3, works half — a LIVE work names a thread that exists.

    Cross-project addresses (``<project>/<thread>``) are skipped: verifying one
    means reading another home's roster, and a finding that fires because a
    neighbour is merely unreachable is a false alarm about our own ignorance.
    Closed works are skipped for the same reason as closed sessions.
    """
    from . import work

    known = _known_thread_keys(root)
    out: List[Finding] = []
    wdir = work.works_dir(Path(root))
    for p in sorted(wdir.iterdir()) if wdir.is_dir() else []:
        f = p / "work.md"
        if not p.is_dir() or not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            if work._status_of(text) not in work.LIVE:
                continue
        except Exception:   # noqa: BLE001 — a broken card is not this check's news
            continue
        m = re.search(r"^thread:\s*(.*)$", text, re.M)
        told = (m.group(1).strip() if m else "")
        if told in ("", "—", "-"):
            out.append(Finding(
                "filing", "work {0}".format(p.name),
                "no thread address — it belongs to no thread on the board",
                "tide work thread {0} <thread>".format(p.name.partition("-")[0])))
        elif "/" not in told and _thread_key(told) not in known:
            out.append(Finding(
                "filing", "work {0}".format(p.name),
                "names thread {0!r}, which does not exist here".format(told),
                "tide work thread {0} <thread>".format(p.name.partition("-")[0])))
    return out


def check(root: Path, tdirs: Optional[List[Path]] = None) -> List[Finding]:
    """Every finding across *tdirs* (default: the caller's thread).

    Work filing is home-wide, not per-thread, precisely because a work with no
    thread address belongs to no thread — scoping that rule to a thread would
    guarantee it never fires on the records that need it most.
    """
    root = Path(root)
    targets = tdirs if tdirs is not None else [resolve_thread(root)]
    out: List[Finding] = []
    for tdir in targets:
        out += _check_decisions(root, tdir)
        out += _check_sessions(tdir)
    return out + _check_filing(root)


def render_check(root: Path, ref: Optional[str] = None, every: bool = False) -> str:
    """The check as a short red list — or one green line when there is nothing."""
    root = Path(root)
    targets = open_threads(root) if every else [resolve_thread(root, ref)]
    found = check(root, targets)
    scope = "all open threads" if every else targets[0].name
    if not found:
        return "tide: {0} — clean (owner · summary · filing)".format(scope)
    out = ["tide: {0} — {1} finding{2}".format(scope, len(found),
                                               "" if len(found) == 1 else "s")]
    for rule in ("decision-owner", "arc-summary", "filing"):
        rows = [f for f in found if f.rule == rule]
        if not rows:
            continue
        out.append("")
        out.append("{0} ({1})".format(rule, len(rows)))
        # Capped even here. A check that prints thirty identical lines is read
        # once and muted forever, and a muted check is worse than none — the
        # rest are the same finding, and the fix on the first row closes them all.
        for f in rows[:CAP]:
            out.append("  {0} — {1}".format(f.where, f.what))
            out.append("      fix: {0}".format(f.fix))
        if len(rows) > CAP:
            out.append("  (+{0} more of the same)".format(len(rows) - CAP))
    return "\n".join(out)


# --- CLI wiring --------------------------------------------------------------

def _cmd_thread(args) -> int:
    root = paths.require_tide_root()
    if getattr(args, "check", False) or getattr(args, "every", False):
        print(render_check(root, ref=getattr(args, "ref", None),
                           every=getattr(args, "every", False)))
        return 0
    print(render(root, ref=getattr(args, "ref", None)))
    return 0


def register(subparsers) -> None:
    """Add the top-level ``thread`` command (called by cli.py)."""
    p = subparsers.add_parser(
        "thread",
        help="one screen a thread is understood from: goal, plan step, decisions "
             "by state, what is moving, what waits for you (--check: the hygiene check)",
    )
    p.add_argument("ref", nargs="?",
                   help="thread slug (default: the caller session's own, or the only one)")
    p.add_argument("--check", action="store_true",
                   help="instead of the screen: decisions with no state, live arcs "
                        "with no summary, records filed under a thread that isn't theirs")
    p.add_argument("--all", dest="every", action="store_true",
                   help="with --check: sweep every open thread, not just this one")
    p.set_defaults(func=_cmd_thread, _cmd="thread")

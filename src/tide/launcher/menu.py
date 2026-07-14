"""tide.launcher.menu — the ``tide menu`` launcher: pick N projects, spawn sessions.

The human entry point into a work session: from a control-home, list the roster,
let the human pick one or more projects, then for EACH picked project build a seed
(:mod:`tide.launcher.seed`) and open a fresh **orchestrator** session in a new
terminal via the configured adapter (:mod:`tide.adapters`).

(The bare ``tide`` command keeps printing help — see ``cli.py``; the menu lives
under the explicit ``tide menu`` subcommand so it never blocks an empty
invocation on stdin.)

The pure pieces — :func:`render_menu`, :func:`parse_selection`,
:func:`select_entries`, :func:`launch_entries` — are argparse-free and unit-
testable (a dry-run adapter proves the wiring without opening a terminal);
:func:`cmd_menu` is the thin interactive handler.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from .. import fields, paths, roster, slug
from ..adapters import SETTINGS_KEY, SpawnResult, get_adapter, resolve_from_settings
from ..adapters.base import persist_seed
from ..arc import stream
from ..arc.stream import StreamError
from . import context, seed, select

# Placeholder seed-file token used in dry-run (nothing is persisted to disk then),
# so the printed command shows the @<seed-file> shape without a real temp path.
DRY_RUN_SEED_FILE = "<seed-file>"

DEFAULT_ROLE = seed.ROLE_ORCHESTRATOR

# Picker sessions are head/orchestrator sessions (like `tide terminal`), so they
# default to skipping the permission prompts. Spliced right after the program
# name; toggled off with `tide menu --no-skip-permissions`.
SKIP_PERMISSIONS = "--dangerously-skip-permissions"


class MenuError(StreamError):
    """A menu/selection error (empty roster, out-of-range or unparsable pick).

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on
    the same ``except`` arm (prints ``tide: …``, exits nonzero).
    """


# --- listing + selection (pure) --------------------------------------------

def list_entries(root: Path) -> List[Dict[str, str]]:
    """The control-home roster as ``[{'name','path'}, …]`` (file order)."""
    return roster.read_roster(root)


def render_pending_handoffs(root: Path, entries: List[Dict[str, str]]) -> str:
    """A banner of OFFERED handoffs hanging in the control-home, with a pickup hint.

    Empty string when nothing is offered — so it adds no noise to an ordinary menu.
    Each line carries the offer + the one-shot command to pick it up (cd the owning
    project + launch claude with the seed); the first message there confirms it (the
    UserPromptSubmit hook flips offered → taken). Full arrow-pick from the menu is a
    later slice (candidate handoff-skill-uses-offer); this makes them VISIBLE now.
    """
    from .. import handoff_queue  # lazy: avoid import cycle at module load

    pending = handoff_queue.list_offers(root, status=handoff_queue.STATUS_OFFERED)
    if not pending:
        return ""
    by_name = {e["name"]: e.get("path", "?") for e in entries}
    lines = ["⌛ pending handoffs (offered — pick up to resume):"]
    for r in pending:
        lines.append("  {0}  [{1}]  {2} · arc {3}".format(
            r["name"], r["mode"], r["project"], r["arc"]))
        proj_path = by_name.get(r["project"], "<{0}-path>".format(r["project"]))
        if r["seed"] and r["seed"] != "-":
            lines.append('    pick up: cd {0} && claude --dangerously-skip-permissions '
                         '"$(cat {1})"'.format(proj_path, r["seed"]))
    lines.append("  (first message in that session confirms it · `tide handoffs list`)")
    return "\n".join(lines)


def is_active(entry: Dict[str, str]) -> bool:
    """True unless the entry carries ``status=archived`` (default is active)."""
    return entry.get("status", roster.STATUS_ACTIVE) != roster.STATUS_ARCHIVED


def active_entries(entries: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """The subset of *entries* that are active (archived projects filtered out)."""
    return [e for e in entries if is_active(e)]


def render_menu(entries: List[Dict[str, str]]) -> str:
    """Render the numbered pick-list (``1) name → path``) or an empty-roster note.

    Archived entries (only ever shown via ``--all``) are tagged ``[archived]`` so
    the human sees why a normally-hidden project is in the list.
    """
    if not entries:
        return "(roster is empty — add a project: tide roster add <name> <path>)"
    lines = ["Pick project(s) to lead this session:"]
    for i, e in enumerate(entries, start=1):
        tag = "" if is_active(e) else "  [archived]"
        lines.append("  {0}) {1} → {2}{3}".format(i, e["name"], e["path"], tag))
    return "\n".join(lines)


def parse_selection(raw: str, count: int) -> List[int]:
    """Parse a pick string into sorted unique 1-based indices, validated to *count*.

    Accepts comma- and/or space-separated numbers (``"1,3"`` / ``"1 3"``) and the
    keyword ``all``. Raises :class:`MenuError` on an empty, non-numeric, or
    out-of-range pick.
    """
    s = (raw or "").strip().lower()
    if not s:
        raise MenuError("menu: empty selection (pick e.g. '1' or '1,3' or 'all')")
    if s == "all":
        return list(range(1, count + 1))

    picks: List[int] = []
    for tok in re.split(r"[,\s]+", s):
        if not tok:
            continue
        if not tok.isdigit():
            raise MenuError("menu: invalid pick {0!r} (want numbers or 'all')".format(tok))
        n = int(tok)
        if not (1 <= n <= count):
            raise MenuError(
                "menu: pick {0} out of range (1..{1})".format(n, count)
            )
        if n not in picks:
            picks.append(n)
    if not picks:
        raise MenuError("menu: no projects selected")
    return sorted(picks)


def select_entries(entries: List[Dict[str, str]], raw: str) -> List[Dict[str, str]]:
    """Resolve a pick string against *entries* into the chosen entry dicts."""
    if not entries:
        raise MenuError("menu: roster is empty — nothing to pick")
    picks = parse_selection(raw, len(entries))
    return [entries[i - 1] for i in picks]


# --- thread (тред) + session selection ------------------------------------
# After a project is picked, the human binds the session in TWO steps: pick a
# THREAD (тред — the arc through which a work-line is managed), then a SESSION
# inside it (continue one, or start new). At each step `0` ALWAYS means "+ new".
# The chosen session's passport becomes the seed's arc_text (sessions live in a
# thread substream that the top-stream read_arc_passport would miss); the thread
# name frames the seed. See tide.arc.stream.

PICK_NEW = "0"  # the universal "new" pick — 0 is always new


def _ask(prompt: str) -> str:
    """input() that treats EOF (piped/empty stdin) as an empty answer."""
    try:
        return input(prompt)
    except EOFError:
        return ""


def _offered_thread_slugs(project: Path) -> set:
    """Bare slugs of threads a LIVE offered handoff points into (cand 28).

    Resolves this *project*'s roster name by path, then collects the thread
    part of every ``offered`` record's arc ref. Fully defensive: any failure
    (no control-home, no roster, empty queue) is just an empty set — this only
    ever ADDS visibility, never hides or breaks the picker.
    """
    from .. import handoff_queue  # lazy: avoid import cycle at module load
    try:
        home = paths.control_home()
        pending = handoff_queue.list_offers(home, status=handoff_queue.STATUS_OFFERED)
        if not pending:
            return set()
        want = Path(project).expanduser().resolve()
        names = {
            e["name"] for e in roster.read_roster(home)
            if Path(e.get("path", "")).expanduser().resolve() == want
        }
    except (OSError, StreamError):
        return set()
    out = set()
    for r in pending:
        if r.get("project") not in names:
            continue
        top = str(r.get("arc") or "").split("/", 1)[0]
        if top and top != "-":
            out.add(slug.entry_slug(top))
    return out


def list_threads(project: Path) -> List[Dict[str, str]]:
    """A project's open threads for the picker, newest-first.

    ``stream.thread_entries`` numbers the stream NN ascending (oldest first);
    the picker reverses it so the freshest thread sits at the top, like sessions.
    Drafts (unfilled template shells, cand 04) are NOT offered — fill the goal
    (the entry activates on the next read) or sweep with ``tide arc gc``; the
    board still shows them as ``[draft]`` so nothing vanishes silently.

    EXCEPT a draft with a LIVE offered handoff inside (cand 28): a thread the
    handoff machinery just seeded is somebody's real intent mid-flight — hiding
    it strands the pickup ('а где тред-то?'). It stays pickable, ⌛-marked.
    """
    offered = _offered_thread_slugs(project)
    out = []
    for entry in stream.thread_entries(project):
        has_offer = slug.entry_slug(entry.name) in offered
        if not stream.goal_filled(entry) and not has_offer:
            continue
        goal = (fields.read_field(stream.passport_path(entry), "goal") or "").strip()
        out.append({
            "slug": slug.entry_slug(entry.name),
            "name": entry.name,
            "goal": goal,
            "path": str(entry),
            "offered": "⌛" if has_offer else "",
        })
    out.reverse()  # newest-first for the picker
    return out


def list_sessions(project: Path, thread_slug: str) -> List[Dict[str, str]]:
    """A thread's open sessions newest-first: ``[{slug, name, title, from, path}, …]``.

    ``stream.session_entries`` numbers the substream NN ascending (oldest first —
    chaining relies on that order); the picker reverses it so the freshest session
    — the one a handoff just seeded — sits at the top, older ones aging downward.
    """
    out = []
    for entry in stream.session_entries(project, thread_slug):
        pp = entry / "arc.md"
        frm = (fields.read_field(pp, "from") or "").strip()
        title = (fields.read_field(pp, "title") or "").strip()
        out.append({
            "slug": slug.entry_slug(entry.name),
            "name": entry.name,
            "title": "" if title.startswith("<") else title,
            "from": frm,
            "path": str(entry),
        })
    out.reverse()  # newest-first for the picker
    return out


def render_thread_menu(project_name: str, threads: List[Dict[str, str]]) -> str:
    """Numbered thread pick-list for *project_name*; ``0`` is the '+ new' row."""
    lines = ["Thread (тред) for {0} — 0 = new thread, or continue one:".format(project_name)]
    lines.append("  0) + new thread")
    for i, p in enumerate(threads, start=1):
        goal = p.get("goal") or ""
        if goal and not goal.startswith("<"):
            suffix = " — {0}".format(goal)
        elif p.get("offered"):
            suffix = " — ⌛ offered handoff inside"
        else:
            suffix = ""
        lines.append("  {0}) {1}{2}".format(i, p["slug"], suffix))
    return "\n".join(lines)


def render_session_menu(thread_slug: str, sessions: List[Dict[str, str]]) -> str:
    """Numbered session pick-list inside *thread_slug*, with from→ lineage; ``0`` = new."""
    lines = ["Session in thread {0} — 0 = new session, or continue one:".format(thread_slug)]
    lines.append("  0) + new session")
    for i, s in enumerate(sessions, start=1):
        title = " — {0}".format(s["title"]) if s.get("title") else ""
        lineage = " (from {0})".format(s["from"]) if s.get("from") else ""
        lines.append("  {0}) {1}{2}{3}".format(i, s["slug"], title, lineage))
    return "\n".join(lines)


def _thread_label(p: Dict[str, str]) -> str:
    """One thread row's label for the arrow picker — numeric index first: ``NN  slug — goal``.

    A thread surfaced only because of its live offer (draft goal, cand 28) says
    so: ``⌛ offered handoff inside`` replaces the missing goal suffix.
    """
    index = p["name"].split("-", 1)[0] if p.get("name") else ""
    goal = p.get("goal") or ""
    if goal and not goal.startswith("<"):
        suffix = " — {0}".format(goal)
    elif p.get("offered"):
        suffix = " — ⌛ offered handoff inside"
    else:
        suffix = ""
    head = "{0}  ".format(index) if index else ""
    return "{0}{1}{2}".format(head, p["slug"], suffix)


ROUTINE_MARKER = "⚙ "  # routine rows are gear-marked so they read differently from tasks


def list_routines(project: Path) -> List[Dict[str, str]]:
    """A project's open routines for the picker: ``[{slug, name, goal, path}, …]``.

    Drafts (no real goal/steps yet, cand 04) are NOT offered — a runbook-less
    routine cannot be run; fill it or sweep with ``tide arc gc``.
    """
    out = []
    for entry in stream.routine_entries(project):
        if not stream.goal_filled(entry):
            continue
        goal = (fields.read_field(stream.passport_path(entry), "goal") or "").strip()
        out.append({
            "slug": slug.entry_slug(entry.name),
            "name": entry.name,
            "goal": goal,
            "path": str(entry),
        })
    out.reverse()  # newest-first for the picker
    return out


def _routine_label(r: Dict[str, str]) -> str:
    """One routine row's label for the arrow picker — gear-marked: ``⚙ NN  slug — goal``.

    The ⚙ marker sets routines apart from tasks visually (routines have nothing to
    do with threads); numeric index first, like the thread/session rows.
    """
    index = r["name"].split("-", 1)[0] if r.get("name") else ""
    goal = r.get("goal") or ""
    suffix = " — {0}".format(goal) if goal and not goal.startswith("<") else ""
    head = "{0}  ".format(index) if index else ""
    return "{0}{1}{2}{3}".format(ROUTINE_MARKER, head, r["slug"], suffix)


def _session_label(s: Dict[str, str]) -> str:
    """One session row's label for the arrow picker — numeric index first.

    ``NN  title (from …)`` once a title exists (set by offload/handoff), else
    ``NN  slug`` for a still-unnamed session.
    """
    index = s["name"].split("-", 1)[0]  # the NN prefix of NN-slug
    label = s["title"] if s.get("title") else s["slug"]
    lineage = " (from {0})".format(s["from"]) if s.get("from") else ""
    return "{0}  {1}{2}".format(index, label, lineage)


def parse_pick(raw: str, count: int):
    """Map a pick to :data:`PICK_NEW` or a 1-based index.

    ``0`` (also empty / ``new``) → new; a number in ``1..count`` → that row.
    Raises :class:`MenuError` on garbage or out-of-range.
    """
    s = (raw or "").strip().lower()
    if s in ("", "0", "n", "new", "+"):
        return PICK_NEW
    if not s.isdigit():
        raise MenuError("pick: invalid {0!r} (0 for new, or a number)".format(raw))
    n = int(s)
    if 1 <= n <= count:
        return n
    raise MenuError("pick: {0} out of range (0 for new, 1..{1})".format(n, count))


def _create_thread(project: Path, name: str) -> Optional[str]:
    name = (name or "").strip()
    if not name:
        return None
    # The human's typed idea IS the thread's goal — write it, don't discard it.
    # Otherwise the fresh container is born an unfilled draft and the picker
    # hides what they just created (gates, cand 04).
    entry = stream.new_thread(project, name, goal=name)
    return slug.entry_slug(entry.name)


def _create_routine(project: Path, name: str) -> Optional[str]:
    name = (name or "").strip()
    if not name:
        return None
    entry = stream.new_routine(project, name, goal=name)
    return slug.entry_slug(entry.name)


def _create_session(project: Path, thread_slug: str, name: str):
    name = (name or "").strip() or "session"
    entry = stream.new_session(project, thread_slug, name)
    return slug.entry_slug(entry.name), str(entry)


def _resolve_thread(project, project_name, *, thread_ref, new_thread, interactive):
    """Continue/create a thread. A flag wins; else interactive 0=new pick; else None."""
    if new_thread:
        return _create_thread(project, new_thread)
    if thread_ref:
        return thread_ref
    if not interactive:
        return None
    threads = list_threads(project)
    choice = select.select(
        "Thread (тред) for {0} — continue one, or start new:".format(project_name),
        [_thread_label(p) for p in threads],
        allow_new=True,
        new_label="+ new thread",
    )
    if choice == select.NEW:
        return _create_thread(project, _ask("new thread name> "))
    return threads[choice]["slug"]


def _resolve_session(project, thread_slug, *, session_ref, new_session, interactive):
    """Continue/create a session inside *thread_slug*. Returns (slug, path, is_new).

    ``is_new`` is True when the session was just created (so it gets a fresh pinned
    claude session-id); False when continuing an existing one (so it resumes).
    """
    if new_session:
        slug_, path_ = _create_session(project, thread_slug, new_session)
        return slug_, path_, True
    if session_ref:
        for s in list_sessions(project, thread_slug):
            if s["slug"] == session_ref:
                return session_ref, s["path"], False
        return session_ref, None, False
    if not interactive:
        # entering a thread non-interactively means a fresh session in it
        slug_, path_ = _create_session(project, thread_slug, "session")
        return slug_, path_, True
    sessions = list_sessions(project, thread_slug)
    choice = select.select(
        "Session in thread {0} — continue one, or start new:".format(thread_slug),
        [_session_label(s) for s in sessions],
        allow_new=True,
        new_label="+ new session",
    )
    if choice == select.NEW:
        # No name prompt — sessions are auto-named (NN-session); the human title
        # is written later by offload/handoff (the `title:` field).
        slug_, path_ = _create_session(project, thread_slug, "")
        return slug_, path_, True
    chosen = sessions[choice]
    return chosen["slug"], chosen["path"], False


def _project_cwd_of(session_path) -> Optional[Path]:
    """The project dir a session lives under (parent of ``.tide/``), or None."""
    for parent in Path(session_path).resolve().parents:
        if parent.name == ".tide":
            return parent.parent
    return None


def _claude_conversation_exists(session_path, session_id: str) -> bool:
    """True when claude has a PERSISTED conversation for *session_id* in this project.

    claude stores each conversation at
    ``~/.claude/projects/<cwd-with-/-and-.-as-dashes>/<session-id>.jsonl``. A pinned
    id whose conversation was never actually engaged has no such file, so
    ``--resume`` would fail ("No conversation found") and only recover via a fallback
    that flashes that scary error — so we treat "no file" as **launch fresh** instead.
    """
    proj = _project_cwd_of(session_path)
    if proj is None:
        return False
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
    encoded = str(proj).replace("/", "-").replace(".", "-")
    return (base / "projects" / encoded / "{0}.jsonl".format(session_id)).is_file()


def _bind_claude_session(session_path, *, is_new):
    """Resolve the pinned claude session-id for a session → (session_id, resume).

    Resume only when the session carries an id AND claude actually has that
    conversation persisted (:func:`_claude_conversation_exists`) — a pinned-but-
    never-engaged id has no conversation, so ``--resume`` would fail and flash "No
    conversation found"; we launch **fresh** there instead (clean, no error), keeping
    the pinned id so the NEXT entry — once the conversation exists — resumes cleanly.
    """
    pp = Path(session_path) / "arc.md"
    stored = (fields.read_field(pp, "claude-session") or "").strip()
    has_id = bool(stored) and not stored.startswith("<")
    if has_id and not is_new and _claude_conversation_exists(session_path, stored):
        return stored, True  # a real persisted conversation → resume it
    sid = stored if has_id else str(uuid.uuid4())
    fields.set_field(pp, "claude-session", sid)
    return sid, False  # fresh launch, but pinned so a later (engaged) entry resumes


def resolve_session(
    project: Path,
    project_name: str,
    *,
    thread_ref: Optional[str] = None,
    new_thread: Optional[str] = None,
    routine_ref: Optional[str] = None,
    new_routine: Optional[str] = None,
    session_ref: Optional[str] = None,
    new_session: Optional[str] = None,
    interactive: bool = False,
) -> Optional[Dict[str, Optional[str]]]:
    """Bind a session/run for one project: resolve a container, then a session in it.

    The container is a **thread** (a task work-line) or — when *routine_ref* /
    *new_routine* is given — a **routine** (a reusable procedure, whose runs ARE
    sessions). A routine flag wins over a thread flag. Returns ``{"arc_ref",
    "arc_text", "thread", "kind", "session_id", "resume", …}`` — the session slug,
    its passport text for the seed, the container (in the ``thread`` slot), its
    ``kind`` (``"thread"``/``"routine"``), the pinned claude session-id, and whether
    to ``--resume``. None when nothing is bound.
    """
    if new_routine or routine_ref:
        container = _create_routine(project, new_routine) if new_routine else routine_ref
        kind = stream.KIND_ROUTINE
    else:
        container = _resolve_thread(
            project, project_name, thread_ref=thread_ref, new_thread=new_thread, interactive=interactive
        )
        kind = stream.KIND_THREAD
    if container is None:
        return None
    sess_slug, sess_path, is_new = _resolve_session(
        project, container, session_ref=session_ref, new_session=new_session, interactive=interactive
    )
    if sess_slug is None:
        return None
    return _session_binding(sess_slug, sess_path, is_new, container, kind=kind)


def _session_binding(sess_slug, sess_path, is_new, thread, *, kind=stream.KIND_THREAD) -> Dict[str, Optional[str]]:
    """Build the bound-session dict (passport, claude session-id/resume, label bits).

    *kind* is ``"thread"`` (a task session) or ``"routine"`` (a routine run). The
    container slug lives in the ``thread`` slot either way so ``build_launch``/seed
    reuse is unchanged; ``kind`` lets the tab title / seed frame a run as a routine.
    """
    arc_text = None
    session_id = None
    resume = False
    session_index = ""
    session_title = ""
    if sess_path:
        try:
            arc_text = (Path(sess_path) / "arc.md").read_text(encoding="utf-8")
        except OSError:
            arc_text = None
        session_id, resume = _bind_claude_session(sess_path, is_new=is_new)
        session_index = Path(sess_path).name.split("-", 1)[0]
        t = (fields.read_field(Path(sess_path) / "arc.md", "title") or "").strip()
        session_title = "" if t.startswith("<") else t
    return {
        "arc_ref": sess_slug,
        "arc_text": arc_text,
        "thread": thread,
        "kind": kind,
        "session_id": session_id,
        "resume": resume,
        "session_index": session_index,
        "session_title": session_title,
        "arc_path": str(sess_path) if sess_path else "",  # for the sid-registry (cand 94)
    }


# --- interactive navigation (project → thread → session, with Back) ---------

# A pending handoff surfaces INSIDE its thread (not on the root screen): the offer's
# seeded session is the thread's tip (marked ⇄), and its thread is marked ⊕. The
# offer→(thread, session) map is derived from the seed PATH —
# ``<arcs>/<thread>/arcs/<session>/input/<seed>.md`` — not the free-form ``arc:``
# field, so it holds regardless of what --arc the offerer passed.
OFFER_THREAD_MARK = "⊕ "   # a thread that carries a pending handoff
OFFER_SESSION_MARK = "⇄ "  # the offered (pick-me-up) session inside a thread


def _offer_session_dir(rec: Dict) -> Optional[Path]:
    """The session dir an offer targets, derived from its seed path (or None)."""
    seed = rec.get("seed")
    if not seed or seed == "-":
        return None
    sd = Path(str(seed)).expanduser().parent.parent  # <session>/input/<seed> → <session>
    return sd if sd.is_dir() else None


def _session_by_slug(arcs: Path, tslug: str, sslug: str) -> Optional[Path]:
    """The session dir ``<thread>/arcs/<session>`` under *arcs*, matched by slug, or None."""
    thread_dir = stream._find(arcs, tslug, goal=True, closed=False)
    if thread_dir is None:
        return None
    sub = thread_dir / paths.ARCS_DIRNAME
    if not sub.is_dir():
        return None
    for e in sorted(sub.iterdir()):
        if e.is_dir() and slug.entry_slug(e.name) == sslug:
            return e
    return None


def project_offers(handoffs: List[Dict], project: Path) -> List[Dict]:
    """Offers that belong to *project*, annotated with their thread/session slugs.

    Resolution is by the offer's ``arc`` field (``<thread>/<session>``) — the
    AUTHORITATIVE pointer, verified against the stream — with a seed-path fallback for
    older records that carry no arc. The seed's location no longer decides visibility:
    a seed placed in the wrong dir can't hide an offer from the menu anymore. Only
    offers whose thread+session actually exist here are returned.
    Shape: ``[{"record", "thread", "session"}, …]``.
    """
    arcs = paths.arcs_dir(project).resolve()
    out: List[Dict] = []
    for rec in handoffs or []:
        tslug = sslug = None
        # primary: the arc field, resolved against the stream
        arc = str(rec.get("arc") or "")
        tpart, _, spart = arc.partition("/")
        if tpart and spart:
            t, s = slug.entry_slug(tpart), slug.entry_slug(spart)
            if _session_by_slug(arcs, t, s) is not None:
                tslug, sslug = t, s
        # fallback: derive from the seed path (<session>/input/<seed>)
        if tslug is None:
            sd = _offer_session_dir(rec)
            if sd is not None:
                sd = sd.resolve()
                if sd.parent.name == paths.ARCS_DIRNAME and sd.parent.parent.parent == arcs:
                    tslug, sslug = slug.entry_slug(sd.parent.parent.name), slug.entry_slug(sd.name)
        if tslug is not None:
            out.append({"record": rec, "thread": tslug, "session": sslug})
    return out


def _confirm(prompt: str) -> bool:
    """A Yes/No guard via the picker (default No). True only on an explicit Yes.

    Guards accidental materialisation: a fat-fingered "+ new thread" (or a voice note
    landing in the name prompt) shouldn't silently create a thread + session + Orca
    tab. BACK / No / cancel all mean "don't".
    """
    choice = select.select(prompt, ["Yes", "No"], allow_new=False, allow_back=True)
    return choice == 0


def _new_container(project, ask_prompt, confirm_noun, create):
    """Shared '+ new' flow with a guard: ask name → confirm → create. None if aborted.

    *create* is :func:`_create_thread` / :func:`_create_routine`. Returns the new
    slug, or None when the name is blank or the human declines the confirm (so the
    caller re-shows the picker — nothing gets materialised on a mis-tap).
    """
    name = _ask(ask_prompt).strip()
    if not name:
        return None
    if not _confirm("Create new {0} '{1}' and start it?".format(confirm_noun, name)):
        return None
    return create(project, name)


def _pick_thread_interactive(project, project_name, offer_threads=frozenset()):
    """Arrow-pick a thread: return its slug, create on NEW (guarded), or :data:`select.BACK`.

    Threads carrying a pending handoff (slug in *offer_threads*) are marked ``⊕`` and
    floated to the top, so a thread you can resume-from-handoff is the first thing
    you see (after ``+ new thread``). Creating a new thread is **guarded** by a
    confirm so a mis-tap can't materialise a junk thread + session + tab.
    """
    threads = list_threads(project)
    flagged = [t for t in threads if t["slug"] in offer_threads]
    rest = [t for t in threads if t["slug"] not in offer_threads]
    ordered = flagged + rest
    labels = [
        (OFFER_THREAD_MARK if t["slug"] in offer_threads else "") + _thread_label(t)
        for t in ordered
    ]
    choice = select.select(
        "Thread (тред) for {0} — continue one, or start new:".format(project_name),
        labels,
        allow_new=True, new_label="+ new thread", allow_back=True,
    )
    if choice == select.BACK:
        return select.BACK
    if choice == select.NEW:
        return _new_container(project, "new thread name> ", "thread", _create_thread)
    return ordered[choice]["slug"]


def _offered_action(rec: Dict) -> str:
    """Sub-choice for a picked offered session: ``'pickup'`` | ``'dismiss'`` | ``'back'``."""
    choice = select.select(
        "Handoff {0} — pick it up, or dismiss it?".format(rec["slug"]),
        ["Pick up (resume from the handoff)", "Dismiss (drop the offer)"],
        allow_new=False, allow_back=True,
    )
    if choice == select.BACK:
        return "back"
    return "pickup" if choice == 0 else "dismiss"


def _pick_session_interactive(
    project, thread_slug, offers=None, *,
    allow_new=False, new_label="+ new session", item="Session", container="thread",
):
    """Pick a session/run to resume / pick up a handoff, or auto-create the first.

    Two callers, two laws:

    * **threads** (default, *allow_new* False) — the thread law: sessions are a
      narrative connected by handoffs, so there is no blank "+ new session"
      mid-thread. An EMPTY thread auto-gets its first session; one with sessions is
      **resume-only**. *offers* float their session to the top marked ⇄; picking
      one opens a pick-up/dismiss choice (pick-up → ``(HANDOFF_PICK, record)``, a
      seed-based launch that honours the distil; dismiss drops the offer + re-lists).
    * **routines** (*allow_new* True) — a run is a fresh execution, NOT a
      handoff-continuation, so "+ new run" stays and there is no auto-first.

    Returns ``(slug, path, is_new)`` | ``(HANDOFF_PICK, record)`` | :data:`select.BACK`.
    """
    by_session = {o["session"]: o["record"] for o in (offers or [])}
    while True:
        sessions = list_sessions(project, thread_slug)
        if not allow_new and not sessions:
            # Thread law: the first session is born with the (empty) thread.
            slug_, path_ = _create_session(project, thread_slug, "")
            return slug_, path_, True
        flagged = [s for s in sessions if s["slug"] in by_session]
        rest = [s for s in sessions if s["slug"] not in by_session]
        ordered = flagged + rest
        labels = [
            (OFFER_SESSION_MARK if s["slug"] in by_session else "") + _session_label(s)
            for s in ordered
        ]
        hint = "continue one, or start new" if allow_new else "resume one (⇄ = pick up a handoff)"
        choice = select.select(
            "{0} in {1} {2} — {3}:".format(item, container, thread_slug, hint),
            labels,
            allow_new=allow_new, new_label=new_label, allow_back=True,
        )
        if choice == select.BACK:
            return select.BACK
        if choice == select.NEW:
            slug_, path_ = _create_session(project, thread_slug, "")
            return slug_, path_, True
        chosen = ordered[choice]
        rec = by_session.get(chosen["slug"])
        if rec is None:
            return chosen["slug"], chosen["path"], False  # a plain session → resume
        action = _offered_action(rec)
        if action == "pickup":
            return (HANDOFF_PICK, rec)
        if action == "dismiss":
            from .. import handoff_queue  # lazy: avoid import cycle
            try:
                handoff_queue.drop(paths.control_home(), rec["name"])
            except Exception:  # noqa: BLE001  dismiss is best-effort, never fatal
                pass
            by_session.pop(chosen["slug"], None)
            continue  # re-list without the dropped offer
        continue  # back → re-show the session list


def _navigate_session(project, project_name, offers=None):
    """Interactive thread→session with Back between the steps.

    *offers* (this project's pending handoffs, annotated with thread/session by
    :func:`project_offers`) mark/float the thread (⊕) and the offered session (⇄).
    Returns the bound-session dict, ``(HANDOFF_PICK, record)`` when an offer is
    picked up, or :data:`select.BACK` to go back to the project picker.
    """
    offers = offers or []
    offer_threads = {o["thread"] for o in offers}
    while True:
        thread = _pick_thread_interactive(project, project_name, offer_threads)
        if thread == select.BACK:
            return select.BACK
        if not thread:  # blank new-thread name → re-show the thread step
            continue
        thread_offers = [o for o in offers if o["thread"] == thread]
        sess = _pick_session_interactive(project, thread, thread_offers)
        if sess == select.BACK:
            continue  # back to the thread step
        if isinstance(sess, tuple) and sess and sess[0] == HANDOFF_PICK:
            return sess  # propagate the handoff pickup up to cmd_menu
        sess_slug, sess_path, is_new = sess
        if sess_slug is None:
            continue
        return _session_binding(sess_slug, sess_path, is_new, thread)


def _pick_routine_interactive(project, project_name):
    """Arrow-pick a routine: return its slug, create on NEW, or :data:`select.BACK`.

    Routine rows are gear-marked (⚙) so they read differently from task threads.
    """
    routines = list_routines(project)
    choice = select.select(
        "Routine (рутина) for {0} — continue one, or start new:".format(project_name),
        [_routine_label(r) for r in routines],
        allow_new=True, new_label="+ new routine", allow_back=True,
    )
    if choice == select.BACK:
        return select.BACK
    if choice == select.NEW:
        return _new_container(project, "new routine name> ", "routine", _create_routine)
    return routines[choice]["slug"]


def _navigate_routine(project, project_name):
    """Interactive routine→run with Back between the steps.

    Mirrors :func:`_navigate_session` but lists routines (not threads) and binds a
    **run** (a session inside the routine), tagged ``kind="routine"``. Returns the
    bound dict, or :data:`select.BACK` to go back to the type step.
    """
    while True:
        routine = _pick_routine_interactive(project, project_name)
        if routine == select.BACK:
            return select.BACK
        if not routine:  # blank new-routine name → re-show the routine step
            continue
        # A routine's runs ARE sessions inside it — reuse the session picker, but a
        # run is a fresh execution (not a handoff-continuation), so "+ new run" stays.
        sess = _pick_session_interactive(
            project, routine, allow_new=True, new_label="+ new run",
            item="Run", container="routine",
        )
        if sess == select.BACK:
            continue  # back to the routine step
        sess_slug, sess_path, is_new = sess
        if sess_slug is None:
            continue
        return _session_binding(
            sess_slug, sess_path, is_new, routine, kind=stream.KIND_ROUTINE
        )


def _pick_offer_interactive(offers):
    """List this project's pending handoffs → pick one to continue (or Back).

    A flat, findable list of the project's offers (labelled by their thread). Picking
    one returns ``(HANDOFF_PICK, record)`` — the caller launches it (and the pickup
    takes it out of the handoffs list). ``BACK`` returns to the type step.
    """
    labels = []
    for o in offers:
        rec = o.get("record", o)
        where = o.get("thread") or rec.get("arc") or ""
        labels.append("⇄ {0}{1}".format(rec.get("slug", "?"), " · " + where if where else ""))
    choice = select.select("Handoff to continue:", labels, allow_new=False, allow_back=True)
    if choice == select.BACK:
        return select.BACK
    picked = offers[choice]
    return (HANDOFF_PICK, picked.get("record", picked))  # the launchable offer record


def _navigate_type(project, project_name, offers=None):
    """The TYPE step after a project: Handoffs (if any) · Threads · Routines, with Back.

    Pending *offers* for the project are their OWN option here — ``⇄ Handoffs (N)`` —
    so they are seen and picked up right where you choose Threads/Routines (they ALSO
    still surface inside their own thread). Returns the bound dict, ``(HANDOFF_PICK,
    record)`` on a pickup, or :data:`select.BACK` back to the project picker.
    """
    offers = offers or []
    while True:
        opts = (["⇄ Handoffs ({0})".format(len(offers))] if offers else []) + ["Threads", "Routines"]
        choice = select.select(
            "What in {0}?".format(project_name), opts, allow_new=False, allow_back=True,
        )
        if choice == select.BACK:
            return select.BACK  # back to the project picker
        picked = opts[choice]
        if picked.startswith("⇄ Handoffs"):
            nav = _pick_offer_interactive(offers)
        elif picked == "Threads":  # the thread→session flow (also carries handoffs)
            nav = _navigate_session(project, project_name, offers)
        else:  # Routines → the routine→run flow
            nav = _navigate_routine(project, project_name)
        if nav == select.BACK:
            continue  # back to the type step
        return nav


# Tag for a handoff-pickup result from navigate_interactive (vs a project entry).
HANDOFF_PICK = "handoff"


def navigate_interactive(entries, handoffs=None):
    """Full project→type→(thread→session | routine→run) arrow flow with Back.

    Pending *handoffs* do NOT lead the root — each surfaces INSIDE its own project
    (project → Threads → thread ⊕ → session ⇄ → pick up). A project carrying pending
    offers is marked ``⊕N`` in the root list so it stands out at a glance. Returns
    ``(entry, bound)``, ``(HANDOFF_PICK, record)``, or None (cancel).
    """
    handoffs = handoffs or []
    while True:
        labels = []
        for e in entries:
            n = sum(1 for h in handoffs if h.get("project") == e["name"])
            mark = "  ⊕{0}".format(n) if n else ""
            labels.append("{0} → {1}{2}".format(e["name"], e["path"], mark))
        choice = select.select(
            "Pick a project to lead this session:", labels, allow_new=False, allow_back=True
        )
        if choice == select.BACK:
            return None  # back out of the first step = cancel
        entry = entries[choice]
        project = Path(entry["path"]).expanduser()
        nav = _navigate_type(project, entry["name"], project_offers(handoffs, project))
        if nav == select.BACK:
            continue  # back to the first picker
        if isinstance(nav, tuple) and nav and nav[0] == HANDOFF_PICK:
            return nav  # an offer was picked up inside its thread
        return entry, nav


def launch_handoff(
    record: Dict,
    entries: List[Dict[str, str]],
    *,
    control_home: Path,
    adapter,
    role: str = DEFAULT_ROLE,
    skip_permissions: bool = True,
    dry_run: bool = False,
) -> SpawnResult:
    """Pick up a handoff: launch its owning project's session seeded from the distil.

    Resolves the offer's project to its roster path and opens a fresh session there
    seeded by the handoff seed file (``--append-system-prompt`` so it starts already
    oriented), with a pinned session id.

    On a SUCCESSFUL launch it marks the offer ``taken`` — a picked-up handoff leaves
    the handoffs list at once (the thread is now a live, resumable session: its
    claude id is pinned to the passport). A FAILED launch (``res.ok`` false) leaves
    the offer hanging, recoverable — the two-stage guarantee kicks in only on errors.
    """
    proj_entry = next((e for e in entries if e["name"] == record["project"]), None)
    if proj_entry is None:
        return SpawnResult(
            ok=False,
            detail="handoff: project {0!r} not in roster".format(record["project"]),
            commands=[],
        )
    project = Path(proj_entry["path"]).expanduser()
    seed_path = record.get("seed")
    if not seed_path or seed_path == "-" or not Path(seed_path).is_file():
        return SpawnResult(
            ok=False, detail="handoff: seed file missing ({0})".format(seed_path), commands=[]
        )
    # Handoff pickup is non-interactive too (▶ from the board) — give it a first user
    # turn so it STARTS the reception instead of sitting blank (cand 100, twin of the
    # spark trigger cand 96). The distil rides the system prompt; this turn executes it.
    slug = record.get("slug") or "нить"
    pickup_trigger = (
        "Ты — свежая сессия, принявшая нить «{0}» по тёплому хендоффу (▶ с доски). "
        "НЕ отчитывайся о состоянии — первым же ходом выполни инструкцию системного "
        "промпта: прими нить (прочитай дистилл прошлой сессии, продолжи с точки "
        "хендоффа), закрой старт-гейт первым tide offload. Приём подтверждает сама "
        "сессия — разрешения на приём не спрашивай."
    ).format(slug)
    # ONE launch path (wave 3): sid pinned before spawn, the offer RESERVED for that
    # sid — the flip to taken happens on the session's first message (signed A:
    # reception is real only when the terminal actually said hello; a failed spawn
    # never eats the offer). Registry recorded by the launcher.
    from .launch import launch_session  # lazy: sibling module

    return launch_session(
        control_home,
        project=project,
        session_dir=Path(seed_path).parent.parent,
        adapter=adapter,
        seed_file=seed_path,
        trigger=pickup_trigger,
        title="handoff · {0}".format(record["slug"]),
        role=role,
        handoff_key=str(record["name"]),
        skip_permissions=skip_permissions,
        dry_run=dry_run,
    )


def _record_launch(control_home: Path, session_id: Optional[str], handle, arc) -> None:
    """Best-effort sid-keyed registry write after a spawn (cand 94). Never fatal."""
    try:
        from .. import registry
        if session_id and handle:
            registry.record(control_home, session_id, str(handle), str(arc))
    except Exception:  # noqa: BLE001  a launcher must not fail over the registry
        pass


# --- launch ----------------------------------------------------------------

def build_launch(
    project: Path,
    *,
    control_home: Path,
    role: str = DEFAULT_ROLE,
    arc_ref: Optional[str] = None,
    arc_text: Optional[str] = None,
    thread_name: Optional[str] = None,
    container_kind: str = stream.KIND_THREAD,
    session_id: Optional[str] = None,
    resume: bool = False,
    skip_permissions: bool = True,
    dry_run: bool = False,
    seed_file: Optional[str] = None,
    user_prompt: str = "",
) -> List[str]:
    """Resolve the scoped ``claude …`` argv for *project*.

    Two shapes, both scoped + (by default) ``--dangerously-skip-permissions``:

    * **resume** (``resume`` + *session_id*): ``claude --resume <id> || <fresh>`` —
      return to the SAME conversation; but claude only persists a session after a
      first turn, so a pinned-but-never-used id has no conversation and ``--resume``
      errors. The ``|| <fresh>`` fallback then launches a fresh seeded session under
      the same id, so re-entry is always forgiving (returned as ``sh -c``).
    * **fresh** (otherwise): a seeded launch (*arc_ref*/*arc_text* carry the bound
      session's passport, *thread_name* frames it). *session_id*, when given, is
      pinned via ``--session-id`` so a later entry can ``--resume`` this exact
      conversation. On dry-run a placeholder seed-file token is used.
    """
    fresh = _fresh_command(
        project,
        control_home=control_home,
        role=role,
        arc_ref=arc_ref,
        arc_text=arc_text,
        thread_name=thread_name,
        container_kind=container_kind,
        session_id=session_id,
        skip_permissions=skip_permissions,
        dry_run=dry_run,
        seed_file=seed_file,
        user_prompt=user_prompt,
    )
    if resume and session_id:
        resume_cmd = [context.SESSION_PROGRAM]
        if skip_permissions:
            resume_cmd.append(SKIP_PERMISSIONS)
        resume_cmd += ["--resume", session_id]
        # Re-apply the project's scoped MCP profile on resume too — the same flags a
        # fresh launch gets. A bare --strict-mcp-config here would drop the project's
        # --mcp-config (e.g. mitehq's linear-mite), so resumed sessions lost MCP.
        resume_cmd += context.scoped_flags(context.load_profile(project))
        shell = "{0} || {1}".format(shlex.join(resume_cmd), shlex.join(fresh))
        return ["sh", "-c", shell]
    return fresh


def _fresh_command(
    project: Path,
    *,
    control_home: Path,
    role: str,
    arc_ref: Optional[str],
    arc_text: Optional[str],
    thread_name: Optional[str],
    container_kind: str,
    session_id: Optional[str],
    skip_permissions: bool,
    dry_run: bool,
    seed_file: Optional[str] = None,
    user_prompt: str = "",
) -> List[str]:
    """The seeded fresh-launch argv (with ``--session-id`` pinned when given).

    *seed_file*, when given, is used VERBATIM as the seed (the handoff-pickup path:
    the session opens oriented by the handoff distil); otherwise a fresh per-project
    seed is generated and persisted.

    *user_prompt* becomes the session's first user turn (see
    :func:`context.build_launch_command`) — the ▶/``spark`` auto-start trigger.
    """
    if seed_file:
        sf = seed_file
    else:
        s = seed.seed_for_project(
            project,
            role=role,
            control_home=control_home,
            arc_ref=arc_ref,
            arc_text=arc_text,
            thread_name=thread_name,
            container_kind=container_kind,
        )
        title = "tide-{0}".format(project.name)
        sf = DRY_RUN_SEED_FILE if dry_run else str(persist_seed(s, title))
    profile = context.load_profile(project)
    command = context.build_launch_command(sf, profile, user_prompt=user_prompt)
    if session_id:
        command[1:1] = ["--session-id", session_id]  # pin for a future --resume
    if skip_permissions and SKIP_PERMISSIONS not in command:
        command[1:1] = [SKIP_PERMISSIONS]  # after the program, before the flags
    return command


def _session_launch_kwargs(entry: Dict) -> Dict:
    """Pull the bound-session launch kwargs off an entry (``{}`` when none bound)."""
    s = entry.get("session") or {}
    return {
        "arc_ref": s.get("arc_ref"),
        "arc_text": s.get("arc_text"),
        "thread_name": s.get("thread"),
        "container_kind": s.get("kind") or stream.KIND_THREAD,
        "session_id": s.get("session_id"),
        "resume": bool(s.get("resume")),
        "user_prompt": s.get("user_prompt") or "",
    }


def launch_entry(
    entry: Dict[str, str],
    *,
    adapter,
    control_home: Path,
    role: str = DEFAULT_ROLE,
    skip_permissions: bool = True,
    dry_run: bool = False,
) -> SpawnResult:
    """Build the scoped launch command for one rostered project and spawn it.

    A resolved session is carried on the entry under the ``"session"`` key (the
    thread + session passport the seed binds to); absent ⇒ no binding. The session
    is spawned with ``cwd`` = the project dir (so its ``CLAUDE.md`` loads).
    """
    project = Path(entry["path"]).expanduser()
    session = entry.get("session") or {}
    command = build_launch(
        project,
        control_home=control_home,
        role=role,
        skip_permissions=skip_permissions,
        dry_run=dry_run,
        **_session_launch_kwargs(entry),
    )
    res = adapter.spawn(
        command=command, cwd=str(project), title=_tab_title(entry), dry_run=dry_run
    )
    # Record sid → terminal so ▶ resolves THIS session's tab later (cand 94).
    if res.ok and not dry_run:
        _record_launch(control_home, session.get("session_id"), res.ref,
                       session.get("arc_path") or session.get("arc_ref") or "")
    return res


def _tab_title(entry: Dict) -> str:
    """The terminal tab title — session/run first, then container: ``<session> · <container>``.

    For a routine run the container is the routine and the title is gear-marked
    (``⚙ <run> · <routine>``) so it reads as a routine. Falls back to
    ``tide-<project>`` when no session is bound.
    """
    s = entry.get("session") or {}
    thread = s.get("thread")
    if not thread:
        return "tide-{0}".format(entry["name"])
    session = s.get("session_title") or s.get("session_index") or s.get("arc_ref") or "session"
    title = "{0} · {1}".format(session, thread)
    if s.get("kind") == stream.KIND_ROUTINE:
        return "{0}{1}".format(ROUTINE_MARKER, title)
    return title


def launch_entries(
    entries: List[Dict[str, str]],
    *,
    control_home: Path,
    adapter_name: Optional[str] = None,
    role: str = DEFAULT_ROLE,
    skip_permissions: bool = True,
    dry_run: bool = False,
) -> List[SpawnResult]:
    """Spawn a seeded session for each chosen project; return one result per project."""
    adapter = get_adapter(adapter_name)
    return [
        launch_entry(
            e,
            adapter=adapter,
            control_home=control_home,
            role=role,
            skip_permissions=skip_permissions,
            dry_run=dry_run,
        )
        for e in entries
    ]


# --- settings (adapter pin) ------------------------------------------------

def _read_settings(root: Path) -> Optional[dict]:
    """Best-effort read of the project ``.claude/settings.json`` (None on any issue)."""
    path = Path(root) / ".claude" / "settings.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (json.JSONDecodeError, ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def resolve_adapter_name(root: Path, override: Optional[str]) -> Optional[str]:
    """Pick the adapter name: explicit *override* wins, else the settings pin, else None."""
    if override:
        return override
    settings = _read_settings(root)
    if isinstance(settings, dict):
        value = settings.get(SETTINGS_KEY)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# --- launch preview (pure) -------------------------------------------------

def launch_preview(
    chosen: List[Dict[str, str]],
    *,
    control_home: Path,
    role: str = DEFAULT_ROLE,
    skip_permissions: bool = True,
) -> List[tuple]:
    """The scoped command(s) the human would enter with — for ``--dry-run`` /
    ``--debug`` display.

    Pure: builds each project's resolved ``claude …`` argv WITHOUT spawning (and
    without persisting a seed — the placeholder ``@<seed-file>`` token stands in
    for the temp path). Returns ``[(name, command_string), …]`` in pick order.
    """
    out = []
    for entry in chosen:
        project = Path(entry["path"]).expanduser()
        command = build_launch(
            project,
            control_home=control_home,
            role=role,
            skip_permissions=skip_permissions,
            dry_run=True,
            **_session_launch_kwargs(entry),
        )
        out.append((entry["name"], " ".join(command)))
    return out


# --- CLI handler -----------------------------------------------------------

def cmd_menu(args) -> int:
    """``tide menu`` — list the roster, pick N projects, launch seeded sessions."""
    root = paths.control_home()
    all_entries = list_entries(root)
    include_archived = bool(getattr(args, "all", False))
    entries = all_entries if include_archived else active_entries(all_entries)

    # Pending handoffs (two-stage pull model): surfaced as pickable rows in the TTY
    # picker, or as a printed banner on the non-tty path (below).
    from .. import handoff_queue  # lazy: avoid import cycle at module load
    pending = handoff_queue.list_offers(root, status=handoff_queue.STATUS_OFFERED)

    if not entries and not pending:
        if not all_entries:
            print(render_menu([]))  # truly empty roster
        else:
            print("(no active projects — use `tide menu --all` to include archived)")
        return 0

    raw = getattr(args, "pick", None)
    adapter_name = resolve_adapter_name(root, getattr(args, "adapter", None))
    dry_run = bool(getattr(args, "dry_run", False))
    debug = bool(getattr(args, "debug", False))
    skip_permissions = not getattr(args, "no_skip_permissions", False)
    role = getattr(args, "role", None) or DEFAULT_ROLE
    interactive = not raw

    if interactive and select.is_interactive_tty():
        # TTY: pending handoffs sit at the TOP of the first picker; below them the
        # normal project → type → thread/session flow, with ←/Esc Back between steps.
        nav = navigate_interactive(entries, handoffs=pending)
        if nav is None:
            print("tide: cancelled")
            return 0
        if nav[0] == HANDOFF_PICK:
            # Picked a handoff → seed-based pickup of its owning project, mark taken.
            res = launch_handoff(
                nav[1], all_entries, control_home=root,
                adapter=get_adapter(adapter_name), role=role,
                skip_permissions=skip_permissions, dry_run=dry_run,
            )
            flag = "ok" if res.ok else "FAILED"
            print("tide: handoff {0} [{1}] {2}".format(nav[1]["slug"], flag, res.detail))
            return 0 if res.ok else 1
        entry, bound = nav
        chosen = [{**entry, "session": bound}]
    else:
        if not raw:
            # non-TTY: surface pending handoffs as a banner, then the typed multi-pick.
            hb = render_pending_handoffs(root, all_entries)
            if hb:
                print(hb)
                print()
            print(render_menu(entries))
            try:
                raw = input("pick> ")
            except EOFError:
                raw = ""
        chosen = select_entries(entries, raw)
        # Bind a session per chosen project from flags (or non-tty interactive prompts).
        chosen = [
            {**entry, "session": resolve_session(
                Path(entry["path"]).expanduser(),
                entry["name"],
                thread_ref=getattr(args, "thread", None),
                new_thread=getattr(args, "new_thread", None),
                routine_ref=getattr(args, "routine", None),
                new_routine=getattr(args, "new_routine", None),
                session_ref=getattr(args, "session", None),
                new_session=getattr(args, "new_session", None),
                interactive=interactive,
            )}
            for entry in chosen
        ]

    # --debug (real launch) and --dry-run (no launch) both show the human EXACTLY
    # what scoped session will run — the resolved claude argv (strict MCP scoping
    # visible, no global MCP loaded) — BEFORE the terminal opens.
    if dry_run or debug:
        for name, command in launch_preview(
            chosen, control_home=root, role=role, skip_permissions=skip_permissions
        ):
            print("tide: {0} scoped command:".format(name))
            print("  {0}".format(command))

    results = launch_entries(
        chosen,
        control_home=root,
        adapter_name=adapter_name,
        role=role,
        skip_permissions=skip_permissions,
        dry_run=dry_run,
    )
    for entry, res in zip(chosen, results):
        flag = "ok" if res.ok else "FAILED"
        print("tide: {0} [{1}] {2}".format(entry["name"], flag, res.detail))
    return 0 if all(r.ok for r in results) else 1


def _spark_trigger(thread_slug: str, project_name: str) -> str:
    """The FIRST user turn for a ▶-sparked session (cand 96). The board's ▶ launches
    non-interactively — nobody is at that terminal to type — so without a first turn
    the session opens blank and never starts the pickup. The system-prompt seed
    already carries canon + the passport + the start-gate; this short turn tells the
    fresh session to EXECUTE it now. Restores the trigger cand 94 dropped with the
    band-aid, on tide's own launch path."""
    return (
        "Тебя подняли кнопкой ▶ с полки проекта {0} вести нить «{1}». "
        "НЕ отчитывайся о состоянии — первым же ходом выполни инструкцию системного "
        "промпта: закрой старт-гейт (живая цель + первый tide offload), построй план "
        "по закону 47 и покажи Грише. Приём подтверждает сама сессия — разрешения на "
        "приём не спрашивай."
    ).format(project_name, thread_slug)


def spark(
    control_home: Path,
    project_entry: Dict[str, str],
    *,
    thread: Optional[str] = None,
    new_thread: Optional[str] = None,
    goal: Optional[str] = None,
    adapter,
    role: str = DEFAULT_ROLE,
    skip_permissions: bool = True,
    dry_run: bool = False,
) -> SpawnResult:
    """▶ spark a fresh session — tide creates the arc, pins the id, spawns, registers.

    The board's ▶ used to launch claude in the project root and have IT run ``tide arc
    new-session`` after start — a divergent flow that left the board blind and couldn't
    resolve the tab. ``spark`` unifies ▶ onto the SAME path as menu/pickup: create the
    session in tide FIRST, bind + pin its claude id (``_session_binding`` →
    ``_bind_claude_session``), launch via the adapter, and record ``sid → terminal`` in
    the registry (via :func:`launch_entry`) — so the head is visible at once and ▶
    resolves this exact tab (cand 94). Continue an existing *thread* or start a
    *new_thread* (with *goal*); the first session's slug is unique in the container.
    """
    project = Path(project_entry["path"]).expanduser()
    from .handoff import resolve_open_entry, _unique_pickup_slug  # lazy: sibling module

    if new_thread:
        thread_entry = stream.new_thread(project, new_thread, goal=goal)
    elif thread:
        thread_entry = resolve_open_entry(project, thread)
        if thread_entry is None or not stream.is_thread(thread_entry):
            raise MenuError("spark: no open thread {0!r} in {1}".format(thread, project.name))
    else:
        raise MenuError("spark: give --thread <slug> or --new-thread <name>")

    container_slug = slug.entry_slug(thread_entry.name)
    sess_slug = _unique_pickup_slug(thread_entry, base=container_slug)
    sess = stream.new_session(project, container_slug, sess_slug)
    binding = _session_binding(
        slug.entry_slug(sess.name), sess, True, container_slug, kind=stream.KIND_THREAD
    )
    # ▶ is non-interactive (launched from the board, nobody at the terminal): give the
    # fresh session a first user turn so it STARTS the pickup instead of sitting blank
    # (cand 96). Interactive doors (tide go/menu) leave this empty — the human types.
    binding["user_prompt"] = _spark_trigger(container_slug, project_entry.get("name") or project.name)
    spark_entry = {**project_entry, "session": binding}
    # ONE launch path (wave 3) — same gesture order as pickup/menu.
    from .launch import launch_session  # lazy: sibling module

    return launch_session(
        control_home,
        project=project,
        session_dir=sess,
        adapter=adapter,
        arc_ref=binding.get("arc_ref"),
        arc_text=binding.get("arc_text"),
        thread_name=binding.get("thread"),
        container_kind=binding.get("kind") or stream.KIND_THREAD,
        trigger=binding["user_prompt"],
        title=_tab_title(spark_entry),
        role=role,
        skip_permissions=skip_permissions,
        dry_run=dry_run,
    )


def cmd_spark(args) -> int:
    """``tide spark <project> (--thread|--new-thread)`` — the board's ▶, tide-owned."""
    root = paths.control_home()
    entry = next((e for e in list_entries(root) if e["name"] == args.project), None)
    if entry is None:
        raise MenuError("spark: no project {0!r} in the roster".format(args.project))
    adapter = get_adapter(resolve_adapter_name(root, getattr(args, "adapter", None)))
    res = spark(
        root, entry,
        thread=getattr(args, "thread", None),
        new_thread=getattr(args, "new_thread", None),
        goal=getattr(args, "goal", None),
        adapter=adapter,
        role=getattr(args, "role", None) or DEFAULT_ROLE,
        skip_permissions=not getattr(args, "no_skip_permissions", False),
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    print("tide: spark [{0}] {1}".format("ok" if res.ok else "FAILED", res.detail))
    return 0 if res.ok else 1


def register(subparsers) -> None:
    """Add the top-level ``menu`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "menu", help="pick N projects from the roster and launch seeded sessions"
    )
    p.add_argument("--pick", help="non-interactive selection (e.g. '1,3' or 'all')")
    p.add_argument(
        "--all",
        action="store_true",
        help="include archived projects in the pick-list (default: active only)",
    )
    p.add_argument("--adapter", help="terminal adapter (orca|tmux; default from settings)")
    p.add_argument("--role", help="session role (default: orchestrator)")
    p.add_argument(
        "--thread",
        "--prism",  # back-compat alias (thread was once 'prism')
        dest="thread",
        metavar="SLUG",
        help="continue an existing thread (тред) by slug (else 0=new in the picker)",
    )
    p.add_argument(
        "--new-thread",
        "--new-prism",  # back-compat alias
        dest="new_thread",
        metavar="NAME",
        help="start a fresh thread (тред) with this name",
    )
    p.add_argument(
        "--routine",
        metavar="SLUG",
        help="continue an existing routine (рутина) by slug (a run inside it); else 0=new in the picker",
    )
    p.add_argument(
        "--new-routine",
        dest="new_routine",
        metavar="NAME",
        help="start a fresh routine (рутина) with this name (a reusable procedure)",
    )
    p.add_argument(
        "--session",
        metavar="SLUG",
        help="continue an existing session/run by slug inside the chosen thread/routine",
    )
    p.add_argument(
        "--new-session",
        dest="new_session",
        metavar="NAME",
        help="start a fresh session with this name inside the chosen thread",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="build seeds + adapter commands without opening a terminal",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="print the full scoped launch command before opening the terminal",
    )
    p.add_argument(
        "--no-skip-permissions",
        action="store_true",
        dest="no_skip_permissions",
        help="keep permission prompts on (default: --dangerously-skip-permissions, like tide terminal)",
    )
    p.set_defaults(func=cmd_menu, _cmd="menu")

    sp = subparsers.add_parser(
        "spark",
        help="▶ start a fresh session in a thread — tide creates + pins + registers it (board's ▶)",
    )
    sp.add_argument("project", help="roster project name")
    sp.add_argument("--thread", metavar="SLUG", help="continue an existing thread (тред) by slug")
    sp.add_argument("--new-thread", dest="new_thread", metavar="NAME",
                    help="start a fresh thread with this name")
    sp.add_argument("--goal", help="goal line for a new thread")
    sp.add_argument("--adapter", help="terminal adapter (orca|tmux; default from settings)")
    sp.add_argument("--role", help="session role (default: orchestrator)")
    sp.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="build the arc + launch command without opening a terminal")
    sp.add_argument("--no-skip-permissions", action="store_true", dest="no_skip_permissions",
                    help="keep permission prompts on")
    sp.set_defaults(func=cmd_spark, _cmd="spark")

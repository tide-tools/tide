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
   (:mod:`tide.handoff_queue`); ``close`` just distils.
4. **raise the fresh session** (decision 07 handoff-autospawn) — right after the
   offer is hung, the CLI runs the SAME door the board's ▶ runs
   (:func:`tide.launcher.pickup.run_pickup`: reserve → spawn through the resolved
   adapter, AO in practice). The old session is told it has handed over and
   stops; the human closes it. One thread — one orchestrator: the offer is
   reserved for the new session, so a later ▶ only focuses it. A failed spawn
   leaves the offer plain ``offered`` (reservation undone) for ▶ by hand;
   ``--no-spawn`` / ``$TIDE_HANDOFF_NO_SPAWN`` keep the old pull-only behaviour.

Two layers as everywhere else: pure functions (``build_summary`` …) are argparse-
and disk-free and snapshot-testable; :func:`run_handoff` is the disk
orchestration; :func:`cmd_handoff` is the thin CLI handler wired by ``cli.py``.
"""

from __future__ import annotations

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

# Opt-out of the auto-spawn (decision 07): set to anything truthy ⇒ pull-only, the
# offer just hangs for ▶ (same as ``--no-spawn``).
NO_SPAWN_ENV = "TIDE_HANDOFF_NO_SPAWN"

# The OLD session's identity must not leak into the spawn: inside AO a caller's
# ``$AO_SESSION_ID`` would make the fresh session look like a child of the one
# being retired, and claude's own markers make a nested launch refuse to start.
# The board's ▶ never carried them — the auto-spawn runs exactly as clean.
_DETACH_ENV = ("AO_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDECODE",
               "CLAUDE_CODE_ENTRYPOINT")


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
    return _roster.name_for_path(owner_root, home=Path(home))


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

    Thin alias over :func:`tide.resolve.open_top_entry` — THE one slug matcher
    (both-form: displayed ``04-@slug`` name AND bare slug; the one-form trap
    sent agents to ``tide arc new`` and duplicated arcs).
    """
    from .. import resolve as _resolve
    return _resolve.open_top_entry(root, arc_ref)


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
    """One-line candidates-backlog reminder shown before the chat is dropped (cand 39).

    The full ``render_list`` dumped the whole backlog (~37 lines) into the handoff
    output, burying what matters — the distil path and the dry-run note. Collapse to a
    COUNT + the drop command; the backlog itself is one ``tide candidate list`` away.
    """
    n = len(candidate.list_candidates(root))
    return (
        "Кандидатов в бэклоге: {0} — заметил новое, урони: "
        'tide candidate add <slug> "<идея>"'.format(n)
    )


# --- orchestration ----------------------------------------------------------

@dataclass
class HandoffResult:
    """Outcome of a handoff: where the distil landed + the offer hung in the queue.

    * ``mode`` — the chosen fork (continue / new / close).
    * ``summary_path`` — the workspace file the distil was written to.
    * ``candidate_reminder`` — the backlog reminder text (always computed).
    * ``offer_path`` — the queue record (None for ``close`` and ``dry_run``).
    * ``spawn`` — the auto-spawn outcome (the ``tide pickup --json`` dict:
      ``ok``/``action``/``detail``/``handle``/``adapter``); None when not attempted.
    """

    mode: str
    summary_path: Path
    candidate_reminder: str
    offer_path: Optional[Path] = None
    notes: List[str] = field(default_factory=list)
    spawn: Optional[dict] = None

    @property
    def handed_over(self) -> bool:
        """True when the fresh session is up (or already coming up) — the old one stops."""
        return bool(self.spawn and self.spawn.get("ok"))


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
    lines = (distil or "").splitlines()
    for i, line in enumerate(lines):
        low = line.lower()
        if "следующий шаг" in low or "next step" in low:
            if line.lstrip().startswith("#"):
                # блок контракта «## следующий шаг» — цель в его первой живой строке
                body = next((ln for ln in lines[i + 1:] if ln.strip()), "")
                hole = body.strip(" -\t").startswith(("#", "—"))  # пустой блок
                g = "" if hole else body
            else:
                g = line.split(":", 1)[1] if ":" in line else line
            g = g.strip().strip("*#-— ").strip()
            if g and not g.startswith("<"):
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
    ``plan.md`` the explicit first step so the thread is closeable from birth. The
    law-47 DEFINITION is single-sourced in :data:`seed.LAW_47_PLAN` (cand 127) so
    this path resolves it without a source grep, exactly like the spark seed.
    """
    from .seed import LAW_47_PLAN  # lazy: sibling; keep the law-47 text single-sourced

    return (
        "## первый шаг — ПЛАН НИТИ (закон 47)\n"
        "Это НОВАЯ нить. ПЕРВЫМ ДЕЛОМ построй `{0}/plan.md` шагами/волнами (что и в "
        "каком порядке до цели), и только потом выполняй. Без плана нить нельзя "
        "закрыть — закрытие (tide arc close; на живой доске — кнопка ✓) "
        "гейтится планом со всеми шагами [x].\n\n"
        "{1}\n\n"
    ).format(thread_entry.name, LAW_47_PLAN)


def _origin_sid(stream_mod, owner_root: Path, thread_slug: str, given: str) -> str:
    """Normalise an explicit ``--from-session`` to a claude sid (best-effort).

    Agents pass the ARC name here (``14-graph``) as readily as a sid — and a
    non-sid origin poisons the seam silently: dissolution can't find the origin's
    passport and the Mickey-17 detector never matches it (live 14.07). When
    *given* names a session arc of this thread, its pinned ``claude-session`` is
    the honest origin id; anything else passes through untouched.
    """
    try:
        from .. import fields
        for entry in stream_mod.session_entries(owner_root, thread_slug):
            if entry.name == given or slug.entry_slug(entry.name) == slug.slugify(given):
                sid = (fields.read_field(entry / "arc.md", "claude-session") or "").strip()
                return sid or given
        return given
    except Exception:  # noqa: BLE001  best-effort — never break a handoff over lineage
        return given


def link_predecessor_thread(owner_root: Path, thread_entry: Path,
                            origins: "tuple" = ()) -> Optional[str]:
    """F13 (SOC-101): новая нить помнит, из какой выросла — ``from:`` в паспорте
    нити = ``<нить-предшественница>/<сессия>`` той сессии, что её родила.

    *origins* — кандидаты в родившую сессию по порядку: явный ``--from-session``
    (sid или имя арки), потом ``$CLAUDE_CODE_SESSION_ID`` вызывателя. Первая
    найденная в этом проекте сессия ДРУГОЙ нити и становится предшественницей;
    живой ``from:`` нити не переписывается. Сид его уже читает
    (:func:`tide.launcher.seed_draft.predecessor_thread`). → записанное значение
    или None. Никогда не роняет хендофф."""
    try:
        from .. import fields, offload, resolve
        from ..arc import stream as _stream

        passport = _stream.passport_path(thread_entry)
        have = (fields.read_field(passport, "from") or "").strip()
        if have and have != "-" and not have.startswith("<"):
            return None
        for ref in origins:
            ref = (ref or "").strip()
            if not ref:
                continue
            sess = offload.find_session_by_claude_id(owner_root, ref)
            if sess is None:
                try:
                    sess = resolve.find_session(owner_root, ref)
                except Exception:  # noqa: BLE001 — неоднозначно/нет: следующий
                    sess = None
            if sess is None:
                continue
            pred = sess.parents[1]
            if pred.resolve() == Path(thread_entry).resolve():
                continue
            value = "{0}/{1}".format(pred.name, sess.name)
            fields.set_field(passport, "from", value)
            return value
    except Exception:  # noqa: BLE001 — родословная не смеет сорвать хендофф
        return None
    return None


def _caller_sid() -> str:
    import os
    return os.environ.get("CLAUDE_CODE_SESSION_ID", "")


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


def spawn_offer(home: Path, key: str, *, adapter_name: Optional[str] = None,
                live_timeout: Optional[float] = None) -> dict:
    """Raise the fresh session for offer *key* NOW — the board's ▶, pressed by tide.

    Delegates to :func:`tide.launcher.pickup.run_pickup` (the one door: in-flight
    guard → reserve → spawn → registry record), so the AO real-sid relink and the
    first-prompt confirm work exactly as after a hand-pressed ▶. Runs with the old
    session's identity scrubbed from the environment (:data:`_DETACH_ENV`).

    A spawn is NOT a reception (SOC-101 F25): with an adapter that can probe the
    agent (AO) the fresh session must give its first signal within *live_timeout*
    seconds (default :data:`LIVE_TIMEOUT`, ``$TIDE_HANDOFF_LIVE_TIMEOUT``); else one
    ``resume-agent`` and another wait; else ``action: not-alive`` — the old session
    is told (and gets a pulse line) that the thread stays with it.

    Never raises. On failure the reservation the attempt made is undone, so the
    offer is a plain ``offered`` record again — ▶ by hand works as before.
    """
    import os

    from .. import handoff_queue
    from . import pickup

    saved = {k: os.environ.pop(k) for k in _DETACH_ENV if k in os.environ}
    try:
        out = pickup.run_pickup(Path(home), key, adapter_name=adapter_name)
    except Exception as exc:  # noqa: BLE001  a spawn failure must not break the handoff
        out = {"ok": False, "action": "failed", "detail": str(exc) or type(exc).__name__}
    finally:
        os.environ.update(saved)
    if out.get("ok") and out.get("action") == "spawned":
        out = _await_alive(Path(home), key, out, adapter_name=adapter_name,
                           timeout=live_timeout)
    if not out.get("ok"):
        try:
            handoff_queue.unreserve(Path(home), key)
        except Exception:  # noqa: BLE001  best-effort — the offer itself is intact
            pass
    if out.get("action") == NOT_ALIVE:
        _tell_origin(Path(home), key, NOT_ALIVE_LINE)
    return out


# --- живость принимающей (SOC-101 F25) -----------------------------------------
# `ao spawn` ok — ещё не приём: свежая сессия Claude может зависнуть на вопросе о
# доверии папке, а первый `ao send` роняет её в exited. Передача засчитывается,
# только когда AO показал живого агента с claude-id (первый сигнал) — или сессия
# уже сама подтвердила приём. Нет сигнала — один resume-agent; нет и тогда —
# резерв снят, старая сессия держит нить.

LIVE_TIMEOUT = 90.0                       # секунд на первый сигнал (на попытку)
LIVE_TIMEOUT_ENV = "TIDE_HANDOFF_LIVE_TIMEOUT"
LIVE_POLL = 3.0
NOT_ALIVE = "not-alive"
NOT_ALIVE_LINE = "приём не состоялся — не замирай, нить твоя"

# подменяются в тестах: время и сон не настоящие
_clock = None
_sleep = None


def _live_timeout(given: Optional[float]) -> float:
    import os

    if given is not None:
        return max(0.0, float(given))
    raw = (os.environ.get(LIVE_TIMEOUT_ENV) or "").strip()
    try:
        return max(0.0, float(raw)) if raw else LIVE_TIMEOUT
    except ValueError:
        return LIVE_TIMEOUT


def _offer_taken(home: Path, key: str) -> bool:
    from .. import handoff_queue
    try:
        return handoff_queue._resolve(home, key).get("status") == handoff_queue.STATUS_TAKEN
    except Exception:  # noqa: BLE001
        return False


def _wait_signal(adapter, handle: str, home: Path, key: str, timeout: float) -> bool:
    import time

    clock = _clock or time.monotonic
    sleep = _sleep or time.sleep
    deadline = clock() + timeout
    while True:
        try:
            if _offer_taken(home, key) or adapter.first_signal(handle):
                return True
        except Exception:  # noqa: BLE001 — a flaky probe is «not yet», not death
            pass
        if clock() >= deadline:
            return False
        sleep(LIVE_POLL)


def _await_alive(home: Path, key: str, out: dict, *, adapter_name: Optional[str],
                 timeout: Optional[float]) -> dict:
    """Ждать первого сигнала принимающей; → *out* как есть или ``not-alive``."""
    from . import pickup

    handle = str(out.get("handle") or "").strip()
    if not handle:
        return out
    try:
        adapter = pickup.get_adapter(adapter_name or out.get("adapter") or None)
    except Exception:  # noqa: BLE001
        return out
    if not callable(getattr(adapter, "first_signal", None)):
        return out  # адаптер без пробы живости (orca, tmux) — как раньше
    limit = _live_timeout(timeout)
    if _wait_signal(adapter, handle, home, key, limit):
        return out
    resumed = False
    revive = getattr(adapter, "resume_agent", None)
    if callable(revive):
        try:
            resumed = bool(revive(handle))
        except Exception:  # noqa: BLE001
            resumed = False
        if _wait_signal(adapter, handle, home, key, limit):
            out = dict(out)
            out["detail"] = (out.get("detail") or "") + " (поднялась после resume-agent)"
            return out
    return {"ok": False, "action": NOT_ALIVE, "handle": handle,
            "adapter": out.get("adapter") or "",
            "detail": "AO {0} не подала сигнала за {1:g} с{2} — агент не поднялся "
                      "(вопрос о доверии папке?)".format(
                          handle, limit,
                          " и после resume-agent" if resumed else
                          " (resume-agent не прошёл)" if callable(revive) else "")}


def _origin_entry(home: Path, rec: dict) -> Optional[Path]:
    """Сессия-арка, отдавшая оффер *rec* (по ``from-session`` в проекте ростера)."""
    from .. import offload, roster as _roster
    sid = str(rec.get("from_session") or "").strip()
    if not sid or sid == "-":
        return None
    entry = next((e for e in _roster.read_roster(home)
                  if e["name"] == rec.get("project")), None)
    if entry is None:
        return None
    return offload.find_session_by_claude_id(Path(entry["path"]).expanduser(), sid)


def _tell_origin(home: Path, key: str, text: str) -> None:
    """Строка пульса в паспорт старой сессии — чтобы и доска, и она сама видели."""
    try:
        from .. import handoff_queue, offload
        rec = handoff_queue._resolve(home, key)
        origin = _origin_entry(home, rec)
        if origin is not None:
            offload.write_pulse(origin, note="{0} (оффер {1})".format(text, key))
    except Exception:  # noqa: BLE001 — запись не смеет сорвать хендофф
        pass


def not_alive_line(spawn: dict, key: str) -> str:
    """Старой сессии, когда принимающая не поднялась: нить остаётся у неё (F25)."""
    return ("{0}. {1}. Оффер {2} висит без резерва: ▶ на доске или 'tide pickup {2}', "
            "когда разберёшься; в новую AO-сессию ao send не шли.".format(
                NOT_ALIVE_LINE, spawn.get("detail") or "сигнала нет", key))


def handed_over_line(spawn: dict) -> str:
    """The one plain line that tells the OLD session it is done (decision 07)."""
    handle = str(spawn.get("handle") or "").strip()
    where = "новую сессию"
    if spawn.get("adapter") == "ao":
        where += " AO"
    if handle:
        where += " " + handle
    return ("передано в {0} — эта сессия больше ничего не делает; "
            "закрой её, когда удобно".format(where))


def run_handoff(
    root: Path,
    *,
    arc_ref: str,
    mode: str = FORK_CONTINUE,
    summary: Optional[str] = None,
    from_session: Optional[str] = None,
    dry_run: bool = False,
    date: Optional[str] = None,
    spawn: bool = False,
    adapter_name: Optional[str] = None,
    born_fields: Optional[dict] = None,
) -> HandoffResult:
    """Run a handoff: distil → workspace, remind candidates, hang the offer, raise it.

    *summary* is the distilled markdown (the caller prepares it); when absent a
    minimal stub is built from *mode*/*arc_ref* so the call never silently writes
    nothing. For ``continue``/``new`` the offer lands in the control-home queue
    (:func:`tide.handoff_queue.offer`); with *spawn* the fresh session is raised
    right away through the ▶ door (:func:`spawn_offer`, adapter *adapter_name* or
    the resolved pin). Without *spawn* (the library default — the CLI turns it on)
    the offer just hangs for ▶. ``close`` just distils and never spawns.
    ``dry_run`` distils but leaves the queue untouched. *born_fields* are stamped
    on the pickup session's passport at birth (the auto-handoff passes its chain
    counter and progress mark this way).
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

    # Resolve the offer queue's control-home UP FRONT — before any pickup session is
    # born. A handoff that creates the pickup (thread case) and only THEN fails to find
    # the control-home leaves an orphan session with no offer: a half-handoff that had
    # to be repaired by hand (cand 90). Fail fast so the seam is transactional — no
    # control-home ⇒ nothing created. close / dry_run hang no offer, so they skip it.
    home = None
    if mode in (FORK_CONTINUE, FORK_NEW) and not dry_run:
        try:
            home = paths.control_home(root)
        except FileNotFoundError as exc:
            raise HandoffError(
                "handoff: no control-home for the offer queue — {0} "
                "(set $TIDE_HOME or run from inside the control-home). Nothing was "
                "created.".format(exc)
            ) from exc

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
            if mode == FORK_NEW:  # F13: новая нить — from: на нить-предшественницу
                link_predecessor_thread(owner_root, entry, (from_session, _caller_sid()))
            # Origin id for the multiples detector: auto-fill from the thread's
            # current holder when the caller didn't pass it (cand 78). Explicit
            # --from-session still wins — normalised to a sid when it names an arc
            # (live 14.07). Read BEFORE the pickup becomes the newest.
            if from_session:
                from_session = _origin_sid(_stream, owner_root, tslug, from_session)
            else:
                from_session = _thread_origin_session(_stream, owner_root, tslug)
            # Name the pickup after the THREAD (03-debug-deck), not a generic 'pickup'
            # (cand 83): readable on the board, still unique via -N. The dir NN keeps
            # siblings apart; the slug now carries meaning instead of noise.
            session_born = _stream.new_session(
                owner_root, tslug, _unique_pickup_slug(entry, base=tslug),
                goal=_pickup_goal(entry, text),
            )
            # «проект — нить · NN» from birth — the same name the spawn gives the AO
            # session; the project by the queue's roster (the name the offer carries)
            from .. import fields as _fields

            title = _stream.thread_session_title(
                session_born, project=_roster_project_name(home, owner_root))
            if title:
                _fields.set_field(session_born / "arc.md", "title", title)
            for key, value in (born_fields or {}).items():
                _fields.set_field(session_born / "arc.md", key, str(value))
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
    assert home is not None  # resolved up front for every offer-bound mode (cand 90)
    result.offer_path = handoff_queue.offer(
        home,
        arc_ref,
        arc=offer_arc,
        project=_roster_project_name(home, owner_root),
        seed=str(summary_path),
        mode=mode,
        from_session=from_session,
    )
    key = result.offer_path.stem
    if not spawn:
        result.notes.append(
            "offer hung: {0} — pick it up from 'tide menu' "
            "(or 'tide pickup {1}')".format(result.offer_path.name, key)
        )
        return result

    result.spawn = spawn_offer(home, key, adapter_name=adapter_name)
    if result.handed_over:
        result.notes.append("offer {0}: {1} — {2}".format(
            key, result.spawn.get("action"), result.spawn.get("detail") or ""))
    elif result.spawn.get("action") == NOT_ALIVE:
        pass  # последней строкой скажет CLI (not_alive_line)
    else:
        result.notes.append(
            "offer hung: {0} — новую сессию сам поднять не вышло ({1}); оффер ждёт "
            "в очереди: ▶ на доске, 'tide menu' или 'tide pickup {2}'".format(
                result.offer_path.name, result.spawn.get("detail") or "без подробностей",
                key)
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


def _spawn_wanted(args) -> bool:
    """Auto-spawn unless ``--no-spawn`` or ``$TIDE_HANDOFF_NO_SPAWN`` opts out."""
    import os

    if getattr(args, "no_spawn", False):
        return False
    env = (os.environ.get(NO_SPAWN_ENV) or "").strip().lower()
    return env in ("", "0", "false", "no", "off")


def cmd_handoff_auto(args) -> int:
    """``tide handoff continue --auto`` — the seed from the arc, not from the chat.

    The positional may name the fork (``continue``/``new``) instead of an arc: with
    ``--auto`` the thread is the caller's own (``$CLAUDE_CODE_SESSION_ID`` →
    its session arc, or ``--session``). Spawns unless the stop condition holds
    (a question to the human waits / the chain stalled) — then the offer just
    hangs and the human gets a signal.
    """
    from . import autohandoff

    root = paths.require_tide_root()
    mode = getattr(args, "mode", None) or FORK_CONTINUE
    session_ref = getattr(args, "session", None)
    if args.arc in (FORK_CONTINUE, FORK_NEW):
        mode = args.arc
    elif args.arc == FORK_CLOSE:
        raise HandoffError("handoff --auto: close не передаёт нить — зови без --auto")
    elif not session_ref:
        session_ref = args.arc  # an explicit session arc
    res = autohandoff.run_auto(
        root, mode=mode, session_ref=session_ref,
        dry_run=bool(getattr(args, "dry_run", False)),
        spawn=_spawn_wanted(args), adapter_name=getattr(args, "adapter", None))
    if res.handoff is None:
        print(autohandoff.render_dry_run(res))
        return 0
    result = res.handoff
    d = res.decision
    print("tide: handoff --auto [{0}] → {1}".format(result.mode, result.summary_path))
    for note in result.notes:
        print("  {0}".format(note))
    if not d.spawn:
        print("tide: новую сессию НЕ поднимаю — {0}. Гриша получил сигнал; "
              "остановись и жди его слова.".format(d.detail))
        return 0
    _print_outcome(result)
    return 0


def _print_outcome(result: "HandoffResult") -> None:
    """LAST line on purpose: the old session reads it — stops, or keeps the thread."""
    if result.handed_over:
        print("tide: {0}".format(handed_over_line(result.spawn)))
    elif result.spawn and result.spawn.get("action") == NOT_ALIVE and result.offer_path:
        print("tide: {0}".format(not_alive_line(result.spawn, result.offer_path.stem)))


def cmd_handoff(args) -> int:
    """``tide handoff <arc>`` — distil, remind, hang the offer, raise the fresh session."""
    if getattr(args, "auto", False):
        return cmd_handoff_auto(args)
    root = paths.require_tide_root()
    summary = _read_summary_arg(args)
    result = run_handoff(
        root,
        arc_ref=args.arc,
        mode=getattr(args, "mode", None) or FORK_CONTINUE,
        summary=summary,
        from_session=getattr(args, "from_session", None),
        dry_run=bool(getattr(args, "dry_run", False)),
        spawn=_spawn_wanted(args),
        adapter_name=getattr(args, "adapter", None),
    )
    print("tide: handoff [{0}] → {1}".format(result.mode, result.summary_path))
    print(result.candidate_reminder)
    for note in result.notes:
        print("  {0}".format(note))
    _print_outcome(result)  # decision 07 + SOC-101 F25
    return 0


def report_spawn(spawn: dict, key: str) -> None:
    """Print the auto-spawn outcome; the hand-over line goes LAST (the old session stops)."""
    if spawn.get("ok"):
        print("  offer {0}: {1} — {2}".format(key, spawn.get("action"), spawn.get("detail") or ""))
        print("tide: {0}".format(handed_over_line(spawn)))
    elif spawn.get("action") == NOT_ALIVE:
        print("tide: {0}".format(not_alive_line(spawn, key)))
    else:
        print("  новую сессию сам поднять не вышло ({0}); оффер {1} ждёт в очереди: "
              "▶ на доске, 'tide menu' или 'tide pickup {1}'".format(
                  spawn.get("detail") or "без подробностей", key))


def cmd_handoffs_offer(args) -> int:
    """``tide handoffs offer …`` — hang the offer (the queue verb), then raise it.

    The ``/handoff`` skill hangs its offer through this verb, so the auto-spawn
    (decision 07) lives here too — same door, same opt-outs as ``tide handoff``.
    ``--mode close`` never spawns.
    """
    from .. import handoff_queue

    path = handoff_queue.offer_from_args(args)
    if (getattr(args, "mode", None) or "") == FORK_CLOSE or not _spawn_wanted(args):
        return 0
    report_spawn(spawn_offer(paths.control_home(), path.stem,
                             adapter_name=getattr(args, "adapter", None)), path.stem)
    return 0


def register_offer_spawn(hsub) -> None:
    """Teach ``tide handoffs offer`` to raise the fresh session (CLI edge composes it —
    the queue is domain and may not import the launcher)."""
    op = hsub.choices.get("offer")
    if op is None:
        return
    op.add_argument("--no-spawn", action="store_true", dest="no_spawn",
                    help="only hang the offer, don't raise the fresh session "
                         "(also ${0}=1)".format(NO_SPAWN_ENV))
    op.add_argument("--adapter", default=None,
                    help="terminal adapter for the fresh session (default: as ▶)")
    op.set_defaults(func=cmd_handoffs_offer)


def register(subparsers) -> None:
    """Add the ``handoff`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "handoff",
        help="warm-handoff: distil chat → arc workspace, hang an offer and raise "
             "the fresh session",
    )
    p.add_argument("arc", help="the open arc to anchor the handoff on (with --auto: "
                               "the fork word continue|new, or a session arc)")
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
    p.add_argument("--no-spawn", action="store_true", dest="no_spawn",
                   help="only hang the offer, don't raise the fresh session "
                        "(also ${0}=1)".format(NO_SPAWN_ENV))
    p.add_argument("--adapter", default=None,
                   help="terminal adapter for the fresh session (default: the "
                        "resolved pin, same as ▶)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="distil only — leave the offer queue untouched (with --auto: write "
             "nothing at all, print the decision and the seed)",
    )
    p.add_argument("--auto", action="store_true",
                   help="auto-handoff: the seed from the arc (passport, decisions, open "
                        "work items, dialog tail), not from the chat; the fresh session "
                        "is raised unless a question waits or the chain stalled")
    p.add_argument("--session", default=None,
                   help="with --auto: the session arc to hand over (default: the "
                        "caller's own, via $CLAUDE_CODE_SESSION_ID)")
    p.set_defaults(func=cmd_handoff, _cmd="handoff")

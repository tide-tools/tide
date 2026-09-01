"""tide.hooks.session_start — the SessionStart board + role reminder + warnings.

Ported from the arcs ``arcs-hook`` SessionStart banner, trimmed per
build-blueprint ``sync_hook_wiring`` SESSIONSTART: print the ``tide arc status``
board inline (no plugin system), a one-line orchestrator/worker **role
reminder**, and the net-new **canon-drift / unmerged-delta warnings**. The arcs
update-nudge and plugin-block emission are DROPPED (the package manager owns
versions; there is no plugin system).

It runs at the top of every Claude session in an opted-in project, so the agent
opens already oriented: what is on the stream, which role it holds, and whether
the canon moved under an open arc (drift) or a closed arc still owes a merge.

:func:`render` is pure (snapshot-testable); :func:`cmd_session_start` is the thin
handler. Both are defensive: outside a tide project they emit nothing and exit 0
(a SessionStart hook must never break a session).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

from .. import fields, paths, slug, sync
from ..arc import board
from . import edit_gate, role_gate

ROLE_REMINDERS = {
    # The allowed surface is quoted from the gate itself (role_gate.ALLOWED_SURFACE),
    # not re-typed: the head learns the cage on the way in instead of finding the
    # bars one denied command at a time, and the two texts cannot drift apart.
    "orchestrator": (
        "tide · role: ORCHESTRATOR — you hold the CLI (`tide …`) and dispatch "
        "build-work to worker subagents; you read, talk, orchestrate — you don't "
        "Write/Edit yourself. The human leads by WHAT and signs the gates (rules, "
        "plans, canon, contracts); don't mint ceremony unasked.\n"
        "tide · your hands: " + role_gate.ALLOWED_SURFACE
        + " Doesn't fit? Shape it or dispatch — don't spend a round finding out."
    ),
    "worker": (
        "tide · role: WORKER — work ONE open arc; write only its own output/ + "
        "delta.md. Never merge canon / promote candidates (orchestrator-only)."
    ),
}


def _role_reminder(role: str) -> str:
    """One-line reminder for the active TIDE_ROLE (defaults to the worker line)."""
    return ROLE_REMINDERS.get(role, ROLE_REMINDERS["worker"])


# Drift is deliberately NOT a WARNINGS section: the board's HEALTH footer
# (``board._health_lines``) already aggregates every drifted entry onto ONE line
# and now carries the re-stamp command, while this hook was re-listing the same
# arcs one line each right below it (live: 4 of 5 WARNINGS lines were that echo).
# One fact, one place — the footer, because it aggregates.


def _unmerged_warnings(root: Path) -> List[str]:
    """Warning lines for CLOSED arcs still carrying an unmerged ``delta.md``."""
    warnings: List[str] = []
    for off in sync.unmerged_deltas(root):
        warnings.append(
            "  ! unmerged delta: {0} → tide canon merge {1}".format(
                off.name, slug.entry_slug(off.name)
            )
        )
    return warnings


def _deferred_warnings(root: Path) -> List[str]:
    """The "канон отстал" line: arcs landed loose that owe a strict reconciliation.

    A single rolled-up warning (not one-per-arc) with the ONE catch-up command, so
    the head opens already knowing the canon lags and how to close the gap.
    """
    from .. import ledger  # lazy: keep SessionStart light

    debt = ledger.entries(root)
    if not debt:
        return []
    return [
        "  ⚠ канон отстал: {0} арок landed loose, ждут strict-реконсиляции "
        "({1}) → tide reconcile".format(len(debt), ", ".join(e.arc for e in debt))
    ]


# Contract states that anchor work (a signed/running/output contract IS an arc's
# binding). A `draft` is unsigned ⇒ NOT yet anchored. Mirrors contract.model.STATES.
_ANCHORING_CONTRACT_STATES = ("sign", "running", "output")


def _has_signed_contract(root: Path) -> bool:
    """True when any arc carries a contract in a work-anchoring state (sign/running/output).

    Lazy-imports ``contract.lifecycle`` so SessionStart stays light (it otherwise
    imports only edit_gate/board/paths/slug/sync). An unsigned ``draft`` does not
    count — only a signed contract anchors work.
    """
    from ..contract import lifecycle

    return any(
        c.get("state") in _ANCHORING_CONTRACT_STATES
        for c in lifecycle.list_contracts(Path(root))
    )


def _is_newborn(root: Path) -> bool:
    """True for a just-adopted project: no README yet AND canon still a blank skeleton.

    ``tide adopt`` writes neither a README nor any canon content — only the four-
    heading skeleton. Reproaching such a project for "readme: drift" scolds the
    agent for the absence of something that should not exist yet. Once ANY canon is
    written (or a README exists at all), this is no longer a newborn and the normal
    drift rules apply. Defensive: any read problem ⇒ not newborn (stay loud).
    """
    try:
        from .. import readme as _readme  # lazy: keep SessionStart import-light
        from ..canon import store as _store

        if _readme.readme_file(root).is_file():
            return False
        return _store.is_empty_skeleton(_store.read(root))
    except Exception:  # noqa: BLE001 — unreadable canon is not a newborn
        return False


def _readme_drift_warnings(root: Path) -> List[str]:
    """Warning line when the current project's README has drifted from canon.

    Emits a single line for code 1 (stale / missing / hand-edited / canon moved
    ahead). Code 0 (current) and code 2 (oracle-error: no CANON.md) are silent —
    the hook must never raise or break a session on any error. A NEWBORN project
    (:func:`_is_newborn`) is silent too: there is no README because there is not
    yet a canon to derive one from.
    """
    try:
        from .. import readme as _readme  # lazy: keep SessionStart import-light

        if _is_newborn(root):
            return []
        if _readme.is_manual_control_home_readme(root):
            # The home's init-written orientation README is manual by design —
            # not drift (work 51; same exemption as the board HEALTH footer).
            return []
        code, _reasons = _readme.check(root)
        if code == 1:
            return ["  readme: drift — run 'tide readme' to regenerate"]
    except Exception as exc:
        # F4: "hook must never raise" means no-raise, NOT silence.
        # Emit a stderr advisory so the degradation is visible.
        print(
            "tide: [session-start] readme-drift check failed: {0}".format(exc),
            file=sys.stderr,
        )
    return []


def _arc_first_warnings(root: Path, role: str) -> List[str]:
    """Warn the HEAD when it leads work with no open arc and no signed contract.

    Advisory only (SessionStart never blocks). A no-op for workers (they always
    hold exactly one arc) and whenever work is already anchored — an open arc OR a
    signed/running/output contract. The escape hatch keeps a legitimate read/orient
    entry on the control-home from nagging once a contract exists.
    """
    if role != "orchestrator":
        return []  # workers always hold one arc — no-op
    if edit_gate.has_open_arc(root):
        return []
    if _has_signed_contract(root):  # signed/running/output ⇒ work is anchored
        return []
    return [
        "  ⚠ arc-first: no open arc / signed contract — anchor work before "
        "leading it ('tide arc new <slug>' or 'tide go --mode new')"
    ]


def _multiple_warnings(session: Optional[str]) -> List[str]:
    """Pinch a 'Mickey 17' MULTIPLE: a session that offered a handoff which was then
    TAKEN has dissolved into its successor — one orchestrator per thread, so it must
    stand down. Fully defensive (lazy import, swallow all) — a hook never breaks a
    session; silent unless this exact session id handed the thread off."""
    if not session:
        return []
    try:
        from .. import handoff_queue as hq
        rec = hq.is_dissolved(paths.control_home(), session)
    except Exception:  # noqa: BLE001 — a hook must never raise
        return []
    if not rec:
        return []
    return [
        "  ⚠ MULTIPLE (Mickey 17): эта сессия отдала нить (оффер {0} → держит {1}). "
        "Один оркестратор на нить — встань в сторону, или дропни оффер и держи сам.".format(
            rec.get("name"), rec.get("taken_by"))
    ]


def _onboarding_nudge(root: Path) -> List[str]:
    """First-run nudge: ONE line pointing at ``tide onboarding`` until it is passed.

    Peripheral add-on (see :mod:`tide.onboarding`): lazy-imported and fully
    defensive, so it can be deleted in a single edit (this function + its one call
    site in :func:`render`) without touching any core session-start logic, and a
    broken add-on never breaks a session. Silent once onboarding is marked passed.
    """
    try:
        from ..onboarding import nudge

        return nudge(root)
    except Exception:
        return []


def _open_board_notes(root: Path) -> List[str]:
    """Surface any open focus board (доска wake) with its phone URL + focus count.

    Peripheral add-on (mirrors :func:`_onboarding_nudge`): scans arc workspaces for
    a ``board.json`` and emits one line per open board — its thread, the focus
    count, and the published Artifact URL when present (so the phone surface is one
    glance away on entry). Fully defensive + lazy: a broken/edited board never
    breaks session-start, and this whole add-on deletes in one edit (this function
    + its call site in :func:`render`).
    """
    try:
        import json as _json

        arcs = paths.arcs_dir(root)
        if not arcs.is_dir():
            return []
        notes: List[str] = []
        for bf in sorted(arcs.rglob("workspace/board.json")):
            # skip boards under a sealed (closed) arc — any __name__ path segment
            if any(seg.startswith("__") and seg.endswith("__") for seg in bf.parts):
                continue
            try:
                data = _json.loads(bf.read_text(encoding="utf-8"))
            except Exception:
                continue
            focus = data.get("focus") or {}
            cards = focus.get("cards") or []
            limit = focus.get("limit", 7)
            thread = bf.parent.parent.name  # arc dir holding workspace/
            line = "  доска {0} (фокус {1}/{2})".format(thread, len(cards), limit)
            url = data.get("artifact_url")
            if url:
                line += " → " + url
            notes.append(line)
        return notes
    except Exception:
        return []


def _project_notes_index(root: Path) -> List[str]:
    """ИНДЕКС заметок проекта (17.07): агент ЗНАЕТ, что можно взять, —
    не грузя тел. Заметка = ``.tide/notes/NN-slug.md`` (карточка-справка полки:
    команды, шпаргалки); сюда доезжают только заголовок + теги, одна строка на
    заметку + одна строка «как читать». Peripheral add-on (mirrors
    :func:`_open_board_notes`): defensive + deletable in one edit."""
    try:
        import re as _re

        d = paths.tide_dir(root) / "notes"
        if not d.is_dir():
            return []
        out: List[str] = []
        for f in sorted(d.glob("*.md")):
            try:
                head = f.read_text(encoding="utf-8", errors="ignore")[:600]
            except OSError:
                continue
            first = head.splitlines()[0] if head.splitlines() else ""
            title = first.lstrip("# ").strip() or f.stem
            m = _re.search(r"^tags:\s*(.+)$", head, _re.M)
            tags = " [{0}]".format(m.group(1).strip()) if m else ""
            out.append("  {0} — {1}{2}".format(f.stem, title, tags))
        if out:
            out.append("  (шпаргалки человека с полки доски; нужна конкретная — "
                       "читай .tide/notes/<имя>.md, целиком не грузи; правишь — "
                       "допиши строку в её ## журнал: время — что — кто)")
        return out
    except Exception:
        return []


def _svetofor_line(root: Path) -> List[str]:
    """The tier-0 Светофор: ONE line of health numbers at the very top of entry.

    Peripheral add-on (mirrors :func:`_open_board_notes`): the four tier-0 counts
    (unread / canon-debt / offers / roster-not-ready) as one glyphed line, so the
    session sees red before it bites. Fully defensive + lazy — a broken corner or a
    missing control-home yields no line, never a session break — and the whole
    add-on deletes in one edit (this function + its call site in :func:`render`)."""
    try:
        from .. import health

        return [health.render_line(health.compute_health(root))]
    except Exception:
        return []


def _session_decisions(root: Path, session: Optional[str]) -> List[str]:
    """The current nit's OPEN decisions, as lines — or [] (cand 128-A).

    Resolves the running *session* (sid) → its session arc → its nit, then renders
    that nit's open decisions (the anti-re-litigation set). Best-effort and silent:
    no sid, no match, or no open decisions → ``[]`` (a hook must never break or add
    noise). Bounded to this nit + capped in :func:`render_open_for_context` (131).
    """
    if not session:
        return []
    try:
        from .. import offload
        from ..arc import decision

        entry = offload.find_session_by_claude_id(root, session)
        if entry is None:
            return []
        thread_slug = slug.entry_slug(entry.parents[1].name)
        block = decision.render_open_for_context(root, thread_slug)
        return block.splitlines() if block else []
    except Exception:  # noqa: BLE001 — a hook must never break a session
        return []


THREAD_SCREEN_POINTER = (
    "tide · to see the whole thread — goal, current step, decisions by state, "
    "what's moving, what waits for the human: `tide thread` (hygiene: "
    "`tide thread --check`). Ask the machine before asking the human to retell it."
)


def _thread_screen_pointer(root: Path, session: Optional[str]) -> List[str]:
    """One line pointing a fresh session at ``tide thread`` — or [].

    The screen's named consumer (release decision 25 — у всякой записи назван
    потребитель) is the session that just woke up with nothing but a seed. If
    nobody tells it the screen exists, it does what it has always done: asks the
    human to retell the thread. Printed only when this session actually sits in a
    thread; best-effort and silent otherwise, like every other hook add-on.
    """
    if not session:
        return []
    try:
        from .. import offload

        entry = offload.find_session_by_claude_id(Path(root), session)
        if entry is None:
            return []
        entry.parents[1]        # raises IndexError when it is not inside a thread
        return [THREAD_SCREEN_POINTER]
    except Exception:  # noqa: BLE001 — a hook must never break a session
        return []


def render(root: Path, role: str, update_note: Optional[str] = None,
           session: Optional[str] = None) -> str:
    """Render the SessionStart text: health line + board + role reminder + warnings.

    *update_note*, when present, is a non-blocking "tide update available" line
    SURFACED (never auto-applied) below the warnings. It is a parameter — not
    computed here — so :func:`render` stays pure/snapshot-testable; the live note
    is resolved by :func:`cmd_session_start`.
    """
    root = Path(root)
    # The Светофор (tier-0 health) rides at the very top — the one line seen first.
    lines: List[str] = _svetofor_line(root)
    if lines:
        lines.append("")
    # Cold entry shows LIVE work only: closed threads are finished history (live:
    # 18 of 23 STREAM rows) and belong to `tide status`, not to the opening breath.
    lines += [board.render_board(root, include_closed=False), "", _role_reminder(role)]
    lines += _thread_screen_pointer(root, session)

    # cand 128-A: the current nit's OPEN decisions ride in right below the role, so
    # an in-place/resume session sees what's already concluded and doesn't re-decide
    # it. Silent when there are none (best-effort, never breaks the session).
    decisions = _session_decisions(root, session)
    if decisions:
        lines.append("")
        lines.extend(decisions)

    warnings = (
        _unmerged_warnings(root)
        + _deferred_warnings(root)
        + _readme_drift_warnings(root)
        + _arc_first_warnings(root, role)
        + _multiple_warnings(session)
    )
    if warnings:
        lines.append("")
        lines.append("WARNINGS")
        lines.extend(warnings)

    if update_note:
        lines.append("")
        lines.append("UPDATE")
        lines.append(update_note)

    # Peripheral board add-on (deletable with _open_board_notes above): surface any
    # open focus board + its phone URL on entry (silent when there is none).
    board_notes = _open_board_notes(root)
    if board_notes:
        lines.append("")
        lines.append("BOARD")
        lines.extend(board_notes)

    # Peripheral notes add-on (deletable with _project_notes_index above): the
    # project's note cards as a light index — titles+tags only, bodies on demand.
    note_lines = _project_notes_index(root)
    if note_lines:
        lines.append("")
        lines.append("NOTES")
        lines.extend(note_lines)

    # Peripheral onboarding add-on (deletable with _onboarding_nudge above): one
    # first-run advisory line, silent once onboarding is passed.
    nudge_lines = _onboarding_nudge(root)
    if nudge_lines:
        lines.append("")
        lines.extend(nudge_lines)

    return "\n".join(lines)


# --- CLI handler -----------------------------------------------------------

def _current_role() -> str:
    """Active TIDE_ROLE via the CLI helper (lazy import avoids any load cycle)."""
    from ..cli import current_role

    return current_role()


def _open_sessions(root: Path) -> List[Path]:
    """Open session sub-arcs (nested one level inside a thread), any order."""
    out: List[Path] = []
    arcs = paths.arcs_dir(Path(root))
    if not arcs.is_dir():
        return out
    for container in arcs.iterdir():
        if (
            not container.is_dir()
            or container.name == paths.CANDIDATES_DIRNAME
            or slug.is_closed_entry(container.name)
        ):
            continue
        sub = container / paths.ARCS_DIRNAME
        if not sub.is_dir():
            continue
        for s in sub.iterdir():
            if s.is_dir() and not slug.is_closed_entry(s.name):
                out.append(s)
    return out


def _is_unclaimed_head(session_dir: Path) -> bool:
    """True when a session has no real head yet — a freshly-spawned, unlinked session.

    'Unclaimed' = its ``claude-session`` is blank or a ``<placeholder>`` AND it never
    pulsed (``offloaded-at`` 0/absent). Such a session is invisible to the board until
    it offloads (cand 93) — the perfect (and safe) target to bind a live id to.

    A PICKUP TARGET is not unclaimed: a session carrying a prepared handoff seed
    (``input/handoff-seed.md``) is waiting for its offer's launch — its sid is minted
    by the pickup launcher, never grabbed here (live 14.07: a passing SessionStart
    bound a random sid to a pending pickup session, which then read as ended before
    it was ever born).
    """
    pp = session_dir / "arc.md"
    cs = (fields.read_field(pp, "claude-session") or "").strip()
    if cs and not cs.startswith("<"):
        return False
    if (session_dir / "input" / "handoff-seed.md").is_file():
        return False
    off = (fields.read_field(pp, "offloaded-at") or "0").strip()
    return off in ("", "0")


def _link_claude_session(root: Path, session_id: Optional[str]) -> Optional[Path]:
    """Bind the running claude *session_id* to its tide session passport AT START (cand 93).

    A session that plans and WAITS for approval never offloads, so — when it wasn't
    launched through tide's own binder (e.g. the deck's ▶) — its passport stayed blank
    and the board showed the nit as 'launching' forever, spawning duplicates on retry.
    Link the id now instead of only on the first offload.

    Safe and idempotent: if any open session already pins THIS id, do nothing; else bind
    it to the ONE open session that is a fresh unclaimed head. Zero or several candidates
    ⇒ skip (never overwrite a real link, never guess between heads) — the first-offload
    linking stays as the fallback. Returns the passport written, or None.
    """
    if not session_id:
        return None
    sessions = _open_sessions(root)
    for s in sessions:
        if (fields.read_field(s / "arc.md", "claude-session") or "").strip() == session_id:
            return None  # already linked (tide's binder pinned it at launch)
    fresh = [s for s in sessions if _is_unclaimed_head(s)]
    if len(fresh) != 1:
        return None  # ambiguous or none — leave it to the first offload
    pp = fresh[0] / "arc.md"
    fields.set_field(pp, "claude-session", session_id)
    return pp


def cmd_session_start(args) -> int:
    """``tide hook session-start`` — print the board + reminder + warnings.

    Resolves the project leniently (``find`` not ``require``): outside a tide
    project it prints nothing and exits 0, so the hook is a no-op anywhere it does
    not apply rather than a session-breaking error.
    """
    root: Optional[Path] = paths.find_tide_root()
    if root is None:
        return 0
    session = _hook_session()
    if session:
        try:
            _link_claude_session(root, session)  # cand 93: board sees the head at once
        except Exception:  # noqa: BLE001 — a hook must never break a session
            pass
        try:
            # The reconcile sweeper (principle №1): bind sid→terminal so «вернуться в
            # сессию» works on EVERY path of ascent — bare `claude` by hand, a spawn
            # whose registry write failed. The session's OWN handle (from the pane's
            # env) leads; the cwd match over `orca terminal list` is the fallback.
            from .. import registry as _registry, sessions as _sessions
            _sessions.reconcile_registry(
                paths.control_home(), root, session,
                terminals=_registry.orca_terminal_list(),
                own_handle=_registry.self_pair()[1],
            )
        except Exception:  # noqa: BLE001 — the sweeper is best-effort by design
            pass
    print(render(root, _current_role(), update_note=_update_note(), session=session))
    return 0


def _hook_session() -> Optional[str]:
    """Best-effort current session id from the SessionStart hook's stdin JSON.

    TTY-guarded so a manual ``tide hook session-start`` never blocks on a read, and
    fully defensive — any hiccup yields None (no session pinch), never a break."""
    try:
        import sys as _sys
        if _sys.stdin is None or _sys.stdin.isatty():
            return None
        import json as _json
        payload = _json.loads(_sys.stdin.read() or "{}")
        return payload.get("session_id") or payload.get("session")
    except Exception:  # noqa: BLE001 — a hook must never raise
        return None


def _update_note() -> Optional[str]:
    """Best-effort 'tide update available' line for SessionStart (None on any issue).

    Lazy-imports :mod:`tide.update.core` so the hook stays light, and swallows all
    errors there — a SessionStart hook must never break a session, and surfacing
    an update is strictly advisory (the update is supervised, never auto-applied).
    """
    try:
        from ..update.core import session_note

        return session_note()
    except Exception:
        return None

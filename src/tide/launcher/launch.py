"""tide.launcher.launch — THE single launcher: every spawn path, one gesture order.

The session-life mechanics (sid pinned before birth, passport floor, handoff
reservation, scoped command, registry record) used to live per-path — menu had one
copy, spark another, the board's pickup a third that skipped half the gestures
(cand 100/101/103 all grew in those gaps). This module is the one path; surfaces
differ only in HOW they build the spec, never in WHAT the harness guarantees.

Gesture order (invariant: everything file-side happens BEFORE the terminal exists):

1. **sid** — pinned into the session's passport (``claude-session:``) before any
   spawn; tide mints the id, it is never read back out of claude.
2. **reserve** (pickup only) — ``handoffs.reserve(key, sid)`` pins WHICH session may
   confirm the offer; the status stays ``offered``. The flip to ``taken`` happens on
   the session's FIRST message (UserPromptSubmit hook → ``confirm_for_session``) —
   the reception is real only when the terminal actually said hello (signed A,
   2026-07-14). A failed spawn therefore never eats the offer.
3. **command** — the one scoped builder (``build_launch``): strict-MCP profile on
   EVERY path (the board's old inline pickup dropped it).
4. **spawn** via the adapter.
5. **record** — ``registry.record(sid, handle, arc)`` on success; the launcher is
   the registry's writer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .. import registry


def launch_session(
    control_home: Path,
    *,
    project: Path,
    session_dir: Path,
    adapter,
    arc_ref: Optional[str] = None,
    arc_text: Optional[str] = None,
    thread_name: Optional[str] = None,
    seed_file: Optional[str] = None,
    trigger: str = "",
    title: str = "tide",
    role: str = "orchestrator",
    handoff_key: Optional[str] = None,
    skip_permissions: bool = True,
    dry_run: bool = False,
):
    """Spawn a session's terminal with every mechanical gesture guaranteed.

    *session_dir* is the session arc (already created by the domain — birth builds
    the passport floor). *seed_file* switches the seed source: a pickup rides the
    prepared distil verbatim; otherwise the seed is built from the passport
    (*arc_ref*/*arc_text*/*thread_name*). *handoff_key* makes this a pickup:
    the offer is reserved for the minted sid before the spawn (gesture 2).
    Returns the adapter's ``SpawnResult``.
    """
    from . import menu as _menu  # lazy: menu imports us nowhere, but keep it one-way

    # 1. sid before birth of the terminal (pin survives a failed spawn — harmless).
    #    DRY-RUN пишет НИЧЕГО (cand 98): sid минтится только в память, паспорт
    #    не трогается — сборка команды честная, диск чистый.
    import uuid as _uuid

    if seed_file:
        # A pickup ALWAYS mints a fresh sid and re-pins the passport (cand 103: the
        # origin's sid is never inherited). The stored pin on a pickup session is
        # whoever touched the arc before — the offerer, or the creator's own id
        # stamped at birth (e2e 14.07: a trusted stale pin spawned claude onto a sid
        # already in use and it died on boot). Fresh launch on the distil, no resume.
        session_id = str(_uuid.uuid4())
        resume = False
        if not dry_run:
            from .. import fields as _fields

            _fields.set_field(Path(session_dir) / "arc.md", "claude-session", session_id)
    elif dry_run:
        session_id, resume = str(_uuid.uuid4()), False
    else:
        session_id, resume = _menu._bind_claude_session(session_dir, is_new=True)

    # 2. pickup: reserve, don't take — the flip is the first prompt's (signed A).
    if handoff_key and not dry_run:
        try:
            from .. import handoff_queue
            handoff_queue.reserve(control_home, handoff_key, session=session_id)
        except Exception:  # noqa: BLE001 — reservation must not kill the launch
            pass

    # 2b. the harness floor: the flip above (and the pulse nudge, and ended:) live
    # in the PROJECT's hooks — a project nobody ran `install-hooks` in strands the
    # offer reserved forever (forge, live 14.07: session up + working 20 min, board
    # honestly stuck on «поднимается»). The installer is merge-safe and idempotent,
    # so ensuring it here makes the harness a property of the launch, not of memory.
    if not dry_run:
        try:
            from ..harness import install_hooks
            install_hooks(Path(project))
        except Exception:  # noqa: BLE001 — hook wiring must not kill the launch
            pass

    # 3. one scoped command builder for every path.
    command = _menu.build_launch(
        Path(project),
        control_home=control_home,
        role=role,
        arc_ref=arc_ref,
        arc_text=arc_text,
        thread_name=thread_name,
        session_id=session_id,
        resume=resume,
        seed_file=seed_file,
        user_prompt=trigger,
        skip_permissions=skip_permissions,
        dry_run=dry_run,
    )

    # 4. spawn.
    res = adapter.spawn(command=command, cwd=str(project), title=title, dry_run=dry_run)

    # 5. record — the launcher is the registry's single writer.
    if res.ok and not dry_run:
        try:
            registry.record(control_home, session_id, str(res.ref or ""), str(session_dir))
        except Exception:  # noqa: BLE001 — the registry must not fail a live spawn
            pass
    return res

"""tide.launcher.return_cmd — ``tide return``: go back to a session's terminal.

ONE return path for every surface (the board's ⟳, the CLI, tomorrow the menu):

1. the RECORDED handle for the sid (``registry.recorded_handle`` — no list
   cross-check, cand 101);
2. probe it by FOCUSING (``adapter.focus`` — the only honest liveness test);
3. dead/unknown → spawn ``claude --resume <sid> || <fresh under the same sid>``
   through the launcher's own command builder (scoped MCP re-applied — the board's
   old inline resume dropped the project profile) and RECORD the new handle.

The board used to carry its own copy of all three gestures (``_reg_*`` /
``_orca_create`` in ``serve_live.py``); this verb is the door that lets that copy
die. ``--json`` output is the machine contract: additive fields only.

``--dry-run`` PREDICTS the same verdict instead of taking it: it never focuses (a
focus is a side effect) and never writes, but it reads the live-handle set so it can
answer ``focused`` — it used to answer ``resumed`` unconditionally, which made a
perfectly healthy registry look like a decision to spawn a duplicate (cand 144).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .. import paths, registry
from ..adapters import get_adapter
# Mac-specific by nature, and honest on every other platform: without ioreg the probe
# answers False, so tmux/Linux returns carry the field as "not locked" and read as before.
from ..adapters.orca import screen_locked as _screen_locked
from . import menu as _menu

_SID_RE = re.compile(r"[0-9a-fA-F-]{8,64}$")


def run_return(
    control_home: Path,
    *,
    sid: str,
    project: Path,
    arc: str = "",
    title: str = "",
    adapter_name: Optional[str] = None,
    say: str = "",
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Focus the session's terminal, or respawn ``--resume`` under the same sid.

    NO dissolved-gate anymore (canon №1 simplified, 16.07): past sessions
    are open history — any of them may be re-entered with one click; the thread's
    current session is derived from the chain, so a look-back can't steal it.
    *force* is kept as an accepted no-op so older boards keep working.

    *say* gives that session a TURN as well as a window: focusing a tab tells the agent
    nothing — it sits on its last turn and finds out about the human's press only by
    accident, on its own next move. With *say* the words are typed into the live
    terminal and submitted (``adapter.say``), or — when the tab had to be respawned —
    ridden in as the resumed conversation's first user turn. Still ONE session either
    way: no branch here spawns a second (canon: одна нить — одна работающая сессия).
    A turn is only ever given because a human pressed something; nothing here wakes a
    session on its own.

    Returns a plain dict (the ``--json`` contract):
    ``{ok, action: focused|resumed|failed, handle, detail}`` (``gone`` retired), plus
    ``screen_locked`` on the two happy paths: the tab was picked, but a locked mac
    brings no window forward, so the surface must say that rather than promise "look at
    Orca" (see :func:`tide.adapters.orca.screen_locked`). With *say* it also carries
    ``said`` — True only when the words really landed. A False there is an HONEST
    miss: the window came up, the turn did not, and the surface must say so instead of
    promising the agent heard.
    """
    del force  # back-compat: старые доски шлют --force, гейта больше нет
    # Одна строка (см. adapters: перевод строки отправил бы реплику на полпути).
    word = " ".join((say or "").split())
    s = (sid or "").strip()
    if not _SID_RE.fullmatch(s):
        return {"ok": False, "action": "failed", "handle": "",
                "detail": "return: bad sid {0!r}".format(sid)}
    adapter = get_adapter(adapter_name)

    # One schema, said out loud: legacy arc-keyed records cannot name a session, so
    # they are dropped rather than silently half-honoured (cand 144).
    dropped = registry.migrate(control_home)
    note = " (dropped {0} legacy arc-keyed record(s))".format(dropped) if dropped else ""

    handle = registry.recorded_handle(control_home, s)
    if handle:
        if dry_run:
            # A dry run must not focus — a focus IS a side effect — so it PREDICTS
            # from `orca terminal list` instead. An EMPTY list means orca stayed
            # silent, or the terminal is background-adopted and merely hidden (cand
            # 101), so it reads as "would focus": the same first move the live path
            # makes. Before this the dry run reported `resumed` unconditionally and
            # so could never show a focus, which is what made the duplicate look
            # like a decision to respawn when the registry was in fact fine.
            live = registry.orca_live_handles()
            if not live or handle in live:
                out = {"ok": True, "action": "focused", "handle": handle,
                       "detail": "dry-run: would focus the session's terminal" + note}
                if word:
                    # dry-run НИЧЕГО не отправляет — реплика тоже side effect
                    out["said"] = False
                    out["detail"] += " and give it the turn"
                return out
        elif adapter.focus(handle):
            out = {"ok": True, "action": "focused", "handle": handle,
                   "detail": "focused the session's terminal" + note,
                   "screen_locked": _screen_locked()}
            if word:
                # Вкладку подняли — теперь дать ей ХОД. Не вышло — говорим прямо:
                # окно перед человеком есть, а агент реплику не услышал.
                out["said"] = adapter.say(handle, word)
                out["detail"] += (" and gave it the turn" if out["said"]
                                  else "; the turn did NOT reach it — say it yourself")
            return out
        else:
            # The focus just proved the handle dead. Drop it — and every other record
            # pointing at the same corpse — so the next press probes nothing and goes
            # straight to the resume, instead of re-focusing a ghost (c636275).
            registry.forget_handle(control_home, handle)

    tab = re.sub(r"\s+", " ", title or "").strip()[:48] or "resume-{0}".format(s[:8])
    command = _menu.build_launch(
        Path(project),
        control_home=control_home,
        session_id=s,
        resume=True,
        dry_run=dry_run,
        user_prompt=word,
    )
    res = adapter.spawn(command=command, cwd=str(project), title=tab, dry_run=dry_run)
    if not res.ok:
        return {"ok": False, "action": "failed", "handle": "",
                "detail": res.detail or "return: spawn failed"}
    if not dry_run:
        registry.record(control_home, s, str(res.ref or ""), arc)
    out = {"ok": True, "action": "resumed", "handle": str(res.ref or ""),
           "detail": (res.detail or "resumed in a new terminal") + note,
           "screen_locked": _screen_locked()}
    if word:
        # Реплика уехала В КОМАНДЕ (первый ход поднятой беседы) — не отдельным
        # каналом: посылать её в терминал, который ещё грузится, некуда.
        out["said"] = not dry_run
    return out


def cmd_return(args) -> int:
    home = paths.control_home()
    project = Path(args.dir).expanduser()
    if not project.is_dir():
        print("tide: return: project dir not found: {0}".format(project))
        return 1
    out = run_return(
        home,
        sid=args.sid,
        project=project,
        arc=getattr(args, "arc", "") or "",
        title=getattr(args, "title", "") or "",
        adapter_name=getattr(args, "adapter", None),
        say=getattr(args, "say", "") or "",
        force=bool(getattr(args, "force", False)),
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    if getattr(args, "json", False):
        print(json.dumps(out, ensure_ascii=False))
    else:
        print("tide: return: {0} — {1}".format(out["action"], out["detail"]))
    return 0 if out["ok"] else 1


def register(subparsers) -> None:
    rp = subparsers.add_parser(
        "return",
        help="return to a session's terminal: focus its recorded tab, else respawn "
             "`claude --resume <sid>` under the same sid (one path for board ⟳ and CLI)",
    )
    rp.add_argument("--sid", required=True, help="the claude session id (passport claude-session:)")
    rp.add_argument("--dir", required=True, help="the project dir the session runs in")
    rp.add_argument("--arc", default="", help="the session's arc path (legacy registry key tolerance)")
    rp.add_argument("--title", default="", help="human tab title for a respawn")
    rp.add_argument("--adapter", default=None, help="terminal adapter (default: auto)")
    rp.add_argument("--say", default="", metavar="TEXT",
                    help="give the session a TURN as well as a window: type TEXT into "
                         "its live terminal and submit it (a respawn rides it in as the "
                         "resumed conversation's first turn). One line. `said` in --json "
                         "tells you whether it really landed")
    rp.add_argument("--force", action="store_true",
                    help="accepted no-op (back-compat): the dissolved-gate is retired — "
                         "past sessions are open history (canon №1, 16.07)")
    rp.add_argument("--dry-run", action="store_true", dest="dry_run", help="build, don't execute")
    rp.add_argument("--json", action="store_true", help="machine-readable result (additive fields only)")
    rp.set_defaults(func=cmd_return, _cmd="return")

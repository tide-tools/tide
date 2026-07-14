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
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .. import paths, registry
from ..adapters import get_adapter
from . import menu as _menu

_SID_RE = re.compile(r"[0-9a-fA-F-]{8,64}$")


def _no_resurrect_stamp(project: Path, sid: str, *, arc: str = "") -> Optional[str]:
    """The ``dissolved:`` verdict for *sid*'s session passport, or None.

    ONLY dissolution blocks a respawn — an ``ended:`` session is exactly the case
    where ``claude --resume`` legitimately reopens the conversation (closed the tab,
    came back). Resolution: the board's ``--arc`` (the session dir — works for
    sessions inside CLOSED threads too), else the open-session scan by pinned sid.
    No passport found → None (an unknown sid stays respawnable — the forgiving
    default).
    """
    from .. import fields
    from ..offload import find_session_by_claude_id

    pp = None
    a = (arc or "").strip()
    if a and "/.tide/arcs/" in a and (Path(a) / "arc.md").is_file():
        cand = Path(a) / "arc.md"
        if (fields.read_field(cand, "claude-session") or "").strip() == sid:
            pp = cand
        # a mismatching arc is NOT a verdict — fall through to the sid scan
        # (live 14.07: an arc pointing at a sibling session made the gate give up
        # and RESURRECT a dissolved head; the arc param is a hint, never the truth)
    if pp is None:
        entry = find_session_by_claude_id(Path(project), sid)
        if entry is not None and (
                fields.read_field(entry / "arc.md", "claude-session") or "").strip() == sid:
            pp = entry / "arc.md"
    if pp is None or not pp.is_file():
        return None
    stamp = (fields.read_field(pp, "dissolved") or "").strip()
    return "dissolved {0}".format(stamp) if stamp else None


def run_return(
    control_home: Path,
    *,
    sid: str,
    project: Path,
    arc: str = "",
    title: str = "",
    adapter_name: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Focus the session's terminal, or respawn ``--resume`` under the same sid.

    Returns a plain dict (the ``--json`` contract):
    ``{ok, action: focused|resumed|failed, handle, detail}``.
    """
    s = (sid or "").strip()
    if not _SID_RE.fullmatch(s):
        return {"ok": False, "action": "failed", "handle": "",
                "detail": "return: bad sid {0!r}".format(sid)}
    adapter = get_adapter(adapter_name)

    handle = registry.recorded_handle(control_home, s, arc=arc)
    if handle and not dry_run and adapter.focus(handle):
        return {"ok": True, "action": "focused", "handle": handle,
                "detail": "focused the session's terminal"}

    # No live tab → before respawning, check the passport: a DISSOLVED head gave its
    # thread away and must never be resurrected (one holder per thread — respawning
    # it would mint a second); an ENDED one finished. Focusing a live tab above is
    # fine (a look-back reads, it doesn't hold) — the gate is on resurrection only.
    stamp = _no_resurrect_stamp(Path(project), s, arc=arc)
    if stamp:
        return {"ok": False, "action": "gone", "handle": "",
                "detail": "session {0}: {1} — нить у преемника, respawn запрещён".format(
                    s[:8], stamp)}

    tab = re.sub(r"\s+", " ", title or "").strip()[:48] or "resume-{0}".format(s[:8])
    command = _menu.build_launch(
        Path(project),
        control_home=control_home,
        session_id=s,
        resume=True,
        dry_run=dry_run,
    )
    res = adapter.spawn(command=command, cwd=str(project), title=tab, dry_run=dry_run)
    if not res.ok:
        return {"ok": False, "action": "failed", "handle": "",
                "detail": res.detail or "return: spawn failed"}
    if not dry_run:
        registry.record(control_home, s, str(res.ref or ""), arc)
    return {"ok": True, "action": "resumed", "handle": str(res.ref or ""),
            "detail": res.detail or "resumed in a new terminal"}


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
    rp.add_argument("--dry-run", action="store_true", dest="dry_run", help="build, don't execute")
    rp.add_argument("--json", action="store_true", help="machine-readable result (additive fields only)")
    rp.set_defaults(func=cmd_return, _cmd="return")

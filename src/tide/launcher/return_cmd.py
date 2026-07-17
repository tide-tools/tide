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


def run_return(
    control_home: Path,
    *,
    sid: str,
    project: Path,
    arc: str = "",
    title: str = "",
    adapter_name: Optional[str] = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Focus the session's terminal, or respawn ``--resume`` under the same sid.

    NO dissolved-gate anymore (canon №1 simplified, Гриша 16.07): past sessions
    are open history — any of them may be re-entered with one click; the thread's
    current session is derived from the chain, so a look-back can't steal it.
    *force* is kept as an accepted no-op so older boards keep working.

    Returns a plain dict (the ``--json`` contract):
    ``{ok, action: focused|resumed|failed, handle, detail}`` (``gone`` retired).
    """
    del force  # back-compat: старые доски шлют --force, гейта больше нет
    s = (sid or "").strip()
    if not _SID_RE.fullmatch(s):
        return {"ok": False, "action": "failed", "handle": "",
                "detail": "return: bad sid {0!r}".format(sid)}
    adapter = get_adapter(adapter_name)

    handle = registry.recorded_handle(control_home, s, arc=arc)
    if handle and not dry_run and adapter.focus(handle):
        return {"ok": True, "action": "focused", "handle": handle,
                "detail": "focused the session's terminal"}

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
    rp.add_argument("--force", action="store_true",
                    help="accepted no-op (back-compat): the dissolved-gate is retired — "
                         "past sessions are open history (canon №1, 16.07)")
    rp.add_argument("--dry-run", action="store_true", dest="dry_run", help="build, don't execute")
    rp.add_argument("--json", action="store_true", help="machine-readable result (additive fields only)")
    rp.set_defaults(func=cmd_return, _cmd="return")

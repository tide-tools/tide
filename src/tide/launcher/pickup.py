"""tide.launcher.pickup — ``tide pickup <key>``: launch a pending handoff's session.

The board's ▶-take used to carry its own inline copy of the pickup (raw ``orca
terminal create`` + hand-built ``claude`` command: no scoped MCP, no registry write,
no reservation — the largest surviving duplicate). This verb is the door: the board
clicks, tide launches through the ONE path (``launch_session``), and the offer flips
to taken only when the fresh session actually says hello (signed A).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .. import handoff_queue, paths
from ..adapters import get_adapter
from . import menu as _menu


def run_pickup(
    control_home: Path,
    key: str,
    *,
    adapter_name: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Launch the OFFERED handoff *key* → ``{ok, action, detail}`` (the --json contract)."""
    record = next(
        (r for r in handoff_queue.list_offers(control_home, status=handoff_queue.STATUS_OFFERED)
         if r["name"] == key),
        None,
    )
    if record is None:
        return {"ok": False, "action": "failed",
                "detail": "pickup: no pending offer {0!r}".format(key)}
    adapter = get_adapter(_menu.resolve_adapter_name(control_home, adapter_name))
    res = _menu.launch_handoff(
        record,
        _menu.list_entries(control_home),
        control_home=control_home,
        adapter=adapter,
        dry_run=dry_run,
    )
    return {"ok": bool(res.ok), "action": "spawned" if res.ok else "failed",
            "detail": res.detail or ""}


def cmd_pickup(args) -> int:
    out = run_pickup(
        paths.control_home(),
        args.key,
        adapter_name=getattr(args, "adapter", None),
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    if getattr(args, "json", False):
        print(json.dumps(out, ensure_ascii=False))
    else:
        print("tide: pickup [{0}] {1}".format(out["action"], out["detail"]))
    return 0 if out["ok"] else 1


def register(subparsers) -> None:
    pp = subparsers.add_parser(
        "pickup",
        help="▶ launch a pending handoff's seeded session (reserve → spawn; the offer "
             "flips to taken on the session's first message — one path with the menu)",
    )
    pp.add_argument("key", help="the offer's name from `tide handoffs list`")
    pp.add_argument("--adapter", default=None, help="terminal adapter (default: auto)")
    pp.add_argument("--dry-run", action="store_true", dest="dry_run", help="build, don't execute")
    pp.add_argument("--json", action="store_true", help="machine-readable result (additive fields only)")
    pp.set_defaults(func=cmd_pickup, _cmd="pickup")

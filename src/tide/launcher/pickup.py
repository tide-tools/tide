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
from . import menu as _menu, raise_log as _raise_log


def in_flight(control_home: Path, record: dict, adapter) -> Optional[dict]:
    """A launch of *record* already IN FLIGHT → the focus answer; else None (go spawn).

    Shared by every door that can start an offer — ``tide pickup`` (board ▶), the
    ``tide menu`` picker and the handoff's own auto-spawn — so a second press on an
    offer that was just raised never mints a second session (one thread — one
    orchestrator).
    """
    # Идемпотент-гард (cand 93): the offer is already RESERVED and a spawn was
    # RECORDED → a second ▶ (double click; the page flips only on the session's
    # first prompt) NEVER relaunches — the reservation+record IS the truth that a
    # launch is in flight. Focus is best-effort only: in the first seconds after
    # create `orca terminal focus` can return false while the tab boots (the same
    # lesson as cand 101 — focus flakiness must not be read as death; live 14.07:
    # reading it as death minted a duplicate sid+tab).
    #
    # A reservation is only evidence of a launch IN FLIGHT while it is fresh. Past
    # the TTL it is evidence of the opposite — ▶ was pressed and the terminal never
    # said hello (19.07: Orca died in `login:`, the tab handle stayed in the registry
    # and this guard focused a corpse forever, so the offer could not be raised
    # again by hand). `pickup_stale` is already computed on every record and the menu
    # already trusts it; the door reads it too, and a stale reserve falls through to
    # a clean relaunch (the fresh `reserve` overwrites it — nothing to unwind).
    reserved = str(record.get("pickup_session") or "").strip()
    if not reserved or reserved == "-" or record.get("pickup_stale"):
        return None
    from .. import registry

    handle = registry.recorded_handle(control_home, reserved)
    if not handle:
        return None
    focused = adapter.focus(handle)
    return {"ok": True,
            "action": "focused" if focused else "already-launching",
            "handle": handle,
            "adapter": getattr(adapter, "name", ""),
            "detail": ("уже поднимается — сфокусировал вкладку" if focused
                       else "уже поднимается — вкладка бутится, дубль не чеканю")}


def run_pickup(
    control_home: Path,
    key: str,
    *,
    adapter_name: Optional[str] = None,
    dry_run: bool = False,
    raised_by: str = _raise_log.PICKUP,
    raised_after: Optional[int] = None,
) -> dict:
    """Launch the OFFERED handoff *key* → ``{ok, action, detail}`` (the --json contract).

    *raised_by* — кто жмёт (SOC-121): ``pickup`` руками, ``board`` (▶ доски
    передаёт ``--by board``), ``auto`` (:func:`tide.launcher.handoff.spawn_offer`),
    ``autoraise`` (*raised_after* — сек ожидания). Пишется в оффер и
    ``state/raises.log`` самой дверью (:func:`tide.launcher.menu.launch_handoff`)."""
    record = next(
        (r for r in handoff_queue.list_offers(control_home, status=handoff_queue.STATUS_OFFERED)
         if r["name"] == key),
        None,
    )
    if record is None:
        return {"ok": False, "action": "failed",
                "detail": "pickup: no pending offer {0!r}".format(key)}
    adapter = get_adapter(_menu.resolve_adapter_name(control_home, adapter_name))
    if not dry_run:
        busy = in_flight(control_home, record, adapter)
        if busy is not None:
            _raise_log.log(control_home, key, raised_by,
                           "уже поднимается ({0}) — дубль не поднимаю".format(busy["action"]))
            return busy
    res = _menu.launch_handoff(
        record,
        _menu.list_entries(control_home),
        control_home=control_home,
        adapter=adapter,
        dry_run=dry_run,
        raised_by=raised_by,
        raised_after=raised_after,
    )
    return {"ok": bool(res.ok), "action": "spawned" if res.ok else "failed",
            "detail": res.detail or "", "handle": str(res.ref or "") if res.ok else "",
            "adapter": getattr(adapter, "name", "")}


def cmd_pickup(args) -> int:
    out = run_pickup(
        paths.control_home(),
        args.key,
        adapter_name=getattr(args, "adapter", None),
        dry_run=bool(getattr(args, "dry_run", False)),
        raised_by=getattr(args, "by", None) or _raise_log.PICKUP,
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
    pp.add_argument("--adapter", default=None,
                    help="terminal adapter (default: $TIDE_ADAPTER, project/global pin, else auto)")
    pp.add_argument("--dry-run", action="store_true", dest="dry_run", help="build, don't execute")
    pp.add_argument("--by", choices=(_raise_log.PICKUP, _raise_log.BOARD),
                    default=_raise_log.PICKUP,
                    help="who presses: pickup (hand, default) | board (the board's ▶) — "
                         "goes to the offer's raised-by: and state/raises.log")
    pp.add_argument("--json", action="store_true", help="machine-readable result (additive fields only)")
    pp.set_defaults(func=cmd_pickup, _cmd="pickup")

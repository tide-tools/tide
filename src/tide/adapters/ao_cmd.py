"""``tide ao ensure`` — tide's hooks reach AO sessions nobody launched through tide.

``ensure_project`` merges the ``postCreate`` step only on ``tide``'s own spawn; a
project whose sessions are started straight from the AO app (music, mitehq,
nochnaya-smena) never got it. This verb merges it (or the ``.claude`` symlink)
into one project, the current one, or every tide project registered in AO —
read → merge → write the whole config, never losing env / agentConfig.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from . import ao


def _describe(res: "ao.EnsureResult", dry_run: bool) -> str:
    lines = []
    if res.changed:
        lines.append("{0}: {1} {2}".format(res.pid, "would add" if dry_run else "added",
                                           "; ".join(res.added)))
    elif not res.missing:
        lines.append("{0}: ok — hooks already reach its sessions".format(res.pid))
    if res.missing:
        # SOC-101 cand 276: the link reaches the sessions, the hooks are not in it
        lines.append("{0}: .claude is linked, but its settings.json lacks tide hooks: "
                     "{1} — `{2}`".format(res.pid, ", ".join(res.missing), res.cure))
    return "\n".join(lines + ["  ! " + n for n in res.notes])


def _targets(args) -> List[dict]:
    projects = ao.list_projects()
    if args.all:
        return [p for p in projects
                if p.get("path") and (Path(p["path"]) / ".tide").is_dir()]
    want: Optional[str] = args.project
    if want:
        for p in projects:
            if p.get("id") == want:
                return [p]
        tree = Path(want).expanduser()
    else:
        from .. import paths
        tree = paths.require_tide_root()
    real = ao._real(str(tree))
    return [p for p in projects if p.get("path") and ao._real(p["path"]) == real]


def cmd_ao_ensure(args) -> int:
    import sys

    if not ao.ao_available():
        print("tide: Agent Orchestrator CLI not found at {0}".format(ao.ao_bin()),
              file=sys.stderr)
        return 1
    try:
        targets = _targets(args)
        if not targets:
            print("tide: {0} is not an AO project — nothing to ensure".format(
                args.project or "this project"), file=sys.stderr)
            return 1
        results = [ao.ensure_hooks(p, dry_run=args.dry_run) for p in targets]
    except ao.AoError as exc:
        print("tide: {0}".format(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(_describe(r, args.dry_run))
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser("ao", help="Agent Orchestrator project wiring")
    sub = p.add_subparsers(dest="ao_cmd", metavar="<cmd>")
    en = sub.add_parser(
        "ensure",
        help="make tide's hooks reach AO sessions started outside tide "
             "(postCreate / .claude symlink, merged into the project config)")
    en.add_argument("project", nargs="?",
                    help="AO project id or path (default: this tide project)")
    en.add_argument("--all", action="store_true",
                    help="every AO project that has a .tide/")
    en.add_argument("--dry-run", action="store_true", help="say what would change")
    en.add_argument("--json", action="store_true", help="machine-readable result")
    en.set_defaults(func=cmd_ao_ensure, _cmd="ao ensure")
    p.set_defaults(func=lambda a: (p.print_help(), 0)[1], _cmd="ao")

"""tide.mcp — per-project MCP server management (``tide mcp …``).

A scoped, remembered MCP config for the project resolved from the cwd. tide's
default launch is *lean*: ``--strict-mcp-config`` with NO ``--mcp-config`` loads
zero global MCP servers (see :mod:`tide.launcher.context`). This module is how a
project opts a *small, scoped* set back in — without ever inheriting the global
telegram/gmail/slack noise.

It edits TWO files in lock-step:

* ``<root>/.tide/state/mcp.json`` — the scoped config, shape
  ``{"mcpServers": {<name>: <serverdef>}, "_disabled": {<name>: <serverdef>}}``.
  Disabled servers are *kept* (moved under ``_disabled``) so a toggle never loses
  the config.
* ``<root>/.tide/state/context.json`` — the launch profile. ``mcp_config`` points
  at ``mcp.json`` whenever any server is enabled, and is cleared (back to lean)
  when the active set becomes empty. ``strict_mcp`` always stays true.

Logic is plain functions (read/modify/write the two JSON files, argparse-free,
unit-testable); :func:`register` wires the thin ``tide mcp list/add/rm/off/on``
handlers that ``cli.py`` calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from . import io as _io, paths
from .arc.stream import StreamError

ACTIVE_KEY = "mcpServers"
DISABLED_KEY = "_disabled"


class McpError(StreamError):
    """A user-facing MCP error (empty name/target, toggling an absent server).

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on
    the same ``except`` arm (prints ``tide: …``, exits nonzero).
    """


# --- serverdef construction ------------------------------------------------

def build_serverdef(
    target: str, *, http: bool = False, env: Optional[Dict[str, str]] = None
) -> Dict[str, object]:
    """Build a server definition from *target*.

    ``--http`` (or a ``http(s)://`` target) → ``{"type": "http", "url": <target>}``.
    Otherwise *target* is a shell command: split on spaces into
    ``{"command": <argv[0]>, "args": [<rest>]}``, plus an ``env`` map when *env* is
    given — a command server that needs e.g. ``GODOT_PATH`` had no way to carry it and
    had to be hand-patched into the scoped file (cand 26). ``env`` on an http target is
    a usage error (raise), since it applies to a spawned process, not a URL.
    """
    t = (target or "").strip()
    if not t:
        raise McpError("mcp: empty server target")
    env = dict(env or {})
    if http or t.startswith("http://") or t.startswith("https://"):
        if env:
            raise McpError("mcp: -e/--env applies to a command server, not an http url")
        return {"type": "http", "url": t}
    parts = t.split()
    serverdef: Dict[str, object] = {"command": parts[0], "args": parts[1:]}
    if env:
        serverdef["env"] = env
    return serverdef


def _parse_env(pairs: Optional[List[str]]) -> Dict[str, str]:
    """Parse repeatable ``-e KEY=VAL`` flags into a dict (empty/None ⇒ ``{}``)."""
    out: Dict[str, str] = {}
    for item in pairs or []:
        if "=" not in item:
            raise McpError("mcp: --env must be KEY=VAL, got {0!r}".format(item))
        key, val = item.split("=", 1)
        key = key.strip()
        if not key:
            raise McpError("mcp: --env has an empty key: {0!r}".format(item))
        out[key] = val
    return out


def summarize(serverdef: Dict[str, object]) -> str:
    """A one-line human summary of a serverdef (its url or its command line + env keys)."""
    if "url" in serverdef:
        return str(serverdef["url"])
    cmd = str(serverdef.get("command", ""))
    args = serverdef.get("args") or []
    line = " ".join([cmd, *[str(a) for a in args]]).strip()
    env = serverdef.get("env") or {}
    if env:
        line += "  (env: {0})".format(", ".join(sorted(env)))
    return line


# --- mcp.json read / write -------------------------------------------------

def read_mcp(root: Path) -> Dict[str, Dict[str, object]]:
    """Return ``{"mcpServers": {…}, "_disabled": {…}}`` for *root* (absent ⇒ empty)."""
    out: Dict[str, Dict[str, object]] = {ACTIVE_KEY: {}, DISABLED_KEY: {}}
    f = paths.mcp_file(Path(root))
    if not f.is_file():
        return out
    try:
        data = json.loads(f.read_text(encoding="utf-8") or "{}")
    except (json.JSONDecodeError, ValueError, OSError):
        return out
    if isinstance(data, dict):
        if isinstance(data.get(ACTIVE_KEY), dict):
            out[ACTIVE_KEY] = dict(data[ACTIVE_KEY])
        if isinstance(data.get(DISABLED_KEY), dict):
            out[DISABLED_KEY] = dict(data[DISABLED_KEY])
    return out


def write_mcp(root: Path, data: Dict[str, Dict[str, object]]) -> None:
    """Persist *data* to ``mcp.json`` (``_disabled`` omitted when empty for leanness)."""
    out: Dict[str, object] = {ACTIVE_KEY: data.get(ACTIVE_KEY, {})}
    disabled = data.get(DISABLED_KEY) or {}
    if disabled:
        out[DISABLED_KEY] = disabled
    _io.atomic_write(paths.mcp_file(Path(root)), json.dumps(out, indent=2) + "\n")


# --- context.json sync -----------------------------------------------------

def _read_context_raw(root: Path) -> Dict[str, object]:
    """Read context.json verbatim (preserving unknown keys); absent/bad ⇒ ``{}``."""
    f = paths.context_file(Path(root))
    if not f.is_file():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8") or "{}")
    except (json.JSONDecodeError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _sync_context(root: Path) -> None:
    """Point context.json ``mcp_config`` at mcp.json when active, else clear it.

    ``strict_mcp`` always stays true — scoping is never relaxed by this sync.
    """
    active = read_mcp(root)[ACTIVE_KEY]
    ctx = _read_context_raw(root)
    ctx["strict_mcp"] = True
    if active:
        ctx["mcp_config"] = str(paths.mcp_file(Path(root)))
    else:
        ctx["mcp_config"] = None
    _io.atomic_write(paths.context_file(Path(root)), json.dumps(ctx, indent=2) + "\n")


# --- operations ------------------------------------------------------------

def add_server(
    root: Path, name: str, target: str, *, http: bool = False,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, object]:
    """Add (or replace) an active server *name*; point context.json at mcp.json.

    *env* carries ``-e KEY=VAL`` vars for a command server (cand 26).
    """
    n = (name or "").strip()
    if not n:
        raise McpError("mcp: empty server name")
    serverdef = build_serverdef(target, http=http, env=env)
    data = read_mcp(root)
    data[ACTIVE_KEY][n] = serverdef
    data[DISABLED_KEY].pop(n, None)  # re-adding a disabled name reactivates it
    write_mcp(root, data)
    _sync_context(root)
    return serverdef


def remove_server(root: Path, name: str) -> None:
    """Remove *name* entirely (active or disabled); clear context if last one goes."""
    n = (name or "").strip()
    data = read_mcp(root)
    gone = data[ACTIVE_KEY].pop(n, None)
    gone_disabled = data[DISABLED_KEY].pop(n, None)
    if gone is None and gone_disabled is None:
        raise McpError("mcp: no server named {0!r}".format(name))
    write_mcp(root, data)
    _sync_context(root)


def disable_server(root: Path, name: str) -> None:
    """Move *name* from the active set to ``_disabled`` (remembered, not loaded)."""
    n = (name or "").strip()
    data = read_mcp(root)
    if n not in data[ACTIVE_KEY]:
        if n in data[DISABLED_KEY]:
            return  # already off — idempotent
        raise McpError("mcp: no server named {0!r}".format(name))
    data[DISABLED_KEY][n] = data[ACTIVE_KEY].pop(n)
    write_mcp(root, data)
    _sync_context(root)


def enable_server(root: Path, name: str) -> None:
    """Move *name* back from ``_disabled`` into the active set."""
    n = (name or "").strip()
    data = read_mcp(root)
    if n not in data[DISABLED_KEY]:
        if n in data[ACTIVE_KEY]:
            return  # already on — idempotent
        raise McpError("mcp: no server named {0!r}".format(name))
    data[ACTIVE_KEY][n] = data[DISABLED_KEY].pop(n)
    write_mcp(root, data)
    _sync_context(root)


def render_list(root: Path) -> str:
    """One line per server: ON/OFF + name + its def (url or command)."""
    data = read_mcp(root)
    active = data[ACTIVE_KEY]
    disabled = data[DISABLED_KEY]
    if not active and not disabled:
        return "(no MCP servers)"
    lines: List[str] = []
    for name, sd in active.items():
        lines.append("  ON   {0}  {1}".format(name, summarize(sd)))
    for name, sd in disabled.items():
        lines.append("  OFF  {0}  {1}".format(name, summarize(sd)))
    return "\n".join(lines)


# --- CLI wiring ------------------------------------------------------------

def _cmd_list(args) -> int:
    print(render_list(paths.require_tide_root()))
    return 0


def _cmd_add(args) -> int:
    root = paths.require_tide_root()
    env = _parse_env(getattr(args, "env", None))
    sd = add_server(root, args.name, args.target, http=getattr(args, "http", False), env=env)
    print("tide: mcp + {0}  {1}".format(args.name, summarize(sd)))
    return 0


def _cmd_rm(args) -> int:
    remove_server(paths.require_tide_root(), args.name)
    print("tide: mcp removed {0}".format(args.name))
    return 0


def _cmd_off(args) -> int:
    disable_server(paths.require_tide_root(), args.name)
    print("tide: mcp off {0}".format(args.name))
    return 0


def _cmd_on(args) -> int:
    enable_server(paths.require_tide_root(), args.name)
    print("tide: mcp on {0}".format(args.name))
    return 0


def register(subparsers) -> None:
    """Add the top-level ``mcp`` command group to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser("mcp", help="manage this project's scoped MCP servers")
    msub = p.add_subparsers(dest="mcp_cmd")

    lp = msub.add_parser("list", help="list servers (ON/OFF + def)")
    lp.set_defaults(func=_cmd_list, _cmd="mcp list")

    ap = msub.add_parser("add", help="add a server (name target [--http] [-e KEY=VAL …])")
    ap.add_argument("name")
    ap.add_argument("target", help="an http(s):// url or a shell command")
    ap.add_argument("--http", action="store_true", help="treat target as an HTTP url")
    ap.add_argument(
        "-e", "--env", action="append", metavar="KEY=VAL",
        help="env var for a command server (repeatable), e.g. -e GODOT_PATH=/path",
    )
    ap.set_defaults(func=_cmd_add, _cmd="mcp add")

    rp = msub.add_parser("rm", help="remove a server entirely (name)")
    rp.add_argument("name")
    rp.set_defaults(func=_cmd_rm, _cmd="mcp rm")

    offp = msub.add_parser("off", help="disable a server, keeping its config (name)")
    offp.add_argument("name")
    offp.set_defaults(func=_cmd_off, _cmd="mcp off")

    onp = msub.add_parser("on", help="re-enable a disabled server (name)")
    onp.add_argument("name")
    onp.set_defaults(func=_cmd_on, _cmd="mcp on")

"""tide.launcher.context — the per-project launch CONTEXT PROFILE + command builder.

The heart of tide is controlling **what context a fresh session loads**, not just
handing it a seed. A bare ``claude`` inherits every globally-configured MCP server
(telegram / gmail / slack / …) and every skill — pure noise for a scoped project
session. This module makes the launcher start a SCOPED, minimal session instead.

Two layers, mirroring the rest of the package:

* :func:`build_launch_command` — **pure** argv assembly from an already-resolved
  profile + the persisted seed-file path. Argparse-free, no I/O, unit-testable.
  ``--strict-mcp-config`` with NO ``--mcp-config`` means *zero* MCP servers load —
  the clean default we want.
* :func:`load_profile` — the **disk** wrapper: reads
  ``<project>/.tide/state/context.json`` (absent ⇒ the lean :data:`DEFAULT_PROFILE`)
  and validates it leniently, always falling back toward the *stricter* (no-MCP)
  direction so a misconfigured file can never quietly widen what loads.

Profile schema (every key optional)::

    {
      # --- tool context (what MCP/tools the fresh session loads) ---
      "strict_mcp":    true,            # pass --strict-mcp-config (default true)
      "mcp_config":    "<path>" | null, # a scoped MCP config to load (default none)
      "allowed_tools": ["..."] | null,  # --allowedTools allow-list (default none)
      "extra_args":    ["..."],         # verbatim extra claude flags (default [])

      # --- loading strategy (HOW this project explains itself on entry) ---
      "read_first":       ["..."] | null,  # orientation read-order; null ⇒ compute
                                            #   default (CLAUDE.md + cannon/CANON.md
                                            #   when present)
      "surface_on_entry": true              # show open arcs/candidates on entry
    }

The two halves are independent: the tool keys (written by ``chandler add``) control
*what loads*; the strategy keys (this unit) control *how the project orients a fresh
session*. Unknown keys are ignored, so the two writers never clobber each other.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from .. import paths
from ..adapters.base import SESSION_PROGRAM

# The lean default: a fresh session with strict MCP scoping and NO --mcp-config,
# i.e. zero global MCP servers, no tool allow-list narrowing, no extra flags. The
# strategy half defaults to "compute read_first" (None) and surface-on-entry on.
DEFAULT_PROFILE: Dict[str, object] = {
    "strict_mcp": True,
    "mcp_config": None,
    "allowed_tools": None,
    "extra_args": [],
    "read_first": None,
    "surface_on_entry": True,
}

# When read_first is unset, these are the orientation reads we try, in order —
# only the ones that actually exist on disk are surfaced (see resolve_read_first).
DEFAULT_READ_FIRST = ("CLAUDE.md",)  # cannon/CANON.md is appended via paths.canon_file


# --- pure command builder --------------------------------------------------

def build_launch_command(seed_file: str, profile: Dict[str, object]) -> List[str]:
    """Build the scoped ``claude`` argv from *profile* + the persisted *seed_file*.

    Pure (no I/O). The default lean profile yields::

        claude --strict-mcp-config --append-system-prompt @<seed_file>

    which loads NO global MCP servers. A profile may add a scoped ``--mcp-config``,
    an ``--allowedTools`` allow-list, and verbatim ``extra_args``. The seed is
    delivered by reference (``@<seed_file>``) so a multi-KB payload never has to be
    keystroked into the new terminal.
    """
    cmd: List[str] = [SESSION_PROGRAM]

    if profile.get("strict_mcp", True):
        cmd.append("--strict-mcp-config")

    mcp_config = profile.get("mcp_config")
    if mcp_config:
        cmd += ["--mcp-config", str(mcp_config)]

    allowed = profile.get("allowed_tools")
    if allowed:
        cmd += ["--allowedTools", ",".join(str(t) for t in allowed)]

    for arg in profile.get("extra_args") or []:
        cmd.append(str(arg))

    cmd += ["--append-system-prompt", "@{0}".format(seed_file)]
    return cmd


# --- profile validation + disk wrapper -------------------------------------

def _coerce_profile(data: object) -> Dict[str, object]:
    """Merge a parsed JSON *data* over :data:`DEFAULT_PROFILE`, lenient + stricter-floor.

    Unknown keys are ignored; a key with the wrong type is dropped (keeps the lean
    default for it) rather than raising — the launcher must always be able to start
    a session, and every fallback is toward *less* loaded context, never more.
    """
    profile = dict(DEFAULT_PROFILE)
    profile["extra_args"] = list(DEFAULT_PROFILE["extra_args"])  # fresh list
    if not isinstance(data, dict):
        return profile

    if isinstance(data.get("strict_mcp"), bool):
        profile["strict_mcp"] = data["strict_mcp"]

    mcp = data.get("mcp_config")
    if isinstance(mcp, str) and mcp.strip():
        profile["mcp_config"] = mcp

    allowed = data.get("allowed_tools")
    if isinstance(allowed, list) and all(isinstance(t, str) for t in allowed):
        profile["allowed_tools"] = list(allowed)

    extra = data.get("extra_args")
    if isinstance(extra, list) and all(isinstance(a, str) for a in extra):
        profile["extra_args"] = list(extra)

    # --- strategy half: read order + surface toggle (both optional) ---
    read_first = data.get("read_first")
    if isinstance(read_first, list) and all(isinstance(r, str) for r in read_first):
        profile["read_first"] = list(read_first)

    surface = data.get("surface_on_entry")
    if isinstance(surface, bool):
        profile["surface_on_entry"] = surface

    return profile


def load_profile(root: Path) -> Dict[str, object]:
    """Resolve the launch profile for project *root* (absent/invalid ⇒ lean default).

    Reads ``<root>/.tide/state/context.json``. A missing or unparseable file yields
    a copy of :data:`DEFAULT_PROFILE` — the safe, stricter floor — so the launcher
    never widens what loads on a bad config.
    """
    path = paths.context_file(Path(root))
    if not path.is_file():
        return _coerce_profile(None)
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (json.JSONDecodeError, ValueError, OSError):
        return _coerce_profile(None)
    return _coerce_profile(data)


def render_profile(root: Path) -> str:
    """A human-readable one-block summary of the resolved TOOL profile (for ``context show``)."""
    profile = load_profile(root)
    mcp = profile["mcp_config"] or "(none — strict, no global MCP)"
    allowed = profile["allowed_tools"]
    allowed_s = ",".join(allowed) if allowed else "(all — no narrowing)"
    extra = profile["extra_args"]
    extra_s = " ".join(extra) if extra else "(none)"
    lines = [
        "launch context — {0}".format(Path(root).resolve().name),
        "  strict_mcp:    {0}".format(profile["strict_mcp"]),
        "  mcp_config:    {0}".format(mcp),
        "  allowed_tools: {0}".format(allowed_s),
        "  extra_args:    {0}".format(extra_s),
        "  command:       {0}".format(
            " ".join(build_launch_command("<seed-file>", profile))
        ),
    ]
    return "\n".join(lines)


# --- loading strategy: read order + on-entry surfacing ---------------------

def resolve_read_first(root: Path, profile: Dict[str, object]) -> List[str]:
    """Resolve the orientation read-order for project *root* (project-relative paths).

    An explicit ``read_first`` list is honoured verbatim (existence is *not* a
    filter — a listed-but-missing file is a real signal, surfaced as ``(missing)``).
    When unset (``None``), the default is computed: ``CLAUDE.md`` then the cannon
    ``CANON.md``, but only the ones that actually exist — so the default never
    points a fresh session at a file that isn't there.
    """
    root = Path(root)
    configured = profile.get("read_first")
    if configured:
        return list(configured)

    out: List[str] = []
    for rel in DEFAULT_READ_FIRST:
        if (root / rel).is_file():
            out.append(rel)
    canon = paths.canon_file(root)
    if canon.is_file():
        out.append(str(canon.relative_to(root)))
    return out


def render_read_first(root: Path, profile: Dict[str, object]) -> str:
    """The ``read first`` block: orientation order, each line existence-marked."""
    reads = resolve_read_first(root, profile)
    lines = ["read first (orientation order):"]
    if not reads:
        lines.append("  (none resolved — no CLAUDE.md / cannon found)")
        return "\n".join(lines)
    for rel in reads:
        missing = "" if (Path(root) / rel).is_file() else "  (missing)"
        lines.append("  {0}{1}".format(rel, missing))
    return "\n".join(lines)


def render_enter(root: Path) -> str:
    """The full deterministic on-entry view: tool profile + read order + work summary.

    This is what makes ``tide context show`` self-explanatory: a fresh session (or a
    human) lands and the project states *what loads*, *what to read first*, and *what
    work is open* — no chat, no guessing. The work summary is gated by the strategy
    field ``surface_on_entry`` (default on); a legacy pre-tide ``.arcs/`` dir is noted
    gracefully so nothing about the project's history is silently dropped.
    """
    root = Path(root)
    profile = load_profile(root)
    parts = [render_profile(root), "", render_read_first(root, profile)]

    if profile.get("surface_on_entry", True):
        from ..arc.board import render_entry_summary  # lazy: avoid import cycle

        parts.append("")
        parts.append(render_entry_summary(root))
        if (root / ".arcs").is_dir():
            parts.append(
                "  note: legacy .arcs/ present (pre-tide arcs method — not summarized)"
            )
    return "\n".join(parts)


# --- CLI handler -----------------------------------------------------------

def cmd_context(args) -> int:
    """``tide context [show]`` — deterministic on-entry view (tools + read order + work)."""
    root = paths.require_tide_root()
    print(render_enter(root))
    return 0


def register(subparsers) -> None:
    """Add the ``context`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "context", help="show the resolved per-project launch context profile"
    )
    csub = p.add_subparsers(dest="context_cmd")
    sp = csub.add_parser("show", help="print the resolved profile + scoped command")
    sp.set_defaults(func=cmd_context, _cmd="context show")
    # bare `tide context` behaves like `tide context show`
    p.set_defaults(func=cmd_context, _cmd="context")

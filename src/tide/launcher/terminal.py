"""tide.launcher.terminal — ``tide terminal``: a clean, logged-in, seeded session HERE.

This is the native absorption of the old ``~/.local/bin/tide-go`` shell one-liner:
a single command that drops you into a **fresh, scoped, still-logged-in** Claude
session *in the current terminal* — no new window, no Orca, no tmux. It ``exec``s
``claude`` in place (``os.execvp``), so the ``tide`` process is REPLACED by the
session: the same terminal, now running a clean seeded Claude.

Why not ``--bare``? Auth lives in ``~/.claude.json`` (the ``oauthAccount`` block),
NOT in ``settings.json`` (``apiKeyHelper`` is unset). ``--bare`` explicitly never
reads OAuth or the keychain, so it logs you OUT ("Not logged in"). The lean,
not-bare path keeps ``~/.claude.json`` in play, so the session stays authenticated.
The scoping we DO want — zero global MCP servers, no skill noise — is achieved with
``--strict-mcp-config`` (from the shared :func:`context.build_launch_command`) plus
``--disable-slash-commands`` (what tide-go used to trim skill noise).

What it loads, what it cuts:

* CUT  — global MCP servers (``--strict-mcp-config``, no ``--mcp-config``), skills
  (``--disable-slash-commands``), and permission prompts
  (``--dangerously-skip-permissions`` — a deliberate operator choice for the
  interactive head, opt out with ``--no-skip-permissions``; never on spawned
  autonomous workers, which go through the menu/Orca path instead).
* KEPT — OAuth auth (``~/.claude.json``), and ``~/.claude/CLAUDE.md`` (the ecc rules).
  There is no standalone flag to skip CLAUDE.md auto-discovery while keeping OAuth —
  the only flag that skips it is ``--bare``, which also drops auth. So the CLAUDE.md
  chain stays; see ``CLAUDE_MD_GAP`` and the module docstring note. (The big bloat —
  plugins + MCP + skills — is already cut.)

The seed is delivered by reference (``--append-system-prompt @<file>``). Its source,
in priority order: an explicit ``--seed <path>``; the control-home's ``MIGRATE.md``
then ``RESUME.md``; else a generated minimal "you entered clean" seed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from .. import paths
from ..adapters.base import SESSION_PROGRAM, persist_seed
from . import context

# The skill-noise trim tide-go carried: disable all skills in the fresh session.
DISABLE_SLASH = "--disable-slash-commands"

# Skip permission prompts in the interactive head session. This is a DELIBERATE
# operator choice for `tide terminal` — the human-driven coordinator session, where
# constant prompts kill the flow — NOT for autonomous/spawned workers. Spawned
# sessions go through context.build_launch_command (the menu / Orca path), which
# never adds this; only the in-terminal head opts in (opt out with --no-skip-perms).
SKIP_PERMISSIONS = "--dangerously-skip-permissions"

# Control-home seed files, in resolution priority (first that exists wins).
SEED_FILENAMES = ("MIGRATE.md", "RESUME.md")

# Documented finding: no flag skips ~/.claude/CLAUDE.md while keeping OAuth auth.
CLAUDE_MD_GAP = (
    "note: ~/.claude/CLAUDE.md (ecc rules) still auto-loads — the only flag that "
    "skips it is --bare, which also drops OAuth auth. Kept on purpose (auth > the "
    "rules-chain); the heavy bloat (plugins/MCP/skills) is already cut."
)

# The fallback seed when no MIGRATE.md/RESUME.md exists: orient a fresh session to
# read the apex first, greet, summarize in three lines, then wait for the human.
MINIMAL_SEED = """# tide — clean terminal session

You were launched by `tide terminal`: a fresh, scoped, logged-in session in this
terminal. Global MCP servers and skills are intentionally cut; you start clean.

Do, in order:
1. Read the apex vector, then this project's CANON — get oriented before acting.
2. Greet the human in one line.
3. Say where we are in **three lines** (no more).
4. Then STOP and wait — do not start work until the human points you at something.
"""


# --- resolution (pure-ish helpers) -----------------------------------------

def find_control_home(start: Optional[Path] = None) -> Path:
    """Resolve the seed/cwd root: nearest control-home ancestor, else the tide root.

    Climbs from *start* (default: cwd) looking for a control-home (a tide root that
    carries ``roster.md`` — the cross-project entry, where ``MIGRATE.md`` lives). If
    none is found, falls back to the nearest ``.tide/`` project root so the command
    still works inside a plain project. Raises if there is no tide root at all.
    """
    here = Path(start).resolve() if start is not None else Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if paths.is_control_home(candidate):
            return candidate
    return paths.require_tide_root(here)


def resolve_seed_file(root: Path, override: Optional[str] = None) -> str:
    """Return the seed-file path to hand the session (``@<file>``), persisting if needed.

    Priority: an explicit *override*; then ``MIGRATE.md`` / ``RESUME.md`` at *root*;
    else the generated :data:`MINIMAL_SEED` written to a temp file. Always returns a
    real, readable path so ``--append-system-prompt @<file>`` resolves.
    """
    if override:
        return str(Path(override).expanduser().resolve())
    for name in SEED_FILENAMES:
        candidate = Path(root) / name
        if candidate.is_file():
            return str(candidate.resolve())
    return str(persist_seed(MINIMAL_SEED, "terminal-clean"))


def build_terminal_command(
    seed_file: str,
    profile: dict,
    *,
    disable_slash: bool = True,
    skip_permissions: bool = True,
) -> List[str]:
    """Assemble the scoped ``claude`` argv for ``tide terminal`` from the lean builder.

    Reuses :func:`context.build_launch_command` (the single source of the scoped
    shape — ``--strict-mcp-config``, the seed reference, any profile extras) and
    splices in :data:`DISABLE_SLASH` and :data:`SKIP_PERMISSIONS` right after the
    program. NEVER adds ``--bare`` (that would drop OAuth auth). *disable_slash*
    False leaves skills enabled; *skip_permissions* False keeps prompts on (the flag
    is a deliberate head-session choice — see :data:`SKIP_PERMISSIONS`).
    """
    cmd = context.build_launch_command(seed_file, profile)
    inserts: List[str] = []
    if disable_slash and DISABLE_SLASH not in cmd:
        inserts.append(DISABLE_SLASH)
    if skip_permissions and SKIP_PERMISSIONS not in cmd:
        inserts.append(SKIP_PERMISSIONS)
    cmd[1:1] = inserts  # after the program name, before the flags
    return cmd


# --- CLI handler -----------------------------------------------------------

def cmd_terminal(args) -> int:
    """``tide terminal`` — exec a clean, logged-in, seeded session in THIS terminal.

    With ``--dry-run`` it prints the resolved command (cwd, seed source, auth note)
    and returns without exec'ing — the only way to inspect the launch from inside a
    subagent or a test without nesting a live session.
    """
    root = find_control_home()
    seed_file = resolve_seed_file(root, getattr(args, "seed", None))
    profile = context.load_profile(root)
    disable_slash = not getattr(args, "no_disable_slash", False)
    skip_permissions = not getattr(args, "no_skip_permissions", False)
    command = build_terminal_command(
        seed_file, profile, disable_slash=disable_slash, skip_permissions=skip_permissions
    )

    if getattr(args, "dry_run", False):
        print("tide terminal — clean logged-in seeded session (dry run, not exec'd)")
        print("  cwd:     {0}".format(root))
        print("  seed:    {0}".format(seed_file))
        print("  command: {0}".format(" ".join(command)))
        print("  auth:    kept — no --bare, so ~/.claude.json (OAuth) loads")
        skip_state = "on" if skip_permissions else "off"
        print(
            "  perms:   {0} — {1} is a deliberate operator choice for the "
            "interactive head (NOT autonomous workers)".format(skip_state, SKIP_PERMISSIONS)
        )
        print("  {0}".format(CLAUDE_MD_GAP))
        return 0

    # Replace this process with the clean session in the SAME terminal. os.execvp
    # does not return on success; the cwd is set first so the session lands at root.
    os.chdir(root)
    os.execvp(SESSION_PROGRAM, command)
    return 0  # pragma: no cover — unreachable once execvp succeeds


def register(subparsers) -> None:
    """Add the top-level ``terminal`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "terminal",
        help="exec a clean, logged-in, seeded claude session in THIS terminal",
    )
    p.add_argument("--seed", help="explicit seed file (default: control-home MIGRATE.md/RESUME.md)")
    p.add_argument(
        "--no-disable-slash",
        action="store_true",
        dest="no_disable_slash",
        help="keep skills enabled (default: disabled, like tide-go)",
    )
    p.add_argument(
        "--no-skip-permissions",
        action="store_true",
        dest="no_skip_permissions",
        help="keep permission prompts on (default: skipped for the interactive head)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="print the resolved command (cwd, seed, auth) without exec'ing",
    )
    p.set_defaults(func=cmd_terminal, _cmd="terminal")

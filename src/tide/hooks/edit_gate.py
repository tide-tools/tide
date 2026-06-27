"""tide.hooks.edit_gate — the PreToolUse edit-gate (block edits with no open arc).

Ported from the arcs ``arcs-gate`` PreToolUse hook (matcher
``Edit|Write|MultiEdit``), retargeted to ``.tide/`` and extended with tide's
between-arcs barrier. In a tide project a *project* file edit is refused until a
**worker arc is open**, so work is always anchored to an arc (input→workspace→
output), never a loose edit.

Load-bearing scan discipline (build-blueprint ``sync_hook_wiring`` EDIT-GATE):

* Scan the canonical passport (``arc.md`` / ``<slug>-goal.md``) at the **stream**
  level and ONE **substream** level only.
* **SKIP** closed ``__…__`` dirs — a closed workspace legitimately keeps its old
  ``status: active`` prose around, and counting it would wedge the gate open.
* **NEVER** ``grep -r`` / ``rglob`` for ``status: active`` — that anti-footgun is
  the whole reason for the structured scan (a closed arc's literal text would
  otherwise re-open the gate).

Always-allowed: any edit **inside ``.tide/``** (the agent must be free to write
arc output, deltas, reports, canon merges). Net-new barrier (decision 9): while
any closed arc still carries an unmerged ``delta.md`` the gate blocks project
edits too — the delta must funnel through ``tide canon merge`` first.

Decision logic is pure (:func:`decide`); :func:`cmd_edit_gate` is the thin CLI
handler that reads the Claude Code PreToolUse payload from stdin and maps the
decision to an exit code (``0`` allow / ``2`` block, stderr carries the reason).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from .. import fields, paths, slug, sync
from ..arc import stream

# PreToolUse exit-code protocol (Claude Code): 0 = allow, 2 = block + feed stderr
# back to the model. We never use 1 here (that would be a hook *error*, not a gate
# decision).
ALLOW = 0
BLOCK = 2

# The tool_input field every edit tool carries (Edit/Write/MultiEdit all name the
# target file the same way).
FILE_KEY = "file_path"


# --- open-arc scan (stream + one substream level, never recursive) ----------

def _open_dirs(stream_dir: Path) -> List[Path]:
    """Non-closed entry dirs of *stream_dir* (skip ``candidates/`` + ``__…__``)."""
    if not Path(stream_dir).is_dir():
        return []
    return [
        p
        for p in sorted(Path(stream_dir).iterdir())
        if p.is_dir()
        and p.name != paths.CANDIDATES_DIRNAME
        and not slug.is_closed_entry(p.name)
    ]


def open_entries(root: Path) -> List[Path]:
    """Open entries at the stream level + ONE substream level (goals' ``arcs/``).

    Deliberately not recursive: this is the anti-``grep -r`` discipline — we look
    exactly where an open arc/goal can live and nowhere else, so a closed arc's
    workspace can never feed the gate.
    """
    arcs = paths.arcs_dir(Path(root))
    result: List[Path] = []
    for entry in _open_dirs(arcs):
        result.append(entry)
        if slug.is_goal_entry(entry.name):
            result.extend(_open_dirs(entry / paths.ARCS_DIRNAME))
    return result


def _is_active(entry_dir: Path) -> bool:
    """True when an entry's canonical passport carries ``status: active``."""
    return fields.read_field(stream.passport_path(entry_dir), "status") == "active"


def has_open_arc(root: Path) -> bool:
    """True when at least one scanned entry is open (``status: active``)."""
    return any(_is_active(e) for e in open_entries(Path(root)))


# --- path classification ---------------------------------------------------

def _resolve_path(file_path: str, cwd: Path) -> Path:
    """Resolve a (possibly relative) edit target against *cwd*."""
    p = Path(file_path)
    if not p.is_absolute():
        p = Path(cwd) / p
    # Resolve without requiring existence (a Write may be creating the file).
    return Path(p).expanduser().resolve(strict=False)


def is_inside_tide(file_path: Path, root: Path) -> bool:
    """True when *file_path* lives inside the project's ``.tide/`` meta dir."""
    tdir = paths.tide_dir(Path(root)).resolve(strict=False)
    fp = Path(file_path)
    return fp == tdir or tdir in fp.parents


# --- decision --------------------------------------------------------------

def decide(file_path: Optional[str], cwd: Path) -> Tuple[int, str]:
    """Decide whether an edit to *file_path* is allowed; return ``(code, reason)``.

    ``code`` is :data:`ALLOW` (0) or :data:`BLOCK` (2). Order of checks:

    1. No resolvable project root → allow (the dir is not a tide project / not
       opted in — the gate stays out of the way).
    2. Edit **inside ``.tide/``** → always allow.
    3. A closed arc carries an unmerged ``delta.md`` → block (merge first).
    4. No open worker arc → block (open an arc before editing).
    5. Otherwise → allow.
    """
    if not file_path:
        # Nothing to reason about (e.g. a tool with no file target) — stay out.
        return ALLOW, ""

    resolved = _resolve_path(file_path, Path(cwd))
    # Find the project from the edit target's own location (its parent may exist
    # even when the file itself is being created), falling back to cwd.
    root = paths.find_tide_root(resolved.parent) or paths.find_tide_root(Path(cwd))
    if root is None:
        return ALLOW, ""

    if is_inside_tide(resolved, root):
        return ALLOW, "edit inside .tide/ is always allowed"

    offenders = sync.unmerged_deltas(root)
    if offenders:
        names = ", ".join(o.name for o in offenders)
        return BLOCK, (
            "tide: blocked — {n} closed arc(s) carry an unmerged canon-delta "
            "({names}); run 'tide canon merge <arc>' before editing".format(
                n=len(offenders), names=names
            )
        )

    if not has_open_arc(root):
        return BLOCK, (
            "tide: blocked — no open arc in this project. Open a worker arc first "
            "('tide arc new <slug>' / 'tide arc open <slug>'), then edit "
            "input→workspace→output. (edits inside .tide/ are always allowed)"
        )

    return ALLOW, ""


# --- CLI handler -----------------------------------------------------------

def _read_payload(stream_in) -> dict:
    """Parse the Claude Code PreToolUse JSON payload from *stream_in* (lenient)."""
    try:
        raw = stream_in.read()
    except (OSError, ValueError):
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _file_path_from_payload(payload: dict) -> Optional[str]:
    """Pull the edit target ``file_path`` out of a PreToolUse payload."""
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        fp = tool_input.get(FILE_KEY)
        if isinstance(fp, str) and fp:
            return fp
    return None


def cmd_edit_gate(args) -> int:
    """``tide hook edit-gate`` — the dispatched PreToolUse handler.

    Reads the tool payload from stdin, decides, and exits 0 (allow) or 2 (block,
    reason on stderr). A missing/garbled payload is treated as "allow" so the gate
    never wedges a session shut on a parse hiccup.
    """
    payload = _read_payload(sys.stdin)
    file_path = _file_path_from_payload(payload)
    code, reason = decide(file_path, Path.cwd())
    if code == BLOCK:
        print(reason, file=sys.stderr)
    return code

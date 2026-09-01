"""tide.roster — the control-home registry of dispatchable projects.

Ported from ``canon focus`` (decision: drop the helm coupling — tide is its own
control-home). The roster lives at ``<control-home>/roster.md`` and is a flat
list the orchestrator session picks projects from:

    # tide roster
    name | path
    name | path | environment

One line per project; three operations — ``add`` (register / replace by name),
``rm`` (remove by name), ``ls`` (render) — all order-preserving and re-runnable.

Line format and parse rule
--------------------------
A roster line is ``name | path`` (local) or ``name | path | environment``
(remote).  The ``environment`` field is **optional** — an absent field means the
project lives on this machine.

Parse algorithm (unambiguous; paths must not contain ``|``):

1. ``stripped.partition("|")``  →  ``name``, _, ``rest``
2. If ``"|"`` appears in ``rest``:
       ``rest.rpartition("|")``  →  ``path_part``, _, ``env``
   Else:
       ``path_part = rest``, env absent.
3. Strip whitespace from ``name``, ``path_part``, and ``env``.
   A blank ``env`` after stripping is treated as absent (no key in the dict).

Round-trip guarantee: ``_render`` writes ``name | path`` when env is absent and
``name | path | env`` when env is present.  Reading back either form recovers the
original dict shape, so old 2-field rosters are **byte-identical** after a write.

Backward-compatibility contract
--------------------------------
*  Old ``name | path`` lines parse to ``{"name": …, "path": …}`` — no
   ``"environment"`` key.  Existing callers that access only ``name``/``path``
   are unaffected.
*  New ``name | path | environment`` lines parse to
   ``{"name": …, "path": …, "environment": …}``.
*  The ``"environment"`` key is present in a dict **only when non-empty** so
   ``entries == [{"name": …, "path": …}]`` equality checks in older tests
   continue to pass.

Logic is plain functions (argparse-free, unit-testable); :func:`register` wires
the thin ``tide roster add|rm|ls`` handlers that ``cli.py`` calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from . import io as _io, paths
from .arc.stream import StreamError

HEADER = "# tide roster"
SEP = " | "

# Project lifecycle status. ``active`` is the default and is NEVER stored as a
# dict key (so old 2-field shapes stay equal); only ``archived`` is persisted.
STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
STATUSES = (STATUS_ACTIVE, STATUS_ARCHIVED)


class RosterError(StreamError):
    """A user-facing roster error (empty name/path, removing an absent project).

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on
    the same ``except`` arm (prints ``tide: …``, exits nonzero).
    """


# --- parse / serialise -----------------------------------------------------

def _parse_third_field(raw: str) -> Dict[str, str]:
    """Parse the optional 3rd roster field into partial entry keys.

    Two accepted forms:

    * **legacy bare value** — the orca ``environment`` name (``box``); kept for
      every pre-tag roster, so those files stay byte-identical.
    * **``key=value`` tags** — space-separated (``env=box status=archived``).
      Recognised keys: ``env`` → ``environment``, ``status`` → ``status``.

    ``status`` is only returned when non-default (i.e. ``archived``), mirroring
    the optional-``environment`` contract. Unknown keys / blank values are
    ignored.
    """
    raw = raw.strip()
    out: Dict[str, str] = {}
    if not raw:
        return out
    if "=" not in raw:  # legacy bare environment
        out["environment"] = raw
        return out
    for tok in raw.split():
        key, _, val = tok.partition("=")
        key, val = key.strip(), val.strip()
        if not val:
            continue
        if key == "env":
            out["environment"] = val
        elif key == "status" and val != STATUS_ACTIVE:
            out["status"] = val
    return out


def _format_third_field(entry: Dict[str, str]) -> str:
    """Serialise an entry's 3rd field, or ``""`` when local + active.

    An active entry with an env renders as the **legacy bare value** so existing
    rosters round-trip byte-for-byte. The ``key=value`` tag form is used only
    once a non-default ``status`` is present.
    """
    env = entry.get("environment") or ""
    status = entry.get("status") or ""
    if status and status != STATUS_ACTIVE:
        parts = []
        if env:
            parts.append("env={0}".format(env))
        parts.append("status={0}".format(status))
        return " ".join(parts)
    return env


def _parse_line(line: str):
    """Return an entry dict for a roster line, or None.

    Supports both the 2-field form ``name | path`` (returns ``{"name", "path"}``)
    and the 3-field form ``name | path | environment`` (returns
    ``{"name", "path", "environment"}``).  See the module docstring for the full
    parse algorithm.  The ``"environment"`` key is included **only** when a
    non-blank env is found — keeping old-style dicts unchanged.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "|" not in stripped:
        return None

    # Step 1: isolate name (before first '|').
    name, _, rest = stripped.partition("|")
    name = name.strip()

    # Step 2: split off the optional 3rd field (after the last '|' in rest).
    if "|" in rest:
        path_part, _, third = rest.rpartition("|")
        if "|" in path_part:  # 4+ fields → malformed line; silently skip
            return None
        path = path_part.strip()
        third = third.strip()
    else:
        path = rest.strip()
        third = ""

    if not name or not path:
        return None

    entry: dict = {"name": name, "path": path}
    entry.update(_parse_third_field(third))
    return entry


def read_roster(root: Path) -> List[Dict[str, str]]:
    """Return roster entries (``[{'name','path'}, …]``) in file order, or ``[]``.

    A missing roster file is simply an empty roster (not an error).
    """
    f = paths.roster_file(root)
    if not f.is_file():
        return []
    out: List[Dict[str, str]] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        entry = _parse_line(line)
        if entry is not None:
            out.append(entry)
    return out


def _render(entries: List[Dict[str, str]]) -> str:
    """Serialise *entries* into roster text (header + one line per entry).

    Local entries (no ``"environment"`` key) render as ``name | path``, which is
    byte-identical to the old 2-field format.  Remote entries render as
    ``name | path | environment``.
    """
    lines = [HEADER]
    for e in entries:
        third = _format_third_field(e)
        if third:
            lines.append("{0}{1}{2}{1}{3}".format(e["name"], SEP, e["path"], third))
        else:
            lines.append("{0}{1}{2}".format(e["name"], SEP, e["path"]))
    return "\n".join(lines) + "\n"


def _write(root: Path, entries: List[Dict[str, str]]) -> None:
    f = paths.roster_file(root)
    _io.atomic_write(f, _render(entries))


# --- operations ------------------------------------------------------------

def add(
    root: Path,
    name: str,
    path: str,
    *,
    env: "str | None" = None,
    status: "str | None" = None,
) -> List[Dict[str, str]]:
    """Register *name*→*path* (with optional *env*/*status*), replacing by name.

    Order-preserving: an existing name keeps its slot (path/env/status updated in
    place); a new name is appended.  Creates the roster file (with header) if
    absent.  Returns the new entry list.

    *env* is the orca environment name for projects on a remote machine.  Pass
    ``None`` (default) for projects that live on this machine.  When *env* is
    ``None`` (or empty) any previously stored environment is **cleared**.

    *status* is the project lifecycle (``active``/``archived``).  ``active`` (the
    default, also ``None``/empty) is never persisted — it clears any stored
    status, reverting the entry to active.  Only ``archived`` is written.
    """
    n = (name or "").strip()
    p = (path or "").strip()
    e_env = (env or "").strip()
    e_status = (status or "").strip()
    if not n:
        raise RosterError("roster: empty project name")
    if not p:
        raise RosterError("roster: empty project path")
    if "|" in p:
        raise RosterError("roster: project path must not contain '|'")
    if e_status and e_status not in STATUSES:
        raise RosterError(
            "roster: invalid status {0!r} (want {1})".format(
                e_status, " or ".join(STATUSES)
            )
        )

    keeps_status = bool(e_status) and e_status != STATUS_ACTIVE

    entries = read_roster(root)
    updated = [dict(e) for e in entries]
    for e in updated:
        if e["name"] == n:
            e["path"] = p
            if e_env:
                e["environment"] = e_env
            else:
                e.pop("environment", None)
            if keeps_status:
                e["status"] = e_status
            else:
                e.pop("status", None)
            break
    else:
        new_entry: Dict[str, str] = {"name": n, "path": p}
        if e_env:
            new_entry["environment"] = e_env
        if keeps_status:
            new_entry["status"] = e_status
        updated.append(new_entry)
    _write(root, updated)
    return updated


def set_status(root: Path, name: str, status: str) -> List[Dict[str, str]]:
    """Set an existing project's lifecycle *status* in place (archive / restore).

    Unlike :func:`add` this needs no path — it flips an already-registered entry.
    ``active`` clears the stored status; ``archived`` sets it. Raises
    :class:`RosterError` for an unknown status or an absent project.
    """
    n = (name or "").strip()
    s = (status or "").strip()
    if s and s not in STATUSES:
        raise RosterError(
            "roster: invalid status {0!r} (want {1})".format(
                s, " or ".join(STATUSES)
            )
        )
    entries = read_roster(root)
    updated = [dict(e) for e in entries]
    for e in updated:
        if e["name"] == n:
            if s and s != STATUS_ACTIVE:
                e["status"] = s
            else:
                e.pop("status", None)
            _write(root, updated)
            return updated
    raise RosterError("roster: no project named {0!r}".format(name))


def remove(root: Path, name: str) -> List[Dict[str, str]]:
    """Remove the project named *name*; raise :class:`RosterError` if absent.

    Returns the new entry list. The header is preserved even when the roster
    becomes empty.
    """
    n = (name or "").strip()
    entries = read_roster(root)
    kept = [dict(e) for e in entries if e["name"] != n]
    if len(kept) == len(entries):
        raise RosterError("roster: no project named {0!r}".format(name))
    _write(root, kept)
    return kept


def render_entries(entries: List[Dict[str, str]]) -> str:
    """One-line-per-project rendering of *entries*, or a ``(no projects)`` note.

    Pure (no disk): callers that show a SUBSET — e.g. the seed, which drops
    archived projects so a fresh session is not handed dead paths — filter first
    and render through here instead of copying the line format.
    """
    if not entries:
        return "(no projects)"

    lines = []
    for e in entries:
        third = _format_third_field(e)
        if third:
            lines.append("{0}{1}{2}{1}{3}".format(e["name"], SEP, e["path"], third))
        else:
            lines.append("{0}{1}{2}".format(e["name"], SEP, e["path"]))
    return "\n".join(lines)


def render_list(root: Path) -> str:
    """One-line-per-project rendering of the WHOLE roster (archived included).

    Local active entries render as ``name | path``; remote entries add a legacy
    bare ``| env``; archived entries use the ``| ... status=archived`` tag form.
    """
    return render_entries(read_roster(root))


# --- CLI wiring ------------------------------------------------------------

def _root() -> Path:
    # Roster ops target the control-home; resolve it from $TIDE_HOME first so
    # `tide roster …` works from any cwd, not only inside the home tree.
    return paths.control_home()


def _cmd_add(args) -> int:
    env = getattr(args, "env", None) or None
    status = STATUS_ARCHIVED if getattr(args, "archive", False) else None
    add(_root(), args.name, args.path, env=env, status=status)
    bits = []
    if env:
        bits.append("env: {0}".format(env))
    if status:
        bits.append("archived")
    suffix = "  ({0})".format(", ".join(bits)) if bits else ""
    print("tide: rostered {0} → {1}{2}".format(args.name, args.path, suffix))
    return 0


def _cmd_rm(args) -> int:
    remove(_root(), args.name)
    print("tide: removed {0} from roster".format(args.name))
    return 0


def _cmd_ls(args) -> int:
    print(render_list(_root()))
    return 0


def _cmd_archive(args) -> int:
    set_status(_root(), args.name, STATUS_ARCHIVED)
    print("tide: archived {0}".format(args.name))
    return 0


def _cmd_restore(args) -> int:
    set_status(_root(), args.name, STATUS_ACTIVE)
    print("tide: restored {0} (active)".format(args.name))
    return 0


def register(subparsers) -> None:
    """Add the top-level ``roster`` command group to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser("roster", help="manage the control-home project roster")
    rsub = p.add_subparsers(dest="roster_cmd")

    ap = rsub.add_parser("add", help="register a project (name path [--env ENV] [--archive])")
    ap.add_argument("name")
    ap.add_argument("path")
    ap.add_argument(
        "--env",
        default=None,
        metavar="ENV",
        help="orca environment name for projects on a remote machine (omit for local)",
    )
    ap.add_argument(
        "--archive",
        action="store_true",
        help="register the project as archived (hidden from the picker by default)",
    )
    ap.set_defaults(func=_cmd_add, _cmd="roster add")

    rp = rsub.add_parser("rm", help="remove a project (name)")
    rp.add_argument("name")
    rp.set_defaults(func=_cmd_rm, _cmd="roster rm")

    lp = rsub.add_parser("ls", help="list registered projects")
    lp.set_defaults(func=_cmd_ls, _cmd="roster ls")

    arp = rsub.add_parser("archive", help="mark a registered project archived (name)")
    arp.add_argument("name")
    arp.set_defaults(func=_cmd_archive, _cmd="roster archive")

    rerp = rsub.add_parser("restore", help="mark an archived project active again (name)")
    rerp.add_argument("name")
    rerp.set_defaults(func=_cmd_restore, _cmd="roster restore")

"""tide.fields — first-line ``^key:`` frontmatter read/write.

The passport docs (``arc.md``, ``<slug>-goal.md``, ``contract.md`` …) carry
frontmatter as plain ``key: value`` lines. Parsing rule (ported from arcs,
language-agnostic — only KEYS are English): a field's value is everything after
the **first** line matching ``^key:`` , with leading whitespace after the colon
stripped.

Two invariants this module owns:

* ``prev:`` is a **read-only alias** of ``supersedes:`` — reading either key
  returns the supersedes value; writing always emits the canonical
  ``supersedes:`` (replacing a stale ``prev:`` line in place if present).
* Stored slug values for ``supersedes:`` have a surrounding ``__…__`` stripped
  (refs are bare slugs; the closed-marker is presentation only).

``set_field`` is **order-preserving**: an existing key is replaced where it
already sits; a new key is inserted at the end of the leading frontmatter block,
never reordering the others. All text functions are pure (return a new string);
file wrappers do the I/O.
"""

from __future__ import annotations

from pathlib import Path
from typing import FrozenSet, List, Optional

# Keys whose read/write is aliased: prev: is an inbound alias for supersedes:.
_SUPERSEDES_KEYS = ("supersedes", "prev")
# canon-rev is the new spelling; cannon-rev is the legacy alias (back-compat).
_CANON_REV_KEYS = ("canon-rev", "cannon-rev")
_CANONICAL = {
    "supersedes": "supersedes",
    "prev": "supersedes",
    # Writing cannon-rev (old spelling) always emits the canonical canon-rev.
    "cannon-rev": "canon-rev",
}
# Keys whose stored value is a bare slug → strip a surrounding __…__.
_SLUG_VALUE_KEYS = {"supersedes", "prev"}

# Whitelist of all frontmatter keys tide actually uses.  ``_line_key`` only
# returns a key when it is in this set, so body lines like ``TODO: fix`` or
# ``NOTE: …`` are never misparsed as frontmatter regardless of their shape.
# Add here when a new key is introduced; keep sorted for readability.
KNOWN_KEYS: FrozenSet[str] = frozenset(
    {
        # Supersedes alias (read-only; canonical write is always "supersedes").
        "prev",
        # Arc passport / goal passport fields.
        "canon-rev",    # new canonical spelling
        "cannon-rev",   # legacy spelling — kept for back-compat parsing
        "claude-session",  # pinned claude --session-id of a session (for --resume)
        "criteria",
        "deferred",
        "from",
        "goal",
        "kind",         # arc kind marker — "thread" tags a session-memory arc
        "merged",
        "mode",
        "offloaded-at",  # session offload marker — transcript size at last флот (offload)
        "orca-base-branch",
        "orca-issue",
        "orca-workspace",
        "project",
        "reality-rev",
        "sign",
        "slug",
        "state",
        "status",
        "supersedes",
        "title",        # session human title (set on handoff/offload)
        # Contract deliverable fields.
        "accepted",
        # Worktree adapter fields.
        "worktree-branch",
    }
)


def _match_keys(key: str) -> List[str]:
    """The set of line-prefixes a read/write for *key* should match."""
    if key in _SUPERSEDES_KEYS:
        return list(_SUPERSEDES_KEYS)
    if key in _CANON_REV_KEYS:
        return list(_CANON_REV_KEYS)
    return [key]


def _line_key(line: str) -> Optional[str]:
    """The frontmatter key of *line* (text before the first ``:``), or None.

    A key is returned only when the candidate token:
      * is a bare word with no whitespace,
      * is present in :data:`KNOWN_KEYS`,

    so body lines such as ``TODO: fix`` or ``NOTE: …`` are never misparsed as
    frontmatter even though they are syntactically key-like.
    """
    stripped = line.rstrip("\n")
    idx = stripped.find(":")
    if idx <= 0:
        return None
    head = stripped[:idx]
    # A frontmatter key is a bare token (no spaces).
    if head != head.strip() or " " in head or "\t" in head:
        return None
    # Restrict to the known-key whitelist — unknown keys in the body are prose.
    if head not in KNOWN_KEYS:
        return None
    return head


def _value_after(line: str, key: str) -> str:
    """Value portion of a ``key: value`` line (whitespace after colon stripped)."""
    rest = line.rstrip("\n")[len(key) + 1:]
    return rest.lstrip(" \t")


def _clean_value(key: str, value: str) -> str:
    """Apply per-key normalisation (strip ``__…__`` from slug-valued keys)."""
    if key in _SLUG_VALUE_KEYS:
        v = value
        if v.startswith("__"):
            v = v[2:]
        if v.endswith("__"):
            v = v[:-2]
        return v
    return value


def read_field_text(text: str, key: str) -> Optional[str]:
    """Return the first ``^key:`` value in *text*, or None (prev:↔supersedes:)."""
    wanted = _match_keys(key)
    for line in text.splitlines():
        lk = _line_key(line)
        if lk is not None and lk in wanted:
            return _clean_value(key, _value_after(line, lk))
    return None


def read_field(path: Path, key: str) -> Optional[str]:
    """File wrapper for :func:`read_field_text`; None if file missing/keyless."""
    p = Path(path)
    if not p.is_file():
        return None
    return read_field_text(p.read_text(encoding="utf-8"), key)


def _frontmatter_insert_index(lines: List[str]) -> int:
    """Index to insert a new frontmatter line at the END of the leading block.

    The leading block = the run of lines from the top that are a heading
    (``#``), blank, or a ``key:`` field, up to the first content/prose line.
    We return the index just after the last ``key:`` line seen in that run; if
    none, just after a leading heading; else 0.
    """
    last_field = -1
    last_heading = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if _line_key(line) is not None:
            last_field = i
            continue
        if stripped == "":
            continue
        if stripped.startswith("#"):
            last_heading = i
            continue
        # First real content line → stop scanning the leading block.
        break
    if last_field >= 0:
        return last_field + 1
    if last_heading >= 0:
        return last_heading + 1
    return 0


def set_field_text(text: str, key: str, value: str) -> str:
    """Return *text* with *key* set to *value*, order-preserving.

    Existing key (incl. a stale ``prev:`` when setting supersedes) is replaced
    in place; otherwise the canonical ``key: value`` is inserted at the end of
    the leading frontmatter block. supersedes values are ``__…__``-stripped.
    """
    write_key = _CANONICAL.get(key, key)
    value = _clean_value(write_key, value)
    new_line = "{0}: {1}".format(write_key, value)
    wanted = _match_keys(key)

    # Preserve a trailing newline if the original text had one.
    had_trailing_nl = text.endswith("\n")
    lines = text.split("\n")
    if had_trailing_nl and lines and lines[-1] == "":
        lines = lines[:-1]

    for i, line in enumerate(lines):
        lk = _line_key(line)
        if lk is not None and lk in wanted:
            lines[i] = new_line
            out = "\n".join(lines)
            return out + "\n" if had_trailing_nl else out

    idx = _frontmatter_insert_index(lines)
    lines.insert(idx, new_line)
    out = "\n".join(lines)
    return out + "\n" if had_trailing_nl else out


def set_field(path: Path, key: str, value: str) -> None:
    """File wrapper for :func:`set_field_text` (atomic read-modify-write, utf-8)."""
    from .io import atomic_write  # deferred to avoid import-cycle risks

    p = Path(path)
    original = p.read_text(encoding="utf-8") if p.is_file() else ""
    atomic_write(p, set_field_text(original, key, value))

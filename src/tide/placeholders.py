"""tide.placeholders — detect leftover scaffold placeholders in committed docs.

The seed templates (:mod:`tide.arc.templates`, :mod:`tide.contract.model`) carry
angle-bracket placeholder text (``<one line — what this arc closes>``), the
unfilled ``## IS → TO-BE`` / ``## where we are`` bodies, and the ``# supersedes:``
hint comment. When an arc/contract is CLOSED those should have been filled in or
removed — leftover placeholders leaked onto the STREAM board in every dogfood run
(decision / dogfood fix F5). This module is the single detector: a pure scan that
returns the offending snippets so the close paths can refuse-or-warn (``-f``
overrides), instead of sealing a passport that still reads like a fill-in form.

What counts as a placeholder:

* any ``<…>`` angle-bracket template span on a single line — covers the field
  placeholders (``goal:``/``criteria:``/``project:``), the ``## IS → TO-BE`` and
  ``## where we are`` bodies, and the goal H1's ``<goal>`` span;
* the bare ``# supersedes:`` hint comment — a real intent-pivot REPLACES it with
  a ``supersedes: <old>`` field (see :func:`tide.arc.stream._write_supersedes`),
  so a surviving hint means the scaffold was never finished.

All functions are pure (read-only); the close verbs in :mod:`tide.arc.stream` and
:mod:`tide.contract.lifecycle` consume them behind their ``force`` flag.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

# A single-line ``<…>`` scaffold span. The templates never emit a multi-line
# placeholder, so a newline ends the match — keeping real prose that merely uses a
# stray ``<`` from being swallowed. Nested ``<``/``>`` are excluded for the same
# reason (the class forbids them inside the span).
_ANGLE = re.compile(r"<[^<>\n]+>")

# The optional ``# supersedes:`` hint comment the templates seed (a ``# ``-led key
# is a comment, not a real field). A real supersede removes it.
_SUPERSEDES_HINT = re.compile(r"^\s*#\s*supersedes:")


def find_in_text(text: str) -> List[str]:
    """Return the leftover scaffold placeholders in *text*, in document order.

    Scans line by line: a ``# supersedes:`` hint line is reported once (as its
    stripped text) and its own angle-bracket span is folded into that one report
    so it is not double-counted; every other ``<…>`` span is reported as-is.
    Returns an empty list for a fully-filled doc.
    """
    offenders: List[str] = []
    for line in (text or "").splitlines():
        if _SUPERSEDES_HINT.match(line):
            offenders.append(line.strip())
            continue
        offenders.extend(m.group(0) for m in _ANGLE.finditer(line))
    return offenders


def find_in_file(path: Path) -> List[str]:
    """Placeholders in a doc on disk; ``[]`` when the file is absent (nothing to seal)."""
    p = Path(path)
    if not p.is_file():
        return []
    return find_in_text(p.read_text(encoding="utf-8"))


def refuse_message(doc_name: str, ref: str, offenders: List[str]) -> str:
    """Build the close-refusal message listing the unfilled placeholder fields.

    *doc_name* is the committed file (``arc.md`` / ``contract.md`` / the goal doc),
    *ref* the arc/goal being closed. The bullet list names each leftover span so
    the human knows exactly what to fill or strip (or override with ``-f``).
    """
    bullets = "\n".join("  - {0}".format(o) for o in offenders)
    return (
        "cannot close {ref!r}: {doc} still carries {n} unfilled scaffold "
        "placeholder(s) — fill them in or remove them first (override: close -f):\n"
        "{bullets}"
    ).format(ref=ref, doc=doc_name, n=len(offenders), bullets=bullets)

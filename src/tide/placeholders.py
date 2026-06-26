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

# A fenced-code-block delimiter line: ≥3 backticks or tildes, optionally indented,
# optionally followed by an info string. The opening fence is matched again to
# close (same char, length ≥ opening) per CommonMark.
_FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})")


def _mask_inline_code(line: str) -> str:
    """Blank out inline code-span content on a single line, preserving length.

    A code span is a run of *n* backticks, a body, then a closing run of exactly
    *n* backticks (CommonMark). Everything from the opening run through the closing
    run (delimiters included) is replaced with spaces so a ``<arg>`` example inside
    backticks is not seen by the placeholder scan. Unmatched backticks are left
    untouched, so a bare ``<…>`` after a stray backtick is still flagged. Offsets
    and length are preserved, so a caller may map masked-text matches back to the
    original by slice. Multiple spans per line are handled.
    """
    chars = list(line)
    n = len(chars)
    i = 0
    while i < n:
        if chars[i] != "`":
            i += 1
            continue
        # Measure the opening backtick run.
        j = i
        while j < n and chars[j] == "`":
            j += 1
        run_len = j - i
        # Scan for a closing run of exactly run_len backticks.
        k = j
        closed_at = -1
        while k < n:
            if chars[k] != "`":
                k += 1
                continue
            p = k
            while p < n and chars[p] == "`":
                p += 1
            if p - k == run_len:
                closed_at = p
                break
            k = p  # a differently-sized run is not a valid closer; skip past it
        if closed_at == -1:
            i = j  # no closing run — leave these backticks as-is, scan onward
            continue
        for x in range(i, closed_at):
            chars[x] = " "
        i = closed_at
    return "".join(chars)


def mask_code(text: str) -> str:
    """Return *text* with all code-region content blanked to spaces.

    Masks both fenced code blocks (``` / ~~~ fences) and inline code spans, so a
    ``<…>`` angle span that lives inside a code example is a legitimate example, not
    an unfilled placeholder, and the scanners below skip it. Per-line length is
    preserved (code chars become spaces); ``<…>`` in ordinary prose survives intact.
    """
    lines = (text or "").splitlines()
    out: List[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    fence_open_at = -1
    for idx, line in enumerate(lines):
        m = _FENCE.match(line)
        if in_fence:
            out.append(" " * len(line))
            if m and m.group(2)[0] == fence_char and len(m.group(2)) >= fence_len:
                in_fence = False
            continue
        if m:
            in_fence = True
            fence_char = m.group(2)[0]
            fence_len = len(m.group(2))
            fence_open_at = idx
            out.append(" " * len(line))
            continue
        out.append(_mask_inline_code(line))

    # An UNTERMINATED fence (opener with no closer before EOF) is a document error,
    # not a real code block — masking it would silently swallow every real
    # ``<placeholder>`` below the opener (completeness is the gate's whole job).
    # Restore the lines blanked since the opener to their originals so those
    # placeholders ARE flagged. A ``<…>`` inside the broken region getting flagged
    # is an acceptable false-positive: a never-closed fence must be fixed anyway.
    if in_fence:
        for idx in range(fence_open_at, len(lines)):
            out[idx] = lines[idx]

    return "\n".join(out)


def find_in_text(text: str) -> List[str]:
    """Return the leftover scaffold placeholders in *text*, in document order.

    Scans line by line: a ``# supersedes:`` hint line is reported once (as its
    stripped text) and its own angle-bracket span is folded into that one report
    so it is not double-counted; every other ``<…>`` span is reported as-is.
    ``<…>`` spans inside code (inline backticks or fenced blocks) are example text,
    not unfilled fields, so they are skipped (see :func:`mask_code`).
    Returns an empty list for a fully-filled doc.
    """
    src = text or ""
    masked = mask_code(src)
    offenders: List[str] = []
    for line, masked_line in zip(src.splitlines(), masked.splitlines()):
        if _SUPERSEDES_HINT.match(line):
            offenders.append(line.strip())
            continue
        # Match on the masked line (code blanked out) but report the original text,
        # which masking leaves byte-aligned for any prose placeholder.
        offenders.extend(line[m.start():m.end()] for m in _ANGLE.finditer(masked_line))
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

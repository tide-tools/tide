"""tide.cannon.merge — structural section-merge of an arc delta into CANON.md.

This is tide's **single serialization point**: workers only ever write their own
arc's ``delta.md``; the one place writes converge into the living truth is this
merge, which runs only in the live orchestrator session (and is CLI-gated to it).

Mechanic (fix F2 — structural section-merge, supersedes the old blind append):

* The delta is split into top-level ``## `` sections. Each **canonical** section
  (``## What it is`` / ``## State & components`` / ``## Interfaces / how used``)
  is routed into the SINGLE matching heading in CANON.md — filling it when empty,
  appending within the section otherwise. Canonical top headings are never
  duplicated and never left empty when the delta carries content for them.
* Everything else in the delta (preamble prose + non-canonical sections) plus a
  stamped ``### <date> · <slug>`` header is appended under the one append-only
  ``## Cannon journal``. Non-canonical delta headings are demoted so they nest
  under the stamp and never masquerade as top-level CANON sections.
* The journal is **append-only / chronological** — prior entries keep their order
  and the new entry lands last. ``## Cannon journal`` is always the final section.
* Duplicate top-level headings already present in CANON.md are de-duplicated
  (their bodies folded into the first occurrence) as a side effect of the merge.

After a file-level merge the source delta is marked merged (``merged: yes``) so
the sync engine won't double-merge it. Text helpers are pure; file wrappers do
the I/O and recompute the bumped cannon-rev.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .. import fields, paths
from . import rev, store

# Canonical section titles a delta is routed INTO (everything bar the journal).
CANONICAL_TITLES: List[str] = list(store.SECTIONS[:-1])
JOURNAL_TITLE: str = store.SECTIONS[-1]
JOURNAL_HEADER: str = "## {0}".format(JOURNAL_TITLE)


def _today() -> str:
    """Today's date as ``YYYY-MM-DD`` (injectable via callers passing *date*)."""
    return datetime.date.today().isoformat()


def has_journal(text: str) -> bool:
    """True when *text* contains a top-level ``## Cannon journal`` heading line."""
    target = JOURNAL_HEADER.strip()
    return any(line.strip() == target for line in text.splitlines())


# --- pure structural helpers ------------------------------------------------

def _split(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Split *text* on top-level ``## `` headers.

    Returns ``(preamble, [(title, body), ...])`` where *preamble* is everything
    before the first ``## `` (H1 + blanks) and each *body* is stripped of its
    surrounding blank lines. Deeper headings (``### ``…) stay inside their owner.
    """
    preamble: List[str] = []
    sections: List[List] = []
    current: Optional[List] = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = [line[3:].strip(), []]
            sections.append(current)
        elif current is None:
            preamble.append(line)
        else:
            current[1].append(line)
    pre = "\n".join(preamble).strip("\n")
    secs = [(title, "\n".join(body).strip("\n")) for title, body in sections]
    return pre, secs


def _join_bodies(a: str, b: str) -> str:
    """Join two section bodies with a single blank-line separator (skip empties)."""
    a = a.strip("\n")
    b = b.strip("\n")
    if not a:
        return b
    if not b:
        return a
    return "{0}\n\n{1}".format(a, b)


def _load(text: str) -> Tuple[str, List[List[str]], str]:
    """Parse CANON *text* into ``(preamble, sections, journal_body)``.

    *sections* is an ordered ``[[title, body], ...]`` list with the journal
    pulled out and any duplicate top headings folded into their first occurrence
    (their bodies concatenated). *journal_body* is the merged journal text.
    """
    pre, secs = _split(text)
    sections: List[List[str]] = []
    index: Dict[str, int] = {}
    journal_body = ""
    for title, body in secs:
        if title == JOURNAL_TITLE:
            journal_body = _join_bodies(journal_body, body)
            continue
        if title in index:
            i = index[title]
            sections[i][1] = _join_bodies(sections[i][1], body)
        else:
            index[title] = len(sections)
            sections.append([title, body])
    return pre, sections, journal_body


def _canonical_insert_index(sections: List[List[str]]) -> int:
    """Index just after the last canonical section, keeping the canon block tight."""
    insert_at = 0
    for i, (title, _body) in enumerate(sections):
        if title in CANONICAL_TITLES:
            insert_at = i + 1
    return insert_at


def _route(sections: List[List[str]], title: str, content: str) -> None:
    """Route *content* into the single *title* section: fill if empty, else append.

    Creates the section (keeping the canonical block contiguous) when it is
    missing. Idempotent: content already present in the section is not re-added.
    """
    content = content.strip("\n")
    for entry in sections:
        if entry[0] == title:
            cur = entry[1]
            if not cur.strip():
                entry[1] = content
            elif content and content not in cur:
                entry[1] = _join_bodies(cur, content)
            return
    sections.insert(_canonical_insert_index(sections), [title, content])


def _delta_remainder(d_pre: str, d_secs: List[Tuple[str, str]]) -> str:
    """Journal body for a delta: preamble + non-canonical sections (headers demoted).

    Canonical sections are routed to the top of CANON, so they are excluded here.
    Remaining ``## `` headers are demoted to ``#### `` so they nest under the
    ``### <date> · <slug>`` stamp instead of forming phantom top-level sections.
    """
    parts: List[str] = []
    if d_pre.strip():
        parts.append(d_pre.strip("\n"))
    for title, body in d_secs:
        if title in CANONICAL_TITLES:
            continue
        block = "#### {0}".format(title)
        if body.strip():
            block = "{0}\n\n{1}".format(block, body.strip("\n"))
        parts.append(block)
    return "\n\n".join(parts).strip("\n")


def _entry_block(date: str, slug: str, body: str) -> str:
    """Render one journal entry: ``### <date> · <slug>`` then the (optional) body."""
    stamp = "### {0} · {1}".format(date, slug)
    body = body.strip("\n")
    if body:
        return "{0}\n\n{1}".format(stamp, body)
    return stamp


def _journal_has_stamp(journal_body: str, date: str, slug: str) -> bool:
    """True when *journal_body* already contains the exact stamp as a line.

    Compared line-by-line (stripped) to avoid false positives from stamps that
    share a common prefix (e.g. ``tide-terminal`` vs ``tide-terminal-2``).
    """
    target = "### {0} · {1}".format(date, slug)
    return any(line.strip() == target for line in journal_body.splitlines())


def _dedup_journal_body(journal_body: str) -> str:
    """Remove duplicate ``### <date> · <slug>`` entries from *journal_body*.

    First occurrence of each stamp is kept in place; all subsequent occurrences
    (including their bodies) are dropped. Non-stamp lines before the first stamp
    are always preserved. Idempotent.
    """
    lines = journal_body.splitlines()
    seen: Set[str] = set()
    out: List[str] = []
    skip_current = False

    for line in lines:
        if line.startswith("### "):
            stamp = line.strip()
            if stamp in seen:
                skip_current = True
            else:
                seen.add(stamp)
                skip_current = False
                out.append(line)
        elif not skip_current:
            out.append(line)

    return "\n".join(out).strip("\n")


def _render(pre: str, sections: List[List[str]], journal_body: str) -> str:
    """Re-emit CANON.md: preamble, sections in order, journal always last."""
    parts: List[str] = []
    if pre.strip():
        parts.append(pre.strip("\n"))
    for title, body in sections:
        block = "## {0}".format(title)
        if body.strip():
            block = "{0}\n\n{1}".format(block, body.strip("\n"))
        parts.append(block)
    journal = JOURNAL_HEADER
    if journal_body.strip():
        journal = "{0}\n\n{1}".format(journal, journal_body.strip("\n"))
    parts.append(journal)
    return "\n\n".join(parts).rstrip("\n") + "\n"


def merge_delta_text(canon_text: str, delta_body: str, *, date: str, slug: str) -> str:
    """Structurally merge *delta_body* into *canon_text* and return the new CANON.

    Canonical delta sections are routed into their single matching CANON heading
    (fill/append-within, never duplicated, never left empty when the delta has
    content); the remainder + a stamped ``### <date> · <slug>`` entry is appended
    under the append-only ``## Cannon journal`` (always the last section).
    """
    pre, sections, journal_body = _load(canon_text)
    d_pre, d_secs = _split(delta_body)

    # Fold any within-delta duplicate sections so each title routes once.
    d_map: Dict[str, str] = {}
    for title, body in d_secs:
        d_map[title] = _join_bodies(d_map.get(title, ""), body)

    for title in CANONICAL_TITLES:
        content = d_map.get(title, "").strip()
        if content:
            _route(sections, title, content)

    remainder = _delta_remainder(d_pre, d_secs)
    if not _journal_has_stamp(journal_body, date, slug):
        entry = _entry_block(date, slug, remainder)
        journal_body = _join_bodies(journal_body, entry)

    return _render(pre, sections, journal_body)


def normalize_canon_text(canon_text: str) -> str:
    """Normalize *canon_text* in-place: fold duplicate headings and dedup journal.

    Idempotent heal pass that repairs two classes of rot:

    * **Duplicate top-level ``## `` headings** — bodies folded into the first
      occurrence (a side-effect of the existing ``_load`` logic).
    * **Duplicate ``### <date> · <slug>`` journal stamps** — first occurrence
      kept, all repeats (including their bodies) dropped.
    * **``## `` headings buried inside the journal** by old blind-append code —
      extracted and routed back to their matching canonical top section or kept
      as non-canonical sections above the journal.

    Safe to call on any well-formed CANON.md; a clean file is returned unchanged.
    """
    pre, sections, journal_body = _load(canon_text)
    journal_body = _dedup_journal_body(journal_body)
    return _render(pre, sections, journal_body)


def mark_merged(delta_path: Path, date: Optional[str] = None) -> None:
    """Stamp ``merged: yes`` into a delta file so it is not merged twice."""
    fields.set_field(Path(delta_path), "merged", "yes")


def merge_delta(
    root: Path,
    arc_dir: Path,
    *,
    slug: str,
    date: Optional[str] = None,
    delta_name: str = "delta.md",
) -> str:
    """Merge ``<arc_dir>/<delta_name>`` into the project's CANON.md.

    Reads the arc's delta body, routes its canonical sections into CANON and
    appends the chronological journal entry, marks the delta merged, and returns
    the bumped cannon-rev (recomputed over the new CANON.md). Raises if the delta
    file is missing.
    """
    date = date or _today()
    delta_path = Path(arc_dir) / delta_name
    if not delta_path.is_file():
        raise FileNotFoundError("no delta to merge at {0}".format(delta_path))

    delta_body = _delta_body(delta_path.read_text(encoding="utf-8"))

    canon = paths.canon_file(root)
    canon_text = canon.read_text(encoding="utf-8") if canon.is_file() else ""
    merged = merge_delta_text(canon_text, delta_body, date=date, slug=slug)
    canon.parent.mkdir(parents=True, exist_ok=True)
    canon.write_text(merged, encoding="utf-8")

    mark_merged(delta_path, date=date)
    return rev.compute(root)


def _delta_body(text: str) -> str:
    """Extract the merge-worthy body of a delta.md (drop frontmatter + H1).

    A delta file may carry a ``# delta — <slug>`` H1 and ``key:`` frontmatter
    (e.g. ``merged:``); only the prose/sections below belong in the merge. Note:
    only the level-1 ``# `` heading is stripped — ``## `` section headers are
    real content and are preserved so the structural merge can route them.
    """
    lines = text.splitlines()
    out: List[str] = []
    in_body = False
    for line in lines:
        if not in_body:
            stripped = line.strip()
            if stripped == "":
                continue
            if stripped.startswith("# ") or stripped == "#":
                continue
            if fields._line_key(line) is not None:
                continue
            in_body = True
        out.append(line)
    return "\n".join(out).strip("\n")

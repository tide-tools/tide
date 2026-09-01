"""tide.canon.store — the canon/ home: init, read, scan.

``canon/`` is a project's durable truth. Its centrepiece is ``CANON.md`` — the
living-IS doc — plus a one-line ``config``. This module owns their on-disk shape
(ported from canon ``init``, English-only headings for language-agnostic
parsing):

    # CANON.md — <name>
    ## What it is
    ## State & components
    ## Interfaces / how used
    ## Canon journal        ← append-only merge log (merge.py writes here)

The journal is the section :mod:`tide.canon.merge` appends arc deltas under, so
``init`` always seeds it (an empty journal is still a valid anchor). Folded
notes/lore/changelog/goals subsections may follow later; ``init`` keeps the
minimal four-section skeleton.

All functions are pure where possible (text helpers) with thin file wrappers; a
``register``-style CLI handler lives in :mod:`tide.canon.commands`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from .. import io as _io, paths

DEFAULT_LANG = "en"

# The merge anchor — intentionally empty in a fresh canon (deltas land here),
# so honesty checks (doctor's empty-skeleton warn) must exempt it.
JOURNAL_SECTION = "Canon journal"

# The section a newborn project's intent is seeded into (``tide adopt --goal``):
# "what this project is" is exactly the question a cold agent opens with.
INTENT_SECTION = "What it is"

# Canonical H2 section titles, in order. Kept in sync with the conftest skeleton
# template so a hand-built fixture and a real ``canon init`` agree byte-for-byte.
SECTIONS: List[str] = [
    INTENT_SECTION,
    "State & components",
    "Interfaces / how used",
    JOURNAL_SECTION,
]


def seed_line(intent: str) -> str:
    """Normalise a free-text *intent* into one canon-safe body line (may be empty).

    Whitespace (newlines included) collapses to single spaces so the seed can never
    inject blank-line breaks, and leading ``#`` is stripped so a goal phrased as a
    heading cannot open a bogus H2 that :func:`scan_text` would parse as a section.
    """
    return " ".join(intent.split()).lstrip("#").strip()


def canon_template(name: str, intent: str = "") -> str:
    """Return the seed ``CANON.md`` text for a project called *name*.

    Header ``# CANON.md — <name>`` then the four canonical H2 sections, each
    separated by a blank line. The trailing ``## Canon journal`` is the merge
    anchor and is intentionally left empty.

    *intent* — an optional one-line seed (``tide adopt --goal``) written under
    :data:`INTENT_SECTION`, so a newborn project says what it is instead of
    handing the first agent four blank headings. Empty *intent* reproduces the
    bare skeleton byte-for-byte (the conftest fixture depends on that).
    """
    seed = seed_line(intent)
    body = ["# CANON.md — {0}".format(name), ""]
    for title in SECTIONS:
        body.append("## {0}".format(title))
        body.append("")
        if seed and title == INTENT_SECTION:
            body.append(seed)
            body.append("")
    # body currently ends with a trailing "" after the last section → one \n.
    return "\n".join(body)


def config_text(lang: str = DEFAULT_LANG) -> str:
    """Return the ``canon/config`` text (single ``lang=`` line, newline-terminated)."""
    return "lang={0}\n".format(lang)


def init(
    root: Path,
    name: Optional[str] = None,
    lang: str = DEFAULT_LANG,
    force: bool = False,
    intent: str = "",
) -> Path:
    """Seed ``<root>/.tide/canon/`` with ``CANON.md`` + ``config``.

    *name* defaults to the project dir name. *intent* seeds the "What it is"
    section (see :func:`canon_template`); it only applies to a canon being
    written, never to one being preserved. Existing files are preserved unless
    *force* is set (so re-running ``canon init`` never clobbers a real CANON).
    Returns the ``canon/`` directory path.

    On the first write, if a legacy ``.tide/cannon/`` exists and ``.tide/canon/``
    does not, the legacy dir is atomically renamed to ``.tide/canon/`` so existing
    instances are migrated in place.
    """
    root = Path(root)
    # Migrate legacy .tide/cannon/ → .tide/canon/ before creating/writing.
    paths.migrate_canon_dir(root)
    canon_directory = paths.tide_dir(root) / paths.CANON_DIRNAME
    canon_directory.mkdir(parents=True, exist_ok=True)

    project_name = name if name else root.resolve().name

    canon = paths.canon_file(root)
    if force or not canon.exists():
        _io.atomic_write(canon, canon_template(project_name, intent=intent))

    cfg = paths.canon_config(root)
    if force or not cfg.exists():
        _io.atomic_write(cfg, config_text(lang))

    return canon_directory


def is_empty_skeleton(text: str) -> bool:
    """True when CANON.md *text* is a fresh ``init`` skeleton: headings, no content.

    A newborn project (``tide adopt``) gets exactly this — the four H2 headings with
    blank bodies. Nothing has been said yet, so surfaces that reproach a project for
    lagging behind its canon (README drift) must hold their tongue over it.
    ``Canon journal`` is the merge anchor and is INTENTIONALLY empty — exempt, as in
    :func:`tide.doctor.check_canon`, which shares this predicate.
    """
    content = {t: b for t, b in scan_text(text).items() if t != JOURNAL_SECTION}
    return bool(content) and all(not body.strip() for body in content.values())


def read(root: Path) -> str:
    """Return the raw ``CANON.md`` text for *root* (raises if it is missing)."""
    canon = paths.canon_file(root)
    if not canon.is_file():
        raise FileNotFoundError(
            "no canon at {0} (run 'tide canon init')".format(canon)
        )
    return canon.read_text(encoding="utf-8")


def scan_text(text: str) -> Dict[str, str]:
    """Split CANON.md *text* into ``{H2 title: body}`` (order not guaranteed).

    A section runs from one ``## `` heading to the next; the H1 preamble and any
    deeper headings stay inside whatever H2 owns them. Bodies keep their inner
    formatting but are stripped of leading/trailing blank lines.
    """
    sections: Dict[str, str] = {}
    current: Optional[str] = None
    buf: List[str] = []

    def _flush() -> None:
        if current is not None:
            sections[current] = "\n".join(buf).strip("\n")

    for line in text.splitlines():
        if line.startswith("## "):
            _flush()
            current = line[3:].strip()
            buf = []
        elif current is not None:
            buf.append(line)
    _flush()
    return sections


def scan(root: Path) -> Dict[str, str]:
    """File wrapper for :func:`scan_text` over a project's ``CANON.md``."""
    return scan_text(read(root))

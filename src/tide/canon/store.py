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

# Canonical H2 section titles, in order. Kept in sync with the conftest skeleton
# template so a hand-built fixture and a real ``canon init`` agree byte-for-byte.
SECTIONS: List[str] = [
    "What it is",
    "State & components",
    "Interfaces / how used",
    "Canon journal",
]


def canon_template(name: str) -> str:
    """Return the seed ``CANON.md`` text for a project called *name*.

    Header ``# CANON.md — <name>`` then the four canonical H2 sections, each
    separated by a blank line. The trailing ``## Canon journal`` is the merge
    anchor and is intentionally left empty.
    """
    body = ["# CANON.md — {0}".format(name), ""]
    for title in SECTIONS:
        body.append("## {0}".format(title))
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
) -> Path:
    """Seed ``<root>/.tide/canon/`` with ``CANON.md`` + ``config``.

    *name* defaults to the project dir name. Existing files are preserved unless
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
        _io.atomic_write(canon, canon_template(project_name))

    cfg = paths.canon_config(root)
    if force or not cfg.exists():
        _io.atomic_write(cfg, config_text(lang))

    return canon_directory


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

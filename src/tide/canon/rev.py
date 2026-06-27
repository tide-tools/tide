"""tide.canon.rev — canon-rev = short sha256 of ``CANON.md`` ONLY.

The canon-rev is tide's drift anchor: arc open stamps it into ``arc.md``; every
canon merge bumps it; a later arc whose stamped rev differs from the current one
has drifted (decision 9). It is a **tide invention** — the canon bash had none.

Scope decision (build-blueprint resolved-risk #2): hash **CANON.md only**, the
truth — not the whole ``canon/`` dir. So tweaks to ``config`` / folded
notes / changelog never spam drift; only the living-IS doc moving the rev.

The hash is a deterministic, stable function of the file's bytes: identical
content ⇒ identical rev regardless of when/where computed; any byte change ⇒ a
different rev. A missing CANON.md hashes as empty (stable, never raises) so the
sync engine can stamp before a project's canon is fully seeded.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .. import paths

# Short prefix of the hex digest. 12 hex chars = 48 bits — ample for a per-project
# drift anchor (we compare for equality, not collision-resistance at scale).
REV_LEN = 12


def compute_text(text: str) -> str:
    """Return the short sha256 rev of *text* (deterministic, stable)."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return digest[:REV_LEN]


def compute(root: Path) -> str:
    """Return the canon-rev for project *root* (hash of CANON.md, '' → empty hash).

    Reads bytes and decodes utf-8 so the rev is content-defined (not affected by
    path or mtime). A missing CANON.md is treated as empty content.
    """
    canon = paths.canon_file(root)
    text = canon.read_text(encoding="utf-8") if canon.is_file() else ""
    return compute_text(text)

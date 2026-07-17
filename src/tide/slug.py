"""tide.slug — slugify + ``__…__``-tolerant reference matching.

Ported from the arcs bash ``slugify`` (load-bearing — get it subtly wrong and
dir names diverge between create and lookup):

    lowercase → transliterate Cyrillic → spaces / ``/`` / ``_`` to ``-`` →
    drop non ``[a-z0-9-]`` → collapse repeated ``-`` → trim leading/trailing ``-``.

The Cyrillic step (cand 127) keeps a Russian-named thread from slugifying to the
empty string: «чай» → ``chay`` instead of ``''`` (which threw *empty slug after
slugify* at birth, so the thread could not start). Both create AND lookup route
through :func:`slugify`, so adding the step here keeps them in lock-step.

References (the ``<old>`` in ``arc supersede``, a slug typed by the agent) may
arrive wrapped in the closed-marker ``__…__``; the matcher strips that before
comparing. Entry dir names carry a ``NN-`` prefix, an optional goal ``@`` mark,
and the closed ``__…__`` wrapper — :func:`entry_slug` peels all of that off so a
bare slug can be matched against on-disk entries.
"""

from __future__ import annotations

import re

_NON_SLUG = re.compile(r"[^a-z0-9-]")
_DASHES = re.compile(r"-+")
# NN- prefix (2+ digits past 99), optional goal '@' marker.
_ENTRY = re.compile(r"^(?P<num>\d{2,})-(?P<goal>@)?(?P<slug>.*)$")

# Cyrillic → Latin, matched to the transliteration already visible in on-disk
# slugs (миграция → migraciya, переходы → perehody: ц→c, х→h, я→ya, ы→y, ё→e).
# Keys are LOWERCASE — :func:`slugify` lowercases before transliterating, so
# uppercase Cyrillic is folded first. Ukrainian extras (і/ї/є/ґ) are cheap.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g",
}


def _translit(text: str) -> str:
    """Best-effort Cyrillic → Latin so a Russian name yields a real handle.

    Non-Cyrillic chars pass through untouched (accented Latin like ``é`` is left
    for the later ``[^a-z0-9-]`` strip, unchanged). Multi-char outputs (``ch``,
    ``shch``, ``ya``) are why this is a join, not ``str.translate``.
    """
    return "".join(_TRANSLIT.get(ch, ch) for ch in text)


def slugify(text: str) -> str:
    """Turn arbitrary text into a glob-safe kebab slug (arcs-compatible)."""
    s = (text or "").lower()
    s = _translit(s)
    s = s.replace(" ", "-").replace("/", "-").replace("_", "-")
    s = _NON_SLUG.sub("", s)
    s = _DASHES.sub("-", s)
    return s.strip("-")


# Candidate slugs are only a handle — cap them so a pasted idea doesn't become a
# 200-char directory name. The full idea lives in the file body (fix F6).
SLUG_MAX_LEN = 48


def short_slug(text: str, max_len: int = SLUG_MAX_LEN) -> str:
    """:func:`slugify` capped to a short kebab handle, trimmed to a word boundary.

    Returns the plain slug when already within *max_len*; otherwise clips on a
    ``-`` boundary so a word isn't cut mid-token, falling back to a hard cut when
    the first word alone exceeds *max_len*. Used for candidate filenames — the
    full pasted text is preserved in the candidate body, not the slug.
    """
    s = slugify(text)
    if len(s) <= max_len:
        return s
    clipped = s[:max_len].rsplit("-", 1)[0].strip("-")
    return clipped or s[:max_len].strip("-")


def strip_marker(ref: str) -> str:
    """Remove a surrounding closed-marker ``__…__`` from a ref (one layer)."""
    r = ref or ""
    if r.startswith("__"):
        r = r[2:]
    if r.endswith("__"):
        r = r[:-2]
    return r


def normalize_ref(ref: str) -> str:
    """Canonicalise a user ref: strip ``__…__`` then slugify."""
    return slugify(strip_marker(ref))


def entry_slug(name: str) -> str:
    """Bare slug of an on-disk entry name.

    Handles open ``NN-slug`` / ``NN-@slug`` and closed ``__NN-slug__`` /
    ``__NN-@slug__``; returns just the ``slug`` part. A name without an
    ``NN-`` prefix falls back to a marker-stripped slugify.
    """
    bare = strip_marker((name or "").rstrip("/"))
    m = _ENTRY.match(bare)
    if m:
        return m.group("slug")
    return slugify(bare)


def is_goal_entry(name: str) -> bool:
    """True when an entry dir name marks a goal (``NN-@slug`` / ``__NN-@slug__``)."""
    bare = strip_marker((name or "").rstrip("/"))
    m = _ENTRY.match(bare)
    return bool(m and m.group("goal"))


def is_closed_entry(name: str) -> bool:
    """True when an entry dir name is wrapped in the closed marker ``__…__``."""
    n = (name or "").rstrip("/")
    return n.startswith("__") and n.endswith("__")


def is_entry(name: str) -> bool:
    """True when a dir name is a stream entry (``NN-…``), open OR closed.

    Marker-tolerant: matches ``NN-slug`` / ``NN-@slug`` / ``__NN-slug__`` /
    ``__NN-@slug__``. Lets a scan tell a real arc/goal dir from incidental dirs
    (``candidates/`` etc.) without caring whether it is open or closed.
    """
    bare = strip_marker((name or "").rstrip("/"))
    return bool(_ENTRY.match(bare))


def ref_matches(ref: str, entry_name: str) -> bool:
    """True when bare *ref* names *entry_name* (both ``__…__``-tolerant)."""
    return normalize_ref(ref) == entry_slug(entry_name)

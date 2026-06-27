"""tide.contract.ask — durable per-arc open-questions (``asks/NN-slug.md``).

Ported from the canon ``ask``/``answer`` mechanic, with the autonomy plumbing
**dropped** (architect ``open-question ask/answer``: no ``--escalate``, no pulse
DB, no Telegram). In tide a worker surfaces a question to the orchestrator/human
in the live synchronous session; the file is just the *durable record* so the
question (and its answer) survive across sessions.

Format (architect ``file_formats``):

    # NN-slug
    from: <ref>
    state: open|answered

    ## question
    <the question>

    ## answer
    <the answer — filled by `answer`>

Numbering is the candidates-style SEPARATE per-arc sequence
(:func:`tide.numbering.next_num_file` over the arc's ``asks/`` dir), so dropping
an ask never touches the work-stream or candidate counters.

All functions are plain (argparse-free, unit-testable); the CLI handlers live in
:mod:`tide.contract.lifecycle`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from .. import fields, io as _io, numbering, slug
from . import model

OPEN = "open"
ANSWERED = "answered"

# An ask file: NN-<slug>.md (2+ digit number).
_ASK_RE = re.compile(r"^(\d{2,})-(.+)\.md$")


# --- template --------------------------------------------------------------

def ask_md(name: str, from_ref: Optional[str], question: Optional[str]) -> str:
    """Seed text for an ``asks/NN-slug.md`` durable open-question.

    *name* is the file stem (``NN-<slug>``) used as the H1; ``from:`` records the
    origin ref (``-`` when none); ``state:`` starts ``open``.
    """
    origin = (from_ref or "").strip() or "-"
    q = (question or "").strip() or "<the open question>"
    return (
        "# {name}\n"
        "from: {origin}\n"
        "state: {state}\n"
        "\n"
        "## question\n"
        "{question}\n"
        "\n"
        "## answer\n"
        "\n"
    ).format(name=name, origin=origin, state=OPEN, question=q)


# --- ask -------------------------------------------------------------------

def ask(
    root: Path,
    arc_ref: str,
    raw_slug: str,
    *,
    question: Optional[str] = None,
    from_ref: Optional[str] = None,
    goal_slug: Optional[str] = None,
) -> Path:
    """Drop a durable open-question into the arc's ``asks/`` (own NN sequence).

    Resolves the arc, ensures ``asks/`` exists, numbers the file on the arc's own
    ask counter, and writes it ``state: open``. Returns the new file path. Does
    NOT require a contract — an ask can precede the contract (but the arc must exist).
    """
    arc_dir = model.resolve_arc_dir(root, arc_ref, goal_slug=goal_slug)
    s = slug.slugify(raw_slug)
    if not s:
        raise model.ContractError("ask: empty slug after slugify")
    adir = model.asks_dir(arc_dir)
    adir.mkdir(parents=True, exist_ok=True)
    nn = numbering.next_num_file(adir)
    name = "{0}-{1}".format(nn, s)
    path = adir / "{0}.md".format(name)
    _io.atomic_write(path, ask_md(name, from_ref, question))
    return path


# --- answer ----------------------------------------------------------------

def _parse(name: str):
    """Return ``(num, slug)`` for an ask filename, or None if it isn't one."""
    m = _ASK_RE.match(name)
    if not m:
        return None
    return m.group(1), m.group(2)


def _resolve(adir: Path, key: str) -> Optional[Path]:
    """Resolve an ask file by *key* — matches ``NN``, ``NN-slug`` or ``slug``."""
    raw = (key or "").strip()
    stem = raw[:-3] if raw.lower().endswith(".md") else raw
    bare = stem.lstrip("-")
    want_slug = slug.slugify(bare)
    for p in sorted(adir.glob("*.md")):
        parsed = _parse(p.name)
        if not parsed:
            continue
        num, aslug = parsed
        full = "{0}-{1}".format(num, aslug)
        if bare.isdigit() and int(bare) == int(num):
            return p
        if stem == full:
            return p
        if want_slug and want_slug == aslug:
            return p
    return None


def _set_answer_section(text: str, answer_text: str) -> str:
    """Replace the body under the ``## answer`` heading with *answer_text*.

    Keeps everything up to and including the ``## answer`` line, drops any prior
    answer body, and writes the new answer. Append-safe: a missing ``## answer``
    heading is created at end-of-file.
    """
    lines = text.splitlines()
    out: List[str] = []
    i = 0
    found = False
    while i < len(lines):
        line = lines[i]
        out.append(line)
        if line.strip() == "## answer":
            found = True
            i += 1
            break
        i += 1
    if not found:
        # No answer heading → append one.
        if out and out[-1].strip() != "":
            out.append("")
        out.append("## answer")
    out.append(answer_text.strip())
    return "\n".join(out).rstrip("\n") + "\n"


def answer(
    root: Path,
    arc_ref: str,
    key: str,
    *,
    answer: Optional[str] = None,
    goal_slug: Optional[str] = None,
) -> Path:
    """Answer an open ask: fill ``## answer`` and flip ``state: open → answered``.

    Resolves the ask by *key* (NN / NN-slug / slug) in the arc's ``asks/``. Returns
    the answered file path. Raises when no ask matches.
    """
    arc_dir = model.resolve_arc_dir(root, arc_ref, goal_slug=goal_slug)
    adir = model.asks_dir(arc_dir)
    path = _resolve(adir, key)
    if path is None:
        raise model.ContractError(
            "answer: no ask matching {0!r} in {1}".format(key, adir)
        )
    body = (answer or "").strip() or "<answer>"
    text = path.read_text(encoding="utf-8")
    text = _set_answer_section(text, body)
    _io.atomic_write(path, text)
    fields.set_field(path, "state", ANSWERED)
    return path


# --- list ------------------------------------------------------------------

def list_asks(root: Path, arc_ref: str, *, goal_slug: Optional[str] = None) -> List[Dict[str, object]]:
    """Return the arc's asks as dicts (``name/stem/num/slug/from/state``), NN order."""
    arc_dir = model.resolve_arc_dir(root, arc_ref, goal_slug=goal_slug)
    adir = model.asks_dir(arc_dir)
    out: List[Dict[str, object]] = []
    if not adir.is_dir():
        return out
    for p in sorted(adir.glob("*.md")):
        parsed = _parse(p.name)
        if not parsed:
            continue
        num, aslug = parsed
        out.append(
            {
                "path": p,
                "name": p.name,
                "stem": p.stem,
                "num": num,
                "slug": aslug,
                "from": fields.read_field(p, "from"),
                "state": fields.read_field(p, "state"),
            }
        )
    return out

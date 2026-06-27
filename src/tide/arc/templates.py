"""tide.arc.templates — English-only seed docs for the work stream.

Ported from the arcs ``arc_md`` / ``goal_md`` templates, collapsed to a single
English variant (localization dropped, decision in build-blueprint). Three seeds:

* :func:`arc_md` — the ``arc.md`` passport for a standalone arc.
* :func:`goal_md` — the immutable ``<slug>-goal.md`` for a goal (arc-with-purpose).
* :func:`from_seed` — the ``input/from-<old>.md`` pointer a supersede writes back.

Field KEYS stay English (``goal:``/``status:``/``supersedes:``/``canon-rev:``)
so parsing is language-agnostic. The ``# supersedes:`` placeholder is a *comment*
line (a key with a leading ``# `` is not a real field); ``arc supersede`` removes
it and writes the real ``supersedes:`` after ``status:``. The ``canon-rev:``
stamp is added on open by :mod:`tide.arc.stream`, not baked into the template.
"""

from __future__ import annotations


def arc_md(name: str) -> str:
    """Seed text for a standalone arc's ``arc.md`` passport.

    *name* is the entry dir name (``NN-<slug>``) used as the H1 heading.
    """
    return (
        "# {name}\n"
        "\n"
        "goal: <one line — what this arc closes>\n"
        "status: active\n"
        "# supersedes: <slug of the arc this one replaces — optional; alias: prev:>\n"
        "\n"
        "## input\n"
        "<input/… — one-line gist of what came in>\n"
        "\n"
        "## output → pointers\n"
        "- <output/... — what goes outward>\n"
    ).format(name=name)


def goal_md(slug: str) -> str:
    """Seed text for a goal's immutable ``<slug>-goal.md`` doc.

    The goal's items ARE its sub-arcs on disk; progress N/M is computed from the
    closed (``__…__``) sub-arc count, never hand-ticked — so there is no checklist
    block. ``## Where we are`` is the only mutable narrative.
    """
    return (
        "# {slug}-goal — <goal>\n"
        "\n"
        "goal: <goal — one line, ≤12 words; the long version goes in \"Where we are\">\n"
        "status: active\n"
        "# supersedes: <slug of the goal whose intent this one pivots from — optional>\n"
        "\n"
        "# The goal's items = its sub-arcs (NN-slug in arcs/). Progress N/M = closed (__) / total.\n"
        "\n"
        "## Where we are\n"
        "<current bottleneck>\n"
    ).format(slug=slug)


def from_seed(old: str, kind: str = "arc") -> str:
    """Seed text for ``input/from-<old>.md`` written by a supersede.

    *kind* is ``"arc"`` or ``"goal"`` — names what the new entry is in the
    one-line pointer back to the closed predecessor.
    """
    return (
        "# from {old}\n"
        "\n"
        "This {kind} supersedes {old} (closed). See __{old}__/ for the predecessor.\n"
    ).format(old=old, kind=kind)

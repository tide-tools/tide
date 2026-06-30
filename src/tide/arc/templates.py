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


def thread_goal_md(slug: str) -> str:
    """Seed text for a thread's goal doc — a ``kind: thread`` container of sessions.

    A *thread* (тред) is a durable work-line. Like a goal it holds its items —
    here **sessions** — as sub-arcs in the nested ``arcs/``; ``kind: thread`` marks
    it so the picker offers threads (not work-goals). ``## where we are`` is the
    only mutable narrative; the live resume point lives on each session's cursor.
    """
    return (
        "# {slug}-thread — <тред: the work-line this thread carries>\n"
        "\n"
        "goal: <one line — what this thread is about; ≤12 words>\n"
        "status: active\n"
        "kind: thread\n"
        "\n"
        "# The thread's items = its sessions (NN-slug in arcs/), chained by from:.\n"
        "\n"
        "## where we are\n"
        "<the live state of this thread across its sessions>\n"
    ).format(slug=slug)


def routine_md(slug: str) -> str:
    """Seed text for a routine's goal doc — a ``kind: routine`` reusable procedure.

    A *routine* (рутина) is work you did once and now re-run, with its own
    accumulated internal experience. Like a thread it is goal-shaped and holds its
    items — here **runs** — as sub-arcs in the nested ``arcs/``; ``kind: routine``
    marks it so the picker offers routines (not threads/work-goals). ``## steps`` is
    the runbook (the reproducible procedure); ``## experience`` accrues lessons
    across runs so the routine gets smarter each time it is run.
    """
    return (
        "# {slug}-routine — <рутина: the reusable procedure this routine carries>\n"
        "\n"
        "goal: <one line — what this routine does each run; ≤12 words>\n"
        "status: active\n"
        "kind: routine\n"
        "\n"
        "# The routine's items = its runs (NN-slug in arcs/), chained by from:.\n"
        "# A run is one execution; re-run the steps below, then append what you\n"
        "# learned to ## experience.\n"
        "\n"
        "## steps\n"
        "<the runbook — the reproducible procedure to follow each run>\n"
        "\n"
        "## experience\n"
        "<lessons that accrue across runs — append what each run taught you>\n"
    ).format(slug=slug)


def session_md(name: str) -> str:
    """Seed text for a session's ``arc.md`` passport (one run inside a thread).

    A *session* is one orchestrator entry within a thread. Its ``## cursor`` is the
    resume slot — re-entering the session drops you back here. ``from:`` chains it
    to the prior session so the lineage is visible. Context offloads / handoff
    distillations accrue in ``## context`` over the session's life.

    *name* is the entry dir name (``NN-<slug>``) used as the H1 heading.
    """
    return (
        "# {name}\n"
        "\n"
        "title: <human title — set on handoff/offload so the picker reads well>\n"
        "goal: <one line — what this session is for>\n"
        "status: active\n"
        "offloaded-at: 0\n"
        "# from: <prior session slug — set automatically, or by branch/handoff --from>\n"
        "\n"
        "## summary\n"
        "<a few plain sentences: what got done, what's unfinished, where it's heading —\n"
        "written on handoff; longer if the session is large>\n"
        "\n"
        "## cursor — resume here\n"
        "<where this session left off; the next concrete step to pick up>\n"
        "\n"
        "## context\n"
        "<session memory — offload appends new context here, incrementally>\n"
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

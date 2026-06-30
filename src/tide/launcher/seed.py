"""tide.launcher.seed — resolve context into a seed string for a fresh session.

The seed is the opening payload a NEW Claude session is launched with: the role
prompt + the project's living-IS canon + (optionally) the active arc passport +
the control-home roster. It orients a fresh orchestrator session the same way the
SessionStart hook orients an in-place one, but it is *transported* by a terminal
adapter (``tide.adapters``) into a brand-new terminal rather than printed inline.

Two layers, mirroring the rest of the package:

* :func:`build_seed` — **pure** string assembly from already-resolved pieces
  (canon text, arc text, roster text, prompt text). Argparse-free, snapshot-
  testable, never touches disk.
* :func:`seed_for_project` — the **disk** wrapper: reads ``CANON.md``, the global
  role prompt (``prompts/<role>.md``, shipped in U12 — absent is tolerated), the
  selected arc's passport, and the control-home roster, then calls
  :func:`build_seed`.

Seed construction is deliberately **adapter-agnostic**: the adapter only carries
the returned string, so adapters stay thin and interchangeable.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .. import paths, slug
from ..canon import store
from ..hooks.session_start import ROLE_REMINDERS

ROLE_ORCHESTRATOR = "orchestrator"
ROLE_WORKER = "worker"

SEED_TITLE = "# tide session seed"


# --- prompt resolution -----------------------------------------------------

def prompt_file_for_role(role: str) -> Path:
    """Path to the shipped global prompt for *role* (``prompts/<role>.md``)."""
    return paths.global_prompts_dir() / "{0}.md".format(role)


def read_role_prompt(role: str) -> Optional[str]:
    """Return the shipped ``prompts/<role>.md`` text, or None when not yet shipped.

    The prompt bodies land in U12; until then (and in a source tree without them)
    this returns None and :func:`build_seed` falls back to the one-line role
    reminder so a seed is always well-formed.
    """
    f = prompt_file_for_role(role)
    if not f.is_file():
        return None
    text = f.read_text(encoding="utf-8").strip()
    return text or None


def _role_block(role: str, prompt_text: Optional[str]) -> str:
    """The role section body: the shipped prompt if present, else the reminder."""
    if prompt_text:
        return prompt_text
    return ROLE_REMINDERS.get(role, ROLE_REMINDERS[ROLE_WORKER])


# --- arc passport resolution -----------------------------------------------

def _find_open_entry(root: Path, ref: str) -> Optional[Path]:
    """First OPEN top-stream entry whose slug matches *ref* (goal preferred)."""
    arcs = paths.arcs_dir(root)
    if not arcs.is_dir():
        return None
    want = slug.slugify(ref)
    matches = [
        p
        for p in arcs.iterdir()
        if p.is_dir()
        and p.name != paths.CANDIDATES_DIRNAME
        and not slug.is_closed_entry(p.name)
        and slug.entry_slug(p.name) == want
    ]
    if not matches:
        return None
    # Prefer the goal when a slug names both a goal and a plain arc.
    matches.sort(key=lambda p: (not slug.is_goal_entry(p.name), p.name))
    return matches[0]


def read_arc_passport(root: Path, ref: str) -> Optional[str]:
    """Return the passport text (goal doc / arc.md) of the open arc *ref*, or None."""
    from ..arc.stream import passport_path  # lazy: arc.stream is a heavier sibling.

    entry = _find_open_entry(root, ref)
    if entry is None:
        return None
    passport = passport_path(entry)
    if not passport.is_file():
        return None
    return passport.read_text(encoding="utf-8").strip() or None


def read_routine_procedure(root: Path, routine_slug: str) -> Optional[str]:
    """Return a routine container's goal doc (its ``## steps`` + ``## experience``).

    The procedure lives on the routine CONTAINER (a ``kind: routine`` goal doc),
    not on the run sub-arc — so a routine run's seed must read it separately to
    carry the actual runbook. Returns None when no open routine matches *slug*.
    """
    from ..arc import stream  # lazy: heavier sibling
    from .. import slug as _slug

    for entry in stream.routine_entries(root):
        if _slug.entry_slug(entry.name) == _slug.slugify(routine_slug):
            pp = stream.passport_path(entry)
            if pp.is_file():
                return pp.read_text(encoding="utf-8").strip() or None
    return None


# --- launch hint -----------------------------------------------------------

def launch_command(project_name: str, arc_ref: Optional[str] = None) -> str:
    """The human-readable jump command the fresh session can re-run (``tide …``)."""
    if arc_ref:
        return "tide {0} {1}".format(project_name, arc_ref)
    return "tide {0}".format(project_name)


# --- pure assembly ---------------------------------------------------------

def build_seed(
    *,
    project_name: str,
    role: str = ROLE_ORCHESTRATOR,
    canon_text: str = "",
    roster_text: Optional[str] = None,
    arc_ref: Optional[str] = None,
    arc_text: Optional[str] = None,
    thread_name: Optional[str] = None,
    container_kind: str = "thread",
    procedure_text: Optional[str] = None,
    prompt_text: Optional[str] = None,
    launch_cmd: Optional[str] = None,
) -> str:
    """Assemble the seed string from already-resolved pieces (pure, no I/O).

    Sections, in order: a header naming the project + role, the role block (shipped
    prompt or the fallback reminder), the project ``CANON.md``, the active entry
    passport (only when *arc_ref* is given), the control-home roster (only when
    *roster_text* is given), and a closing launch hint. When *thread_name* is given
    the active entry is framed as a **session inside a thread (тред)** — or, when
    *container_kind* is ``"routine"``, as a **routine run** (a reusable procedure:
    its ``## steps`` are the runbook, ``## experience`` accrues across runs). The
    ``## cursor`` is the resume point either way. Empty pieces render as an explicit
    ``(…)`` note so the shape is stable for snapshot tests.
    """
    lines: List[str] = [
        SEED_TITLE,
        "",
        "You are opening a fresh **{0}** tide session for project **{1}**.".format(
            role.upper(), project_name
        ),
        "",
        "## Role",
        _role_block(role, prompt_text),
        "",
        "## CANON.md — {0}".format(project_name),
        canon_text.strip() if canon_text.strip() else "(no canon yet — run 'tide canon init')",
    ]

    if arc_ref:
        if thread_name and container_kind == "routine":
            lines += [
                "",
                "## Active routine run — {0}  (routine: {1})".format(arc_ref, thread_name),
                "This session IS a run of the reusable routine (рутина) **{0}** — that "
                "is your job here: execute the procedure below WITH the human (this is "
                "the one place you act, not stay passive). Follow the `## steps`; mind "
                "`## experience` (lessons from prior runs); when done, append what this "
                "run taught back to `## experience` and update this run's `## cursor`.".format(thread_name),
                "",
                "### Routine procedure — {0}".format(thread_name),
                procedure_text.strip() if (procedure_text and procedure_text.strip())
                else "(routine procedure not found — read its goal doc in .tide/arcs/)",
                "",
                "### This run — {0}".format(arc_ref),
                arc_text.strip() if (arc_text and arc_text.strip()) else "(no run passport found)",
            ]
        elif thread_name:
            lines += [
                "",
                "## Active session — {0}  (thread: {1})".format(arc_ref, thread_name),
                "You are continuing a **session** inside the thread (тред) **{0}** — "
                "the arc through which this work-line is managed. Resume from the "
                "session's `## cursor`; keep the cursor + `## context` updated as you "
                "work so the next session can pick up.".format(thread_name),
                "",
                arc_text.strip() if (arc_text and arc_text.strip()) else "(no session passport found)",
            ]
        else:
            lines += [
                "",
                "## Active arc — {0}".format(arc_ref),
                arc_text.strip() if (arc_text and arc_text.strip()) else "(no open arc passport found)",
            ]

    if roster_text is not None:
        lines += [
            "",
            "## Roster (control-home)",
            roster_text.strip() if roster_text.strip() else "(no projects)",
            "",
            "Notice work that belongs to a NEIGHBOUR project (above)? Don't lose it and "
            "don't context-switch — drop it as a candidate there: "
            "`tide candidate add <slug> \"<the idea>\" --project <roster-name>`. It lands "
            "in that project's backlog (tagged with where it came from) for its "
            "orchestrator to promote later. Capturing is cheap; promoting stays local.",
        ]

    lines += [
        "",
        "## Launch",
        "Re-enter from a terminal with: `{0}`".format(
            launch_cmd or launch_command(project_name, arc_ref)
        ),
    ]
    return "\n".join(lines) + "\n"


# --- disk wrapper ----------------------------------------------------------

def seed_for_project(
    root: Path,
    *,
    arc_ref: Optional[str] = None,
    arc_text: Optional[str] = None,
    thread_name: Optional[str] = None,
    container_kind: str = "thread",
    role: str = ROLE_ORCHESTRATOR,
    control_home: Optional[Path] = None,
) -> str:
    """Build the seed for project *root*, reading canon / arc / prompt / roster.

    *control_home* (when given and a real control-home) supplies the roster block
    so a cross-project orchestrator session sees the whole portfolio. *arc_text*,
    when given, is used verbatim as the active entry's passport (the picker passes
    a session's passport directly, since sessions live in a thread substream that
    the top-stream ``read_arc_passport`` would not find); otherwise the passport is
    read by *arc_ref*. *thread_name* frames the entry as a session inside that
    thread. A missing CANON.md / prompt / arc all degrade to explicit notes — a
    seed is always producible.
    """
    root = Path(root)
    project_name = root.resolve().name

    canon = paths.canon_file(root)
    canon_text_str = canon.read_text(encoding="utf-8") if canon.is_file() else ""

    if arc_text is None and arc_ref:
        arc_text = read_arc_passport(root, arc_ref)
    # A routine run must carry the routine's procedure (## steps / ## experience),
    # which lives on the routine container — NOT on the run sub-arc's passport.
    procedure_text = None
    if container_kind == "routine" and thread_name:
        procedure_text = read_routine_procedure(root, thread_name)
    prompt_text = read_role_prompt(role)

    roster_text: Optional[str] = None
    if control_home is not None:
        from .. import roster as roster_mod

        home = Path(control_home)
        if paths.is_control_home(home):
            roster_text = roster_mod.render_list(home)

    return build_seed(
        project_name=project_name,
        role=role,
        canon_text=canon_text_str,
        roster_text=roster_text,
        arc_ref=arc_ref,
        arc_text=arc_text,
        thread_name=thread_name,
        container_kind=container_kind,
        procedure_text=procedure_text,
        prompt_text=prompt_text,
        launch_cmd=launch_command(project_name, arc_ref),
    )

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

from .. import paths

ROLE_ORCHESTRATOR = "orchestrator"
ROLE_WORKER = "worker"

SEED_TITLE = "# tide session seed"

# The START GATE (cand 81/87), slimmed since the passport FLOOR became birth mechanics
# (cands 102/105): every session is now BORN with a default title and — when the thread
# has one — an inherited goal, so the seed no longer asks the agent to build the floor,
# only to make it speak: sharpen the words and do the first pulse. The offload nudge
# (Stop hook) enforces the pulse half.
def _start_gate(thread: str, session: str, role: str = ROLE_ORCHESTRATOR) -> str:
    """Старт-гейт с ГОТОВЫМИ командами (SOC-286 эксп. 1): ref нити и сессии подставлены,
    метки пересказа — из :data:`tide.handoff_queue.RETELL_ITEMS`. Голове-оркестратору
    role-gate запрещает писать файлы — plan.md вписывает помощник (Agent)."""
    from .. import handoff_queue as _hq, slug as _slug  # lazy: keep the seed module light

    thread, session = _slug.entry_slug(thread), _slug.entry_slug(session)
    goal_cmd, offload_cmd = _hq.start_gate_commands(thread, session)
    plan = ("попроси помощника (Agent) вписать в" if role == ROLE_ORCHESTRATOR
            else "впиши в")
    return (
        "**Старт-гейт — до первого хода работы.** Паспорт уже рождён с именем сессии "
        "(`title:` «проект — нить · NN», тот же текст, что в списке сессий AO — его не "
        "меняй) и целью нити — сделай цель живой под ЭТУ сессию:\n"
        "`{0}`\n"
        "{2} `{3}/plan.md` раздел «## главное — читать первым» — минимум три "
        "строки вместо подсказок: SOC-N · кому и каким каналом сдавать · с кем не "
        "дублировать (решение № 24; у новой нити без них первый пульс не пройдёт); "
        "затем первый пульс — пересказ своими словами (каждую метку заполни; чего не "
        "знаешь — «нет»):\n"
        "`{1}`\n"
        "Только потом — работа."
    ).format(goal_cmd, offload_cmd, plan, thread)


# Закон 47 — план нити. Greet (спарк/хендофф) велит свежей сессии «построй план по
# закону 47», но до cand 127 ОПРЕДЕЛЕНИЕ жило только в read_plan доски
# (board/live_projection.py) — холодный агент грепал всё дерево за ним и ловил
# таймаут (1.8МБ, exit 144). Носим компактное, авторитетное определение прямо в
# seed, чтобы план строился не выходя из паспорта и без грепа исходника.
LAW_47_PLAN = (
    "**Закон 47 — план нити.** Живёт в `<нить>/plan.md`. Строка "
    "`final: <куда идём одной фразой>`, затем шаги формата "
    "`- [x|>| ] N. имя | что делается | результат` (`x` — сделан, `>` — текущий, "
    "пусто — впереди). Текущий шаг можно раскрыть под-строками `  описание:` и "
    "`  проверка:`. План **иммутабелен**: шаги не переписывай — правки копятся "
    "версиями в секции `## патчи`, заголовок несёт `· vN`. Развилка несущая, "
    "только если оформлена (∥N в имени шага + шаг слияния). Доску кормит этот "
    "файл — определение под рукой, исходник не грепать."
)


def _esc_rules() -> str:
    """SOC-157/SOC-181: правила нити (итог сабагента, тесты в фоне, почта, троттлинг, модели,
    опыт, эскалация) — одной строкой-ссылкой на файл :func:`tide.escalate_rules.rules_file`,
    не копией текста."""
    from .. import escalate_rules as escalate
    from .. import ball
    return "**Правила.** " + escalate.rules_line() + "; " + ball.BALL_LINE + "."


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


def _role_block(role: str, prompt_text: Optional[str]) -> Optional[str]:
    """The role section body — the shipped prompt, or None when none is shipped.

    The one-line role reminder is NOT re-rendered here: the SessionStart hook
    prints it in every session, and the launcher installs that hook before every
    spawn (``launcher.launch``), so a seeded session heard it once already. Absent
    a shipped ``prompts/<role>.md`` the section is simply dropped — the seed header
    already names the role.
    """
    return prompt_text or None


# --- arc passport resolution -----------------------------------------------

def _find_open_entry(root: Path, ref: str) -> Optional[Path]:
    """First OPEN top-stream entry whose slug matches *ref* (goal preferred).

    Thin alias over :func:`tide.resolve.open_top_entry`. This copy used to
    match only the bare slug (one-form) — the displayed name ``04-@slug``
    silently missed, the exact cand-43 trap the shared resolver kills.
    """
    from .. import resolve as _resolve
    return _resolve.open_top_entry(root, ref)


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
    prompt_text: Optional[str] = None,
    launch_cmd: Optional[str] = None,
    decisions_text: Optional[str] = None,
) -> str:
    """Assemble the seed string from already-resolved pieces (pure, no I/O).

    Sections, in order: a header naming the project + role, the role block (only
    when a ``prompts/<role>.md`` is shipped), the project ``CANON.md``, the active
    entry passport (only when *arc_ref* is given), the control-home roster (only
    when *roster_text* is given), and a closing launch hint. When *thread_name* is given
    the active entry is framed as a **session inside a thread (тред)**; the
    ``## cursor`` is the resume point. Empty pieces render as an explicit ``(…)``
    note so the shape is stable for snapshot tests.
    """
    lines: List[str] = [
        SEED_TITLE,
        "",
        "You are opening a fresh **{0}** tide session for project **{1}**.".format(
            role.upper(), project_name
        ),
    ]

    role_block = _role_block(role, prompt_text)
    if role_block:
        lines += ["", "## Role", role_block]

    lines += [
        "",
        "## CANON.md — {0}".format(project_name),
        canon_text.strip() if canon_text.strip() else "(no canon yet — run 'tide canon init')",
    ]

    if decisions_text and decisions_text.strip():
        # SOC-102: договорённости ссылкой и решения Гриши по проекту за 3 суток
        lines += ["", "## Договорённости и решения Гриши", decisions_text.strip()]

    if arc_ref:
        if thread_name:
            lines += [
                "",
                "## Active session — {0}  (thread: {1})".format(arc_ref, thread_name),
                "You are continuing a **session** inside the thread (тред) **{0}** — "
                "the arc through which this work-line is managed. Resume from the "
                "session's `## cursor`; keep the cursor + `## context` updated as you "
                "work so the next session can pick up.".format(thread_name),
                "",
                _start_gate(thread_name, arc_ref, role),
                "",
                LAW_47_PLAN,
                "",
                _esc_rules(),
                "",
                arc_text.strip() if (arc_text and arc_text.strip()) else "(no session passport found)",
            ]
            # The nit's OPEN decisions (cand 128-A) are NOT rendered here: the
            # SessionStart hook injects the same block from the same source
            # (``decision.render_open_for_context``), and the launcher binds the sid
            # into the session passport BEFORE spawn — so the hook resolves the nit
            # and prints them once. Rendering them here too printed them twice.
        else:
            lines += [
                "",
                "## Active arc — {0}".format(arc_ref),
                arc_text.strip() if (arc_text and arc_text.strip()) else "(no open arc passport found)",
            ]

    if roster_text is not None:
        # ONE line instead of the two evergreen paragraphs that used to sit here
        # (cand 127): both said the same two things forever — park a neighbour's
        # work as a candidate there, and «заводим проект» means `tide adopt`, not a
        # new thread. Compressed to the two commands with the reason each exists.
        lines += [
            "",
            "## Roster (control-home)",
            roster_text.strip() if roster_text.strip() else "(no projects)",
            "",
            "Work for a NEIGHBOUR project — park it there, don't context-switch: "
            "`tide candidate add <slug> \"<idea>\" --project <roster-name>`. "
            "«Заводим проект» — `tide adopt <abs-path> [--name <roster-name>]` (a "
            "thread `NN-@slug` is a work-line INSIDE a project, never a roster row).",
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
    prompt_text = read_role_prompt(role)

    roster_text: Optional[str] = None
    if control_home is not None:
        from .. import roster as roster_mod
        from . import menu as menu_mod

        home = Path(control_home)
        if paths.is_control_home(home):
            # ARCHIVED projects are dead paths a fresh session must never be handed
            # (live: 13 of 27 rows). The picker already hides them — same filter here
            # (``tide roster ls`` keeps showing everything; that is its job).
            roster_text = roster_mod.render_entries(
                menu_mod.active_entries(roster_mod.read_roster(home))
            )

    # SOC-102: договорённости ссылкой и решения Гриши по проекту (имя в ростере) за 3 суток
    from .. import grisha_decisions as _gd
    try:
        from .. import roster as roster_name_mod
        roster_name = roster_name_mod.name_for_path(
            root, home=Path(control_home) if control_home is not None else None)
    except Exception:  # noqa: BLE001  no control-home — dir name
        roster_name = project_name
    decisions_text = _gd.seed_text(roster_name)

    return build_seed(
        project_name=project_name,
        role=role,
        canon_text=canon_text_str,
        roster_text=roster_text,
        arc_ref=arc_ref,
        arc_text=arc_text,
        thread_name=thread_name,
        prompt_text=prompt_text,
        launch_cmd=launch_command(project_name, arc_ref),
        decisions_text=decisions_text,
    )

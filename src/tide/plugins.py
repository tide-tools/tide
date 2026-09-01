"""tide.plugins — the one list that says what is core and what is removable.

Why this module exists
----------------------
Until now the boundary between "the stack itself" and "things bolted onto it"
existed nowhere but in the skills dir. The board's tabs (threads, issues, work,
projects, news, pages, skills) were nailed straight into the markup: turning the
news feed or the drawing canvas off meant deleting code. This module is the
single place that names the parts, so handing tide to another person can start
from "here is the core, here is what you may switch on".

Two halves, deliberately separated
----------------------------------
1. **The catalogue** — :data:`CORE` and :data:`PLUGINS` right below. This ships
   with the engine, because *what a part is* is a property of the code, not of
   the person running it. One table, readable top to bottom.
2. **The switchboard** — a plain text file in the person's control-home
   (``<control-home>/.tide/plugins``). This is per-person state: which of the
   removable parts this particular install wants. It sits at the ``.tide/`` root
   next to ``deferred.md`` for the same reason that one does — it is meant to be
   opened and edited by a human, not managed by a tool.

The safe default is ON
----------------------
A missing file, an unreadable file, a garbage line, an unknown name — none of
these may take a working surface away from someone. :func:`enabled` folds all of
them into "everything is on". The only thing that switches a plugin off is an
explicit ``name = off`` line. That is what guarantees an existing install (which
has no such file) keeps the board it had yesterday, to the pixel.

Reading it
----------
    from tide import plugins
    plugins.enabled()            # {'news', 'pages', 'skills', 'issues', 'work'}
    plugins.is_enabled('news')   # True

Callers outside the engine (the board) import :func:`enabled` and pass the home
explicitly, because the board is launched by launchd from a project dir and has
no ``$TIDE_HOME`` to climb from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Set

from . import io as _io, paths
from .arc.stream import StreamError

REGISTRY_FILE = "plugins"


class Part(NamedTuple):
    """One named part of the stack.

    ``name``     stable id — what goes in the registry file and in ``tide plugins``
    ``title``    what a human calls it
    ``note``     one line saying what it is and where it lives
    ``core``     True → shipped, not switchable
    ``default``  for plugins: the state when the registry says nothing
    ``planned``  True → a place held open, no code behind it yet
    """

    name: str
    title: str
    note: str
    core: bool = False
    default: bool = True
    planned: bool = False


# --- the catalogue ---------------------------------------------------------
# CORE — what "tide" means. Not switchable: there is no install without these.

CORE: List[Part] = [
    Part("projects", "проекты",
         "дома в ростере: завести, усыновить, переключиться", core=True),
    Part("threads", "нити",
         "нить и её базовая структура input/output/workspace", core=True),
    Part("cli", "вербы",
         "команды tide — единственный способ править .tide/", core=True),
    Part("board", "каркас доски",
         "tide board на localhost: стримы дома и проектов; ряд вкладок "
         "поверх — у живой доски",
         core=True),
    Part("system-skills", "системные скиллы",
         "скиллы метода (tide-flow, handoff, offload), ставит tide install-skills",
         core=True),
    Part("hooks", "хуки",
         "вход и выход сессии, гейты правки и роли", core=True),
]

# PLUGINS — removable. Everything here defaults ON, because every existing
# install already has it; a fresh install is what starts from core-only (see
# :func:`seed_new_install`).

PLUGINS: List[Part] = [
    Part("issues", "issues",
         "стол входящих — вид структуры нити (вкладку «issues» рисует живая "
         "доска; tide board его не показывает)"),
    Part("work", "работы",
         "согласование человек↔агент — вид структуры нити; карточки работ "
         "показывает и tide board"),
    Part("canon", "канон",
         "свод правил проекта — вид структуры нити; .tide/canon/"),
    Part("news", "новости",
         "лента инбокса и конвейер разбора (вкладку «новости» рисует живая "
         "доска; tide board её не показывает)"),
    Part("pages", "страницы",
         "рисовалка — холст, штрихи, картинки (вкладку рисует живая доска; "
         "tide board её не показывает)"),
    Part("skills", "навыки",
         "витрина скиллов, глобальных и по проектам (вкладку «навыки» рисует "
         "живая доска; tide board её не показывает)"),
    Part("linear", "внешний Linear",
         "вид структуры нити поверх Linear — место заложено, кода ещё нет",
         default=False, planned=True),
]

ALL: List[Part] = CORE + PLUGINS
_BY_NAME: Dict[str, Part] = {p.name: p for p in ALL}


class PluginError(StreamError):
    """An unknown plugin name, or an attempt to switch a core part off.

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on
    the same arm as every other tide error (prints ``tide: …``, exits nonzero).
    """


def part(name: str) -> Optional[Part]:
    """The catalogue entry for *name*, or None when the name is unknown."""
    return _BY_NAME.get((name or "").strip().lower())


def is_core(name: str) -> bool:
    p = part(name)
    return bool(p and p.core)


def plugin_names() -> List[str]:
    """Names of the removable parts, in catalogue order."""
    return [p.name for p in PLUGINS]


def default_enabled() -> Set[str]:
    """What is on when the registry says nothing — every non-planned plugin.

    This is the answer to "no registry = everything on", and the reason an
    install that predates this module sees no change at all.
    """
    return {p.name for p in PLUGINS if p.default and not p.planned}


# --- the switchboard file --------------------------------------------------

def registry_file(home: Optional[Path] = None) -> Path:
    """Path to ``<control-home>/.tide/plugins``.

    *home* may be given explicitly (the board does that — it is launched from a
    project dir and cannot climb to the control-home). Otherwise resolution goes
    through :func:`tide.paths.control_home`, i.e. ``$TIDE_HOME`` then the climb.
    """
    root = Path(home) if home is not None else paths.control_home()
    return paths.tide_dir(root) / REGISTRY_FILE


HEADER = """\
# tide plugins — что включено у этого человека.
#
# Строка «имя = on» включает часть, «имя = off» выключает. Строка с # —
# комментарий. Нет файла или нет строки — часть ВКЛЮЧЕНА: пустой реестр
# ничего не отнимает.
#
# Кор — не выключается, строки для него тут не нужны:
{core}
#
# Съёмные части:
"""


def _core_comment() -> str:
    return "\n".join(
        "#   {0:<14} {1}".format(p.name, p.note) for p in CORE
    )


def render(state: Dict[str, bool]) -> str:
    """Render the whole registry file from a name → on/off map.

    Every known plugin gets a line with its note above it, so the file reads as
    documentation of what can be switched, not as an opaque config.
    """
    out = [HEADER.format(core=_core_comment())]
    for p in PLUGINS:
        on = state.get(p.name, p.default and not p.planned)
        tail = "   # места ещё нет — заготовка" if p.planned else ""
        out.append("\n# {0} — {1}{2}\n{3} = {4}\n".format(
            p.title, p.note, tail, p.name, "on" if on else "off"))
    return "".join(out)


def _parse(text: str) -> Dict[str, bool]:
    """Parse ``name = on|off`` lines. Anything unparseable is skipped silently.

    Silently, on purpose: a typo in this file must not cost someone their board.
    ``tide plugins`` is where unknown names get reported to the human.
    """
    state: Dict[str, bool] = {}
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip().lower()
        value = value.strip().lower()
        if value in ("on", "1", "true", "yes"):
            state[name] = True
        elif value in ("off", "0", "false", "no"):
            state[name] = False
    return state


def read_registry(home: Optional[Path] = None) -> Dict[str, bool]:
    """Raw parsed registry (may contain unknown names), or ``{}`` when absent.

    Never raises: a missing home, a missing file, a permission error and an
    undecodable file all read back as "nothing said".
    """
    try:
        f = registry_file(home)
        if not f.is_file():
            return {}
        return _parse(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def enabled(home: Optional[Path] = None) -> Set[str]:
    """The set of plugin names that are ON for this person.

    Starts from :func:`default_enabled` and applies only the lines the registry
    actually states, ignoring names that are not in the catalogue and any line
    naming a core part (core cannot be switched off, and a stray ``board = off``
    must not blank someone's screen).

    Never raises. This is the function the board calls on every request.
    """
    try:
        state = read_registry(home)
        on = default_enabled()
        for name, value in state.items():
            p = _BY_NAME.get(name)
            if p is None or p.core:
                continue
            if value:
                on.add(name)
            else:
                on.discard(name)
        return on
    except Exception:
        return default_enabled()


def is_enabled(name: str, home: Optional[Path] = None) -> bool:
    """True when *name* is a core part, or a plugin switched on for this person."""
    if is_core(name):
        return True
    return name in enabled(home)


def unknown_names(home: Optional[Path] = None) -> List[str]:
    """Names present in the registry file that the catalogue does not know."""
    return sorted(n for n in read_registry(home) if n not in _BY_NAME)


# --- writing ---------------------------------------------------------------

def write_registry(state: Dict[str, bool], home: Optional[Path] = None) -> Path:
    """Rewrite the registry file from *state*, returning the path written."""
    f = registry_file(home)
    f.parent.mkdir(parents=True, exist_ok=True)
    _io.atomic_write(f, render(state))
    return f


def set_plugin(name: str, on: bool, home: Optional[Path] = None) -> Path:
    """Switch one plugin on/off — the one gesture behind ``tide plugins on|off``.

    Writes the FULL file (every plugin with its note), not just the touched
    line: the first switch a person flips is also the moment they get a readable
    list of everything else they could flip.
    """
    p = part(name)
    if p is None:
        raise PluginError(
            "plugins: unknown part {0!r} (known: {1})".format(
                name, ", ".join(plugin_names())))
    if p.core:
        raise PluginError(
            "plugins: {0} is core — it does not switch off".format(p.name))
    state = {q.name: (q.name in enabled(home)) for q in PLUGINS}
    state[p.name] = on
    return write_registry(state, home)


def seed_new_install(home: Optional[Path] = None) -> Path:
    """Write a core-only registry — every plugin OFF. What a fresh install gets.

    Deliberately NOT called from anywhere automatic in an existing home: the
    absence of the file is what keeps an existing board unchanged, and creating
    a file that says ``off`` everywhere would be exactly the change we promised
    not to make.
    """
    return write_registry({p.name: False for p in PLUGINS}, home)


# --- CLI wiring ------------------------------------------------------------

def _fmt_row(p: Part, on: bool) -> str:
    if p.core:
        mark = "кор"
    elif p.planned:
        mark = "—  " if not on else "on "
    else:
        mark = "on " if on else "off"
    return "  {0}  {1:<14} {2}".format(mark, p.name, p.note)


def _cmd_plugins(args) -> int:
    home = paths.control_home()
    action = getattr(args, "action", None)
    if action in ("on", "off"):
        f = set_plugin(args.name, action == "on", home)
        print("tide: {0} → {1}  ({2})".format(args.name, action, f))
        # A plugin's skill rides its plugin (work 49) — deliver/retract it in the
        # SAME gesture, so the person never has to remember a second command
        # (work 51: one gesture per step). Best-effort: a machine with no source
        # checkout / no skills dir still gets its registry flip.
        try:
            from .skills_install import install_skills

            for name, verdict in install_skills():
                if verdict in ("linked", "copied", "replaced") or verdict.startswith("removed"):
                    print("tide: skill {0}: {1}".format(name, verdict))
        except Exception:  # noqa: BLE001 — the flip must not die on skill delivery
            pass
        return 0
    if action == "where":
        print(registry_file(home))
        return 0
    on = enabled(home)
    f = registry_file(home)
    print("кор — всегда:")
    for p in CORE:
        print(_fmt_row(p, True))
    print("\nсъёмное:")
    for p in PLUGINS:
        print(_fmt_row(p, p.name in on))
    print("\nреестр: {0}{1}".format(f, "" if f.is_file() else "  (нет файла — всё включено)"))
    strays = unknown_names(home)
    if strays:
        print("незнакомые имена в реестре (пропущены): {0}".format(", ".join(strays)))
    return 0


def register(subparsers) -> None:
    """Add ``tide plugins [on|off <name>|where]`` (called by cli.py)."""
    p = subparsers.add_parser(
        "plugins", help="what is core, what is removable, what is on here")
    sub = p.add_subparsers(dest="action")
    for act, helptext in (("on", "switch a plugin on"), ("off", "switch a plugin off")):
        s = sub.add_parser(act, help=helptext)
        s.add_argument("name", help="plugin name (see `tide plugins`)")
        s.set_defaults(func=_cmd_plugins, _cmd="plugins")
    w = sub.add_parser("where", help="print the registry file path")
    w.set_defaults(func=_cmd_plugins, _cmd="plugins")
    p.set_defaults(func=_cmd_plugins, _cmd="plugins")

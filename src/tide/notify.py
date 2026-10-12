"""SOC-238: уровень Telegram нити — что из нити доходит до человека в Telegram.

Слово Гриши (SOC-238, этап 2, идея 2): в Telegram от нити приходит только то, что он для
неё выбрал. Поле паспорта ``notify:`` — ``all`` (как раньше, по умолчанию), ``questions``
(только вопросы человеку: ❓/🖐 и отчёты с «ждёт решения»), ``quiet`` (в Telegram ничего).

Паспорт — :func:`tide.arc.stream.passport_path` арки (у нити ``<slug>-goal.md``, у сессии
``arc.md``). Наследование — по дереву арок: эффективный уровень = самый тихий из своего и
всех родителей (ребёнок не может быть громче родителя). ``all`` при записи снимает своё
поле — арка снова наследует.

Уровень касается только Telegram: доска и лента «Отчёты» читают всё, как раньше. Сам
фильтр — в пульте (он решает, слать ли отчёт/алерт в Telegram); пульт спрашивает уровень
так: ``tide notify --source <проект>/<нить>[/<сессия>] --level-only`` (источник — тот же
``TIDE_ALERT_SOURCE`` / поля ``project``/``thread``/``session`` отчёта). Не нашли арку —
``all``: поведение не меняется.

CLI: ``tide notify <нить>[/<сессия>] [all|questions|quiet]`` — показать / поставить;
``--dir <каталог арки>`` — контракт доски; ``--json`` — машинный вывод для доски.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import fields, paths, resolve, slug
from .arc.stream import StreamError, passport_path

FIELD = "notify"
ALL, QUESTIONS, QUIET = "all", "questions", "quiet"
LEVELS: Tuple[str, ...] = (ALL, QUESTIONS, QUIET)
DEFAULT = ALL
_RANK = {lvl: i for i, lvl in enumerate(LEVELS)}   # больше — тише


class NotifyError(StreamError):
    pass


def _norm(value: Optional[str]) -> Optional[str]:
    v = (value or "").strip().lower()
    return v if v in _RANK else None


# --- модель -------------------------------------------------------------------

def own_level(arc_dir: Path) -> Optional[str]:
    """Свой уровень арки из паспорта; нет поля или мусор — None (наследует)."""
    pp = passport_path(Path(arc_dir))
    if not pp.is_file():
        return None
    return _norm(fields.read_field(pp, FIELD))


def chain(arc_dir: Path) -> List[Path]:
    """Арка и все её родители-арки вверх по ``…/arcs/<entry>`` до верхнего потока проекта."""
    d = Path(arc_dir)
    out = [d]
    while d.parent.name == paths.ARCS_DIRNAME:
        up = d.parent.parent
        if up.name == paths.TIDE_DIR or not (up.parent.name == paths.ARCS_DIRNAME):
            break
        out.append(up)
        d = up
    return out


def effective(arc_dir: Path) -> Tuple[str, Optional[Path]]:
    """(эффективный уровень, арка, откуда он взят) — самый тихий по цепочке.

    При равенстве побеждает родитель (ограничение сверху). Нигде не задан — (all, None).
    """
    best, src = DEFAULT, None
    for d in chain(arc_dir):
        lvl = own_level(d)
        if lvl is not None and _RANK[lvl] >= _RANK[best]:
            best, src = lvl, d
    return best, src


def set_level(arc_dir: Path, level: str) -> Path:
    """Поставить свой уровень; ``all`` снимает поле (арка снова наследует). Паспорт — вернуть."""
    lvl = _norm(level)
    if lvl is None:
        raise NotifyError("notify: уровень {0!r} — нужен один из {1}".format(level, "|".join(LEVELS)))
    d = Path(arc_dir)
    if not d.is_dir():
        raise NotifyError("notify: нет такой арки: {0}".format(d))
    pp = passport_path(d)
    if not pp.is_file():
        raise NotifyError("notify: нет паспорта в {0}".format(d))
    if lvl == ALL:
        fields.remove_field(pp, FIELD)
    else:
        fields.set_field(pp, FIELD, lvl)
    return pp


def describe(arc_dir: Path) -> Dict[str, object]:
    """Формат для доски: ``{dir, own, effective, from, levels}``."""
    lvl, src = effective(arc_dir)
    return {"dir": str(Path(arc_dir)), "own": own_level(arc_dir), "effective": lvl,
            "from": str(src) if src else None, "levels": list(LEVELS)}


# --- источник пульта: <проект>/<нить>[/<сессия>] ------------------------------

def _project_root(name: str) -> Optional[Path]:
    from . import roster

    try:
        entries = roster.read_roster(paths.control_home())
    except Exception:  # noqa: BLE001 — нет control-home: источник не найден
        return None
    for e in entries:
        p = Path(e["path"]).expanduser()
        if e["name"] == name or p.name == name:
            return p
    return None


def _child(stream_dir: Path, part: str) -> Optional[Path]:
    """Дочерняя арка по имени каталога или слагу; открытая — в приоритете."""
    found = [p for p in resolve.child_entries(stream_dir)
             if p.name == part or resolve.entry_matches(part, p.name)]
    found.sort(key=lambda p: slug.is_closed_entry(p.name))
    return found[0] if found else None


def resolve_source(source: str) -> Optional[Path]:
    """Каталог арки по ``<проект>/<нить>[/<сессия>…]``; нет проекта/нити — None.

    Неизвестная сессия — нить (её уровень всё равно ограничивает сессию).
    """
    parts = [p for p in str(source or "").strip().split("/") if p.strip()]
    if len(parts) < 2:
        return None
    root = _project_root(parts[0])
    if root is None:
        return None
    cur = None
    stream_dir = paths.arcs_dir(root)
    for part in parts[1:]:
        nxt = _child(stream_dir, part)
        if nxt is None:
            break
        cur = nxt
        stream_dir = cur / paths.ARCS_DIRNAME
    return cur


def level_for_source(source: str) -> str:
    """Эффективный уровень по источнику; не нашли — all (поведение не меняется)."""
    d = resolve_source(source)
    return effective(d)[0] if d is not None else DEFAULT


# --- CLI ----------------------------------------------------------------------

def _resolve_ref(ref: str) -> Path:
    root = paths.require_tide_root()
    if "/" in ref:
        entry = resolve.find_session(root, ref)
    else:
        entry = resolve.open_top_entry(root, ref) or resolve.find_session(root, ref)
    if entry is None:
        raise NotifyError("notify: не нашёл нить/сессию {0!r} в {1}".format(ref, root))
    return entry


def _name(d: Optional[Path]) -> str:
    if d is None:
        return "—"
    d = Path(d)
    if d.parent.name == paths.ARCS_DIRNAME and d.parent.parent.parent.name == paths.ARCS_DIRNAME:
        return "{0}/{1}".format(slug.entry_slug(d.parent.parent.name), slug.entry_slug(d.name))
    return slug.entry_slug(d.name)


def run_cli(args) -> int:
    ref, level = getattr(args, "ref", None), getattr(args, "level", None)
    if (args.dir or args.source) and ref and not level:
        ref, level = None, ref                 # tide notify --dir D quiet
    if level is not None and _norm(level) is None:
        print("tide: notify: уровень {0!r} — нужен all|questions|quiet".format(level), file=sys.stderr)
        return 2
    if args.source:
        d = resolve_source(args.source)
        if d is None and level is None:
            print(DEFAULT if args.level_only else json.dumps(
                {"source": args.source, "dir": None, "own": None, "effective": DEFAULT,
                 "from": None, "levels": list(LEVELS)}, ensure_ascii=False) if args.json else
                "tide: notify: источник {0!r} не найден — all".format(args.source))
            return 0
        if d is None:
            raise NotifyError("notify: источник {0!r} не найден".format(args.source))
    elif args.dir:
        d = Path(args.dir).expanduser()
        if ".." in str(args.dir) or "/.tide/arcs/" not in str(d.resolve() if d.exists() else d):
            raise NotifyError("notify: не каталог арки .tide/arcs: {0}".format(args.dir))
        if not d.is_dir():
            raise NotifyError("notify: нет такой арки: {0}".format(d))
    elif ref:
        d = _resolve_ref(ref)
    else:
        print("tide: notify: назови нить — tide notify <нить>[/<сессия>] [all|questions|quiet]",
              file=sys.stderr)
        return 2
    if level is not None:
        set_level(d, level)
    info = describe(d)
    if args.level_only:
        print(info["effective"])
    elif args.json:
        print(json.dumps(info, ensure_ascii=False))
    else:
        own = info["own"] or "не задан (all)"
        src = Path(info["from"]) if info["from"] else None
        tail = " (от {0})".format(_name(src)) if src is not None and src != d else ""
        print("tide: notify {0} — свой: {1} · действует: {2}{3}".format(
            _name(d), own, info["effective"], tail))
        print("  all — в Telegram всё · questions — только вопросы человеку (❓/🖐, «ждёт решения») · "
              "quiet — в Telegram ничего; доска и лента — без фильтра")
    return 0


def register(sub) -> None:
    ap = sub.add_parser(
        "notify", help="уровень Telegram нити: all | questions | quiet (SOC-238), с наследованием")
    ap.add_argument("ref", nargs="?", default=None, help="нить или <нить>/<сессия> текущего проекта")
    ap.add_argument("level", nargs="?", default=None, help="all | questions | quiet — поставить")
    ap.add_argument("--dir", default=None, help="каталог арки (контракт доски)")
    ap.add_argument("--source", default=None,
                    help="<проект>/<нить>[/<сессия>] из любого каталога (пульт: TIDE_ALERT_SOURCE)")
    ap.add_argument("--json", action="store_true", help="машинный вывод: {dir, own, effective, from, levels}")
    ap.add_argument("--level-only", action="store_true", help="только эффективный уровень одним словом")
    ap.set_defaults(func=run_cli, _cmd="notify")

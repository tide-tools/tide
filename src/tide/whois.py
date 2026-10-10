"""tide.whois — кому слать: текущий AO-id и название менеджера или нити (SOC-101 F19/F20).

Адресаты не должны жить константами («management-7», «ao-control · NN»): после
хендоффа нить ведёт другая сессия, а скрипты, будильники и подсказки tide
продолжали звать прежнюю. Один резолвер на всех:

* :func:`manager` — адрес менеджера: ``<control-home>/.tide/state/manager.json``,
  без него — старый ``~/.tide/manager.json`` (его пока пишет пульт)
  (``{"ao_session", "thread", "title"}``; ``$TIDE_MANAGER_FILE`` переопределяет);
  без файла (или без ``ao_session`` в нём) — цепочка хендоффов его нити: ведёт
  новейшая принятая сессия (:func:`tide.arc.holder.holder`).
* :func:`thread` — то же для любой нити: ``<нить>`` или ``<проект>/<нить>``.

Глагол ``tide whois manager`` / ``tide whois <нить>`` печатает
``<AO-id>\\t<title>`` (``--json`` — всё) для скриптов. Никогда не падает на
мусоре в файле — тогда идёт цепочка.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, Optional

from . import fields, paths, roster
from .arc.stream import StreamError

# Старый адрес: ~/.tide делал домашнюю папку «tide-проектом» для подъёма к
# корню (edit-gate блокировал репо без своей .tide). Только чтение с отката.
MANAGER_FILE_LEGACY = Path.home() / ".tide" / "manager.json"
MANAGER_FILE = MANAGER_FILE_LEGACY  # совместимость имени
MANAGER_FILE_NAME = "manager.json"
MANAGER_FILE_ENV = "TIDE_MANAGER_FILE"
# Нить менеджера, когда файла нет: «<проект>/<нить>» (проект — имя ростера).
MANAGER_THREAD = "management/main"
MANAGER_THREAD_ENV = "TIDE_MANAGER_THREAD"

_AO_TAIL = re.compile(r" · (\S+-\d+)$")


class WhoisError(StreamError):
    """Адрес не нашёлся."""


def manager_file_home() -> Optional[Path]:
    """Новое место manager.json — ``<control-home>/.tide/state/`` (как прочие
    глобальные состояния); None — control-home не нашёлся."""
    try:
        return paths.state_dir(paths.control_home()) / MANAGER_FILE_NAME
    except Exception:  # noqa: BLE001
        return None


def _manager_file() -> Path:
    """``$TIDE_MANAGER_FILE``; иначе новое место, если файл там есть; иначе старый
    ``~/.tide/manager.json`` (откат, чтобы живой пульт не сломался)."""
    env = (os.environ.get(MANAGER_FILE_ENV) or "").strip()
    if env:
        return Path(env).expanduser()
    new = manager_file_home()
    if new is not None and new.is_file():
        return new
    return MANAGER_FILE_LEGACY


def read_manager_file(path: Optional[Path] = None) -> Dict[str, str]:
    """Поля manager.json (строки) или {} — нет файла / битый."""
    try:
        data = json.loads(Path(path or _manager_file()).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v).strip() for k, v in data.items() if v is not None}


def _split(ref: str) -> "tuple[str, str]":
    project, _, name = (ref or "").strip().strip("/").rpartition("/")
    return project, name


def _project_root(home: Optional[Path], project: str) -> Optional[Path]:
    if not project:
        return None
    try:
        entries = roster.read_roster(home) if home else []
    except Exception:  # noqa: BLE001
        entries = []
    entry = next((e for e in entries if e["name"] == project), None)
    return Path(entry["path"]).expanduser() if entry else None


def _home() -> Optional[Path]:
    try:
        return paths.control_home()
    except Exception:  # noqa: BLE001
        return None


def thread(ref: str, *, home: Optional[Path] = None,
           root: Optional[Path] = None) -> Dict[str, str]:
    """Кто ведёт нить *ref* (``нить`` или ``проект/нить``) по цепочке хендоффов →
    ``{ao_session, title, thread, arc, claude_session, source}``."""
    from .arc import holder as _holder

    home = home or _home()
    project, name = _split(ref)
    if not name:
        raise WhoisError("whois: назови нить — tide whois <нить> | <проект>/<нить>")
    if project:
        root = _project_root(home, project)
        if root is None:
            raise WhoisError("whois: проекта {0!r} нет в ростере".format(project))
    elif root is None:
        root = paths.find_tide_root() or home
    if root is None:
        raise WhoisError("whois: не в tide-проекте и нет control-home")
    try:
        rec = _holder.holder(Path(root), name, home=home)
    except Exception as exc:  # noqa: BLE001 — HolderError и прочее: адреса нет
        raise WhoisError(str(exc)) from None
    title = ""
    if rec.get("arc"):
        passport = paths.arcs_dir(Path(root)) / str(rec["thread"]) / "arcs" / \
            str(rec["arc"]).split("/", 1)[1] / "arc.md"
        title = (fields.read_field(passport, "title") or "").strip()
        if title.startswith("<"):
            title = ""
    ao = str(rec.get("ao_session") or "")
    if not ao and title:
        m = _AO_TAIL.search(title)
        ao = m.group(1) if m else ""
    return {"ao_session": ao, "title": title, "thread": str(rec.get("thread") or name),
            "arc": str(rec.get("arc") or ""),
            "claude_session": str(rec.get("claude_session") or ""), "source": "chain"}


def manager(*, home: Optional[Path] = None, path: Optional[Path] = None) -> Dict[str, str]:
    """Адрес менеджера: manager.json, без него — цепочка хендоффов его нити."""
    data = read_manager_file(path)
    if data.get("ao_session"):
        return {"ao_session": data["ao_session"], "title": data.get("title", ""),
                "thread": data.get("thread", ""), "arc": "", "claude_session": "",
                "source": "manager.json"}
    ref = (os.environ.get(MANAGER_THREAD_ENV) or "").strip() or MANAGER_THREAD
    t = data.get("thread", "")
    if t:
        ref = t if "/" in t else "{0}/{1}".format(_split(ref)[0] or "management", t)
    return thread(ref, home=home)


def address(rec: Dict[str, str]) -> str:
    """Человекочитаемо: «<title> (ao_session <id>)», или то, что есть."""
    ao, title = rec.get("ao_session", ""), rec.get("title", "")
    if title and ao:
        return "{0} (ao_session {1})".format(title, ao)
    return title or ao


def manager_address(default: str = "") -> str:
    """:func:`address` менеджера; не нашёлся — *default*. Никогда не падает."""
    try:
        return address(manager()) or default
    except Exception:  # noqa: BLE001
        return default


def _cmd_whois(args) -> int:
    target = (args.target or "").strip()
    rec = manager() if target == "manager" else thread(target)
    if getattr(args, "as_json", False):
        print(json.dumps(rec, ensure_ascii=False))
    else:
        print("{0}\t{1}".format(rec.get("ao_session") or "-", rec.get("title") or "-"))
    return 0 if rec.get("ao_session") else 1


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "whois", help="who to message: the current AO id and title of the manager "
                      "or of a thread's holder (newest taken session)")
    p.add_argument("target", help="'manager', a thread, or <project>/<thread>")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="machine-readable: ao_session, title, thread, arc, source")
    p.set_defaults(func=_cmd_whois, _cmd="whois")


__all__ = ["MANAGER_FILE", "MANAGER_FILE_LEGACY", "manager_file_home", "MANAGER_THREAD", "WhoisError", "address", "manager",
           "manager_address", "read_manager_file", "register", "thread"]

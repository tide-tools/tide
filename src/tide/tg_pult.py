"""SOC-270/271: где пульт Telegram и как его звать — общее для ``tide say`` и ``tide topic``.

Пульт (tg-pult, код ao-control) живёт каталогом в проекте ``management``:
``tg-alert`` (кладёт сообщение в outbox демона), ``pult.py`` (подкоманды ``topic-*``),
``dialog.jsonl`` (журнал: что ушло, номера Telegram), ``role-topics.json`` (маршруты тем).

Каталог пульта, по порядку:

1. ``$TIDE_ALERT_CMD`` — тот же канал, что у autohandoff/perm-notify: каталог этого файла;
2. ``$TIDE_PULT_DIR``;
3. проект ``management`` из ростера + :data:`PULT_REL_DIR` (как ``tide.arc.dialog.pult_path``).

Сам Telegram отсюда не трогается: только вызовы пульта подпроцессом. Вызов — через
:func:`run` (тесты подменяют его).
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from . import paths
from .arc.stream import StreamError

ALERT_CMD_ENV = "TIDE_ALERT_CMD"
PULT_DIR_ENV = "TIDE_PULT_DIR"
PULT_PROJECT = "management"
PULT_REL_DIR = ".tide/arcs/01-@ao-control/arcs/02-tg-pult/workspace/tg-pult"

# Имена групп tide → имена пульта. Пультовы имена проходят как есть.
GROUP_ALIASES: Dict[str, str] = {
    "topics": "factory", "tide topics": "factory", "tide-topics": "factory",
    "agents": "pulse", "tide agents": "pulse", "tide-agents": "pulse",
}
GROUP_TITLES: Dict[str, str] = {
    "factory": "tide topics", "pulse": "tide agents",
    "groovamp": "Groovamp", "nightshift": "Night Shift",
}


class PultError(StreamError):
    pass


def pult_dir() -> Path:
    """Каталог пульта (см. порядок в docstring модуля); не нашёлся — :class:`PultError`."""
    raw = (os.environ.get(ALERT_CMD_ENV) or "").strip()
    if raw:
        try:
            first = shlex.split(raw)[0]
        except (ValueError, IndexError):
            first = ""
        p = Path(first).expanduser()
        if p.is_file():
            return p.resolve().parent
    raw = (os.environ.get(PULT_DIR_ENV) or "").strip()
    if raw and Path(raw).expanduser().is_dir():
        return Path(raw).expanduser()
    try:
        from . import roster

        for e in roster.read_roster(paths.control_home()):
            if e.get("name") == PULT_PROJECT:
                p = Path(e["path"]).expanduser() / PULT_REL_DIR
                if p.is_dir():
                    return p
    except Exception:  # noqa: BLE001 — нет ростера — нет пульта
        pass
    raise PultError("пульт Telegram не найден: нет ${0}, ${1} и проекта {2!r} в ростере".format(
        ALERT_CMD_ENV, PULT_DIR_ENV, PULT_PROJECT))


def alert_cmd() -> List[str]:
    """Команда tg-alert: ``$TIDE_ALERT_CMD`` (как есть), иначе ``<пульт>/tg-alert``."""
    raw = (os.environ.get(ALERT_CMD_ENV) or "").strip()
    if raw:
        try:
            return shlex.split(raw)
        except ValueError:
            pass
    return [str(pult_dir() / "tg-alert")]


def pult_python(d: Optional[Path] = None) -> List[str]:
    """Интерпретатор пульта + ``pult.py``: venv пульта (там telethon), иначе python3."""
    d = d or pult_dir()
    py = d / "venv" / "bin" / "python"
    return [str(py) if py.exists() else "python3", str(d / "pult.py")]


def dialog_file(d: Optional[Path] = None) -> Path:
    return (d or pult_dir()) / "dialog.jsonl"


def group(name: str) -> str:
    """tide-имя группы → имя пульта (``topics`` → factory, ``agents`` → pulse)."""
    g = " ".join(str(name or "").split()).lower()
    if not g:
        raise PultError("назови группу: topics | agents (или имя пульта: factory, pulse, groovamp, nightshift)")
    return GROUP_ALIASES.get(g, g)


def group_label(g: str) -> str:
    t = GROUP_TITLES.get(g)
    return "{0} ({1})".format(t, g) if t else g


def caller_source() -> str:
    """``<проект>/<нить>/<сессия>`` вызывающего для ``TIDE_ALERT_SOURCE``.

    Своя сессия — как у ``tide report`` (sid/AO); не определилась — ``<проект>/<AO-сессия>``,
    вне проекта — ``$AO_SESSION_ID`` или ``""``.
    """
    ao = (os.environ.get("AO_SESSION_ID") or "").strip()
    try:
        root = paths.find_tide_root()
    except Exception:  # noqa: BLE001
        root = None
    if root is not None:
        try:
            from .hooks.context_watch import alert_source
            from .launcher.handoff_report import current_entry

            entry = current_entry(root)
            if entry is not None:
                return alert_source(root, entry)
        except Exception:  # noqa: BLE001 — источник — подпись, не повод падать
            pass
        return "{0}/{1}".format(root.name, ao) if ao else root.name
    return ao


def run(cmd: List[str], *, env: Optional[Dict[str, str]] = None, input_text: Optional[str] = None,
        cwd: Optional[Path] = None, timeout: float = 60) -> subprocess.CompletedProcess:
    """Подпроцесс пульта (единственная точка — тесты подменяют её)."""
    try:
        return subprocess.run(cmd, env=env, input=input_text if input_text is not None else "",
                              cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise PultError("пульт не ответил за {0:g} с: {1}".format(timeout, " ".join(cmd[:3])))
    except OSError as exc:
        raise PultError("пульт не запустился ({0}): {1}".format(exc, cmd[0]))

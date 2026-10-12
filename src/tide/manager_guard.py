"""SOC-232: в AO-сессию менеджера ничего не впечатывается автоматически.

Всё, что tide хотел сказать менеджеру через ``ao send``, остаётся в журнале: событие
``suppressed-to-manager`` в почтовом ``inbox/log.jsonl`` нити менеджера (запасной
путь — ``~/.tide/suppressed-to-manager.jsonl``). Менеджер читает это read-only.

Менеджер — ``tide whois manager`` (``ao_session``) ИЛИ имя сессии ``management-*``
(на случай устаревшего ``manager.json``). Ничего здесь не падает."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import paths

MANAGER_PREFIX = "management-"
FALLBACK_LOG = "suppressed-to-manager.jsonl"
EVENT = "suppressed-to-manager"
MANAGER_MAIL_REF = "management/main"


def _manager_ao() -> str:
    try:
        from . import whois

        return str(whois.manager().get("ao_session") or "").strip()
    except Exception:  # noqa: BLE001 — нет адреса менеджера: решает запасной префикс
        return ""


def is_manager_session(handle: Optional[str]) -> bool:
    """*handle* (AO-сессия) — сессия менеджера: ``ao_session`` из ``tide whois manager``
    или имя с ним как префиксом (``<ao>-4b``). Префикс ``management-`` — только запас,
    когда whois адреса не дал (у других нитей проекта management тоже ``management-N``)."""
    h = (handle or "").strip()
    if not h:
        return False
    mgr = _manager_ao()
    if mgr:
        return h == mgr or h.startswith(mgr + "-")
    return h.startswith(MANAGER_PREFIX)


def is_manager_self() -> bool:
    """Этот процесс — сам менеджер (``$AO_SESSION_ID`` совпадает с его ao_session)."""
    me = (os.environ.get("AO_SESSION_ID") or "").strip()
    mgr = _manager_ao()
    return bool(me and mgr and me == mgr)


def log_suppressed(handle: str, text: str, *, source: str = "") -> None:
    """Записать подавленное сообщение в журнал (почта менеджера, иначе файл)."""
    ev = {"ev": EVENT, "type": EVENT, "to": handle, "text": text, "source": source,
          "at": datetime.now().isoformat(timespec="seconds")}
    try:
        from .arc.work import thread_dir

        tdir = thread_dir(paths.find_tide_root() or Path("."), MANAGER_MAIL_REF)
        if tdir is None:
            raise OSError("нет нити менеджера")
        log = Path(tdir) / "inbox" / "log.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        return
    except Exception:  # noqa: BLE001 — нить не нашли: в файл
        pass
    try:
        home = Path(os.environ.get("TIDE_HOME") or Path.home() / ".tide")
        home.mkdir(parents=True, exist_ok=True)
        with (home / FALLBACK_LOG).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except OSError:
        pass


def suppress_if_manager(handle: str, text: str, *, source: str = "") -> bool:
    """Менеджер → записать в журнал и вернуть True (слать не надо)."""
    if not is_manager_session(handle):
        return False
    log_suppressed(handle, text, source=source)
    return True

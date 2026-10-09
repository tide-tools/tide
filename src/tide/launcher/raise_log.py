"""tide.launcher.raise_log — журнал подъёма: КАК поднялась сессия по офферу (SOC-121).

Раньше оффер помнил только «когда»: ``reserved-at`` / ``taken-at``. Каким путём
поднялась новая сессия — ▶ доски, ``tide pickup`` руками, автохендофф сразу или
автоподъём через N мин — нигде не оставалось, и аудитор не мог этого доказать.

Теперь каждый подъём (одна дверь — :func:`tide.launcher.menu.launch_handoff`)
пишет:

* в оффер поля ``raised-by:`` (:data:`AUTO` | :data:`AUTORAISE` | :data:`BOARD` |
  :data:`PICKUP`) и ``raised-at:``;
* строку в ``.tide/state/raises.log`` control-home (соседи — ``alerts.log``,
  ``forwarded.log``): ``ISO · оффер · raised-by · откуда · ao-id``.

Автоподъём пишет туда же своё ожидание и решение («autoraise: жду N сек»,
«autoraise: оффер взят — выхожу»). Журнал — уведомление, не шаг: ничего здесь
не падает.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from .. import paths

RAISES_LOG = "raises.log"
RAISED_BY = "raised-by"
RAISED_AT = "raised-at"

AUTO = "auto"            # автохендофф поднял сразу (run_handoff spawn → spawn_offer)
AUTORAISE = "autoraise"  # вставшая нить поднята сама через N мин (run_autoraise)
BOARD = "board"          # ▶ человека: кнопка доски или выбор в tide menu
PICKUP = "pickup"        # tide pickup <оффер> руками
KINDS = (AUTO, AUTORAISE, BOARD, PICKUP)

SEP = " · "


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def log_path(home: Path) -> Path:
    return paths.state_dir(Path(home)) / RAISES_LOG


def log(home: Path, *parts: str) -> Optional[str]:
    """Дописать строку ``ISO · part · part …`` в ``state/raises.log`` → строка|None."""
    line = SEP.join([_now()] + [str(p) for p in parts if str(p or "").strip()])
    try:
        path = log_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        return None
    return line


def origin(record: Dict) -> str:
    """«откуда»: сессия-источник и нить оффера."""
    sid = str(record.get("from_session") or "").strip()
    arc = str(record.get("arc") or "").strip()
    thread = arc.split("/", 1)[0] if arc and arc != "-" else ""
    bits = []
    if sid and sid != "-":
        bits.append("из сессии {0}".format(sid))
    if thread:
        bits.append("нить {0}".format(thread))
    return " ".join(bits) or "источник неизвестен"


def how(raised_by: str, *, after: Optional[int] = None, via: str = "") -> str:
    """Способ подъёма словами — для стартового промпта новой сессии."""
    if raised_by == AUTO:
        return "поднята автохендоффом сразу"
    if raised_by == AUTORAISE:
        mins = max(0, int(after or 0)) // 60
        return "поднята сама через {0} мин — никто не ответил на стоп".format(mins)
    if raised_by == PICKUP:
        return "tide pickup"
    if via:
        return "▶ в {0}".format(via)
    return "▶ с доски"


def note_raise(home: Path, record: Dict, raised_by: str, *, handle: str = "",
               adapter: str = "", ok: bool = True, detail: str = "",
               via: str = "") -> Optional[str]:
    """Подъём состоялся (или нет): поля в оффере + строка журнала. Никогда не падает."""
    from .. import handoff_queue as hq

    key = str(record.get("name") or "")
    if ok:
        try:
            path = Path(record.get("path") or hq._resolve(Path(home), key)["path"])
            hq._set_field(path, RAISED_BY, raised_by)
            hq._set_field(path, RAISED_AT, _now())
        except Exception:  # noqa: BLE001 — журнал не ломает подъём
            pass
    h = str(handle or "").strip()
    if not ok:
        who = "не поднялась: {0}".format(detail or "без подробностей")
    elif h:
        who = "{0} {1}".format("ao" if adapter == "ao" else (adapter or "handle"), h)
    else:
        who = "ao-id неизвестен"
    by = raised_by if not via else "{0} ({1})".format(raised_by, via)
    return log(home, key, by, origin(record), who)


__all__ = ["AUTO", "AUTORAISE", "BOARD", "KINDS", "PICKUP", "RAISED_AT", "RAISED_BY",
           "RAISES_LOG", "how", "log", "log_path", "note_raise", "origin"]

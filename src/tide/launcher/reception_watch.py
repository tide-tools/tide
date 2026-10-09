"""tide.launcher.reception_watch — сторож приёма: поднятая новая не молчит вечно (SOC-143).

Живость принимающей (SOC-101 F25, :func:`tide.launcher.handoff._await_alive`)
проверял только автохендофф — синхронно, внутри ``spawn_offer``. Подъём руками
(``tide pickup``), ▶ доски, ``tide menu`` и автоподъём ничего не ждали: оффер
288-04-designer 09.10 поднят ``tide pickup`` в AO app-development-31, агент так и
не стартовал (claude-id пуст, статус exited), ``taken-at`` не встал — а резерв
висел «поднимается», передающая ждала ~2,5 ч, менеджер не знал.

Теперь каждый настоящий подъём заводит отсоединённый сторож
(``tide handoffs watch-reception``), он ждёт в два шага:

1. первый сигнал агента (тот же ``_await_alive``: 90 с, один resume-agent, ещё 90 с)
   — пропускается, если автохендофф уже дождался его сам;
2. подтверждение приёма (оффер ушёл из ``offered``) за :data:`ACCEPT_TIMEOUT`
   (``$TIDE_HANDOFF_ACCEPT_TIMEOUT``); агент умер раньше — сразу сигнал.

Сигнал (:func:`signal_failed`) — один на неудачу: резерв снят (если он всё ещё
этого подъёма), строка «не ожила» в ``state/raises.log``, пульс в паспорт
передающей и ``ao send`` ей, менеджеру ``ao send``, Грише одно сообщение
(как и раньше у автохендоффа). Ничего здесь не падает.

Выключатель: ``$TIDE_RECEPTION_WATCH=off`` (тесты держат его выключенным).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

WATCH_ENV = "TIDE_RECEPTION_WATCH"
ACCEPT_TIMEOUT = 600.0                    # секунд от первого сигнала до приёма
ACCEPT_TIMEOUT_ENV = "TIDE_HANDOFF_ACCEPT_TIMEOUT"
ACCEPT_POLL = 10.0

DIED = "died"
NOT_ACCEPTED = "not-accepted"

# подменяются в тестах: время и сон не настоящие
_clock = None
_sleep = None


def enabled() -> bool:
    raw = (os.environ.get(WATCH_ENV) or "").strip().lower()
    return raw not in ("0", "off", "no", "false", "нет")


def _accept_timeout(given: Optional[float]) -> float:
    if given is not None:
        return max(0.0, float(given))
    raw = (os.environ.get(ACCEPT_TIMEOUT_ENV) or "").strip()
    try:
        return max(0.0, float(raw)) if raw else ACCEPT_TIMEOUT
    except ValueError:
        return ACCEPT_TIMEOUT


# --- запуск сторожа --------------------------------------------------------------

def _spawn_watch(home: Path, key: str, handle: str, *, adapter: str, sid: str,
                 raised_by: str, skip_alive: bool) -> Optional[int]:
    """Отсоединённый ``tide handoffs watch-reception`` (своя сессия процессов —
    переживёт и того, кто жал ▶, и передающую) → pid."""
    argv = [sys.executable, "-m", "tide", "handoffs", "watch-reception", key,
            "--handle", handle, "--home", str(home), "--by", raised_by or "-"]
    if adapter:
        argv += ["--adapter", adapter]
    if sid:
        argv += ["--sid", sid]
    if skip_alive:
        argv.append("--skip-alive")
    proc = subprocess.Popen(argv, cwd=str(home), start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, close_fds=True)
    return proc.pid


def schedule(home: Path, key: str, handle: str, *, adapter, sid: str = "",
             raised_by: str = "", skip_alive: bool = False) -> Optional[int]:
    """Завести сторож приёма → pid | None (выключен, адаптер без пробы, нет AO-id).

    Адаптер без пробы живости (orca, tmux) — как раньше: без сторожа."""
    if not enabled() or not str(handle or "").strip():
        return None
    if not callable(getattr(adapter, "first_signal", None)):
        return None
    try:
        return _spawn_watch(Path(home), key, str(handle).strip(),
                            adapter=str(getattr(adapter, "name", "") or ""),
                            sid=str(sid or "").strip(), raised_by=raised_by,
                            skip_alive=skip_alive)
    except Exception:  # noqa: BLE001 — сторож не смеет сорвать подъём
        return None


# --- сигнал о несостоявшемся приёме -----------------------------------------------

def manager_text(key: str, handle: str, detail: str, project: str = "") -> str:
    where = " ({0})".format(project) if project and project != "-" else ""
    return ("tide: приём хендоффа {0}{1} не состоялся (новая {2}): {3}. Нить остаётся у "
            "передающей; оффер снова свободен: tide pickup {0}".format(
                key, where, handle or "сессия", (detail or "сигнала нет").rstrip(".")))


def signal_failed(home: Path, key: str, *, detail: str, handle: str = "",
                  sid: str = "", raised_by: str = "", tell_origin_ao: bool = True
                  ) -> Dict[str, object]:
    """Приём не состоялся: снять резерв, записать, сказать передающей, менеджеру,
    Грише. Никогда не падает. → что сделано (для журнала/тестов)."""
    from .. import handoff_queue as hq
    from . import autohandoff, handoff, raise_log, reception

    home = Path(home)
    out: Dict[str, object] = {"unreserved": False, "origin_ao": None, "manager": None,
                              "human": None}
    try:
        rec = hq._resolve(home, key)
    except Exception:  # noqa: BLE001 — оффера нет: сказать некому и не о чем
        return out
    if rec.get("status") != hq.STATUS_OFFERED:
        return out  # уже принят/снят — не тревога
    reserved = str(rec.get("pickup_session") or "").strip()
    if not sid or reserved in ("", "-") or reserved == sid:
        try:
            out["unreserved"] = hq.unreserve(home, key) is not None
        except Exception:  # noqa: BLE001
            pass
    raise_log.log(home, key, raised_by or "-", "не ожила: {0}".format(
        detail or "сигнала нет"))
    handoff._tell_origin(home, key, handoff.NOT_ALIVE_LINE)
    if tell_origin_ao:
        try:
            from ..arc import holder
            origin = holder.ao_handle_for(home, str(rec.get("from_session") or ""))
        except Exception:  # noqa: BLE001
            origin = ""
        if origin and origin != handle:
            out["origin_ao"] = {"to": origin, "code": autohandoff._ao_send(
                origin, handoff.not_alive_line({"detail": detail}, key))}
    out["manager"] = autohandoff.tell_manager(
        manager_text(key, handle, detail, str(rec.get("project") or "")))
    out["human"] = reception.announce_not_alive(home, key)
    return out


# --- сам сторож ------------------------------------------------------------------

def _offer_state(home: Path, key: str, sid: str) -> str:
    """offered | done (принят/снят/нет) | superseded (резерв уже чужой)."""
    from .. import handoff_queue as hq
    try:
        rec = hq._resolve(home, key)
    except Exception:  # noqa: BLE001 — оффер исчез
        return "done"
    if rec.get("status") != hq.STATUS_OFFERED:
        return "done"
    reserved = str(rec.get("pickup_session") or "").strip()
    if sid and reserved != sid:
        return "superseded"
    return "offered"


def watch(home: Path, key: str, handle: str, *, adapter_name: Optional[str] = None,
          sid: str = "", raised_by: str = "", skip_alive: bool = False,
          live_timeout: Optional[float] = None,
          accept_timeout: Optional[float] = None) -> Dict[str, object]:
    """Ждать приёма оффера *key*, поднятого в *handle*; не дождались — :func:`signal_failed`.

    → ``{"action": accepted|superseded|not-alive|died|not-accepted, ...}``."""
    import time

    from . import handoff, pickup

    home = Path(home)
    clock = _clock or time.monotonic
    sleep = _sleep or time.sleep
    try:
        adapter = pickup.get_adapter(adapter_name or None)
    except Exception:  # noqa: BLE001
        return {"action": "skip", "detail": "адаптер не найден"}
    out: Dict[str, object] = {"key": key, "handle": handle}
    if not skip_alive:
        alive = handoff._await_alive(home, key, {"ok": True, "action": "spawned",
                                                 "handle": handle,
                                                 "adapter": adapter_name or ""},
                                     adapter_name=adapter_name, timeout=live_timeout)
        if alive.get("action") == handoff.NOT_ALIVE:
            if _offer_state(home, key, sid) != "offered":
                return dict(out, action="superseded")
            out.update(action=handoff.NOT_ALIVE, detail=alive.get("detail") or "")
            out["signal"] = signal_failed(home, key, detail=str(out["detail"]),
                                          handle=handle, sid=sid, raised_by=raised_by)
            return out
    limit = _accept_timeout(accept_timeout)
    deadline = clock() + limit
    probe = getattr(adapter, "alive", None)
    while True:
        state = _offer_state(home, key, sid)
        if state == "done":
            return dict(out, action="accepted")
        if state == "superseded":
            return dict(out, action="superseded")
        dead = False
        if callable(probe):
            try:
                dead = not probe(handle)
            except Exception:  # noqa: BLE001 — шаткая проба — «ещё не знаю»
                dead = False
        if dead:
            out.update(action=DIED, detail="AO {0} умерла (exited), не подтвердив приём"
                       .format(handle))
            break
        if clock() >= deadline:
            out.update(action=NOT_ACCEPTED,
                       detail="AO {0} не подтвердила приём за {1:g} мин".format(
                           handle, limit / 60.0))
            break
        sleep(ACCEPT_POLL)
    out["signal"] = signal_failed(home, key, detail=str(out["detail"]), handle=handle,
                                  sid=sid, raised_by=raised_by)
    return out


def _cmd_watch(args) -> int:
    import json

    from .. import paths as _paths

    home = Path(args.home) if args.home else _paths.control_home()
    by = "" if args.by in (None, "-") else args.by
    out = watch(home, args.key, args.handle, adapter_name=args.adapter, sid=args.sid or "",
                raised_by=by, skip_alive=bool(args.skip_alive))
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


def register(hsub) -> None:
    """Скрытая ``tide handoffs watch-reception`` — её запускает :func:`schedule`."""
    ap = hsub.add_parser("watch-reception")
    ap.add_argument("key")
    ap.add_argument("--handle", required=True)
    ap.add_argument("--home", default=None)
    ap.add_argument("--adapter", default=None)
    ap.add_argument("--sid", default="")
    ap.add_argument("--by", default="-")
    ap.add_argument("--skip-alive", action="store_true", dest="skip_alive")
    ap.set_defaults(func=_cmd_watch, _cmd="handoffs watch-reception")
    hsub._choices_actions = [a for a in hsub._choices_actions
                             if a.dest != "watch-reception"]


__all__ = ["ACCEPT_TIMEOUT", "ACCEPT_TIMEOUT_ENV", "DIED", "NOT_ACCEPTED", "WATCH_ENV",
           "enabled", "manager_text", "register", "schedule", "signal_failed", "watch"]

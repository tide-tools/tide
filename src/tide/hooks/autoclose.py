"""tide.hooks.autoclose — старая сессия закрывается сама, когда ей больше нечего делать
(SOC-101, слово менеджера; ВЫКЛЮЧЕНО по умолчанию до живой проверки на паре).

После приёма оффера (``taken-at``) нить ведёт новая, а старая только доводит своих
фоновых воркеров и пересылает их итоги новой. Когда воркеров нет и все итоги
пересланы, старая висит зря — её закрываем механически: ``ao session kill <AO-id>``.

Условия закрытия (все сразу):

* оффер ``continue`` принят, и старая по-прежнему отдавшая
  (:func:`tide.handoff_queue.is_dissolved` — та же сессия позже не взяла другую нить);
* принят не раньше :data:`LOOKBACK_HOURS` часов назад (старые пары не трогаем);
* у старой и новой разные AO-сессии, AO-сессия старой известна и не закрыта;
* новая — принявшая этот оффер (``taken-by``), её AO-сессия известна и жива;
* в транскрипте старой нет живых фоновых воркеров — тот же признак, что у блока
  сида «в работе прямо сейчас» (:func:`tide.launcher.seed_draft.background_workers`);
  и ни один сабагент не сдал лишь промежуточный итог — последнее уведомление
  «background work of its own still running … may be interim» или письмо ему после
  итога = воркер жив (:func:`interim_workers`, SOC-143);
* каждый итог воркера, пришедший старой после передачи (``<task-notification>``),
  она потом переслала новой — ход старой, где назван AO-id новой (``ao send``,
  SendMessage); и в ``state/forward-pending.jsonl`` нет писем этой пары;
* старая молчит не меньше :data:`QUIET_MIN` минут (не посреди хода).

Перед закрытием: строка в ``state/autoclose.log`` (рядом с ``raises.log``), строка
менеджеру (``send_alert(route=manager)``, нет команды — ``ao send`` менеджеру по
whois) и последнее сообщение новой «старая закрыта в ЧЧ:ММ». Одна пара — одно
закрытие (ключ оффера в журнале).

Включатель — ``$TIDE_AUTOCLOSE_OLD=1`` (все пары) или ``tide handoffs autoclose
--offer <id>`` (одна пара, SOC-143). Пруф без действий —
``tide handoffs autoclose --dry-run [--offer <id>]``: кого закрыл бы и почему / почему нет.
Новая пары — принявшая этот оффер (``taken-by`` → AO-id), не нынешняя голова нити.
Срабатывает на хуке ``handoff-confirm`` (UserPromptSubmit) любой сессии, кроме
самой старой (её закрывают со стороны, не изнутри её же хода), не чаще раза в
:data:`THROTTLE_SEC` секунд. Никогда не падает.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from .. import paths
# Признаки воркеров по транскрипту — общие с приёмом и сидом (SOC-143, слои):
# живут в tide.arc.workers, автозакрытие берёт оттуда.
from ..arc.workers import (  # noqa: F401 — часть публичного имени autoclose
    FINAL_STATUSES, ended, interim_workers, last_activity, unforwarded_results,
    rows as _rows, row_at as _at, parse_iso as _parse_iso, pending_for as _pending_for,
)

ENV = "TIDE_AUTOCLOSE_OLD"
LOG = "autoclose.log"
STAMP = "autoclose.last"
LOOKBACK_HOURS = 24
QUIET_MIN = 3
THROTTLE_SEC = 120
ROUTE_MANAGER = "manager"


def enabled(env: Optional[Dict[str, str]] = None) -> bool:
    e = os.environ if env is None else env
    return (e.get(ENV) or "").strip().lower() in ("1", "on", "yes", "true")


def _log_path(home: Path) -> Path:
    return paths.state_dir(Path(home)) / LOG


def _closed_before(home: Path, offer: str) -> bool:
    try:
        lines = _log_path(home).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    # закрытой считаем только пару с удачным kill: не вышедший kill не запирает пару
    return any(" · {0} · kill ".format(offer) in ln and ln.rstrip().endswith(" · ok")
               for ln in lines)


def journal(home: Path, *parts: str) -> Optional[str]:
    line = " · ".join([datetime.now().isoformat(timespec="seconds")]
                      + [str(p) for p in parts if str(p or "").strip()])
    try:
        path = _log_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        return None
    return line


# --- решение -------------------------------------------------------------------

def verdict(home: Path, rec: Dict[str, object], *, now: Optional[datetime] = None,
            session_of: Optional[Callable[[str], Optional[dict]]] = None,
            transcript_of: Optional[Callable[[str], Optional[Path]]] = None,
            workers_of: Optional[Callable[[Optional[Path]], List[str]]] = None
            ) -> Dict[str, object]:
    """Закрыть ли старую пары *rec*: ``{offer, old, new, old_ao, new_ao, close, why}``.
    Только чтение."""
    from .. import handoff_queue as hq
    from ..arc import holder
    from ..arc.stream import with_ao_id

    now = now or datetime.now()
    home = Path(home)
    offer = str(rec.get("name") or "?")
    old_sid = str(rec.get("from_session") or "")
    old_ao = holder.ao_handle_for(home, old_sid) if old_sid else ""
    # Новая пары — та, что приняла ЭТОТ оффер (taken-by → AO-id), а не нынешняя
    # голова нити по whois: у старых пар голова уже дальше по цепочке (SOC-143).
    new_sid = str(rec.get("taken_by") or "")
    new_ao = holder.ao_handle_for(home, new_sid) if new_sid else ""
    try:  # нынешняя голова — пересылка итогов ей тоже считается
        from .handed_off import successor
        head_ao = successor(home, rec).get("ao_session", "")
    except Exception:  # noqa: BLE001
        head_ao = ""
    forward_to = [a for a in (new_ao, head_ao) if a and a != old_ao]
    try:
        from ..launcher import reception
        new_t, old_t = reception.titles(home, rec)
    except Exception:  # noqa: BLE001
        new_t, old_t = "новая сессия", "старая сессия"
    out: Dict[str, object] = {
        "offer": offer, "old_ao": old_ao, "new_ao": new_ao,
        "old": with_ao_id(old_t, old_ao) if old_ao else old_t,
        "new": with_ao_id(new_t, new_ao) if new_ao else new_t,
        "close": False, "why": ""}

    def no(why: str) -> Dict[str, object]:
        out["why"] = why
        return out

    if (hq.is_dissolved(home, old_sid) or {}).get("name") != rec.get("name"):
        return no("старая потом взяла другую нить — она снова голова, не закрываю")
    if _closed_before(home, offer):
        return no("уже закрыта автозакрытием (state/{0})".format(LOG))
    taken = _parse_iso(rec.get("taken_at"))
    if taken is None:
        return no("нет taken-at — приём не подтверждён")
    if now - taken > timedelta(hours=LOOKBACK_HOURS):
        return no("принят больше {0} ч назад — старые пары не трогаю".format(LOOKBACK_HOURS))
    if not old_ao:
        return no("AO-сессия старой неизвестна — закрыть нечем")
    if not new_ao:
        return no("AO-сессия принявшей оффер неизвестна — некому сообщить, не закрываю")
    if old_ao == new_ao:
        return no("старая и новая в одной AO-сессии {0} — закрыть нельзя".format(old_ao))
    if session_of is None:
        from ..adapters.ao import AoAdapter
        session_of = AoAdapter().session
    s = session_of(old_ao)
    if not s:
        return no("AO не знает {0} — уже закрыта или AO недоступен".format(old_ao))
    gone = ended(s)
    if gone:
        out["gone"] = gone
        return no("старая {0} уже вышла (AO: {1}) — kill не нужен".format(old_ao, gone))
    n = session_of(new_ao)
    if not n or ended(n):
        return no("новая {0} не жива (AO: {1}) — старую не закрываю".format(
            new_ao, "закрыта" if n else "не знает"))
    if transcript_of is None:
        from ..launcher.seed_draft import transcript_path
        transcript_of = transcript_path
    tr = transcript_of(old_sid)
    if tr is None:
        return no("транскрипт старой не найден — воркеров не проверить, не закрываю")
    if workers_of is None:
        from ..launcher.seed_draft import background_workers
        workers_of = background_workers
    live = workers_of(tr)
    if live:
        return no("воркеры старой живы ({0}): {1} — жду".format(
            len(live), "; ".join(ln.lstrip("- ").split(" — ", 1)[0] for ln in live[:3])))
    interim = interim_workers(tr)
    if interim:
        return no("воркеры старой не сдали итог (промежуточный/возобновлён) ({0}): {1} — "
                  "жду".format(len(interim), "; ".join(interim[:3])))
    raised = _parse_iso(rec.get("raised_at"))
    since = min(t for t in (taken, raised) if t is not None)
    left = unforwarded_results(tr, since, forward_to)
    if left:
        return no("итоги воркеров не пересланы новой: {0} — жду".format(", ".join(left[:3])))
    queued = _pending_for(home, offer)
    if queued:
        return no("в очереди пересылки {0} писем этой пары — жду".format(queued))
    last = last_activity(tr)
    if last is not None and now - last < timedelta(minutes=QUIET_MIN):
        return no("старая работала {0} мин назад — жду тишины {1} мин".format(
            int((now - last).total_seconds() // 60), QUIET_MIN))
    out["close"] = True
    out["why"] = "воркеров нет, итоги пересланы, тихо с {0}".format(
        last.strftime("%H:%M") if last else "?")
    return out


def offer_matches(rec: Dict[str, object], offer: str) -> bool:
    """*offer* — полное имя («299-30-handoffs») или номер («299»)."""
    name = str(rec.get("name") or "")
    want = str(offer or "").strip()
    return bool(want) and (name == want or name.split("-", 1)[0] == want)


def candidates(home: Path, *, now: Optional[datetime] = None,
               offer: Optional[str] = None) -> List[Dict[str, object]]:
    """Принятые офферы ``continue`` за :data:`LOOKBACK_HOURS` ч — свежие сверху;
    *offer* — только этот оффер."""
    from .. import handoff_queue as hq

    now = now or datetime.now()
    out = []
    for r in hq.list_offers(Path(home), status=hq.STATUS_TAKEN):
        if offer and not offer_matches(r, offer):
            continue
        if not hq._transfers_thread(r):
            continue
        t = _parse_iso(r.get("taken_at"))
        if t is None or now - t > timedelta(hours=LOOKBACK_HOURS):
            continue
        out.append(r)
    out.sort(key=lambda r: str(r.get("taken_at") or ""), reverse=True)
    return out


# --- действие ------------------------------------------------------------------

def manager_text(v: Dict[str, object], at: str) -> str:
    return ("tide: автозакрытие — {0} передала нить {1} (оффер {2}); {3}. Закрываю "
            "старую в {4}: ao session kill {5}".format(
                v["old"], v["new"], v["offer"], v["why"], at, v["old_ao"]))


def new_text(v: Dict[str, object], at: str) -> str:
    return ("tide: старая {0} закрыта в {1} — воркеров у неё нет, итоги пересланы тебе. "
            "Нить целиком твоя.".format(v["old"], at))


def new_text_gone(v: Dict[str, object]) -> str:
    """Kill не вышел, но старая уже вышла сама — «закрыта» не говорим (SOC-143)."""
    return ("tide: старая {0} уже вышла сама (AO: {1}), закрывать не пришлось — воркеров "
            "нет, итоги пересланы. Нить целиком твоя.".format(v["old"], v.get("gone") or "?"))


def manager_text_gone(v: Dict[str, object]) -> str:
    return ("tide: автозакрытие — {0} передала нить {1} (оффер {2}); kill не вышел ({3}), "
            "но старая уже вышла сама (AO: {4}) — закрывать не пришлось.".format(
                v["old"], v["new"], v["offer"], v.get("kill_error") or "?",
                v.get("gone") or "?"))


def manager_text_failed(v: Dict[str, object]) -> str:
    return ("tide: автозакрытие не вышло: {0}, старая {1} жива — закрой руками "
            "(ao session kill {2}) или разберись. Оффер {3}, новая {4}.".format(
                v.get("kill_error") or "?", v["old"], v["old_ao"], v["offer"], v["new"]))


def _alert_manager(text: str, source: str) -> None:
    from ..launcher import autohandoff as _ah
    if _ah.send_alert(text, route=ROUTE_MANAGER, source=source) is None:
        _ah.tell_manager(text)


def _kill(handle: str):
    """``ao session kill`` → ``(ok, причина)``; причина — слова ao (stderr/код)."""
    from ..adapters import ao
    try:
        ao._call(["session", "kill", handle], timeout=60)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc) or type(exc).__name__
    return True, ""


def close(home: Path, v: Dict[str, object], *, say=None, alert=None, kill=None,
          now: Optional[datetime] = None,
          session_of: Optional[Callable[[str], Optional[dict]]] = None) -> bool:
    """Журнал → ``ao session kill`` → тексты по исходу (SOC-143: «закрыта» — только
    после удачного kill). Kill вышел — менеджеру и новой «закрыта». Не вышел —
    причина в журнал и в *v* (``kill_error``), статус старой спрашиваем заново:
    уже вышла (``gone``) — новой «вышла сама, закрывать не пришлось»; жива —
    новой ничего, менеджеру «автозакрытие не вышло, закрой руками»."""
    at = (now or datetime.now()).strftime("%H:%M")
    offer = str(v["offer"])
    journal(home, offer, "закрываю", str(v["old_ao"]), "→ " + str(v["new_ao"]),
            str(v["why"]))
    res = (kill or _kill)(str(v["old_ao"]))
    ok, why = (res if isinstance(res, tuple) else (bool(res), ""))
    gone = ""
    if ok:
        journal(home, offer, "kill " + str(v["old_ao"]), "ok")
    else:
        v["kill_error"] = why or "ao не сказал причину"
        try:
            if session_of is None:
                from ..adapters.ao import AoAdapter
                session_of = AoAdapter().session
            gone = ended(session_of(str(v["old_ao"])))
        except Exception:  # noqa: BLE001
            gone = ""
        if gone:
            v["gone"] = gone
        journal(home, offer, "kill " + str(v["old_ao"]), "НЕ ВЫШЛО",
                "причина: " + str(v["kill_error"]),
                "старая уже вышла (AO: {0})".format(gone) if gone else "старая всё ещё жива")
    if ok:
        to_manager, to_new = manager_text(v, at), new_text(v, at)
    elif gone:
        to_manager, to_new = manager_text_gone(v), new_text_gone(v)
    else:
        to_manager, to_new = manager_text_failed(v), None
    try:
        (alert or _alert_manager)(to_manager, "{0}/autoclose".format(offer))
    except Exception:  # noqa: BLE001
        pass
    if to_new is not None:
        try:
            if say is None:
                from ..adapters.ao import AoAdapter
                say = AoAdapter().say
            say(str(v["new_ao"]), to_new)
        except Exception:  # noqa: BLE001
            pass
    return ok


def sweep(home: Path, *, dry_run: bool = False, skip: Iterable[str] = (),
          now: Optional[datetime] = None, offer: Optional[str] = None,
          **kw) -> List[Dict[str, object]]:
    """Пройти свежие пары; не *dry_run* — закрыть тех, кого можно. *skip* — claude-id
    сессий, которых не закрываем (та, в чьём хуке идём); *offer* — только эта пара."""
    skip = {s for s in skip if s}
    act = {k: kw.pop(k) for k in ("say", "alert", "kill") if k in kw}
    out = []
    seen = set()
    for rec in candidates(home, now=now, offer=offer):
        sid = str(rec.get("from_session") or "")
        if sid in seen:
            continue  # у сессии важна последняя отдача
        seen.add(sid)
        v = verdict(home, rec, now=now, **kw)
        if v.get("gone") and not dry_run and not _gone_logged(home, str(v["offer"])):
            journal(home, str(v["offer"]), "уже вышла", str(v["old_ao"]),
                    "AO: " + str(v["gone"]), "без kill")
        if v["close"] and sid in skip:
            v["close"], v["why"] = False, "это мой собственный ход — закрою со стороны"
        if v["close"] and not dry_run:
            v["closed"] = close(home, v, now=now, session_of=kw.get("session_of"), **act)
        out.append(v)
    return out


def _gone_logged(home: Path, offer: str) -> bool:
    try:
        lines = _log_path(home).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(" · {0} · уже вышла".format(offer) in ln for ln in lines)


def _throttled(home: Path) -> bool:
    p = paths.state_dir(Path(home)) / STAMP
    try:
        if p.is_file() and datetime.now().timestamp() - p.stat().st_mtime < THROTTLE_SEC:
            return True
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    except OSError:
        return True
    return False


def on_prompt(payload: Dict[str, object], *, home: Optional[Path] = None,
              env: Optional[Dict[str, str]] = None) -> List[Dict[str, object]]:
    """Хук handoff-confirm: включено — подмести пары. Никогда не падает."""
    try:
        e = os.environ if env is None else env
        if not enabled(e):
            return []
        home = Path(home) if home is not None else paths.control_home()
        if _throttled(home):
            return []
        mine = [str(payload.get("session_id") or ""),
                (e.get("CLAUDE_CODE_SESSION_ID") or "").strip()]
        return sweep(home, skip=mine)
    except Exception:  # noqa: BLE001 — хук не смеет уронить сессию
        return []


def render(v: Dict[str, object], *, dry_run: bool) -> str:
    if v.get("close"):
        head = "закрыл бы" if dry_run else (
            "закрыла" if v.get("closed") else "пыталась закрыть (kill не вышел)")
    else:
        head = "не закрываю"
    text = "{0} {1}: старая {2} → новая {3}\n  почему: {4}".format(
        v["offer"], head, v["old"], v["new"], v["why"])
    if v.get("close") and not dry_run and not v.get("closed"):
        text += "\n  kill: {0}".format(v.get("kill_error") or "?")
        text += ("\n  старая уже вышла (AO: {0})".format(v["gone"]) if v.get("gone")
                 else "\n  старая всё ещё жива по AO")
    return text


def cmd_autoclose(args) -> int:
    """``tide handoffs autoclose [--dry-run] [--offer <id>]``.

    Без ``--offer`` живой запуск закрывает только при ``$TIDE_AUTOCLOSE_OLD=1``;
    ``--offer`` — явный включатель на одну пару (остальные не трогаются)."""
    home = paths.control_home()
    dry = bool(getattr(args, "dry_run", False))
    offer = (getattr(args, "offer", None) or "").strip() or None
    if not dry and not offer and not enabled():
        print("tide: автозакрытие выключено (${0}=1 — включить, или --offer <id> — "
              "на одну пару); смотри --dry-run".format(ENV))
        dry = True
    res = sweep(home, dry_run=dry, offer=offer)
    if not res:
        if offer:
            print("tide: оффер {0} — не среди свежих принятых continue-пар (за {1} ч)".format(
                offer, LOOKBACK_HOURS))
        else:
            print("tide: свежих принятых пар нет (за {0} ч)".format(LOOKBACK_HOURS))
    for v in res:
        print(render(v, dry_run=dry))
    return 0


def register_autoclose(hsub) -> None:
    ap = hsub.add_parser("autoclose", help="close the old session of a taken handoff when "
                         "it has no live workers (TIDE_AUTOCLOSE_OLD=1; --dry-run shows)")
    ap.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="only print whom it would close and why / why not")
    ap.add_argument("--offer", default=None, metavar="ID",
                    help="only this offer (299 or 299-30-handoffs); without --dry-run "
                         "closes this one pair even when TIDE_AUTOCLOSE_OLD is off")
    ap.set_defaults(func=cmd_autoclose, _cmd="handoffs autoclose")


__all__ = ["ENV", "LOG", "candidates", "ended", "interim_workers", "offer_matches", "close", "cmd_autoclose", "enabled", "on_prompt",
           "register_autoclose", "render", "sweep", "unforwarded_results", "verdict"]

"""tide.hooks.context_watch — Stop-хук ``tide hook context-watch``: сторож контекста.

Сжатие tide не делает (вердикт Гриши 09.10): переполненная сессия ПЕРЕДАЁТ нить
свежей. Сама сессия о контексте не думает — её пингует хук. Порог передачи — по
слову Гриши «Давай А» (SOC-143, 10.10; заменил лестницу 200/300/500):

* ``SOFT`` (250 000) — порог передачи контракта сида
  (:data:`tide.handoff_contract.HANDOFF_TOKENS`): с этого момента сессия передаёт
  работу в ПЕРВЫЙ СПОКОЙНЫЙ МОМЕНТ — воркеры/сабагенты сдали, правки закоммичены.
  Спокойно ли — по транскрипту: живые сабагенты (:func:`busy_workers` — тот же
  признак, что у автозакрытия и сида, :mod:`tide.arc.workers`). Спокойно — пинг
  передачи с тремя шагами и ``tide handoff continue --auto``; занята — короткий
  пинг «передай в первый спокойный момент», ступень не засчитана (``ctx-ping:
  250w``): на следующих остановках хук смотрит снова, повтор пинга — не чаще раза
  в ``$TIDE_CTX_CALM_REMIND_MIN`` минут, спокойно — пинг передачи;
* ``HARD`` (300 000) — жёсткий потолок: передача обязательна сейчас, спокойно или
  нет — «доведи текущий шаг, новую работу не бери, хендофф сейчас»;
* ``ALARM`` (500 000) — тревога сверх потолка: сессия ещё раз получает требование
  хендоффа, и менеджеру уходит сигнал (``$TIDE_ALERT_CMD`` с
  ``TIDE_ALERT_ROUTE=manager``).

На мягком и жёстком пороге тот же текст кратко уходит в ``$TIDE_ALERT_CMD`` с
``TIDE_ALERT_ROUTE=log`` (пульт только пишет событие в лог). Нет команды — молча
пропускаем. Пройденный порог запоминается в паспорте (``ctx-ping: 250|300|500``):
повторно не пингуем, скачок через несколько порогов — один пинг старшего.

Счёт — ТОЛЬКО через :mod:`tide.context_fill` (им же рисует доска). Пороги —
здесь, env: ``$TIDE_CTX_SOFT`` / ``$TIDE_CTX_HARD`` / ``$TIDE_CTX_ALARM``
(``$TIDE_CTX_NUDGE`` — старый синоним мягкого). Субагенты хендофф не делают (у них
SubagentStop). Молчит: вне tide-проекта, без арки-сессии, на продолжении после
блока (``stop_hook_active``). Растворённая или уже передавшая сессия хендофф не
получает, но контекст её считается (SOC-98): на потолке — пинг «доведи воркера,
перешли итог, замолчи», на тревоге — ещё и сигнал менеджеру «закрой её». Любая
ошибка — молчаливый exit 0.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from .. import fields, handoff_contract, offload, paths, slug

# Мягкий порог = порог передачи контракта сида: с него — передача в первый спокойный
# момент; жёсткий — потолок, передача обязательна (Гриша «Давай А», SOC-143 10.10).
SOFT_TOKENS = handoff_contract.HANDOFF_TOKENS
HARD_TOKENS = 300_000
ALARM_TOKENS = 500_000
SOFT_ENV = "TIDE_CTX_SOFT"
HARD_ENV = "TIDE_CTX_HARD"
ALARM_ENV = "TIDE_CTX_ALARM"
NUDGE_ENV = "TIDE_CTX_NUDGE"  # старое имя мягкого порога — синоним SOFT_ENV
# мягкий порог в неспокойный момент: повтор пинга «передай, как станет спокойно» — не чаще
CALM_REMIND_ENV = "TIDE_CTX_CALM_REMIND_MIN"
CALM_REMIND_MIN = 10
WAIT_MARK = "w"  # ``ctx-ping: 250w …`` — мягкий порог пройден, ждём спокойного момента

AUTO_CMD = "tide handoff continue --auto"
CTX_PING_FIELD = "ctx-ping"
HANDED_OFF_FIELD = "handed-off"  # same key launcher.autohandoff stamps

LEVELS = ("soft", "hard", "alarm")
ROUTE_LOG = "log"
ROUTE_MANAGER = "manager"


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip().replace("_", "").replace(" ", "")
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def thresholds() -> Tuple[int, int, int]:
    """``(soft, hard, alarm)`` в токенах; env переопределяет, лестница не убывает."""
    soft = _env_int(SOFT_ENV, _env_int(NUDGE_ENV, SOFT_TOKENS))
    hard = max(_env_int(HARD_ENV, HARD_TOKENS), soft)
    alarm = max(_env_int(ALARM_ENV, ALARM_TOKENS), hard)
    return soft, hard, alarm


def _k(tokens: int) -> str:
    return "{0} тыс.".format(int(round(tokens / 1000.0)))


def _kn(tokens: int) -> int:
    return int(round(tokens / 1000.0))


def _ref(entry: Path) -> str:
    return "{0}/{1}".format(slug.entry_slug(entry.parent.parent.name),
                            slug.entry_slug(entry.name))


def _offload_cmd(entry: Path) -> str:
    return ("tide offload {0} --cursor \"<где стоишь>\" --next \"<шаги через · >\" "
            "\"<что сделал>\"".format(_ref(entry)))


def _hm(at) -> str:
    return at.strftime("%d.%m %H:%M") if at else "?"


def layers_step(entry: Path) -> str:
    """Шаг 2 передачи — слои нити и «главное» одной командой ``tide thread layer``.

    SOC-101 F66: слой ожидания просим не только «если пуст» — и тогда, когда он
    правлен раньше пульса с «Ожидание Гриши:» (непустой старый слой вытесняет
    свежую цитату ПЕРЕДАЧИ в сиде). Пульс ПЕРЕДАЧА пишется шагом 1, поэтому правило
    названо и словами: та же цитата — в ``--expect``. F61: ``--main`` — если
    «главное» пусто или старше ПЕРЕДАЧИ. F65: ``--where`` заменяет «где мы»."""
    from ..arc import thread_layers

    thread = entry.parent.parent
    name = slug.entry_slug(thread.name)
    try:
        fr = thread_layers.freshness(thread)
    except Exception:  # noqa: BLE001 — шаблон не смеет упасть
        fr = {"expect": "?", "main": "?"}
    cmd = thread_layers.layer_cmd(name)
    main_cmd = thread_layers.main_cmd(name)
    main_arg = main_cmd[main_cmd.index(" --main "):]
    why = []
    if fr["expect"] == "empty":
        why.append("ожидание Гриши пусто — впиши")
    elif fr["expect"] == "stale":
        why.append("ожидание Гриши правлено {0}, а пульс с «Ожидание Гриши:» — {1}: "
                   "слой старый, он вытеснит свежую цитату — перепиши --expect".format(
                       _hm(fr.get("expect_at")), _hm(fr.get("expect_pulse_at"))))
    else:
        why.append("--expect — если ожидание в ПЕРЕДАЧЕ (шаг 1) новее слоя: та же цитата")
    if fr["main"] in ("empty", "stale"):
        cmd += main_arg
        why.append("«главное» нити {0} — --main обязателен".format(
            "пусто" if fr["main"] == "empty" else "правлено {0}, раньше ПЕРЕДАЧИ {1}".format(
                _hm(fr.get("main_at")), _hm(fr.get("pass_at")))))
    else:
        why.append("«главное» свежее — если оно старше этой ПЕРЕДАЧИ, добавь" + main_arg)
    why.append("--where заменяет «где мы» целиком (прежнее уйдёт в журнал слоёв)")
    return ("  2. слои нити (ожидание Гриши словами Гриши с датой · где мы · опыт) и "
            "«главное»:\n     {0}\n{1}").format(
                cmd, "\n".join("     · " + w for w in why))


def handoff_steps(entry: Path) -> str:
    """Три шага передачи (SOC-101 F44): пульс ПЕРЕДАЧА со всеми метками, слои нити
    и «главное» (:func:`layers_step`), автопередача. Без ПЕРЕДАЧИ сид собирается без
    карты, ожидания Гриши, «ждёт Гришу», «в полёте» и хвостов — поэтому шаблон
    готовый, а не «<что сделал>»."""
    from ..launcher import autohandoff

    return ("  1. пульс ПЕРЕДАЧА — заполни каждую метку (нечего — «нет»):\n"
            "     {0}\n"
            "{1}\n"
            "  3. {2}").format(autohandoff.pass_template(entry), layers_step(entry),
                              AUTO_CMD)


def soft_text(entry: Path, tokens: int, soft: int, hard: int) -> str:
    return (
        "tide: контекст сессии {0} токенов (порог передачи {1}), и сейчас спокойно — "
        "воркеры сдали. Передай работу сейчас: новое не начинай, правки закоммить и:\n"
        "{2}\n"
        "Новая сессия поднимется сама и продолжит с курсора. {3} — потолок: там "
        "передача обязательна."
    ).format(_k(tokens), _k(soft), handoff_steps(entry), _k(hard))


def soft_wait_text(tokens: int, soft: int, hard: int, busy: List[str]) -> str:
    return (
        "tide: контекст сессии {0} токенов (порог передачи {1}). С этого момента "
        "передай работу в первый спокойный момент: воркеры сдали, правки закоммичены → "
        "/handoff (tide handoff continue --auto). Сейчас в работе: {2}. Новое не "
        "начинай. {3} — потолок: передача обязательна."
    ).format(_k(tokens), _k(soft), "; ".join(busy[:3]) or "воркеры", _k(hard))


def busy_workers(payload: dict, session: str) -> List[str]:
    """Живые сабагенты/воркеры сессии по её транскрипту — «неспокойно» (пусто —
    спокойно). Признак один на tide: :func:`tide.launcher.seed_draft.worker_runs`
    (запуск — Agent/SendMessage, конец — итог; промежуточный итог — ещё в полёте,
    :func:`tide.arc.workers.interim_workers`) — им же сид пишет «в полёте», а
    автозакрытие судит старую. Транскрипта нет / ошибка — спокойно."""
    try:
        from .. import context_fill
        from ..launcher.seed_draft import worker_runs
        target = payload.get("transcript_path") or ""
        path = Path(target) if target else context_fill.transcript_for(session)
        if path is None or not Path(path).is_file():
            return []
        live, _done = worker_runs(Path(path))
        return ["«{0}»".format(desc or agent) for agent, desc, _at, _cwd in live]
    except Exception:  # noqa: BLE001 — сторож не смеет упасть
        return []


def hard_text(entry: Path, tokens: int, hard: int) -> str:
    return (
        "tide: контекст {0} токенов — жёсткий порог {1}. Доведи текущий шаг, новую "
        "работу не бери, хендофф сейчас:\n"
        "{2}"
    ).format(_k(tokens), _k(hard), handoff_steps(entry))


def alarm_text(entry: Path, tokens: int, alarm: int) -> str:
    return (
        "tide: ТРЕВОГА — контекст {0} токенов (порог {1}), а хендоффа нет. Менеджер "
        "получил сигнал. Передай нить немедленно, ничего больше не делая:\n"
        "{2}"
    ).format(_k(tokens), _k(alarm), handoff_steps(entry))


def _short(entry: Path, tokens: int, level: str) -> str:
    """Кратко для лога пульта (route=log): что сессия услышала."""
    what = {"soft": "порог передачи, спокойно: хендофф сейчас",
            "soft-wait": "порог передачи, воркеры в работе: хендофф в первый спокойный момент",
            "hard": "потолок: доведи шаг, хендофф сейчас"}[level]
    return "сессия {0} на {1} тыс. токенов — {2}".format(
        slug.entry_slug(entry.name), _kn(tokens), what)


def alert_source(root: Path, entry: Path) -> str:
    """``<проект>/<нить>/<сессия>`` для ``TIDE_ALERT_SOURCE``."""
    return "{0}/{1}/{2}".format(Path(root).name, slug.entry_slug(entry.parent.parent.name),
                                slug.entry_slug(entry.name))


def _pinged_rank(passport: Path, ladder: List[int]) -> int:
    """Сколько ступеней уже пропинговано (0..3) по ``ctx-ping: <тыс.> …``."""
    raw = (fields.read_field(passport, CTX_PING_FIELD) or "").strip().split(" ", 1)[0]
    try:
        k = int(raw)
    except ValueError:
        return 0
    return sum(1 for t in ladder if _kn(t) <= k)


def _send(level: str, root: Path, entry: Path, text: str) -> None:
    from ..launcher import autohandoff as _ah

    route = ROUTE_MANAGER if level == "alarm" else ROUTE_LOG
    _ah.send_alert(text, route=route, source=alert_source(root, entry))


def _session_entry(root: Path, session: str) -> Optional[Path]:
    if not session:
        return None
    return offload.find_session_by_claude_id(Path(root), session)


def _stood_down(entry: Path, session: str) -> bool:
    """Растворена (нить у преемника) или уже передала — сторожу тут делать нечего."""
    pp = entry / "arc.md"
    if (fields.read_field(pp, "dissolved") or "").strip():
        return True
    if (fields.read_field(pp, HANDED_OFF_FIELD) or "").strip():
        return True
    return bool(offload._dissolved_stand_down(session))


HANDED_TOLD_FIELD = "handed-told"


def handed_over_text(new_title: str) -> str:
    return ("tide: нить передана {0} — хендофф от тебя больше не нужен. Новое не "
            "начинай: доведи то, что уже в работе, и перешли итоги ей.".format(new_title))


# SOC-101 F49: старая после приёма жива (воркер), и новая гоняет задачи через неё.
# Stop-хук старой напоминает её роль снова — не чаще раза в N минут.
HANDED_REMIND_ENV = "TIDE_HANDED_REMIND_MIN"
HANDED_REMIND_MIN = 15


def _remind_min() -> int:
    return _env_int(HANDED_REMIND_ENV, HANDED_REMIND_MIN)


def handed_remind_text(new_title: str) -> str:
    from .handed_off import OLD_ROLE

    return ("tide: ты всё ещё работаешь, а нить ведёт {0}. Две головы на нити нельзя: "
            "ты — {1}. Задачи, которые она шлёт тебе, не бери — пусть делает сама.".format(
                new_title, OLD_ROLE))


def handed_over_reason(entry: Path, session: str, *,
                       now: Optional[datetime] = None) -> Optional[str]:
    """Сессия уже отдала нить (её оффер принят, SOC-101 F21): хендофф с неё не
    требуем, а говорим, кому передано: первый раз — сразу, дальше (F49) — снова
    не чаще раза в ``$TIDE_HANDED_REMIND_MIN`` минут, пока старая работает, и тогда
    же тревога «две головы» обеим. → текст, ``""`` (молчать), None (нить не
    отдавала — обычный сторож)."""
    try:
        from .. import handoff_queue as hq
        rec = hq.is_dissolved(paths.control_home(), session)
    except Exception:  # noqa: BLE001
        return None
    if not rec:
        return None
    now = now or datetime.now()
    pp = entry / "arc.md"
    try:
        from ..launcher import reception
        new_title = reception._title(reception._session_of(rec), "новой сессии")
    except Exception:  # noqa: BLE001
        new_title = "новой сессии"
    told = (fields.read_field(pp, HANDED_TOLD_FIELD) or "").strip()
    stamp = "{0} → {1}".format(now.isoformat(timespec="seconds"), rec.get("name"))
    if not told:
        fields.set_field(pp, HANDED_TOLD_FIELD, stamp)
        return handed_over_text(new_title)
    try:
        told_at = datetime.fromisoformat(told.split(" ", 1)[0])
    except ValueError:
        return ""
    if (now - told_at).total_seconds() < _remind_min() * 60:
        return ""
    fields.set_field(pp, HANDED_TOLD_FIELD, stamp)
    # SOC-143 F76: «две головы» — только по признаку F49 (пульс старой после
    # приёма или поручение новой + правки/сабагенты старой), а не потому, что
    # старая «ещё работает»: пересылка итога своего воркера — её законная работа.
    got = _two_heads_evidence(rec)
    if not got:
        return _forward_nudge(rec, session, new_title)
    try:
        from . import handed_off
        handed_off.two_heads_alarm(
            paths.control_home(), dict(rec, **got),
            evidence="{0} (Stop-хук)".format(got.get("evidence") or "старая работает"))
    except Exception:  # noqa: BLE001
        pass
    return handed_remind_text(new_title)


def _two_heads_evidence(rec: dict) -> Optional[dict]:
    """Признак F49 у пары *rec* (:func:`tide.handoff_queue.pair_evidence`) или None."""
    try:
        from .. import handoff_queue as hq
        return hq.pair_evidence(paths.control_home(), rec)
    except Exception:  # noqa: BLE001 — не доказано — не упрекаем
        return None


def handed_forward_text(new_title: str, left: List[str]) -> str:
    return ("tide: итог воркера ещё не переслан {0}: {1}. Перешли его ей — и всё, "
            "новое не начинай.".format(new_title, "; ".join(left[:3])))


def _forward_nudge(rec: dict, session: str, new_title: str) -> str:
    """Старая без признака двух голов: молчим, если итоги воркеров пересланы новой
    (:func:`tide.arc.workers.unforwarded_results`); иначе — коротко «перешли»."""
    try:
        from .. import handoff_queue as hq
        from ..arc.workers import parse_iso, unforwarded_results
        since = parse_iso(rec.get("taken_at"))
        new_ao = hq._new_ao_of(paths.control_home(), rec)
        if since is None or not new_ao:
            return ""
        for tr in hq._transcripts(session):
            left = unforwarded_results(tr, since, new_ao)
            if left:
                return handed_forward_text(new_title, left)
    except Exception:  # noqa: BLE001
        pass
    return ""


def fill_tokens(payload: dict, session: str) -> Optional[int]:
    from .. import context_fill

    target = payload.get("transcript_path") or session
    if not target:
        return None
    res = context_fill.context_fill(target)
    return int(res["tokens"]) if res else None


def _session_name(entry: Path) -> str:
    """``designer · 03 · <ao-id>`` — заголовок паспорта без префикса проекта."""
    try:
        from ..launcher import reception
        title = reception._title(entry, entry.name)
    except Exception:  # noqa: BLE001
        title = entry.name
    return title.split(" — ", 1)[1] if " — " in title else title


def _handed_at(entry: Path, session: str) -> str:
    """Когда сессия отдала нить: штамп ``handed-off``, иначе приём оффера, иначе
    первое напоминание. → ``ЧЧ:ММ`` (или как записано), ``""`` — неизвестно."""
    pp = entry / "arc.md"
    raw = (fields.read_field(pp, HANDED_OFF_FIELD) or "").strip()
    if not raw:
        try:
            from .. import handoff_queue as hq
            rec = hq.is_dissolved(paths.control_home(), session) or {}
            raw = str(rec.get("taken-at") or "").strip()
        except Exception:  # noqa: BLE001
            raw = ""
    if not raw:
        raw = (fields.read_field(pp, HANDED_TOLD_FIELD) or "").strip()
    raw = raw.split(" ", 1)[0]
    try:
        return datetime.fromisoformat(raw).strftime("%H:%M")
    except ValueError:
        return raw


def handed_ping_text(tokens: int) -> str:
    return ("tide: ты передала нить, контекст {0}: доведи воркера, перешли итог новой "
            "и замолчи.".format(_k(tokens)))


def handed_alarm_text(entry: Path, session: str, tokens: int) -> str:
    return "сессия {0} передала нить в {1}, контекст {2} — закрой её".format(
        _session_name(entry), _handed_at(entry, session) or "?", _k(tokens))


def _alert_manager(root: Path, entry: Path, text: str) -> None:
    """Тревога менеджеру тем же путём, что тревожная ступень лестницы (``route=manager``);
    нет ``$TIDE_ALERT_CMD`` — напрямую ``ao send`` менеджеру (как signal_stall)."""
    from ..launcher import autohandoff as _ah

    if _ah.send_alert(text, route=ROUTE_MANAGER, source=alert_source(root, entry)) is None:
        _ah.tell_manager(text)


def _handed_ping(root: Path, entry: Path, session: str, payload: dict,
                 now: Optional[datetime]) -> Optional[str]:
    """SOC-98: сессия, отдавшая нить, растёт дальше — на 300 тыс. пинг ей, на 500 —
    ещё и тревога менеджеру. Мягкая ступень (250) ей не нужна (передача уже сделана).
    Один пинг на ступень — тот же штамп ``ctx-ping``."""
    tokens = fill_tokens(payload, session)
    if tokens is None:
        return None
    ladder = list(thresholds())
    reached = sum(1 for t in ladder if tokens >= t)
    if reached < 2:
        return None
    pp = entry / "arc.md"
    if _pinged_rank(pp, ladder) >= reached:
        return None
    step = ladder[reached - 1]
    fields.set_field(pp, CTX_PING_FIELD, "{0} {1} {2}".format(
        _kn(step), (now or datetime.now()).isoformat(timespec="seconds"), tokens))
    try:
        if reached >= 3:
            _alert_manager(root, entry, handed_alarm_text(entry, session, tokens))
        else:
            _send("hard", root, entry, "сессия {0} передала нить, а контекст {1} тыс. "
                  "— пинг: доведи воркера и замолчи".format(
                      slug.entry_slug(entry.name), _kn(tokens)))
    except Exception:  # noqa: BLE001 — a signal must never break the ping
        pass
    return handed_ping_text(tokens)


def _calm_remind_min() -> int:
    return _env_int(CALM_REMIND_ENV, CALM_REMIND_MIN)


def _soft_wait(root: Path, entry: Path, pp: Path, tokens: int, soft: int, hard: int,
               busy: List[str], now: Optional[datetime]) -> Optional[str]:
    """Мягкий порог, а сессия занята (живые воркеры): ступень не засчитана —
    ``ctx-ping: <тыс.>w <время> <токены>``; пинг «передай в первый спокойный
    момент» — сразу и потом не чаще :data:`CALM_REMIND_MIN` минут."""
    now = now or datetime.now()
    raw = (fields.read_field(pp, CTX_PING_FIELD) or "").strip().split()
    if raw and raw[0] == "{0}{1}".format(_kn(soft), WAIT_MARK) and len(raw) > 1:
        try:
            last = datetime.fromisoformat(raw[1])
            if (now.replace(tzinfo=None) - last).total_seconds() < _calm_remind_min() * 60:
                return None
        except ValueError:
            pass
        first = False
    else:
        first = True
    fields.set_field(pp, CTX_PING_FIELD, "{0}{1} {2} {3}".format(
        _kn(soft), WAIT_MARK, now.replace(tzinfo=None).isoformat(timespec="seconds"), tokens))
    if first:
        try:
            _send("soft", root, entry, _short(entry, tokens, "soft-wait"))
        except Exception:  # noqa: BLE001 — a signal must never break the ping
            pass
    return soft_wait_text(tokens, soft, hard, busy)


def watch_reason(root: Path, payload: dict, *,
                 now: Optional[datetime] = None) -> Optional[str]:
    """Причина блока остановки — или None. Один пинг на ступень, штамп ``ctx-ping``."""
    session = str(payload.get("session_id") or payload.get("session") or "")
    entry = _session_entry(root, session)
    if entry is None:
        return None
    if payload.get("stop_hook_active"):
        return None
    told = handed_over_reason(entry, session, now=now)
    if told is not None or _stood_down(entry, session):
        # отдала нить: хендофф не требуем, но контекст считаем (SOC-98) —
        # напоминание о передаче и пинг ступени — одним блоком
        ping = _handed_ping(root, entry, session, payload, now)
        parts = [t for t in (told, ping) if t]
        return "\n".join(parts) or None
    tokens = fill_tokens(payload, session)
    if tokens is None:
        return None
    ladder = list(thresholds())
    reached = sum(1 for t in ladder if tokens >= t)
    if reached == 0:
        return None
    pp = entry / "arc.md"
    if _pinged_rank(pp, ladder) >= reached:
        return None
    level = LEVELS[reached - 1]
    step = ladder[reached - 1]
    if level == "soft":
        busy = busy_workers(payload, session)
        if busy:
            return _soft_wait(root, entry, pp, tokens, step, ladder[1], busy, now)
    fields.set_field(pp, CTX_PING_FIELD, "{0} {1} {2}".format(
        _kn(step), (now or datetime.now()).isoformat(timespec="seconds"), tokens))
    if level == "soft":
        reason = soft_text(entry, tokens, step, ladder[1])
        signal = _short(entry, tokens, level)
    elif level == "hard":
        reason = hard_text(entry, tokens, step)
        signal = _short(entry, tokens, level)
    else:
        reason = alarm_text(entry, tokens, step)
        signal = "сессия {0} на {1} тыс. токенов, хендоффа нет".format(
            slug.entry_slug(entry.name), _kn(tokens))
    try:
        _send(level, root, entry, signal)
    except Exception:  # noqa: BLE001 — a signal must never break the ping
        pass
    return reason


def cmd_context_watch(args) -> int:
    """``tide hook context-watch`` — Stop: сторож заполнения контекста."""
    try:
        root = paths.find_tide_root()
        if root is None:
            return 0
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except (ValueError, OSError):
            return 0
        if not isinstance(payload, dict):
            return 0
        reason = watch_reason(root, payload)
        if reason:
            print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 — a hook must never raise
        print("tide: [context-watch] skipped: {0}".format(exc), file=sys.stderr)
    return 0

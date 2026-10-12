"""tide.hooks.context_watch — Stop-хук ``tide hook context-watch``: сторож контекста.

Сжатие tide не делает (вердикт Гриши 09.10): переполненная сессия ПЕРЕДАЁТ нить
свежей. Сама сессия о контексте не думает — её пингует хук. Порог передачи — по
слову Гриши 12.10 (SOC-237: передавать раньше; было 250/300/500 по «Давай А», SOC-143):

* ``SOFT`` (150 000) — порог передачи контракта сида
  (:data:`tide.handoff_contract.HANDOFF_TOKENS`): с этого момента сессия передаёт
  работу в ПЕРВЫЙ СПОКОЙНЫЙ МОМЕНТ — воркеры/сабагенты сдали, правки закоммичены.
  Спокойно ли — по транскрипту: живые сабагенты (:func:`busy_workers` — тот же
  признак, что у автозакрытия и сида, :mod:`tide.arc.workers`). Спокойно — пинг
  передачи с тремя шагами и ``tide handoff continue --auto``; занята — короткий
  пинг «передай в первый спокойный момент», ступень не засчитана (``ctx-ping:
  250w``): на следующих остановках хук смотрит снова, повтор пинга — не чаще раза
  в ``$TIDE_CTX_CALM_REMIND_MIN`` минут, спокойно — пинг передачи;
* ``HARD`` (200 000) — жёсткий потолок: передача обязательна сейчас, спокойно или
  нет — «доведи текущий шаг, новую работу не бери, хендофф сейчас»;
* ``ALARM`` (300 000) — тревога сверх потолка: сессия ещё раз получает требование
  хендоффа, и менеджеру уходит сигнал (``$TIDE_ALERT_CMD`` с
  ``TIDE_ALERT_ROUTE=manager``).

На мягком и жёстком пороге тот же текст кратко уходит в ``$TIDE_ALERT_CMD`` с
``TIDE_ALERT_ROUTE=log`` (пульт только пишет событие в лог). Нет команды — молча
пропускаем. Пройденный порог запоминается в паспорте (``ctx-ping: 150|200|300``):
повторно не пингуем, скачок через несколько порогов — один пинг старшего.

Холодное пробуждение (SOC-237): если перед текущим ходом сессия простояла больше
``$TIDE_CTX_COLD_MIN`` минут (60), а контекст больше ``$TIDE_CTX_COLD_TOKENS`` (60 000),
кэш промпта уже остыл — на первом ходе после простоя хук требует ту же передачу, что на
пороге (:func:`cold_text`). Простой — по транскрипту (:func:`idle_before_turn`): самый
длинный разрыв между записями текущего хода, считая от конца прошлого (строка
``stop_hook_summary``). Одно пробуждение — один пинг: штамп ``cold-ping: <время
пробуждения> <мин> <токены>``.

Счёт — ТОЛЬКО через :mod:`tide.context_fill` (им же рисует доска). Пороги —
здесь, env: ``$TIDE_CTX_SOFT`` / ``$TIDE_CTX_HARD`` / ``$TIDE_CTX_ALARM``
(``$TIDE_CTX_NUDGE`` — старый синоним мягкого). Субагенты хендофф не делают (у них
SubagentStop). Молчит: вне tide-проекта, без арки-сессии, на продолжении после
блока (``stop_hook_active``), с ``auto-handoff: off`` в паспорте (разговор, SOC-230) —
и пороги, и холодное пробуждение. Растворённая или уже передавшая сессия хендофф не
получает, но контекст её считается (SOC-98): на потолке — пинг «доведи воркера,
перешли итог, замолчи», на тревоге — ещё и сигнал менеджеру «закрой её». Любая
ошибка — молчаливый exit 0.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from .. import fields, handoff_contract, offload, paths, slug

# Мягкий порог = порог передачи контракта сида: с него — передача в первый спокойный
# момент; жёсткий — потолок, передача обязательна (слово Гриши 12.10, SOC-237).
SOFT_TOKENS = handoff_contract.HANDOFF_TOKENS
HARD_TOKENS = 200_000
ALARM_TOKENS = 300_000
SOFT_ENV = "TIDE_CTX_SOFT"
HARD_ENV = "TIDE_CTX_HARD"
ALARM_ENV = "TIDE_CTX_ALARM"
NUDGE_ENV = "TIDE_CTX_NUDGE"  # старое имя мягкого порога — синоним SOFT_ENV
# мягкий порог в неспокойный момент: повтор пинга «передай, как станет спокойно» — не чаще
CALM_REMIND_ENV = "TIDE_CTX_CALM_REMIND_MIN"
CALM_REMIND_MIN = 10
WAIT_MARK = "w"  # ``ctx-ping: 150w …`` — мягкий порог пройден, ждём спокойного момента
# Холодное пробуждение (SOC-237): простой дольше COLD_MIN минут при контексте больше
# COLD_TOKENS — на первом ходе после простоя та же передача, что на пороге.
COLD_MIN_ENV = "TIDE_CTX_COLD_MIN"
COLD_MIN = 60
COLD_TOKENS_ENV = "TIDE_CTX_COLD_TOKENS"
COLD_TOKENS = 60_000
COLD_PING_FIELD = "cold-ping"
COLD_TAIL_BYTES = 16 * 1024 * 1024  # хвост транскрипта, где ищем текущий ход

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

    return (autohandoff.report_step(entry) +
            "  1. пульс ПЕРЕДАЧА — заполни каждую метку (нечего — «нет»):\n"
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


AUTO_HANDOFF_FIELD = "auto-handoff"


def _auto_handoff_off(entry: Path) -> bool:
    """``auto-handoff: off`` в паспорте — сторож молчит на любом пороге (SOC-230)."""
    raw = (fields.read_field(entry / "arc.md", AUTO_HANDOFF_FIELD) or "").strip().lower()
    return raw in ("off", "no", "false", "0")


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
    нет ``$TIDE_ALERT_CMD`` — только журнал (SOC-232: в окно менеджера не пишем)."""
    from ..launcher import autohandoff as _ah

    if _ah.send_alert(text, route=ROUTE_MANAGER, source=alert_source(root, entry)) is None:
        from ..manager_guard import log_suppressed

        log_suppressed("", text, source=alert_source(root, entry))


def _handed_ping(root: Path, entry: Path, session: str, payload: dict,
                 now: Optional[datetime]) -> Optional[str]:
    """SOC-98: сессия, отдавшая нить, растёт дальше — на потолке (200 тыс.) пинг ей, на
    тревоге (300) — ещё и тревога менеджеру. Мягкая ступень (150) ей не нужна.
    Холодное пробуждение ей тоже не нужно (передача уже сделана).
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


def _parse_ts(raw) -> Optional[datetime]:
    """ISO-время записи транскрипта (``…Z`` или со смещением) → наивное UTC; иначе None."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if at.tzinfo is not None:
        at = at.astimezone(timezone.utc).replace(tzinfo=None)
    return at


def _tail_rows(path: Path) -> List[dict]:
    """Записи хвоста транскрипта (до :data:`COLD_TAIL_BYTES`), по порядку."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        start = max(0, size - COLD_TAIL_BYTES)
        fh.seek(start)
        blob = fh.read()
    lines = blob.splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # первая строка обрезана посередине
    rows = []
    for raw in lines:
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _is_turn_input(row: dict) -> bool:
    """Запись, с которой начинается ход: реплика человека, доставка, уведомление —
    любая ``user``-запись, кроме ответа инструмента."""
    if row.get("type") != "user":
        return False
    content = (row.get("message") or {}).get("content")
    if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return False
    return True


def idle_before_turn(path: Path) -> Optional[Tuple[float, datetime]]:
    """Простой перед текущим ходом по транскрипту: ``(минуты, время пробуждения)`` или None.

    Ход — записи после конца прошлого хода (последняя ``system/stop_hook_summary``,
    её пишет любой Stop-хук, в tide-сессии — сам сторож); сама граница входит в счёт.
    Простой — самый длинный разрыв между соседними записями с временем (кроме
    ``queue-operation``: она штампуется в момент прихода письма, а не хода и
    сабагентов); время пробуждения — запись после разрыва. Границы нет — весь хвост.
    В ходе должна быть входная запись (:func:`_is_turn_input`) — иначе None."""
    rows = [r for r in _tail_rows(path)
            if not r.get("isSidechain") and r.get("type") != "queue-operation"]
    start = 0
    for i, row in enumerate(rows):
        if row.get("type") == "system" and row.get("subtype") == "stop_hook_summary":
            start = i
    turn = rows[start:]
    if not any(_is_turn_input(r) for r in (turn[1:] if start else turn)):
        return None
    best: Optional[Tuple[float, datetime]] = None
    prev: Optional[datetime] = None
    for row in turn:
        at = _parse_ts(row.get("timestamp"))
        if at is None:
            continue
        if prev is not None:
            gap = (at - prev).total_seconds() / 60.0
            if best is None or gap > best[0]:
                best = (gap, at)
        prev = at if prev is None or at > prev else prev
    return best


def cold_text(entry: Path, tokens: int, idle_min: float) -> str:
    hours, mins = divmod(int(idle_min), 60)
    idle = "{0} ч {1} мин".format(hours, mins) if hours else "{0} мин".format(mins)
    return (
        "tide: сессия проснулась после простоя {0}, контекст {1} токенов — кэш остыл, "
        "каждый ход теперь дорогой. Передай работу свежей сессии: новое не начинай, "
        "доведи текущий шаг (воркеры в работе — дождись их итога), правки закоммить и:\n"
        "{2}\n"
        "Новая сессия поднимется сама и продолжит с курсора."
    ).format(idle, _k(tokens), handoff_steps(entry))


def _cold_reason(root: Path, entry: Path, payload: dict, session: str, tokens: int,
                 now: Optional[datetime]) -> Optional[str]:
    """Холодное пробуждение (SOC-237): простой > ``COLD_MIN`` минут перед этим ходом и
    контекст > ``COLD_TOKENS`` — пинг передачи, один на пробуждение (``cold-ping``)."""
    if tokens <= _env_int(COLD_TOKENS_ENV, COLD_TOKENS):
        return None
    try:
        from .. import context_fill
        target = payload.get("transcript_path") or ""
        path = Path(target) if target else context_fill.transcript_for(session)
        if path is None or not Path(path).is_file():
            return None
        idle = idle_before_turn(Path(path))
    except Exception:  # noqa: BLE001 — сторож не смеет упасть
        return None
    if idle is None or idle[0] <= _env_int(COLD_MIN_ENV, COLD_MIN):
        return None
    idle_min, woke = idle
    pp = entry / "arc.md"
    woke_iso = woke.isoformat(timespec="seconds")
    if (fields.read_field(pp, COLD_PING_FIELD) or "").strip().split(" ", 1)[0] == woke_iso:
        return None
    fields.set_field(pp, COLD_PING_FIELD, "{0} {1} {2}".format(
        woke_iso, int(idle_min), tokens))
    try:
        _send("soft", root, entry, "сессия {0} проснулась после {1} мин простоя на {2} "
              "тыс. токенов — холодный кэш: хендофф".format(
                  slug.entry_slug(entry.name), int(idle_min), _kn(tokens)))
    except Exception:  # noqa: BLE001 — a signal must never break the ping
        pass
    return cold_text(entry, tokens, idle_min)


def watch_reason(root: Path, payload: dict, *,
                 now: Optional[datetime] = None) -> Optional[str]:
    """Причина блока остановки — или None. Один пинг на ступень, штамп ``ctx-ping``;
    ступени молчат — холодное пробуждение (:func:`_cold_reason`, штамп ``cold-ping``)."""
    session = str(payload.get("session_id") or payload.get("session") or "")
    entry = _session_entry(root, session)
    if entry is None:
        return None
    if _auto_handoff_off(entry):
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
    reason = _ladder_reason(root, entry, payload, session, tokens, now)
    if reason:
        return reason
    return _cold_reason(root, entry, payload, session, tokens, now)


def _ladder_reason(root: Path, entry: Path, payload: dict, session: str, tokens: int,
                   now: Optional[datetime]) -> Optional[str]:
    """Лестница порогов: текст пинга ступени или None (ступени молчат)."""
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

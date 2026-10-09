"""tide.hooks.context_watch — Stop-хук ``tide hook context-watch``: сторож контекста.

Сжатие tide не делает (вердикт Гриши 09.10): переполненная сессия ПЕРЕДАЁТ нить
свежей. Сама сессия о контексте не думает — её пингует хук. Лестница порогов
(решение 09.10, заменила 300/500), на КАЖДОМ пороге ровно один пинг — блок одной
остановки с текстом, а не блок каждой остановки:

* ``SOFT`` (200 000) — порог передачи контракта сида
  (:data:`tide.handoff_contract.HANDOFF_TOKENS`): передача начинается здесь, не
  позже — «новое не начинай, доведи текущее действие, tide offload и
  ``tide handoff continue --auto``»;
* ``HARD`` (300 000) — жёстко: «доведи текущий шаг, новую работу не бери, хендофф
  сейчас»;
* ``ALARM`` (500 000) — тревога: сессия ещё раз получает требование хендоффа, и
  менеджеру уходит сигнал (``$TIDE_ALERT_CMD`` с ``TIDE_ALERT_ROUTE=manager``).

На мягком и жёстком пороге тот же текст кратко уходит в ``$TIDE_ALERT_CMD`` с
``TIDE_ALERT_ROUTE=log`` (пульт только пишет событие в лог). Нет команды — молча
пропускаем. Пройденный порог запоминается в паспорте (``ctx-ping: 200|300|500``):
повторно не пингуем, скачок через несколько порогов — один пинг старшего.

Счёт — ТОЛЬКО через :mod:`tide.context_fill` (им же рисует доска). Пороги —
здесь, env: ``$TIDE_CTX_SOFT`` / ``$TIDE_CTX_HARD`` / ``$TIDE_CTX_ALARM``
(``$TIDE_CTX_NUDGE`` — старый синоним мягкого). Субагенты хендофф не делают (у них
SubagentStop). Молчит: вне tide-проекта, без арки-сессии, у растворённой или уже
передавшей сессии, на продолжении после блока (``stop_hook_active``). Любая
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

# Мягкий порог = порог передачи контракта сида: передача начинается здесь, не позже.
SOFT_TOKENS = handoff_contract.HANDOFF_TOKENS
HARD_TOKENS = 300_000
ALARM_TOKENS = 500_000
SOFT_ENV = "TIDE_CTX_SOFT"
HARD_ENV = "TIDE_CTX_HARD"
ALARM_ENV = "TIDE_CTX_ALARM"
NUDGE_ENV = "TIDE_CTX_NUDGE"  # старое имя мягкого порога — синоним SOFT_ENV

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


def _layer_cmd(entry: Path) -> str:
    """Шаг слоёв нити (SOC-101 F53): голове писать файлы нельзя, CLI — можно."""
    from ..arc import thread_layers

    return thread_layers.layer_cmd(slug.entry_slug(entry.parent.parent.name))


def handoff_steps(entry: Path) -> str:
    """Три шага передачи (SOC-101 F44): пульс ПЕРЕДАЧА со всеми метками, слои нити,
    автопередача. Без ПЕРЕДАЧИ сид собирается без карты, ожидания Гриши, «ждёт
    Гришу», «в полёте» и хвостов — поэтому шаблон готовый, а не «<что сделал>»."""
    from ..launcher import autohandoff

    return ("  1. пульс ПЕРЕДАЧА — заполни каждую метку (нечего — «нет»):\n"
            "     {0}\n"
            "  2. слои нити (ожидание Гриши словами Гриши с датой · где мы · опыт), если "
            "пусты:\n"
            "     {1}\n"
            "  3. {2}").format(autohandoff.pass_template(entry), _layer_cmd(entry),
                              AUTO_CMD)


def soft_text(entry: Path, tokens: int, soft: int, hard: int) -> str:
    return (
        "tide: контекст сессии {0} токенов (порог передачи {1}) — передача нити "
        "начинается сейчас, не позже. Новое не начинай, доведи текущее действие и:\n"
        "{2}\n"
        "Новая сессия поднимется сама и продолжит с курсора. На {3} — хендофф "
        "без отсрочек."
    ).format(_k(tokens), _k(soft), handoff_steps(entry), _k(hard))


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
    what = {"soft": "порог передачи: доведи текущее действие, offload и хендофф",
            "hard": "жёсткий пинг: доведи шаг, хендофф сейчас"}[level]
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


def handed_over_reason(entry: Path, session: str, *,
                       now: Optional[datetime] = None) -> Optional[str]:
    """Сессия уже отдала нить (её оффер принят, SOC-101 F21): хендофф с неё не
    требуем, а ОДИН раз говорим, кому передано. → текст (первый раз), ``""``
    (уже сказано — молчать), None (нить не отдавала — обычный сторож)."""
    try:
        from .. import handoff_queue as hq
        rec = hq.is_dissolved(paths.control_home(), session)
    except Exception:  # noqa: BLE001
        return None
    if not rec:
        return None
    pp = entry / "arc.md"
    if (fields.read_field(pp, HANDED_TOLD_FIELD) or "").strip():
        return ""
    try:
        from ..launcher import reception
        new_title = reception._title(reception._session_of(rec), "новой сессии")
    except Exception:  # noqa: BLE001
        new_title = "новой сессии"
    fields.set_field(pp, HANDED_TOLD_FIELD, "{0} → {1}".format(
        (now or datetime.now()).isoformat(timespec="seconds"), rec.get("name")))
    return handed_over_text(new_title)


def fill_tokens(payload: dict, session: str) -> Optional[int]:
    from .. import context_fill

    target = payload.get("transcript_path") or session
    if not target:
        return None
    res = context_fill.context_fill(target)
    return int(res["tokens"]) if res else None


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
    if told is not None:
        return told or None
    if _stood_down(entry, session):
        return None
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

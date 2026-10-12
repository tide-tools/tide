"""tide.hooks.ask_gate — PreToolUse ``tide hook ask-gate``: вопрос не диалогом (SOC-108).

Беда (09.10): в AO-сессии ``AskUserQuestion`` молча вешает сессию — диалог ждёт
человека, которого в окне нет, а сообщения к ней (uds / ``ao send``) копятся в
очереди, отправитель думает, что доставил. На диалогах висели tide-stack-7, -10, -12.

Гейт: в AO-сессии (``$AO_SESSION_ID`` задан), где за последние
:data:`HUMAN_WINDOW_MIN` минут не было живой реплики человека, ``AskUserQuestion``
отказывается — с текстом «вопрос во „Вопросы“ доски через tg-alert, дальше по ⭐» (SOC-242)
и запасным адресом менеджера из manager.json (:func:`tide.whois._manager_file`). Сам вопрос дописывается строкой в
``<проект>/.tide/state/blocked-asks.jsonl`` — его подбирает ловец SOC-109
(формат согласован с management-9, менять нельзя).

Живая реплика — запись транскрипта ``type: user`` с ``origin.kind: human``, текстом
(не tool_result), не доставка AO / tg / task-notification / system-reminder / peer,
и НЕ первая такая запись файла (первая — промпт спавна AO).

Молчит (пропуск, exit 0, ничего не печатает): другой инструмент, ``TIDE_ASK_GATE=off``,
нет ``$AO_SESSION_ID`` (человек в своём терминале), живая реплика в окне. Любая
ошибка — пропуск (fail-open): гейт никогда не ломает сессию.

Отказ — JSON PreToolUse ``hookSpecificOutput`` с ``permissionDecision: deny`` на
stdout и exit 0 (а не exit 2 как у role-gate): так причина доходит до модели
целиком, а решение явное.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from .. import paths

# Окно «человек в окне»: живая реплика не старше стольких минут — диалог проходит.
# Гриша сидит в окнах AO и может смотреть, не печатая; одно слово в окно открывает.
HUMAN_WINDOW_MIN = 10

TOOL_NAME = "AskUserQuestion"
OFF_ENV = "TIDE_ASK_GATE"
AO_SESSION_ENV = "AO_SESSION_ID"

# Адрес менеджера: стабильный файл (SOC-101 делает постоянный адрес); путь
# выбирает tide.whois._manager_file (env → control-home → старый ~/.tide); в
# тестах — env или параметр. Константа — старый путь, для совместимости имени.
MANAGER_FILE = Path.home() / ".tide" / "manager.json"
MANAGER_FILE_ENV = "TIDE_MANAGER_FILE"
# Адрес не нашёлся ни в manager.json, ни по цепочке хендоффов — без констант
# (SOC-101 C14: «management-7» / «ao-control · NN» устаревают на первом хендоффе).
MANAGER_FALLBACK = (
    "сессия, что сейчас ведёт главную нить менеджера — её AO-id и имя печатает "
    "`tide whois manager`"
)

BLOCKED_ASKS_FILE = "blocked-asks.jsonl"
NOT_RECORDED = "не записан"

# Сколько хвоста транскрипта читаем: файл живой сессии растёт до десятков МБ.
TAIL_BYTES = 4 * 1024 * 1024

# Начала текста, которые человеком НЕ считаются: доставки AO, телеграм-мост,
# уведомления задач, системные вставки, сообщения соседних сессий.
NOT_HUMAN_PREFIXES = (
    "<ao-session-delivery",
    "[tg]",
    "<task-notification",
    "<system-reminder",
    "Another Claude session sent a message",
)

# SOC-242 (правило вопросов, слово пользователя 12.10): вопрос — сразу во «Вопросы»
# доски через tg-alert, не менеджеру в окно; после вопроса не ждать, а идти по ⭐.
DENY_TEMPLATE = (
    "Это не поломка: вопрос по правилу идёт на доску. "
    "Вопрос не диалогом: это сессия под AO, человека в окне нет (живой реплики за "
    "последние {window} мин) — диалог молча повесит сессию. Выставь вопрос во «Вопросы» "
    "доски через tg-alert: ❓, «Почему:» 1–3 строки, «варианты: ⭐ рекомендуемый первым …»; "
    "в личку Telegram не дублируй. Не жди: продолжай по ⭐ или бери следующее, ответ придёт — "
    "поправишь; ждать только необратимое или наружу — тогда курсор «мяч у пользователя: "
    "вопрос TG N». Нет tg-alert в руках — выставление отдай сабагенту, не выходит — письмом "
    "менеджеру ({manager}). Если Гриша сейчас здесь — пусть напишет в окно одно слово, и "
    "диалог пройдёт. Вопрос записан: {recorded}."
)


# --- адрес менеджера -----------------------------------------------------------

def manager_address(path: Optional[Path] = None) -> str:
    """Адрес менеджера для SendMessage.

    ``uds_name`` в manager.json — как есть (``<uds_name> (ao_session <id>)``);
    иначе резолвер :func:`tide.whois.manager`: ``title``/``ao_session`` файла, без
    них — новейшая принятая сессия нити менеджера. Ничего — запасной текст."""
    try:
        from .. import whois
        if path is None:
            path = whois._manager_file()  # env → control-home/.tide/state → ~/.tide
        data = whois.read_manager_file(path)
        uds = data.get("uds_name", "")
        if uds:
            ao = data.get("ao_session", "")
            return "{0} (ao_session {1})".format(uds, ao) if ao else uds
        return whois.address(whois.manager(path=path)) or MANAGER_FALLBACK
    except Exception:  # noqa: BLE001 — нет адреса → запасной текст
        return MANAGER_FALLBACK


# --- транскрипт: была ли живая реплика человека ---------------------------------

def _text_of(content) -> Optional[str]:
    """Текст реплики: строка или text-блоки списка; tool_result → None."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                return None
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts) if parts else None
    return None


def _is_human_record(rec) -> bool:
    """``type: user`` с ``origin.kind: human`` (без проверки текста)."""
    if not isinstance(rec, dict) or rec.get("type") != "user":
        return False
    origin = rec.get("origin")
    return isinstance(origin, dict) and origin.get("kind") == "human"


def _human_text(rec) -> Optional[str]:
    """Текст живой реплики человека — или None, если запись не она."""
    if not _is_human_record(rec):
        return None
    message = rec.get("message")
    if not isinstance(message, dict):
        return None
    text = _text_of(message.get("content"))
    if text is None:
        return None
    if text.lstrip().startswith(NOT_HUMAN_PREFIXES):
        return None
    return text


def _parse_ts(raw) -> Optional[datetime]:
    """``2026-10-09T08:07:05.253Z`` → aware UTC datetime."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _records(lines: Iterable[bytes]):
    for line in lines:
        try:
            yield json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue


def _first_human_uuid(path: Path) -> Optional[str]:
    """``uuid`` первой human-записи с начала файла (промпт спавна AO)."""
    with open(path, "rb") as fh:
        for rec in _records(fh):
            if _is_human_record(rec):
                return str(rec.get("uuid") or rec.get("timestamp") or "")
    return None


def _tail_lines(path: Path, size: Optional[int] = None) -> List[bytes]:
    """Последние ~*size* байт файла по строкам (первая, обрезанная, отброшена)."""
    size = TAIL_BYTES if size is None else size
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        end = fh.tell()
        start = max(0, end - size)
        fh.seek(start)
        data = fh.read()
    lines = data.split(b"\n")
    if start > 0 and lines:
        lines = lines[1:]
    return [ln for ln in lines if ln.strip()]


def has_live_human(transcript: Path, *, now: Optional[datetime] = None,
                   window_min: int = HUMAN_WINDOW_MIN) -> bool:
    """Была ли живая реплика человека в транскрипте за последние *window_min* минут."""
    path = Path(transcript)
    if not path.is_file():
        return False
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=window_min)
    first = _first_human_uuid(path)
    for rec in _records(_tail_lines(path)):
        if _human_text(rec) is None:
            continue
        key = str(rec.get("uuid") or rec.get("timestamp") or "")
        if first is not None and key == first:
            continue  # промпт спавна AO — не человек в окне
        ts = _parse_ts(rec.get("timestamp"))
        if ts is not None and ts >= cutoff:
            return True
    return False


# --- запись вопроса для ловца SOC-109 --------------------------------------------

def _questions(tool_input) -> list:
    """``[{question, header, options: [label…]}]`` из tool_input.questions."""
    out = []
    raw = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if not isinstance(raw, list):
        return out
    for q in raw:
        if not isinstance(q, dict):
            continue
        options = []
        for opt in q.get("options") or []:
            if isinstance(opt, dict):
                options.append(str(opt.get("label") or ""))
            elif isinstance(opt, str):
                options.append(opt)
        out.append({"question": str(q.get("question") or ""),
                    "header": str(q.get("header") or ""),
                    "options": options})
    return out


def record_blocked_ask(payload: dict, *, now: Optional[datetime] = None) -> Optional[Path]:
    """Дописать вопрос строкой в ``<проект>/.tide/state/blocked-asks.jsonl``.

    Корень — :func:`tide.paths.find_tide_root` от ``cwd`` входа (AO-worktree
    отображается в главное дерево). Нет корня → None (не записан).
    """
    cwd = str(payload.get("cwd") or "") or os.getcwd()
    root = paths.find_tide_root(Path(cwd))
    if root is None:
        return None
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    row = {
        "id": str(uuid.uuid4()),
        "ts": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project": Path(root).name,
        "ao_session": os.environ.get(AO_SESSION_ENV, ""),
        "claude_session": str(payload.get("session_id") or ""),
        "cwd": cwd,
        "questions": _questions(payload.get("tool_input")),
        "answered": None,
    }
    target = paths.state_dir(Path(root)) / BLOCKED_ASKS_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return target


# --- решение -------------------------------------------------------------------

def deny_reason(payload: dict, *, now: Optional[datetime] = None,
                manager_file: Optional[Path] = None) -> Optional[str]:
    """Текст отказа — или None (пропуск). Пишет blocked-asks при отказе."""
    if payload.get("tool_name") != TOOL_NAME:
        return None
    if (os.environ.get(OFF_ENV) or "").strip().lower() == "off":
        return None
    if not (os.environ.get(AO_SESSION_ENV) or "").strip():
        return None  # человек в своём терминале
    transcript = payload.get("transcript_path")
    if transcript and has_live_human(Path(str(transcript)), now=now):
        return None
    try:
        recorded = record_blocked_ask(payload, now=now)
    except Exception:  # noqa: BLE001 — запись не должна мешать отказу
        recorded = None
    return DENY_TEMPLATE.format(
        window=HUMAN_WINDOW_MIN,
        manager=manager_address(manager_file),
        recorded=str(recorded) if recorded else NOT_RECORDED,
    )


def deny_output(reason: str) -> dict:
    """JSON PreToolUse-отказа."""
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


def cmd_ask_gate(args) -> int:
    """``tide hook ask-gate`` — PreToolUse на AskUserQuestion; всегда exit 0."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        reason = deny_reason(payload)
        if reason:
            print(json.dumps(deny_output(reason), ensure_ascii=False))
    except Exception:  # noqa: BLE001 — fail-open: гейт никогда не ломает сессию
        pass
    return 0

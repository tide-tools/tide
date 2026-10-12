"""tide.hooks.subagent_budget — PreToolUse ``tide hook subagent-budget``: счётчик сабагентов.

Идея 1 из разбора bb (SOC-237): сабагенты — 61 % счёта, у веера не было потолка.
Хук на инструмент ``Agent`` (matcher ``Agent|Task`` — Task прежнее имя) считает запуски
сабагентов в сессии и при превышении потолков из ``prompts/thread-rules.md`` добавляет
мягкое предупреждение. НЕ блокирует: ``permissionDecision`` не ставится, вызов идёт обычным
путём; предупреждение — ``additionalContext`` (модели) и ``systemMessage`` (человеку).

Потолки: :data:`MAX_BURST` запусков за :data:`BURST_WINDOW_SEC` секунд (приближение к
«одновременно» — без PostToolUse живых не посчитать) и :data:`MAX_TOTAL` за сессию.

Счёт — ``<проект>/.tide/state/subagent-count/<session_id>.json``; нет корня tide — молчит.
``TIDE_SUBAGENT_BUDGET=off`` — выкл. Любая ошибка — пропуск (fail-open), exit 0.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

from .. import paths

TOOL_NAMES = ("Agent", "Task")
OFF_ENV = "TIDE_SUBAGENT_BUDGET"
MAX_BURST = 3
BURST_WINDOW_SEC = 120
MAX_TOTAL = 8
COUNT_DIR = "subagent-count"

WARN_TEMPLATE = (
    "Потолок сабагентов (thread-rules.md): {why}. Всего в сессии запущено {total}. "
    "Запуск не заблокирован, но правило нити: не больше {burst} одновременно и {total_max} "
    "за задачу; объедини поручения или доведи шаг сам."
)


def _count_file(payload: dict, root: Optional[Path] = None) -> Optional[Path]:
    sid = re.sub(r"[^A-Za-z0-9_.-]", "_", str(payload.get("session_id") or ""))
    if not sid:
        return None
    if root is None:
        root = paths.find_tide_root(Path(str(payload.get("cwd") or "") or os.getcwd()))
    if root is None:
        return None
    return paths.state_dir(Path(root)) / COUNT_DIR / (sid + ".json")


def register_spawn(payload: dict, *, now: Optional[float] = None,
                   root: Optional[Path] = None) -> Optional[str]:
    """Записать запуск; вернуть текст предупреждения или None."""
    if payload.get("tool_name") not in TOOL_NAMES:
        return None
    if (os.environ.get(OFF_ENV) or "").strip().lower() == "off":
        return None
    target = _count_file(payload, root)
    if target is None:
        return None
    ts = time.time() if now is None else now
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        stamps = [float(x) for x in data.get("spawns", [])]
    except Exception:  # noqa: BLE001 — нет файла/битый → счёт с нуля
        stamps = []
    stamps.append(ts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"spawns": stamps}), encoding="utf-8")
    total = len(stamps)
    burst = sum(1 for s in stamps if ts - s <= BURST_WINDOW_SEC)
    why = []
    if burst > MAX_BURST:
        why.append("{0} запусков за {1} с (потолок {2})".format(burst, BURST_WINDOW_SEC, MAX_BURST))
    if total > MAX_TOTAL:
        why.append("{0}-й сабагент в сессии (потолок {1})".format(total, MAX_TOTAL))
    if not why:
        return None
    return WARN_TEMPLATE.format(why="; ".join(why), total=total,
                                burst=MAX_BURST, total_max=MAX_TOTAL)


def warn_output(text: str) -> dict:
    """JSON PreToolUse без решения о доступе: вызов не блокируется."""
    return {"systemMessage": text,
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}


def cmd_subagent_budget(args) -> int:
    """``tide hook subagent-budget`` — PreToolUse на Agent; всегда exit 0."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if isinstance(payload, dict):
            text = register_spawn(payload)
            if text:
                print(json.dumps(warn_output(text), ensure_ascii=False))
    except Exception:  # noqa: BLE001 — fail-open
        pass
    return 0

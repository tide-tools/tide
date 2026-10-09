"""tide.hooks.context_watch — Stop-хук ``tide hook context-watch``: сторож контекста.

Сжатие tide не делает (вердикт Гриши 09.10): переполненная сессия ПЕРЕДАЁТ нить
свежей. Хук на каждой остановке сессии:

1. меряет заполнение контекста — ТОЛЬКО через :mod:`tide.context_fill` (им же
   рисует доска; второго расчёта нет);
2. ``NUDGE`` (300 000 токенов) — блокирует остановку ОДИН раз: «доведи шаг,
   сделай offload, затем ``tide handoff continue --auto``»;
3. ``HARD`` (500 000) — блокирует КАЖДУЮ естественную остановку одной командой
   ``tide handoff continue --auto``, пока сессия не передаст нить.

Пороги — в одном месте (здесь), переопределяются ``$TIDE_CTX_NUDGE`` /
``$TIDE_CTX_HARD``. Субагенты хендофф не делают: у них срабатывает SubagentStop,
на него tide ничего не вешает. Молчит: вне tide-проекта, без арки-сессии, у
растворённой или уже передавшей сессии, на продолжении после блока
(``stop_hook_active`` — анти-петля). Любая ошибка — молчаливый exit 0.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from .. import fields, offload, paths, slug

NUDGE_TOKENS = 300_000
HARD_TOKENS = 500_000
NUDGE_ENV = "TIDE_CTX_NUDGE"
HARD_ENV = "TIDE_CTX_HARD"

AUTO_CMD = "tide handoff continue --auto"
CTX_NUDGE_FIELD = "ctx-nudge"
HANDED_OFF_FIELD = "handed-off"  # same key launcher.autohandoff stamps


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip().replace("_", "").replace(" ", "")
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def thresholds() -> Tuple[int, int]:
    """``(nudge, hard)`` в токенах; env переопределяет, hard не ниже nudge."""
    nudge = _env_int(NUDGE_ENV, NUDGE_TOKENS)
    hard = _env_int(HARD_ENV, HARD_TOKENS)
    return nudge, max(hard, nudge)


def _k(tokens: int) -> str:
    return "{0} тыс.".format(int(round(tokens / 1000.0)))


def nudge_text(entry: Path, tokens: int, nudge: int, hard: int) -> str:
    ref = "{0}/{1}".format(slug.entry_slug(entry.parent.parent.name),
                           slug.entry_slug(entry.name))
    return (
        "tide: контекст сессии {0} токенов (порог {1}) — пора передать нить свежей "
        "сессии. Доведи текущий шаг до точки, затем:\n"
        "  1. tide offload {2} --cursor \"<где стоишь>\" --next \"<шаги через · >\" "
        "\"<что сделал>\"\n"
        "  2. {3}\n"
        "Новая сессия поднимется сама и продолжит с курсора. После {4} каждая "
        "остановка будет блокироваться, пока не передашь."
    ).format(_k(tokens), _k(nudge), ref, AUTO_CMD, _k(hard))


def hard_text(tokens: int, hard: int) -> str:
    return (
        "tide: контекст {0} токенов — выше жёсткого порога {1}. Передай нить "
        "сейчас, одной командой:\n  {2}\n"
        "Ничего больше не делай, пока не передашь."
    ).format(_k(tokens), _k(hard), AUTO_CMD)


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


def fill_tokens(payload: dict, session: str) -> Optional[int]:
    from .. import context_fill

    target = payload.get("transcript_path") or session
    if not target:
        return None
    res = context_fill.context_fill(target)
    return int(res["tokens"]) if res else None


def watch_reason(root: Path, payload: dict, *,
                 now: Optional[datetime] = None) -> Optional[str]:
    """Причина блока остановки — или None. Штампует ``ctx-nudge`` при мягком блоке."""
    session = str(payload.get("session_id") or payload.get("session") or "")
    entry = _session_entry(root, session)
    if entry is None:
        return None
    if payload.get("stop_hook_active"):
        return None
    if _stood_down(entry, session):
        return None
    tokens = fill_tokens(payload, session)
    if tokens is None:
        return None
    nudge, hard = thresholds()
    if tokens >= hard:
        return hard_text(tokens, hard)
    if tokens >= nudge:
        pp = entry / "arc.md"
        if (fields.read_field(pp, CTX_NUDGE_FIELD) or "").strip():
            return None
        fields.set_field(pp, CTX_NUDGE_FIELD, "{0} {1}".format(
            (now or datetime.now()).isoformat(timespec="seconds"), tokens))
        return nudge_text(entry, tokens, nudge, hard)
    return None


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

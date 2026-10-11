"""tide.grisha_decisions — договорённости и решения Гриши в сиде (SOC-102).

Слово Гриши через менеджера: «решения у нас нигде не оседают» (tg 3976), «важно, как и когда
эти решения читаются» (tg 3980). Решения живут в журнале менеджера
``management/docs/agreements/decisions.md`` (пишет пульт Telegram), правила — рядом, в той же папке.
Сид каждой сессии несёт:

* строку-ссылку на папку договорённостей (читать на старте);
* блок «решения Гриши по проекту за 3 суток» — записи журнала с проектом нити, не больше
  :data:`LIMIT` строк «дата · вопрос → ответ · SOC», остальное — «ещё N — <журнал>».

Нет журнала / записей по проекту — блока нет; любая ошибка чтения — тоже нет блока (сид не ломается).
Формат журнала — в его шапке: блок ``## D-NNN · ГГГГ-ММ-ДД ЧЧ:ММ`` + поля ``- ключ: значение``
(``—`` = пусто); пример формата в шапке лежит в код-блоке — его пропускаем. Время — местное.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

AGREEMENTS_ENV = "TIDE_AGREEMENTS_DIR"
DECISIONS_ENV = "TIDE_DECISIONS_FILE"
DEFAULT_AGREEMENTS = Path.home() / "Documents" / "management" / "docs" / "agreements"

DAYS = 3
LIMIT = 10
TEXT_MAX = 110

HEAD_RE = re.compile(r"^## (D-\d{3,}) · (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*$")
FIELD_RE = re.compile(r"^- ([^:\n]+): ?(.*)$")
EMPTY = "—"


def agreements_dir() -> Path:
    return Path(os.environ.get(AGREEMENTS_ENV) or DEFAULT_AGREEMENTS)


def decisions_file() -> Path:
    return Path(os.environ.get(DECISIONS_ENV) or (agreements_dir() / "decisions.md"))


def agreements_line() -> str:
    return "договорённости: {0}/ (читать на старте)".format(str(agreements_dir()).rstrip("/"))


def parse(text: str) -> List[Dict[str, str]]:
    """Текст журнала → записи в порядке файла; код-блоки (пример формата в шапке) пропускаем."""
    out: List[Dict[str, str]] = []
    cur: Optional[Dict[str, str]] = None
    code = False
    for ln in str(text or "").splitlines():
        if ln.lstrip().startswith("```"):
            code = not code
            continue
        if code:
            continue
        m = HEAD_RE.match(ln)
        if m:
            cur = {"id": m.group(1), "when": m.group(2)}
            out.append(cur)
            continue
        if cur is None:
            continue
        f = FIELD_RE.match(ln)
        if f:
            v = f.group(2).strip()
            cur[f.group(1).strip()] = "" if v == EMPTY else v
        elif ln.startswith("#"):
            cur = None
    return out


def _when(e: Dict[str, str]) -> Optional[datetime]:
    try:
        return datetime.strptime(e.get("when", ""), "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def recent(project: str, *, now: Optional[datetime] = None, days: int = DAYS,
           path: Optional[Path] = None) -> List[Dict[str, str]]:
    """Записи журнала с ``проект: <project>`` за последние *days* суток, новые первыми."""
    if not project:
        return []
    try:
        text = Path(path or decisions_file()).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    now = now or datetime.now()
    since = now - timedelta(days=days)
    rows = []
    for e in parse(text):
        t = _when(e)
        if t is not None and since <= t <= now + timedelta(minutes=5) and e.get("проект") == project:
            rows.append((t, e))
    rows.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in rows]


def _short(s: str, limit: int = TEXT_MAX) -> str:
    t = " ".join(str(s or "").split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def _answer(s: str) -> str:
    s = str(s or "").strip()
    if not (s.startswith("«") and s.endswith("»")):
        s = "«{0}»".format(s)
    return _short(s)


def line(e: Dict[str, str]) -> str:
    """«- 11.10 02:27 · вопрос → «ответ» · SOC-184» (+ «(уточнить)», если запись сомнительная)."""
    t = _when(e)
    stamp = t.strftime("%d.%m %H:%M") if t else e.get("when", "")
    parts = ["{0} · {1} → {2}".format(stamp, _short(e.get("вопрос", "")), _answer(e.get("ответ", "")))]
    if e.get("SOC"):
        parts.append(e["SOC"])
    out = "- " + " · ".join(parts)
    if e.get("уточнить"):
        out += " (уточнить)"
    return out


def seed_block(project: str, *, now: Optional[datetime] = None, limit: int = LIMIT,
               path: Optional[Path] = None) -> str:
    """Блок «решения Гриши по проекту за 3 суток» или "" (нет журнала / записей / ошибка)."""
    try:
        rows = recent(project, now=now, path=path)
        if not rows:
            return ""
        src = Path(path or decisions_file())
        lines = ["решения Гриши по проекту {0} за {1} суток (журнал {2}):".format(project, DAYS, src)]
        lines += [line(e) for e in rows[:limit]]
        if len(rows) > limit:
            lines.append("- ещё {0} — {1}".format(len(rows) - limit, src))
        return "\n".join(lines)
    except Exception:  # noqa: BLE001 — сид не ломается из-за журнала
        return ""


def seed_text(project: str, *, now: Optional[datetime] = None) -> str:
    """Строка договорённостей + блок решений (если есть) — для сида."""
    block = seed_block(project, now=now)
    return agreements_line() + ("\n" + block if block else "")


__all__ = ["AGREEMENTS_ENV", "DAYS", "DECISIONS_ENV", "LIMIT", "agreements_dir", "agreements_line",
           "decisions_file", "line", "parse", "recent", "seed_block", "seed_text"]

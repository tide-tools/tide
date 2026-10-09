"""tide.arc.plan_main — «## главное — читать первым» в ``plan.md`` нити (решение Гриши № 24).

Раздел, который нить обязана сказать до первой работы: минимум три строки —
задача Linear (SOC-N), кому и каким каналом сдавать, с кем не дублировать. Это
то, что теряется на передаче первым и что новая сессия не выведет сама.

Кто читает:

* :func:`tide.arc.templates.thread_plan_md` — каркас с тремя подсказками
  (:data:`HINTS`) при рождении нити;
* ``tide offload`` (старт-гейт): первый пульс нити не проходит, пока раздел пуст
  или в нём подсказки; у старых нитей (раздела нет совсем) — предупреждение;
* сид хендоффа — блок «главное» контракта (:mod:`tide.handoff_contract`).

Чистое чтение диска, без записи.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, NamedTuple, Optional

PLAN_FILE = "plan.md"
HEADING = "## главное — читать первым"
PREFIX = "## главное"        # заголовок узнаётся по началу — хвост за автором
MIN_LINES = 3

# Подсказки каркаса: в угловых скобках, как все заготовки tide — живой строкой
# не считаются.
HINTS = (
    "<SOC-N — задача Linear нити, ссылкой>",
    "<кому и каким каналом сдавать итог (сессия, ao send, менеджер)>",
    "<с кем не дублировать — соседние нити и сессии>",
)

# Метка в паспорте нити: нить рождена с каркасом «главного» и обязана его
# заполнить на старт-гейте. Нет метки — нить старая: только предупреждение.
REQUIRED_FIELD = "plan-main"
REQUIRED_VALUE = "required"

_STEP = re.compile(r"(?m)^\s*-\s*\[[ x>]\]")
_PULSE = re.compile(r"^\s*-\s*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
_MECHANICAL = "шов приёма закрыт механикой"


def _is_live(line: str) -> bool:
    s = line.strip().lstrip("-*").strip()
    if not s or s.startswith("<") or s.startswith("<!--"):
        return False
    return s not in ("—", "-", "–") and not s.startswith("— ")


def section(text: str) -> Optional[List[str]]:
    """Строки раздела «главное» в тексте плана; None — раздела нет совсем."""
    out: List[str] = []
    inside = found = False
    for ln in (text or "").splitlines():
        if ln.startswith("## "):
            if inside:
                break
            inside = ln.startswith(PREFIX)
            found = found or inside
            continue
        if inside:
            out.append(ln.rstrip())
    return out if found else None


def live_lines(thread_dir: Path) -> List[str]:
    """Живые строки «главного» нити (без подсказок и прочерков); [] — нет."""
    body = read(thread_dir)
    return [ln.strip() for ln in (body or []) if _is_live(ln)]


def read(thread_dir: Path) -> Optional[List[str]]:
    """Сырые строки раздела из ``<нить>/plan.md``; None — нет плана или раздела."""
    plan = Path(thread_dir) / PLAN_FILE
    try:
        text = plan.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    return section(text)


def is_scaffold(thread_dir: Path) -> bool:
    """plan.md — нетронутый каркас: ни шагов, ни ``final:``, ни живого «главного».
    Такую нить можно сбросить как пустую (``drop-thread``)."""
    plan = Path(thread_dir) / PLAN_FILE
    try:
        text = plan.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    if _STEP.search(text) or re.search(r"(?m)^final:", text):
        return False
    body = section(text)
    return body is not None and not [ln for ln in body if _is_live(ln)]


def required(thread_dir: Path) -> bool:
    """Нить рождена под решение № 24 (метка :data:`REQUIRED_FIELD` в паспорте)."""
    try:
        from .. import fields
        from . import stream
        raw = fields.read_field(stream.passport_path(Path(thread_dir)), REQUIRED_FIELD)
    except Exception:  # noqa: BLE001
        return False
    return (raw or "").strip() == REQUIRED_VALUE


class Verdict(NamedTuple):
    block: bool      # True — пульс не проходит (старт-гейт новой нити)
    reason: str      # понятная причина / предупреждение


def problem(thread_dir: Path) -> Optional[str]:
    """Что не так с «главным» нити — или None, если в нём три живые строки."""
    plan = Path(thread_dir) / PLAN_FILE
    body = read(thread_dir)
    if body is None:
        where = "нет plan.md" if not plan.is_file() else "в plan.md нет раздела"
        return ("{0} «{1}» ({2}) — впиши минимум {3} строки: SOC-N · кому и каким "
                "каналом сдавать · с кем не дублировать").format(
                    where, HEADING, plan, MIN_LINES)
    live = [ln for ln in body if _is_live(ln)]
    if len(live) >= MIN_LINES:
        return None
    hints = sum(1 for ln in body if ln.strip().lstrip("-* ").startswith("<"))
    state = "пуст" if not live and not hints else (
        "в нём одни подсказки" if not live else
        "в нём {0} из {1} живых строк".format(len(live), MIN_LINES))
    return ("раздел «{0}» в {1} {2} — впиши минимум {3} строки вместо подсказок: "
            "SOC-N · кому и каким каналом сдавать · с кем не дублировать").format(
                HEADING, plan, state, MIN_LINES)


def _real_pulses(passport: Path) -> int:
    try:
        text = passport.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    n, inside = 0, False
    for ln in text.splitlines():
        if ln.startswith("## "):
            inside = ln.strip() == "## context"
            continue
        if inside and _PULSE.match(ln) and _MECHANICAL not in ln:
            n += 1
    return n


def thread_pulsed(thread_dir: Path) -> bool:
    """Был ли у нити хоть один настоящий пульс (любой её сессии)."""
    arcs = Path(thread_dir) / "arcs"
    if not arcs.is_dir():
        return False
    return any(_real_pulses(p / "arc.md") for p in arcs.iterdir()
               if p.is_dir() and (p / "arc.md").is_file())


def gate(session_dir: Path) -> Optional[Verdict]:
    """Старт-гейт «главного» для пульса сессии *session_dir*.

    * Нить новая (паспорт несёт ``plan-main: required``) и ещё не пульсовала:
      раздел пуст, в подсказках или стёрт → БЛОК с причиной.
    * Нить уже пульсовала, или она старая (метки нет) → предупреждение, но
      только на первом пульсе ЭТОЙ сессии — не на каждом.
    * Всё заполнено, или это не сессия нити → None.
    """
    session_dir = Path(session_dir)
    thread = session_dir.parent.parent
    try:
        from . import stream
        if not stream.is_thread(thread):
            return None
    except Exception:  # noqa: BLE001 — гейт не смеет уронить пульс
        return None
    why = problem(thread)
    if why is None:
        return None
    if required(thread) and not thread_pulsed(thread):
        return Verdict(True, "tide: старт-гейт не закрыт — {0}. Потом повтори "
                             "tide offload (решение Гриши № 24).".format(why))
    if _real_pulses(session_dir / "arc.md"):
        return None
    return Verdict(False, "tide: ⚠ {0} (решение Гриши № 24; пульс записан).".format(why))


__all__ = ["HEADING", "HINTS", "MIN_LINES", "PLAN_FILE", "PREFIX", "REQUIRED_FIELD",
           "REQUIRED_VALUE", "Verdict", "gate", "is_scaffold", "live_lines", "problem",
           "read", "required", "section", "thread_pulsed"]

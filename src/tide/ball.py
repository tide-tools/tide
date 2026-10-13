"""tide.ball — правило мяча (SOC-242, слово пользователя 12.10 TG 4586/4588).

У каждой сессии курсор — одно из четырёх:

* «мяч у меня: задача + срок» — SOC-N / работа N / письмо N и HH:MM (или поле ``eta``);
* «мяч у пользователя: вопрос TG N» — открытый вопрос во «Вопросах» доски;
* «отдых по слову пользователя»;
* «мяч у <адресат>: что ждём · письмо N» (SOC-265) — мяч у роли/нити/сессии, без срока:
  адресат, «что ждём» и номер письма обязательны, иначе это голое «жду».

«Жду/ждёт» без открытого вопроса — нарушение: Stop-хук ``offload-nudge`` блокирует ход
(:func:`tide.offload.cmd_offload_nudge`), ``tide offload --cursor`` предупреждает сразу,
сид хендоффа помечает такой курсор. Ожидание своего помощника, письма другой сессии или
прогона — законный «мяч у меня», если названы задача и срок.

Разбор чисто текстовый, без сети и без пульта: открытость вопроса TG N хук не сверяет
с ``dialog.jsonl`` — номер в курсоре и есть обязательство сессии. Модуль ничего не
импортирует из tide (его зовут хуки и сборщики сида).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

# Одна строка правила — в сиды (сид запуска и шапка хендоффа). Полный текст — в
# prompts/thread-rules.md, строка «мяч:».
# Без « · »: шапка сида режет строку ссылок по этому разделителю.
BALL_LINE = ("правило мяча: курсор — «мяч у меня: задача + срок» / «мяч у пользователя: "
             "вопрос TG N» / «мяч у <адресат>: что ждём, письмо N» / «отдых по слову "
             "пользователя»; «жду» без адресата и открытого вопроса ловит Stop-хук")

# Пометка в блоке курсора сида: принявшая первым пульсом выставляет мяч.
SEED_FLAG = "⚠ мяч не выставлен"

WAIT = "wait-no-question"
USER_NO_Q = "user-no-question"
MINE_INCOMPLETE = "mine-incomplete"
OTHER_INCOMPLETE = "other-incomplete"

_WAIT_RE = re.compile(
    r"(?<!\w)(?:жду|ждём|ждем|ждёт|ждет|ждут|ждать|ожида\w*|waiting|wait)(?!\w)",
    re.IGNORECASE)
_HUMAN = r"(?:пользовател\w*|гриш\w*|человек\w*|user)"
_USER_BALL_RE = re.compile(r"мяч\s+у\s+" + _HUMAN, re.IGNORECASE)
_TG_REF_RE = re.compile(r"(?<!\w)(?:tg|тг|telegram)\s*[№#]?\s*\d{2,}", re.IGNORECASE)
_MINE_RE = re.compile(r"мяч\s+у\s+меня", re.IGNORECASE)
_REST_RE = re.compile(r"отдых\w*\s+по\s+слову\s+" + _HUMAN, re.IGNORECASE)
# Четвёртая форма (SOC-265): «мяч у <адресат>: что ждём · письмо N». Срок не нужен.
_ANY_BALL_RE = re.compile(r"мяч\s+у\s+([\w@-]+)", re.IGNORECASE)
_LETTER_RE = re.compile(r"письм\w*\s*[№#]?\s*\d+", re.IGNORECASE)
_OTHER_FULL_RE = re.compile(
    r"мяч\s+у\s+[\w@-]+(?:\s+[\w@-]+)?\s*[:—–-]\s*(?P<what>[^·]*?\w[^·]*?)\s*[·,;—–-]?\s*"
    r"письм\w*\s*[№#]?\s*\d+", re.IGNORECASE)
_TASK_RE = re.compile(
    r"(?<!\w)(?:[A-Z]{2,}-\d+|(?:работ\w*|письм\w*|задач\w*|PR|цикл\w*)\s*[№#]?\s*\d+)",
    re.IGNORECASE)
_DEADLINE_RE = re.compile(r"(?<![\d:])\d{1,2}:\d{2}(?![\d:])")
# Цитаты не считаются: «чиню хук, который ловит «жду»» — не ожидание.
_QUOTED_RE = re.compile(r"«[^«»]*»|\"[^\"]*\"|“[^”]*”|„[^“”]*[“”]")


def _unquoted(text: str) -> str:
    prev = None
    while prev != text:  # «внешняя «вложенная»» — снимаем изнутри наружу
        prev, text = text, _QUOTED_RE.sub(" ", text)
    return text


def other_ball(text: str) -> Optional[str]:
    """Адресат «мяч у <адресат>» (не «у меня» и не «у пользователя»), иначе None. *text* — без цитат."""
    if _MINE_RE.search(text) or _USER_BALL_RE.search(text):
        return None
    m = _ANY_BALL_RE.search(text)
    return m.group(1) if m else None


def violation(cursor: str, *, eta: str = "") -> Optional[str]:
    """Вид нарушения правила мяча в курсоре *cursor*, или None (курсор законный).

    *eta* — поле паспорта ``eta:``: срок для «мяча у меня», если в курсоре нет HH:MM."""
    text = " ".join(_unquoted(cursor or "").split())
    if not text:
        return None
    if _REST_RE.search(text):
        return None
    if _USER_BALL_RE.search(text):
        return None if _TG_REF_RE.search(text) else USER_NO_Q
    if other_ball(text):
        return None if _OTHER_FULL_RE.search(text) else OTHER_INCOMPLETE
    if not _WAIT_RE.search(text):
        return None
    if _MINE_RE.search(text):
        has_task = bool(_TASK_RE.search(text))
        has_deadline = bool(_DEADLINE_RE.search(text) or (eta or "").strip())
        return None if (has_task and has_deadline) else MINE_INCOMPLETE
    return WAIT


def wait_deadlines(cursor: str, *, eta: str = "") -> List[str]:
    """Сроки законного ожидания «мяч у меня: SOC-N, жду … к HH:MM» (SOC-245): все HH:MM
    курсора, а без них — поле *eta* (ISO). [] — курсор не ожидание мяча у меня или
    нарушает правило. Время не разбираем: что значит HH:MM, решает вызывающий."""
    if violation(cursor, eta=eta) is not None:
        return []
    text = " ".join(_unquoted(cursor or "").split())
    if not (_MINE_RE.search(text) and _WAIT_RE.search(text)):
        return []
    marks = _DEADLINE_RE.findall(text)
    if marks:
        return marks
    return [eta.strip()] if (eta or "").strip() else []


_WHY = {
    WAIT: "курсор говорит «жду», а открытого вопроса нет — это простой",
    USER_NO_Q: "«мяч у пользователя» без номера вопроса — вопроса во «Вопросах» нет",
    OTHER_INCOMPLETE: "«мяч у <адресат>» без «что ждём» или письма N — это голое «жду», а не передача мяча",
    MINE_INCOMPLETE: "«мяч у меня» с ожиданием, но без задачи (SOC-N/работа N) или срока (HH:MM, --eta)",
}


def reason(kind: str, cursor: str, ref: str) -> str:
    """Текст требования: что не так и три законных выхода, с готовой командой."""
    return (
        "tide: правило мяча — {why}. Курсор: «{cur}».\n"
        "Выставь одно из четырёх (или просто возьми следующую работу):\n"
        "  • нужен выбор пользователя → вопрос во «Вопросы» через tg-alert (❓, «Почему:», "
        "«варианты: ⭐ …»), дальше продолжай по ⭐; ждать только необратимое/наружу:\n"
        "      tide offload {ref} --cursor \"мяч у пользователя: вопрос TG <N>\"\n"
        "  • ждёшь помощника / письмо / прогон → tide offload {ref} --cursor "
        "\"мяч у меня: SOC-N, жду <что> к HH:MM\"\n"
        "  • мяч у другой роли/сессии (менеджер, main, нить), письмо ей уже ушло → tide offload {ref} "
        "--cursor \"мяч у <адресат>: что ждём · письмо N\" (срок не нужен)\n"
        "  • пользователь сказал отдыхать → tide offload {ref} --cursor "
        "\"отдых по слову пользователя\"\n"
        "Правила: prompts/thread-rules.md, строки «вопрос пользователю» и «мяч»."
    ).format(why=_WHY.get(kind, _WHY[WAIT]), cur=" ".join((cursor or "").split()), ref=ref)


def cursor_of(passport: Path) -> str:
    """Тело ``## cursor`` паспорта одной строкой (заглушки ``<…>`` не в счёт); "" — нет."""
    try:
        text = Path(passport).read_text(encoding="utf-8")
    except OSError:
        return ""
    out = []
    inside = False
    for ln in text.splitlines():
        if ln.startswith("## "):
            if inside:
                break
            inside = ln[3:].strip().lower().startswith("cursor")
            continue
        if inside and ln.strip() and not ln.strip().startswith("<"):
            out.append(ln.strip())
    return " ".join(out)


MINE, USER, REST, OTHER, NONE = "mine", "user", "rest", "other", "none"


def side(cursor: str, *, eta: str = "") -> "tuple[str, str]":
    """У кого мяч по курсору (SOC-271, ``tide idle``/``tide why``) → ``(вид, подпись)``.

    *вид*: :data:`MINE` / :data:`USER` / :data:`REST` / :data:`OTHER` («мяч у
    менеджера/main/…») / :data:`NONE` (курсора нет или он без мяча). Нарушение правила (:func:`violation`) — в подписи «⚠ …»."""
    text = " ".join(_unquoted(cursor or "").split())
    if not text:
        return NONE, "—"
    bad = violation(cursor, eta=eta)
    flag = " ⚠ {0}".format(bad) if bad else ""
    if _REST_RE.search(text):
        return REST, "отдых"
    if _USER_BALL_RE.search(text):
        m = _TG_REF_RE.search(text)
        return USER, ("у пользователя ({0})".format(" ".join(m.group(0).split()))
                      if m else "у пользователя") + flag
    if _MINE_RE.search(text):
        return MINE, "у меня" + flag
    m = _ANY_BALL_RE.search(text)
    if m:
        return OTHER, "у " + m.group(1) + flag
    return NONE, ("не выставлен" + flag).strip()

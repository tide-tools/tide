"""tide.handoff_contract — контракт сида хендоффа: ОДНО место, где живут его блоки.

Источник — «Контракт хендоффа и приёмки» (continuity-audit, черновик v1 09.10,
SOC-101): двенадцать обязательных блоков и «главное» нити (решение Гриши № 24), в этом порядке. Раньше список жил в
пяти местах (скилл handoff, сборщик черновика, автосид, мерка доски, каркас
цели нити) — везде по семь блоков, и они расходились. Теперь все читают отсюда:

* :mod:`tide.launcher.seed_draft` — черновик (``tide handoffs draft``) и сборка;
* :mod:`tide.launcher.autohandoff` — автосид (``tide handoff continue --auto``);
* :mod:`tide.launcher.handoff_audit` — разделы аудита (:data:`AUDIT_SECTIONS`);
* :mod:`tide.arc.templates` — слои ``## where we are`` нити (:data:`THREAD_LAYERS`);
* доска — мерка сида (:data:`SEED_CONTRACT`);
* скилл ``skills/handoff/SKILL.md`` — таблица блоков (тест сверяет её с модулем).

Правило пустоты одно на все блоки: блок не выкидывается никогда; пусто по правде —
``— <почему пусто>`` (видимая дыра честнее выдуманного абзаца).

Модуль чистый: только данные и пара функций без диска — его можно импортировать
откуда угодно (в том числе доске, которая поднимается без движка).
"""

from __future__ import annotations

from typing import Dict, Iterable, NamedTuple, Optional, Sequence, Tuple

# Как доска мерит блок: прозовый — есть/нет, списочный — счётом пунктов.
PROSE, LIST = "проза", "список"

# Пустой блок: прочерк и причина — «— <почему пусто>».
EMPTY = "—"

# Сырой журнал — не больше этой доли тела сида; остальное сжато до строки и путь
# к полной записи (контракт, «сырой журнал»).
JOURNAL_SHARE = 0.30

# Порог передачи в токенах контекста: передача начинается здесь — не позже
# (контракт, «механизм передачи», п.1; одна передача случилась на 231 тыс.).
HANDOFF_TOKENS = 200_000


class Block(NamedTuple):
    """Один обязательный блок сида."""

    key: str                     # латинский ключ — для кода и --json, не меняется
    name: str                    # каноническое имя заголовка: ``## <name>``
    kind: str                    # PROSE / LIST — как его мерит доска
    aliases: Tuple[str, ...]     # старые имена (нижний регистр): сиды до контракта
    empty: str                   # почему пусто — по умолчанию, после «— »
    keep_empty: bool = True      # пустой блок НЕ выкидывается, пишется «— <почему>»


BLOCKS: Tuple[Block, ...] = (
    # Решение Гриши № 24: «главное — читать первым» из plan.md нити — отдельным
    # блоком и ПЕРВЫМ. Не подзаголовком «ожидания»: там слова Гриши с датой, а тут
    # маршрут — задача Linear, кому сдавать, с кем не дублировать; это ответ на
    # третий вопрос холодной сессии, и прочесть его надо до всего остального.
    Block("main", "главное", LIST,
          ("главное читать первым",),
          "«главное» в plan.md нити не записано — SOC-N, кому сдавать и с кем не "
          "дублировать неизвестны"),
    Block("expectation", "ожидание Гриши", PROSE,
          ("ожидание", "финал", "от а к б", "что выкачено", "итог"),
          "ожидание Гриши на диске не записано (слой «ожидание Гриши» нити пуст)"),
    Block("where", "где мы", PROSE,
          ("курсор", "состояние"),
          "курсора нет — сессия не пульсировала"),
    Block("experience", "опыт", LIST,
          ("отвергнуто", "петли", "грабли", "уроки"),
          "уроков не записано"),
    Block("decisions", "решения", LIST,
          (),
          "решений в нити нет"),
    Block("linear", "задачи Linear", LIST,
          ("linear",),
          "задач Linear в сиде не упомянуто"),
    Block("awaiting", "ждёт Гришу", LIST,
          ("ждет гришу",),
          "Гришу ничего не ждёт: вопросов и работ на проверке нет"),
    Block("next", "следующий шаг", LIST,
          ("дальше", "next step", "что дальше"),
          "следующего шага на диске нет (## next паспорта пуст)"),
    Block("inflight", "в работе прямо сейчас", LIST,
          ("в работе", "в полёте", "в полете"),
          "ничего не в полёте"),
    Block("unanswered", "неотвеченные реплики Гриши", LIST,
          ("неотвеченн", "хвост диалога"),
          "журнала диалога нет — реплик не видно"),
    Block("entry_map", "карта входа", LIST,
          ("карта", "reading-path", "что читать"),
          "карту входа не написали"),
    Block("environment", "окружение", PROSE,
          ("среда", "как работать", "как просил"),
          "окружение не унаследовано (во входном сиде его нет)"),
    Block("journal", "сырой журнал", LIST,
          ("журнал сессии", "сырой"),
          "пульса старше курсора нет"),
)

_BY_KEY: Dict[str, Block] = {b.key: b for b in BLOCKS}

# Имена заголовков в контрактном порядке — то, что пишет сборщик и мерит доска.
NAMES: Tuple[str, ...] = tuple(b.name for b in BLOCKS)


def block(key: str) -> Block:
    """Блок по латинскому ключу (``KeyError`` — нет такого)."""
    return _BY_KEY[key]


def name(key: str) -> str:
    """Каноническое имя заголовка блока по ключу."""
    return _BY_KEY[key].name


def norm(head: str) -> str:
    """Заголовок → сравнимое имя: нижний регистр, без хвоста в скобках, тире в
    пробел, пробелы схлопнуты. Дефис не трогаем — им живёт «reading-path»."""
    import re

    h = re.sub(r"\s*\([^)]*\)\s*$", "", (head or "").strip().lower())
    h = h.replace("—", " ").replace("–", " ")
    return re.sub(r"\s+", " ", h).strip()


def names_of(b: Block) -> Tuple[str, ...]:
    """Каноническое имя (нижний регистр) и все синонимы — чем блок узнаётся."""
    return (b.name.lower(),) + tuple(b.aliases)


def empty_line(b: Block, why: Optional[str] = None) -> str:
    """Строка пустого блока: ``— <почему пусто>``."""
    return "{0} {1}".format(EMPTY, (why or b.empty).strip())


def find_body(secs: Dict[str, Sequence[str]], key: str) -> list:
    """Тело блока *key* среди секций ``{заголовок: строки}`` (заголовки в нижнем
    регистре, как их отдаёт ``seed_draft.sections``): сначала по каноническому
    имени, потом по синонимам; совпадение — по началу заголовка. Нет — []."""
    b = _BY_KEY[key]
    heads = [(h, norm(h)) for h in secs]
    for want in names_of(b):
        for raw, h in heads:
            if h.startswith(want):
                return list(secs[raw])
    return []


# Мерка доски — ``(подпись, вид, синонимы)`` в контрактном порядке. Синонимы
# включают каноническое имя; доска матчит их ПОДСТРОКОЙ по нормализованному
# заголовку (:func:`norm`), один заголовок закрывает один блок.
SEED_CONTRACT: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = tuple(
    (b.name, b.kind, names_of(b)) for b in BLOCKS)


# Слои ``## where we are`` цели нити (каркас — :mod:`tide.arc.templates`): три
# блока сида, которые живут в нити, а не в сессии. Подзаголовок = имя блока.
THREAD_LAYERS: Tuple[Tuple[str, str], ...] = (
    ("expectation", "<что Гриша ждёт от нити — его словами, с датой>"),
    ("where", "<сделано · идёт · ждёт Гришу — по строке на каждое>"),
    ("experience", "<грабли, петли, «отвергнуто» — копится от сессии к сессии>"),
)


# Разделы ``tide handoffs audit``: ключ (для --json/доски, стабилен) → подпись.
# Подпись называет блок сида, куда источник должен был дойти; «понятая цель» —
# не блок сида, а приёмка (контракт, раздел 4).
_AUDIT = (
    ("main", "main", "plan.md нити"),
    ("cursor", "where", "курсор прошлой сессии"),
    ("context", "journal", "context-строки"),
    ("next", "next", ""),
    ("decisions", "decisions", ""),
    ("experience", "experience", ""),
    ("expectation", "expectation", "/ где мы"),
    ("dialog", "unanswered", "хвост диалога"),
)


def _audit_title(bkey: str, note: str) -> str:
    head = name(bkey)
    if not note:
        return head
    return "{0} {1}".format(head, note) if note.startswith("/") else \
        "{0}: {1}".format(head, note)


AUDIT_SECTIONS: Tuple[Tuple[str, str], ...] = tuple(
    (k, _audit_title(b, note)) for k, b, note in _AUDIT) + (("understood", "понятая цель"),)


def missing(heads: Iterable[str]) -> Tuple[str, ...]:
    """Канонические имена блоков, которых нет среди заголовков *heads*."""
    have = [norm(h) for h in heads]
    out = []
    for b in BLOCKS:
        if not any(h.startswith(w) for h in have for w in names_of(b)):
            out.append(b.name)
    return tuple(out)


__all__ = ["AUDIT_SECTIONS", "BLOCKS", "Block", "EMPTY", "HANDOFF_TOKENS",
           "JOURNAL_SHARE", "LIST", "NAMES", "PROSE", "SEED_CONTRACT", "THREAD_LAYERS",
           "block", "empty_line", "find_body", "missing", "name", "names_of", "norm"]

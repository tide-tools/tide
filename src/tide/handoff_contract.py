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
``— <почему пусто>`` (видимая дыра честнее выдуманного абзаца). У блоков, которые
собираются из источников, причина — «источник пуст: <какие источники проверены>»,
а не утверждение о мире (SOC-101 F23: «Гришу ничего не ждёт» и «в полёте ничего»
были ложью — ждали три пункта, в полёте было четыре).

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
          "источник пуст: слой «ожидание Гриши» в goal-файле нити, метка «Ожидание "
          "Гриши:» в пульсах сессии"),
    Block("where", "где мы", PROSE,
          ("курсор", "состояние"),
          "источник пуст: слой «где мы» нити, курсор и пульс сессии"),
    Block("experience", "опыт", LIST,
          ("отвергнуто", "петли", "грабли", "уроки"),
          "источник пуст: метки «Опыт:» в пульсах сессии, слой «опыт» нити, опыт "
          "входного сида, кандидаты сессии"),
    Block("decisions", "решения", LIST,
          (),
          "источник пуст: метки «Решения:» в пульсах сессии, decisions.md нити, "
          "нить-предшественница"),
    Block("linear", "задачи Linear", LIST,
          ("linear",),
          "задач Linear в сиде не упомянуто"),
    Block("awaiting", "ждёт Гришу", LIST,
          ("ждет гришу",),
          "источник пуст: метки «Ждёт Гришу:» в пульсах сессии, слой «ждёт Гришу» "
          "нити, вопросы доски, работы на проверке"),
    Block("next", "следующий шаг", LIST,
          ("дальше", "next step", "что дальше"),
          "следующего шага на диске нет (## next паспорта пуст)"),
    Block("inflight", "в работе прямо сейчас", LIST,
          ("в работе", "в полёте", "в полете"),
          "источник пуст: метки «В полёте:» в пульсах сессии, открытые пункты работ "
          "нити"),
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


# Метки в ноте пульса (``tide offload "…"``, SOC-101 F22): самая полная ручная
# передача пишется пульсом «ПЕРЕДАЧА. Ждёт Гришу: … В полёте: … Опыт: … Решения: …»,
# и раньше сборщик видел в нём одну строку журнала. Метка → блок сида. Регистр не
# важен, меток в ноте сколько угодно; текст метки — до следующей метки.
# ``(блок, (варианты метки — регэкспы без «:»))``.
PULSE_LABELS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("awaiting", (r"жд[её]т\s+гриш[уи]", r"открытые\s+решения\s+гриши")),
    ("inflight", (r"в\s+пол[её]те",)),
    ("experience", (r"опыт", r"урок[иа]?")),
    ("decisions", (r"решени[еяй](?:\s+(?:сессии|гриши))?",)),
    ("expectation", (r"ожидани[ея]\s+гриши",)),
)

# Метки, которые не идут ни в какой блок, но ЗАКАНЧИВАЮТ текст предыдущей —
# иначе «В полёте: ничего. Адресаты: …» унёс бы адресатов в «в работе».
PULSE_STOP_LABELS: Tuple[str, ...] = (
    r"где\s+мы", r"адресаты", r"таблица", r"сделано", r"дальше", r"курсор",
)


def _label_re():
    import re

    alts = []
    for key, pats in PULSE_LABELS:
        for i, p in enumerate(pats):
            alts.append(r"(?P<{0}_{1}>{2})".format(key, i, p))
    for i, p in enumerate(PULSE_STOP_LABELS):
        alts.append(r"(?P<stop_{0}>{1})".format(i, p))
    # Метка стоит в начале ноты или после конца фразы/тире; за ней — «:», между
    # ними может быть уточнение в скобках («РЕШЕНИЕ ГРИШИ (через …, 09.10):»).
    return re.compile(
        r"(?:^|(?<=[.;!?»)\]—])\s+|(?<=\s—)\s)(?:{0})(?:\s*\([^()]*\))?\s*:\s*".format(
            "|".join(alts)), re.IGNORECASE)


_LABEL_RE = None


def pulse_labels(note: str) -> Dict[str, list]:
    """Нота пульса → ``{ключ блока: [куски текста]}`` по меткам (SOC-101 F22).

    Кусок — текст после метки до следующей метки (любой, в т.ч. стоп-метки), без
    хвостовой точки. Нет меток — ``{}``. Чистая функция, без диска.
    """
    global _LABEL_RE
    if _LABEL_RE is None:
        _LABEL_RE = _label_re()
    text = " ".join((note or "").split())
    hits = list(_LABEL_RE.finditer(text))
    out: Dict[str, list] = {}
    for i, m in enumerate(hits):
        group = next(g for g, v in m.groupdict().items() if v is not None)
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        piece = text[m.end():end].strip().rstrip(".;").strip()
        if group.startswith("stop_") or not piece:
            continue
        out.setdefault(group.rsplit("_", 1)[0], []).append(piece)
    return out


def pulse_head(note: str) -> str:
    """Нота пульса без размеченных кусков — то, что не ушло ни в один блок."""
    global _LABEL_RE
    if _LABEL_RE is None:
        _LABEL_RE = _label_re()
    text = " ".join((note or "").split())
    m = _LABEL_RE.search(text)
    return text if m is None else text[:m.start()].strip()


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
           "JOURNAL_SHARE", "LIST", "NAMES", "PROSE", "PULSE_LABELS", "PULSE_STOP_LABELS",
           "SEED_CONTRACT", "THREAD_LAYERS", "block", "empty_line", "find_body", "missing",
           "name", "names_of", "norm", "pulse_head", "pulse_labels"]

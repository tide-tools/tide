"""tide.launcher.seed_draft — ``tide handoffs draft``: харнес собирает ЧЕРНОВИК сида.

Боль (владелец, работа 15 / кандидат 153): дистилл хендоффа пишет САМАЯ уставшая
сессия — в конце, из памяти, руками. А материал уже лежит на диске: пульс в
паспорте (``tide offload``), ``decisions.md`` нити, живые работы нити, кандидаты
прогона, входной сид. Разбор живого сида 144-shov показал: блоки «решения», «дальше»,
«окружение» в нём почти дословно повторяли диск — ручной труд был только в
«опыте» и «карте входа».

Поэтому механическую половину берёт движок: ``draft`` компонует ЧЕРНОВИК по
контракту сида — двенадцать блоков из :mod:`tide.handoff_contract` (SOC-101; до
него — семь, решение 05 нити edinyy-sloy) — и кладёт его в
``<сессия>/workspace/seed-draft.md`` + печатает в stdout. Уставший агент
КУРИРУЕТ: режет лишнее, пишет «карту входа», «в работе прямо сейчас» и
рекомендации к «ждёт Гришу» (там нужна голова держащего контекст) и сверяет
дифф окружения.

Черновик — НЕ сид: он ничего не вешает и не отправляет. Очередь офферов,
статусы и `input/` целевой сессии эта команда не трогает — финальный сид туда
кладёт агент по скиллу handoff, как и раньше.

Два слоя как везде: чистые функции (``render_draft``, ``block_*``) — без диска и
argparse, снапшот-тестируемые; ``build_draft``/``draft`` — чтение и запись;
``_cmd_draft`` — тонкий CLI-хендлер, который вешает ``tide handoffs draft``.
"""

from __future__ import annotations

import os
import re

from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .. import fields, handoff_contract as contract, io as _io, offload, paths, \
    placeholders, resolve, slug
from ..arc import dialog as _dialog, stream as _stream, work as _work
from ..arc.stream import StreamError

DRAFT_FILENAME = "seed-draft.md"
WORKSPACE_DIRNAME = "workspace"
INPUT_SEED_NAME = "handoff-seed.md"

# Контракт сида — имена и ПОРЯДОК блоков живут в tide.handoff_contract (одно место
# на движок, доску и скилл): доска мерит сид по этим подписям, поэтому имя блока —
# не стиль, а интерфейс.
BLOCKS = contract.NAMES

# Имена блоков, которыми оперирует сборка (ключ → заголовок).
MAIN = contract.name("main")
EXPECTATION = contract.name("expectation")
WHERE = contract.name("where")
EXPERIENCE = contract.name("experience")
DECISIONS = contract.name("decisions")
LINEAR = contract.name("linear")
AWAITING = contract.name("awaiting")
NEXT = contract.name("next")
INFLIGHT = contract.name("inflight")
UNANSWERED = contract.name("unanswered")
ENTRY_MAP = contract.name("entry_map")
ENVIRONMENT = contract.name("environment")
JOURNAL = contract.name("journal")

# Пустой источник → видимая дыра, а не выдуманный абзац: «— <почему пусто>».
EMPTY = contract.EMPTY

# Сколько последних реплик диалога нити класть в «неотвеченные реплики Гриши».
DIALOG_TAIL = 12

# Сколько последних пульс-строк ``## context`` тащить в курсор: хвост, а не вся
# память сессии — сид приземляет на следующий шаг, полную запись держит арка.
PULSE_TAIL = 5

_HEAD_RE = re.compile(r"^##\s+(.+?)\s*$")
_NEXT_SEP = "·"
# «·» inside a session's name is not a list separator (SOC-101 F8): «проект — нить ·
# 04», «ao-and-order · 04», «… · 01 · tide-stack-8» (SOC-72) stay whole.
_NAME_DOT = re.compile(r"\s·\s(?=\d{2}\b(?:\s·|\s*$|[,;:)]))"
                       r"|(?<=\b\d{2})\s·\s(?=[A-Za-z][\w.-]*-\d+\b)")
_KEEP = "\x00"
# «проект — нить · NN» с любым хвостом (SOC-101): «management — main · 02 через ao
# send», «… · NN (management-11)» — номер сессии стоит за именем вида «X — Y»,
# и « · » перед ним имени не рвёт, что бы ни шло следом.
_DASH_NAME_DOT = re.compile(r"(?<=\S)(\s—\s[\w.@-]+)\s·\s(?=(?:\d{2}|NN)\b)")


def _keep_name_dots(line: str) -> str:
    """Спрятать « · » внутри имён сессий, чтобы ``split`` по « · » их не рвал."""
    line = _DASH_NAME_DOT.sub(lambda m: m.group(1) + _KEEP, line)
    return _NAME_DOT.sub(_KEEP, line)

# Сырой журнал пульса в сиде — не больше этой доли тела (SOC-101 F5): старое сжато
# до строки, но не тонет — счёт и путь к полной записи в паспорте.
JOURNAL_SHARE = contract.JOURNAL_SHARE
JOURNAL_CLIP = 160
JOURNAL_GIST = 72


class SeedDraftError(StreamError):
    """Ошибка пользователю (нет такой сессии, некуда целиться).

    Наследует :class:`tide.arc.stream.StreamError` — ``cli.main`` ловит её тем же
    ``except`` (печатает ``tide: …``, ненулевой код).
    """


# --- разбор паспорта/сида (чистое) ------------------------------------------

def sections(text: str) -> Dict[str, List[str]]:
    """Секции ``## …`` текста → ``{заголовок в нижнем регистре: строки тела}``.

    Заголовки берём как есть (``cursor — resume here``, ``окружение``): точного
    совпадения от паспорта требовать нельзя, поэтому ищем потом по префиксу
    (:func:`section_body`).
    """
    out: Dict[str, List[str]] = {}
    name: Optional[str] = None
    for line in (text or "").splitlines():
        m = _HEAD_RE.match(line)
        if m:
            name = m.group(1).strip().lower()
            out.setdefault(name, [])
        elif name is not None:
            out[name].append(line)
    return out


def section_body(secs: Dict[str, List[str]], prefix: str) -> List[str]:
    """Тело первой секции, чей заголовок начинается с *prefix* (иначе []).

    По префиксу, а не по точной строке: паспорт пишет ``## cursor — resume here``,
    а зовут его «cursor» — точное имя жило бы в двух местах и разошлось.
    """
    want = prefix.strip().lower()
    for head, body in secs.items():
        if head.startswith(want):
            return list(body)
    return []


def live_lines(body: Sequence[str]) -> List[str]:
    """Непустые строки тела БЕЗ шаблонных ``<…>``-заглушек.

    Свежий паспорт несёт `<where this session left off…>` — это не содержимое, и
    в черновик оно попасть не должно (то же правило, что у ``offload``).
    """
    return [
        ln.rstrip() for ln in body
        if ln.strip() and not ln.strip().startswith("<")
    ]


def is_hole(line: str) -> bool:
    """Строка пустого блока (``—`` или ``— <почему пусто>``) — не содержимое."""
    return line.strip(" -\t").startswith(EMPTY) or not line.strip(" -\t")


def layers(text: str) -> Dict[str, List[str]]:
    """``## where we are`` паспорта нити по слоям ``###`` → живые строки.

    Ключ — подзаголовок в нижнем регистре; заглушки ``<…>`` вырезаны, слой из
    одной заглушки в словарь не попадает (он не «пуст», в нём просто не писали)."""
    out: Dict[str, List[str]] = {}
    head = ""
    for ln in section_body(sections(text), "where we are"):
        if ln.startswith("### "):
            head = ln[4:].strip().lower()
            continue
        if ln.strip() and not ln.strip().startswith("<"):
            out.setdefault(head, []).append(ln.rstrip())
    return out


def layer(lays: Dict[str, List[str]], key: str) -> List[str]:
    """Строки слоя нити, названного блоком контракта *key* (по началу подзаголовка:
    «### где мы — 9 окт …» тоже «где мы»)."""
    want = contract.name(key).lower()
    return [ln for head, body in lays.items() if head.startswith(want) for ln in body]


def layer_named(lays: Dict[str, List[str]], *heads: str) -> List[str]:
    """Строки слоя нити по подзаголовку (вне контракта: «### ждёт Гришу»)."""
    return [ln for head, body in lays.items()
            if any(head.startswith(h) for h in heads) for ln in body]


def prose_lines(body: Sequence[str]) -> List[str]:
    """Как :func:`live_lines`, но заглушка ``<…>`` может тянуться на несколько строк
    (``## summary`` свежего паспорта) — вырезается целиком."""
    text = re.sub(r"(?m)^[ \t]*<[^<>]*>[ \t]*$", "", "\n".join(body))
    return live_lines(text.splitlines())


# --- блоки (чистое) ---------------------------------------------------------

_NOTE_STAMP = re.compile(r"^-?\s*(\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})(?::\d{2})?)\s*[—-]\s*")

# Метки, чьё содержимое — СОСТОЯНИЕ (его переписывает каждый новый пульс): берётся
# только из самого свежего пульса с этой меткой. Опыт и решения копятся — из всех.
_STATE_LABELS = ("awaiting", "inflight", "expectation")


def pulse_notes(context: Sequence[str]) -> List["tuple[str, str]"]:
    """Пульс-строки ``## context`` → ``[(ЧЧ:ММ, нота)]`` в порядке записи."""
    out = []
    for ln in live_lines(context):
        raw = ln.strip()
        m = _NOTE_STAMP.match(raw)
        if m:
            out.append((m.group(2), raw[m.end():].strip()))
        else:
            out.append(("", raw.lstrip("- ").strip()))
    return out


def labeled_pulses(context: Sequence[str]) -> Dict[str, List[str]]:
    """Размеченное в нотах пульсов → строки блоков сида (SOC-101 F22).

    «Ждёт Гришу: … В полёте: … Опыт: … Решения: …» в ноте ``tide offload`` раньше
    доходили до сида строкой журнала (обрезанной до 72 знаков). Теперь кусок
    метки — пункты своего блока: кусок режется по «; » на пункты, у каждого —
    время пульса. Состояние (ждёт Гришу, в полёте, ожидание) — из самого свежего
    пульса с меткой; опыт и решения — из всех, свежие сверху, без повторов.
    """
    out: Dict[str, List[str]] = {}
    for stamp, note in reversed(pulse_notes(context)):
        for key, pieces in contract.pulse_labels(note).items():
            if key in _STATE_LABELS and key in out:
                continue  # состояние уже взято из более свежего пульса
            lines = out.setdefault(key, [])
            for piece in pieces:
                for item in re.split(r";\s+", piece):
                    item = item.strip().rstrip(".;").strip()
                    if not item:
                        continue
                    line = "- {0}{1}".format(item, " (пульс {0})".format(stamp) if stamp else "")
                    if not any(item == x.split(" (пульс ")[0][2:] for x in lines):
                        lines.append(line)
    return out


def _merge_lines(*groups: Sequence[str]) -> List[str]:
    """Склеить источники блока по порядку, без повторов и без строк-дыр."""
    out: List[str] = []
    for g in groups:
        for ln in g:
            if ln.strip() and not is_hole(ln) and ln not in out:
                out.append(ln)
    return out


def block_final(thread_goal: str, session_goal: str) -> List[str]:
    """Финал: цель нити + цель этой сессии, 1–2 строки.

    Слепую цель (плейсхолдер / равна слагу) не печатаем — «идём к: handoff» на
    нити ``01-@handoff`` читается как ложь. Цель сессии наследуется от нити при
    рождении, поэтому дубль сворачиваем в одну строку.
    """
    lines: List[str] = []
    if thread_goal:
        lines.append("нить: {0}".format(thread_goal))
    if session_goal and session_goal != thread_goal:
        lines.append("этот шаг: {0}".format(session_goal))
    return lines


def _work_order(rec: Dict[str, object]) -> "tuple[int, str]":
    """Ключ порядка работ — по НОМЕРУ, а не по строке: «9» младше «100»."""
    num = str(rec.get("num") or "")
    return (int(num) if num.isdigit() else 0, num)


def work_line(rec: Dict[str, object]) -> str:
    """Живая работа одной строкой: номер · имя · статус · что ждёт.

    ``- работа 28 Кандидаты на доске — review, ждёт закрытия словом``
    ``- работа 29 Мелочи пульта — taken 1/4, курсор на пункте 2``

    Всё — из карточки, ничего из головы: счёт по согласованному чеклисту,
    «курсор на пункте» только когда исполнитель его ПОСТАВИЛ (``at:``). Взятой
    работе без курсора добавить нечего — пустая фраза врала бы про место.
    Слово «работа» в начале не украшение: в сиде рядом лежат номера кандидатов
    и решений, и голое «28» читатель отнесёт не туда.

    «Ждёт исполнителя» — не синоним статуса open, а ФАКТ, и говорится только
    когда он верен: никто не числится и ничего не сделано. Паспорт бывает
    рассогласован (кандидат 168: open, чужой ``taken-by``, 1/1 сделано), и
    записка, поверившая одному статусу, отправляла принимающую сессию делать
    сделанное. Сходится не всё — строка зовёт читателя на карточку вместо того,
    чтобы выбрать за него, чему верить.
    """
    status = str(rec.get("status") or "").strip()
    total = int(rec.get("total") or 0)
    done = int(rec.get("done") or 0)
    who = str(rec.get("taken_by") or "").strip()
    head = "- работа {0} {1} — {2}".format(rec.get("num"), rec.get("title"), status)
    if status != "review" and total:
        head += " {0}/{1}".format(done, total)
    if status == "review":
        note = "ждёт закрытия словом"
    elif status == "open":
        if who:
            note = "числится за {0}, статус не сходится — смотри карточку".format(who)
        elif done:
            note = "исполнителя нет, а пункты сделаны — статус не сходится, смотри карточку"
        else:
            note = "ждёт исполнителя"
    elif int(rec.get("at") or 0):
        note = "курсор на пункте {0}".format(rec["at"])
    else:
        note = ""
    return head + (", " + note if note else "")


def block_cursor(cursor: Sequence[str], context: Sequence[str],
                 *, works: Sequence[Dict[str, object]] = (),
                 tail: int = PULSE_TAIL, clip: int = 0) -> List[str]:
    """Курсор: тело ``## cursor``, живые работы нити, хвост пульса — СВЕЖИЕ СВЕРХУ.

    Курсор — где мы стоим; работы под ним — что нить держит открытым прямо
    сейчас (кандидат 161: записка должна нести состояние работ сама, иначе
    принимающая сессия идёт за ним на доску); пульс-строки ниже — как мы сюда
    пришли. ``offload`` дописывает пульс в конец, поэтому хвост разворачиваем:
    новое сверху (как вся история у tide), и работы тем же порядком — старшим
    номером вверх.
    """
    lines: List[str] = []
    cur = live_lines(cursor)
    if cur:
        lines.append(cur[0])
    for rec in sorted(works, key=_work_order, reverse=True):
        lines.append(work_line(rec))
    pulse = live_lines(context)
    for ln in reversed(pulse[-tail:] if tail > 0 else pulse):
        ln = ln if ln.lstrip().startswith("-") else "- {0}".format(ln)
        if clip and len(ln) > clip:
            ln = ln[: clip - 1].rstrip() + "…"
        lines.append(ln)
    return lines


def raw_pulse_chars(context: Sequence[str], tail: int = PULSE_TAIL, clip: int = 0) -> int:
    """Сколько знаков сырого пульса несёт курсор (его хвост из *tail* строк)."""
    pulse = live_lines(context)
    return sum(min(len(ln) + 3, (clip + 1) if clip else len(ln) + 3)
               for ln in (pulse[-tail:] if tail > 0 else pulse))


LINEAR_URL_ENV = "TIDE_LINEAR_ISSUE_URL"
LINEAR_URL_DEFAULT = "https://linear.app/socaseinpoint/issue/{key}"
_ISSUE_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b")


def block_links(text: str, prefixes: Sequence[str] = ("SOC",)) -> List[str]:
    """Каждая задача Linear, упомянутая в сиде, — один раз со ссылкой.

    Голый «SOC-62» новой сессии ничего не говорит, пока она не сходит в Linear
    искать, где это. Адрес — ``$TIDE_LINEAR_ISSUE_URL`` (шаблон с ``{key}``)."""
    tmpl = (os.environ.get(LINEAR_URL_ENV) or "").strip() or LINEAR_URL_DEFAULT
    seen: List[str] = []
    for key in _ISSUE_RE.findall(text or ""):
        if key.split("-", 1)[0] in prefixes and key not in seen:
            seen.append(key)
    seen.sort(key=lambda k: int(k.rsplit("-", 1)[1]))
    return ["- {0} — {1}".format(k, tmpl.format(key=k)) for k in seen]


def block_awaiting(questions: Sequence[str],
                   works: Sequence[Dict[str, object]] = ()) -> List[str]:
    """Ждёт Гришу: вопросы к нему без ответа и работы на проверке — списком, у
    каждого пункта «предлагаю …» (контракт). Рекомендацию по сути машина не знает —
    она называет жест; курирующий меняет его на своё мнение."""
    out = ["- вопрос {0} — предлагаю: ответь на доске".format(q) for q in questions]
    for rec in sorted(works, key=_work_order, reverse=True):
        if str(rec.get("status") or "").strip() == "review":
            out.append("- работа {0} {1} на проверке — предлагаю: проверь и закрой "
                       "словом".format(rec.get("num"), rec.get("title")))
    return out


def block_next(nxt: Sequence[str]) -> List[str]:
    """Дальше: ``## next`` паспорта, разбитый по « · » в пункты списка.

    ``tide offload --next`` пишет 1–3 шага одной строкой через « · » — это уже
    список, просто слипшийся; доска мерит блок счётом пунктов.
    """
    items: List[str] = []
    for line in live_lines(nxt):
        for part in _keep_name_dots(line).split(_NEXT_SEP):
            part = part.replace(_KEEP, " · ").strip(" -\t")
            if part:
                items.append("- {0}".format(part))
    return items


def decision_done(rec: Dict[str, object], closed_works: Sequence[str] = ()) -> str:
    """Почему решение выполнено (пусто — не выполнено), SOC-101 F7.

    Не только явная строка ``done:``: пруф (``proof:``) — это тоже запись о
    сделанном, а решение, чья работа (``work:``) закрыта, выполнено её закрытием.
    """
    from ..arc import decision  # lazy: decision reaches up into cli (known debt)

    if decision.is_done(rec):
        return "done"
    proof = str(rec.get("proof") or "").strip()
    if proof and proof not in ("—", "-"):
        return "done по пруфу"
    work = str(rec.get("work") or "").strip()
    if work and work not in ("—", "-") and any(
            w == work or w.split("-", 1)[0] == work for w in closed_works):
        return "done: работа {0} закрыта".format(work)
    return ""


def block_decisions(records: Sequence[Dict[str, object]],
                    closed_works: Sequence[str] = ()) -> List[str]:
    """Решения нити: ``NN — what (status)``, сначала accepted, потом done.

    Сначала то, что В СИЛЕ и ещё НЕ выполнено — принимающая сессия чаще всего
    ломает именно это, потому что оно выглядит как незанятое место. Следом
    выполненное: оно тоже анти-перерешивание, но спорить с ним уже дороже.
    ``superseded``/``dropped`` — история, её в сид не тащим. Порядок внутри
    группы — по номеру: номер и есть хронология.
    """
    from ..arc import decision  # lazy: decision reaches up into cli (known debt)

    live = [r for r in records
            if decision.normalize_status(str(r.get("status") or "")) == decision.ACCEPTED]
    out: List[str] = []
    marks = {id(r): decision_done(r, closed_works) for r in live}
    for done in (False, True):
        for rec in live:
            if bool(marks[id(rec)]) is not done:
                continue
            what = str(rec.get("what") or "").strip() or str(rec.get("slug") or "")
            out.append("- {0} — {1} ({2})".format(
                rec.get("num"), what, marks[id(rec)] or "not done"))
    return out


def _nums_phrase(nums: Sequence[str]) -> str:
    """``146–153`` для сплошного ряда, иначе перечисление ``146, 149, 153``.

    Диапазон печатаем ТОЛЬКО когда ряд сплошной: «146–153» с дыркой внутри —
    красиво и неправда, а сид читают как факт.
    """
    ordered = sorted(set(nums), key=lambda n: (int(n), n))
    if len(ordered) > 1 and int(ordered[-1]) - int(ordered[0]) == len(ordered) - 1:
        return "{0}–{1}".format(ordered[0], ordered[-1])
    return ", ".join(ordered)


def block_experience(candidate_nums: Sequence[str],
                     inherited: Sequence[str] = (),
                     candidate_gists: Optional[Dict[str, str]] = None) -> List[str]:
    """Опыт: унаследованный из входного сида + кандидаты сессии + каркас для агента.

    Опыт копится от сессии к сессии (SOC-101 F3): строки «опыта» входного сида
    переходят дальше как есть, без подсказок куратора. Кандидат — строкой с его
    сутью, а не голым номером: сид читают без контекста (SOC-74). Грабли, петли и
    «отвергнуто» этой сессии пишет курирующий — подсказка, а не выдуманный текст.
    """
    out: List[str] = list(dict.fromkeys(
        ln for ln in live_lines(inherited) if "<!--" not in ln and not is_hole(ln)))
    gists = candidate_gists or {}
    if candidate_nums and gists:
        for num in sorted(set(candidate_nums), key=lambda n: (int(n), n)):
            line = "- кандидат {0} — {1}".format(num, gists.get(num) or "без описания")
            if line not in out:
                out.append(line)
    elif candidate_nums:
        out.append("- кандидаты сессии: {0}".format(_nums_phrase(candidate_nums)))
    out.append(
        "- {0}  <!-- курируй: грабли · петли · «отвергнуто» — <путь> — <почему нет> -->"
        .format(EMPTY)
    )
    return out


def block_entry_map(inherited: Sequence[str] = ()) -> List[str]:
    """Карта входа: унаследованная из входного сида + каркас для пишущего.

    Движок её не собирает: пути он бы насыпал, но сид требует ПОРЯДКА чтения и
    «зачем» — знание того, кто держит контекст. Карту, которую уже написали,
    передаём дальше (SOC-101 F4), куратор правит дифф.
    """
    kept = [ln for ln in live_lines(inherited)
            if "<!--" not in ln and not is_hole(ln)]
    return kept + [
        "- {0}  <!-- пишет агент: что читать и зачем, в порядке чтения; "
        "кодовый шаг — файл/модуль, команда тестов, процедура выката -->".format(EMPTY)
    ]


_PULSE_STAMP = re.compile(r"^-?\s*\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})(?::\d{2})?\s*[—-]?\s*")


def pulse_gist(line: str, limit: int = JOURNAL_GIST) -> str:
    """Строка пульса сжато: ``- ЧЧ:ММ — первая фраза`` до *limit* знаков.

    Дата у пульсов одной сессии почти всегда одна, а первая фраза — это и есть
    сделанный шаг; хвост строки (детали, пути) лежит в паспорте целиком."""
    raw = line.strip().lstrip("-").strip()
    m = _PULSE_STAMP.match("- " + raw)
    stamp, rest = (m.group(1) + " — ", raw[m.end() - 2:]) if m else ("", raw)
    first = re.split(r"(?<=[.;!?])\s|\s·\s", rest, maxsplit=1)[0].strip()
    text = "- " + stamp + first
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def block_journal(context: Sequence[str], *, budget: int, source: str = "",
                  tail: int = PULSE_TAIL) -> List[str]:
    """Пульс старше хвоста курсора — не тонет (SOC-101 F5).

    Курсор несёт последние *tail* строк; всё, что старше, идёт сюда свежим сверху:
    целиком, пока влезает в *budget* знаков, иначе сжатой строкой
    (:func:`pulse_gist`); что не влезло и так — счётом и путём к полной записи
    (*source*). Пусто — когда старше хвоста ничего нет.
    """
    older = live_lines(context)
    older = older[:-tail] if tail > 0 else []
    if not older:
        return []
    full = [ln if ln.lstrip().startswith("-") else "- {0}".format(ln)
            for ln in reversed(older)]
    if sum(len(ln) + 1 for ln in full) <= budget:
        return full  # влезает целиком — ничего не сжимаем
    out: List[str] = []
    pointer = "- (сжато строк: 00, не вошло: 00 — полностью: {0})".format(
        source or "## context паспорта сессии")
    used, clipped, left = len(pointer) + 1, 0, 0  # указатель тоже в бюджете
    for ln in full:  # не влезает — ВСЕ строки сжатыми, чтобы дошло больше шагов
        short = pulse_gist(ln)
        if used + len(short) + 1 > budget:
            left += 1
            continue
        clipped += short != ln
        out.append(short)
        used += len(short) + 1
    if clipped or left:
        out.append("- (сжато строк: {0}, не вошло: {1} — полностью: {2})".format(
            clipped, left, source or "## context паспорта сессии"))
    return out


def block_environment(env: Sequence[str]) -> List[str]:
    """Окружение: секция входного сида этой сессии, как есть.

    Между сессиями окружение почти не меняется — наследуем целиком, а курирующий
    правит только дифф (что умерло / что родилось).
    """
    return live_lines(env)


# --- сборка (чистое) --------------------------------------------------------

def draft_header(session_name: str, thread_name: str) -> str:
    """Шапка черновика: чей он и что с ним делать (две строки, без церемоний)."""
    return (
        "# черновик сида — {0} · нить {1}\n"
        "\n"
        "Собрал харнес (`tide handoffs draft`) из накопленного на диске. Это ЧЕРНОВИК:\n"
        "куратор режет лишнее, пишет «карту входа» и «в работе прямо сейчас», сверяет\n"
        "дифф «окружения»."
    ).format(session_name, thread_name)


def render_draft(bodies: Dict[str, List[str]], *, header: str = "") -> str:
    """Собрать сид: все блоки контракта (:mod:`tide.handoff_contract`), по порядку.

    ОДНО место, где живут порядок блоков и правило пустоты: блок без источника
    печатается ``— <почему пусто>``. Секцию не выкидываем никогда — доска мерит
    СОСТАВ, и видимая дыра честнее выдуманного абзаца.
    """
    lines: List[str] = []
    if header:
        lines += [header, ""]
    for b in contract.BLOCKS:
        body = [ln for ln in bodies.get(b.name, []) if ln.strip()] \
            or [contract.empty_line(b)]
        lines += ["## {0}".format(b.name), "", *body, ""]
    return "\n".join(lines).rstrip() + "\n"


def seal(bodies: Dict[str, List[str]], *, context: Sequence[str], header: str = "",
         source: str = "", clip: int = 0) -> str:
    """Дописать вычисляемые блоки и отрендерить: сырой журнал и задачи Linear.

    Сырой журнал (хвост пульса в «где мы» + пульс старше него) — не больше
    :data:`JOURNAL_SHARE` тела: J ≤ (share·R − T) / (1 − share), R — тело без
    журнала, T — хвост пульса в «где мы». Задачи Linear — каждая упомянутая в
    сиде, со ссылкой."""
    out = dict(bodies)
    out[JOURNAL], out[LINEAR] = [], []
    share = JOURNAL_SHARE
    rest = len(render_draft(out, header=header))
    tail = raw_pulse_chars(context, clip=clip)
    budget = max(0, int((share * rest - tail) / (1 - share)))
    for _ in range(8):  # формула — оценка; доля проверяется по готовому тексту
        out[JOURNAL] = block_journal(context, budget=budget, source=source)
        out[LINEAR] = block_links(render_draft(out, header=header))
        text = render_draft(out, header=header)
        over = tail + sum(len(ln) + 1 for ln in out[JOURNAL]) - share * len(text)
        if over <= 0 or budget == 0:
            break
        budget = max(0, budget - int(over) - 1)
    return text


# --- чтение диска -----------------------------------------------------------

def _read(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _goal_of(entry: Path) -> str:
    """Живая цель арки (пусто, когда цель слепая — плейсхолдер/равна слагу)."""
    raw = (fields.read_field(_stream.passport_path(entry), "goal") or "").strip()
    if placeholders.is_blind_goal(raw, slug.entry_slug(entry.name)):
        return ""
    return raw


def _thread_decisions(root: Path, thread_entry: Path) -> List[Dict[str, object]]:
    """Решения нити — [] когда лога нет или нить не резолвится (закрыта и т.п.)."""
    from ..arc import decision  # lazy: decision/candidate reach up into cli (known debt)

    try:
        return decision.list_decisions(root, slug.entry_slug(thread_entry.name))
    except StreamError:
        return []


def _thread_works(root: Path, thread_entry: Path) -> List[Dict[str, object]]:
    """Живые работы нити — [] когда работ у неё нет (или папки works/ ещё нет)."""
    try:
        return _work.thread_works(root, thread_entry.name)
    except StreamError:
        return []


def _session_candidate_nums(root: Path, session_entry: Path,
                            thread_entry: Path) -> List[str]:
    """Номера кандидатов, у которых ``from:`` указывает на эту сессию или нить."""
    from ..arc import candidate  # lazy: reaches up into cli (known debt)

    wanted = {
        session_entry.name, thread_entry.name,
        slug.entry_slug(session_entry.name), slug.entry_slug(thread_entry.name),
    }
    out: List[str] = []
    for item in candidate.list_candidates(root):
        origin = str(item.get("from") or "").strip()
        if not origin or origin == "-":
            continue
        tail = origin.rsplit("/", 1)[-1]
        if tail in wanted or resolve.entry_matches(tail, session_entry.name) \
                or resolve.entry_matches(tail, thread_entry.name):
            out.append(str(item["num"]))
    return out


def _candidate_gists(root: Path, nums: Sequence[str]) -> Dict[str, str]:
    """Суть кандидата одной фразой: первая строка тела, обрезанная до 160 знаков."""
    from ..arc import candidate  # lazy: reaches up into cli (known debt)

    want = set(nums)
    out: Dict[str, str] = {}
    if not want:
        return out
    for item in candidate.list_candidates(root):
        num = str(item["num"])
        if num not in want:
            continue
        body = [ln.strip() for ln in _read(Path(item["path"])).splitlines()
                if ln.strip() and not ln.startswith("#")
                and not re.match(r"^[a-z-]+:\s", ln)]
        gist = re.split(r"(?<=[.!?])\s", body[0], maxsplit=1)[0] if body else ""
        out[num] = gist if len(gist) <= 160 else gist[:159].rstrip() + "…"
    return out


def _closed_works(root: Path) -> List[str]:
    """Имена папок закрытых работ (``__NN-slug__`` или ``status: done``)."""
    out: List[str] = []
    wdir = _work.works_dir(root)
    if not wdir.is_dir():
        return out
    for p in wdir.iterdir():
        if not p.is_dir():
            continue
        name = slug.strip_marker(p.name)
        status = (fields.read_field(p / "work.md", "status") or "").strip()
        if p.name != name or status == "done":
            out.append(name)
    return out


def predecessor_thread(root: Path, thread_entry: Path) -> Optional[Path]:
    """Нить, из которой выросла эта (SOC-101 F7): ``from:`` паспорта нити или
    ``from:`` её первой сессии, указывающий на сессию ДРУГОЙ нити; None — нет."""
    refs = [(fields.read_field(_stream.passport_path(thread_entry), "from") or "").strip()]
    arcs = thread_entry / paths.ARCS_DIRNAME
    firsts = sorted(p for p in arcs.iterdir() if p.is_dir()) if arcs.is_dir() else []
    if firsts:
        refs.append((fields.read_field(firsts[0] / "arc.md", "from") or "").strip())
    top = paths.arcs_dir(root)
    for ref in refs:
        if not ref or ref == "-":
            continue
        head = ref.split("/", 1)[0]
        for cand in (top.iterdir() if top.is_dir() else []):
            if cand == thread_entry or not cand.is_dir() or not _stream.is_thread(cand):
                continue
            if resolve.entry_matches(head, cand.name):
                return cand
        try:
            sess = resolve.find_session(root, ref)
        except Exception:  # noqa: BLE001 — ambiguous/absent: no predecessor
            sess = None
        if sess is not None and sess.parents[1] != thread_entry:
            return sess.parents[1]
    return None


def block_main(thread_entry: Path, inherited: Sequence[str] = (),
               pred: Optional[Path] = None) -> List[str]:
    """«Главное» нити (решение № 24): живые строки раздела plan.md нити; нет —
    из входного сида, потом из plan.md нити-предшественницы (с пометкой)."""
    from ..arc import plan_main

    own = plan_main.live_lines(thread_entry)
    if own:
        return ["- " + ln.lstrip("-* ").strip() for ln in own]
    kept = [ln for ln in live_lines(inherited) if not is_hole(ln) and "<!--" not in ln]
    if kept:
        return kept
    if pred is not None:
        theirs = plan_main.live_lines(pred)
        if theirs:
            return ["- {0} (из нити-предшественницы {1})".format(
                ln.lstrip("-* ").strip(), pred.name) for ln in theirs]
    return []


def draft_bodies(root: Path, session_entry: Path, *, pulse_clip: int = 0
                 ) -> Dict[str, List[str]]:
    """Тела блоков контракта для *session_entry* с диска — общее у черновика и
    автохендоффа. «Сырой журнал» и «задачи Linear» считает :func:`seal`."""
    from . import autohandoff as _auto  # lazy: autohandoff зовёт этот модуль

    session_entry = Path(session_entry)
    thread_entry = session_entry.parents[1]  # …/NN-@thread/arcs/NN-session
    passport = sections(_read(session_entry / "arc.md"))
    seed_in = sections(_read(session_entry / "input" / INPUT_SEED_NAME))
    lays = layers(_read(_stream.passport_path(thread_entry)))
    works = _thread_works(root, thread_entry)

    nums = _session_candidate_nums(root, session_entry, thread_entry)
    closed = _closed_works(root)
    decisions = block_decisions(_thread_decisions(root, thread_entry), closed)
    pred = predecessor_thread(root, thread_entry)
    if pred is not None:
        inherited = block_decisions(_thread_decisions(root, pred), closed)
        if inherited:
            decisions += ["- из нити-предшественницы {0}:".format(pred.name)]
            decisions += ["  " + ln for ln in inherited]
    labels = labeled_pulses(section_body(passport, "context"))
    summary = prose_lines(section_body(passport, "summary"))
    where = layer(lays, "where") + block_cursor(
        section_body(passport, "cursor"), section_body(passport, "context"),
        works=works, clip=pulse_clip)
    if summary:
        where.append("- итог сессии: {0}".format(" ".join(ln.strip() for ln in summary)))
    return {
        MAIN: block_main(thread_entry, contract.find_body(seed_in, "main"), pred),
        EXPECTATION: layer(lays, "expectation") + block_final(
            _goal_of(thread_entry), _goal_of(session_entry)),
        WHERE: where,
        # Источники по порядку (SOC-101 F22): метки пульсов передающей сессии →
        # слои нити → входной сид (опыт наследуется) → вычисляемое с доски.
        EXPERIENCE: block_experience(
            nums, labels.get("experience", []) + layer(lays, "experience")
            + contract.find_body(seed_in, "experience"),
            _candidate_gists(root, nums)),
        DECISIONS: _merge_lines(labels.get("decisions", []), decisions),
        AWAITING: _merge_lines(
            labels.get("awaiting", []), layer_named(lays, "ждёт гришу", "ждет гришу"),
            block_awaiting(_auto.open_questions(root, thread_entry), works)),
        NEXT: block_next(section_body(passport, "next")),
        INFLIGHT: _merge_lines(labels.get("inflight", []),
                               _auto.open_items(root, thread_entry)),
        UNANSWERED: _dialog.tail(thread_entry, DIALOG_TAIL),
        ENTRY_MAP: block_entry_map(contract.find_body(seed_in, "entry_map")),
        ENVIRONMENT: block_environment(contract.find_body(seed_in, "environment")),
    }


# Подсказки куратору в черновике (автосид их вырезает): блоки, которые пишет голова.
DRAFT_HINTS = {
    AWAITING: "- {0}  <!-- курируй: что Грише решить или нажать — и «предлагаю …» "
              "по каждому пункту -->".format(EMPTY),
    INFLIGHT: "- {0}  <!-- пишет агент: воркеры, сабагенты, запросы без ответа — кто "
              "доведёт и куда придёт результат; нет — «ничего не в полёте» -->".format(EMPTY),
}


def build_draft(root: Path, session_entry: Path) -> str:
    """Собрать текст черновика для *session_entry* из того, что лежит на диске."""
    session_entry = Path(session_entry)
    thread_entry = session_entry.parents[1]  # …/NN-@thread/arcs/NN-session
    bodies = draft_bodies(root, session_entry)
    for name, hint in DRAFT_HINTS.items():
        bodies[name] = list(bodies.get(name) or []) + [hint]
    context = section_body(sections(_read(session_entry / "arc.md")), "context")
    return seal(bodies, context=context,
                header=draft_header(session_entry.name, thread_entry.name),
                source="{0} (## context)".format(session_entry / "arc.md"))


# --- резолв сессии + запись -------------------------------------------------

def resolve_session(root: Path, ref: Optional[str] = None) -> Path:
    """Сессия, для которой собираем черновик: *ref* или ТЕКУЩАЯ (по sid).

    Без аргумента — сессия, к которой пришпилен ``$CLAUDE_CODE_SESSION_ID``
    (тот же путь, которым ``tide work take`` узнаёт звонящего): агент, который
    сворачивает СВОЙ чат, не должен вспоминать имя собственной арки.
    """
    root = Path(root)
    if ref:
        entry = offload.find_session(root, ref)
        if entry is None:
            names = ", ".join(
                "{0}/{1}".format(e.parent.parent.name, e.name)
                for e in resolve.open_session_dirs(root)
            ) or "(none)"
            raise SeedDraftError(
                "handoffs draft: no open session matching {0!r}. Open: {1}".format(
                    ref, names)
            )
        return entry
    sid = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    if not sid:
        raise SeedDraftError(
            "handoffs draft: no $CLAUDE_CODE_SESSION_ID to resolve the current "
            "session — name the session arc: tide handoffs draft <арка-сессии>"
        )
    entry = offload.find_session_by_claude_id(root, sid)
    if entry is None:
        raise SeedDraftError(
            "handoffs draft: session {0} is not pinned to any arc "
            "(claude-session: in a passport) — name the arc explicitly: "
            "tide handoffs draft <арка-сессии>".format(sid)
        )
    return entry


def draft(root: Path, ref: Optional[str] = None) -> "tuple[Path, str]":
    """Собрать черновик и положить его в ``<сессия>/workspace/seed-draft.md``.

    Возвращает ``(путь, текст)``. Очередь офферов, статусы и ``input/`` целевой
    сессии НЕ трогает — черновик это материал для курации, а не передача.
    """
    root = Path(root)
    entry = resolve_session(root, ref)
    text = build_draft(root, entry)
    out = entry / WORKSPACE_DIRNAME / DRAFT_FILENAME
    out.parent.mkdir(parents=True, exist_ok=True)
    _io.atomic_write(out, text)
    return out, text


# --- CLI --------------------------------------------------------------------

def _cmd_draft(args) -> int:
    """``tide handoffs draft [<арка-сессии>]`` — черновик в workspace + в stdout."""
    root = paths.require_tide_root()
    path, text = draft(root, getattr(args, "session", None))
    print("tide: seed draft → {0}".format(path))
    print()
    print(text, end="")
    return 0


def register_draft(hsub) -> None:
    """Повесить ``draft`` на группу ``handoffs`` (зовётся из handoff_queue.register)."""
    dp = hsub.add_parser(
        "draft",
        help="draft a handoff seed on the seed contract (tide.handoff_contract) — "
             "from the pulse, the decisions and the candidates",
    )
    dp.add_argument(
        "session", nargs="?",
        help="the session arc (default: the current one, via $CLAUDE_CODE_SESSION_ID)",
    )
    dp.set_defaults(func=_cmd_draft, _cmd="handoffs draft")

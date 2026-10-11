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
from . import seed_compress as _sc
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
# Раздел вне контракта (SOC-101 C12/F40): печатается сразу после «где мы».
TAILS = contract.TAILS_NAME

# Пустой источник → видимая дыра, а не выдуманный абзац: «— <почему пусто>».
EMPTY = contract.EMPTY

# Сколько последних реплик диалога нити класть в «неотвеченные реплики Гриши».
DIALOG_TAIL = 12

# Сколько последних пульс-строк ``## context`` тащить в курсор: хвост, а не вся
# память сессии — сид приземляет на следующий шаг, полную запись держит арка.
PULSE_TAIL = 5

_HEAD_RE = re.compile(r"^##\s+(.+?)\s*$")
_NEXT_SEP = "·"
# Имена сессий «роль · NN» — общий разборщик :mod:`tide.name_dots` (SOC-143 F70).
from ..name_dots import NAME_DOT as _NAME_DOT, KEEP as _KEEP, \
    DASH_NAME_DOT as _DASH_NAME_DOT, keep_name_dots as _keep_name_dots  # noqa: E402,F401

# Сырой журнал пульса в сиде — не больше этой доли тела (SOC-101 F5): старое сжато
# до строки, но не тонет — счёт и путь к полной записи в паспорте.
JOURNAL_SHARE = contract.JOURNAL_SHARE
JOURNAL_CLIP = 160   # хвост пульса режется сюда ТОЛЬКО когда журнал не влез в долю
JOURNAL_GIST = 72    # …и сюда, если и 160 мало


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
_STATE_LABELS = ("awaiting", "inflight", "expectation", "entry_map")


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


# F38: «…; предлагаю — …» — рекомендация к пункту, а не новый пункт.
_ITEM_SPLIT = re.compile(r";\s+(?!предлага)", re.IGNORECASE)
# Конец фразы: точка/!/? и пробел перед заглавной, цифрой, кавычкой, скобкой.
_SENT_END = re.compile(r"(?<=[.!?])\s+(?=[A-ZА-ЯЁ0-9«\"(])")
# C4: статус решения, записанный словами в самой метке.
_DECISION_STATUS = re.compile(
    r"(?<!\w)(в силе|done|сделано|выполнено|отменено|отменили|принято|правило|"
    r"not done|не сделано|в работе|ждёт|ждет)(?!\w)", re.IGNORECASE)
DECISION_NO_STATUS = "(статус: не указан)"
# Метки, чей последний пункт режется по концу фразы (C4/F39).
_SENTENCE_LABELS = ("decisions", "experience")
# «Хвосты:», «Открыто:» (tails) и «Не вживую:» (unverified) — один раздел сида.
_TAIL_KEYS = ("tails", "unverified")
TAILS_KEY = "tails"


def first_sentence(text: str) -> str:
    """Первая фраза куска (до «. Заглавная»): метка хвоста — до конца своей фразы,
    дальше в пульсе обычно уже рассказ (F40: «Не вживую: …; …. Д1 ещё работает»)."""
    return _SENT_END.split(text or "", maxsplit=1)[0].strip()


def labeled_pulses(context: Sequence[str]) -> Dict[str, List[str]]:
    """Размеченное в нотах пульсов → строки блоков сида (SOC-101 F22).

    «Ждёт Гришу: … В полёте: … Опыт: … Решения: …» в ноте ``tide offload`` раньше
    доходили до сида строкой журнала (обрезанной до 72 знаков). Теперь кусок
    метки — пункты своего блока: кусок режется по «; » на пункты, у каждого —
    время пульса; «; предлагаю — …» остаётся при своём пункте (F38). Состояние
    (ждёт Гришу, в полёте, ожидание) — из самого свежего пульса с меткой; опыт,
    решения и хвосты — из всех, свежие сверху, без повторов (сравнение
    нормализованное — F39). Решение без статуса в тексте — с пометкой
    :data:`DECISION_NO_STATUS` (C4). Хвосты (C12/F40) — ключ :data:`TAILS_KEY`:
    «Хвосты:»/«Открыто:» по пунктам, «Не вживую:» — строкой целиком.
    """
    out: Dict[str, List[str]] = {}
    for stamp, note in reversed(pulse_notes(context)):
        for key, pieces in contract.pulse_labels(note).items():
            if key in _STATE_LABELS and key in out:
                continue  # состояние уже взято из более свежего пульса
            target = TAILS_KEY if key in _TAIL_KEYS else key
            lines = out.setdefault(target, [])
            for piece in pieces:
                if target == TAILS_KEY:
                    piece = first_sentence(piece)
                items = [piece] if key == "unverified" else _ITEM_SPLIT.split(piece)
                if key in _SENTENCE_LABELS and items:
                    # C4/F39: последний пункт метки кончается своей фразой — дальше
                    # в пульсе рассказ («… «прочитано как да». Адреса: …»)
                    items[-1] = first_sentence(items[-1])
                for item in items:
                    item = item.strip().rstrip(".;").strip()
                    if not item:
                        continue
                    if key == "unverified":
                        item = "не вживую: " + item
                    if key == "decisions" and not _DECISION_STATUS.search(item):
                        item += " " + DECISION_NO_STATUS
                    line = "- {0}{1}".format(item, " (пульс {0})".format(stamp) if stamp else "")
                    if not any(norm_line(line) == norm_line(x) for x in lines):
                        lines.append(line)
    return out


# Хвост строки сида, который не меняет её смысла: «(пульс ЧЧ:ММ)», «(статус: …)».
_LINE_SUFFIX = re.compile(r"\s*\((?:пульс|статус:|из прошлого сида)[^()]*\)\s*$")
FOLD_MIN = 15  # короче — не сворачиваем «вложенным»: «ao send» не съест чужую строку


def norm_line(line: str) -> str:
    """Строка сида для сравнения (F39): без маркера списка и хвоста «(пульс …)»,
    в нижнем регистре, без бэктиков и пунктуации, пробелы схлопнуты."""
    s = re.sub(r"^\s*[-*]\s+", "", line or "")
    while True:
        t = _LINE_SUFFIX.sub("", s)
        if t == s:
            break
        s = t
    s = re.sub(r"[^\w\s]", " ", s.lower().replace("ё", "е"))
    return " ".join(s.split())


def fold_lines(lines: Sequence[str]) -> List[str]:
    """Свернуть повторы (F39): одинаковые по :func:`norm_line` — одна строка
    (первая); строка, целиком вложенная в другую, уступает более полной (на месте
    первой из них). Пустые строки и дыры (``—``) выкидываются."""
    out: List[str] = []
    keys: List[str] = []
    for ln in lines:
        if not ln.strip() or is_hole(ln):
            continue
        k = norm_line(ln)
        if not k:
            continue
        pad = " {0} ".format(k)
        if any(k == e or (len(k) >= FOLD_MIN and pad in " {0} ".format(e)) for e in keys):
            continue
        hits = [i for i, e in enumerate(keys)
                if len(e) >= FOLD_MIN and " {0} ".format(e) in pad]
        if hits:
            out[hits[0]], keys[hits[0]] = ln, k
            for i in reversed(hits[1:]):
                del out[i], keys[i]
            continue
        out.append(ln)
        keys.append(k)
    return out


SIMILAR_SHARE = 0.8  # доля слов короткой строки в длинной — один и тот же урок
SIMILAR_MIN_WORDS = 3


def _sig_words(line: str) -> set:
    return {w for w in norm_line(line).split() if len(w) >= 3}


def fold_similar(lines: Sequence[str]) -> List[str]:
    """Свернуть один урок, записанный разными словами (F39, опыт): короткая строка,
    чьи слова (3+ знака, не меньше :data:`SIMILAR_MIN_WORDS`) на
    :data:`SIMILAR_SHARE` лежат в другой, уступает более полной — на месте первой."""
    out: List[str] = []
    for ln in fold_lines(lines):
        w = _sig_words(ln)
        hit = None
        for i, prev in enumerate(out):
            pw = _sig_words(prev)
            small, big = (w, pw) if len(w) <= len(pw) else (pw, w)
            if len(small) >= SIMILAR_MIN_WORDS and \
                    len(small & big) >= SIMILAR_SHARE * len(small):
                hit = i
                break
        if hit is None:
            out.append(ln)
        elif len(_sig_words(ln)) > len(_sig_words(out[hit])):
            out[hit] = ln
    return out


def _merge_lines(*groups: Sequence[str]) -> List[str]:
    """Склеить источники блока по порядку, без повторов и без строк-дыр; повтор —
    и тот же пункт другими знаками (F39, :func:`fold_lines`)."""
    return fold_lines([ln for g in groups for ln in g])


# Маркеры хвоста в пульсе без меток (C12/F40): то, что не сделано или не проверено.
_TAIL_MARKERS = re.compile(r"красн|не\s+вживую|не\s+провер|хвост", re.IGNORECASE)


_NO_TAILS = {"нет", "ничего", "хвостов нет", "нет хвостов", "никаких", "не осталось"}


def block_tails(context: Sequence[str], labeled: Sequence[str] = ()) -> List[str]:
    """Открытые хвосты (SOC-101 C12/F40) — целиком, а не обрезками журнала.

    Источник первый — метки «Хвосты:», «Открыто:», «Не вживую:» в пульсах сессии
    (:func:`labeled_pulses`, все пульсы, свежие сверху). Меток нет ни в одном
    пульсе — фразы с маркерами хвоста («красный», «не вживую», «не проверен»,
    «хвост») из ПЕРЕДАЧИ и последних :data:`PULSE_TAIL` пульсов. Нет и их — [] (раздел
    скажет прочерком, что проверено)."""
    if labeled:
        # «Хвосты: нет» — честный прочерк; самый свежий такой — перекрывает старшие
        first = labeled[0]
        if norm_line(first) in _NO_TAILS:
            stamp = re.search(r"\(пульс ([^()]*)\)\s*$", first)
            return [contract.TAILS_NONE, "(хвостов нет — так сказано в пульсе{0})".format(
                " " + stamp.group(1) if stamp else "")]
        return [ln for ln in labeled if norm_line(ln) not in _NO_TAILS]
    notes = pulse_notes(context)
    pulse = live_lines(context)
    idx = set(range(max(0, len(notes) - PULSE_TAIL), len(notes)))
    passed = last_pass_index(pulse)
    if passed is not None:
        idx.add(passed)
    out: List[str] = []
    for i in sorted(idx, reverse=True):
        stamp, note = notes[i]
        for sent in _SENT_END.split(note):
            sent = sent.strip().rstrip(".").strip()
            if sent and _TAIL_MARKERS.search(sent):
                out.append("- {0}{1} (маркер в пульсе)".format(
                    sent, " (пульс {0})".format(stamp) if stamp else ""))
    return fold_lines(out)


# «в работе прямо сейчас» → хвост (F56): незакрытое поручение и живой воркер — тоже
# хвост; «— нет» в хвостах при четырёх поручениях в работе — ложь сида.
_ORDER_RE = re.compile(r"^-\s*поручено мне — от (.+?) в (\d\d:\d\d): (.*)$")
_WORKER_RE = re.compile(r"^-\s*воркер «(.*?)» — жив")
# Шапка адресации в начале поручения («[board · 03 → ck]», «management-16 →
# context-keeper · 03:») съедает короткую суть — срезаем.
_ADDRESS_HEAD = re.compile(r"^(?:\[[^\]]{0,60}\]\s*|[^:.«]{0,60}?→[^:.«]{0,40}?[:.]\s+)")
TAIL_GIST = 70


def tails_from_inflight(inflight: Sequence[str]) -> List[str]:
    """Строки «в работе» (:func:`incoming_orders`, :func:`background_workers`) →
    по строке хвоста: от кого, что коротко, «см. в работе». Прочее — мимо."""
    out: List[str] = []
    for ln in inflight:
        ln = ln.strip()
        m = _ORDER_RE.match(ln)
        if m:
            out.append("- поручение от {0} ({1}) не закрыто: {2} — см. «{3}»".format(
                m.group(1), m.group(2),
                clip_words(_ADDRESS_HEAD.sub("", m.group(3)) or m.group(3), TAIL_GIST),
                INFLIGHT))
            continue
        m = _WORKER_RE.match(ln)
        if m:
            out.append("- воркер «{0}» жив в старой сессии — см. «{1}»".format(
                m.group(1), INFLIGHT))
    return out


def merge_tails(tails: Sequence[str], inflight: Sequence[str]) -> List[str]:
    """Хвосты сверены с «в работе» (F56): прочерк «— нет» уходит, если там есть
    незакрытые поручения или живые воркеры; названные хвосты — сверху."""
    extra = tails_from_inflight(inflight)
    if not extra:
        return list(tails)
    named = list(tails)
    if tails and tails[0].strip() == contract.TAILS_NONE:  # снятые (F74) остаются
        named = [ln for ln in tails[1:] if _bare(ln).lower().startswith(CLOSED_TAIL)]
    return named + extra


# SOC-143 F74: хвост «plan.md нити без «главное»» остался в сиде, хотя тот же шов
# вписал «главное» за 8 с до сборки. Хвост про пустой/старый слой нити сверяется со
# слоем на момент сборки: слой есть (или правлен после пульса) — хвост снят строкой
# «снято: … — причина: …» (как решения, F63), приём её открытой не считает.
CLOSED_TAIL = "снято:"
# Слой нити → как его зовут в хвосте. «главное» живёт в plan.md, прочие — в goal-файле.
_LAYER_NAMES = (
    ("main", re.compile(r"главно", re.IGNORECASE)),
    ("expectation", re.compile(r"ожидани", re.IGNORECASE)),
    ("where", re.compile(r"где\s+мы", re.IGNORECASE)),
    ("experience", re.compile(r"(?<!\w)опыт", re.IGNORECASE)),
)
# Хвост говорит именно о слое: «слой», «plan.md», «*-goal.md» или имя слоя в «…».
_LAYER_REF = re.compile(r"сло[йяюеё]|plan\.md|-goal\.md|«(?:главное|ожидание|где мы|опыт)",
                        re.IGNORECASE)
_LAYER_EMPTY = re.compile(r"(?<!\w)(?:без|нет|пуст\w*|не\s+заполн\w*|не\s+вписан\w*|"
                          r"не\s+дописан\w*)(?!\w)", re.IGNORECASE)
_LAYER_STALE = re.compile(r"стар\w*|устарел\w*|не\s+обновл\w*|не\s+правлен\w*",
                          re.IGNORECASE)
_TAIL_STAMP = re.compile(r"\(пульс (\d\d:\d\d)\)")


def _layer_state(thread_entry: Path, key: str) -> "tuple[bool, Optional[float], Path]":
    """``(есть ли живые строки, mtime файла, файл)`` слоя *key* нити сейчас."""
    from ..arc import plan_main

    if key == "main":
        f = Path(thread_entry) / plan_main.PLAN_FILE
        has = bool(plan_main.live_lines(thread_entry))
    else:
        f = _stream.passport_path(thread_entry)
        has = bool(layer(layers(_read(f)), key))
    try:
        mtime = f.stat().st_mtime
    except OSError:
        mtime = None
    return has, mtime, f


def _tail_pulse_at(line: str, context: Sequence[str]):
    """Полное время пульса хвоста («(пульс ЧЧ:ММ)») — по последнему пульсу с этим
    ЧЧ:ММ в ``## context``; не нашёлся — None."""
    m = _TAIL_STAMP.search(line)
    if not m:
        return None
    hits = [at for at, _ln in _pulse_stamps(context) if at.strftime("%H:%M") == m.group(1)]
    return hits[-1] if hits else None


def close_layer_tails(tails: Sequence[str], thread_entry: Path,
                      context: Sequence[str] = ()) -> List[str]:
    """Хвосты про пустой/старый слой нити, закрытые к сборке (F74), → «снято: …».

    Хвост про пустой слой («plan.md нити без «главное»», «слой «опыт» пуст») снят,
    если слой сейчас не пуст. Хвост про старый слой («ожидание Гриши устарело»)
    снят, если файл слоя правлен позже пульса хвоста. Причина — со временем правки
    (mtime на момент сборки). Не осталось открытых — сверху «— нет»."""
    from datetime import datetime

    out: List[str] = []
    closed = 0
    for ln in tails:
        body = _bare(ln)
        if not body or body.lower().startswith(CLOSED_TAIL) or ln.strip() in (
                contract.TAILS_NONE,) or not _LAYER_REF.search(body):
            out.append(ln)
            continue
        key = next((k for k, rx in _LAYER_NAMES if rx.search(body)), "")
        empty, stale = _LAYER_EMPTY.search(body), _LAYER_STALE.search(body)
        why = ""
        if key and (empty or stale):
            has, mtime, f = _layer_state(thread_entry, key)
            at = _tail_pulse_at(body, context)
            fresh = mtime is not None and at is not None and \
                datetime.fromtimestamp(mtime) > at
            when = datetime.fromtimestamp(mtime).strftime("%d.%m %H:%M:%S") \
                if mtime is not None else "?"
            name = contract.name(key)
            if empty and has:
                why = "слой «{0}» вписан ({1}, правка {2}) — на момент сборки не пуст".format(
                    name, f, when)
            elif stale and not empty and has and fresh:
                why = "слой «{0}» правлен после пульса ({1}, правка {2})".format(
                    name, f, when)
        if why:
            out.append("- {0} {1} — причина: {2}".format(CLOSED_TAIL, body, why))
            closed += 1
        else:
            out.append(ln)
    if closed and not any(
            _bare(x) and not _bare(x).lower().startswith(CLOSED_TAIL)
            and x.strip() != contract.TAILS_NONE and not x.strip().startswith("(")
            for x in out):
        out = [contract.TAILS_NONE] + [x for x in out if x.strip() != contract.TAILS_NONE]
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


def block_expectation(layer_lines: Sequence[str], goal_file: Optional[Path] = None,
                      pulse_lines: Sequence[str] = (),
                      quotes: Sequence[str] = ()) -> List[str]:
    """Ожидание Гриши: слой нити ДОСЛОВНО и с датой (SOC-101, пакет Г п.5).

    Раньше блок склеивал ``goal:`` нити и ``goal:`` сессии — это цель, а не слово
    Гриши, и она устаревала (адресат management-7 давно закрыт). Теперь: строки
    слоя «### ожидание Гриши» как есть + строка-источник с путём и датой правки
    goal-файла. Слой пуст — метка «Ожидание Гриши:»/«Ожидание:» самого свежего
    пульса; нет и её — цитаты Гриши с датой из пульсов (*quotes*, C1,
    :func:`grisha_quotes`); нет и их — пусто (блок скажет, какие источники проверены).
    """
    lines = [ln for ln in layer_lines if ln.strip()]
    if lines:
        if goal_file is not None:
            from datetime import datetime

            try:
                day = datetime.fromtimestamp(Path(goal_file).stat().st_mtime).strftime("%Y-%m-%d")
            except OSError:
                day = "дата правки неизвестна"
            lines.append("- (слой нити дословно: {0} · правлен {1})".format(goal_file, day))
        return lines
    if pulse_lines:
        return list(pulse_lines)
    return list(quotes)


# C1/F46: цитата Гриши — «слово/слова Гриши …: «…»» или «Гриша (tg N): «…»»: Гриша —
# говорящий, а за подписью сразу двоеточие или скобка и ёлочки. «"решение" только у
# ответа Гриши», «Ждёт решения менеджера: помечать «решением» …» — не его слова.
_GRISHA_SPEAKER = (r"(?:[Сс]лов[оа]\s+Гриши|\bГриша)"
                   r"(?P<mid>(?:[^«.\n]|\.(?!\s)){0,120}?)[:)]\s*«(?P<q>[^«»]{8,})»")
_GRISHA_QUOTE = re.compile(_GRISHA_SPEAKER)
_TG_NUM = re.compile(r"\btg\b\D{0,12}?(\d{3,6})", re.IGNORECASE)


def grisha_quotes(*contexts: Sequence[str], limit: int = 5) -> List[str]:
    """Цитаты Гриши с датой из пульсов (C1): фраза, где есть «Гриш…» и «…» (8+ знаков),
    с полным штампом пульса — свежие сверху, без повторов, не больше *limit*."""
    out: List[str] = []
    for context in contexts:
        for ln in reversed(live_lines(context)):
            m = _FULL_STAMP.match(ln.strip())
            if not m:
                continue  # без даты — не цитата с датой
            note = ln.strip()[m.end():].lstrip(" —-").strip()
            for sent in _SENT_END.split(_keep_name_dots(note)):
                sent = sent.replace(_KEEP, " · ").strip().rstrip(".").strip()
                if _GRISHA_QUOTE.search(sent):
                    out.append("- {0} (цитата из пульса {1})".format(
                        sent, m.group(1).replace("T", " ")[:16]))
    return fold_lines(out)[:limit]


def grisha_words(transcript: Optional[Path], limit: int = 5) -> List[str]:
    """Слова Гриши из доставок транскрипта (F46): «Слово Гриши (tg N): «…»» в
    сообщениях менеджера и других сессий → ``- «…» (слово Гриши, tg N, через X в
    ЧЧ:ММ)``, свежие сверху, без повторов, не больше *limit*."""
    if transcript is None:
        return []
    try:
        rows = list(_transcript_rows(transcript))
    except OSError:
        return []
    found = []
    for row in rows:
        if row.get("type") not in ("user", "attachment"):
            continue
        at = _dialog._stamp(str(row.get("timestamp") or ""))
        text = _user_text(row)
        for rx in (_DELIVERY_RE, _CROSS_RE):
            for m in rx.finditer(text):
                who, body = m.group(1).strip(), " ".join(m.group(2).split())
                for q in _GRISHA_QUOTE.finditer(body):
                    tg = _TG_NUM.search(q.group("mid") or "")
                    found.append((at, "- «{0}» (слово Гриши{1}, через {2}{3})".format(
                        q.group("q").strip(), ", tg " + tg.group(1) if tg else "",
                        sender_key(who), " в " + at.strftime("%H:%M") if at else "")))
    found.sort(key=lambda r: r[0] or 0, reverse=True)
    out: List[str] = []
    seen = set()
    for _at, line in found:
        key = norm_line(line.split("» (", 1)[0])
        if key not in seen:
            seen.add(key)
            out.append(line)
    return out[:limit]


def session_grisha_words(session_entry: Path) -> List[str]:
    """:func:`grisha_words` транскрипта передающей сессии. Не падает."""
    try:
        sid = (fields.read_field(Path(session_entry) / "arc.md",
                                 offload.CLAUDE_SESSION_FIELD) or "").strip()
        return grisha_words(transcript_path(sid))
    except Exception:  # noqa: BLE001 — сид важнее
        return []


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
    start = max(0, len(pulse) - tail) if tail > 0 else 0
    passed = last_pass_index(pulse)
    for i in reversed(range(start, len(pulse))):
        ln = pulse[i]
        ln = ln if ln.lstrip().startswith("-") else "- {0}".format(ln)
        if clip and len(ln) > clip and not is_protected_pulse(pulse, i):
            ln = clip_words(ln, clip)
        if passed is not None and i < passed:
            ln += " " + BEFORE_PASS  # F34: старше ПЕРЕДАЧИ — не спорит с ней молча
        lines.append(ln)
    return lines


# Пометка пульса «где мы», записанного раньше ПЕРЕДАЧИ (SOC-101 F34): «остаток F23,
# F27» в 08:22 рядом с «А–Г сданы» в ПЕРЕДАЧЕ 08:26 читался как противоречие.
BEFORE_PASS = "(до ПЕРЕДАЧИ)"


def last_pass_index(pulse: Sequence[str]) -> Optional[int]:
    """Индекс последнего пульса «ПЕРЕДАЧА» среди *pulse*; нет — None."""
    for i in reversed(range(len(pulse))):
        if _PASS_RE.match(pulse[i].strip()):
            return i
    return None


# Метка «ПЕРЕДАЧА» — начало ноты пульса (после штампа), а не слово в тексте:
# «передача работы 117 фактов» — обычный пульс. Живёт в контракте: ``tide offload``
# распознаёт так же (C11).
_PASS_RE = contract.PASS_RE


def is_protected_pulse(pulse: Sequence[str], i: int) -> bool:
    """Пульс, который не режется никогда (SOC-101 F24/F16): последний перед
    передачей и любой с меткой «ПЕРЕДАЧА» — это кураторский текст, а не журнал."""
    return i == len(pulse) - 1 or bool(_PASS_RE.match(pulse[i].strip()))


def raw_pulse_chars(context: Sequence[str], tail: int = PULSE_TAIL, clip: int = 0) -> int:
    """Сколько знаков СЫРОГО пульса несёт курсор (его хвост из *tail* строк).

    Защищённые пульсы (:func:`is_protected_pulse`) — не сырой журнал: они идут
    целиком и в долю 30% не считаются."""
    pulse = live_lines(context)
    start = max(0, len(pulse) - tail) if tail > 0 else 0
    return sum(min(len(pulse[i]) + 3, (clip + 1) if clip else len(pulse[i]) + 3)
               for i in range(start, len(pulse)) if not is_protected_pulse(pulse, i))


LINEAR_URL_ENV = "TIDE_LINEAR_ISSUE_URL"
LINEAR_URL_DEFAULT = "https://linear.app/socaseinpoint/issue/{key}"
_ISSUE_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b")


LINEAR_STATUSES = ("In Review", "In Progress", "Done", "Todo", "Backlog", "Canceled",
                   "Cancelled", "Duplicate", "Triage")
_STATUS_RE = re.compile(r"\b({0})\b".format("|".join(LINEAR_STATUSES)), re.IGNORECASE)
_SLASH_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-(\d+)((?:/\d+)+)\b")
_KEY_RUN = r"[A-Z][A-Z0-9]{1,9}-\d+"
# «SOC-57, SOC-39, SOC-51 — In Review», «SOC-108 In Review», «SOC-100 в In Review»
_STATUS_RUN_RE = re.compile(
    r"((?:{0})(?:\s*(?:,|и|·)\s*(?:{0}))*)\s*(?:[—:–-]\s*|\(|в\s+|is\s+)?\s*({1})\b".format(
        _KEY_RUN, "|".join(LINEAR_STATUSES)), re.IGNORECASE)


def expand_issue_runs(text: str) -> str:
    """«SOC-92/96/77» → «SOC-92, SOC-96, SOC-77»: каждый номер — свой ключ."""
    def one(m):
        nums = [m.group(2)] + [n for n in m.group(3).split("/") if n]
        return ", ".join("{0}-{1}".format(m.group(1), n) for n in nums)
    return _SLASH_RE.sub(one, text or "")


def issue_statuses(text: str) -> Dict[str, str]:
    """Статус задачи Linear, записанный рядом с её номером в *text* (первое
    упоминание выигрывает — подавай текст свежим сверху). Только локально:
    в Linear не ходим."""
    out: Dict[str, str] = {}
    canon = {s.lower(): s for s in LINEAR_STATUSES}
    for m in _STATUS_RUN_RE.finditer(expand_issue_runs(text)):
        status = canon[m.group(2).lower()]
        for key in re.findall(_KEY_RUN, m.group(1)):
            out.setdefault(key, status)
    return out


# --- статусы задач Linear из самого Linear (SOC-101 F30) ------------------------
#
# Статус «рядом с номером в пульсе» врал: SOC-100 «In Review» стояла Done, SOC-39/51/57
# давно закрытые шли открытыми задачами нити. Теперь при сборке — ОДИН запрос GraphQL
# на все номера (ключ — Keychain ``linear-api-key``, таймаут короткий); нет ключа /
# сети / ответа — откат на текст пульсов с пометкой «статус не сверен».

LINEAR_API = "https://api.linear.app/graphql"
LINEAR_KEYCHAIN = "linear-api-key"
LINEAR_OFF_ENV = "TIDE_LINEAR_OFFLINE"    # =1 — в Linear не ходить (статус из текста)
LINEAR_TIMEOUT = 4.0                       # с; плюс ≤1 с на Keychain — укладываемся в 5
CLOSED_TYPES = ("completed", "canceled")
# SOC-143 F85: в том же запросе — комментарии «Взял: <проект> — <нить> · NN» (кто ведёт
# задачу); отдельного похода в сеть нет.
_LINEAR_QUERY = ("query($team:String!,$nums:[Float!]){issues(filter:{team:{key:{eq:$team}},"
                 "number:{in:$nums}},first:250){nodes{identifier state{name type} "
                 "comments(filter:{or:[{body:{startsWith:\"Взял\"}},"
                 "{body:{startsWith:\"Назначено\"}}]},last:10){nodes{body}}}}}")
# «Взял: tide-stack — handoffs · 06 (…)», «Назначено: tide-stack — board» → (проект, нить)
_TAKER_RE = re.compile(r"^\s*(?:Взял|Назначено):\s*(\S.*?)\s+[—–]\s+([^\s·(,.;:]+)")


def parse_taker(body: str) -> "Optional[tuple[str, str]]":
    """Кто ведёт задачу по комментарию «Взял: <проект> — <нить> · NN …» или «Назначено:
    <проект> — <нить>» (контракт v1 §3) → ``(проект, нить)``; не такой комментарий — None."""
    m = _TAKER_RE.match(body or "")
    return (m.group(1).strip(), m.group(2).strip()) if m else None


class LinearUnavailable(Exception):
    """Linear не ответил (нет ключа, сети, ошибка API) — причина в тексте."""


def _linear_key() -> str:
    import subprocess

    try:
        r = subprocess.run(["security", "find-generic-password", "-s", LINEAR_KEYCHAIN, "-w"],
                           capture_output=True, text=True, timeout=1.0)
    except (OSError, subprocess.SubprocessError) as e:
        raise LinearUnavailable("Keychain недоступен ({0})".format(type(e).__name__))
    key = (r.stdout or "").strip()
    if not key:
        raise LinearUnavailable("нет ключа {0} в Keychain".format(LINEAR_KEYCHAIN))
    return key


def linear_fetch(keys: Sequence[str]) -> Dict[str, "tuple[str, str]"]:
    """``{SOC-N: (имя статуса, тип)}`` из Linear — один запрос на команду (префикс).

    Сеть. Тесты подменяют эту функцию (``monkeypatch.setattr(seed_draft,
    "linear_fetch", …)``); под pytest без подмены в сеть не ходим вовсе."""
    import json
    import urllib.request

    by_team: Dict[str, List[float]] = {}
    for k in keys:
        team, _, num = k.partition("-")
        if num.isdigit():
            by_team.setdefault(team, []).append(float(num))
    if not by_team:
        return {}
    token = _linear_key()
    out: Dict[str, "tuple[str, str]"] = {}
    takers: Dict[str, List["tuple[str, str]"]] = {}
    for team, nums in by_team.items():
        body = json.dumps({"query": _LINEAR_QUERY,
                           "variables": {"team": team, "nums": nums}}).encode()
        req = urllib.request.Request(LINEAR_API, data=body, headers={
            "Authorization": token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=LINEAR_TIMEOUT) as r:
                data = json.load(r)
        except Exception as e:  # noqa: BLE001 — сеть/таймаут/HTTP — всё одно: не сверено
            raise LinearUnavailable("Linear не ответил ({0})".format(type(e).__name__))
        if not isinstance(data, dict) or data.get("errors") or not data.get("data"):
            raise LinearUnavailable("Linear вернул ошибку")
        for node in (data["data"].get("issues") or {}).get("nodes") or []:
            st = node.get("state") or {}
            key = str(node.get("identifier"))
            out[key] = (str(st.get("name") or ""), str(st.get("type") or ""))
            got = [parse_taker(str(c.get("body") or ""))
                   for c in ((node.get("comments") or {}).get("nodes") or [])]
            if any(got):
                takers[key] = [t for t in got if t]
    out = _States(out)
    out.takers = takers
    return out


_DEFAULT_FETCH = linear_fetch
_LINEAR_CACHE: Dict[frozenset, object] = {}


def linear_states(keys: Sequence[str]):
    """Статусы *keys* из Linear или :class:`LinearUnavailable` (как значение, не
    исключение); None — сверка не запускалась (pytest без подменённого клиента). Кэш на процесс: сборка зовёт блок много раз (бюджет журнала), а
    запрос — один; провал тоже кэшируется — таймаут не повторяется."""
    want = frozenset(keys)
    if not want:
        return {}
    if (os.environ.get(LINEAR_OFF_ENV) or "").strip() not in ("", "0"):
        return LinearUnavailable("сверка выключена ({0})".format(LINEAR_OFF_ENV))
    fetch = linear_fetch
    if fetch is _DEFAULT_FETCH and os.environ.get("PYTEST_CURRENT_TEST"):
        return None  # под тестами без подменённого клиента — сверки нет, как до F30
    for got, val in _LINEAR_CACHE.items():
        if want <= got and getattr(val, "_fetch", None) is fetch:
            return val
    try:
        got = fetch(sorted(want))
        val = _States(got)
        val.takers = dict(getattr(got, "takers", None) or {})
    except LinearUnavailable as e:
        val = e
    except Exception as e:  # noqa: BLE001 — сид важнее
        val = LinearUnavailable("сверка упала ({0})".format(type(e).__name__))
    try:
        val._fetch = fetch
    except AttributeError:
        pass
    _LINEAR_CACHE[want] = val
    return val


class _States(dict):
    """Ответ Linear (dict) с пометкой, какой функцией получен (для кэша), и
    ``takers`` — ``{SOC-N: [(проект, нить), …]}`` из комментариев «Взял:» (F85)."""

    takers: Dict[str, List["tuple[str, str]"]] = {}


MINE_MARK = " · эта нить"


def mine_issues(keys: Sequence[str], live, me: Sequence[str] = ()) -> List[str]:
    """Задачи, которые в Linear ведёт ЭТА нить: комментарий «Взял:» или «Назначено:»
    с её проектом и нитью (SOC-181: «мои открытые SOC» сида)."""
    takers = getattr(live, "takers", None) or {}
    proj, thread = (list(me) + ["", ""])[:2]
    if not thread:
        return []
    return [k for k in keys if any(t == thread and (not proj or not p or p == proj)
                                   for p, t in takers.get(k) or [])]


def block_links(text: str, prefixes: Sequence[str] = ("SOC",),
                status_text: str = "", live=None,
                foreign: Optional[Dict[str, str]] = None,
                only: Optional[Sequence[str]] = None,
                mine: Sequence[str] = ()) -> List[str]:
    """Каждая задача Linear, упомянутая в сиде, — один раз со ссылкой.

    Голый «SOC-62» новой сессии ничего не говорит, пока она не сходит в Linear
    искать, где это. Адрес — ``$TIDE_LINEAR_ISSUE_URL`` (шаблон с ``{key}``).
    «SOC-92/96/77» — три задачи.

    Статус (F30): *live* — ответ :func:`linear_states`. Словарь — статус из
    Linear; закрытые (Done/Canceled) уходят вниз отдельной группой «закрыты в
    Linear». :class:`LinearUnavailable` — статус из *status_text*/*text*
    (:func:`issue_statuses`) с пометкой «статус не сверен». None — как до F30:
    только текст, без пометок. *only* — печатать только эти номера (SOC-181 п.13:
    проект с трекером заказчика, :func:`own_issues`)."""
    tmpl = (os.environ.get(LINEAR_URL_ENV) or "").strip() or LINEAR_URL_DEFAULT
    text = expand_issue_runs(text)
    seen: List[str] = []
    for key in _ISSUE_RE.findall(text or ""):
        if key.split("-", 1)[0] in prefixes and key not in seen and (
                only is None or key in only):
            seen.append(key)
    seen.sort(key=lambda k: int(k.rsplit("-", 1)[1]))
    foreign = foreign or {}
    if isinstance(live, dict):
        opened, closed, others = [], [], []
        for k in seen:
            name, kind = live.get(k, ("", ""))
            if kind in CLOSED_TYPES:
                closed.append("  - {0} — {1} — закрыта ({2})".format(k, tmpl.format(key=k), name))
            elif k in foreign:  # F71/F85: ведёт другая нить — не задача этой
                others.append("  - {0} — {1} — {2} ({3})".format(
                    k, tmpl.format(key=k), name or "в Linear не найдена", foreign[k]))
            else:
                opened.append("- {0} — {1} — {2}{3}".format(
                    k, tmpl.format(key=k), name or "в Linear не найдена",
                    MINE_MARK if k in mine else ""))
        if not seen:
            return []
        out = ["- (статусы — из Linear на момент сборки)"] + opened
        if others:
            out += ["- ведут другие нити — не задачи этой нити:"] + others
        if closed:
            out += ["- закрыты в Linear — не открытые задачи нити:"] + closed
        return out
    statuses = issue_statuses((status_text or "") + "\n" + text)
    if live is None:
        return ["- {0} — {1}{2}".format(k, tmpl.format(key=k),
                                        " — " + statuses[k] if k in statuses else "")
                for k in seen]
    if not seen:
        return []
    return ["- (статус не сверен с Linear: {0}; рядом — из текста пульсов)".format(live)] + [
        "- {0} — {1}{2}".format(k, tmpl.format(key=k),
                                " — {0} (из пульса, не сверен)".format(statuses[k])
                                if k in statuses else "")
        for k in seen]


def linear_keys(text: str, prefixes: Sequence[str] = ("SOC",)) -> List[str]:
    """Номера задач *prefixes* в *text* (с раскрытием «SOC-92/96/77»), без повторов."""
    out: List[str] = []
    for key in _ISSUE_RE.findall(expand_issue_runs(text)):
        if key.split("-", 1)[0] in prefixes and key not in out:
            out.append(key)
    return out


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


# F69: решение без статуса в метке пульса больше не печатается с «(статус: не
# указан)» — шум: статус задачи берётся из Linear (seal), правило — из decisions.md.
_NO_STATUS_SUFFIX = re.compile(r"\s*" + re.escape(DECISION_NO_STATUS))
# Номер решения нити в начале строки: «07 Tencent — …» (метка), «07 — …» (decisions.md).
_DECISION_NUM = re.compile(r"^\s*(?:[-*]\s+)?(\d{2})\b")
DECISION_TWIN_SHARE = 0.5   # доля слов короткой строки — то же решение под тем же номером


def _decision_num(line: str) -> str:
    m = _DECISION_NUM.match(line or "")
    return m.group(1) if m else ""


def fold_decisions(lines: Sequence[str]) -> List[str]:
    """Блок «решения» без дублей и без «(статус: не указан)» (SOC-101 F69).

    Метка «Решения: 07 Tencent — …» ПЕРЕДАЧИ и решение 07 нити — одно решение:
    строки с одним номером NN, делящие :data:`DECISION_TWIN_SHARE` значимых слов
    короткой, сливаются в более полную. Без номера — то же решение другими словами
    (:func:`fold_similar`)."""
    clean = [_NO_STATUS_SUFFIX.sub("", ln) for ln in lines]
    out: List[str] = []
    for ln in fold_similar(clean):
        num, w = _decision_num(ln), _sig_words(ln)
        hit = None
        for i, prev in enumerate(out):
            if not num or _decision_num(prev) != num:
                continue
            pw = _sig_words(prev)
            small, big = (w, pw) if len(w) <= len(pw) else (pw, w)
            if len(small & big) >= max(REVOKE_MIN_WORDS, DECISION_TWIN_SHARE * len(small)):
                hit = i
                break
        if hit is None:
            out.append(ln)
        elif len(w) > len(_sig_words(out[hit])):
            out[hit] = ln
    return out


_NOT_DONE_SUFFIX = re.compile(r"\s*\(not done\)")


def mark_decision_linear(lines: Sequence[str], live) -> List[str]:
    """Статус решения с задачей Linear — из Linear, как в блоке «задачи Linear»
    (SOC-101 F69): «01 — SOC-100 … (not done)» при SOC-100 Done врало. *live* —
    ответ :func:`linear_states`; не словарь (сверки не было, Linear не ответил) —
    строки как есть. «(not done)» заменяется статусом; правило («в силе») остаётся
    правилом, статус задачи — рядом."""
    if not isinstance(live, dict):
        return list(lines)
    out: List[str] = []
    for ln in lines:
        # SOC-181: статус заменяется, а не дописывается — хвост прошлой сборки снят
        ln = _sc.strip_linear_tails(ln)
        keys = linear_keys(ln)
        got = [(k, live[k]) for k in keys if k in live]
        if not got:
            out.append(ln)
            continue
        first_closed = got[0][1][1] in CLOSED_TYPES
        tag = "{0}в Linear: {1}".format(
            "done — " if first_closed else "",
            "; ".join("{0} — {1}".format(k, name or kind) for k, (name, kind) in got))
        if _NOT_DONE_SUFFIX.search(ln):
            ln = _NOT_DONE_SUFFIX.sub(" ({0})".format(tag), ln, count=1)
        else:
            ln = "{0} ({1})".format(ln.rstrip(), tag)
        out.append(ln)
    return out


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
            # C4: правило не «выполняют» — оно действует; «not done» у него — шум
            mark = marks[id(rec)] or ("правило, в силе" if decision.is_rule(rec)
                                      else "not done")
            out.append("- {0} — {1} ({2})".format(rec.get("num"), what, mark))
    return out


# SOC-101 F63: решения копятся, как опыт (F22) — решения входного сида переходят
# дальше; снятое — строкой «снято: … — причина: …», а не молча.
INHERITED_MARK = "(из прошлого сида)"
_REVOKE_RE = re.compile(r"снят|сняли|снимаю|отмен|больше не|не в силе|не действ|"
                        r"заменен|заменён|superseded|dropped", re.IGNORECASE)
REVOKE_SHARE = 0.5     # доля слов решения в строке-отмене — это она его снимает
REVOKE_MIN_WORDS = 2


def _bare(line: str) -> str:
    return re.sub(r"^\s*[-*]\s+", "", line or "").strip()


def _revokes(revoker: str, decision_line: str) -> bool:
    dw, rw = _sig_words(decision_line), _sig_words(revoker)
    shared = len(dw & rw)
    return shared >= REVOKE_MIN_WORDS and shared >= REVOKE_SHARE * len(dw)


def inherit_decisions(inherited: Sequence[str], current: Sequence[str] = (),
                      records: Sequence[Dict[str, object]] = ()) -> List[str]:
    """Решения входного сида → строки блока «решения» этого сида (F63).

    Каждое переходит дальше с пометкой :data:`INHERITED_MARK`; снятое — строкой
    ``- снято: <решение> — причина: <что сняло>``. Сняло — строка этой сессии
    (*current*: метки «Решения:», решения нити) со словом отмены
    (:data:`_REVOKE_RE`), делящая с решением большую часть слов, или решение нити
    (*records*) в статусе superseded/dropped о том же. Уже снятое прошлым сидом
    («снято: …») дальше не едет: его показал один сид, полное — на диске (SOC-181);
    хвосты статуса и пометка — по одному на строку, не стопкой."""
    from ..arc import decision  # lazy: decision reaches up into cli (known debt)

    revokers = [_bare(c) for c in current if _REVOKE_RE.search(c or "")]
    gone = []
    for rec in records:
        st = decision.normalize_status(str(rec.get("status") or ""))
        if st != decision.ACCEPTED:
            what = str(rec.get("what") or "").strip()
            if what:
                gone.append(("решение {0} нити — {1}".format(rec.get("num"), st), what))
    out: List[str] = []
    for ln in live_lines(inherited):
        if "<!--" in ln or is_hole(ln) or _sc._DROPPED_RE.match(ln.strip()) \
                or _sc.CAPPED_MARK in ln:
            continue
        # SOC-181: хвосты статуса и «(из прошлого сида)» — по одному, не стопкой
        body = _sc.strip_status_tails(_bare(ln))
        if not body or body.lower().startswith("снято:"):
            continue  # снятое показано одним сидом — дальше не едет (SOC-181)
        why = next((r for r in revokers if _revokes(r, body)), "")
        if not why:
            why = next((reason for reason, what in gone if _revokes(what, body)), "")
        if why:
            out.append("- снято: {0} — причина: {1}".format(
                _LINE_SUFFIX.sub("", body), why))
        else:
            out.append("- {0} {1}".format(body, INHERITED_MARK))
    return out


def dropped_decisions(prev_seed: str, new_seed: str, archive: str = "") -> List[str]:
    """Решения прошлого сида, которых нет в новом ни строкой, ни «снято: …» (F63) —
    для TG о приёме: «Потеряно: ничего» при выпавшем решении — неправда. SOC-181: решения
    в теле сида больше не печатаются — их блок в архиве сида (*archive*), это не потеря."""
    prev = [_bare(ln) for ln in live_lines(contract.find_body(sections(prev_seed or ""),
                                                              "decisions"))
            if "<!--" not in ln and not is_hole(ln)]
    new_body = contract.find_body(sections(new_seed or ""), "decisions") + \
        contract.find_body(sections(archive or ""), "decisions")
    new = [_bare(ln) for ln in live_lines(new_body)]
    # SOC-181: закрытое в Linear снято сводной строкой с номерами, сжатое потолком —
    # строкой «где полное»: это не потеря
    gone_keys, _nums, capped = _sc.dropped_refs(new_body)
    if capped:
        return []
    out = []
    for d in prev:
        if not d or d.lower().startswith("снято:") or _sc._DROPPED_RE.match(d) \
                or _sc.CAPPED_MARK in d:
            continue
        keys = set(linear_keys(d))
        if keys and keys <= gone_keys:
            continue
        dw = _sig_words(d)
        if not any(norm_line(d) == norm_line(n) or (
                dw and len(dw & _sig_words(n)) >= SIMILAR_SHARE * len(dw)) for n in new):
            out.append(d)
    return out


# SOC-143 F77: «ждёт Гришу» копится, как опыт (F22) и решения (F63). Метка «Ждёт
# Гришу:» — состояние из самого свежего пульса, и пункт входного сида, который
# передающая не повторила (тап по tg-ссылке, tg 3409), пропадал молча. Теперь пункт
# входного сида переходит с пометкой «(из прошлого сида)»; снятый пульсом сессии
# («снимаю …», «заменяет 3399») — строкой «снято: … — причина: …»; закрытые и
# чужие задачи Linear снимает :func:`drop_resolved` (F73/F85). Вычисляемое заново
# — «(из следующего шага)», «SOC-N на проверке» из Linear — не наследуется.
_AWAIT_DERIVED = re.compile(r"\(из следующего шага\)|^SOC-\d+ на проверке \(", re.IGNORECASE)
_AWAIT_REVOKE = re.compile(r"сня[тл]|снима|отмен|больше не|не в силе|заменя|заменен|"
                           r"заменён|закрыт|решён|решен|ответил|superseded|dropped",
                           re.IGNORECASE)
_TG_NUM = re.compile(r"(?:tg|❓)\s*~?(\d{3,6})(?!\d)", re.IGNORECASE)
_NO_AWAIT = re.compile(r"^(?:нет|ничего|никого|ничто)(?!\w)", re.IGNORECASE)
REVOKE_QUOTE = 90


def _await_inheritable(line: str) -> str:
    """Тело пункта «ждёт Гришу» входного сида, который наследуется; иначе ""."""
    if "<!--" in line or is_hole(line):
        return ""
    # SOC-181: статус Linear прошлой сборки снят — пометка «(из прошлого сида)» снова
    # в конце строки и не множится; сводные строки «снято из сида» не наследуются
    body = _sc.strip_linear_tails(_bare(line))
    if not body or body.lower().startswith("снято:") or _NO_AWAIT.match(body) \
            or _AWAIT_DERIVED.search(body) or _sc._DROPPED_RE.match(body) \
            or _sc.CAPPED_MARK in body:
        return ""
    return body


def _revoke_clauses(context: Sequence[str]) -> List["tuple[str, str]"]:
    """``[(ЧЧ:ММ, кусок)]`` — куски пульсов сессии со словом снятия (F77)."""
    out = []
    for stamp, note in pulse_notes(context):
        for sent in _SENT_END.split(note):
            for piece in _ITEM_SPLIT.split(sent):
                piece = piece.strip().rstrip(".;").strip()
                if piece and _AWAIT_REVOKE.search(piece):
                    out.append((stamp, piece))
    return out


def _revoke_reason(stamp: str, piece: str, num: str = "") -> str:
    piece = re.sub(r"[()]", "", piece)
    at = piece.find(num) if num else -1
    if at > REVOKE_QUOTE - 20:  # длинный кусок — окно вокруг снятого номера
        start = piece.rfind(" ", 0, at - (REVOKE_QUOTE - 30)) + 1
        piece = "…" + piece[start:]
    quote = clip_words(piece, REVOKE_QUOTE)
    return "«{0}»{1}".format(quote, " — пульс {0}".format(stamp) if stamp else "")


def _await_nums(text: str) -> set:
    return set(_TG_NUM.findall(text or ""))


_INHERITED_TAIL = re.compile(r"\s*\(из прошлого сида([^()]*)\)\s*$")
_GONE_NUM = re.compile(r"tg (\d+) — («[^«»]*(?:«[^«»]*»[^«»]*)*»(?: — пульс \d\d:\d\d)?)")


def _await_covered(item: str, lines: Sequence[str]) -> bool:
    """Пункт уже есть среди *lines*: тем же текстом или на :data:`SIMILAR_SHARE` его слов."""
    w = _sig_words(item)
    return any(norm_line(item) == norm_line(x) or (
        len(w) >= SIMILAR_MIN_WORDS and len(w & _sig_words(x)) >= SIMILAR_SHARE * len(w))
        for x in lines)


def inherit_awaiting(inherited: Sequence[str], current: Sequence[str] = (),
                     context: Sequence[str] = ()) -> List[str]:
    """Пункты «ждёт Гришу» входного сида → строки этого сида (F77).

    Пункт, который уже есть в *current* (тем же текстом, похожими словами или всеми
    своими tg-номерами), не дублируется. Пункт с tg-номерами снят, если кусок пульса
    *context* со словом снятия (:data:`_AWAIT_REVOKE`) называет его номер: все номера
    сняты — ``- снято: … — причина: «…» — пульс ЧЧ:ММ``; часть — пункт переходит с
    пометкой, какой номер снят и чем. Без номеров — как решения (:func:`_revokes`).
    Остальное — с пометкой :data:`INHERITED_MARK`."""
    cur_text = "\n".join(current)
    cur_nums = _await_nums(cur_text)
    clauses = _revoke_clauses(context)
    out: List[str] = []
    for ln in live_lines(inherited):
        body = _await_inheritable(ln)
        if not body:
            continue
        # уже переходившая строка: её пометка — без повтора, снятые номера помнятся
        mark = _INHERITED_TAIL.search(body)
        clean = body[:mark.start()] if mark else body
        gone: Dict[str, str] = dict(_GONE_NUM.findall(mark.group(1))) if mark else {}
        nums = _await_nums(clean)
        if _await_covered(clean, current) or (nums and nums <= cur_nums):
            continue  # пункт уже в этом сиде
        why = ""
        if nums:
            for n in sorted(nums):
                hit = next(((st, p) for st, p in clauses
                            if re.search(r"(?<!\d){0}(?!\d)".format(n), p)
                            and not (n in cur_nums)), None)
                if hit:
                    gone[n] = _revoke_reason(hit[0], hit[1], n)
            if gone and not nums - set(gone) - cur_nums:  # все номера сняты или уже здесь
                why = "; ".join(["tg {0} — {1}".format(n, r) for n, r in sorted(gone.items())]
                                + ["tg {0} — уже в «ждёт Гришу» этого сида".format(n)
                                   for n in sorted(nums & cur_nums - set(gone))])
        else:
            hit = next(((st, p) for st, p in clauses if _revokes(p, clean)), None)
            if hit:
                why = _revoke_reason(*hit)
        if why:
            out.append("- снято: {0} — причина: {1}".format(_LINE_SUFFIX.sub("", clean), why))
        elif gone:
            out.append("- {0} {1}; снято: {2})".format(
                clean, INHERITED_MARK[:-1], "; ".join(
                    "tg {0} — {1}".format(n, re.sub(r"[()]", "", r))
                    for n, r in sorted(gone.items()))))
        else:
            out.append("- {0} {1}".format(clean, INHERITED_MARK))
    return out


def dropped_awaiting(prev_seed: str, new_seed: str, archive: str = "") -> List[str]:
    """Пункты «ждёт Гришу» прошлого сида, которых нет в новом ни строкой, ни
    «снято: …», ни своими tg-номерами (F77) — для TG о приёме. Пункты только про
    задачи Linear (без tg-номера) — не потеря: их снимает сверка с Linear (F85).
    SOC-181: пункт, ушедший в архив сида (*archive*: сверх 7, срок наследования,
    приёмка у проверяющего), — не потеря."""
    prev = [_await_inheritable(ln) for ln in live_lines(
        contract.find_body(sections(prev_seed or ""), "awaiting"))]
    new_body = contract.find_body(sections(new_seed or ""), "awaiting") + \
        contract.find_body(sections(archive or ""), "awaiting")
    from .seed_budget import strip_await_tag  # lazy: seed_budget зовёт этот модуль
    new = [_bare(strip_await_tag(ln)) for ln in live_lines(new_body)]
    new_nums = _await_nums("\n".join(new))
    # SOC-181: отвеченное и закрытое снято сводной строкой с номерами — не потеря
    gone_keys, gone_nums, capped = _sc.dropped_refs(new_body)
    if capped:
        return []
    out = []
    for p in prev:
        if not p or _sc._DROPPED_RE.match(p) or _sc.CAPPED_MARK in p:
            continue
        keys, nums_p = _sc.refs_of(p)
        if (keys and keys <= gone_keys) or (nums_p and nums_p <= gone_nums) \
                or _sc._ANSWERED.search(p):
            continue
        mark = _INHERITED_TAIL.search(p)
        p = p[:mark.start()] if mark else p
        nums = _await_nums(p)
        if not nums and linear_keys(p):
            continue
        if (nums and nums <= new_nums) or _await_covered(p, new):
            continue
        out.append(p)
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
                     candidate_gists: Optional[Dict[str, str]] = None,
                     folded: bool = False) -> List[str]:
    """Опыт: унаследованный из входного сида + кандидаты сессии + каркас для агента.

    Опыт копится от сессии к сессии (SOC-101 F3): строки «опыта» входного сида
    переходят дальше как есть, без подсказок куратора. Кандидат — строкой с его
    сутью, а не голым номером: сид читают без контекста (SOC-74). Грабли, петли и
    «отвергнуто» этой сессии пишет курирующий — подсказка, а не выдуманный текст.
    """
    # F39: метка ПЕРЕДАЧИ, слой нити и прошлый сид пишут один урок разными знаками;
    # *folded* — уже склеено (SOC-181, seed_compress.fold_experience)
    kept = [ln for ln in live_lines(inherited) if "<!--" not in ln and not is_hole(ln)]
    out: List[str] = kept if folded else fold_similar(kept)
    gists = candidate_gists or {}
    if candidate_nums and gists:
        for num in sorted(set(candidate_nums), key=lambda n: (int(n), n)):
            line = "- кандидат {0} — {1}".format(num, gists.get(num) or "без описания")
            if norm_line(line) not in {norm_line(x) for x in out}:
                out.append(line)
    elif candidate_nums:
        out.append("- кандидаты сессии: {0}".format(_nums_phrase(candidate_nums)))
    out.append(
        "- {0}  <!-- курируй: грабли · петли · «отвергнуто» — <путь> — <почему нет> -->"
        .format(EMPTY)
    )
    return out


COLLECTED = "(карта собрана сборщиком)"
MAP_WARNING = contract.MAP_WARNING


def block_entry_map(inherited: Sequence[str] = (), labeled: Sequence[str] = (),
                    collected: Sequence[str] = ()) -> List[str]:
    """Карта входа (C11): метка «Карта:» свежего пульса → унаследованная карта
    входного сида → карта, собранная сборщиком с диска (*collected*), + каркас.

    Карту, которую написала передающая сессия, сборщик раскладывает по пунктам и
    ничего к ней не добавляет. Нет её — передаём карту из входного сида (SOC-101
    F4), куратор правит дифф. Нет и её — пункты *collected* с пометкой
    :data:`COLLECTED`: честный прочерк холодной сессии не помогает.
    """
    kept = [ln for ln in live_lines(inherited)
            if "<!--" not in ln and not is_hole(ln) and COLLECTED not in ln
            and not ln.lstrip("- ").startswith(("топик:", "автохендофф:", "указатель:"))]
    # ↑ указатель на топик считается заново; заглушка автохендоффа и собранная
    # сборщиком карта — не карта (SOC-101: заглушки наследовались и множились)
    hint = ["- {0}  <!-- пишет агент: что читать и зачем, в порядке чтения; "
            "кодовый шаг — файл/модуль, команда тестов, процедура выката -->".format(EMPTY)]
    if labeled:
        return list(labeled)
    if kept:
        return kept + hint
    return list(collected) + hint


def pass_without_map(context: Sequence[str]) -> bool:
    """Последний пульс «ПЕРЕДАЧА» есть, а метки «Карта:» в нём нет (C11)."""
    notes = pulse_notes(context)
    for _stamp, note in reversed(notes):
        if contract.is_pass_note(note):
            return contract.pass_note_without_map(note)
    return False


# Команда тестов в опыте или в транскрипте: «… -m pytest …», «npm test», «go test».
_TEST_CMD = re.compile(r"(?:\b(?:env\s+-u\s+\S+\s+)*(?:[A-Z_]+=\S+\s+)*\S*python3?\s+-m\s+pytest"
                       r"|\bpytest\b|\bnpm\s+(?:run\s+)?test\b|\bgo\s+test\b|\bnode\s+--test\b)")


def test_command(experience: Sequence[str] = (), transcripts: Sequence[Path] = ()) -> str:
    """Команда тестов кодовой нити (F50): из опыта (строка с командой в бэктиках или
    после «тесты:»), иначе — последний прогон тестов в транскриптах сессии и её
    воркеров (сегмент ``cd … && … pytest …``). Нет — пусто."""
    import json

    for ln in experience:
        for code in re.findall(r"`([^`]+)`", ln):
            if _TEST_CMD.search(code):
                return "{0} (из опыта)".format(code.strip())
    last = ("", "")
    for t in transcripts:
        try:
            raw = Path(t).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for ln in raw.splitlines():
            if '"tool_use"' not in ln or "test" not in ln:
                continue
            try:
                row = json.loads(ln)
            except ValueError:
                continue
            for b in ((row.get("message") or {}).get("content") or []) if isinstance(row, dict) else []:
                if not isinstance(b, dict) or b.get("name") != "Bash":
                    continue
                cmd = str((b.get("input") or {}).get("command") or "").split("\n", 1)[0]
                parts = [x.strip() for x in cmd.split("&&")]
                hit = [i for i, x in enumerate(parts) if _TEST_CMD.search(x)]
                if not hit:
                    continue
                seg = parts[hit[0]].split("|", 1)[0].split(" 2>", 1)[0].strip()
                cd = [x for x in parts[:hit[0]] if x.startswith("cd ")]
                got = " && ".join(cd[-1:] + [seg])
                ts = str(row.get("timestamp") or "")
                if ts >= last[0]:
                    last = (ts, got)
    return "{0} (последний прогон в транскрипте)".format(last[1]) if last[1] else ""


def touched_files(transcripts: Sequence[Path], since=None, limit: int = 6) -> List[str]:
    """Последние тронутые кодовые файлы сессии и её воркеров (F50): ``file_path``
    Edit/Write и файлы коммитов ``git commit`` из их транскриптов — свежие сверху.
    Файлы арок (``/.tide/``) — не код."""
    import json

    found = {}
    for t in transcripts:
        try:
            raw = Path(t).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for ln in raw.splitlines():
            if '"tool_use"' not in ln:
                continue
            try:
                row = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            ts = str(row.get("timestamp") or "")
            for b in (row.get("message") or {}).get("content") or []:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                inp = b.get("input") or {}
                paths = []
                if b.get("name") in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
                    paths = [str(inp.get("file_path") or inp.get("notebook_path") or "")]
                elif b.get("name") == "Bash":
                    cmd = str(inp.get("command") or "")
                    m = re.search(r"^cd\s+(\S+)", cmd)
                    base = m.group(1) if m else ""
                    for m2 in re.finditer(r"\bgit\s+add\s+([^&|;\n]+)", cmd):
                        for f in m2.group(1).split():
                            if f.startswith("-") or f in (".", "-A"):
                                continue
                            paths.append(f if f.startswith("/") or not base
                                         else "{0}/{1}".format(base.rstrip("/"), f))
                for f in paths:
                    if f and "/.tide/" not in f and not f.startswith(".tide/"):
                        found[f] = max(found.get(f, ""), ts)
    order = sorted(found, key=lambda f: found[f], reverse=True)
    return order[:limit]


def session_transcripts(session_entry: Path) -> List[Path]:
    """Транскрипт передающей сессии и транскрипты её сабагентов."""
    try:
        sid = (fields.read_field(Path(session_entry) / "arc.md",
                                 offload.CLAUDE_SESSION_FIELD) or "").strip()
        main = transcript_path(sid)
    except Exception:  # noqa: BLE001
        return []
    if main is None:
        return []
    subs = sorted((main.parent / main.stem / "subagents").glob("*.jsonl"))
    return [main] + subs


def collected_map(root: Path, session_entry: Path, thread_entry: Path,
                  experience: Sequence[str] = (),
                  transcripts: Optional[Sequence[Path]] = None) -> List[str]:
    """Карта-черновик с диска (C11): «главное» plan.md нити (только если оно есть —
    F50), topic.md нити (или goal-файл со слоями), паспорт передающей сессии; для
    кодовой нити — последние тронутые файлы и команда тестов (F50)."""
    from ..arc import plan_main

    out: List[str] = []
    plan = Path(thread_entry) / "plan.md"
    try:
        has_main = bool(plan_main.live_lines(thread_entry))
    except Exception:  # noqa: BLE001
        has_main = False
    if plan.is_file() and has_main:
        out.append("- {0} — «главное»: SOC-N, кому сдавать, с кем не дублировать {1}".format(
            plan.resolve(), COLLECTED))
    topics = thread_topics(root, thread_entry)
    for f in topics:
        out.append("- {0} — топик нити: что Гриша ждёт по теме, что в полёте, "
                   "Linear-родитель {1}".format(f.resolve(), COLLECTED))
    if not topics:
        goal = _stream.passport_path(thread_entry)
        if goal.is_file():
            out.append("- {0} — слои нити: ожидание Гриши, где мы, опыт {1}".format(
                goal.resolve(), COLLECTED))
    arc = Path(session_entry) / "arc.md"
    if arc.is_file():
        out.append("- {0} — паспорт прошлой сессии: курсор, ## context с пульсом "
                   "ПЕРЕДАЧА {1}".format(arc.resolve(), COLLECTED))
    if transcripts is None:
        transcripts = session_transcripts(session_entry)
    files = touched_files(transcripts)
    if files:
        out.append("- код (последние тронутые сессией и воркерами): {0} {1}".format(
            ", ".join(files), COLLECTED))
        cmd = test_command(experience, transcripts)
        if cmd:
            out.append("- тесты: {0} {1}".format(cmd, COLLECTED))
    return out


_PULSE_STAMP = re.compile(r"^-?\s*\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})(?::\d{2})?\s*[—-]?\s*")


_GIST_NAME_DOT = re.compile(r"\s·\s(?=(?:\d{2}|NN)\b)")
_GIST_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZА-ЯЁ0-9«\"(])|\s·\s")


def clip_words(text: str, limit: int) -> str:
    """*text* не длиннее *limit* знаков, с «…» — по границе слова (F40: «3389076 F…»,
    «82d24a…» резали хеш посреди). Слово длиннее половины лимита режется как есть."""
    if len(text) <= limit:
        return text
    cut = text[: max(1, limit - 1)]
    sp = cut.rfind(" ")
    if sp > limit // 2:
        cut = cut[:sp]
    return cut.rstrip(" ,;:·—–-") + "…"


def _short_stamp(line: str) -> str:
    """Строка пульса со штампом ``ЧЧ:ММ`` вместо полного: ``- 08:52 — …``."""
    raw = line.strip().lstrip("-").strip()
    m = _PULSE_STAMP.match("- " + raw)
    return "- " + ((m.group(1) + " — " + raw[m.end() - 2:]) if m else raw)


def journal_line(line: str, limit: int) -> str:
    """Строка журнала в *limit* знаков: штамп ``ЧЧ:ММ`` и сколько влезет, по словам."""
    return clip_words(_short_stamp(line), limit)


def _fill_limit(lengths: Sequence[int], avail: int, floor: int) -> int:
    """Наибольший потолок L ≥ *floor*, при котором Σ min(len, L)+1 ≤ *avail*
    (короткие строки идут целиком, их остаток бюджета достаётся длинным)."""
    top = max(lengths) if lengths else floor
    if sum(n + 1 for n in lengths) <= avail:
        return top
    lo, hi = floor, top
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if sum(min(n, mid) + 1 for n in lengths) <= avail:
            lo = mid
        else:
            hi = mid - 1
    return lo


def pulse_gist(line: str, limit: int = JOURNAL_GIST) -> str:
    """Строка пульса сжато: ``- ЧЧ:ММ — первая фраза`` до *limit* знаков.

    Дата у пульсов одной сессии почти всегда одна, а первая фраза — это и есть
    сделанный шаг; хвост строки (детали, пути) лежит в паспорте целиком."""
    raw = line.strip().lstrip("-").strip()
    m = _PULSE_STAMP.match("- " + raw)
    stamp, rest = (m.group(1) + " — ", raw[m.end() - 2:]) if m else ("", raw)
    # F31: « · NN» — часть имени («context-keeper · 03»), «;» — середина правила:
    # режем только по концу предложения или по « · » между фразами
    first = _GIST_SPLIT.split(_keep_name_dots(_GIST_NAME_DOT.sub(_KEEP, rest)),
                              maxsplit=1)[0].replace(_KEEP, " · ").strip()
    return clip_words("- " + stamp + first, limit)


def block_journal(context: Sequence[str], *, budget: int, source: str = "",
                  tail: int = PULSE_TAIL) -> List[str]:
    """Пульс старше хвоста курсора — не тонет (SOC-101 F5).

    Курсор несёт последние *tail* строк; всё, что старше, идёт сюда свежим сверху:
    целиком, пока влезает в *budget* знаков, иначе сжатой строкой
    (:func:`journal_line`): потолок строки — сколько даёт бюджет на все строки
    (F40), но не меньше :data:`JOURNAL_GIST`, режется по словам; что не влезло и
    так — счётом и путём к полной записи (*source*). Пусто — когда старше хвоста
    ничего нет.
    """
    pulse = live_lines(context)
    n_old = max(0, len(pulse) - tail) if tail > 0 else 0
    if not n_old:
        return []
    keep = {i for i in range(n_old) if is_protected_pulse(pulse, i)}
    full = [(i, pulse[i] if pulse[i].lstrip().startswith("-") else "- {0}".format(pulse[i]))
            for i in reversed(range(n_old))]
    if sum(len(ln) + 1 for i, ln in full if i not in keep) <= budget:
        return [ln for _i, ln in full]  # влезает целиком — ничего не сжимаем
    out: List[str] = []
    pointer = "- (сжато строк: 00, не вошло: 00 — полностью: {0})".format(
        source or "## context паспорта сессии")
    used, clipped, left = len(pointer) + 1, 0, 0  # указатель тоже в бюджете
    limit = _fill_limit([len(_short_stamp(ln)) for i, ln in full if i not in keep],
                        budget - used, JOURNAL_GIST)
    for i, ln in full:  # не влезает — ВСЕ строки сжатыми, чтобы дошло больше шагов
        if i in keep:
            out.append(ln)  # ПЕРЕДАЧА — целиком и вне бюджета
            continue
        short = journal_line(ln, limit)
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
    правит только дифф (что умерло / что родилось). Голый прочерк прошлого сида
    («—» без слова) не наследуется: пустой блок скажет причину (F23).
    """
    return [ln for ln in live_lines(env) if ln.strip(" -\t—–")]


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
        if b.key == "where" and TAILS in bodies:
            # C12/F40: хвосты — сразу за «где мы», вне контракта, пустой — со словом
            tails = [ln for ln in bodies[TAILS] if ln.strip()] \
                or [contract.TAILS_NONE, contract.TAILS_EMPTY]
            lines += ["## {0}".format(TAILS), "", *tails, ""]
    return "\n".join(lines).rstrip() + "\n"


# Блоки, где номер задачи, закрытой в Linear, помечается на месте (F43): там его
# читают как живую работу — шаг плана, «ждёт Гришу», открытый хвост.
# SOC-143 F73: статус «SOC-107 (In Review)» из пульса врал и в «где мы», «ожидании»,
# «следующем шаге» — помечаются все блоки, где номер читают как состояние; кроме
# «задач Linear» (статус там свой), «решений» (F69) и сырого журнала (история).
MARK_CLOSED_IN = (MAIN, EXPECTATION, WHERE, TAILS, EXPERIENCE, AWAITING, NEXT, INFLIGHT,
                  UNANSWERED, ENTRY_MAP, ENVIRONMENT)


def mark_closed(lines: Sequence[str], live) -> List[str]:
    """Пометить в *lines* номера задач, закрытых в Linear (F43): к строке —
    «(SOC-57 — Done в Linear)». *live* — ответ :func:`linear_states`; не словарь
    (сверки не было / Linear не ответил) — строки как есть."""
    if not isinstance(live, dict):
        return list(lines)
    out: List[str] = []
    for ln in lines:
        if _sc._DROPPED_RE.match(ln.strip()):
            out.append(ln)  # сводная строка снятого — номера там уже закрытые
            continue
        ln = _sc._CLOSED_TAIL.sub("", ln).rstrip()  # SOC-181: статус заменяется
        closed = []
        for key in linear_keys(ln):
            name, kind = live.get(key, ("", ""))
            tag = "{0} — {1} в Linear".format(key, name or "закрыта")
            if kind in CLOSED_TYPES and tag not in closed and tag not in ln:
                closed.append(tag)
        out.append(ln + (" ({0})".format("; ".join(closed)) if closed else ""))
    return out


# F51: номера задач не только из пульсов — из цели нити и из прошлого сида. Скрытый
# ключ тел блоков (render_draft его не печатает): ``[(источник, текст)]``.
LINEAR_SOURCES = "_linear_sources"


def mark_linear_source(lines: Sequence[str], seen_text: str,
                       sources: Sequence["tuple[str, str]"]) -> List[str]:
    """Строке задачи, которой нет в тексте сида и пульсах, — откуда она: «(из цели
    нити)», «(из прошлого сида)». Молча номер не выпадает и не появляется."""
    have = set(linear_keys(seen_text))
    out = []
    for ln in lines:
        keys = linear_keys(ln)
        tag = ""
        if keys and keys[0] not in have:
            for name, text in sources:
                if keys[0] in linear_keys(text):
                    tag = " (из {0})".format(name)
                    break
        out.append(ln + tag)
    return out


def review_awaiting(live, foreign: Sequence[str] = ()) -> List[str]:
    """Задачи Linear на проверке (F48) — пункты «ждёт Гришу»: In Review ждёт приёмки
    (Гриша смотрит, Done ставит проверяющий уровнем выше). *live* не словарь — [].
    Чужие (*foreign*, ведёт другая нить — SOC-143 F85) — не пункт этой нити."""
    if not isinstance(live, dict):
        return []
    out = []
    for key in sorted(live, key=lambda k: int(k.rsplit("-", 1)[1])):
        name = str((live.get(key) or ("", ""))[0] or "")
        if "review" in name.lower() and key not in foreign:
            out.append("- {0} на проверке ({1}) — предлагаю: посмотреть и сказать слово "
                       "через менеджера; Done ставит проверяющий".format(key, name))
    return out


# SOC-143 F85/F71: чья задача. Скрытый ключ тел блоков — ``[проект, нить]`` этой нити.
THREAD_SELF = "_thread_self"
_DONT_DUP = re.compile(r"не\s+дублир\w*", re.IGNORECASE)


def dont_dup_keys(lines: Sequence[str]) -> List[str]:
    """Номера задач из строк «не дублировать: …» — их ведут другие нити (F71)."""
    out: List[str] = []
    for ln in lines:
        m = _DONT_DUP.search(ln or "")
        if m:
            out += [k for k in linear_keys(ln[m.end():]) if k not in out]
    return out


def foreign_issues(keys: Sequence[str], live, me: Sequence[str] = (),
                   dont_dup: Sequence[str] = ()) -> Dict[str, str]:
    """``{SOC-N: «ведёт …»}`` — задачи, которые ведёт ДРУГАЯ нить (F85/F71).

    Главнее — Linear: комментарии «Взял: <проект> — <нить> · NN» (*live*.takers);
    есть такие, и ни один не от этой нити (*me* = ``[проект, нить]``) — чужая. Нет
    их — номер из строки «не дублировать» чужой. Нить не известна — по Linear не
    судим, только «не дублировать»."""
    takers = getattr(live, "takers", None) or {}
    proj, thread = (list(me) + ["", ""])[:2]
    out: Dict[str, str] = {}
    for k in keys:
        got = takers.get(k) or []
        if got and thread:
            mine = any(t == thread and (not proj or not p or p == proj) for p, t in got)
            if not mine:
                p, t = got[-1]
                out[k] = "ведёт {0} — {1}".format(p, t)
        elif not got and k in dont_dup:
            out[k] = "в «не дублировать»"
    return out


# SOC-181 п.13: проект, где свой трекер — у заказчика (mitehq: MIT, его правила).
# SOC-строки шаблона сборщика туда не подмешиваются: в «задачи Linear» и «ждёт Гришу»
# едут только задачи SOC самой нити (:func:`own_issues`); номер, попавший в текст
# почтой или поручением («доска SOC-112 читает механически»), — не её задача.
# Явно: ``tracker=customer`` / ``tracker=own`` в ``.tide/canon/config`` проекта.
CUSTOMER_TRACKER = "_customer_tracker"
CUSTOMER_TRACKER_PROJECTS = frozenset({"mitehq"})


def customer_tracker(root: Path) -> bool:
    """Свой трекер проекта — у заказчика (SOC-181 п.13)? Никогда не падает."""
    try:
        from ..canon import store as _store

        val = (_store.config_value(Path(root), "tracker") or "").strip().lower()
    except Exception:  # noqa: BLE001 — сид важнее
        val = ""
    if val:
        return val == "customer"
    return _project_name(root) in CUSTOMER_TRACKER_PROJECTS


def own_issues(keys: Sequence[str], live, me: Sequence[str] = (),
               named_text: str = "") -> List[str]:
    """Задачи SOC самой нити: названы в её цели/«главном» (*named_text*) или взяты
    ею в Linear («Взял: <проект> — <нить> · NN», *live*.takers). Остальные номера
    текста — чужие (SOC-181 п.13, проект с трекером заказчика)."""
    named = set(linear_keys(named_text or ""))
    takers = getattr(live, "takers", None) or {}
    proj, thread = (list(me) + ["", ""])[:2]
    out: List[str] = []
    for k in keys:
        mine = bool(thread) and any(
            t == thread and (not proj or not p or p == proj) for p, t in takers.get(k) or [])
        if k in named or mine:
            out.append(k)
    return out


# F85: пункт «ждёт Гришу» про приёмку/Done задачи, которая уже закрыта в Linear, — снят.
_REVIEW_ASK = re.compile(r"\bDone\b|In Review|на проверк|приёмк|приемк|приня|закрыт|"
                         r"посмотреть", re.IGNORECASE)


def drop_resolved(lines: Sequence[str], live, foreign: Sequence[str] = ()) -> List[str]:
    """«Ждёт Гришу» без пунктов, которые решены (F85): все номера строки закрыты в
    Linear (SOC-181: закрытое выкидывается, о чём бы ни был пункт); или строка просит
    приёмку/Done, а номера закрыты или чужие («SOC-102 на проверке» ведёт board).
    *live* не словарь — как есть."""
    if not isinstance(live, dict):
        return list(lines)
    out = []
    for ln in lines:
        keys = linear_keys(ln)
        closed = [k for k in keys if (live.get(k) or ("", ""))[1] in CLOSED_TYPES]
        if keys and len(closed) == len(keys) and not _sc._DROPPED_RE.match(ln.strip()):
            continue
        if keys and _REVIEW_ASK.search(ln) and all(k in closed or k in foreign for k in keys):
            continue
        out.append(ln)
    return out


def drop_closed_decisions(lines: Sequence[str], live) -> "tuple[List[str], List[str]]":
    """Решения без закрытых (SOC-181): строка, все задачи Linear которой закрыты, — вон
    из сида (на диске она остаётся); правило нити «в силе» не закрывается задачей.
    ``(оставшиеся, снятые)``; *live* не словарь — всё остаётся."""
    if not isinstance(live, dict):
        return list(lines), []
    keep, gone = [], []
    for ln in lines:
        keys = linear_keys(_sc.strip_linear_tails(ln))
        if keys and "правило, в силе" not in ln and not ln.startswith(" ") and all(
                (live.get(k) or ("", ""))[1] in CLOSED_TYPES for k in keys):
            gone.append(ln)
        else:
            keep.append(ln)
    return keep, gone


# Скрытые ключи тел блоков (render_draft их не печатает), SOC-181: входной сид (куда
# смотреть полное) и пункты «ждёт Гришу», снятые как отвеченные при сборке тел.
SEED_IN_PATH = "_seed_in_path"
AWAITING_DROPPED = "_awaiting_dropped"


def _drop_refs(lines: Sequence[str]) -> List[str]:
    refs: List[str] = []
    for ln in lines:
        refs += ["tg {0}".format(n) for n in sorted(_sc.await_nums(ln))]
        refs += linear_keys(_sc.strip_linear_tails(ln))
    return refs


def next_for_grisha(nxt_lines: Sequence[str]) -> List[str]:
    """Шаги ``--next``, которые зовут Гришу («дать Грише на проверку»), — в «ждёт
    Гришу» (F48)."""
    return ["{0} (из следующего шага)".format(ln.rstrip())
            for ln in nxt_lines if re.search(r"Гриш", ln)]


def seal(bodies: Dict[str, List[str]], *, context: Sequence[str], header: str = "",
         source: str = "", clip: int = 0, journal_off: str = "") -> str:
    """Дописать вычисляемые блоки и отрендерить: сырой журнал и задачи Linear.

    Сырой журнал (хвост пульса в «где мы» + пульс старше него) — не больше
    :data:`JOURNAL_SHARE` тела: J ≤ (share·R − T) / (1 − share), R — тело без
    журнала, T — хвост пульса в «где мы». *journal_off* — строка-указатель вместо
    журнала (сид менеджера, :func:`journal_pointer`): журнал остаётся на диске.
    Задачи Linear — каждая упомянутая в сиде, со ссылкой."""
    out = dict(bodies)
    out[JOURNAL], out[LINEAR] = [], []
    plain = {k: list(out[k]) for k in MARK_CLOSED_IN if k in out}
    awaiting = list(out.get(AWAITING) or [])
    decisions = list(out.get(DECISIONS) or [])
    share = JOURNAL_SHARE
    rest = len(render_draft(out, header=header))
    tail = raw_pulse_chars(context, clip=clip)
    budget = max(0, int((share * rest - tail) / (1 - share)))
    for _ in range(8):  # формула — оценка; доля проверяется по готовому тексту
        # SOC-181: указатель — когда журнал есть (пульс старше хвоста курсора); нет —
        # блок пуст по правде («пульса старше курсора нет»)
        has_old = len(live_lines(context)) > PULSE_TAIL
        out[JOURNAL] = (([journal_off] if has_old else []) if journal_off else
                        block_journal(context, budget=budget, source=source))
        # номера — и из пульсов, обрезанных журналом (acceptance-02 §9 п.9)
        seen = render_draft(out, header=header) + "\n" + "\n".join(context)
        sources = list(out.get(LINEAR_SOURCES) or [])
        hay = seen + "".join("\n" + text for _name, text in sources)
        keys = linear_keys(hay)
        live = linear_states(keys)
        foreign = foreign_issues(  # F85/F71: чужие задачи — не задачи нити
            keys, live, out.get(THREAD_SELF) or (),
            dont_dup_keys([ln for k in (MAIN, AWAITING, NEXT) for ln in out.get(k) or []]))
        only = None
        if out.get(CUSTOMER_TRACKER):  # SOC-181 п.13: трекер заказчика — только свои SOC
            named = "\n".join([text for name, text in sources if name == "цели нити"]
                              + list(out.get(MAIN) or []))
            only = own_issues(keys, live, out.get(THREAD_SELF) or (), named)
            foreign = dict(foreign, **{k: "трекер заказчика — не задача нити"
                                       for k in keys if k not in only})
        out[LINEAR] = mark_linear_source(
            block_links(hay, status_text="\n".join(reversed(list(context))), live=live,
                        foreign=foreign, only=only,
                        mine=mine_issues(keys, live, out.get(THREAD_SELF) or ())),
            seen, sources)
        where = "".join(out.get(SEED_IN_PATH) or [])
        if AWAITING in out:  # F48: задачи на проверке ждут Гришу (F85: свои, не Done)
            merged = _merge_lines(awaiting, review_awaiting(live, foreign))
            out[AWAITING] = drop_resolved(merged, live, foreign)
            # SOC-181: отвеченное и закрытое — одной строкой с номерами, полное на диске
            gone = list(out.get(AWAITING_DROPPED) or []) + [
                ln for ln in merged if ln not in out[AWAITING]]
            if gone:
                out[AWAITING] = out[AWAITING] + [_sc.dropped_line(
                    len(gone), _drop_refs(gone), "отвечено или закрыто в Linear", where)]
            if AWAITING in plain:
                plain[AWAITING] = list(out[AWAITING])
        for k, body in plain.items():  # F43: закрытые в Linear — помечены на месте
            if k in out:
                out[k] = mark_closed(body, live)
        if DECISIONS in out:  # F69: статус решения с задачей — из Linear
            out[DECISIONS], gone = drop_closed_decisions(
                mark_decision_linear(decisions, live), live)
            if gone:  # SOC-181: закрытое в Linear — вон из сида, одной строкой
                out[DECISIONS] = out[DECISIONS] + [_sc.dropped_line(
                    len(gone), _drop_refs(gone), "закрыто в Linear", where)]
        text = render_draft(out, header=header)
        over = tail + sum(len(ln) + 1 for ln in out[JOURNAL]
                          if not _PASS_RE.match(ln.strip())) - share * len(text)
        if over <= 0 or budget == 0:
            break
        budget = max(0, budget - int(over) - 1)
    return text


def journal_pointer(root: Path, session_entry: Path) -> str:
    """Сид без сырого журнала (Гриша «Давай А», SOC-143 10.10 — сначала для нити
    менеджера; SOC-181 — для всех нитей): строка-указатель «сырой журнал — на диске:
    <паспорт прошлой сессии>, ## context». Журнал лежит в паспорте, сид несёт ссылку.
    Никогда не падает (ошибка — ``""``, журнал по бюджету, как был)."""
    try:
        session_entry = Path(session_entry)
        return "- сырой журнал — на диске: {0}, ## context".format(
            session_entry.resolve() / "arc.md")
    except Exception:  # noqa: BLE001 — сид важнее
        return ""


def seal_budgeted(bodies_at, *, context: Sequence[str], header: str = "",
                  source: str = "", journal_off: str = "") -> str:
    """Сид с журналом по бюджету (SOC-101 F16/F24): сжатие — только по бюджету.

    *bodies_at(clip)* — тела блоков с хвостом пульса в «где мы», обрезанным до
    *clip* (0 — целиком). Сначала всё целиком; не влез весь журнал в долю
    :data:`JOURNAL_SHARE` — сжимается старший журнал (:func:`block_journal`), а
    хвост в «где мы» режется до :data:`JOURNAL_CLIP`, потом до :data:`JOURNAL_GIST`,
    только если он один съел бы больше половины бюджета. Последний пульс и
    «ПЕРЕДАЧА» не режутся никогда (:func:`is_protected_pulse`).
    """
    warn_missing_map(context)
    text = ""
    for clip in (0, JOURNAL_CLIP, JOURNAL_GIST):
        bodies = bodies_at(clip)
        text = seal(bodies, context=context, header=header, clip=clip, source=source,
                    journal_off=journal_off)
        tail = raw_pulse_chars(context, clip=clip)
        if tail <= JOURNAL_SHARE * len(text) / 2:
            break
    return local_times(text)


# SOC-143 F86: в одном блоке «ушёл ~21:55 UTC» рядом с местными 19:2x — холодная
# читала это как несходство времени. Сид — в местном времени машины: «ЧЧ:ММ UTC»,
# «ЧЧ:ММ:ССZ», ISO «…T22:20:32Z» (и с датой «09.10 20:36 UTC») переводятся.
_UTC_ISO = re.compile(r"(?<![\w-])(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?"
                      r"(?:\.\d+)?(?:Z|\+00:?00)(?![\w:])")
_UTC_HM = re.compile(r"(?<![\w:./])(?:(\d{2})\.(\d{2})(?:\.(\d{4}))?\s+)?"
                     r"(\d{1,2}):(\d{2})(?::(\d{2}))?(?:Z(?!\w)|\s?(?:UTC|GMT)(?![\w+-]))")


def local_times(text: str, now=None) -> str:
    """UTC-время в тексте сида → местное (пояс машины), без пометки пояса: весь
    сид — в местном (F86). Дата без года — год *now*; без даты — сегодняшняя
    (для смещения пояса). Секунды и дата сохраняются, если были."""
    from datetime import datetime, timezone

    now = now or datetime.now()

    def conv(y, mo, d, h, mi, sec):
        try:
            utc = datetime(int(y), int(mo), int(d), int(h), int(mi), int(sec or 0),
                           tzinfo=timezone.utc)
        except ValueError:
            return None
        return utc.astimezone().replace(tzinfo=None)

    def iso(m):
        t = conv(*m.groups())
        if t is None:
            return m.group(0)
        return t.strftime("%Y-%m-%dT%H:%M" + (":%S" if m.group(6) else ""))

    def hm(m):
        dd, mm, yy, h, mi, sec = m.groups()
        t = conv(yy or now.year, mm or now.month, dd or now.day, h, mi, sec)
        if t is None:
            return m.group(0)
        out = t.strftime("%H:%M" + (":%S" if sec else ""))
        if dd:
            out = t.strftime("%d.%m" + (".%Y" if yy else "")) + " " + out
        return out

    return _UTC_HM.sub(hm, _UTC_ISO.sub(iso, text or ""))


def warn_missing_map(context: Sequence[str], stream=None) -> bool:
    """Предупредить передающую сессию (stderr), что в ПЕРЕДАЧЕ нет «Карта:» (C11).
    Не блокирует: сид соберётся с картой сборщика. True — предупредили."""
    import sys

    if not pass_without_map(context):
        return False
    print("tide: внимание — {0}".format(MAP_WARNING), file=stream or sys.stderr)
    return True


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
        return ["- " + ln.lstrip("-* ").strip() for ln in own] + plan_patches(thread_entry)
    kept = [ln for ln in live_lines(inherited) if not is_hole(ln) and "<!--" not in ln
            and not ln.lstrip("- ").startswith("менеджер")]  # адрес считается заново
    if kept:
        return kept
    if pred is not None:
        theirs = plan_main.live_lines(pred)
        if theirs:
            return ["- {0} (из нити-предшественницы {1})".format(
                ln.lstrip("-* ").strip(), pred.name) for ln in theirs]
    return []


PATCHES_HEAD = "патчи"


def plan_patches(thread_entry: Path) -> List[str]:
    """Раздел «## патчи» plan.md нити — в «главное» (F42): там уточнения Гриши к
    шагам (расход = токены + субагенты, шаги 3а–3в), и сборщик их терял. Строки
    как есть, под строкой-заголовком с путём; раздела нет / пуст — []."""
    plan = Path(thread_entry) / "plan.md"
    body = [ln.rstrip() for ln in section_body(sections(_read(plan)), PATCHES_HEAD)
            if ln.strip() and not ln.strip().startswith("<")]
    if not body:
        return []
    return ["- патчи плана (уточнения к шагам выше — сильнее их; {0} ## патчи):".format(
        plan.resolve())] + ["  " + ln if not ln.startswith(" ") else ln for ln in body]


TOPICS_DIRNAME = "topics"
TOPIC_FILE = "topic.md"
# Как нить связана с топиком (SOC-105): поле ``topic:`` в goal-файле/plan.md нити —
# сильнее всего; иначе топик сам называет нить — ``threads:``, ``дом:``,
# ``нити и работы:`` в topic.md.
_TOPIC_THREAD_KEYS = ("threads", "дом", "нити и работы", "нить", "нити")


def _topic_field(text: str, key: str) -> str:
    m = re.search(r"(?mi)^{0}:[ \t]*(.*)$".format(re.escape(key)), text)
    return m.group(1).strip() if m else ""


def thread_topics(root: Path, thread_entry: Path) -> List[Path]:
    """topic.md топиков, к которым относится нить (по полям, без догадок по тексту)."""
    tdir = paths.tide_dir(Path(root)) / TOPICS_DIRNAME
    if not tdir.is_dir():
        return []
    name = Path(thread_entry).name
    tslug = slug.entry_slug(name)
    own = [(fields.read_field(_stream.passport_path(thread_entry), "topic") or "").strip(),
           (fields.read_field(Path(thread_entry) / "plan.md", "topic") or "").strip()]
    out: List[Path] = []
    for d in sorted(p for p in tdir.iterdir() if p.is_dir()):
        f = d / TOPIC_FILE
        if not f.is_file():
            continue
        if any(v and (v == d.name or slug.entry_slug(v) == slug.entry_slug(d.name))
               for v in own):
            out.append(f)
            continue
        text = _read(f)
        for key in _TOPIC_THREAD_KEYS:
            val = _topic_field(text, key)
            if re.search(r"(?<![\w@-]){0}(?![\w-])".format(re.escape(name)), val) or \
                    (key == "threads" and tslug in re.split(r"[\s,·]+", val)):
                out.append(f)
                break
    return out


def topic_pointer(root: Path, thread_entry: Path) -> str:
    """Одна строка сида: абсолютный путь topic.md нити + статус Linear-родителя
    из поля ``linear:`` — только то, что записано; в Linear не ходим (SOC-105)."""
    found = thread_topics(root, thread_entry)
    if not found:
        return ("- топик: нить не связана ни с одним топиком (проверено: {0}/*/{1} — "
                "threads:/дом:, поле topic: нити)".format(
                    paths.tide_dir(Path(root)) / TOPICS_DIRNAME, TOPIC_FILE))
    parts = []
    for f in found:
        lin = _topic_field(_read(f), "linear")
        if lin:
            key = _ISSUE_RE.search(lin)
            st = _STATUS_RE.search(lin)
            parent = "Linear-родитель {0} — {1}".format(
                key.group(1) if key else lin,
                st.group(1) if st else "статус — в блоке «задачи Linear»")
        else:
            parent = "Linear-родитель не записан (поля linear: нет)"
        parts.append("{0} · {1}".format(f.resolve(), parent))
    return "- топик: " + " · ".join(parts)


# F55 (SOC-101): указатель на топик — не ответ. Холодная сессия файлы не читает, и
# «чего ждёт Гриша» по топику ей неоткуда взять. Под строкой «топик:» печатаем
# дословно «Гриша ждёт» и «дальше» страницы + дату её правки (видно, если отстала).
# Префикс «топик:» — чтобы строки шли за указателем, где бы его ни переставили
# (autohandoff уносит «топик:» в конец карты), и не наследовались из прошлого сида.
TOPIC_MARK = "топик:"
TOPIC_DIGEST_LINES = 8   # строк на один топик, вместе со строкой даты
TOPIC_CLIP = 220
# SOC-105: страница топика «отстала», если её «сверено» старше последнего пульса нити
# больше чем на столько часов. Только метка в сиде и в шов-синке — страницу не правим.
TOPIC_STALE_HOURS = 2
_TOPIC_WAIT_HEAD = re.compile(r"(?i)жд[её]т\s+гриша|гриша\s+жд[её]т")
_TOPIC_WAIT_LINE = re.compile(r"(?i)^\s*[-*]?\s*гриша\s+жд[её]т\s*:\s*(.+)$")
_TOPIC_NEXT_FIELD = re.compile(r"(?i)^(?:что\s+)?дальше\s*:\s*(.*)$")
_TOPIC_NEXT_HEAD = re.compile(r"(?i)^##\s+(?:что\s+)?дальше\b")
_TOPIC_FIELD = re.compile(r"^[^\s:#][^:]{0,30}:(?:\s|$)")


def _clip(text: str, n: int = TOPIC_CLIP) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def topic_waits(text: str) -> List[str]:
    """«Гриша ждёт» страницы: строки «Гриша ждёт: …» где угодно, иначе пункты
    секции, чей заголовок про «ждёт Гриша»."""
    lines = text.splitlines()
    waits = [m.group(1).strip() for m in map(_TOPIC_WAIT_LINE.match, lines) if m]
    if waits:
        return waits
    out: List[str] = []
    inside = False
    for ln in lines:
        head = _HEAD_RE.match(ln)
        if head:
            inside = bool(_TOPIC_WAIT_HEAD.search(head.group(1)))
            continue
        if inside and ln.strip():
            if _TOPIC_FIELD.match(ln):
                break
            out.append(ln.strip().lstrip("-* ").strip())
    return out


def topic_next(text: str) -> str:
    """«дальше» страницы: поле «дальше:» (первая строка) или первая строка секции."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m = _TOPIC_NEXT_FIELD.match(ln)
        if m and m.group(1).strip():
            return m.group(1).strip()
        if _TOPIC_NEXT_HEAD.match(ln):
            for nxt in lines[i + 1:]:
                if _HEAD_RE.match(nxt):
                    break
                if nxt.strip():
                    return nxt.strip().lstrip("-* ").strip()
    return ""


def topic_digest(topic_file: Path) -> List[str]:
    """До :data:`TOPIC_DIGEST_LINES` строк под указателем «топик:» — дословно."""
    from datetime import datetime

    f = Path(topic_file)
    text = _read(f)
    try:
        stamp = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        stamp = "?"
    checked = _topic_field(text, "сверено")
    out = ["  - {0} {1} — страница правлена {2}{3}".format(
        TOPIC_MARK, f.parent.name, stamp, ", сверено: " + checked if checked else "")]
    waits = topic_waits(text)
    nxt = topic_next(text)
    room = TOPIC_DIGEST_LINES - 1 - (1 if nxt else 0)
    if not waits:
        ask = _topic_field(text, "вопрос")
        out.append("  - {0} Гриша ждёт — на странице не записано{1}".format(
            TOPIC_MARK, "; вопрос топика: " + _clip(ask) if ask else ""))
    else:
        shown = waits if len(waits) <= room else waits[:room - 1]
        out += ["  - {0} Гриша ждёт — {1}".format(TOPIC_MARK, _clip(w)) for w in shown]
        if len(shown) < len(waits):
            out.append("  - {0} … ещё {1} — в {2}".format(
                TOPIC_MARK, len(waits) - len(shown), f.resolve()))
    if nxt:
        out.append("  - {0} дальше — {1}".format(TOPIC_MARK, _clip(nxt)))
    return out


# дата, затем (через пробелы, «T», запятую, «в») время: «2026-10-09 19:55», «…09, 19:55»
_TOPIC_CHECKED = re.compile(
    r"(\d{4}-\d{2}-\d{2})(?:[T\s,;]+(?:в\s+)?(\d{1,2}:\d{2}))?")
TOPIC_UNREADABLE_TAIL = "сверено нечитаемо — проверь дату"
TOPIC_STALE_TAIL = "освежи «где мы / в полёте / сверено»"


def topic_checked_at(text: str):
    """Когда страницу сверяли — поле «сверено:» (дата или дата и время) → datetime;
    поля нет — ``None``; поле есть, но не читается — ``False`` (судить нельзя).
    Только дата — конец того дня: метка не должна ошибаться в сторону тревоги."""
    from datetime import datetime

    raw = _topic_field(text, "сверено")
    if not raw:
        return None
    m = _TOPIC_CHECKED.search(raw)
    if not m:
        return False
    try:
        return datetime.fromisoformat("{0}T{1}".format(m.group(1), m.group(2) or "23:59"))
    except ValueError:
        return False


def topic_stale_line(topic_file: Path, last_pulse, *,
                     hours: float = TOPIC_STALE_HOURS) -> str:
    """Метка «страница топика отстала» (SOC-105) или ``""``. *last_pulse* — время
    последнего пульса нити (нет пульса — метки нет). «Сверено» старше пульса больше
    чем на *hours* ч — «отстала на N ч»; поля «сверено» нет вовсе — «не сверялась».
    Ничего не правит: только предупреждает новую сессию."""
    from datetime import timedelta

    f = Path(topic_file)
    checked = topic_checked_at(_read(f))
    if checked is False:
        # не отстала и не свежа — судить нельзя; не молчим, но «отстала» не пишем
        return "  - {0} {1} — страница топика: {2}".format(
            TOPIC_MARK, f.parent.name, TOPIC_UNREADABLE_TAIL)
    if last_pulse is None:
        return ""
    if checked is None:
        what = "отстала — не сверялась (поля «сверено» нет)"
    else:
        gap = last_pulse - checked
        if gap <= timedelta(hours=hours):
            return ""
        what = "отстала на {0} ч".format(max(int(round(gap.total_seconds() / 3600)), 1))
    return "  - {0} {1} — страница топика {2} — {3}".format(
        TOPIC_MARK, f.parent.name, what, TOPIC_STALE_TAIL)


def topic_home_thread(root: Path, topic_file: Path) -> Optional[Path]:
    """Нить-дом топика: открытая нить, названная в полях topic.md (``threads:``,
    ``дом:`` первым, затем ``threads:``, ``нити и работы:`` …, тот же список ключей, что
    у :func:`thread_topics`). Поле называет нить так же, как её ищет
    :func:`thread_topics`: «нить main (31-@main)». Из нескольких названных в поле —
    упомянутая раньше; дома нет — ``None``."""
    text = _read(Path(topic_file))
    threads = _stream.thread_entries(Path(root))
    # дом: — главное поле; threads:/нити — все нити топика, дом среди них не отличить
    for key in ("дом",) + tuple(k for k in _TOPIC_THREAD_KEYS if k != "дом"):
        val = _topic_field(text, key)
        if not val:
            continue
        hits = []
        for t in threads:
            m = re.search(r"(?<![\w@-]){0}(?![\w-])".format(re.escape(t.name)), val)
            if m:
                hits.append((m.start(), t))
        if hits:
            return min(hits, key=lambda h: h[0])[1]
    return None


def thread_last_pulse(root: Path, thread_entry: Path):
    """Последний пульс нити: новейшая сессия (открытая или закрытая, по номеру) →
    позже из последней пульс-строки её ``## context`` и штампа ``offloaded-at``.
    Сессий нет или у новейшей нет ни одного штампа — ``None``."""
    from datetime import datetime

    sessions = (_stream.session_entries(Path(root), Path(thread_entry).name)
                + _stream.session_entries(Path(root), Path(thread_entry).name, closed=True))
    if not sessions:
        return None
    newest = max(sessions, key=lambda p: p.name.lstrip("_"))
    arc = newest / "arc.md"
    found = [_last_pulse_at(section_body(sections(_read(arc)), "context"))]
    raw = (fields.read_field(arc, "offloaded-at") or "").strip()
    try:
        found.append(datetime.fromisoformat(raw) if raw else None)
    except ValueError:
        pass
    found = [t for t in found if t is not None]
    return max(found) if found else None


def topic_block(root: Path, thread_entry: Path,
                context: Optional[Sequence[str]] = None) -> List[str]:
    """Строка «топик:» + дословная выжимка каждой страницы топика (F55) + метка
    «страница топика отстала» (SOC-105). «Сверено» сравниваем с последним пульсом
    нити-дома топика (не своей сессии: топик 02 может собирать сид в другой нити, чем
    его дом 31-@main); дом не найден или без пульса — с пульсом своей сессии (*context*)."""
    lines = [topic_pointer(root, thread_entry)]
    own = _last_pulse_at(context) if context else None
    for f in thread_topics(root, thread_entry):
        home = topic_home_thread(root, f)
        pulse = thread_last_pulse(root, home) if home is not None else None
        if pulse is None or (home is not None and Path(home) == Path(thread_entry)):
            pulse = max([t for t in (pulse, own) if t is not None], default=None)
        digest = topic_digest(f)
        stale = topic_stale_line(f, pulse)
        lines += digest[:1] + ([stale] if stale else []) + digest[1:]
    return lines


# --- входящие поручения из транскрипта передающей сессии (SOC-101 п.10) -----------
#
# continuity-audit · 01 → 02 потеряла поручение, пришедшее через ao send / uds после
# последнего пульса: сид несёт только то, что сессия записала. Транскрипт Claude
# Code пишет каждую доставку — берём их механически.

INCOMING_LOOKBACK_HOURS = 6     # пульса не было — смотрим столько часов назад
INCOMING_MAX = 10               # потолок строк в «в работе прямо сейчас»
INCOMING_CLIP = 200
_TRANSCRIPT_TAIL = 8 * 1024 * 1024
_DELIVERY_RE = re.compile(
    r"<ao-session-delivery\b[^>]*>\s*\[from ([^\]]+)\]\s*(.*?)\s*(?:</ao-session-delivery>|\Z)",
    re.S)
_CROSS_RE = re.compile(
    r"<cross-session-message\b[^>]*?\bfrom-name=\"([^\"]+)\"[^>]*>\s*(.*?)\s*"
    r"(?:</cross-session-message>|\Z)", re.S)
_FULL_STAMP = re.compile(r"^-?\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?)")


def claude_projects_dir() -> Path:
    """Где Claude Code держит транскрипты: ``$CLAUDE_CONFIG_DIR/projects``."""
    base = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    return (Path(base).expanduser() if base else Path.home() / ".claude") / "projects"


def transcript_path(sid: str, projects: Optional[Path] = None) -> Optional[Path]:
    """``<projects>/<папка cwd>/<sid>.jsonl`` — папку не угадываем (сессия могла жить
    в AO-worktree), ищем по имени файла; несколько — самый свежий."""
    sid = (sid or "").strip()
    base = Path(projects) if projects is not None else claude_projects_dir()
    if not sid or not base.is_dir():
        return None
    found = [p for p in base.glob("*/{0}.jsonl".format(sid)) if p.is_file()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def _pulse_stamps(context: Sequence[str]) -> List["tuple"]:
    """``[(время, строка)]`` пульс-строк с разбираемым штампом, в порядке записи."""
    from datetime import datetime

    out = []
    for ln in live_lines(context):
        m = _FULL_STAMP.match(ln.strip())
        if not m:
            continue
        try:
            out.append((datetime.fromisoformat(m.group(1)), ln))
        except ValueError:
            continue
    return out


def _last_pulse_at(context: Sequence[str]):
    stamps = _pulse_stamps(context)
    return stamps[-1][0] if stamps else None


def incoming_since(context: Sequence[str]):
    """С какого момента брать входящие (SOC-101 F29): от пульса ПЕРЕД последним
    «ПЕРЕДАЧА» (её нет — перед последним пульсом). Последним пульсом почти всегда
    становится сама ПЕРЕДАЧА, и окно «с последнего пульса» выходило пустым: доклад,
    пришедший за минуту до неё, терял хеши. Пульс один — None (окно по времени)."""
    stamps = _pulse_stamps(context)
    if not stamps:
        return None
    last = len(stamps) - 1
    for i in reversed(range(len(stamps))):
        if _PASS_RE.match(stamps[i][1].strip()):
            last = i
            break
    return stamps[last - 1][0] if last > 0 else None


# Текст доставки в строке транскрипта — общий с автозакрытием (tide.arc.workers).
from ..arc.workers import user_text as _user_text  # noqa: E402


# Ответ отправителю закрывает входящее, только если он — итог (SOC-101 F29, разборы
# SOC-127): «получен», «принял, пришлю к 15:15», «спасибо, переслала» — не закрывают.
_RESULT_RE = re.compile(
    r"итог\s*:|сдан|сдала|сдаю|готово|вердикт|коммит|pytest|passed|output/|"
    r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)
_PROMISE_RE = re.compile(
    r"пришлю|пришлём|срок|\bк\s+\d{1,2}:\d{2}|позже|возьм|взял|беру|жду|приступ",
    re.IGNORECASE)
_AO_SEND_RE = re.compile(r"\bao\s+send\b[^\n]*?--session[ =]+[\"']?([\w.-]+)")
_UDS_SUFFIX = re.compile(r"^(.+-\d+)-[0-9a-f]{2}$")
_SPEAKER_RE = re.compile(r"^\[?\s*(?:[^\]→]*?—\s*)?([^\[\]()→,:]{3,40}?)\s*(?:\(|→|,)")
_CARRY_WORD = re.compile(r"[0-9a-zа-яё][0-9a-zа-яё._/-]{4,}", re.IGNORECASE)
INCOMING_CARRY_WORDS = 3   # общих слов с пульсом, назвавшим отправителя, — уже записано
INCOMING_CARRY_WORDS_ANON = 6  # столько же без имени отправителя в пульсе
INCOMING_TAKE_SLACK_MIN = 10  # окно открывается чуть раньше первого пульса сессии


def sender_key(name: str) -> str:
    """Имя отправителя без хвоста uds-сокета: ``app-development-1-da`` →
    ``app-development-1``, ``management-16-61 [4a4d71]`` → ``management-16``."""
    name = (name or "").strip().split()[0] if (name or "").strip() else ""
    m = _UDS_SUFFIX.match(name)
    return (m.group(1) if m else name).lower()


def is_closing_reply(text: str) -> bool:
    """Ответ отправителю — итог (сдано, хеш, pytest, путь в output/), а не квитанция
    «получен» / «принял, пришлю к …»."""
    return bool(_RESULT_RE.search(text or "")) and not _PROMISE_RE.search(text or "")


# SOC-101 F60: закрытое входящее не выдаётся за открытое. Ответ отправителю
# закрывает, если он не голая квитанция («получен», «принято, спасибо») и не
# обещание («пришлю к 16:00»): та работа ещё в полёте (F29). Пульс, назвавший id
# отправителя после доставки, — тоже закрытие: сессия его записала.
_ACK_RE = re.compile(r"получ|приня|принял|спасибо|благодар|\bок\b|дошл|увидел",
                     re.IGNORECASE)
_WAIT_RE = re.compile(r"запрос|запрош|жд[уёе]|ждём|просил|\bк\s+\d{1,2}:\d{2}",
                      re.IGNORECASE)
RECEIPT_MAX = 60               # короче — квитанция, а не ответ по делу
_MESSAGE_ARG_RE = re.compile(r"--message[ =]+(.*)$", re.S)
_QUOTED_WS_RE = re.compile(r"\"(?:\\.|[^\"\\])*\s(?:\\.|[^\"\\])*\"|'[^']*\s[^']*'", re.S)

# Письмо без просьбы — не поручение (F60): благодарность, «нить передана»,
# «теперь ведёт …», «доклады — сюда». Есть просьба, находка или вопрос — поручение.
_INFO_RE = re.compile(
    r"спасибо|благодар|нить\s+\S+\s+передана|нить\s+передана|теперь\s+(?:\S+\s+)?"
    r"вед[её]т|ведёт\s+теперь|вместо\s+[\w-]+-\d+|дальше\s+пиши|доклады\b[^.]*сюда|"
    r"адрес[а]?\s+сменил", re.IGNORECASE)
_ASK_RE = re.compile(
    r"прошу|просьб|нужн|надо\b|сделай|сделать|поручени|поручаю|провер|почин|чини|"
    r"пришли|срочн|\bбаг|дефект|находк|возьми|положи|добавь|покажи|ответь|"
    r"срок|задач[уа]\b|\?", re.IGNORECASE)


def reply_message(text: str) -> str:
    """Текст сообщения из ``ao send … --message "…"`` (без кавычек); не команда —
    как есть."""
    m = _MESSAGE_ARG_RE.search(text or "")
    if not m:
        return (text or "").strip()
    return m.group(1).strip().strip("\"'").strip()


def is_receipt(text: str) -> bool:
    """Голая квитанция: короткое «получен / принято / спасибо» без итога."""
    msg = " ".join(reply_message(text).split())
    return len(msg) <= RECEIPT_MAX and bool(_ACK_RE.search(msg)) and \
        not _RESULT_RE.search(msg)


def closes_by_reply(text: str) -> bool:
    """Ответ отправителю закрывает входящее (F60): итог — всегда; квитанция и
    обещание — нет (F29); любой другой ответ по делу — да."""
    if is_closing_reply(text):
        return True
    msg = reply_message(text)
    return not (_PROMISE_RE.search(msg) or is_receipt(msg))


def is_info_letter(body: str) -> bool:
    """Информационное письмо без просьбы (благодарность, «нить передана», «теперь
    ведёт …») — не поручение (F60)."""
    return bool(_INFO_RE.search(body or "")) and not _ASK_RE.search(body or "")


def pulse_names_sender(who: str, at, pulses: Sequence["tuple"],
                       roles: Optional["SenderRoles"] = None) -> bool:
    """Пульс не раньше доставки называет отправителя — сессия входящее записала
    (F60). Имя — AO-id (``tide-stack-24``, без хвоста uds; ``tide-stack-2`` не
    совпадает с ``tide-stack-24``) или роль из реестра сессий *roles* (F82):
    «main · 05», а своя нить — и коротко «· 03»."""
    key = sender_key(who)
    if not key:
        return False
    rx = re.compile(r"(?<![\w-]){0}(?!\d)".format(re.escape(key)), re.IGNORECASE)
    alias = roles.matcher(key) if roles is not None else None
    for p_at, line in pulses:
        if p_at < at.replace(second=0, microsecond=0):
            continue
        # фраза «запросила у X, жду к 15:15» — ожидание от X, а не запись его письма
        if any((rx.search(sent) or (alias is not None and alias(sent)))
               and not _WAIT_RE.search(sent)
               for sent in _SENT_END.split(line)):
            return True
    return False


# SOC-143 F82: пульсы зовут отправителя ролью («main · 05», «· 03»), а доставка
# AO — id («tide-stack-31»). Реестр сессий — паспорта проекта: title
# «проект — нить · NN · <AO-id>» (with_ao_id) даёт AO-id → «нить · NN».
_TITLE_ROLE = re.compile(r"^(?:.*? — )?(\S.*? · \d{2,}) · (\S+)$")


def _project_roles(root: Path) -> Dict[str, str]:
    """AO-id → «нить · NN» по паспортам сессий проекта *root*. Никогда не падает."""
    out: Dict[str, str] = {}
    try:
        passports = sorted(paths.arcs_dir(Path(root)).glob("*/arcs/*/arc.md"))
    except Exception:  # noqa: BLE001
        return out
    for passport in passports:
        try:
            title = (fields.read_field(passport, "title") or "").strip()
        except Exception:  # noqa: BLE001
            continue
        m = _TITLE_ROLE.match(title)
        if m:
            out[m.group(2).lower()] = " ".join(m.group(1).split()).lower()
    return out


class SenderRoles:
    """Роли отправителей для :func:`pulse_names_sender` (F82): AO-id → «нить · NN»
    по реестру сессий своего проекта и проекта отправителя (AO-id
    ``<проект>-NN`` → проект ростера). *own_thread* — нить передающей сессии: её
    сессии пульс зовёт коротко «· 03»; «handoffs · 03» — уже чужая нить."""

    def __init__(self, roles: Dict[str, str], own_thread: str = "",
                 lookup=None) -> None:
        self.roles = {k.lower(): v for k, v in roles.items()}
        self.own_thread = (own_thread or "").lower()
        self._lookup = lookup  # key → {AO-id: роль} проекта отправителя (лениво)

    def role(self, key: str) -> str:
        key = (key or "").lower()
        if key not in self.roles and self._lookup is not None:
            try:
                self.roles.update({k: v for k, v in (self._lookup(key) or {}).items()
                                   if k not in self.roles})
            except Exception:  # noqa: BLE001
                pass
        return self.roles.get(key, "")

    def threads(self) -> set:
        return {r.rsplit(" · ", 1)[0] for r in self.roles.values() if " · " in r}

    def matcher(self, key: str):
        """Функция «предложение зовёт отправителя ролью» или None (роли нет)."""
        role = self.role(key)
        if " · " not in role:
            return None
        name, num = role.rsplit(" · ", 1)
        full = re.compile(r"(?<![\w-]){0}\s*·\s*{1}(?!\d)".format(
            r"\s+".join(re.escape(w) for w in name.split()), re.escape(num)),
            re.IGNORECASE)
        if name != self.own_thread:
            return full.search
        others = self.threads() - {name}
        short = re.compile(r"(?:([\w-]+)\s*)?·\s*{0}(?!\d)".format(re.escape(num)))

        def names(sent: str) -> bool:
            if full.search(sent):
                return True
            # «шаг 2 сдан · 03» — своя сессия 03; «handoffs · 03» — чужая нить
            return any((m.group(1) or "").lower() not in others
                       for m in short.finditer(sent))
        return names


def sender_roles(root: Path, session_entry: Path) -> SenderRoles:
    """:class:`SenderRoles` для передающей сессии: реестр своего проекта + ленивый
    проект отправителя из ростера. Никогда не падает."""
    own_thread = slug.entry_slug(Path(session_entry).parents[1].name) \
        if len(Path(session_entry).parents) > 1 else ""
    cache: Dict[str, Dict[str, str]] = {}

    def lookup(key: str) -> Dict[str, str]:
        project = key.rsplit("-", 1)[0] if "-" in key else ""
        if not project or project in cache:
            return cache.get(project, {})
        cache[project] = {}
        from .. import roster
        for entry in roster.read_roster(paths.control_home()):
            if entry.get("name") == project:
                cache[project] = _project_roles(Path(entry["path"]).expanduser())
        return cache[project]

    return SenderRoles(_project_roles(root), own_thread, lookup)


# SOC-143 F82: почта шов-синка приходит доставкой AO «почта 002 … — tide mail read
# <нить> 002». Разобрана, когда сессия письмо прочла (квитанция ``ack`` в
# inbox/log.jsonl) и пульс после этого говорит «беру / снимаю / разобран».
_MAIL_REF = re.compile(r"tide\s+mail\s+read\s+\S+\s+(\d{3,})|почт[аеуы]\s+(\d{3,})",
                       re.IGNORECASE)
_TAKE_DROP_RE = re.compile(r"(?<!\w)(?:беру|снимаю|снят[оаы]?|сняла?|разобран[аоы]?)"
                           r"(?!\w)", re.IGNORECASE)


def closed_by_mail_read(body: str, at, pulses: Sequence["tuple"],
                        reads: Dict[str, object]) -> bool:
    """Доставка письма нити разобрана (F82): квитанция прочтения *reads* (номер →
    время ack, местное) и пульс «беру / снимаю» не раньше доставки и квитанции."""
    m = _MAIL_REF.search(body or "")
    if not m or not reads:
        return False
    read_at = reads.get(m.group(1) or m.group(2))
    if read_at is None:
        return False
    since = max(at, read_at).replace(second=0, microsecond=0)
    return any(p_at >= since and _TAKE_DROP_RE.search(line) for p_at, line in pulses)


def _carry_words(text: str) -> set:
    return {w.lower().strip("._/-") for w in _CARRY_WORD.findall(text or "")}


def carried_by_pulse(who: str, body: str, at, pulses: Sequence["tuple"]) -> bool:
    """Входящее уже лежит в пульсе (дедуп против пульсов и ПЕРЕДАЧИ): пульс не
    раньше доставки называет отправителя и делит с ней :data:`INCOMING_CARRY_WORDS`
    слов (без имени — :data:`INCOMING_CARRY_WORDS_ANON`). Одно имя не в счёт —
    «запросила разбор у music-15» не несёт сам разбор."""
    names = {n for n in (sender_key(who), speaker_label(body)) if n}
    if any(n.startswith("management") for n in names):
        names.add("менеджер")  # пульсы зовут менеджера ролью, номер меняется
    words = _carry_words(_dialog.clip(body, INCOMING_CLIP * 2)) - names
    for p_at, line in pulses:
        if p_at < at.replace(second=0, microsecond=0):
            continue
        low = line.lower()
        shared = len(words & _carry_words(low))
        if shared >= INCOMING_CARRY_WORDS_ANON or (
                shared >= INCOMING_CARRY_WORDS and any(n in low for n in names)):
            return True
    return False


def speaker_label(body: str) -> str:
    """Подпись отправителя в начале текста — так его зовут пульсы: «handoffs · 03 →
    …» → ``handoffs · 03``, «[tide-stack — board · 03, tide-stack-23 → …]» →
    ``board · 03``. Нет подписи — пусто."""
    m = _SPEAKER_RE.match(body or "")
    return " ".join(m.group(1).split()).lower() if m else ""


def _transcript_rows(transcript: Path):
    import json

    with open(transcript, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - _TRANSCRIPT_TAIL))
        raw = fh.read().decode("utf-8", errors="ignore")
    for ln in raw.splitlines():
        if ("session-delivery" not in ln and "cross-session-message" not in ln
                and '"tool_use"' not in ln):
            continue
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if isinstance(row, dict):
            yield row


def _replies(row: dict) -> List["tuple[str, str]"]:
    """``[(кому, текст)]`` исходящих сообщений в строке ассистента: ``ao send
    --session X`` в Bash и SendMessage."""
    out = []
    if row.get("type") != "assistant":
        return out
    for b in (row.get("message") or {}).get("content") or []:
        if not isinstance(b, dict) or b.get("type") != "tool_use":
            continue
        inp = b.get("input") or {}
        if b.get("name") == "SendMessage":
            out.append((sender_key(str(inp.get("to") or "")),
                        str(inp.get("message") or "")))
        elif b.get("name") == "Bash":
            cmd = str(inp.get("command") or "")
            # адресат — из самой команды, не из текста письма: «ao send --session X»
            # внутри кавычек --message — упоминание, а не ответ X (F60)
            bare = _QUOTED_WS_RE.sub('""', cmd)
            for m in _AO_SEND_RE.finditer(bare):
                out.append((sender_key(m.group(1)), cmd))
    return out


def incoming_orders(transcript: Optional[Path], since, context: Sequence[str] = (),
                    *, roles: Optional[SenderRoles] = None,
                    mail_reads: Optional[Dict[str, object]] = None) -> List[str]:
    """Входящие сообщения других сессий в транскрипте после *since* (местное
    время) → ``- поручено мне — от <X> в ЧЧ:ММ: <первые ~200 знаков>``.

    Берутся доставки AO (``<ao-session-delivery …>[from X]``, и пачкой в одной
    реплике, и внутри ``<pasted_content>``) и межсессионная почта
    (``<cross-session-message … from-name="X">``); уведомления харнесса (F17) —
    нет. Не берутся закрытые (F60): сессия ответила отправителю по делу
    (:func:`closes_by_reply`; квитанция «получен» и обещание не закрывают), пульс
    после доставки назвал отправителя — id или ролью из *roles* (F82,
    :func:`pulse_names_sender`) — или несёт её суть (:func:`carried_by_pulse`),
    письмо нити прочитано и разобрано пульсом «беру / снимаю» (F82,
    :func:`closed_by_mail_read`); и не поручения — письма без просьбы
    (:func:`is_info_letter`). Свежие сверху, не больше
    :data:`INCOMING_MAX`, остальное — счётом и путём к транскрипту."""
    if transcript is None:
        return []
    try:
        rows = list(_transcript_rows(transcript))
    except OSError:
        return []
    found = []
    replies = []  # (время, кому, текст)
    seen = set()  # одна доставка бывает и очередью, и репликой — один раз
    for row in rows:
        at = _dialog._stamp(str(row.get("timestamp") or ""))
        if at is None:
            continue
        if row.get("type") == "assistant":
            replies += [(at, to, text) for to, text in _replies(row)]
            continue
        if row.get("type") not in ("user", "attachment"):
            continue
        if since is not None and at <= since:
            continue
        text = _user_text(row)
        for rx in (_DELIVERY_RE, _CROSS_RE):
            for m in rx.finditer(text):
                who, body = m.group(1).strip(), " ".join(m.group(2).split())
                if not body or _dialog.is_harness_notice(body) or (who, body) in seen:
                    continue
                seen.add((who, body))
                found.append((at, who, body))
    pulses = _pulse_stamps(context)
    found = [(at, who, body) for at, who, body in found
             if not is_info_letter(body)
             and not any(r_at >= at and to == sender_key(who) and closes_by_reply(text)
                         for r_at, to, text in replies)
             and not pulse_names_sender(who, at, pulses, roles)
             and not carried_by_pulse(who, body, at, pulses)
             and not closed_by_mail_read(body, at, pulses, mail_reads or {})]
    found.sort(key=lambda r: r[0], reverse=True)
    out = ["- поручено мне — от {0} в {1}: {2}".format(
        who, at.strftime("%H:%M"), _dialog.clip(body, INCOMING_CLIP))
        for at, who, body in found[:INCOMING_MAX]]
    if len(found) > INCOMING_MAX:
        out.append("- (ещё поручений: {0} — транскрипт {1})".format(
            len(found) - INCOMING_MAX, transcript))
    elif any(len(body) > INCOMING_CLIP for _, _, body in found[:INCOMING_MAX]):
        out.append("- (полные тексты входящих — транскрипт {0})".format(transcript))
    return out


def session_taken_at(context: Sequence[str]):
    """Когда сессия приняла нить: первый пульс её паспорта (приём пишет его сам —
    «нить принята»). Пульсов нет — None."""
    stamps = _pulse_stamps(context)
    return stamps[0][0] if stamps else None


# --- фоновые сабагенты передающей сессии (SOC-101 F45) -------------------------
#
# «В работе» не видел своих воркеров: ``Agent`` в фоне (или возобновлённый
# SendMessage'ем) без ``<task-notification>`` о конце — жив в старой сессии, и
# новая запускала дубль на тот же файл (пара 12, acceptance-05 §9 п.2).

_NOTIF_RE = re.compile(r"<task-notification>.*?<tool-use-id>([\w-]+)</tool-use-id>"
                       r"(?:.*?<status>(\w+)</status>)?", re.S)
_LAUNCH_RE = re.compile(r"Async agent launched.*?agentId:\s*([\w-]+)", re.S)
_RESUME_RE = re.compile(r"\"resumedAgentId\"\s*:\s*\"([\w-]+)\"")


def _result_text(block: dict) -> str:
    c = block.get("content")
    if isinstance(c, list):
        return "\n".join(str(x.get("text") or "") for x in c if isinstance(x, dict))
    return str(c or "")


def _git_mark(cwd: str, at) -> "tuple[str, str]":
    """``(корень репо, короткий sha последнего коммита до *at*)`` для *cwd*; не git —
    пусто."""
    import subprocess

    if not cwd or not Path(cwd).is_dir():
        return "", ""
    try:
        top = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        sha = subprocess.run(["git", "-C", cwd, "log", "--all", "-1", "--format=%h",
                              "--before={0}".format(at.isoformat())],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "", ""
    return top, sha


def _interim_agents(transcript: Path) -> set:
    """agentId сабагентов, чей последний ``<task-notification>`` промежуточный
    («… still running … may be interim») или возобновлённых письмом после итога —
    ещё в полёте (SOC-143). Признак — один на tide: :func:`tide.arc.workers.
    interim_workers` (автозакрытие старой судит по нему же)."""
    try:
        from ..arc.workers import interim_workers
        return {w.split(" ", 1)[0] for w in interim_workers(Path(transcript))}
    except Exception:  # noqa: BLE001 — сид важнее
        return set()


def worker_runs(transcript: Optional[Path], interim: bool = True):
    """Фоновые сабагенты транскрипта на момент сборки (F45, SOC-143 F83) →
    ``(живые, сдавшие)``: живые — ``[(agentId, описание, время запуска, cwd)]``,
    сдавшие — ``[(agentId, описание, время финального итога)]``.

    Запуск — ``Agent`` с ответом «Async agent launched … agentId: X»; возобновление —
    SendMessage с ``resumedAgentId``; конец — ``<task-notification>`` с тем же
    tool-use-id. Промежуточное уведомление (:func:`_interim_agents`) — не конец
    (*interim* False — любое уведомление конец, как до SOC-143)."""
    import json

    if transcript is None:
        return [], []
    try:
        raw = Path(transcript).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return [], []
    calls = {}    # tool_use_id → (описание, agentId из входа SendMessage, время, cwd)
    desc_of = {}  # agentId → описание
    runs = {}     # tool_use_id → (agentId, описание, время, cwd)
    done = {}     # tool_use_id → время уведомления о конце
    for ln in raw.splitlines():
        if ('"tool_use"' not in ln and '"tool_result"' not in ln
                and "task-notification" not in ln):
            continue
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        at = _dialog._stamp(str(row.get("timestamp") or ""))
        if row.get("type") == "assistant":
            for b in (row.get("message") or {}).get("content") or []:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                inp = b.get("input") or {}
                if b.get("name") in ("Agent", "Task"):
                    calls[b.get("id")] = (str(inp.get("description") or "без описания"),
                                          "", at, str(row.get("cwd") or ""))
                elif b.get("name") == "SendMessage":
                    calls[b.get("id")] = ("", str(inp.get("to") or ""), at,
                                          str(row.get("cwd") or ""))
            continue
        if row.get("type") not in ("user", "attachment"):
            continue
        content = (row.get("message") or {}).get("content")
        blocks = content if isinstance(content, list) else []
        for b in blocks:
            if not isinstance(b, dict) or b.get("type") != "tool_result":
                continue
            call = calls.get(b.get("tool_use_id"))
            if call is None:
                continue
            text = _result_text(b)
            m = _LAUNCH_RE.search(text) or _RESUME_RE.search(text)
            if not m:
                continue
            agent = m.group(1)
            desc = call[0] or desc_of.get(agent, "")
            if call[0]:
                desc_of[agent] = call[0]
            runs[b.get("tool_use_id")] = (agent, desc, call[2] or at, call[3])
        for m in _NOTIF_RE.finditer(_user_text(row) or json.dumps(content, ensure_ascii=False)):
            done[m.group(1)] = at
    still = _interim_agents(transcript) if runs and interim else set()
    live, ended = {}, {}
    for tid, run in runs.items():
        if tid not in done or run[0] in still:
            live[run[0]] = run  # один воркер — одна строка (последний запуск)
        else:
            prev = ended.get(run[0])
            if prev is None or (done[tid] and prev[2] and done[tid] > prev[2]):
                ended[run[0]] = (run[0], run[1], done[tid])
    finished = [r for a, r in ended.items() if a not in live]
    finished.sort(key=lambda r: r[2] or 0, reverse=True)
    return sorted(live.values(), key=lambda r: r[2] or 0, reverse=True), finished


def background_workers(transcript: Optional[Path], git=_git_mark) -> List[str]:
    """Незавершённые фоновые сабагенты в транскрипте (F45) — строками «в работе»:
    «воркер «описание» — жив в старой сессии, итог перешлёт старая; адрес
    SendMessage/task-id; коммиты — git log <репо> после <sha>» (:func:`worker_runs`).

    Конец — любое уведомление: автозакрытие и приём (hooks/autoclose, reception)
    судят о промежуточном итоге сами (``interim_workers``). Сид — через
    :func:`session_worker_runs`, где промежуточный итог = ещё в полёте (F83)."""
    return worker_lines(worker_runs(transcript, interim=False)[0], git=git)


def worker_lines(live, git=_git_mark) -> List[str]:
    out = []
    for agent, desc, at, cwd in live:
        top, sha = git(cwd, at) if at else ("", "")
        tail = "; коммиты — git log {0} после {1}".format(top, sha) if top and sha else ""
        out.append("- воркер «{0}» — жив в старой сессии (с {1}), итог перешлёт старая; "
                   "адрес: SendMessage {2} из старой, task-id {2}{3}".format(
                       desc or "без описания", at.strftime("%H:%M") if at else "?",
                       agent, tail))
    return out


def session_workers(session_entry: Path) -> List[str]:
    """Фоновые воркеры передающей сессии (:func:`background_workers`). Не падает."""
    return worker_lines(session_worker_runs(session_entry)[0])


def session_worker_runs(session_entry: Path):
    """:func:`worker_runs` транскрипта передающей сессии. Не падает."""
    try:
        sid = (fields.read_field(Path(session_entry) / "arc.md",
                                 offload.CLAUDE_SESSION_FIELD) or "").strip()
        return worker_runs(transcript_path(sid))
    except Exception:  # noqa: BLE001 — сид важнее
        return [], []


# --- SOC-143 F83: «в полёте» — по транскрипту на момент сборки ------------------
#
# Метка «В полёте: сабагент — тема …» и слой «где мы» пишутся ДО итога сабагента
# (хук велит ПЕРЕДАЧУ, пока он работает); итог пришёл за секунды до сборки — сид
# держал сдавшего живым. Финальный итог в транскрипте → не в полёте.

_WORKER_WORD = re.compile(r"саб-?агент|суб-?агент|воркер|subagent|\bагент", re.IGNORECASE)
_WORKER_STOP = {"сабагент", "субагент", "воркер", "агент", "subagent", "agent", "итог",
                "принять", "диску"}
WORKER_MATCH_MIN = 2       # общих значимых слов строки и описания воркера — тот же
_PULSE_AT = re.compile(r"\(пульс (\d{2}:\d{2})\)")


_CLAUSE_SPLIT = re.compile(r"\s·\s|;\s+")


def _worker_words(text: str) -> set:
    return {w for w in norm_line(text).split()
            if (len(w) >= 4 and w not in _WORKER_STOP) or (w.isdigit() and len(w) >= 3)}


def _worker_clauses(line: str) -> List[str]:
    """Куски строки (по « · » и «; »), где назван сабагент/воркер."""
    return [c for c in _CLAUSE_SPLIT.split(line or "") if _WORKER_WORD.search(c)]


def _match_worker(line: str, runs) -> Optional[tuple]:
    """Воркер из *runs* (``(agentId, описание, …)``), о котором строка: agentId в ней
    или :data:`WORKER_MATCH_MIN` общих значимых слов с описанием."""
    words = _worker_words(line)
    best, score = None, 0
    for run in runs:
        if run[0] and run[0] in line:
            return run
        n = len(words & _worker_words(run[1]))
        if n >= WORKER_MATCH_MIN and n > score:
            best, score = run, n
    return best


def _hhmm(at) -> str:
    return at.strftime("%H:%M:%S") if at else "?"


def settle_inflight(lines: Sequence[str], live, finished):
    """Пункты «в полёте» из меток пульсов, сверенные с транскриптом (F83) →
    ``(оставшиеся, снятые)``. Пункт про сабагента снимается, если его воркер сдал
    финальный итог (описание совпало), или — пункт без узнаваемого воркера — живых
    нет, а последний итог пришёл не раньше пульса с меткой. Снятые — строками для
    «где мы» (с временем итога): молча не тонет."""
    kept, gone = [], []
    for ln in lines:
        if not _WORKER_WORD.search(ln) or not finished:
            kept.append(ln)
            continue
        if _match_worker(ln, live):
            kept.append(ln)
            continue
        run = _match_worker(ln, finished)
        if run is None and not live:
            m = _PULSE_AT.search(ln)
            last = finished[0]
            if not m or (last[2] and last[2].strftime("%H:%M") >= m.group(1)):
                run = last
        if run is None:
            kept.append(ln)
            continue
        gone.append("- сабагент «{0}» сдал итог в {1} (транскрипт на момент сборки) — "
                    "снят из «{2}»: {3}".format(run[1] or run[0], _hhmm(run[2]), INFLIGHT,
                                                ln.strip().lstrip("- ")))
    return kept, gone


def mark_finished_workers(lines: Sequence[str], live, finished) -> List[str]:
    """Строки («где мы»), где сабагент назван идущим, а его воркер уже сдал
    финальный итог (F83), — с пометкой «сдал итог в ЧЧ:ММ:СС»."""
    out = []
    for ln in lines:
        run = None
        for clause in _worker_clauses(ln) if finished else ():
            if not _match_worker(clause, live):
                run = _match_worker(clause, finished)
            if run is not None:
                break
        if run is not None and "сдал итог" not in ln:
            ln = "{0} (сабагент «{1}» сдал итог в {2} — по транскрипту на момент " \
                 "сборки, не в полёте)".format(ln.rstrip(), run[1] or run[0], _hhmm(run[2]))
        out.append(ln)
    return out


def session_incoming(session_entry: Path, context: Sequence[str], now=None,
                     root: Optional[Path] = None) -> List[str]:
    """Входящие поручения и материалы передающей сессии ЗА ВСЮ ЕЁ ЖИЗНЬ — с приёма
    (:func:`session_taken_at` минус :data:`INCOMING_TAKE_SLACK_MIN`), не закрытые
    итогом и не записанные пульсом (SOC-101 F29: окно «с пульса перед ПЕРЕДАЧЕЙ»
    теряло доставки, пришедшие раньше и не записанные). Пульсов нет — за
    :data:`INCOMING_LOOKBACK_HOURS` до «сейчас». Никогда не падает."""
    from datetime import datetime, timedelta

    try:
        sid = (fields.read_field(Path(session_entry) / "arc.md",
                                 offload.CLAUDE_SESSION_FIELD) or "").strip()
        taken = session_taken_at(context)
        since = (taken - timedelta(minutes=INCOMING_TAKE_SLACK_MIN) if taken else
                 (now or datetime.now()) - timedelta(hours=INCOMING_LOOKBACK_HOURS))
        title = (fields.read_field(Path(session_entry) / "arc.md", "title") or "").strip()
        m = _TITLE_ROLE.match(title)
        me = m.group(2).lower() if m else ""
        root = root or Path(session_entry).parents[4]
        return incoming_orders(transcript_path(sid), since, context,
                               roles=sender_roles(root, session_entry),
                               mail_reads=mail_reads(Path(session_entry).parents[1], me))
    except Exception:  # noqa: BLE001 — сид важнее
        return []


MAIL_MAX = 8                    # потолок строк почты в «в работе прямо сейчас»
MAIL_FIRST = 120                # первая строка письма — не длиннее
# Формат почты нити (SOC-143, tide.mail): <нить>/inbox/NNN-от-<кто>.md + журнал
# inbox/log.jsonl (sent/ack). tide.mail — край (edge), launcher его не импортирует
# (tests/test_layers.py) — здесь только чтение того же формата.
_MAIL_LETTER = re.compile(r"^(\d{3,})-от-(.+)\.md$")


def _mail_events(thread_entry: Path) -> List[Dict[str, object]]:
    """Журнал почты нити (inbox/log.jsonl) — события по порядку; нет — []."""
    import json

    log = Path(thread_entry) / "inbox" / "log.jsonl"
    evs: List[Dict[str, object]] = []
    if log.is_file():
        for ln in log.read_text(encoding="utf-8").splitlines():
            try:
                ev = json.loads(ln)
            except ValueError:
                continue
            if isinstance(ev, dict):
                evs.append(ev)
    return evs


def mail_reads(thread_entry: Path, me: str = "") -> Dict[str, object]:
    """Номер письма → первая квитанция прочтения (``ack``) сессией *me* (AO-id), в
    местном времени (F82). *me* неизвестен — квитанция любого. Никогда не падает."""
    out: Dict[str, object] = {}
    try:
        evs = _mail_events(Path(thread_entry))
    except Exception:  # noqa: BLE001
        return out
    me = (me or "").lower()
    for ev in evs:
        if ev.get("ev") != "ack" or (me and sender_key(str(ev.get("by") or "")) != me):
            continue
        at = _dialog._stamp(str(ev.get("at") or ""))
        num = str(ev.get("num") or "")
        if at is not None and num and (num not in out or at < out[num]):
            out[num] = at
    return out


def _mail_unread(thread_entry: Path) -> List[Dict[str, str]]:
    """Непрочитанные письма нити: ``[{num, sender, first}]`` по номеру."""
    box = Path(thread_entry) / "inbox"
    if not box.is_dir():
        return []
    evs = _mail_events(Path(thread_entry))
    acked = {str(e.get("num")) for e in evs if e.get("ev") == "ack"}
    sent = {str(e.get("num")): e for e in evs if e.get("ev") == "sent"}
    out = []
    for f in sorted(box.iterdir()):
        m = _MAIL_LETTER.match(f.name)
        if not m or not f.is_file() or m.group(1) in acked:
            continue
        ev = sent.get(m.group(1), {})
        first = str(ev.get("first") or "")
        if not first:
            body = _read(f).split("\n---\n", 1)[-1]
            first = next((x.strip() for x in body.splitlines() if x.strip()), "")
        out.append({"num": m.group(1), "sender": str(ev.get("from") or m.group(2)),
                    "first": first})
    return out


def _mail_address(thread_entry: Path) -> str:
    """``проект/нить`` для команды чтения — как :func:`tide.mail.address_of`."""
    from .. import roster, slug as _slug

    name = _slug.entry_slug(Path(thread_entry).name)
    proj_root = Path(thread_entry).parent.parent.parent
    try:
        for entry in roster.read_roster(paths.control_home()):
            if Path(entry["path"]).expanduser().resolve() == proj_root.resolve():
                return "{0}/{1}".format(entry["name"], name)
    except Exception:  # noqa: BLE001 — нет ростера: адрес короткий
        pass
    return name


def thread_mail(thread_entry: Path, limit: int = MAIL_MAX) -> List[str]:
    """Непрочитанные письма нити (SOC-143) — строками «в работе прямо сейчас»:
    номер, отправитель, первая строка и команда ``tide mail read <проект>/<нить>
    NNN``. Писем нет — []. Никогда не падает."""
    try:
        letters = _mail_unread(Path(thread_entry))
        if not letters:
            return []
        addr = _mail_address(Path(thread_entry))
    except Exception:  # noqa: BLE001 — сид важнее почты
        return []
    out = ["- почта: непрочитанное письмо {0} от {1}: {2} — tide mail read {3} {0}".format(
        r["num"], r["sender"], clip_words(r["first"] or "(без первой строки)", MAIL_FIRST),
        addr) for r in letters[:limit]]
    if len(letters) > limit:
        out.append("- почта: ещё непрочитанных {0} — tide mail ls {1}".format(
            len(letters) - limit, addr))
    return out


def block_unanswered(thread_entry: Path, n: int = DIALOG_TAIL) -> List[str]:
    """Неотвеченные реплики Гриши: хвост диалога нити; журнал есть, а реплик в нём
    нет — так и сказано, со счётом отброшенных уведомлений харнесса (SOC-101 F33:
    «журнала диалога нет» при живом dialog.md с одной харнесс-строкой было неправдой)."""
    lines = _dialog.tail(thread_entry, n)
    if lines:
        return lines
    path = _dialog.dialog_path(Path(thread_entry))
    log = _dialog.log_path(Path(thread_entry))
    if not path.is_file() and not (log is not None and log.is_file()):
        return []  # журналов нет — правило пустоты блока (контракт)
    dropped = 0
    for ln in _read(path).splitlines():
        m = _dialog._LINE_RE.match(ln)
        if m and m.group(2).strip() == _dialog.HUMAN and _dialog.is_harness_notice(m.group(4)):
            dropped += 1
    return ["{0} реплик Гриши нет (журнал есть; уведомлений харнесса отброшено: {1})".format(
        EMPTY, dropped)]


# Адрес менеджера в строке слоя; «через management-11» / «от management-11» — это
# происхождение слов (кто донёс), не адрес — не устаревает.
_MANAGER_ID = re.compile(r"\bmanagement-\d+\b")
# Происхождение (F57): «через management-9», «через менеджера management-9»,
# «окно менеджера management-9», «от management-16» — кто донёс слова, не адрес.
_PROVENANCE_HEAD = re.compile(
    r"(?:\b(?:через|от|из)|\bокн[оаеу]?)(?:\s+(?:менеджера|сессии|окна|окно|main))*\s*$",
    re.IGNORECASE)
STALE_MARK = "(устарело: менеджер сейчас"


def manager_addresses(line: str) -> set:
    """AO-id менеджеров, которые в *line* стоят адресом (не происхождением)."""
    return {m.group(0) for m in _MANAGER_ID.finditer(line)
            if not _PROVENANCE_HEAD.search(line[max(0, m.start() - 40):m.start()])}


def current_manager_id() -> str:
    """AO-id менеджера сейчас (``tide whois manager``); не нашёлся — пусто."""
    from .. import whois

    try:
        return str((whois.manager() or {}).get("ao_session") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def mark_stale_manager(lines: Sequence[str], current: Optional[str] = None) -> List[str]:
    """Строки слоя нити с адресом менеджера, который уже не менеджер, — с пометкой
    «(устарело: менеджер сейчас X)». Слой правят руками, менеджер меняется
    хендоффами: «где мы» нити main звала management-11 при management-16."""
    cur = current_manager_id() if current is None else current
    if not cur:
        return list(lines)
    out = []
    for ln in lines:
        ids = manager_addresses(ln)
        if ids and cur not in ids and STALE_MARK not in ln:
            ln = "{0} (устарело: менеджер сейчас {1})".format(ln.rstrip(), cur)
        out.append(ln)
    return out


def manager_line() -> str:
    """Текущий адрес менеджера одной строкой — резолвером :mod:`tide.whois`
    (``~/.tide/manager.json``, без него — цепочка хендоффов нити менеджера), а не
    константой «management-NN»: менеджер меняется хендоффами (SOC-101 F19/F20)."""
    from .. import whois

    addr = whois.manager_address()
    if addr:
        return "- менеджер сейчас: {0} — адрес на момент сборки (tide whois manager)".format(addr)
    return ("- менеджер: адрес не нашёлся (tide whois manager: нет ~/.tide/manager.json и "
            "цепочки его нити) — спроси tide whois manager перед отправкой")


def _project_name(root: Path) -> str:
    root = Path(root)
    return root.parent.name if root.name == ".tide" else root.name


def _thread_word(thread_entry: Path) -> str:
    """«30-@handoffs» → «handoffs» — так нить зовут в «Взял: <проект> — <нить> · NN»."""
    return re.sub(r"^\d+-@?", "", Path(thread_entry).name)


_OPEN_LINEAR = re.compile(r"— (?:Backlog|Todo|In Progress|In Review|Triage)\b")


def _awaiting_with_inherited(current: List[str], inherited: Sequence[str],
                             context: Sequence[str]) -> List[str]:
    """F77: «ждёт Гришу» этой сессии + пункты входного сида (:func:`inherit_awaiting`)."""
    return _merge_lines(current, mark_stale_manager(
        inherit_awaiting(inherited, current, context)))


def draft_bodies(root: Path, session_entry: Path, *, pulse_clip: int = 0
                 ) -> Dict[str, List[str]]:
    """Тела блоков контракта для *session_entry* с диска — общее у черновика и
    автохендоффа. «Сырой журнал» и «задачи Linear» считает :func:`seal`."""
    from . import autohandoff as _auto  # lazy: autohandoff зовёт этот модуль

    session_entry = Path(session_entry)
    thread_entry = session_entry.parents[1]  # …/NN-@thread/arcs/NN-session
    passport = sections(_read(session_entry / "arc.md"))
    seed_in_path = session_entry / "input" / INPUT_SEED_NAME
    seed_in = sections(_read(seed_in_path))
    history = _sc.SeedHistory(thread_entry)  # SOC-181: возраст строки — первый сид с ней
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
    context = section_body(passport, "context")
    labels = labeled_pulses(context)
    summary = prose_lines(section_body(passport, "summary"))
    live_runs, done_runs = session_worker_runs(session_entry)
    # F83: слой «где мы» — снимок до итога сабагента; сдавший помечен по транскрипту
    where = mark_finished_workers(mark_stale_manager(layer(lays, "where")),
                                  live_runs, done_runs) + block_cursor(
        section_body(passport, "cursor"), section_body(passport, "context"),
        works=works, clip=pulse_clip)
    if summary:
        where.append("- итог сессии: {0}".format(" ".join(ln.strip() for ln in summary)))
    label_inflight, settled = settle_inflight(labels.get("inflight", []),
                                              live_runs, done_runs)
    where += settled
    inflight = _merge_lines(label_inflight, worker_lines(live_runs),
                            session_incoming(session_entry,
                                             section_body(passport, "context"),
                                             root=root),
                            _auto.open_items(root, thread_entry),
                            thread_mail(thread_entry))
    goal_file = _stream.passport_path(thread_entry)
    # SOC-181: опыт — один урок одной строкой, свежее за сутки + самые частые старые,
    # без граблей из общего файла; остальное — ссылкой на слой нити
    experience = _sc.fold_experience(
        [("pulse", labels.get("experience", [])), ("layer", layer(lays, "experience")),
         ("seed", contract.find_body(seed_in, "experience"))],
        history=history, gotchas=_sc.gotcha_lines(),
        full="слой нити {0}, ### опыт; прошлый сид {1}".format(goal_file, seed_in_path))
    # SOC-181: «ждёт Гришу» — отвеченное (вопрос в журнале решений Гриши) вон из сида
    awaiting_all = _awaiting_with_inherited(
        _merge_lines(
            labels.get("awaiting", []),
            mark_stale_manager(layer_named(lays, "ждёт гришу", "ждет гришу")),
            block_awaiting(_auto.open_questions(root, thread_entry), works),
            next_for_grisha(block_next(section_body(passport, "next")))),
        contract.find_body(seed_in, "awaiting"), context)
    answered_nums = _sc.journal_question_nums() | set(
        _TG_NUM.findall(_read(Path(thread_entry) / "decisions.md")))
    awaiting = [ln for ln in awaiting_all if not _sc.is_answered(ln, answered_nums)]
    awaiting_dropped = [ln for ln in awaiting_all if ln not in awaiting]
    # F51 + SOC-181: из прошлого сида — только открытые задачи; закрытые, чужие и не
    # найденные прошлых сидов дальше не едут
    prev_linear = [ln for ln in contract.find_body(seed_in, "linear")
                   if ln.startswith("- ") and _OPEN_LINEAR.search(ln)]
    return {
        # F57: «главное» — тоже слой нити (plan.md): «Кому сдавать: … management-12»
        MAIN: (mark_stale_manager(block_main(
                   thread_entry, contract.find_body(seed_in, "main"), pred))
               or [contract.empty_line(contract.block("main"))]) + [manager_line()],
        EXPECTATION: block_expectation(
            mark_stale_manager(layer(lays, "expectation")), _stream.passport_path(thread_entry),
            labels.get("expectation", []),
            fold_lines(session_grisha_words(session_entry) + grisha_quotes(
                context, section_body(
                    sections(_read(_stream.passport_path(thread_entry))), "context")))[:5]),
        WHERE: where,
        # F74: хвост про пустой/старый слой, закрытый к сборке (тем же швом), — снят
        TAILS: merge_tails(close_layer_tails(block_tails(context, labels.get(TAILS_KEY, [])),
                                             thread_entry, context), inflight),
        # Источники по порядку (SOC-101 F22): метки пульсов передающей сессии →
        # слои нити → входной сид (опыт наследуется) → вычисляемое с доски.
        EXPERIENCE: block_experience(nums, experience, _candidate_gists(root, nums),
                                     folded=True),
        # F63: решения копятся, как опыт (F22): метки пульсов → решения нити →
        # решения входного сида (снятые — с причиной)
        DECISIONS: fold_decisions(_merge_lines(
            labels.get("decisions", []), decisions,
            inherit_decisions(contract.find_body(seed_in, "decisions"),
                              labels.get("decisions", []) + decisions,
                              _thread_decisions(root, thread_entry)))),
        AWAITING: awaiting,
        NEXT: block_next(section_body(passport, "next")),
        INFLIGHT: inflight,
        # SOC-181: только реплики Гриши без ответа; отвеченное — строкой со ссылкой
        UNANSWERED: _sc.only_unanswered(block_unanswered(thread_entry),
                                        dialog=str(_dialog.dialog_path(thread_entry)),
                                        human=_dialog.HUMAN),
        ENTRY_MAP: block_entry_map(contract.find_body(seed_in, "entry_map"),
                                   labels.get("entry_map", []),
                                   collected_map(root, session_entry, thread_entry,
                                                 labels.get("experience", [])
                                                 + layer(lays, "experience")
                                                 + contract.find_body(seed_in, "experience")))
        + topic_block(root, thread_entry, context),
        # SOC-181 п.6: окружение из сида в сид не переносится вовсе — только живые
        # источники (ao session ls, git rev-parse, версия прода) сверяет сама сессия
        ENVIRONMENT: [],
        THREAD_SELF: [_project_name(root), _thread_word(thread_entry)],
        CUSTOMER_TRACKER: ["1"] if customer_tracker(root) else [],
        LINEAR_SOURCES: [("цели нити", _goal_of(thread_entry)),
                         ("прошлого сида", "\n".join(prev_linear))],
        SEED_IN_PATH: [str(seed_in_path)],
        AWAITING_DROPPED: awaiting_dropped,
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
    def bodies_at(clip: int) -> Dict[str, List[str]]:
        bodies = draft_bodies(root, session_entry, pulse_clip=clip)
        for name, hint in DRAFT_HINTS.items():
            bodies[name] = list(bodies.get(name) or []) + [hint]
        return bodies

    context = section_body(sections(_read(session_entry / "arc.md")), "context")
    return seal_budgeted(bodies_at, context=context,
                         header=draft_header(session_entry.name, thread_entry.name),
                         source="{0} (## context)".format(session_entry / "arc.md"),
                         journal_off=journal_pointer(root, session_entry))


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

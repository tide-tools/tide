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


def block_expectation(layer_lines: Sequence[str], goal_file: Optional[Path] = None,
                      pulse_lines: Sequence[str] = ()) -> List[str]:
    """Ожидание Гриши: слой нити ДОСЛОВНО и с датой (SOC-101, пакет Г п.5).

    Раньше блок склеивал ``goal:`` нити и ``goal:`` сессии — это цель, а не слово
    Гриши, и она устаревала (адресат management-7 давно закрыт). Теперь: строки
    слоя «### ожидание Гриши» как есть + строка-источник с путём и датой правки
    goal-файла. Слой пуст — метка «Ожидание Гриши:» самого свежего пульса; нет и
    её — пусто (блок скажет, какие источники проверены).
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
    return list(pulse_lines)


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
            ln = ln[: clip - 1].rstrip() + "…"
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
_LINEAR_QUERY = ("query($team:String!,$nums:[Float!]){issues(filter:{team:{key:{eq:$team}},"
                 "number:{in:$nums}},first:250){nodes{identifier state{name type}}}}")


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
            out[str(node.get("identifier"))] = (str(st.get("name") or ""),
                                                str(st.get("type") or ""))
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
        val = _States(fetch(sorted(want)))
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
    """Ответ Linear (dict) с пометкой, какой функцией получен (для кэша)."""


def block_links(text: str, prefixes: Sequence[str] = ("SOC",),
                status_text: str = "", live=None) -> List[str]:
    """Каждая задача Linear, упомянутая в сиде, — один раз со ссылкой.

    Голый «SOC-62» новой сессии ничего не говорит, пока она не сходит в Linear
    искать, где это. Адрес — ``$TIDE_LINEAR_ISSUE_URL`` (шаблон с ``{key}``).
    «SOC-92/96/77» — три задачи.

    Статус (F30): *live* — ответ :func:`linear_states`. Словарь — статус из
    Linear; закрытые (Done/Canceled) уходят вниз отдельной группой «закрыты в
    Linear». :class:`LinearUnavailable` — статус из *status_text*/*text*
    (:func:`issue_statuses`) с пометкой «статус не сверен». None — как до F30:
    только текст, без пометок."""
    tmpl = (os.environ.get(LINEAR_URL_ENV) or "").strip() or LINEAR_URL_DEFAULT
    text = expand_issue_runs(text)
    seen: List[str] = []
    for key in _ISSUE_RE.findall(text or ""):
        if key.split("-", 1)[0] in prefixes and key not in seen:
            seen.append(key)
    seen.sort(key=lambda k: int(k.rsplit("-", 1)[1]))
    if isinstance(live, dict):
        opened, closed = [], []
        for k in seen:
            name, kind = live.get(k, ("", ""))
            if kind in CLOSED_TYPES:
                closed.append("  - {0} — {1} — закрыта ({2})".format(k, tmpl.format(key=k), name))
            else:
                opened.append("- {0} — {1} — {2}".format(
                    k, tmpl.format(key=k), name or "в Linear не найдена"))
        if not seen:
            return []
        out = ["- (статусы — из Linear на момент сборки)"] + opened
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


def collected_map(root: Path, session_entry: Path, thread_entry: Path) -> List[str]:
    """Карта-черновик с диска (C11): «главное» plan.md нити, topic.md нити (или
    goal-файл со слоями), паспорт передающей сессии — абсолютные пути и «зачем»."""
    out: List[str] = []
    plan = Path(thread_entry) / "plan.md"
    if plan.is_file():
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
    return out


_PULSE_STAMP = re.compile(r"^-?\s*\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})(?::\d{2})?\s*[—-]?\s*")


_GIST_NAME_DOT = re.compile(r"\s·\s(?=(?:\d{2}|NN)\b)")
_GIST_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZА-ЯЁ0-9«\"(])|\s·\s")


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
    for i, ln in full:  # не влезает — ВСЕ строки сжатыми, чтобы дошло больше шагов
        if i in keep:
            out.append(ln)  # ПЕРЕДАЧА — целиком и вне бюджета
            continue
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
        # номера — и из пульсов, обрезанных журналом (acceptance-02 §9 п.9)
        hay = render_draft(out, header=header) + "\n" + "\n".join(context)
        out[LINEAR] = block_links(hay, status_text="\n".join(reversed(list(context))),
                                  live=linear_states(linear_keys(hay)))
        text = render_draft(out, header=header)
        over = tail + sum(len(ln) + 1 for ln in out[JOURNAL]
                          if not _PASS_RE.match(ln.strip())) - share * len(text)
        if over <= 0 or budget == 0:
            break
        budget = max(0, budget - int(over) - 1)
    return text


def seal_budgeted(bodies_at, *, context: Sequence[str], header: str = "",
                  source: str = "") -> str:
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
        text = seal(bodies, context=context, header=header, clip=clip, source=source)
        tail = raw_pulse_chars(context, clip=clip)
        if tail <= JOURNAL_SHARE * len(text) / 2:
            break
    return text


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
        return ["- " + ln.lstrip("-* ").strip() for ln in own]
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


def _user_text(row: dict) -> str:
    """Текст доставки в строке транскрипта: реплика пользователя (``type: user``)
    или сообщение, пришедшее очередью, пока сессия работала (SOC-101 F28:
    ``type: attachment`` с ``attachment.type: queued_command`` — ``prompt``)."""
    if row.get("type") == "attachment":
        att = row.get("attachment") or {}
        if not isinstance(att, dict) or att.get("type") != "queued_command":
            return ""
        content = att.get("prompt")
    elif row.get("type") == "user":
        content = (row.get("message") or {}).get("content")
    else:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def incoming_orders(transcript: Optional[Path], since) -> List[str]:
    """Входящие сообщения других сессий в транскрипте после *since* (местное
    время) → ``- поручено мне — от <X> в ЧЧ:ММ: <первые ~200 знаков>``.

    Берутся доставки AO (``<ao-session-delivery …>[from X]``) и межсессионная почта
    (``<cross-session-message … from-name="X">``); уведомления харнесса (F17) —
    нет. Свежие сверху, не больше :data:`INCOMING_MAX`, остальное — счётом."""
    import json

    if transcript is None:
        return []
    try:
        with open(transcript, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - _TRANSCRIPT_TAIL))
            raw = fh.read().decode("utf-8", errors="ignore")
    except OSError:
        return []
    found = []
    seen = set()  # одна доставка бывает и очередью, и репликой — один раз
    for ln in raw.splitlines():
        if "session-delivery" not in ln and "cross-session-message" not in ln:
            continue
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("type") not in ("user", "attachment"):
            continue
        at = _dialog._stamp(str(row.get("timestamp") or ""))
        if at is None or (since is not None and at <= since):
            continue
        text = _user_text(row)
        for rx in (_DELIVERY_RE, _CROSS_RE):
            for m in rx.finditer(text):
                who, body = m.group(1).strip(), " ".join(m.group(2).split())
                if not body or _dialog.is_harness_notice(body) or (who, body) in seen:
                    continue
                seen.add((who, body))
                found.append((at, who, body))
    found.sort(key=lambda r: r[0], reverse=True)
    out = ["- поручено мне — от {0} в {1}: {2}".format(
        who, at.strftime("%H:%M"), _dialog.clip(body, INCOMING_CLIP))
        for at, who, body in found[:INCOMING_MAX]]
    if len(found) > INCOMING_MAX:
        out.append("- (ещё поручений: {0} — транскрипт {1})".format(
            len(found) - INCOMING_MAX, transcript))
    return out


def session_incoming(session_entry: Path, context: Sequence[str], now=None) -> List[str]:
    """Входящие поручения передающей сессии от пульса перед ПЕРЕДАЧЕЙ
    (:func:`incoming_since`; одного пульса мало — за :data:`INCOMING_LOOKBACK_HOURS`
    до него или до «сейчас»). Никогда не падает."""
    from datetime import datetime, timedelta

    try:
        sid = (fields.read_field(Path(session_entry) / "arc.md",
                                 offload.CLAUDE_SESSION_FIELD) or "").strip()
        since = incoming_since(context) or (
            (_last_pulse_at(context) or now or datetime.now())
            - timedelta(hours=INCOMING_LOOKBACK_HOURS))
        return incoming_orders(transcript_path(sid), since)
    except Exception:  # noqa: BLE001 — сид важнее
        return []


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
        MAIN: (block_main(thread_entry, contract.find_body(seed_in, "main"), pred)
               or [contract.empty_line(contract.block("main"))]) + [manager_line()],
        EXPECTATION: block_expectation(
            layer(lays, "expectation"), _stream.passport_path(thread_entry),
            labels.get("expectation", [])),
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
                               session_incoming(session_entry,
                                                section_body(passport, "context")),
                               _auto.open_items(root, thread_entry)),
        UNANSWERED: block_unanswered(thread_entry),
        ENTRY_MAP: block_entry_map(contract.find_body(seed_in, "entry_map"),
                                   labels.get("entry_map", []),
                                   collected_map(root, session_entry, thread_entry))
        + [topic_pointer(root, thread_entry)],
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
    def bodies_at(clip: int) -> Dict[str, List[str]]:
        bodies = draft_bodies(root, session_entry, pulse_clip=clip)
        for name, hint in DRAFT_HINTS.items():
            bodies[name] = list(bodies.get(name) or []) + [hint]
        return bodies

    context = section_body(sections(_read(session_entry / "arc.md")), "context")
    return seal_budgeted(bodies_at, context=context,
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

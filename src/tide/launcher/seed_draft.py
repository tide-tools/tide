"""tide.launcher.seed_draft — ``tide handoffs draft``: харнес собирает ЧЕРНОВИК сида.

Боль (владелец, работа 15 / кандидат 153): дистилл хендоффа пишет САМАЯ уставшая
сессия — в конце, из памяти, руками. А материал уже лежит на диске: пульс в
паспорте (``tide offload``), ``decisions.md`` нити, живые работы нити, кандидаты
прогона, входной сид. Разбор живого сида 144-shov показал: блоки «решения», «дальше»,
«окружение» в нём почти дословно повторяли диск — ручной труд был только в
«опыте» и «карте входа».

Поэтому механическую половину берёт движок: ``draft`` компонует ЧЕРНОВИК по
контракту семи блоков (решение 05 нити edinyy-sloy, подпись 30.07) — финал ·
курсор · дальше · решения · опыт · карта входа · окружение — и кладёт его в
``<сессия>/workspace/seed-draft.md`` + печатает в stdout. Уставший агент
КУРИРУЕТ: режет лишнее, пишет «карту входа» (единственный блок, где нужна
голова держащего контекст) и сверяет дифф окружения.

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

from .. import fields, io as _io, offload, paths, placeholders, resolve, slug
from ..arc import stream as _stream, work as _work
from ..arc.stream import StreamError

DRAFT_FILENAME = "seed-draft.md"
WORKSPACE_DIRNAME = "workspace"
INPUT_SEED_NAME = "handoff-seed.md"

# Контракт семи блоков — имена и ПОРЯДОК ровно как в скилле handoff (шаг 3) и в
# SEED_CONTRACT доски: она мерит сид по этим подписям, поэтому имя блока здесь —
# не стиль, а интерфейс.
BLOCKS = ("финал", "курсор", "дальше", "решения", "опыт", "карта входа", "окружение")

# Пустой источник → видимая дыра, а не выдуманный абзац (контракт, подпись 30.07).
EMPTY = "—"

# Сколько последних пульс-строк ``## context`` тащить в курсор: хвост, а не вся
# память сессии — сид приземляет на следующий шаг, полную запись держит арка.
PULSE_TAIL = 5

_HEAD_RE = re.compile(r"^##\s+(.+?)\s*$")
_NEXT_SEP = "·"


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


# --- блоки (чистое) ---------------------------------------------------------

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
                 tail: int = PULSE_TAIL) -> List[str]:
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
        lines.append(ln if ln.lstrip().startswith("-") else "- {0}".format(ln))
    return lines


def block_next(nxt: Sequence[str]) -> List[str]:
    """Дальше: ``## next`` паспорта, разбитый по « · » в пункты списка.

    ``tide offload --next`` пишет 1–3 шага одной строкой через « · » — это уже
    список, просто слипшийся; доска мерит блок счётом пунктов.
    """
    items: List[str] = []
    for line in live_lines(nxt):
        for part in line.split(_NEXT_SEP):
            part = part.strip(" -\t")
            if part:
                items.append("- {0}".format(part))
    return items


def block_decisions(records: Sequence[Dict[str, object]]) -> List[str]:
    """Решения нити: ``NN — what (status)``, сначала open, потом settled.

    Open — то, что нить УЖЕ решила и не перерешивает; settled осело в канон, но
    в сиде остаётся анти-перерешиванием. ``superseded`` — история, её в сид не
    тащим. Порядок внутри группы — по номеру: номер и есть хронология.
    """
    out: List[str] = []
    for want in ("open", "settled"):
        for rec in records:
            status = str(rec.get("status") or "open").strip() or "open"
            if status != want:
                continue
            what = str(rec.get("what") or "").strip() or str(rec.get("slug") or "")
            out.append("- {0} — {1} ({2})".format(rec.get("num"), what, status))
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


def block_experience(candidate_nums: Sequence[str]) -> List[str]:
    """Опыт: кандидаты этой сессии/нити одной строкой + каркас для агента.

    Харнес знает только МЕХАНИЧЕСКУЮ половину — что было уронено кандидатом.
    Грабли, петли и «отвергнуто» пишет курирующий: отбор тут — работа головы,
    поэтому оставляем подсказку, а не выдуманный текст.
    """
    out: List[str] = []
    if candidate_nums:
        out.append("- кандидаты сессии: {0}".format(_nums_phrase(candidate_nums)))
    out.append(
        "- {0}  <!-- курируй: грабли · петли · «отвергнуто» — <путь> — <почему нет> -->"
        .format(EMPTY)
    )
    return out


def block_entry_map() -> List[str]:
    """Карта входа: каркас — харнесу нечем, «что читать и зачем» знает пишущий.

    Единственный блок контракта, который движок принципиально не собирает: пути
    он бы насыпал, но сид требует ПОРЯДКА чтения и «зачем» — это знание того,
    кто сейчас держит контекст.
    """
    return [
        "- {0}  <!-- пишет агент: что читать и зачем, в порядке чтения; "
        "кодовый шаг — файл/модуль, команда тестов, процедура выката -->".format(EMPTY)
    ]


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
        "куратор режет лишнее, пишет «карту входа», сверяет дифф «окружения»."
    ).format(session_name, thread_name)


def render_draft(bodies: Dict[str, List[str]], *, header: str = "") -> str:
    """Собрать черновик: семь блоков контракта, в контрактном порядке.

    ОДНО место, где живут порядок блоков и правило пустоты: блок без источника
    печатается с ``—``. Секцию не выкидываем никогда — доска мерит СОСТАВ, и
    видимая дыра честнее выдуманного абзаца.
    """
    lines: List[str] = []
    if header:
        lines += [header, ""]
    for name in BLOCKS:
        body = [ln for ln in bodies.get(name, []) if ln.strip()] or [EMPTY]
        lines += ["## {0}".format(name), "", *body, ""]
    return "\n".join(lines).rstrip() + "\n"


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


def build_draft(root: Path, session_entry: Path) -> str:
    """Собрать текст черновика для *session_entry* из того, что лежит на диске."""
    session_entry = Path(session_entry)
    thread_entry = session_entry.parents[1]  # …/NN-@thread/arcs/NN-session
    passport = sections(_read(session_entry / "arc.md"))
    seed_in = sections(_read(session_entry / "input" / INPUT_SEED_NAME))

    bodies = {
        "финал": block_final(_goal_of(thread_entry), _goal_of(session_entry)),
        "курсор": block_cursor(section_body(passport, "cursor"),
                               section_body(passport, "context"),
                               works=_thread_works(root, thread_entry)),
        "дальше": block_next(section_body(passport, "next")),
        "решения": block_decisions(_thread_decisions(root, thread_entry)),
        "опыт": block_experience(
            _session_candidate_nums(root, session_entry, thread_entry)),
        "карта входа": block_entry_map(),
        "окружение": block_environment(section_body(seed_in, "окружение")),
    }
    return render_draft(
        bodies, header=draft_header(session_entry.name, thread_entry.name))


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
        help="черновик сида по контракту семи блоков — из пульса, решений, кандидатов",
    )
    dp.add_argument(
        "session", nargs="?",
        help="арка-сессии (по умолчанию текущая, по $CLAUDE_CODE_SESSION_ID)",
    )
    dp.set_defaults(func=_cmd_draft, _cmd="handoffs draft")

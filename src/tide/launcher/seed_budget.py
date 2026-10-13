"""tide.launcher.seed_budget — сид передачи в бюджете знаков (SOC-181, шаг б).

Слово Гриши через менеджера: «посмотри контекст всех типов агентов на старте… надо
переписать всё правильно… без шума». Аудит старта (management 19-main-19,
``output/start-context-audit.md``, раздел 5 п.1–9) — что именно:

* бюджет — в ЗНАКАХ (``len(str)``) на весь ``handoff-seed.md`` кроме личности: 12 000 ролям,
  16 000 нити менеджера (:func:`budget_for`); у каждого блока квота (:data:`LAYOUT`);
  что не влезло — целиком в ``input/seed-archive.md`` новой сессии, в сиде одна
  строка-ссылка (п.1);
* порядок: кто ты и цель нити → сегодня и обещания с часами → следующий шаг → в работе
  сейчас → почта → мои открытые SOC → ждёт Гришу → слова Гриши → опыт → карта входа;
  «решения», «сырой журнал», «окружение», «задачи Linear», полная «где мы» — только в
  архиве (п.2, 5, 6);
* наследование со сроком: пункт «из прошлого сида» без свежего подтверждения живёт одну
  передачу, потом — в архив; пометок «(из прошлого сида)» и «(пульс ЧЧ:ММ)» в теле нет,
  дата — один раз в начале группы (п.3);
* одна цитата Гриши — одна строка с первой датой (п.4);
* «ждёт Гришу» — не больше 7 пунктов, без «проверь и закрой словом» и «SOC-N на проверке»;
  у пункта — SOC, кто несёт, рекомендация (п.7);
* шапка — несколько строк, правила нити одной ссылкой на файл (п.8, :mod:`tide.escalate_rules`);
* слой «ожидание Гриши» в сиде — не больше :data:`EXPECTATION_MAX` знаков, плейсхолдеры
  ``<…>`` в сид не попадают (п.9).

Сборщик тел блоков (:mod:`tide.launcher.seed_draft`) не меняется по сути: он собирает всё,
этот модуль раскладывает собранное по бюджету. Модуль ничего не пишет на диск сам —
архив пишет :func:`tide.launcher.handoff._with_throughline`.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Set, Tuple

from .. import handoff_contract as contract
from . import seed_compress as _sc

EMPTY = contract.EMPTY
ROLE_BUDGET = contract.SEED_BUDGET_CHARS
MANAGER_BUDGET = contract.SEED_BUDGET_MANAGER_CHARS
BUDGET_FIELD = contract.SEED_BUDGET_FIELD
ARCHIVE_NAME = contract.SEED_ARCHIVE_NAME
EXPECTATION_MAX = contract.SEED_EXPECTATION_CHARS
AWAITING_MAX = contract.SEED_AWAITING_MAX
ITEM_MAX = 520          # пункт длиннее — обрезан «…», целиком — в архиве
ARCHIVE_LINE_HEAD = "архив сида:"

# Блоки сида в порядке чтения: (ключ, заголовок, доля бюджета). Доля — гарантия блоку
# на первом проходе; остаток бюджета второй проход раздаёт по этому же порядку.
LAYOUT: Tuple[Tuple[str, str, float], ...] = (
    ("head", "кто ты и цель нити", 0.10),
    ("today", "сегодня — обещания, сдано, где мы", 0.13),
    ("next", "следующий шаг", 0.06),
    ("inflight", "в работе сейчас", 0.08),
    ("tails", contract.TAILS_NAME, 0.04),
    ("mail", "почта", 0.05),
    ("my_soc", "мои открытые SOC", 0.03),
    ("awaiting", "ждёт Гришу (несёт менеджер)", 0.10),
    ("words", "слова Гриши", 0.22),
    ("experience", "опыт", 0.12),
    ("entry_map", "карта входа", 0.07),
)
HEADINGS = {k: h for k, h, _s in LAYOUT}

# Блоки контракта, которые в тело сида не идут — только в архив (п.2, п.6).
ARCHIVE_ONLY = ("decisions", "journal", "environment", "linear")


# --- бюджет -------------------------------------------------------------------

def is_manager_thread(thread_entry: Path) -> bool:
    """Нить — та, что ведёт менеджер (``tide whois manager``: ``thread`` из manager.json
    / ``$TIDE_MANAGER_THREAD``, проект менеджера по ростеру). Точное имя нити, без
    синонимов: у операционки менеджера (ao-control) — бюджет роли. Никогда не падает."""
    try:
        from .. import whois, slug as _slug
        project, name = whois._split(whois.manager_thread_ref())
        thread_entry = Path(thread_entry)
        if _slug.entry_slug(thread_entry.name) != name:
            return False
        root = thread_entry.parents[2]
        proot = whois._project_root(whois._home(), project)
        if proot is not None:
            return Path(proot).resolve() == root.resolve()
        return root.name == project
    except Exception:  # noqa: BLE001
        return False


def budget_for(thread_entry: Path) -> int:
    """Бюджет сида нити в знаках: поле ``seed-budget:`` goal-файла нити (явно), иначе
    :data:`MANAGER_BUDGET` нити менеджера, иначе :data:`ROLE_BUDGET`."""
    try:
        from .. import fields
        from ..arc import stream as _stream
        raw = (fields.read_field(_stream.passport_path(Path(thread_entry)), BUDGET_FIELD)
               or "").strip().replace(" ", "").replace("_", "")
        if raw.isdigit() and int(raw) >= 2000:
            return int(raw)
    except Exception:  # noqa: BLE001
        pass
    return MANAGER_BUDGET if is_manager_thread(thread_entry) else ROLE_BUDGET


# --- разбор текста ------------------------------------------------------------

_HEAD = re.compile(r"^##\s+(.+?)\s*$")
_PULSE_TAIL = re.compile(r"\s*\(пульс \d{1,2}:\d{2}\)")
_PULSE_AT = re.compile(r"\(пульс (\d{1,2}):(\d{2})\)")
_LEAD_STAMP = re.compile(
    r"^(?P<d>\d{1,2})\.(?P<m>\d{2})(?:\.\d{2,4})?(?:\s+~?(?P<H>\d{1,2}):(?P<M>\d{2})"
    r"(?:\s*[–-]\s*\d{1,2}:\d{2})?)?(?:\s*[−-]\d{2})?\s*(?:—|:|·)\s*")
_ISO_STAMP = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2})?\S*\s*[—-]\s*")
_PLACEHOLDER = re.compile(r"<(?:тред|one line|the work-line|what this|where this)[^<>]*>")
_SOC = re.compile(r"\b(?:SOC|MIT)-\d+\b")
_STAR = re.compile(r"⭐\s*([A-ZА-ЯЁ]\d?)\b")
_ADVICE = re.compile(r"(?:предлагаю|рекомендую|моя)\s*[:—-]?\s*([^;()]{2,80})", re.IGNORECASE)
_AWAIT_TAG = re.compile(r"\s*\[(?:несёт|несу)[^\[\]]*\]\s*$")
_REVIEW_ITEM = re.compile(
    r"проверь и закрой|закро[йе]\w* словом|close --word|^\s*-?\s*(?:SOC|MIT)-\d+ на проверке \(|"
    r"на проверке у него|посмотреть и сказать слово", re.IGNORECASE)
_MINE = " · эта нить"


def is_contract_seed(text: str) -> bool:
    """Распил по бюджету — только для сида с блоками контракта (автосид, черновик);
    свободный текст передачи режется целыми разделами (:func:`fit_free`)."""
    secs = _sections(text)
    hits = sum(1 for b in contract.BLOCKS if contract.find_body(secs, b.key))
    return hits >= 4


def _sections(text: str) -> Dict[str, List[str]]:
    from .seed_draft import sections
    return sections(text)


def _body(secs: Dict[str, List[str]], key: str) -> List[str]:
    if key == "tails":
        for h, body in secs.items():
            if contract.norm(h).startswith(contract.TAILS_NAME):
                return list(body)
        return []
    return contract.find_body(secs, key)


def _live(body: Sequence[str]) -> List[str]:
    """Пункты блока: непустые, без заглушек ``<…>`` и html-подсказок."""
    out = []
    for ln in body:
        s = ln.rstrip()
        if not s.strip() or "<!--" in s or s.strip().startswith("<"):
            continue
        out.append(s)
    return out


def clean(line: str) -> str:
    """Пункт без служебного: маркер списка, «(пульс ЧЧ:ММ)», «(из прошлого сида)», хвосты
    статусов Linear, плейсхолдеры ``<…>``, пробелы схлопнуты."""
    s = _sc.bare(line)
    s = _PULSE_TAIL.sub("", s)
    s = _sc.strip_status_tails(s)
    s = _PLACEHOLDER.sub("", s).replace("«в работе прямо сейчас»", "«в работе сейчас»")
    return " ".join(s.split())


def strip_stamp(text: str) -> str:
    """Без даты-времени в начале («11.10 02:46 — », «2026-10-11T03:22:47 — »)."""
    s = _ISO_STAMP.sub("", text)
    return _LEAD_STAMP.sub("", s, count=1) if _LEAD_STAMP.match(s) else s


class Clock:
    """Дата пункта: штамп в начале строки, «(пульс ЧЧ:ММ)» по пульсам передающей сессии.
    Неизвестно — None (пункт старше: из прошлых сидов)."""

    def __init__(self, pulses: Sequence[datetime] = (), now: Optional[datetime] = None):
        self.now = now or datetime.now()
        self.pulses = sorted(pulses)

    def of(self, line: str) -> Optional[datetime]:
        s = _sc.bare(line)
        m = _ISO_STAMP.match(s)
        if m:
            y, mo, d, hh, mm = (int(x) for x in m.groups())
            try:
                return datetime(y, mo, d, hh, mm)
            except ValueError:
                return None
        at = _sc.line_when(s, self.now)
        if at is not None:
            return at
        m = _PULSE_AT.search(s)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            hits = [p for p in self.pulses if p.hour == hh and p.minute == mm]
            if hits:
                return hits[-1]
        return None


def thread_pulses(thread_entry: Optional[Path], origin: Optional[Path],
                  now: datetime, hours: int = 48) -> List[datetime]:
    """Время пульсов сессий нити, чьи паспорта правлены за *hours* часов (и передающей):
    по ним «(пульс ЧЧ:ММ)» получает дату."""
    from . import seed_draft as sd
    dirs: List[Path] = []
    if thread_entry is not None and (Path(thread_entry) / "arcs").is_dir():
        for d in sorted((Path(thread_entry) / "arcs").iterdir()):
            try:
                if (d / "arc.md").is_file() and \
                        now.timestamp() - (d / "arc.md").stat().st_mtime <= hours * 3600:
                    dirs.append(d)
            except OSError:
                continue
    if origin is not None and Path(origin) not in dirs:
        dirs.append(Path(origin))
    out: List[datetime] = []
    for d in dirs:
        for ln in sd.section_body(sd.sections(sd._read(d / "arc.md")), "context"):
            m = _ISO_STAMP.match(ln.strip().lstrip("- ").strip())
            if m:
                try:
                    at = datetime(*(int(x) for x in m.groups()))
                except ValueError:
                    continue
                if at <= now + timedelta(minutes=5):
                    out.append(at)
    return out


def by_day(items: Sequence[Item]) -> List[Item]:
    """Пункты группы по дате — новые первыми, без даты — в конце; внутри дня порядок свой."""
    return sorted(items, key=lambda it: (it.day is None,
                                         -(it.day.toordinal() if it.day else 0)))


# --- пункты и блоки -----------------------------------------------------------

class Item(NamedTuple):
    text: str                    # что печатается (одна строка, без «- »)
    full: str = ""               # полный текст для архива (пусто — как text)
    day: Optional[date] = None   # дата группы; None — без даты
    pinned: bool = False         # печатается всегда (строка-ссылка на архив)
    raw: bool = False            # печатать как есть, без «- » (строки шапки)


class Group:
    def __init__(self, label: str = "", items: Sequence[Item] = (), share: float = 1.0,
                 cap: int = 0, archive_key: str = "", dated: bool = True):
        self.label = label           # подпись группы («решения Гриши по проекту…:»)
        self.items = list(items)
        self.share = share           # доля квоты блока
        self.cap = cap               # абсолютный потолок знаков группы (0 — нет)
        self.archive_key = archive_key
        self.dated = dated
        self.taken: List[Item] = []


class Block:
    def __init__(self, key: str, groups: Sequence[Group], *, empty: str = "",
                 omit_empty: bool = False):
        self.key = key
        self.heading = HEADINGS[key]
        self.groups = list(groups)
        self.empty = empty
        self.omit_empty = omit_empty

    def has(self) -> bool:
        return any(g.taken for g in self.groups)


def _clip(text: str, n: int = ITEM_MAX) -> str:
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _day_label(d: Optional[date], now: datetime) -> str:
    if d is None:
        return "раньше:"
    return "{0}:".format(d.strftime("%d.%m"))


_NO_DAY = object()


def render_group(g: Group, now: datetime, cur: object = _NO_DAY) -> List[str]:
    """Строки группы; *cur* — день последнего пункта предыдущей группы блока (подпись дня
    не повторяется). Подпись группы начинает дни заново."""
    return _render_group(g, now, cur)[0]


def _render_group(g: Group, now: datetime, cur: object = _NO_DAY) -> Tuple[List[str], object]:
    if not g.taken:
        return [], cur
    out = [g.label] if g.label else []
    if g.label:
        cur = _NO_DAY
    days = [it.day for it in g.taken if not it.raw]
    dated = g.dated and any(d is not None for d in days)
    for it in g.taken:
        if it.raw:
            out.append(it.text)
            continue
        if dated and it.day != cur and not it.pinned:
            out.append(_day_label(it.day, now))
            cur = it.day
        out.append("- " + it.text)
    return out, cur


def render_block(b: Block, now: datetime) -> str:
    lines = ["## " + b.heading, ""]
    body: List[str] = []
    cur: object = _NO_DAY
    for g in b.groups:
        part, cur = _render_group(g, now, cur)
        body += part
    if not body:
        if b.omit_empty:
            return ""
        body = [b.empty or "{0} пусто".format(EMPTY)]
    return "\n".join(lines + body) + "\n"


# --- отдельные блоки ------------------------------------------------------------

def _dedup(items: Iterable[Item]) -> List[Item]:
    out, seen = [], set()
    for it in items:
        k = _sc.lesson_text(it.text)
        if k and k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def _lead(line: str) -> str:
    m = re.match(r"[\wа-яё]+", line.lower().lstrip("[✓x> ]"))
    return m.group(0) if m else line[:8].lower()


_FRAGMENT = re.compile(r"^(?:\d{1,3}\b(?![.:])|[A-ZА-Я]»|[a-zа-яё]{1,3}\b»)")
_STEP = re.compile(r"^\[(.)\]\s*\d+\.")
_VERSION = re.compile(r"^v\d+(?:\.\d+)?\s*·\s*\d{4}-\d{2}-\d{2}")
_LABEL_START = re.compile(r"^[\wа-яё][\wа-яё \-]{1,30}:\s")
_TERMINAL = re.compile(r"[.!?»)]\s*$")
_OPEN_END = re.compile(r"[,;(—-]\s*$")


def join_wrapped(lines: Iterable[str]) -> List[str]:
    """Строки «главного», которые сборщик разрезал (жёсткий перенос абзаца или « · » внутри
    пункта), — обратно в пункт: строка с маленькой буквы, цифры, «(» или обломка «B»»
    после строки без точки в конце — продолжение предыдущей; «метка: …» — новый пункт."""
    out: List[str] = []
    for s in lines:
        if not s:
            continue
        cont = bool(out) and not _TERMINAL.search(out[-1]) and not _LABEL_START.match(s) \
            and (bool(_OPEN_END.search(out[-1])) or s[:1].islower() or s[:1].isdigit()
                 or s[:1] == "(" or bool(_FRAGMENT.match(s))) \
            and not _STEP.match(s) and not _VERSION.match(s)
        if cont:
            sep = " · " if (s[:1].isdigit() or _FRAGMENT.match(s)) else " "
            out[-1] = out[-1] + sep + s
        else:
            out.append(s)
    return out


def main_items(body: Sequence[str]) -> List[Item]:
    """«Главное» нити (plan.md, решение № 24). Скелет плана — «final:» и открытые шаги
    («[ ]», «[>]» с описанием текущего) — по порядку; сделанные шаги — в архив. Дописанная
    лента («SOC…», «Грише…», «не дублировать…») — новые первыми, из строк одного вида
    только последняя; обломки, устаревшие адреса и адрес менеджера (он в шапке) — вон."""
    plan: List[str] = []
    feed: List[str] = []
    current = False
    for s in join_wrapped(clean(ln) for ln in _live(body)
                          if not (ln.startswith("  ") and not ln.strip().startswith("- "))):
        low = s.lower()
        if not s or s.startswith(EMPTY) or low.startswith(("менеджер сейчас", "менеджер:")) \
                or _sc_stale(s) or _FRAGMENT.match(s) or len(s) < 12:
            continue
        step = _STEP.match(s)
        if step:
            current = step.group(1) == ">"
            if step.group(1) in (" ", ">"):
                plan.append(s)
            continue
        if low.startswith(("описание:", "проверка:")):
            if current:
                plan.append(s)
            continue
        current = False
        if low.startswith("final:"):
            plan.insert(0, s)
            continue
        if _VERSION.match(s):
            continue  # история версий плана — в архиве
        feed.append(s)
    last: Dict[str, int] = {}
    for i, s in enumerate(feed):
        last[_lead(s)] = i
    fresh = [s for i, s in enumerate(feed) if last[_lead(s)] == i]
    return [Item(_clip(s, 360), s) for s in plan + fresh]


def _sc_stale(s: str) -> bool:
    return "(устарело: менеджер сейчас" in s


def awaiting_items(body: Sequence[str], *, older: Set[str], manager: bool,
                   ) -> Tuple[List[Item], List[Tuple[str, str]]]:
    """«Ждёт Гришу»: ``(пункты в сид по порядку, [(строка, почему в архиве)])``.

    В архив: «проверь и закрой словом» и «SOC-N на проверке» (Done ставит проверяющий,
    не Гриша), сводки «снято…», пункт из прошлого сида, который уже прожил одну
    передачу (*older* — ядра пунктов сидов старше входного). Свежие — первыми."""
    fresh: List[Item] = []
    inherited: List[Item] = []
    gone: List[Tuple[str, str]] = []
    who = "несу я" if manager else "несёт менеджер"
    for ln in _live(body):
        raw = _sc.bare(ln)
        if _REVIEW_ITEM.search(raw):
            gone.append((ln, "приёмка — у проверяющего, не у Гриши"))
            continue
        if _sc._DROPPED_RE.match(raw) or raw.lower().startswith("снято:") or raw.startswith(EMPTY):
            gone.append((ln, "снято"))
            continue
        was = _sc.INHERITED_MARK in raw or "(из прошлого сида;" in raw
        core = await_core(raw)
        if was and core in older:
            gone.append((ln, "из прошлого сида без подтверждения — прожил одну передачу"))
            continue
        text = _AWAIT_TAG.sub("", clean(re.sub(r"\s*\(из прошлого сида[^()]*\)", "", raw)))
        socs = list(dict.fromkeys(_SOC.findall(text)))
        star = _STAR.search(text)
        adv = _ADVICE.search(text)
        rec = "⭐" + star.group(1) if star else (adv.group(1).strip() if adv else "не названа")
        tag = " [{0}; {1}; {2}]".format(
            who, " ".join(socs) if socs else "без SOC",
            rec if rec.startswith("⭐") else "рекомендация: " + _clip(rec, 60))
        it = Item(_clip(text, ITEM_MAX - len(tag)) + tag, ln)
        (inherited if was else fresh).append(it)
    items = _dedup(fresh + inherited)
    return items, gone


def strip_await_tag(line: str) -> str:
    """Пункт «ждёт Гришу» без тега сида «[несёт …; SOC; рекомендация]»."""
    return _AWAIT_TAG.sub("", line or "")


def await_core(line: str) -> str:
    """Ядро пункта «ждёт Гришу» для сверки между сидами: без пометок, хвостов, тега."""
    s = re.sub(r"\s*\(из прошлого сида[^()]*\)", "", _sc.bare(line))
    return _sc.lesson_text(_AWAIT_TAG.sub("", clean(s)))


def older_awaiting(thread_entry: Optional[Path], skip: Sequence[Path]) -> Set[str]:
    """Ядра «ждёт Гришу» всех сидов нити, кроме *skip* (входной сид передающей и сид
    новой сессии): что уже было там — прожило передачу."""
    out: Set[str] = set()
    if thread_entry is None:
        return out
    from .seed_draft import INPUT_SEED_NAME
    skip_r = {Path(p).resolve() for p in skip if p}
    arcs = Path(thread_entry) / "arcs"
    for d in (sorted(arcs.iterdir()) if arcs.is_dir() else []):
        f = d / "input" / INPUT_SEED_NAME
        try:
            if f.resolve() in skip_r or not f.is_file():
                continue
            secs = _sections(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        for ln in _live(contract.find_body(secs, "awaiting")):
            out.add(await_core(ln))
    out.discard("")
    return out


# Цитаты Гриши: «…» (8+ знаков). Обёртки «Гриша 09.10:», «10.10:», «(tg 3845, 10.10 11:13)».
_QUOTE = re.compile(r"«([^«»]{8,})»")
_TG_PAREN = re.compile(r"\s*\((?:tg|тг|❓)[^()]*\)", re.IGNORECASE)
_TG_BARE = re.compile(r"\s*\btg\s*~?\d{3,6}(?:,\s*\d{1,2}\.\d{2}\s+\d{1,2}:\d{2})?", re.IGNORECASE)
_WRAP = re.compile(r"^(?:(?:слова?\s+)?Гриш[аи]\s*(?:\d{1,2}\.\d{2})?\s*[:—-]\s*|"
                   r"\d{1,2}\.\d{2}(?:\s+~?\d{1,2}:\d{2})?\s*[:—-]\s*)", re.IGNORECASE)


def quote_key(text: str) -> str:
    """Цитата для сверки: нижний регистр, без пунктуации, кавычек, многоточий."""
    s = text.lower().replace("ё", "е")
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


_SENT_DATE = re.compile(r"(?<=[.!?»)])\s+(?=\d{1,2}\.\d{2}\b|❓|«)")


def _split_segments(line: str) -> List[str]:
    """Строка слоя → куски: по « · », «; » (:func:`seed_compress.split_lessons`) и по
    границе фразы перед датой, ❓ или цитатой."""
    body = strip_stamp(clean(line))
    parts = [p[2:] if p.startswith("- ") else p for p in _sc.split_lessons(body)] or [body]
    out: List[str] = []
    for p in parts:
        out += [x for x in _SENT_DATE.split(p) if x.strip()]
    return out


def _quote_parts(q: str) -> List[str]:
    """Цитата с пропусками «…» → куски для сверки (каждый от 8 знаков)."""
    return [quote_key(x) for x in re.split(r"…|\.\.\.", q) if len(quote_key(x)) >= 8]


def _seen_quote(q: str, seen: Sequence[str]) -> bool:
    parts = _quote_parts(q) or [quote_key(q)]
    return all(any(p in s or (len(s) >= 12 and s in p) for s in seen) for p in parts if p)


def words_items(lines: Sequence[str], clock: Clock, seen: Optional[List[str]] = None
                ) -> List[Item]:
    """Слова Гриши: одна цитата — одна строка с первой датой (п.4). Строку слоя с
    несколькими цитатами режем на куски; кусок, все цитаты которого уже были
    (сверка по тексту без кавычек, обёрток и tg-номеров), — вон. *seen* — общий
    список ключей цитат между группами блока (и почтой)."""
    seen = seen if seen is not None else []
    rows: List[Tuple[Optional[datetime], int, str, str]] = []
    for i, ln in enumerate(_live(lines)):
        s = _sc.bare(ln)
        if s.startswith("(") or s.startswith(EMPTY) or _PLACEHOLDER.search(s):
            continue
        at = clock.of(ln)
        for seg in _split_segments(ln):
            own = _sc.line_when(seg, clock.now)
            seg = _WRAP.sub("", _TG_BARE.sub("", _TG_PAREN.sub("", seg))).strip(" ·;:,—-")
            if own is not None and (at is None or own < at):
                at_seg = own
            else:
                at_seg = at
            if len(seg) >= 6:
                rows.append((at_seg, i, seg, ln))
    # первая дата цитаты — самая ранняя строка, где она есть
    rows.sort(key=lambda r: (r[0] or datetime.min, r[1]))
    kept: List[Tuple[Optional[datetime], str, str]] = []
    for at, _i, seg, ln in rows:
        raw_q = _QUOTE.findall(seg) or [seg]
        if raw_q and all(_seen_quote(q, seen) for q in raw_q):
            continue
        for q in raw_q:
            seen.extend(_quote_parts(q) or [quote_key(q)])
        kept.append((at, seg, ln))
    kept.sort(key=lambda r: r[0] or datetime.min, reverse=True)
    return [Item(_clip(seg), ln, at.date() if at else None) for at, seg, ln in kept]


def decisions_items(lines: Sequence[str], seen: List[str], now: datetime) -> List[Item]:
    """Решения Гриши по проекту за 3 суток («- 11.10 02:31 · вопрос → «ответ» · SOC-181»):
    дата — группой, ответ, уже бывший в ожидании, — вон."""
    out = []
    for ln in _live(lines):
        s = _sc.bare(ln)
        if s.startswith("ещё "):
            out.append(Item(s, raw=False, pinned=True))
            continue
        at = _sc.line_when(s.replace(" · ", " — ", 1), now)
        body = strip_stamp(s.replace(" · ", " — ", 1))
        answer = body.split("→", 1)[-1]
        raw_q = _QUOTE.findall(answer)
        if raw_q and all(_seen_quote(q, seen) for q in raw_q):
            continue
        for q in raw_q:
            seen.extend(_quote_parts(q) or [quote_key(q)])
        # обрезанный ответ («…в т…» без закрывающей ёлочки) — тоже в сверку почты
        seen.extend(_quote_parts(re.split(r"·\s*(?:SOC|MIT)-\d+|\(уточнить\)", answer)[0]
                                 .strip(" «»")))
        hm = at.strftime("%H:%M") + " · " if at else ""
        out.append(Item(_clip(hm + body), ln, at.date() if at else None))
    return out


_DIALOG = re.compile(r"^- (\S+) · ([^·]+?) · (\S+) · (.*)$")
_MAIL_LINE = re.compile(r"^-?\s*почта: непрочитанное письмо (\d+) от (\S+?):")
_MAIL_MORE = re.compile(r"^-?\s*почта: ещё непрочитанных (\d+) — tide mail ls (\S+)")
_MAIL_ADDR = re.compile(r"tide mail (?:read|ls) (\S+)")


def is_real_grisha(speaker: str, channel: str, text: str) -> bool:
    """Реплика самого Гриши, а не письмо сессии под его именем: «Гриша · ao · [from …]» —
    пересылка почты; «Гриша · ao» без пометки — его слово в окне AO."""
    if speaker.strip() != "Гриша":
        return False
    t = text.strip()
    if t.startswith("[from ") or "tide mail read" in t:
        return False
    try:
        from ..arc import dialog as _dialog
        if _dialog.is_harness_notice(t):
            return False
    except Exception:  # noqa: BLE001
        pass
    return True


def mail_items(unanswered: Sequence[str], inflight: Sequence[str], seen: Sequence[str],
               now: datetime) -> List[Item]:
    """Почта: непрочитанное от самого Гриши строками (то, что уже легло в журнал решений,
    — нет); письма между сессиями — числом и командой ``tide mail ls``."""
    out: List[Item] = []
    for ln in _live(unanswered):
        m = _DIALOG.match(ln.strip())
        if not m or not is_real_grisha(m.group(2), m.group(3), m.group(4)):
            continue
        text = " ".join(m.group(4).split())
        k = quote_key(text)
        if any(k and (k in s or s in k) for s in seen if len(s) >= 12):
            continue
        try:
            at = datetime.fromisoformat(m.group(1)[:19])
        except ValueError:
            at = None
        hm = at.strftime("%H:%M") + " " if at else ""
        out.append(Item("{0}{1}: «{2}»".format(hm, m.group(3), _clip(text, 300)), ln,
                        at.date() if at else None))
    n, addr = 0, ""
    for ln in inflight:
        s = ln.strip()
        m = _MAIL_LINE.match(s)
        if m:
            n += 1
        m2 = _MAIL_MORE.match(s)
        if m2:
            n += int(m2.group(1))
        a = _MAIL_ADDR.search(s)
        if a and not addr:
            addr = a.group(1)
    if n:
        out.append(Item("писем от сессий непрочитано: {0} — tide mail ls {1}".format(
            n, addr or "<проект>/<нить>"), pinned=True))
    return out


def soc_items(body: Sequence[str]) -> Tuple[List[Item], str]:
    """Мои открытые SOC: задачи, которые в Linear взяла или получила эта нить
    («Взял:»/«Назначено:»), в статусе In Progress или Todo. ``(пункты, почему пусто)``."""
    out: List[Item] = []
    unsure = ""
    for ln in _live(body):
        s = ln.strip()
        if s.startswith("- (статус не сверен"):
            unsure = s[2:].strip("() ")
        if not s.startswith("- ") or _MINE not in s:
            continue
        m = re.match(r"^- ((?:SOC|MIT)-\d+) — \S+ — (In Progress|Todo)\b", s)
        if m:
            out.append(Item("{0} — {1}".format(m.group(1), m.group(2)), s))
    why = ("{0} Linear не сверен ({1}) — задачи нити смотри в Linear".format(EMPTY, unsure)
           if unsure else "{0} назначенных этой нити задач в In Progress/Todo нет".format(EMPTY))
    return out, why


_EXPECT_LABEL = re.compile(r"(?:^|(?<=[.;!?»)\]—·])\s+)(Ожидание(?:\s+Гриши)?|Нельзя)\s*:\s*",
                           re.IGNORECASE)


def pulse_words(origin: Optional[Path]) -> List[str]:
    """Метки «Ожидание:» и «Нельзя:» самого свежего пульса передающей сессии, где они
    есть, — строками слоя с датой пульса (слой нити их не всегда успевает записать)."""
    if origin is None:
        return []
    from . import seed_draft as sd
    ctx = sd.section_body(sd.sections(sd._read(Path(origin) / "arc.md")), "context")
    if contract._LABEL_RE is None:
        contract._LABEL_RE = contract._label_re()
    for ln in reversed(sd.live_lines(ctx)):
        raw = ln.strip().lstrip("- ").strip()
        m = _ISO_STAMP.match(raw)
        if not m:
            continue
        note = " ".join(raw[m.end():].split())
        hits = list(_EXPECT_LABEL.finditer(note))
        if not hits:
            continue
        stamp = "{0}.{1} {2}:{3} — ".format(m.group(3), m.group(2), m.group(4), m.group(5))
        out = []
        for h in hits:
            rest = note[h.end():]
            nxt = [x for x in (contract._LABEL_RE.search(" " + rest),
                               _EXPECT_LABEL.search(" " + rest)) if x]
            end = min(x.start() for x in nxt) - 1 if nxt else len(rest)
            piece = rest[:max(end, 0)].strip(" .;·")
            if piece:
                lead = "нельзя: " if h.group(1).lower().startswith("нельзя") else ""
                out.append("- " + stamp + lead + piece)
        return out
    return []


# --- сегодня --------------------------------------------------------------------

_PROMISE = re.compile(r"(?:(?<!\w)к|(?<!\w)до)\s+~?(\d{1,2}):(\d{2})\b", re.IGNORECASE)
_PIECE_SPLIT = re.compile(r"(?<=[.!?])\s+|\s·\s|;\s+")


def _deadline(said: datetime, hh: int, mm: int) -> datetime:
    at = said.replace(hour=hh % 24, minute=mm, second=0, microsecond=0)
    if at < said - timedelta(minutes=30):
        at += timedelta(days=1)
    return at


def today_items(origin: Optional[Path], where: Sequence[str], clock: Clock,
                now: datetime) -> Tuple[List[Item], List[Item]]:
    """Сегодня: срок сессии (``--eta``/``--deliverable``), обещания «к ЧЧ:ММ» из пульсов
    (просроченное помечено), слой «где мы» за сегодня, пульсы за сегодня — новые первыми.
    ``(обещания и срок, где мы и сдано)``."""
    from .. import fields
    from . import seed_draft as sd
    promises: List[Item] = []
    done: List[Item] = []
    since = min(datetime.combine(now.date(), datetime.min.time()), now - timedelta(hours=6))
    if origin is not None:
        passport = Path(origin) / "arc.md"
        eta = (fields.read_field(passport, "eta") or "").strip()
        what = (fields.read_field(passport, "deliverable") or "").strip()
        if eta and eta not in ("-", "none"):
            try:
                at = datetime.fromisoformat(eta).replace(tzinfo=None)
                late = " — просрочено" if at < now else ""
                promises.append(Item("срок сессии: к {0}{1}{2}".format(
                    at.strftime("%d.%m %H:%M") if at.date() != now.date() else at.strftime("%H:%M"),
                    " — " + what if what else "", late), pinned=True))
            except ValueError:
                pass
        psecs = sd.sections(sd._read(passport))
        cursor = [ln for ln in sd.live_lines(sd.section_body(psecs, "cursor"))
                  if not ln.strip().startswith("#")]
        if cursor:
            promises.insert(0, Item("курсор прошлой сессии: " + _clip(
                " ".join(cursor[0].strip(" -").split()), 300), cursor[0], pinned=True))
        context = sd.section_body(psecs, "context")
        pulses = []
        for ln in sd.live_lines(context):
            m = _ISO_STAMP.match(ln.strip().lstrip("- ").strip())
            if not m:
                continue
            y, mo, d, hh, mi = (int(x) for x in m.groups())
            try:
                at = datetime(y, mo, d, hh, mi)
            except ValueError:
                continue
            note = ln.strip().lstrip("- ").strip()[m.end():].strip()
            pulses.append((at, note))
        seen_p: Set[str] = set()
        for at, note in pulses:
            if at < since:
                continue
            for piece in _PIECE_SPLIT.split(note):
                for pm in _PROMISE.finditer(piece):
                    dl = _deadline(at, int(pm.group(1)), int(pm.group(2)))
                    key = quote_key(piece)[:80]
                    if key in seen_p:
                        continue
                    seen_p.add(key)
                    late = " — просрочено" if dl < now else ""
                    promises.append(Item("обещано к {0} (сказано {1}): {2}{3}".format(
                        dl.strftime("%H:%M"), at.strftime("%H:%M"),
                        _clip(_PULSE_TAIL.sub("", piece).strip(" .·"), 220), late),
                        piece, dl.date()))
        for at, note in sorted(pulses, key=lambda p: p[0], reverse=True):
            if at < since:
                continue
            if contract.is_pass_note(note):
                m = re.search(r"Где\s+мы\s*:\s*", note, re.IGNORECASE)
                gist = ""
                if m:
                    rest = note[m.end():]
                    if contract._LABEL_RE is None:
                        contract._LABEL_RE = contract._label_re()
                    nxt = contract._LABEL_RE.search(" " + rest)
                    gist = rest if nxt is None else rest[:max(nxt.start() - 1, 0)]
                gist = "передача: " + (gist.strip(" .·") or "см. архив «где мы»")
            else:
                gist = contract.pulse_head(note)
                if not gist:
                    continue  # нота из одних меток — разнесена по блокам сида
            done.append(Item("{0} — {1}".format(at.strftime("%H:%M"), _clip(gist.strip(), 420)),
                             "{0} — {1}".format(at.isoformat(timespec="minutes"), note),
                             at.date()))
    for ln in _live(where):
        s = _sc.bare(ln)
        at = clock.of(ln)
        if _ISO_STAMP.match(s) or s.startswith("итог сессии"):
            continue  # пульсы — выше, итог сессии — в архиве
        if at is not None and at >= since:
            done.insert(0, Item("где мы ({0}): {1}".format(
                at.strftime("%H:%M"), _clip(strip_stamp(clean(ln)), 420)), ln, at.date()))
            break
    return promises, done


# --- сборка ---------------------------------------------------------------------

class Head(NamedTuple):
    title: str = ""
    goal: str = ""
    who: str = ""
    manager: str = ""
    links: str = ""                    # правила нити · договорённости · грабли
    decisions_label: str = ""          # «решения Гриши по проекту … за 3 суток (журнал …):»
    decisions: Tuple[str, ...] = ()
    start: str = ""                    # готовые команды старт-гейта (SOC-286 эксп. 1)


class Shaped(NamedTuple):
    text: str
    archive: str
    report: Dict[str, object]


def _fit(blocks: Sequence[Block], budget: int, fixed: int, now: datetime) -> None:
    """Раскладка по бюджету: закреплённое — сразу; первый проход — квоты групп; второй —
    остаток бюджета по порядку блоков. Пункт, не влезший в квоту, ждёт второго прохода."""
    shares = {k: s for k, _h, s in LAYOUT}
    avail = max(0, budget - fixed)
    chosen: Dict[int, Set[int]] = {}

    def sync(g: Group) -> None:
        idx = chosen[id(g)]
        g.taken = [it for i, it in enumerate(g.items) if i in idx]

    def total() -> int:  # как в :func:`assemble`: блоки через пустую строку
        return fixed + sum(len(r) + 1 for r in (render_block(b, now) for b in blocks) if r)

    def gsize(g: Group) -> int:
        return len("\n".join(render_group(g, now)))

    for b in blocks:
        for g in b.groups:
            chosen[id(g)] = {i for i, it in enumerate(g.items) if it.pinned}
            sync(g)

    def attempt(g: Group, i: int, limit: Optional[int]) -> None:
        chosen[id(g)].add(i)
        sync(g)
        if (limit is not None and gsize(g) > limit) or (g.cap and gsize(g) > g.cap) \
                or total() > budget:
            chosen[id(g)].discard(i)
            sync(g)

    for b in blocks:
        quota = int(avail * shares.get(b.key, 0.05))
        for g in b.groups:
            gq = int(quota * g.share)
            limit = min(gq, g.cap) if g.cap else gq
            for i, _it in enumerate(g.items):
                if i not in chosen[id(g)]:
                    attempt(g, i, limit)
    for b in blocks:
        for g in b.groups:
            for i, _it in enumerate(g.items):
                if i not in chosen[id(g)]:
                    attempt(g, i, None)


def build(distil: str, *, head: Head, persona: str = "", budget: int = ROLE_BUDGET,
          thread_entry: Optional[Path] = None, origin: Optional[Path] = None,
          born: Optional[Path] = None, archive_path: str = "", manager: bool = False,
          now: Optional[datetime] = None) -> Shaped:
    """Сид по бюджету из собранного сборщиком текста (*distil* — блоки контракта).

    Возвращает текст сида, текст архива и отчёт (знаки по блокам, личность, что ушло
    в архив). Бюджет — на весь сид кроме блока личности."""
    from .seed_draft import INPUT_SEED_NAME
    now = now or datetime.now()
    secs = _sections(distil)
    clock = Clock(thread_pulses(thread_entry, origin, now), now)
    archive: Dict[str, List[str]] = {}

    def to_archive(key: str, lines: Iterable[str], why: str = "") -> None:
        name = contract.name(key) if key in {b.key for b in contract.BLOCKS} else (
            contract.TAILS_NAME if key == "tails" else key)
        bucket = archive.setdefault(name, [])
        for ln in lines:
            s = ln.rstrip()
            if s.strip():
                bucket.append(s if s.lstrip().startswith(("- ", "  ")) else "- " + s.strip())
        if why and bucket and not any(b.startswith("(") for b in bucket[:1]):
            bucket.insert(0, "({0})".format(why))

    # шапка
    head_items = [Item(x, raw=True, pinned=True) for x in (
        "идёт к: " + head.goal if head.goal else "",
        head.who + ("; " + head.manager if head.manager else ""),
        head.start, head.links) if x.strip()]
    main_body = _body(secs, "main")
    mains = main_items(main_body)
    head_block = Block("head", [Group("", head_items, 0.0, dated=False),
                                Group("главное:", mains, 1.0, archive_key="main", dated=False)])

    promises, done = today_items(origin, _body(secs, "where"), clock, now)
    today = Block("today", [Group("", by_day(promises), 0.35, archive_key="where"),
                            Group("", by_day(done), 0.65, archive_key="where")],
                  empty="{0} за сегодня пульсов и обещаний нет — где мы: архив, блок «где мы»".format(
                      EMPTY))

    nxt = Block("next", [Group("", _dedup(Item(_clip(clean(ln)), ln)
                                          for ln in _live(_body(secs, "next"))
                                          if not _sc.bare(ln).startswith(EMPTY)),
                               archive_key="next", dated=False)],
                empty=contract.empty_line(contract.block("next")))

    infl_body = _live(_body(secs, "inflight"))
    infl = Block("inflight", [Group("", _dedup(
        Item(_clip(strip_stamp(clean(ln))), ln) for ln in infl_body
        if not _MAIL_LINE.match(ln.strip()) and not _MAIL_MORE.match(ln.strip())
        and not _sc.bare(ln).startswith(EMPTY)), archive_key="inflight", dated=False)],
                 empty="{0} в работе ничего не названо (метки «В полёте:», воркеры, поручения)".format(
                     EMPTY))

    tails = Block("tails", [Group("", _dedup(
        Item(_clip(clean(ln)), ln) for ln in _live(_body(secs, "tails"))
        if not _sc.bare(ln).startswith((EMPTY, "(")) and clean(ln) not in ("нет",)),
        archive_key="tails", dated=False)], omit_empty=True)

    # слова Гриши: ожидание (≤ EXPECTATION_MAX) + решения по проекту за 3 суток
    seen: List[str] = []
    exp_lines = pulse_words(origin) + [ln for ln in _live(_body(secs, "expectation"))
                                       if not _sc.bare(ln).startswith("(")]
    words_exp = words_items(exp_lines, clock, seen)
    words_dec = decisions_items(head.decisions, seen, now)
    words = Block("words", [
        Group("", words_exp, 0.62, cap=EXPECTATION_MAX, archive_key="expectation"),
        Group(head.decisions_label, words_dec, 0.38, archive_key="expectation")],
        empty=contract.empty_line(contract.block("expectation")))

    mail = Block("mail", [Group("", mail_items(_body(secs, "unanswered"), infl_body, seen, now),
                                archive_key="unanswered")],
                 empty="{0} непрочитанного от Гриши нет, писем от сессий нет".format(EMPTY))

    socs, soc_why = soc_items(_body(secs, "linear"))
    my_soc = Block("my_soc", [Group("", socs, archive_key="linear", dated=False)], empty=soc_why)

    skip = [Path(origin) / "input" / INPUT_SEED_NAME if origin is not None else None,
            Path(born) / "input" / INPUT_SEED_NAME if born is not None else None]
    older = older_awaiting(thread_entry, [p for p in skip if p is not None])
    aw_items, aw_gone = awaiting_items(_body(secs, "awaiting"), older=older, manager=manager)
    aw_top, aw_rest = aw_items[:AWAITING_MAX], aw_items[AWAITING_MAX:]
    awaiting = Block("awaiting", [Group("", aw_top, archive_key="awaiting", dated=False)],
                     empty="{0} Гришу ничего не ждёт (метки «Ждёт Гришу:», вопросы доски)".format(EMPTY))
    if manager:
        awaiting.heading = "ждёт Гришу (несу я)"

    exp_items: List[Item] = []
    old_group: List[Item] = []
    in_old = False
    for ln in _live(_body(secs, "experience")):
        s = _sc.bare(ln)
        if _sc.OLD_HEAD_RE.match(ln.strip()):
            in_old = True
            continue
        if s.startswith(("(опыт: ещё", "(свежий опыт сверх")) or s.startswith(EMPTY):
            continue
        if in_old and not ln.startswith("  "):
            in_old = False
        at = clock.of(ln)
        it = Item(_clip(strip_stamp(clean(ln))), ln, at.date() if at else None)
        (old_group if in_old else exp_items).append(it)
    experience = Block("experience", [
        Group("", by_day(_dedup(exp_items)), 0.7, archive_key="experience"),
        Group("старше суток — самые частые:", _dedup(old_group), 0.3, archive_key="experience",
              dated=False)],
        empty=contract.empty_line(contract.block("experience")))

    em: List[Item] = []
    for ln in _live(_body(secs, "entry_map")):
        s = _sc.bare(ln)
        if ln.startswith("  - топик:") or s.startswith(("указатель:", EMPTY)) \
                or ARCHIVE_LINE_HEAD in s:
            continue
        em.append(Item(_clip(clean(ln).replace("— статус — в блоке «задачи Linear»",
                                               "— статус — в Linear"), 300), ln))
    pointer = []
    if origin is not None:
        pointer.append("паспорт прошлой сессии: {0} (курсор, ## context — сырой журнал)".format(
            Path(origin).resolve() / "arc.md"))
    if archive_path:
        pointer.append("{0} {1} — что не влезло в бюджет; там же решения, задачи Linear, "
                       "полная «где мы»".format(ARCHIVE_LINE_HEAD, archive_path))
    entry = Block("entry_map", [Group("", _dedup(em), archive_key="entry_map", dated=False),
                                Group("", [Item(p, pinned=True) for p in pointer], dated=False)],
                  empty=contract.empty_line(contract.block("entry_map")))

    blocks = [head_block, today, nxt, infl, tails, mail, my_soc, awaiting, words, experience,
              entry]
    title = (head.title or "# сид передачи").strip()
    persona_text = ("\n" + persona.strip() + "\n") if persona.strip() else ""
    extras = extra_sections(distil)
    fixed = len(title) + 2 + (len(extras) + 1 if extras else 0)  # «title\n\n» + разделы вне контракта
    _fit(blocks, budget, fixed, now)

    # что не влезло — в архив, целиком
    for b in blocks:
        for g in b.groups:
            if not g.archive_key:
                continue
            taken = {id(it) for it in g.taken}
            rest = [it.full or it.text for it in g.items if id(it) not in taken]
            clipped = [it.full for it in g.taken if it.full and it.text.endswith("…")]
            to_archive(g.archive_key, rest + clipped)
    to_archive("awaiting", [ln for ln, _why in aw_gone] + [it.full for it in aw_rest])
    for key in ARCHIVE_ONLY:
        to_archive(key, [ln for ln in _live(_body(secs, key))
                         if not _sc.bare(ln).startswith(EMPTY)])
    where_full = [ln for ln in _live(_body(secs, "where"))]
    shown = {it.full for g in today.groups for it in g.taken}
    to_archive("where", [ln for ln in where_full if ln not in shown])
    to_archive("unanswered", [ln for ln in _live(_body(secs, "unanswered"))
                              if not _DIALOG.match(ln.strip()) or not any(
                                  ln == it.full for g in mail.groups for it in g.taken)])
    to_archive("main", [ln for ln in _live(main_body)
                        if ln not in {it.full for it in head_block.groups[1].taken}])

    parts = [title, ""]
    sizes: Dict[str, int] = {"заголовок": len(title) + 2}
    for b in blocks:
        r = render_block(b, now)
        if not r:
            continue
        parts.append(r)
        sizes[b.heading] = len(r) + 1
        if b.key == "head":
            if extras:
                parts.append(extras)
            if persona_text:
                parts.append(persona_text)
    text = "\n".join(parts).rstrip() + "\n"
    body_chars = len(text) - (len(persona_text) + 1 if persona_text else 0)
    arch = render_archive(archive, born=born, seed_path=(
        str(Path(born) / "input" / INPUT_SEED_NAME) if born is not None else ""),
        budget=budget, now=now)
    report = {"budget": budget, "chars": body_chars, "persona": len(persona_text.strip()),
              "bytes": len(text.encode("utf-8")), "blocks": sizes,
              "archive_chars": len(arch), "archive_blocks": {k: len(v) for k, v in archive.items()}}
    return Shaped(text, arch, report)


def extra_sections(distil: str) -> str:
    """Разделы ``## `` вне контракта («первый шаг — ПЛАН НИТИ» у новой нити) — как есть."""
    out: List[str] = []
    keep = False
    known = [contract.names_of(b) for b in contract.BLOCKS]
    for ln in (distil or "").splitlines():
        m = _HEAD.match(ln)
        if m:
            h = contract.norm(m.group(1))
            keep = not (any(h.startswith(n) for names in known for n in names)
                        or h.startswith(contract.TAILS_NAME))
        if keep:
            out.append(ln)
    return "\n".join(out).strip()


def render_archive(archive: Dict[str, List[str]], *, born: Optional[Path], seed_path: str,
                   budget: int, now: datetime) -> str:
    """Архив сида: блоки контракта (канонические имена — их читают приём и аудит),
    в каждом — что не попало в сид целиком."""
    filled = {k: v for k, v in archive.items() if any(
        ln.strip() and not ln.startswith("(") for ln in v)}
    if not filled:
        return ""
    head = ["# архив сида{0}".format(" — " + Path(born).name if born is not None else ""), "",
            "Собран {0}. Всё, что не влезло в сид (бюджет {1} знаков) или в теле сида только "
            "ссылкой: решения, задачи Linear, полная «где мы», сырой журнал. Сид: {2}".format(
                now.strftime("%d.%m %H:%M"), budget, seed_path or "handoff-seed.md рядом"), ""]
    order = [b.name for b in contract.BLOCKS] + [contract.TAILS_NAME]
    body: List[str] = []
    for name in sorted(filled, key=lambda n: order.index(n) if n in order else 99):
        body += ["## " + name, ""] + list(dict.fromkeys(filled[name])) + [""]
    return "\n".join(head + body).rstrip() + "\n"


def fit_free(text: str, budget: int, persona: str = "") -> Tuple[str, str]:
    """Свободный текст передачи (не блоки контракта) в бюджете: разделы ``## `` с конца
    уходят в архив целиком, пока не влезет; первый раздел остаётся всегда."""
    limit = budget + len(persona)
    if len(text) <= limit:
        return text, ""
    parts = re.split(r"(?m)^(?=## )", text)
    kept, gone = parts[:1], []
    for p in parts[1:]:
        if len("".join(kept)) + len(p) <= limit:
            kept.append(p)
        else:
            gone.append(p)
    return "".join(kept), "".join(gone)


def archive_path(session_born: Path) -> Path:
    return Path(session_born) / "input" / ARCHIVE_NAME


def read_archive(seed_path: Path) -> str:
    """Архив рядом с сидом (``input/seed-archive.md``) или ""."""
    try:
        return (Path(seed_path).parent / ARCHIVE_NAME).read_text(encoding="utf-8")
    except OSError:
        return ""


__all__ = ["ARCHIVE_NAME", "ARCHIVE_ONLY", "AWAITING_MAX", "Block", "Clock", "EXPECTATION_MAX",
           "Group", "Head", "Item", "LAYOUT", "MANAGER_BUDGET", "ROLE_BUDGET", "Shaped",
           "archive_path", "await_core", "strip_await_tag", "awaiting_items", "budget_for", "build", "clean",
           "decisions_items", "fit_free", "is_contract_seed", "is_manager_thread",
           "is_real_grisha", "mail_items", "main_items", "older_awaiting", "quote_key",
           "read_archive", "render_archive", "soc_items", "strip_stamp", "today_items",
           "words_items"]

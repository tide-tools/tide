"""tide.launcher.seed_compress — чистка оперативки сида и потолок (SOC-181, SOC-179).

Слово Гриши через менеджера (письма 046/053): «Сжимаем опыт? — Да», «Давай начнём с тебя».
Аудит передач (management 18-main-18/output/handoff-audit.md): сид main вырос с 7 до 83 КБ,
потому что сборщик копил, а не чистил. Здесь — правила чистки, общие для всех нитей:

* статус заменяется, а не дописывается: хвост Linear и «(из прошлого сида)» — один на строку
  (:func:`strip_status_tails`);
* опыт: один урок, записанный разными словами (слой нити с датой, метка пульса, прошлый
  сид), — одна строка (:func:`same_lesson`); в сид — свежее за сутки и не больше
  :data:`~tide.handoff_contract.EXPERIENCE_TOP` самых частых старых, остальное — ссылкой
  на слой нити; грабли из общего файла (:func:`tide.grisha_decisions.gotchas_file`) не копируются;
* «ждёт Гришу»: отвеченное (вопрос есть в журнале решений Гриши, или в пункте «ответил»)
  выкидывается, на диске остаётся; закрытое в Linear — тоже (сводная строка «снято из сида»);
* неотвеченные реплики — только реплики Гриши, на которые после него никто не ответил;
* окружение старше суток не переносится (возраст строки — первый сид нити, где она была);
* личность нити (``persona.md`` в корне нити) — отдельным блоком после правил, ничего не
  заменяет (поправка Гриши 054: «личность дополнительна, нужна для креативных задач»);
* потолок готового сида :data:`~tide.handoff_contract.SEED_MAX_BYTES` (:func:`cap_seed`).

Модуль не импортирует ``seed_draft`` на уровне модуля (тот зовёт сюда) и ничего не пишет.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from .. import handoff_contract as contract

EMPTY = contract.EMPTY
SEED_MAX_BYTES = contract.SEED_MAX_BYTES

# --- хвосты статуса ------------------------------------------------------------

_ISSUE = r"[A-Z][A-Z0-9]{1,9}-\d+"
# «(в Linear: SOC-156 — In Review)», «(done — в Linear: SOC-106 — Done)» (mark_decision_linear)
_LINEAR_TAIL = re.compile(r"\s*\((?:done — )?в Linear: [^()]*\)")
# «(SOC-57 — Done в Linear)», «(SOC-1 — Done в Linear; SOC-2 — Canceled в Linear)» (mark_closed)
_CLOSED_TAIL = re.compile(r"\s*\({0} — [^();]*? в Linear(?:;\s*{0} — [^();]*? в Linear)*\)".format(
    _ISSUE))
INHERITED_MARK = "(из прошлого сида)"
_INHERITED = re.compile(r"\s*\(из прошлого сида\)")


def strip_linear_tails(line: str) -> str:
    """Строка без статусов Linear, дописанных прошлыми сборками (их бывало ×10)."""
    s = _CLOSED_TAIL.sub("", _LINEAR_TAIL.sub("", line or ""))
    return s.rstrip()


def strip_status_tails(line: str) -> str:
    """Строка без статусов Linear и без голых пометок «(из прошлого сида)» — где бы они ни
    стояли. Пометка «(из прошлого сида; снято: …)» несёт смысл и остаётся."""
    return _INHERITED.sub("", strip_linear_tails(line)).rstrip()


# --- уроки: нормализация и сходство -------------------------------------------

# «11.10 02:02 — », «10.10 ~00:30 −03: », «09.10 ~20:00–22:40 −03: », «10.10: », «10.10 — »
_STAMP_HEAD = re.compile(
    r"^(?P<d>\d{1,2})\.(?P<m>\d{2})(?:\.\d{2,4})?"
    r"(?:\s+~?(?P<H>\d{1,2}):(?P<M>\d{2})(?:\s*[–-]\s*\d{1,2}:\d{2})?)?"
    r"(?:\s*[−-]\d{2})?\s*(?:—|:)\s*")
_LIST = re.compile(r"^\s*[-*]\s+")
_PULSE_STAMP = re.compile(r"\s*\(пульс \d{1,2}:\d{2}\)")
_TIME_PAREN = re.compile(r"\((?:[^()]*?\d{1,2}:\d{2}[^()]*?)\)")
_QUOTES = re.compile(r"[«»„“”\"'`]")
# Служебные слова — не ключевые: по ним разные уроки не склеиваются.
_STOP = frozenset((
    "только", "это", "что", "как", "для", "все", "всё", "при", "или", "его", "уже", "так",
    "тоже", "если", "когда", "чтобы", "где", "там", "тут", "нет", "есть", "был", "была",
    "было", "быть", "они", "она", "оно", "мне", "нам", "нас", "себя", "свой", "свои",
    "своей", "этот", "эта", "эти", "тот", "той", "том", "там", "сам", "сама", "сразу",
    "надо", "нужно", "можно", "the", "and", "not", "for", "with"))
STEM = 5               # ключ слова — первые 5 букв: «закрывать/закрытие» → «закры»
LESSON_SHARE = 0.6     # доля ключей меньшей строки в большей — тот же урок
LESSON_MIN = 3         # меньше общих ключей — не склеиваем (короткие строки — только дословно)


def bare(line: str) -> str:
    return _LIST.sub("", line or "").strip()


def line_when(line: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """Дата в начале строки слоя («11.10 02:02 — …») → datetime; нет даты — None."""
    m = _STAMP_HEAD.match(bare(line))
    if not m:
        return None
    now = now or datetime.now()
    try:
        d, mo = int(m.group("d")), int(m.group("m"))
        hh = int(m.group("H")) if m.group("H") else 12
        mm = int(m.group("M")) if m.group("M") else 0
        at = datetime(now.year, mo, d, hh, mm)
    except ValueError:
        return None
    if at > now + timedelta(days=1):
        at = at.replace(year=at.year - 1)
    return at


def lesson_text(line: str) -> str:
    """Строка для сравнения: без маркера, даты, «(пульс ЧЧ:ММ)», статусов, кавычек, регистра."""
    s = bare(line)
    s = _STAMP_HEAD.sub("", s)
    s = strip_status_tails(_PULSE_STAMP.sub("", s))
    s = _TIME_PAREN.sub(" ", s)
    s = _QUOTES.sub(" ", s.lower().replace("ё", "е"))
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


def stems(line: str) -> Set[str]:
    return {w[:STEM] for w in lesson_text(line).split()
            if len(w) >= 3 and w not in _STOP and not w.isdigit()}


def same_lesson(a: Set[str], b: Set[str]) -> bool:
    """Один урок: общих ключей не меньше :data:`LESSON_MIN` и доли :data:`LESSON_SHARE`
    от меньшей строки."""
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    if len(small) < LESSON_MIN:
        return bool(small) and small == big
    return len(small & big) >= max(LESSON_MIN, math.ceil(LESSON_SHARE * len(small)))


def split_lessons(line: str) -> List[str]:
    """Строка слоя, где через « · » или «; » несколько уроков, → по строке на урок
    (дата — у каждого). « · NN» — часть имени («main · 05»), скобки не режутся."""
    body = bare(line)
    m = _STAMP_HEAD.match(body)
    head, rest = (body[:m.end()], body[m.end():]) if m else ("", body)
    parts: List[str] = []
    depth, start, i = 0, 0, 0
    while i < len(rest):
        ch = rest[i]
        if ch in "(«":
            depth += 1
        elif ch in ")»":
            depth = max(0, depth - 1)
        elif depth == 0:
            cut = 0
            if rest.startswith(" · ", i) and not re.match(r"\d{2}\b|NN\b", rest[i + 3:]):
                cut = 3
            elif ch == ";" and rest[i + 1:i + 2] == " " and not rest[i + 2:].lower().startswith(
                    "предлага"):
                cut = 2
            if cut:
                parts.append(rest[start:i])
                start = i + cut
                i += cut
                continue
        i += 1
    parts.append(rest[start:])
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) <= 1:
        return ["- " + body] if body else []
    return ["- " + head + p for p in parts]


# --- история сидов нити (возраст строки) ---------------------------------------

class SeedHistory:
    """Сиды сессий нити (``arcs/*/input/handoff-seed.md``) по времени: когда строка блока
    впервые попала в сид — её возраст. Только чтение."""

    def __init__(self, thread_entry: Optional[Path]):
        self._seeds: List[Tuple[datetime, Dict[str, List[str]]]] = []
        self._keys: Dict[str, List[Tuple[datetime, Set[str]]]] = {}
        if thread_entry is None:
            return
        from .seed_draft import INPUT_SEED_NAME, sections
        arcs = Path(thread_entry) / "arcs"
        for d in (sorted(arcs.iterdir()) if arcs.is_dir() else []):
            f = d / "input" / INPUT_SEED_NAME
            try:
                at = datetime.fromtimestamp(f.stat().st_mtime)
                secs = sections(f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            self._seeds.append((at, secs))
        self._seeds.sort(key=lambda x: x[0])

    def _index(self, key: str) -> List[Tuple[datetime, Set[str]]]:
        if key not in self._keys:
            self._keys[key] = [(at, {lesson_text(ln) for ln in contract.find_body(secs, key)})
                               for at, secs in self._seeds]
        return self._keys[key]

    def first_seen(self, line: str, key: str) -> Optional[datetime]:
        want = lesson_text(line)
        for at, keys in self._index(key):
            if want in keys:
                return at
        return None


# --- опыт ----------------------------------------------------------------------

OLD_HEAD = "- старше суток — самые частые:"
OLD_HEAD_RE = re.compile(r"^- старше суток — самые частые")
EXP_POINTER = "- (опыт: ещё {0} строк старше суток — полностью: {1})"


def gotcha_lines(path: Optional[Path] = None) -> List[str]:
    """Строки общего файла граблей (``- …``); нет файла — []."""
    from .. import grisha_decisions as _gd
    try:
        text = Path(path or _gd.gotchas_file()).read_text(encoding="utf-8")
    except OSError:
        return []
    return [ln for ln in text.splitlines() if ln.lstrip().startswith("- ")]


def fold_experience(groups: Sequence[Tuple[str, Sequence[str]]], *, now: Optional[datetime] = None,
                    history: Optional[SeedHistory] = None, gotchas: Sequence[str] = (),
                    fresh_hours: int = contract.EXPERIENCE_FRESH_HOURS,
                    top: int = contract.EXPERIENCE_TOP, full: str = "",
                    stats: Optional[Dict[str, int]] = None) -> List[str]:
    """Опыт сида из источников ``[(источник, строки)]`` (``pulse`` — метки этой сессии,
    ``layer`` — слой нити, ``seed`` — входной сид).

    Многоурочная строка режется на уроки (:func:`split_lessons`); урок из общего файла
    граблей выкидывается; один урок разными словами — одна строка (самая полная),
    частота — сколько строк в неё склеилось. Свежие (дата строки, метка этой сессии или
    первый сид с ней — моложе *fresh_hours*) — все, новые сверху; старые — не больше *top*
    самых частых под строкой :data:`OLD_HEAD`; остальное — строкой-ссылкой на *full*."""
    now = now or datetime.now()
    fresh_for = timedelta(hours=fresh_hours)
    items: List[Tuple[str, Set[str], Optional[datetime]]] = []
    for source, lines in groups:
        for ln in lines:
            if not ln.strip() or "<!--" in ln or bare(ln).startswith(EMPTY) \
                    or OLD_HEAD_RE.match(ln.strip()) or bare(ln).startswith("(опыт: ещё"):
                continue
            when = line_when(ln, now)
            if when is None and source != "pulse" and history is not None:
                when = history.first_seen(ln, "experience")
            if when is None:  # метка этой сессии или строка, которой ещё не было в сидах
                when = now
            for part in split_lessons(ln.lstrip()):
                st = stems(part)
                if st:
                    items.append((part, st, when))
    gst = [s for s in (stems(g) for g in gotchas) if s]
    kept = [it for it in items if not any(same_lesson(it[1], g) for g in gst)]
    clusters: List[dict] = []
    for line, st, when in kept:
        hit = next((c for c in clusters if same_lesson(st, c["stems"])), None)
        if hit is None:
            clusters.append({"line": line, "stems": st, "when": when, "n": 1})
            continue
        hit["n"] += 1
        if when is not None and (hit["when"] is None or when > hit["when"]):
            hit["when"] = when
        if len(st) > len(hit["stems"]):
            hit["line"], hit["stems"] = line, st
    fresh = [c for c in clusters if c["when"] is not None and now - c["when"] <= fresh_for]
    old = [c for c in clusters if c not in fresh]
    fresh.sort(key=lambda c: c["when"], reverse=True)
    old.sort(key=lambda c: (-c["n"], -(c["when"].timestamp() if c["when"] else 0)))
    out = [c["line"] for c in fresh]
    if old[:top]:
        out.append(OLD_HEAD)
        out += ["  " + c["line"] for c in old[:top]]
    rest = len(old) - min(len(old), top)
    if rest:
        out.append(EXP_POINTER.format(rest, full or "слой нити, ### опыт"))
    if stats is not None:
        stats.update(raw=len(items), gotchas=len(items) - len(kept),
                     merged=len(kept) - len(clusters), fresh=len(fresh),
                     old_top=min(len(old), top), old_rest=rest)
    return out


# --- ждёт Гришу: отвеченное ----------------------------------------------------

_TG_ANY = re.compile(r"(?:\btg|❓)\s*~?(\d{3,6})(?!\d)", re.IGNORECASE)
_LEAD_NUM = re.compile(r"^(?:❓\s*)?(\d{3,5})(?=\s|$)")
_ANSWERED = re.compile(r"(?:—|\(|,|;)\s*(?:ответил[аи]?|отвечено|ответ дан)\b", re.IGNORECASE)
DROPPED_HEAD = "снято из сида"
_DROPPED_RE = re.compile(r"^-?\s*снято из сида\b")
CAPPED_MARK = "сжато сверх потолка"


def await_nums(item: str) -> Set[str]:
    """Номера сообщений tg пункта: «tg 1356», «❓1356», «1356 ритуал…» в начале."""
    body = bare(item)
    out = set(_TG_ANY.findall(body))
    m = _LEAD_NUM.match(body)
    if m:
        out.add(m.group(1))
    return out


def journal_question_nums(path: Optional[Path] = None) -> Set[str]:
    """Номера наших вопросов, на которые в журнале решений Гриши есть ответ (``вопрос-tg``)."""
    from .. import grisha_decisions as _gd
    try:
        text = Path(path or _gd.decisions_file()).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    out: Set[str] = set()
    for e in _gd.parse(text):
        for n in re.findall(r"\d{3,6}", e.get("вопрос-tg", "") or ""):
            out.add(n)
    return out


def is_answered(item: str, answered_nums: Set[str]) -> bool:
    """Пункт «ждёт Гришу» отвечен: все его номера — в журнале, или в нём «ответил» и он
    про один вопрос."""
    body = bare(item)
    if body.lower().startswith("снято:"):
        return False
    nums = await_nums(body)
    if nums and nums <= answered_nums:
        return True
    return bool(_ANSWERED.search(body)) and len(nums) <= 1


def dropped_line(count: int, refs: Sequence[str], why: str, where: str) -> str:
    """Сводная строка снятого из сида: что снято (номера) и где оно на диске."""
    refs = list(dict.fromkeys(refs))
    return "- {0} ({1}): {2}{3}{4}".format(
        DROPPED_HEAD, why, count, " — " + " · ".join(refs) if refs else "",
        " — на диске: " + where if where else "")


def dropped_refs(block: Sequence[str]) -> Tuple[Set[str], Set[str], bool]:
    """``(SOC-ключи, tg-номера, сжато_потолком)`` из сводных строк блока нового сида."""
    keys: Set[str] = set()
    nums: Set[str] = set()
    capped = False
    for ln in block:
        if _DROPPED_RE.match(ln.strip()):
            keys |= set(re.findall(_ISSUE, ln))
            nums |= set(_TG_ANY.findall(ln))
        if CAPPED_MARK in ln:
            capped = True
    return keys, nums, capped


def refs_of(item: str) -> Tuple[Set[str], Set[str]]:
    return set(re.findall(_ISSUE, item or "")), await_nums(item)


# --- неотвеченные реплики --------------------------------------------------------

_DIALOG_LINE = re.compile(r"^- (\S+) · ([^·]+?) · (\S+) · (.*)$")
_RELAY = re.compile(r"^\[from [\w.-]+\]")


def only_unanswered(lines: Sequence[str], dialog: str = "", human: str = "Гриша",
                    clip: int = 120) -> List[str]:
    """Хвост диалога → реплики Гриши, после которых никто не ответил; отвеченное —
    одной строкой со ссылкой на журнал. Не строки диалога — как есть."""
    parsed = [(ln, _DIALOG_LINE.match(ln.strip())) for ln in lines]
    if not any(m for _ln, m in parsed):
        return list(lines)
    # «[from management-28] почта 046 …» под именем Гриши — пересылка сессии, не его слово:
    # почта нити и так в «в работе прямо сейчас»
    relays = sum(1 for _ln, m in parsed if m and _RELAY.match(m.group(4)))
    parsed = [(ln, m) for ln, m in parsed if not (m and _RELAY.match(m.group(4)))]
    last_agent = max((i for i, (_ln, m) in enumerate(parsed)
                      if m and m.group(2).strip() != human), default=-1)
    keep = [ln for i, (ln, m) in enumerate(parsed)
            if m and m.group(2).strip() == human and i > last_agent]
    answered = [m for i, (_ln, m) in enumerate(parsed)
                if m and m.group(2).strip() == human and i < last_agent]
    relay_note = "; пересылок сессий снято: {0}".format(relays) if relays else ""
    if not answered:
        if keep:
            return keep + (["- (" + relay_note[2:] + ")"] if relays else [])
        if relays:
            return ["{0} реплик Гриши в хвосте нет{1}{2}".format(
                EMPTY, relay_note, " — весь диалог: " + dialog if dialog else "")]
        return list(lines)
    last = answered[-1]
    quote = " ".join(last.group(4).split())
    if len(quote) > clip:
        quote = quote[:clip - 1].rstrip() + "…"
    when = last.group(1)[11:16] if len(last.group(1)) >= 16 else last.group(1)
    tail = "отвечено и снято из сида: {0} (последняя {1} «{2}»){3}{4}".format(
        len(answered), when, quote, relay_note, " — весь диалог: " + dialog if dialog else "")
    if keep:
        return keep + ["- " + tail]
    return ["{0} без ответа реплик нет; {1}".format(EMPTY, tail)]


# --- окружение -------------------------------------------------------------------

def fresh_environment(lines: Sequence[str], history: Optional[SeedHistory],
                      now: Optional[datetime] = None,
                      hours: int = contract.ENVIRONMENT_MAX_HOURS) -> List[str]:
    """Строки окружения моложе *hours* (по первому сиду нити, где строка была); старое
    не переносится — одной строкой «— окружение старше суток…»."""
    if history is None:
        return list(lines)
    now = now or datetime.now()
    keep, old, first = [], 0, None
    for ln in lines:
        at = history.first_seen(ln, "environment")
        if at is not None and now - at > timedelta(hours=hours):
            old += 1
            first = at if first is None or at < first else first
            continue
        keep.append(ln)
    if old and not keep:
        return ["{0} окружение старше суток не переносится ({1} строк, с {2}) — сверь "
                "вживую".format(EMPTY, old, first.strftime("%d.%m %H:%M"))]
    return keep


# --- личность нити ------------------------------------------------------------

PERSONA_FILE = "persona.md"
PERSONA_HEAD = "## личность — для творческих задач"


def persona_block(thread_entry: Path) -> str:
    """Личность нити (``<корень нити>/persona.md``) целиком — блоком для сида; нет файла
    или он пуст — "". Заголовки файла опущены на два уровня, чтобы его «## …» не
    читались блоками сида."""
    p = Path(thread_entry) / PERSONA_FILE
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not text.strip():
        return ""
    body = re.sub(r"(?m)^(#{1,4})(?=\s)", lambda m: "#" * (len(m.group(1)) + 2), text.strip())
    return "{0}\n\nдополняет правила выше и ничего в них не заменяет · файл: {1}\n\n{2}".format(
        PERSONA_HEAD, p, body)


# --- потолок ---------------------------------------------------------------------

_HEAD = re.compile(r"^##\s+(.+?)\s*$")
_URL = re.compile(r"\s—\s(https?://\S*?/)({0})(?=\s|$)".format(_ISSUE))


class _Chunk:
    def __init__(self, head: str, lines: List[str]):
        self.head = head
        self.lines = lines
        self.key = ""
        h = contract.norm(head[3:]) if head else ""
        for b in contract.BLOCKS:
            if any(h.startswith(n) for n in contract.names_of(b)):
                self.key = b.key
                break
        if h.startswith(contract.TAILS_NAME):
            self.key = "tails"
        self.inherited = [INHERITED_MARK in ln for ln in lines]


def _parse(text: str) -> List[_Chunk]:
    out = [_Chunk("", [])]
    for ln in text.split("\n"):
        if _HEAD.match(ln):
            out.append(_Chunk(ln, []))
        else:
            out[-1].lines.append(ln)
    for c in out:
        c.inherited = [INHERITED_MARK in ln for ln in c.lines]
    return out


def _render(chunks: Sequence[_Chunk]) -> str:
    parts = []
    for c in chunks:
        if c.head:
            parts.append(c.head)
        parts += c.lines
    return "\n".join(parts)


def _size(chunks: Sequence[_Chunk]) -> int:
    return len(_render(chunks).encode("utf-8"))


def _body(c: _Chunk) -> List[int]:
    return [i for i, ln in enumerate(c.lines) if ln.strip()]


def _set(c: _Chunk, lines: List[str], inherited: Optional[List[bool]] = None) -> None:
    lead = [""] if c.head else []
    c.lines = lead + lines + [""]
    c.inherited = [False] * len(lead) + (inherited or [INHERITED_MARK in ln for ln in lines]) + [False]


def _items(c: _Chunk) -> Tuple[List[str], List[bool]]:
    idx = _body(c)
    return [c.lines[i] for i in idx], [c.inherited[i] for i in idx]


def cap_seed(text: str, *, limit: int = SEED_MAX_BYTES, passport: str = "", prev_seed: str = "",
             goal_file: str = "", dialog: str = "", decisions_file: str = "",
             now: Optional[datetime] = None, report: Optional[List[str]] = None) -> str:
    """Готовый сид не больше *limit* байт: режет по порядку, пока не влезет —
    журнал → повторные хвосты → старый опыт → закрытые Linear одной строкой → реплики
    (только слова Гриши) → решения → старое ожидание. Нить, личность, правила, главное,
    свежее ожидание, где мы, хвосты, ждёт Гришу, следующий шаг, в работе — не трогает.
    Каждый срез оставляет строку, где полное. *report* — список, куда пишутся шаги."""
    if len(text.encode("utf-8")) <= limit:
        return text
    now = now or datetime.now()
    chunks = _parse(text)
    by = {c.key: c for c in chunks if c.key}
    log = report if report is not None else []

    def over() -> bool:
        return _size(chunks) > limit

    def step(name: str, fn: Callable[[], None]) -> bool:
        before = _size(chunks)
        fn()
        log.append("{0}: {1} → {2} Б".format(name, before, _size(chunks)))
        return not over()

    def journal() -> None:
        c = by.get("journal")
        if c and len(_body(c)) > 1:
            _set(c, ["- сырой журнал — на диске: {0}, ## context ({1})".format(
                passport or "паспорт прошлой сессии", CAPPED_MARK)])

    def tails() -> None:
        for c in chunks:
            if not c.key:
                continue
            for i, ln in enumerate(c.lines):
                if INHERITED_MARK in ln or _LINEAR_TAIL.search(ln) or _CLOSED_TAIL.search(ln):
                    keep_status = _CLOSED_TAIL.findall(ln)[-1:] or _LINEAR_TAIL.findall(ln)[-1:]
                    s = strip_status_tails(ln)
                    c.lines[i] = s + (keep_status[0] if keep_status else "")

    def old_experience() -> None:
        c = by.get("experience")
        if not c:
            return
        lines, inh = _items(c)
        out, dropped, skip = [], 0, False
        for ln in lines:
            if OLD_HEAD_RE.match(ln.strip()):
                skip = True
                continue
            if skip and ln.startswith("  "):
                dropped += 1
                continue
            skip = False
            m = re.match(r"^- \(опыт: ещё (\d+) строк", ln)
            if m:
                dropped += int(m.group(1))
                continue
            out.append(ln)
        if dropped:
            out.append("- (опыт: ещё {0} строк старше суток — {1}; полностью: {2})".format(
                dropped, CAPPED_MARK, goal_file or "слой нити, ### опыт"))
            _set(c, out)

    def linear() -> None:
        c = by.get("linear")
        if not c:
            return
        lines, _inh = _items(c)
        out, closed, url = [], [], ""
        group = ""
        for ln in lines:
            if ln.startswith("- закрыты в Linear") or ln.startswith("- ведут другие нити"):
                group = ln
                continue
            m = _URL.search(ln)
            if m:
                url = url or m.group(1)
                ln = _URL.sub("", ln)
            if ln.startswith("  - ") and group:
                closed.append(re.findall(_ISSUE, ln)[0] if re.findall(_ISSUE, ln) else ln.strip())
                continue
            group = ""
            if "в Linear не найдена" in ln:
                closed.append(re.findall(_ISSUE, ln)[0])
                continue
            out.append(ln)
        if closed:
            out.append("- закрыты, чужие или не найдены ({0}): {1}".format(
                len(closed), " · ".join(closed)))
        if url:
            out.append("- ссылка на задачу: {0}<SOC-N>".format(url))
        _set(c, out)

    def unanswered() -> None:
        c = by.get("unanswered")
        if not c:
            return
        lines, _inh = _items(c)
        out = []
        for ln in lines:
            m = _DIALOG_LINE.match(ln.strip())
            if m and m.group(2).strip() != "Гриша":
                continue
            if not m and len(ln) > 200:
                ln = ln[:199] + "…"
            out.append(ln)
        _set(c, out)

    def decisions() -> None:
        c = by.get("decisions")
        if not c:
            return
        lines, inh = _items(c)
        out = [ln for ln, i in zip(lines, inh) if not i]
        cut = len(lines) - len(out)
        where = "{0}{1}".format(prev_seed or "входной сид прошлой сессии",
                                ", " + decisions_file if decisions_file else "")
        if cut:
            _set(c, out + ["- (решений из прошлых сидов: {0} — {1}; полностью: {2})".format(
                cut, CAPPED_MARK, where)])
        if over():
            # остаются решения нити (decisions.md, «NN — …»); пульсовые — ссылкой
            thread = [ln for ln in out if re.match(r"^-\s+\d{2}\s+—", ln)]
            pulses = len(out) - len(thread)
            if pulses:
                _set(c, thread + ["- (решений из прошлых сидов: {0}, пульсов: {1} — {2}; "
                                  "полностью: {3}{4})".format(
                                      cut, pulses, CAPPED_MARK, where,
                                      ", " + passport if passport else "")])

    def old_expectation() -> None:
        # свежее — строка с датой моложе суток; без даты — старое (слой с первых дней нити)
        c = by.get("expectation")
        if not c:
            return
        lines, inh = _items(c)
        out, cut = [], 0
        for ln in lines:
            at = line_when(ln, now)
            if bare(ln).startswith("(") or (
                    at is not None and now - at <= timedelta(hours=contract.EXPERIENCE_FRESH_HOURS)):
                out.append(ln)
                continue
            cut += 1
        if cut:
            out.append("- (ожидание старше суток: {0} строк — {1}; полностью: {2})".format(
                cut, CAPPED_MARK, goal_file or "слой нити, ### ожидание Гриши"))
            _set(c, out)

    def fresh_experience() -> None:
        # за порядком задачи: свежий опыт сверх EXPERIENCE_TOP новейших — ссылкой
        c = by.get("experience")
        if not c:
            return
        lines, _inh = _items(c)
        lessons = [ln for ln in lines if not bare(ln).startswith(("(", "кандидат"))
                   and EMPTY not in ln[:4]]
        extra = lessons[contract.EXPERIENCE_TOP:]
        if extra:
            out = [ln for ln in lines if ln not in extra]
            out.append("- (свежий опыт сверх {0} строк: {1} — {2}; полностью: {3})".format(
                contract.EXPERIENCE_TOP, len(extra), CAPPED_MARK,
                goal_file or "слой нити, ### опыт"))
            _set(c, out)

    def entry_map() -> None:
        c = by.get("entry_map")
        if not c:
            return
        lines, _inh = _items(c)
        # подробности топика («  - топик: …») — в самом топике, ссылка на него остаётся
        _set(c, [ln if len(ln) <= 200 else ln[:199] + "…" for ln in lines
                 if not ln.startswith("  - топик:")])

    def open_linear() -> None:
        c = by.get("linear")
        if not c:
            return
        lines, _inh = _items(c)
        opened = [ln for ln in lines if re.match(r"^- {0} — ".format(_ISSUE), ln)]
        if len(opened) > 1:
            rest = [ln for ln in lines if ln not in opened]
            one = "- открытые: " + " · ".join(ln[2:].replace(" — ", " ", 1) for ln in opened)
            _set(c, rest[:1] + [one] + rest[1:])

    # Порядок среза — слово Гриши (письмо 053/054); за ним — блоки вне списка
    # «не резать»: свежий опыт, карта входа, открытые задачи одной строкой.
    for name, fn in (("журнал", journal), ("повторные хвосты", tails),
                     ("старый опыт", old_experience), ("закрытые Linear", linear),
                     ("реплики", unanswered), ("решения", decisions),
                     ("старое ожидание", old_expectation),
                     ("свежий опыт", fresh_experience), ("карта входа", entry_map),
                     ("открытые Linear", open_linear)):
        if step(name, fn):
            break
    return _render(chunks)


__all__ = ["CAPPED_MARK", "DROPPED_HEAD", "INHERITED_MARK", "OLD_HEAD", "PERSONA_FILE",
           "PERSONA_HEAD", "SEED_MAX_BYTES", "SeedHistory", "await_nums", "cap_seed",
           "dropped_line", "dropped_refs", "fold_experience", "fresh_environment",
           "gotcha_lines", "is_answered", "journal_question_nums", "line_when",
           "lesson_text", "only_unanswered", "persona_block", "refs_of", "same_lesson",
           "split_lessons", "stems", "strip_linear_tails", "strip_status_tails"]

"""tide.truth — «что сказал человек» по темам: поиск для агентов, только чтение.

Темы проекта лежат в ``<корень>/.tide/topics/NN-slug/``: ``topic.md`` отвечает
«как дела», рядом ``facts.md`` — «что сказал и хочет человек»: шапка (``сверено:``),
секции «Сейчас» / «Открыто», «Решения» и «Факты (новые сверху)» строками
``- факт · дата · ссылки``. Счётчик «фактов N» — строки-пункты всех секций,
кроме служебных (:data:`SERVICE_SECTIONS`); решения — ещё и отдельно. Строки с «заменено →» — история: на первом уровне
скрыты, видно только их число (``--all`` показывает всё).

Три входа (контекст-кипер, интерфейс контекста, решение 3):

    tide truth                         список тем всех проектов ростера
    tide truth <тема>                  topic.md целиком, потом «Сейчас»/«Открыто» и факты
    tide truth <проект>                «где мы» / «в полёте» / «горит» каждой темы проекта
    tide truth [<тема>] --grep слова   поиск живыми словами, с адресом файл:строка
    tide truth check [--project P]     сверка журналов решений decisions-tg.jsonl
                                       с «## Решения» (:mod:`tide.decisions_tg`)
    tide truth inbox [--since D]       ответы человека в TG, у которых нет решения
    tide truth decide --topic … --q N --a M --decision "…"   запись решения
                                       (--no-question --note "…" — ответ без вопроса)
    tide truth skip --q N --a M --reason "…"   пара сознательно без решения
    tide truth baseline [--project P]  стартовая строка журнала правок тем
                                       (edits.jsonl, :mod:`tide.truth_edits`)
    tide truth tg [проект|slug|проект/нить] [--limit N]   тема Telegram проекта:
                                       ответы Гриши и открытые вопросы (:mod:`tide.truth_tg`)

Поиск живыми словами: запрос режется на слова, служебные («что», «где», «по»…)
выбрасываются, каждое слово сводится к основе (``передача`` → ``переда``) и ищется
подстрокой (короткая — с начала слова). Строки ранжируются по числу совпавших
основ; «все слова» не требуются — в выдачу идут строки, где совпала хотя бы
половина основ и не больше чем на одну меньше, чем у лучшей строки
(:func:`need_words`); строки с меньшим совпадением идут ниже под пометкой
«совпало k из n». Слово с цифрой (``SOC-102``) —
идентификатор, обязательно всегда. Имя проекта ростера в запросе — фильтр
``--project``; слова из заголовка темы сужают поиск до неё. Служебные строки
(заголовки, шапка, таблицы ссылок) и заменённые факты в выдачу не идут.

Свежесть: тема без даты сверки — «[не сверено]», ``status: stale`` —
«[устарело]»; такие строки идут после свежих. Тему, которую соседняя тема
прямо велит «не читать», и ``status: archived`` — скрыты везде, видны с ``--all``.

Строка, где нить «ведёт» (``нить board (29-@board)``, ``board · 02``), получает
пометку ``→ сейчас: board · 03, tide-stack-23 (жива)`` — держателя нити из
:func:`tide.arc.holder.holder` (то же, что ``tide thread holder``).

Охват — проекты ростера (активные), темы ищутся в КОРНЕ проекта из ростера, а не
в cwd: ``.tide/topics`` не в git, в AO-worktree её нет. Модуль ничего не пишет.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import paths
from .arc.stream import StreamError
from .roster import STATUS_ARCHIVED, read_roster

TOPICS_DIRNAME = "topics"
TOPIC_FILE = "topic.md"
FACTS_FILE = "facts.md"
DEFAULT_LIMIT = 30
REPLACED_MARK = "заменено →"
EMPTY_HINT = "меньше слов · tide truth <тема> · вне тем ищи в Linear (SOC-N) и git log --grep"

_NUM_RE = re.compile(r"^(\d+)-")
_FIELD_RE = re.compile(r"^([^\s:][^:]{0,30}):\s*(.*)$")
_CYR_RE = re.compile(r"^[а-я]+$")

# служебные слова вопроса — короткий список, без них остаются слова по делу
STOPWORDS = frozenset("""
что кто где как чего когда ли по о об обо в во на и а от для про у с со к ко
это мне меня мой моя я ты он она оно мы вы они его ее их же бы не ни из за до
то так там тут или но есть был была было были будет какой какая какое какие
зачем почему чем кому куда сейчас ли
""".split())

# ключи шапки, которые — метаданные, а не ответ (сравнение в нижнем регистре, ё→е)
META_KEYS = frozenset({
    "title", "kind", "status", "created", "threads", "decision", "updated",
    "тема", "сверено", "сверяли", "дата сверки", "проверено", "фактов", "откуда",
    "граница", "как читать", "работали",
})
CHECKED_KEYS = ("сверено", "сверяли", "дата сверки")
STALE_STATUSES = frozenset({"stale", "outdated", "устарело", "устарел", "устарела"})
ARCHIVED_STATUSES = frozenset({"archived", "архив", "в архиве"})

# свежесть темы → метка в выдаче; порядок — для сортировки (свежие первыми)
FRESH_LABEL = {"": "", "unchecked": "[не сверено]", "stale": "[устарело]",
               "skip": "[устарело]", "archived": "[архив]"}
FRESH_RANK = {"": 0, "unchecked": 1, "stale": 1, "skip": 2, "archived": 2}
HIDDEN = ("skip", "archived")
# секции facts.md, чьи пункты — не факты (справка для читателя)
SERVICE_SECTIONS = ("как читать", "ссылки", "источники")


class TruthError(StreamError):
    """Понятная ошибка верба (нет такой темы/проекта) — cli печатает ``tide: …``."""


def norm(s: str) -> str:
    return s.lower().replace("ё", "е")


# --- где искать -------------------------------------------------------------

def projects(home: Path, only: Optional[str] = None) -> List[Tuple[str, Path]]:
    """``[(имя, корень)]`` активных проектов ростера *home*, у которых корень есть.

    Корень — путь из ростера (не cwd). *only* сужает до одного имени; неизвестное
    имя — :class:`TruthError`.
    """
    out: List[Tuple[str, Path]] = []
    entries = read_roster(home)
    for e in entries:
        name = e.get("name", "")
        if only is not None and name != only:
            continue
        if only is None and e.get("status") == STATUS_ARCHIVED:
            continue
        root = Path(e.get("path", "")).expanduser()
        if root.is_dir():
            out.append((name, root))
    if only is not None and not out:
        names = ", ".join(e.get("name", "") for e in entries) or "ростер пуст"
        raise TruthError("truth: нет проекта «{0}» в ростере ({1})".format(only, names))
    return out


def _home_or_none() -> Optional[Path]:
    try:
        return paths.control_home()
    except FileNotFoundError:
        return None


def resolve_projects(only: Optional[str] = None,
                     home: Optional[Path] = None) -> List[Tuple[str, Path]]:
    """Проекты для поиска: ростер дома; без дома — корень текущего проекта.

    Без control-home cwd всё равно приводится к главному дереву
    (:func:`paths.find_tide_root` сводит AO-worktree к main), так что поиск идёт
    в корне, а не в worktree.
    """
    home = home if home is not None else _home_or_none()
    if home is not None:
        return projects(home, only)
    root = paths.find_tide_root()
    if root is None:
        raise TruthError("truth: нет ни дома с ростером ($TIDE_HOME), ни .tide/ рядом")
    return [(root.name, root)]


# --- разбор файлов ----------------------------------------------------------

def _read_lines(path: Path) -> List[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _header_field(lines: List[str], key: str) -> str:
    """Значение ``key:`` из шапки (до первой ``## ``-секции), или ``""``."""
    for ln in lines:
        if ln.startswith("## "):
            break
        m = _FIELD_RE.match(ln)
        if m and norm(m.group(1).strip()) == key:
            return m.group(2).strip()
    return ""


def _checked_date(lines: List[str]) -> str:
    """Дата сверки из любого места файла (``сверено:``/``сверяли:``) — только с цифрой."""
    for ln in lines:
        m = _FIELD_RE.match(ln)
        if m and norm(m.group(1).strip()) in CHECKED_KEYS:
            val = m.group(2).strip()
            if any(c.isdigit() for c in val):
                return val
    return ""


def _dont_read(lines: List[str]) -> List[Tuple[int, str]]:
    """Строки ``не читать: …`` темы — ``[(номер, значение)]``."""
    out = []
    for i, ln in enumerate(lines, 1):
        m = _FIELD_RE.match(ln)
        if m and norm(m.group(1).strip()) == "не читать":
            out.append((i, m.group(2).strip()))
    return out


def meta_lines(lines: List[str], is_topic: bool) -> set:
    """Номера служебных строк: заголовки, таблицы, цитаты-пометки, шапка-метаданные.

    В topic.md ещё и фронтматтер — первый блок ``ключ:`` сразу под заголовком
    (или ``---``-блок в начале файла).
    """
    meta = set()
    in_yaml = False
    front = False  # идём по первому блоку полей topic.md
    seen_heading = False
    prev_meta_field = False
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if i == 1 and s == "---":
            in_yaml = True
            meta.add(i)
            continue
        if in_yaml:
            meta.add(i)
            if s == "---":
                in_yaml = False
            continue
        if not s:
            if front:
                front = False
            prev_meta_field = False
            continue
        if s.startswith(("#", "|", ">")):
            meta.add(i)
            if is_topic and not seen_heading and s.startswith("# "):
                seen_heading = True
                front = True
            prev_meta_field = False
            continue
        if front:
            meta.add(i)
            continue
        m = _FIELD_RE.match(ln)
        if m:
            prev_meta_field = norm(m.group(1).strip()) in META_KEYS
            if prev_meta_field:
                meta.add(i)
            continue
        if prev_meta_field and ln[:1].isspace():
            meta.add(i)  # продолжение служебного поля
            continue
        prev_meta_field = False
    return meta


def parse_facts(lines: List[str]) -> Dict[str, List[Tuple[int, str]]]:
    """Секции facts.md → ``{"now", "open", "decisions", "facts", "other"}: [(номер, строка)]``.

    Берутся только строки-пункты (``- …``); номера строк — с единицы. «other» —
    пункты прочих несервисных секций; пункты служебных секций и шапки не берутся.
    """
    out: Dict[str, List[Tuple[int, str]]] = {"now": [], "open": [], "decisions": [],
                                             "facts": [], "other": []}
    cur: Optional[str] = None
    for i, ln in enumerate(lines, 1):
        if ln.startswith("## "):
            head = ln[3:].strip().lower()
            if head.startswith("сейчас"):
                cur = "now"
            elif head.startswith("открыто"):
                cur = "open"
            elif head.startswith("факты"):
                cur = "facts"
            elif head.startswith("решения"):
                cur = "decisions"
            elif head.startswith(SERVICE_SECTIONS):
                cur = None
            else:
                cur = "other"
            continue
        if cur and ln.startswith("- "):
            out[cur].append((i, ln))
    return out


def _is_replaced(line: str) -> bool:
    return REPLACED_MARK in line


def count_points(sections: Dict[str, List[Tuple[int, str]]]) -> int:
    """Все строки-пункты facts.md, кроме служебных секций (решения — тоже)."""
    return sum(len(v) for v in sections.values())


def topic_info(project: str, tdir: Path) -> Dict:
    """Сводка одной темы: проект, slug, номер, заголовок, факты, сверка, статус."""
    topic_lines = _read_lines(tdir / TOPIC_FILE)
    facts_path = tdir / FACTS_FILE
    has_facts = facts_path.is_file()
    facts_lines = _read_lines(facts_path) if has_facts else []
    sections = parse_facts(facts_lines)
    m = _NUM_RE.match(tdir.name)
    checked = _checked_date(facts_lines) or _checked_date(topic_lines)
    status = norm(_header_field(topic_lines, "status"))
    if status in ARCHIVED_STATUSES:
        fresh = "archived"
    elif status in STALE_STATUSES:
        fresh = "stale"
    elif not checked:
        fresh = "unchecked"
    else:
        fresh = ""
    return {
        "project": project,
        "slug": tdir.name,
        "num": int(m.group(1)) if m else None,
        "title": _header_field(topic_lines, "title"),
        "dir": str(tdir),
        "topic": str(tdir / TOPIC_FILE) if topic_lines else "",
        "facts_file": str(facts_path) if has_facts else "",
        "facts": count_points(sections),
        # действующие решения — без «заменено →»; так же считает tide truth check
        "decisions": sum(1 for _, ln in sections["decisions"] if not _is_replaced(ln)),
        "replaced": sum(1 for _, ln in sections["facts"] if _is_replaced(ln)),
        "checked": checked,
        "status": status,
        "fresh": fresh,
        "flag": "",
        "_dont_read": _dont_read(topic_lines),
    }


def _mark_dont_read(topics: List[Dict]) -> None:
    """Тема, которую соседняя тема велит «не читать» (slug в поле), — fresh=skip."""
    for t in topics:
        for i, val in t["_dont_read"]:
            low = norm(val)
            for u in topics:
                if u is t:
                    continue
                full = norm("{0}/{1}".format(u["project"], u["slug"]))
                same = u["project"] == t["project"] and norm(u["slug"]) in low
                if same or full in low:
                    if u["fresh"] != "archived":
                        u["fresh"] = "skip"
                    u["flag"] = "«не читать» в {0}/{1}/topic.md:{2}".format(
                        t["project"], t["slug"], i)


def list_topics(projs: List[Tuple[str, Path]], show_all: bool = False) -> List[Dict]:
    """Темы проектов *projs* по порядку ростера, внутри — по имени папки.

    Архивные (``status: archived``) и помеченные «не читать» — только с *show_all*.
    """
    out: List[Dict] = []
    for name, root in projs:
        tdir = paths.tide_dir(root) / TOPICS_DIRNAME
        if not tdir.is_dir():
            continue
        for d in sorted(p for p in tdir.iterdir() if p.is_dir()):
            if (d / TOPIC_FILE).is_file() or (d / FACTS_FILE).is_file():
                out.append(topic_info(name, d))
    _mark_dont_read(out)
    for t in out:
        t.pop("_dont_read", None)
    if not show_all:
        out = [t for t in out if t["fresh"] not in HIDDEN]
    return out


# --- выбор темы -------------------------------------------------------------

def select(topics: List[Dict], query: str,
           prefer_project: Optional[str] = None) -> List[Dict]:
    """Темы под *query*: ``проект/slug``, точный slug, номер, часть slug/заголовка.

    Возвращает самый точный непустой уровень совпадений. Несколько тем и одна из
    них в *prefer_project* (проект, откуда спросили) — берётся она.
    """
    q = query.strip().lower()
    if "/" in q:
        proj, _, rest = q.partition("/")
        pool = [t for t in topics if t["project"].lower() == proj]
        q = rest
    else:
        pool = topics
    levels = [
        [t for t in pool if t["slug"].lower() == q],
        [t for t in pool if q.isdigit() and t["num"] == int(q)],
        [t for t in pool if q and q in t["slug"].lower()],
        [t for t in pool if q and q in t["title"].lower()],
    ]
    for hits in levels:
        if hits:
            if len(hits) > 1 and prefer_project:
                mine = [t for t in hits if t["project"] == prefer_project]
                if len(mine) == 1:
                    return mine
            return hits
    return []


def current_project(projs: List[Tuple[str, Path]]) -> Optional[str]:
    """Имя проекта ростера, в котором стоит cwd (worktree сводится к main)."""
    root = paths.find_tide_root()
    if root is None:
        return None
    try:
        want = root.resolve()
    except OSError:
        return None
    for name, r in projs:
        try:
            if r.resolve() == want:
                return name
        except OSError:
            continue
    return None


# --- слова запроса ----------------------------------------------------------

_STRIP = "«»\"'`.,:;!?()[]{}…—–-"


def tokenize(words: List[str]) -> List[str]:
    """Слова запроса: по пробелам (и внутри кавычек), без краевой пунктуации."""
    out = []
    for w in words:
        for tok in w.split():
            t = tok.strip(_STRIP)
            if t:
                out.append(t)
    return out


def stem(word: str) -> str:
    """Основа: lower, ё→е; у русских слов длиннее 4 букв срезается окончание.

    Латиница и слова с цифрами (``music``, ``SOC-102``) — как есть.
    """
    w = norm(word)
    if _CYR_RE.match(w) and len(w) > 4:
        return w[:max(4, len(w) - 2)]
    return w


def _hit(stem_: str, low: str) -> bool:
    """Основа в строке: подстрокой; короткая русская (≤4 букв) — только с начала слова.

    Иначе «текс» ловит «контекст», «реши» — «разрешил».
    """
    if len(stem_) > 4 or not _CYR_RE.match(stem_):
        return stem_ in low
    return re.search(r"(?<![а-яa-z0-9])" + re.escape(stem_), low) is not None


def make_terms(words: List[str]) -> List[Dict]:
    """``[{word, stem, key}]`` без служебных слов; key — слово с цифрой (обязательно)."""
    toks = tokenize(words)
    kept = [t for t in toks if norm(t) not in STOPWORDS] or toks
    out: List[Dict] = []
    seen = set()
    for t in kept:
        s = stem(t)
        if s in seen:
            continue
        seen.add(s)
        out.append({"word": t, "stem": s, "key": any(c.isdigit() for c in t)})
    return out


def pick_project(terms: List[Dict], names: List[str]) -> Tuple[Optional[str], List[Dict]]:
    """Слово-имя проекта ростера → (имя, остальные слова)."""
    by = {norm(n): n for n in names}
    found = None
    rest = []
    for x in terms:
        if found is None and norm(x["word"]) in by:
            found = by[norm(x["word"])]
        else:
            rest.append(x)
    return found, rest


def pick_topics(topics: List[Dict], terms: List[Dict]) -> Tuple[List[Dict], List[str]]:
    """Темы, на которые указывают слова запроса: ≥2 основ в заголовке или слово в slug.

    Возвращает (темы с лучшим счётом, слова-указатели) или ``([], [])``.
    """
    best: List[Dict] = []
    best_n = 0
    best_words: List[str] = []
    for t in topics:
        title = norm(t["title"])
        slug = norm(t["slug"])
        words = [x["word"] for x in terms
                 if (_CYR_RE.match(x["stem"]) and x["stem"] in title)
                 or (len(x["stem"]) >= 4 and not x["key"] and x["stem"] in slug)]
        slug_hit = any(len(x["stem"]) >= 4 and not x["key"] and x["stem"] in slug
                       for x in terms)
        n = len(words)
        if n < 2 and not slug_hit:
            continue
        if n > best_n:
            best, best_n, best_words = [t], n, words
        elif n == best_n:
            best.append(t)
    return best, best_words


# --- grep -------------------------------------------------------------------

def need_words(n: int, best: int) -> int:
    """Порог строки: сколько основ из *n* должно совпасть, если лучшая строка — *best*.

    Не меньше половины значимых слов (одно общее слово из длинного вопроса — шум)
    и не дальше одного слова от лучшей строки: лишнее слово в запросе не отсекает
    нужную строку, но строки, где не хватает двух и больше слов против лучшей, —
    уже про другое. Одно слово в запросе — одно совпадение.
    """
    return max(1, -(-n // 2), best - 1)


def _rows(t: Dict):
    """Строки темы для поиска без служебных: ``(path, номер, текст, заменено?, ранг)``.

    Ранг — порядок внутри темы при равном счёте: topic.md (0) → шапка и
    «Сейчас»/«Открыто» facts.md (1) → факты, новые сверху (2).
    """
    for key, is_topic in (("topic", True), ("facts_file", False)):
        path = t[key]
        if not path:
            continue
        lines = _read_lines(Path(path))
        skip = meta_lines(lines, is_topic)
        if is_topic:
            fact_lines = set()
        else:
            sec = parse_facts(lines)
            fact_lines = {i for i, _ in sec["facts"] + sec["decisions"]}
        for i, ln in enumerate(lines, 1):
            if i in skip or not ln.strip():
                continue
            rank = 0 if is_topic else (2 if i in fact_lines else 1)
            yield path, i, ln.strip(), (not is_topic and _is_replaced(ln)), rank


def grep(topics: List[Dict], words, show_all: bool = False,
         keep_topics: bool = False) -> Dict:
    """Поиск живыми словами по facts.md и topic.md тем *topics*.

    *words* — слова запроса или готовые термы :func:`make_terms`. Ответ —
    ``{mode: all|partial|none, n, matched, hits, hidden_replaced, hidden_topics}``.
    Скрытые темы (архив, «не читать») ищутся, только если *show_all* или
    *keep_topics* (тему назвали явно); заменённые факты — только с *show_all*.
    """
    terms = words if words and isinstance(words[0], dict) else make_terms(list(words or []))
    stems = [x["stem"] for x in terms]
    keys = [x["stem"] for x in terms if x["key"]]
    n = len(stems)
    res = {"mode": "none", "n": n, "matched": 0, "stems": stems, "hits": [],
           "hidden_replaced": 0, "hidden_topics": []}
    if not n:
        return res
    cands: List[Dict] = []
    order = 0
    for ti, t in enumerate(topics):
        for path, i, text, replaced, rank in _rows(t):
            low = norm(text)
            if any(k not in low for k in keys):
                continue
            k = sum(1 for s in stems if _hit(s, low))
            if not k:
                continue
            order += 1
            cands.append({"project": t["project"], "slug": t["slug"], "path": path,
                          "line": i, "text": text, "k": k, "replaced": replaced,
                          "fresh": t["fresh"], "label": FRESH_LABEL[t["fresh"]],
                          "flag": t["flag"], "_order": (ti, rank, order)})
    hide_topic = not (show_all or keep_topics)
    vis = [c for c in cands
           if (show_all or not c["replaced"]) and not (hide_topic and c["fresh"] in HIDDEN)]
    best = max((c["k"] for c in vis), default=0)
    need = need_words(n, best)
    if best >= need:
        res["mode"] = "all" if best == n else "partial"
    hits = [c for c in vis if c["k"] >= need]
    hits.sort(key=lambda c: (-c["k"], FRESH_RANK[c["fresh"]], c["_order"]))
    for c in hits:
        c.pop("_order", None)
    res["hits"] = hits
    res["matched"] = hits[0]["k"] if hits else 0
    if not show_all:
        res["hidden_replaced"] = sum(1 for c in cands if c["replaced"] and c["k"] >= need
                                     and not (hide_topic and c["fresh"] in HIDDEN))
    if hide_topic:
        hid: Dict[Tuple[str, str], Dict] = {}
        for c in cands:
            if c["fresh"] in HIDDEN and c["k"] >= need:
                h = hid.setdefault((c["project"], c["slug"]),
                                   {"project": c["project"], "slug": c["slug"],
                                    "why": ("архив" if c["fresh"] == "archived"
                                            else c["flag"]), "lines": 0})
                h["lines"] += 1
        res["hidden_topics"] = list(hid.values())
    return res


# --- рендер -----------------------------------------------------------------

def _checked(t: Dict) -> str:
    base = "сверено {0}".format(t["checked"]) if t["checked"] else "не сверено"
    if t.get("fresh") == "archived":
        base += " · [архив]"
    elif t.get("fresh") in ("stale", "skip"):
        base += " · [устарело]"
    if t.get("flag"):
        base += " · " + t["flag"]
    return base


def _facts_word(t: Dict) -> str:
    if not t["facts_file"]:
        return "фактов нет"
    word = "фактов {0}".format(t["facts"])
    if t.get("decisions"):
        word += ", из них решений {0}".format(t["decisions"])
    return word


def render_list(topics: List[Dict]) -> str:
    if not topics:
        return "тем нет (ни в одном проекте нет .tide/topics/)"
    lines = ["{0} · {1} · {2} · {3} · {4}".format(
        t["project"], t["slug"], t["title"] or "—", _facts_word(t), _checked(t))
        for t in topics]
    lines.append("тема: tide truth <slug|номер|слово|проект> · поиск: tide truth --grep слова")
    return "\n".join(lines)


def _row(i: int, text: str, project: str, holders: Optional["Holders"]) -> Dict:
    r = {"line": i, "text": text}
    note = holders.note(text, project) if holders is not None else ""
    if note:
        r["holder"] = note
    return r


def show(t: Dict, limit: int = DEFAULT_LIMIT, show_all: bool = False,
         holders: Optional["Holders"] = None) -> Dict:
    """Данные темы: topic.md целиком, потом «Сейчас»/«Открыто» и факты facts.md.

    Номера строк — с единицы; пустые строки topic.md опущены, остальные — как
    на странице. Строка «ведёт …» несёт ``holder`` — кто держит нить сейчас.
    """
    p = t["project"]
    lines = _read_lines(Path(t["facts_file"])) if t["facts_file"] else []
    sec = parse_facts(lines)
    facts = sec["facts"] if show_all else [f for f in sec["facts"] if not _is_replaced(f[1])]
    topic_lines = _read_lines(Path(t["topic"])) if t["topic"] else []
    return {
        **t,
        "page": [_row(i, ln.rstrip(), p, holders)
                 for i, ln in enumerate(topic_lines, 1) if ln.strip()],
        "now": [_row(i, ln, p, holders) for i, ln in sec["now"]],
        "open": [_row(i, ln, p, holders) for i, ln in sec["open"]],
        "decisions_rows": [_row(i, ln, p, holders) for i, ln in sec["decisions"]
                           if show_all or not _is_replaced(ln)],
        "other": [_row(i, ln, p, holders) for i, ln in sec["other"]],
        "shown": [_row(i, ln, p, holders) for i, ln in facts[:limit]],
        "total": len(facts),
        "hidden_replaced": 0 if show_all else t["replaced"],
    }


def _note(r: Dict) -> str:
    return "  " + r["holder"] if r.get("holder") else ""


def _addr(rows: List[Dict], fname: str = FACTS_FILE) -> List[str]:
    return ["{0}:{1} {2}{3}".format(fname, r["line"], r["text"], _note(r)) for r in rows]


def render_show(d: Dict) -> str:
    out = ["{0} · {1} — {2} · {3}".format(
        d["project"], d["slug"], d["title"] or "—", _checked(d))]
    out.append(d["dir"])
    out.extend(_addr(d.get("page") or [], TOPIC_FILE))
    if not d["facts_file"]:
        out.append("фактов нет: facts.md не заведён в {0}".format(d["dir"]))
    else:
        if d["now"]:
            out.append("Сейчас:")
            out.extend(_addr(d["now"]))
        if d["open"]:
            out.append("Открыто:")
            out.extend(_addr(d["open"]))
        if d.get("decisions_rows"):
            out.append("Решения:")
            out.extend(_addr(d["decisions_rows"]))
        if d.get("other"):
            out.append("Другие секции:")
            out.extend(_addr(d["other"]))
        tail = "; заменено скрыто: {0}".format(d["hidden_replaced"]) if d["hidden_replaced"] else ""
        rest = d["now"] or d["open"] or d.get("decisions_rows") or d.get("other")
        if d["total"] or d["hidden_replaced"] or not rest:
            out.append("Факты ({0} из {1}, новые сверху{2}):".format(
                len(d["shown"]), d["total"], tail))
            out.extend(_addr(d["shown"]))
        more = []
        if len(d["shown"]) < d["total"]:
            more.append("--limit {0}".format(d["total"]))
        if d["hidden_replaced"]:
            more.append("--all")
        more.append("--grep слова")
        out.append("глубже: tide truth {0} {1}".format(d["slug"], " · ".join(more)))
    return "\n".join(out)


# --- вид проекта: «где мы» / «в полёте» / «горит» каждой темы ---------------

# поля topic.md, которые отвечают «как дела» (порядок — как на странице)
STATE_FIELDS = ("где мы", "в полете", "горит", "дальше")


def state_rows(lines: List[str], fields: Tuple[str, ...] = STATE_FIELDS) -> List[Tuple[int, str]]:
    """Строки полей *fields* topic.md с продолжениями (строки с отступом под полем)."""
    out: List[Tuple[int, str]] = []
    cur = None
    for i, ln in enumerate(lines, 1):
        if not ln.strip() or ln.startswith("#"):
            cur = None
            continue
        if ln[:1].isspace():
            if cur in fields:
                out.append((i, ln.rstrip()))
            continue
        m = _FIELD_RE.match(ln)
        cur = norm(m.group(1).strip()) if m else None
        if cur in fields:
            out.append((i, ln.rstrip()))
    return out


def project_view(topics: List[Dict], holders: Optional["Holders"] = None) -> List[Dict]:
    """Каждая тема проекта: шапка + «где мы» / «в полёте» / «горит» / «дальше» topic.md."""
    out = []
    for t in topics:
        lines = _read_lines(Path(t["topic"])) if t["topic"] else []
        rows = [_row(i, ln, t["project"], holders) for i, ln in state_rows(lines)]
        out.append({**t, "state": rows})
    return out


def render_project(view: List[Dict]) -> str:
    if not view:
        return "тем нет (в проекте нет .tide/topics/)"
    out: List[str] = []
    for d in view:
        if out:
            out.append("")
        out.append("{0} · {1} — {2} · {3}".format(
            d["project"], d["slug"], d["title"] or "—", _checked(d)))
        out.append(d["dir"])
        if d["state"]:
            out.extend(_addr(d["state"], TOPIC_FILE))
        else:
            out.append("где мы / в полёте / горит — на странице не записаны")
        out.append("вся тема: tide truth {0}/{1} ({2})".format(
            d["project"], d["slug"], _facts_word(d)))
    return "\n".join(out)


# --- держатель нити ---------------------------------------------------------

# адрес нити в строке: слаг ``34-@context-keeper`` или имя сессии ``board · 02``
_REF_RE = re.compile(r"(?<![\w@/-])(\d+-@[a-z0-9][a-z0-9-]*)(?![a-z0-9/-])|(?<![\w@/-])([a-z][a-z0-9-]*) · \d{1,3}(?![\w-])")
_LEADS_RE = re.compile(r"вед[её]т")
ALIVE_WORD = {True: "жива", False: "не отвечает", None: "не проверял"}


def _alive_probe():
    """Проверка живости AO-сессии — та же, что у ``tide thread holder``."""
    from .adapters.ao import AoAdapter

    return AoAdapter().alive


def _unknown_alive(handle: str) -> bool:
    raise RuntimeError("живость не проверить")  # holder() сведёт это к alive=None


class Holders:
    """Кто сейчас держит нить, названную в строке темы, — через :func:`tide.arc.holder.holder`.

    Строка берётся, если в ней «ведёт» (тогда — первая нить после слова) или нить
    в ней одна и названа «нить …» (``в полёте: … — нить main (01-@main)``); адрес
    внутри пути (``arcs/31-@main/…``) — не нить. Проект нити — имя проекта ростера перед адресом (``(tide-stack,
    29-@board)``, ``в management (04-@main)``), иначе проект темы; нет там такой
    нити — единственный проект ростера, где она есть. Ничего не нашлось — пусто.
    Ответы кэшируются на прогон.
    """

    def __init__(self, projs: List[Tuple[str, Path]], home: Optional[Path] = None,
                 alive=None):
        self.roots = dict(projs)
        self.home = home
        self._alive = alive
        self._cache: Dict[Tuple[str, str], str] = {}
        self._recs: Dict[Tuple[str, str], Optional[Dict]] = {}
        self._names = sorted(self.roots, key=len, reverse=True)

    def _refs(self, text: str) -> List[Tuple[int, int, str]]:
        out = []
        for m in _REF_RE.finditer(text):
            ref = m.group(1) or m.group(2)
            out.append((m.start(), m.end(), ref))
        return out

    def _project_before(self, chunk: str) -> Optional[str]:
        low = norm(chunk)
        best, pos = None, -1
        for name in self._names:
            for m in re.finditer(r"(?<![\w-])" + re.escape(norm(name)) + r"(?![\w-])", low):
                if m.start() > pos:
                    best, pos = name, m.start()
        return best

    def pick(self, text: str, project: str) -> Optional[Tuple[str, str]]:
        """``(проект, адрес нити)`` строки *text* — или None."""
        refs = self._refs(text)
        if not refs:
            return None
        lead = _LEADS_RE.search(norm(text))
        chosen = None
        if lead:
            chosen = next((r for r in refs if r[0] >= lead.end()), None)
        if (chosen is None and len({slug_key(r[2]) for r in refs}) == 1
                and "нит" in norm(text[max(0, refs[0][0] - 40):refs[0][0]])):
            chosen = refs[0]  # одна нить, названная «нить …», — тоже «кто ведёт»
        if chosen is None:
            return None
        idx = refs.index(chosen)
        start = refs[idx - 1][1] if idx else 0
        proj = self._project_before(text[start:chosen[0]]) or project
        return proj, chosen[2]

    def _lookup(self, project: str, ref: str) -> str:
        key = (project, slug_key(ref))
        if key in self._cache:
            return self._cache[key]
        note = ""
        root = self.roots.get(project)
        rec = self._holder(root, ref) if root is not None else None
        if rec is None:  # нить живёт в соседнем проекте — берём, если он один такой
            found = [r for r in (self._holder(rt, ref) for n, rt in self.roots.items()
                                 if n != project) if r is not None]
            rec = found[0] if len(found) == 1 else None
        if rec is not None and rec.get("arc"):
            thread = slug_key(str(rec["thread"]))
            head = str(rec["arc"]).rsplit("/", 1)[-1]
            m = _NUM_RE.match(head)
            who = "{0} · {1}".format(thread, m.group(1)) if m else head
            if rec.get("ao_session"):
                who += ", {0} ({1})".format(rec["ao_session"], ALIVE_WORD[rec.get("alive")])
            note = "→ сейчас: " + who
        self._cache[key] = note
        return note

    def _holder(self, root: Path, ref: str) -> Optional[Dict]:
        from .arc import holder as _holder

        key = (str(root), slug_key(ref))
        if key not in self._recs:
            if self._alive is None:
                try:
                    self._alive = _alive_probe()
                except Exception:  # noqa: BLE001 — AO не достать: живость «не проверял»
                    self._alive = _unknown_alive
            try:
                rec = _holder.holder(root, ref, home=self.home, alive=self._alive)
            except Exception:  # noqa: BLE001 — нет нити/сломан реестр: просто не подставляем
                rec = None
            self._recs[key] = rec
        return self._recs[key]

    def note(self, text: str, project: str) -> str:
        """``→ сейчас: board · 03, tide-stack-23 (жива)`` для строки *text* — или ``""``."""
        try:
            got = self.pick(text, project)
            return self._lookup(*got) if got else ""
        except Exception:  # noqa: BLE001 — подсказка, не повод падать
            return ""


def slug_key(ref: str) -> str:
    """Ключ нити без номера и @: ``29-@board`` / ``board`` → ``board``."""
    from . import slug as _slug

    return _slug.entry_slug(ref) or ref


def make_holders(home: Optional[Path] = None) -> Optional[Holders]:
    """Держатели по всему ростеру (нить может жить в соседнем проекте)."""
    try:
        home = home if home is not None else _home_or_none()
        return Holders(resolve_projects(None, home), home)
    except Exception:  # noqa: BLE001
        return None


def render_grep(res: Dict, words: List[str], limit: int,
                scope: Optional[List[str]] = None) -> str:
    out: List[str] = list(scope or [])
    hits = res["hits"]
    if not hits:
        stems = " · ".join(res.get("stems") or [])
        out.append("в фактах нет: «{0}»{1}".format(
            " ".join(words), " (основы: {0})".format(stems) if stems else ""))
        out.append("попробуй: " + EMPTY_HINT)
    else:
        n = res["n"]
        if res["mode"] == "partial":
            out.append("не все слова: совпало {0} из {1} (основы: {2})".format(
                res["matched"], n, " · ".join(res["stems"])))
        group = None
        tier = res["matched"]
        for h in hits[:limit]:
            if h["k"] != tier:  # строки с меньшим совпадением — ниже, под своей пометкой
                tier = h["k"]
                out.append("совпало {0} из {1}:".format(tier, n))
                group = None
            key = (h["project"], h["slug"])
            if key != group:
                group = key
                lab = " " + h["label"] if h["label"] else ""
                out.append("{0} · {1}{2}  {3}".format(h["project"], h["slug"], lab,
                                                     str(Path(h["path"]).parent)))
            lab = h["label"] + " " if h["label"] else ""
            out.append("{0}:{1} {2}{3}{4}".format(Path(h["path"]).name, h["line"], lab,
                                                 h["text"], _note(h)))
        if len(hits) > limit:
            out.append("ещё {0} строк — --limit {1}".format(len(hits) - limit, len(hits)))
    if res.get("hidden_replaced"):
        out.append("заменено скрыто: {0} — --all".format(res["hidden_replaced"]))
    for h in res.get("hidden_topics") or []:
        out.append("скрыта тема {0}/{1} ({2}): строк {3} — --all".format(
            h["project"], h["slug"], h["why"], h["lines"]))
    return "\n".join(out)


def _ambiguous(hits: List[Dict], query: str) -> str:
    rows = ["{0}/{1} · {2}".format(t["project"], t["slug"], t["title"] or "—") for t in hits]
    return ("truth: «{0}» подходит нескольким темам — уточни (проект/slug или --project):\n"
            .format(query) + "\n".join(rows))


# --- CLI --------------------------------------------------------------------

def _emit(args, payload, text: str) -> None:
    if getattr(args, "as_json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print(text)


def _show_or_list(args, topics: List[Dict], limit: int) -> int:
    if len(topics) == 1:
        d = show(topics[0], limit, args.all, make_holders())
        _emit(args, d, render_show(d))
    else:
        _emit(args, topics, render_list(topics))
    return 0


def _project(args, topics: List[Dict]) -> int:
    """`tide truth <проект>`: «где мы» / «в полёте» / «горит» каждой темы проекта."""
    view = project_view(topics, make_holders())
    _emit(args, view, render_project(view))
    return 0


def _cmd_truth(args) -> int:
    if args.topic == "tg" and not args.grep:
        from . import truth_tg  # лениво: truth_tg сам импортирует truth
        return truth_tg.cmd_tg(args)
    if getattr(args, "tg_target", None) is not None:
        raise TruthError("truth: лишний аргумент «{0}» (второе слово — только у tide truth tg)"
                         .format(args.tg_target))
    if args.topic in ("check", "inbox", "decide", "skip", "baseline") and not args.grep:
        from . import decisions_tg  # лениво: decisions_tg сам импортирует truth
        return getattr(decisions_tg, "cmd_" + args.topic)(args)
    project = getattr(args, "project", None)
    show_all = bool(args.all)
    limit = max(1, int(DEFAULT_LIMIT if args.limit is None else args.limit))
    names = [] if project else [n for n, _ in resolve_projects(None)]
    query = args.topic
    grep_words = list(args.grep or [])
    explicit = False
    by_project = False

    # имя проекта позиционно — `tide truth music`
    if query and names:
        by = {norm(n): n for n in names}
        if norm(query.strip()) in by:
            project, query, names = by[norm(query.strip())], None, []
            by_project = True

    projs = resolve_projects(project)
    topics = list_topics(projs, show_all)
    words: List[str] = list(grep_words)

    if query:
        hits = select(topics, query, current_project(projs))
        if not hits and not show_all:
            hidden = select(list_topics(projs, True), query, current_project(projs))
            if hidden and not words:
                rows = ["{0}/{1} · {2}".format(t["project"], t["slug"], _checked(t))
                        for t in hidden]
                print("truth: «{0}» — тема скрыта (архив/«не читать»), покажет --all:\n{1}"
                      .format(query, "\n".join(rows)))
                return 0
        if not hits:
            words = [query] + words  # темы нет — ищем слова по всем темам
        elif len(hits) > 1:
            if not words:
                raise TruthError(_ambiguous(hits, query))
            topics, explicit = hits, True
        else:
            topics, explicit = hits, True
            if not words:
                return _show_or_list(args, topics, limit)

    if not words:
        if by_project:
            return _project(args, topics)
        _emit(args, topics, render_list(topics))
        return 0
    if not explicit:
        topics = list_topics(projs, True)  # скрытые темы прячет grep и считает их

    terms = make_terms(words)
    scope: List[str] = []
    if names:
        found, rest = pick_project(terms, names)
        if found:
            project = found
            topics = [t for t in topics if t["project"] == found]
            terms = rest
            scope.append("проект: {0}".format(found))
            if not terms:
                if not show_all:
                    topics = [t for t in topics if t["fresh"] not in HIDDEN]
                return _project(args, topics)
    if not explicit and len(topics) > 1:
        narrowed, by_words = pick_topics(topics, terms)
        if narrowed and len(narrowed) < len(topics):
            topics = narrowed
            scope.append("тема: {0} (по словам: {1})".format(
                ", ".join("{0}/{1}".format(t["project"], t["slug"]) for t in narrowed),
                ", ".join(by_words)))
    res = grep(topics, terms, show_all, explicit)
    holders = make_holders()
    if holders is not None:
        for h in res["hits"][:limit]:
            note = holders.note(h["text"], h["project"])
            if note:
                h["holder"] = note
    shown_words = [x["word"] for x in terms]
    payload = {"words": shown_words, "stems": res["stems"], "mode": res["mode"],
               "matched": res["matched"], "n": res["n"], "project": project,
               "topics": ["{0}/{1}".format(t["project"], t["slug"]) for t in topics],
               "total": len(res["hits"]), "hits": res["hits"][:limit],
               "hidden_replaced": res["hidden_replaced"],
               "hidden_topics": res["hidden_topics"]}
    _emit(args, payload, render_grep(res, words, limit, scope))
    return 0


def register(subparsers) -> None:
    """Верхний верб ``tide truth`` (зовёт cli.py)."""
    p = subparsers.add_parser(
        "truth",
        help="что сказал человек по темам: список · тема · --grep (только чтение)",
    )
    p.add_argument("topic", nargs="?",
                   help="тема: slug, номер, часть slug/заголовка, проект/slug или имя проекта; "
                        "check — сверка журналов решений decisions-tg.jsonl (код 1 при ошибках); "
                        "inbox — ответы человека в TG без решения; "
                        "decide — записать решение в тему (журнал + «## Решения»); "
                        "skip — пара без решения в decisions-skip.jsonl; "
                        "baseline — стартовая строка журнала правок edits.jsonl; "
                        "tg — тема Telegram проекта: ответы Гриши и открытые вопросы")
    p.add_argument("tg_target", nargs="?", metavar="ЧТО",
                   help="для tg: проект, slug темы или проект/нить (по умолчанию — проект cwd)")
    p.add_argument("--grep", nargs="+", metavar="СЛОВО",
                   help="поиск живыми словами: основы слов, сначала строки со всеми")
    p.add_argument("--project", help="только этот проект ростера")
    p.add_argument("--limit", type=int, default=None,
                   help="сколько фактов/строк показать (по умолчанию {0}; у tg — 10 ответов)"
                        .format(DEFAULT_LIMIT))
    p.add_argument("--all", action="store_true",
                   help="показать и заменённые факты, и скрытые темы (архив, «не читать»)")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="машинный вывод")
    from . import decisions_tg  # лениво: decisions_tg сам импортирует truth
    decisions_tg.add_arguments(p)
    p.set_defaults(func=_cmd_truth, _cmd="truth")

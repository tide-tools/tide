"""tide.acts — АКТ: одна строка в паспорте сессии, единственный писатель.

Акт — это то, что сессия сделала, сказанное так, чтобы читалось через год:
**что · пруф · адрес**. Пульс, чек с пруфом и «строитель отправлен» — один и
тот же жест, поэтому и место у них одно: секция ``## context`` паспорта той
сессии, из которой жест сделан. Раньше мест было два (журнал работы и паспорт
сессии), и собрать по ним историю дня было нечем.

Грамматика (слово человека, 02.09.2026) — дословно::

    - 2026-09-02T16:17:32 — акт: что сделано · пруф: чем · слово: «…» · #66.4

* разделитель полей — ``" · "`` (пробел-интерпункт-пробел);
* адрес работы стоит ПОСЛЕДНИМ: ``#66`` — работа целиком, ``#66.4`` — её пункт 4;
* акт ВСЕГДА одна строка. Переводы строк схлопываются в пробелы
  (:func:`tide.offload.write_pulse` делает это сам, и это правильно), а длинное
  не лезет в акт вообще: :data:`ACT_MAX` — потолок, выше него
  :class:`ValueError`. Длинный пруф кладут файлом в нить, а в акт дают путь.

Акт рождается ТОЛЬКО в сессии (решение 37 нити: подтверждение человека идёт
через чат, доска — витрина). Сессию вызывателя находит :func:`caller_session`
через ``$CLAUDE_CODE_SESSION_ID``; нет сессии — акта нет, и это не ошибка:
вербы зовут и из скриптов, и с чужой машины, и падать они там не смеют.

Писатель один — :func:`write_act` поверх :func:`tide.offload.write_pulse`.
Читатель — :func:`read_acts`: акты всех сессий одной нити в один список,
новое сверху, с фильтром по адресу работы. Поверх него живут два вида:
``tide work show`` (журнал работы = акты + легаси-строки файла) и ``tide thread``
(лента :func:`head_session` — ведущей сессии нити).

Длинное в акт не лезет, и это не проблема, а правило: :func:`spill_proof`
кладёт полный пруф файлом в ``workspace/proofs/`` сессии, :func:`file_proof`
собирает для акта первую строку пруфа и путь к файлу. Обрезать пруф нельзя —
с тех пор как журнал стал ВИДОМ на акты, обрезок был бы единственной записью.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, NamedTuple, Optional

from . import fields, offload, paths, slug

# Потолок длины акта — вместе с меткой времени, то есть ровно та строка, которую
# человек увидит в паспорте. Три сотни знаков — это примерно две строки экрана;
# всё, что длиннее, — уже не акт, а отчёт, и ему место в файле.
ACT_MAX = 300

KIND = "акт"
SEP = " · "
PROOF_LABEL = "пруф"
WORD_LABEL = "слово"

# «· #66» или «· #66.4» в самом хвосте строки
_REF_RE = re.compile(r"(?:^|\s·\s)#(\d{1,4})(?:\.(\d{1,4}))?\s*$")
# «- 2026-09-02T16:17:32 — текст акта»
_ACT_LINE_RE = re.compile(r"^-\s+(\S+?)\s+—\s+(.*)$")


class Act(NamedTuple):
    """Один акт: когда, что написано, по какому адресу и в чьём паспорте."""

    at: datetime
    text: str
    ref: str
    session: Path


# --- адрес -------------------------------------------------------------------

def make_ref(work: object, item: Optional[object] = None) -> str:
    """Адрес работы для акта: ``"66"`` или ``"66.4"`` (без решётки).

    *work* — номер работы или имя её папки (``66-akty-v-odnom-meste``): берётся
    ведущее число, потому что вербы держат в руках именно папку.
    """
    raw = str(work or "").strip()
    num = raw.partition("-")[0].strip()
    if not num.isdigit():
        return ""
    ref = str(int(num))
    if item is not None and str(item).strip().isdigit():
        ref = "{0}.{1}".format(ref, int(str(item).strip()))
    return ref


# --- писатель ----------------------------------------------------------------

def compose(what: str, *, proof: Optional[str] = None,
            word: Optional[str] = None, ref: Optional[str] = None) -> str:
    """Тело акта по грамматике — без метки времени и без ведущего «- ».

    Пустые поля просто не пишутся: акт без пруфа честнее акта с пустым «пруф:».
    """
    body = _flat(what)
    if not body:
        raise ValueError("акт: пустой акт — скажи, что сделано")
    parts = ["{0}: {1}".format(KIND, body)]
    if _flat(proof):
        parts.append("{0}: {1}".format(PROOF_LABEL, _flat(proof)))
    if _flat(word):
        parts.append("{0}: «{1}»".format(WORD_LABEL, _flat(word)))
    tail = _flat(ref).lstrip("#")
    if tail:
        parts.append("#" + tail)
    return SEP.join(parts)


def write_act(session_dir: Path, what: str, *, proof: Optional[str] = None,
              word: Optional[str] = None, ref: Optional[str] = None,
              now: Optional[datetime] = None) -> str:
    """Дописать акт в ``## context`` паспорта *session_dir*; вернуть строку.

    Пишет через :func:`tide.offload.write_pulse` — второго писателя в ``##
    context`` нет и быть не должно. Акт длиннее :data:`ACT_MAX` не пишется
    вовсе: :class:`ValueError` с подсказкой, куда девать длинное.
    """
    body = compose(what, proof=proof, word=word, ref=ref)
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    line = "- {0} — {1}".format(stamp, body)
    if len(line) > ACT_MAX:
        raise ValueError(
            "акт: {0} знаков при потолке {1} — акт это одна строка «что · пруф · "
            "адрес». Положи длинное файлом в нить, в акт дай путь.".format(
                len(line), ACT_MAX))
    offload.write_pulse(Path(session_dir), note=body, now=now)
    return line


def fit_proof(what: str, proof: object, *, word: Optional[str] = None,
              ref: Optional[str] = None) -> str:
    """Пруф, укороченный с хвоста ровно настолько, чтобы акт влез в :data:`ACT_MAX`.

    АВАРИЙНЫЙ путь, а не рабочий: обрезанный пруф — потерянный пруф, потому что
    журнал работы теперь ВИД на акты, а не второе место хранения. Длинное кладут
    файлом (:func:`spill_proof`) и дают путь (:func:`file_proof`); сюда падают,
    только когда файл написать не вышло — лучше начало пруфа с «…», чем пустая
    строка. Пустая строка — если не влезает даже без пруфа.
    """
    flat = _flat(proof)
    if not flat:
        return ""
    over = _over(what, flat, word=word, ref=ref)
    if over <= 0:
        return flat
    keep = len(flat) - over - 1  # знак под многоточие
    if keep < 1:
        return ""
    return flat[:keep].rstrip() + "…"


def _over(what: str, proof: str, *, word: Optional[str] = None,
          ref: Optional[str] = None) -> int:
    """На сколько знаков акт с таким пруфом перевешивает потолок (≤0 — влезает)."""
    room = len("- {0} — ".format(datetime.now().isoformat(timespec="seconds")))
    return room + len(compose(what, proof=proof, word=word, ref=ref)) - ACT_MAX


# --- длинный пруф: файлом, а не обрезком --------------------------------------

PROOFS_DIRNAME = "proofs"


def spill_proof(session_dir: Path, proof: object, *, ref: str = "",
                now: Optional[datetime] = None) -> Path:
    """Полный пруф — файлом в ``workspace/proofs/`` сессии; вернуть путь.

    Слово человека 02.09.2026: «одна строка, длинное — файлом». Акт однострочен
    по построению, но пруф на три абзаца от этого не перестаёт быть пруфом —
    и обрезать его нельзя: журнал работы стал ВИДОМ на акты, и обрезок стал бы
    единственной записью. Поэтому длинное уезжает в файл рядом с сессией,
    которая его написала, а в акт идёт первая строка и путь.

    Имя файла — адрес работы, пункт и метка времени (``66.4-20260902-195413.md``):
    по нему видно, к чему пруф, не открывая его.
    """
    stamp = (now or datetime.now())
    name = "{0}-{1}.md".format(_flat(ref).lstrip("#") or "act",
                               stamp.strftime("%Y%m%d-%H%M%S"))
    d = Path(session_dir) / "workspace" / PROOFS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    head = "# пруф{0} · {1}".format(
        " · #" + _flat(ref).lstrip("#") if _flat(ref) else "",
        stamp.isoformat(timespec="seconds"))
    f.write_text("{0}\n\n{1}\n".format(head, str(proof or "").strip()),
                 encoding="utf-8")
    return f


def proof_ref(session_dir: Path, proof_file: Path) -> str:
    """Путь к файлу пруфа так, как его читают в акте — от папки сессии.

    Акт лежит в паспорте этой самой сессии, поэтому её папка — естественный
    ноль отсчёта: ``workspace/proofs/66.4-….md`` короче абсолютного пути и
    говорит ровно то же самое.
    """
    try:
        return str(Path(proof_file).relative_to(Path(session_dir)))
    except ValueError:
        return str(proof_file)


def file_proof(what: str, proof: object, where: str, *,
               word: Optional[str] = None, ref: Optional[str] = None) -> str:
    """Пруф для акта, когда полный текст уехал файлом: первая строка + путь.

    Первая строка пруфа — это то, что автор написал ЧЕЛОВЕКУ (норма чека 31.07:
    коротко для человека, техника ниже), поэтому именно она и остаётся в акте;
    ужимается она только если и с ней акт не влезает. Путь не режется никогда —
    без него запись перестаёт вести к пруфу, а в этом весь смысл.
    """
    first = _flat(str(proof or "").strip().split("\n", 1)[0])
    if not where:
        return fit_proof(what, first, word=word, ref=ref)
    over = _over(what, "{0} · {1}".format(first, where) if first else where,
                 word=word, ref=ref)
    if over <= 0:
        return "{0} · {1}".format(first, where) if first else where
    keep = len(first) - over - 1
    if keep < 1:
        return where
    return "{0}… · {1}".format(first[:keep].rstrip(), where)


def _flat(value: object) -> str:
    """Одна строка без переводов и лишних пробелов (акт однострочен всегда)."""
    return " ".join(str(value or "").split())


# --- кто пишет ---------------------------------------------------------------

def caller_session(root: Optional[Path] = None) -> Optional[Path]:
    """Папка сессии-арки текущего вызывателя — или None.

    Тот же механизм, которым живёт ``work.resolve_caller_thread``:
    ``$CLAUDE_CODE_SESSION_ID`` → :func:`tide.offload.find_session_by_claude_id`
    в проекте вызывателя. None — когда sid не задан, проект не tide-шный или
    паспорт с таким пином не найден; тогда акт просто не рождается.
    """
    sid = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    if not sid:
        return None
    origin = Path(root) if root is not None else paths.find_tide_root()
    if origin is None:
        return None
    try:
        return offload.find_session_by_claude_id(Path(origin), sid)
    except OSError:
        return None


# --- читатель ----------------------------------------------------------------

def read_acts(thread_dir: Path, *, ref: Optional[str] = None) -> List[Act]:
    """Акты ВСЕХ сессий нити *thread_dir* одним списком, новое сверху.

    *ref* фильтрует по адресу: ``"66"`` отдаёт и ``#66``, и ``#66.4``;
    ``"66.4"`` — только пункт 4. Решётку можно писать, можно не писать.

    Закрытые сессии читаются наравне с открытыми: история нити не убывает от
    того, что арку закрыли.
    """
    sessions = _thread_sessions(Path(thread_dir))
    want = _flat(ref).lstrip("#")
    rows: List[tuple] = []
    order = 0
    for entry in sessions:
        for at, text, addr in _session_acts(entry):
            order += 1
            if want and not _ref_matches(addr, want):
                continue
            rows.append((at, order, Act(at, text, addr, entry)))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return [r[2] for r in rows]


def head_session(thread_dir: Path) -> Optional[Path]:
    """Ведущая сессия нити — та, чьи акты и есть «что сейчас делается». Или None.

    Канон №1: нить ведёт НОВЕЙШАЯ ПРИНЯТАЯ сессия — она выводится, не штампуется.
    Выводим тремя вопросами, от самого честного к самому дешёвому, по открытым и
    не отпущенным рукой (``dismissed:``) сессиям, новыми сначала:

    1. у кого на диске есть настоящий чат claude (приём состоялся на деле);
    2. у кого хотя бы проштампован ``claude-session:`` (приём состоялся на бумаге);
    3. просто новейшая открытая — лучше сказать про живую сессию, чем промолчать.

    Первый вопрос отделяет ПРИЗРАКА: подъём оффера чеканит sid и штампует его до
    того, как сессия заговорит; сорвался спавн — и в паспорте остался айди чата,
    которого никогда не было. Доска ловит того же призрака своим ``_head_session``,
    но у неё под рукой уже разобранные сессии, а тут файлы — общий тут ответ, а
    не общий код.
    """
    live = []
    for entry in sorted(_thread_sessions(Path(thread_dir)), reverse=True):
        if slug.is_closed_entry(entry.name):
            continue
        pp = entry / "arc.md"
        if (fields.read_field(pp, "dismissed") or "").strip():
            continue
        live.append((entry, (fields.read_field(pp, "claude-session") or "").strip()))
    if not live:
        return None
    return (next((e for e, sid in live if sid and _has_transcript(e, sid)), None)
            or next((e for e, sid in live if sid), None)
            or live[0][0])


def _has_transcript(entry: Path, sid: str) -> bool:
    """Есть ли у сессии РЕАЛЬНЫЙ чат на диске (``~/.claude/projects/…/<sid>.jsonl``).

    Сперва там, где чат этого проекта и лежит (каталог — cwd с ``/`` и ``.``,
    заменёнными на дефис — так его именует сам claude); мимо — глобом по всем
    проектам, потому что сессию поднимают и не из корня.
    """
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR")
                or (Path.home() / ".claude")) / "projects"
    root = paths.find_tide_root(entry)
    if root is not None:
        encoded = str(root).replace("/", "-").replace(".", "-")
        if (base / encoded / "{0}.jsonl".format(sid)).is_file():
            return True
    try:
        return any(base.glob("*/{0}.jsonl".format(sid)))
    except OSError:
        return False


def strip_ref(text: str) -> str:
    """Тело акта без хвостового адреса — для тех, кто ставит адрес сам."""
    return _REF_RE.sub("", text or "").rstrip()


# --- сито склейки: одно на движок и доску -------------------------------------

def act_what(text: str) -> str:
    """«акт: что сделано · пруф: … · #66.4» → «что сделано» — первое поле акта.

    Оно же и единственное, по которому акт узнаётся: пруф в акте лежит своим
    полем, а в журнальной строке вшит в текст, поэтому сверять целиком нечего.
    """
    head = strip_ref(text or "").split(SEP)[0].strip()
    kind = KIND + ": "
    return head[len(kind):].strip() if head.startswith(kind) else head


def sift_journal(entries: List[tuple], rows: List[Act]) -> List[object]:
    """Записи файлового журнала, у которых АКТА-ДВОЙНИКА нет — сито по ЖЕСТУ.

    ОДНО место склейки на всех (работа 66 п.1). Читателей журнала двое — движок
    (:func:`tide.arc.work.journal_view`) и доска (`_work_journal_view`), — и до
    этой функции каждый нёс свою копию правила: они уже разъехались формулой
    сравнения, а разъехавшись дальше, начали бы показывать человеку две разные
    истории одной работы.

    *entries* — записи файла как ``(метка времени, первая строка тела, что
    вернуть)``: форма журнальной строки у читателей разная (у одного с «- », у
    другого без), поэтому разбор строки остаётся у них, а ПРАВИЛО живёт здесь.
    *rows* — акты этой работы (:class:`Act`).

    Правило: запись выбрасывается, только если тот же жест уже пришёл актом —
    та же МИНУТА и одно тело начинается с другого. Сито именно по жесту, а не
    по времени первого акта: акты в паспорт пишет не только двойная запись
    вербов, но и рука — на живой работе 66 между первым актом и первым вербом
    лежат тринадцать журнальных строк без двойника, и граница по времени стёрла
    бы их с экрана вместе с пруфами.

    Акт гасит РОВНО ОДНУ запись: два одинаковых жеста в одну минуту (пере-
    отправленный строитель) — два события, и терять второе нельзя.
    """
    used = [False] * len(rows)
    out: List[object] = []
    for at, body, payload in entries:
        if not _take_twin(at, body, rows, used):
            out.append(payload)
    return out


def _take_twin(at: Optional[datetime], body: str, rows: List[Act],
               used: List[bool]) -> bool:
    """Найти и ЗАНЯТЬ акт-двойник этой записи; False — двойника нет."""
    if at is None:
        return False
    minute = at.replace(second=0, microsecond=0)
    head = _flat(body).casefold()
    if not head:
        return False
    for i, r in enumerate(rows):
        if used[i] or r.at.replace(second=0, microsecond=0) != minute:
            continue
        what = _flat(act_what(r.text)).casefold()
        if what and (head.startswith(what) or what.startswith(head)):
            used[i] = True
            return True
    return False


def _ref_matches(addr: str, want: str) -> bool:
    """``#66`` ловит и ``66``, и ``66.4``; ``#66.4`` — только себя."""
    return addr == want or addr.startswith(want + ".")


def _thread_sessions(thread: Path) -> List[Path]:
    """Сессии нити — открытые и закрытые, в файловом порядке."""
    sub = thread / paths.ARCS_DIRNAME
    if not sub.is_dir():
        return []
    return [p for p in sorted(sub.iterdir())
            if p.is_dir() and slug.is_entry(p.name) and (p / "arc.md").is_file()]


def _session_acts(entry: Path) -> List[tuple]:
    """``(datetime, текст, адрес)`` каждой строки ``## context`` паспорта.

    Строка без разбираемой метки времени пропускается: акт без времени нельзя
    поставить в ленту, а гадать за него — врать.
    """
    try:
        text = (entry / "arc.md").read_text(encoding="utf-8")
    except OSError:
        return []
    if offload.CONTEXT_SECTION not in text:
        return []
    body = text.partition(offload.CONTEXT_SECTION)[2].split("\n## ", 1)[0]
    out = []
    for ln in body.splitlines():
        m = _ACT_LINE_RE.match(ln.strip())
        if not m:
            continue
        try:
            at = datetime.fromisoformat(m.group(1))
        except ValueError:
            continue
        line = m.group(2).strip()
        addr = ""
        hit = _REF_RE.search(line)
        if hit:
            addr = hit.group(1) if not hit.group(2) else "{0}.{1}".format(*hit.groups())
        out.append((at, line, addr))
    return out


__all__ = ["ACT_MAX", "Act", "PROOFS_DIRNAME", "act_what", "caller_session",
           "compose", "file_proof", "fit_proof", "head_session", "make_ref",
           "proof_ref", "read_acts", "sift_journal", "spill_proof", "strip_ref",
           "write_act"]

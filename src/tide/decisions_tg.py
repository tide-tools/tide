"""tide.decisions_tg — журнал решений человека из Telegram: схема и проверка.

Где лежит. Каждое решение — строка JSON в журнале своей темы
``<корень проекта>/.tide/topics/NN-slug/decisions-tg.jsonl``. Журнал только
дописывается. Рядом, в ``facts.md`` той же темы, в секции «## Решения» —
зеркальная строка ``- решение: … · `tg:N`, `tg:M```.

Схема одной записи (ключи по порядку :data:`KEYS`):

    id             str   ``tg-<номер вопроса>[-суффикс]``, напр. ``tg-3274-2``
    date           str   день решения, ``YYYY-MM-DD``
    project        str   проект ростера (= проект, в чьём корне лежит журнал)
    topic          str   slug темы (= имя каталога темы)
    question       obj|null  ``{tg: int, ask: str, at: ISO, text: str}`` — что спросили;
                         null — решение без вопроса (голос без реплая, реплай на
                         своё же сообщение), тогда ``note`` обязана сказать почему
    answer         obj   ``{tg: [int, …], at: ISO, text: str}`` — что ответил человек
    decision       str   решение своими словами, одной фразой
    handed_to      [str] кому передано исполнять
    soc            [str] задачи Linear (``SOC-N``)
    status         str   ``принято`` | ``заменено``
    supersedes     str|null  id решения, которое это заменяет
    superseded_by  str|null  id решения, которое заменило это (у «заменено» обязателен)
    note           str|null  оговорки: ответ не прямой, расхождения
    src            str   ссылка на исходное слово: ``tg:<answer.tg[0]>``
    valid_from     ISO   с какого момента решение действует; по умолчанию answer.at

Все моменты (``question.at``, ``answer.at``, ``valid_from``) — ISO с поясом.
У старых строк нет ``src`` и ``valid_from`` — :func:`normalize` их дополняет
в памяти, а ``tide truth check`` требует их на диске.

Последняя по id главнее: если в журнале несколько строк с одним ``id``,
действует последняя (так правят запись — дописывают её новую версию).

Как заменить решение: новое решение — новая строка со своим id и
``supersedes: <старый id>``; старое дописывается ещё раз с тем же id,
``status: заменено`` и ``superseded_by: <новый id>``. Ничего не стирается.

Проверка: ``tide truth check [--project P]`` обходит темы проектов ростера
(как ``tide truth``) и сверяет журналы с «## Решения» в ``facts.md``. Только
читает.

Поток «ответ человека в TG → решение в теме»:

    tide truth inbox [--since YYYY-MM-DD] [--json]
        пары «вопрос агента → ответ человека» из журнала пульта (dialog.jsonl),
        для которых нет решения ни в одном decisions-tg.jsonl ростера.
    tide truth decide --topic <проект>/<NN-slug> --q N --a M[,K…] --decision "…"
        единственный путь записи: строка в журнал темы + строка в «## Решения».

Пара берётся из журнала пульта двумя путями: строка ``kind: ask-closed``
``{ask, source}``, где ``ask`` — исходящее (номер tg или id события log.jsonl),
``source`` — входящее сообщение человека; либо входящее с ``reply_to_tg`` на
исходящее. Битые строки журнала пропускаются. Пара «уже записана», если номер
ответа есть в ``answer.tg`` какой-либо записи или номер вопроса равен
``question.tg``. Пары, которые сознательно не пишут (личное, вопрос свёрнут без
ответа), лежат строками ``{q_tg, a_tg, reason}`` в
``<корень management>/.tide/topics/decisions-skip.jsonl``.

Где журнал пульта: ``--dialog PATH`` → ``$TIDE_TG_DIALOG`` → корень проекта
``management`` из ростера + :data:`DIALOG_REL`. Журнал событий —
``--log PATH`` → ``$TIDE_CONTROL_LOG`` → тот же корень + :data:`LOG_REL`.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date as _date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import truth
from . import truth_edits as edits

JOURNAL_FILE = "decisions-tg.jsonl"
DECISIONS_HEAD = "## Решения"

KEYS = ("id", "date", "project", "topic", "question", "answer", "decision",
        "handed_to", "soc", "status", "supersedes", "superseded_by", "note",
        "src", "valid_from")
STATUSES = ("принято", "заменено")

_ID_RE = re.compile(r"^tg-\d+(-[0-9A-Za-zА-Яа-яЁё_-]+)?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TG_REF_RE = re.compile(r"`tg:(\d+)`")


# --- схема ------------------------------------------------------------------

def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_iso_tz(v) -> bool:
    if not isinstance(v, str):
        return False
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return False
    return dt.tzinfo is not None


def src_for(rec: Dict) -> Optional[str]:
    """``tg:<answer.tg[0]>`` или None, если номера ответа нет."""
    ans = rec.get("answer")
    tgs = ans.get("tg") if isinstance(ans, dict) else None
    if isinstance(tgs, list) and tgs and _is_int(tgs[0]):
        return "tg:{0}".format(tgs[0])
    return None


def normalize(rec: Dict) -> Dict:
    """Копия *rec* с ``src`` и ``valid_from`` (если их не было) и ключами по :data:`KEYS`.

    Неизвестные ключи сохраняются, идут после схемных.
    """
    out = dict(rec)
    if "src" not in out:
        src = src_for(out)
        if src is not None:
            out["src"] = src
    if "valid_from" not in out:
        ans = out.get("answer")
        if isinstance(ans, dict) and "at" in ans:
            out["valid_from"] = ans["at"]
    ordered = {k: out[k] for k in KEYS if k in out}
    ordered.update((k, v) for k, v in out.items() if k not in ordered)
    return ordered


def validate(rec, project: Optional[str] = None, topic: Optional[str] = None) -> List[str]:
    """Ошибки схемы записи *rec* (пустой список — запись верна).

    *project* / *topic* — ожидаемые проект и slug темы (по месту журнала);
    None — не сверять.
    """
    if not isinstance(rec, dict):
        return ["запись не объект JSON"]
    errs: List[str] = []
    missing = [k for k in KEYS if k not in rec]
    if missing:
        errs.append("нет ключей: " + ", ".join(missing))

    def has(k):
        return k in rec

    for k in ("id", "date", "project", "topic", "decision", "status", "src", "valid_from"):
        if has(k) and not (isinstance(rec[k], str) and rec[k].strip()):
            errs.append("{0}: нужна непустая строка".format(k))
    for k in ("supersedes", "superseded_by", "note"):
        if has(k) and rec[k] is not None and not isinstance(rec[k], str):
            errs.append("{0}: нужна строка или null".format(k))
    for k in ("handed_to", "soc"):
        if has(k) and not (isinstance(rec[k], list) and all(isinstance(x, str) for x in rec[k])):
            errs.append("{0}: нужен список строк".format(k))

    if isinstance(rec.get("id"), str) and rec["id"] and not _ID_RE.match(rec["id"]):
        errs.append("id «{0}»: нужен вид tg-<число>[-суффикс]".format(rec["id"]))
    if isinstance(rec.get("date"), str) and rec["date"] and not _DATE_RE.match(rec["date"]):
        errs.append("date «{0}»: нужен YYYY-MM-DD".format(rec["date"]))

    q = rec.get("question")
    if has("question"):
        if q is None:
            if not (isinstance(rec.get("note"), str) and rec["note"].strip()):
                errs.append("question null: нужна note — почему решение без вопроса")
        elif not isinstance(q, dict):
            errs.append("question: нужен объект")
        else:
            if not _is_int(q.get("tg")):
                errs.append("question.tg: нужно число")
            for k in ("ask", "text"):
                if not isinstance(q.get(k), str):
                    errs.append("question.{0}: нужна строка".format(k))
            if not _is_iso_tz(q.get("at")):
                errs.append("question.at: нужен ISO с поясом")
    a = rec.get("answer")
    if has("answer"):
        if not isinstance(a, dict):
            errs.append("answer: нужен объект")
        else:
            tgs = a.get("tg")
            if not (isinstance(tgs, list) and tgs and all(_is_int(x) for x in tgs)):
                errs.append("answer.tg: нужен непустой список чисел")
            if not isinstance(a.get("text"), str):
                errs.append("answer.text: нужна строка")
            if not _is_iso_tz(a.get("at")):
                errs.append("answer.at: нужен ISO с поясом")

    if has("valid_from") and isinstance(rec["valid_from"], str) and rec["valid_from"] \
            and not _is_iso_tz(rec["valid_from"]):
        errs.append("valid_from: нужен ISO с поясом")
    if has("src") and isinstance(rec["src"], str):
        want = src_for(rec)
        if want is not None and rec["src"] != want:
            errs.append("src «{0}»: должен быть {1} (первый номер answer.tg)".format(
                rec["src"], want))

    st = rec.get("status")
    if has("status") and isinstance(st, str) and st not in STATUSES:
        errs.append("status «{0}»: только {1}".format(st, " | ".join(STATUSES)))
    if st == "заменено" and not rec.get("superseded_by"):
        errs.append("status заменено: нужен superseded_by")

    if topic is not None and isinstance(rec.get("topic"), str) and rec["topic"] != topic:
        errs.append("topic «{0}»: журнал лежит в теме {1}".format(rec["topic"], topic))
    if project is not None and isinstance(rec.get("project"), str) and rec["project"] != project:
        errs.append("project «{0}»: журнал лежит в проекте {1}".format(rec["project"], project))
    return errs


# --- журнал -----------------------------------------------------------------

DISK_KEYS = ("src", "valid_from")


def load(path: Path, project: Optional[str] = None, topic: Optional[str] = None,
         lines_by_id: Optional[Dict[str, List[int]]] = None) -> Tuple[List[Dict], List[str]]:
    """Журнал *path* → ``(решения, ошибки)``.

    Каждая строка нормализуется (:func:`normalize`) и проверяется
    (:func:`validate`); ошибка — ``файл:строка: текст``. Решения — по одному на
    id, действует последняя строка с этим id; порядок — по первому появлению id.
    У каждого решения ключ ``_line`` — номер его действующей строки.
    *topic* по умолчанию — имя каталога журнала. *lines_by_id* (если дан)
    наполняется номерами всех строк каждого id.

    ``src`` и ``valid_from`` обязаны лежать на диске: :func:`normalize`
    дописывает их только в памяти, и без этой проверки строка без них прошла бы.
    """
    path = Path(path)
    topic = topic if topic is not None else path.parent.name
    by_id: Dict[str, Dict] = {}
    errs: List[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return [], ["{0}: не читается ({1})".format(path, e)]
    for i, ln in enumerate(text.splitlines(), 1):
        if not ln.strip():
            continue
        try:
            raw = json.loads(ln)
        except ValueError as e:
            errs.append("{0}:{1}: не JSON ({2})".format(path, i, e))
            continue
        if not isinstance(raw, dict):
            errs.append("{0}:{1}: запись не объект JSON".format(path, i))
            continue
        rec = normalize(raw)
        errs.extend("{0}:{1}: {2}".format(path, i, e)
                    for e in validate(rec, project, topic))
        absent = [k for k in DISK_KEYS if k not in raw]
        if absent:
            errs.append("{0}:{1}: нет на диске: {2} (при чтении дописаны, в файле их нет)"
                        .format(path, i, ", ".join(absent)))
        rid = rec.get("id")
        if isinstance(rid, str) and rid:
            rec["_line"] = i
            if lines_by_id is not None:
                lines_by_id.setdefault(rid, []).append(i)
            by_id[rid] = rec  # последняя по id главнее; место — первое
    return list(by_id.values()), errs


# --- зеркало в facts.md -----------------------------------------------------

def decision_rows(facts_lines: List[str]) -> Optional[List[Tuple[int, str]]]:
    """Строки-пункты секции «## Решения» ``[(номер, строка)]``; None — секции нет."""
    rows: Optional[List[Tuple[int, str]]] = None
    inside = False
    for i, ln in enumerate(facts_lines, 1):
        if ln.startswith("## "):
            inside = ln.strip().lower().startswith(DECISIONS_HEAD.lower())
            if inside and rows is None:
                rows = []
            continue
        if inside and ln.startswith("- "):
            rows.append((i, ln))
    return rows


def check_facts(facts_path: Path, records: List[Dict],
                journal_path: Optional[Path] = None) -> List[str]:
    """Сверка «## Решения» в *facts_path* с решениями журнала.

    Каждая строка-пункт секции несёт хотя бы одну ссылку ```tg:<число>```; для
    каждого решения журнала в секции есть строка с одним из его ``answer.tg``.
    """
    facts_path = Path(facts_path)
    jp = journal_path if journal_path is not None else facts_path.parent / JOURNAL_FILE
    try:
        lines = facts_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = None
    if lines is None:
        if records:
            return ["{0}: facts.md нет, а в журнале решений {1}".format(facts_path, len(records))]
        return []
    rows = decision_rows(lines)
    if rows is None:
        if records:
            return ["{0}: нет секции «{1}», а в журнале решений {2}".format(
                facts_path, DECISIONS_HEAD, len(records))]
        return []
    errs: List[str] = []
    refs = set()
    for i, ln in rows:
        found = [int(n) for n in _TG_REF_RE.findall(ln)]
        if not found:
            errs.append("{0}:{1}: строка решения без ссылки `tg:<число>`".format(facts_path, i))
        refs.update(found)
    for rec in records:
        ans = rec.get("answer")
        tgs = ans.get("tg") if isinstance(ans, dict) else None
        if not isinstance(tgs, list):
            continue
        if not any(_is_int(n) and n in refs for n in tgs):
            errs.append("{0}:{1}: решение {2} не отражено в facts.md «{3}» "
                        "(нет строки с `tg:{4}`)".format(
                            jp, rec.get("_line", "?"), rec.get("id"), DECISIONS_HEAD,
                            "`/`tg:".join(str(n) for n in tgs)))
    return errs


# --- обход ------------------------------------------------------------------

def live_rows(facts_lines: List[str]) -> List[Tuple[int, str]]:
    """Строки «## Решения» без пометки «заменено →» — действующие решения."""
    return [(i, ln) for i, ln in (decision_rows(facts_lines) or [])
            if truth.REPLACED_MARK not in ln]


def duplicates(lines_by_id: Dict[str, List[int]], records: List[Dict]) -> List[str]:
    """Пометки о повторных id: правка записи (последняя строка главнее).

    Не ошибка — так схема правит запись; что правка прошла через журнал,
    сверяет журнал правок, а лишнюю строку в «## Решения» ловит счёт.
    """
    by = {r.get("id"): r for r in records}
    out = []
    for rid, nums in lines_by_id.items():
        if len(nums) < 2:
            continue
        r = by.get(rid) or {}
        if r.get("status") == "заменено" and r.get("superseded_by"):
            continue
        out.append("{0}: строк {1} ({2}) без superseded_by — правка записи, действует "
                   "строка {3}".format(rid, len(nums), ", ".join(map(str, nums)), nums[-1]))
    return out


def check_topic(project: str, tdir: Path) -> Optional[Dict]:
    """Проверка одной темы: ``{project, slug, decisions, replaced, errors, notes}``.

    ``decisions`` — действующие решения (``принято``, последняя строка по id);
    столько же должно быть строк «## Решения» без «заменено →» — так же считает
    сводка ``tide truth``. Ещё сверяется журнал правок (:mod:`tide.truth_edits`).
    None — у темы нет ни журнала, ни facts.md, ни журнала правок.
    """
    tdir = Path(tdir)
    jp = tdir / JOURNAL_FILE
    fp = tdir / truth.FACTS_FILE
    has_journal = jp.is_file()
    if not (has_journal or fp.is_file() or (tdir / edits.EDITS_FILE).is_file()):
        return None
    facts_lines = truth._read_lines(fp) if fp.is_file() else []
    lines_by_id: Dict[str, List[int]] = {}
    records, errs = load(jp, project, tdir.name, lines_by_id) if has_journal else ([], [])
    errs = errs + check_facts(fp, records, jp)
    active = [r for r in records if r.get("status") != "заменено"]
    live = live_rows(facts_lines)
    if (has_journal or live) and len(live) != len(active):
        errs.append("{0}: в «{1}» действующих строк {2}, а действующих решений в журнале {3} "
                    "— лишнюю строку пометь «({4} …)»".format(
                        fp, DECISIONS_HEAD, len(live), len(active), truth.REPLACED_MARK))
    errs = errs + edits.check_dir(tdir)
    return {"project": project, "slug": tdir.name, "dir": str(tdir),
            "decisions": len(active), "replaced": len(records) - len(active),
            "errors": errs, "notes": duplicates(lines_by_id, records)}


def check_skip_root(project: str, root: Path) -> Optional[Dict]:
    """Журнал правок ``.tide/topics/`` (там decisions-skip.jsonl)."""
    d = Path(root) / ".tide" / truth.TOPICS_DIRNAME
    if not ((d / SKIP_FILE).is_file() or (d / edits.EDITS_FILE).is_file()):
        return None
    return {"project": project, "slug": "(" + SKIP_FILE + ")", "dir": str(d),
            "decisions": 0, "replaced": 0, "skips": len(read_jsonl(d / SKIP_FILE)),
            "errors": edits.check_dir(d), "notes": []}


def check(projs: List[Tuple[str, Path]]) -> List[Dict]:
    """Все темы проектов *projs* (включая архивные и «не читать»)."""
    out = []
    for t in truth.list_topics(projs, show_all=True):
        res = check_topic(t["project"], Path(t["dir"]))
        if res is not None:
            out.append(res)
    for name, root in projs:
        res = check_skip_root(name, root)
        if res is not None:
            out.append(res)
    return out


def render_check(results: List[Dict]) -> str:
    if not results:
        return "журналов решений нет (ни decisions-tg.jsonl, ни «## Решения» в темах)"
    out = []
    for r in results:
        head = "{0}/{1}".format(r["project"], r["slug"])
        what = ("{0} пропусков".format(r["skips"]) if "skips" in r
                else "{0} решений".format(r["decisions"]))
        if r.get("replaced"):
            what += " (+{0} заменено)".format(r["replaced"])
        if not r["errors"]:
            out.append("{0}: ок {1}".format(head, what))
        else:
            out.append("{0}: ошибок {1} ({2})".format(head, len(r["errors"]), what))
            out.extend("  " + e for e in r["errors"])
        out.extend("  правка записи: " + n for n in r.get("notes") or [])
    bad = sum(len(r["errors"]) for r in results)
    out.append("итого: тем {0}, решений {1}, ошибок {2}".format(
        len(results), sum(r["decisions"] for r in results), bad))
    return "\n".join(out)


def cmd_check(args) -> int:
    """``tide truth check [--project P] [--json]`` — код 1, если есть ошибки."""
    projs = truth.resolve_projects(getattr(args, "project", None))
    results = check(projs)
    if getattr(args, "as_json", False):
        print(json.dumps(results, ensure_ascii=False, indent=1))
    else:
        print(render_check(results))
    return 1 if any(r["errors"] for r in results) else 0


# --- журнал пульта (dialog.jsonl) и журнал событий (log.jsonl) ---------------

MANAGEMENT = "management"
DIALOG_REL = ".tide/arcs/01-@ao-control/arcs/02-tg-pult/workspace/tg-pult/dialog.jsonl"
LOG_REL = ".tide/arcs/01-@ao-control/arcs/01-control/output/log.jsonl"
DIALOG_ENV = "TIDE_TG_DIALOG"
LOG_ENV = "TIDE_CONTROL_LOG"
SKIP_FILE = "decisions-skip.jsonl"
FALLBACK_MANAGEMENT = Path.home() / "Documents" / MANAGEMENT
SHORT_INBOX = 80
UNKNOWN_Q = "(вопроса нет в журнале пульта)"
SHORT_FACT = 100

_VOICE_RE = re.compile(r"\[tg 🎤\]\s*")
_AUDIO_RE = re.compile(r"\s*\(аудио: [^)]*\)")
_TAG_RE = re.compile(r"</?[a-zA-Z][^<>]*>")
_DATE_TAIL_RE = re.compile(r" · ≈?\d{2}\.\d{2}\.\d{4} · `")


def read_jsonl(path: Path) -> List[Dict]:
    """Строки-объекты JSONL *path*; битые строки и не-объекты пропускаются."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    out: List[Dict] = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def management_root(projs: List[Tuple[str, Path]]) -> Path:
    for name, root in projs:
        if name == MANAGEMENT:
            return Path(root)
    return FALLBACK_MANAGEMENT


def pick_path(flag: Optional[str], env: str, rel: str,
              projs: List[Tuple[str, Path]]) -> Path:
    """Флаг → переменная окружения → корень management из ростера + *rel*."""
    if flag:
        return Path(flag).expanduser()
    if os.environ.get(env):
        return Path(os.environ[env]).expanduser()
    return management_root(projs) / rel


def clean_text(text: str, html: bool = False) -> str:
    """Текст сообщения одной строкой: без «[tg 🎤]», «(аудио: …)», html-тегов."""
    s = _AUDIO_RE.sub("", _VOICE_RE.sub("", text or ""))
    if html:
        import html as _html
        s = _html.unescape(_TAG_RE.sub("", s))
    return " ".join(s.split())


def short(text: str, n: int) -> str:
    """Первые ~*n* символов по границе слова, с «…», если обрезано."""
    if len(text) <= n:
        return text
    cut = text[:n]
    sp = cut.rfind(" ")
    if sp > n // 2:
        cut = cut[:sp]
    return cut.rstrip(" ,.;:—-") + "…"


def local_dt(iso: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return dt.astimezone() if dt.tzinfo else dt


def _ts_key(iso: str) -> float:
    dt = local_dt(iso)
    return dt.timestamp() if dt is not None and dt.tzinfo else 0.0


class Pult:
    """Индекс журнала пульта: исходящие и входящие по tg-номеру, ask-closed, лог."""

    def __init__(self, dialog_rows: List[Dict], log_rows: List[Dict]):
        self.out: Dict[int, Dict] = {}
        self.inc: Dict[int, Dict] = {}
        self.closed: List[Dict] = []
        self._out_by_text: Dict[str, int] = {}
        for r in dialog_rows:
            d, k = r.get("dir"), r.get("kind")
            if d == "out":
                ids = [n for n in (r.get("tg_msg_ids") or []) if _is_int(n)]
                for n in ids:
                    self.out.setdefault(n, r)
                t = (r.get("text") or "").strip()
                if ids and t:
                    self._out_by_text.setdefault(t, ids[0])
            elif d == "in" and k == "tg" and _is_int(r.get("tg_msg_id")):
                self.inc.setdefault(r["tg_msg_id"], r)
            elif k == "ask-closed":
                self.closed.append(r)
        self.log: Dict[str, Dict] = {}
        self._log_by_text: Dict[str, str] = {}
        for r in log_rows:
            rid = r.get("id")
            if not isinstance(rid, str):
                continue
            self.log.setdefault(rid, r)
            t = (r.get("text") or "").strip()
            if t and r.get("actor") != "grisha":
                self._log_by_text.setdefault(t, rid)

    @classmethod
    def from_files(cls, dialog: Path, log: Path) -> "Pult":
        return cls(read_jsonl(dialog), read_jsonl(log))

    def tg_of(self, ref) -> Optional[int]:
        """Номер tg по ссылке ask/source: число, «tg:N» или id события log.jsonl."""
        if _is_int(ref):
            return ref
        if not isinstance(ref, str):
            return None
        s = ref.strip()
        if s.isdigit():
            return int(s)
        if s.startswith("tg:") and s[3:].isdigit():
            return int(s[3:])
        rec = self.log.get(s)
        if rec is None:
            return None
        if _is_int(rec.get("tg_msg_id")):
            return rec["tg_msg_id"]
        return self._out_by_text.get((rec.get("text") or "").strip())

    def ask_of(self, q: int) -> str:
        """id события лога для вопроса *q* (из ask-closed или по тексту), иначе ``tg:q``."""
        for r in self.closed:
            a = r.get("ask")
            if isinstance(a, str) and a in self.log and self.tg_of(a) == q:
                return a
        row = self.out.get(q)
        if row is not None:
            rid = self._log_by_text.get((row.get("text") or "").strip())
            if rid:
                return rid
        return "tg:{0}".format(q)

    def text_of(self, row: Dict) -> str:
        return clean_text(row.get("text") or "", row.get("format") == "html")

    def pairs(self) -> List[Dict]:
        """Пары «вопрос агента → ответ человека», по времени ответа."""
        seen: Dict[Tuple[int, int], Dict] = {}

        def add(q, a, unknown_ok=False):
            if q is None or a is None or a not in self.inc or (q, a) in seen:
                return
            if q not in self.out and not (unknown_ok and q not in self.inc):
                return
            qr, ar = self.out.get(q), self.inc[a]
            question = {"tg": q, "ask": self.ask_of(q), "at": "", "text": UNKNOWN_Q}
            if qr is not None:
                question.update(at=qr.get("ts", ""), text=self.text_of(qr))
            seen[(q, a)] = {
                "question": question,
                "answer": {"tg": a, "at": ar.get("ts", ""), "text": self.text_of(ar)},
            }

        for r in self.closed:
            add(self.tg_of(r.get("ask")), self.tg_of(r.get("source")))
        for a, r in self.inc.items():
            q = r.get("reply_to_tg")
            if _is_int(q):
                # реплай на сообщение, которого нет в журнале пульта (агент слал
                # мимо пульта) — тоже ответ человека, показываем с пометкой
                add(q, a, unknown_ok=True)
        return sorted(seen.values(), key=lambda p: (p["answer"]["at"], p["question"]["tg"]))


# --- tide truth inbox -------------------------------------------------------

def journals(projs: List[Tuple[str, Path]]) -> List[Tuple[str, Path]]:
    """``[(проект, путь журнала)]`` всех тем проектов *projs*, где журнал есть."""
    out = []
    for t in truth.list_topics(projs, show_all=True):
        jp = Path(t["dir"]) / JOURNAL_FILE
        if jp.is_file():
            out.append((t["project"], jp))
    return out


def recorded(projs: List[Tuple[str, Path]]) -> Tuple[set, set]:
    """``(номера ответов, номера вопросов)`` всех строк всех журналов."""
    answers, questions = set(), set()
    for _, jp in journals(projs):
        for r in read_jsonl(jp):
            q, a = r.get("question"), r.get("answer")
            if isinstance(q, dict) and _is_int(q.get("tg")):
                questions.add(q["tg"])
            if isinstance(a, dict) and isinstance(a.get("tg"), list):
                answers.update(n for n in a["tg"] if _is_int(n))
    return answers, questions


def load_skip(path: Path) -> set:
    """``{(q_tg, a_tg)}`` из decisions-skip.jsonl."""
    out = set()
    for r in read_jsonl(path):
        try:
            out.add((int(r.get("q_tg")), int(r.get("a_tg"))))
        except (TypeError, ValueError):
            continue
    return out


def inbox(pult: Pult, projs: List[Tuple[str, Path]], since: _date,
          skip: set) -> Tuple[List[Dict], int]:
    """Пары без решения с ответом не раньше *since* (местная дата) и число пропущенных."""
    answers, questions = recorded(projs)
    pending, skipped = [], 0
    for p in pult.pairs():
        at = local_dt(p["answer"]["at"])
        if at is None or at.date() < since:
            continue
        q, a = p["question"]["tg"], p["answer"]["tg"]
        if a in answers or q in questions:
            continue
        if (q, a) in skip:
            skipped += 1
            continue
        pending.append(p)
    return pending, skipped


def render_inbox(pending: List[Dict], skipped: int) -> str:
    out = []
    for p in pending:
        at = local_dt(p["answer"]["at"])
        when = at.strftime("%d.%m %H:%M") if at else "?"
        out.append("{0}  {1} → {2}  «{3}» → «{4}»".format(
            when, p["question"]["tg"], p["answer"]["tg"],
            short(p["question"]["text"], SHORT_INBOX), short(p["answer"]["text"], SHORT_INBOX)))
    out.append("без решения: {0}".format(len(pending)))
    out.append("пропущено: {0}".format(skipped))
    return "\n".join(out)


def _since(args) -> _date:
    raw = getattr(args, "since", None)
    if not raw:
        return _date.today()
    try:
        return _date.fromisoformat(raw)
    except ValueError:
        raise truth.TruthError("inbox: --since «{0}»: нужен YYYY-MM-DD".format(raw))


def _pult(args, projs) -> Pult:
    dialog = pick_path(getattr(args, "dialog", None), DIALOG_ENV, DIALOG_REL, projs)
    if not dialog.is_file():
        raise truth.TruthError("нет журнала пульта: {0} (--dialog / ${1})".format(
            dialog, DIALOG_ENV))
    log = pick_path(getattr(args, "control_log", None), LOG_ENV, LOG_REL, projs)
    return Pult.from_files(dialog, log)


def cmd_inbox(args) -> int:
    """``tide truth inbox [--since YYYY-MM-DD] [--json]``."""
    projs = truth.resolve_projects(None)
    since = _since(args)
    pult = _pult(args, projs)
    skip = load_skip(management_root(projs) / ".tide" / truth.TOPICS_DIRNAME / SKIP_FILE)
    pending, skipped = inbox(pult, projs, since, skip)
    if getattr(args, "as_json", False):
        print(json.dumps(pending, ensure_ascii=False, indent=1))
    else:
        print(render_inbox(pending, skipped))
    return 0


# --- tide truth decide ------------------------------------------------------

def resolve_topic(projs: List[Tuple[str, Path]], query: str) -> Dict:
    hits = truth.select(truth.list_topics(projs, show_all=True), query)
    if not hits:
        raise truth.TruthError("decide: топика нет, заведи: «{0}» "
                               "(<проект>/.tide/topics/NN-slug/topic.md)".format(query))
    if len(hits) > 1:
        raise truth.TruthError("decide: «{0}» — несколько тем: {1}; дай <проект>/<NN-slug>"
                               .format(query, ", ".join("{0}/{1}".format(t["project"], t["slug"])
                                                        for t in hits)))
    return hits[0]


def _parse_answers(raw) -> List[int]:
    out = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise truth.TruthError("decide: --a «{0}»: нужны номера tg через запятую".format(raw))
        out.append(int(part))
    if not out:
        raise truth.TruthError("decide: --a: нужен хотя бы один номер ответа")
    return out


def build_record(pult: Pult, project: str, slug: str, q: int, answers: List[int],
                 decision: str, rid: Optional[str] = None, handed_to=None, soc=None,
                 note: Optional[str] = None, supersedes: Optional[str] = None) -> Dict:
    """Запись решения: тексты и моменты — из журнала пульта.

    *q* None — решение без вопроса: ``question: null``, id по первому ответу.
    """
    if q is not None and q not in pult.out:
        raise truth.TruthError("decide: вопроса tg:{0} нет среди исходящих журнала пульта "
                               "(текст и время вопроса взять неоткуда)".format(q))
    missing = [a for a in answers if a not in pult.inc]
    if missing:
        raise truth.TruthError("decide: ответа {0} нет среди входящих журнала пульта".format(
            ", ".join("tg:{0}".format(a) for a in missing)))
    ars = [pult.inc[a] for a in answers]
    a_at = max((r.get("ts", "") for r in ars), key=_ts_key)
    day = local_dt(a_at)
    question = None
    if q is not None:
        qr = pult.out[q]
        question = {"tg": q, "ask": pult.ask_of(q), "at": qr.get("ts", ""),
                    "text": pult.text_of(qr)}
    rec = {
        "id": rid or "tg-{0}".format(q if q is not None else answers[0]),
        "date": day.date().isoformat() if day else "",
        "project": project, "topic": slug,
        "question": question,
        "answer": {"tg": list(answers), "at": a_at,
                   "text": " / ".join(pult.text_of(r) for r in ars)},
        "decision": decision, "handed_to": list(handed_to or []), "soc": list(soc or []),
        "status": "принято", "supersedes": supersedes, "superseded_by": None,
        "note": note,
    }
    return normalize(rec)


def facts_row(rec: Dict, fact: Optional[str] = None) -> str:
    """Строка «## Решения»: ``- решение: … — ответ Гриши «…» · DD.MM.YYYY · `tg:q`, `tg:a```."""
    y, m, d = rec["date"].split("-")
    q = (rec.get("question") or {}).get("tg")
    nums = ([q] if q is not None else []) + [n for n in rec["answer"]["tg"] if n != q]
    refs = ", ".join("`tg:{0}`".format(n) for n in nums)
    body = fact.strip() if fact else "решение: {0} — ответ Гриши «{1}»".format(
        rec["decision"], short(rec["answer"]["text"], SHORT_FACT))
    return "- {0} · {1}.{2}.{3} · {4}".format(body, d, m, y, refs)


def add_decision_row(lines: List[str], row: str) -> List[str]:
    """*lines* facts.md с *row* в конце секции «## Решения» (секцию создаёт)."""
    lines = list(lines)
    head = next((i for i, ln in enumerate(lines)
                 if ln.startswith("## ") and ln.strip().lower().startswith(DECISIONS_HEAD.lower())),
                None)
    if head is None:
        while lines and not lines[-1].strip():
            lines.pop()
        if lines:
            lines.append("")
        return lines + [DECISIONS_HEAD, "", row]
    end = next((i for i in range(head + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    bullets = [i for i in range(head + 1, end) if lines[i].startswith("- ")]
    if bullets:
        at = bullets[-1] + 1
        while at < end and lines[at].startswith("  "):  # продолжение пункта
            at += 1
        lines.insert(at, row)
    else:
        lines[head + 1:head + 1] = ["", row]
    return lines


def mark_replaced(lines: List[str], old: Dict, new_id: str) -> Tuple[List[str], Optional[int]]:
    """Пометка «(заменено → new_id)» у строки старого решения; номер строки (с 0) или None."""
    rows = decision_rows(lines) or []
    tgs = [n for n in (old.get("answer") or {}).get("tg", []) if _is_int(n)]
    cands = [(i - 1, ln) for i, ln in rows
             if truth.REPLACED_MARK not in ln
             and any("`tg:{0}`".format(n) in ln for n in tgs)]
    if not cands:
        return list(lines), None
    q = (old.get("question") or {}).get("tg")
    head = (old.get("decision") or "")[:40]
    cands.sort(key=lambda c: ("`tg:{0}`".format(q) not in c[1], head not in c[1]))
    idx, ln = cands[0]
    mark = " ({0} {1})".format(truth.REPLACED_MARK, new_id)
    m = _DATE_TAIL_RE.search(ln)
    new = ln[:m.start()] + mark + ln[m.start():] if m else ln + mark
    out = list(lines)
    out[idx] = new
    return out, idx


def _dump(rec: Dict) -> str:
    return json.dumps({k: v for k, v in rec.items() if not k.startswith("_")},
                      ensure_ascii=False)


def decide(pult: Pult, topic: Dict, q: Optional[int], answers: List[int], decision: str,
           rid: Optional[str] = None, handed_to=None, soc=None, note: Optional[str] = None,
           supersedes: Optional[str] = None, fact: Optional[str] = None,
           dry_run: bool = False) -> Dict:
    """Записать решение в журнал темы и «## Решения». Ошибка — :class:`truth.TruthError`.

    Запись идёт через журнал правок (:func:`tide.truth_edits.guard`): файлы
    темы разошлись с ним — ничего не пишется. Возвращает
    ``{journal, facts, lines, facts_row, marked, written}``.
    """
    tdir = Path(topic["dir"])
    jp, fp = tdir / JOURNAL_FILE, tdir / truth.FACTS_FILE
    project, slug = topic["project"], tdir.name
    rec = build_record(pult, project, slug, q, answers, decision, rid, handed_to, soc,
                       note, supersedes)
    existing, _ = load(jp, project, slug) if jp.is_file() else ([], [])
    by_id = {r["id"]: r for r in existing if isinstance(r.get("id"), str)}
    new_lines: List[Dict] = []
    old = None
    if supersedes:
        old = by_id.get(supersedes)
        if old is None:
            raise truth.TruthError("decide: --supersedes {0}: такого id в {1} нет".format(
                supersedes, jp))
        if supersedes == rec["id"]:
            raise truth.TruthError("decide: новый id совпадает с заменяемым ({0}) — дай --id"
                                   .format(supersedes))
        old_copy = dict(old)
        old_copy.pop("_line", None)
        old_copy.update(status="заменено", superseded_by=rec["id"])
        new_lines.append(normalize(old_copy))
    if rec["id"] in by_id:
        raise truth.TruthError("decide: id занят: {0} ({1}:{2}); дай --id или --supersedes"
                               .format(rec["id"], jp, by_id[rec["id"]].get("_line", "?")))
    new_lines.append(rec)
    errs = []
    for r in new_lines:
        errs.extend("{0}: {1}".format(r.get("id"), e) for e in validate(r, project, slug))
    if errs:
        raise truth.TruthError("decide: запись не прошла проверку, ничего не записано:\n  "
                               + "\n  ".join(errs))

    try:
        facts_text = fp.read_text(encoding="utf-8")
    except OSError:
        facts_text = ""
    lines = facts_text.splitlines()
    marked = None
    if old is not None:
        lines, idx = mark_replaced(lines, old, rec["id"])
        marked = lines[idx] if idx is not None else None
    row = facts_row(rec, fact)
    lines = add_decision_row(lines, row)

    res = {"journal": str(jp), "facts": str(fp), "lines": [_dump(r) for r in new_lines],
           "facts_row": row, "marked": marked, "replaced_missing": old is not None and marked is None,
           "written": False}
    if dry_run:
        return res
    try:
        with edits.guard(tdir, "decide", rid=rec["id"], src=rec.get("src"),
                         note=("заменяет " + supersedes) if supersedes else None):
            _append_lines(jp, res["lines"])
            fp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except edits.EditsError as e:
        raise truth.TruthError("decide: " + str(e))
    res["written"] = True
    return res


def _append_lines(path: Path, lines: List[str]) -> None:
    prefix = ""
    if path.is_file():
        cur = path.read_text(encoding="utf-8")
        if cur and not cur.endswith("\n"):
            prefix = "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(prefix + "".join(ln + "\n" for ln in lines))


def render_decide(res: Dict) -> str:
    out = ["dry-run: ничего не записано" if not res["written"] else "записано"]
    out.append("журнал {0}:".format(res["journal"]))
    out.extend("+ " + ln for ln in res["lines"])
    out.append("facts {0}:".format(res["facts"]))
    if res["marked"]:
        out.append("~ " + res["marked"])
    elif res["replaced_missing"]:
        out.append("! строку заменяемого решения в «## Решения» не нашёл — пометки нет")
    out.append("+ " + res["facts_row"])
    return "\n".join(out)


def cmd_decide(args) -> int:
    """``tide truth decide --topic P/NN-slug --q N --a M[,K] --decision "…" […]``."""
    no_q = bool(getattr(args, "d_no_question", False))
    need = [f for f, v in (("--topic", args.d_topic), ("--a", args.d_a),
                           ("--decision", args.d_decision)) if v in (None, "")]
    if not no_q and args.d_q is None:
        need.insert(1, "--q (или --no-question)")
    if need:
        raise truth.TruthError("decide: нужны {0}".format(", ".join(need)))
    if no_q and args.d_q is not None:
        raise truth.TruthError("decide: --q и --no-question вместе нельзя")
    if no_q and not (args.d_note or "").strip():
        raise truth.TruthError("decide: --no-question требует --note «почему без вопроса»")
    projs = truth.resolve_projects(None)
    topic = resolve_topic(projs, args.d_topic)
    pult = _pult(args, projs)
    res = decide(pult, topic, None if no_q else int(args.d_q), _parse_answers(args.d_a),
                 args.d_decision,
                 rid=args.d_id, handed_to=args.d_handed_to, soc=args.d_soc, note=args.d_note,
                 supersedes=args.d_supersedes, fact=args.d_fact, dry_run=args.d_dry_run)
    if getattr(args, "as_json", False):
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(render_decide(res))
    return 0


# --- tide truth skip / baseline ----------------------------------------------

def skip(projs: List[Tuple[str, Path]], q: int, a: int, reason: str,
         dry_run: bool = False) -> Dict:
    """Пара *q*→*a* сознательно без решения: строка в decisions-skip.jsonl
    management (через журнал правок)."""
    if not (reason or "").strip():
        raise truth.TruthError("skip: нужна --reason — почему пару не пишут")
    d = management_root(projs) / ".tide" / truth.TOPICS_DIRNAME
    path = d / SKIP_FILE
    if (q, a) in load_skip(path):
        raise truth.TruthError("skip: пара {0} → {1} уже в {2}".format(q, a, path))
    row = {"q_tg": q, "a_tg": a, "reason": reason.strip(),
           "at": datetime.now().astimezone().isoformat(timespec="seconds")}
    line = json.dumps(row, ensure_ascii=False)
    res = {"file": str(path), "line": line, "written": False}
    if dry_run:
        return res
    d.mkdir(parents=True, exist_ok=True)
    try:
        with edits.guard(d, "skip", src="tg:{0}".format(a), note=reason.strip()):
            _append_lines(path, [line])
    except edits.EditsError as e:
        raise truth.TruthError("skip: " + str(e))
    res["written"] = True
    return res


def cmd_skip(args) -> int:
    """``tide truth skip --q N --a M --reason "…"``."""
    if args.d_q is None or not args.d_a:
        raise truth.TruthError("skip: нужны --q и --a")
    answers = _parse_answers(args.d_a)
    if len(answers) != 1:
        raise truth.TruthError("skip: --a — один номер ответа")
    res = skip(truth.resolve_projects(None), int(args.d_q), answers[0],
               getattr(args, "d_reason", None) or "", dry_run=args.d_dry_run)
    print(("записано" if res["written"] else "dry-run: ничего не записано")
          + "\n{0}:\n+ {1}".format(res["file"], res["line"]))
    return 0


def baseline_dirs(projs: List[Tuple[str, Path]]) -> List[Tuple[str, Path]]:
    """``[(имя для вывода, каталог)]``: темы с охраняемыми файлами + ``.tide/topics``."""
    out = []
    for t in truth.list_topics(projs, show_all=True):
        out.append(("{0}/{1}".format(t["project"], t["slug"]), Path(t["dir"])))
    for name, root in projs:
        d = Path(root) / ".tide" / truth.TOPICS_DIRNAME
        if (d / SKIP_FILE).is_file() or (d / edits.EDITS_FILE).is_file():
            out.append(("{0}/({1})".format(name, SKIP_FILE), d))
    return out


def cmd_baseline(args) -> int:
    """``tide truth baseline [--project P] [--topic P/slug --reason "…"] [--dry-run]``.

    Без --topic — стартовая строка во все каталоги без журнала правок. С --topic
    и --reason — признать расхождение файлов темы с журналом правок как есть.
    """
    reason = (getattr(args, "d_reason", None) or "").strip() or None
    projs = truth.resolve_projects(getattr(args, "project", None))
    if args.d_topic:
        q = args.d_topic.strip()
        if q.endswith("(" + SKIP_FILE + ")") or q.endswith("/topics"):
            name = q.split("/", 1)[0]
            dirs = [x for x in baseline_dirs(projs) if x[0] == "{0}/({1})".format(name, SKIP_FILE)]
            if not dirs:
                raise truth.TruthError("baseline: у {0} нет {1}".format(name, SKIP_FILE))
        else:
            t = resolve_topic(projs, q)
            dirs = [("{0}/{1}".format(t["project"], t["slug"]), Path(t["dir"]))]
    else:
        if reason:
            raise truth.TruthError("baseline: --reason только вместе с --topic")
        dirs = baseline_dirs(projs)
    rc = 0
    for name, d in dirs:
        last = edits.last_after(d)
        cur = edits.state(d)
        if last is not None and last == cur:
            print("{0}: журнал правок сходится — не трогаю".format(name))
            continue
        if all(v is None for v in cur.values()) and last is None:
            continue
        if last is not None and not (args.d_topic and reason):
            print("{0}: файлы разошлись с журналом правок — признать: "
                  "tide truth baseline --topic {0} --reason \"…\"".format(name))
            rc = 1
            continue
        if args.d_dry_run:
            print("{0}: dry-run — записал бы baseline {1}".format(name, cur))
            continue
        row = edits.baseline(d, reason=reason, force=bool(args.d_topic and reason))
        if row is not None:
            print("{0}: baseline → {1}".format(name, Path(d) / edits.EDITS_FILE))
    return rc


def add_arguments(p) -> None:
    """Флаги inbox/decide на парсере ``tide truth`` (зовёт truth.register)."""
    p.add_argument("--since", metavar="YYYY-MM-DD",
                   help="inbox: ответы с этого дня (по умолчанию сегодня)")
    p.add_argument("--dialog", metavar="PATH",
                   help="inbox/decide: журнал пульта (иначе ${0})".format(DIALOG_ENV))
    p.add_argument("--log", metavar="PATH", dest="control_log",
                   help="inbox/decide: журнал событий log.jsonl (иначе ${0})".format(LOG_ENV))
    p.add_argument("--topic", dest="d_topic", metavar="ПРОЕКТ/NN-slug", help="decide: тема")
    p.add_argument("--q", dest="d_q", type=int, metavar="N", help="decide: tg вопроса")
    p.add_argument("--no-question", dest="d_no_question", action="store_true",
                   help="decide: ответ без вопроса в журнале пульта (голос без реплая, "
                        "реплай на своё сообщение); нужна --note «почему»")
    p.add_argument("--a", dest="d_a", metavar="N[,M…]", help="decide: tg ответа(ов)")
    p.add_argument("--decision", dest="d_decision", help="decide: решение одной фразой")
    p.add_argument("--id", dest="d_id", help="decide: id (по умолчанию tg-<q>)")
    p.add_argument("--handed-to", dest="d_handed_to", action="append", default=[],
                   help="decide: кому передано (можно несколько)")
    p.add_argument("--soc", dest="d_soc", action="append", default=[],
                   help="decide: задача Linear SOC-N (можно несколько)")
    p.add_argument("--note", dest="d_note", help="decide: оговорка")
    p.add_argument("--supersedes", dest="d_supersedes", metavar="ID",
                   help="decide: id решения, которое это заменяет")
    p.add_argument("--fact", dest="d_fact", help="decide: свой текст строки facts.md")
    p.add_argument("--reason", dest="d_reason",
                   help="skip: почему пару не пишут; baseline --topic: почему признаём "
                        "расхождение с журналом правок")
    p.add_argument("--dry-run", dest="d_dry_run", action="store_true",
                   help="decide: показать, что запишет, и ничего не писать")

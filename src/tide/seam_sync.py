"""tide.seam_sync — шов-синк «сделано / дальше / что решить» между сессиями (SOC-143).

Слово Гриши (tg 3523): «между хендоффом должна происходить синхронизация что
сделано и что дальше делать». Старая пишет ПЕРЕДАЧУ, новая первым пульсом
пересказывает своими словами (старт-гейт — :mod:`tide.handoff_queue`). Здесь —
сверка пересказа с сидом:

* пункты сида из блоков «следующий шаг», «в работе прямо сейчас», «открытые
  хвосты» — каждый либо НАЗВАН в первых пульсах новой (:data:`FIRST_PULSES`),
  либо явно СНЯТ («снимаю: … — почему»); иначе — расхождение;
* итог — короткий список: сделано (по ПЕРЕДАЧЕ старой) · дальше (что новая
  назвала) · что решить (блок «ждёт Гришу»);
* список уходит подтверждающему: старой, пока она жива, иначе main проекта
  (``tide whois <проект>/main``). Письмо — файлом в почту нити
  (:func:`tide.mail.send`, с пометкой «шов-синк»), адресату — короткий пинг
  ``ao send`` с командой ответа: почта адресует нить, а нить уже ведёт новая;
* ответ — ``tide handoff sync ok <новая>`` или ``tide handoff sync fix <новая> "…"``
  (поправка уходит новой письмом в почту нити); итог пишется в паспорт новой
  (``sync:`` ok | fix | ждёт ответа; без ответа дольше :data:`NO_ANSWER_MIN` минут
  ``tide handoff sync status`` говорит «нет ответа»).

Порог 250–300 и сверку с Гришей каждую 3-ю передачу здесь НЕ строим — ждут Гришу.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from . import fields, paths
from .arc.stream import StreamError

FIRST_PULSES = 3                 # столько настоящих пульсов новой — автозапуск сверки
PULSES_ENV = "TIDE_SYNC_PULSES"  # потолок пульсов в сверке (по умолчанию — все после приёма)
REPO_ENV = "TIDE_SYNC_REPO"      # репо кода нити (через «:»), коммиты после ПЕРЕДАЧИ
AUTO_FRESH_HOURS = 2             # приём старше — история, автозапуск не трогает никогда
# автозапуск включён с этого момента: приёмы раньше — старые передачи, их не сверяем
# (живой дефект: context-keeper получил сверку через 7 ч после приёма, оффер 277)
AUTO_SINCE = "2026-10-09T19:25:00"
REMIND_LOOKBACK_HOURS = 48       # напоминание менеджеру смотрит приёмы не старше
REMIND_STAMP = "sync-remind.last"
REMIND_THROTTLE_SEC = 60
NO_ANSWER_MIN = 30               # ждёт ответа дольше — «нет ответа»
REPORT_NAME = "seam-sync.md"     # отчёт сверки в workspace/ новой
SENDER = "шов-синк"

SYNC = "sync"                    # ждёт ответа | ok | fix
SYNC_GAPS = "sync-gaps"          # сколько пунктов сида не названо и не снято
SYNC_TO = "sync-to"              # кто подтверждает (AO-id и роль)
SYNC_AT = "sync-at"              # когда отправлено
SYNC_LETTER = "sync-letter"      # номер письма в почте нити
SYNC_ANSWER = "sync-answer"      # кто ответил и что (для fix — текст)
SYNC_ANSWER_AT = "sync-answer-at"
SYNC_REMINDED = "sync-reminded"  # напоминание менеджеру ушло (одно на пару)

WAITING = "ждёт ответа"
OK = "ok"
FIX = "fix"
NO_ANSWER = "нет ответа"

# блоки сида → роль пункта в сверке
ITEM_BLOCKS = (
    ("next", "следующий шаг", ("следующий шаг",)),
    ("inflight", "в работе", ("в работе прямо сейчас",)),
    ("tails", "хвост", ("открытые хвосты",)),
)
AWAITING_HEADS = ("ждёт гришу", "ждет гришу")
WHERE_HEADS = ("где мы",)

_MECHANICAL = "шов приёма закрыт механикой"
_PULSE = re.compile(r"^\s*-\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?)\S*\s*—\s*(.+?)\s*$")
_HOLE = re.compile(r"^(?:—|-|нет\b|ничего\b|пусто\b)", re.I)
_SUFFIX = re.compile(r"\s*\((?:пульс [^()]*|маркер в пульсе|из следующего шага|до ПЕРЕДАЧИ)\)\s*$")
_SEE_INFLIGHT = re.compile(r"см\. «в работе прямо сейчас»\s*$")
_DROP = re.compile(r"сним(?:аю|аем|ает)\s*:\s*(.+?)\s+[—–-]\s+(.+?)(?=\s*(?:[;·]|\bсним(?:аю|аем|ает)\s*:|$))",
                   re.I)
# id сессии с хвостом uds (tide-stack-20-9e) — целиком, иначе ключ теряется (SOC-143)
_ID = re.compile(r"(?<![\w-])(?:[a-z]+(?:-[a-z]+)*-\d+(?:-[0-9a-z]{2})?[a-z0-9]*|f\d{1,3}|"
                 r"[0-9a-f]{7,40})(?![\w-])")
_ORDER = re.compile(r"^поручено мне\s+—\s+от\s+(\S+?)(?:\s+в\s+(\d{1,2}:\d{2}))?\s*:\s*(.*)$", re.S)
_F_RUN = re.compile(r"(?<![\w-])f(\d{1,3})(?:\s*[–-]\s*f(\d{1,3}))?(?!\w)", re.I)
_HASH = re.compile(r"(?<![\w])(?:[a-z][\w.-]*@)?([0-9a-f]{7,40})(?![\w])")
_DONE_WORD = r"(?:сделан|сдан|закрыт|готов|done)\w*"
_F_LIST = r"((?:f\d{1,3}(?:\s*[–-]\s*f\d{1,3})?[\s/,+и]*)+)"
_DONE_AFTER = re.compile(_DONE_WORD + r"\s+" + _F_LIST, re.I)
_DONE_BEFORE = re.compile(_F_LIST + r"\s*(?:—\s*)?" + _DONE_WORD, re.I)
HASH_NEAR = 25                   # F-N не дальше стольких знаков от хеша — «F60 tide@0e8dcd8»
# «ждёт Гришу» — только решения Гриши (контракт v1): не работа нитей и не In Review
_FOR_GRISHA = re.compile(r"жд[её]т гриш|решени[ея] гриш|реша[её]т гриш|❓", re.I)
_NOT_GRISHA = re.compile(r"на проверке\s*\(in review\)|done ставит", re.I)
_FROM_NEXT = re.compile(r"\(из следующего шага\)\s*$")
_WORD = re.compile(r"[a-zа-я]{5,}")
_STOP = {
    "после", "через", "старой", "старая", "новой", "новая", "сессии", "сессия", "только",
    "прямо", "сейчас", "пульс", "который", "которые", "когда", "будет", "нужно", "чтобы",
    "итоги", "итогам", "строка", "строки", "работе", "начат", "начато", "начаты", "проверен",
    "проверено", "закрыто", "закрыт", "жив", "живой", "поручение", "поручено", "воркер",
    "следом", "также", "здесь",
}


class SeamSyncError(StreamError):
    """Сверку не из чего собрать / некому подтвердить."""


# --- разбор сида и пульсов -----------------------------------------------------

def _norm(text: str) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split())


def _block(text: str, heads: Sequence[str]) -> List[str]:
    """Строки-пункты блока ``## <заголовок>`` сида (первый совпавший по префиксу)."""
    out: Optional[List[str]] = None
    for line in (text or "").splitlines():
        m = re.match(r"^##\s+(.*?)\s*$", line)
        if m:
            head = m.group(1).strip().lower()
            if out is not None:
                break
            if any(head.startswith(h) for h in heads):
                out = []
            continue
        if out is not None:
            body = line.strip()
            if body.startswith("- ") or body.startswith("* "):
                out.append(body[2:].strip())
            elif body and out and not body.startswith(("<", "#")) and line[:1] == " ":
                out[-1] += " " + body  # перенос пункта
    return out or []


def _clean(item: str) -> str:
    item = _SUFFIX.sub("", item.strip())
    return _SUFFIX.sub("", item).strip()


def _is_item(item: str) -> bool:
    return bool(item) and not item.startswith(("(", "<")) and not _HOLE.match(item)


def seed_items(seed_text: str) -> Dict[str, List[str]]:
    """``{next, inflight, tails, awaiting}`` — пункты сида для сверки. Хвост со
    ссылкой «см. «в работе прямо сейчас»» — повтор строки «в работе», не считается."""
    out: Dict[str, List[str]] = {}
    for key, _label, heads in ITEM_BLOCKS:
        items = [_clean(i) for i in _block(seed_text, heads)]
        items = [i for i in items if _is_item(i)]
        if key == "tails":
            items = [i for i in items if not _SEE_INFLIGHT.search(i)]
        out[key] = items
    out["awaiting"] = awaiting_items(seed_text, out)
    return out


def awaiting_items(seed_text: str, items: Optional[Dict[str, List[str]]] = None) -> List[str]:
    """«Что решить» — только решения Гриши (поправка main · 05, контракт v1): строки
    «SOC-N на проверке (In Review) — Done ставит проверяющий» (Done ставит менеджер) и
    работа нитей (строка «(из следующего шага)», повтор пункта «дальше»/«хвосты») —
    вон; явное «ждёт Гришу / решение Гриши / ❓» остаётся всегда."""
    work = {_norm(i) for key in ("next", "tails") for i in (items or {}).get(key, [])}
    out = []
    for raw in _block(seed_text, AWAITING_HEADS):
        item = _clean(raw)
        if not _is_item(item):
            continue
        if not _FOR_GRISHA.search(item) and (
                _NOT_GRISHA.search(item) or _FROM_NEXT.search(raw) or _norm(item) in work):
            continue
        out.append(item)
    return out


def done_lines(seed_text: str, limit: int = 3) -> List[str]:
    """«Сделано» по ПЕРЕДАЧЕ старой из блока «где мы» сида: метка «Сделано:», иначе
    «Где мы:», иначе вся ПЕРЕДАЧА; нет ПЕРЕДАЧИ — «итог сессии»."""
    where = _block(seed_text, WHERE_HEADS)
    passing = next((ln for ln in where if "ПЕРЕДАЧА" in ln), "")
    if passing:
        body = passing.split("ПЕРЕДАЧА", 1)[1].lstrip(" .:")
        m = (re.search(r"Сделано:\s*(.+?)(?=\s+(?:Дальше|В полёте|В полете|Хвосты|Ждёт|Ждет)\s*:|$)", body)
             or re.search(r"Где мы:\s*(.+?)(?=\s+(?:Сделано|Дальше|В полёте|В полете|Хвосты|Ждёт|Ждет)\s*:|$)", body))
        body = m.group(1) if m else body
        return [p for p in _split_top(body) if p][:limit]
    final = next((ln for ln in where if ln.startswith("итог сессии:")), "")
    return [final.split(":", 1)[1].strip()] if final else []


def _split_top(body: str) -> List[str]:
    """Фразы ПЕРЕДАЧИ: «;» и «. Заглавная» — только вне скобок."""
    out, cur, depth = [], "", 0
    for i, ch in enumerate(body):
        depth += {"(": 1, ")": -1}.get(ch, 0)
        depth = max(depth, 0)
        nxt = body[i + 1:i + 3]
        if depth == 0 and (ch == ";" or (ch == "." and nxt[:1] == " " and nxt[1:2].isupper())):
            out.append(cur.strip(" ."))
            cur = ""
            continue
        cur += ch
    out.append(cur.strip(" ."))
    return out


def _parse_ts(raw: object) -> Optional[datetime]:
    try:
        ts = datetime.fromisoformat(str(raw or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts.astimezone().replace(tzinfo=None) if ts.tzinfo else ts


def first_pulses(passport_text: str, n: int = 0,
                 since: object = None) -> List["tuple[str, str]"]:
    """Первые *n* настоящих пульсов ``## context`` (не механика приёма), не раньше
    *since* → ``[(штамп, текст)]``; *n* ≤ 0 — все."""
    cutoff = _parse_ts(since)
    out: List["tuple[str, str]"] = []
    in_ctx = False
    for ln in (passport_text or "").splitlines():
        if ln.startswith("## "):
            in_ctx = ln.strip() == "## context"
            continue
        m = _PULSE.match(ln) if in_ctx else None
        if not m or _MECHANICAL in m.group(2):
            continue
        ts = _parse_ts(m.group(1))
        if cutoff is not None and (ts is None or ts < cutoff):
            continue
        out.append((m.group(1), m.group(2)))
        if 0 < n <= len(out):
            break
    return out


def _expand(text: str) -> str:
    try:
        from .launcher.seed_draft import expand_issue_runs
        return expand_issue_runs(text)
    except Exception:  # noqa: BLE001
        return text


def ids(text: str) -> set:
    """Опознаваемые ключи пункта: SOC-129, F60, tide-stack-25, хеши."""
    low = _norm(_expand(text))
    return {m.group(0) for m in _ID.finditer(low) if any(c.isdigit() for c in m.group(0))}


STEM = 6                         # основа слова: первые 6 букв (падежи не мешают)


def stems(text: str) -> set:
    return {w[:STEM] for w in _WORD.findall(_norm(text)) if w not in _STOP}


def mentions(item: str, text: str) -> "tuple[bool, str]":
    """Назван ли пункт *item* в *text* → ``(да/нет, чем)``: по ключам (половина и
    больше), иначе по словам (половина и больше, не меньше двух)."""
    i_ids, t_ids = ids(item), ids(text)
    i_st, t_st = stems(item), stems(text)
    st_hit = i_st & t_st
    if i_ids:
        hit = i_ids & t_ids
        if hit and (len(hit) * 2 >= len(i_ids)
                    or (i_st and len(st_hit) * 5 >= 2 * len(i_st))):
            return True, ", ".join(sorted(hit))
        if hit or not i_st:
            return False, ""
    if i_st and len(st_hit) * 2 >= len(i_st) and len(st_hit) >= min(2, len(i_st)):
        return True, "слова: " + ", ".join(sorted(st_hit)[:4])
    return False, ""


def drops(pulses: Sequence["tuple[str, str]"]) -> List["tuple[str, str]"]:
    """Явно снятые пункты: «снимаю: <что> — <почему>» → ``[(что, почему)]``."""
    out = []
    for _at, text in pulses:
        for m in _DROP.finditer(text):
            out.append((m.group(1).strip(" «»\"'"), m.group(2).strip(" .")))
    return out


# --- сделано после ПЕРЕДАЧИ и закрытые поручения (поправка main · 05) -------------

def f_ids(text: str) -> set:
    """Номера находок ``F60``, ``F61/F62``, ``F55–F63`` (диапазон раскрыт) → ``{"f60", …}``."""
    out = set()
    for m in _F_RUN.finditer(text or ""):
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else lo
        if not lo <= hi <= lo + 30:
            hi = lo
        out.update("f{0}".format(i) for i in range(lo, hi + 1))
    return out


_AFTER_DEP = re.compile(r"(?:после|до|когда|ждёт|ждет|вслед за)\s+" + _F_LIST, re.I)


def work_f_ids(item: str) -> set:
    """F-N, которые пункт ДЕЛАЕТ: «автозакрытие после F49» — F49 условие, не работа."""
    return f_ids(_AFTER_DEP.sub(" ", item or ""))


def _hashes(text: str) -> List["tuple[int, int, str]"]:
    return [(m.start(), m.end(), m.group(1)) for m in _HASH.finditer(text or "")
            if any(c.isdigit() for c in m.group(1)) and any(c.isalpha() for c in m.group(1))]


def done_in_line(text: str, label: str = "") -> Dict[str, str]:
    """Что строка пульса/сида называет сделанным → ``{f-id: пруф}``: F-N рядом с хешем
    («F60 tide@0e8dcd8», «cdc0434 F56») или со словом «сделано/сдан/закрыто»."""
    out: Dict[str, str] = {}
    hashes = _hashes(text)
    for m in _F_RUN.finditer(text or ""):
        near = [h for h in hashes
                if (0 <= m.start() - h[1] <= HASH_NEAR or 0 <= h[0] - m.end() <= HASH_NEAR)
                and ";" not in text[min(m.end(), h[1]):max(m.start(), h[0])]]
        if near:
            near.sort(key=lambda h: min(abs(m.start() - h[1]), abs(h[0] - m.end())))
            for f in f_ids(m.group(0)):
                out.setdefault(f, near[0][2][:7])
    for rx in (_DONE_AFTER, _DONE_BEFORE):
        for m in rx.finditer(text or ""):
            for f in f_ids(m.group(1)):
                out.setdefault(f, label or "сделано")
    return out


def done_evidence(seed_text: str, pulses: Sequence["tuple[str, str]"],
                  commits: Sequence[str] = ()) -> Dict[str, str]:
    """Сделанное к сверке → ``{f-id: пруф}``: коммиты репо нити после ПЕРЕДАЧИ
    (``"<sha> <тема>"`` — F-N в теме), пульсы новой и «где мы» сида с хешами."""
    out: Dict[str, str] = {}
    for line in commits or ():
        sha, _, subject = str(line).partition(" ")
        for f in f_ids(subject):
            out.setdefault(f, sha[:7])
    for at, text in pulses:
        for f, proof in done_in_line(text, "пульс " + str(at)[11:16]).items():
            out.setdefault(f, proof)
    for line in _block(seed_text, WHERE_HEADS):
        for f, proof in done_in_line(line, "где мы").items():
            out.setdefault(f, proof)
    return out


def passing_at(seed_text: str) -> Optional[datetime]:
    """Штамп ПЕРЕДАЧИ старой из «где мы» сида."""
    for line in _block(seed_text, WHERE_HEADS):
        m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?)\S*\s*—\s*ПЕРЕДАЧА", line)
        if m:
            return _parse_ts(m.group(1))
    return None


def _sender_rx(who: str):
    """Id отправителя как сильный ключ: ``tide-stack-20-9e`` ловит и сам себя, и
    ``tide-stack-20``; ``tide-stack-2`` не ловит ``tide-stack-24``."""
    from .launcher.seed_draft import sender_key
    key = sender_key(who)
    if not key:
        return None
    return re.compile(r"(?<![\w-]){0}(?!\d)".format(re.escape(key)), re.I)


def order_closed(item: str, pulses: Sequence["tuple[str, str]"]) -> str:
    """Входящее «поручено мне — от X …» закрыто → причина, иначе ``""``. Признаки —
    те же, что у сборщика сида (SOC-101 F60, :mod:`tide.launcher.seed_draft`):
    письмо без просьбы — не поручение; пульс новой назвал id отправителя — записано."""
    m = _ORDER.match(item or "")
    if not m:
        return ""
    who, body = m.group(1), m.group(3)
    from .launcher import seed_draft as sd
    if sd.is_info_letter(body):
        return "письмо без просьбы — не поручение"
    stamped = [(t, line) for t, line in ((_parse_ts(a), x) for a, x in pulses) if t is not None]
    if stamped and sd.pulse_names_sender(who, datetime.min, stamped):
        return "пульс новой назвал {0}".format(sd.sender_key(who))
    return ""


def _drop_for(item: str, dropped: Sequence["tuple[str, str]"]) -> Optional["tuple[str, str]"]:
    """Снятие, которое относится к пункту: по словам/ключам «что», а для поручения —
    по id отправителя в любом месте снятия («снимаю: поручение tide-stack-20-9e 13:27 …»)."""
    gone = next((d for d in dropped if mentions(item, d[0])[0]), None)
    if gone is not None:
        return gone
    m = _ORDER.match(item or "")
    rx = _sender_rx(m.group(1)) if m else None
    if rx is not None:
        return next((d for d in dropped if rx.search(d[0] + " " + d[1])), None)
    return None


# --- сверка --------------------------------------------------------------------

def reconcile(seed_text: str, passport_text: str, *, n: int = 0,
              since: object = None, commits: Sequence[str] = ()) -> Dict[str, object]:
    """Сверка пересказа новой с сидом → ``{items: [{block, label, text, status, by,
    why}], done, awaiting, pulses, gaps}``; status — dropped | done (F-N сделаны после
    ПЕРЕДАЧИ: коммиты *commits*, пульсы с хешами) | closed (поручение закрыто) | named
    | missing. *n* ≤ 0 — все пульсы после приёма."""
    items = seed_items(seed_text)
    pulses = first_pulses(passport_text, n, since)
    said = " \n".join(t for _a, t in pulses)
    dropped = drops(pulses)
    proof = done_evidence(seed_text, pulses, commits)
    rows: List[Dict[str, object]] = []
    seen = set()
    for key, label, _heads in ITEM_BLOCKS:
        for it in items.get(key, []):
            if _norm(it) in seen:
                continue
            seen.add(_norm(it))
            row = {"block": key, "label": label, "text": it, "status": "missing",
                   "by": "", "why": ""}
            gone = _drop_for(it, dropped)
            fs = work_f_ids(it)
            closed = order_closed(it, pulses)
            if gone is not None:
                row.update(status="dropped", why=gone[1], by=gone[0])
            elif fs and fs <= set(proof):
                row.update(status="done", by=", ".join(
                    "{0} {1}".format(f.upper(), proof[f]) for f in sorted(fs, key=lambda x: int(x[1:]))))
            elif closed:
                row.update(status="closed", by=closed)
            else:
                ok, by = mentions(it, said)
                if ok:
                    row.update(status="named", by=by)
            rows.append(row)
    return {"items": rows, "done": done_lines(seed_text), "awaiting": items["awaiting"],
            "pulses": pulses, "gaps": sum(1 for r in rows if r["status"] == "missing")}


def _clip(text: str, n: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def render(rep: Dict[str, object], *, title: str = "", answer: str = "") -> str:
    """Короткий список «сделано / дальше / что решить» + что не названо."""
    rows = rep["items"]
    out = ["шов-синк{0}".format(" " + title if title else "")]
    if rep.get("to"):
        line = addressee_line(rep["to"])
        if line:
            out.append(line)
    out.append("")
    out.append("сделано (по ПЕРЕДАЧЕ старой):")
    out += ["- " + _clip(x) for x in rep["done"]] or ["- — ПЕРЕДАЧИ в сиде нет"]
    after = [r for r in rows if r["status"] in ("done", "closed")]
    if after:
        out.append("")
        out.append("сделано и закрыто после передачи (снято с «дальше»):")
        out += ["- [{0}] {1} — {2}".format(r["label"], _clip(r["text"], 110), _clip(r["by"], 80))
                for r in after]
    out.append("")
    out.append("дальше (новая назвала в {0} пульсах после приёма):".format(len(rep["pulses"])))
    named = [r for r in rows if r["status"] == "named"]
    out += ["- [{0}] {1}".format(r["label"], _clip(r["text"])) for r in named] or ["- —"]
    gone = [r for r in rows if r["status"] == "dropped"]
    if gone:
        out.append("")
        out.append("снято новой:")
        out += ["- [{0}] {1} — {2}".format(r["label"], _clip(r["text"], 100), _clip(r["why"], 100))
                for r in gone]
    miss = [r for r in rows if r["status"] == "missing"]
    out.append("")
    out.append("не названо и не снято ({0}):".format(len(miss)))
    out += ["- [{0}] {1}".format(r["label"], _clip(r["text"])) for r in miss] or ["- — всё названо"]
    out.append("")
    out.append("что решить (ждёт Гришу):")
    out += ["- " + _clip(x) for x in rep["awaiting"]] or ["- — ничего"]
    if answer:
        out += ["", answer]
    return "\n".join(out) + "\n"


# --- кто подтверждает ----------------------------------------------------------

def session_paths(new_dir: Path) -> Dict[str, Path]:
    new_dir = Path(new_dir)
    return {"passport": new_dir / "arc.md", "seed": new_dir / "input" / "handoff-seed.md",
            "thread": new_dir.parents[1], "root": new_dir.parents[4]}


def offer_of(home: Optional[Path], new_dir: Path) -> Optional[Dict[str, object]]:
    """Принятый оффер, чей сид лежит в *new_dir* (новейший)."""
    if home is None:
        return None
    from . import handoff_queue as hq
    try:
        target = Path(new_dir).resolve()
        found = [r for r in hq.list_offers(Path(home), status=hq.STATUS_TAKEN)
                 if r.get("seed") not in (None, "", "-")
                 and Path(str(r["seed"])).parent.parent.resolve() == target]
    except Exception:  # noqa: BLE001
        return None
    return found[0] if found else None  # list_offers — новейшие сверху


def _ao_alive(handle: str) -> Optional[bool]:
    try:
        from .adapters.ao import AoAdapter
        return AoAdapter().alive(handle)
    except Exception:  # noqa: BLE001
        return None


def _is_main_thread(thread_dir: Optional[Path]) -> bool:
    name = Path(thread_dir).name if thread_dir else ""
    return re.sub(r"^\d+-@?", "", name) == "main"


def _new_ao(home: Optional[Path], new_dir: Optional[Path]) -> str:
    if new_dir is None or home is None:
        return ""
    try:
        from .arc import holder
        sid = (fields.read_field(Path(new_dir) / "arc.md", "claude-session") or "").strip()
        return holder.ao_handle_for(Path(home), sid) if sid else ""
    except Exception:  # noqa: BLE001
        return ""


def confirmer(home: Optional[Path], rec: Optional[Dict[str, object]], root: Path, *,
              alive: Callable[[str], Optional[bool]] = _ao_alive,
              main_of: Optional[Callable[[Path], Dict[str, str]]] = None,
              new_dir: Optional[Path] = None,
              manager_of: Optional[Callable[[], Dict[str, str]]] = None) -> Dict[str, str]:
    """Кто подтверждает → ``{ao_session, role, why}``: старая, пока её AO-сессия жива;
    закрыта / exited / не знаем адреса — main проекта (``tide whois main``); новая
    сама main проекта (нить main или адрес main = адрес новой) — менеджер
    (``tide whois manager``): сам себе не подтверждают."""
    old = ""
    if rec is not None and str(rec.get("from_session") or "-") != "-":
        try:
            from .arc import holder
            old = holder.ao_handle_for(Path(home) if home else None, str(rec["from_session"]))
        except Exception:  # noqa: BLE001
            old = ""
    if old and alive(old):
        return {"ao_session": old, "role": "старая", "why": "старая жива"}
    why = "старой нет адреса" if not old else "старая {0} закрыта".format(old)
    if main_of is None:
        def main_of(r: Path) -> Dict[str, str]:
            from . import whois
            return whois.thread("main", home=home, root=r)
    try:
        m = main_of(Path(root)) or {}
    except Exception:  # noqa: BLE001
        m = {}
    main_ao = str(m.get("ao_session") or "")
    new_ao = _new_ao(home, new_dir)
    thread_dir = Path(new_dir).parents[1] if new_dir is not None else None
    if _is_main_thread(thread_dir) or (main_ao and new_ao and main_ao == new_ao):
        if manager_of is None:
            def manager_of() -> Dict[str, str]:
                from . import whois
                return whois.manager(home=Path(home) if home else None)
        try:
            mg = manager_of() or {}
        except Exception:  # noqa: BLE001
            mg = {}
        return {"ao_session": str(mg.get("ao_session") or ""), "role": "менеджер",
                "why": "{0}; новая — сама main проекта".format(why)}
    return {"ao_session": main_ao, "role": "main проекта", "why": why}


def addressee_line(to: Dict[str, str]) -> str:
    """Кому письмо и почему он (поправка main · 05): «подтверждаешь ты — …, потому что …»."""
    if not to or to.get("role") in (None, "", "—", "-"):
        return ""
    return "подтверждаешь ты — {0} {1}, потому что {2}".format(
        to.get("role"), to.get("ao_session") or "(без адреса)", to.get("why") or "так вышло")


# --- поля паспорта ----------------------------------------------------------------
# Свои ключи sync* не в белом списке tide.fields (его правят другие) — пишем сами:
# строка «ключ: значение» в шапке паспорта, до первого «## ».

def _get(passport: Path, key: str) -> str:
    try:
        text = Path(passport).read_text(encoding="utf-8")
    except OSError:
        return ""
    for ln in text.splitlines():
        if ln.startswith("## "):
            break
        if ln.startswith(key + ":"):
            return ln[len(key) + 1:].strip()
    return ""


def _set(passport: Path, key: str, value: str) -> None:
    from .io import atomic_write

    passport = Path(passport)
    lines = passport.read_text(encoding="utf-8").split("\n")
    line = "{0}: {1}".format(key, " ".join(str(value).split()))
    head_end, last_field = len(lines), None
    for i, ln in enumerate(lines):
        if ln.startswith("## "):
            head_end = i
            break
        if ln.startswith(key + ":"):
            lines[i] = line
            atomic_write(passport, "\n".join(lines))
            return
        if re.match(r"^[a-z][a-z0-9-]*:\s", ln):
            last_field = i
    at = last_field + 1 if last_field is not None else head_end
    lines.insert(at, line)
    atomic_write(passport, "\n".join(lines))


# --- жесты ---------------------------------------------------------------------

def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _pulses_n() -> int:
    """Потолок пульсов из ``$TIDE_SYNC_PULSES``; нет — 0 (все после приёма)."""
    try:
        return max(0, int(os.environ.get(PULSES_ENV) or 0))
    except ValueError:
        return 0


_ABS_PATH = re.compile(r"(?<![\w.])(/(?:Users|home|opt|srv|var|tmp)/[^\s«»\"'()`,;:]+)")


def _git_top(path: Path) -> Optional[Path]:
    try:
        res = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (res.stdout or "").strip()
    return Path(out) if res.returncode == 0 and out else None


def code_repos(seed_text: str, limit: int = 4) -> List[Path]:
    """Репо кода нити: ``$TIDE_SYNC_REPO`` (через «:»), иначе git-репо по абсолютным
    путям сида («закоммичены в /…/tide»). Не нашлось — пусто, сверка без коммитов."""
    env = [x for x in (os.environ.get(REPO_ENV) or "").split(os.pathsep) if x.strip()]
    found: List[Path] = []
    for raw in env or [m.group(1) for m in _ABS_PATH.finditer(seed_text or "")]:
        path = Path(raw.rstrip(".")).expanduser()
        for _ in range(8):
            if path.is_dir() or path == path.parent:
                break
            path = path.parent
        if not path.is_dir():
            continue
        top = _git_top(path)
        if top is not None and top not in found:
            found.append(top)
        if len(found) >= limit:
            break
    return found


def git_commits(repos: Sequence[Path], since: Optional[datetime]) -> List[str]:
    """``["<sha> <тема>"]`` коммитов *repos* после *since* (нет — пусто)."""
    if since is None:
        return []
    out: List[str] = []
    for repo in repos:
        try:
            res = subprocess.run(["git", "-C", str(repo), "log", "--since=" + since.isoformat(),
                                  "--format=%h %s"], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        if res.returncode == 0:
            out += [ln for ln in res.stdout.splitlines() if ln.strip()]
    return out


def answer_hint(new_dir: Path) -> str:
    return ('ответ: tide handoff sync ok {0} | tide handoff sync fix {0} "что поправить"'
            .format(Path(new_dir)))


def ping_text(thread: str, num: str, addr: str, new_dir: Path, gaps: int) -> str:
    """Пинг подтверждающему (< 500 байт): письмо и короткая команда ответа; полный
    путь новой — в самом письме."""
    ref = "{0}/{1}".format(Path(new_dir).parents[1].name, Path(new_dir).name)
    return ("шов-синк {0}: новая поняла так (письмо {1}, расхождений {2}) — "
            "tide mail read {3} {1}; ответ: tide handoff sync ok {4} | "
            'tide handoff sync fix {4} "…"').format(thread, num, gaps, addr, ref)


def check(new_dir: Path, *, home: Optional[Path] = None, dry_run: bool = False,
          n: Optional[int] = None, seed: Optional[Path] = None,
          send_mail: Optional[Callable[..., Dict[str, object]]] = None,
          send_ping: Optional[Callable[[str, str], Optional[int]]] = None,
          who: Optional[Dict[str, str]] = None,
          commits: Optional[Sequence[str]] = None) -> Dict[str, object]:
    """Сверить новую *new_dir* с её сидом; без *dry_run* — письмо в почту нити,
    пинг подтверждающему, поля ``sync*`` в паспорт новой, отчёт в workspace/.
    *n* — потолок пульсов (нет — все после приёма); *commits* — коммиты после
    ПЕРЕДАЧИ (нет — ``git log`` репо кода нити, см. :func:`code_repos`)."""
    new_dir = Path(new_dir)
    p = session_paths(new_dir)
    seed = Path(seed) if seed else p["seed"]
    if not seed.is_file():
        raise SeamSyncError("sync: нет сида {0}".format(seed))
    if not p["passport"].is_file():
        raise SeamSyncError("sync: нет паспорта {0}".format(p["passport"]))
    if home is None:
        try:
            home = paths.control_home(p["root"])
        except Exception:  # noqa: BLE001
            home = None
    rec = offer_of(home, new_dir)
    seed_text = seed.read_text(encoding="utf-8")
    if commits is None:
        try:
            since = passing_at(seed_text) or _parse_ts((rec or {}).get("created"))
            commits = git_commits(code_repos(seed_text), since)
        except Exception:  # noqa: BLE001 — без коммитов сверка всё равно идёт
            commits = []
    rep = reconcile(seed_text, p["passport"].read_text(encoding="utf-8"),
                    n=_pulses_n() if n is None else n, since=(rec or {}).get("taken_at"),
                    commits=commits)
    rep["commits"] = len(commits)
    origin = (fields.read_field(p["passport"], "from") or "").strip()
    title = "{0}{1} (нить {2}{3})".format(
        origin + " → " if origin and not origin.startswith("<") else "", new_dir.name,
        p["thread"].name, ", оффер {0}".format(rec.get("num")) if rec is not None else "")
    rep["title"] = title
    if who:
        to = who
    elif rec is not None or not dry_run:
        to = confirmer(home, rec, p["root"], new_dir=new_dir)
    else:
        to = {"ao_session": "", "role": "—", "why": "принятого оффера не нашлось"}
    rep["to"] = to
    rep["text"] = render(rep, title=title, answer=answer_hint(new_dir))
    if dry_run:
        return rep
    from . import mail as _mail
    send_mail = send_mail or _mail.send
    letter = send_mail(p["thread"].name, rep["text"], sender=SENDER, root=p["root"],
                       do_ping=False, watch=False)
    num = str(letter.get("num") or "")
    addr = _mail.address_of(p["thread"])
    code = None
    if to.get("ao_session"):
        if send_ping is None:
            from .launcher.autohandoff import _ao_send as send_ping
        code = send_ping(to["ao_session"],
                         ping_text(p["thread"].name, num, addr, new_dir, rep["gaps"]))
    rep.update(letter=num, ping_code=code)
    at = _now()
    for key, val in ((SYNC, WAITING), (SYNC_GAPS, str(rep["gaps"])),
                     (SYNC_TO, "{0} {1} ({2})".format(to.get("role"), to.get("ao_session") or "без адреса",
                                                      to.get("why"))),
                     (SYNC_AT, at), (SYNC_LETTER, num)):
        _set(p["passport"], key, val)
    report = new_dir / "workspace" / REPORT_NAME
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(rep["text"], encoding="utf-8")
    rep["report"] = report
    return rep


def _who() -> str:
    try:
        from . import mail as _mail
        return _mail._who_am_i()
    except Exception:  # noqa: BLE001
        return (os.environ.get("AO_SESSION_ID") or "").strip() or "человек"


def answer(new_dir: Path, verdict: str, text: str = "", *, by: Optional[str] = None,
           send_mail: Optional[Callable[..., Dict[str, object]]] = None,
           ack: Optional[Callable[..., object]] = None) -> Dict[str, str]:
    """Ответ подтверждающего: ``ok`` или ``fix`` с текстом. Поля — в паспорт новой;
    письмо сверки — прочитано (квитанция); поправка — письмом новой (с пингом)."""
    new_dir = Path(new_dir)
    p = session_paths(new_dir)
    if not p["passport"].is_file():
        raise SeamSyncError("sync: нет паспорта {0}".format(p["passport"]))
    if verdict not in (OK, FIX):
        raise SeamSyncError("sync: ответ — ok или fix")
    if verdict == FIX and not (text or "").strip():
        raise SeamSyncError('sync fix: скажи, что поправить — tide handoff sync fix <новая> "…"')
    who = (by or "").strip() or _who()
    from . import mail as _mail
    num = _get(p["passport"], SYNC_LETTER)
    if num:
        try:
            (ack or _mail.read)(p["thread"].name, num, by=who, root=p["root"])
        except Exception:  # noqa: BLE001 — квитанция не главное
            pass
    say = who if verdict == OK else "{0}: {1}".format(who, " ".join(text.split()))
    _set(p["passport"], SYNC, verdict)
    _set(p["passport"], SYNC_ANSWER, say)
    _set(p["passport"], SYNC_ANSWER_AT, _now())
    out = {"sync": verdict, "answer": say, "letter": ""}
    if verdict == FIX:
        res = (send_mail or _mail.send)(
            p["thread"].name, "шов-синк {0}: поправка от {1}\n\n{2}\n".format(
                num or "-", who, text.strip()),
            sender=SENDER, root=p["root"])
        out["letter"] = str((res or {}).get("num") or "")
    return out


def status(new_dir: Path, *, now: Optional[datetime] = None) -> Dict[str, str]:
    """Итог сверки из паспорта; «ждёт ответа» дольше :data:`NO_ANSWER_MIN` — «нет ответа»."""
    passport = Path(new_dir) / "arc.md"
    out = {k: _get(passport, k) for k in (SYNC, SYNC_GAPS, SYNC_TO, SYNC_AT, SYNC_LETTER, SYNC_ANSWER,
                               SYNC_ANSWER_AT)}
    if out[SYNC] == WAITING:
        at = _parse_ts(out[SYNC_AT])
        if at is not None and ((now or datetime.now()) - at).total_seconds() > NO_ANSWER_MIN * 60:
            out[SYNC] = NO_ANSWER
    if not out[SYNC]:
        out[SYNC] = "не сверялась"
    return out


# --- автозапуск и напоминание ----------------------------------------------------

def _taken_ago(rec: Dict[str, object], now: datetime) -> Optional[timedelta]:
    taken = _parse_ts(rec.get("taken_at"))
    return None if taken is None else now - taken


def due(new_dir: Path, rec: Optional[Dict[str, object]], *,
        now: Optional[datetime] = None) -> bool:
    """Пора ли сверке самой: принятый оффер continue, приём свежий, ``sync:`` в паспорте
    новой ещё нет, после приёма у новой :data:`FIRST_PULSES` настоящих пульсов или
    пульс со «снимаю:»."""
    from . import handoff_queue as hq
    if rec is None or not hq._transfers_thread(rec):
        return False
    ago = _taken_ago(rec, now or datetime.now())
    taken, since = _parse_ts(rec.get("taken_at")), _parse_ts(AUTO_SINCE)
    if (ago is None or ago.total_seconds() > AUTO_FRESH_HOURS * 3600
            or taken is None or (since is not None and taken < since)):
        return False
    passport = Path(new_dir) / "arc.md"
    if _get(passport, SYNC):
        return False
    pulses = first_pulses(passport.read_text(encoding="utf-8"), 0, rec.get("taken_at"))
    return len(pulses) >= FIRST_PULSES or bool(drops(pulses))


def auto_check(new_dir: Path, *, home: Optional[Path] = None,
               now: Optional[datetime] = None, **kw) -> Optional[Dict[str, object]]:
    """После пульса новой: пора (:func:`due`) — один раз :func:`check` по пульсам, что
    есть сейчас. Повтор отсекает поле ``sync`` в паспорте. Никогда не падает."""
    try:
        new_dir = Path(new_dir)
        if home is None:
            home = paths.control_home(session_paths(new_dir)["root"])
        rec = offer_of(home, new_dir)
        if not due(new_dir, rec, now=now):
            return None
        return check(new_dir, home=home, **kw)
    except Exception as exc:  # noqa: BLE001 — сверка не смеет сорвать пульс
        try:
            import sys
            print("tide: шов-синк не ушёл: {0}".format(exc), file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass
        return None


def wrap_offload(sub) -> None:
    """Научить ``tide offload`` запускать шов-синк сам (SOC-143): handoff_queue и
    offload — домен и сверку не видят, компонуется в cli поверх обёртки приёма."""
    p = sub.choices.get("offload") if hasattr(sub, "choices") else None
    inner = p.get_default("func") if p is not None else None
    if inner is None:
        return

    def _offload_and_sync(args) -> int:
        rc = inner(args)
        if rc != 0:
            return rc
        try:
            from . import offload as _offload
            session_dir = _offload.find_session(paths.require_tide_root(), args.session)
            rep = auto_check(session_dir) if session_dir is not None else None
            if rep:
                to = rep.get("to") or {}
                print("tide: шов-синк ушёл сам — письмо {0} в почту нити, подтверждает {1} {2}; "
                      "не названо {3}".format(rep.get("letter"), to.get("role"),
                                              to.get("ao_session") or "без адреса", rep["gaps"]))
        except Exception:  # noqa: BLE001
            pass
        return rc

    p.set_defaults(func=_offload_and_sync)


def remind_text(new_dir: Path, st: Dict[str, str], minutes: int) -> str:
    ref = "{0}/{1}".format(Path(new_dir).parents[1].name, Path(new_dir).name)
    return ("шов-синк {0}: нет ответа {1} мин от {2} (письмо {3}) — "
            "tide handoff sync ok {4} | tide handoff sync fix {4} \"…\"").format(
        ref, minutes, st.get(SYNC_TO) or "подтверждающего", st.get(SYNC_LETTER) or "-",
        Path(new_dir))


def remind(home: Path, *, now: Optional[datetime] = None,
           alert: Optional[Callable[..., Optional[int]]] = None) -> List[str]:
    """Свежие пары, где сверка «ждёт ответа» дольше :data:`NO_ANSWER_MIN` минут, —
    одна строка менеджеру (``send_alert(route=manager)``); метка ``sync-reminded``
    в паспорте новой — одна на пару. → тексты отправленных."""
    from . import handoff_queue as hq
    now = now or datetime.now()
    out: List[str] = []
    for rec in hq.list_offers(Path(home), status=hq.STATUS_TAKEN):
        try:
            ago = _taken_ago(rec, now)
            if (ago is None or ago.total_seconds() > REMIND_LOOKBACK_HOURS * 3600
                    or not hq._transfers_thread(rec) or str(rec.get("seed") or "-") == "-"):
                continue
            new_dir = Path(str(rec["seed"])).parent.parent
            passport = new_dir / "arc.md"
            if not passport.is_file() or _get(passport, SYNC_REMINDED):
                continue
            st = status(new_dir, now=now)
            if st[SYNC] != NO_ANSWER:
                continue
            at = _parse_ts(st[SYNC_AT])
            minutes = int((now - at).total_seconds() // 60) if at else NO_ANSWER_MIN
            text = remind_text(new_dir, st, minutes)
            _set(passport, SYNC_REMINDED, _now())  # метка ДО отправки — одна на пару
            if alert is None:
                from .launcher.autohandoff import send_alert as alert
            alert(text, route="manager", source="{0}/sync".format(rec.get("name")))
            out.append(text)
        except Exception:  # noqa: BLE001 — напоминание не смеет уронить хук
            continue
    return out


def on_prompt(*, home: Optional[Path] = None) -> List[str]:
    """Хук handoff-confirm: раз в :data:`REMIND_THROTTLE_SEC` — :func:`remind`."""
    try:
        home = Path(home) if home is not None else paths.control_home()
        stamp = paths.state_dir(home) / REMIND_STAMP
        if stamp.is_file() and datetime.now().timestamp() - stamp.stat().st_mtime < REMIND_THROTTLE_SEC:
            return []
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(_now(), encoding="utf-8")
        return remind(home)
    except Exception:  # noqa: BLE001
        return []


# --- CLI -----------------------------------------------------------------------

def _resolve(ref: Optional[str]) -> Path:
    """Сессия-арка новой: путь к каталогу, ``нить/сессия`` или текущая (по sid)."""
    if ref:
        cand = Path(ref).expanduser()
        if (cand / "arc.md").is_file():
            return cand.resolve()
    root = paths.require_tide_root()
    from .launcher.seed_draft import resolve_session
    return resolve_session(root, ref)


def _cmd_check(args) -> int:
    new_dir = _resolve(args.session)
    rep = check(new_dir, dry_run=args.dry_run, n=args.pulses,
                seed=Path(args.seed) if args.seed else None)
    print(rep["text"], end="")
    to = rep.get("to") or {}
    if args.dry_run:
        print("\n(dry-run: ничего не отправлено; подтверждал бы {0} {1} — {2})".format(
            to.get("role", "—"), to.get("ao_session") or "без адреса", to.get("why", "")))
    else:
        print("\nотправлено: письмо {0} в почту нити, пинг {1} {2} (код {3}); отчёт {4}".format(
            rep.get("letter"), to.get("role"), to.get("ao_session") or "— нет адреса",
            rep.get("ping_code"), rep.get("report")))
    return 0 if not rep["gaps"] else 2


def _cmd_answer(args) -> int:
    new_dir = _resolve(args.session)
    out = answer(new_dir, args.verdict, getattr(args, "text", "") or "")
    print("tide: шов-синк {0} — {1}{2}".format(
        new_dir.name, out["sync"],
        "; поправка ушла новой письмом {0}".format(out["letter"]) if out["letter"] else ""))
    return 0


def _cmd_status(args) -> int:
    new_dir = _resolve(args.session)
    st = status(new_dir)
    print("шов-синк {0}: {1}".format(new_dir.name, st[SYNC]))
    for key in (SYNC_GAPS, SYNC_TO, SYNC_AT, SYNC_LETTER, SYNC_ANSWER, SYNC_ANSWER_AT):
        if st[key]:
            print("  {0}: {1}".format(key, st[key]))
    report = new_dir / "workspace" / REPORT_NAME
    if report.is_file():
        print("  отчёт: {0}".format(report))
    return 0


def register_sync(hsub) -> None:
    """``tide handoffs sync check|ok|fix|status`` (и алиас ``tide handoff sync …``)."""
    p = hsub.add_parser("sync", help="seam sync (SOC-143): the new session's retell vs the "
                                     "seed — done / next / to decide, confirmed by the old one")
    sub = p.add_subparsers(dest="sync_cmd", metavar="<check|ok|fix|status>")
    c = sub.add_parser("check", help="reconcile the first pulses with the seed and send the "
                                     "list to the confirmer (old session, else project main)")
    c.add_argument("session", nargs="?", help="new session: dir | <нить>/<сессия> | current")
    c.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="print the list only, send and write nothing")
    c.add_argument("--pulses", type=int, default=None,
                   help="cap on pulses after the take (default: all; ${0})".format(PULSES_ENV))
    c.add_argument("--seed", default=None, help="seed path (default: <session>/input/handoff-seed.md)")
    c.set_defaults(func=_cmd_check, _cmd="handoffs sync check")
    o = sub.add_parser("ok", help="the confirmer agrees with the retell")
    o.add_argument("session", nargs="?")
    o.set_defaults(func=_cmd_answer, verdict=OK, _cmd="handoffs sync ok")
    f = sub.add_parser("fix", help="the confirmer corrects the retell (sent to the new one)")
    f.add_argument("session", help="new session: dir | <нить>/<сессия>")
    f.add_argument("text")
    f.set_defaults(func=_cmd_answer, verdict=FIX, _cmd="handoffs sync fix")
    s = sub.add_parser("status", help="seam sync result in the new session's passport")
    s.add_argument("session", nargs="?")
    s.set_defaults(func=_cmd_status, _cmd="handoffs sync status")
    p.set_defaults(func=lambda a: (p.print_help() or 0), _cmd="handoffs sync")


def alias_argv(argv: List[str]) -> List[str]:
    """``tide handoff sync …`` → ``tide handoffs sync …`` (у ``handoff`` позиционный
    аргумент — арка, подкоманду туда не повесить)."""
    if len(argv) >= 2 and argv[0] == "handoff" and argv[1] == "sync":
        return ["handoffs"] + list(argv[1:])
    return list(argv)


__all__ = ["FIRST_PULSES", "NO_ANSWER_MIN", "SeamSyncError", "alias_argv", "answer",
           "auto_check", "awaiting_items", "check", "code_repos", "confirmer", "done_evidence",
           "done_in_line", "done_lines", "drops", "due", "f_ids", "first_pulses", "git_commits",
           "ids", "mentions", "on_prompt", "order_closed", "passing_at", "reconcile",
           "register_sync", "remind", "render", "seed_items", "status", "wrap_offload"]

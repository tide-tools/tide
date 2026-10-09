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
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from . import fields, paths
from .arc.stream import StreamError

FIRST_PULSES = 3                 # сколько первых настоящих пульсов новой смотрим
PULSES_ENV = "TIDE_SYNC_PULSES"
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
_ID = re.compile(r"(?<![\w-])(?:[a-z]+(?:-[a-z]+)*-\d+[a-z0-9]*|f\d{1,3}|[0-9a-f]{7,40})(?![\w-])")
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
    out["awaiting"] = [i for i in (_clean(x) for x in _block(seed_text, AWAITING_HEADS))
                       if _is_item(i)]
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


def first_pulses(passport_text: str, n: int = FIRST_PULSES,
                 since: object = None) -> List["tuple[str, str]"]:
    """Первые *n* настоящих пульсов ``## context`` (не механика приёма), не раньше
    *since* → ``[(штамп, текст)]``."""
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
        if len(out) >= n:
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


# --- сверка --------------------------------------------------------------------

def reconcile(seed_text: str, passport_text: str, *, n: int = FIRST_PULSES,
              since: object = None) -> Dict[str, object]:
    """Сверка пересказа новой с сидом → ``{items: [{block, label, text, status, by,
    why}], done, awaiting, pulses, gaps}``; status — named | dropped | missing."""
    items = seed_items(seed_text)
    pulses = first_pulses(passport_text, n, since)
    said = " \n".join(t for _a, t in pulses)
    dropped = drops(pulses)
    rows: List[Dict[str, object]] = []
    seen = set()
    for key, label, _heads in ITEM_BLOCKS:
        for it in items.get(key, []):
            if _norm(it) in seen:
                continue
            seen.add(_norm(it))
            row = {"block": key, "label": label, "text": it, "status": "missing",
                   "by": "", "why": ""}
            gone = next((d for d in dropped if mentions(it, d[0])[0]), None)
            if gone is not None:
                row.update(status="dropped", why=gone[1], by=gone[0])
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
    out = ["шов-синк{0}".format(" " + title if title else ""), ""]
    out.append("сделано (по ПЕРЕДАЧЕ старой):")
    out += ["- " + _clip(x) for x in rep["done"]] or ["- — ПЕРЕДАЧИ в сиде нет"]
    out.append("")
    out.append("дальше (новая назвала в первых {0} пульсах):".format(len(rep["pulses"])))
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
    return found[-1] if found else None


def _ao_alive(handle: str) -> Optional[bool]:
    try:
        from .adapters.ao import AoAdapter
        return AoAdapter().alive(handle)
    except Exception:  # noqa: BLE001
        return None


def confirmer(home: Optional[Path], rec: Optional[Dict[str, object]], root: Path, *,
              alive: Callable[[str], Optional[bool]] = _ao_alive,
              main_of: Optional[Callable[[Path], Dict[str, str]]] = None) -> Dict[str, str]:
    """Кто подтверждает → ``{ao_session, role, why}``: старая, пока её AO-сессия жива;
    закрыта / exited / не знаем адреса — main проекта (``tide whois main``)."""
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
    return {"ao_session": str(m.get("ao_session") or ""), "role": "main проекта", "why": why}


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
    try:
        return max(1, int(os.environ.get(PULSES_ENV) or FIRST_PULSES))
    except ValueError:
        return FIRST_PULSES


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
          who: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    """Сверить новую *new_dir* с её сидом; без *dry_run* — письмо в почту нити,
    пинг подтверждающему, поля ``sync*`` в паспорт новой, отчёт в workspace/."""
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
    rep = reconcile(seed.read_text(encoding="utf-8"),
                    p["passport"].read_text(encoding="utf-8"),
                    n=n or _pulses_n(), since=(rec or {}).get("taken_at"))
    origin = (fields.read_field(p["passport"], "from") or "").strip()
    title = "{0}{1} (нить {2}{3})".format(
        origin + " → " if origin and not origin.startswith("<") else "", new_dir.name,
        p["thread"].name, ", оффер {0}".format(rec.get("num")) if rec is not None else "")
    rep["title"] = title
    rep["text"] = render(rep, title=title, answer=answer_hint(new_dir))
    if dry_run:
        if who:
            rep["to"] = who
        elif rec is not None:
            rep["to"] = confirmer(home, rec, p["root"])
        else:
            rep["to"] = {"ao_session": "", "role": "—", "why": "принятого оффера не нашлось"}
        return rep
    to = who or confirmer(home, rec, p["root"])
    rep["to"] = to
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
                   help="how many first pulses count (default {0}, ${1})".format(
                       FIRST_PULSES, PULSES_ENV))
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


__all__ = ["FIRST_PULSES", "NO_ANSWER_MIN", "SeamSyncError", "alias_argv", "answer", "check",
           "confirmer", "done_lines", "drops", "first_pulses", "ids", "mentions", "reconcile",
           "register_sync", "render", "seed_items", "status"]

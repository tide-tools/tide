"""tide.launcher.reception — приём нити (или неудача): журнал; Грише — по выключателю.

Слово Гриши (tg 3119): на каждый приём — ровно одно короткое сообщение: кто какую
нить от кого принял, что передано, что взято, что потерялось. Форма (замечание
management-14 на msg 3171 — сырой хвост сида Гриша не читает): до четырёх строк —
«<новая> приняла у · MM», «Передано: …» (из пульса ПЕРЕДАЧА старой), «Взято: …»
(целыми фразами), «Потеряно: …» (сверка разделов + открытые хвосты сида). Повод — первый
настоящий пульс принявшей сессии (тот, что пишет ``first-pulse:`` в оффер):
к нему уже есть и понятая цель, и аудит сида. Если принимающая так и не
поднялась (SOC-101 F25) — одно сообщение «не поднялась, нить остаётся у …».

Канал — тот же ``$TIDE_ALERT_CMD``, что у сторожа контекста и «нить встала»
(:func:`tide.launcher.autohandoff.send_alert`, ``TIDE_ALERT_ROUTE=human``). Нет
команды — тихо пропускаем и пишем строку в ``.tide/state/alerts.log``
control-home. Метка ``announced:`` в оффере — второй раз не шлём.

Выключатель ``$TIDE_ACCEPT_NOTIFY``: ``off`` (по умолчанию с 10.10, слово Гриши
tg 3733 на 3728–3732 «приняла у …»: «не отправляй мне таких убогих и бессмысленных
сообщений») — Грише ничего, только доска и строка в журнале; ``all`` — оба
сообщения (старое поведение), ``fail`` — только «не поднялась». Строка в журнале
есть всегда. Предложение закрыть старую менеджеру от него не зависит.

Сам модуль никогда не падает: сообщение — уведомление, не шаг передачи.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .. import fields, handoff_queue as hq, paths, slug as _slug
from ..arc import stream as _stream

ANNOUNCED = "announced"          # метка в оффере: «<штамп> · sent|no-cmd|rc=N»
ALERT_LOG = "alerts.log"
FIELD_MAX = 200                  # примерно столько знаков на поле сообщения
ROUTE_HUMAN = "human"

# Выключатель сообщений Грише. По умолчанию off — слово Гриши tg 3733 (ответ на
# 3728–3732 «… приняла у …»): «не отправляй мне таких убогих и бессмысленных
# сообщений»; приём и хендофф видны на доске и в alerts.log, Грише не пишем.
# all — о приёме и о неудаче (было по умолчанию с tg 3119), fail — только
# «не поднялась». Предложение закрыть менеджеру — всегда, от выключателя не зависит.
NOTIFY_ENV = "TIDE_ACCEPT_NOTIFY"
NOTIFY_ALL, NOTIFY_FAIL, NOTIFY_OFF = "all", "fail", "off"
NOTIFY_DEFAULT = NOTIFY_OFF


def notify_mode() -> str:
    raw = (os.environ.get(NOTIFY_ENV) or "").strip().lower()
    return raw if raw in (NOTIFY_ALL, NOTIFY_FAIL, NOTIFY_OFF) else NOTIFY_DEFAULT


# --- текст (чистое) ------------------------------------------------------------

def clip(text: str, limit: int = FIELD_MAX) -> str:
    """Одна строка, не длиннее *limit*: режем по границе слова, с «…»."""
    line = " ".join((text or "").split())
    if len(line) <= limit:
        return line
    cut = line[:limit - 1]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:—-·") + "…"


TAKEN_MAX = 200                  # «Взято» — целые фразы, не больше стольких знаков
PASSED_MAX = 140                 # «Передано»: «где мы» целыми фразами …
FLIGHT_MAX = 100                 # … и «в полёте»
LOST_MAX = 100
TAILS_MAX = 90                   # первый открытый хвост в «Потеряно»

# Конец фразы: «.», «!», «?» (или «…») и пробел. Строчная буква следом — тоже новая
# фраза (агенты начинают с имён: «main · 04 подтвердила»), кроме сокращений.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_ABBREV = re.compile(r"(?:^|\s)(?:т\.е|т\.к|т\.д|т\.п|см|напр|др|ср|стр|гл|п|e\.g|i\.e|vs|etc)\.$",
                     re.IGNORECASE)
# «Принял(а) нить X у 03 (оффер 263).» — то же, что первая строка сообщения.
_ECHO_TAKE = re.compile(r"^принял[аи]?\s+(?:нить|оффер|хендофф)\b", re.IGNORECASE)


def sentences(text: str) -> List[str]:
    line = " ".join((text or "").split())
    out: List[str] = []
    for part in _SENTENCE_END.split(line):
        if not part.strip():
            continue
        if out and _ABBREV.search(out[-1]):
            out[-1] = out[-1] + " " + part
        else:
            out.append(part)
    return out


def clip_sentences(text: str, limit: int, *, enough: int = 60) -> str:
    """Целые фразы, а не обрубок посреди слова (SOC-101 F41): первая фраза целиком;
    пока набрано меньше *enough* знаков — следующая, если влезает в *limit*. Фраза
    длиннее *limit* — по слову, с «…» (:func:`clip`)."""
    parts = sentences(text)
    if not parts:
        return ""
    out = parts[0]
    if len(out) > limit:
        return clip(out, limit)
    for nxt in parts[1:]:
        if len(out) >= enough or len(out) + 1 + len(nxt) > limit:
            break
        out = out + " " + nxt
    return out


def taken_text(text: str) -> str:
    """«Взято»: без эха «Принял нить … у 03» (это уже первая строка), целыми фразами."""
    parts = sentences(text)
    while len(parts) > 1 and _ECHO_TAKE.match(parts[0]):
        parts = parts[1:]
    return clip_sentences(" ".join(parts), TAKEN_MAX) or "—"


def _base(title: str) -> str:
    """«проект — нить · NN» без хвоста « · <AO-id>»."""
    from ..arc.stream import with_ao_id
    return with_ao_id(title, "")


def _short_old(new_title: str, old_title: str) -> str:
    """Старая той же нити — коротко «· MM»; иначе её имя целиком."""
    nb, ob = _base(new_title), _base(old_title)
    if " · " in nb and " · " in ob and nb.rsplit(" · ", 1)[0] == ob.rsplit(" · ", 1)[0]:
        return "· " + ob.rsplit(" · ", 1)[1]
    return old_title


# SOC-101 F36: аудит видит только РАЗДЕЛЫ сида — потерянные хеши, статусы Linear и
# правила менеджера внутри раздела ему не видны. Голое «Потеряно: ничего» давало
# Грише ложное спокойствие, поэтому строка всегда говорит, чем мерили.
LOST_SCOPE = "по сверке разделов"


def lost_line(lost: Optional[List[str]], tails: Sequence[str] = ()) -> str:
    """Хвост строки «Потеряно: …» — с мерой, а не голым «ничего».

    *lost* — пропавшие разделы; ``None`` — сверка не прошла (тогда не «ничего»).
    *tails* — пункты блока «## открытые хвосты» сида (F36/F41): сверка разделов их
    не ловит, а по сути это и есть недоделанное — называем число и первый."""
    if lost is None:
        head = "сверка разделов не прошла — не знаю"
    elif not lost:
        head = LOST_SCOPE + " — ничего"
    else:
        head = clip("{0}: {1}".format(LOST_SCOPE, ", ".join(lost)), LOST_MAX)
    tails = [t for t in tails if t.strip()]
    if not tails:
        return head
    more = " (и ещё {0})".format(len(tails) - 1) if len(tails) > 1 else ""
    return "{0}; открытых хвостов {1}: {2}{3}".format(
        head, len(tails), clip(tails[0], TAILS_MAX), more)


def reception_text(*, new_title: str, old_title: str, taken: str,
                   lost: Optional[List[str]], passed: str = "",
                   tails: Sequence[str] = ()) -> str:
    """До четырёх строк Грише: кто у кого принял, что передано, что взято, что
    потеряно. Нет *passed* — строки «Передано» нет (старую не нашли)."""
    lines = ["{0} приняла у {1}".format(new_title, _short_old(new_title, old_title))]
    if passed and passed.strip():
        lines.append("Передано: " + clip(passed, PASSED_MAX + FLIGHT_MAX + 12))
    lines.append("Взято: " + taken_text(taken or "—"))
    lines.append("Потеряно: " + lost_line(lost, tails))
    return "\n".join(lines)


def not_alive_text(new_title: str, old_title: str) -> str:
    """Две строки Грише: кто не поднялся и что делать."""
    return ("{0} не поднялась, нить остаётся у {1}\n"
            "Делать ничего не надо: {2} работает дальше, передачу можно повторить".format(
                new_title, _short_old(new_title, old_title), _base(old_title)))


# --- диск ----------------------------------------------------------------------

def _title(entry: Optional[Path], fallback: str) -> str:
    if entry is None:
        return fallback
    t = (fields.read_field(entry / "arc.md", "title") or "").strip()
    if t and not t.startswith("<"):
        return t
    return _stream.thread_session_title(entry) or entry.name


def _session_of(rec: Dict[str, object]) -> Optional[Path]:
    seed = str(rec.get("seed") or "-")
    if seed == "-":
        return None
    d = Path(seed).parent.parent
    return d if (d / "arc.md").is_file() else None


def titles(home: Path, rec: Dict[str, object]) -> "tuple[str, str]":
    """(название новой, название старой) — как их зовут доска и AO."""
    from .handoff import _origin_entry

    new = _title(_session_of(rec), "новая сессия")
    try:
        origin = _origin_entry(Path(home), rec)
    except Exception:  # noqa: BLE001
        origin = None
    sid = str(rec.get("from_session") or "-")
    return new, _title(origin, "прошлой сессии" if sid == "-" else
                       "прошлой сессии {0}".format(sid[:8]))


def _lost(home: Path, key: str) -> Optional[List[str]]:
    """Разделы аудита со статусом missing (кроме понятой цели — её даёт «Взято»).
    ``None`` — аудит не прошёл: это не «ничего не потеряно» (F36)."""
    try:
        from . import handoff_audit
        report = handoff_audit.audit(Path(home), key)
    except Exception:  # noqa: BLE001 — нет аудита — не знаем, а не «ничего»
        return None
    return [s["title"] for s in report.get("sections") or []
            if s.get("status") == handoff_audit.MISSING and s.get("key") != "understood"]


TAILS_HEAD = "## открытые хвосты"
_NO_TAILS = re.compile(r"^[-—–\s]*(?:нет|ничего|—|-)?[.\s]*$", re.IGNORECASE)


def _seed_text(rec: Dict[str, object]) -> str:
    seed = str(rec.get("seed") or "-")
    if seed == "-":
        return ""
    try:
        return Path(seed).read_text(encoding="utf-8")
    except OSError:
        return ""


def seed_section(text: str, head: str) -> List[str]:
    """Строки раздела сида «## <head>…» (до следующего «## »); нет раздела — []."""
    out: List[str] = []
    inside = False
    for ln in (text or "").splitlines():
        if ln.startswith("## "):
            inside = ln.strip().lower().startswith(head.lower())
            continue
        if inside and ln.strip():
            out.append(ln.rstrip())
    return out


def open_tails(seed_text: str) -> List[str]:
    """Пункты блока «## открытые хвосты» сида: что красное, не проверено вживую,
    обещано. Блока нет или в нём «нет»/прочерк/заглушка — пусто (поведение прежнее)."""
    items: List[str] = []
    for ln in seed_section(seed_text, TAILS_HEAD):
        s = ln.strip()
        if ln[:1].isspace() or s.startswith(("<", "(")):
            continue  # продолжение пункта, заглушка, строка-источник
        body = s[2:].strip() if s.startswith(("- ", "* ")) else s
        if not _NO_TAILS.match(body) and not body.lower().startswith("снято:"):  # F74
            items.append(body)
    return items


# Метки, после которых в пульсе ПЕРЕДАЧА лежит «что передано»: «Где мы:» — первым,
# «В полёте:» — следом. Текст метки — до следующей метки (любой из контракта).
_PASSED_LABELS = (("где мы", re.compile(r"где\s+мы\s*:\s*", re.IGNORECASE)),
                  ("в полёте", re.compile(r"в\s+пол[её]те\s*:\s*", re.IGNORECASE)))


def _label_piece(text: str, label_re) -> str:
    from .. import handoff_contract as contract

    m = label_re.search(text)
    if m is None:
        return ""
    if contract._LABEL_RE is None:
        contract._LABEL_RE = contract._label_re()
    rest = text[m.end():]
    nxt = contract._LABEL_RE.search(" " + rest)
    piece = rest if nxt is None else rest[:max(nxt.start() - 1, 0)]
    return piece.strip().rstrip(".;").strip()


def passed_from_note(note: str) -> str:
    """«Передано» из пульса ПЕРЕДАЧА старой: «Где мы:» целыми фразами + «в полёте: …».
    Меток нет — текст после шапки «ПЕРЕДАЧА …». Пусто — нечего сказать."""
    text = " ".join((note or "").split())
    where = _label_piece(text, _PASSED_LABELS[0][1])
    flight = _label_piece(text, _PASSED_LABELS[1][1])
    if not where and not flight:
        from .. import handoff_contract as contract
        head = contract.pulse_head(text)
        body = sentences(head)[1:] if head else []  # первая фраза — «ПЕРЕДАЧА a → b»
        where = " ".join(body)
    out = clip_sentences(where, PASSED_MAX) if where else ""
    if flight:
        flight = clip_sentences(flight, FLIGHT_MAX)
        out = "{0}; в полёте: {1}".format(out.rstrip("."), flight) if out else (
            "в полёте: " + flight)
    return out


def _last_pass_note(passport: Path) -> str:
    """Самый свежий пульс ПЕРЕДАЧА в ``## context`` паспорта старой (без штампа)."""
    from .. import handoff_contract as contract

    try:
        text = Path(passport).read_text(encoding="utf-8")
    except OSError:
        return ""
    found = ""
    inside = False
    for ln in text.splitlines():
        if ln.startswith("## "):
            inside = ln.strip().lower().startswith("## context")
            continue
        if inside and ln.strip() and contract.is_pass_note(ln.strip()):
            found = ln.strip()
    return re.sub(r"^-?\s*\d{4}-\d{2}-\d{2}T[\d:]+\s*[—-]\s*", "", found)


_STAMP_HEAD = re.compile(r"^-?\s*\d{4}-\d{2}-\d{2}T[\d:]+\s*[—-]\s*")
_MANAGER_ID = re.compile(r"\bmanagement-\d+\b")


def _passport_fresh(passport: Path) -> str:
    """Свежее из паспорта старой, когда ПЕРЕДАЧИ нет: тело ``## cursor``, иначе
    последний пульс ``## context``, иначе ``## summary``."""
    try:
        text = Path(passport).read_text(encoding="utf-8")
    except OSError:
        return ""
    secs: Dict[str, List[str]] = {}
    cur = None
    for ln in text.splitlines():
        if ln.startswith("## "):
            cur = ln[3:].strip().lower().split(" ", 1)[0]
            secs.setdefault(cur, [])
            continue
        if cur is not None and ln.strip() and ln.strip() not in ("-", "—") \
                and "<!--" not in ln:
            secs[cur].append(ln.strip())

    def live(key: str) -> List[str]:
        # заготовка шаблона «<…>» бывает в несколько строк — вырезаем целиком
        text = re.sub(r"<[^<>]*>", "", "\n".join(secs.get(key) or []))
        return [ln.strip() for ln in text.splitlines() if ln.strip(" -—")]

    if live("cursor"):
        return " ".join(live("cursor"))
    if live("context"):
        return _STAMP_HEAD.sub("", live("context")[-1])
    return " ".join(live("summary"))


def _stale_manager(line: str) -> bool:
    """В строке — адрес менеджера, который уже не менеджер (tide whois manager)."""
    ids = set(_MANAGER_ID.findall(line or ""))
    if not ids:
        return False
    try:
        from .. import whois
        cur = str((whois.manager() or {}).get("ao_session") or "").strip()
    except Exception:  # noqa: BLE001
        return False
    return bool(cur) and cur not in ids


def passed_text(home: Path, rec: Dict[str, object], seed_text: str = "") -> str:
    """Что передающая отдала — из свежего (ошибка менеджера: «Передано» взяло
    устаревшую первую строку слоя «где мы» нити main с «менеджер management-11»):
    её пульс ПЕРЕДАЧА; нет его — курсор / последний пульс / итог её паспорта; нет
    паспорта — «где мы» сида без строк слоя (они идут до курсора и без штампа)
    и без строк с устаревшим адресом менеджера."""
    try:
        from .handoff import _origin_entry
        origin = _origin_entry(Path(home), rec)
    except Exception:  # noqa: BLE001
        origin = None
    if origin is not None:
        got = passed_from_note(_last_pass_note(origin / "arc.md"))
        if got:
            return got
        fresh = _passport_fresh(origin / "arc.md")
        if fresh:
            return clip_sentences(fresh, PASSED_MAX)
    where = [ln.strip()[2:].strip() if ln.strip().startswith("- ") else ln.strip()
             for ln in seed_section(seed_text, "## где мы") if not ln.startswith("  ")]
    stamped = [i for i, ln in enumerate(where) if _STAMP_HEAD.match(ln)]
    if stamped:  # свежее — пульсы; всё выше первого пульса без штампа — слой и курсор
        where = [_STAMP_HEAD.sub("", ln) for ln in where[stamped[0]:]]
    where = [ln for ln in where if ln and not _stale_manager(ln)]
    return clip_sentences(" ".join(where[:2]), PASSED_MAX) if where else ""


def compose_reception(home: Path, rec: Dict[str, object]) -> str:
    new, old = titles(home, rec)
    seed_text = _seed_text(rec)
    taken = ""
    for key in ("understood_goal", "first_pulse"):  # цель старт-гейта / курсор
        v = str(rec.get(key) or "-").strip()
        if v != "-":
            taken = v
            break
    if not taken:
        sess = _session_of(rec)
        taken = (fields.read_field(sess / "arc.md", "goal") or "").strip() if sess else ""
    try:
        passed = passed_text(home, rec, seed_text)
    except Exception:  # noqa: BLE001 — без «Передано» сообщение всё равно уходит
        passed = ""
    lost = _lost(home, str(rec["name"]))
    if lost is not None:
        lost = lost + _lost_decisions(home, rec, seed_text)
    return reception_text(new_title=new, old_title=old, taken=taken or "—",
                          lost=lost, passed=passed, tails=open_tails(seed_text))


def _lost_decisions(home: Path, rec: Dict[str, object], seed_text: str) -> List[str]:
    """SOC-101 F63: решения входного сида старой, выпавшие из нового сида молча
    (не перенесены и не «снято: …»), — тоже потеря; сверка разделов её не видит."""
    try:
        from .handoff import _origin_entry
        from .seed_draft import INPUT_SEED_NAME, dropped_awaiting, dropped_decisions
        origin = _origin_entry(Path(home), rec)
        if origin is None:
            return []
        prev = (origin / "input" / INPUT_SEED_NAME).read_text(encoding="utf-8")
        gone = dropped_decisions(prev, seed_text)
        waits = dropped_awaiting(prev, seed_text)  # SOC-143 F77
    except Exception:  # noqa: BLE001 — уведомление не падает
        return []
    out = []
    if gone:
        out.append("решения прошлого сида: выпало {0} ({1})".format(
            len(gone), clip(gone[0], 60)))
    if waits:
        out.append("«ждёт Гришу» прошлого сида: выпало {0} ({1})".format(
            len(waits), clip(waits[0], 60)))
    return out


def _log(home: Path, line: str) -> None:
    try:
        log = paths.state_dir(Path(home)) / ALERT_LOG
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("{0} {1}\n".format(datetime.now().isoformat(timespec="seconds"), line))
    except OSError:
        pass


def send(home: Path, text: str, *, source: str = "") -> str:
    """В ``$TIDE_ALERT_CMD`` (route=human) → ``sent`` / ``no-cmd`` / ``rc=N``."""
    from .autohandoff import ALERT_CMD_ENV, send_alert

    if not (os.environ.get(ALERT_CMD_ENV) or "").strip():
        _log(home, "не отправлено (нет ${0}): {1}".format(ALERT_CMD_ENV, text))
        return "no-cmd"
    rc = send_alert(text, route=ROUTE_HUMAN, source=source)
    _log(home, "{0}: {1}".format("отправлено" if rc == 0 else "rc={0}".format(rc), text))
    return "sent" if rc == 0 else "rc={0}".format(rc)


def _source(rec: Dict[str, object]) -> str:
    return "{0}/{1}".format(rec.get("project") or "-", rec.get("arc") or "-")


def announce_reception(home: Path, key: str) -> Optional[str]:
    """Одно сообщение о приёме оффера *key*; метка ``announced:`` — повтор молчит.
    → итог отправки или None (уже сказано / не принят)."""
    try:
        rec = hq._resolve(Path(home), key)
        if rec.get("status") != hq.STATUS_TAKEN:
            return None
        if hq._get(Path(str(rec["path"])).read_text(encoding="utf-8"), ANNOUNCED) != "-":
            return None
        stamp = datetime.now().isoformat(timespec="seconds")
        hq._set_field(Path(str(rec["path"])), ANNOUNCED, stamp)  # метка ДО отправки
        text = compose_reception(home, rec)
        if notify_mode() != NOTIFY_ALL:
            _log(home, "не отправлено (${0}={1}): {2}".format(NOTIFY_ENV, notify_mode(), text))
            outcome = "off"
        else:
            outcome = send(home, text, source=_source(rec))
        hq._set_field(Path(str(rec["path"])), ANNOUNCED, "{0} · {1}".format(stamp, outcome))
        return outcome
    except Exception:  # noqa: BLE001 — сообщение не смеет сорвать пульс
        return None


def announce_not_alive(home: Path, key: str) -> Optional[str]:
    """«<новая> не поднялась, нить остаётся у <старой>» — одно на неудачный подъём."""
    try:
        rec = hq._resolve(Path(home), key)
        new, old = titles(home, rec)
        text = not_alive_text(new, old)
        if notify_mode() == NOTIFY_OFF:
            _log(home, "не отправлено (${0}=off): {1}".format(NOTIFY_ENV, text))
            return "off"
        return send(home, text, source=_source(rec))
    except Exception:  # noqa: BLE001
        return None


# --- предложение закрыть старую — менеджеру (контракт v2 §3 п.4) -----------------

CLOSE_PROPOSED = "close-proposed"   # метка в оффере: «<штамп> · <кому> · sent|…»


def close_text(old_title: str, new_title: str) -> str:
    return ("{0} передала нить {1}, приём подтверждён; закрыть {0}?".format(
        old_title, new_title))


def _with_old_ao(home: Path, rec: Dict[str, object], old_title: str) -> str:
    """Имя старой с её AO-id («… · 02 · tide-stack-12») — менеджеру не сопоставлять
    руками. AO-id — по claude-сессии отдавшей (реестр / AO-стэш); нет — как было."""
    sid = str(rec.get("from_session") or "-")
    if sid == "-":
        return old_title
    try:
        from ..arc import holder
        from ..arc.stream import with_ao_id
        handle = holder.ao_handle_for(Path(home), sid)
    except Exception:  # noqa: BLE001
        return old_title
    return with_ao_id(old_title, handle) if handle else old_title


def _ao_adapter():
    from ..adapters.ao import AoAdapter
    return AoAdapter()


CLOSE_DEFERRED = "close-deferred"   # «<штамп> · <почему>» — предложение ждёт воркеров
DEFERRED_STAMP = "close-proposal.last"
DEFERRED_THROTTLE_SEC = 120


def _old_transcript(sid: str) -> Optional[Path]:
    from .seed_draft import transcript_path
    return transcript_path(sid)


def _old_workers(transcript: Optional[Path]) -> List[str]:
    from .seed_draft import background_workers
    return background_workers(transcript)


def _chain_end(home: Path, rec: Dict[str, object]) -> Dict[str, object]:
    """Последний принятый оффер по цепочке ИМЕННО этой передачи (SOC-143).

    От *rec* идём по офферам той же нити того же проекта: принявший *rec* сам
    отдал нить (``from-session`` = его сид, ``mode: continue``, принят позже) —
    шаг дальше. Параллельная сессия нити в эту цепочку не попадает: её оффер
    отдан другим сидом. → запись последнего оффера (сам *rec*, если дальше
    никто не передавал)."""
    project = str(rec.get("project") or "")
    thread = str(rec.get("arc") or "").split("/", 1)[0]
    try:
        taken = [r for r in hq.list_offers(home, status=hq.STATUS_TAKEN)
                 if str(r.get("project") or "") == project
                 and str(r.get("arc") or "").split("/", 1)[0] == thread
                 and hq._transfers_thread(r)]
    except Exception:  # noqa: BLE001 — очередь не читается: остаёмся на rec
        return rec
    cur, seen = rec, {str(rec.get("name") or "")}
    for _ in range(len(taken)):
        sid = str(cur.get("taken_by") or "").strip()
        at = str(cur.get("taken_at") or "")
        if not sid or sid == "-":
            break
        nxt = sorted((r for r in taken
                      if str(r.get("from_session") or "").strip() == sid
                      and str(r.get("taken_at") or "") >= at
                      and str(r.get("name") or "") not in seen),
                     key=lambda r: str(r.get("taken_at") or ""))
        if not nxt:
            break
        cur = nxt[0]
        seen.add(str(cur.get("name") or ""))
    return cur


def successor(home: Path, rec: Dict[str, object]) -> Dict[str, str]:
    """Кому теперь писать вместо отдавшей оффер *rec*: ``{ao_session, title}``.

    SOC-143: преемник ИМЕННО этой передачи — по цепочке её офферов
    (:func:`_chain_end`: принявший *rec*, а если он передал дальше — его
    преемник), а не новейшая сессия нити: в нити бывают две живые параллельные
    сессии, и письма одной не должны уходить преемнику другой. Адрес — AO-id
    принявшего по реестру, иначе хвост названия его сессии; whois по нити —
    только когда цепочка сессию не нашла (старые офферы без сида)."""
    out = {"ao_session": "", "title": ""}
    try:
        end = _chain_end(Path(home), rec)
        entry = _session_of(end)
        if entry is not None:
            from ..arc import holder
            sid = str(end.get("taken_by") or "").strip()
            title = _title(entry, "")
            ao = holder.ao_handle_for(Path(home), sid) if sid and sid != "-" else ""
            if not ao:
                pinned = (fields.read_field(entry / "arc.md", "claude-session") or "").strip()
                ao = holder.ao_handle_for(Path(home), pinned) if pinned else ""
            if not ao and title:
                from .. import whois
                m = whois._AO_TAIL.search(title)
                ao = m.group(1) if m else ""
            out = {"ao_session": ao, "title": title}
    except Exception:  # noqa: BLE001 — цепочка не прочиталась: запасные пути ниже
        out = {"ao_session": "", "title": ""}
    project = str(rec.get("project") or "").strip()
    thread = str(rec.get("arc") or "").split("/", 1)[0]
    if (not out["ao_session"] and not out["title"]
            and project and project != "-" and thread and thread != "-"):
        try:
            from .. import whois
            w = whois.thread("{0}/{1}".format(project, _slug.entry_slug(thread)), home=home)
            out = {"ao_session": w.get("ao_session", ""), "title": w.get("title", "")}
        except Exception:  # noqa: BLE001 — нет адреса по цепочке: запасной путь
            pass
    if not out["ao_session"]:
        try:
            from ..arc import holder
            out["ao_session"] = holder.ao_handle_for(Path(home), str(rec.get("taken_by") or ""))
        except Exception:  # noqa: BLE001
            pass
    if not out["title"]:
        try:
            out["title"] = titles(home, rec)[0]
        except Exception:  # noqa: BLE001
            out["title"] = "новая сессия"
    return out


def close_blockers(home: Path, rec: Dict[str, object]) -> List[str]:
    """Почему старую ЕЩЁ рано предлагать закрыть (F72, SOC-143) — та же проверка
    воркеров, что у автозакрытия (:func:`tide.hooks.autoclose.verdict`): живые фоновые
    воркеры по транскрипту старой, сабагент с промежуточным итогом или возобновлённый
    (:func:`~tide.arc.workers.interim_workers`), итог, не пересланный новой
    (:func:`~tide.arc.workers.unforwarded_results`), письма пары в очереди
    пересылки. Пусто — можно предлагать. Транскрипта нет — проверить нечем, не держим
    (менеджер решает сам, как до F72). Только чтение."""
    from ..arc import workers as ac

    home = Path(home)
    old_sid = str(rec.get("from_session") or "")
    tr = _old_transcript(old_sid) if old_sid else None
    if tr is None:
        return []
    out: List[str] = []
    live = _old_workers(tr)
    if live:
        out.append("воркеры старой живы ({0}): {1}".format(
            len(live), "; ".join(ln.lstrip("- ").split(" — ", 1)[0] for ln in live[:3])))
    interim = ac.interim_workers(tr)
    if interim:
        out.append("воркеры старой не сдали итог (промежуточный/возобновлён) ({0}): {1}"
                   .format(len(interim), "; ".join(interim[:3])))
    forward_to: List[str] = []
    try:
        from ..arc import holder
        old_ao = holder.ao_handle_for(home, old_sid)
        new_sid = str(rec.get("taken_by") or "")
        new_ao = holder.ao_handle_for(home, new_sid) if new_sid else ""
        head_ao = successor(home, rec).get("ao_session", "")
        forward_to = [a for a in (new_ao, head_ao) if a and a != old_ao]
    except Exception:  # noqa: BLE001
        pass
    taken = ac.parse_iso(rec.get("taken_at"))
    raised = ac.parse_iso(rec.get("raised_at"))
    stamps = [t for t in (taken, raised) if t is not None]
    if stamps and forward_to:
        left = ac.unforwarded_results(tr, min(stamps), forward_to)
        if left:
            out.append("итоги воркеров не пересланы новой: {0}".format(", ".join(left[:3])))
    queued = ac.pending_for(home, str(rec.get("name") or ""))
    if queued:
        out.append("в очереди пересылки {0} писем этой пары".format(queued))
    return out


def _close_to_manager(to: str, text: str, source: str) -> str:
    """SOC-179 (правило 3 токеномики — несрочное пачкой): «закрыть?» не срочно — в пульт
    ``route=manager``, он кладёт строку в пачку менеджеру (раз в 25 мин), а не будит
    его ``ao send`` на каждый приём. Нет ``$TIDE_ALERT_CMD`` или пульт не принял —
    как раньше, ``ao send`` на *to*. → ``batched`` / ``sent`` / ``not-delivered``."""
    from .autohandoff import send_alert

    try:
        rc = send_alert(text, route="manager", source=source)
    except Exception:  # noqa: BLE001
        rc = -1
    if rc == 0:
        return "batched"
    return "sent" if _ao_adapter().say(to, text) else "not-delivered"


def propose_close(home: Path, key: str) -> Optional[str]:
    """После приёма — менеджеру (адрес — :func:`tide.whois.manager`), пачкой через пульт
    (:func:`_close_to_manager`; без пульта — ``ao send``):
    «<старая> передала нить <новой>, приём подтверждён; закрыть <старую>?». Не
    старой сессии самой (она не закрывается сама) — тому, кто закрывает. Один раз:
    метка ``close-proposed:`` в оффере. → итог или None (уже предложено/не принят).

    F72: пока у старой живы воркеры или их итоги не пересланы новой
    (:func:`close_blockers`) — не предлагаем: метка ``close-deferred:`` и итог
    ``"deferred"``; предложение уйдёт из :func:`retry_deferred` (хук handoff-confirm),
    когда условие выполнится."""
    try:
        rec = hq._resolve(Path(home), key)
        if rec.get("status") != hq.STATUS_TAKEN or not hq._transfers_thread(rec):
            return None
        path = Path(str(rec["path"]))
        if hq._get(path.read_text(encoding="utf-8"), CLOSE_PROPOSED) != "-":
            return None
        wait = close_blockers(home, rec)
        if wait:
            why = "; ".join(wait)
            prev = hq._get(path.read_text(encoding="utf-8"), CLOSE_DEFERRED)
            if prev == "-" or prev.split(" · ", 1)[-1] != why:
                hq._set_field(path, CLOSE_DEFERRED, "{0} · {1}".format(
                    datetime.now().isoformat(timespec="seconds"), why))
                _log(home, "предложение закрыть отложено ({0}): {1}".format(
                    rec.get("name"), why))
            return "deferred"
        stamp = datetime.now().isoformat(timespec="seconds")
        hq._set_field(path, CLOSE_PROPOSED, stamp)  # метка ДО отправки
        new, old = titles(home, rec)
        text = close_text(_with_old_ao(home, rec, old), new)
        try:
            from .. import whois
            to = whois.manager(home=Path(home)).get("ao_session", "")
        except Exception:  # noqa: BLE001
            to = ""
        if not to:
            outcome = "no-manager"
        else:
            outcome = _close_to_manager(to, text, _source(rec) + "/close")
        _log(home, "предложение закрыть → {0} ({1}): {2}".format(to or "?", outcome, text))
        hq._set_field(path, CLOSE_PROPOSED, "{0} · {1} · {2}".format(stamp, to or "-", outcome))
        return outcome
    except Exception:  # noqa: BLE001 — предложение не смеет сорвать пульс
        return None


def _deferred_throttled(home: Path) -> bool:
    p = paths.state_dir(Path(home)) / DEFERRED_STAMP
    try:
        if (p.is_file()
                and datetime.now().timestamp() - p.stat().st_mtime < DEFERRED_THROTTLE_SEC):
            return True
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    except OSError:
        return True
    return False


def retry_deferred(home: Path, *, throttle: bool = True) -> List[str]:
    """Отложенные предложения закрыть (F72): свежие принятые пары с ``close-deferred:``
    и без ``close-proposed:`` — снова :func:`propose_close`; уйдёт, когда воркеры
    старой сдали итоги и те пересланы новой. Зовётся из хука handoff-confirm рядом
    с автозакрытием, не чаще раза в :data:`DEFERRED_THROTTLE_SEC` с. Старая, взявшая
    потом другую нить, — снова голова: ей не предлагаем. → ``["<оффер>: <итог>"]``.
    Никогда не падает."""
    out: List[str] = []
    try:
        home = Path(home)
        recs = []
        for r in hq.list_offers(home, status=hq.STATUS_TAKEN):
            text = Path(str(r["path"])).read_text(encoding="utf-8")
            if (hq._get(text, CLOSE_DEFERRED) != "-"
                    and hq._get(text, CLOSE_PROPOSED) == "-" and _fresh(r)):
                recs.append(r)
        if not recs or (throttle and _deferred_throttled(home)):
            return out
        for r in recs:
            sid = str(r.get("from_session") or "")
            if sid and (hq.is_dissolved(home, sid) or {}).get("name") != r.get("name"):
                continue
            res = propose_close(home, str(r["name"]))
            out.append("{0}: {1}".format(r.get("name"), res))
    except Exception:  # noqa: BLE001 — хук не смеет уронить сессию
        pass
    return out


# --- после пульса (компонуется в cli: offload — домен и launcher не видит) ----------

def _taken_offer_for(home: Path, session_dir: Path) -> Optional[Dict[str, object]]:
    try:
        target = Path(session_dir).resolve()
    except OSError:
        return None
    for r in hq.list_offers(home, status=hq.STATUS_TAKEN):
        sess = _session_of(r)
        try:
            if sess is not None and sess.resolve() == target:
                return r
        except OSError:
            continue
    return None


FRESH_HOURS = 24  # приём старше — история: первый пульс давнего приёма не повод


def _fresh(rec: Dict[str, object]) -> bool:
    try:
        taken = datetime.fromisoformat(str(rec.get("taken_at") or ""))
    except ValueError:
        return False
    if taken.tzinfo is not None:
        taken = taken.astimezone().replace(tzinfo=None)
    return (datetime.now() - taken).total_seconds() <= FRESH_HOURS * 3600


def wrap_offload(sub) -> None:
    """Научить ``tide offload`` слать сообщение о приёме: если ЭТОТ пульс поставил
    принявшей сессии ``first-pulse:`` в оффер — это и есть приём, сказать Грише."""
    p = sub.choices.get("offload") if hasattr(sub, "choices") else None
    if p is None:
        return
    inner = p.get_default("func")
    if inner is None:
        return

    def _offload_and_announce(args) -> int:
        before = None
        home = None
        session_dir = None
        try:
            from .. import offload as _offload
            home = paths.control_home()
            session_dir = _offload.find_session(paths.require_tide_root(), args.session)
            if session_dir is not None:
                rec = _taken_offer_for(home, session_dir)
                before = str(rec.get("first_pulse") or "-") if rec else None
        except Exception:  # noqa: BLE001
            session_dir = None
        rc = inner(args)
        if rc != 0 or home is None or session_dir is None:
            return rc
        try:
            rec = _taken_offer_for(home, session_dir)
            if (rec and before in (None, "-") and _fresh(rec)
                    and str(rec.get("first_pulse") or "-") != "-"):
                announce_reception(home, str(rec["name"]))
                propose_close(home, str(rec["name"]))
        except Exception:  # noqa: BLE001
            pass
        return rc

    p.set_defaults(func=_offload_and_announce)


__all__ = ["ANNOUNCED", "CLOSE_DEFERRED", "CLOSE_PROPOSED", "close_blockers",
           "retry_deferred", "announce_not_alive", "announce_reception",
           "clip", "clip_sentences", "close_text", "lost_line", "open_tails",
           "passed_from_note", "passed_text", "propose_close",
           "compose_reception", "not_alive_text", "reception_text", "send", "titles",
           "wrap_offload"]

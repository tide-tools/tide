"""tide.launcher.handoff_audit — ``tide handoffs audit <id>``: чего сиду не хватило.

Хендофф переносит три источника в один файл: паспорт прошлой сессии (курсор,
пульс, «дальше»), нить (решения, «где мы», опыт, ``dialog.md``) и — на приёме —
понимание новой сессии (SOC-101 п.2). Аудит кладёт их рядом с сидом оффера и по
каждому разделу говорит, что дошло, что дошло сжатым и чего в сиде нет.

Разделы — :data:`AUDIT_SECTIONS` из единого контракта сида
(:mod:`tide.handoff_contract`). Источник берётся на момент оффера: пульс и реплики
позже ``created:`` сид нести не мог и в счёт не идут. Сверка текстовая и грубая — ключ строки (начало
без штампа времени) ищется в сиде; это сигнал куда смотреть, а не приговор.

Чистые функции (``audit_section``, ``render``) — без диска; :func:`audit` читает
диск; ``_cmd_audit`` — тонкий CLI (человекочитаемо или ``--json`` для доски).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .. import fields, handoff_contract as contract, handoff_queue as hq, offload, \
    paths, resolve
from ..arc import dialog as _dialog
from . import seed_draft

# Что сверяем: ключ (для --json/доски) → подпись по-человечески. Живёт в
# контракте сида (tide.handoff_contract) — подпись называет блок, куда источник
# должен был дойти.
AUDIT_SECTIONS = contract.AUDIT_SECTIONS

DIALOG_TAIL = seed_draft.DIALOG_TAIL  # как у сида
KEY_CHARS = 60     # ключ строки — столько знаков её начала
GIST_CHARS = 40    # ключ сжатой строки (pulse_gist) — короче
SHOW_MISSING = 5   # в человеческом выводе — столько пропусков на раздел

OK, PARTIAL, MISSING, EMPTY = "ok", "partial", "missing", "n/a"
_GLYPH = {OK: "✓", PARTIAL: "≈", MISSING: "✗", EMPTY: "·"}

_STAMP = re.compile(r"^\s*-?\s*(\d{4}-\d{2}-\d{2}T[\d:]+(?:[+-]\d{2}:?\d{2}|Z)?)\s*[—·-]?\s*")
_WS = re.compile(r"\s+")


class AuditError(seed_draft.SeedDraftError):
    """Ошибка пользователю (нет оффера, нет сида)."""


# --- сверка (чистое) ---------------------------------------------------------

def norm(text: str) -> str:
    """Для сравнения: нижний регистр, без разметки, пробелы схлопнуты."""
    t = (text or "").lower().replace("`", "").replace("*", "").replace("ё", "е")
    return _WS.sub(" ", t).strip()


def line_key(line: str) -> str:
    """Начало строки без маркера списка и штампа времени — по нему ищем в сиде."""
    raw = _STAMP.sub("", line.strip().lstrip("-").strip())
    return norm(raw.lstrip("— ·-"))[:KEY_CHARS]


def _stamp_of(line: str) -> Optional[datetime]:
    m = _STAMP.match(line)
    if not m:
        return None
    try:
        ts = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts.astimezone().replace(tzinfo=None) if ts.tzinfo else ts


def before(lines: Sequence[str], cutoff: Optional[datetime]) -> List[str]:
    """Строки не позже *cutoff* (строка без штампа — остаётся)."""
    if cutoff is None:
        return list(lines)
    out = []
    for ln in lines:
        ts = _stamp_of(ln)
        if ts is None or ts <= cutoff:
            out.append(ln)
    return out


def audit_section(key: str, title: str, source: Sequence[str], seed_text: str,
                  *, gist: bool = False) -> Dict[str, object]:
    """Сколько строк *source* дошло до сида: целиком / сжатыми (*gist*) / нет."""
    hay = norm(seed_text)
    present, compressed, missing = 0, 0, []
    items = [ln for ln in source if line_key(ln)]
    for ln in items:
        if line_key(ln) in hay:
            present += 1
            continue
        if gist:
            short = seed_draft.pulse_gist(ln)
            gkey = line_key(short).rstrip("…")[:GIST_CHARS]
            if gkey and gkey in hay:
                compressed += 1
                continue
        missing.append(ln.strip())
    if not items:
        status = EMPTY
    elif not missing and not compressed:
        status = OK
    elif present or compressed:
        status = PARTIAL
    else:
        status = MISSING
    return {"key": key, "title": title, "status": status, "total": len(items),
            "present": present, "compressed": compressed, "missing": missing}


def _decision_line(rec: Dict[str, object]) -> str:
    what = str(rec.get("what") or "").strip() or str(rec.get("slug") or "")
    return "- {0} — {1}".format(rec.get("num"), what)


def audit_decisions(records: Sequence[Dict[str, object]], seed_text: str,
                    title: str) -> Dict[str, object]:
    """Решения в силе: номер в блоке «решения» сида или их суть где угодно в сиде."""
    from ..arc import decision  # lazy: reaches up into cli (known debt)

    live = [r for r in records
            if decision.normalize_status(str(r.get("status") or "")) == decision.ACCEPTED]
    block = "\n".join(contract.find_body(seed_draft.sections(seed_text), "decisions"))
    # У решений нет даты, но номер — хронология: номер старше последнего в блоке
    # сида — вероятно, принято ПОСЛЕ оффера; в пропуски не считаем, называем
    # отдельно («after»). Дыра ВНУТРИ ряда сида — настоящий пропуск.
    nums = [int(n) for n in re.findall(r"(?m)^\s*-\s*(\d+)\b", block)]
    later = [r for r in live if nums and str(r.get("num")).isdigit()
             and int(str(r.get("num"))) > max(nums)]
    live = [r for r in live if r not in later]
    res = audit_section("decisions", title, [_decision_line(r) for r in live], seed_text)
    missing = []
    for r in live:
        line = _decision_line(r)
        if line.strip() not in res["missing"]:
            continue
        if re.search(r"(?m)^\s*-\s*{0}\b".format(re.escape(str(r.get("num")))), block):
            res["present"] = int(res["present"]) + 1
        else:
            missing.append(line.strip())
    res["missing"] = missing
    res["after"] = len(later)
    if live:
        res["status"] = OK if not missing else (PARTIAL if res["present"] else MISSING)
    return res


# Пульс, который пишет механика приёма (handoff_queue._stamp_reception), — не
# содержимое прошлой сессии, сиду его нести незачем.
_MECHANICAL = (hq._MECHANICAL_MARK,)


def _own(lines: Sequence[str]) -> List[str]:
    return [ln for ln in lines if not any(m in ln for m in _MECHANICAL)]


def audit_understood(rec: Dict[str, object], title: str,
                     passport: Optional[Path] = None) -> Dict[str, object]:
    """Понятая цель (SOC-101 п.2): записал ли приём цель и первый пульс.

    Первый пульс — самый первый настоящий пульс принявшей сессии после приёма,
    с её паспорта (*passport*); штамп ``first-pulse:`` оффера — запасной: его
    ставил первый пульс, дошедший до кода, а не первый вообще."""
    goal = str(rec.get("understood_goal") or "-")
    pulse = str(rec.get("first_pulse") or "-")
    pulse_at = str(rec.get("first_pulse_at") or "-")
    if passport is not None and rec.get("status") == hq.STATUS_TAKEN:
        found = hq.first_real_pulse(passport, rec.get("taken_at"))
        if found:
            pulse, pulse_at = found
    have = [x for x in (goal, pulse) if x != "-"]
    missing = []
    if goal == "-":
        missing.append("цель принявшей сессии не записана")
    if pulse == "-":
        missing.append("первого пульса принявшей сессии нет")
    if rec.get("status") != hq.STATUS_TAKEN:
        status = EMPTY
        missing = ["оффер ещё не принят ({0})".format(rec.get("status"))]
    else:
        status = OK if len(have) == 2 else (PARTIAL if have else MISSING)
    return {"key": "understood", "title": title, "status": status, "total": 2,
            "present": len(have), "compressed": 0, "missing": missing,
            "goal": None if goal == "-" else goal,
            "first_pulse": None if pulse == "-" else pulse,
            "first_pulse_at": None if pulse_at == "-" else pulse_at}


# --- чтение диска ------------------------------------------------------------

def _layers(thread_dir: Path) -> Dict[str, List[str]]:
    """``## where we are`` нити по слоям ``###`` → живые строки (без заглушек)."""
    from ..arc import stream as _stream

    return seed_draft.layers(seed_draft._read(_stream.passport_path(thread_dir)))


def _prior_session(session_dir: Path, rec: Dict[str, object]) -> Optional[Path]:
    """Паспорт прошлой сессии: ``from:`` принявшей сессии среди соседей по нити,
    иначе по ``from-session`` оффера (claude-id)."""
    origin = (fields.read_field(session_dir / "arc.md", "from") or "").strip()
    siblings = sorted(p for p in session_dir.parent.iterdir()
                      if p.is_dir() and p != session_dir and (p / "arc.md").is_file())
    if origin and origin != "-" and not origin.startswith("<"):
        tail = origin.rsplit("/", 1)[-1]
        for p in siblings:
            if p.name == tail or resolve.entry_matches(tail, p.name):
                return p
    sid = str(rec.get("from_session") or "").strip()
    root = paths.find_tide_root(session_dir)
    if sid and sid != "-" and root is not None:
        try:
            return offload.find_session_by_claude_id(root, sid)
        except Exception:  # noqa: BLE001
            return None
    return None


def _cutoff(rec: Dict[str, object]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(rec.get("created")))
    except ValueError:
        return None


def audit(home: Path, key: str) -> Dict[str, object]:
    """Сверить сид оффера *key* с паспортом прошлой сессии, нитью и её диалогом."""
    from ..arc import decision  # lazy: reaches up into cli (known debt)

    try:
        rec = hq._resolve(home, key)
    except hq.HandoffError as exc:
        raise AuditError(str(exc))
    seed_path = Path(str(rec.get("seed") or "-"))
    if str(rec.get("seed")) in ("-", "") or not seed_path.is_file():
        raise AuditError("handoffs audit: у оффера {0} нет сида на диске ({1})".format(
            rec["name"], rec.get("seed")))
    seed_text = seed_draft._read(seed_path)
    session_dir = seed_path.parent.parent
    thread_dir = session_dir.parent.parent
    cutoff = _cutoff(rec)
    prior = _prior_session(session_dir, rec)
    titles = dict(AUDIT_SECTIONS)

    psecs = seed_draft.sections(seed_draft._read(prior / "arc.md")) if prior else {}
    cursor = seed_draft.live_lines(seed_draft.section_body(psecs, "cursor"))[:1]
    context = _own(before(seed_draft.live_lines(seed_draft.section_body(psecs, "context")),
                          cutoff))
    nxt = [ln.lstrip("- ").strip()
           for ln in seed_draft.block_next(seed_draft.section_body(psecs, "next"))]

    layers = _layers(thread_dir)
    prior_seed = seed_draft._read(prior / "input" / seed_draft.INPUT_SEED_NAME) if prior else ""
    inherited = [ln for ln in seed_draft.live_lines(
        contract.find_body(seed_draft.sections(prior_seed), "experience"))
        if "<!--" not in ln and not seed_draft.is_hole(ln)]
    def layer(key: str) -> List[str]:  # «### где мы — 9 окт …» → по началу
        return seed_draft.layer(layers, key)

    experience = list(dict.fromkeys(layer("experience") + inherited))
    expectation = layer("expectation") + layer("where")

    root = paths.find_tide_root(thread_dir)
    try:
        from .. import slug as _slug
        records = decision.list_decisions(root, _slug.entry_slug(thread_dir.name)) \
            if root else []
    except Exception:  # noqa: BLE001 — no decisions log is a fine answer
        records = []

    tail = before(_dialog.tail(thread_dir, n=0), cutoff)[-DIALOG_TAIL:]

    sections = {
        "cursor": audit_section("cursor", titles["cursor"], cursor, seed_text),
        "context": audit_section("context", titles["context"], context, seed_text,
                                 gist=True),
        "next": audit_section("next", titles["next"], nxt, seed_text),
        "decisions": audit_decisions(records, seed_text, titles["decisions"]),
        "experience": audit_section("experience", titles["experience"], experience,
                                    seed_text),
        "expectation": audit_section("expectation", titles["expectation"], expectation,
                                     seed_text),
        "dialog": audit_section("dialog", titles["dialog"], tail, seed_text),
        "understood": audit_understood(rec, titles["understood"],
                                       session_dir / "arc.md"),
    }
    return {
        "offer": rec["name"], "status": rec["status"], "created": rec.get("created"),
        "seed": str(seed_path), "session": str(session_dir), "thread": str(thread_dir),
        "prior": str(prior) if prior else None,
        "sections": [sections[k] for k, _t in AUDIT_SECTIONS],
    }


# --- вывод -------------------------------------------------------------------

def render(report: Dict[str, object]) -> str:
    """Человекочитаемо: строка на раздел, под ней — чего не хватает."""
    lines = [
        "аудит хендоффа {0} ({1})".format(report["offer"], report["status"]),
        "  сид:     {0}".format(report["seed"]),
        "  прошлая: {0}".format(report.get("prior") or "— не нашлась (нет from: и from-session)"),
        "",
    ]
    for s in report["sections"]:
        if s["key"] == "understood":
            head = "{0} {1}".format(_GLYPH[s["status"]], s["title"])
            lines.append(head)
            if s.get("goal"):
                lines.append("    цель: {0}".format(s["goal"]))
            if s.get("first_pulse"):
                lines.append("    первый пульс: {0}".format(s["first_pulse"]))
            lines += ["    нет: {0}".format(m) for m in s["missing"]]
            continue
        count = "{0}/{1}".format(s["present"], s["total"]) if s["total"] else "источник пуст"
        if s["compressed"]:
            count += ", сжато {0}".format(s["compressed"])
        if s.get("after"):
            count += ", ещё {0} новее сида (вероятно, после оффера)".format(s["after"])
        lines.append("{0} {1} — {2}".format(_GLYPH[s["status"]], s["title"], count))
        miss = list(s["missing"])
        for m in miss[:SHOW_MISSING]:
            lines.append("    нет: {0}".format(m if len(m) <= 140 else m[:139] + "…"))
        if len(miss) > SHOW_MISSING:
            lines.append("    … и ещё {0} (--json — все)".format(len(miss) - SHOW_MISSING))
    return "\n".join(lines)


def _cmd_audit(args) -> int:
    report = audit(paths.control_home(), args.key)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
    return 0


def register_audit(hsub) -> None:
    """Повесить ``audit`` на группу ``handoffs`` (компонуется в cli)."""
    ap = hsub.add_parser(
        "audit",
        help="what a handoff seed lacks vs the prior session passport, the thread "
             "and its dialog.md (+ the understood goal)")
    ap.add_argument("key", help="offer NN / NN-slug / slug")
    ap.add_argument("--json", action="store_true", help="machine-readable, for the board")
    ap.set_defaults(func=_cmd_audit, _cmd="handoffs audit")

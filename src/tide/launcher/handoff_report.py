"""SOC-233: отчёт агента о проделанной работе — запись в ``reports.jsonl``.

Слово Гриши: «отчеты о проделанной работе на доску от агентов и в телеграм».
Каждый агент-исполнитель (не сабагент) по готовности работы (итог задачи,
In Review, конец цикла, хендофф) делает ``tide report --done … --proof … --next …
--waits … --soc SOC-N``. tide в Telegram НЕ шлёт: он дописывает строку в
``<management>/.tide/state/reports.jsonl`` (один источник), пульт читает её и
шлёт в тему проекта, доска читает её для ленты «Отчёты». В паспорт сессии ставится
``report: <id> <ISO>``. Без него закрыть передачу нельзя (:func:`require_report`);
аварийно — ``TIDE_HANDOFF_NO_REPORT=1`` (в журнал).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .. import fields, paths, slug
from ..arc.stream import StreamError

REPORTS_FILE_ENV = "TIDE_REPORTS_FILE"
NO_REPORT_ENV = "TIDE_HANDOFF_NO_REPORT"
REPORT_FIELD = "report"
LOG_NAME = "handoff-report.log"
REPORTS_NAME = "reports.jsonl"
MANAGEMENT_PROJECT = "management"
KINDS = ("done", "handoff", "cycle")
WORK_FLAGS = ("done", "proof", "next", "waits", "soc", "kind")

REPORT_FORMAT = ('tide report --kind handoff --done "…" --proof "<путь или url>" --next "…" '
                 '--waits "…" --soc SOC-N')


class ReportError(StreamError):
    pass


# --- куда писать --------------------------------------------------------------

def state_dir() -> Path:
    """``<management>/.tide/state`` (проект management из ростера control-home);
    нет такого — ``<control-home>/.tide/state``."""
    from .. import roster

    home = paths.control_home()
    try:
        for e in roster.read_roster(home):
            if e["name"] == MANAGEMENT_PROJECT:
                return paths.state_dir(Path(e["path"]).expanduser())
    except Exception:  # noqa: BLE001
        pass
    return paths.state_dir(home)


def reports_file() -> Path:
    env = (os.environ.get(REPORTS_FILE_ENV) or "").strip()
    return Path(env).expanduser() if env else state_dir() / REPORTS_NAME


def _log(line: str) -> None:
    try:
        d = state_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / LOG_NAME, "a", encoding="utf-8") as fh:
            fh.write("{0} {1}\n".format(datetime.now().astimezone().isoformat(timespec="seconds"), line))
    except Exception:  # noqa: BLE001
        pass


# --- запись -------------------------------------------------------------------

def _socs(*chunks: str) -> List[str]:
    out: List[str] = []
    for c in chunks:
        for m in re.findall(r"SOC-\d+", c or "", re.I):
            m = m.upper()
            if m not in out:
                out.append(m)
    return out


def build_text(rec: Dict[str, object]) -> str:
    """Готовый текст 3–8 строк для Telegram из полей записи."""
    lines = []
    soc = " ".join(rec.get("soc") or [])
    head = "{0} · {1}".format(rec.get("project"), rec.get("role"))
    lines.append(head + (" · " + soc if soc else ""))
    lines.append("Сделано: " + str(rec.get("done") or "—"))
    proof = rec.get("proof") or []
    if proof:
        lines.append("Пруф: " + "; ".join(proof))
    lines.append("Дальше: " + str(rec.get("next") or "—"))
    if rec.get("waits"):
        lines.append("Ждёт решения: " + str(rec["waits"]))
    return "\n".join(lines[:8])


def make_record(entry: Path, *, done: str = "", proof: Optional[List[str]] = None,
                next_: str = "", waits: str = "", soc: Optional[List[str]] = None,
                kind: str = "done", text: str = "",
                now: Optional[datetime] = None) -> Dict[str, object]:
    root = paths.find_tide_root(Path(entry))
    thread_dir = Path(entry).parents[1]
    now = (now or datetime.now().astimezone()).replace(microsecond=0)
    rec: Dict[str, object] = {
        "id": "r-{0}-{1}".format(now.strftime("%Y%m%d-%H%M"), secrets.token_hex(2)),
        "at": now.isoformat(),
        "project": root.name if root else "-",
        "thread": thread_dir.name,
        "role": slug.entry_slug(thread_dir.name),
        "session": Path(entry).name,
        "ao": (os.environ.get("AO_SESSION_ID") or "").strip(),
        "kind": kind or "done",
        "soc": _socs(" ".join(soc or []), done, text, next_, waits),
        "done": done or text,
        "proof": list(proof or []),
        "next": next_,
        "waits": waits,
    }
    rec["text"] = text.strip() if (text and done) else build_text(rec)
    return rec


def append_record(rec: Dict[str, object], path: Optional[Path] = None) -> Path:
    """Одна строка JSON одним ``write`` в режиме append (параллельные писатели не рвут строки)."""
    path = Path(path) if path else reports_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return path


def write_report(entry: Path, **kw) -> Dict[str, object]:
    if not (kw.get("done") or kw.get("text") or "").strip():
        raise ReportError("пустой отчёт: нужен --done или текст")
    rec = make_record(entry, **kw)
    append_record(rec)
    fields.set_field(Path(entry) / "arc.md", REPORT_FIELD, "{0} {1}".format(rec["id"], rec["at"]))
    _log("{0} {1}/{2} {3}".format(rec["id"], rec["thread"], rec["session"], rec["kind"]))
    return rec


# --- гейт ---------------------------------------------------------------------

def has_report(entry: Path) -> bool:
    val = (fields.read_field(Path(entry) / "arc.md", REPORT_FIELD) or "").strip()
    return val.startswith("r-")


def require_report(entry: Optional[Path]) -> None:
    """Гейт передачи: сессия без отчёта в паспорте → ReportError (любая роль)."""
    if entry is None or has_report(entry):
        return
    if (os.environ.get(NO_REPORT_ENV) or "").strip() in ("1", "true", "yes"):
        _log("bypass {0}=1: {1}/{2}".format(NO_REPORT_ENV, Path(entry).parents[1].name,
                                            Path(entry).name))
        print("tide: ⚠ отчёт пропущен ({0}=1), записано в журнал".format(NO_REPORT_ENV),
              file=sys.stderr)
        return
    raise ReportError(
        "передача без отчёта о работе. Сначала:\n  {0}\n"
        "(сделано с пруфом · дальше · что ждёт решения пользователя · SOC-N; уйдёт в "
        "Telegram-тему проекта и ленту «Отчёты»). Аварийно: {1}=1.".format(
            REPORT_FORMAT, NO_REPORT_ENV))


def report_step(entry: Path) -> str:
    """Шаг 0 шаблона передачи — у всех ролей."""
    return ("  0. отчёт о работе — первым шагом, без него передача не закроется (3–8 строк "
            "по-русски, без id сессий: сделано с пруфом · дальше · что ждёт пользователя · "
            "SOC-N; уйдёт в Telegram-тему проекта и ленту «Отчёты» на доске):\n"
            "     {0}\n").format(REPORT_FORMAT)


def current_entry(root: Path, ref: Optional[str] = None) -> Optional[Path]:
    """Своя сессия (по ref или sid) — или None, если не определилась."""
    from . import seed_draft

    try:
        return seed_draft.resolve_session(Path(root), ref)
    except Exception:  # noqa: BLE001
        return None


# --- CLI ----------------------------------------------------------------------

def has_work_flags(args) -> bool:
    return any(getattr(args, k, None) for k in WORK_FLAGS)


def run_cli(args) -> int:
    root = paths.find_tide_root()
    if root is None:
        print("tide: report: не в tide-проекте", file=sys.stderr)
        return 1
    from . import seed_draft

    try:
        entry = seed_draft.resolve_session(root, getattr(args, "session", None))
    except Exception as exc:  # noqa: BLE001
        print("tide: report: {0}".format(exc), file=sys.stderr)
        return 1
    words = getattr(args, "what", None) or getattr(args, "text", None) or []
    text = " ".join(words).strip()
    try:
        rec = write_report(
            entry, done=(getattr(args, "done", None) or ""), proof=getattr(args, "proof", None),
            next_=(getattr(args, "next", None) or ""), waits=(getattr(args, "waits", None) or ""),
            soc=[getattr(args, "soc", None) or ""], kind=(getattr(args, "kind", None) or "done"),
            text=text)
    except ReportError as exc:
        print("tide: report: {0}".format(exc), file=sys.stderr)
        return 1
    print("tide: отчёт {0} записан ({1}); пульс отправит в Telegram, доска покажет в ленте"
          .format(rec["id"], reports_file()))
    return 0


def add_work_flags(p) -> None:
    p.add_argument("--done", default=None, help="что сделано (отчёт о работе, SOC-233)")
    p.add_argument("--proof", action="append", default=None, help="пруф: путь или url (много)")
    p.add_argument("--next", default=None, help="что дальше")
    p.add_argument("--waits", default=None, help="что ждёт решения пользователя")
    p.add_argument("--soc", default=None, help="SOC-N (также берутся из текста)")
    p.add_argument("--kind", default=None, help="done | handoff | cycle")
    p.add_argument("--session", default=None, help="арка-сессия (по умолчанию своя)")


def register_report(hsub) -> None:
    """``tide handoffs report`` — алиас ``tide report`` с полями работы."""
    ap = hsub.add_parser("report", help="алиас tide report: отчёт о работе (SOC-233)")
    ap.add_argument("text", nargs="*", help="текст отчёта")
    add_work_flags(ap)
    ap.set_defaults(func=run_cli, _cmd="handoffs report")

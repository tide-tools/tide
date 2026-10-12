"""SOC-233: отчёт лида в Telegram на хендоффе.

Слово Гриши: «Пусть менеджер в конце работы сессии пишет отчёт о работе сюда»,
«Роли-лиды пусть тоже пишут отчёт на хендоффе». Лид — ведущая нить проекта
(:data:`LEADS`) или менеджер. Уходящая сессия лида первым шагом передачи шлёт
``tide handoffs report "<текст>"``: проектный лид — в тему проекта группы «tide
agents» (``role-topics.json`` пульта, тема с ``route`` этого проекта), менеджер —
в личку. В паспорт сессии пишется ``handoff-report: sent <ISO>``; без этой
отметки закрыть передачу лиду нельзя (:func:`require_report`). Не лид — ничего
не меняется. Аварийный обход — ``TIDE_HANDOFF_NO_REPORT=1`` (в журнал).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import fields, paths, slug
from ..arc.stream import StreamError

ALERT_CMD_ENV = "TIDE_ALERT_CMD"
ROLE_TOPICS_ENV = "TIDE_ROLE_TOPICS"
NO_REPORT_ENV = "TIDE_HANDOFF_NO_REPORT"
REPORT_FIELD = "handoff-report"
LOG_NAME = "handoff-report.log"
GROUP = "pulse"

# проект (имя в ростере) → нити-лиды (слаг без «NN-@»)
LEADS: Dict[str, Tuple[str, ...]] = {
    "app-development": ("main", "techlead"),
    "music": ("main",),
    "nochnaya-smena": ("main",),
    "mitehq": ("main", "bidbook"),
    "tide-stack": ("main", "board"),
}

REPORT_FORMAT = ('tide handoffs report "Сделано: … (пруф) · В работе: … у кого · '
                 'Ждёт решения Гриши: … · SOC-N"')


class ReportError(StreamError):
    pass


def role_of(entry: Path) -> Optional[Tuple[str, str]]:
    """``("manager", проект)`` / ``("lead", проект)`` / None — не лид."""
    try:
        from .. import whois

        root = paths.find_tide_root(Path(entry))
        if root is None:
            return None
        thread_dir = Path(entry).parents[1]
        if whois.is_manager_thread(root, thread_dir):
            return ("manager", root.name)
        word = slug.entry_slug(thread_dir.name)
        if word in LEADS.get(root.name, ()):
            return ("lead", root.name)
    except Exception:  # noqa: BLE001 — не лид, а не падение передачи
        return None
    return None


def is_lead(entry: Path) -> bool:
    return role_of(entry) is not None


def _log(line: str) -> None:
    try:
        d = paths.state_dir(paths.control_home())
        d.mkdir(parents=True, exist_ok=True)
        with open(d / LOG_NAME, "a", encoding="utf-8") as fh:
            fh.write("{0} {1}\n".format(datetime.now().isoformat(timespec="seconds"), line))
    except Exception:  # noqa: BLE001
        pass


def role_topics_path() -> Optional[Path]:
    """role-topics.json пульта: ``$TIDE_ROLE_TOPICS`` или рядом с ``$TIDE_ALERT_CMD``."""
    env = (os.environ.get(ROLE_TOPICS_ENV) or "").strip()
    if env:
        return Path(env).expanduser()
    try:
        first = shlex.split(os.environ.get(ALERT_CMD_ENV) or "")[0]
    except (ValueError, IndexError):
        return None
    cand = Path(first).expanduser().resolve().parent / "role-topics.json"
    return cand if cand.is_file() else None


def topic_id_for(project: str, path: Optional[Path] = None) -> Optional[int]:
    """Тема группы pulse, чей ``route`` — ``<project>/…`` (первая по порядку файла)."""
    path = path or role_topics_path()
    if path is None:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for e in data.get("topics") or []:
        if (isinstance(e, dict) and e.get("group") == GROUP and e.get("id")
                and str(e.get("route") or "").split("/", 1)[0] == project):
            return int(e["id"])
    return None


def _run_alert(argv: List[str], env: Dict[str, str]) -> Tuple[int, str]:
    try:
        p = subprocess.run(argv, env=env, timeout=30, capture_output=True, text=True)
        return p.returncode, (p.stderr or "").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, str(exc)


def deliver(entry: Path, text: str) -> str:
    """Отправить *text* по роли сессии → «куда» (для печати). ReportError — не вышло."""
    role = role_of(entry)
    if role is None:
        raise ReportError("не лид")
    kind, project = role
    cmd = (os.environ.get(ALERT_CMD_ENV) or "").strip()
    if not cmd:
        raise ReportError("нет ${0} — отправить некуда".format(ALERT_CMD_ENV))
    argv = shlex.split(cmd)
    if kind == "manager":
        where = "личка"
    else:
        tid = topic_id_for(project)
        if tid is None:
            raise ReportError("в role-topics.json нет темы группы {0} для проекта {1}"
                              .format(GROUP, project))
        argv += ["--group", GROUP, "--topic", str(tid)]
        where = "тема проекта {0} (id {1})".format(project, tid)
    socs = sorted(set(re.findall(r"SOC-\d+", text)))
    if socs:
        argv += ["--soc", ",".join(socs)]
    argv += ["--", text]
    env = dict(os.environ, TIDE_ALERT_ROUTE="human",
               TIDE_ALERT_SOURCE="{0}/{1}".format(
                   project, slug.entry_slug(Path(entry).parents[1].name)))
    env.pop("TIDE_ALERT_URGENT", None)
    rc, err = _run_alert(argv, env)
    if rc != 0:
        raise ReportError("tg-alert rc={0}{1}".format(rc, ": " + err if err else ""))
    return where


def send_report(entry: Path, text: str) -> Optional[str]:
    """Отправить и отметить паспорт. None — не лид (тихо). Иначе «куда»."""
    if not text.strip():
        raise ReportError("пустой отчёт")
    if role_of(entry) is None:
        return None
    where = deliver(entry, text.strip())
    stamp = datetime.now().isoformat(timespec="seconds")
    fields.set_field(Path(entry) / "arc.md", REPORT_FIELD, "sent {0}".format(stamp))
    _log("sent {0}/{1}: {2}".format(Path(entry).parents[1].name, Path(entry).name, where))
    return where


def has_report(entry: Path) -> bool:
    val = (fields.read_field(Path(entry) / "arc.md", REPORT_FIELD) or "").strip()
    return val.startswith("sent")


def require_report(entry: Optional[Path]) -> None:
    """Гейт передачи: лид без отчёта в паспорте текущей сессии → ReportError."""
    if entry is None or not is_lead(entry) or has_report(entry):
        return
    if (os.environ.get(NO_REPORT_ENV) or "").strip() in ("1", "true", "yes"):
        _log("bypass {0}=1: {1}/{2}".format(NO_REPORT_ENV, Path(entry).parents[1].name,
                                            Path(entry).name))
        print("tide: ⚠ отчёт в Telegram пропущен ({0}=1), записано в журнал".format(
            NO_REPORT_ENV), file=sys.stderr)
        return
    raise ReportError(
        "лид закрывает передачу без отчёта в Telegram. Сначала:\n  {0}\n"
        "(3–8 строк по-русски: что сделано с пруфом, что в работе и у кого, что ждёт "
        "решения Гриши, SOC-N). Аварийно: {1}=1.".format(REPORT_FORMAT, NO_REPORT_ENV))


def current_entry(root: Path, ref: Optional[str] = None) -> Optional[Path]:
    """Своя сессия (по ref или sid) — или None, если не определилась."""
    from . import seed_draft

    try:
        return seed_draft.resolve_session(Path(root), ref)
    except Exception:  # noqa: BLE001
        return None


def _cmd_report(args) -> int:
    root = paths.find_tide_root()
    if root is None:
        print("tide: handoffs report: не в tide-проекте", file=sys.stderr)
        return 1
    from . import seed_draft

    try:
        entry = seed_draft.resolve_session(root, getattr(args, "session", None))
    except Exception as exc:  # noqa: BLE001
        print("tide: handoffs report: {0}".format(exc), file=sys.stderr)
        return 1
    text = " ".join(args.text) if args.text else sys.stdin.read()
    try:
        where = send_report(entry, text)
    except ReportError as exc:
        print("tide: handoffs report: {0}".format(exc), file=sys.stderr)
        return 1
    if where is None:
        return 0
    print("tide: отчёт отправлен ({0}); в паспорте {1}".format(where, REPORT_FIELD))
    return 0


def register_report(hsub) -> None:
    ap = hsub.add_parser("report", help="отчёт лида в Telegram на хендоффе (SOC-233)")
    ap.add_argument("text", nargs="*", help="текст отчёта (без аргументов — из stdin)")
    ap.add_argument("--session", default=None, help="арка-сессия (по умолчанию своя)")
    ap.set_defaults(func=_cmd_report, _cmd="handoffs report")

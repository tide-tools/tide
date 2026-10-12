"""SOC-234: карточка автономного цикла — события в ``cycles.jsonl``.

Цикл — отрезок автономной работы нити над одной задачей SOC-N с критерием «готово
когда», оценкой (% квоты) и сроком. Источник один: ``<management>/.tide/state/cycles.jsonl``
(append-only, как ``reports.jsonl`` из SOC-233); доска и control только читают и сворачивают
события функцией :func:`fold`.

Цикл стартует только с планом (comment-b7831a38): ``plan`` = {ref, steps, not_do, check}
плюс оценка и «готово когда»; без них :func:`start` отказывает (:class:`PlanMissing`).

События: ``start`` · ``extend`` · ``mode`` (запас под talk/auto) · ``end``
(``status`` = done | user | stopped). В паспорте сессии рабочего — поле ``cycle: <id>``,
оно переходит к новой сессии при ``handoff continue``. ``tide work-report --kind done|cycle``
по SOC активного цикла из сессии с этим полем закрывает цикл (:func:`close_by_report`).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import fields, paths, slug
from .arc.stream import StreamError

CYCLES_FILE_ENV = "TIDE_CYCLES_PATH"
CYCLES_NAME = "cycles.jsonl"
CYCLE_FIELD = "cycle"
END_STATUSES = ("done", "user", "stopped")
MODES = ("talk", "auto")
BY = ("user", "lead", "manager")
ACTIVE = "active"


class CycleError(StreamError):
    pass


class PlanMissing(CycleError, ValueError):
    """Цикл без плана: нет разделов или ссылка на план не годится."""


# раздел плана → флаг CLI, в порядке вывода
PLAN_FLAGS = (("plan", "--plan"), ("steps", "--steps"), ("not_do", "--not-do"), ("check", "--check"),
              ("estimate", "--estimate"), ("done_when", "--done-when"))
PLAN_HINT = "план цикла — prompts/thread-rules.md"


# --- куда писать --------------------------------------------------------------

def cycles_file() -> Path:
    env = (os.environ.get(CYCLES_FILE_ENV) or "").strip()
    if env:
        return Path(env).expanduser()
    from .launcher.handoff_report import state_dir

    return state_dir() / CYCLES_NAME


def append_event(ev: Dict[str, object], path: Optional[Path] = None) -> Path:
    """Одна строка JSON одним ``write`` в режиме append."""
    path = Path(path) if path else cycles_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return path


def read_events(path: Optional[Path] = None) -> List[Dict[str, object]]:
    path = Path(path) if path else cycles_file()
    out: List[Dict[str, object]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("ev") and r.get("id"):
                    out.append(r)
    except OSError:
        pass
    return out


# --- свёртка ------------------------------------------------------------------

def fold(events: Iterable[Dict[str, object]]) -> Dict[str, Dict[str, object]]:
    """События → ``{id: состояние}`` в порядке появления.

    Состояние: поля ``start`` (в т.ч. ``plan``; у старых карточек его нет) + ``status``
    (``active`` | done | user | stopped), ``mode`` (по умолчанию ``auto``), ``notes``
    (из extend/end), ``ended_at``, ``report``, ``updated_at``. События без своего ``start``
    пропускаются."""
    cards: Dict[str, Dict[str, object]] = {}
    for ev in events:
        cid, kind = ev.get("id"), ev.get("ev")
        if kind == "start":
            card = {k: v for k, v in ev.items() if k != "ev"}
            card.update(status=ACTIVE, mode="auto", notes=[], started_at=ev.get("at"),
                        ended_at=None, report=None, updated_at=ev.get("at"))
            cards[str(cid)] = card
            continue
        card = cards.get(str(cid))
        if card is None:
            continue
        card["updated_at"] = ev.get("at")
        if kind == "extend":
            for k in ("estimate_pct", "due"):
                if ev.get(k) is not None:
                    card[k] = ev[k]
            if ev.get("note"):
                card["notes"].append(ev["note"])
        elif kind == "mode" and ev.get("mode") in MODES:
            card["mode"] = ev["mode"]
        elif kind == "end":
            card["status"] = ev.get("status") or "stopped"
            card["ended_at"] = ev.get("at")
            card["report"] = ev.get("report")
            if ev.get("note"):
                card["notes"].append(ev["note"])
    return cards


def state(path: Optional[Path] = None) -> Dict[str, Dict[str, object]]:
    return fold(read_events(path))


def is_active(card: Dict[str, object]) -> bool:
    return card.get("status") == ACTIVE


def active_for_soc(cards: Dict[str, Dict[str, object]], soc: str) -> Optional[Dict[str, object]]:
    soc = (soc or "").upper()
    for c in cards.values():
        if is_active(c) and str(c.get("soc", "")).upper() == soc:
            return c
    return None


# --- разбор значений ----------------------------------------------------------

def _now() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)


def parse_due(text: Optional[str], now: Optional[datetime] = None) -> Optional[str]:
    """``DD.MM HH:MM`` (местное, текущий год; прошедшее — следующий) или ISO → ISO с зоной."""
    if not text or not text.strip():
        return None
    text = text.strip()
    now = now or _now()
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\s+(\d{1,2}):(\d{2})", text)
    try:
        if m:
            day, mon, year, hh, mm = m.groups()
            d = now.replace(year=int(year or now.year), month=int(mon), day=int(day),
                            hour=int(hh), minute=int(mm), second=0)
            if not year and d < now:
                d = d.replace(year=d.year + 1)
        else:
            d = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.astimezone()
    except ValueError:
        raise CycleError("не разобрал срок {0!r}: нужно «DD.MM HH:MM» или ISO".format(text))
    return d.replace(microsecond=0).isoformat()


def _pct(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(str(v).replace(",", ".").rstrip("%"))
    except ValueError:
        raise CycleError("оценка {0!r}: нужно число (% квоты)".format(v))
    if f <= 0:
        raise CycleError("оценка должна быть больше нуля")
    return f


def _plan_ref(ref: str) -> str:
    """URL Linear, ``comment-<id>`` или абсолютный путь к существующему ``.md``."""
    if re.fullmatch(r"https?://linear\.app/\S+", ref) or re.fullmatch(r"comment-[0-9a-zA-Z-]+", ref):
        return ref
    f = Path(ref).expanduser()
    if f.is_absolute() and f.suffix == ".md" and f.is_file():
        return ref
    raise PlanMissing("--plan {0!r}: нужен URL Linear, comment-<id> или абсолютный путь к .md, "
                      "который есть на диске ({1})".format(ref, PLAN_HINT))


def check_plan(**parts) -> Dict[str, str]:
    """Все разделы плана на месте → ``{ref, steps, not_do, check}``; иначе :class:`PlanMissing`."""
    missing = [flag for key, flag in PLAN_FLAGS if not str(parts.get(key) or "").strip()]
    if missing:
        raise PlanMissing("цикл без плана не стартует: нет {0} ({1})".format(", ".join(missing), PLAN_HINT))
    return {"ref": _plan_ref(str(parts["plan"]).strip()), "steps": str(parts["steps"]).strip(),
            "not_do": str(parts["not_do"]).strip(), "check": str(parts["check"]).strip()}


def _soc(v: str) -> str:
    m = re.fullmatch(r"(?:SOC-)?(\d+)", (v or "").strip(), re.I)
    if not m:
        raise CycleError("--soc: нужен SOC-N, получено {0!r}".format(v))
    return "SOC-" + m.group(1)


# --- куда лёг цикл ------------------------------------------------------------

def _current_entry(root: Path) -> Optional[Path]:
    from .launcher import handoff_report

    return handoff_report.current_entry(root)


def _project_root(name: str) -> Optional[Path]:
    from . import roster

    try:
        for e in roster.read_roster(paths.control_home()):
            if e["name"] == name:
                return Path(e["path"]).expanduser()
    except Exception:  # noqa: BLE001
        pass
    return None


def resolve_target(thread_arg: Optional[str]) -> Dict[str, object]:
    """Где живёт рабочий: ``project``, ``thread``, ``entry`` (паспортная сессия или None)."""
    from .arc import stream

    root = paths.find_tide_root()
    cur = _current_entry(root) if root else None
    if not thread_arg:
        if cur is None:
            raise CycleError("не определилась текущая сессия — укажи --thread проект/нить")
        return {"project": root.name, "thread": cur.parents[1].name, "entry": cur}
    proj, _, th = thread_arg.partition("/")
    if not th:
        proj, th = (root.name if root else ""), proj
    th_slug = slug.entry_slug(th)
    if cur is not None and root and proj == root.name and slug.entry_slug(cur.parents[1].name) == th_slug:
        return {"project": proj, "thread": cur.parents[1].name, "entry": cur}
    proot = root if (root and root.name == proj) else _project_root(proj)
    entry = None
    if proot is not None:
        try:
            entry = stream.last_session(proot, th_slug)
        except Exception:  # noqa: BLE001
            entry = None
    return {"project": proj, "thread": th, "entry": entry}


# --- операции -----------------------------------------------------------------

def start(*, soc: str, thread: Optional[str] = None, done_when: str = "", estimate=None,
          due: Optional[str] = None, by: str = "lead", plan: str = "", steps: str = "",
          not_do: str = "", check: str = "", path: Optional[Path] = None,
          now: Optional[datetime] = None) -> Dict[str, object]:
    soc = _soc(soc)
    plan_card = check_plan(plan=plan, steps=steps, not_do=not_do, check=check, estimate=estimate,
                           done_when=done_when)
    if by not in BY:
        raise CycleError("--by: {0}".format(" | ".join(BY)))
    now = (now or _now()).replace(microsecond=0)
    cards = state(path)
    busy = active_for_soc(cards, soc)
    if busy:
        raise CycleError("по {0} уже идёт цикл {1}: сначала tide cycle end {1}".format(soc, busy["id"]))
    tgt = resolve_target(thread)
    entry: Optional[Path] = tgt["entry"]  # type: ignore[assignment]
    sid = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    ev = {
        "ev": "start",
        "id": "c-{0}-{1}".format(now.strftime("%Y%m%d-%H%M"), secrets.token_hex(2)),
        "at": now.isoformat(), "soc": soc, "project": tgt["project"], "thread": tgt["thread"],
        "session": entry.name if entry is not None else "",
        "ao": (os.environ.get("AO_SESSION_ID") or "").strip() if entry is not None and
              sid and fields.read_field(entry / "arc.md", "claude-session") == sid else "",
        "by": by, "done_when": done_when.strip(), "estimate_pct": _pct(estimate),
        "due": parse_due(due, now), "plan": plan_card,
    }
    append_event(ev, path)
    if entry is not None:
        fields.set_field(Path(entry) / "arc.md", CYCLE_FIELD, str(ev["id"]))
    return ev


def _card(cid: str, path: Optional[Path]) -> Dict[str, object]:
    cards = state(path)
    if cid in cards:
        return cards[cid]
    hit = [c for k, c in cards.items() if k.endswith(cid) or k.startswith(cid)]
    if len(hit) == 1:
        return hit[0]
    raise CycleError("цикл {0!r} не найден".format(cid))


def extend(cid: str, *, estimate=None, due: Optional[str] = None, note: str = "",
           path: Optional[Path] = None) -> Dict[str, object]:
    card = _card(cid, path)
    if not is_active(card):
        raise CycleError("цикл {0} уже закрыт ({1})".format(card["id"], card["status"]))
    est, d = _pct(estimate), parse_due(due)
    if est is None and d is None and not note:
        raise CycleError("нечего менять: --estimate, --due или --note")
    ev = {"ev": "extend", "id": card["id"], "at": _now().isoformat()}
    if est is not None:
        ev["estimate_pct"] = est
    if d is not None:
        ev["due"] = d
    ev["note"] = note or ""
    append_event(ev, path)
    return ev


def end(cid: str, *, status: str, note: str = "", report: Optional[str] = None,
        path: Optional[Path] = None) -> Dict[str, object]:
    if status not in END_STATUSES:
        raise CycleError("--status: {0}".format(" | ".join(END_STATUSES)))
    card = _card(cid, path)
    if not is_active(card):
        raise CycleError("цикл {0} уже закрыт ({1})".format(card["id"], card["status"]))
    ev = {"ev": "end", "id": card["id"], "at": _now().isoformat(), "status": status,
          "report": report, "note": note or ""}
    append_event(ev, path)
    return ev


def inherited_fields(origin_passport: Path) -> Dict[str, str]:
    """Поля паспорта, что переходят к преемнику при ``continue`` (cycle, eta)."""
    out: Dict[str, str] = {}
    for key in (CYCLE_FIELD, "eta"):
        val = (fields.read_field(Path(origin_passport), key) or "").strip()
        if val:
            out[key] = val
    return out


def close_by_report(entry: Path, rec: Dict[str, object],
                    path: Optional[Path] = None) -> Optional[Dict[str, object]]:
    """Отчёт ``done``/``cycle`` по SOC активного цикла из сессии с этим циклом → ``end done``."""
    if rec.get("kind") not in ("done", "cycle"):
        return None
    cid = (fields.read_field(Path(entry) / "arc.md", CYCLE_FIELD) or "").strip()
    if not cid:
        return None
    card = state(path).get(cid)
    if card is None or not is_active(card):
        return None
    if str(card.get("soc", "")).upper() not in [s.upper() for s in (rec.get("soc") or [])]:
        return None
    return end(cid, status="done", report=str(rec.get("id")), path=path)


# --- вывод --------------------------------------------------------------------

def _fmt_due(iso) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(str(iso)).astimezone().strftime("%d.%m %H:%M")
    except ValueError:
        return str(iso)


def _fmt_pct(v) -> str:
    return "—" if v is None else "{0:g}%".format(v)


def line(c: Dict[str, object]) -> str:
    return "{0}  {1}  {2}/{3}  оценка {4}  срок {5}  {6}".format(
        c["id"], c["soc"], c["project"], slug.entry_slug(str(c["thread"])),
        _fmt_pct(c.get("estimate_pct")), _fmt_due(c.get("due")),
        "идёт" if is_active(c) else c["status"])


def _block(label: str, text) -> str:
    """``label: текст``; строки многострочного текста — с отступом."""
    return label + ": " + "\n  ".join(str(text).splitlines())


def show_text(c: Dict[str, object]) -> str:
    plan = c.get("plan") if isinstance(c.get("plan"), dict) else None
    rows = [line(c), "готово когда: " + (str(c.get("done_when") or "") or "—")]
    if plan:
        rows += [_block("план", plan.get("ref") or "—"), _block("шаги", plan.get("steps") or "—"),
                 _block("не делать", plan.get("not_do") or "—"), _block("проверка", plan.get("check") or "—")]
    else:
        rows.append("план: —")
    rows += ["поставил: {0} · сессия {1} · режим {2}".format(c.get("by"), c.get("session") or "—", c.get("mode")),
             "начат: " + _fmt_due(c.get("started_at"))]
    if c.get("ended_at"):
        rows.append("закрыт: {0}{1}".format(_fmt_due(c["ended_at"]),
                                           " · отчёт " + str(c["report"]) if c.get("report") else ""))
    rows += ["заметка: " + n for n in c.get("notes") or []]
    return "\n".join(rows)


# --- CLI ----------------------------------------------------------------------

def run_cli(args) -> int:
    cmd = args.cycle_cmd
    try:
        if cmd == "start":
            ev = start(soc=args.soc, thread=args.thread, done_when=args.done_when or "",
                       estimate=args.estimate, due=args.due, by=args.by, plan=args.plan or "",
                       steps=args.steps or "", not_do=args.not_do or "", check=args.check or "")
            print("tide: цикл {0} начат ({1}, {2}/{3}); {4}".format(
                ev["id"], ev["soc"], ev["project"], ev["thread"], cycles_file()))
        elif cmd == "show":
            cards = state()
            if args.soc:
                c = active_for_soc(cards, _soc(args.soc)) or next(
                    (x for x in reversed(list(cards.values())) if x["soc"] == _soc(args.soc)), None)
                if c is None:
                    raise CycleError("по {0} циклов нет".format(args.soc))
            else:
                c = _card(args.id, None)
            print(show_text(c))
        elif cmd == "ls":
            rows = [c for c in state().values() if args.all or is_active(c)]
            if not rows:
                print("циклов нет" if args.all else "активных циклов нет (--all — все)")
            for c in rows:
                print(line(c))
        elif cmd == "extend":
            ev = extend(args.id, estimate=args.estimate, due=args.due, note=args.note or "")
            print("tide: цикл {0} продлён".format(ev["id"]))
        elif cmd == "end":
            ev = end(args.id, status=args.status, note=args.note or "")
            print("tide: цикл {0} закрыт ({1})".format(ev["id"], ev["status"]))
    except CycleError as exc:
        print("tide: cycle: {0}".format(exc), file=sys.stderr)
        return 1
    return 0


def register(sub) -> None:
    """``tide cycle start|show|ls|extend|end``."""
    ap = sub.add_parser("cycle", help="карточка автономного цикла (SOC-234)")
    csub = ap.add_subparsers(dest="cycle_cmd", required=True)
    # разделы плана обязательны, но проверяет их start(): одна строка-отказ со всем недостающим
    p = csub.add_parser("start", help="начать цикл по задаче (только с планом)")
    p.add_argument("--soc", required=True, help="SOC-N (один активный цикл на задачу)")
    p.add_argument("--plan", default=None,
                   help="обязательно: план — URL Linear-комментария/документа, comment-<id> или абсолютный путь к .md")
    p.add_argument("--steps", default=None, help="обязательно: шаги — что · где · готово когда шага")
    p.add_argument("--not-do", dest="not_do", default=None, help="обязательно: что не делать")
    p.add_argument("--check", default=None, help="обязательно: как проверить вживую")
    p.add_argument("--done-when", default=None, help="обязательно: критерий «готово когда»")
    p.add_argument("--estimate", default=None, help="обязательно: оценка, %% общей квоты")
    p.add_argument("--due", default=None, help="срок: «DD.MM HH:MM» или ISO")
    p.add_argument("--by", default="lead", choices=BY, help="кто поставил")
    p.add_argument("--thread", default=None, help="проект/нить рабочего (по умолчанию текущая)")
    p = csub.add_parser("show", help="карточка цикла")
    p.add_argument("id", nargs="?", default=None)
    p.add_argument("--soc", default=None)
    p = csub.add_parser("ls", help="циклы (по умолчанию активные)")
    p.add_argument("--all", action="store_true")
    p = csub.add_parser("extend", help="поменять оценку/срок")
    p.add_argument("id")
    p.add_argument("--estimate", default=None)
    p.add_argument("--due", default=None)
    p.add_argument("--note", default=None)
    p = csub.add_parser("end", help="закрыть цикл")
    p.add_argument("id")
    p.add_argument("--status", required=True, choices=END_STATUSES)
    p.add_argument("--note", default=None)
    ap.set_defaults(func=run_cli, _cmd="cycle")

"""tide.mail — почта нити: письмо файлом, квитанция, повтор пинга (SOC-143).

Зачем: ``ao send`` режет ~4075 байт (отказ глотается), в занятую сессию
сообщение висит в поле ввода до 10 мин, uds теряется. Почта даёт гарантированный
канал между агентами:

* адрес — НИТЬ, не сессия: ``tide mail send <нить> "текст"`` (или ``--file``)
  кладёт письмо в ``<нить>/inbox/NNN-от-<кто>.md`` — без лимита размера;
* затем короткий пинг (< 500 байт) ``ao send`` ведущей сессии нити — её ищем
  тем же путём, что ``tide whois <нить>`` (:func:`tide.whois.thread`);
* нить с несколькими живыми сессиями (принятые, не передавшие, AO не
  terminated) — пинг всем, в пинге строка «в нити N живых сессий — письмо
  общее»; адрес можно уточнить сессией: ``<проект>/<нить>@<ao-id | techlead-13>``
  — письмо в тот же ящик с ``to-session:`` в шапке, пинг только ей, квитанция
  и повторы — от неё (SOC-143); сессия уже отдала нить и приём подтверждён —
  письмо, пинг и квитанция преемника по цепочке хендоффов (:func:`succession`,
  SOC-245), в шапке ``redirected-from:``;
* квитанция: ``tide mail read <нить> NNN`` печатает письмо и пишет ``ack``
  (кто, когда) в журнал ``inbox/log.jsonl``; ``tide mail ls <нить>`` —
  непрочитанное / прочитанное;
* повтор: отсоединённый ожидальщик ящика (flock ``inbox/watch.lock``) через
  ``$TIDE_MAIL_RETRY_MIN`` минут (по умолчанию 15) без квитанции пингует снова;
  после :data:`MAX_RETRIES` повторов — строка менеджеру
  (:func:`tide.launcher.autohandoff.send_alert`, ``route=manager``);
  умер ожидальщик (kill, перезагрузка) — любой хук почты и любой ``tide mail *``
  поднимают его заново (:func:`revive_project`), без launchd/cron;
* хуки SessionStart и UserPromptSubmit сессии нити печатают
  «почта: N непрочитанных — tide mail ls <нить>»;
* пинг пачкой (SOC-179): пинг несрочного письма копится по адресату в
  ``<control-home>/.tide/state/mail-ping-queue.json`` и уходит одним пингом
  «почта: N новых — tide mail ls <нить>» не чаще раза в ``$TIDE_MAIL_BATCH_SEC``
  (1500 с; 0 — по-старому сразу); сброс — на отправке и на тике
  ``com.tide.orphan-watch``. Сразу, как раньше: письмо от менеджера
  (``tide whois manager``), ``--urgent``, эскалация (``tide escalate``), шов-синк.
* лок очереди пингов (SOC-277): под ним — только очередь, ждать — не дольше
  ``$TIDE_MAIL_LOCK_SEC`` (10 с), иначе пинг в лоток ``mail-ping-spool/``; держат
  дольше минуты или очередь разрослась — тревога менеджеру (раз в 30 мин).

* «не слышит» (SOC-256): пинг, который ``ao send`` не доставил, не глотается —
  см. раздел ниже (журнал ``err``/``deaf``, ``mail-deaf.json``, тревога менеджеру,
  код 1 у ``tide mail send``/``flush``, ``tide mail deaf``).

Журнал — append-only JSONL, одна строка на жест: ``sent``, ``ping``, ``ack``,
``alert``. Сами письма после отправки не правятся.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import paths, slug
from .arc.stream import StreamError

INBOX_DIRNAME = "inbox"
LOG_NAME = "log.jsonl"
CLOSED_EV = "escalation-closed"   # SOC-157: явное закрытие эскалации (tide escalate --close)
RETRY_ENV = "TIDE_MAIL_RETRY_MIN"
RETRY_DEFAULT_MIN = 15.0
WATCH_ENV = "TIDE_MAIL_WATCH"
MAX_RETRIES = 2
PING_MAX_BYTES = 499
AO_SESSION_ENV = "AO_SESSION_ID"
BATCH_ENV = "TIDE_MAIL_BATCH_SEC"     # SOC-179: пинг несрочного — пачкой не чаще раза в N с
BATCH_DEFAULT_SEC = 1500
QUEUE_NAME = "mail-ping-queue.json"
SEAM_SENDER = "шов-синк"              # tide.seam_sync.SENDER — его пинги приёма срочные
ESCALATION_KIND = "escalation"        # tide.escalate.KIND

_LETTER = re.compile(r"^(\d{3,})-от-(.+)\.md$")


class MailError(StreamError):
    """Почта не легла / не нашлась."""


# --- адрес и место ---------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(raw: str) -> Optional[datetime]:
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def thread_dir(ref: str, root: Optional[Path] = None) -> Path:
    """Каталог нити *ref* (``нить`` или ``проект/нить``) — как у ``tide whois``."""
    from .arc.work import thread_dir as _thread_dir

    ref = (ref or "").strip().strip("/")
    if not ref:
        raise MailError("mail: назови нить — <нить> | <проект>/<нить>")
    root = Path(root) if root is not None else paths.find_tide_root()
    if root is None and "/" not in ref:
        raise MailError("mail: не в tide-проекте — укажи <проект>/<нить>")
    tdir = _thread_dir(Path(root or "."), ref)
    if tdir is None:
        raise MailError("mail: нет нити {0!r}".format(ref))
    return tdir


def _project_name(tdir: Path) -> str:
    """Имя проекта нити по ростеру ("" — не нашёлся): адрес в пинге — полный,
    чтобы читающий из рабочей копии попал в тот же ящик."""
    proj_root = tdir.parent.parent.parent  # <root>/.tide/arcs/<нить>
    try:
        from . import roster

        home = paths.control_home()
        for entry in roster.read_roster(home):
            if Path(entry["path"]).expanduser().resolve() == proj_root.resolve():
                return entry["name"]
    except Exception:  # noqa: BLE001 — нет ростера: адрес короткий
        pass
    return ""


def address_of(tdir: Path) -> str:
    """``проект/нить`` (или голая нить) — что писать в пинге и подсказках."""
    name = slug.entry_slug(tdir.name)
    project = _project_name(tdir)
    return "{0}/{1}".format(project, name) if project else name


def inbox(tdir: Path) -> Path:
    return Path(tdir) / INBOX_DIRNAME


# --- журнал ----------------------------------------------------------------------

def _log_path(tdir: Path) -> Path:
    return inbox(tdir) / LOG_NAME


def events(tdir: Path) -> List[Dict[str, object]]:
    f = _log_path(tdir)
    if not f.is_file():
        return []
    out: List[Dict[str, object]] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


def _append(tdir: Path, ev: Dict[str, object]) -> None:
    f = _log_path(tdir)
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")


# --- письма ----------------------------------------------------------------------

def _num(raw: object) -> str:
    try:
        return "{0:03d}".format(int(str(raw).strip()))
    except ValueError:
        raise MailError("mail: номер письма — число, не {0!r}".format(raw)) from None


def letters(tdir: Path) -> List[Dict[str, object]]:
    """Письма нити по номеру → ``{num, path, sender, sent, bytes, first, acks,
    pings, alerted}``."""
    box = inbox(tdir)
    if not box.is_dir():
        return []
    by_num: Dict[object, List[Dict[str, object]]] = {}
    for e in events(tdir):   # SOC-277: один проход по журналу, не журнал на каждое письмо
        by_num.setdefault(e.get("num"), []).append(e)
    succ: Dict[str, List[Dict[str, str]]] = {}
    out: List[Dict[str, object]] = []
    for p in sorted(box.iterdir()):
        m = _LETTER.match(p.name)
        if not m or not p.is_file():
            continue
        num = m.group(1)
        mine = by_num.get(num, [])
        sent = next((e for e in mine if e.get("ev") == "sent"), {})
        acks = [e for e in mine if e.get("ev") == "ack"]
        to_session = str(sent.get("to_session") or "")
        to_ao = str(sent.get("to_ao") or "")
        acked = bool(acks)
        if to_session:
            # SOC-143: письмо сессии — прочитано, когда квитанцию дал адресат;
            # SOC-245: или его преемник по принятому хендоффу
            acked = any(_ack_by_addressee(str(a.get("by") or ""), to_session, to_ao)
                        for a in acks)
            if not acked and acks:
                if to_session not in succ:
                    succ[to_session] = succession(tdir, to_session)
                acked = any(_ack_by_addressee(str(a.get("by") or ""), s["name"], s["ao"])
                            for s in succ[to_session] for a in acks)
        out.append({
            "num": num, "path": p, "sender": str(sent.get("from") or m.group(2)),
            "sent": str(sent.get("at") or ""), "bytes": int(sent.get("bytes") or 0),
            "first": str(sent.get("first") or ""),
            "acks": acks,
            "acked": acked,
            "to_session": to_session, "to_ao": to_ao,
            "pings": [e for e in mine if e.get("ev") == "ping"],
            "alerted": any(e.get("ev") == "alert" for e in mine),
            # SOC-157: своё окно до тревоги менеджеру (эскалация) — None у обычного письма
            "alert_after": _alert_after(sent.get("alert_after")),
            "kind": str(sent.get("kind") or ""),
            "urgent": bool(sent.get("urgent")),   # SOC-179: --urgent — пинг сразу, не в пачку
            # SOC-157: эскалацию закрыли явно (``tide escalate --close``) — None, если нет
            "closed": next((e for e in mine if e.get("ev") == CLOSED_EV), None),
        })
    return out


def _alert_after(raw: object) -> Optional[float]:
    try:
        val = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


def letter(tdir: Path, num: object) -> Dict[str, object]:
    want = _num(num)
    rec = next((r for r in letters(tdir) if r["num"] == want), None)
    if rec is None:
        raise MailError("mail: в нити {0} нет письма {1}".format(
            slug.entry_slug(Path(tdir).name), want))
    return rec


def unread(tdir: Path) -> List[Dict[str, object]]:
    return [r for r in letters(tdir) if not r["acked"]]


def _ack_by_addressee(by: str, to_session: str, to_ao: str) -> bool:
    """Квитанция *by* — от сессии-адресата (её AO-id, uds-имя «ao-id-xx», имя
    арки «13-techlead-13» / «techlead-13» или «нить·13-techlead-13»)."""
    by = (by or "").strip()
    if not by:
        return False
    if to_ao and (by == to_ao or by.startswith(to_ao + "-")):
        return True
    if to_session:
        names = {to_session, slug.entry_slug(to_session)}
        return by in names or any(by.endswith("·" + n) for n in names)
    return False


def _first_line(text: str) -> str:
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")


def _who_am_i() -> str:
    """Кто пишет/читает по умолчанию: AO-сессия, иначе нить текущей claude-сессии,
    иначе «человек»."""
    ao = (os.environ.get(AO_SESSION_ENV) or "").strip()
    if ao:
        return ao
    sid = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    root = paths.find_tide_root()
    if sid and root is not None:
        try:
            from . import offload

            entry = offload.find_session_by_claude_id(root, sid)
            if entry is not None:
                return "{0}·{1}".format(slug.entry_slug(entry.parents[1].name), entry.name)
        except Exception:  # noqa: BLE001
            pass
    return "человек"


def _write_new(box: Path, sender: str, content: str) -> Tuple[str, Path]:
    """Следующий свободный номер, файл создаётся атомарно (O_EXCL)."""
    box.mkdir(parents=True, exist_ok=True)
    who = (slug.slugify(sender) or "kto")[:40]
    for _ in range(1000):
        nums = [int(m.group(1)) for m in (_LETTER.match(p.name) for p in box.iterdir()) if m]
        num = "{0:03d}".format(max(nums, default=0) + 1)
        path = box / "{0}-от-{1}.md".format(num, who)
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        return num, path
    raise MailError("mail: не нашёл свободный номер в {0}".format(box))


def ping_text(num: str, sender: str, first: str, addr: str, note: str = "") -> str:
    """Пинг < 500 байт: «почта NNN от X: <первая строка> — tide mail read <нить> NNN»;
    *note* — ещё одна строка (SOC-143: «в нити N живых сессий — письмо общее»)."""
    tail = " — tide mail read {0} {1}".format(addr, num)
    if note:
        tail += "\n" + note
    head = "почта {0} от {1}: ".format(num, sender)
    room = PING_MAX_BYTES - len((head + tail).encode("utf-8"))
    line = first
    if len(line.encode("utf-8")) > max(room, 0):
        b = line.encode("utf-8")[:max(room - 3, 0)]
        line = b.decode("utf-8", errors="ignore").rstrip() + "…"
    return head + line + tail


def _holder_ao(tdir: Path) -> str:
    """AO-id ведущей сессии нити — тем же путём, что ``tide whois <нить>``."""
    from . import whois

    proj_root = tdir.parent.parent.parent
    try:
        return str(whois.thread(tdir.name, root=proj_root).get("ao_session") or "")
    except Exception:  # noqa: BLE001 — адреса нет: письмо лежит, пинг пропущен
        return ""


# --- сессии нити (SOC-143: несколько живых сессий, адрес @сессия) ----------------

LIVE_NOTE = "в нити {0} живых сессий — письмо общее"


def _home() -> Optional[Path]:
    try:
        return paths.control_home()
    except Exception:  # noqa: BLE001 — нет control-home: AO-id только из названий
        return None


def _ao_terminated() -> set:
    """AO-сессии, которые AO пометил terminated (чтение). Ошибка — пусто."""
    try:
        from .adapters import ao

        return set(ao.terminated_sessions())
    except Exception:  # noqa: BLE001 — AO не ответил: считаем всех живыми
        return set()


def _ao_of(home: Optional[Path], passport: Path, sid: str) -> str:
    """AO-id сессии: по реестру claude-id, иначе хвост её названия («… · proj-36»)."""
    from . import fields
    from .arc import holder

    ao = holder.ao_handle_for(home, sid) if sid else ""
    if not ao:
        title = (fields.read_field(passport, "title") or "").strip()
        m = re.search(r" · (\S+-\d+)$", title)
        ao = m.group(1) if m else ""
    return ao


def thread_sessions(tdir: Path) -> List[Dict[str, object]]:
    """Открытые, не отпущенные рукой сессии нити →
    ``{entry, name, sid, ao, handed}`` (``handed`` — уже передала/закончила)."""
    from . import fields

    sub = Path(tdir) / paths.ARCS_DIRNAME
    if not sub.is_dir():
        return []
    home = _home()
    out: List[Dict[str, object]] = []
    for entry in sorted(sub.iterdir()):
        pp = entry / "arc.md"
        if not (entry.is_dir() and slug.is_entry(entry.name) and pp.is_file()):
            continue
        if slug.is_closed_entry(entry.name) or (fields.read_field(pp, "dismissed") or "").strip():
            continue
        sid = (fields.read_field(pp, "claude-session") or "").strip()
        ao = _ao_of(home, pp, sid)
        handed = any((fields.read_field(pp, f) or "").strip()
                     for f in ("handed-off", "ended"))
        if not handed and sid and home is not None:
            try:
                from . import handoff_queue as hq

                handed = hq.is_dissolved(home, sid) is not None
            except Exception:  # noqa: BLE001
                pass
        out.append({"entry": entry, "name": entry.name, "sid": sid, "ao": ao,
                    "handed": handed})
    return out


def live_sessions(tdir: Path) -> List[Dict[str, object]]:
    """Живые сессии нити: приняты (есть claude-session и AO-id), не передали и
    не закончили, AO не пометил их terminated. AO спрашиваем, только когда
    кандидатов больше одного — одна нить, одна сессия: как раньше, без AO."""
    rows = [r for r in thread_sessions(tdir) if r["sid"] and r["ao"] and not r["handed"]]
    if len(rows) < 2:
        return rows
    dead = _ao_terminated()
    return [r for r in rows if r["ao"] not in dead]


def resolve_session(tdir: Path, want: str) -> Dict[str, object]:
    """Сессия нити по AO-id (или uds-имени «ao-id-xx») или имени арки
    («13-techlead-13» / «techlead-13»). Нет — :class:`MailError`."""
    want = (want or "").strip()
    rows = thread_sessions(tdir)
    for r in rows:
        ao = str(r["ao"] or "")
        if ao and (want == ao or want.startswith(ao + "-")):
            return r
    for r in rows:
        if want == r["name"] or slug.entry_slug(str(r["name"])) == slug.slugify(want):
            return r
    raise MailError("mail: в нити {0} нет открытой сессии {1!r}".format(
        slug.entry_slug(Path(tdir).name), want))


# --- преемник отдавшей сессии (SOC-245) ------------------------------------------
#
# После ПРИНЯТОГО хендоффа (оффер ``continue`` взят — :func:`tide.handoff_queue.is_dissolved`)
# нить ведёт преемник. Письмо старой по ``@сессия`` уходит ему, повтор пингует его,
# его квитанция — квитанция адресата: иначе пинги бьют в мёртвую AO-сессию, письмо
# висит «НЕ ПРОЧИТАНО адресатом» и тревожит менеджера (живой случай 12.10, письма
# 126 и 135 нити techlead). Оффер повешен, но не принят — старая ещё голова, ей.

SUCCESSION_MAX = 50


def succession(tdir: Path, name: str) -> List[Dict[str, str]]:
    """Преемники сессии *name* (имя арки) нити по цепочке принятых хендоффов — от
    ближнего к нынешнему: ``[{name, ao}]``. [] — не передавала или приём не
    подтверждён. Цепочка только внутри нити: преемник в другой нити её обрывает."""
    from . import fields
    from . import handoff_queue as hq

    home = _home()
    if home is None or not name:
        return []
    sub = (Path(tdir) / paths.ARCS_DIRNAME).resolve()
    sid = (fields.read_field(sub / name / "arc.md", "claude-session") or "").strip()
    out: List[Dict[str, str]] = []
    seen = set()
    while sid and sid not in seen and len(out) < SUCCESSION_MAX:
        seen.add(sid)
        try:
            rec = hq.is_dissolved(home, sid)
        except Exception:  # noqa: BLE001 — очередь не читается: адресат как есть
            rec = None
        seed = str((rec or {}).get("seed") or "-")
        new_sid = str((rec or {}).get("taken_by") or "").strip()
        if rec is None or seed == "-" or not new_sid or new_sid == "-":
            break
        nxt = Path(seed).parent.parent
        if nxt.parent.resolve() != sub or not (nxt / "arc.md").is_file():
            break
        out.append({"name": nxt.name, "ao": _ao_of(home, nxt / "arc.md", new_sid)})
        sid = new_sid
    return out


def target(ref: str, root: Optional[Path] = None) -> Tuple[Path, str]:
    """``<нить>`` | ``<проект>/<нить>`` [``@<сессия>``] → (каталог нити, сессия или "").

    Имя нити само может нести «@» (``02-@techlead``): сначала адрес целиком, и
    только если такой нити нет — уточнение после последней «@»."""
    try:
        return thread_dir(ref, root), ""
    except MailError:
        head, at, want = (ref or "").strip().rpartition("@")
        if not (at and head and want.strip()) or head.endswith("/"):
            raise
        return thread_dir(head, root), want.strip()


def ping(tdir: Path, num: str, *, kind: str = "ping") -> Dict[str, object]:
    """Пинг о письме *num*; жест пишется в журнал. Не падает.

    Кому (SOC-143): письмо сессии (``to-session``) — только ей; иначе живым
    сессиям нити — всем, когда их несколько (в пинге строка «письмо общее»);
    одна живая или ни одной — ведущей сессии нити, как раньше."""
    from .launcher.autohandoff import _ao_send

    rec = letter(tdir, num)
    note = ""
    moved = ""
    if rec.get("to_session"):
        to_ao = str(rec.get("to_ao") or "")
        chain = succession(tdir, str(rec["to_session"]))
        if chain:
            # SOC-245: адресат отдал нить — пинг преемнику, не мёртвой старой
            moved = str(rec["to_session"])
            to_ao = chain[-1]["ao"] or _holder_ao(tdir)
        elif not to_ao:
            try:
                to_ao = str(resolve_session(tdir, str(rec["to_session"]))["ao"] or "")
            except MailError:
                to_ao = ""
        targets = [to_ao] if to_ao else []
    else:
        try:
            live = live_sessions(tdir)
        except Exception:  # noqa: BLE001 — не прочитали сессии: как раньше
            live = []
        if len(live) > 1:
            targets = list(dict.fromkeys(str(r["ao"]) for r in live))
            note = LIVE_NOTE.format(len(targets))
        else:
            holder_ao = _holder_ao(tdir)
            targets = [holder_ao] if holder_ao else []
    addr = address_of(tdir)
    text = ping_text(rec["num"], rec["sender"], rec["first"], addr, note=note)
    home = _batch_home() if targets else None
    if home is not None and not is_urgent(rec):
        return _queue_ping(home, tdir, rec, targets, addr, kind, text)
    codes: List[Optional[int]] = []
    _mark_pinged(targets)
    errs: List[str] = []
    for to in targets:
        try:
            codes.append(_ao_send(to, text))
        except Exception:  # noqa: BLE001
            codes.append(-1)
        errs.append(_send_err(to) if codes[-1] else "")
    code: Optional[int] = None
    if codes:
        code = next((c for c in codes if c), codes[0])
    err = next((e for c, e in zip(codes, errs) if c), "")
    failed = False
    for to, c, e in zip(targets, codes, errs):
        failed = _note_delivery(to, c, e, tdir, str(rec["num"])) or failed
    ev: Dict[str, object] = {"ev": "ping", "num": rec["num"], "at": _iso(_now()),
                             "to": ", ".join(targets) or None, "code": code,
                             "kind": kind, "bytes": len(text.encode("utf-8"))}
    if len(targets) > 1:
        ev["to_all"] = targets
        ev["codes"] = codes
    if failed:
        ev["err"] = err or "код {0}".format(code)
        ev["deaf"] = True
    if moved:
        ev["redirected_from"] = moved
    _append(tdir, ev)
    return ev


# --- «не слышит» (SOC-256) -------------------------------------------------------
#
# ``ao send`` не доставил пинг (SESSION_MODE_MISMATCH и любой другой отказ): письмо
# лежит файлом, но голова адресата о нём не знает. Тихо это не оставляем:
#   * пинг в журнале письма с ``code`` ≠ 0, ``err`` (слова ao) и ``deaf: true``;
#   * адресат (AO-id) попадает в ``<control-home>/.tide/state/mail-deaf.json`` —
#     оттуда доска читает «роль не слышит»; пропадает при первом удачном пинге
#     или квитанции этой сессии (``tide mail read``);
#   * первый отказ адресату — тревога менеджеру сразу (route=manager, urgent);
#   * ``tide mail send`` / ``tide mail flush`` выходят с кодом 1.

DEAF_NAME = "mail-deaf.json"
MODE_MISMATCH = "SESSION_MODE_MISMATCH"


def _deaf_path(home: Path) -> Path:
    return paths.state_dir(Path(home)) / DEAF_NAME


def _deaf_home() -> Optional[Path]:
    try:
        home = paths.control_home()
    except Exception:  # noqa: BLE001 — нет control-home: флаг некуда писать
        return None
    return home if paths.state_dir(home).parent.is_dir() else None


def deaf_sessions(home: Optional[Path] = None) -> Dict[str, Dict[str, object]]:
    """Кто сейчас «не слышит»: ``{ao-id: {since, at, code, err, mode_mismatch, n,
    letters:[{addr, num}]}}``. Читает и доска."""
    home = home or _deaf_home()
    if home is None:
        return {}
    try:
        data = json.loads(_deaf_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = data.get("deaf") if isinstance(data, dict) else None
    return rows if isinstance(rows, dict) else {}


def _save_deaf(home: Path, rows: Dict[str, Dict[str, object]]) -> None:
    f = _deaf_path(home)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"deaf": rows, "updated": _iso(_now())},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(f))


def deaf_clear(to: str) -> bool:
    """Снять флаг с AO-id *to* (пинг дошёл / сессия прочла письмо). → был ли флаг."""
    home = _deaf_home()
    if home is None or not to:
        return False
    try:
        with _QueueLock(home):
            rows = deaf_sessions(home)
            if to not in rows:
                return False
            del rows[to]
            _save_deaf(home, rows)
            return True
    except Exception:  # noqa: BLE001 — витрина не смеет сломать почту
        return False


def _note_delivery(to: str, code: Optional[int], err: str, tdir: Path, num: str) -> bool:
    """Итог пинга в *to* → состояние «не слышит». → True, если отказ (code ≠ 0).
    Первый отказ адресату — тревога менеджеру сразу. Не падает."""
    if code is None:
        return False
    if code == 0:
        deaf_clear(to)
        return False
    home = _deaf_home()
    if home is None:
        return True
    mismatch = MODE_MISMATCH in (err or "")
    addr = address_of(tdir)
    first = False
    try:
        with _QueueLock(home):
            rows = deaf_sessions(home)
            cur = rows.get(to) if isinstance(rows.get(to), dict) else None
            first = cur is None
            cur = cur or {"since": _iso(_now()), "n": 0, "letters": []}
            cur.update(at=_iso(_now()), code=code, err=(err or "")[-300:],
                       mode_mismatch=mismatch or bool(cur.get("mode_mismatch")))
            cur["n"] = int(cur.get("n") or 0) + 1
            if not any(l.get("addr") == addr and l.get("num") == num
                       for l in cur["letters"] if isinstance(l, dict)):
                cur["letters"] = (cur["letters"] + [{"addr": addr, "num": num}])[-20:]
            rows[to] = cur
            _save_deaf(home, rows)
    except Exception:  # noqa: BLE001
        return True
    if first:
        try:
            from .launcher.autohandoff import send_alert

            why = ("терминальная сессия: ao send письмо не принимает ({0})".format(MODE_MISMATCH)
                   if mismatch else "ao send вернул код {0}: {1}".format(code, (err or "—")[-120:]))
            text = ("tide: пинг почты {0} в {1} не дошёл — {2}. Окно {1} не слышит почту; "
                    "письмо лежит непрочитанным: tide mail read {3} {0}. "
                    "Состояние: {4}").format(num, to, why, addr, _deaf_path(home))
            ac = send_alert(text, route="manager", source=addr, urgent=True)
            _append(tdir, {"ev": "alert", "num": num, "at": _iso(_now()),
                           "route": "manager", "code": ac, "why": "deaf", "to": to})
        except Exception:  # noqa: BLE001
            pass
    return True


def _send_err(to: str) -> str:
    try:
        from .launcher.autohandoff import last_send_error

        return last_send_error(to)
    except Exception:  # noqa: BLE001
        return ""


# --- пинг пачкой (SOC-179, токеномика правило 3) ----------------------------------
#
# Каждый пинг будит голову адресата. Несрочный пинг не шлётся сразу: он ложится в
# очередь адресата (AO-id) в ``<control-home>/.tide/state/mail-ping-queue.json``, а
# :func:`flush_pings` отдаёт её одним пингом, если адресату не пинговали
# :func:`batch_gap_sec` с. Сброс — сразу после постановки (адресат давно не
# слышал почту — пинг уходит, как раньше) и на тике ``com.tide.orphan-watch``.
# Повтор ожидальщика несрочного письма — тоже в очередь (дубль не множится).
# Отложенное голова увидит и сама: хук UserPromptSubmit печатает «почта: N
# непрочитанных». Нет control-home или ``$TIDE_MAIL_BATCH_SEC=0`` — сразу.

_MANAGER_AO: List[str] = []   # кэш на процесс: AO-id менеджера ("" — не нашёлся)


def batch_gap_sec() -> int:
    """Окно пачки, с; 0 — пачки нет (пинг сразу)."""
    raw = (os.environ.get(BATCH_ENV) or "").strip().lower()
    if raw in ("0", "off", "no", "false", "нет"):
        return 0
    try:
        val = int(float(raw)) if raw else BATCH_DEFAULT_SEC
    except ValueError:
        val = BATCH_DEFAULT_SEC
    return max(val, 0)


def _manager_ao() -> str:
    if not _MANAGER_AO:
        try:
            from . import whois

            _MANAGER_AO.append(str(whois.manager().get("ao_session") or "").strip())
        except Exception:  # noqa: BLE001 — менеджер не нашёлся: никто не «от менеджера»
            _MANAGER_AO.append("")
    return _MANAGER_AO[0]


def from_manager(sender: str) -> bool:
    """Отправитель — менеджер (``tide whois manager``): его AO-id, uds-имя
    «ao-id-xx» или AO-id в скобках подписи («… (management-28)»)."""
    ao = _manager_ao()
    s = (sender or "").strip()
    if not (ao and s):
        return False
    return s == ao or s.startswith(ao + "-") or "({0})".format(ao) in s


def is_urgent(rec: Dict[str, object]) -> bool:
    """Пинг письма — сразу: ``--urgent``, эскалация, шов-синк, письмо от менеджера."""
    return (bool(rec.get("urgent")) or rec.get("kind") == ESCALATION_KIND
            or str(rec.get("first") or "").startswith("эскалация:")
            or str(rec.get("sender") or "") == SEAM_SENDER
            or from_manager(str(rec.get("sender") or "")))


def _batch_home() -> Optional[Path]:
    if not batch_gap_sec():
        return None
    try:
        home = paths.control_home()
    except Exception:  # noqa: BLE001 — нет control-home: пачки нет, пинг сразу
        return None
    return home if paths.state_dir(home).parent.is_dir() else None


def _queue_path(home: Path) -> Path:
    return paths.state_dir(Path(home)) / QUEUE_NAME


# --- лок очереди (SOC-277: короткий, с тайм-аутом, с тревогой) ---------------------
#
# Живой случай 12.10: держатель лока под ним перечитывал журнал ящика на каждую
# строку очереди (журнал management/main ~455 КБ, 51 строка → ~22 с на проход), ~65
# процессов ``tide mail send`` / ``mail watch`` / ``handoff sync`` стояли за ним
# конвоем до ~50 мин. Теперь под локом — только чтение-правка-запись очереди;
# журналы — вне лока, один раз на ящик за проход. Ждать лок — не дольше
# ``$TIDE_MAIL_LOCK_SEC`` (10 с): не дождался — письмо уже лежит в ящике, пинг
# кладётся файлом в ``mail-ping-spool/`` (без лока) и уходит со следующим сбросом;
# процесс выходит, не висит. Лок держат дольше ``$TIDE_MAIL_LOCK_ALERT_SEC`` (60 с)
# или очередь выросла за ``$TIDE_MAIL_QUEUE_ALERT_ROWS`` строк — тревога менеджеру
# (:func:`tide.launcher.autohandoff.send_alert`, ``route=manager``), не чаще раза в
# ``$TIDE_MAIL_LOCK_ALERT_MIN`` минут (30).

LOCK_ENV = "TIDE_MAIL_LOCK_SEC"
LOCK_DEFAULT_SEC = 10.0
LOCK_ALERT_ENV = "TIDE_MAIL_LOCK_ALERT_SEC"
LOCK_ALERT_DEFAULT_SEC = 60.0
QUEUE_ALERT_ENV = "TIDE_MAIL_QUEUE_ALERT_ROWS"
QUEUE_ALERT_DEFAULT_ROWS = 300
ALERT_GAP_ENV = "TIDE_MAIL_LOCK_ALERT_MIN"
ALERT_GAP_DEFAULT_MIN = 30.0
SPOOL_NAME = "mail-ping-spool"
QUEUE_ALERT_NAME = "mail-queue-alert.json"
MARK_LOCK_SEC = 2.0     # витрина окна прямого пинга — ждать её дольше не стоит


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    try:
        val = float(raw) if raw else default
    except ValueError:
        val = default
    return val if val >= 0 else default


def lock_timeout_sec() -> float:
    return _env_float(LOCK_ENV, LOCK_DEFAULT_SEC)


class QueueBusy(MailError):
    """Лок очереди пингов не дался за тайм-аут — держит другой процесс."""

    def __init__(self, msg: str, holder: Optional[Dict[str, object]] = None,
                 held_sec: Optional[float] = None):
        super().__init__(msg)
        self.holder = holder or {}
        self.held_sec = held_sec


def _holder_path(home: Path) -> Path:
    return _queue_path(home).with_suffix(".json.holder")


def _read_holder(home: Path) -> Tuple[Dict[str, object], Optional[float]]:
    """Кто держит лок (pid, cmd, с какого момента) и сколько секунд — по записке держателя."""
    try:
        rec = json.loads(_holder_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, None
    if not isinstance(rec, dict):
        return {}, None
    try:
        held = max(0.0, time.time() - float(rec.get("since") or 0))
    except (TypeError, ValueError):
        held = None
    return rec, held


class _QueueLock:
    """flock на ``mail-ping-queue.json.lock`` — короткий, только чтение-правка-запись.

    *timeout* (с, None — :func:`lock_timeout_sec`): не дался — :class:`QueueBusy`.
    Держатель оставляет записку ``.json.holder`` (pid, cmd, since) — по ней ждущий
    видит, сколько лок держат, и тревожит менеджера (:func:`_alarm_busy`)."""

    def __init__(self, home: Path, timeout: Optional[float] = None):
        self.home = Path(home)
        self.path = _queue_path(home).with_suffix(".json.lock")
        self.timeout = lock_timeout_sec() if timeout is None else max(0.0, float(timeout))
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(str(self.path), "a+")
        try:
            import fcntl
        except ImportError:  # pragma: no cover — не POSIX
            return self
        deadline = time.monotonic() + self.timeout
        pause = 0.01
        while True:
            try:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self.fh.close()
                    self.fh = None
                    holder, held = _read_holder(self.home)
                    raise QueueBusy("mail: очередь пингов занята {0:g} с (держит pid {1})"
                                    .format(self.timeout, holder.get("pid") or "?"),
                                    holder, held) from None
                time.sleep(pause)
                pause = min(pause * 2, 0.2)
            except OSError:  # pragma: no cover — ФС без flock: как раньше, без лока
                break
        try:
            _holder_path(self.home).write_text(json.dumps(
                {"pid": os.getpid(), "since": time.time(), "at": _iso(_now()),
                 "cmd": " ".join(sys.argv[1:4])[:120]}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return self

    def __exit__(self, *exc):
        if self.fh is not None:
            try:
                _holder_path(self.home).unlink()
            except OSError:
                pass
            self.fh.close()
            self.fh = None


def _load_queue(home: Path) -> Dict[str, Dict[str, object]]:
    try:
        data = json.loads(_queue_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    pend = data.get("pending") if isinstance(data.get("pending"), dict) else {}
    last = data.get("last") if isinstance(data.get("last"), dict) else {}
    return {"pending": pend, "last": last}


def _save_queue(home: Path, data: Dict[str, Dict[str, object]]) -> None:
    f = _queue_path(home)
    tmp = f.with_suffix(".json.tmp.{0}".format(os.getpid()))
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(f))


def _queue_rows(data: Dict[str, Dict[str, object]]) -> int:
    return sum(len(v) for v in data["pending"].values() if isinstance(v, list))


# --- запасной лоток: пинг без лока (SOC-277) ---------------------------------------

def _spool_dir(home: Path) -> Path:
    return paths.state_dir(Path(home)) / SPOOL_NAME


def _spool_put(home: Path, targets: List[str], row: Dict[str, object]) -> Path:
    """Пинг, который не дождался лока, — отдельным файлом (tmp + rename, атомарно);
    следующий сброс (:func:`_drain_spool`, под локом) переложит его в очередь."""
    d = _spool_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    name = "{0}-{1}-{2}".format(int(time.time() * 1000), os.getpid(),
                                hashlib.sha256(os.urandom(8)).hexdigest()[:8])
    tmp = d / (".tmp-" + name)
    tmp.write_text(json.dumps({"targets": list(targets), "row": row}, ensure_ascii=False),
                   encoding="utf-8")
    dst = d / (name + ".json")
    os.replace(str(tmp), str(dst))
    return dst


def _spool_files(home: Path) -> List[Path]:
    d = _spool_dir(home)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix == ".json" and not p.name.startswith("."))


def _enqueue(data: Dict[str, Dict[str, object]], targets: List[str],
             row: Dict[str, object]) -> None:
    for to in targets:
        cur = data["pending"].setdefault(to, [])
        if not isinstance(cur, list):
            cur = data["pending"][to] = []
        if any(e.get("tdir") == row.get("tdir") and e.get("num") == row.get("num")
               for e in cur if isinstance(e, dict)):
            continue   # повтор того же письма: пинг уже ждёт в пачке
        cur.append(dict(row))


def _drain_spool(home: Path, data: Dict[str, Dict[str, object]]) -> int:
    """Под локом: пинги из лотка — в очередь, файлы лотка — прочь. → сколько файлов."""
    n = 0
    for f in _spool_files(home):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(rec, dict) and isinstance(rec.get("row"), dict):
                _enqueue(data, [str(t) for t in rec.get("targets") or []], rec["row"])
        except (OSError, ValueError):
            pass
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n


# --- тревога «почта встала» (SOC-277) -----------------------------------------------

def _alarm_due(home: Path, now: float) -> bool:
    """Не чаще раза в :data:`ALERT_GAP_ENV` минут; отметка — до отправки (без гонки
    двух ждущих: файл пишет тот, кто взял неблокирующий flock на отметку)."""
    gap = _env_float(ALERT_GAP_ENV, ALERT_GAP_DEFAULT_MIN) * 60
    f = paths.state_dir(Path(home)) / QUEUE_ALERT_NAME
    try:
        import fcntl

        fh = open(str(f.with_suffix(".json.lock")), "a+")
    except (ImportError, OSError):
        return False
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False   # другой ждущий уже тревожит
        try:
            last = float(json.loads(f.read_text(encoding="utf-8")).get("at_ts") or 0)
        except (OSError, ValueError, AttributeError, TypeError):
            last = 0.0
        if now - last < gap:
            return False
        f.write_text(json.dumps({"at_ts": now, "at": _iso(_now()), "pid": os.getpid()}),
                     encoding="utf-8")
        return True
    finally:
        fh.close()


def _alarm(home: Path, why: str) -> Optional[int]:
    """Строка менеджеру «почта tide встала» (срочно — пульт будит сразу). Не падает."""
    try:
        if not _alarm_due(home, time.time()):
            return None
        from .launcher.autohandoff import send_alert

        text = ("tide: почта встала — {0}. Письма лежат в ящиках, пинги ждут в "
                "{1} (лоток {2}). Проверь: ps -ef | grep 'tide mail'; tide mail flush"
                ).format(why, _queue_path(home), _spool_dir(home))
        return send_alert(text, route="manager", source="tide mail", urgent=True)
    except Exception:  # noqa: BLE001 — тревога не смеет сломать почту
        return None


def _alarm_busy(home: Path, exc: QueueBusy) -> Optional[int]:
    """Лок не дался: держат дольше :data:`LOCK_ALERT_ENV` с — тревога."""
    limit = _env_float(LOCK_ALERT_ENV, LOCK_ALERT_DEFAULT_SEC)
    if exc.held_sec is None or exc.held_sec < limit:
        return None
    h = exc.holder
    return _alarm(home, "лок очереди пингов держит pid {0} ({1}) уже {2:.0f} с".format(
        h.get("pid") or "?", h.get("cmd") or "?", exc.held_sec))


def _alarm_rows(home: Path, rows: int) -> Optional[int]:
    limit = int(_env_float(QUEUE_ALERT_ENV, QUEUE_ALERT_DEFAULT_ROWS))
    if limit <= 0 or rows < limit:
        return None
    return _alarm(home, "в очереди пингов {0} строк (порог {1})".format(rows, limit))


def _mark_pinged(targets: List[str], when: Optional[datetime] = None) -> None:
    """Прямой (срочный) пинг тоже будит голову — окно пачки адресата от него."""
    home = _batch_home() if targets else None
    if home is None:
        return
    try:
        with _QueueLock(home, timeout=MARK_LOCK_SEC):
            data = _load_queue(home)
            for to in targets:
                data["last"][to] = _iso(when or _now())
            _save_queue(home, data)
    except QueueBusy as exc:
        _alarm_busy(home, exc)
    except Exception:  # noqa: BLE001 — витрина окна не смеет сломать пинг
        pass


def _queue_ping(home: Path, tdir: Path, rec: Dict[str, object], targets: List[str],
                addr: str, kind: str, text: str) -> Dict[str, object]:
    """Поставить пинг письма в очереди адресатов; жест ``ping`` с ``queued`` — в
    журнал (часы повтора идут от постановки). Затем — сброс созревших адресатов.
    Лок не дался (SOC-277) — пинг в лоток, сброса нет (его сделает держатель/тик)."""
    at = _iso(_now())
    row = {"tdir": str(tdir), "addr": addr, "num": rec["num"], "sender": rec["sender"],
           "first": rec["first"], "at": at, "text": text}
    busy: Optional[QueueBusy] = None
    try:
        with _QueueLock(home):
            data = _load_queue(home)
            _enqueue(data, targets, row)
            _save_queue(home, data)
    except QueueBusy as exc:
        busy = exc
        _spool_put(home, targets, row)
    ev: Dict[str, object] = {"ev": "ping", "num": rec["num"], "at": at,
                             "to": ", ".join(targets), "code": None, "kind": kind,
                             "queued": True, "bytes": len(text.encode("utf-8"))}
    if len(targets) > 1:
        ev["to_all"] = targets
    if busy is not None:
        ev["spooled"] = True
    _append(tdir, ev)
    if busy is not None:
        _alarm_busy(home, busy)
        return ev
    try:
        flushed = flush_pings(home, only=targets, lock_timeout=MARK_LOCK_SEC)
    except Exception:  # noqa: BLE001 — сброс не смеет сломать отправку; добьёт тик
        flushed = []
    sent = [f for f in flushed if f.get("to") in targets and f.get("sent")]
    if sent:
        ev = dict(ev, queued=False, code=sent[0].get("code"), batch=True)
        if sent[0].get("deaf"):
            ev.update(err=sent[0].get("err"), deaf=True)
    return ev


def batch_text(rows: List[Dict[str, object]]) -> str:
    """Один пинг на пачку: одно письмо — его обычный пинг; несколько —
    «почта: N новых — tide mail ls <нить>[; tide mail ls <нить2>]» (< 500 байт)."""
    if len(rows) == 1 and rows[0].get("text"):
        return str(rows[0]["text"])
    addrs = list(dict.fromkeys(str(r.get("addr") or "") for r in rows))
    text = "почта: {0} новых — {1}".format(
        len(rows), "; ".join("tide mail ls {0}".format(a) for a in addrs if a))
    b = text.encode("utf-8")
    if len(b) > PING_MAX_BYTES:
        text = b[:PING_MAX_BYTES - 3].decode("utf-8", errors="ignore").rstrip() + "…"
    return text


def _acked_map(rows: List[Dict[str, object]]) -> Dict[Tuple[str, str], bool]:
    """(tdir, num) → прочитано ли; журнал каждого ящика — один раз (SOC-277).
    Нет письма / нити — в карту не попадает (строка уходит без пинга)."""
    by_box: Dict[str, Dict[str, bool]] = {}
    out: Dict[Tuple[str, str], bool] = {}
    for r in rows:
        t, n = str(r.get("tdir") or ""), str(r.get("num") or "")
        if t not in by_box:
            try:
                by_box[t] = {str(x["num"]): bool(x["acked"]) for x in letters(Path(t))}
            except Exception:  # noqa: BLE001 — ящик пропал: пинговать нечего
                by_box[t] = {}
        try:
            key = _num(n)
        except MailError:
            continue
        if key in by_box[t]:
            out[(t, n)] = by_box[t][key]
    return out


def flush_pings(home: Optional[Path] = None, *, only: Optional[List[str]] = None,
                now: Optional[datetime] = None, force: bool = False,
                lock_timeout: Optional[float] = None) -> List[Dict[str, object]]:
    """Отдать созревшие пачки: адресату не пинговали :func:`batch_gap_sec` с →
    один пинг на все его непрочитанные письма из очереди (прочитанные — прочь без
    пинга). → ``[{to, n, sent, code | why}]`` по каждому адресату с очередью.

    SOC-277: под локом — только очередь. Проход: (1) под локом снимок строк (и
    лоток — в очередь); (2) без лока — прочитано ли, журнал ящика один раз;
    (3) под локом заново: прочь прочитанное, созревших — в отправку (строки,
    пришедшие между (1) и (3), остаются как непрочитанные); (4) без лока — ``ao
    send``, жест ``ping`` с ``kind: batch`` в журнал каждого письма. Лок не дался
    за *lock_timeout* — ``[{to: "*", sent: False, why: "очередь занята …"}]``."""
    from .launcher.autohandoff import _ao_send

    if home is None:
        home = _batch_home()
        if home is None:
            return []
    home = Path(home)
    if not _queue_path(home).is_file() and not _spool_files(home):
        return []
    now = now or _now()
    gap = batch_gap_sec()
    try:
        with _QueueLock(home, timeout=lock_timeout):
            data = _load_queue(home)
            if _drain_spool(home, data):
                _save_queue(home, data)
            snap = [r for to, rs in data["pending"].items()
                    if (only is None or to in only) and isinstance(rs, list)
                    for r in rs if isinstance(r, dict)]
            total = _queue_rows(data)
    except QueueBusy as exc:
        _alarm_busy(home, exc)
        return [{"to": "*", "n": 0, "sent": False, "why": "очередь занята — сброс позже"}]
    _alarm_rows(home, total)
    acked = _acked_map(snap)
    seen = {(str(r.get("tdir") or ""), str(r.get("num") or "")) for r in snap}
    due: Dict[str, List[Dict[str, object]]] = {}
    report: List[Dict[str, object]] = []
    try:
        with _QueueLock(home, timeout=lock_timeout):
            data = _load_queue(home)
            _drain_spool(home, data)
            for to in list(data["pending"]):
                if only is not None and to not in only:
                    continue
                rows = [r for r in data["pending"][to] if isinstance(r, dict)]
                live: List[Dict[str, object]] = []
                for r in rows:
                    key = (str(r.get("tdir") or ""), str(r.get("num") or ""))
                    if key not in seen:
                        live.append(r)       # пришла после снимка — проверит следующий проход
                    elif key in acked and not acked[key]:
                        live.append(r)
                if not live:
                    del data["pending"][to]
                    report.append({"to": to, "n": 0, "sent": False, "why": "прочитано"})
                    continue
                last = _parse_iso(str(data["last"].get(to) or ""))
                wait = gap - (now - last).total_seconds() if last else 0
                if wait > 0 and not force:
                    data["pending"][to] = live
                    report.append({"to": to, "n": len(live), "sent": False,
                                   "why": "окно ещё {0} с".format(int(wait))})
                    continue
                due[to] = live
                del data["pending"][to]
                data["last"][to] = _iso(now)
            _save_queue(home, data)
    except QueueBusy as exc:
        _alarm_busy(home, exc)
        return [{"to": "*", "n": 0, "sent": False, "why": "очередь занята — сброс позже"}]
    for to, rows in due.items():
        text = batch_text(rows)
        try:
            code = _ao_send(to, text)
        except Exception:  # noqa: BLE001
            code = -1
        err = _send_err(to) if code else ""
        row: Dict[str, object] = {"to": to, "n": len(rows), "sent": True, "code": code}
        if code:
            row["err"] = err or "код {0}".format(code)
            row["deaf"] = True
        report.append(row)
        for r in rows:
            ev: Dict[str, object] = {
                "ev": "ping", "num": r["num"], "at": _iso(now), "to": to,
                "code": code, "kind": "batch", "batch": len(rows),
                "bytes": len(text.encode("utf-8"))}
            if code:
                ev.update(err=row["err"], deaf=True)
            try:
                _append(Path(str(r["tdir"])), ev)
                _note_delivery(to, code, err, Path(str(r["tdir"])), str(r["num"]))
            except Exception:  # noqa: BLE001
                pass
        if not code:
            deaf_clear(to)
    return report



def send(ref: str, text: str, *, sender: Optional[str] = None,
         root: Optional[Path] = None, do_ping: bool = True,
         watch: Optional[bool] = None, alert_after_min: Optional[float] = None,
         kind: str = "", urgent: bool = False) -> Dict[str, object]:
    """Положить письмо в ящик нити, пингнуть ведущую сессию, завести ожидальщика.

    *alert_after_min* (SOC-157, ``tide escalate``): без квитанции столько минут от
    отправки — ожидальщик сразу шлёт тревогу менеджеру, не дожидаясь повторов.
    *urgent* (SOC-179): пинг сразу, мимо пачки (и повторы тоже)."""
    if not (text or "").strip():
        raise MailError("mail: пустое письмо")
    tdir, want = target(ref, root)
    to_row = resolve_session(tdir, want) if want else None
    moved: Optional[Dict[str, object]] = None
    if to_row is not None:
        chain = succession(tdir, str(to_row["name"]))
        if chain:
            # SOC-245: адресат уже отдал нить (приём подтверждён) — письмо преемнику
            moved, to_row = to_row, dict(chain[-1])
    who = (sender or "").strip() or _who_am_i()
    body = text if text.endswith("\n") else text + "\n"
    raw = body.encode("utf-8")
    at = _iso(_now())
    to_line = ""
    if to_row is not None:
        to_line = "to-session: {0}{1}\n".format(
            to_row["name"], " ({0})".format(to_row["ao"]) if to_row["ao"] else "")
    if moved is not None:
        to_line += "redirected-from: {0}{1} — передала нить\n".format(
            moved["name"], " ({0})".format(moved["ao"]) if moved["ao"] else "")
    head = ("---\nfrom: {0}\nto: {1}\n{5}sent: {2}\nbytes: {3}\nsha256: {4}\n---\n\n"
            ).format(who, address_of(tdir), at, len(raw), hashlib.sha256(raw).hexdigest(),
                     to_line)
    num, path = _write_new(inbox(tdir), who, head + body)
    sent_ev: Dict[str, object] = {"ev": "sent", "num": num, "at": at, "from": who,
                                  "file": path.name, "bytes": len(raw),
                                  "first": _first_line(text)[:200]}
    if kind:
        sent_ev["kind"] = kind
    if urgent:
        sent_ev["urgent"] = True
    if to_row is not None:
        sent_ev["to_session"] = str(to_row["name"])
        if to_row["ao"]:
            sent_ev["to_ao"] = str(to_row["ao"])
    if moved is not None:
        sent_ev["redirected_from"] = str(moved["name"])
    if _alert_after(alert_after_min):
        sent_ev["alert_after"] = float(alert_after_min)  # type: ignore[arg-type]
    _append(tdir, sent_ev)
    out: Dict[str, object] = {"num": num, "path": str(path), "bytes": len(raw), "ping": None,
                              "watcher": None}
    if moved is not None:
        out["redirected_from"] = str(moved["name"])
        out["to_session"] = str(to_row["name"])  # type: ignore[index]
    if do_ping:
        out["ping"] = ping(tdir, num)
    if watch is None:
        watch = _watch_enabled()
    if watch and not watcher_alive(tdir):
        out["watcher"] = _spawn_watch(tdir)
    return out


def read(ref: str, num: object, *, by: Optional[str] = None,
         root: Optional[Path] = None) -> Tuple[str, Dict[str, object]]:
    """Текст письма + квитанция ``ack`` (кто, когда) в журнал."""
    tdir = target(ref, root)[0]
    rec = letter(tdir, num)
    text = Path(rec["path"]).read_text(encoding="utf-8")
    ack = {"ev": "ack", "num": rec["num"], "at": _iso(_now()),
           "by": (by or "").strip() or _who_am_i(),
           "file_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]}
    _append(tdir, ack)
    reader = (os.environ.get(AO_SESSION_ENV) or "").strip()
    if reader:
        deaf_clear(reader)   # SOC-256: прочла сама — значит слышит
    return text, ack


# --- повтор ----------------------------------------------------------------------

def retry_minutes() -> float:
    raw = (os.environ.get(RETRY_ENV) or "").strip()
    try:
        val = float(raw) if raw else RETRY_DEFAULT_MIN
    except ValueError:
        val = RETRY_DEFAULT_MIN
    return val if val > 0 else RETRY_DEFAULT_MIN


def alert_text(tdir: Path, rec: Dict[str, object], minutes: float) -> str:
    return ("tide: почта {0} от {1} в нить {2} без квитанции {3:g} мин — "
            "пинг и {4} повтора не дошли. Письмо: {5}. Прочесть: tide mail read {2} {0}"
            ).format(rec["num"], rec["sender"], address_of(tdir),
                     minutes * (MAX_RETRIES + 1), MAX_RETRIES, rec["path"])


def escalation_alert_text(tdir: Path, rec: Dict[str, object], minutes: float) -> str:
    return ("tide: эскалация {0} от {1} в нить {2} без квитанции {3:g} мин — {4}. "
            "Письмо: {5}. Прочесть: tide mail read {2} {0}"
            ).format(rec["num"], rec["sender"], address_of(tdir), minutes,
                     re.sub(r"^эскалация:\s*", "", str(rec["first"])), rec["path"])


def step(tdir: Path, num: object, *, now: Optional[datetime] = None,
         minutes: Optional[float] = None) -> str:
    """Один шаг ожидальщика по письму *num* → ``acked`` | ``wait`` | ``retry`` |
    ``alert`` | ``done`` (тревога уже ушла) | ``closed`` (эскалацию закрыли — не тревожить)."""
    from .launcher.autohandoff import send_alert

    now = now or _now()
    minutes = minutes if minutes is not None else retry_minutes()
    rec = letter(tdir, num)
    if rec["acked"]:
        return "acked"
    if rec.get("closed"):
        return "closed"
    if rec["alerted"]:
        return "done"
    deadline = rec.get("alert_after")
    sent_at = _parse_iso(rec["sent"])
    if deadline and sent_at and (now - sent_at).total_seconds() >= float(deadline) * 60:
        # SOC-157: своё окно письма вышло — тревога менеджеру сразу, без повторов
        text = escalation_alert_text(tdir, rec, float(deadline))
        code = send_alert(text, route="manager", source=address_of(tdir),
                          urgent=True)   # SOC-179: эскалация — сразу, не в пачку
        _append(tdir, {"ev": "alert", "num": rec["num"], "at": _iso(now),
                       "route": "manager", "code": code, "why": "alert_after"})
        return "alert"
    marks = [_parse_iso(str(p.get("at"))) for p in rec["pings"]]
    marks = [m for m in marks if m] or [_parse_iso(rec["sent"]) or now]
    if (now - max(marks)).total_seconds() < minutes * 60:
        return "wait"
    retries = sum(1 for p in rec["pings"] if p.get("kind") == "retry")
    if retries < MAX_RETRIES:
        ping(tdir, rec["num"], kind="retry")
        return "retry"
    text = alert_text(tdir, rec, minutes)
    code = send_alert(text, route="manager", source=address_of(tdir))
    _append(tdir, {"ev": "alert", "num": rec["num"], "at": _iso(now), "route": "manager",
                   "code": code})
    return "alert"


def watch(tdir: Path, num: object, *, minutes: Optional[float] = None,
          sleep=time.sleep) -> str:
    """Ожидальщик одного письма (старый вход, оставлен для совместимости)."""
    minutes = minutes if minutes is not None else retry_minutes()
    while True:
        sleep(max(1.0, minutes * 60 / 4))
        try:
            res = step(tdir, num, minutes=minutes)
        except MailError:
            return "gone"
        if res in ("acked", "alert", "done", "closed"):
            return res


# --- ожидальщик ящика и его подъём (SOC-143: повтор переживает перезапуск) --------
#
# Один ожидальщик на ящик нити. Пока он жив, он держит flock на
# ``inbox/watch.lock`` — ядро снимает лок сам, когда процесс умер (kill,
# перезагрузка), так что «жив ли» = «занят ли лок», без pid-гаданий.
# ``inbox/watch.pid`` — витрина (pid + пульс), не источник правды.
# Любой хук почты и любой ``tide mail *`` зовут :func:`revive_project` /
# :func:`revive`: есть письма в ожидании и лок свободен → поднять ожидальщика.
# Ожидальщик первым делом делает проход (просроченный повтор — сразу), а второй
# ожидальщик не встанет: он берёт лок без ожидания и, не взяв, уходит.

LOCK_NAME = "watch.lock"
PIDFILE_NAME = "watch.pid"


def _watch_enabled() -> bool:
    return (os.environ.get(WATCH_ENV) or "").strip().lower() not in (
        "0", "off", "no", "false", "нет")


def pending(tdir: Path) -> List[Dict[str, object]]:
    """Письма, которые ещё ждут ожидальщика: без квитанции, без тревоги, не закрытые."""
    return [r for r in letters(tdir)
            if not r["acked"] and not r["alerted"] and not r.get("closed")]


def _try_lock(tdir: Path):
    """Открытый файл с эксклюзивным flock или None (лок занят / нет fcntl)."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover — не POSIX
        return None
    box = inbox(tdir)
    box.mkdir(parents=True, exist_ok=True)
    fh = open(str(box / LOCK_NAME), "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def watcher_alive(tdir: Path) -> bool:
    """Жив ли ожидальщик ящика: лок держит кто-то другой. Миллисекунды, без сети."""
    if not (inbox(tdir) / LOCK_NAME).exists():
        return False
    fh = _try_lock(tdir)
    if fh is None:
        return True
    fh.close()  # закрытие снимает лок
    return False


def _heartbeat(tdir: Path) -> None:
    try:
        (inbox(tdir) / PIDFILE_NAME).write_text(
            "{0} {1}\n".format(os.getpid(), _iso(_now())), encoding="utf-8")
    except OSError:
        pass


def watch_box(tdir: Path, *, minutes: Optional[float] = None,
              sleep=time.sleep) -> str:
    """Ожидальщик ящика: проход по всем ждущим письмам сразу, затем каждые
    ``minutes/4``; выходит, когда ждать нечего. ``busy`` — уже есть живой."""
    minutes = minutes if minutes is not None else retry_minutes()
    while True:
        fh = _try_lock(tdir)
        if fh is None:
            return "busy"
        try:
            while True:
                _heartbeat(tdir)
                waiting = False
                for rec in pending(tdir):
                    try:
                        res = step(tdir, rec["num"], minutes=minutes)
                    except MailError:
                        continue
                    waiting = waiting or res in ("wait", "retry")
                if not waiting:
                    break
                sleep(max(1.0, minutes * 60 / 4))
        finally:
            try:
                (inbox(tdir) / PIDFILE_NAME).unlink()
            except OSError:
                pass
            fh.close()
        # письмо могло лечь, пока лок ещё держали, — перепроверить без лока
        if not pending(tdir):
            return "idle"


def _spawn_watch(tdir: Path, num: Optional[str] = None) -> Optional[int]:
    """Отсоединённый ``tide mail watch <ящик>`` (своя сессия процессов) → pid."""
    argv = [sys.executable, "-m", "tide", "mail", "watch", str(tdir)]
    try:
        proc = subprocess.Popen(argv, cwd=str(tdir), start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, close_fds=True)
        return proc.pid
    except Exception:  # noqa: BLE001 — сигнал не смеет сломать отправку
        return None


def revive(tdir: Path) -> Optional[int]:
    """Есть ждущие письма и нет живого ожидальщика → поднять его (pid). Не падает."""
    try:
        if not _watch_enabled() or not pending(tdir) or watcher_alive(tdir):
            return None
        return _spawn_watch(tdir)
    except Exception:  # noqa: BLE001 — хук и CLI не смеют упасть из-за подъёма
        return None


def revive_project(root: Path) -> List[int]:
    """:func:`revive` по всем ящикам нитей проекта ``<root>/.tide/arcs/*/inbox``."""
    out: List[int] = []
    try:
        arcs = Path(root) / ".tide" / "arcs"
        for log in sorted(arcs.glob("*/{0}/{1}".format(INBOX_DIRNAME, LOG_NAME))):
            pid = revive(log.parent.parent)
            if pid:
                out.append(pid)
    except Exception:  # noqa: BLE001
        pass
    return out


# --- хуки ------------------------------------------------------------------------

def session_line(root: Path, session: Optional[str]) -> Optional[str]:
    """«почта: N непрочитанных — tide mail ls <нить>» для сессии нити, иначе None.

    Попутно (любой хук почты) поднимает умерших ожидальщиков проекта."""
    revive_project(Path(root))
    if not session:
        return None
    try:
        from . import offload

        entry = offload.find_session_by_claude_id(Path(root), session)
        if entry is None:
            return None
        tdir = entry.parents[1]
        n = len(unread(tdir))
        if not n:
            return None
        return "почта: {0} непрочитанных — tide mail ls {1}".format(n, address_of(tdir))
    except Exception:  # noqa: BLE001 — хук не смеет уронить сессию
        return None


def cmd_prompt_hook(args) -> int:
    """UserPromptSubmit: прежний ``handoff-confirm`` целиком, затем строка почты."""
    from .hooks.handed_off import cmd_prompt

    try:
        raw = sys.stdin.read() if sys.stdin is not None and not sys.stdin.isatty() else ""
    except (OSError, ValueError):
        raw = ""
    sys.stdin = io.StringIO(raw)
    rc = cmd_prompt(args)
    try:
        root = paths.find_tide_root()
        payload = json.loads(raw or "{}")
        sid = ""
        if isinstance(payload, dict):
            sid = str(payload.get("session_id") or "")
        sid = sid or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
        if root is not None:
            line = session_line(root, sid)  # внутри — revive_project
            if line:
                print(line)
    except Exception as exc:  # noqa: BLE001
        print("tide: [mail] skipped: {0}".format(exc), file=sys.stderr)
    return rc


# --- CLI -------------------------------------------------------------------------

def _revive_cli(ref: Optional[str] = None) -> None:
    """Любой ``tide mail *``: поднять умерших ожидальщиков проекта (и нити *ref*)."""
    try:
        root = paths.find_tide_root()
        if root is not None:
            revive_project(root)
        if ref:
            revive(target(ref)[0])
    except Exception:  # noqa: BLE001
        pass


def _cmd_send(args) -> int:
    if args.file:
        text = Path(args.file).expanduser().read_text(encoding="utf-8")
    elif args.text == "-" or args.text is None:
        text = sys.stdin.read()
    else:
        text = args.text
    _revive_cli()
    out = send(args.thread, text, sender=args.sender, do_ping=not args.no_ping,
               urgent=args.urgent)
    p = out["ping"] or {}
    if not args.no_ping:
        if p.get("spooled"):
            how = ("пинг {0} — в лоток: очередь пингов занята, уйдёт со следующим сбросом "
                   "(тик orphan-watch / tide mail flush)").format(p["to"])
        elif p.get("queued"):
            how = ("пинг {0} — в пачку: уйдёт одним пингом не позже чем через {1} мин "
                   "(срочное — --urgent)").format(p["to"], max(1, batch_gap_sec() // 60))
        elif p.get("to"):
            how = "пинг {0} → код {1}".format(p["to"], p.get("code"))
            if p.get("to_all"):
                how += " ({0})".format(LIVE_NOTE.format(len(p["to_all"])))
        else:
            how = "пинг не ушёл: у нити нет ведущей AO-сессии (ожидальщик повторит)"
    else:
        how = "без пинга"
    if out.get("redirected_from"):
        how += " · {0} передала нить — письмо преемнику {1}".format(
            out["redirected_from"], out["to_session"])
    failed = (not args.no_ping) and bool(p.get("deaf"))
    if failed:
        how = "пинг {0} НЕ ДОШЁЛ (код {1}: {2}) — адресат не слышит, менеджер оповещён".format(
            p.get("to"), p.get("code"), str(p.get("err") or "").strip()[-160:])
    print("почта {0} → {1} ({2} Б) · {3}".format(out["num"], out["path"], out["bytes"], how))
    if failed:
        print("письмо лежит непрочитанным; адресат: tide mail read {0} {1}".format(
            args.thread, out["num"]), file=sys.stderr)
        return 1
    return 0


def _cmd_read(args) -> int:
    text, ack = read(args.thread, args.num, by=args.by)
    _revive_cli(args.thread)
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    print("— квитанция: {0} прочитал(а) {1} в {2}".format(ack["by"], ack["num"], ack["at"]))
    return 0


def _cmd_ls(args) -> int:
    tdir = target(args.thread)[0]
    _revive_cli(args.thread)
    recs = letters(tdir)
    new = [r for r in recs if not r["acked"]]
    print("почта нити {0}: {1} непрочитанных, {2} прочитанных".format(
        address_of(tdir), len(new), len(recs) - len(new)))
    for r in recs:
        if r["acked"]:
            last = r["acks"][-1]
            state = "прочитано {0} {1}".format(last.get("by"), last.get("at"))
        else:
            who = str(r["to_session"] or "")
            chain = succession(tdir, who) if who else []
            if chain:  # SOC-245: адресат отдал нить — ждём квитанции преемника
                who = "{0} (нить от {1})".format(chain[-1]["name"], who)
            state = "НЕ ПРОЧИТАНО{0} · пингов {1}".format(
                " адресатом {0}".format(who) if who else "", len(r["pings"]))
            if r["alerted"]:
                state += " · тревога менеджеру"
        if r.get("closed"):
            state += " · эскалация закрыта"
        print("  {0} от {1} · {2} · {3} Б · {4} · {5}".format(
            r["num"], r["sender"], r["sent"], r["bytes"], state, r["first"][:80]))
    return 0


def _cmd_watch(args) -> int:
    target = Path(args.thread)
    tdir = target if target.is_dir() else thread_dir(args.thread)
    print(watch_box(tdir))
    return 0


def flush_line(res: List[Dict[str, object]]) -> str:
    """Строка в лог тика: «mail-batch: → X 3 письма (код 0); Y ждёт: окно ещё 600 с»."""
    parts = []
    for r in res:
        if r.get("sent"):
            parts.append("→ {0} {1} пис. (код {2})".format(r["to"], r["n"], r.get("code")))
        else:
            parts.append("{0} {1} пис.: {2}".format(r["to"], r["n"], r.get("why")))
    return "mail-batch: " + ("; ".join(parts) if parts else "очередь пуста")


def _cmd_flush(args) -> int:
    res = flush_pings(force=args.force)
    print(flush_line(res))
    return 1 if any(r.get("sent") and r.get("code") for r in res) else 0


def _cmd_deaf(args) -> int:
    rows = deaf_sessions()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    elif not rows:
        print("не слышат: никто")
    else:
        for to, r in sorted(rows.items()):
            print("{0}: не слышит с {1} · отказов {2} · {3} · письма: {4}".format(
                to, r.get("since"), r.get("n"),
                MODE_MISMATCH if r.get("mode_mismatch") else "код {0}".format(r.get("code")),
                ", ".join("{0} {1}".format(l.get("addr"), l.get("num"))
                          for l in r.get("letters") or [] if isinstance(l, dict))))
    return 0


def _cmd_retry(args) -> int:
    tdir = target(args.thread)[0]
    for r in unread(tdir):
        print("{0}: {1}".format(r["num"], step(tdir, r["num"])))
    _revive_cli(args.thread)
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "mail", help="thread mail: letters as files in the thread's inbox, ack on read, "
                     "re-ping until read (SOC-143)")
    sub = p.add_subparsers(dest="mail_cmd", metavar="<send|read|ls|retry|flush|deaf|watch>")

    s = sub.add_parser("send", help="drop a letter into <thread>/inbox and ping its holder")
    s.add_argument("thread", help="<нить> | <проект>/<нить>[@<ao-id | сессия>] — "
                                  "@ уточняет адресата: пинг и квитанция только её")
    s.add_argument("text", nargs="?", help="letter text ('-' or omitted — stdin)")
    s.add_argument("--file", help="read the letter from a file (no size limit)")
    s.add_argument("--from", dest="sender", help="who writes (default: $AO_SESSION_ID)")
    s.add_argument("--no-ping", action="store_true", help="only drop the file")
    s.add_argument("--urgent", action="store_true",
                   help="ping now, not in the batch (SOC-179: non-urgent pings go out "
                        "together at most once per $TIDE_MAIL_BATCH_SEC, 1500 s)")
    s.set_defaults(func=_cmd_send, _cmd="mail send")

    r = sub.add_parser("read", help="print a letter and record the ack")
    r.add_argument("thread")
    r.add_argument("num")
    r.add_argument("--by", help="who reads (default: $AO_SESSION_ID)")
    r.set_defaults(func=_cmd_read, _cmd="mail read")

    ls = sub.add_parser("ls", help="unread / read letters of a thread")
    ls.add_argument("thread")
    ls.set_defaults(func=_cmd_ls, _cmd="mail ls")

    rt = sub.add_parser("retry", help="one pass: re-ping overdue unread letters, "
                                      "alert the manager after two retries")
    rt.add_argument("thread")
    rt.set_defaults(func=_cmd_retry, _cmd="mail retry")

    w = sub.add_parser("watch", help=argparse.SUPPRESS)
    w.add_argument("thread", help="thread ref or thread dir")
    w.add_argument("num", nargs="?", help="ignored: the watcher serves the whole inbox")
    w.set_defaults(func=_cmd_watch, _cmd="mail watch")

    fl = sub.add_parser("flush", help="send due batched pings now (the orphan-watch tick "
                                      "does it every 2 min); --force ignores the window")
    fl.add_argument("--force", action="store_true")
    fl.set_defaults(func=_cmd_flush, _cmd="mail flush")

    df = sub.add_parser("deaf", help="sessions whose mail ping failed (ao send refused, e.g. "
                                     "SESSION_MODE_MISMATCH): the 'does not hear' state (SOC-256)")
    df.add_argument("--json", action="store_true")
    df.set_defaults(func=_cmd_deaf, _cmd="mail deaf")

    p.set_defaults(func=lambda a: (p.print_help() or 0), _cmd="mail")


__all__ = ["LOCK_ENV", "QueueBusy", "lock_timeout_sec", "BATCH_ENV", "DEAF_NAME", "MODE_MISMATCH", "deaf_clear", "deaf_sessions", "batch_gap_sec", "batch_text", "flush_line", "flush_pings",
           "from_manager", "is_urgent", "CLOSED_EV", "LIVE_NOTE", "MAX_RETRIES", "MailError", "live_sessions", "resolve_session",
           "target", "thread_sessions", "RETRY_ENV", "WATCH_ENV", "address_of",
           "cmd_prompt_hook", "letters", "ping", "ping_text", "read", "register",
           "pending", "retry_minutes", "revive", "revive_project", "send", "session_line", "step", "thread_dir", "unread", "watch",
           "watch_box", "watcher_alive"]

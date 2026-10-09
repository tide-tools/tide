"""tide.mail — почта нити: письмо файлом, квитанция, повтор пинга (SOC-143).

Зачем: ``ao send`` режет ~4075 байт (отказ глотается), в занятую сессию
сообщение висит в поле ввода до 10 мин, uds теряется. Почта даёт гарантированный
канал между агентами:

* адрес — НИТЬ, не сессия: ``tide mail send <нить> "текст"`` (или ``--file``)
  кладёт письмо в ``<нить>/inbox/NNN-от-<кто>.md`` — без лимита размера;
* затем короткий пинг (< 500 байт) ``ao send`` ведущей сессии нити — её ищем
  тем же путём, что ``tide whois <нить>`` (:func:`tide.whois.thread`);
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
  «почта: N непрочитанных — tide mail ls <нить>».

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
RETRY_ENV = "TIDE_MAIL_RETRY_MIN"
RETRY_DEFAULT_MIN = 15.0
WATCH_ENV = "TIDE_MAIL_WATCH"
MAX_RETRIES = 2
PING_MAX_BYTES = 499
AO_SESSION_ENV = "AO_SESSION_ID"

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
    evs = events(tdir)
    out: List[Dict[str, object]] = []
    for p in sorted(box.iterdir()):
        m = _LETTER.match(p.name)
        if not m or not p.is_file():
            continue
        num = m.group(1)
        mine = [e for e in evs if e.get("num") == num]
        sent = next((e for e in mine if e.get("ev") == "sent"), {})
        out.append({
            "num": num, "path": p, "sender": str(sent.get("from") or m.group(2)),
            "sent": str(sent.get("at") or ""), "bytes": int(sent.get("bytes") or 0),
            "first": str(sent.get("first") or ""),
            "acks": [e for e in mine if e.get("ev") == "ack"],
            "pings": [e for e in mine if e.get("ev") == "ping"],
            "alerted": any(e.get("ev") == "alert" for e in mine),
        })
    return out


def letter(tdir: Path, num: object) -> Dict[str, object]:
    want = _num(num)
    rec = next((r for r in letters(tdir) if r["num"] == want), None)
    if rec is None:
        raise MailError("mail: в нити {0} нет письма {1}".format(
            slug.entry_slug(Path(tdir).name), want))
    return rec


def unread(tdir: Path) -> List[Dict[str, object]]:
    return [r for r in letters(tdir) if not r["acks"]]


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


def ping_text(num: str, sender: str, first: str, addr: str) -> str:
    """Пинг < 500 байт: «почта NNN от X: <первая строка> — tide mail read <нить> NNN»."""
    tail = " — tide mail read {0} {1}".format(addr, num)
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


def ping(tdir: Path, num: str, *, kind: str = "ping") -> Dict[str, object]:
    """Пинг ведущей сессии о письме *num*; жест пишется в журнал. Не падает."""
    from .launcher.autohandoff import _ao_send

    rec = letter(tdir, num)
    text = ping_text(rec["num"], rec["sender"], rec["first"], address_of(tdir))
    to = _holder_ao(tdir)
    code: Optional[int] = None
    if to:
        try:
            code = _ao_send(to, text)
        except Exception:  # noqa: BLE001
            code = -1
    ev = {"ev": "ping", "num": rec["num"], "at": _iso(_now()), "to": to or None,
          "code": code, "kind": kind, "bytes": len(text.encode("utf-8"))}
    _append(tdir, ev)
    return ev


def send(ref: str, text: str, *, sender: Optional[str] = None,
         root: Optional[Path] = None, do_ping: bool = True,
         watch: Optional[bool] = None) -> Dict[str, object]:
    """Положить письмо в ящик нити, пингнуть ведущую сессию, завести ожидальщика."""
    if not (text or "").strip():
        raise MailError("mail: пустое письмо")
    tdir = thread_dir(ref, root)
    who = (sender or "").strip() or _who_am_i()
    body = text if text.endswith("\n") else text + "\n"
    raw = body.encode("utf-8")
    at = _iso(_now())
    head = ("---\nfrom: {0}\nto: {1}\nsent: {2}\nbytes: {3}\nsha256: {4}\n---\n\n").format(
        who, address_of(tdir), at, len(raw), hashlib.sha256(raw).hexdigest())
    num, path = _write_new(inbox(tdir), who, head + body)
    _append(tdir, {"ev": "sent", "num": num, "at": at, "from": who,
                   "file": path.name, "bytes": len(raw), "first": _first_line(text)[:200]})
    out: Dict[str, object] = {"num": num, "path": str(path), "bytes": len(raw), "ping": None,
                              "watcher": None}
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
    tdir = thread_dir(ref, root)
    rec = letter(tdir, num)
    text = Path(rec["path"]).read_text(encoding="utf-8")
    ack = {"ev": "ack", "num": rec["num"], "at": _iso(_now()),
           "by": (by or "").strip() or _who_am_i(),
           "file_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]}
    _append(tdir, ack)
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


def step(tdir: Path, num: object, *, now: Optional[datetime] = None,
         minutes: Optional[float] = None) -> str:
    """Один шаг ожидальщика по письму *num* → ``acked`` | ``wait`` | ``retry`` |
    ``alert`` | ``done`` (тревога уже ушла)."""
    from .launcher.autohandoff import send_alert

    now = now or _now()
    minutes = minutes if minutes is not None else retry_minutes()
    rec = letter(tdir, num)
    if rec["acks"]:
        return "acked"
    if rec["alerted"]:
        return "done"
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
        if res in ("acked", "alert", "done"):
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
    """Письма, которые ещё ждут ожидальщика: без квитанции и без тревоги."""
    return [r for r in letters(tdir) if not r["acks"] and not r["alerted"]]


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
            revive(thread_dir(ref))
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
    out = send(args.thread, text, sender=args.sender, do_ping=not args.no_ping)
    p = out["ping"] or {}
    if not args.no_ping:
        if p.get("to"):
            how = "пинг {0} → код {1}".format(p["to"], p.get("code"))
        else:
            how = "пинг не ушёл: у нити нет ведущей AO-сессии (ожидальщик повторит)"
    else:
        how = "без пинга"
    print("почта {0} → {1} ({2} Б) · {3}".format(out["num"], out["path"], out["bytes"], how))
    return 0


def _cmd_read(args) -> int:
    text, ack = read(args.thread, args.num, by=args.by)
    _revive_cli(args.thread)
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    print("— квитанция: {0} прочитал(а) {1} в {2}".format(ack["by"], ack["num"], ack["at"]))
    return 0


def _cmd_ls(args) -> int:
    tdir = thread_dir(args.thread)
    _revive_cli(args.thread)
    recs = letters(tdir)
    new = [r for r in recs if not r["acks"]]
    print("почта нити {0}: {1} непрочитанных, {2} прочитанных".format(
        address_of(tdir), len(new), len(recs) - len(new)))
    for r in recs:
        if r["acks"]:
            last = r["acks"][-1]
            state = "прочитано {0} {1}".format(last.get("by"), last.get("at"))
        else:
            state = "НЕ ПРОЧИТАНО · пингов {0}".format(len(r["pings"]))
            if r["alerted"]:
                state += " · тревога менеджеру"
        print("  {0} от {1} · {2} · {3} Б · {4} · {5}".format(
            r["num"], r["sender"], r["sent"], r["bytes"], state, r["first"][:80]))
    return 0


def _cmd_watch(args) -> int:
    target = Path(args.thread)
    tdir = target if target.is_dir() else thread_dir(args.thread)
    print(watch_box(tdir))
    return 0


def _cmd_retry(args) -> int:
    tdir = thread_dir(args.thread)
    for r in unread(tdir):
        print("{0}: {1}".format(r["num"], step(tdir, r["num"])))
    _revive_cli(args.thread)
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "mail", help="thread mail: letters as files in the thread's inbox, ack on read, "
                     "re-ping until read (SOC-143)")
    sub = p.add_subparsers(dest="mail_cmd", metavar="<send|read|ls|retry|watch>")

    s = sub.add_parser("send", help="drop a letter into <thread>/inbox and ping its holder")
    s.add_argument("thread", help="<нить> | <проект>/<нить>")
    s.add_argument("text", nargs="?", help="letter text ('-' or omitted — stdin)")
    s.add_argument("--file", help="read the letter from a file (no size limit)")
    s.add_argument("--from", dest="sender", help="who writes (default: $AO_SESSION_ID)")
    s.add_argument("--no-ping", action="store_true", help="only drop the file")
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

    p.set_defaults(func=lambda a: (p.print_help() or 0), _cmd="mail")


__all__ = ["MAX_RETRIES", "MailError", "RETRY_ENV", "WATCH_ENV", "address_of",
           "cmd_prompt_hook", "letters", "ping", "ping_text", "read", "register",
           "pending", "retry_minutes", "revive", "revive_project", "send", "session_line", "step", "thread_dir", "unread", "watch",
           "watch_box", "watcher_alive"]

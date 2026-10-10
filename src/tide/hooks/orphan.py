"""tide.hooks.orphan — нить-сирота: ведущей нет в живых, оффера нет (SOC-167, F87).

Случай 09.10: board · 06 (AO tide-stack-33) упала между ходами — процесс Claude Code
получил SIGSEGV в 20:57 (отчёт macOS ``2.1.296-2026-10-09-205703.ips``). Ни
контекст-хука, ни оффера, ни kill: все старые сторожа привязаны к офферу или к ходу
самой сессии — автоподъём (:func:`tide.launcher.autohandoff.run_autoraise`) ждёт
висящий оффер, сторож приёма (:mod:`tide.launcher.reception_watch`) — только что
поднятую новую, «нить встала» (``signal_stall``) зовёт сама передающая из своего
хука, «две головы» ловит двух живых, а не ноль. Нить простояла без ведущей, пока
её не подняли руками; воркер погиб вместе с ней.

Теперь на хуке ``handoff-confirm`` (UserPromptSubmit любой сессии, рядом с
автозакрытием и шов-синком) раз в :data:`THROTTLE_SEC` проходим нити проектов
ростера и ищем сироту:

* проект в ростере не в архиве и не на паузе пульта лимита
  (``state/throttle.json`` control-home: ``paused.all`` / ``paused.projects``);
* нить открыта (не ``__…__``) и её статус ``active``;
* ведущая — новейшая принятая сессия (:func:`tide.acts.head_session`, она же у
  ``tide whois``); у неё есть ``claude-session:`` и AO-сессия по реестру;
* ведущая не передавала нить (нет ``handed-off:``) — иначе её оффер и есть ответ;
* последний след ведущей (паспорт или транскрипт) свежее :data:`RECENT_HOURS` ч —
  давно уснувшие нити не будим;
* в очереди нет открытого оффера этой нити (``offered``, в том числе с резервом);
* AO говорит, что сессия кончилась (``exited`` / ``terminated``). Не знаем
  (AO не отвечает, перезапуск) — молчим: лучше пропустить проход, чем соврать;
* транскрипт ведущей (``claude-session:`` → ``~/.claude/projects/*/<id>.jsonl``)
  не писался последние :data:`FRESH_MIN` минут (``$TIDE_ORPHAN_FRESH_MIN``). Живой
  случай 10.10: AO после своего перезапуска в 11:32 показывал живую, пишущую
  транскрипт tide-stack-40 как ``exited`` — по одному ответу AO вышла бы ложная
  тревога менеджеру, а с автоподъёмом — вторая голова. Свежий транскрипт важнее
  ответа AO: не сирота, в ``alerts.log`` строка «AO говорит exited, но транскрипт
  свеж N мин — не сирота» (не чаще раза в :func:`repeat_minutes` по нити);
* живого процесса claude этой сессии нет (:mod:`tide.adapters.claude_proc`: одна
  выборка ``ps`` на проход; ``claude --session-id=<claude-session>`` или claude-потомок
  ``ao chat-host <ao-id>``). Тот же случай 10.10: простаивающие ведущие
  (app-development-54 с 08:33, nochnaya-smena-9 с 11:16 до 12:04) транскрипт не
  пишут, AO зовёт их exited, а процесс жив — не сирота, в ``alerts.log`` «не сирота:
  процесс жив (pid N)». ``ps`` не прочёлся — «неизвестно», молчим.

Итого сирота ТОЛЬКО если AO говорит exited/terminated И процесса нет И транскрипт
старше :data:`FRESH_MIN`.

Сирота → строка менеджеру (``send_alert(route=manager)``, нет команды — ``ao send``
по ``tide whois manager``; Грише ничего — tg 3733) и строка в ``state/alerts.log``.
Повтор по той же нити — не чаще раза в :data:`REPEAT_MIN` минут
(``$TIDE_ORPHAN_REPEAT_MIN``), счёт по ``alerts.log``.

Автоподъём — за выключателем, по умолчанию ВЫКЛЮЧЕН (включение решает менеджер):
``$TIDE_ORPHAN_AUTORAISE`` (``1/on`` — все проекты, ``0/off`` — никакие); не задан —
по проекту, ``orphan-autoraise=on`` в ``.tide/canon/config`` (как ``autoclose-old``).
Включён — вместе с тревогой отсоединённый ``tide handoffs orphan-raise`` делает то
же, что менеджер руками 09.10: ``handoff continue --auto`` за ушедшую ведущую — сид
с диска её паспорта (:func:`tide.launcher.autohandoff.run_auto`), и пишет менеджеру
исход. Висит вопрос к человеку или цепочка встала (:func:`autohandoff.decide_for`) —
не поднимает, только говорит менеджеру почему.

Весь сторож выключается ``$TIDE_ORPHAN_WATCH=off`` (тесты держат его выключенным).
``tide handoffs orphans`` — пруф без действий: кого счёл бы сиротой и почему нет.
Никогда не падает.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .. import paths

WATCH_ENV = "TIDE_ORPHAN_WATCH"          # off — сторож молчит совсем
RAISE_ENV = "TIDE_ORPHAN_AUTORAISE"      # 1/on — автоподъём везде, 0/off — нигде
CONFIG_KEY = "orphan-autoraise"          # .tide/canon/config проекта: orphan-autoraise=on
REPEAT_ENV = "TIDE_ORPHAN_REPEAT_MIN"
REPEAT_MIN = 60                          # по той же нити — не чаще раза в столько минут
RECENT_HOURS = 6                         # след ведущей старше — нить спит, не сирота
FRESH_ENV = "TIDE_ORPHAN_FRESH_MIN"
FRESH_MIN = 15                           # транскрипт свежее — ведущая жива, что бы ни сказал AO
STAMP = "orphan.last"
THROTTLE_SEC = 120
THROTTLE_FILE = "throttle.json"          # пульт лимита доски (пауза проектов)
ALERT_LOG = "alerts.log"
MARK = "нить-сирота"                     # метка строки в alerts.log (по ней и повтор)
MARK_FRESH = "не-сирота"                 # отбой (свежий транскрипт / живой процесс) — свой ключ
ROUTE_MANAGER = "manager"
_ON = ("1", "on", "yes", "true", "да")
_OFF = ("0", "off", "no", "false", "нет")


# --- выключатели ------------------------------------------------------------------

def watch_enabled(env: Optional[Dict[str, str]] = None) -> bool:
    e = os.environ if env is None else env
    return (e.get(WATCH_ENV) or "").strip().lower() not in _OFF


def raise_switch(env: Optional[Dict[str, str]] = None) -> Optional[bool]:
    """``$TIDE_ORPHAN_AUTORAISE``: True/False — везде, None — решает конфиг проекта."""
    e = os.environ if env is None else env
    raw = (e.get(RAISE_ENV) or "").strip().lower()
    if raw in _ON:
        return True
    if raw in _OFF:
        return False
    return None


def raise_enabled_for(project_root: Path, env: Optional[Dict[str, str]] = None) -> bool:
    """Автоподъём для проекта: env важнее ``orphan-autoraise=on`` в его конфиге."""
    sw = raise_switch(env)
    if sw is not None:
        return sw
    try:
        from ..canon import store
        raw = store.config_value(Path(project_root), CONFIG_KEY)
    except Exception:  # noqa: BLE001 — не прочли — выключено
        return False
    return str(raw or "").strip().lower() in _ON


def repeat_minutes(env: Optional[Dict[str, str]] = None) -> int:
    e = os.environ if env is None else env
    raw = (e.get(REPEAT_ENV) or "").strip()
    try:
        return max(1, int(raw)) if raw else REPEAT_MIN
    except ValueError:
        return REPEAT_MIN


def fresh_minutes(env: Optional[Dict[str, str]] = None) -> float:
    """``$TIDE_ORPHAN_FRESH_MIN`` или :data:`FRESH_MIN`; 0 — проверка выключена."""
    e = os.environ if env is None else env
    raw = (e.get(FRESH_ENV) or "").strip()
    try:
        return max(0.0, float(raw)) if raw else float(FRESH_MIN)
    except ValueError:
        return float(FRESH_MIN)


# --- пауза пульта лимита ------------------------------------------------------------

def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.astimezone()


def paused(home: Path, project: str, *, now: Optional[datetime] = None) -> bool:
    """Проект на паузе пульта лимита (``state/throttle.json``, договор доски SOC-166):
    ``paused.all`` или ``paused.projects[<проект>]``, и ``until`` ещё не прошёл.
    Нет файла / не читается — не на паузе."""
    try:
        raw = json.loads((paths.state_dir(Path(home)) / THROTTLE_FILE).read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return False
    p = raw.get("paused") if isinstance(raw, dict) else None
    if not isinstance(p, dict):
        return False
    projects = p.get("projects") if isinstance(p.get("projects"), dict) else {}
    if not (p.get("all") is True or projects.get(project) is True):
        return False
    until = raw.get("until")
    if until:
        try:
            end = _aware(datetime.fromisoformat(str(until).replace("Z", "+00:00")))
        except ValueError:
            return True
        if end <= _aware(now or datetime.now()):
            return False  # сброс окна прошёл — доска снимет паузу на пересчёте
    return True


# --- решение по одной нити (только чтение) -------------------------------------------

def _last_sign(entry: Path, sid: str) -> Optional[datetime]:
    """Последний след ведущей: свежее из mtime паспорта и транскрипта."""
    from .. import context_fill

    stamps = []
    for p in (entry / "arc.md", context_fill.transcript_for(sid)):
        try:
            if p is not None:
                stamps.append(Path(p).stat().st_mtime)
        except OSError:
            pass
    return datetime.fromtimestamp(max(stamps)) if stamps else None


def transcript_age_min(sid: str, *, now: Optional[datetime] = None) -> Optional[float]:
    """Сколько минут назад писался транскрипт claude-сессии *sid* — или None
    (общий помощник: :func:`tide.adapters.claude_proc.transcript_age_min`)."""
    from ..adapters import claude_proc
    return claude_proc.transcript_age_min(sid, now=now)


def lazy_procs() -> Callable[[], Optional[List[Any]]]:
    """Одна выборка процессов на проход: первый спрос снимает ``ps``, дальше — память.
    ``None`` в ответе — ``ps`` не прочёлся (неизвестно)."""
    memo: Dict[str, Optional[List[Any]]] = {}

    def get() -> Optional[List[Any]]:
        if "v" not in memo:
            from ..adapters import claude_proc
            try:
                memo["v"] = claude_proc.snapshot()
            except Exception:  # noqa: BLE001
                memo["v"] = None
        return memo["v"]
    return get


def open_offer(offers: List[Dict[str, object]], project: str, thread: str) -> str:
    """Имя открытого (``offered``, не взятого) оффера нити — или ``""``."""
    from .. import handoff_queue as hq

    for rec in offers:
        if rec.get("status") != hq.STATUS_OFFERED:
            continue
        if str(rec.get("taken_at") or "-").strip() not in ("", "-"):
            continue
        if str(rec.get("project") or "") not in (project, "-", ""):
            continue
        arc = str(rec.get("arc") or "")
        if arc == thread or arc.startswith(thread + "/"):
            return str(rec.get("name") or "?")
    return ""


def verdict(home: Path, project: str, root: Path, thread_dir: Path, *,
            offers: List[Dict[str, object]], now: Optional[datetime] = None,
            session_of: Optional[Callable[[str], Optional[dict]]] = None,
            recent_hours: float = RECENT_HOURS,
            fresh_min: Optional[float] = None,
            procs: Optional[Callable[[], Optional[List[Any]]]] = None) -> Dict[str, object]:
    """Сирота ли нить *thread_dir* проекта *project* → ``{project, thread, session,
    title, ao, ref, last, orphan, why}``. Только чтение; AO спрашивается последним,
    его «кончилась» перебивают транскрипт свежее *fresh_min* минут (``fresh`` в ответе)
    и живой процесс claude ведущей (``alive_pid``; выборка — *procs*, по умолчанию
    свой ``ps``; не прочёлся — не сирота)."""
    from .. import acts, fields, slug
    from ..arc import holder, stream
    from .autoclose import ended

    now = now or datetime.now()
    tslug = slug.entry_slug(thread_dir.name)
    out: Dict[str, object] = {"project": project, "thread": tslug, "thread_dir": str(thread_dir),
                              "root": str(root), "session": "", "title": "", "ao": "",
                              "ref": "", "last": None, "orphan": False, "why": ""}

    def no(why: str) -> Dict[str, object]:
        out["why"] = why
        return out

    if stream.effective_status(thread_dir) != "active":
        return no("нить не active ({0})".format(stream.effective_status(thread_dir)))
    head = acts.head_session(thread_dir)
    if head is None:
        return no("в нити нет открытых сессий")
    pp = head / "arc.md"
    sid = (fields.read_field(pp, "claude-session") or "").strip()
    out["session"] = head.name
    out["ref"] = "{0}/{1}".format(tslug, slug.entry_slug(head.name))
    out["title"] = stream.thread_session_title(head, project) or head.name
    if not sid:
        return no("ведущая ещё не принята (нет claude-session)")
    if (fields.read_field(pp, "handed-off") or "").strip():
        return no("ведущая передала нить (handed-off) — ответ за её оффером")
    last = _last_sign(head, sid)
    out["last"] = last
    if last is None or now - last > timedelta(hours=recent_hours):
        return no("нить спит: последний след ведущей старше {0} ч".format(recent_hours))
    offer = open_offer(offers, project, thread_dir.name)
    if offer:
        out["offer"] = offer
        return no("оффер {0} висит — им занят автоподъём/▶".format(offer))
    handle = holder.ao_handle_for(Path(home), sid)
    out["ao"] = handle
    if not handle:
        return no("AO-сессия ведущей неизвестна — живость не проверить")
    try:
        if session_of is None:
            from ..adapters.ao import AoAdapter
            session_of = AoAdapter().session
        s = session_of(handle)
    except Exception:  # noqa: BLE001
        s = None
    if not s:
        return no("AO не ответил про {0} — не знаю, молчу".format(handle))
    gone = ended(s)
    if not gone:
        return no("ведущая {0} жива".format(handle))
    out["gone"] = gone
    # SOC-167: AO после своего перезапуска врёт «exited» про живую — вторая проверка:
    # свежий транскрипт ИЛИ живой процесс claude ведущей (общий помощник живости)
    from ..adapters import claude_proc
    window = fresh_minutes() if fresh_min is None else fresh_min
    live = claude_proc.liveness(session_ids=[sid], ao_handle=handle, fresh_min=window,
                                procs=procs or lazy_procs(), now=now)
    age = live["age"]
    if live["by"] == "transcript":
        out["fresh"] = int(age)
        return no("AO говорит {0}, но транскрипт свеж {1} мин — не сирота".format(
            gone, int(age)))
    if live["alive"] is None:
        return no("AO говорит {0}, процессы не прочитать (ps) — не знаю, молчу".format(gone))
    if live["alive"]:
        out["alive_pid"] = live["pid"]
        quiet = "транскрипт молчит {0} мин".format(int(age)) if age is not None \
            else "транскрипта нет"
        return no("не сирота: процесс жив (pid {0}) — AO говорит {1}, {2}".format(
            live["pid"], gone, quiet))
    out["orphan"] = True
    out["why"] = "ведущая {0} не жива в AO ({1}), оффера нет".format(handle, gone)
    return out


# --- проход ----------------------------------------------------------------------------

def _projects(home: Path) -> List[Dict[str, str]]:
    from .. import roster

    try:
        entries = roster.read_roster(Path(home))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for e in entries:
        if str(e.get("status") or "").strip() == roster.STATUS_ARCHIVED:
            continue
        root = Path(e["path"]).expanduser()
        if paths.arcs_dir(root).is_dir():
            out.append({"name": e["name"], "root": str(root)})
    return out


def scan(home: Path, *, now: Optional[datetime] = None,
         session_of: Optional[Callable[[str], Optional[dict]]] = None,
         recent_hours: float = RECENT_HOURS,
         fresh_min: Optional[float] = None,
         procs: Optional[Callable[[], Optional[List[Any]]]] = None
         ) -> List[Dict[str, object]]:
    """Вердикты по всем открытым нитям проектов ростера (архив и пауза — мимо)."""
    from .. import handoff_queue as hq
    from ..arc import stream

    home = Path(home)
    try:
        offers = hq.list_offers(home)
    except Exception:  # noqa: BLE001
        offers = []
    out: List[Dict[str, object]] = []
    procs = procs or lazy_procs()            # один ps на весь проход, и то по спросу
    for proj in _projects(home):
        if paused(home, proj["name"], now=now):
            out.append({"project": proj["name"], "thread": "*", "orphan": False,
                        "why": "проект на паузе пульта лимита (throttle.json)"})
            continue
        root = Path(proj["root"])
        try:
            threads = stream.thread_entries(root)
        except Exception:  # noqa: BLE001
            continue
        for tdir in threads:
            try:
                out.append(verdict(home, proj["name"], root, tdir, offers=offers, now=now,
                                   session_of=session_of, recent_hours=recent_hours,
                                   fresh_min=fresh_min, procs=procs))
            except Exception as exc:  # noqa: BLE001 — одна нить не роняет проход
                out.append({"project": proj["name"], "thread": tdir.name,
                            "orphan": False, "why": "ошибка: {0}".format(exc)})
    return out


# --- журнал и повтор --------------------------------------------------------------------

def _log_path(home: Path) -> Path:
    return paths.state_dir(Path(home)) / ALERT_LOG


def _key(v: Dict[str, object], mark: str = MARK) -> str:
    return "{0} · {1}/{2}".format(mark, v["project"], v["thread"])


def alarmed_recently(home: Path, v: Dict[str, object], *, now: Optional[datetime] = None,
                     minutes: int = REPEAT_MIN, mark: str = MARK) -> bool:
    """В ``alerts.log`` уже есть строка *mark* (тревога) по этой нити свежее *minutes* мин."""
    try:
        lines = _log_path(home).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    key = " {0} · ".format(_key(v, mark))
    now = now or datetime.now()
    for ln in reversed(lines):
        if key not in ln:
            continue
        try:
            at = datetime.fromisoformat(ln.split(" ", 1)[0])
        except ValueError:
            continue
        return now - at < timedelta(minutes=minutes)
    return False


def journal(home: Path, v: Dict[str, object], tail: str, *,
            now: Optional[datetime] = None, mark: str = MARK) -> Optional[str]:
    line = "{0} {1} · {2} · {3}".format(
        (now or datetime.now()).isoformat(timespec="seconds"), _key(v, mark),
        v.get("ao") or "?", tail)
    try:
        path = _log_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        return None
    return line


# --- тексты ------------------------------------------------------------------------------

def _hhmm(dt: object) -> str:
    return dt.strftime("%d.%m %H:%M") if isinstance(dt, datetime) else "?"


def raise_command(v: Dict[str, object]) -> str:
    return "cd {0} && tide handoff continue --auto --session {1}".format(v["root"], v["ref"])


def manager_text(v: Dict[str, object], *, raising: bool) -> str:
    head = ("tide: нить {0}/{1} осталась без ведущей — {2} (AO {3}) не жива ({4}), "
            "последний след {5}, оффера нет.".format(
                v["project"], v["thread"], v["title"], v["ao"], v.get("gone") or "?",
                _hhmm(v.get("last"))))
    if raising:
        return head + " Поднимаю сам из паспорта (автоподъём сироты включён); исход пришлю."
    return head + " Подними: {0} (автоподъём сироты выключен: ${1}).".format(
        raise_command(v), RAISE_ENV)


def _alert_manager(text: str, source: str, thread: str) -> None:
    """Тот же путь, что у тревог автозакрытия: пульт ``route=manager``; нет команды —
    ``ao send`` менеджеру по whois. Грише — ничего (tg 3733)."""
    from ..launcher import autohandoff as _ah
    if _ah.send_alert(text, route=ROUTE_MANAGER, source=source,
                      extra_env={"TIDE_ALERT_THREAD": thread}) is None:
        _ah.tell_manager(text)


# --- автоподъём ---------------------------------------------------------------------------

def _spawn_raise(v: Dict[str, object]) -> Optional[int]:
    """Отсоединённый ``tide handoffs orphan-raise`` (своя сессия процессов: подъём ждёт
    живость новой до ~3 мин — хуку столько нельзя)."""
    argv = [sys.executable, "-m", "tide", "handoffs", "orphan-raise", str(v["ref"]),
            "--root", str(v["root"]), "--project", str(v["project"])]
    proc = subprocess.Popen(argv, cwd=str(v["root"]), start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, close_fds=True)
    return proc.pid


def run_raise(home: Path, root: Path, project: str, ref: str, *,
              session_of: Optional[Callable[[str], Optional[dict]]] = None,
              alert=None, now: Optional[datetime] = None) -> Dict[str, object]:
    """Поднять сироту: перепроверить (оффер мог появиться, ведущая — ожить), решить
    по :func:`autohandoff.decide_for`, затем :func:`autohandoff.run_auto` за ушедшую
    ведущую. Исход — менеджеру и в ``alerts.log``. Никогда не падает."""
    from .. import handoff_queue as hq
    from ..launcher import autohandoff as _ah
    from ..arc import stream

    home, root = Path(home), Path(root)
    say = alert or _alert_manager
    out: Dict[str, object] = {"ref": ref, "action": "skip"}
    try:
        entry = _ah.caller_session_entry(root, ref)
        tdir = _ah.thread_of(entry)
        v = verdict(home, project, root, tdir, offers=hq.list_offers(home), now=now,
                    session_of=session_of)
        out["verdict"] = v
        if not v["orphan"] or v.get("session") != entry.name:
            out["why"] = v["why"] or "ведущая уже другая"
            journal(home, v, "автоподъём не нужен: {0}".format(out["why"]), now=now)
            return out
        src = "{0}/{1}/orphan".format(project, v["thread"])
        d = _ah.decide_for(entry)
        if not d.spawn:
            out["action"], out["why"] = "stopped", d.detail
            text = ("tide: нить {0}/{1} без ведущей — сам не поднимаю: {2}. Подними: {3}"
                    .format(project, v["thread"], d.detail, raise_command(v)))
            journal(home, v, "автоподъём остановлен: {0}".format(d.detail), now=now)
            say(text, src, str(v["thread"]))
            return out
        res = _ah.run_auto(root, session_ref=ref)
        h = res.handoff
        offer = getattr(h, "offer_path", None)
        key = offer.stem if offer else "?"
        if h is not None and getattr(h, "handed_over", False):
            out["action"] = "raised"
            text = ("tide: нить {0}/{1} была без ведущей ({2} не жива) — поднял новую "
                    "сессию сам из паспорта, оффер {3}".format(project, v["thread"],
                                                                v["title"], key))
        else:
            out["action"] = "failed"
            text = ("tide: нить {0}/{1} без ведущей — поднять сам не вышло, оффер {2} "
                    "висит; подними: tide pickup {2}".format(project, v["thread"], key))
        out["offer"] = key
        journal(home, v, "автоподъём: {0} · оффер {1}".format(out["action"], key), now=now)
        say(text, src, str(v["thread"]))
    except Exception as exc:  # noqa: BLE001
        out["action"], out["why"] = "failed", str(exc)
    return out


# --- тревога ---------------------------------------------------------------------------

def alarm(home: Path, v: Dict[str, object], *, env: Optional[Dict[str, str]] = None,
          now: Optional[datetime] = None, alert=None, spawn=None) -> Dict[str, object]:
    """Сирота → строка в alerts.log + менеджеру; включён автоподъём — отсоединённый
    подъём. Повтор по нити — не чаще :func:`repeat_minutes`."""
    if alarmed_recently(home, v, now=now, minutes=repeat_minutes(env)):
        return {"sent": False, "why": "повтор раньше {0} мин".format(repeat_minutes(env))}
    raising = raise_enabled_for(Path(str(v["root"])), env)
    text = manager_text(v, raising=raising)
    journal(home, v, "{0} · {1}".format(
        v["why"], "автоподъём" if raising else "автоподъём выключен"), now=now)
    try:
        (alert or _alert_manager)(text, "{0}/{1}/orphan".format(v["project"], v["thread"]),
                                  str(v["thread"]))
    except Exception:  # noqa: BLE001
        pass
    pid = None
    if raising:
        try:
            pid = (spawn or _spawn_raise)(v)
        except Exception:  # noqa: BLE001
            pid = None
    return {"sent": True, "text": text, "raise_pid": pid, "raising": raising}


def sweep(home: Path, *, env: Optional[Dict[str, str]] = None,
          now: Optional[datetime] = None, **kw) -> List[Dict[str, object]]:
    """Найти сирот и поднять тревогу по каждой (:func:`alarm`)."""
    act = {k: kw.pop(k) for k in ("alert", "spawn") if k in kw}
    kw.setdefault("fresh_min", fresh_minutes(env))
    out = []
    for v in scan(home, now=now, **kw):
        if v.get("orphan"):
            v["alarm"] = alarm(home, v, env=env, now=now, **act)
        elif ("fresh" in v or "alive_pid" in v) and not alarmed_recently(home, v, now=now,
                                                   minutes=repeat_minutes(env),
                                                   mark=MARK_FRESH):
            journal(home, v, str(v["why"]), now=now, mark=MARK_FRESH)
        out.append(v)
    return out


def _throttled(home: Path) -> bool:
    p = paths.state_dir(Path(home)) / STAMP
    try:
        if p.is_file() and datetime.now().timestamp() - p.stat().st_mtime < THROTTLE_SEC:
            return True
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    except OSError:
        return True
    return False


def on_prompt(*, home: Optional[Path] = None,
              env: Optional[Dict[str, str]] = None) -> List[Dict[str, object]]:
    """Хук handoff-confirm: раз в :data:`THROTTLE_SEC` — :func:`sweep`. Не падает."""
    try:
        e = os.environ if env is None else env
        if not watch_enabled(e):
            return []
        home = Path(home) if home is not None else paths.control_home()
        if _throttled(home):
            return []
        return sweep(home, env=e)
    except Exception:  # noqa: BLE001 — хук не смеет уронить сессию
        return []


# --- CLI ----------------------------------------------------------------------------------

def render(v: Dict[str, object]) -> str:
    where = "{0}/{1}".format(v.get("project"), v.get("thread"))
    if v.get("orphan"):
        return "СИРОТА {0}: {1} — {2}; последний след {3}".format(
            where, v.get("title"), v.get("why"), _hhmm(v.get("last")))
    return "  {0}: {1}".format(where, v.get("why"))


def cmd_orphans(args) -> int:
    """``tide handoffs orphans`` — пруф без действий: кто сирота и почему остальные нет."""
    home = paths.control_home()
    res = scan(home)
    for v in res:
        print(render(v))
    n = sum(1 for v in res if v.get("orphan"))
    print("tide: сирот {0} из {1} нитей (ничего не отправлено; автоподъём ${2}, "
          "конфиг проекта {3}=on)".format(n, len(res), RAISE_ENV, CONFIG_KEY))
    return 0


def cmd_orphan_raise(args) -> int:
    import json as _json

    out = run_raise(paths.control_home(), Path(args.root), args.project, args.ref)
    out.pop("verdict", None)
    print(_json.dumps(out, ensure_ascii=False, default=str))
    return 0


def register(hsub) -> None:
    ap = hsub.add_parser("orphans", help="threads whose head session is dead in AO with no "
                         "open offer (SOC-167); read-only")
    ap.set_defaults(func=cmd_orphans, _cmd="handoffs orphans")
    rp = hsub.add_parser("orphan-raise")
    rp.add_argument("ref")
    rp.add_argument("--root", required=True)
    rp.add_argument("--project", required=True)
    rp.set_defaults(func=cmd_orphan_raise, _cmd="handoffs orphan-raise")
    # внутренняя команда, не для рук — не светить в help группы
    hsub._choices_actions = [a for a in hsub._choices_actions if a.dest != "orphan-raise"]


__all__ = ["CONFIG_KEY", "FRESH_ENV", "RAISE_ENV", "REPEAT_ENV", "WATCH_ENV", "alarm", "alarmed_recently",
           "manager_text", "on_prompt", "open_offer", "paused", "raise_enabled_for",
           "lazy_procs", "raise_switch", "register", "run_raise", "scan", "sweep",
           "transcript_age_min",
           "verdict"]

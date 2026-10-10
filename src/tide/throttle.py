"""tide.throttle — пауза до сброса, потолок агентов и правило 85 % (SOC-166, движок).

Слово Гриши 10.10: «неэффективная токеномика, когда агенты начинают работу, потом
блок; хочу с дашборда… останавливать работу или продолжать, и… лимит, сколько агентов
может работать параллельно».

Доска (tide-stack ``board/throttle.py``) пишет ``.tide/state/throttle.json``
control-home; движок его ТОЛЬКО читает (договор файла)::

    {"v":1, "paused":{"all":bool, "projects":{"<проект>":bool}}, "until":ISO|null,
     "ceiling":int|null, "ceiling_auto":int|null, "auto_pct":85, "window_pct":число|null,
     "window_until":ISO|null, "by":"board", "at":ISO, "auto_at":…}

Правила:

* пауза = ``paused.all`` или ``paused.projects[проект]``, и (``until`` null или now < until);
* потолок = min(ceiling, ceiling_auto), null не учитывается; живых рабочих AO-сессий
  (``ao session ls --json``: не закрыта, не оркестратор, статус working) ≥ потолка —
  новые агенты не стартуют;
* правило 85 %: ``window_pct ≥ auto_pct`` (и окно ещё не сброшено) — тяжёлые запуски
  (новые агенты, tide-ci, полный pytest) не начинаются.

Что гейтится (вид запуска — :func:`kind_of`): ``spawn`` — ``ao spawn``, ``ao session
restore|resume-agent``, ``ao review trigger``; ``heavy`` — ``tide-ci`` и полный pytest
(без файлов/``-k``). Правки, git commit, tide offload/mail, ao send — никогда.

Безопасность: role-gate стоит в каждой AO-сессии, поэтому чтение файла не падает
НИКОГДА — нет файла / битый JSON / нет полей → без ограничений и строка в
``state/throttle.log``. Решения — туда же, одна строка на решение; повтор той же
причины той же сессии — не чаще раза в :data:`REPEAT_MIN` минут.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, NamedTuple, Optional, Tuple

FILE = "throttle.json"
LOG = "throttle.log"
REPEAT_MIN = 10          # повтор той же причины той же сессии в журнал — не чаще
STALE_MIN = 10           # файл старше — «доска не обновляла файл N мин»
AO_LS_TIMEOUT = 5        # сек на ``ao session ls`` — гейт не ждёт дольше
LOG_TAIL_BYTES = 64 * 1024
LIVE_STATUSES = ("working", "active", "running")
DEFAULT_AUTO_PCT = 85.0

KIND_SPAWN = "spawn"
KIND_HEAVY = "heavy"
KIND_WAKE = "wake"       # автопинок после сброса (SOC-161): держит только пауза

# ao <verb> [<sub>] — порождение рабочей AO-сессии/агента.
_AO_SPAWN = {("spawn",), ("session", "restore"), ("session", "resume-agent"),
             ("review", "trigger")}
# pytest: флаги, чьё значение — отдельный токен (его не путаем с файлом).
_PYTEST_VALUE_FLAGS = frozenset({
    "-m", "-n", "-p", "-c", "-o", "-W", "-r", "--maxfail", "--timeout", "--rootdir",
    "--deselect", "--ignore", "--ignore-glob", "--durations", "--tb", "--basetemp",
    "--junitxml", "--log-level", "--confcutdir", "--cov", "--import-mode", "--dist",
    "--capture", "--override-ini",
})
_PYTEST_TARGETED_FLAGS = frozenset({"--lf", "--last-failed"})
_PYTEST_WHOLE = frozenset({"tests", "tests/", ".", "./"})
_WRAPPERS = frozenset({"time", "nice", "nohup", "command", "exec", "caffeinate"})
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_REDIRECT = re.compile(r"^(\d*|&)(>>?|<)")


# --- файл доски ---------------------------------------------------------------

def _home() -> Optional[Path]:
    try:
        from . import paths
        return paths.control_home()
    except Exception:  # noqa: BLE001 — нет control-home — нет и троттлинга
        return None


def file_path(home: Optional[Path]) -> Optional[Path]:
    """``<control-home>/.tide/state/throttle.json`` (через tide-home, не строкой)."""
    if home is None:
        return None
    from . import paths
    return paths.state_dir(Path(home)) / FILE


def _iso(raw) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        d = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.astimezone()


def _int(v) -> Optional[int]:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _num(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse(raw) -> Tuple[Optional[dict], Optional[str]]:
    """Сырой JSON доски → (состояние, беда). Нет ни ``paused``, ни ``ceiling`` или
    не объект — (None, беда): без ограничений. Кривое поле — пропущено, беда названа."""
    if not isinstance(raw, dict):
        return None, "файл не объект JSON"
    if "paused" not in raw and "ceiling" not in raw:
        return None, "нет полей paused/ceiling"
    bad: List[str] = []
    p = raw.get("paused")
    if p is not None and not isinstance(p, dict):
        bad.append("paused")
        p = {}
    p = p or {}
    projects = p.get("projects")
    if projects is not None and not isinstance(projects, dict):
        bad.append("paused.projects")
        projects = {}
    st = {
        "paused_all": p.get("all") is True,
        "paused_projects": sorted(str(k) for k, v in (projects or {}).items() if v is True),
        "until": _iso(raw.get("until")),
        "ceiling": _int(raw.get("ceiling")),
        "ceiling_auto": _int(raw.get("ceiling_auto")),
        "auto_pct": _num(raw.get("auto_pct")),
        "window_pct": _num(raw.get("window_pct")),
        "window_until": _iso(raw.get("window_until")),
        "at": _iso(raw.get("at")),
    }
    for key in ("until", "window_until", "at"):
        if raw.get(key) is not None and st[key] is None:
            bad.append(key)
    for key, conv in (("ceiling", _int), ("ceiling_auto", _int), ("auto_pct", _num),
                      ("window_pct", _num)):
        if raw.get(key) is not None and conv(raw.get(key)) is None:
            bad.append(key)
    if st["auto_pct"] is None:
        st["auto_pct"] = DEFAULT_AUTO_PCT
    return st, ("кривые поля: " + ", ".join(bad)) if bad else None


def load(home: Optional[Path] = None) -> Tuple[Optional[dict], Optional[str], Optional[Path]]:
    """Прочесть файл доски безопасно → (состояние | None, беда | None, путь).

    Никогда не бросает: нет control-home / файла / битый JSON → (None, причина, путь)."""
    try:
        path = file_path(home if home is not None else _home())
        if path is None:
            return None, "нет control-home ($TIDE_HOME)", None
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None, "нет файла", path
        except OSError as exc:
            return None, "не читается: {0}".format(type(exc).__name__), path
        try:
            raw = json.loads(text)
        except ValueError:
            return None, "битый JSON", path
        st, bad = parse(raw)
        return st, bad, path
    except Exception as exc:  # noqa: BLE001 — троттлинг не ломает хук
        return None, "сбой чтения: {0}".format(type(exc).__name__), None


# --- правила ------------------------------------------------------------------

def _now(now: Optional[datetime]) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def paused(st: Optional[dict], project: Optional[str], now: Optional[datetime] = None) -> bool:
    """Пауза для *project*: all или проект, и until null или now < until."""
    if not st:
        return False
    on = st["paused_all"] or (bool(project) and project in st["paused_projects"])
    if not on:
        return False
    until = st.get("until")
    return until is None or _now(now) < until


def ceiling(st: Optional[dict]) -> Optional[int]:
    """Действующий потолок = min(ceiling, ceiling_auto), null не учитывается."""
    if not st:
        return None
    vals = [v for v in (st.get("ceiling"), st.get("ceiling_auto")) if v is not None]
    return min(vals) if vals else None


def window_hot(st: Optional[dict], now: Optional[datetime] = None) -> bool:
    """Правило 85 %: window_pct ≥ auto_pct, и окно ещё не сброшено (window_until)."""
    if not st or st.get("window_pct") is None or st.get("auto_pct") is None:
        return False
    wu = st.get("window_until")
    if wu is not None and _now(now) >= wu:
        return False   # окно уже сброшено — процент прошлого окна не в счёт
    return st["window_pct"] >= st["auto_pct"]


def hhmm(d: Optional[datetime], now: Optional[datetime] = None) -> str:
    if d is None:
        return ""
    loc = d.astimezone()
    if loc.date() != _now(now).astimezone().date():
        return loc.strftime("%d.%m %H:%M")
    return loc.strftime("%H:%M")


def live_agents(timeout: int = AO_LS_TIMEOUT) -> Optional[int]:
    """Живых рабочих AO-сессий сейчас (``ao session ls --json``: не закрыта, не
    оркестратор, статус working/active) или None — AO недоступен/молчит."""
    try:
        from .adapters.ao import ao_bin
        proc = subprocess.run([ao_bin(), "session", "ls", "--json"], capture_output=True,
                              text=True, timeout=timeout)
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout or "{}")
        if not isinstance(data, dict):
            return None
        rows = data.get("data") or data.get("sessions") or []
    except Exception:  # noqa: BLE001
        return None
    n = 0
    for r in rows:
        if not isinstance(r, dict) or r.get("isTerminated") or r.get("role") == "orchestrator":
            continue
        if str(r.get("status") or "") in LIVE_STATUSES or r.get("activity") == "active":
            n += 1
    return n


class Verdict(NamedTuple):
    allow: bool
    code: str      # pause · window · ceiling · ok · unknown
    reason: str


_HEAVY_WHAT = "тяжёлые прогоны (tide-ci, полный pytest) не начинаются"


def verdict(st: Optional[dict], project: Optional[str], kind: str, *,
            now: Optional[datetime] = None,
            live: Optional[Callable[[], Optional[int]]] = None) -> Verdict:
    """Решение по запуску вида *kind* (spawn | heavy) в проекте *project*."""
    if not st:
        return Verdict(True, "ok", "")
    now = _now(now)
    if paused(st, project, now):
        whom = "всех проектов" if st["paused_all"] else "проекта {0}".format(project)
        until = ("до сброса окна (до {0})".format(hhmm(st["until"], now))
                 if st.get("until") else "без срока — снимет доска")
        if kind == KIND_SPAWN:
            return Verdict(False, "pause", "tide · пауза {0} {1} — доведи шаг до коммита, "
                           "новые агенты не стартуют.".format(whom, until))
        if kind == KIND_WAKE:
            return Verdict(False, "pause", "tide · пауза {0} {1} — не будить.".format(whom, until))
        return Verdict(False, "pause", "tide · пауза {0} {1} — доведи шаг до коммита; "
                       "{2}, точечный pytest по файлам можно.".format(whom, until, _HEAVY_WHAT))
    if kind == KIND_WAKE:
        return Verdict(True, "ok", "")
    if window_hot(st, now):
        tail = (" до сброса в {0}".format(hhmm(st["window_until"], now))
                if st.get("window_until") else "")
        what = "новые агенты не стартуют" if kind == KIND_SPAWN else _HEAVY_WHAT
        return Verdict(False, "window", "tide · окно израсходовано на {0:.0f}% (порог {1:.0f}%){2} "
                       "— {3}; точечный pytest по файлам можно, доведи шаг до коммита."
                       .format(st["window_pct"], st["auto_pct"], tail, what))
    if kind == KIND_SPAWN:
        top = ceiling(st)
        if top is not None:
            n = (live or live_agents)()
            if n is None:
                return Verdict(True, "unknown", "потолок {0}, живых не сосчитать (AO молчит)"
                               .format(top))
            if n >= top:
                return Verdict(False, "ceiling", "tide · потолок {0} агентов, сейчас {1} — "
                               "встань в очередь: доведи текущий шаг, повтори запуск, когда "
                               "кто-то закончит (tide throttle status). Очереди по приоритету "
                               "проектов нет — решает менеджер.".format(top, n))
            return Verdict(True, "ok", "потолок {0}, сейчас {1}".format(top, n))
    return Verdict(True, "ok", "")


# --- вид запуска по команде ---------------------------------------------------

# ``<<EOF`` / ``<<-'EOF'`` (не ``<<<``): тело heredoc — данные, не команды. Сплиттер
# role-gate режет по переводу строки, и строка тела «ao review trigger …» в тексте
# коммита читалась бы как запуск агента (живой случай 10.10).
_HEREDOC = re.compile(r"(?<!<)<<(?!<)-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def strip_heredocs(command: str) -> str:
    """*command* без тел heredoc (строка-открывашка остаётся, тело и терминатор — нет)."""
    lines = command.split("\n")
    out: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        for m in _HEREDOC.finditer(line):
            delim = m.group(2)
            while i < len(lines) and lines[i].strip() != delim:
                i += 1
            i += 1
    return "\n".join(out)


def _strip_prefix(tokens: List[str]) -> List[str]:
    t = list(tokens)
    while t:
        if _ASSIGN.match(t[0]):
            t.pop(0)
            continue
        base = os.path.basename(t[0])
        if base == "env":
            t.pop(0)
            while t and (t[0].startswith("-") or _ASSIGN.match(t[0])):
                flag = t.pop(0)
                if flag in ("-u", "--unset", "-C", "--chdir", "-S") and t:
                    t.pop(0)
            continue
        if base in _WRAPPERS:
            t.pop(0)
            continue
        if base == "timeout" and len(t) > 1:
            t = t[2:]
            continue
        break
    return t


def _drop_redirects(tokens: List[str]) -> List[str]:
    out: List[str] = []
    skip = False
    for tok in tokens:
        if skip:
            skip = False
            continue
        m = _REDIRECT.match(tok)
        if m:
            if m.end() == len(tok):
                skip = True       # ``> файл`` — файл отдельным токеном
            continue
        out.append(tok)
    return out


def pytest_is_full(args: List[str]) -> bool:
    """pytest без файлов/узлов и без ``-k`` — полный прогон. ``tests``/``.`` — тоже полный."""
    targets: List[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            targets.extend(args[i + 1:])
            break
        if a == "-k" or (a.startswith("-k") and not a.startswith("--")):
            return False
        if a in _PYTEST_TARGETED_FLAGS:
            return False
        if a in _PYTEST_VALUE_FLAGS:
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        targets.append(a)
        i += 1
    return all(t in _PYTEST_WHOLE for t in targets)


def kind_of(tokens: List[str]) -> Optional[str]:
    """Вид запуска одной команды (токены shlex) → ``spawn`` | ``heavy`` | None."""
    t = _drop_redirects(_strip_prefix(tokens))
    if not t:
        return None
    base = os.path.basename(t[0])
    if base == "ao":
        rest = t[1:]
        for verb in _AO_SPAWN:
            if tuple(rest[:len(verb)]) == verb:
                return KIND_SPAWN
        return None
    if base in ("bash", "sh", "zsh") and len(t) > 1:
        return kind_of(t[1:]) if os.path.basename(t[1]) == "tide-ci" else None
    if base == "tide-ci":
        return KIND_HEAVY
    if base == "uv" and len(t) > 2 and t[1] == "run":
        return kind_of(t[2:])
    args: Optional[List[str]] = None
    if base in ("pytest", "py.test"):
        args = t[1:]
    elif base.startswith("python"):
        for i in range(1, len(t) - 1):
            if t[i] == "-m":
                if t[i + 1] == "pytest":
                    args = t[i + 2:]
                break
            if not t[i].startswith("-"):
                break
    if args is not None and pytest_is_full(args):
        return KIND_HEAVY
    return None


# --- проект и журнал ----------------------------------------------------------

def project_of(cwd: Optional[str] = None, home: Optional[Path] = None) -> Optional[str]:
    """Проект сессии: ``$AO_PROJECT_ID`` (ключ доски — id проекта AO), иначе имя по
    ростеру для корня tide под *cwd* (worktree AO сводится к main)."""
    env = (os.environ.get("AO_PROJECT_ID") or "").strip()
    if env:
        return env
    try:
        from . import paths, roster
        root = paths.find_tide_root(Path(cwd) if cwd else None)
        if root is None:
            return None
        return roster.name_for_path(root, home)
    except Exception:  # noqa: BLE001
        return None


def _log_path(home: Optional[Path]) -> Optional[Path]:
    if home is None:
        return None
    from . import paths
    return paths.state_dir(Path(home)) / LOG


def _recent(path: Path, key: str, now: datetime, minutes: int) -> bool:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - LOG_TAIL_BYTES))
            lines = fh.read().decode("utf-8", "ignore").splitlines()
    except OSError:
        return False
    for ln in reversed(lines):
        stamp, _, rest = ln.partition(" · ")
        if not rest.startswith(key + " · ") and rest != key:
            continue
        when = _iso(stamp)
        if when is not None and (now - when).total_seconds() < minutes * 60:
            return True
        return False
    return False


def journal(home: Optional[Path], session: str, project: Optional[str], kind: str,
            code: str, text: str, *, now: Optional[datetime] = None,
            repeat_min: int = REPEAT_MIN) -> Optional[str]:
    """Строка решения в ``state/throttle.log``; повтор того же (сессия, проект, вид,
    код) свежее *repeat_min* мин — не пишется. Никогда не бросает."""
    try:
        path = _log_path(home)
        if path is None:
            return None
        now = _now(now)
        key = " · ".join([session or "-", project or "-", kind or "-", code])
        if _recent(path, key, now, repeat_min):
            return None
        line = "{0} · {1} · {2}".format(now.astimezone().isoformat(timespec="seconds"),
                                         key, " ".join(str(text).split()))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return line
    except Exception:  # noqa: BLE001
        return None


def gate(kinds: List[Tuple[str, str]], *, session: str = "", cwd: Optional[str] = None,
         home: Optional[Path] = None, now: Optional[datetime] = None,
         live: Optional[Callable[[], Optional[int]]] = None) -> Optional[str]:
    """Гейт хука: *kinds* — [(вид, звено команды)]. Отказ → текст причины, иначе None.
    Никогда не бросает (любой сбой — пропуск)."""
    try:
        home = home if home is not None else _home()
        st, bad, _path = load(home)
        project = project_of(cwd, home)
        session = (os.environ.get("AO_SESSION_ID") or "").strip() or session
        if st is None:
            for kind, _seg in kinds:
                journal(home, session, project, kind, "nofile",
                        "пропуск — без ограничений: {0}".format(bad), now=now)
            return None
        if bad:
            journal(home, session, project, "-", "badfield", bad, now=now)
        for kind, seg in kinds:
            v = verdict(st, project, kind, now=now, live=live)
            if not v.allow:
                journal(home, session, project, kind, v.code,
                        "отказ `{0}` — {1}".format(seg[:120], v.reason), now=now)
                return v.reason
            if kind == KIND_SPAWN and v.reason:
                journal(home, session, project, kind, v.code,
                        "пропуск `{0}` — {1}".format(seg[:120], v.reason), now=now)
        return None
    except Exception:  # noqa: BLE001 — троттлинг не ломает хук
        return None


# --- tide throttle status / check ---------------------------------------------

def status_line(home: Optional[Path] = None, *, now: Optional[datetime] = None,
                live: Optional[Callable[[], Optional[int]]] = None,
                project: Optional[str] = None) -> str:
    """Одна строка: пауза/нет до когда, потолок, живых сейчас, окно %, файл и свежесть."""
    now = _now(now)
    home = home if home is not None else _home()
    st, bad, path = load(home)
    where = str(path) if path is not None else "—"
    try:
        where = "~/" + str(Path(where).relative_to(Path.home()))
    except ValueError:
        pass
    n = (live or live_agents)()
    alive = "живых сейчас {0}".format(n if n is not None else "? (AO молчит)")
    if st is None:
        return "tide throttle · без ограничений ({0}) · {1} · файл {2}".format(bad, alive, where)
    parts = ["tide throttle"]
    on_all = paused(st, None, now)
    if on_all:
        parts.append("пауза всех до {0}".format(hhmm(st["until"], now))
                     if st["until"] else "пауза всех без срока")
    else:
        mine = [p for p in st["paused_projects"] if paused(st, p, now)]
        if mine:
            until = " до {0}".format(hhmm(st["until"], now)) if st["until"] else " без срока"
            parts.append("пауза: {0}{1}".format(", ".join(mine), until))
        else:
            parts.append("работа идёт")
    top = ceiling(st)
    if top is None:
        parts.append("потолка нет")
    else:
        auto = st.get("ceiling_auto")
        src = " (ручной {0}, авто {1})".format(st.get("ceiling") if st.get("ceiling") is not None
                                              else "—", auto if auto is not None else "—")
        full = " — новые не стартуют" if n is not None and n >= top else ""
        parts.append("потолок {0}{1}".format(top, src))
        alive += full
    parts.append(alive)
    if st.get("window_pct") is None:
        wp = "окно —%"
    else:
        wp = "окно {0:.0f}%".format(st["window_pct"])
    wp += " (порог {0:.0f}%".format(st["auto_pct"])
    if st.get("window_until"):
        wp += ", сброс {0}".format(hhmm(st["window_until"], now))
    wp += ")"
    if window_hot(st, now):
        wp += " — тяжёлые не начинаются"
    parts.append(wp)
    fresh = "файл {0}".format(where)
    if st.get("at") is not None:
        age = int((now - st["at"]).total_seconds() // 60)
        fresh += " · at {0}".format(hhmm(st["at"], now))
        if age >= STALE_MIN:
            fresh += " (доска не обновляла файл {0} мин)".format(age)
    else:
        fresh += " · at —"
    if bad:
        fresh += " · {0}".format(bad)
    parts.append(fresh)
    return " · ".join(parts)


def _cmd_status(args) -> int:
    print(status_line())
    return 0


def _cmd_check(args) -> int:
    st, bad, _ = load()
    project = args.project or project_of()
    v = verdict(st, project, args.kind)
    if v.allow:
        print("можно ({0}{1})".format(args.kind, " · " + v.reason if v.reason else
                                      (" · без ограничений: " + bad if st is None else "")))
        return 0
    print(v.reason)
    return 1


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "throttle",
        help="пауза до сброса, потолок агентов, правило 85%% — как их видит движок (SOC-166)")
    sub = p.add_subparsers(dest="throttle_cmd")
    s = sub.add_parser("status", help="одна строка: пауза, потолок, живых, окно, свежесть файла")
    s.set_defaults(func=_cmd_status, _cmd="throttle status")
    c = sub.add_parser("check", help="можно ли сейчас запуск вида spawn|heavy|wake (код 1 — "
                                     "нельзя; wake — автопинок SOC-161, держит только пауза)")
    c.add_argument("--kind", choices=(KIND_SPAWN, KIND_HEAVY, KIND_WAKE), default=KIND_SPAWN)
    c.add_argument("--project", help="проект (по умолчанию — $AO_PROJECT_ID / cwd)")
    c.set_defaults(func=_cmd_check, _cmd="throttle check")
    p.set_defaults(func=_cmd_status, _cmd="throttle status")


__all__ = ["FILE", "KIND_HEAVY", "KIND_SPAWN", "KIND_WAKE", "LOG", "Verdict", "ceiling", "gate",
           "journal", "kind_of", "live_agents", "load", "parse", "paused", "project_of",
           "pytest_is_full", "register", "status_line", "strip_heredocs", "verdict",
           "window_hot"]

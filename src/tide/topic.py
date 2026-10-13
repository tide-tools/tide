"""SOC-270: ``tide topic …`` — темы Telegram одной командой (обёртка над ``pult.py topic-*``).

    tide topic new <группа> <название> --to <проект/нить>
    tide topic route <группа> <тема> --to <проект/нить>
    tide topic rename <группа> <тема> <новое>
    tide topic close <группа> <тема>
    tide topic reopen <группа> <тема>
    tide topic ls [<группа>]

<группа> — ``topics`` (= пульт factory, «tide topics») | ``agents`` (= pulse, «tide agents»)
или имя пульта как есть (factory, pulse, groovamp, nightshift). <тема> — id или заголовок.
Сама работа — в пульте ao-control (``pult.py topic-new|route|rename|close|reopen|ls --json``):
Telegram, ``role-topics.json`` под замком с бэкапом. Здесь — разбор, вызов и человеческий вывод.

Плюс ``tide spark … --topic [<группа>]`` (:func:`register_spark_topic`): после удачного
spark нити — тема с названием нити и маршрутом на неё (группа по умолчанию ``agents``).
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional

from . import slug, tg_pult
from .tg_pult import PultError

SPARK_DEFAULT_GROUP = "agents"
TIMEOUT_TG = 120.0      # topic-new/rename/close ходят в Telegram
TIMEOUT_LOCAL = 30.0    # route/ls — только файлы


def call(verb: str, flags: List[str], *, telegram: bool = True) -> Any:
    """``pult.py topic-<verb> <flags> --json`` → ``result``; ``ok: false`` — :class:`PultError`."""
    d = tg_pult.pult_dir()
    cmd = tg_pult.pult_python(d) + ["topic-" + verb] + flags + ["--json"]
    proc = tg_pult.run(cmd, cwd=d, timeout=TIMEOUT_TG if telegram else TIMEOUT_LOCAL)
    rec: Optional[Dict[str, Any]] = None
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                rec = json.loads(line)
                break
            except ValueError:
                continue
    if rec is None:
        why = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise PultError("topic {0}: пульт не дал ответа (код {1}){2}".format(
            verb, proc.returncode, ": " + why[-1] if why else ""))
    if not rec.get("ok"):
        raise PultError("topic {0}: {1}".format(verb, rec.get("error") or "отказ пульта"))
    return rec.get("result")


def _route(to: str) -> str:
    r = str(to or "").strip().strip("/")
    if not r or "/" not in r:
        raise PultError("--to: нужен <проект>/<нить> (например management/signing), получено {0!r}".format(to))
    return r


def new(group: str, title: str, to: str, *, dry: bool = False) -> Dict[str, Any]:
    g = tg_pult.group(group)
    flags = ["--group", g, "--title", title, "--route", _route(to)] + (["--dry-run"] if dry else [])
    return call("new", flags, telegram=not dry)


def route(group: str, topic: str, to: str) -> Dict[str, Any]:
    return call("route", ["--group", tg_pult.group(group), "--topic", str(topic), "--route", _route(to)],
                telegram=False)


def rename(group: str, topic: str, new_title: str, *, dry: bool = False) -> Dict[str, Any]:
    flags = ["--group", tg_pult.group(group), "--topic", str(topic), "--title", new_title]
    return call("rename", flags + (["--dry-run"] if dry else []))


def set_closed(group: str, topic: str, closed: bool, *, dry: bool = False) -> Dict[str, Any]:
    flags = ["--group", tg_pult.group(group), "--topic", str(topic)]
    return call("close" if closed else "reopen", flags + (["--dry-run"] if dry else []))


def ls(group: Optional[str] = None) -> List[Dict[str, Any]]:
    return call("ls", ["--group", tg_pult.group(group)] if group else [], telegram=False) or []


# --- вывод --------------------------------------------------------------------

def _bak(res: Dict[str, Any]) -> str:
    return " · бэкап {0}".format(res["backup"]) if res.get("backup") else ""


def render(verb: str, res: Any) -> str:
    if verb == "ls":
        if not res:
            return "topic ls: тем нет"
        lines = []
        for r in res:
            via = " (маршрут группы)" if r.get("route_via") == "group" else ""
            shut = "  [закрыта]" if r.get("closed") else ""
            lines.append("{0}\t{1}\t{2}\t→ {3}{4}{5}".format(
                tg_pult.GROUP_TITLES.get(r.get("group"), r.get("group")), r.get("id"), r.get("title"),
                r.get("route") or "-", via, shut))
        return "\n".join(lines)
    where = tg_pult.group_label(str(res.get("group") or ""))
    if verb == "new":
        if res.get("id") is None:
            return "topic new: {0}".format(res.get("how") or "dry-run")
        return "topic new: «{0}» в {1} · id {2} · {3} · маршрут → {4}{5}".format(
            res.get("title"), where, res.get("id"), res.get("how"), res.get("route"), _bak(res))
    if verb == "route":
        return "topic route: «{0}» (id {1}) в {2}: {3} → {4}{5}".format(
            res.get("title"), res.get("id"), where, res.get("old") or "-", res.get("route"), _bak(res))
    if verb == "rename":
        return "topic rename: id {0} в {1}: «{2}» → «{3}» ({4}){5}".format(
            res.get("id"), where, res.get("old"), res.get("title"), res.get("how"), _bak(res))
    return "topic {0}: «{1}» (id {2}) в {3} — {4}{5}".format(
        verb, res.get("title"), res.get("id"), where, res.get("how"), _bak(res))


# --- CLI ----------------------------------------------------------------------

USAGE = ("tide topic new <группа> <название> --to <проект/нить> | route <группа> <тема> --to <проект/нить> | "
         "rename <группа> <тема> <новое> | close|reopen <группа> <тема> | ls [<группа>]")

_ARITY = {"new": 2, "route": 2, "rename": 3, "close": 2, "reopen": 2}


def run_cli(args) -> int:
    verb = args.verb
    rest = list(args.rest or [])
    dry = bool(getattr(args, "dry_run", False))
    to = getattr(args, "to", None)
    try:
        if verb == "ls":
            if len(rest) > 1:
                raise PultError("topic ls [<группа>]")
            res: Any = ls(rest[0] if rest else None)
        else:
            n = _ARITY[verb]
            if len(rest) < n:
                raise PultError("мало аргументов: " + USAGE)
            if verb in ("new", "route") and not to:
                raise PultError("topic {0}: нужен --to <проект/нить>".format(verb))
            # название/новое заголовок могут прийти несколькими словами без кавычек
            head, tail = rest[:n - 1], " ".join(rest[n - 1:])
            if verb == "new":
                res = new(head[0], tail, to, dry=dry)
            elif verb == "route":
                res = route(head[0], tail, to)
            elif verb == "rename":
                res = rename(head[0], head[1], tail, dry=dry)
            else:
                res = set_closed(head[0], tail, verb == "close", dry=dry)
    except PultError as exc:
        print("tide: {0}".format(exc), file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(render(verb, res))
    return 0


def register(sub) -> None:
    ap = sub.add_parser("topic", help="темы Telegram: new | route | rename | close | reopen | ls (SOC-270, через пульт)",
                        description=USAGE)
    ap.add_argument("verb", choices=("new", "route", "rename", "close", "reopen", "ls"))
    ap.add_argument("rest", nargs="*", help="<группа> <тема|название> [<новое>]; группа: topics | agents | имя пульта")
    ap.add_argument("--to", default=None, metavar="ПРОЕКТ/НИТЬ", help="маршрут темы (new, route)")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="Telegram и role-topics.json не трогать (new, rename, close, reopen)")
    ap.add_argument("--json", action="store_true", help="машинный вывод (result пульта)")
    ap.set_defaults(func=run_cli, _cmd="topic")


# --- tide spark --topic ---------------------------------------------------------

def spark_topic_target(args) -> Optional[Dict[str, str]]:
    """{group, title, route} темы для spark или None (флага нет / нити не назвали)."""
    grp = getattr(args, "topic", None)
    if not grp:
        return None
    new_thread = (getattr(args, "new_thread", None) or "").strip()
    thread = (getattr(args, "thread", None) or "").strip()
    if new_thread:
        th_slug, title = slug.slugify(new_thread), new_thread
    elif thread:
        th_slug = slug.entry_slug(thread)
        title = th_slug
    else:
        return None
    return {"group": grp, "title": " ".join(title.split()), "route": "{0}/{1}".format(args.project, th_slug)}


def register_spark_topic(sub) -> None:
    """Научить ``tide spark`` флагу ``--topic [<группа>]`` (после удачного spark — ``topic new``)."""
    sp = sub.choices.get("spark")
    if sp is None:
        return
    sp.add_argument("--topic", nargs="?", const=SPARK_DEFAULT_GROUP, default=None, metavar="ГРУППА",
                    help="после spark завести тему Telegram с названием нити и маршрутом на неё "
                         "(группа: agents по умолчанию | topics | имя пульта; SOC-270)")
    prev = sp.get_default("func")

    def _dispatch(args) -> int:
        rc = int(prev(args) or 0)
        tgt = spark_topic_target(args)
        if rc != 0 or tgt is None:
            return rc
        try:
            res = new(tgt["group"], tgt["title"], tgt["route"], dry=bool(getattr(args, "dry_run", False)))
        except PultError as exc:
            print("tide: spark --topic: нить поднята, тема не заведена: {0}".format(exc), file=sys.stderr)
            return 1
        print("tide: spark --topic: " + render("new", res))
        return 0

    sp.set_defaults(func=_dispatch)

"""tide.truth_tg — тема Telegram проекта одной командой: ответы Гриши и открытые вопросы.

    tide truth tg [проект|slug|проект/нить] [--limit N] [--json]

Только чтение. Журналы тем пишет пульт менеджера (только дописывает) в
``<корень management>/.tide/tg-threads/``: ``index.json`` (ключ
``"<group>:<topic_id>"`` → ``{slug, name, project, tide_thread, soc_default}``)
и по файлу ``<slug>.jsonl`` на тему. Строки журнала:

* сообщения — ``dir: in`` (Гриша, ``tg_msg_id``) / ``out`` (мы, ``tg_msg_ids``),
  ``text`` дословно, ``soc`` + ``soc_src``, у входящих ``q_ref`` + ``q_ref_how``
  (к какому нашему вопросу), у наших вопросов — ``q_state: open``;
* ``kind: edit`` — новый ``text`` того же сообщения; ``kind: delete`` — удалено;
* ``kind: link`` — задним числом ``soc`` / ``q_ref`` к сообщению;
* ``kind: q_state`` — ``{tg_msg_id вопроса, q_state open|answered, answered_by}``;
* ``kind: thread_close`` — тема закрыта.

Правки применяются по порядку строк файла: последняя главнее. Неизвестные
``kind`` без ``dir`` пропускаются.

Без аргумента — темы проекта, в корне которого стоит cwd (AO-worktree сводится к
main, как у всего ``tide truth``); из management или вне проектов — все темы.
Каталог журналов: ``$TIDE_TG_THREADS`` → корень ``management`` из ростера +
:data:`THREADS_REL`. Время в выводе — по Гришиному поясу, −03.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import decisions_tg as dtg, paths, truth

THREADS_ENV = "TIDE_TG_THREADS"
THREADS_REL = ".tide/tg-threads"
INDEX_FILE = "index.json"
DEFAULT_LIMIT = 10
ANSWER_CHARS = 300
QUESTION_CHARS = 120
TZ = timezone(timedelta(hours=-3))
TZ_LABEL = "−03"

HOW_WORD = {"reply": "реплай", "loose": "по времени", "link": "привязано вручную"}
SOC_SRC_WORD = {"text": "в тексте", "q_ref": "от вопроса", "alert": "флаг алерта",
                "default": "по умолчанию темы", "link": "привязано вручную"}
_OPS = ("edit", "delete", "link", "q_state")


# --- где лежит --------------------------------------------------------------

def threads_dir(projs: List[Tuple[str, Path]]) -> Path:
    env = os.environ.get(THREADS_ENV)
    if env and env.strip():
        return Path(env).expanduser()
    return dtg.management_root(projs) / THREADS_REL


def read_index(d: Path) -> Optional[List[Dict]]:
    """Темы из ``index.json``: список ``{key, group, topic_id, slug, …}``; нет файла — None."""
    try:
        raw = json.loads((d / INDEX_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    out: List[Dict] = []
    for key, v in raw.items():
        if not isinstance(v, dict) or not v.get("slug"):
            continue
        group, _, tid = str(key).partition(":")
        out.append({
            "key": key, "group": group, "topic_id": int(tid) if tid.isdigit() else tid,
            "slug": v["slug"], "name": v.get("name") or v["slug"],
            "project": v.get("project"), "tide_thread": v.get("tide_thread"),
            "soc_default": list(v.get("soc_default") or []),
        })
    return out


# --- разбор журнала темы ----------------------------------------------------

def _ids(r: Dict) -> List[int]:
    ids = r.get("tg_msg_ids")
    if isinstance(ids, list):
        return [n for n in ids if dtg._is_int(n)]
    n = r.get("tg_msg_id")
    return [n] if dtg._is_int(n) else []


def parse(path: Path) -> Dict:
    """Сообщения темы с применёнными edit/delete/link/q_state, по порядку строк."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"exists": False, "msgs": [], "closed": None}
    msgs: List[Dict] = []
    by_id: Dict[int, Dict] = {}
    ops: List[Tuple[int, Dict]] = []
    closed = None
    for n, ln in enumerate(lines, 1):
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        k, d = r.get("kind"), r.get("dir")
        if k in _OPS:
            ops.append((n, r))
            continue
        if k == "thread_close":
            closed = r.get("at") or r.get("ts")
            continue
        if d not in ("in", "out"):
            continue  # неизвестное — пропускаем
        ids = _ids(r)
        m = {
            "dir": d, "kind": k, "tg": ids, "group": r.get("group"),
            "at": r.get("at") or r.get("ts") or "", "text": r.get("text") or "",
            "html": r.get("format") == "html", "voice": bool(r.get("voice")),
            "reply_to_tg": r.get("reply_to_tg"),
            "soc": list(r.get("soc") or []), "soc_src": r.get("soc_src"),
            "q_ref": r.get("q_ref"), "q_ref_how": r.get("q_ref_how"),
            "q_state": r.get("q_state"), "answered_by": None,
            "line": n, "edited": [], "linked": [], "deleted": False,
        }
        msgs.append(m)
        for i in ids:
            by_id.setdefault(i, m)
    for n, r in ops:
        k = r.get("kind")
        for i in _ids(r):
            m = by_id.get(i)
            if m is None:
                continue
            if k == "edit":
                if r.get("text") is not None:
                    m["text"] = r["text"]
                m["edited"].append(n)
            elif k == "delete":
                m["deleted"] = True
            elif k == "link":
                if r.get("soc"):
                    m["soc"], m["soc_src"] = list(r["soc"]), "link"
                if r.get("q_ref") is not None:
                    m["q_ref"] = r["q_ref"]
                    m["q_ref_how"] = r.get("q_ref_how") or "link"
                m["linked"].append(n)
            elif k == "q_state":
                m["q_state"] = r.get("q_state")
                m["answered_by"] = r.get("answered_by")
    return {"exists": True, "msgs": msgs, "by_id": by_id, "closed": closed}


def _when(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return "?"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ).strftime("%d.%m %H:%M")


def _key(m: Dict) -> Tuple[float, int, int]:
    return (dtg._ts_key(m["at"]), m["tg"][0] if m["tg"] else 0, m["line"])


def _text(m: Dict, n: int) -> str:
    return dtg.short(dtg.clean_text(m["text"], m["html"]), n)


def _link(group: Optional[str], tg: List[int]) -> str:
    return "tg:{0}/{1}".format(group or "?", tg[0] if tg else "?")


def topic_view(t: Dict, d: Path, limit: int) -> Dict:
    """Одна тема: шапка, открытые вопросы, последние *limit* ответов Гриши."""
    path = d / "{0}.jsonl".format(t["slug"])
    p = parse(path)
    view = dict(t, file=str(path), exists=p["exists"], closed=p["closed"],
                questions=[], answers=[], answers_total=0)
    if not p["exists"]:
        return view
    by_id = p["by_id"]
    live = [m for m in p["msgs"] if not m["deleted"]]
    group = t["group"]
    for m in sorted(live, key=_key, reverse=True):
        if m["dir"] == "out" and m["q_state"] == "open":
            view["questions"].append({
                "tg": m["tg"][0] if m["tg"] else None, "link": _link(m["group"] or group, m["tg"]),
                "at": m["at"], "when": _when(m["at"]), "text": _text(m, QUESTION_CHARS),
                "soc": m["soc"], "where": "{0}:{1}".format(path, m["line"]),
            })
    answers = [m for m in live if m["dir"] == "in"]
    view["answers_total"] = len(answers)
    for m in sorted(answers, key=_key, reverse=True)[:limit]:
        q = m["q_ref"]
        qm = by_id.get(q) if dtg._is_int(q) else None
        view["answers"].append({
            "tg": m["tg"][0] if m["tg"] else None, "link": _link(m["group"] or group, m["tg"]),
            "at": m["at"], "when": _when(m["at"]), "kind": m["kind"], "voice": m["voice"],
            "text": _text(m, ANSWER_CHARS), "edited": bool(m["edited"]),
            "q_ref": q, "q_ref_how": m["q_ref_how"],
            "question": _text(qm, QUESTION_CHARS) if qm else None,
            "question_state": qm["q_state"] if qm else None,
            "soc": m["soc"], "soc_src": m["soc_src"],
            "where": "{0}:{1}".format(path, m["line"]),
            "also": ["{0}:{1}".format(path, n) for n in m["edited"] + m["linked"]],
        })
    return view


# --- выбор тем --------------------------------------------------------------

def _current(projs: List[Tuple[str, Path]]) -> Optional[str]:
    cur = truth.current_project(projs)
    if cur is None:
        root = paths.find_tide_root()
        cur = root.name if root is not None else None
    return cur


def pick(topics: List[Dict], target: Optional[str], cur: Optional[str]) -> Tuple[List[Dict], str]:
    """Темы под запрос и подпись «что выбрано». Ничего не нашлось по запросу — TruthError."""
    if not target:
        if cur and cur != dtg.MANAGEMENT:
            return [t for t in topics if t["project"] == cur], "проект {0}".format(cur)
        return topics, "все темы"
    q = truth.norm(target.strip().strip("/"))
    if "/" in q:
        proj, _, thread = q.partition("/")
        hits = [t for t in topics
                if truth.norm(t["tide_thread"] or "") == q
                or (truth.norm(t["project"] or "") == proj
                    and thread in (truth.norm(t["slug"]),
                                   truth.norm((t["tide_thread"] or "").partition("/")[2])))]
        if hits:
            return hits, target
    for field in ("slug", "project"):
        hits = [t for t in topics if truth.norm(t[field] or "") == q]
        if hits:
            return hits, ("проект {0}".format(target) if field == "project" else target)
    hits = [t for t in topics if q in truth.norm(t["name"]) or q in truth.norm(t["slug"])]
    if hits:
        return hits, target
    known = ", ".join("{0} ({1})".format(t["slug"], t["project"] or "—") for t in topics)
    raise truth.TruthError("tg: нет темы «{0}» — есть: {1}".format(target, known or "ничего"))


# --- вывод ------------------------------------------------------------------

def _soc(socs: List[str], src: Optional[str] = None) -> str:
    if not socs:
        return "без SOC"
    s = ", ".join(socs)
    return "{0} ({1})".format(s, SOC_SRC_WORD[src]) if src in SOC_SRC_WORD else s


def render_topic(v: Dict) -> str:
    head = "{0} · {1} · нить {2} · SOC по умолчанию: {3}".format(
        v["name"], v["slug"], v["tide_thread"] or "—", ", ".join(v["soc_default"]) or "—")
    out = [head, "  журнал: {0}".format(v["file"])]
    if v["closed"]:
        out.append("  тема закрыта {0}".format(_when(v["closed"])))
    if not v["exists"]:
        out.append("  журнала нет — в теме ещё ничего не записано")
        return "\n".join(out)
    qs = v["questions"]
    out.append("  открытые вопросы — {0}{1}".format(len(qs), ":" if qs else ""))
    for q in qs:
        out.append("    {0}  {1}  {2}  «{3}»  {4}".format(
            q["when"], q["link"], _soc(q["soc"]), q["text"], q["where"]))
    ans = v["answers"]
    if not ans:
        out.append("  ответов Гриши нет")
        return "\n".join(out)
    out.append("  ответы Гриши — {0} из {1}, новые сверху (время {2}):".format(
        len(ans), v["answers_total"], TZ_LABEL))
    for a in ans:
        mark = (" (голос)" if a["voice"] else "") + (" (правлено)" if a["edited"] else "")
        out.append("    {0}  «{1}»{2}".format(a["when"], a["text"], mark))
        if a["q_ref"] is not None:
            q = "к вопросу {0} ({1})".format(a["q_ref"], HOW_WORD.get(a["q_ref_how"],
                                                                     a["q_ref_how"] or "?"))
            if a["question"]:
                q += " «{0}»".format(dtg.short(a["question"], 60))
            if a["question_state"] == "answered":
                q += ", вопрос отвечен"
            elif a["question_state"] == "open":
                q += ", вопрос ещё открыт"
        else:
            q = "без вопроса"
        out.append("      ↳ {0} · {1} · {2} · {3}".format(
            q, _soc(a["soc"], a["soc_src"]), a["link"], a["where"]))
    return "\n".join(out)


def cmd_tg(args) -> int:
    """``tide truth tg [проект|slug|проект/нить] [--limit N] [--json]``."""
    projs = truth.resolve_projects(None)
    d = threads_dir(projs)
    topics = read_index(d)
    as_json = getattr(args, "as_json", False)
    if topics is None:
        msg = "tg: нет тем Telegram — {0} не найден (${1})".format(d / INDEX_FILE, THREADS_ENV)
        print(json.dumps([], ensure_ascii=False) if as_json else msg)
        return 0
    target = getattr(args, "tg_target", None) or getattr(args, "project", None)
    chosen, label = pick(topics, target, None if target else _current(projs))
    limit = DEFAULT_LIMIT if getattr(args, "limit", None) is None else max(1, int(args.limit))
    views = [topic_view(t, d, limit) for t in chosen]
    if as_json:
        print(json.dumps(views, ensure_ascii=False, indent=1))
        return 0
    if not views:
        print("tg: нет тем в Telegram — {0} (темы: {1})".format(label, d / INDEX_FILE))
        return 0
    print("\n\n".join(render_topic(v) for v in views))
    return 0

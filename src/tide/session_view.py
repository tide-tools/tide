"""tide.session_view — быстрые команды головы менеджера про сессии (SOC-271).

Три вербa вместо цепочек ``ao session ls → ao session get → tide whois → grep arc.md``:

* ``tide idle`` — одна таблица живых ролей всех проектов (или ``--project``): AO id,
  «проект — нить · NN», сколько простаивает (``lastActivityAt`` AO), у кого мяч
  (:func:`tide.ball.side`), курсор паспорта, контекст (журнал AO, без него —
  транскрипт: :mod:`tide.context_fill`). Простаивающие дольше — сверху.
* ``tide why <AO id | проект/нить>`` — на чём стоит роль: паспорт, курсор, next,
  последний offload, открытый вопрос TG, хвост context.
* ``tide gc sessions`` — AO-сессии, заменённые хендоффом; по ``--yes`` —
  ``ao session kill``. Без ``--yes`` — только список с причинами.

Паспорт AO-сессии ищет :func:`tide.watch_stuck.locate` (claude-id → паспорт),
запасной путь — хвост ``title: … · <AO id>`` в открытых паспортах проекта.
Ведущая нити — :func:`tide.acts.head_session`, её AO — :func:`tide.arc.holder.ao_handle_for`.
Только чтение, кроме ``gc sessions --yes``.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from . import ball, fields, paths, roster, slug

# gc: моложе этого (минуты с последней активности AO) — не трогаем
GC_QUIET_MIN = 10
CTX_TAIL = 3

_Q_ID_RE = re.compile(r"(?<![\w-])q-[0-9a-f]{6,}(?![\w-])")
_AO_ID_RE = re.compile(r"^[A-Za-z0-9][\w.-]*-\d+$")


# --- общее ------------------------------------------------------------------------

def _when(raw) -> Optional[datetime]:
    from .watch_stuck import _when as w

    return w(raw)


def _ago(sec: Optional[float]) -> str:
    if sec is None:
        return "?"
    m = int(max(0, sec) // 60)
    if m < 60:
        return "{0}м".format(m)
    h, m = divmod(m, 60)
    if h < 48:
        return "{0}ч{1:02d}".format(h, m)
    return "{0}д{1}ч".format(h // 24, h % 24)


def _clip(text: str, n: int) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= n else one[: n - 1] + "…"


def _home(home: Optional[Path] = None) -> Optional[Path]:
    if home is not None:
        return Path(home)
    try:
        return paths.control_home()
    except Exception:  # noqa: BLE001
        return None


def _roster(home: Optional[Path]) -> List[Dict[str, str]]:
    if home is None:
        return []
    try:
        return [e for e in roster.read_roster(home) if e.get("status") != "archived"]
    except Exception:  # noqa: BLE001
        return []


def _project_of(root: Optional[Path], entries: Sequence[Dict[str, str]], fallback: str) -> str:
    if root is not None:
        r = Path(root).resolve()
        for e in entries:
            try:
                if Path(e["path"]).expanduser().resolve() == r:
                    return e["name"]
            except Exception:  # noqa: BLE001
                continue
        return r.name
    return fallback


def _root_of_project(name: str, entries: Sequence[Dict[str, str]]) -> Optional[Path]:
    e = next((x for x in entries if x.get("name") == name), None)
    return Path(e["path"]).expanduser() if e else None


def section(passport: Path, name: str) -> List[str]:
    """Непустые строки секции ``## <name>…`` паспорта (заглушки ``<…>`` не в счёт)."""
    try:
        text = Path(passport).read_text(encoding="utf-8")
    except OSError:
        return []
    out: List[str] = []
    inside = False
    for ln in text.splitlines():
        if ln.startswith("## "):
            if inside:
                break
            inside = ln[3:].strip().lower().startswith(name.lower())
            continue
        if inside and ln.strip() and not ln.strip().startswith("<"):
            out.append(ln.rstrip())
    return out


def current_ao(env: Optional[Dict[str, str]] = None) -> str:
    """AO-сессия этого процесса (``$AO_SESSION_ID``, иначе ``$AO_SESSION``)."""
    e = os.environ if env is None else env
    return (e.get("AO_SESSION_ID") or e.get("AO_SESSION") or "").strip()


def ao_rows() -> List[dict]:
    """Живые AO-сессии (``ao session ls --json``), без закрытых и вышедших."""
    from . import watch_stuck as ws

    return [s for s in ws.ao_sessions()
            if not s.get("isTerminated") and ws._activity(s) != "exited"]


def _by_title(handle: str, roots: Iterable[Path]) -> Optional[Path]:
    """Открытый паспорт с ``title: … · <handle>`` (свежий по mtime) — или None."""
    from . import resolve

    tail = " · " + handle
    best = None
    for root in roots:
        try:
            dirs = resolve.open_session_dirs(Path(root))
        except Exception:  # noqa: BLE001
            continue
        for entry in dirs:
            t = (fields.read_field(entry / "arc.md", "title") or "").strip()
            if t.endswith(tail):
                m = (entry / "arc.md").stat().st_mtime
                if best is None or m > best[0]:
                    best = (m, entry)
    return best[1] if best else None


def label_of(title: str, project: str, thread: str, entry: Optional[Path]) -> str:
    """«проект — нить · NN» из title паспорта (без хвоста AO id) или из папок."""
    t = (title or "").strip()
    if t and not t.startswith("<"):
        parts = t.split(" · ")
        if len(parts) >= 3 and _AO_ID_RE.match(parts[-1].strip()):
            parts = parts[:-1]
        return " · ".join(parts)
    nn = ""
    if entry is not None:
        m = re.match(r"^_*(\d+)-", entry.name)
        nn = m.group(1) if m else ""
    return "{0} — {1}{2}".format(project or "?", thread or "?", " · " + nn if nn else "")


def describe(s: dict, *, home: Optional[Path] = None,
             entries: Optional[Sequence[Dict[str, str]]] = None,
             locator: Optional[Callable[[dict], Dict]] = None,
             now: Optional[datetime] = None) -> Dict:
    """AO-сессия *s* → роль: AO-поля + паспорт (``entry``, ``passport``, ``cursor``…)."""
    from . import watch_stuck as ws

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    entries = list(entries if entries is not None else _roster(home))
    handle = str(s.get("id") or "")
    try:
        loc = (locator or ws.locate)(s) or {}
    except Exception:  # noqa: BLE001
        loc = {}
    entry = loc.get("entry")
    root = loc.get("root")
    if entry is None:
        proot = _root_of_project(str(s.get("projectId") or ""), entries)
        roots = [proot] if proot else [Path(e["path"]).expanduser() for e in entries]
        entry = _by_title(handle, roots)
        if entry is not None:
            root = paths.find_tide_root(entry) or root
    passport = Path(entry) / "arc.md" if entry is not None else None
    title = (fields.read_field(passport, "title") or "").strip() if passport else ""
    thread = loc.get("thread") or (slug.entry_slug(Path(entry).parent.parent.name)
                                   if entry is not None else "")
    project = _project_of(root, entries, str(s.get("projectId") or ""))
    cursor = loc.get("cursor") or (ball.cursor_of(passport) if passport else "")
    eta = (fields.read_field(passport, "eta") or "").strip() if passport else ""
    last = _when(s.get("lastActivityAt")) or _when(s.get("updatedAt"))
    activity = ws._activity(s)
    kind, who = ball.side(cursor, eta=eta)
    return {
        "ao_session": handle,
        "project": project,
        "project_id": str(s.get("projectId") or ""),
        "thread": thread,
        "title": title,
        "label": label_of(title, project, thread, Path(entry) if entry else None),
        "activity": activity,
        "status": str(s.get("status") or ""),
        "last_activity_at": s.get("lastActivityAt") or "",
        "created_at": s.get("createdAt") or "",
        "idle_sec": (now - last).total_seconds() if last else None,
        "entry": str(entry) if entry is not None else "",
        "passport": str(passport) if passport is not None else "",
        "root": str(root) if root else "",
        "cursor": cursor,
        "eta": eta,
        "ball": kind,
        "ball_label": who,
        "claude_session": loc.get("sid") or "",
        "transcript": str(loc.get("transcript") or ""),
    }


# --- контекст ---------------------------------------------------------------------

def context_of(ao_id: str, transcript: str = "") -> Optional[Dict]:
    """``{tokens, window, hard, left}``: журнал AO (живое число), без него — транскрипт.
    *left* — сколько осталось до жёсткого порога хендоффа (200k). None — неизвестно."""
    from . import context_fill
    from .hooks import context_watch

    used = window = 0
    j = context_fill.ao_journal_usage(ao_id)
    if j and j.get("used"):
        used, window = int(j["used"]), int(j.get("size") or 0)
    elif transcript:
        try:
            f = context_fill.context_fill(Path(transcript))
        except Exception:  # noqa: BLE001
            f = None
        if f:
            used, window = int(f.get("tokens") or 0), int(f.get("window") or 0)
    if not used:
        return None
    hard = context_watch.HARD_TOKENS
    return {"tokens": used, "window": window, "hard": hard, "left": hard - used}


def _k(n: int) -> str:
    return "{0}k".format(int(round(n / 1000.0)))


def ctx_label(c: Optional[Dict]) -> str:
    if not c:
        return "—"
    left = c["left"]
    return "{0} (ост {1})".format(_k(c["tokens"]), _k(left)) if left > 0 else \
        "{0} (за 200k)".format(_k(c["tokens"]))


# --- tide idle --------------------------------------------------------------------

def _match_project(role: Dict, project: str) -> bool:
    p = (project or "").strip()
    return not p or p in (role.get("project"), role.get("project_id"))


def idle(*, project: str = "", sessions: Optional[Sequence[dict]] = None,
         home: Optional[Path] = None, locator=None, now: Optional[datetime] = None,
         with_context: bool = True, context=None) -> List[Dict]:
    """Живые роли (по *project* или все) — простаивающие дольше сверху."""
    home = _home(home)
    entries = _roster(home)
    rows = list(ao_rows() if sessions is None else sessions)
    out = []
    for s in rows:
        if s.get("isTerminated"):
            continue
        r = describe(s, home=home, entries=entries, locator=locator, now=now)
        if not _match_project(r, project):
            continue
        if with_context:
            r["context"] = (context or context_of)(r["ao_session"], r["transcript"])
        out.append(r)
    out.sort(key=lambda r: (-(r["idle_sec"] if r["idle_sec"] is not None else -1),
                            r["ao_session"]))
    return out


def render_idle(rows: Sequence[Dict]) -> str:
    if not rows:
        return "tide idle: живых AO-сессий нет"
    head = ("AO id", "роль", "простой", "AO", "мяч", "контекст", "курсор")
    table = [head]
    for r in rows:
        table.append((r["ao_session"], _clip(r["label"], 42), _ago(r["idle_sec"]),
                      r["activity"] or "?", r["ball_label"], ctx_label(r.get("context")),
                      _clip(r["cursor"] or "—", 70)))
    widths = [max(len(t[i]) for t in table) for i in range(len(head) - 1)]
    lines = []
    for t in table:
        lines.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(t[:-1])) + "  " + t[-1])
    return "\n".join(lines)


# --- tide why ---------------------------------------------------------------------

def _open_tg(cursor: str) -> Dict:
    """Вопрос TG из курсора «мяч у пользователя: вопрос TG N» и отвечен ли он
    (журнал решений, :func:`tide.launcher.seed_compress.journal_question_nums`)."""
    text = " ".join(ball._unquoted(cursor or "").split())
    if not ball._USER_BALL_RE.search(text):
        return {}
    m = ball._TG_REF_RE.search(text)
    if not m:
        q = _Q_ID_RE.search(text)  # вопрос доски «q-…» — открытость по журналу не сверяем
        return {"tg": q.group(0) if q else "", "answered": False}
    num = re.sub(r"\D", "", m.group(0))
    try:
        from .launcher.seed_compress import journal_question_nums

        answered = num in journal_question_nums()
    except Exception:  # noqa: BLE001
        answered = False
    return {"tg": num, "answered": answered}


def why(ref: str, *, sessions: Optional[Sequence[dict]] = None,
        home: Optional[Path] = None, locator=None, now: Optional[datetime] = None) -> Dict:
    """На чём стоит роль *ref* (AO id или ``проект/нить``)."""
    from . import whois

    home = _home(home)
    entries = _roster(home)
    ref = (ref or "").strip()
    rows = list(ao_rows() if sessions is None else sessions)
    by_id = {str(s.get("id")): s for s in rows}
    role: Dict = {}
    if "/" not in ref and ref in by_id:
        role = describe(by_id[ref], home=home, entries=entries, locator=locator, now=now)
    elif "/" not in ref and _AO_ID_RE.match(ref):
        # AO-сессии нет среди живых — паспорт по хвосту title
        entry = _by_title(ref, [Path(e["path"]).expanduser() for e in entries])
        if entry is None:
            raise whois.WhoisError("why: AO-сессии {0!r} нет среди живых и паспорта с ней "
                                   "нет".format(ref))
        role = _role_from_entry(entry, entries, ao=ref, alive=False)
    else:
        rec = whois.thread(ref, home=home)
        project, _, _name = ref.rpartition("/")
        root = _root_of_project(project, entries) if project else paths.find_tide_root()
        if not rec.get("arc") or root is None:
            raise whois.WhoisError("why: у нити {0!r} нет сессий".format(ref))
        tname, sname = str(rec["arc"]).split("/", 1)
        entry = paths.arcs_dir(Path(root)) / tname / "arcs" / sname
        ao = rec.get("ao_session") or ""
        if ao in by_id:
            role = describe(by_id[ao], home=home, entries=entries, locator=locator, now=now)
            if role.get("entry") and Path(role["entry"]) != entry:
                role = {}  # AO id переиспользован — верим паспорту нити
        if not role:
            role = _role_from_entry(entry, entries, ao=ao, alive=ao in by_id)
    pp = Path(role["passport"]) if role.get("passport") else None
    role["next"] = section(pp, "next") if pp else []
    role["context_tail"] = section(pp, "context")[-CTX_TAIL:] if pp else []
    role["offloaded_at"] = (fields.read_field(pp, "offloaded-at") or "").strip() if pp else ""
    role["status_field"] = (fields.read_field(pp, "status") or "").strip() if pp else ""
    role["handed_off"] = (fields.read_field(pp, "handed-off") or "").strip() if pp else ""
    role["question"] = _open_tg(role.get("cursor", ""))
    role["open_questions"] = []
    if pp and role.get("root"):
        try:
            from .launcher.autohandoff import open_questions

            role["open_questions"] = open_questions(Path(role["root"]), pp.parent.parent.parent)
        except Exception:  # noqa: BLE001
            pass
    return role


def _role_from_entry(entry: Path, entries, *, ao: str, alive: bool) -> Dict:
    pp = Path(entry) / "arc.md"
    root = paths.find_tide_root(Path(entry))
    project = _project_of(root, entries, "")
    thread = slug.entry_slug(Path(entry).parent.parent.name)
    title = (fields.read_field(pp, "title") or "").strip()
    cursor = ball.cursor_of(pp)
    eta = (fields.read_field(pp, "eta") or "").strip()
    kind, who = ball.side(cursor, eta=eta)
    return {"ao_session": ao, "project": project, "thread": thread, "title": title,
            "label": label_of(title, project, thread, Path(entry)),
            "activity": "" if alive else "нет в ao session ls", "idle_sec": None,
            "entry": str(entry), "passport": str(pp), "root": str(root or ""),
            "cursor": cursor, "eta": eta, "ball": kind, "ball_label": who,
            "claude_session": (fields.read_field(pp, "claude-session") or "").strip(),
            "transcript": ""}


def render_why(r: Dict) -> str:
    q = r.get("question") or {}
    if q:
        qline = ("TG {0}{1}".format(q["tg"], " — отвечен (журнал решений)" if q.get("answered")
                                    else " — открыт") if q.get("tg")
                 else "мяч у пользователя без номера вопроса ⚠")
    else:
        qline = "—"
    if r.get("open_questions"):
        qline += " · артефакты-вопросы: " + "; ".join(r["open_questions"][:3])
    act = r.get("activity") or "?"
    if r.get("idle_sec") is not None:
        act += ", простой " + _ago(r["idle_sec"])
    lines = [
        "{0} · {1}".format(r.get("label") or "?", r.get("ao_session") or "AO ?"),
        "AO:        " + act,
        "паспорт:   " + (r.get("passport") or "не найден"),
        "статус:    " + (r.get("status_field") or "?")
        + (" · handed-off " + r["handed_off"] if r.get("handed_off") else ""),
        "мяч:       " + (r.get("ball_label") or "—"),
        "курсор:    " + (r.get("cursor") or "—"),
        "next:      " + (" / ".join(x.strip() for x in r.get("next") or []) or "—"),
        "offload:   " + (r.get("offloaded_at") or "—"),
        "вопрос TG: " + qline,
        "context (последние {0}):".format(CTX_TAIL),
    ]
    lines += ["  " + _clip(x, 300) for x in r.get("context_tail") or []] or ["  —"]
    return "\n".join(lines)


# --- tide gc sessions -------------------------------------------------------------

def _replaced_sign(role: Dict, home: Optional[Path]) -> str:
    """Явный признак, что сессию заменили: ``handed-off:`` в паспорте, закрытый
    паспорт, или её оффер принят (:func:`tide.handoff_queue.is_dissolved`)."""
    from . import sessions

    pp = Path(role["passport"])
    h = (fields.read_field(pp, "handed-off") or "").strip()
    if h:
        return "паспорт: handed-off " + h
    if sessions.is_closed_passport(pp):
        return "паспорт закрыт (status/__…__)"
    sid = role.get("claude_session") or ""
    if home is not None and sid:
        try:
            from . import handoff_queue as hq

            rec = hq.is_dissolved(home, sid)
        except Exception:  # noqa: BLE001
            rec = None
        if rec:
            return "оффер {0} принят".format(rec.get("name") or "?")
    return ""


def _busy(transcript: str) -> List[str]:
    if not transcript:
        return []
    try:
        from .arc.workers import background_shells
        from .launcher.seed_draft import background_workers

        tp = Path(transcript)
        return list(background_workers(tp)) + list(background_shells(tp))
    except Exception:  # noqa: BLE001
        return []


def _num(name: str) -> int:
    m = re.match(r"^_*(\d+)-", name or "")
    return int(m.group(1)) if m else -1


def gc_verdict(role: Dict, *, alive: Dict[str, Dict], home: Optional[Path],
               me: str, quiet_min: int = GC_QUIET_MIN, busy=None) -> Dict:
    """Закрыть ли AO-сессию роли *role* → ``{close, why, successor}``. *alive* —
    живые роли по AO id (для преемника)."""
    from . import acts
    from .arc import holder

    out = {"ao_session": role["ao_session"], "label": role.get("label", ""),
           "close": False, "why": "", "successor": ""}

    def keep(why: str) -> Dict:
        out["why"] = why
        return out

    if me and role["ao_session"] == me:
        return keep("текущая сессия")
    if not role.get("passport"):
        return keep("паспорт не найден — признака замены нет")
    sign = _replaced_sign(role, home)
    if not sign:
        return keep("нет явного признака замены")
    entry = Path(role["entry"])
    head = acts.head_session(entry.parent.parent)
    if head is None or head.resolve() == entry.resolve():
        return keep("ведущая сессия нити ({0})".format(sign))
    if _num(head.name) < _num(entry.name):
        return keep("ведущая нити {0} старше этой — не трогаю".format(head.name))
    head_sid = (fields.read_field(head / "arc.md", "claude-session") or "").strip()
    succ = holder.ao_handle_for(home, head_sid)
    if not succ:
        t = (fields.read_field(head / "arc.md", "title") or "")
        m = re.search(r" · (\S+-\d+)$", t)
        succ = m.group(1) if m else ""
    if not succ:
        return keep("у ведущей {0} нет AO-сессии — преемник не подтверждён".format(head.name))
    if succ == role["ao_session"]:
        return keep("ведущая {0} в той же AO-сессии".format(head.name))
    if succ not in alive:
        return keep("преемник {0} ({1}) не жив".format(succ, head.name))
    idle_sec = role.get("idle_sec")
    if idle_sec is None or idle_sec < quiet_min * 60:
        return keep("активность {0} назад (< {1} мин)".format(_ago(idle_sec), quiet_min))
    live = (busy or _busy)(role.get("transcript") or "")
    if live:
        return keep("живы помощники/фоновые команды ({0}): {1}".format(
            len(live), _clip(str(live[0]).lstrip("- "), 80)))
    out["close"] = True
    out["successor"] = succ
    out["why"] = "{0}; нить ведёт {1} ({2}); тихо {3}".format(
        sign, alive[succ].get("label") or head.name, succ, _ago(idle_sec))
    return out


def gc_sessions(*, project: str = "", sessions: Optional[Sequence[dict]] = None,
                home: Optional[Path] = None, locator=None, now: Optional[datetime] = None,
                me: Optional[str] = None, busy=None) -> List[Dict]:
    """Вердикт по каждой живой AO-сессии (*project* — только её проект)."""
    home = _home(home)
    roles = idle(project="", sessions=sessions, home=home, locator=locator, now=now,
                 with_context=False)
    alive = {r["ao_session"]: r for r in roles}
    me = current_ao() if me is None else me
    out = []
    for r in roles:
        if not _match_project(r, project):
            continue
        out.append(gc_verdict(r, alive=alive, home=home, me=me, busy=busy))
    out.sort(key=lambda v: (not v["close"], v["ao_session"]))
    return out


def gc_kill(verdicts: Sequence[Dict], *, home: Optional[Path] = None, kill=None) -> List[Dict]:
    """``ao session kill`` для вердиктов с ``close`` → то же с ``killed``/``kill_error``.
    След — ``state/autoclose.log`` (строка «gc-sessions»)."""
    from .hooks import autoclose

    home = _home(home)
    out = []
    for v in verdicts:
        if not v.get("close"):
            continue
        ok, err = (kill or autoclose._kill)(v["ao_session"])
        v = dict(v, killed=bool(ok), kill_error=err or "")
        if home is not None:
            autoclose.journal(home, "gc-sessions", "kill " + v["ao_session"],
                              v["why"], "ok" if ok else "НЕ ВЫШЛО: " + (err or "?"))
        out.append(v)
    return out


def render_gc(verdicts: Sequence[Dict], killed: Optional[Sequence[Dict]] = None,
              *, show_kept: bool = True) -> str:
    close = [v for v in verdicts if v["close"]]
    lines = ["закрыть ({0}):".format(len(close)) if killed is None
             else "закрыто ({0}):".format(sum(1 for k in killed if k.get("killed")))]
    done = {k["ao_session"]: k for k in killed or []}
    for v in close:
        mark = ""
        if killed is not None:
            k = done.get(v["ao_session"], {})
            mark = "  [kill ok]" if k.get("killed") else "  [kill НЕ ВЫШЕЛ: {0}]".format(
                k.get("kill_error") or "?")
        lines.append("  {0}  {1} — {2}{3}".format(v["ao_session"], v["label"], v["why"], mark))
    if not close:
        lines.append("  —")
    if show_kept:
        kept = [v for v in verdicts if not v["close"]]
        lines.append("оставить ({0}):".format(len(kept)))
        for v in kept:
            lines.append("  {0}  {1} — {2}".format(v["ao_session"], v["label"], v["why"]))
    if killed is None and close:
        lines.append("закрыть: tide gc sessions --yes")
    return "\n".join(lines)


# --- CLI --------------------------------------------------------------------------

def _jsonable(rows):
    return json.dumps(rows, ensure_ascii=False, indent=1, default=str)


def cmd_idle(args) -> int:
    rows = idle(project=getattr(args, "project", "") or "",
                with_context=not getattr(args, "no_context", False))
    print(_jsonable(rows) if getattr(args, "as_json", False) else render_idle(rows))
    return 0


def cmd_why(args) -> int:
    r = why(args.target)
    print(_jsonable(r) if getattr(args, "as_json", False) else render_why(r))
    return 0


def cmd_gc_sessions(args) -> int:
    verdicts = gc_sessions(project=getattr(args, "project", "") or "")
    killed = gc_kill(verdicts) if getattr(args, "yes", False) else None
    if getattr(args, "as_json", False):
        print(_jsonable({"verdicts": verdicts, "killed": killed}))
    else:
        print(render_gc(verdicts, killed, show_kept=not getattr(args, "quiet", False)))
    if killed and not all(k.get("killed") for k in killed):
        return 1
    return 0


def register(subparsers) -> None:
    """``tide idle``, ``tide why``, ``tide gc sessions``."""
    p = subparsers.add_parser("idle", help="live roles across the roster: idle time, "
                                           "ball, cursor, context (SOC-271)")
    p.add_argument("--project", default="", help="only this roster project / AO project")
    p.add_argument("--json", action="store_true", dest="as_json")
    p.add_argument("--no-context", action="store_true", help="skip context fill (faster)")
    p.set_defaults(func=cmd_idle, _cmd="idle")

    p = subparsers.add_parser("why", help="what a role is standing on: passport, cursor, "
                                          "next, last offload, TG question (SOC-271)")
    p.add_argument("target", help="AO id (management-53) or <project>/<thread>")
    p.add_argument("--json", action="store_true", dest="as_json")
    p.set_defaults(func=cmd_why, _cmd="why")

    p = subparsers.add_parser("gc", help="garbage-collect replaced things (SOC-271)")
    gsub = p.add_subparsers(dest="gc_what", metavar="<what>")
    g = gsub.add_parser("sessions", help="AO sessions replaced by a handoff: list; "
                                         "--yes kills them (ao session kill)")
    g.add_argument("--project", default="", help="only this project")
    g.add_argument("--yes", action="store_true", help="really kill the candidates")
    g.add_argument("--json", action="store_true", dest="as_json")
    g.add_argument("--quiet", action="store_true", help="only the candidates")
    g.set_defaults(func=cmd_gc_sessions, _cmd="gc sessions")
    p.set_defaults(func=lambda a: (p.print_help(), 0)[1], _cmd="gc")


__all__ = ["idle", "why", "gc_sessions", "gc_verdict", "gc_kill", "describe",
           "context_of", "register", "render_idle", "render_why", "render_gc"]

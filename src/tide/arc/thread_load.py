"""tide.arc.thread_load — нагрузка нити и не пора ли её делить (SOC-153).

    tide thread load [--project P] [--json] [--rule tuned|draft] [--linear PATH] [--now ISO]

Перенос прототипа continuity-audit (``.tide/arcs/32-@continuity-audit/arcs/
04-continuity-audit-4/workspace/load/thread_load.py``); контракт выхода ``--json`` —
``load-format.md`` там же (его читает доска, поля не менять). Только чтение.

По каждой живой нити (есть ведущая сессия, активность за 24 ч): контекст ведущей ·
передачи за 24 ч и медианный интервал · почта · задачи Linear в работе · живые
сабагенты ведущей · входящие поручения · открытые хвосты последней записки ·
застревание (SOC-157) · расход · сработавшие сигналы · флаг · предложение разделить.

Источники:
  офферы передач   <control-home>/.tide/handoffs/NNN-*.md ($TIDE_HOME, иначе ~/Documents/tide-home)
  нити и сессии    <проект>/.tide/arcs/NN-@нить/arcs/*/arc.md (claude-session) — ведущая = самая новая
  транскрипты      :func:`tide.context_fill.transcript_for`; контекст — :func:`tide.context_fill.fill_of`
  сабагенты        запуск Agent «Async agent launched … agentId» без итога; промежуточный итог =
                   жив (:func:`tide.arc.workers.interim_workers`)
  почта            <нить>/inbox/log.jsonl (sent / ack)
  Linear           кэш JSON (--linear, $TIDE_LINEAR_CACHE, иначе linear-cache.json прототипа), без
                   него — раздел «задачи Linear» последней записки передачи
  расход           cost-tree.json SOC-127 ($TIDE_COST_TREE, иначе путь прототипа)
--now — посчитать «как было» в прошлый момент (передачи, почта, контекст, входящие;
Linear и расход — текущие).
"""

from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import statistics
from pathlib import Path

from .. import context_fill
from . import workers as _workers

LOCAL = dt.datetime.now().astimezone().tzinfo
WINDOW_H = 24
_PROTO = "Documents/projects/tide-stack/.tide/arcs/32-@continuity-audit/arcs/"


def _home() -> str:
    return str(Path.home())


def handoffs_dir() -> str:
    th = (os.environ.get("TIDE_HOME") or "").strip()
    base = os.path.expanduser(th) if th else os.path.join(_home(), "Documents/tide-home")
    return os.path.join(base, ".tide", "handoffs")


def cost_tree_path() -> str:
    return (os.environ.get("TIDE_COST_TREE") or "").strip() or os.path.join(
        _home(), _PROTO + "03-continuity-audit-3/output/cost-tree.json")


def linear_cache_path() -> str:
    return (os.environ.get("TIDE_LINEAR_CACHE") or "").strip() or os.path.join(
        _home(), _PROTO + "04-continuity-audit-4/workspace/load/linear-cache.json")


def project_globs():
    h = _home()
    return [os.path.join(h, "Documents/*/.tide"), os.path.join(h, "Documents/projects/*/.tide")]


# Правила. Сигнал считается сработавшим, если значение > порога (для interval — меньше порога).
RULES = {
    "draft": {  # черновик из задачи: 2 из 4
        "need": 2,
        "signals": {
            "handoff_interval_h": ("<", 2.0),     # передаётся чаще раза в 2 ч
            "linear_in_progress": (">", 3),        # больше 3 задач в работе
            "mail_oldest_unacked_min": (">", 30),  # письмо без квитанции дольше 30 мин
            "spend_share_project": (">", 0.40),    # доля нити в расходе проекта
        },
    },
    "tuned": {  # пороги по цифрам 09.10 (thresholds.md): 2 из 6
        "need": 2,
        "signals": {
            "handoffs_24h": (">=", 8),             # 8+ передач за сутки — голова сжигает окно раз в 3 ч и чаще
            "linear_in_progress": (">=", 3),       # 3+ задачи нити в In Progress
            "mail_oldest_unacked_min": (">", 30),  # письмо без квитанции дольше 30 мин (09.10 не срабатывал)
            "spend_usd_per_h": (">=", 10.0),       # 10+ $/ч за последние 5 ч (нить со всеми сабагентами)
            "open_tails": (">=", 14),              # 14+ хвостов и шагов в последней записке передачи
            "stuck_markers": (">=", 1),            # SOC-157: стоит > 30 мин / повторяет шаг / упирается в роль (3 ч)
        },
    },
}


# ---------- время ----------

def parse_ts(s):
    """ISO с зоной (…Z/±hh:mm) или без зоны (местное) → aware datetime."""
    if not s:
        return None
    s = str(s).strip()
    try:
        if s.endswith("Z"):
            return dt.datetime.fromisoformat(s[:-1][:26]).replace(tzinfo=dt.timezone.utc)
        d = dt.datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=LOCAL)
    except ValueError:
        return None


def iso(d):
    return d.astimezone(LOCAL).isoformat(timespec="seconds") if d else None


# ---------- нити и сессии ----------

def read_fields(path, keys):
    out = {}
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for i, ln in enumerate(f):
                if i > 60 or ln.startswith("## "):
                    break
                k, _, v = ln.partition(":")
                if k.strip() in keys and k.strip() not in out:
                    out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def projects():
    out = {}
    for g in project_globs():
        for t in glob.glob(g):
            root = os.path.dirname(t)
            out.setdefault(os.path.basename(root), root)
    return out


def threads(project_root):
    for d in sorted(glob.glob(os.path.join(project_root, ".tide/arcs/[0-9]*-@*"))):
        name = os.path.basename(d)
        if name.startswith("__"):
            continue
        sessions = []
        for a in sorted(glob.glob(os.path.join(d, "arcs/[0-9]*"))):
            f = read_fields(os.path.join(a, "arc.md"), ("claude-session", "title", "auto-chain"))
            if f.get("claude-session"):
                try:
                    num = int(os.path.basename(a).split("-")[0])
                except ValueError:
                    continue
                sessions.append(dict(num=num, arc=os.path.basename(a), dir=a, sid=f["claude-session"],
                                     title=f.get("title", "")))
        if sessions:
            sessions.sort(key=lambda s: s["num"])
            yield name, d, sessions


_tr_cache = {}


def transcript(sid):
    """Транскрипт сессии *sid* (:func:`tide.context_fill.transcript_for`, свежий из нескольких)."""
    if sid not in _tr_cache:
        p = context_fill.transcript_for(sid or "")
        _tr_cache[sid] = str(p) if p else None
    return _tr_cache[sid]


_rows_cache = {}
_KEEP_USER = ("Async agent launched", "resumedAgentId", "task-notification", "hook error", "role-gate")


def rows(path):
    """Строки транскрипта, нужные сборщику. Тяжёлые строки с результатами инструментов разбираются,
    только если в них запуск/итог сабагента — так файл в десятки МБ читается за доли секунды."""
    if path not in _rows_cache:
        out = []
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                for ln in f:
                    if '"role":"assistant"' in ln:
                        pass
                    elif '"tool_result"' in ln and not any(k in ln for k in _KEEP_USER):
                        continue
                    elif '"role":"user"' not in ln and '"type":"attachment"' not in ln:
                        continue
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    if isinstance(r, dict):
                        out.append(r)
        except (OSError, TypeError):
            pass
        _rows_cache[path] = out
    return _rows_cache[path]


def context_at(path, now):
    """Заполнение окна на последнем ответе ведущей до *now* (тыс. токенов) и время этого ответа."""
    last = (None, None)
    for r in rows(path):
        if r.get("type") != "assistant" or r.get("isSidechain"):
            continue
        m = r.get("message") or {}
        if m.get("model") == "<synthetic>":
            continue
        t = parse_ts(r.get("timestamp"))
        if t is None or t > now:
            continue
        f = context_fill.fill_of(m.get("usage"))
        if f:
            last = (f, t)
    return last


_fl_cache = {}


def first_last(path):
    """Первая метка времени (первые строки файла) и последняя запись (mtime файла) — без полного чтения."""
    if path not in _fl_cache:
        first = None
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                for i, ln in enumerate(f):
                    if i > 50:
                        break
                    m = re.search(r'"timestamp"\s*:\s*"([^"]+)"', ln)
                    if m:
                        first = parse_ts(m.group(1))
                        break
            last = dt.datetime.fromtimestamp(os.path.getmtime(path), dt.timezone.utc)
        except OSError:
            last = None
        _fl_cache[path] = (first, last)
    return _fl_cache[path]


_LAUNCH = re.compile(r"Async agent launched.*?agentId:\s*([\w-]+)", re.S)
_RESUME = re.compile(r"\"resumedAgentId\"\s*:\s*\"([\w-]+)\"")
_NOTIF = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)
_TID = re.compile(r"<task-id>\s*([^<\s]+)\s*</task-id>")


def _text(content):
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if isinstance(b, dict):
            if b.get("type") == "text":
                out.append(b.get("text") or "")
            elif b.get("type") == "tool_result":
                c = b.get("content")
                out.append(c if isinstance(c, str) else _text(c))
    return "\n".join(out)


def live_workers(path, now):
    """Сабагенты ведущей, живые на момент *now*: [{"id","what","since"}].
    Запуск/возобновление — здесь; итог промежуточный или письмо после итога —
    :func:`tide.arc.workers.interim_workers` (один признак на весь tide)."""
    calls, runs, notified = {}, {}, set()
    src = rows(path)
    for r in src:
        t = parse_ts(r.get("timestamp"))
        if t is None or t > now:
            continue
        m = r.get("message") or {}
        if r.get("type") == "assistant":
            for b in m.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("Agent", "Task"):
                    inp = b.get("input") or {}
                    calls[b.get("id")] = (str(inp.get("description") or "без описания"), t)
            continue
        if r.get("type") not in ("user", "attachment"):
            continue
        c = m.get("content")
        for b in c if isinstance(c, list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in calls:
                g = _LAUNCH.search(_text([b])) or _RESUME.search(_text([b]))
                if g:
                    runs[g.group(1)] = calls[b.get("tool_use_id")]
                    notified.discard(g.group(1))  # (пере)запущен — итог впереди
        txt = _text(c) if c is not None else json.dumps(r.get("attachment") or {}, ensure_ascii=False)
        if "task-notification" in txt:
            for n in _NOTIF.finditer(txt.replace("\\n", "\n")):
                tid = _TID.search(n.group(1))
                if tid and tid.group(1) in runs:
                    notified.add(tid.group(1))
    still = set()
    if notified:
        still = {w.split(" ", 1)[0] for w in _workers.interim_workers(Path(path), until=now, source_rows=src)}
    live = [a for a in runs if a not in notified or a in still]
    return [dict(id=a, what=runs[a][0][:80], since=iso(runs[a][1])) for a in live]


def incoming(path, since, now):
    """Входящие за окно: доставки ao от других сессий (``<ao-session-delivery`` — поручения, письма-пинги),
    реплики человека/оркестратора (без сида «Ты запущен tide…» и без служебных isMeta) и, отдельно,
    сообщения своих тиммейтов (``<teammate-message``) — это болтовня собственной команды, не поручения."""
    c = dict(deliveries=0, human=0, teammate=0)
    for r in rows(path):
        if r.get("type") != "user" or r.get("isSidechain") or r.get("isMeta"):
            continue
        t = parse_ts(r.get("timestamp"))
        if not t or t < since or t > now:
            continue
        m = (r.get("message") or {}).get("content")
        if isinstance(m, list):
            if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m):
                continue  # ответ инструмента, не реплика
            m = [b for b in m if isinstance(b, dict) and b.get("type") == "text"]
        txt = _text(m).strip()
        if not txt or txt.startswith(("<task-notification", "<local-command", "<command-", "[Request interrupted",
                                      "<system-reminder>", "Caveat:", "Ты запущен tide")):
            continue
        if "<ao-session-delivery" in txt[:200]:
            c["deliveries"] += 1
        elif "teammate-message" in txt[:200]:
            c["teammate"] += 1
        else:
            c["human"] += 1
    return c


STALL_MIN = 30
REPEAT_MIN = 3
STUCK_WINDOW_H = 3      # метки застревания для сигнала — за последние 3 ч
IDLE_DEAD_MIN = 360     # молчит дольше 6 ч — нить просто не работает, не «застряла»
ROLE_DENY_MIN = 10      # отказов role-gate за 3 ч: обычный фон 1–7 (свежая сессия учит роль)  # один и тот же вызов 3+ раза = «повторил шаг 2+ раз»
_POLL = ("sleep", "ao status", "ao session", "tide mail ls", "git status", "git log", "tail ", "ls ")


def stuck(path, since, now, until=None):
    """Застревание сессии в окне [since, min(now, until)] (SOC-157):
    stalls        — паузы ведущей > 30 мин между её ходами, которые закончились НЕ итогом сабагента
                    (ждала человека/письмо/ничего — а не своего воркера);
    max_stall_min — самая длинная такая пауза;
    idle_now_min  — сколько ведущая молчит к моменту now (если сессия ещё ведущая, until=None);
    repeats       — сколько разных вызовов инструмента повторены 3+ раза одинаково (кроме опросов: sleep, ao status…);
    role_denies   — отказы хуков: «[tide hook role-gate]» и прочие «hook error» в ответах инструментов."""
    end = min(now, until) if until else now
    out = dict(stalls=0, max_stall_min=0.0, idle_now_min=None, repeats=0, role_denies=0, hook_errors=0)
    calls = {}
    last_turn = None
    for r in rows(path):
        if r.get("isSidechain"):
            continue
        t = parse_ts(r.get("timestamp"))
        if t is None or t > end:
            continue
        m = r.get("message") or {}
        c = m.get("content")
        if r.get("type") == "assistant":
            if t >= since:
                for b in c or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        key = b.get("name") + json.dumps(b.get("input") or {}, sort_keys=True, ensure_ascii=False)
                        if not any(x in key for x in _POLL):
                            calls[key] = calls.get(key, 0) + 1
            last_turn = t
            continue
        if r.get("type") != "user" or t < since:
            continue
        txt = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)[:4000]
        if "tool_result" in txt or isinstance(c, list):
            if "[tide hook role-gate]" in txt:
                out["role_denies"] += 1
            elif "hook error" in txt:
                out["hook_errors"] += 1
        if last_turn is not None and (t - last_turn).total_seconds() > STALL_MIN * 60:
            woke_by_worker = "task-notification" in txt[:300]
            if not woke_by_worker:
                gap = (t - last_turn).total_seconds() / 60
                out["stalls"] += 1
                out["max_stall_min"] = round(max(out["max_stall_min"], gap), 1)
    out["repeats"] = sum(1 for v in calls.values() if v >= REPEAT_MIN)
    if until is None and last_turn is not None:
        out["idle_now_min"] = round((now - last_turn).total_seconds() / 60, 1)
    return out


# ---------- передачи ----------

def offers():
    out = []
    for p in glob.glob(os.path.join(handoffs_dir(), "[0-9]*.md")):
        f = read_fields(p, ("status", "arc", "project", "created", "from-session", "taken-by", "taken-at",
                            "raised-by", "mode"))
        f["file"] = os.path.basename(p)
        f["created_dt"] = parse_ts(f.get("created"))
        out.append(f)
    return out


def handoffs_of(all_offers, project, thread, since, now):
    hs = [o for o in all_offers if o.get("project") == project and (o.get("arc") or "").startswith(thread + "/")
          and o.get("status") == "taken" and o.get("mode", "continue") == "continue"
          and o["created_dt"] and since <= o["created_dt"] <= now]
    return sorted(hs, key=lambda o: o["created_dt"])


# ---------- почта ----------

def mail(thread_dir, now):
    log = os.path.join(thread_dir, "inbox", "log.jsonl")
    sent, acks = {}, {}
    try:
        with open(log, encoding="utf-8") as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                t = parse_ts(e.get("at"))
                if not t or t > now:
                    continue
                if e.get("ev") == "sent":
                    sent[e.get("num")] = (t, e.get("from"))
                elif e.get("ev") == "ack":
                    acks.setdefault(e.get("num"), t)
    except OSError:
        return dict(letters=0, unread=0, oldest_unacked_min=None, max_ack_delay_min=None)
    unread = [n for n in sent if n not in acks]
    oldest = max(((now - sent[n][0]).total_seconds() / 60 for n in unread), default=None)
    delays = [(acks[n] - sent[n][0]).total_seconds() / 60 for n in sent if n in acks]
    return dict(letters=len(sent), unread=len(unread),
                oldest_unacked_min=round(oldest, 1) if oldest is not None else None,
                max_ack_delay_min=round(max(delays), 1) if delays else None)


# ---------- записка передачи: хвосты и задачи ----------

_SOC = re.compile(r"(SOC-\d+)\s+—\s+\S+\s+—\s+([^(\n]+)")


def seed_sections(session_dir):
    p = os.path.join(session_dir, "input", "handoff-seed.md")
    secs, cur = {}, None
    try:
        with open(p, encoding="utf-8", errors="ignore") as f:
            for ln in f:
                if ln.startswith("## "):
                    cur = ln[3:].strip()
                    secs[cur] = []
                elif cur is not None:
                    secs[cur].append(ln.rstrip("\n"))
    except OSError:
        return None
    return secs


def seed_info(sessions):
    """Хвосты и задачи из записки ведущей (или ближайшей прошлой, если у ведущей её нет)."""
    for s in reversed(sessions):
        secs = seed_sections(s["dir"])
        if not secs:
            continue
        bullets = lambda k: [l for l in secs.get(k, []) if l.startswith("- ")]
        tails = bullets("открытые хвосты") + bullets("следующий шаг")
        tasks = []
        for l in secs.get("задачи Linear", []):
            if l.startswith("  "):
                continue  # «закрыты в Linear»
            m = _SOC.search(l)
            if m:
                tasks.append(dict(id=m.group(1), status=m.group(2).strip(), title=None, src="seed"))
        return dict(seed_of=s["arc"], open_tails=len(tails), tasks=tasks,
                    tails=[l[2:] for l in bullets("открытые хвосты")], next=[l[2:] for l in bullets("следующий шаг")])
    return dict(seed_of=None, open_tails=None, tasks=[], tails=[], next=[])


# ---------- Linear ----------

def short_thread(name):
    return name.split("-@", 1)[-1]


def linear_tasks(cache, project, thread, seed):
    """Задачи нити: по кэшу Linear (последнее «Взял»/«Назначено»), иначе — из записки."""
    if cache is not None:
        out = []
        for i in cache.get("issues", []):
            who = i.get("taken") or i.get("assigned") or {}
            if who.get("project") == project and who.get("thread") == short_thread(thread):
                out.append(dict(id=i["id"], status=i.get("status"), title=i.get("title"),
                                lproject=i.get("project"), src="linear"))
        return out, "linear-cache " + str(cache.get("fetched_at"))
    return list(seed["tasks"]), "seed " + str(seed["seed_of"])


def load_linear(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ---------- расход ----------

def load_cost(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    nodes = {n["key"]: n for n in d["nodes"] if n["level"] in ("project", "thread")}
    total = sum(n["today"]["usd"] for n in d["nodes"] if n["level"] == "project")
    total_h5 = sum(n["h5"]["usd"] for n in d["nodes"] if n["level"] == "project")
    return dict(nodes=nodes, total=total, total_h5=total_h5, at=d.get("generated_at"))


def spend(cost, project, thread):
    if not cost:
        return dict(usd_today=None, usd_h5=None, usd_per_h=None, share_project=None, share_total=None)
    t = cost["nodes"].get(project + "/" + thread)
    p = cost["nodes"].get(project)
    if not t:
        return dict(usd_today=0.0, usd_h5=0.0, usd_per_h=0.0, share_project=0.0, share_total=0.0)
    pt = p["today"]["usd"] if p else 0
    return dict(usd_today=round(t["today"]["usd"], 1), usd_h5=round(t["h5"]["usd"], 1),
                usd_per_h=round(t["h5"]["usd"] / 5.0, 1),
                share_project=round(t["today"]["usd"] / pt, 3) if pt else None,
                share_total=round(t["today"]["usd"] / cost["total"], 3) if cost["total"] else None)


# ---------- сигналы и флаг ----------

def values_for_rule(r):
    return {
        "handoff_interval_h": r["handoffs"]["median_interval_h"],
        "handoffs_24h": r["handoffs"]["count_24h"],
        "linear_in_progress": r["linear"]["in_progress"],
        "linear_open": r["linear"]["in_progress"] + r["linear"]["in_review"],
        "mail_oldest_unacked_min": r["mail"]["oldest_unacked_min"],
        "spend_share_project": r["spend"]["share_project"],
        "spend_share_total": r["spend"]["share_total"],
        "spend_usd_per_h": r["spend"]["usd_per_h"],
        "open_tails": r["open_tails"],
        "live_workers": len(r["workers"]),
        "incoming_24h": r["incoming_24h"],
        "context_k": r["head"]["context_k"],
        "stuck_markers": len(r["stuck"]["markers"]),
    }


def judge(r, rule):
    v = values_for_rule(r)
    fired = []
    for k, (op, lim) in rule["signals"].items():
        x = v.get(k)
        if x is None:
            continue
        ok = {"<": x < lim, ">": x > lim, ">=": x >= lim, "<=": x <= lim}[op]
        if ok:
            fired.append(dict(signal=k, value=x, rule="{0} {1}".format(op, lim)))
    return fired, len(fired) >= rule["need"]


_PREFIX = re.compile(r"^([^:]{2,40}):\s")
_STOP = {"и", "в", "на", "по", "с", "для", "не", "из", "за", "к", "о", "от", "как", "что", "а"}


def group_key(t):
    title = t.get("title") or ""
    m = _PREFIX.match(title)
    if m:
        return m.group(1).strip()
    if t.get("lproject"):
        return t["lproject"]
    words = [w for w in re.findall(r"\w+", title.lower()) if w not in _STOP and len(w) > 3]
    return words[0] if words else t["id"]


_SOCID = re.compile(r"SOC-\d+")


def split_proposal(r, seed):
    """Как делить перегруженную нить (только если флаг горит).
    0) Одна тема ≥ 70% задач (и задач 4+) → A = её задачи In Progress, B = остальные (очередь, ревью).
    1) Задачи Linear нити (In Progress / In Review / Todo) группируются по префиксу заголовка до двоеточия
       («Groovamp: …», «Доска: …»), иначе по проекту Linear, иначе по первому значимому слову.
       Две и больше групп → A = самая крупная группа, B = остальные.
    2) Задач меньше двух групп → делим хвосты последней записки: A = хвосты с номером SOC (по задачам),
       B = хвосты без задачи (дефекты, ревью, поручения других сессий).
    3) Хвостов с SOC нет → A = «следующий шаг», B = «открытые хвосты»."""
    if not r["flag"]:
        return None
    tasks = [t for t in r["linear"]["tasks"] if t.get("status") in ("In Progress", "In Review", "Todo")]
    groups = {}
    for t in tasks:
        groups.setdefault(group_key(t), []).append(t["id"])
    gs = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    if gs and len(tasks) >= 4 and len(gs[0][1]) >= 0.7 * len(tasks):
        # одна тема забрала почти всё — делим по стадии: A ведёт начатое, B — очередь и проверку
        st = {t["id"]: t.get("status") for t in tasks}
        a_ids = [i for i in gs[0][1] if st[i] == "In Progress"]
        b_ids = [t["id"] for t in tasks if t["id"] not in a_ids]
        if a_ids and b_ids:
            return dict(basis="linear", text="предлагаю разделить «{0}» на A «в работе» ({1}) и B «очередь и "
                        "проверка» ({2})".format(gs[0][0], ", ".join(a_ids), ", ".join(b_ids)),
                        a=dict(name=gs[0][0] + " — в работе", tasks=a_ids, tails=0),
                        b=dict(name=gs[0][0] + " — очередь и проверка", tasks=b_ids, tails=0))
    if len(gs) >= 2:
        a = gs[0]
        b_ids = [i for _, ids in gs[1:] for i in ids]
        b_name = gs[1][0] if len(gs) == 2 else "остальное: " + ", ".join(k for k, _ in gs[1:4])
        return dict(basis="linear", text="предлагаю разделить на A «{0}» ({1}) и B «{2}» ({3})".format(
            a[0], ", ".join(a[1]), b_name, ", ".join(b_ids)),
            a=dict(name=a[0], tasks=a[1], tails=0), b=dict(name=b_name, tasks=b_ids, tails=0))
    tails = seed.get("tails", []) + seed.get("next", [])
    with_soc = [t for t in tails if _SOCID.search(t)]
    ids = []
    for t in with_soc + [x["id"] for x in tasks]:
        for i in _SOCID.findall(t):
            if i not in ids:
                ids.append(i)
    rest = [t for t in tails if t not in with_soc]
    if ids:
        return dict(basis="seed", text="предлагаю разделить на A «задачи {0}» ({1} хвостов) и B «дефекты, ревью и "
                    "поручения без задачи» ({2} хвостов)".format(", ".join(ids[:4]), len(with_soc), len(rest)),
                    a=dict(name="задачи " + ", ".join(ids[:4]), tasks=ids, tails=len(with_soc)),
                    b=dict(name="дефекты, ревью и поручения", tasks=[], tails=len(rest)))
    return dict(basis="seed", text="предлагаю разделить на A «следующий шаг» ({0}) и B «открытые хвосты» ({1})"
                .format(len(seed.get("next", [])), len(seed.get("tails", []))),
                a=dict(name="следующий шаг", tasks=[], tails=len(seed.get("next", []))),
                b=dict(name="открытые хвосты", tasks=[], tails=len(seed.get("tails", []))))


# ---------- сборка ----------

def collect(project_filter=None, now=None, linear_path=None, rule_name="tuned", window_h=WINDOW_H):
    now = now or dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(hours=window_h)
    cache = load_linear(linear_path) if linear_path else None
    cost = load_cost(cost_tree_path())
    all_offers = offers()
    rule = RULES[rule_name]
    out, seen = [], set()
    for pname, proot in sorted(projects().items(), key=lambda kv: (len(kv[0]), kv[0])):
        if project_filter and pname != project_filter:
            continue
        for tname, tdir, sessions in threads(proot):
            head = sessions[-1]
            if head["sid"] in seen:
                continue  # рабочая копия того же проекта (git worktree) — та же нить
            seen.add(head["sid"])
            hp = transcript(head["sid"])
            hs = handoffs_of(all_offers, pname, tname, since, now)
            # живая нить: ведущая писала за окно или были передачи
            fl = first_last(hp) if hp else (None, None)
            if not hs and not (fl[1] and fl[1] >= since):
                continue
            # ведущая на момент now: самая новая сессия, начавшаяся до now
            alive = [s for s in sessions if transcript(s["sid"]) and (first_last(transcript(s["sid"]))[0] or now) <= now]
            if alive:
                head = alive[-1]
                hp = transcript(head["sid"])
            ctx, ctx_at = context_at(hp, now) if hp else (None, None)
            created = [o["created_dt"] for o in hs]
            gaps = [(b - a).total_seconds() / 3600 for a, b in zip(created, created[1:])]
            seam_ctx, seam_workers = [], []
            for o in hs:
                op = transcript(o.get("from-session") or "")
                if op:
                    c, _ = context_at(op, o["created_dt"])
                    if c:
                        seam_ctx.append(round(c / 1000))
                    seam_workers.append(len(live_workers(op, o["created_dt"])))
            inc = dict(deliveries=0, human=0, teammate=0)
            for s in sessions:
                p = transcript(s["sid"])
                if p:
                    a, b = first_last(p)
                    if b and b >= since and a and a <= now:
                        for k, v in incoming(p, since, now).items():
                            inc[k] += v
            offer_of = {o.get("from-session"): o["created_dt"] for o in all_offers
                        if o.get("from-session") and o["created_dt"]}
            st = dict(stalls=0, max_stall_min=0.0, idle_now_min=None, repeats=0, role_denies=0, hook_errors=0)
            per_session = []
            for s_ in sessions:
                p = transcript(s_["sid"])
                if not p:
                    continue
                a_, b_ = first_last(p)
                if not (b_ and b_ >= since and a_ and a_ <= now):
                    continue
                x = stuck(p, since, now, None if s_ is head else offer_of.get(s_["sid"], now))
                per_session.append(dict(arc=s_["arc"], session=s_["sid"], **x))
                for k in ("stalls", "repeats", "role_denies", "hook_errors"):
                    st[k] += x[k]
                st["max_stall_min"] = max(st["max_stall_min"], x["max_stall_min"])
                if s_ is head:
                    st["idle_now_min"] = x["idle_now_min"]
            st["sessions"] = per_session
            # сигнал — по последним 3 ч: метки застревания
            rsince = now - dt.timedelta(hours=STUCK_WINDOW_H)
            rec = dict(stalls=0, repeats=0, role_denies=0)
            for s_ in sessions:
                p = transcript(s_["sid"])
                if not p:
                    continue
                a_, b_ = first_last(p)
                if not (b_ and b_ >= rsince and a_ and a_ <= now):
                    continue
                x = stuck(p, rsince, now, None if s_ is head else offer_of.get(s_["sid"], now))
                for k in rec:
                    rec[k] += x[k]
            idle = st["idle_now_min"]
            markers = []
            if rec["stalls"] or (idle is not None and STALL_MIN < idle <= IDLE_DEAD_MIN):
                markers.append("стоит > {0} мин".format(STALL_MIN))
            if rec["repeats"]:
                markers.append("повторяет шаг")
            if rec["role_denies"] >= ROLE_DENY_MIN:
                markers.append("упирается в роль ({0} отказов)".format(rec["role_denies"]))
            st["recent_3h"] = rec
            st["markers"] = markers
            seed = seed_info([s for s in sessions if s["num"] <= head["num"]])
            tasks, tasks_src = linear_tasks(cache, pname, tname, seed)
            r = dict(
                project=pname, thread=tname, name=short_thread(tname),
                head=dict(arc=head["arc"], title=head["title"], session=head["sid"],
                          context_k=round(ctx / 1000) if ctx else None, context_at=iso(ctx_at)),
                handoffs=dict(count_24h=len(hs),
                              median_interval_h=round(statistics.median(gaps), 2) if gaps else None,
                              last_at=iso(created[-1]) if created else None,
                              auto=sum(1 for o in hs if o.get("raised-by") == "auto"),
                              offers=[o["file"][:-3] for o in hs],
                              context_at_handoff_k=seam_ctx,
                              live_workers_at_handoff=seam_workers),
                mail=mail(tdir, now),
                linear=dict(in_progress=sum(1 for t in tasks if t.get("status") == "In Progress"),
                            in_review=sum(1 for t in tasks if t.get("status") == "In Review"),
                            tasks=tasks, source=tasks_src),
                workers=live_workers(hp, now) if hp else [],
                incoming_24h=inc["deliveries"] + inc["human"],
                incoming_detail=inc,
                open_tails=seed["open_tails"],
                spend=spend(cost, pname, tname),
                stuck=st,
            )
            r["signals"], r["flag"] = judge(r, rule)
            r["split"] = split_proposal(r, seed)
            out.append(r)
    out.sort(key=lambda r: (r["project"], r["thread"]))
    return dict(generated_at=iso(now), window_h=window_h, rule=dict(name=rule_name, need=rule["need"],
                signals={k: "{0} {1}".format(op, lim) for k, (op, lim) in rule["signals"].items()}),
                sources=dict(linear=(cache or {}).get("fetched_at") if cache else "seed",
                             cost=cost and cost["at"]),
                threads=out)


def _v(x):
    """Человеческое значение: нет данных (None) — «—», а не слово None. В --json null остаётся null."""
    return "—" if x is None or x == "" else x


def _mail_part(m):
    """Почта: нет непрочитанных — «—» (не «0/None мин»); есть — «N/возраст мин»."""
    if not m["unread"]:
        return "почта —"
    return "почта {0}/{1} мин".format(m["unread"], _v(m["oldest_unacked_min"]))


def human(d):
    lines = ["нагрузка нитей на {0} (правило {1}: {2} из {3})".format(
        _v(d["generated_at"]), d["rule"]["name"], d["rule"]["need"], len(d["rule"]["signals"]))]
    for r in sorted(d["threads"], key=lambda r: (-len(r["signals"]), r["project"], r["thread"])):
        h = r["handoffs"]
        lines.append("{flag} {p}/{n}: контекст {c} тыс. · передач {hc} (интервал {hi} ч) · {mail} · "
                     "Linear {lp}+{lr} · сабагенты {w} · входящие {inc} · хвосты {t} · {usd} $/ч".format(
                         flag="!!" if r["flag"] else "  ", p=r["project"], n=r["name"],
                         c=_v(r["head"]["context_k"]), hc=_v(h["count_24h"]), hi=_v(h["median_interval_h"]),
                         mail=_mail_part(r["mail"]),
                         lp=_v(r["linear"]["in_progress"]), lr=_v(r["linear"]["in_review"]),
                         w=len(r["workers"]), inc=_v(r["incoming_24h"]), t=_v(r["open_tails"]),
                         usd=_v(r["spend"]["usd_per_h"]))
                     + (" · застревание: " + ", ".join(r["stuck"]["markers"]) if r["stuck"]["markers"] else ""))
        if r["signals"]:
            lines.append("     сигналы: " + "; ".join("{0}={1} ({2})".format(s["signal"], _v(s["value"]), s["rule"])
                                                     for s in r["signals"]))
        if r["split"]:
            lines.append("     " + r["split"]["text"])
    return "\n".join(lines)


def cmd_load(args) -> int:
    """``tide thread load`` (SOC-153): печать среза; ``--json`` — контракт load-format.md."""
    _tr_cache.clear()
    _rows_cache.clear()
    _fl_cache.clear()
    lin = getattr(args, "load_linear", None)
    lin = linear_cache_path() if lin is None else (lin or None)
    now = getattr(args, "load_now", None)
    d = collect(getattr(args, "load_project", None), parse_ts(now) if now else None, lin,
                getattr(args, "load_rule", None) or "tuned")
    print(json.dumps(d, ensure_ascii=False, indent=1) if getattr(args, "as_json", False) else human(d))
    return 0

"""tide.watch_stuck — сторож «агент молчит» (SOC-247): ``tide watch-stuck``, один проход.

Беда (12.10): на доске «active 18 ч» у дизайнера и 9 ч у MIT bidbook. Обе не висели —
закончили ход и ждали письма, но метка AO ``activity=active`` не сбросилась, а плашка
доски «застряла» отключается курсором «жду…» навсегда. Хука на «завис посреди хода»
не было вовсе: ни толчка, ни письма менеджеру.

Проход (идемпотентный, launchd раз в минуту ``com.tide.watch-stuck``): для каждой
живой AO-сессии (``ao session ls --json``) — транскрипт Claude (``agentSessionId`` →
``<projects>/*/<sid>.jsonl``), паспорт сессии (``claude-session:``) и классификация:

* **в ходе** (последняя запись — не конец хода). Тишина ≥ :attr:`Limits.nudge`
  (20 мин) → причина: висящий ``tool_use`` без ``tool_result`` / запрос разрешения
  (строка ``state/blocked-perms.jsonl`` после вызова, SOC-108) / ошибка API / ожидание
  ввода (AskUserQuestion, AO ``waiting_input``) / модель молчит после результата.
  Первый раз — толчок ``ao send`` (сессии менеджера — никогда, SOC-232). Ещё
  :attr:`Limits.mail` (10 мин) без новых записей — письмо ``tide mail send
  management/main`` и состояние ``stuck``. Повторно не пишем, пока эпизод тот же
  (ключ — время последней записи транскрипта).
* **простаивает** — ход закончен (Stop / turn_duration), AO говорит ``active``, тишина
  ≥ :attr:`Limits.idle`: состояние ``idle`` для доски, без толчка и письма.
* **ждёт** — ход закончен, курсор «жду…» / «мяч у пользователя», без событий (любая
  запись транскрипта, письмо тоже) ≥ :attr:`Limits.wait` (4 ч): письмо менеджеру
  «ждёт N ч: <курсор>», повтор не чаще :attr:`Limits.wait_repeat`.
* **помощники** — живые фоновые сабагенты (``Agent run_in_background`` без итога):
  ход формально закончен или висит на их ожидании, транскрипт молчит — это не простой.
  Освобождение действует :attr:`Limits.workers_max` (3 ч) от запуска помощника.

Состояние — ``<control-home>/.tide/state/``: :data:`STATE_FILE` (снимок по сессиям,
читает доска) и :data:`EVENTS_FILE` (журнал толчков и писем). Пороги — env
(:data:`ENV_KEYS`) для теста; ``TIDE_WATCH_STUCK=off`` — выключено.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from . import paths

OFF_ENV = "TIDE_WATCH_STUCK"
STATE_DIR_ENV = "TIDE_WATCH_STATE_DIR"
MAIL_CMD_ENV = "TIDE_WATCH_MAIL_CMD"
MANAGER_REF = "management/main"
SENDER = "tide-watch"

STATE_FILE = "watch-stuck.json"
EVENTS_FILE = "watch-stuck.jsonl"
LOCK_FILE = "watch-stuck.lock"
PERMS_FILE = "blocked-perms.jsonl"
TAIL_BYTES = 2 * 1024 * 1024

TICK_LABEL = "com.tide.watch-stuck"
TICK_INTERVAL = 60
TICK_LOG = "watch-stuck.log"

_LIMIT_ENV = {
    "nudge": "TIDE_WATCH_NUDGE_SEC",
    "mail": "TIDE_WATCH_MAIL_SEC",
    "idle": "TIDE_WATCH_IDLE_SEC",
    "wait": "TIDE_WATCH_WAIT_SEC",
    "wait_repeat": "TIDE_WATCH_WAIT_REPEAT_SEC",
    "workers_max": "TIDE_WATCH_WORKERS_MAX_SEC",
}
ENV_KEYS = tuple(_LIMIT_ENV.values()) + (OFF_ENV, STATE_DIR_ENV, MAIL_CMD_ENV)

# Без ответа человека / окна — «ожидание ввода», а не зависший инструмент.
INPUT_TOOLS = ("AskUserQuestion", "ExitPlanMode")
# Строки транскрипта, которые не говорят о ходе: служебные метки Claude Code.
_META_TYPES = {"last-prompt", "ai-title", "atis-latch", "queue-operation", "summary",
               "file-history-snapshot", "custom-title", "permission-mode", "tag"}

STATE_OK, STATE_QUIET, STATE_NUDGED, STATE_STUCK = "ok", "quiet", "nudged", "stuck"
STATE_IDLE, STATE_WAITING, STATE_WORKERS = "idle", "waiting", "workers"

REASONS = {
    "tool": "висит инструмент без результата",
    "permission": "запрос разрешения в окне",
    "api_error": "ошибка API",
    "input": "ждёт ввода в окне",
    "no_reply": "модель не отвечает после результата инструмента",
}


@dataclass
class Limits:
    """Пороги в секундах; дефолты задачи SOC-247, env — :data:`_LIMIT_ENV`."""

    nudge: int = 20 * 60
    mail: int = 10 * 60
    idle: int = 20 * 60
    wait: int = 4 * 3600
    wait_repeat: int = 4 * 3600
    workers_max: int = 3 * 3600

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "Limits":
        e = os.environ if env is None else env
        out = cls()
        for name, key in _LIMIT_ENV.items():
            raw = (e.get(key) or "").strip()
            if raw:
                try:
                    setattr(out, name, max(0, int(float(raw))))
                except ValueError:
                    pass
        return out


# --- время / транскрипт ---------------------------------------------------------------

def _when(raw) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        at = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_rows(path: Path, tail: int = TAIL_BYTES) -> List[dict]:
    """Хвост транскрипта (*tail* байт) → строки jsonl; битые пропускаются."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - tail))
            raw = fh.read().decode("utf-8", errors="ignore")
    except OSError:
        return []
    lines = raw.splitlines()
    if size > tail and lines:
        lines = lines[1:]  # первая строка обрезана
    out = []
    for ln in lines:
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if isinstance(row, dict) and not row.get("isSidechain"):
            out.append(row)
    return out


def _blocks(row: dict) -> list:
    c = (row.get("message") or {}).get("content")
    return [b for b in c if isinstance(b, dict)] if isinstance(c, list) else []


def _text(row: dict) -> str:
    c = (row.get("message") or {}).get("content")
    if isinstance(c, str):
        return c
    return " ".join(str(b.get("text") or "") for b in _blocks(row) if b.get("type") == "text")


def _tool_label(block: dict) -> str:
    from .hooks.perm_notify import summarize_input

    inp = summarize_input(block.get("input"))
    name = str(block.get("name") or "?")
    return "{0} `{1}`".format(name, inp[:160]) if inp else name


INTERRUPT_MARK = "[Request interrupted by user"


def _interrupted(row: dict) -> bool:
    """Строка user — след Esc: ``[Request interrupted by user…]`` (текст, блок text или
    tool_result). После неё Claude Code не пишет ни Stop-хук, ни turn_duration."""
    if row.get("type") != "user":
        return False
    c = (row.get("message") or {}).get("content")
    if isinstance(c, str):
        return c.lstrip().startswith(INTERRUPT_MARK)
    for b in _blocks(row):
        if b.get("type") == "text" and str(b.get("text") or "").lstrip().startswith(
                INTERRUPT_MARK):
            return True
        if b.get("type") == "tool_result":
            inner = b.get("content")
            if isinstance(inner, list):
                inner = " ".join(str(x.get("text") or "") for x in inner
                                 if isinstance(x, dict))
            if str(inner or "").lstrip().startswith(INTERRUPT_MARK):
                return True
    return False


def _ends_turn(row: dict) -> Optional[bool]:
    """True — строка закрывает ход; False — строка хода; None — служебная."""
    kind = row.get("type")
    if kind in _META_TYPES or kind is None:
        return None
    if kind == "system":
        sub = row.get("subtype")
        if sub == "stop_hook_summary":
            # Stop-хук заблокировал (hookErrors) — ход продолжается
            return not row.get("hookErrors")
        if sub == "turn_duration":
            return True
        return None
    if kind == "attachment":
        att = row.get("attachment") or {}
        if isinstance(att, dict) and att.get("type") == "hook_blocking_error":
            return False
        return None
    if kind == "user" and _interrupted(row):
        return True
    if kind in ("user", "assistant"):
        return False
    return None


def _perm_after(perms: Sequence[dict], since: Optional[datetime], ao: str, sid: str
                ) -> Optional[dict]:
    for p in reversed(list(perms or ())):
        at = _when(p.get("ts"))
        if since is not None and (at is None or at < since):
            continue
        if ao and p.get("ao_session") and p.get("ao_session") != ao:
            continue
        if sid and p.get("claude_session") and p.get("claude_session") != sid:
            continue
        return p
    return None


def classify(rows: Sequence[dict], *, now: datetime, activity: str = "",
             perms: Sequence[dict] = (), cursor: str = "", workers: Sequence = (),
             limits: Optional[Limits] = None, ao: str = "", sid: str = "") -> Dict:
    """Транскрипт + метка AO + курсор → вердикт ``{kind, state, reason, reason_text,
    quiet_sec, last_at, last_tool, wait_h}``.

    *kind*: ``in_turn`` (ход не закончен или кончился ошибкой API) / ``ended`` /
    ``empty``. *state* — без учёта истории (:func:`decide` превращает ``quiet`` в
    ``nudged``/``stuck``): ``ok`` / ``quiet`` / ``idle`` / ``waiting`` / ``workers``.
    *workers* — живые фоновые помощники ``[(agentId, описание, запуск, cwd)]``."""
    lim = limits or Limits()
    now = now.astimezone(timezone.utc)
    last_at: Optional[datetime] = None      # последняя запись хода (без служебных)
    any_at: Optional[datetime] = None       # любая запись со временем (письмо — тоже)
    for row in rows:
        at = _when(row.get("timestamp"))
        if at is None:
            continue
        any_at = at if any_at is None or at > any_at else any_at
        if _ends_turn(row) is not None or row.get("type") == "attachment":
            last_at = at if last_at is None or at > last_at else last_at
    out: Dict = {"kind": "empty", "state": STATE_OK, "reason": "", "reason_text": "",
                 "quiet_sec": 0.0, "last_at": _iso(last_at) if last_at else "",
                 "last_tool": "", "wait_h": 0, "activity": str(activity or "")}
    sig = [r for r in rows if _ends_turn(r) is not None]
    if not sig:
        return out
    quiet = (now - last_at).total_seconds() if last_at else 0.0
    out["quiet_sec"] = quiet
    # хвост текущего хода: строки после последнего закрытия
    cut = 0
    for i, r in enumerate(sig):
        if _ends_turn(r):
            cut = i + 1
    tail = sig[cut:]
    uses: Dict[str, dict] = {}
    use_at: Dict[str, Optional[datetime]] = {}
    done = set()
    last_tool = ""
    for r in sig:
        for b in _blocks(r):
            if r.get("type") == "assistant" and b.get("type") == "tool_use":
                last_tool = _tool_label(b)
    out["last_tool"] = last_tool
    for r in tail:
        for b in _blocks(r):
            if r.get("type") == "assistant" and b.get("type") == "tool_use":
                uses[str(b.get("id"))] = b
                use_at[str(b.get("id"))] = _when(r.get("timestamp"))
            elif b.get("type") == "tool_result":
                done.add(str(b.get("tool_use_id")))
    pending = [k for k in uses if k not in done]
    # ошибка API: ход закрыт turn_duration, но последний ответ — ошибка
    last_asst = next((r for r in reversed(sig) if r.get("type") == "assistant"), None)
    last_user = next((r for r in reversed(sig) if r.get("type") == "user"), None)
    api_err = bool(last_asst and last_asst.get("isApiErrorMessage") and (
        last_user is None or (_when(last_user.get("timestamp")) or now)
        <= (_when(last_asst.get("timestamp")) or now)))
    ended = not tail and not api_err
    live = [w for w in workers or () if not _worker_expired(w, now, lim)]

    if ended:
        out["kind"] = "ended"
        if live:
            out["state"] = STATE_WORKERS
            return out
        idle_for = (now - any_at).total_seconds() if any_at else quiet
        if cursor_waits(cursor) and idle_for >= lim.wait:
            out["state"] = STATE_WAITING
            out["wait_h"] = int(idle_for // 3600)
            out["reason_text"] = "ждёт {0} ч: курсор «{1}»".format(
                out["wait_h"], _clip(cursor, 160))
            return out
        if str(activity or "") == "active" and quiet >= lim.idle:
            out["state"] = STATE_IDLE
            out["reason_text"] = "ход закончен {0} мин назад, а AO держит метку active".format(
                int(quiet // 60))
        return out

    out["kind"] = "in_turn"
    if api_err:
        out["reason"] = "api_error"
        out["reason_text"] = "ошибка API: {0}".format(_clip(_text(last_asst), 160))
    elif pending:
        key = pending[-1]
        b = uses[key]
        label = _tool_label(b)
        perm = _perm_after(perms, use_at.get(key), ao, sid)
        if perm is not None:
            out["reason"] = "permission"
            out["reason_text"] = "запрос разрешения в окне: {0} `{1}`".format(
                perm.get("tool") or b.get("name"), _clip(str(perm.get("input") or ""), 120))
        elif b.get("name") in INPUT_TOOLS or activity == "waiting_input":
            out["reason"] = "input"
            out["reason_text"] = "ждёт ввода в окне: {0}".format(label)
        else:
            out["reason"] = "tool"
            out["reason_text"] = "висит инструмент без результата: {0}".format(label)
    elif activity in ("waiting_input", "blocked"):
        out["reason"] = "input"
        out["reason_text"] = "AO: {0} — ждёт ввода в окне".format(activity)
    else:
        out["reason"] = "no_reply"
        out["reason_text"] = REASONS["no_reply"]
    if live:
        out["state"] = STATE_WORKERS
    elif quiet >= lim.nudge:
        out["state"] = STATE_QUIET
    return out


def _worker_expired(w, now: datetime, lim: Limits) -> bool:
    at = w[2] if len(w) > 2 else None
    if not isinstance(at, datetime):
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return (now - at).total_seconds() > lim.workers_max


def _clip(text: str, n: int) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= n else one[: n - 1] + "…"


def cursor_waits(cursor: str) -> bool:
    """Курсор говорит «жду…» или «мяч у пользователя» (цитаты не в счёт, как в
    :mod:`tide.ball`)."""
    from . import ball

    text = " ".join(ball._unquoted(cursor or "").split())
    if not text:
        return False
    return bool(ball._WAIT_RE.search(text) or ball._USER_BALL_RE.search(text))


def live_workers(transcript: Optional[Path]) -> list:
    """Живые фоновые помощники транскрипта (``launcher.seed_draft.worker_runs``)."""
    if transcript is None:
        return []
    try:
        from .launcher.seed_draft import worker_runs

        return list(worker_runs(Path(transcript))[0])
    except Exception:  # noqa: BLE001 — сторож не падает
        return []


# --- решение по истории ------------------------------------------------------------

def is_manager(handle: str) -> bool:
    """Сессия менеджера (``tide whois manager`` или ``management-*``) — без толчков."""
    h = (handle or "").strip()
    if h.startswith("management-"):
        return True
    try:
        from .manager_guard import is_manager_session

        return is_manager_session(h)
    except Exception:  # noqa: BLE001
        return False


def nudge_text(v: Dict) -> str:
    return ("сторож: ты молчишь {0} мин в ходе — {1}; если ждёшь — скажи tide offload, "
            "если завис — прерви инструмент").format(int(v["quiet_sec"] // 60),
                                                     v.get("reason_text") or "причина неясна")


def stuck_letter(rec: Dict) -> str:
    where = "{0} · {1}".format(rec.get("project") or "?", rec.get("thread") or "нить ?")
    lines = [
        "сторож (SOC-247): застряла {0} ({1}) — молчит {2} мин в ходе.".format(
            where, rec["ao_session"], rec.get("quiet_min", 0)),
        "Причина: {0}.".format(rec.get("reason_text") or "?"),
        "Последний инструмент: {0}.".format(rec.get("last_tool") or "—"),
        "Толчок: {0}.".format(rec["nudged_at"] + ", ответа нет" if rec.get("nudged_at")
                              else "не слали (сессия менеджера или открытый диалог в окне — только письмо)"),
        "Сессия: {0}; транскрипт: {1}".format(rec.get("session") or "?",
                                              rec.get("transcript") or "?"),
        "Что сделать: перезапустить роль свежей сессией по хендоффу или прервать инструмент.",
    ]
    return "\n".join(lines)


def waiting_letter(rec: Dict) -> str:
    where = "{0} · {1}".format(rec.get("project") or "?", rec.get("thread") or "нить ?")
    return ("сторож (SOC-247): {0} ({1}) {2}.\nСессия: {3}; транскрипт: {4}\n"
            "Если ответ за менеджером или пользователем — ответь письмом; иначе разбуди роль."
            ).format(where, rec["ao_session"], rec.get("reason_text") or "ждёт",
                     rec.get("session") or "?", rec.get("transcript") or "?")


# причины, где ответить может только человек в окне: толчок туда печатается в диалог
DIALOG_REASONS = ("permission", "input")
DIALOG_ACTIVITY = ("waiting_input", "blocked")


def in_dialog(v: Dict) -> bool:
    """Сессия стоит на диалоге (разрешение / AskUserQuestion / AO waiting_input|blocked):
    ``ao send`` напечатал бы текст и Enter прямо в селектор — толкать нельзя."""
    return (v.get("reason") in DIALOG_REASONS
            or str(v.get("activity") or "") in DIALOG_ACTIVITY)


def decide(prev: Optional[Dict], v: Dict, *, now: datetime, limits: Limits,
           manager: bool) -> "tuple[Dict, List[str]]":
    """Вердикт + прошлая запись → новая запись (без мест) и действия
    ``nudge`` / ``mail_stuck`` / ``mail_wait``."""
    prev = dict(prev or {})
    now = now.astimezone(timezone.utc)
    rec: Dict = {"state": v["state"], "kind": v["kind"], "reason": v.get("reason", ""),
                 "reason_text": v.get("reason_text", ""), "last_at": v.get("last_at", ""),
                 "quiet_min": int(v.get("quiet_sec", 0) // 60),
                 "last_tool": v.get("last_tool", ""), "wait_h": v.get("wait_h", 0)}
    acts: List[str] = []
    open_ep = prev.get("state") in (STATE_NUDGED, STATE_STUCK)
    # Лимит API: толчок будит сессию, она сразу отвечает новой ошибкой и сдвигает last_at.
    # Эпизод api_error держим по причине, а не по last_at: один толчок, потом письмо.
    api_cont = (open_ep and v.get("reason") == "api_error"
                and prev.get("reason") == "api_error"
                and v["state"] in (STATE_QUIET, STATE_OK))
    same = (prev.get("last_at") == rec["last_at"] and open_ep) or api_cont
    if v["state"] == STATE_QUIET or api_cont:
        if same:
            rec["nudged_at"] = prev.get("nudged_at", "")
            rec["episode_at"] = prev.get("episode_at", "")
            rec["episode_last_at"] = prev.get("episode_last_at") or prev.get("last_at", "")
            if api_cont:
                t0 = _when(rec["episode_last_at"])
                if t0 is not None:
                    rec["quiet_min"] = int((now - t0).total_seconds() // 60)
            if prev.get("state") == STATE_STUCK:
                rec["state"] = STATE_STUCK
                rec["mailed_at"] = prev.get("mailed_at", "")
            else:
                start = _when(prev.get("episode_at")) or now
                if (now - start).total_seconds() >= limits.mail:
                    rec["state"] = STATE_STUCK
                    rec["mailed_at"] = _iso(now)
                    acts.append("mail_stuck")
                else:
                    rec["state"] = STATE_NUDGED
        elif in_dialog(v):
            # диалог в окне: толчка нет (его бы приняли за ответ), письмо менеджеру сразу
            rec["state"] = STATE_STUCK
            rec["episode_at"] = _iso(now)
            rec["episode_last_at"] = rec["last_at"]
            rec["nudged_at"] = ""
            rec["mailed_at"] = _iso(now)
            acts.append("mail_stuck")
        else:
            rec["state"] = STATE_NUDGED
            rec["episode_at"] = _iso(now)
            rec["episode_last_at"] = rec["last_at"]
            rec["nudged_at"] = "" if manager else _iso(now)
            if not manager:
                acts.append("nudge")
    elif v["state"] == STATE_WAITING:
        last = _when(prev.get("wait_mailed_at")) if prev.get("state") == STATE_WAITING else None
        if last is None or (now - last).total_seconds() >= limits.wait_repeat:
            rec["wait_mailed_at"] = _iso(now)
            acts.append("mail_wait")
        else:
            rec["wait_mailed_at"] = prev.get("wait_mailed_at", "")
    rec["since"] = prev.get("since", "") if prev.get("state") == rec["state"] else _iso(now)
    return rec, acts


# --- места: сессии AO, транскрипт, паспорт ---------------------------------------------

def ao_sessions() -> List[dict]:
    """Живые AO-сессии (``ao session ls --json``) через адаптер tide."""
    from .adapters import ao

    if not ao.ao_available():
        return []
    data = ao._json(["session", "ls", "--json"])
    rows = data.get("data") or data.get("sessions") or []
    return [r for r in rows if isinstance(r, dict) and r.get("id")]


def _activity(s: dict) -> str:
    a = s.get("activity")
    if isinstance(a, dict):
        return str(a.get("state") or "")
    return str(a or "")


def _mangle(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def locate(s: dict) -> Dict:
    """``{sid, transcript, root, entry, thread, session, cursor}`` для AO-сессии *s*.
    claude-id — ``agentSessionId`` из JSON сессии или БД AO (адаптер); без него —
    новейший транскрипт папки AO-worktree. Не падает."""
    from .launcher.seed_draft import claude_projects_dir, transcript_path

    out: Dict = {"sid": "", "transcript": None, "root": None, "entry": None,
                 "thread": "", "session": "", "cursor": ""}
    handle = str(s.get("id") or "")
    sid = str(s.get("agentSessionId") or s.get("agent_session_id") or "").strip()
    if not sid:
        try:
            from .adapters.ao import _db_agent_session_id

            sid = _db_agent_session_id(handle)
        except Exception:  # noqa: BLE001
            sid = ""
    tp = transcript_path(sid) if sid else None
    if tp is None and s.get("projectId"):
        wt = Path.home() / ".ao" / "data" / "worktrees" / str(s["projectId"]) / handle
        d = claude_projects_dir() / _mangle(str(wt))
        found = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime) if d.is_dir() else []
        if found:
            tp = found[-1]
            sid = sid or tp.stem
    out["sid"], out["transcript"] = sid, tp
    cwd = ""
    if tp is not None:
        for row in reversed(read_rows(tp, tail=256 * 1024)):
            if row.get("cwd"):
                cwd = str(row["cwd"])
                break
    try:
        root = paths.find_tide_root(Path(cwd)) if cwd and Path(cwd).exists() else None
    except Exception:  # noqa: BLE001
        root = None
    out["root"] = root
    if root is not None and sid:
        try:
            from . import ball, offload, slug

            entry = offload.find_session_by_claude_id(root, sid)
            if entry is not None:
                out["entry"] = entry
                out["thread"] = slug.entry_slug(entry.parent.parent.name)
                out["session"] = str(entry)
                out["cursor"] = ball.cursor_of(entry / "arc.md")
        except Exception:  # noqa: BLE001
            pass
    return out


def _perms(root: Optional[Path]) -> List[dict]:
    if root is None:
        return []
    return read_rows(paths.state_dir(Path(root)) / PERMS_FILE, tail=256 * 1024)


# --- доставка ------------------------------------------------------------------------

def default_say(handle: str, text: str) -> bool:
    """``ao send`` через адаптер (у него свой запрет на менеджера, SOC-232)."""
    if is_manager(handle):
        return False
    from .adapters.ao import AoAdapter

    return AoAdapter().say(handle, text)


def _tide_argv() -> List[str]:
    import shutil

    exe = shutil.which("tide")
    return [exe] if exe else [sys.executable, "-m", "tide"]


def default_mail(text: str) -> bool:
    """``tide mail send management/main --from tide-watch -`` (текст — stdin).
    ``$TIDE_WATCH_MAIL_CMD`` — своя команда (тест, живая проверка без писем)."""
    raw = (os.environ.get(MAIL_CMD_ENV) or "").strip()
    argv = shlex.split(raw) if raw else _tide_argv() + ["mail", "send"]
    argv = argv + [MANAGER_REF, "--from", SENDER, "-"]
    try:
        proc = subprocess.run(argv, input=text, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


# --- проход --------------------------------------------------------------------------

def default_state_dir(env: Optional[Dict[str, str]] = None) -> Path:
    e = os.environ if env is None else env
    raw = (e.get(STATE_DIR_ENV) or "").strip()
    if raw:
        return Path(raw).expanduser()
    return paths.state_dir(paths.control_home())


def _load(sdir: Path) -> Dict:
    try:
        data = json.loads((sdir / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data.get("sessions") or {} if isinstance(data, dict) else {}


def _save(sdir: Path, sessions: Dict, now: datetime) -> None:
    sdir.mkdir(parents=True, exist_ok=True)
    tmp = sdir / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps({"updated": _iso(now), "version": 1, "sessions": sessions},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, sdir / STATE_FILE)


def _event(sdir: Path, ev: Dict) -> None:
    sdir.mkdir(parents=True, exist_ok=True)
    with open(sdir / EVENTS_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")


def run_once(*, sessions: Optional[Iterable[dict]] = None, now: Optional[datetime] = None,
             say: Optional[Callable[[str, str], bool]] = None,
             mail: Optional[Callable[[str], bool]] = None,
             state_dir: Optional[Path] = None, dry_run: bool = False,
             env: Optional[Dict[str, str]] = None,
             locator: Callable[[dict], Dict] = None) -> List[Dict]:
    """Один проход по живым AO-сессиям → записи (с ``actions``). Идемпотентен:
    повторный проход без новых событий ничего не шлёт."""
    e = os.environ if env is None else env
    if (e.get(OFF_ENV) or "").strip().lower() == "off":
        return []
    lim = Limits.from_env(e)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sdir = Path(state_dir) if state_dir is not None else default_state_dir(e)
    say = say or default_say
    mail = mail or default_mail
    locator = locator or locate
    lock = None
    if not dry_run:
        sdir.mkdir(parents=True, exist_ok=True)
        lock = open(sdir / LOCK_FILE, "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            return []  # другой проход ещё идёт
    try:
        rows_in = list(ao_sessions() if sessions is None else sessions)
        prev_all = _load(sdir)
        new_all: Dict[str, Dict] = {}
        out: List[Dict] = []
        for s in rows_in:
            if s.get("isTerminated") or _activity(s) == "exited":
                continue
            handle = str(s["id"])
            loc = locator(s)
            tp = loc.get("transcript")
            if tp is None:
                continue
            rows = read_rows(Path(tp))
            act = _activity(s)
            v = classify(rows, now=now, activity=act, cursor=loc.get("cursor", ""),
                         perms=_perms(loc.get("root")), limits=lim, ao=handle,
                         sid=loc.get("sid", ""))
            if v["state"] in (STATE_QUIET, STATE_IDLE):
                # помощников ищем только когда это что-то меняет (полный транскрипт)
                live = live_workers(tp) if v["state"] != STATE_OK else []
                if live:
                    v = classify(rows, now=now, activity=act, cursor=loc.get("cursor", ""),
                                 perms=_perms(loc.get("root")), workers=live, limits=lim,
                                 ao=handle, sid=loc.get("sid", ""))
            mgr = is_manager(handle)
            rec, acts = decide(prev_all.get(handle), v, now=now, limits=lim, manager=mgr)
            root = loc.get("root")
            rec.update({
                "ao_session": handle,
                "project": Path(root).name if root else str(s.get("projectId") or ""),
                "thread": loc.get("thread", ""),
                "session": loc.get("session", ""),
                "claude_session": loc.get("sid", ""),
                "transcript": str(tp),
                "ao_activity": act,
                "cursor": _clip(loc.get("cursor", ""), 200),
                "manager": mgr,
                "updated": _iso(now),
            })
            sent: List[str] = []
            for a in acts:
                if dry_run:
                    sent.append(a)
                    continue
                ok = False
                try:
                    if a == "nudge":
                        ok = bool(say(handle, nudge_text(v)))
                    elif a == "mail_stuck":
                        ok = bool(mail(stuck_letter(rec)))
                    elif a == "mail_wait":
                        ok = bool(mail(waiting_letter(rec)))
                except Exception:  # noqa: BLE001 — доставка не роняет проход
                    ok = False
                sent.append(a)
                _event(sdir, {"ev": {"nudge": "nudge", "mail_stuck": "stuck",
                                     "mail_wait": "waiting"}[a],
                              "at": _iso(now), "ok": ok, "ao_session": handle,
                              "project": rec["project"], "thread": rec["thread"],
                              "reason": rec["reason"], "reason_text": rec["reason_text"],
                              "quiet_min": rec["quiet_min"], "transcript": rec["transcript"]})
            rec["actions"] = sent
            new_all[handle] = {k: x for k, x in rec.items() if k != "actions"}
            out.append(rec)
        if not dry_run:
            _save(sdir, new_all, now)
        return out
    finally:
        if lock is not None:
            lock.close()


def line(recs: Sequence[Dict], *, now: Optional[datetime] = None) -> str:
    """Одна строка в лог launchd на проход."""
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    bad = [r for r in recs if r.get("state") not in (STATE_OK, STATE_WORKERS)]
    tail = "".join(" · {0}={1}{2}".format(r["ao_session"], r["state"],
                                          "[" + ",".join(r["actions"]) + "]"
                                          if r.get("actions") else "") for r in bad)
    return "{0} watch-stuck: сессий {1}, внимания {2}{3}".format(stamp, len(recs), len(bad), tail)


# --- launchd ---------------------------------------------------------------------------

def tick_plist(home: Path, *, argv: Optional[List[str]] = None,
               env: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    """launchd-агент :data:`TICK_LABEL`: раз в минуту ``tide watch-stuck``."""
    e = os.environ if env is None else env
    user = Path(e.get("HOME") or Path.home())
    envd = {
        "PATH": ":".join(["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin",
                          str(user / ".local" / "bin"), "/usr/bin", "/bin", "/usr/sbin",
                          "/sbin"]),
        "HOME": str(user),
        "LANG": "en_US.UTF-8",
        "PYTHONUNBUFFERED": "1",
        paths.TIDE_HOME_ENV: str(home),
    }
    log = str(paths.state_dir(Path(home)) / TICK_LOG)
    return {
        "Label": TICK_LABEL,
        "ProgramArguments": list(argv or _tide_argv()) + ["watch-stuck"],
        "WorkingDirectory": str(home),
        "EnvironmentVariables": envd,
        "StartInterval": TICK_INTERVAL,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "Nice": 10,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


def plist_text(home: Path, *, argv: Optional[List[str]] = None) -> str:
    from .hooks.orphan import plist_xml

    return plist_xml(tick_plist(home, argv=argv))


def cmd_watch_stuck(args) -> int:
    if getattr(args, "print_plist", False):
        try:
            home = paths.control_home()
        except FileNotFoundError as exc:
            print("tide: {0}".format(exc), file=sys.stderr)
            return 1
        sys.stdout.write(plist_text(home))
        return 0
    try:
        recs = run_once(dry_run=bool(getattr(args, "dry_run", False)))
    except Exception as exc:  # noqa: BLE001 — таймер не падает
        print("{0} watch-stuck: ошибка: {1}".format(
            datetime.now().isoformat(timespec="seconds"), exc), flush=True)
        return 0
    if getattr(args, "json", False):
        print(json.dumps(recs, ensure_ascii=False, indent=1))
    else:
        print(line(recs), flush=True)
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "watch-stuck",
        help="one pass of the stuck-agent watch over live AO sessions (SOC-247)")
    p.add_argument("--dry-run", action="store_true",
                   help="classify and print, send nothing, write no state")
    p.add_argument("--json", action="store_true", help="print records as JSON")
    p.add_argument("--print-plist", action="store_true",
                   help="print the launchd agent (com.tide.watch-stuck) — not loaded")
    p.set_defaults(func=cmd_watch_stuck)

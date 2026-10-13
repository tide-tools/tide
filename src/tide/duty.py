"""tide.duty — «дежурный» (SOC-278): надстройка сторожа ``watch-stuck`` без языковой модели.

Сторож (:mod:`tide.watch_stuck`) раз в минуту видит каждую живую AO-сессию. Дежурный
добавляет к вердикту «у кого мяч» (:func:`tide.ball.side`) и «следующий шаг» (секция
``next`` паспорта) и сам будит стоящих. Ноль вызовов модели: только курсор, паспорт,
``ao send``, ``tide mail send`` и ``$TIDE_ALERT_CMD``.

Правила (пороги — :class:`tide.watch_stuck.Limits`, env ``TIDE_DUTY_*``):

1. **Мяч у роли** (``mine``), ход закончен, тишина ≥ 20 мин → ``ao send``
   «дежурный: стоишь N мин, мяч у тебя: <next>». Не чаще раза в 30 мин на сессию.
   Менеджеров не толкаем.
2. **Мяч у менеджера** (курсор «мяч у менеджера/main…»), роль стоит ≥ 20 мин →
   письмо ``management/main`` (как у «stuck»), не чаще раза в 60 мин на сессию.
3. **Диалог** (запрос разрешения / AskUserQuestion / AO ``waiting_input|blocked``) →
   сразу ❓ пользователю через ``send_alert(route="human")`` с адресом сессии
   (AO id, проект — роль) и тем, что висит. Одна тревога на диалог: пока сессия
   не вышла из диалога, ключ ``alert_key`` не меняется.
4. **Не решилось пинком**: после толчка (дежурного или обычного) и/или письма
   тишина тянется ещё ≥ 30 мин → одно письмо ``management/supervisor`` с разбором
   (AO id, причина, сколько стоит, курсор, что уже сделано). Один раз на эпизод.
5. **Почта**: статус очереди пингов (``mail-ping-queue.json``, лок
   ``…json.lock``, записка держателя ``.json.holder``). Саму тревогу «лок > 60 с или
   ≥ 300 строк» шлёт ``mail.py`` (SOC-277, ``mail-queue-alert.json``) — здесь только
   статус, без дубля.

Не более :data:`MAX_ACTIONS` действий дежурного за проход (шквал при первой
установке растягивается на несколько тиков). Каждая часть обёрнута: сбой одной не роняет
проход.

Формат ``<state>/duty.json`` (читает доска, SOC-268)::

    {"updated": ISO-UTC, "version": 1,
     "mail": {"queue_rows": int, "lock_held": bool, "lock_held_sec": float|null,
              "lock_holder": {"pid", "cmd"}|null, "alarm": "ok"|"busy"|"queue_big",
              "last_alert_at": ISO|"" , "alert_by": "mail.py SOC-277"},
     "sessions": {"<AO id>": {
        "ao_id": "app-development-102", "project": "app-development",
        "role": "artist",                       # нить
        "title": "app-development — artist · 4", # title паспорта
        "state": "ok|quiet|nudged|stuck|idle|waiting|workers|overdue",
        "ball": "mine|user|rest|other|none",     # tide.ball.side
        "ball_label": "у меня",                  # подпись для доски
        "idle_min": 42,                          # минут без записей в транскрипте
        "last_action": "пнута 12:03",            # человеческая подпись, местное время
        "last_action_at": ISO-UTC,
        "next": "первый шаг из паспорта",
        "cursor": "курсор (≤200 зн.)",
        # служебное дежурного:
        "episode": "<last_at>", "nudged_at", "mailed_manager_at", "escalated",
        "alert_key", "first_action_at"}}}

Подписи ``last_action``: «пнута HH:MM», «письмо менеджеру HH:MM», «❓ пользователю HH:MM»,
«разбор супервайзеру HH:MM», «пнута сторожем HH:MM» (обычный толчок сторожа).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

DUTY_FILE = "duty.json"
SUPERVISOR_REF = "management/supervisor"
MAX_ACTIONS = 5
ALERT_CMD_ENV = "TIDE_ALERT_CMD"
# где лежит пульт тревог, если сторож запущен без $TIDE_ALERT_CMD (plist не менялся)
ALERT_CMD_FALLBACK = (Path.home() / "Documents" / "management" / ".tide" / "arcs"
                      / "01-@ao-control" / "arcs" / "02-tg-pult" / "workspace"
                      / "tg-pult" / "tg-alert")

# «жду …» в курсоре — роль сама ждёт чужого хода, мяч «у меня» формальный: не толкаем
_WAITING_RE = re.compile(r"(?<!\w)жд(?:у|ёт|ем|ём|ать)(?!\w)", re.IGNORECASE)
_MGR_RE = re.compile(r"менеджер|manager|\bmain\b", re.IGNORECASE)

ACT_NUDGE, ACT_MAIL_MGR = "duty_nudge", "duty_mail_manager"
ACT_ALERT, ACT_ESCALATE = "duty_alert", "duty_escalate"


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _when(raw) -> Optional[datetime]:
    from .watch_stuck import _when as w

    return w(raw)


def _hm(at: datetime) -> str:
    return at.astimezone().strftime("%H:%M")


def _clip(text: str, n: int) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= n else one[: n - 1] + "…"


def next_step(lines) -> str:
    """Первый шаг из секции ``next`` паспорта: без маркера списка, ≤ 200 зн."""
    for ln in lines or ():
        t = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*(?:\[[ xX]\]\s*)?", "", str(ln)).strip()
        if t:
            return _clip(t, 200)
    return ""


def ball_of(cursor: str, eta: str = "") -> Tuple[str, str]:
    """``(вид, подпись)`` — :func:`tide.ball.side`; сбой → ``none``."""
    try:
        from . import ball

        return ball.side(cursor, eta=eta)
    except Exception:  # noqa: BLE001
        return "none", "—"


def _since(prev_iso: str, now: datetime) -> float:
    at = _when(prev_iso)
    return (now - at).total_seconds() if at else float("inf")


def decide(prev: Optional[Dict], rec: Dict, v: Dict, *, ball_kind: str, ball_label: str,
           nxt: str, cursor: str, now: datetime, limits, manager: bool,
           dialog: bool, watch_acts=(), budget: int = MAX_ACTIONS) -> Tuple[Dict, List[str]]:
    """Прошлая запись дежурного + запись сторожа *rec* + вердикт *v* → новая запись
    дежурного и действия (``duty_nudge`` / ``duty_mail_manager`` / ``duty_alert`` /
    ``duty_escalate``). Чистая функция: ничего не шлёт."""
    prev = dict(prev or {})
    now = now.astimezone(timezone.utc)
    d: Dict = {k: prev.get(k, "") for k in (
        "nudged_at", "mailed_manager_at", "escalated", "alert_key", "first_action_at",
        "last_action", "last_action_at")}
    state = rec.get("state", v.get("state", ""))
    last_at = v.get("last_at", "")
    ended = v.get("kind") == "ended"
    standing = ended and v.get("state") not in ("workers", "waiting") \
        and v.get("quiet_sec", 0) >= limits.idle
    episode = (("dlg:" if dialog else "idle:" if ended else "turn:")
               + (rec.get("episode_at") or last_at))
    if prev.get("episode") != episode:
        d["escalated"] = ""
        d["first_action_at"] = ""
    d["episode"] = episode
    acts: List[str] = []

    def mark(label: str, act: str) -> None:
        d["last_action"] = "{0} {1}".format(label, _hm(now))
        d["last_action_at"] = _iso(now)
        d["first_action_at"] = d["first_action_at"] or _iso(now)

    # обычный толчок/письмо сторожа в этот тик — тоже «действие» для статуса
    if "nudge" in watch_acts:
        mark("пнута сторожем", "nudge")
    if "mail_stuck" in watch_acts:
        mark("письмо менеджеру", "mail_stuck")

    # 3. диалог → ❓ пользователю, один раз на диалог
    if dialog:
        key = "{0}:{1}".format(v.get("reason") or v.get("activity") or "dialog", last_at)
        if d["alert_key"] != key:
            acts.append(ACT_ALERT)
            d["alert_key"] = key  # снимается (→ "") в execute, если доставка не удалась
    else:
        d["alert_key"] = ""

    # 1/2. стоит без диалога
    spent = 0
    if standing and not dialog and not manager and budget > 0:
        if ball_kind == "mine" and not _WAITING_RE.search(cursor or "") \
                and _since(d["nudged_at"], now) >= limits.duty_nudge_gap \
                and "nudge" not in watch_acts:
            acts.append(ACT_NUDGE)
            spent += 1
        elif ball_kind == "other" and _MGR_RE.search(cursor or "") \
                and _since(d["mailed_manager_at"], now) >= limits.duty_mail_gap:
            acts.append(ACT_MAIL_MGR)
            spent += 1

    # 4. не решилось пинком
    if not dialog and not manager and budget - spent > 0 and not d["escalated"]:
        base = d["first_action_at"] or ""
        if state == "stuck" and not base:
            base = rec.get("mailed_at") or ""
        if base and _since(base, now) >= limits.duty_escalate and (
                state == "stuck" or standing):
            acts.append(ACT_ESCALATE)
    d["_planned"] = list(acts)
    return d, acts


def apply_done(d: Dict, act: str, ok: bool, now: datetime) -> None:
    """Зафиксировать выполненное действие в записи дежурного (при ok)."""
    now = now.astimezone(timezone.utc)

    def stamp(label: str) -> None:
        d["last_action"] = "{0} {1}".format(label, _hm(now))
        d["last_action_at"] = _iso(now)
        d["first_action_at"] = d.get("first_action_at") or _iso(now)

    if act == ACT_ALERT:
        if ok:
            stamp("❓ пользователю")
        else:
            d["alert_key"] = ""      # не доставлено — попробуем на следующем тике
    elif not ok:
        return
    elif act == ACT_NUDGE:
        d["nudged_at"] = _iso(now)
        stamp("пнута")
    elif act == ACT_MAIL_MGR:
        d["mailed_manager_at"] = _iso(now)
        d["last_action"] = "письмо менеджеру {0}".format(_hm(now))
        d["last_action_at"] = _iso(now)
    elif act == ACT_ESCALATE:
        d["escalated"] = _iso(now)
        d["last_action"] = "разбор супервайзеру {0}".format(_hm(now))
        d["last_action_at"] = _iso(now)


# --- тексты ------------------------------------------------------------------------

def _where(rec: Dict) -> str:
    return "{0} — {1}".format(rec.get("project") or "?", rec.get("thread") or "роль ?")


def nudge_text(rec: Dict, v: Dict, nxt: str, cursor: str) -> str:
    return "дежурный: стоишь {0} мин, мяч у тебя: {1}".format(
        int(v.get("quiet_sec", 0) // 60), nxt or _clip(cursor, 160) or "см. курсор")


def manager_letter(rec: Dict, v: Dict, nxt: str, cursor: str) -> str:
    return ("дежурный (SOC-278): {0} ({1}) стоит {2} мин — мяч у менеджера.\n"
            "Курсор: {3}\nСледующий шаг роли: {4}\nСессия: {5}\n"
            "Ответь роли письмом или забери мяч."
            ).format(_where(rec), rec.get("ao_session"), int(v.get("quiet_sec", 0) // 60),
                     _clip(cursor, 200) or "—", nxt or "—", rec.get("session") or "?")


def alert_text(rec: Dict, v: Dict) -> str:
    what = v.get("reason_text") or ("AO: " + str(v.get("activity") or "диалог"))
    tool = v.get("last_tool")
    return ("❓ {0} ({1}) ждёт тебя в окне: {2}{3}. Открой сессию и ответь или разреши."
            ).format(_where(rec), rec.get("ao_session"), what,
                     " [{0}]".format(_clip(tool, 120)) if tool else "")


def escalate_letter(rec: Dict, v: Dict, d: Dict, nxt: str, cursor: str) -> str:
    done = []
    if rec.get("nudged_at"):
        done.append("толчок сторожа {0}".format(rec["nudged_at"]))
    if d.get("nudged_at"):
        done.append("толчок дежурного {0}".format(d["nudged_at"]))
    if rec.get("mailed_at"):
        done.append("письмо менеджеру {0}".format(rec["mailed_at"]))
    if d.get("mailed_manager_at"):
        done.append("письмо менеджеру (мяч) {0}".format(d["mailed_manager_at"]))
    return ("дежурный (SOC-278): пинок не помог — {0} ({1}).\n"
            "Состояние: {2}; причина: {3}.\nСтоит: {4} мин без записей.\n"
            "Курсор: {5}\nСледующий шаг: {6}\nУже сделано: {7}.\n"
            "Сессия: {8}; транскрипт: {9}"
            ).format(_where(rec), rec.get("ao_session"), rec.get("state"),
                     rec.get("reason_text") or rec.get("reason") or "—",
                     int(v.get("quiet_sec", 0) // 60), _clip(cursor, 200) or "—",
                     nxt or "—", "; ".join(done) or "ничего", rec.get("session") or "?",
                     rec.get("transcript") or "?")


# --- доставка ----------------------------------------------------------------------

def default_alert(text: str, source: str) -> bool:
    """❓ человеку: ``send_alert(route="human", urgent=True)``. Нет ``$TIDE_ALERT_CMD`` —
    берём пульт по умолчанию (plist сторожа не трогаем)."""
    from .launcher.autohandoff import send_alert

    if not (os.environ.get(ALERT_CMD_ENV) or "").strip() and ALERT_CMD_FALLBACK.exists():
        os.environ[ALERT_CMD_ENV] = str(ALERT_CMD_FALLBACK)
    return send_alert(text, route="human", source=source, urgent=True) == 0


# --- почта: статус (тревогу шлёт mail.py, SOC-277) -------------------------------------

def mail_status(sdir: Path, now: datetime) -> Dict:
    """Состояние очереди пингов для доски. Без дубля тревоги: она в ``mail.py``."""
    out: Dict = {"queue_rows": 0, "lock_held": False, "lock_held_sec": None,
                 "lock_holder": None, "alarm": "ok", "last_alert_at": "",
                 "alert_by": "mail.py SOC-277"}
    try:
        data = json.loads((sdir / "mail-ping-queue.json").read_text(encoding="utf-8"))
        pend = data.get("pending") or {}
        out["queue_rows"] = sum(len(x) for x in pend.values() if isinstance(x, list))
    except (OSError, ValueError, AttributeError):
        pass
    try:
        holder = json.loads((sdir / "mail-ping-queue.json.holder").read_text(encoding="utf-8"))
        out["lock_holder"] = {"pid": holder.get("pid"), "cmd": holder.get("cmd")}
        out["lock_held_sec"] = round(max(0.0, now.timestamp() - float(holder.get("since") or 0)), 1)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    try:
        import fcntl

        lock = sdir / "mail-ping-queue.json.lock"
        if lock.exists():
            with open(str(lock), "a+") as fh:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except BlockingIOError:
                    out["lock_held"] = True
    except (ImportError, OSError):
        pass
    try:
        al = json.loads((sdir / "mail-queue-alert.json").read_text(encoding="utf-8"))
        vals = [str(x) for x in al.values() if isinstance(x, str)] if isinstance(al, dict) else []
        out["last_alert_at"] = max(vals) if vals else ""
    except (OSError, ValueError, AttributeError):
        pass
    if out["lock_held"] and (out["lock_held_sec"] or 0) >= 60:
        out["alarm"] = "busy"
    elif out["queue_rows"] >= 300:
        out["alarm"] = "queue_big"
    return out


# --- файл --------------------------------------------------------------------------

def load(sdir: Path) -> Dict[str, Dict]:
    try:
        data = json.loads((sdir / DUTY_FILE).read_text(encoding="utf-8"))
        s = data.get("sessions")
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def save(sdir: Path, sessions: Dict[str, Dict], mail: Dict, now: datetime) -> None:
    sdir.mkdir(parents=True, exist_ok=True)
    tmp = sdir / (DUTY_FILE + ".tmp")
    tmp.write_text(json.dumps({"updated": _iso(now), "version": 1, "mail": mail,
                               "sessions": sessions}, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    os.replace(tmp, sdir / DUTY_FILE)


def board_row(rec: Dict, d: Dict, *, ball_kind: str, ball_label: str, nxt: str,
              v: Dict) -> Dict:
    """Строка для доски: публичные поля :func:`decide` + служебные."""
    row = dict(d)
    row.pop("_planned", None)
    row.update({
        "ao_id": rec.get("ao_session"), "project": rec.get("project"),
        "role": rec.get("thread"), "title": rec.get("title", ""),
        "state": rec.get("state"), "ball": ball_kind, "ball_label": ball_label,
        "idle_min": int(v.get("quiet_sec", 0) // 60), "next": nxt,
        "cursor": rec.get("cursor", ""),
    })
    return row

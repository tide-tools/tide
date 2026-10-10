"""tide.arc.workers — фоновые воркеры сессии по её транскрипту (SOC-143).

Один признак «воркер ещё в полёте / итог не переслан» на весь tide: автозакрытие
старой (:mod:`tide.hooks.autoclose`), предложение «закрыть старую?»
(:func:`tide.launcher.reception.close_blockers`) и сид (блок «в полёте»,
:mod:`tide.launcher.seed_draft`) судят по одним функциям. Вынесено из
``hooks/autoclose.py`` в слой domain, чтобы launcher видел их без импорта вверх
(правило слоёв — ``tests/test_layers.py``). Только чтение.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .. import paths

# Очередь писем пары, ждущих приёма новой (пишет :mod:`tide.hooks.handed_off`).
PENDING_LOG = "forward-pending.jsonl"


# Конечные статусы AO (``status``/``activity.state``): сессия уже вышла — kill не нужен.
FINAL_STATUSES = ("exited", "terminated", "killed", "done", "completed", "merged",
                  "cleanup", "errored", "failed", "stopped", "dead")


def ended(s: Optional[dict]) -> str:
    """Конечный статус AO-сессии *s* (``"exited"`` …) или ``""``, если жива."""
    if not s:
        return ""
    act = s.get("activity")
    act = act.get("state") if isinstance(act, dict) else act
    for v in (s.get("status"), act):
        v = str(v or "").strip().lower()
        if v in FINAL_STATUSES:
            return v
    return "terminated" if s.get("isTerminated") else ""


def user_text(row: dict) -> str:
    """Текст доставки в строке транскрипта: реплика пользователя (``type: user``)
    или сообщение, пришедшее очередью, пока сессия работала (SOC-101 F28:
    ``type: attachment`` с ``attachment.type: queued_command`` — ``prompt``)."""
    if row.get("type") == "attachment":
        att = row.get("attachment") or {}
        if not isinstance(att, dict) or att.get("type") != "queued_command":
            return ""
        content = att.get("prompt")
    elif row.get("type") == "user":
        content = (row.get("message") or {}).get("content")
    else:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


# --- транскрипт ---------------------------------------------------------

def rows(transcript: Path) -> Iterable[dict]:
    try:
        raw = Path(transcript).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    out = []
    for ln in raw.splitlines():
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def row_at(row: dict) -> Optional[datetime]:
    from . import dialog
    return dialog._stamp(str(row.get("timestamp") or ""))


def unforwarded_results(transcript: Path, since: datetime, new_ao) -> List[str]:
    """Итоги воркеров, пришедшие старой после *since* и не пересланные новой:
    после ``<task-notification>`` нет хода старой (tool_use), где назван *new_ao*
    (строка или список AO-id — подходит любой). → ``["ЧЧ:ММ «summary»", …]``."""
    import re

    targets = [new_ao] if isinstance(new_ao, str) else list(new_ao or ())
    targets = [t for t in targets if t]

    notif = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)
    summ = re.compile(r"<summary>(.*?)</summary>", re.S)
    pending: List[tuple] = []
    for row in rows(transcript):
        at = row_at(row)
        if row.get("type") == "assistant":
            if not pending or not targets:
                continue
            for b in (row.get("message") or {}).get("content") or []:
                if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                    continue
                said = json.dumps(b.get("input") or {}, ensure_ascii=False)
                if any(t in said for t in targets):
                    pending = []
                    break
            continue
        if at is None or at < since:
            continue
        blob = json.dumps(row.get("message") or row.get("attachment") or {},
                          ensure_ascii=False)
        if "task-notification" not in blob:
            continue
        text = user_text(row) or blob
        for m in notif.finditer(str(text)):
            s = summ.search(m.group(1))
            pending.append((at, " ".join((s.group(1) if s else "итог воркера").split())[:80]))
    return ["{0} «{1}»".format(at.strftime("%H:%M"), what) for at, what in pending]


_INTERIM = ("background work of its own still running", "may be interim",
            "notifies again")


def interim_workers(transcript: Path, until: Optional[datetime] = None,
                    source_rows: Optional[Iterable[dict]] = None) -> List[str]:
    """Сабагенты, чей ПОСЛЕДНИЙ ``<task-notification>`` промежуточный (SOC-143):
    «stopped with background work of its own still running … result below may be
    interim» — воркер ещё гоняет своё (полный pytest), итог впереди. Сюда же —
    сабагент, которому старая написала SendMessage после его последнего уведомления
    (возобновлён, итога ещё нет). ``background_workers`` считает концом любое
    уведомление — этого мало. → ``["agentId «summary»", …]``.

    *until* — судить «как было» на этот момент (строки позже — не в счёт; SOC-153
    ``tide thread load --now``); *source_rows* — уже прочитанные строки транскрипта
    (сборщик нагрузки читает файл один раз)."""
    import re

    notif = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)
    tid_re = re.compile(r"<task-id>\s*([^<\s]+)\s*</task-id>")
    summ = re.compile(r"<summary>(.*?)</summary>", re.S)
    if until is not None and until.tzinfo is not None:
        until = until.astimezone().replace(tzinfo=None)
    state: Dict[str, tuple] = {}  # agentId → (живой?, подпись)
    for row in (rows(transcript) if source_rows is None else source_rows):
        if until is not None:
            at = row_at(row)
            if at is not None and at > until:
                continue
        if row.get("type") == "assistant":
            for b in (row.get("message") or {}).get("content") or []:
                if (isinstance(b, dict) and b.get("type") == "tool_use"
                        and b.get("name") == "SendMessage"):
                    to = str((b.get("input") or {}).get("to") or "")
                    if to in state:
                        state[to] = (True, "возобновлён письмом после итога")
            continue
        if row.get("type") not in ("user", "attachment"):
            continue
        blob = json.dumps(row.get("message") or row.get("attachment") or {},
                          ensure_ascii=False)
        if "task-notification" not in blob:
            continue
        text = blob.replace("\\n", "\n").replace('\\"', '"')
        for m in notif.finditer(text):
            body = m.group(1)
            t = tid_re.search(body)
            if not t:
                continue
            s_ = summ.search(body)
            what = " ".join((s_.group(1) if s_ else "итог воркера").split())[:80]
            state[t.group(1)] = (any(k in body for k in _INTERIM), what)
    return ["{0} «{1}»".format(a, w) for a, (live, w) in state.items() if live]


_BG_ID = re.compile(r"running in background with ID:\s*([\w-]+)")
_TASK_ID = re.compile(r"<task-id>\s*([\w-]+)\s*</task-id>")
_AO_SPAWN_CMD = re.compile(r"(?:^|[\s;&|(`$])ao\s+spawn\b")
_AO_SPAWNED = re.compile(r"spawned session (\S+)")


def _tool_result_text(block: dict) -> str:
    c = block.get("content")
    if isinstance(c, list):
        return "\n".join(str(x.get("text") or "") for x in c if isinstance(x, dict))
    return str(c or "")


def background_shells(transcript: Path,
                      source_rows: Optional[Iterable[dict]] = None) -> List[str]:
    """Фоновые команды самой сессии (``Bash`` с ``run_in_background``), у которых ещё
    нет ``<task-notification>`` со своим task-id и которые не остановлены
    (``TaskStop``/``KillShell``) — команда ещё идёт (полный pytest, CI). Сабагентов
    здесь нет — их ведёт ``background_workers``/:func:`interim_workers`.
    → ``["<task-id> «описание»", …]``."""
    calls: Dict[str, str] = {}      # tool_use_id → описание
    launched: Dict[str, str] = {}   # task-id → описание
    done = set()
    for row in (rows(transcript) if source_rows is None else source_rows):
        msg = row.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if row.get("type") == "assistant" and isinstance(content, list):
            for b in content:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                inp = b.get("input") or {}
                if b.get("name") == "Bash" and inp.get("run_in_background"):
                    calls[str(b.get("id"))] = " ".join(str(
                        inp.get("description") or inp.get("command") or "").split())[:80]
                elif b.get("name") in ("TaskStop", "KillShell", "KillBash"):
                    for v in inp.values():
                        done.add(str(v))
            continue
        blob = json.dumps(row, ensure_ascii=False)
        if "task-notification" in blob:
            done.update(_TASK_ID.findall(blob.replace("\\n", " ")))
        if isinstance(content, list):
            for b in content:
                if (isinstance(b, dict) and b.get("type") == "tool_result"
                        and str(b.get("tool_use_id")) in calls):
                    m = _BG_ID.search(_tool_result_text(b))
                    if m:
                        launched[m.group(1)] = calls[str(b.get("tool_use_id"))]
    return ["{0} «{1}»".format(t, d or "фоновая команда")
            for t, d in launched.items() if t not in done]


def ao_spawned(transcript: Path,
               source_rows: Optional[Iterable[dict]] = None) -> List[str]:
    """AO-сессии, которые сессия сама подняла ``ao spawn`` (Bash; ответ AO
    «spawned session <id>») — её ao-воркеры. Живы ли — решает зовущий по AO
    (у AO нет поля «кто породил»). → AO-id в порядке запуска, без повторов."""
    calls = set()
    out: List[str] = []
    for row in (rows(transcript) if source_rows is None else source_rows):
        msg = row.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if (row.get("type") == "assistant" and b.get("type") == "tool_use"
                    and b.get("name") == "Bash"
                    and _AO_SPAWN_CMD.search(str((b.get("input") or {}).get("command") or ""))):
                calls.add(str(b.get("id")))
            elif b.get("type") == "tool_result" and str(b.get("tool_use_id")) in calls:
                for h in _AO_SPAWNED.findall(_tool_result_text(b)):
                    h = h.strip("\"'.,;")
                    if h and h not in out:
                        out.append(h)
    return out


def last_activity(transcript: Path) -> Optional[datetime]:
    last = None
    for row in rows(transcript):
        at = row_at(row)
        if at is not None and (last is None or at > last):
            last = at
    return last


def pending_forwards(home: Path) -> List[Dict[str, str]]:
    """Письма ``state/forward-pending.jsonl`` (ждут приёма новой, SOC-101 F68)."""
    try:
        lines = (paths.state_dir(Path(home)) / PENDING_LOG).read_text(
            encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            item = json.loads(ln)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def parse_iso(raw: object) -> Optional[datetime]:
    try:
        ts = datetime.fromisoformat(str(raw or "").strip())
    except ValueError:
        return None
    return ts.astimezone().replace(tzinfo=None) if ts.tzinfo else ts


def pending_for(home: Path, offer: str) -> int:
    try:
        return sum(1 for i in pending_forwards(home) if str(i.get("offer") or "") == offer)
    except Exception:  # noqa: BLE001
        return 0

"""tide.adapters.claude_proc — жив ли процесс claude этой сессии (SOC-167).

Вторая проверка живости, кроме ответа AO и свежести транскрипта. Живой случай 10.10:
AO после своего перезапуска в 11:32 (−03) показывал ``exited`` у ~8 живых сессий, а
простаивающая ведущая (app-development-54 с 08:33, nochnaya-smena-9 с 11:16 до 12:04)
транскрипт не пишет — по AO и транскрипту она «мертва», хотя её claude работает.

Одна выборка ``ps -axww -o pid=,ppid=,command=`` (с таймаутом) на проход сторожа;
по ней процесс сессии находится так (что реально видно на этой машине):

* ``claude … --session-id=<uuid>`` (так AO и tide запускают claude; ``--resume <uuid>``
  — так же) — ``<uuid>`` = ``claude-session:`` из паспорта;
* запасной ключ — claude-потомок ``ao chat-host <ao-id> …`` (хозяин чата AO, его argv
  несёт id AO-сессии): если AO поднял claude под другим uuid, сессия всё равно найдётся.

Ошибка ``ps`` (нет бинаря, таймаут, мусор) → :func:`snapshot` даёт ``None`` —
«неизвестно»; решать за вызывающим (сторож сироты: неизвестно → не сирота).
Каналы ``/tmp/cc-socks/<pid>.sock`` не берём: они копятся (не чистятся после смерти
процесса) и требуют ``lsof`` на каждый pid — дороже и шумнее одного ``ps``.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, NamedTuple, Optional

PS_TIMEOUT = 5.0
_SID_RE = r"--(?:session-id|resume)(?:=|\s+){0}(?:\s|$)"
_HOST_RE = r"(?:^|\s|/)ao\s+chat-host\s+{0}(?:\s|$)"
_ANCESTRY = 4                                  # claude ← node ← ao chat-host: хватает 4


class Proc(NamedTuple):
    pid: int
    ppid: int
    command: str


def parse_ps(text: str) -> List[Proc]:
    """Строки ``pid ppid command`` → список :class:`Proc` (битые строки — мимо)."""
    out: List[Proc] = []
    for ln in text.splitlines():
        parts = ln.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            out.append(Proc(int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    return out


def snapshot(*, timeout: float = PS_TIMEOUT,
             run: Optional[Callable[..., "subprocess.CompletedProcess"]] = None
             ) -> Optional[List[Proc]]:
    """Одна выборка процессов машины; ``None`` — прочесть не вышло (неизвестно)."""
    try:
        proc = (run or subprocess.run)(["ps", "-axww", "-o", "pid=,ppid=,command="],
                                       capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    procs = parse_ps(proc.stdout or "")
    return procs or None                       # пустой ps на живой машине — это сбой


def is_claude(command: str) -> bool:
    """argv[0] — бинарь ``claude`` (не ``--chrome-native-host`` и не шелл с текстом)."""
    head = command.split(None, 1)[0] if command.strip() else ""
    return os.path.basename(head) == "claude" and "--chrome-native-host" not in command


def claude_pid(procs: List[Proc], *, session_id: str = "", ao_handle: str = ""
               ) -> Optional[int]:
    """pid живого claude этой сессии в выборке *procs* — или ``None``, такого нет."""
    sid_re = re.compile(_SID_RE.format(re.escape(session_id))) if session_id else None
    host_re = re.compile(_HOST_RE.format(re.escape(ao_handle))) if ao_handle else None
    by_pid = {p.pid: p for p in procs}
    for p in procs:
        if not is_claude(p.command):
            continue
        if sid_re is not None and sid_re.search(p.command):
            return p.pid
    if host_re is None:
        return None
    for p in procs:
        if not is_claude(p.command):
            continue
        up, seen = by_pid.get(p.ppid), 0
        while up is not None and seen < _ANCESTRY:
            if host_re.search(up.command):
                return p.pid
            up, seen = by_pid.get(up.ppid), seen + 1
    return None


# --- общий вердикт живости: транскрипт ИЛИ процесс ----------------------------------

FRESH_MIN = 5.0                                # транскрипт свежее — сессия жива (SOC-167: 5, не 15 — есть и проверка процесса)


def transcript_age_min(sid: str, *, now: Optional[datetime] = None) -> Optional[float]:
    """Сколько минут назад писался транскрипт claude-сессии *sid*
    (:func:`tide.context_fill.transcript_for`) — или None, нет на диске."""
    from .. import context_fill

    try:
        p = context_fill.transcript_for(sid)
        if p is None:
            return None
        mtime = datetime.fromtimestamp(Path(p).stat().st_mtime)
    except OSError:
        return None
    return max(0.0, ((now or datetime.now()) - mtime).total_seconds() / 60.0)


def liveness(*, session_ids: Iterable[str] = (), ao_handle: str = "",
             fresh_min: float = FRESH_MIN,
             procs: Optional[Callable[[], Optional[List[Proc]]]] = None,
             now: Optional[datetime] = None) -> Dict[str, Any]:
    """Жива ли claude-сессия, кроме ответа AO → ``{"alive": True|False|None, "by":
    "transcript"|"process"|"", "age": мин|None, "pid": int|None}``.

    Жива — транскрипт любого из *session_ids* свежее *fresh_min* мин (0 — не смотреть)
    ИЛИ живой claude этой сессии (:func:`claude_pid`; выборка *procs*, по умолчанию
    свой :func:`snapshot`). ``ps`` не прочёлся — ``alive=None`` («неизвестно»)."""
    sids = [s.strip() for s in session_ids if str(s or "").strip() not in ("", "-")]
    ages = [a for a in (transcript_age_min(s, now=now) for s in sids) if a is not None]
    age = min(ages) if ages else None
    out: Dict[str, Any] = {"alive": False, "by": "", "age": age, "pid": None}
    if fresh_min > 0 and age is not None and age < fresh_min:
        out.update(alive=True, by="transcript")
        return out
    try:
        snap = (procs or snapshot)()
    except Exception:  # noqa: BLE001
        snap = None
    if snap is None:
        out["alive"] = None
        return out
    for s in sids or [""]:
        pid = claude_pid(snap, session_id=s, ao_handle=ao_handle)
        if pid:
            out.update(alive=True, by="process", pid=pid)
            return out
    return out


__all__ = ["FRESH_MIN", "Proc", "claude_pid", "is_claude", "liveness", "parse_ps",
           "snapshot", "transcript_age_min"]

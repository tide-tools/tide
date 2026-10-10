"""tide.hooks.perm_notify — PermissionRequest ``tide hook perm-notify``: только сообщить (SOC-108).

Беда (09.10): AO-сессия встала на диалоге разрешения («Allow Bash …?»), а Гриша
об этом не знает — окно никто не смотрит. Слово Гриши (tg 3573): «вопросы доносить
в TG».

Хук ТОЛЬКО СООБЩАЕТ. Он ничего не разрешает и не запрещает: на stdout не пишет
ничего, решение остаётся за обычным диалогом Claude Code в окне сессии. Применять
ответ из TG к сессии нельзя до отдельного «да» Гриши (угон TG = удалённое
«разрешить»), поэтому в TG уходит простое сообщение, не --poll.

Что делает в AO-сессии (``$AO_SESSION_ID`` задан):

1. дописывает строку в ``<проект>/.tide/state/blocked-perms.jsonl``
   ``{id, ts, project, ao_session, thread, role, claude_session, cwd, tool, input, key}``
   — ``input`` краткий (команда / путь / url), секреты замаскированы, ≤ 300 знаков;
2. ставит сообщение в outbox пульта через ``$TIDE_ALERT_CMD`` (tg-alert,
   ``TIDE_ALERT_ROUTE=human``, ``--html``).

Дедуп: тот же запрос (сессия + инструмент + вход) не чаще раза в
:data:`DEDUP_MIN` минут — повтор не пишется и не шлётся.

Молчит (exit 0, пустой stdout): ``TIDE_PERM_NOTIFY=off``, нет ``$AO_SESSION_ID``,
повтор в окне дедупа. Любая ошибка — тихий выход 0. Весь хук укладывается в
:data:`BUDGET_SEC` секунд (сторож по SIGALRM + таймаут команды пульта).
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .. import paths

OFF_ENV = "TIDE_PERM_NOTIFY"
AO_SESSION_ENV = "AO_SESSION_ID"
ALERT_CMD_ENV = "TIDE_ALERT_CMD"  # тот же канал, что у autohandoff.send_alert

BLOCKED_PERMS_FILE = "blocked-perms.jsonl"
DEDUP_MIN = 10
INPUT_MAX = 300
BUDGET_SEC = 1.0          # тело хука (старт CLI tide ~0.5–1.5 с сверху)
ALERT_TIMEOUT_SEC = 0.8   # tg-alert только кладёт файл в outbox
TAIL_BYTES = 256 * 1024   # хвост jsonl для дедупа

MESSAGE_TEMPLATE = (
    "❓ <b>{where}</b> ждёт разрешения в окне: <code>{tool}</code> <code>{inp}</code>. "
    "Решить можно только в окне сессии; ответ здесь пока ничего не применяет."
)

MASK = "***"

# --- маскирование секретов ------------------------------------------------------

_SECRET_PATTERNS = (
    # Authorization: Bearer / Basic
    re.compile(r"(?i)(\b(?:bearer|basic)\s+)([A-Za-z0-9._~+/=-]{8,})"),
    # user:pass@ в URL
    re.compile(r"(://[^/\s:@]+:)([^@\s/]+)(@)"),
    # ключ=значение / ключ: значение, где имя похоже на секрет
    re.compile(r"(?i)(\b[\w.-]*(?:token|secret|passw(?:or)?d|api[_-]?key|apikey|auth|"
               r"credential|private[_-]?key|access[_-]?key)[\w.-]*[\"']?\s*[=:]\s*[\"']?)"
               r"([^\s\"'&]{4,})"),
    # --token VALUE (флаг с пробелом)
    re.compile(r"(?i)(--[\w-]*(?:token|secret|passw(?:or)?d|api[_-]?key|apikey|"
               r"credential|private[_-]?key|access[_-]?key)[\w-]*\s+[\"']?)([^\s\"'&]{4,})"),
)

_TOKEN_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),                 # OpenAI / Anthropic
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),  # GitHub
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),          # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                     # AWS
    re.compile(r"\blin_(?:api|oauth)_[A-Za-z0-9]{20,}"),     # Linear
    re.compile(r"\br8_[A-Za-z0-9]{20,}"),                    # Replicate
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),                 # Google
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
    re.compile(r"(?<!\d)\d{8,10}:[A-Za-z0-9_-]{30,}"),            # Telegram bot token
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
               re.S),
    # длинная «случайная» строка: hex ≥ 32 или base64-подобная ≥ 40 с цифрами
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
    re.compile(r"(?<![\w/.-])(?=[A-Za-z0-9+/_-]*\d)(?=[A-Za-z0-9+/_-]*[A-Za-z])"
               r"[A-Za-z0-9+/_-]{40,}={0,2}"),
)


def mask_secrets(text: str) -> str:
    """Заменить то, что похоже на токены/ключи/пароли, на ``***``."""
    out = text
    for pat in _SECRET_PATTERNS:
        if pat.groups == 3:
            out = pat.sub(lambda m: m.group(1) + MASK + m.group(3), out)
        else:
            out = pat.sub(lambda m: m.group(1) + MASK, out)
    for pat in _TOKEN_PATTERNS:
        out = pat.sub(MASK, out)
    return out


# --- краткий вход ----------------------------------------------------------------

_INPUT_KEYS = ("command", "file_path", "notebook_path", "path", "url", "pattern", "query",
               "skill", "prompt")


def summarize_input(tool_input) -> str:
    """Команда / путь / url из tool_input — одной строкой, секреты замаскированы,
    обрезано до :data:`INPUT_MAX` знаков."""
    if isinstance(tool_input, dict):
        raw = ""
        for key in _INPUT_KEYS:
            val = tool_input.get(key)
            if isinstance(val, str) and val.strip():
                raw = val
                break
        if not raw:
            raw = json.dumps(tool_input, ensure_ascii=False, sort_keys=True)
    elif tool_input is None:
        raw = ""
    else:
        raw = str(tool_input)
    one = " ".join(mask_secrets(raw).split())
    if len(one) > INPUT_MAX:
        one = one[: INPUT_MAX - 1] + "…"
    return one


# --- где мы: проект · нить · роль -------------------------------------------------

def _where(root: Optional[Path], session_id: str) -> dict:
    """``{project, thread, role, source}`` — нить и роль, если сессия нашлась."""
    ao = (os.environ.get(AO_SESSION_ENV) or "").strip()
    project = Path(root).name if root else Path(os.getcwd()).name
    out = {"project": project, "thread": "", "role": "", "source": "{0}//{1}".format(project, ao)}
    if root is None or not session_id:
        return out
    try:
        from .. import fields, offload, slug
        entry = offload.find_session_by_claude_id(Path(root), session_id)
        if entry is None:
            return out
        out["thread"] = slug.entry_slug(entry.parent.parent.name)
        out["role"] = (fields.read_field(entry / "arc.md", "role") or "").strip()
        out["source"] = "{0}/{1}/{2}".format(project, out["thread"], slug.entry_slug(entry.name))
    except Exception:  # noqa: BLE001 — нить не нашлась → без неё
        pass
    return out


# --- журнал + дедуп --------------------------------------------------------------

def _key(ao: str, session_id: str, tool: str, inp: str) -> str:
    raw = "\x1f".join((ao, session_id, tool, inp))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _parse_ts(raw) -> Optional[datetime]:
    if not isinstance(raw, str):
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def recently_sent(target: Path, key: str, now: datetime, minutes: int = DEDUP_MIN) -> bool:
    """Был ли запрос с тем же ключом в хвосте журнала за последние *minutes* мин."""
    if not target.is_file():
        return False
    cutoff = now - timedelta(minutes=minutes)
    with open(target, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        end = fh.tell()
        fh.seek(max(0, end - TAIL_BYTES))
        data = fh.read()
    for line in data.split(b"\n"):
        if key.encode() not in line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        ts = _parse_ts(rec.get("ts")) if isinstance(rec, dict) else None
        if rec.get("key") == key and ts is not None and ts >= cutoff:
            return True
    return False


def journal_path(root: Optional[Path]) -> Path:
    """``<проект>/.tide/state/blocked-perms.jsonl``; без проекта — state control-home.

    Не ``~/.tide/state/``: ``~/.tide`` делал домашнюю папку «проектом» для
    подъёма к корню (edit-gate блокировал репо без своей .tide). Нет и
    control-home — временная папка, но не дом."""
    if root is not None:
        return paths.state_dir(Path(root)) / BLOCKED_PERMS_FILE
    try:
        return paths.state_dir(paths.control_home()) / BLOCKED_PERMS_FILE
    except Exception:  # noqa: BLE001 — нет control-home
        return Path(tempfile.gettempdir()) / "tide-state" / BLOCKED_PERMS_FILE


# --- сообщение -------------------------------------------------------------------

def message_text(where: dict, tool: str, inp: str) -> str:
    """HTML-текст для tg-alert --html (одной строкой: теги закрыты в строке)."""
    label = where["project"]
    if where.get("thread"):
        label += " · " + where["thread"]
    ao = (os.environ.get(AO_SESSION_ENV) or "").strip()
    if ao:
        label += " ({0})".format(ao)
    esc = lambda s: html.escape(" ".join(str(s).split()), quote=False)  # noqa: E731
    return MESSAGE_TEMPLATE.format(where=esc(label), tool=esc(tool or "?"), inp=esc(inp or "—"))


def send_message(text: str, source: str) -> Optional[int]:
    """``$TIDE_ALERT_CMD --html <text>`` (route=human) → код; нет команды — None."""
    cmd = (os.environ.get(ALERT_CMD_ENV) or "").strip()
    if not cmd:
        return None
    env = dict(os.environ, TIDE_ALERT_TEXT=text, TIDE_ALERT_ROUTE="human",
               TIDE_ALERT_SOURCE=source)
    try:
        proc = subprocess.run(shlex.split(cmd) + ["--html", text], env=env,
                              timeout=ALERT_TIMEOUT_SEC, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)
        return proc.returncode
    except (OSError, ValueError, subprocess.SubprocessError):
        return -1


# --- главное ---------------------------------------------------------------------

def notify(payload: dict, *, now: Optional[datetime] = None) -> Optional[dict]:
    """Записать запрос и поставить сообщение в пульт → строка журнала (или None,
    если хук молчит). Ничего не решает."""
    if (os.environ.get(OFF_ENV) or "").strip().lower() == "off":
        return None
    ao = (os.environ.get(AO_SESSION_ENV) or "").strip()
    if not ao:
        return None  # человек в своём терминале — он и так видит диалог
    tool = str(payload.get("tool_name") or "")
    session_id = str(payload.get("session_id") or "")
    cwd = str(payload.get("cwd") or "") or os.getcwd()
    inp = summarize_input(payload.get("tool_input"))
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    root = paths.find_tide_root(Path(cwd))
    target = journal_path(root)
    key = _key(ao, session_id, tool, inp)
    if recently_sent(target, key, stamp):
        return None

    where = _where(root, session_id)
    row = {
        "id": str(uuid.uuid4()),
        "ts": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project": where["project"],
        "ao_session": ao,
        "thread": where["thread"],
        "role": where["role"],
        "claude_session": session_id,
        "cwd": cwd,
        "tool": tool,
        "input": inp,
        "key": key,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    try:
        row["alert_rc"] = send_message(message_text(where, tool, inp), where["source"])
    except Exception:  # noqa: BLE001 — пульт не должен ломать хук
        row["alert_rc"] = -1
    return row


class _Budget(Exception):
    pass


def _on_alarm(signum, frame):  # noqa: ARG001
    raise _Budget()


def cmd_perm_notify(args) -> int:
    """``tide hook perm-notify`` — PermissionRequest; stdout пуст, всегда exit 0."""
    old = None
    try:
        if hasattr(signal, "setitimer"):
            old = signal.signal(signal.SIGALRM, _on_alarm)
            signal.setitimer(signal.ITIMER_REAL, BUDGET_SEC)
        payload = json.loads(sys.stdin.read() or "{}")
        if isinstance(payload, dict):
            notify(payload)
    except BaseException:  # noqa: BLE001 — тихо: хук никогда не мешает диалогу
        pass
    finally:
        try:
            if old is not None:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, old)
        except Exception:  # noqa: BLE001
            pass
    return 0

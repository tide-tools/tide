"""tide.arc.dialog — хвост журнала диалога с человеком для посева автохендоффа.

Журнал tide НЕ ведёт: его пишет тот, кто носит реплики (пульт менеджера —
``dialog.jsonl``, строка на реплику, обе стороны). Здесь — только чтение хвоста,
чтобы новая сессия (:mod:`tide.launcher.autohandoff`) знала, о чём говорили
последним, без пересказа чата.

Где журнал — настраивается, без привязки к конкретному проекту:

1. ``$TIDE_DIALOG_LOG`` — путь к файлу;
2. поле ``dialog-log:`` в паспорте нити (абсолютный путь или от корня проекта).

Нет ни того, ни другого, или файла нет — хвоста нет, и это не ошибка.

Форматы: ``.jsonl`` (``{"ts", "dir": "in"|"out", "kind", "text"}`` — как у
пульта) рендерится строкой на реплику, ответ обрезан до :data:`CLIP`; любой
другой файл (например ``dialog-tail.md``) отдаётся последними непустыми строками.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from .. import fields, paths

LOG_ENV = "TIDE_DIALOG_LOG"
LOG_FIELD = "dialog-log"

HUMAN = "Гриша"
AGENT = "агент"

# Реплика бывает простынёй — посеву нужна суть, полный текст живёт в журнале.
CLIP = 500

# Журнал растёт; хвост всегда у конца файла.
_TAIL_BYTES = 256 * 1024


def clip(text: str, limit: int = CLIP) -> str:
    flat = " ".join(str(text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: max(0, limit - 1)].rstrip() + "…"


def log_path(thread_dir: Optional[Path] = None) -> Optional[Path]:
    """Путь к журналу: env, иначе поле ``dialog-log:`` нити; None — не настроен."""
    raw = (os.environ.get(LOG_ENV) or "").strip()
    base: Optional[Path] = None
    if not raw and thread_dir is not None:
        from .stream import passport_path

        raw = (fields.read_field(passport_path(Path(thread_dir)), LOG_FIELD) or "").strip()
        base = paths.find_tide_root(Path(thread_dir))
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_absolute() and base is not None:
        p = base / p
    return p


def _read_tail(path: Path) -> str:
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - _TAIL_BYTES))
        return fh.read().decode("utf-8", errors="ignore")


def render_row(row: dict) -> Optional[str]:
    """Одна строка jsonl пульта → ``- ts · кто · канал · текст`` (None — пустая)."""
    text = clip(row.get("text") or "")
    if not text:
        return None
    who = AGENT if row.get("dir") == "out" else HUMAN
    return "- {0} · {1} · {2} · {3}".format(
        row.get("ts") or "?", who, row.get("kind") or "?", text)


def tail(thread_dir: Optional[Path] = None, n: int = 12) -> List[str]:
    """Последние *n* реплик журнала (старые выше); [] — журнала нет/не читается."""
    path = log_path(thread_dir)
    if path is None or not path.is_file():
        return []
    try:
        text = _read_tail(path)
    except OSError:
        return []
    lines = text.splitlines()
    if path.suffix == ".jsonl":
        out: List[str] = []
        for raw in lines:
            try:
                row = json.loads(raw)
            except ValueError:
                continue  # the cut first line of the tail, or a torn write
            if isinstance(row, dict):
                line = render_row(row)
                if line:
                    out.append(line)
        return out[-n:] if n > 0 else out
    kept = [ln.rstrip() for ln in lines if ln.strip()]
    return kept[-n:] if n > 0 else kept


__all__ = ["AGENT", "CLIP", "HUMAN", "LOG_ENV", "LOG_FIELD", "clip", "log_path",
           "render_row", "tail"]

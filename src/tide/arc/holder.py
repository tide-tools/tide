"""tide.arc.holder — адрес нити: какая сессия её сейчас ведёт (``tide thread holder``).

Канон №1: нить ведёт новейшая принятая сессия — она выводится, не штампуется
(:func:`tide.acts.head_session`). Здесь к ней добавляется, ГДЕ эта сессия живёт:
claude-id из паспорта → AO-сессия по реестру запусков (``terminals.json``,
``sid → handle``), запасной путь — тайник AO (``ao-sessions.json``,
``ao_id → sid``). Пульт и любой внешний писатель спрашивают отсюда, кому слать
реплику, вместо жёсткого ``target.json``.

Живость сессии проверяет вызывающий (адаптер AO — слой выше): сюда она приходит
функцией *alive*, чтобы домен не тянулся к терминалам.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, Optional

from .. import acts, fields, paths, registry, sessions, slug
from .stream import StreamError


class HolderError(StreamError):
    """Нет такой нити."""


def _thread_dir(root: Path, ref: str) -> Path:
    from .work import thread_dir

    d = thread_dir(Path(root), ref)
    if d is None:
        raise HolderError("thread holder: нет нити {0!r} в {1}".format(
            ref, paths.arcs_dir(Path(root))))
    return d


def ao_handle_for(home: Optional[Path], sid: str) -> str:
    """AO-сессия, в которой живёт claude-сессия *sid* — или ``""``."""
    if not sid or home is None:
        return ""
    try:
        handle = (registry.read(home).get(sid) or {}).get("handle") or ""
    except Exception:  # noqa: BLE001 — a broken registry just means «не знаю»
        handle = ""
    if handle:
        return str(handle).strip()
    try:
        stash = sessions._read_ao_stash(home)
    except Exception:  # noqa: BLE001
        return ""
    for ao_id, rec in stash.items():
        if ((rec or {}).get("sid") or "").strip() == sid:
            return ao_id
    return ""


def holder(root: Path, ref: str, *, home: Optional[Path] = None,
           alive: Optional[Callable[[str], bool]] = None) -> Dict[str, object]:
    """``{thread, arc, claude_session, ao_session, alive}`` для нити *ref*.

    ``arc`` — ``"<нить>/<сессия>"`` или ``""``, когда сессий нет; ``alive`` —
    True/False по *alive*, None — когда AO-сессии не знаем или проверять нечем.
    """
    tdir = _thread_dir(root, ref)
    if home is None:
        try:
            home = paths.control_home(Path(root))
        except Exception:  # noqa: BLE001 — no control-home ⇒ no registry to read
            home = None
    head = acts.head_session(tdir)
    sid = ""
    if head is not None:
        sid = (fields.read_field(head / "arc.md", "claude-session") or "").strip()
    handle = ao_handle_for(home, sid)
    state: Optional[bool] = None
    if handle and alive is not None:
        try:
            state = bool(alive(handle))
        except Exception:  # noqa: BLE001
            state = None
    return {
        "thread": tdir.name,
        "arc": "{0}/{1}".format(tdir.name, head.name) if head is not None else "",
        "claude_session": sid,
        "ao_session": handle,
        "alive": state,
    }


def render(rec: Dict[str, object]) -> str:
    alive = {True: "жива", False: "не отвечает", None: "не проверял"}[rec.get("alive")]
    if not rec.get("arc"):
        return "нить {0}: сессий нет".format(slug.entry_slug(str(rec["thread"])))
    return "\n".join([
        "нить {0} ведёт {1}".format(slug.entry_slug(str(rec["thread"])), rec["arc"]),
        "  claude: {0}".format(rec.get("claude_session") or "—"),
        "  AO: {0} ({1})".format(rec.get("ao_session") or "—", alive),
    ])


__all__ = ["HolderError", "ao_handle_for", "holder", "render"]

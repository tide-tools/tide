"""tide.escalate — «не справляюсь»: письмо main проекта, без квитанции — менеджеру (SOC-157).

Зачем (слово Гриши 09.10): агент, который застрял, молча крутится, а проблема
всплывает только на передаче. Теперь застрявший сам поднимает её наверх одной
командой::

    tide escalate --what "…" --tried "…" --need "…" [--thread <моя нить>]

* письмо — почтой нити (:func:`tide.mail.send`): файл в ``<main>/inbox``, короткий
  пинг ведущей сессии main проекта, квитанция ``tide mail read``;
* адресат — нить ``main`` проекта текущей сессии; если пишет сама main (или в
  проекте нет main) — нить менеджера (:data:`tide.whois.MANAGER_THREAD` /
  ``manager.json``);
* без квитанции :data:`ACK_TIMEOUT_MIN` минут — тот же ожидальщик ящика почты
  (``tide mail watch``) шлёт тревогу менеджеру (``send_alert(route="manager")``),
  не дожидаясь обычных повторов пинга. Новый демон не нужен.

Пороги «не справляюсь» живут одним местом — :mod:`tide.escalate_rules`; их уточнит
continuity-audit по цифрам.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from . import mail, paths, slug
from .arc.stream import StreamError

# пороги и текст правил — одно место (чистые данные, их же читает сид)
from .escalate_rules import (ACK_TIMEOUT_MIN, ESCALATE_RULE, EXP_RULE,  # noqa: F401
                             FAILED_ATTEMPTS, STALL_MIN, seed_rules)

KIND = "escalation"


class EscalateError(StreamError):
    """Эскалация не легла."""


def _is_main(name: str) -> bool:
    return slug.entry_slug(name).lstrip("@") == "main"


def _my_thread(root: Path, ref: Optional[str]) -> Optional[Path]:
    """Нить того, кто эскалирует: --thread, иначе нить текущей claude-сессии."""
    if ref:
        try:
            return mail.thread_dir(ref, root)
        except StreamError:
            return None
    sid = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    if not sid:
        return None
    try:
        from . import offload

        entry = offload.find_session_by_claude_id(root, sid)
        return entry.parents[1] if entry is not None else None
    except Exception:  # noqa: BLE001
        return None


def _manager_ref() -> str:
    from . import whois

    data = whois.read_manager_file()
    t = data.get("thread", "")
    ref = (os.environ.get(whois.MANAGER_THREAD_ENV) or "").strip() or whois.MANAGER_THREAD
    if t:
        ref = t if "/" in t else "{0}/{1}".format(ref.rpartition("/")[0] or "management", t)
    return ref


def target(root: Path, mine: Optional[Path]) -> Dict[str, object]:
    """Куда письмо → ``{tdir, role}``: main проекта; сама main / нет main — менеджер."""
    if mine is None or not _is_main(mine.name):
        try:
            return {"tdir": mail.thread_dir("main", root), "role": "main проекта"}
        except StreamError:
            pass
    try:
        return {"tdir": mail.thread_dir(_manager_ref(), root), "role": "менеджер"}
    except StreamError as exc:
        raise EscalateError("escalate: не нашёл ни main проекта, ни нить менеджера ({0})"
                            .format(exc)) from None


def letter_text(what: str, tried: str, need: str, *, sender: str, mine: str,
                ack_hint: str) -> str:
    """Письмо по контракту: заголовок «эскалация: …», затем Почему / Пробовал / Нужно."""
    return ("эскалация: {what}\n\n"
            "Почему: {what}\n"
            "Пробовал: {tried}\n"
            "Нужно: {need}\n\n"
            "от: {sender}{mine}\n"
            "{hint}\n").format(what=what.strip(), tried=tried.strip(), need=need.strip(),
                               sender=sender, mine=" · нить {0}".format(mine) if mine else "",
                               hint=ack_hint)


def escalate(what: str, tried: str, need: str, *, thread: Optional[str] = None,
             root: Optional[Path] = None, sender: Optional[str] = None,
             do_ping: bool = True, watch: Optional[bool] = None) -> Dict[str, object]:
    """Положить письмо-эскалацию в ящик адресата с окном тревоги :data:`ACK_TIMEOUT_MIN`."""
    fields_ = {"--what": what, "--tried": tried, "--need": need}
    empty = [k for k, v in fields_.items() if not (v or "").strip()]
    if empty:
        raise EscalateError("escalate: пусто {0} — нужны все три: что / что пробовал / "
                            "что нужно".format(", ".join(empty)))
    root = Path(root) if root is not None else paths.find_tide_root()
    if root is None:
        raise EscalateError("escalate: не в tide-проекте")
    mine = _my_thread(root, thread)
    to = target(root, mine)
    tdir = to["tdir"]
    who = (sender or "").strip() or mail._who_am_i()
    addr = mail.address_of(tdir)
    hint = ("квитанция: tide mail read {0} <номер> — без неё через {1} мин уйдёт менеджеру"
            .format(addr, ACK_TIMEOUT_MIN))
    text = letter_text(what, tried, need, sender=who,
                       mine=mail.address_of(mine) if mine is not None else "",
                       ack_hint=hint)
    out = mail.send(str(tdir.name), text, sender=who, root=tdir.parent.parent.parent,
                    do_ping=do_ping, watch=watch, alert_after_min=ACK_TIMEOUT_MIN,
                    kind=KIND)
    out.update({"to": addr, "role": to["role"], "alert_after": ACK_TIMEOUT_MIN})
    return out


# --- CLI -------------------------------------------------------------------------

def _cmd_escalate(args) -> int:
    out = escalate(args.what, args.tried, args.need, thread=args.thread)
    p = out.get("ping") or {}
    how = ("пинг {0} → код {1}".format(p["to"], p.get("code")) if p.get("to")
           else "пинг не ушёл: нет ведущей AO-сессии (ожидальщик повторит)")
    print("эскалация {0} → {1} ({2}) · {3} · {4}".format(
        out["num"], out["to"], out["role"], out["path"], how))
    print("без квитанции {0} мин — тревога менеджеру (ожидальщик почты)".format(
        out["alert_after"]))
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "escalate",
        help="не справляюсь: письмо main проекта (почтой нити); без квитанции "
             "{0} мин — тревога менеджеру (SOC-157)".format(ACK_TIMEOUT_MIN),
        description=ESCALATE_RULE)
    p.add_argument("--what", required=True, help="что случилось (станет «Почему:»)")
    p.add_argument("--tried", required=True, help="что пробовал («Пробовал:»)")
    p.add_argument("--need", required=True, help="что нужно («Нужно:»)")
    p.add_argument("--thread", help="твоя нить (по умолчанию — нить текущей сессии)")
    p.set_defaults(func=_cmd_escalate, _cmd="escalate")


__all__ = ["ACK_TIMEOUT_MIN", "ESCALATE_RULE", "EXP_RULE", "EscalateError",
           "FAILED_ATTEMPTS", "KIND", "STALL_MIN", "escalate", "letter_text",
           "register", "seed_rules", "target"]

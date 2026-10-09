"""tide.hooks.handed_off — почта старой сессии после приёма идёт новой (SOC-101 C14).

После хендоффа нить ведёт новая сессия, а другие нити и скрипты по привычке пишут
старой (``ao send`` / межсессионная почта uds). Раньше переключение держалось на
ручной рассылке новой и на подсказке старой. Теперь на каждом входящем сообщении
(UserPromptSubmit) сессия, уже ОТДАВШАЯ нить (её оффер принят —
:func:`tide.handoff_queue.is_dissolved`), делает две вещи:

* пересылает текст новой механикой: ``ao send --session <AO-id новой> --message
  "[переслано от старой …, отправитель X] …"``; адрес новой — :func:`tide.whois.thread`
  по цепочке хендоффов (ведёт новейшая принятая), запасной — AO-id того, кто принял;
* печатает в свой контекст: «нить передана <новая · NN · AO-id>; переслано ей —
  скажи отправителю X новый адрес» (или «перешли сама», если переслать не вышло).

Пересылаются только сообщения других сессий — по их обёртке
(``<ao-session-delivery …>[from X]`` и ``<cross-session-message from-name="X">``);
слова человека в окне старой не трогаем. Не пересылаем то, что прислала сама новая
(иначе пинг-понг) и менеджер (он про приём уже знает механикой — ``close-proposed``),
и не шлём самой себе. Одна доставка — одна пересылка: id доставки пишется в
``.tide/state/forwarded.log`` control-home. Отправителю и Грише хук ничего не шлёт.
Выключатель — ``$TIDE_FORWARD_HANDED_OFF=off`` (тогда только подсказка).

Хук никогда не падает и не блокирует: ошибка — пустой вывод.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .. import paths, slug

FORWARD_ENV = "TIDE_FORWARD_HANDED_OFF"
FORWARD_LOG = "forwarded.log"
BODY_MAX = 12000
# ``ao send --message`` отказывает длиннее ~4075 байт UTF-8 (MESSAGE_TOO_LONG,
# проверено 09.10). Кириллица — 2 байта на букву: письмо дизайнера в 2600 знаков
# (4672 байта с шапкой) не ушло, и хук молча просил переслать руками (SOC-101 F54).
# Длинное шлём частями с запасом на шапку.
AO_MESSAGE_MAX_BYTES = 3800

_AO_DELIVERY = re.compile(
    r"<ao-session-delivery\s+id=\"([^\"]+)\"\s*>\s*\[from\s+([^\]\s]+)\]\s*(.*?)"
    r"(?:</ao-session-delivery>|\Z)", re.S)
_UDS = re.compile(
    r"<cross-session-message\s+([^>]*)>\s*(.*?)(?:</cross-session-message>|\Z)", re.S)
_ATTR = re.compile(r"([\w-]+)=\"([^\"]*)\"")


def incoming(prompt: str) -> Optional[Dict[str, str]]:
    """Сообщение другой сессии → ``{id, sender, body, channel}``; слова человека — None."""
    text = str(prompt or "")
    m = _AO_DELIVERY.search(text)
    if m:
        return {"id": m.group(1), "sender": m.group(2), "channel": "ao",
                "body": m.group(3).strip()}
    m = _UDS.search(text)
    if m:
        attrs = dict(_ATTR.findall(m.group(1)))
        sender = attrs.get("from-name") or attrs.get("from") or "?"
        body = m.group(2).strip()
        key = "uds:{0}:{1}".format(
            sender, hashlib.sha1(body.encode("utf-8")).hexdigest()[:12])
        return {"id": key, "sender": sender, "channel": "uds", "body": body}
    return None


def _sender_is(sender: str, ao_id: str) -> bool:
    """«tide-stack-24» и uds-имя «tide-stack-24-9e» — одна сессия."""
    s, a = (sender or "").strip(), (ao_id or "").strip()
    return bool(s and a) and (s == a or s.startswith(a + "-"))


def _already_in(home: Path, name: str, key: str) -> bool:
    try:
        log = paths.state_dir(Path(home)) / name
        return any(ln.split("\t", 1)[0] == key
                   for ln in log.read_text(encoding="utf-8").splitlines())
    except OSError:
        return False


def _remember_in(home: Path, name: str, key: str, line: str) -> None:
    try:
        log = paths.state_dir(Path(home)) / name
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("{0}\t{1}\t{2}\n".format(
                key, datetime.now().isoformat(timespec="seconds"), " ".join(line.split())))
    except OSError:
        pass


def _already(home: Path, key: str) -> bool:
    return _already_in(home, FORWARD_LOG, key)


def _remember(home: Path, key: str, line: str) -> None:
    _remember_in(home, FORWARD_LOG, key, line)


def successor(home: Path, rec: Dict[str, object]) -> Dict[str, str]:
    """Кто ведёт нить сейчас: ``{ao_session, title}`` — whois по цепочке, запасной —
    тот, кто принял оффер *rec* (его AO-id и название по сиду)."""
    out = {"ao_session": "", "title": ""}
    project = str(rec.get("project") or "").strip()
    thread = str(rec.get("arc") or "").split("/", 1)[0]
    if project and project != "-" and thread and thread != "-":
        try:
            from .. import whois
            w = whois.thread("{0}/{1}".format(project, slug.entry_slug(thread)), home=home)
            out = {"ao_session": w.get("ao_session", ""), "title": w.get("title", "")}
        except Exception:  # noqa: BLE001 — нет адреса по цепочке: запасной путь
            pass
    if not out["ao_session"]:
        try:
            from ..arc import holder
            out["ao_session"] = holder.ao_handle_for(Path(home), str(rec.get("taken_by") or ""))
        except Exception:  # noqa: BLE001
            pass
    if not out["title"]:
        try:
            from ..launcher import reception
            out["title"] = reception.titles(home, rec)[0]
        except Exception:  # noqa: BLE001
            out["title"] = "новая сессия"
    return out


def _address(new: Dict[str, str]) -> str:
    from ..arc.stream import with_ao_id
    title, ao = new.get("title") or "новая сессия", new.get("ao_session") or ""
    return with_ao_id(title, ao) if ao else title


def forward_text(*, old: str, sender: str, body: str, part: str = "") -> str:
    body = body if len(body) <= BODY_MAX else body[:BODY_MAX - 1] + "…"
    return "[переслано от старой {0}, отправитель {1}{2}] {3}".format(
        old, sender, ", часть {0}".format(part) if part else "", body)


def _nbytes(text: str) -> int:
    return len(text.encode("utf-8"))


def _cut(line: str, room: int) -> List[str]:
    """Строку длиннее *room* байт — на куски по границе символа."""
    out, cur = [], ""
    for ch in line:
        if _nbytes(cur + ch) > room:
            out.append(cur)
            cur = ""
        cur += ch
    return out + ([cur] if cur else [])


def forward_parts(*, old: str, sender: str, body: str,
                  limit: int = AO_MESSAGE_MAX_BYTES) -> List[str]:
    """Пересылка частями, каждая ≤ *limit* байт UTF-8 вместе с шапкой (F54).
    Режем по строкам; строка длиннее части — по символам."""
    body = body if len(body) <= BODY_MAX else body[:BODY_MAX - 1] + "…"
    whole = forward_text(old=old, sender=sender, body=body)
    if _nbytes(whole) <= limit:
        return [whole]
    head = _nbytes(forward_text(old=old, sender=sender, body="", part="99/99")) + 1
    room = max(200, limit - head)
    chunks, cur = [], ""
    for line in body.split("\n"):
        for piece in (_cut(line, room) if _nbytes(line) > room else [line]):
            joined = piece if not cur else cur + "\n" + piece
            if cur and _nbytes(joined) > room:
                chunks.append(cur)
                cur = piece
            else:
                cur = joined
    if cur:
        chunks.append(cur)
    n = len(chunks)
    return [forward_text(old=old, sender=sender, body=c, part="{0}/{1}".format(i, n))
            for i, c in enumerate(chunks, 1)]


# --- две головы (SOC-101 F49) ---------------------------------------------------

TWO_HEADS_LOG = "two-heads.log"
OLD_ROLE = ("старая — только довести своего воркера и переслать итог новой; новые "
            "задачи старой не отдавать и самой не брать")


def two_heads_text(*, thread: str, new_addr: str, old_addr: str, evidence: str = "") -> str:
    return ("tide: ⚠ две головы у нити {0}: нить ведёт новая — {1}; {2} — {3}.{4}".format(
        thread or "?", new_addr, old_addr or "старая сессия", OLD_ROLE,
        " Признак: {0}.".format(evidence) if evidence else ""))


def two_heads_alarm(home: Path, rec: Dict[str, object], *, evidence: str = "",
                    say=None) -> List[str]:
    """Тревога «две головы»: ``ao send`` новой и старой — нить ведёт новая, старая
    только доводит воркера и пересылает итог. Раз на пару (оффер в
    ``.tide/state/two-heads.log``). Вернуть AO-id, кому ушло. Никогда не падает."""
    try:
        home = Path(home)
        key = "two-heads:{0}".format(rec.get("name") or rec.get("from_session") or "?")
        if _already_in(home, TWO_HEADS_LOG, key):
            return []
        new = successor(home, rec)
        new_ao = new.get("ao_session", "")
        try:
            from ..arc import holder
            old_ao = holder.ao_handle_for(home, str(rec.get("from_session") or ""))
            if new_ao and new_ao == old_ao:
                # цепочка по чатам видит живую старую — новая та, что приняла оффер
                new_ao = holder.ao_handle_for(home, str(rec.get("taken_by") or ""))
                new = {"ao_session": new_ao, "title": new_ao or "новая сессия"}
        except Exception:  # noqa: BLE001
            old_ao = ""
        try:
            from ..launcher import reception
            old_title = reception.titles(home, rec)[1]
        except Exception:  # noqa: BLE001
            old_title = "старая сессия"
        from ..arc.stream import with_ao_id
        old_addr = with_ao_id(old_title, old_ao) if old_ao else old_title
        text = two_heads_text(thread=str(rec.get("arc") or "").split("/", 1)[0],
                              new_addr=_address(new), old_addr=old_addr, evidence=evidence)
        send = say or _ao_adapter().say
        sent = [h for h in dict.fromkeys([new_ao, old_ao]) if h and send(h, text)]
        if sent:
            _remember_in(home, TWO_HEADS_LOG, key, "→ {0}: {1}".format(
                ", ".join(sent), evidence or "-"))
        return sent
    except Exception:  # noqa: BLE001 — тревога не смеет уронить хук
        return []


def context_text(*, new_addr: str, sender: str, forwarded: Optional[str],
                 new_ao: str, mine: bool, from_new: bool = False) -> str:
    """Строка в контекст старой: кому передана нить и что делать с этим сообщением."""
    head = "tide: нить передана {0}.".format(new_addr)
    if from_new:
        # F49: новая отдаёт задачи через старую — это две головы
        return (head + " Это сообщение — от неё самой: не пересылаю. Нить ведёт она, а ты — "
                + OLD_ROLE +
                ". Задачу из него не бери: ответь ей, чтобы делала сама (воркера и его "
                "итог можешь передать ей).")
    if mine:
        return head + " Это сообщение — от неё самой или менеджера: не пересылаю."
    tell = " Скажи отправителю {0} новый адрес: {1}.".format(sender, new_addr)
    if forwarded == "sent":
        return head + " Это сообщение уже переслано ей механикой (ao send)." + tell
    how = (" (ao send --session {0} --message …)".format(new_ao) if new_ao else
           " (адрес не нашёлся — tide whois <нить>)")
    return head + " Перешли это сообщение ей" + how + "." + tell


def _ao_adapter():
    from ..adapters.ao import AoAdapter
    return AoAdapter()


def _manager_ao(home: Path) -> str:
    try:
        from .. import whois
        return whois.manager(home=home).get("ao_session", "")
    except Exception:  # noqa: BLE001
        return ""


def redirect(payload: Dict[str, object], *, home: Optional[Path] = None,
             env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Входящее сообщение старой, уже отдавшей нить → текст в её контекст (и пересылка
    новой). None — сессия нить не отдавала, или пришли слова человека."""
    e = os.environ if env is None else env
    msg = incoming(str(payload.get("prompt") or ""))
    if msg is None:
        return None
    from .. import handoff_queue as hq

    home = Path(home) if home is not None else paths.control_home()
    sids = [s for s in dict.fromkeys([str(payload.get("session_id") or ""),
                                      (e.get("CLAUDE_CODE_SESSION_ID") or "").strip()]) if s]
    rec = None
    for sid in sids:
        rec = hq.is_dissolved(home, sid)
        if rec:
            break
    if not rec:
        return None
    new = successor(home, rec)
    new_ao, mine_ao = new.get("ao_session", ""), (e.get(hq.AO_SESSION_ENV) or "").strip()
    addr = _address(new)
    from_new = _sender_is(msg["sender"], new_ao) and not (new_ao and new_ao == mine_ao)
    if from_new:
        # новая пишет старой — старая стала ретранслятором: тревога обеим (F49)
        two_heads_alarm(home, rec, evidence="новая {0} пишет старой".format(new_ao))
        return context_text(new_addr=addr, sender=msg["sender"], forwarded=None,
                            new_ao=new_ao, mine=True, from_new=True)
    skip = (_sender_is(msg["sender"], _manager_ao(home)) or (new_ao and new_ao == mine_ao))
    if skip:
        return context_text(new_addr=addr, sender=msg["sender"], forwarded=None,
                            new_ao=new_ao, mine=True)
    forwarded = None
    if new_ao and (e.get(FORWARD_ENV) or "").strip().lower() != "off":
        if _already(home, msg["id"]):
            forwarded = "sent"
        else:
            try:
                from ..launcher import reception
                old = reception.titles(home, rec)[1]
            except Exception:  # noqa: BLE001
                old = "сессии"
            if mine_ao:
                from ..arc.stream import with_ao_id
                old = with_ao_id(old, mine_ao)
            parts = forward_parts(old=old, sender=msg["sender"], body=msg["body"])
            adapter = _ao_adapter()
            ok = all(adapter.say(new_ao, text) for text in parts)
            forwarded = "sent" if ok else "not-delivered"
            if ok:
                _remember(home, msg["id"], "→ {0} от {1}{2}".format(
                    new_ao, msg["sender"],
                    " ({0} частей)".format(len(parts)) if len(parts) > 1 else ""))
    return context_text(new_addr=addr, sender=msg["sender"], forwarded=forwarded,
                        new_ao=new_ao, mine=False)


def cmd_prompt(args) -> int:
    """``tide hook handoff-confirm`` целиком: подтверждение приёма
    (:func:`tide.handoff_queue.cmd_handoff_confirm`), затем — для старой, отдавшей
    нить, — пересылка входящего новой и строка в контекст. Никогда не падает."""
    from ..handoff_queue import cmd_handoff_confirm

    try:
        raw = sys.stdin.read() if sys.stdin is not None else ""
    except (OSError, ValueError):
        raw = ""
    sys.stdin = io.StringIO(raw)
    rc = cmd_handoff_confirm(args)
    try:
        if paths.find_tide_root() is None:
            return rc
        payload = json.loads(raw or "{}")
        if isinstance(payload, dict):
            note = redirect(payload)
            if note:
                print(note)
    except Exception as exc:  # noqa: BLE001 — хук не смеет уронить сессию
        print("tide: [handed-off] skipped: {0}".format(exc), file=sys.stderr)
    return rc


__all__ = ["AO_MESSAGE_MAX_BYTES", "FORWARD_ENV", "OLD_ROLE", "cmd_prompt", "context_text",
           "forward_parts", "forward_text",
           "incoming", "redirect", "successor", "two_heads_alarm", "two_heads_text"]

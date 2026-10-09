"""tide.launcher.reception — Грише в Telegram: приём нити состоялся (или нет).

Слово Гриши (tg 3119): на каждый приём — ровно одно короткое сообщение, кто у
кого принял нить, что передано, что взято и что потеряно. Повод — первый
настоящий пульс принявшей сессии (тот, что пишет ``first-pulse:`` в оффер):
к нему уже есть и понятая цель, и аудит сида. Если принимающая так и не
поднялась (SOC-101 F25) — одно сообщение «не поднялась, нить остаётся у …».

Канал — тот же ``$TIDE_ALERT_CMD``, что у сторожа контекста и «нить встала»
(:func:`tide.launcher.autohandoff.send_alert`, ``TIDE_ALERT_ROUTE=human``). Нет
команды — тихо пропускаем и пишем строку в ``.tide/state/alerts.log``
control-home. Метка ``announced:`` в оффере — второй раз не шлём.

Сам модуль никогда не падает: сообщение — уведомление, не шаг передачи.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .. import fields, handoff_contract as contract, handoff_queue as hq, paths
from ..arc import stream as _stream

ANNOUNCED = "announced"          # метка в оффере: «<штамп> · sent|no-cmd|rc=N»
ALERT_LOG = "alerts.log"
FIELD_MAX = 200                  # примерно столько знаков на поле сообщения
ROUTE_HUMAN = "human"


# --- текст (чистое) ------------------------------------------------------------

def clip(text: str, limit: int = FIELD_MAX) -> str:
    """Одна строка, не длиннее *limit*: режем по границе слова, с «…»."""
    line = " ".join((text or "").split())
    if len(line) <= limit:
        return line
    cut = line[:limit - 1]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:—-·") + "…"


def _block_text(secs: Dict[str, List[str]], key: str) -> str:
    """Живые строки блока сида *key* одной строкой; пустой блок — «—»."""
    from . import seed_draft

    lines = []
    for ln in seed_draft.live_lines(contract.find_body(secs, key)):
        if "<!--" in ln:
            continue
        if seed_draft.is_hole(ln):
            return "—"
        lines.append(ln.strip().lstrip("-*").strip())
    return "; ".join(x for x in lines if x) or "—"


def reception_text(*, new_title: str, old_title: str, nxt: str, expectation: str,
                   inflight: str, taken: str, lost: List[str]) -> str:
    """Текст сообщения о приёме — по форме слова Гриши."""
    return (
        "{new} приняла нить от {old}. Передано: следующий шаг — {nxt}; ожидание "
        "Гриши — {exp}; в полёте — {fly}. Взято: {taken}. Потеряно: {lost}."
    ).format(new=new_title, old=old_title, nxt=clip(nxt), exp=clip(expectation),
             fly=clip(inflight), taken=clip(taken or "—"),
             lost=clip(", ".join(lost)) if lost else "ничего")


def not_alive_text(new_title: str, old_title: str) -> str:
    return "{0} не поднялась, нить остаётся у {1}".format(new_title, old_title)


# --- диск ----------------------------------------------------------------------

def _title(entry: Optional[Path], fallback: str) -> str:
    if entry is None:
        return fallback
    t = (fields.read_field(entry / "arc.md", "title") or "").strip()
    if t and not t.startswith("<"):
        return t
    return _stream.thread_session_title(entry) or entry.name


def _session_of(rec: Dict[str, object]) -> Optional[Path]:
    seed = str(rec.get("seed") or "-")
    if seed == "-":
        return None
    d = Path(seed).parent.parent
    return d if (d / "arc.md").is_file() else None


def titles(home: Path, rec: Dict[str, object]) -> "tuple[str, str]":
    """(название новой, название старой) — как их зовут доска и AO."""
    from .handoff import _origin_entry

    new = _title(_session_of(rec), "новая сессия")
    try:
        origin = _origin_entry(Path(home), rec)
    except Exception:  # noqa: BLE001
        origin = None
    sid = str(rec.get("from_session") or "-")
    return new, _title(origin, "прошлой сессии" if sid == "-" else
                       "прошлой сессии {0}".format(sid[:8]))


def _lost(home: Path, key: str) -> List[str]:
    """Разделы аудита со статусом missing (кроме понятой цели — её даёт «Взято»)."""
    try:
        from . import handoff_audit
        report = handoff_audit.audit(Path(home), key)
    except Exception:  # noqa: BLE001 — нет аудита — не «потеряно»
        return []
    return [s["title"] for s in report.get("sections") or []
            if s.get("status") == handoff_audit.MISSING and s.get("key") != "understood"]


def compose_reception(home: Path, rec: Dict[str, object]) -> str:
    from . import seed_draft

    new, old = titles(home, rec)
    seed = str(rec.get("seed") or "-")
    secs = seed_draft.sections(seed_draft._read(Path(seed))) if seed != "-" else {}
    taken = str(rec.get("understood_goal") or "-")
    if taken == "-":
        sess = _session_of(rec)
        taken = (fields.read_field(sess / "arc.md", "goal") or "").strip() if sess else ""
    return reception_text(
        new_title=new, old_title=old, nxt=_block_text(secs, "next"),
        expectation=_block_text(secs, "expectation"),
        inflight=_block_text(secs, "inflight"), taken=taken or "—",
        lost=_lost(home, str(rec["name"])))


def _log(home: Path, line: str) -> None:
    try:
        log = paths.state_dir(Path(home)) / ALERT_LOG
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("{0} {1}\n".format(datetime.now().isoformat(timespec="seconds"), line))
    except OSError:
        pass


def send(home: Path, text: str, *, source: str = "") -> str:
    """В ``$TIDE_ALERT_CMD`` (route=human) → ``sent`` / ``no-cmd`` / ``rc=N``."""
    from .autohandoff import ALERT_CMD_ENV, send_alert

    if not (os.environ.get(ALERT_CMD_ENV) or "").strip():
        _log(home, "не отправлено (нет ${0}): {1}".format(ALERT_CMD_ENV, text))
        return "no-cmd"
    rc = send_alert(text, route=ROUTE_HUMAN, source=source)
    _log(home, "{0}: {1}".format("отправлено" if rc == 0 else "rc={0}".format(rc), text))
    return "sent" if rc == 0 else "rc={0}".format(rc)


def _source(rec: Dict[str, object]) -> str:
    return "{0}/{1}".format(rec.get("project") or "-", rec.get("arc") or "-")


def announce_reception(home: Path, key: str) -> Optional[str]:
    """Одно сообщение о приёме оффера *key*; метка ``announced:`` — повтор молчит.
    → итог отправки или None (уже сказано / не принят)."""
    try:
        rec = hq._resolve(Path(home), key)
        if rec.get("status") != hq.STATUS_TAKEN:
            return None
        if hq._get(Path(str(rec["path"])).read_text(encoding="utf-8"), ANNOUNCED) != "-":
            return None
        stamp = datetime.now().isoformat(timespec="seconds")
        hq._set_field(Path(str(rec["path"])), ANNOUNCED, stamp)  # метка ДО отправки
        outcome = send(home, compose_reception(home, rec), source=_source(rec))
        hq._set_field(Path(str(rec["path"])), ANNOUNCED, "{0} · {1}".format(stamp, outcome))
        return outcome
    except Exception:  # noqa: BLE001 — сообщение не смеет сорвать пульс
        return None


def announce_not_alive(home: Path, key: str) -> Optional[str]:
    """«<новая> не поднялась, нить остаётся у <старой>» — одно на неудачный подъём."""
    try:
        rec = hq._resolve(Path(home), key)
        new, old = titles(home, rec)
        return send(home, not_alive_text(new, old), source=_source(rec))
    except Exception:  # noqa: BLE001
        return None


# --- предложение закрыть старую — менеджеру (контракт v2 §3 п.4) -----------------

CLOSE_PROPOSED = "close-proposed"   # метка в оффере: «<штамп> · <кому> · sent|…»


def close_text(old_title: str, new_title: str) -> str:
    return ("{0} передала нить {1}, приём подтверждён; закрыть {0}?".format(
        old_title, new_title))


def _ao_adapter():
    from ..adapters.ao import AoAdapter
    return AoAdapter()


def propose_close(home: Path, key: str) -> Optional[str]:
    """После приёма — ``ao send`` менеджеру (адрес — :func:`tide.whois.manager`):
    «<старая> передала нить <новой>, приём подтверждён; закрыть <старую>?». Не
    старой сессии самой (она не закрывается сама) — тому, кто закрывает. Один раз:
    метка ``close-proposed:`` в оффере. → итог или None (уже предложено/не принят)."""
    try:
        rec = hq._resolve(Path(home), key)
        if rec.get("status") != hq.STATUS_TAKEN or not hq._transfers_thread(rec):
            return None
        path = Path(str(rec["path"]))
        if hq._get(path.read_text(encoding="utf-8"), CLOSE_PROPOSED) != "-":
            return None
        stamp = datetime.now().isoformat(timespec="seconds")
        hq._set_field(path, CLOSE_PROPOSED, stamp)  # метка ДО отправки
        new, old = titles(home, rec)
        text = close_text(old, new)
        try:
            from .. import whois
            to = whois.manager(home=Path(home)).get("ao_session", "")
        except Exception:  # noqa: BLE001
            to = ""
        if not to:
            outcome = "no-manager"
        else:
            outcome = "sent" if _ao_adapter().say(to, text) else "not-delivered"
        _log(home, "предложение закрыть → {0} ({1}): {2}".format(to or "?", outcome, text))
        hq._set_field(path, CLOSE_PROPOSED, "{0} · {1} · {2}".format(stamp, to or "-", outcome))
        return outcome
    except Exception:  # noqa: BLE001 — предложение не смеет сорвать пульс
        return None


# --- после пульса (компонуется в cli: offload — домен и launcher не видит) ----------

def _taken_offer_for(home: Path, session_dir: Path) -> Optional[Dict[str, object]]:
    try:
        target = Path(session_dir).resolve()
    except OSError:
        return None
    for r in hq.list_offers(home, status=hq.STATUS_TAKEN):
        sess = _session_of(r)
        try:
            if sess is not None and sess.resolve() == target:
                return r
        except OSError:
            continue
    return None


FRESH_HOURS = 24  # приём старше — история: первый пульс давнего приёма не повод


def _fresh(rec: Dict[str, object]) -> bool:
    try:
        taken = datetime.fromisoformat(str(rec.get("taken_at") or ""))
    except ValueError:
        return False
    if taken.tzinfo is not None:
        taken = taken.astimezone().replace(tzinfo=None)
    return (datetime.now() - taken).total_seconds() <= FRESH_HOURS * 3600


def wrap_offload(sub) -> None:
    """Научить ``tide offload`` слать сообщение о приёме: если ЭТОТ пульс поставил
    принявшей сессии ``first-pulse:`` в оффер — это и есть приём, сказать Грише."""
    p = sub.choices.get("offload") if hasattr(sub, "choices") else None
    if p is None:
        return
    inner = p.get_default("func")
    if inner is None:
        return

    def _offload_and_announce(args) -> int:
        before = None
        home = None
        session_dir = None
        try:
            from .. import offload as _offload
            home = paths.control_home()
            session_dir = _offload.find_session(paths.require_tide_root(), args.session)
            if session_dir is not None:
                rec = _taken_offer_for(home, session_dir)
                before = str(rec.get("first_pulse") or "-") if rec else None
        except Exception:  # noqa: BLE001
            session_dir = None
        rc = inner(args)
        if rc != 0 or home is None or session_dir is None:
            return rc
        try:
            rec = _taken_offer_for(home, session_dir)
            if (rec and before in (None, "-") and _fresh(rec)
                    and str(rec.get("first_pulse") or "-") != "-"):
                announce_reception(home, str(rec["name"]))
                propose_close(home, str(rec["name"]))
        except Exception:  # noqa: BLE001
            pass
        return rc

    p.set_defaults(func=_offload_and_announce)


__all__ = ["ANNOUNCED", "CLOSE_PROPOSED", "announce_not_alive", "announce_reception",
           "clip", "close_text", "propose_close",
           "compose_reception", "not_alive_text", "reception_text", "send", "titles",
           "wrap_offload"]

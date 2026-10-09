"""SOC-101 F49 — ложная тревога «две головы» у нити 32 (continuity-audit 03→04).

Новая tide-stack-30 первым делом написала старой tide-stack-26 «нить приняла, жду
вердикт» — задач не давала, старая ничего не брала, а тревога ушла обеим, и копия
тревоги у старой ещё и переслалась новой. Теперь: письмо новой само не признак;
признак — поручение (не первое письмо после приёма) и после него правки/сабагенты
старой, или пульс старой. Одна тревога на пару: копия тревоги не пересылается.

Всё на временных каталогах, ``ao send`` подменён — наружу ничего не уходит.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tide import handoff_queue as hq
from tide.hooks import handed_off as ho
from tests.test_handed_off_forward import _hook, handed  # noqa: F401 — фикстура
from tests.test_handoff_autospawn import world  # noqa: F401 — фикстура handed

ACK = ("continuity-audit · 04 (tide-stack-30) → · 03: нить приняла, сид 287 прочитан, "
       "старт-гейт закрыт. Жду строку вердикта пары 13; acceptance-06.md приму по диску, "
       "«Итог:» в SOC-101, main · 04 и management-18 — мои.")


def _utc(dt):
    return dt.astimezone().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _delivery(sender, body, n=1):
    return '<ao-session-delivery id="session-send:{0}">\n[from {1}] {2}\n' \
           '</ao-session-delivery>'.format(n, sender, body)


def _row_letter(at, sender, body, n=1):
    return {"type": "user", "timestamp": _utc(at),
            "message": {"role": "user", "content": _delivery(sender, body, n)}}


def _row_turn(at, tool=None, cmd=""):
    content = [{"type": "text", "text": "ок"}]
    if tool:
        content.append({"type": "tool_use", "name": tool, "input": {"command": cmd}})
    return {"type": "assistant", "timestamp": _utc(at),
            "message": {"role": "assistant", "content": content}}


@pytest.fixture
def pair(handed, monkeypatch, tmp_path):
    """Пара старая proj-19 (old-sid) → новая proj-24 (new-sid) и транскрипт старой."""
    home = handed["home"]
    rec = hq.list_offers(home, status=hq.STATUS_TAKEN)[0]
    monkeypatch.setattr("tide.arc.holder.ao_handle_for",
                        lambda h, sid: {"old-sid": "proj-19", "new-sid": "proj-24"}.get(sid, ""))
    from pathlib import Path
    from tide import fields, roster

    # новая приняла нить по-настоящему: claude-session на её паспорте
    proj = Path(roster.read_roster(home)[0]["path"])
    thread, sess = rec["arc"].split("/")
    fields.set_field(proj / ".tide" / "arcs" / thread / "arcs" / sess / "arc.md",
                     "claude-session", "new-sid")
    cfg = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    tx = cfg / "projects" / "-x-old" / "old-sid.jsonl"
    tx.parent.mkdir(parents=True)
    t0 = datetime.fromisoformat(rec["taken_at"])

    def write(rows):
        tx.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                      encoding="utf-8")
        # чат новой свежее — цепочка видит новой её, а не старую
        new_tx = cfg / "projects" / "-x-new" / "new-sid.jsonl"
        new_tx.parent.mkdir(parents=True, exist_ok=True)
        new_tx.write_text(json.dumps(_row_turn(t0 + timedelta(minutes=30))) + "\n",
                          encoding="utf-8")

    return {"home": home, "rec": rec, "ao": handed["ao"], "t0": t0, "write": write,
            "at": lambda m: t0 + timedelta(minutes=m)}


def test_ack_of_take_is_not_an_order():
    assert not hq.is_instruction(ACK)
    assert hq.is_instruction("почини доску")
    assert hq.is_instruction("SOC-98 надо взять до вечера")
    assert not hq.is_instruction("SOC-98 принят, спасибо")


def test_ack_letter_raises_no_alarm_and_is_not_forwarded(pair, monkeypatch, capsys):
    """Нить 32: новая подтверждает приём, старая отвечает и доводит воркера."""
    at = pair["at"]
    pair["write"]([_row_letter(at(3), "proj-24", ACK), _row_turn(at(3.5)),
                   _row_turn(at(20), "Bash", "ao send --session proj-24 --message вердикт")])
    assert _hook(monkeypatch, _delivery("proj-24", ACK)) == 0
    out = capsys.readouterr().out
    assert "не пересылаю" in out and "Задачу из него не бери" not in out
    assert pair["ao"].calls == []
    assert hq.multiples(pair["home"]) == []
    # и Stop-хук старой (context_watch) через тот же вход тревоги не поднимет
    assert ho.two_heads_alarm(pair["home"], pair["rec"], evidence="Stop-хук",
                              say=lambda h, t: True) == []


def test_first_letter_never_counts_even_if_it_orders(pair):
    at = pair["at"]
    pair["write"]([_row_letter(at(2), "proj-24", "возьми SOC-5"), _row_turn(at(4), "Edit")])
    assert hq.multiples(pair["home"]) == []


def test_order_then_work_of_old_raises_alarm_once(pair, monkeypatch, capsys):
    at = pair["at"]
    pair["write"]([
        _row_letter(at(2), "proj-24", ACK, 1),
        _row_letter(at(6), "proj-24", "проверь SOC-112 и пришли итог", 2),
        _row_turn(at(7), "Bash", "cd /x && ao spawn --prompt 'проверка'"),
        _row_turn(at(8), "Edit"),
    ])
    (m,) = hq.multiples(pair["home"])
    assert "поручение новой proj-24" in m["evidence"] and "2 правок/сабагентов" in m["evidence"]
    # следующее поручение новой — тревога обеим, раз на пару
    order = _delivery("proj-24", "почини ещё и вот это", 3)
    assert _hook(monkeypatch, order) == 0
    assert "Задачу из него не бери" in capsys.readouterr().out
    sent = pair["ao"].calls
    assert [h for h, _t in sent] == ["proj-24", "proj-19"]
    assert all("две головы" in t and "поручение новой" in t for _h, t in sent)
    assert _hook(monkeypatch, _delivery("proj-24", "сделай третье", 4)) == 0
    assert len(pair["ao"].calls) == 2  # повтор не шлётся


def test_pulse_of_old_is_still_a_sign(pair):
    from tide import fields, offload, roster
    from pathlib import Path

    proj = Path(roster.read_roster(pair["home"])[0]["path"])
    arc = offload.find_session_by_claude_id(proj, "old-sid")
    fields.set_field(arc / "arc.md", "offloaded-at", pair["at"](9).isoformat(timespec="seconds"))
    (m,) = hq.multiples(pair["home"])
    assert m["evidence"].startswith("пульс после приёма")


def test_alarm_copy_at_old_is_not_forwarded_nor_realarmed(pair, monkeypatch, capsys):
    """Тревога, ушедшая старой (отправитель — сама старая), не пересылается новой
    и не порождает новую тревогу: одна на пару."""
    text = ho.two_heads_text(thread="32-@continuity-audit", new_addr="новая · proj-24",
                             old_addr="старая · proj-19", evidence="x")
    assert _hook(monkeypatch, _delivery("proj-19", text, 9)) == 0
    assert "не пересылаю" in capsys.readouterr().out
    assert pair["ao"].calls == []
    log = pair["home"] / ".tide" / "state" / "forwarded.log"
    assert not log.exists() or "session-send:9" not in log.read_text(encoding="utf-8")

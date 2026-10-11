"""SOC-179: пинг почты пачкой — несрочное копится по адресату и уходит одним пингом
не чаще раза в $TIDE_MAIL_BATCH_SEC; от менеджера, --urgent, эскалация, шов-синк — сразу."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from tide import cli, fields, mail, registry, roster
from tide.arc import stream
from tide.hooks import orphan
from tide.launcher import autohandoff


@pytest.fixture
def world(tmp_path, monkeypatch):
    from tests.conftest import build_tide_skeleton

    home = tmp_path / "home"
    home.mkdir()
    build_tide_skeleton(home, name="home", control_home=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    build_tide_skeleton(proj, name="proj")
    roster.add(home, "proj", str(proj))
    for name, num, ao in (("handoffs", 1, "proj-41"), ("board", 2, "proj-42")):
        stream.new_thread(proj, name, goal=name)
        s = stream.new_session(proj, name, "{0}-{1}".format(name, num),
                               claude_session="sid-" + name)
        fields.set_field(s / "arc.md", "title", "proj — {0} · 0{1}".format(name, num))
        registry.record(home, "sid-" + name, ao, name)
    man = tmp_path / "manager.json"
    man.write_text(json.dumps({"ao_session": "management-28", "title": "менеджер"}),
                   encoding="utf-8")
    monkeypatch.setenv("TIDE_MANAGER_FILE", str(man))
    monkeypatch.setattr(mail, "_MANAGER_AO", [])
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.setenv(mail.WATCH_ENV, "0")
    monkeypatch.setenv(mail.BATCH_ENV, "1500")
    monkeypatch.delenv("TIDE_ALERT_CMD", raising=False)
    monkeypatch.chdir(proj)
    sent = []
    monkeypatch.setattr(autohandoff, "_ao_send", lambda h, t: sent.append((h, t)) or 0)
    monkeypatch.setattr(autohandoff, "send_alert", lambda text, **kw: 0)
    return {"home": home, "sent": sent, "tdir": mail.thread_dir("handoffs"),
            "queue": home / ".tide" / "state" / mail.QUEUE_NAME}


def _log(tdir):
    return [json.loads(x) for x in (tdir / "inbox" / "log.jsonl").read_text().splitlines()]


def test_first_letter_pings_now_next_ones_wait_for_one_batch(world):
    mail.send("handoffs", "раз", sender="board-9")
    assert [t for _, t in world["sent"]] == [
        "почта 001 от board-9: раз — tide mail read proj/handoffs 001"]
    out2 = mail.send("handoffs", "два", sender="board-9")
    out3 = mail.send("handoffs", "три", sender="main-3")
    assert len(world["sent"]) == 1                       # окно 25 мин: копится
    assert out2["ping"]["queued"] and out3["ping"]["queued"]
    q = json.loads(world["queue"].read_text())
    assert [r["num"] for r in q["pending"]["proj-41"]] == ["002", "003"]
    # тик раньше окна — ждёт
    assert mail.flush_pings()[0]["why"].startswith("окно ещё")
    later = mail._now() + timedelta(seconds=1501)
    res = mail.flush_pings(now=later)
    assert res == [{"to": "proj-41", "n": 2, "sent": True, "code": 0}]
    (to, text), = world["sent"][1:]
    assert to == "proj-41" and text == "почта: 2 новых — tide mail ls proj/handoffs"
    assert len(text.encode("utf-8")) < 500
    batch = [e for e in _log(world["tdir"]) if e.get("kind") == "batch"]
    assert [(e["num"], e["batch"]) for e in batch] == [("001", 1), ("002", 2), ("003", 2)]
    assert not json.loads(world["queue"].read_text())["pending"]


def test_read_letters_leave_the_batch_without_a_ping(world):
    mail.send("handoffs", "раз", sender="x")
    mail.send("handoffs", "два", sender="x")
    mail.read("handoffs", "2", by="proj-41")
    res = mail.flush_pings(now=mail._now() + timedelta(seconds=1600))
    assert res == [{"to": "proj-41", "n": 0, "sent": False, "why": "прочитано"}]
    assert len(world["sent"]) == 1


@pytest.mark.parametrize("kw", [
    {"sender": "management-28"},                    # от менеджера (tide whois manager)
    {"sender": "x", "urgent": True},                # --urgent
    {"sender": "x", "kind": "escalation"},          # tide escalate
    {"sender": "шов-синк"},                         # пинги приёма хендоффа
])
def test_urgent_letters_ping_now_even_inside_the_window(world, kw):
    mail.send("handoffs", "раз", sender="x")
    mail.send("handoffs", "два", sender="x")         # в пачке
    out = mail.send("handoffs", "срочно", **kw)
    assert not out["ping"].get("queued") and out["ping"]["code"] == 0
    assert len(world["sent"]) == 2 and "срочно" in world["sent"][-1][1]


def test_urgent_retry_stays_urgent_and_plain_retry_joins_the_batch(world):
    tdir = world["tdir"]
    mail.send("handoffs", "срочно", sender="x", urgent=True)
    mail.send("handoffs", "обычное", sender="x")      # в пачку: только что пинговали
    t = mail._now() + timedelta(minutes=16)
    assert mail.step(tdir, 1, now=t, minutes=15) == "retry"
    assert mail.step(tdir, 2, now=t, minutes=15) == "retry"
    assert [h for h, _ in world["sent"]] == ["proj-41", "proj-41"]   # срочный повтор — сразу
    q = json.loads(world["queue"].read_text())
    assert [r["num"] for r in q["pending"]["proj-41"]] == ["002"]     # дубль не множится


def test_batch_lists_every_thread_of_one_addressee(world):
    rows = [{"addr": "proj/handoffs", "num": "001"}, {"addr": "proj/board", "num": "004"}]
    assert mail.batch_text(rows) == ("почта: 2 новых — tide mail ls proj/handoffs; "
                                     "tide mail ls proj/board")


def test_window_zero_means_old_behaviour(world, monkeypatch):
    monkeypatch.setenv(mail.BATCH_ENV, "0")
    for t in ("раз", "два", "три"):
        mail.send("handoffs", t, sender="x")
    assert len(world["sent"]) == 3 and not world["queue"].exists()


def test_cli_urgent_flag_and_orphan_tick_flushes(world, capsys, monkeypatch):
    assert cli.main(["mail", "send", "handoffs", "раз", "--from", "x"]) == 0
    assert cli.main(["mail", "send", "handoffs", "два", "--from", "x"]) == 0
    assert "в пачку" in capsys.readouterr().out
    assert cli.main(["mail", "send", "handoffs", "срочно", "--from", "x", "--urgent"]) == 0
    assert len(world["sent"]) == 2
    monkeypatch.setenv(mail.BATCH_ENV, "1")         # окно вышло — тик отдаёт пачку
    monkeypatch.setattr(orphan, "on_tick", lambda **kw: {"ran": False, "why": "тест"})
    import time
    time.sleep(1.1)
    assert cli.main(["hook", "orphan-tick"]) == 0
    out = capsys.readouterr().out
    assert "mail-batch: → proj-41 1 пис. (код 0)" in out
    assert world["sent"][-1][1].startswith("почта 002 от x: два")

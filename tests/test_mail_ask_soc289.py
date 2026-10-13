"""SOC-289: просьба менеджеру без ответа — 15 мин ask-wake, 30 мин ❓ на доску, --close при ответе.

tg-alert подменён скриптом-заглушкой через $TIDE_ALERT_CMD; настоящий ao не трогаем."""

from __future__ import annotations

import argparse
import json
import stat
from datetime import timedelta

import pytest

from tide import mail, registry, roster, fields
from tide.arc import stream
from tide.launcher import autohandoff


@pytest.fixture
def world(tmp_path, monkeypatch):
    from tests.conftest import build_tide_skeleton

    home = tmp_path / "home"
    home.mkdir()
    build_tide_skeleton(home, name="home", control_home=True)
    proj = tmp_path / "mgmt"
    proj.mkdir()
    build_tide_skeleton(proj, name="management")
    roster.add(home, "management", str(proj))
    stream.new_thread(proj, "main", goal="m")
    s = stream.new_session(proj, "main", "main-1", claude_session="sid-m")
    fields.set_field(s / "arc.md", "title", "management — main · 01")
    registry.record(home, "sid-m", "management-9", "main")
    stream.new_thread(proj, "role", goal="r")
    s2 = stream.new_session(proj, "role", "role-1", claude_session="sid-r")
    fields.set_field(s2 / "arc.md", "title", "management — role · 01")
    registry.record(home, "sid-r", "management-70", "role")
    mgr = tmp_path / "manager.json"
    mgr.write_text(json.dumps({"ao_session": "management-9", "thread": "main"}))
    calls = tmp_path / "alert-calls.txt"
    fake = tmp_path / "tg-alert"
    fake.write_text('#!/bin/sh\necho "$@" >> "{0}"\n[ "$1" = "--close" ] && exit 0\n'
                    'echo q-abc123def0\n'.format(calls))
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    aocalls = tmp_path / "ao-calls.txt"
    ao = tmp_path / "ao"
    ao.write_text('#!/bin/sh\necho "$@" >> "{0}"\nexit 0\n'.format(aocalls))
    ao.chmod(ao.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.setenv("TIDE_MANAGER_FILE", str(mgr))
    monkeypatch.setenv("TIDE_ALERT_CMD", str(fake))
    monkeypatch.setenv("TIDE_AO_BIN", str(ao))
    monkeypatch.setenv(mail.WATCH_ENV, "0")
    monkeypatch.setenv(mail.BATCH_ENV, "0")
    for k in (mail.ASK_FORCE_ENV, mail.ASK_SWEEP_ENV, mail.ASK_WAKE_ENV, mail.ASK_BOARD_ENV,
              mail.AO_SESSION_ENV, "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(mail, "_MANAGER_AO", [])
    monkeypatch.chdir(proj)
    return {"calls": calls, "aocalls": aocalls, "tmp": tmp_path}


def _ask(text="нужно решение по SOC-77\nдетали", sender="role-1"):
    out = mail.send("management/main", text, sender=sender, kind="ask")
    tdir = mail.thread_dir("management/main")
    t0 = mail._parse_iso(next(r["sent"] for r in mail.letters(tdir) if r["num"] == out["num"]))
    return tdir, out["num"], t0


def _evs(tdir, ev):
    return [json.loads(x) for x in (tdir / "inbox" / "log.jsonl").read_text().splitlines()
            if json.loads(x).get("ev") == ev]


def _calls(w):
    return w["calls"].read_text().splitlines() if w["calls"].exists() else []


def test_send_ask_is_urgent_and_letters_expose_it(world):
    tdir, num, _ = _ask()
    rec = mail.letter(tdir, num)
    assert rec["ask"] and rec["kind"] == "ask" and mail.is_urgent(rec)
    sent = _evs(tdir, "sent")[0]
    assert sent["kind"] == "ask"


def test_wake_force_off_no_ping(world, monkeypatch):
    monkeypatch.setenv(mail.ASK_FORCE_ENV, "0")
    tdir, num, t0 = _ask()
    rows = mail.ask_sweep(now=t0 + timedelta(minutes=16))
    assert rows[0]["action"] == "wake"
    ev = _evs(tdir, "ask-wake")
    assert len(ev) == 1 and ev[0]["pinged"] is False
    assert _calls(world) == []
    assert not world["aocalls"].exists() or "management-9" not in world["aocalls"].read_text()


def test_wake_pings_through_suppress_by_default(world):
    tdir, num, t0 = _ask()
    world["aocalls"].write_text("")
    mail.ask_sweep(now=t0 + timedelta(minutes=16))
    assert "management-9" in world["aocalls"].read_text()
    assert _evs(tdir, "ask-wake")[0]["pinged"] is True


def test_board_at_31_min_once(world):
    tdir, num, t0 = _ask()
    now = t0 + timedelta(minutes=31)
    mail.ask_sweep(now=now)
    calls = _calls(world)
    assert len(calls) == 1 and "--soc SOC-77" in calls[0] and "❓ Просьба role-1" in calls[0]
    assert "нужно решение по SOC-77" in calls[0] and "tide mail read management/main " + num in calls[0]
    assert _evs(tdir, "ask-board")[0]["qid"] == "q-abc123def0"
    assert len(_evs(tdir, "ask-wake")) == 1
    mail.ask_sweep(now=now + timedelta(minutes=2))
    assert len(_calls(world)) == 1 and len(_evs(tdir, "ask-wake")) == 1


def test_ack_before_threshold_does_nothing(world):
    tdir, num, t0 = _ask()
    mail.read("management/main", num, by="management-9")
    assert mail.ask_sweep(now=t0 + timedelta(minutes=40)) == []
    assert _calls(world) == [] and _evs(tdir, "ask-wake") == []


def test_close_and_reply_cancel(world):
    tdir, num, t0 = _ask()
    mail.ask_close("management/main", num, word="сделано")
    assert mail.ask_sweep(now=t0 + timedelta(minutes=40)) == []
    tdir, num2, t1 = _ask("второе")
    mail.send("management/role", "ответ", sender="management-9", kind="", re_num=num2)
    assert mail.ask_sweep(now=t1 + timedelta(minutes=40)) == []
    assert _calls(world) == []


def test_answered_after_card_closes_it(world):
    tdir, num, t0 = _ask()
    mail.ask_sweep(now=t0 + timedelta(minutes=31))
    mail.ask_close("management/main", num, word="ок")
    mail.ask_sweep(now=t0 + timedelta(minutes=35))
    calls = _calls(world)
    assert calls[-1].startswith("--close q-abc123def0") and len(calls) == 2
    assert len(_evs(tdir, "ask-board-closed")) == 1
    mail.ask_sweep(now=t0 + timedelta(minutes=37))
    assert len(_calls(world)) == 2


def test_older_than_a_day_and_off_switch_and_dry(world, monkeypatch):
    tdir, num, t0 = _ask()
    assert mail.ask_sweep(now=t0 + timedelta(hours=25)) == []
    rows = mail.ask_sweep(now=t0 + timedelta(minutes=31), dry=True)
    assert rows[0]["action"] == "wake + board" and not rows[0]["done"]
    assert _calls(world) == [] and _evs(tdir, "ask-wake") == []
    monkeypatch.setenv(mail.ASK_SWEEP_ENV, "off")
    assert mail.ask_sweep(now=t0 + timedelta(minutes=31)) == []


def test_max_three_cards_per_sweep(world):
    for i in range(5):
        tdir, num, t0 = _ask("просьба {0}".format(i))
    mail.ask_sweep(now=t0 + timedelta(minutes=31))
    assert len(_evs(tdir, "ask-board")) == 3
    mail.ask_sweep(now=t0 + timedelta(minutes=33))
    assert len(_evs(tdir, "ask-board")) == 5


def test_cli_asks_and_close(world, capsys):
    tdir, num, t0 = _ask()
    ns = argparse.Namespace(now=(t0 + timedelta(minutes=16)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            dry_run=True)
    assert mail._cmd_asks(ns) == 0
    assert "сделал бы: wake" in capsys.readouterr().out
    mail._cmd_close(argparse.Namespace(thread="management/main", num=num, word="w", by="x"))
    mail._cmd_ls(argparse.Namespace(thread="management/main"))
    assert "ПРОСЬБА закрыта: w" in capsys.readouterr().out

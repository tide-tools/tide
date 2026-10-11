"""SOC-143: почта нити — письмо файлом, пинг < 500 байт, квитанция, повтор, тревога."""

from __future__ import annotations

import io
import json
import sys
from datetime import timedelta

import pytest

from tide import cli, fields, mail, registry, roster
from tide.arc import stream
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
    stream.new_thread(proj, "handoffs", goal="передачи")
    s = stream.new_session(proj, "handoffs", "handoffs-1", claude_session="sid-h1")
    fields.set_field(s / "arc.md", "title", "proj — handoffs · 01")
    registry.record(home, "sid-h1", "proj-41", "handoffs")
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.setenv(mail.WATCH_ENV, "0")
    monkeypatch.delenv("TIDE_ALERT_CMD", raising=False)
    monkeypatch.delenv(mail.AO_SESSION_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.chdir(proj)
    sent, alerts = [], []
    monkeypatch.setattr(autohandoff, "_ao_send",
                        lambda h, t: sent.append((h, t)) or 0)
    monkeypatch.setattr(autohandoff, "send_alert",
                        lambda text, **kw: alerts.append((text, kw)) or 0)
    return {"home": home, "proj": proj, "sent": sent, "alerts": alerts,
            "tdir": mail.thread_dir("handoffs")}


def test_big_letter_lands_whole_and_ping_is_short(world):
    body = "Первая строка письма\n" + ("x" * 10240) + "\nконец\n"
    out = mail.send("handoffs", body, sender="manager-7")
    p = world["tdir"] / "inbox" / "001-от-manager-7.md"
    assert out["num"] == "001" and p.is_file()
    text = p.read_text(encoding="utf-8")
    assert body in text and "from: manager-7" in text and "sha256:" in text
    (to, ping), = world["sent"]
    assert to == "proj-41"                                  # ведущая сессия нити
    assert len(ping.encode("utf-8")) < 500
    assert ping.startswith("почта 001 от manager-7: Первая строка письма")
    assert ping.endswith("tide mail read proj/handoffs 001")


def test_ping_clips_a_long_first_line_under_500_bytes():
    t = mail.ping_text("007", "кто-то", "я" * 2000, "proj/handoffs")
    assert len(t.encode("utf-8")) < 500 and t.endswith("tide mail read proj/handoffs 007")


def test_numbers_grow_and_read_writes_ack(world):
    mail.send("handoffs", "раз", sender="a")
    mail.send("handoffs", "два", sender="b")
    assert [r["num"] for r in mail.unread(world["tdir"])] == ["001", "002"]
    text, ack = mail.read("handoffs", "2", by="proj-41")
    assert "два" in text and ack["by"] == "proj-41" and ack["at"]
    assert [r["num"] for r in mail.unread(world["tdir"])] == ["001"]
    log = [json.loads(x) for x in
           (world["tdir"] / "inbox" / "log.jsonl").read_text().splitlines()]
    assert [e["ev"] for e in log] == ["sent", "ping", "sent", "ping", "ack"]


def test_cli_send_ls_read(world, capsys, tmp_path):
    f = tmp_path / "letter.md"
    f.write_text("Тема: план\n" + "y" * 5000, encoding="utf-8")
    assert cli.main(["mail", "send", "handoffs", "--file", str(f), "--from", "m"]) == 0
    assert "почта 001" in capsys.readouterr().out
    assert cli.main(["mail", "ls", "handoffs"]) == 0
    out = capsys.readouterr().out
    assert "1 непрочитанных, 0 прочитанных" in out and "НЕ ПРОЧИТАНО" in out
    assert cli.main(["mail", "read", "handoffs", "001", "--by", "reader"]) == 0
    out = capsys.readouterr().out
    assert "y" * 5000 in out and "квитанция: reader" in out
    assert cli.main(["mail", "ls", "handoffs"]) == 0
    assert "0 непрочитанных, 1 прочитанных" in capsys.readouterr().out


def test_unknown_thread_and_letter_fail_loud(world):
    with pytest.raises(mail.MailError):
        mail.send("nope", "x")
    with pytest.raises(mail.MailError):
        mail.read("handoffs", "9")
    assert cli.main(["mail", "read", "handoffs", "9"]) == 1


def test_no_holder_letter_still_lands(world, monkeypatch):
    monkeypatch.setattr(mail, "_holder_ao", lambda tdir: "")
    out = mail.send("handoffs", "без адресата", sender="m")
    assert out["ping"]["to"] is None and world["sent"] == []
    assert len(mail.unread(world["tdir"])) == 1


def test_retry_twice_then_one_alert_to_manager(world):
    mail.send("handoffs", "срочно", sender="m")
    tdir, t0 = world["tdir"], mail._now()
    assert mail.step(tdir, 1, now=t0, minutes=15) == "wait"
    t = t0
    for i in range(mail.MAX_RETRIES):
        t = t + timedelta(minutes=16)
        assert mail.step(tdir, 1, now=t, minutes=15) == "retry"
        # повтор штампуется настоящим временем — сдвигаем «сейчас» от него
        t = mail._now()
    assert len(world["sent"]) == 1 + mail.MAX_RETRIES
    assert mail.step(tdir, 1, now=t + timedelta(minutes=16), minutes=15) == "alert"
    (text, kw), = world["alerts"]
    assert kw["route"] == "manager" and "почта 001" in text and "без квитанции" in text
    assert not kw.get("urgent")          # SOC-179: почта без квитанции — пачкой
    assert mail.step(tdir, 1, now=t + timedelta(minutes=60), minutes=15) == "done"
    assert len(world["alerts"]) == 1
    mail.read("handoffs", 1, by="x")
    assert mail.step(tdir, 1, minutes=15) == "acked"


def test_ack_stops_retries(world):
    mail.send("handoffs", "прочти", sender="m")
    mail.read("handoffs", 1, by="x")
    assert mail.step(world["tdir"], 1, now=mail._now() + timedelta(hours=2),
                     minutes=15) == "acked"
    assert len(world["sent"]) == 1 and world["alerts"] == []


def test_retry_minutes_env(monkeypatch):
    monkeypatch.setenv(mail.RETRY_ENV, "0.1")
    assert mail.retry_minutes() == 0.1
    monkeypatch.setenv(mail.RETRY_ENV, "мусор")
    assert mail.retry_minutes() == mail.RETRY_DEFAULT_MIN


def test_session_start_shows_unread(world):
    from tide.hooks import session_start

    assert mail.session_line(world["proj"], "sid-h1") is None
    mail.send("handoffs", "a", sender="m")
    mail.send("handoffs", "b", sender="m")
    line = "почта: 2 непрочитанных — tide mail ls proj/handoffs"
    assert mail.session_line(world["proj"], "sid-h1") == line
    assert line in session_start.render(world["proj"], "executor", session="sid-h1")
    assert mail.session_line(world["proj"], "sid-other") is None


def test_prompt_hook_keeps_confirm_and_adds_mail_line(world, monkeypatch, capsys):
    from tide.hooks import handed_off

    seen = {}

    def fake_prompt(args):
        seen["stdin"] = sys.stdin.read()
        return 0

    monkeypatch.setattr(handed_off, "cmd_prompt", fake_prompt)
    mail.send("handoffs", "a", sender="m")
    payload = json.dumps({"session_id": "sid-h1", "prompt": "привет"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert mail.cmd_prompt_hook(None) == 0
    assert seen["stdin"] == payload                     # прежний хук получил весь stdin
    assert "почта: 1 непрочитанных" in capsys.readouterr().out


def test_hook_wiring_points_at_mail_wrapper():
    parser = cli.build_parser()
    args = parser.parse_args(["hook", "handoff-confirm"])
    assert args.func is mail.cmd_prompt_hook

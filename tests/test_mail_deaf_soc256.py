"""SOC-256: пинг почты, который ``ao send`` не доставил (SESSION_MODE_MISMATCH), не глотается.

Фейковый ``ao`` через ``$TIDE_AO_BIN``; настоящий ao и чужие сессии не трогаем."""

from __future__ import annotations

import argparse
import json
import stat

import pytest

from tide import fields, mail, registry, roster
from tide.arc import stream
from tide.launcher import autohandoff

MISMATCH = ("this session was created in Terminal UI mode and has no chat conversation "
            "(SESSION_MODE_MISMATCH) [request x/1]")


def _fake_ao(path, *, rc: int, out: str, fail_for: str = "") -> str:
    """Скрипт-ao: на ``send --session <fail_for>`` печатает *out* и выходит с *rc*,
    остальным — молча 0 (как настоящий ao при удаче)."""
    path.write_text(
        '#!/bin/sh\n[ "$1" = send ] || exit 0\nif [ -z "{f}" ] || [ "$3" = "{f}" ]; then\n'
        '  echo "{o}" >&2; exit {rc}\nfi\nexit 0\n'.format(f=fail_for, o=out, rc=rc))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


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
    for name, ao in (("tui", "proj-41"), ("chat", "proj-42")):
        stream.new_thread(proj, name, goal=name)
        s = stream.new_session(proj, name, name + "-1", claude_session="sid-" + name)
        fields.set_field(s / "arc.md", "title", "proj — {0} · 01".format(name))
        registry.record(home, "sid-" + name, ao, name)
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.setenv(mail.WATCH_ENV, "0")
    monkeypatch.setenv(mail.BATCH_ENV, "0")             # пинг сразу, без пачки
    monkeypatch.delenv("TIDE_ALERT_CMD", raising=False)
    monkeypatch.delenv(mail.AO_SESSION_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setenv("TIDE_MANAGER_FILE", str(tmp_path / "no-manager.json"))
    monkeypatch.setattr(mail, "_MANAGER_AO", [])
    monkeypatch.chdir(proj)
    fake = _fake_ao(tmp_path / "ao", rc=1, out=MISMATCH, fail_for="proj-41")
    monkeypatch.setenv("TIDE_AO_BIN", fake)
    alerts = []
    monkeypatch.setattr(autohandoff, "send_alert",
                        lambda text, **kw: alerts.append((text, kw)) or 0)
    return {"home": home, "alerts": alerts, "tmp": tmp_path,
            "state": home / ".tide" / "state" / mail.DEAF_NAME}


def _log(ref):
    tdir = mail.thread_dir(ref)
    return [json.loads(x) for x in (tdir / "inbox" / "log.jsonl").read_text().splitlines()]


def _args(thread, text):
    return argparse.Namespace(thread=thread, text=text, file=None, sender="t-1",
                              no_ping=False, urgent=False)


def test_ao_send_reports_the_refusal_and_its_words(world):
    assert autohandoff._ao_send("proj-41", "x") == 1
    assert "SESSION_MODE_MISMATCH" in autohandoff.last_send_error("proj-41")
    assert autohandoff._ao_send("proj-42", "x") == 0
    assert autohandoff.last_send_error("proj-42") == ""


def test_ao_exit_zero_with_error_code_in_output_is_not_success(world, monkeypatch):
    ao = _fake_ao(world["tmp"] / "ao0", rc=0, out=MISMATCH)
    monkeypatch.setenv("TIDE_AO_BIN", ao)
    assert autohandoff._ao_send("proj-41", "x") == autohandoff.SILENT_FAIL_CODE
    assert "SESSION_MODE_MISMATCH" in autohandoff.last_send_error("proj-41")


def test_refused_ping_is_logged_flagged_and_alerted_once(world):
    out = mail.send("tui", "срочно", sender="m-1")
    p = out["ping"]
    assert p["code"] == 1 and p["deaf"] is True and "SESSION_MODE_MISMATCH" in p["err"]
    ev, = [e for e in _log("tui") if e["ev"] == "ping"]
    assert ev["code"] == 1 and ev["deaf"] and "SESSION_MODE_MISMATCH" in ev["err"]
    state = json.loads(world["state"].read_text())["deaf"]["proj-41"]
    assert state["mode_mismatch"] is True and state["n"] == 1
    assert state["letters"] == [{"addr": "proj/tui", "num": "001"}]
    (text, kw), = world["alerts"]
    assert kw["route"] == "manager" and kw["urgent"] is True
    assert "proj-41" in text and "SESSION_MODE_MISMATCH" in text
    mail.send("tui", "ещё", sender="m-1")                # второй отказ — без второй тревоги
    assert len(world["alerts"]) == 1
    assert json.loads(world["state"].read_text())["deaf"]["proj-41"]["n"] == 2


def test_cli_send_exits_nonzero_on_refusal_and_zero_on_chat(world, capsys):
    assert mail._cmd_send(_args("tui", "привет")) == 1
    cap = capsys.readouterr()
    assert "НЕ ДОШЁЛ" in cap.out and "SESSION_MODE_MISMATCH" in cap.out
    assert mail._cmd_send(_args("chat", "привет")) == 0
    assert "код 0" in capsys.readouterr().out
    assert "proj-42" not in mail.deaf_sessions()


def test_success_and_own_read_clear_the_flag(world, monkeypatch):
    mail.send("tui", "раз", sender="m-1")
    assert "proj-41" in mail.deaf_sessions()
    monkeypatch.setenv("TIDE_AO_BIN", _fake_ao(world["tmp"] / "ao-ok", rc=0, out=""))
    mail.send("tui", "два", sender="m-1")                # удачный пинг снимает флаг
    assert "proj-41" not in mail.deaf_sessions()
    monkeypatch.setenv("TIDE_AO_BIN", _fake_ao(world["tmp"] / "ao-bad", rc=1, out=MISMATCH))
    mail.send("tui", "три", sender="m-1")
    assert "proj-41" in mail.deaf_sessions()
    monkeypatch.setenv(mail.AO_SESSION_ENV, "proj-41")
    mail.read("tui", "003")                              # сама прочла — слышит
    assert "proj-41" not in mail.deaf_sessions()


def test_batched_flush_failure_is_flagged_and_exit_code_one(world, monkeypatch, capsys):
    monkeypatch.setenv(mail.BATCH_ENV, "1500")
    mail.send("tui", "раз", sender="m-1")                # первое уходит сразу и падает
    assert mail.deaf_sessions()["proj-41"]["n"] == 1
    mail.send("tui", "два", sender="m-1")                # второе копится в пачке
    res = mail.flush_pings(force=True)
    assert res[0]["sent"] and res[0]["code"] == 1 and res[0]["deaf"]
    assert "SESSION_MODE_MISMATCH" in res[0]["err"]
    mail.send("tui", "три", sender="m-1")
    assert mail._cmd_flush(argparse.Namespace(force=True)) == 1


def test_tide_mail_deaf_lists_the_state(world, capsys):
    mail.send("tui", "раз", sender="m-1")
    assert mail._cmd_deaf(argparse.Namespace(json=False)) == 0
    out = capsys.readouterr().out
    assert "proj-41: не слышит" in out and "SESSION_MODE_MISMATCH" in out
    assert mail._cmd_deaf(argparse.Namespace(json=True)) == 0
    assert "proj-41" in json.loads(capsys.readouterr().out)

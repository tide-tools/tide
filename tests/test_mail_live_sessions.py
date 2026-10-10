"""SOC-143: письмо нити с двумя живыми параллельными сессиями доходит обеим.

Живой случай 09.10: в нити techlead две живые сессии (13 ведёт хаб игр, 14 —
линию Groovamp); ``tide mail send app-development/techlead`` пинговал только
новейшую (14). Теперь: без уточнения — пинг всем живым сессиям нити и строка
«в нити N живых сессий — письмо общее»; ``<нить>@<сессия>`` — пинг, квитанция и
повторы только адресата; одна живая — как раньше (ведущая нити).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from tide import cli, fields, mail, registry, roster
from tide.arc import stream
from tide.launcher import autohandoff


@pytest.fixture
def two(tmp_path, monkeypatch):
    from tests.conftest import build_tide_skeleton

    home = tmp_path / "home"
    home.mkdir()
    build_tide_skeleton(home, name="home", control_home=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    build_tide_skeleton(proj, name="proj")
    roster.add(home, "proj", str(proj))
    thread = stream.new_thread(proj, "techlead", goal="техлид")
    s12 = stream.new_session(proj, "techlead", "techlead-12", claude_session="sid-12")
    s13 = stream.new_session(proj, "techlead", "techlead-13", claude_session="sid-13")
    registry.record(home, "sid-12", "proj-36", "techlead")
    registry.record(home, "sid-13", "proj-50", "techlead")
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.setenv(mail.WATCH_ENV, "0")
    monkeypatch.delenv("TIDE_ALERT_CMD", raising=False)
    monkeypatch.delenv(mail.AO_SESSION_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.chdir(proj)
    sent, dead = [], set()
    monkeypatch.setattr(autohandoff, "_ao_send", lambda h, t: sent.append((h, t)) or 0)
    monkeypatch.setattr(autohandoff, "send_alert", lambda text, **kw: 0)
    monkeypatch.setattr(mail, "_ao_terminated", lambda: set(dead))
    return {"home": home, "proj": proj, "thread": thread, "s12": s12, "s13": s13,
            "sent": sent, "dead": dead, "tdir": mail.thread_dir("techlead")}


def test_two_live_sessions_both_get_the_ping(two):
    out = mail.send("techlead", "сверка хаба", sender="manager-22")
    assert sorted(h for h, _t in two["sent"]) == ["proj-36", "proj-50"]
    for _h, text in two["sent"]:
        assert text.splitlines()[-1] == "в нити 2 живых сессий — письмо общее"
        assert len(text.encode("utf-8")) < 500
    assert out["ping"]["to_all"] == ["proj-36", "proj-50"]
    # квитанция любой из двух — письмо общее, прочитано
    mail.read("techlead", out["num"], by="proj-36")
    assert mail.unread(two["tdir"]) == []


def test_at_session_pings_only_it_and_waits_for_its_ack(two):
    out = mail.send("techlead@proj-50", "только тринадцатой", sender="manager-22")
    assert [h for h, _t in two["sent"]] == ["proj-50"]
    assert "письмо общее" not in two["sent"][0][1]
    text = (two["tdir"] / "inbox" / "001-от-manager-22.md").read_text(encoding="utf-8")
    assert "to-session: {0} (proj-50)".format(two["s13"].name) in text
    # квитанция чужой сессии не считается: письмо ждёт адресата, повтор — ей
    mail.read("techlead", out["num"], by="proj-36")
    assert [r["num"] for r in mail.unread(two["tdir"])] == ["001"]
    later = mail._now() + timedelta(minutes=20)
    assert mail.step(two["tdir"], "001", now=later, minutes=15) == "retry"
    assert [h for h, _t in two["sent"]] == ["proj-50", "proj-50"]
    # квитанция адресата (uds-имя с хвостом) — прочитано
    mail.read("techlead", "001", by="proj-50-15")
    assert mail.unread(two["tdir"]) == []
    assert mail.step(two["tdir"], "001", now=later, minutes=15) == "acked"


def test_at_session_by_name_and_cli(two, capsys):
    assert cli.main(["mail", "send", "proj/techlead@techlead-12", "двенадцатой",
                     "--from", "m"]) == 0
    assert [h for h, _t in two["sent"]] == ["proj-36"]
    assert "пинг proj-36" in capsys.readouterr().out
    assert cli.main(["mail", "ls", "techlead"]) == 0
    assert "НЕ ПРОЧИТАНО адресатом {0}".format(two["s12"].name) in capsys.readouterr().out
    # имя нити с «@» в самом каталоге — адрес целиком, без уточнения
    assert mail.target(two["thread"].name) == (two["tdir"], "")
    with pytest.raises(mail.MailError):
        mail.send("techlead@techlead-99", "нет такой", sender="m")


def test_one_live_session_keeps_old_behaviour(two):
    fields.set_field(two["s12"] / "arc.md", "handed-off", "2026-10-09T21:18:45 → 317")
    mail.send("techlead", "одной", sender="m")
    (to, text), = two["sent"]
    assert to == "proj-50" and "письмо общее" not in text


def test_terminated_ao_is_not_live(two):
    two["dead"].add("proj-36")
    mail.send("techlead", "одной живой", sender="m")
    (to, text), = two["sent"]
    assert to == "proj-50" and "письмо общее" not in text

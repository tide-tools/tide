"""SOC-101 F19/F20/C14: адресат — резолвером, не константой.

`tide whois manager` — manager.json, без него — новейшая принятая сессия нити
менеджера; `tide whois <нить>` — то же для любой нити. Печатает AO-id и title.
"""

from __future__ import annotations

import json

import pytest

from tide import cli, fields, registry, roster, whois
from tide.arc import stream


@pytest.fixture
def world(tmp_path, monkeypatch):
    from tests.conftest import build_tide_skeleton

    home = tmp_path / "home"
    home.mkdir()
    build_tide_skeleton(home, name="home", control_home=True)
    mgmt = tmp_path / "management"
    mgmt.mkdir()
    build_tide_skeleton(mgmt, name="management")
    roster.add(home, "management", str(mgmt))
    stream.new_thread(mgmt, "main", goal="управлять")
    for n, (sid, ao) in enumerate([("sid-old", "management-9"),
                                   ("sid-new", "management-14")], start=1):
        s = stream.new_session(mgmt, "main", "main-{0}".format(n), claude_session=sid)
        fields.set_field(s / "arc.md", "title", "management — main · 0{0}".format(n))
        registry.record(home, sid, ao, "main")
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.setenv(whois.MANAGER_FILE_ENV, str(tmp_path / "no-manager.json"))
    monkeypatch.chdir(tmp_path)
    return {"home": home, "mgmt": mgmt, "tmp": tmp_path}


def test_manager_from_file_wins(world, monkeypatch):
    mf = world["tmp"] / "manager.json"
    mf.write_text(json.dumps({"ao_session": "management-20", "thread": "main",
                              "title": "management — main · 07"}), encoding="utf-8")
    monkeypatch.setenv(whois.MANAGER_FILE_ENV, str(mf))
    rec = whois.manager()
    assert rec["ao_session"] == "management-20" and rec["source"] == "manager.json"
    assert rec["title"] == "management — main · 07"


def test_manager_without_file_comes_from_the_chain(world):
    rec = whois.manager()
    assert rec["source"] == "chain"
    assert rec["ao_session"] == "management-14"          # newest session, not -9
    assert rec["title"] == "management — main · 02"


def test_thread_verb_prints_ao_id_and_title(world, capsys):
    assert cli.main(["whois", "management/main"]) == 0
    assert capsys.readouterr().out.strip() == "management-14\tmanagement — main · 02"
    assert cli.main(["whois", "manager", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ao_session"] == "management-14" and data["thread"] == "01-@main"


def test_ao_id_from_title_tail_when_registry_is_silent(world):
    s = stream.new_session(world["mgmt"], "main", "main-3", claude_session="sid-3")
    fields.set_field(s / "arc.md", "title", "management — main · 03 · management-15")
    assert whois.thread("management/main")["ao_session"] == "management-15"


def test_unknown_thread_fails_loud(world, capsys):
    with pytest.raises(whois.WhoisError):
        whois.thread("management/nope")
    assert cli.main(["whois", "nope/main"]) != 0

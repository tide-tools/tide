"""SOC-101 F26: only the RECEIVING session confirms a reception by its pulse.

The pulse fallback found the offer by the session DIR — so the old session
(app-development-10), pulsing into its successor's arc, confirmed the reception
for it. Now the caller's own id ($CLAUDE_CODE_SESSION_ID, + its AO mapping) must
be the session the offer was given to; an unbound reservation may be confirmed by
anyone but the giving session.
"""

from __future__ import annotations

import pytest

from tide import cli, handoff_queue as hq, paths, registry, sessions
from tide.arc import stream


@pytest.fixture
def seam(tmp_project, tmp_path, monkeypatch):
    from tests.conftest import build_tide_skeleton

    home = tmp_path / "control-home"
    home.mkdir()
    build_tide_skeleton(home, name="home", control_home=True)
    monkeypatch.setenv(paths.TIDE_HOME_ENV, str(home))
    stream.new_thread(tmp_project, "seam", goal="keep the seam")
    new = stream.new_session(tmp_project, "seam", "successor", goal="carry on")
    (new / "input").mkdir(exist_ok=True)
    seed = new / "input" / "handoff-seed.md"
    seed.write_text("# seed\n", encoding="utf-8")
    hq.offer(home, "seam", arc="seam/successor", project="proj", seed=str(seed),
             from_session="old-sid")
    key = hq.list_offers(home)[0]["name"]
    hq.reserve(home, key, session="pre-pick")
    from tide import offload
    monkeypatch.setattr(offload, "_main_gate", lambda root, ref: None)  # not under test
    monkeypatch.chdir(tmp_project)
    return {"home": home, "key": key, "new": new}


def _pulse(monkeypatch, sid):
    if sid:
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)
    return cli.main(["offload", "seam/successor", "--cursor", "работаю"])


def _status(seam):
    return hq.list_offers(seam["home"])[0]["status"]


def test_old_session_pulse_into_successor_does_not_confirm(seam, monkeypatch, capsys):
    assert _pulse(monkeypatch, "old-sid") == 0
    assert "confirmed by this pulse" not in capsys.readouterr().out
    assert _status(seam) == hq.STATUS_OFFERED


def test_unknown_caller_does_not_confirm(seam, monkeypatch):
    assert _pulse(monkeypatch, None) == 0
    assert _status(seam) == hq.STATUS_OFFERED


def test_reserved_session_confirms(seam, monkeypatch, capsys):
    assert _pulse(monkeypatch, "pre-pick") == 0
    assert "confirmed by this pulse" in capsys.readouterr().out
    rec = hq.list_offers(seam["home"])[0]
    assert rec["status"] == hq.STATUS_TAKEN and rec["taken_by"] == "pre-pick"


def test_unbound_reservation_confirmed_by_a_non_origin(seam, monkeypatch):
    # AO pre-pick nobody answered yet: the fresh session runs under its real id
    assert _pulse(monkeypatch, "real-new") == 0
    rec = hq.list_offers(seam["home"])[0]
    assert rec["status"] == hq.STATUS_TAKEN and rec["taken_by"] == "real-new"


def test_bound_reservation_needs_the_learnt_real_id(seam, monkeypatch):
    # tide already learnt claude's real id behind the pre-pick: a stranger may not
    registry.record(seam["home"], "pre-pick", "proj-7", "seam/successor")
    sessions.remember_ao_sid(seam["home"], "proj-7", "real-new")
    assert _pulse(monkeypatch, "stranger") == 0
    assert _status(seam) == hq.STATUS_OFFERED
    assert _pulse(monkeypatch, "real-new") == 0
    assert _status(seam) == hq.STATUS_TAKEN


def test_ao_mapping_lets_the_new_session_confirm(seam, monkeypatch):
    # inside AO: $AO_SESSION_ID maps to the reserved pre-pick through the registry
    registry.record(seam["home"], "pre-pick", "proj-7", "seam/successor")
    monkeypatch.setenv("AO_SESSION_ID", "proj-7")
    assert _pulse(monkeypatch, "whatever-real") == 0
    assert _status(seam) == hq.STATUS_TAKEN


def test_may_confirm_refuses_the_origin(seam):
    rec = hq.list_offers(seam["home"])[0]
    assert hq.may_confirm(seam["home"], rec, ["old-sid"]) is False
    assert hq.may_confirm(seam["home"], rec, []) is False
    assert hq.may_confirm(seam["home"], rec, ["pre-pick"]) is True

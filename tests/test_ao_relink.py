"""Agent Orchestrator: tide learns claude's REAL session id after start.

AO mints claude's id itself (``claude --session-id=<its uuid>``), so the id tide
pre-picked at launch — written into the passport, the handoff queue and the registry —
is answered by no chat. SessionStart stashes the real id per AO session; the confirm
hook stamps it; :func:`tide.sessions.ao_relink` swaps every leftover pre-pick for it.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from tide import cli, fields, handoff_queue as hq, registry, sessions

AO = "proj-1"
PRE = "3f6ceb8a-0000-4000-8000-000000000001"   # tide's pre-pick (no chat answers to it)
REAL = "94b6c3d5-0000-4000-8000-000000000002"  # the id AO actually started claude with


@pytest.fixture
def world(tmp_control_home, tmp_path, monkeypatch):
    """A control-home + a project whose session waits on a handoff reserved for PRE."""
    from tide.init_home import scaffold_project

    home = tmp_control_home
    proj = tmp_path / "proj"
    proj.mkdir()
    scaffold_project(proj, name="proj")
    sdir = proj / ".tide" / "arcs" / "01-thread" / "arcs" / "02-ao"
    (sdir / "input").mkdir(parents=True)
    (sdir / "arc.md").write_text("# 02-ao\n\nstatus: active\n", encoding="utf-8")
    seed = sdir / "input" / "handoff-seed.md"
    seed.write_text("seed\n", encoding="utf-8")
    hq.offer(home, "ao", arc="01-thread/02-ao", project="proj", seed=str(seed))
    hq.reserve(home, "ao", session=PRE)
    monkeypatch.setenv("TIDE_HOME", str(home))
    return {"home": home, "proj": proj, "sdir": sdir, "pp": sdir / "arc.md"}


def _rec(home):
    return hq.list_offers(home)[0]


def _launcher_recorded(w):
    """What launch_session does after a successful AO spawn."""
    registry.record(w["home"], PRE, AO, str(w["sdir"]))


def _sweeper_recorded(w):
    """What SessionStart's reconcile does inside AO (own handle = $AO_SESSION_ID)."""
    registry.record(w["home"], REAL, AO, str(w["proj"]))


def _bug_state(w):
    """The live 09.10 state: confirmed under the pre-pick everywhere."""
    _sweeper_recorded(w)
    _launcher_recorded(w)
    hq.confirm_for_session(w["home"], PRE)


# --- ao_relink, the core ---------------------------------------------------------

def test_mismatch_is_corrected_everywhere(world):
    _bug_state(world)
    assert fields.read_field(world["pp"], "claude-session") == PRE
    assert _rec(world["home"])["taken_by"] == PRE

    got = sessions.ao_relink(world["home"], AO, REAL, project_root=world["proj"])

    assert got == [PRE]
    assert fields.read_field(world["pp"], "claude-session") == REAL
    rec = _rec(world["home"])
    assert rec["taken_by"] == REAL and rec["pickup_session"] == REAL
    reg = registry.read(world["home"])
    assert PRE not in reg
    assert reg[REAL]["handle"] == AO
    assert reg[REAL]["arc"] == str(world["sdir"])  # the session dir beats the project root


def test_relink_is_idempotent(world):
    _bug_state(world)
    sessions.ao_relink(world["home"], AO, REAL, project_root=world["proj"])
    before = (world["pp"].read_text(), Path(_rec(world["home"])["path"]).read_text(),
              registry.registry_file(world["home"]).read_text())
    assert sessions.ao_relink(world["home"], AO, REAL, project_root=world["proj"]) == []
    after = (world["pp"].read_text(), Path(_rec(world["home"])["path"]).read_text(),
             registry.registry_file(world["home"]).read_text())
    assert before == after


def test_unknown_ao_session_is_a_noop(world):
    _bug_state(world)
    reg = registry.registry_file(world["home"]).read_text()
    assert sessions.ao_relink(world["home"], "other-7", REAL, project_root=world["proj"]) == []
    assert fields.read_field(world["pp"], "claude-session") == PRE
    assert _rec(world["home"])["taken_by"] == PRE
    assert registry.registry_file(world["home"]).read_text() == reg


def test_a_sid_with_a_real_chat_is_never_rewritten(world, tmp_path):
    # A ⟳ continuation records the OLD session's sid against the new AO handle; that
    # sid has a real transcript — it is history, not a pre-pick.
    _bug_state(world)
    chats = tmp_path / "claude-config" / "projects" / "-some-project"
    chats.mkdir(parents=True)
    (chats / "{0}.jsonl".format(PRE)).write_text("{}\n")
    assert sessions.ao_relink(world["home"], AO, REAL, project_root=world["proj"]) == []
    assert fields.read_field(world["pp"], "claude-session") == PRE


# --- the hooks ------------------------------------------------------------------

def _session_start(monkeypatch, cwd, sid):
    monkeypatch.chdir(cwd)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": sid})))
    return cli.main(["hook", "session-start"])


def _confirm(monkeypatch, cwd, sid):
    monkeypatch.chdir(cwd)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": sid})))
    return cli.main(["hook", "handoff-confirm"])


def test_ao_pickup_end_to_end_records_the_real_id(world, monkeypatch, capsys):
    monkeypatch.setenv("AO_SESSION_ID", AO)
    # SessionStart fires first (AO starts claude), before the launcher's registry write
    assert _session_start(monkeypatch, world["proj"], REAL) == 0
    stash = json.loads((world["home"] / sessions.AO_STASH_FILENAME).read_text())
    assert stash[AO]["sid"] == REAL
    _launcher_recorded(world)  # ao spawn returned → launcher records the pre-pick
    # the first turn echoes tide's pre-pick into the confirm hook
    assert _confirm(monkeypatch, world["proj"], PRE) == 0
    assert "confirmed" in capsys.readouterr().out
    rec = _rec(world["home"])
    assert rec["status"] == hq.STATUS_TAKEN and rec["taken_by"] == REAL
    assert fields.read_field(world["pp"], "claude-session") == REAL
    assert PRE not in registry.read(world["home"])
    assert registry.read(world["home"])[REAL]["handle"] == AO


def test_session_start_repairs_an_already_wrong_record(world, monkeypatch):
    _bug_state(world)
    monkeypatch.setenv("AO_SESSION_ID", AO)
    assert _session_start(monkeypatch, world["proj"], REAL) == 0
    assert fields.read_field(world["pp"], "claude-session") == REAL
    assert _rec(world["home"])["taken_by"] == REAL


def test_confirm_uses_the_process_env_when_there_is_no_stash(world, monkeypatch):
    _launcher_recorded(world)
    monkeypatch.setenv("AO_SESSION_ID", AO)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", REAL)
    assert _confirm(monkeypatch, world["proj"], PRE) == 0
    assert _rec(world["home"])["taken_by"] == REAL
    assert fields.read_field(world["pp"], "claude-session") == REAL


def test_no_ao_env_leaves_everything_as_before(world, monkeypatch):
    _launcher_recorded(world)
    assert _session_start(monkeypatch, world["proj"], REAL) == 0
    assert not (world["home"] / sessions.AO_STASH_FILENAME).exists()
    assert _confirm(monkeypatch, world["proj"], PRE) == 0
    rec = _rec(world["home"])
    assert rec["taken_by"] == PRE  # outside AO the minted id IS claude's id
    assert fields.read_field(world["pp"], "claude-session") == PRE
    assert PRE in registry.read(world["home"])


def test_ao_without_a_known_real_id_does_not_guess(world, monkeypatch):
    # No stash, no env: the payload may be the echoed pre-pick — never relink onto it.
    _sweeper_recorded(world)
    _launcher_recorded(world)
    monkeypatch.setenv("AO_SESSION_ID", AO)
    assert _confirm(monkeypatch, world["proj"], PRE) == 0
    assert _rec(world["home"])["taken_by"] == PRE
    assert REAL in registry.read(world["home"])  # the sweeper's real entry survives

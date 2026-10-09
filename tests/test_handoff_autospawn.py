"""Decision 07 (handoff-autospawn): `tide handoff` raises the fresh session itself.

The offer is hung exactly as before, then the CLI presses the board's ▶ on it —
the same door (`tide pickup` → launch_session), so reservation, registry record,
first-prompt confirm and the AO real-sid relink behave identically. A failed
spawn leaves a plain `offered` record; `--no-spawn` keeps the pull model; `close`
never spawns; a later ▶ on the raised offer never spawns a second session.

Every adapter here is a fake — no real terminal or AO session is ever opened.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from tide import cli, fields, handoff_queue as hq, registry, roster
from tide.adapters.base import SpawnResult, TerminalAdapter
from tide.arc import stream
from tide.launcher import handoff, menu, pickup

AO_NEW = "proj-7"                                  # the AO session the spawn returns
REAL = "94b6c3d5-0000-4000-8000-0000000000aa"      # claude's real id inside it


class _Fake(TerminalAdapter):
    name = "fake"

    def __init__(self, *, ok=True, ref="term_x"):
        self._ok, self._ref = ok, ref
        self.spawned = None
        self.env_seen = {}
        self.focused = []

    def spawn(self, *, command, cwd, title="tide", dry_run=False):
        self.spawned = {"command": command, "cwd": cwd, "title": title}
        self.env_seen = {k: os.environ.get(k) for k in handoff._DETACH_ENV}
        if not self._ok:
            return SpawnResult(ok=False, detail="fake failure")
        return SpawnResult(ok=True, ref=self._ref, detail="fake spawn")

    def focus(self, handle):
        self.focused.append(handle)
        return True


class _FakeAo(_Fake):
    name = "ao"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Control-home (rostered project) + a project with an open thread, cwd = project."""
    from tests.conftest import build_tide_skeleton

    home = tmp_path / "home"
    home.mkdir()
    build_tide_skeleton(home, name="home", control_home=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    build_tide_skeleton(proj, name="proj")
    roster.add(home, "proj", str(proj))
    stream.new_thread(proj, "seam", goal="keep the seam clean")
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.delenv(handoff.NO_SPAWN_ENV, raising=False)  # auto-spawn ON
    monkeypatch.chdir(proj)
    return {"home": home, "proj": proj}


def _use(monkeypatch, adapter):
    monkeypatch.setattr(pickup, "get_adapter", lambda name=None: adapter)
    return adapter


def _spy_pickup(monkeypatch):
    calls = []
    real = pickup.run_pickup

    def spy(home, key, **kw):
        calls.append(key)
        return real(home, key, **kw)

    monkeypatch.setattr(pickup, "run_pickup", spy)
    return calls


def _offer(home):
    (rec,) = hq.list_offers(home)
    return rec


# --- the auto-spawn itself -------------------------------------------------------

def test_handoff_spawns_the_offer_through_pickup(world, monkeypatch, capsys):
    adapter = _use(monkeypatch, _Fake())
    calls = _spy_pickup(monkeypatch)
    rc = cli.main(["handoff", "seam", "--from-session", "old-sid"])
    out = capsys.readouterr().out
    assert rc == 0
    rec = _offer(world["home"])
    # pickup was pressed on exactly the offer the handoff hung
    assert calls == [rec["name"]]
    assert adapter.spawned is not None
    # one door: reserved for the minted sid, registry recorded, still `offered`
    # until the fresh session's first message
    assert rec["status"] == hq.STATUS_OFFERED
    sid = rec["pickup_session"]
    assert sid and sid != "-"
    assert "--session-id {0}".format(sid) in " ".join(adapter.spawned["command"])
    assert registry.recorded_handle(world["home"], sid) == "term_x"
    # the old session is told plainly that it is done — as the LAST line
    last = out.strip().splitlines()[-1]
    assert "передано в новую сессию term_x" in last
    assert "эта сессия больше ничего не делает" in last
    assert "offer hung" not in out


def test_new_fork_spawns_too(world, monkeypatch):
    adapter = _use(monkeypatch, _Fake())
    assert cli.main(["handoff", "seam", "--mode", "new"]) == 0
    assert adapter.spawned is not None
    assert _offer(world["home"])["mode"] == "new"


def test_ao_adapter_names_the_ao_session(world, monkeypatch, capsys):
    _use(monkeypatch, _FakeAo(ref=AO_NEW))
    assert cli.main(["handoff", "seam"]) == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last == ("tide: передано в новую сессию AO {0} — эта сессия больше ничего "
                    "не делает; закрой её, когда удобно".format(AO_NEW))


def test_spawn_runs_without_the_old_sessions_identity(world, monkeypatch):
    # inside AO the caller is the OLD session: its ids must not ride into the spawn
    # (child-of-the-retiring-session / nested-claude refusal), and come back after
    monkeypatch.setenv("AO_SESSION_ID", "proj-1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "old-sid")
    monkeypatch.setenv("CLAUDECODE", "1")
    adapter = _use(monkeypatch, _Fake())
    assert cli.main(["handoff", "seam"]) == 0
    assert all(v is None for v in adapter.env_seen.values()), adapter.env_seen
    assert os.environ["AO_SESSION_ID"] == "proj-1"
    assert os.environ["CLAUDE_CODE_SESSION_ID"] == "old-sid"


# --- graceful fallback -------------------------------------------------------------

def test_failed_spawn_leaves_a_plain_offered_offer(world, monkeypatch, capsys):
    _use(monkeypatch, _Fake(ok=False))
    rc = cli.main(["handoff", "seam"])
    out = capsys.readouterr().out
    assert rc == 0  # the handoff itself succeeded — the offer hangs
    rec = _offer(world["home"])
    assert rec["status"] == hq.STATUS_OFFERED
    # the attempt's reservation is undone — the board shows a normal ▶, not «поднимается»
    assert rec["pickup_session"] == "-" and rec["reserved_at"] == "-"
    assert "сам поднять не вышло" in out and "fake failure" in out
    assert "tide pickup {0}".format(rec["name"]) in out
    assert "передано в" not in out
    # ...and ▶ by hand then works as always
    again = _use(monkeypatch, _Fake())
    assert pickup.run_pickup(world["home"], rec["name"])["action"] == "spawned"
    assert again.spawned is not None


def test_spawn_exception_is_contained(world, monkeypatch, capsys):
    def boom(home, key, **kw):
        raise RuntimeError("adapter exploded")

    monkeypatch.setattr(pickup, "run_pickup", boom)
    assert cli.main(["handoff", "seam"]) == 0
    assert "adapter exploded" in capsys.readouterr().out
    assert _offer(world["home"])["status"] == hq.STATUS_OFFERED


# --- opt-outs ---------------------------------------------------------------------

def test_no_spawn_flag_keeps_the_pull_model(world, monkeypatch, capsys):
    adapter = _use(monkeypatch, _Fake())
    calls = _spy_pickup(monkeypatch)
    assert cli.main(["handoff", "seam", "--no-spawn"]) == 0
    out = capsys.readouterr().out
    assert calls == [] and adapter.spawned is None
    assert "offer hung" in out and "передано в" not in out
    rec = _offer(world["home"])
    assert rec["status"] == hq.STATUS_OFFERED and rec["pickup_session"] == "-"


def test_no_spawn_env_keeps_the_pull_model(world, monkeypatch, capsys):
    monkeypatch.setenv(handoff.NO_SPAWN_ENV, "1")
    adapter = _use(monkeypatch, _Fake())
    assert cli.main(["handoff", "seam"]) == 0
    assert adapter.spawned is None
    assert "offer hung" in capsys.readouterr().out


def test_library_default_never_spawns(world, monkeypatch):
    adapter = _use(monkeypatch, _Fake())
    res = handoff.run_handoff(world["proj"], arc_ref="seam", mode="continue")
    assert res.spawn is None and adapter.spawned is None


def test_close_never_spawns(world, monkeypatch, capsys):
    adapter = _use(monkeypatch, _Fake())
    calls = _spy_pickup(monkeypatch)
    assert cli.main(["handoff", "seam", "--mode", "close"]) == 0
    res = handoff.run_handoff(world["proj"], arc_ref="seam", mode="close", spawn=True)
    assert res.spawn is None
    assert calls == [] and adapter.spawned is None
    assert hq.list_offers(world["home"]) == []
    assert "передано в" not in capsys.readouterr().out


def test_dry_run_never_spawns(world, monkeypatch):
    adapter = _use(monkeypatch, _Fake())
    assert cli.main(["handoff", "seam", "--dry-run"]) == 0
    assert adapter.spawned is None and hq.list_offers(world["home"]) == []


# --- one thread — one orchestrator --------------------------------------------------

def test_later_board_press_focuses_never_spawns_twice(world, monkeypatch):
    _use(monkeypatch, _Fake())
    assert cli.main(["handoff", "seam"]) == 0
    key = _offer(world["home"])["name"]
    second = _use(monkeypatch, _Fake())
    out = pickup.run_pickup(world["home"], key)
    assert out["ok"] and out["action"] in ("focused", "already-launching")
    assert second.spawned is None and second.focused == ["term_x"]
    # once the fresh session said hello the offer is gone from the ▶ list entirely
    hq.confirm_for_session(world["home"], _offer(world["home"])["pickup_session"])
    out = pickup.run_pickup(world["home"], key)
    assert not out["ok"] and "no pending offer" in out["detail"]
    assert second.spawned is None


def test_later_menu_pick_focuses_never_spawns_twice(world, monkeypatch, capsys):
    from tide.launcher import select

    _use(monkeypatch, _Fake())
    assert cli.main(["handoff", "seam"]) == 0
    rec = _offer(world["home"])
    second = _Fake()
    monkeypatch.setattr(menu, "get_adapter", lambda name=None: second)
    monkeypatch.setattr(select, "is_interactive_tty", lambda: True)
    monkeypatch.setattr(menu, "navigate_interactive",
                        lambda entries, handoffs=None: (menu.HANDOFF_PICK, rec))
    monkeypatch.chdir(world["home"])
    assert cli.main(["menu"]) == 0
    assert second.spawned is None and second.focused == ["term_x"]
    assert "[focused]" in capsys.readouterr().out


# --- AO: acceptance + real-sid relink work exactly as after ▶ --------------------

def _hook(monkeypatch, cwd, verb, sid):
    monkeypatch.chdir(cwd)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": sid})))
    return cli.main(["hook", verb])


def test_ao_autospawn_then_reception_relinks_to_the_real_id(world, monkeypatch, capsys):
    # the OLD session (inside AO) runs the handoff
    monkeypatch.setenv("AO_SESSION_ID", "proj-1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "old-sid")
    _use(monkeypatch, _FakeAo(ref=AO_NEW))
    assert cli.main(["handoff", "seam"]) == 0
    rec = _offer(world["home"])
    pre = rec["pickup_session"]
    assert registry.recorded_handle(world["home"], pre) == AO_NEW
    pp = os.path.join(os.path.dirname(os.path.dirname(rec["seed"])), "arc.md")

    # the old session's own next prompt must NOT accept the new offer
    assert _hook(monkeypatch, world["proj"], "handoff-confirm", "old-sid") == 0
    assert _offer(world["home"])["status"] == hq.STATUS_OFFERED

    # the NEW session: AO started claude under its own id
    monkeypatch.setenv("AO_SESSION_ID", AO_NEW)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", REAL)
    assert _hook(monkeypatch, world["proj"], "session-start", REAL) == 0
    capsys.readouterr()
    assert _hook(monkeypatch, world["proj"], "handoff-confirm", pre) == 0
    assert "confirmed" in capsys.readouterr().out
    rec = _offer(world["home"])
    assert rec["status"] == hq.STATUS_TAKEN and rec["taken_by"] == REAL
    assert fields.read_field(pp, "claude-session") == REAL
    reg = registry.read(world["home"])
    assert pre not in reg and reg[REAL]["handle"] == AO_NEW


# --- the raised session is named «проект — нить · NN» — passport and AO sidebar alike

def test_raised_session_is_named_thread_and_number(world, monkeypatch):
    adapter = _use(monkeypatch, _FakeAo(ref=AO_NEW))
    assert cli.main(["handoff", "seam"]) == 0
    rec = _offer(world["home"])
    session_dir = Path(rec["seed"]).parent.parent
    want = "proj — seam · {0}".format(session_dir.name.split("-", 1)[0])
    # SOC-72: after the spawn the AO id joins the name — «… · NN · <AO-id>»
    assert fields.read_field(session_dir / "arc.md", "title") == want + " · " + AO_NEW
    assert adapter.spawned["title"] == want            # → `ao spawn --name`
    # the fresh session is told the title is already set — keep it
    assert "«{0}» уже стоит".format(want) in adapter.spawned["command"][-1]


def test_skill_path_offer_renames_a_hand_titled_session(world, monkeypatch):
    # the /handoff skill births the session itself (`tide arc new-session`), an agent
    # may have titled it by hand; the raise names it «проект — нить · NN» all the same
    adapter = _use(monkeypatch, _FakeAo(ref=AO_NEW))
    stream.new_session(world["proj"], "seam", "seam")                  # 01
    sess = stream.new_session(world["proj"], "seam", "ao")             # 02
    fields.set_field(sess / "arc.md", "title", "seam · ao (заход 2)")
    seed = sess / "input" / "handoff-seed.md"
    seed.parent.mkdir(parents=True, exist_ok=True)
    seed.write_text("# seed\n", encoding="utf-8")
    thread = sess.parent.parent
    assert cli.main(["handoffs", "offer", "ao", "--project", "proj",
                     "--arc", "{0}/{1}".format(thread.name, sess.name),
                     "--seed", str(seed)]) == 0
    assert adapter.spawned["title"] == "proj — seam · 02"
    assert fields.read_field(sess / "arc.md", "title") == "proj — seam · 02 · " + AO_NEW


def test_raised_session_name_uses_the_roster_name(world, monkeypatch):
    # the roster names the project «waifus» while its folder is «proj»: the human
    # knows the roster name, so passport and sidebar carry it
    roster_file = world["home"] / "roster.md"
    text = roster_file.read_text(encoding="utf-8").replace("proj |", "waifus |")
    roster_file.write_text(text, encoding="utf-8")
    adapter = _use(monkeypatch, _FakeAo(ref=AO_NEW))
    assert cli.main(["handoff", "seam"]) == 0
    rec = _offer(world["home"])
    session_dir = Path(rec["seed"]).parent.parent
    want = "waifus — seam · {0}".format(session_dir.name.split("-", 1)[0])
    assert adapter.spawned["title"] == want
    assert fields.read_field(session_dir / "arc.md", "title") == want + " · " + AO_NEW

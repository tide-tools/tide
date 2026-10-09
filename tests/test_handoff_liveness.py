"""SOC-101 F25: `ao spawn` ok is not a reception — the fresh session must be ALIVE.

A fresh Claude in AO can hang on the folder-trust question and fall to `exited` on
the first `ao send`, while the old session already believed the thread was handed
over. Now `spawn_offer` waits for the agent's first signal (AO session live with a
claude id), tries one `resume-agent`, and otherwise undoes the reservation and
tells the OLD session the thread stays with it.

Every adapter is a fake; time is fake too — no AO, no sleeping.
"""

from __future__ import annotations

import sqlite3

import pytest

from tide import cli, fields, handoff_queue as hq
from tide.adapters import ao as ao_mod
from tide.arc import stream
from tide.launcher import handoff, pickup

from tests.test_handoff_autospawn import _FakeAo, _use, world  # noqa: F401  fixture

AO_NEW = "proj-7"


class _Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = 0

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps += 1
        self.t += s


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(handoff, "_clock", c.now)
    monkeypatch.setattr(handoff, "_sleep", c.sleep)
    monkeypatch.delenv(handoff.LIVE_TIMEOUT_ENV, raising=False)
    return c


class _ProbeAo(_FakeAo):
    """A fake AO whose agent gives its first signal after *signal_after* probes
    (None — never), optionally only after a resume-agent."""

    def __init__(self, *, signal_after=None, after_resume=False, resume_ok=True):
        super().__init__(ref=AO_NEW)
        self.signal_after = signal_after
        self.after_resume = after_resume
        self.resume_ok = resume_ok
        self.probes = 0
        self.resumed = []

    def first_signal(self, handle):
        assert handle == AO_NEW
        self.probes += 1
        if self.after_resume:
            return bool(self.resumed)
        return self.signal_after is not None and self.probes > self.signal_after

    def resume_agent(self, handle):
        self.resumed.append(handle)
        return self.resume_ok


def _origin(world):
    """The thread's current session, pinned to the old claude id."""
    proj = world["proj"]
    s = stream.new_session(proj, "seam", "old-work", goal="old work")
    fields.set_field(s / "arc.md", "claude-session", "old-sid")
    return s


def test_live_signal_counts_as_handed_over(world, monkeypatch, capsys, clock):
    adapter = _use(monkeypatch, _ProbeAo(signal_after=2))
    assert cli.main(["handoff", "seam", "--from-session", "old-sid"]) == 0
    out = capsys.readouterr().out
    assert "передано в новую сессию AO {0}".format(AO_NEW) in out.strip().splitlines()[-1]
    assert adapter.probes == 3 and adapter.resumed == []
    (rec,) = hq.list_offers(world["home"])
    assert rec["pickup_session"] not in ("", "-")  # reservation kept


def test_resume_agent_once_then_alive(world, monkeypatch, capsys, clock):
    adapter = _use(monkeypatch, _ProbeAo(after_resume=True))
    assert cli.main(["handoff", "seam"]) == 0
    out = capsys.readouterr().out
    assert adapter.resumed == [AO_NEW]
    assert "передано в новую сессию AO" in out
    assert "поднялась после resume-agent" in out
    # waited ~90 s for the first try (fake clock)
    assert clock.t >= handoff.LIVE_TIMEOUT


def test_no_signal_unreserves_and_keeps_the_thread_with_the_old(world, monkeypatch,
                                                                 capsys, clock):
    origin = _origin(world)
    adapter = _use(monkeypatch, _ProbeAo(signal_after=None))
    assert cli.main(["handoff", "seam", "--from-session", "old-sid"]) == 0
    out = capsys.readouterr().out
    last = out.strip().splitlines()[-1]
    assert adapter.resumed == [AO_NEW]  # exactly one revive
    assert "передано в" not in out
    assert last.startswith("tide: приём не состоялся — не замирай, нить твоя")
    assert AO_NEW in last
    (rec,) = hq.list_offers(world["home"])
    assert rec["status"] == hq.STATUS_OFFERED
    assert rec["pickup_session"] == "-" and rec["reserved_at"] == "-"
    # the old session's passport has the line too — the board and the session see it
    assert "приём не состоялся — не замирай, нить твоя" in (origin / "arc.md").read_text()
    # two waits of 90 s each
    assert clock.t >= 2 * handoff.LIVE_TIMEOUT


def test_timeout_is_a_parameter(world, monkeypatch, clock):
    adapter = _use(monkeypatch, _ProbeAo(signal_after=None))
    rec = hq.offer(world["home"], "seam", arc="-", project="proj", seed="-")
    monkeypatch.setattr(pickup, "run_pickup",
                        lambda home, key, **kw: {"ok": True, "action": "spawned",
                                                 "handle": AO_NEW, "adapter": "ao",
                                                 "detail": "x"})
    out = handoff.spawn_offer(world["home"], rec.stem, live_timeout=6)
    assert out["action"] == handoff.NOT_ALIVE and not out["ok"]
    assert clock.t == pytest.approx(12.0)
    assert adapter.resumed == [AO_NEW]


def test_confirmed_offer_is_a_signal_too(world, monkeypatch, clock):
    # the fresh session already said hello (offer taken) — alive, no AO probe needed
    adapter = _use(monkeypatch, _ProbeAo(signal_after=None))
    monkeypatch.setattr(handoff, "_offer_taken", lambda home, key: True)
    assert cli.main(["handoff", "seam"]) == 0
    assert adapter.resumed == []


def test_adapter_without_probe_keeps_the_old_behaviour(world, monkeypatch, capsys, clock):
    _use(monkeypatch, _FakeAo(ref=AO_NEW))  # no first_signal: orca/tmux-like
    assert cli.main(["handoff", "seam"]) == 0
    assert "передано в новую сессию AO" in capsys.readouterr().out
    assert clock.sleeps == 0


def test_auto_handoff_does_not_stamp_handed_off_when_not_alive(world, monkeypatch,
                                                               capsys, clock):
    origin = _origin(world)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "old-sid")
    _use(monkeypatch, _ProbeAo(signal_after=None))
    assert cli.main(["handoff", "continue", "--auto"]) == 0
    out = capsys.readouterr().out
    assert "передано в" not in out
    assert "приём не состоялся" in out.strip().splitlines()[-1]
    assert not (fields.read_field(origin / "arc.md", "handed-off") or "").strip()


# --- the AO probe itself -----------------------------------------------------------

def _db(tmp_path, rows):
    db = tmp_path / "ao.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, agent_session_id TEXT)")
    con.executemany("INSERT INTO sessions VALUES (?, ?)", rows)
    con.commit()
    con.close()
    return db


def test_ao_first_signal_reads_the_claude_id(tmp_path, monkeypatch):
    db = _db(tmp_path, [("p-1", "aa3f7bdb"), ("p-2", "")])
    monkeypatch.setenv(ao_mod.AO_DB_ENV, str(db))
    a = ao_mod.AoAdapter()
    live = {"id": "x", "isTerminated": False, "activity": {"state": "waiting_input"}}
    monkeypatch.setattr(a, "session", lambda h: dict(live, id=h))
    assert a.agent_session_id("p-1") == "aa3f7bdb"
    assert a.first_signal("p-1") is True
    assert a.first_signal("p-2") is False          # trust question: no claude id yet
    assert a.first_signal("p-9") is False          # unknown in the db
    monkeypatch.setattr(a, "session", lambda h: dict(live, activity={"state": "exited"}))
    assert a.first_signal("p-1") is False          # agent exited


def test_ao_agent_id_from_session_json_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(ao_mod.AO_DB_ENV, str(tmp_path / "none.db"))
    a = ao_mod.AoAdapter()
    monkeypatch.setattr(a, "session", lambda h: {"agentSessionId": "zz"})
    assert a.agent_session_id("p-1") == "zz"


def test_ao_resume_agent_calls_ao(monkeypatch):
    calls = []
    monkeypatch.setattr(ao_mod, "ao_available", lambda: True)
    monkeypatch.setattr(ao_mod, "_call", lambda args, timeout=60: calls.append(args) or "")
    assert ao_mod.AoAdapter().resume_agent("p-3") is True
    assert calls == [["session", "resume-agent", "p-3"]]

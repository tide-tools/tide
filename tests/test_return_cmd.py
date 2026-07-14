"""tide return — one return path: focus the recorded terminal, else respawn resume."""

from __future__ import annotations

from pathlib import Path

from tide import fields, registry
from tide.adapters.base import SpawnResult, TerminalAdapter
from tide.launcher import return_cmd


class _FakeAdapter(TerminalAdapter):
    name = "fake"

    def __init__(self, *, focus_ok: bool, spawn_ok: bool = True):
        self._focus_ok = focus_ok
        self._spawn_ok = spawn_ok
        self.focused_with = None
        self.spawned = None

    def spawn(self, *, command, cwd, title="tide", dry_run=False):
        self.spawned = {"command": command, "cwd": cwd, "title": title, "dry_run": dry_run}
        if not self._spawn_ok:
            return SpawnResult(ok=False, detail="fake spawn failure")
        return SpawnResult(ok=True, ref="term_new", detail="fake spawn")

    def focus(self, handle):
        self.focused_with = handle
        return self._focus_ok


SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _patched(monkeypatch, adapter):
    monkeypatch.setattr(return_cmd, "get_adapter", lambda name=None: adapter)


def test_return_focuses_recorded_live_terminal(tmp_path, monkeypatch):
    registry.record(tmp_path, SID, "term_live", "/arc")
    adapter = _FakeAdapter(focus_ok=True)
    _patched(monkeypatch, adapter)
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path)
    assert out == {"ok": True, "action": "focused", "handle": "term_live",
                   "detail": "focused the session's terminal"}
    assert adapter.focused_with == "term_live"
    assert adapter.spawned is None  # no duplicate tab — the whole point


def test_return_respawns_resume_when_focus_fails(tmp_path, monkeypatch):
    registry.record(tmp_path, SID, "term_dead", "/arc")
    adapter = _FakeAdapter(focus_ok=False)
    _patched(monkeypatch, adapter)
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path, title="my thread")
    assert out["ok"] is True and out["action"] == "resumed"
    joined = " ".join(adapter.spawned["command"])
    assert "--resume {0}".format(SID) in joined
    assert adapter.spawned["title"] == "my thread"
    # the NEW handle is recorded under the same sid — return stays exact next time
    assert registry.recorded_handle(tmp_path, SID) == "term_new"


def test_return_respawns_when_sid_unknown(tmp_path, monkeypatch):
    adapter = _FakeAdapter(focus_ok=True)
    _patched(monkeypatch, adapter)
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path)
    assert out["action"] == "resumed"
    assert adapter.focused_with is None  # nothing recorded — nothing to probe


def test_return_reports_spawn_failure(tmp_path, monkeypatch):
    adapter = _FakeAdapter(focus_ok=False, spawn_ok=False)
    _patched(monkeypatch, adapter)
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path)
    assert out["ok"] is False and out["action"] == "failed"
    assert registry.recorded_handle(tmp_path, SID) is None  # no lie in the registry


def test_return_refuses_bad_sid(tmp_path, monkeypatch):
    adapter = _FakeAdapter(focus_ok=True)
    _patched(monkeypatch, adapter)
    out = return_cmd.run_return(tmp_path, sid="not a sid!", project=tmp_path)
    assert out["ok"] is False and "bad sid" in out["detail"]


def test_return_legacy_arc_key_tolerated(tmp_path, monkeypatch):
    # pre-cand-94 records were keyed by ARC path — they must still resolve
    registry.record(tmp_path, "/abs/arc/path", "term_old", "/abs/arc/path")
    adapter = _FakeAdapter(focus_ok=True)
    _patched(monkeypatch, adapter)
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path, arc="/abs/arc/path")
    assert out["action"] == "focused" and out["handle"] == "term_old"


def test_return_dry_run_builds_without_executing(tmp_path, monkeypatch):
    registry.record(tmp_path, SID, "term_live", "/arc")
    adapter = _FakeAdapter(focus_ok=True)
    _patched(monkeypatch, adapter)
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path, dry_run=True)
    # dry-run never probes focus (a focus IS a side effect) and never records
    assert adapter.focused_with is None
    assert adapter.spawned["dry_run"] is True
    assert out["action"] == "resumed"
    assert registry.recorded_handle(tmp_path, SID) == "term_live"  # untouched


def test_return_never_resurrects_a_dissolved_head(tmp_path, monkeypatch):
    # live 14.07: the origin's tab died after dissolution — return must NOT respawn
    # it (one holder per thread); it reports "gone" instead
    from tide.arc import stream

    (tmp_path / ".tide" / "arcs").mkdir(parents=True)
    stream.new_thread(tmp_path, "demo", goal="ship")
    sess = stream.new_session(tmp_path, "demo", "origin")
    fields.set_field(sess / "arc.md", "claude-session", SID)
    fields.set_field(sess / "arc.md", "dissolved", "2026-07-14T14:11:19")
    adapter = _FakeAdapter(focus_ok=False)  # tab is dead
    _patched(monkeypatch, adapter)
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path, arc=str(sess))
    assert out["ok"] is False and out["action"] == "gone"
    assert "dissolved" in out["detail"]
    assert adapter.spawned is None  # no resurrection


def test_return_still_focuses_a_live_dissolved_tab(tmp_path, monkeypatch):
    # a look-back reads, it doesn't hold — focusing the still-open tab is fine
    from tide.arc import stream

    (tmp_path / ".tide" / "arcs").mkdir(parents=True)
    stream.new_thread(tmp_path, "demo", goal="ship")
    sess = stream.new_session(tmp_path, "demo", "origin")
    fields.set_field(sess / "arc.md", "claude-session", SID)
    fields.set_field(sess / "arc.md", "dissolved", "2026-07-14T14:11:19")
    registry.record(tmp_path, SID, "term_live", str(sess))
    adapter = _FakeAdapter(focus_ok=True)
    _patched(monkeypatch, adapter)
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path, arc=str(sess))
    assert out["ok"] is True and out["action"] == "focused"


def test_return_respawns_an_ended_head(tmp_path, monkeypatch):
    # ended is NOT dissolution: closed the tab, came back → resume reopens it
    from tide.arc import stream

    (tmp_path / ".tide" / "arcs").mkdir(parents=True)
    stream.new_thread(tmp_path, "demo", goal="ship")
    sess = stream.new_session(tmp_path, "demo", "origin")
    fields.set_field(sess / "arc.md", "claude-session", SID)
    fields.set_field(sess / "arc.md", "ended", "2026-07-14T14:11:48")
    adapter = _FakeAdapter(focus_ok=False)
    _patched(monkeypatch, adapter)
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path, arc=str(sess))
    assert out["ok"] is True and out["action"] == "resumed"


def test_gate_survives_a_mismatching_arc_hint(tmp_path, monkeypatch):
    # live 14.07: an arc param pointing at a SIBLING session made the gate give up
    # and resurrect a dissolved head — the arc is a hint, the sid scan is the truth
    from tide.arc import stream

    (tmp_path / ".tide" / "arcs").mkdir(parents=True)
    stream.new_thread(tmp_path, "demo", goal="ship")
    origin = stream.new_session(tmp_path, "demo", "origin")
    sibling = stream.new_session(tmp_path, "demo", "priem")
    fields.set_field(origin / "arc.md", "claude-session", SID)
    fields.set_field(origin / "arc.md", "dissolved", "2026-07-14T16:25:40")
    fields.set_field(sibling / "arc.md", "claude-session", "other-sid")
    adapter = _FakeAdapter(focus_ok=False)
    _patched(monkeypatch, adapter)
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path, arc=str(sibling))
    assert out["action"] == "gone" and adapter.spawned is None


def test_force_reenters_a_dissolved_head(tmp_path, monkeypatch):
    # the HUMAN's confirmed override (Гриша 14.07: «достать из старой — мало ли
    # что»): force skips the dissolved-gate; agents never pass force
    from tide.arc import stream

    (tmp_path / ".tide" / "arcs").mkdir(parents=True)
    stream.new_thread(tmp_path, "demo", goal="ship")
    sess = stream.new_session(tmp_path, "demo", "origin")
    fields.set_field(sess / "arc.md", "claude-session", SID)
    fields.set_field(sess / "arc.md", "dissolved", "2026-07-14T16:25:40")
    adapter = _FakeAdapter(focus_ok=False)
    _patched(monkeypatch, adapter)
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path,
                                arc=str(sess), force=True)
    assert out["ok"] is True and out["action"] == "resumed"
    assert "--resume {0}".format(SID) in " ".join(adapter.spawned["command"])

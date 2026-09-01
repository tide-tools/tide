"""tide return — one return path: focus the recorded terminal, else respawn resume."""

from __future__ import annotations

from pathlib import Path

from tide import fields, registry
from tide.adapters.base import SpawnResult, TerminalAdapter
from tide.launcher import return_cmd


class _FakeAdapter(TerminalAdapter):
    name = "fake"

    def __init__(self, *, focus_ok: bool, spawn_ok: bool = True, say_ok: bool = True):
        self._focus_ok = focus_ok
        self._spawn_ok = spawn_ok
        self._say_ok = say_ok
        self.focused_with = None
        self.spawned = None
        self.said = []

    def spawn(self, *, command, cwd, title="tide", dry_run=False):
        self.spawned = {"command": command, "cwd": cwd, "title": title, "dry_run": dry_run}
        if not self._spawn_ok:
            return SpawnResult(ok=False, detail="fake spawn failure")
        return SpawnResult(ok=True, ref="term_new", detail="fake spawn")

    def focus(self, handle):
        self.focused_with = handle
        return self._focus_ok

    def say(self, handle, text):
        self.said.append((handle, text))
        return self._say_ok


SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _patched(monkeypatch, adapter):
    monkeypatch.setattr(return_cmd, "get_adapter", lambda name=None: adapter)


def test_return_focuses_recorded_live_terminal(tmp_path, monkeypatch):
    registry.record(tmp_path, SID, "term_live", "/arc")
    adapter = _FakeAdapter(focus_ok=True)
    _patched(monkeypatch, adapter)
    # the verdict now carries screen_locked (a locked mac raises no window, and the
    # surface must say so) — pin the probe so the test doesn't read this machine
    monkeypatch.setattr(return_cmd, "_screen_locked", lambda: False)
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path)
    assert out == {"ok": True, "action": "focused", "handle": "term_live",
                   "detail": "focused the session's terminal",
                   "screen_locked": False}
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


def test_return_migrates_legacy_arc_keys_out_loud(tmp_path, monkeypatch):
    # pre-cand-94 records were keyed by ARC path. They name no session, so they are
    # dropped — and the drop is REPORTED, never silent (cand 144).
    registry.record(tmp_path, "/abs/arc/path", "term_old", "/abs/arc/path")
    adapter = _FakeAdapter(focus_ok=True)
    _patched(monkeypatch, adapter)
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path, arc="/abs/arc/path")
    assert out["action"] == "resumed"  # never focus a tab we cannot tie to THIS sid
    assert "legacy arc-keyed" in out["detail"]
    assert registry.read(tmp_path).get("/abs/arc/path") is None


def test_return_forgets_the_handle_a_failed_focus_proved_dead(tmp_path, monkeypatch):
    # protuхший handle → чистим и идём дальше: no re-probing the same corpse, and no
    # second record of it left behind to focus next time (c636275).
    registry.record(tmp_path, SID, "term_dead", "/arc")
    registry.record(tmp_path, "other-sid", "term_dead", "/arc2")
    adapter = _FakeAdapter(focus_ok=False)
    _patched(monkeypatch, adapter)
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path)
    assert out["action"] == "resumed"
    assert registry.recorded_handle(tmp_path, "other-sid") is None  # corpse swept
    assert registry.recorded_handle(tmp_path, SID) == "term_new"  # the live tab


def test_return_dry_run_predicts_focus_without_focusing(tmp_path, monkeypatch):
    registry.record(tmp_path, SID, "term_live", "/arc")
    adapter = _FakeAdapter(focus_ok=True)
    _patched(monkeypatch, adapter)
    monkeypatch.setattr(registry, "orca_live_handles", lambda: {"term_live"})
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path, dry_run=True)
    # a dry run never probes focus (a focus IS a side effect) and never records —
    # but it must still SAY what the live path would do, or it reads as a decision
    # to respawn when the registry is in fact fine
    assert adapter.focused_with is None
    assert adapter.spawned is None
    assert out["action"] == "focused" and out["handle"] == "term_live"
    assert registry.recorded_handle(tmp_path, SID) == "term_live"  # untouched


def test_return_dry_run_predicts_focus_when_orca_stays_silent(tmp_path, monkeypatch):
    # an empty live-set is an orca outage or a background-adopted terminal (cand 101),
    # never proof of death — predict the same first move the live path makes
    registry.record(tmp_path, SID, "term_live", "/arc")
    _patched(monkeypatch, _FakeAdapter(focus_ok=True))
    monkeypatch.setattr(registry, "orca_live_handles", lambda: set())
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path, dry_run=True)
    assert out["action"] == "focused"


def test_return_dry_run_predicts_respawn_for_a_dead_handle(tmp_path, monkeypatch):
    registry.record(tmp_path, SID, "term_dead", "/arc")
    adapter = _FakeAdapter(focus_ok=True)
    _patched(monkeypatch, adapter)
    monkeypatch.setattr(registry, "orca_live_handles", lambda: {"term_other"})
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path, dry_run=True)
    assert out["action"] == "resumed"
    assert adapter.spawned["dry_run"] is True
    assert registry.recorded_handle(tmp_path, SID) == "term_dead"  # dry-run writes nothing


def test_legacy_dissolved_stamp_no_longer_gates(tmp_path, monkeypatch):
    # canon №1 simplified (Гриша 16.07): past sessions are open history — a legacy
    # dissolved: stamp in an old passport does not block re-entry anymore
    from tide.arc import stream

    (tmp_path / ".tide" / "arcs").mkdir(parents=True)
    stream.new_thread(tmp_path, "demo", goal="ship")
    sess = stream.new_session(tmp_path, "demo", "origin")
    fields.set_field(sess / "arc.md", "claude-session", SID)
    fields.set_field(sess / "arc.md", "dissolved", "2026-07-14T14:11:19")
    adapter = _FakeAdapter(focus_ok=False)  # tab is dead
    _patched(monkeypatch, adapter)
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path, arc=str(sess))
    assert out["ok"] is True and out["action"] == "resumed"
    assert "--resume {0}".format(SID) in " ".join(adapter.spawned["command"])


# --- --say: возврат даёт сессии ХОД, а не только окно (работа 44 п.8) --------

WORD = "Человек нажал «да»: план работы 44 согласован — веди её до приёмки"


def test_say_gives_the_live_session_a_turn(tmp_path, monkeypatch):
    # фокус поднимает вкладку и молчит: агент узнаёт о нажатии человека только
    # случайно, со своего следующего хода. --say кладёт слова В сессию.
    registry.record(tmp_path, SID, "term_live", "/arc")
    adapter = _FakeAdapter(focus_ok=True)
    _patched(monkeypatch, adapter)
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path, say=WORD)
    assert out["action"] == "focused" and out["said"] is True
    assert adapter.said == [("term_live", WORD)]
    assert adapter.spawned is None  # вторая сессия по нити НЕ рождается


def test_say_that_did_not_land_is_reported_as_a_miss(tmp_path, monkeypatch):
    # окно перед человеком есть, хода у агента нет — обещать доставку нельзя
    registry.record(tmp_path, SID, "term_live", "/arc")
    adapter = _FakeAdapter(focus_ok=True, say_ok=False)
    _patched(monkeypatch, adapter)
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path, say=WORD)
    assert out["ok"] is True and out["action"] == "focused"
    assert out["said"] is False and "did NOT reach" in out["detail"]


def test_say_rides_into_a_respawned_conversation(tmp_path, monkeypatch):
    # вкладки нет — реплика уезжает первым ходом ТОЙ ЖЕ беседы (--resume <sid>),
    # не второй сессией
    adapter = _FakeAdapter(focus_ok=False)
    _patched(monkeypatch, adapter)
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path, say=WORD)
    assert out["action"] == "resumed" and out["said"] is True
    joined = " ".join(adapter.spawned["command"])
    assert "--resume {0}".format(SID) in joined and WORD in joined


def test_say_is_flattened_to_one_line(tmp_path, monkeypatch):
    # перевод строки отправил бы реплику на полпути, а хвост — в следующий промпт
    registry.record(tmp_path, SID, "term_live", "/arc")
    adapter = _FakeAdapter(focus_ok=True)
    _patched(monkeypatch, adapter)
    return_cmd.run_return(tmp_path, sid=SID, project=tmp_path,
                          say="первая строка\nвторая  строка\n")
    assert adapter.said == [("term_live", "первая строка вторая строка")]


def test_say_sends_nothing_on_a_dry_run(tmp_path, monkeypatch):
    registry.record(tmp_path, SID, "term_live", "/arc")
    adapter = _FakeAdapter(focus_ok=True)
    _patched(monkeypatch, adapter)
    monkeypatch.setattr(registry, "orca_live_handles", lambda: {"term_live"})
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path,
                                say=WORD, dry_run=True)
    assert out["action"] == "focused" and out["said"] is False
    assert adapter.said == []


def test_no_say_leaves_the_verdict_shape_alone(tmp_path, monkeypatch):
    # старые доски читают вердикт как раньше: без --say поля `said` нет вовсе
    registry.record(tmp_path, SID, "term_live", "/arc")
    _patched(monkeypatch, _FakeAdapter(focus_ok=True))
    out = return_cmd.run_return(tmp_path, sid=SID, project=tmp_path)
    assert "said" not in out


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


def test_force_stays_accepted_as_noop(tmp_path, monkeypatch):
    # back-compat: старые доски шлют --force; гейт снят, флаг тихо принимается
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

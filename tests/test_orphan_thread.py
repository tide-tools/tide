"""SOC-167 (F87): нить-сирота — ведущая не жива в AO, оффера нет → менеджеру.

Случай 09.10: board · 06 (AO tide-stack-33) упала SIGSEGV между ходами, оффера не
было, старые сторожа (автоподъём оффера, сторож приёма, «нить встала», две головы)
её не видели — нить стояла без ведущей, пока не подняли руками.

AO, отправка менеджеру, подъём — подделки; ничего наружу.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tide import handoff_queue as hq
from tide.adapters import claude_proc
from tide.arc import holder
from tide.hooks import orphan
from tide.launcher import autohandoff

from tests.conftest import build_tide_skeleton

SID_OLD, SID_HEAD = "sid-board-5", "sid-board-6"
AO = {SID_OLD: "tide-stack-29", SID_HEAD: "tide-stack-33"}
DEAD = {"isTerminated": True, "status": "terminated", "activity": {"state": "exited"}}
LIVE = {"isTerminated": False, "status": "working", "activity": {"state": "active"}}


def _session(thread: Path, name: str, sid: str, *, extra: str = "") -> Path:
    d = thread / "arcs" / name
    d.mkdir(parents=True)
    (d / "arc.md").write_text(
        "# {0}\n\ntitle: proj — board · {1}\ngoal: доска v1\nstatus: active\n"
        "claude-session: {2}\n{3}\n## context\n- 2026-10-09T20:56:42 — пульс\n".format(
            name, name.split("-")[0], sid, extra), encoding="utf-8")
    return d


@pytest.fixture
def world(tmp_control_home, tmp_path, monkeypatch):
    """control-home с ростером → проект proj → нить 29-@board: board · 05 (старая),
    board · 06 (ведущая, принята)."""
    home = tmp_control_home
    proj = tmp_path / "proj"
    build_tide_skeleton(proj, name="proj")
    (home / "roster.md").write_text("# tide roster\nproj | {0}\n".format(proj),
                                    encoding="utf-8")
    thread = proj / ".tide" / "arcs" / "29-@board"
    thread.mkdir(parents=True)
    (thread / "board-goal.md").write_text(
        "# board-thread\n\ngoal: доска tide — что происходит по проектам\nstatus: active\n"
        "kind: thread\n", encoding="utf-8")
    _session(thread, "05-board-5", SID_OLD, extra="handed-off: 2026-10-09T19:13:00 → 296-29-board")
    head = _session(thread, "06-board-6", SID_HEAD)
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.delenv("TIDE_ORPHAN_WATCH", raising=False)
    monkeypatch.delenv(orphan.REPEAT_ENV, raising=False)
    monkeypatch.setattr(holder, "ao_handle_for", lambda h, sid: AO.get(sid, ""))
    monkeypatch.setattr(hq, "list_offers", lambda h, status=None: [])
    # своя машина не в счёт: по умолчанию живых claude нет (ps прочёлся, пусто)
    monkeypatch.setattr(claude_proc, "snapshot", lambda **kw: [])
    return {"home": home, "proj": proj, "thread": thread, "head": head}


class _Box:
    def __init__(self):
        self.alerts, self.spawned = [], []

    def alert(self, text, source, thread):
        self.alerts.append((source, text))

    def spawn(self, v):
        self.spawned.append(v["ref"])
        return 4242


@pytest.fixture
def box():
    return _Box()


def _states(**by_ao):
    return lambda h: by_ao.get(h)


def _sweep(world, box, *, env=None, now=None, states=None):
    return orphan.sweep(world["home"], env=env or {}, now=now,
                        session_of=states or _states(**{"tide-stack-33": DEAD}),
                        alert=box.alert, spawn=box.spawn)


def _alert_lines(home: Path):
    p = home / ".tide" / "state" / "alerts.log"
    return [ln for ln in p.read_text(encoding="utf-8").splitlines()
            if orphan.MARK in ln] if p.is_file() else []


# --- сирота → менеджеру ---------------------------------------------------------------

def test_orphan_alarms_manager_and_logs(world, box):
    res = _sweep(world, box)
    hits = [v for v in res if v.get("orphan")]
    assert len(hits) == 1
    v = hits[0]
    assert v["ao"] == "tide-stack-33" and v["session"] == "06-board-6"
    assert len(box.alerts) == 1
    src, text = box.alerts[0]
    assert src == "proj/board/orphan"
    assert "нить proj/board осталась без ведущей" in text
    assert "tide-stack-33" in text and "оффера нет" in text
    assert "tide handoff continue --auto --session board/board-6" in text
    lines = _alert_lines(world["home"])
    assert len(lines) == 1 and "нить-сирота · proj/board · tide-stack-33" in lines[0]
    assert box.spawned == []  # автоподъём по умолчанию выключен


def test_manager_route_not_human(world, monkeypatch):
    """Путь тревоги — пульт route=manager (нет команды — ao send менеджеру); Грише ничего."""
    routes, told = [], []
    monkeypatch.setattr(autohandoff, "send_alert",
                        lambda text, route, source="", extra_env=None: routes.append(route) or 0)
    monkeypatch.setattr(autohandoff, "tell_manager", lambda text: told.append(text))
    orphan.sweep(world["home"], env={}, session_of=_states(**{"tide-stack-33": DEAD}),
                 spawn=lambda v: None)
    assert routes == ["manager"] and told == []


def test_no_alert_cmd_goes_to_manager_by_ao_send(world, monkeypatch):
    told = []
    monkeypatch.setattr(autohandoff, "tell_manager", lambda text: told.append(text))
    orphan.sweep(world["home"], env={}, session_of=_states(**{"tide-stack-33": DEAD}))
    assert len(told) == 1 and "без ведущей" in told[0]


# --- тишина -------------------------------------------------------------------------------

def test_open_offer_is_quiet(world, box, monkeypatch):
    monkeypatch.setattr(hq, "list_offers", lambda h, status=None: [{
        "name": "312-29-board", "status": "offered", "taken_at": "-",
        "project": "proj", "arc": "29-@board/07-board-7"}])
    res = _sweep(world, box)
    assert not any(v.get("orphan") for v in res)
    assert box.alerts == [] and _alert_lines(world["home"]) == []
    v = next(v for v in res if v.get("thread") == "board")
    assert "оффер 312-29-board висит" in v["why"]


def test_offer_of_other_thread_does_not_hide(world, box, monkeypatch):
    monkeypatch.setattr(hq, "list_offers", lambda h, status=None: [{
        "name": "313-31-main", "status": "offered", "taken_at": "-",
        "project": "proj", "arc": "31-@main/06-main-6"}])
    assert any(v.get("orphan") for v in _sweep(world, box))


def test_live_head_is_quiet(world, box):
    res = _sweep(world, box, states=_states(**{"tide-stack-33": LIVE}))
    assert not any(v.get("orphan") for v in res) and box.alerts == []


def test_ao_unknown_is_quiet(world, box):
    """AO не ответил (перезапуск 21:05) — не знаем, не врём."""
    res = _sweep(world, box, states=lambda h: None)
    assert not any(v.get("orphan") for v in res) and box.alerts == []


def test_exited_agent_in_live_ao_session_is_orphan(world, box):
    """AO-сессия есть, но агент вышел (activity exited) — тоже сирота."""
    st = {"isTerminated": False, "status": "working", "activity": {"state": "exited"}}
    assert any(v.get("orphan") for v in _sweep(world, box, states=lambda h: st))



# --- SOC-167: свежий транскрипт важнее ответа AO ------------------------------------------

def _transcript(sid: str, *, age_min: float) -> Path:
    """Транскрипт claude-сессии *sid* в изолированном CLAUDE_CONFIG_DIR, mtime age_min назад."""
    d = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "-tmp-proj"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "{0}.jsonl".format(sid)
    p.write_text('{"type":"user"}\n', encoding="utf-8")
    t = (datetime.now() - timedelta(minutes=age_min)).timestamp()
    os.utime(p, (t, t))
    return p


def _fresh_lines(home: Path):
    p = home / ".tide" / "state" / "alerts.log"
    return [ln for ln in p.read_text(encoding="utf-8").splitlines()
            if orphan.MARK_FRESH in ln] if p.is_file() else []


def test_ao_exited_but_fresh_transcript_is_not_orphan(world, box, monkeypatch):
    """Живой случай 10.10: AO после перезапуска 11:32 зовёт живую tide-stack-40 exited,
    а её транскрипт пишется — тишина менеджеру, без подъёма, строка в журнал."""
    monkeypatch.delenv(orphan.FRESH_ENV, raising=False)
    _transcript(SID_HEAD, age_min=2)
    res = _sweep(world, box, env={orphan.RAISE_ENV: "on"})
    v = next(v for v in res if v.get("session") == "06-board-6")
    assert not v["orphan"] and v["fresh"] == 2
    assert v["why"] == "AO говорит terminated, но транскрипт свеж 2 мин — не сирота"
    assert box.alerts == [] and box.spawned == []
    assert _alert_lines(world["home"]) == []          # тревожной строки нет
    lines = _fresh_lines(world["home"])
    assert len(lines) == 1 and "транскрипт свеж 2 мин — не сирота" in lines[0]
    _sweep(world, box)                                 # повтор не пишется
    assert len(_fresh_lines(world["home"])) == 1


def test_ao_exited_wording_and_old_transcript_alarms(world, box, monkeypatch):
    st = {"isTerminated": False, "status": "working", "activity": {"state": "exited"}}
    _transcript(SID_HEAD, age_min=3)
    v = next(v for v in _sweep(world, box, states=lambda h: st)
             if v.get("session") == "06-board-6")
    assert v["why"].startswith("AO говорит exited, но транскрипт свеж 3 мин")
    _transcript(SID_HEAD, age_min=40)                  # старше 15 мин — AO прав
    res = _sweep(world, box, now=datetime.now() + timedelta(minutes=61))
    assert any(v.get("orphan") for v in res) and len(box.alerts) == 1


def test_fresh_window_from_env(world, box, monkeypatch):
    _transcript(SID_HEAD, age_min=10)
    assert any(v.get("orphan") for v in _sweep(world, box, env={orphan.FRESH_ENV: "5"}))
    box.alerts.clear()
    res = orphan.sweep(world["home"], env={orphan.FRESH_ENV: "30", orphan.REPEAT_ENV: "1"},
                       now=datetime.now() + timedelta(minutes=2),
                       session_of=_states(**{"tide-stack-33": DEAD}), alert=box.alert,
                       spawn=box.spawn)
    assert not any(v.get("orphan") for v in res) and box.alerts == []


def test_run_raise_does_not_raise_over_fresh_transcript(world, box, monkeypatch):
    """Автоподъём перепроверяет вердикт — свежий транскрипт не даёт второй головы."""
    monkeypatch.delenv(orphan.FRESH_ENV, raising=False)
    _transcript(SID_HEAD, age_min=1)
    out, calls = _run_raise(world, box, monkeypatch)
    assert out["action"] == "skip" and calls == [] and box.alerts == []
    assert "транскрипт свеж" in out["why"]


# --- SOC-167: вторая проверка живости — процесс claude ведущей --------------------------

PS_LIVE = (
    "  101     1 /Applications/Agent Orchestrator.app/Contents/Resources/daemon/ao chat-host "
    "tide-stack-33 /Users/x/.ao/data /Users/x/.ao/data/worktrees/proj/tide-stack-33 acp ab\n"
    "  102   101 /Applications/Agent Orchestrator.app/acp-runtime/node/bin/node index.js\n"
    "  {pid}   102 /Users/x/.local/bin/claude --output-format stream-json --verbose "
    "--session-id={sid} --tools default\n"
    "  200     1 /bin/zsh -c grep -- '--session-id=" + SID_HEAD + "' x\n")


def _procs(text):
    return lambda: claude_proc.parse_ps(text)


def test_ao_exited_but_claude_process_alive_is_not_orphan(world, box, monkeypatch):
    """app-development-54 10.10: простаивает с 08:33, AO зовёт exited, транскрипт старый,
    а claude жив — тишина менеджеру, строка «не сирота: процесс жив (pid N)»."""
    _transcript(SID_HEAD, age_min=200)
    procs = _procs(PS_LIVE.format(pid=4711, sid=SID_HEAD))
    res = orphan.sweep(world["home"], env={orphan.RAISE_ENV: "on"},
                       session_of=_states(**{"tide-stack-33": DEAD}), procs=procs,
                       alert=box.alert, spawn=box.spawn)
    v = next(v for v in res if v.get("session") == "06-board-6")
    assert not v["orphan"] and v["alive_pid"] == 4711
    assert v["why"].startswith("не сирота: процесс жив (pid 4711) — AO говорит terminated")
    assert box.alerts == [] and box.spawned == [] and _alert_lines(world["home"]) == []
    lines = _fresh_lines(world["home"])
    assert len(lines) == 1 and "не сирота: процесс жив (pid 4711)" in lines[0]


def test_alive_by_ao_chat_host_when_uuid_differs(world, box):
    """claude поднят AO под другим uuid — находим по хозяину ``ao chat-host <ao-id>``."""
    _transcript(SID_HEAD, age_min=200)
    procs = _procs(PS_LIVE.format(pid=4712, sid="other-uuid"))
    res = orphan.scan(world["home"], session_of=_states(**{"tide-stack-33": DEAD}),
                      procs=procs)
    v = next(v for v in res if v.get("session") == "06-board-6")
    assert not v["orphan"] and v["alive_pid"] == 4712


def test_ao_exited_no_process_old_transcript_is_orphan(world, box):
    """Процесса нет (шелл с uuid в тексте grep — не claude) + старый транскрипт — сирота."""
    _transcript(SID_HEAD, age_min=200)
    text = "\n".join(ln for ln in PS_LIVE.format(pid=4711, sid=SID_HEAD).splitlines()
                     if "/claude " not in ln)
    res = orphan.sweep(world["home"], env={}, session_of=_states(**{"tide-stack-33": DEAD}),
                       procs=_procs(text), alert=box.alert, spawn=box.spawn)
    v = next(v for v in res if v.get("session") == "06-board-6")
    assert v["orphan"] and "alive_pid" not in v and len(box.alerts) == 1


def test_ps_error_is_not_orphan(world, box, monkeypatch):
    """ps не прочёлся — «неизвестно» — не сирота и тишина."""
    _transcript(SID_HEAD, age_min=200)
    monkeypatch.setattr(claude_proc, "snapshot", lambda **kw: None)
    res = _sweep(world, box)
    v = next(v for v in res if v.get("session") == "06-board-6")
    assert not v["orphan"] and "процессы не прочитать (ps)" in v["why"]
    assert box.alerts == []


def test_snapshot_error_paths():
    import subprocess as sp

    def boom(*a, **k):
        raise sp.TimeoutExpired("ps", 5)
    assert claude_proc.snapshot(run=boom) is None
    assert claude_proc.snapshot(run=lambda *a, **k: sp.CompletedProcess(a, 1, "", "")) is None
    assert claude_proc.snapshot(run=lambda *a, **k: sp.CompletedProcess(a, 0, "", "")) is None
    ok = claude_proc.snapshot(run=lambda *a, **k: sp.CompletedProcess(
        a, 0, " 7 1 /u/.local/bin/claude --session-id=s1\n", ""))
    assert ok == [claude_proc.Proc(7, 1, "/u/.local/bin/claude --session-id=s1")]


def test_claude_pid_matching():
    procs = claude_proc.parse_ps(
        " 5 1 /u/.local/bin/claude --chrome-native-host --session-id=s1\n"
        " 6 1 /u/.local/bin/claude --resume s2 --verbose\n"
        " 7 1 /u/.local/bin/claude --session-id=s3x\n")
    assert claude_proc.claude_pid(procs, session_id="s1") is None   # chrome-хост
    assert claude_proc.claude_pid(procs, session_id="s2") == 6      # --resume
    assert claude_proc.claude_pid(procs, session_id="s3") is None   # не префикс


def test_head_is_current_session_not_previous(world, box):
    """Случай nochnaya-smena/main 10.10: в нити прошлые сессии с claude-session; ведущая —
    новейшая принятая, транскрипт и процесс ищутся по ЕЁ claude-session."""
    _transcript(SID_OLD, age_min=1)                    # прошлая пишет — не в счёт
    _transcript(SID_HEAD, age_min=48)                  # ведущая простаивала 11:16→12:04
    procs = _procs(PS_LIVE.format(pid=99275, sid=SID_HEAD).replace("tide-stack-33", "x-1"))
    res = orphan.scan(world["home"], session_of=_states(**{"tide-stack-33": DEAD}),
                      procs=procs)
    v = next(v for v in res if v.get("thread") == "board")
    assert v["session"] == "06-board-6" and v["ao"] == "tide-stack-33"
    assert not v["orphan"] and v["alive_pid"] == 99275
    res = orphan.scan(world["home"], session_of=_states(**{"tide-stack-33": DEAD}),
                      procs=_procs(PS_LIVE.format(pid=1, sid=SID_OLD).replace(
                          "tide-stack-33", "x-1")))
    v = next(v for v in res if v.get("thread") == "board")
    assert v["orphan"]                                 # живой claude прошлой не спасает


def test_autoclose_with_accepted_new_is_quiet(world, box, monkeypatch):
    """Старая закрыта автозакрытием, новая приняла — ведущая новая, и она жива."""
    _session(world["thread"], "07-board-7", "sid-board-7")
    fields_path = world["head"] / "arc.md"
    fields_path.write_text(fields_path.read_text(encoding="utf-8").replace(
        "claude-session:", "handed-off: 2026-10-09T21:02:14 → 312-29-board\nclaude-session:"),
        encoding="utf-8")
    monkeypatch.setattr(holder, "ao_handle_for",
                        lambda h, sid: dict(AO, **{"sid-board-7": "tide-stack-38"}).get(sid, ""))
    res = _sweep(world, box, states=_states(**{"tide-stack-33": DEAD, "tide-stack-38": LIVE}))
    v = next(v for v in res if v.get("thread") == "board")
    assert v["session"] == "07-board-7" and not v["orphan"]
    assert box.alerts == []


def test_handed_off_head_is_quiet(world, box):
    pp = world["head"] / "arc.md"
    pp.write_text(pp.read_text(encoding="utf-8").replace(
        "claude-session:", "handed-off: 2026-10-09T21:00:00 → 400-29-board\nclaude-session:"),
        encoding="utf-8")
    assert not any(v.get("orphan") for v in _sweep(world, box))


def test_sleeping_thread_is_quiet(world, box):
    old = (datetime.now() - timedelta(hours=orphan.RECENT_HOURS + 1)).timestamp()
    os.utime(world["head"] / "arc.md", (old, old))
    res = _sweep(world, box)
    assert not any(v.get("orphan") for v in res)
    assert "спит" in next(v for v in res if v.get("thread") == "board")["why"]


def test_closed_thread_is_skipped(world, box):
    world["thread"].rename(world["thread"].with_name("__29-@board__"))
    res = _sweep(world, box)
    assert not any(v.get("thread") == "board" for v in res) and box.alerts == []


def test_inactive_thread_is_quiet(world, box):
    goal = world["thread"] / "board-goal.md"
    goal.write_text(goal.read_text(encoding="utf-8").replace("status: active", "status: done"),
                    encoding="utf-8")
    assert not any(v.get("orphan") for v in _sweep(world, box))


def test_archived_project_is_skipped(world, box):
    (world["home"] / "roster.md").write_text(
        "# tide roster\nproj | {0} | status=archived\n".format(world["proj"]), encoding="utf-8")
    assert _sweep(world, box) == [] and box.alerts == []


def test_paused_project_is_quiet(world, box):
    state = world["home"] / ".tide" / "state" / "throttle.json"
    state.write_text('{"v": 1, "paused": {"all": false, "projects": {"proj": true}}, '
                     '"until": null}', encoding="utf-8")
    res = _sweep(world, box)
    assert not any(v.get("orphan") for v in res) and box.alerts == []
    # пауза до сброса окна — сброс прошёл, пауза больше не держит
    state.write_text('{"v": 1, "paused": {"all": true, "projects": {}}, '
                     '"until": "2026-10-09T10:00:00-03:00"}', encoding="utf-8")
    assert any(v.get("orphan") for v in _sweep(world, box))


# --- повтор ---------------------------------------------------------------------------------

def test_repeat_not_more_often_than_n_minutes(world, box):
    t0 = datetime.now()
    _sweep(world, box, now=t0)
    _sweep(world, box, now=t0 + timedelta(minutes=10))
    _sweep(world, box, now=t0 + timedelta(minutes=orphan.REPEAT_MIN - 1))
    assert len(box.alerts) == 1
    _sweep(world, box, now=t0 + timedelta(minutes=orphan.REPEAT_MIN + 1))
    assert len(box.alerts) == 2
    assert len(_alert_lines(world["home"])) == 2


def test_repeat_window_from_env(world, box):
    t0 = datetime.now()
    env = {orphan.REPEAT_ENV: "5"}
    _sweep(world, box, now=t0, env=env)
    _sweep(world, box, now=t0 + timedelta(minutes=4), env=env)
    _sweep(world, box, now=t0 + timedelta(minutes=6), env=env)
    assert len(box.alerts) == 2


# --- автоподъём за выключателем ----------------------------------------------------------------

def test_autoraise_off_by_default(world, box):
    _sweep(world, box)
    assert box.spawned == []
    assert "автоподъём сироты выключен" in box.alerts[0][1]


@pytest.mark.parametrize("val", ["0", "off"])
def test_autoraise_env_off_wins_over_config(world, box, val):
    (world["proj"] / ".tide" / "canon" / "config").write_text(
        "lang=en\norphan-autoraise=on\n", encoding="utf-8")
    _sweep(world, box, env={orphan.RAISE_ENV: val})
    assert box.spawned == []


def test_autoraise_env_on(world, box):
    _sweep(world, box, env={orphan.RAISE_ENV: "1"})
    assert box.spawned == ["board/board-6"]
    assert "Поднимаю сам из паспорта" in box.alerts[0][1]


def test_autoraise_project_config_on(world, box):
    (world["proj"] / ".tide" / "canon" / "config").write_text(
        "lang=en\norphan-autoraise=on\n", encoding="utf-8")
    _sweep(world, box)
    assert box.spawned == ["board/board-6"]


def test_autoraise_not_repeated_within_window(world, box):
    t0 = datetime.now()
    env = {orphan.RAISE_ENV: "on"}
    _sweep(world, box, env=env, now=t0)
    _sweep(world, box, env=env, now=t0 + timedelta(minutes=3))
    assert box.spawned == ["board/board-6"]


class _Handoff:
    def __init__(self, ok, key="400-29-board"):
        self.handed_over = ok
        self.offer_path = Path("/x/.tide/handoffs/{0}.md".format(key))


def _run_raise(world, box, monkeypatch, *, spawn=True, ok=True):
    calls = []
    monkeypatch.setattr(autohandoff, "decide_for", lambda e: autohandoff.Decision(
        spawn=spawn, detail="" if spawn else "в нити висит вопрос к тебе: X"))

    def fake_run_auto(root, *, session_ref=None, **kw):
        calls.append(session_ref)
        return type("R", (), {"handoff": _Handoff(ok)})()

    monkeypatch.setattr(autohandoff, "run_auto", fake_run_auto)
    out = orphan.run_raise(world["home"], world["proj"], "proj", "board/board-6",
                           session_of=_states(**{"tide-stack-33": DEAD}), alert=box.alert)
    return out, calls


def test_run_raise_raises_from_passport(world, box, monkeypatch):
    out, calls = _run_raise(world, box, monkeypatch)
    assert out["action"] == "raised" and calls == ["board/board-6"]
    assert "поднял новую сессию сам из паспорта, оффер 400-29-board" in box.alerts[0][1]
    assert any("автоподъём: raised" in ln for ln in _alert_lines(world["home"]))


def test_run_raise_stops_on_question(world, box, monkeypatch):
    out, calls = _run_raise(world, box, monkeypatch, spawn=False)
    assert out["action"] == "stopped" and calls == []
    assert "сам не поднимаю: в нити висит вопрос" in box.alerts[0][1]


def test_run_raise_failed_spawn_tells_pickup(world, box, monkeypatch):
    out, _ = _run_raise(world, box, monkeypatch, ok=False)
    assert out["action"] == "failed"
    assert "tide pickup 400-29-board" in box.alerts[0][1]


def test_run_raise_skips_when_head_came_back(world, box, monkeypatch):
    calls = []
    monkeypatch.setattr(autohandoff, "run_auto", lambda *a, **k: calls.append(1))
    out = orphan.run_raise(world["home"], world["proj"], "proj", "board/board-6",
                           session_of=_states(**{"tide-stack-33": LIVE}), alert=box.alert)
    assert out["action"] == "skip" and calls == [] and box.alerts == []


# --- хук -------------------------------------------------------------------------------------

def test_on_prompt_off_switch_and_throttle(world, monkeypatch):
    seen = []
    monkeypatch.setattr(orphan, "sweep", lambda home, env=None: seen.append(home) or [])
    assert orphan.on_prompt(env={orphan.WATCH_ENV: "off"}) == []
    assert seen == []
    orphan.on_prompt(env={})
    orphan.on_prompt(env={})  # второй раз — раньше THROTTLE_SEC
    assert len(seen) == 1


def test_handoff_confirm_hook_runs_orphan_watch(world, monkeypatch, capsys):
    import io
    import json

    from tide.hooks import handed_off

    seen = []
    monkeypatch.setattr(orphan, "on_prompt", lambda **kw: seen.append(1) or [])
    monkeypatch.chdir(world["proj"])
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"session_id": "sid-x", "prompt": "привет"})))
    handed_off.cmd_prompt(type("A", (), {})())
    assert seen == [1]

"""SOC-247: сторож «агент молчит» — `tide watch-stuck`, один проход по живым AO-сессиям.

Классификация по синтетическим транскриптам (висящий tool_use, разрешение, ошибка API,
Stop + метка AO active, курсор «жду» > 4 ч, фоновые помощники), «один толчок — одно
письмо», исключение менеджера, state-файл для доски. AO, ``ao send`` и почта —
подделки: ничего наружу.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tide import watch_stuck as ws

from tests.conftest import build_tide_skeleton

T0 = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)
SID = "11111111-2222-3333-4444-555555555555"
LIMITS = ws.Limits()  # дефолты: 20 мин / 10 мин / 20 мин / 4 ч / 4 ч / 3 ч


def _ts(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user_text(at, text="сделай"):
    return {"type": "user", "timestamp": _ts(at), "message": {"role": "user", "content": text}}


def tool_use(at, tid="toolu_1", name="Bash", inp=None):
    return {"type": "assistant", "timestamp": _ts(at),
            "message": {"role": "assistant", "stop_reason": "tool_use", "content": [
                {"type": "tool_use", "id": tid, "name": name,
                 "input": inp if inp is not None else {"command": "sleep 3000"}}]}}


def tool_result(at, tid="toolu_1", text="ok"):
    return {"type": "user", "timestamp": _ts(at), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text}]}}


def end_turn(at, text="готово"):
    return {"type": "assistant", "timestamp": _ts(at),
            "message": {"role": "assistant", "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": text}]}}


def stop_summary(at, errors=()):
    return {"type": "system", "subtype": "stop_hook_summary", "timestamp": _ts(at),
            "hookErrors": list(errors), "preventedContinuation": False}


def api_error(at, text="API Error: 529 overloaded"):
    return {"type": "assistant", "timestamp": _ts(at), "isApiErrorMessage": True,
            "message": {"role": "assistant", "stop_reason": "stop_sequence",
                        "content": [{"type": "text", "text": text}]}}


def turn_duration(at):
    return {"type": "system", "subtype": "turn_duration", "timestamp": _ts(at)}


def meta(kind="last-prompt"):
    return {"type": kind}


def ended_rows(at):
    return [user_text(at - timedelta(minutes=2)), end_turn(at), stop_summary(at)]


# --- классификация -------------------------------------------------------------------

def test_hanging_tool_use_is_in_turn_with_tool_reason():
    rows = [user_text(T0), tool_use(T0 + timedelta(seconds=5)), meta(), meta("ai-title")]
    v = ws.classify(rows, now=T0 + timedelta(minutes=25), activity="active", limits=LIMITS)
    assert v["kind"] == "in_turn"
    assert v["reason"] == "tool"
    assert "Bash" in v["reason_text"] and "sleep 3000" in v["reason_text"]
    assert v["quiet_sec"] == pytest.approx(25 * 60 - 5)
    assert v["last_tool"].startswith("Bash")


def test_permission_reason_from_blocked_perms():
    at = T0 + timedelta(seconds=5)
    rows = [user_text(T0), tool_use(at, inp={"command": "rm -rf build"})]
    perms = [{"ts": _ts(at + timedelta(seconds=1)), "ao_session": "proj-7",
              "claude_session": SID, "tool": "Bash", "input": "rm -rf build"}]
    v = ws.classify(rows, now=T0 + timedelta(minutes=30), perms=perms, limits=LIMITS)
    assert v["kind"] == "in_turn"
    assert v["reason"] == "permission"
    assert "разрешени" in v["reason_text"]


def test_old_permission_row_does_not_count():
    at = T0 + timedelta(seconds=5)
    rows = [user_text(T0), tool_use(at)]
    perms = [{"ts": _ts(T0 - timedelta(hours=2)), "ao_session": "proj-7", "tool": "Bash"}]
    v = ws.classify(rows, now=T0 + timedelta(minutes=30), perms=perms, limits=LIMITS)
    assert v["reason"] == "tool"


def test_api_error_is_stuck_reason():
    rows = [user_text(T0), api_error(T0 + timedelta(seconds=3)),
            turn_duration(T0 + timedelta(seconds=3))]
    v = ws.classify(rows, now=T0 + timedelta(minutes=40), limits=LIMITS)
    assert v["kind"] == "in_turn"
    assert v["reason"] == "api_error"
    assert "529" in v["reason_text"]


def test_ask_user_question_is_waiting_input():
    rows = [user_text(T0), tool_use(T0, name="AskUserQuestion", inp={"questions": []})]
    v = ws.classify(rows, now=T0 + timedelta(minutes=30), limits=LIMITS)
    assert v["reason"] == "input"


def test_model_silent_after_tool_result():
    rows = [user_text(T0), tool_use(T0), tool_result(T0 + timedelta(seconds=2))]
    v = ws.classify(rows, now=T0 + timedelta(minutes=30), limits=LIMITS)
    assert v["kind"] == "in_turn"
    assert v["reason"] == "no_reply"


def test_stop_hook_blocking_keeps_turn_open():
    rows = [user_text(T0), end_turn(T0), stop_summary(T0, errors=["tide: передай нить"])]
    v = ws.classify(rows, now=T0 + timedelta(minutes=30), limits=LIMITS)
    assert v["kind"] == "in_turn"


def test_stop_plus_ao_active_is_idle():
    rows = ended_rows(T0) + [meta(), meta("queue-operation")]
    v = ws.classify(rows, now=T0 + timedelta(minutes=30), activity="active", limits=LIMITS)
    assert v["kind"] == "ended"
    assert v["state"] == "idle"


def test_stop_plus_ao_idle_is_ok():
    v = ws.classify(ended_rows(T0), now=T0 + timedelta(minutes=30), activity="idle",
                    limits=LIMITS)
    assert v["state"] == "ok"


def test_short_quiet_in_turn_is_ok():
    rows = [user_text(T0), tool_use(T0)]
    v = ws.classify(rows, now=T0 + timedelta(minutes=10), limits=LIMITS)
    assert v["kind"] == "in_turn"
    assert v["state"] == "ok"


def test_cursor_wait_over_4h_is_waiting():
    v = ws.classify(ended_rows(T0), now=T0 + timedelta(hours=5), activity="active",
                    cursor="жду ответ пользователя по SOC-201", limits=LIMITS)
    assert v["state"] == "waiting"
    assert v["wait_h"] == 5


def test_user_ball_cursor_counts_as_wait():
    v = ws.classify(ended_rows(T0), now=T0 + timedelta(hours=4, minutes=30),
                    activity="idle", cursor="мяч у пользователя: вопрос TG 4600",
                    limits=LIMITS)
    assert v["state"] == "waiting"


def test_cursor_wait_under_4h_with_active_label_is_idle():
    v = ws.classify(ended_rows(T0), now=T0 + timedelta(hours=2), activity="active",
                    cursor="жду ответ пользователя", limits=LIMITS)
    assert v["state"] == "idle"


def test_letter_resets_wait_clock():
    rows = ended_rows(T0) + [{"type": "queue-operation", "operation": "enqueue",
                              "timestamp": _ts(T0 + timedelta(hours=3))}]
    v = ws.classify(rows, now=T0 + timedelta(hours=5), activity="idle",
                    cursor="жду ответ", limits=LIMITS)
    assert v["state"] == "ok"


def test_background_workers_are_not_idle_or_stuck():
    v = ws.classify(ended_rows(T0), now=T0 + timedelta(minutes=50), activity="active",
                    workers=[("a1", "проверка", T0, "")], limits=LIMITS)
    assert v["state"] == "workers"
    rows = [user_text(T0), tool_use(T0)]
    v = ws.classify(rows, now=T0 + timedelta(minutes=50), workers=[("a1", "x", T0, "")],
                    limits=LIMITS)
    assert v["state"] == "workers"


def test_workers_older_than_cap_do_not_excuse():
    rows = [user_text(T0), tool_use(T0)]
    v = ws.classify(rows, now=T0 + timedelta(hours=4), workers=[("a1", "x", T0, "")],
                    limits=LIMITS)
    assert v["state"] == "quiet"


def test_live_workers_read_from_transcript(tmp_path):
    p = tmp_path / "t.jsonl"
    rows = [user_text(T0),
            tool_use(T0, tid="toolu_a", name="Agent",
                     inp={"description": "проверка", "run_in_background": True}),
            tool_result(T0, tid="toolu_a",
                        text="Async agent launched successfully. agentId: a1b2"),
            end_turn(T0), stop_summary(T0)]
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                 encoding="utf-8")
    live = ws.live_workers(p)
    assert [w[0] for w in live] == ["a1b2"]


def test_limits_from_env():
    lim = ws.Limits.from_env({"TIDE_WATCH_NUDGE_SEC": "3", "TIDE_WATCH_MAIL_SEC": "2",
                              "TIDE_WATCH_WAIT_SEC": "7"})
    assert (lim.nudge, lim.mail, lim.wait) == (3, 2, 7)
    assert lim.idle == 20 * 60


# --- проход: толчок, письмо, состояние ---------------------------------------------

class Fakes:
    def __init__(self):
        self.said, self.mailed = [], []

    def say(self, handle, text):
        self.said.append((handle, text))
        return True

    def mail(self, text):
        self.mailed.append(text)
        return True


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Проект с нитью и паспортом сессии, транскрипт в CLAUDE_CONFIG_DIR, state — в tmp."""
    proj = tmp_path / "proj"
    build_tide_skeleton(proj, name="proj")
    entry = proj / ".tide" / "arcs" / "05-@designer" / "arcs" / "23-designer-23"
    entry.mkdir(parents=True)
    (entry / "arc.md").write_text(
        "# 23-designer-23\n\ntitle: proj — designer · 23\nstatus: active\n"
        "claude-session: {0}\n\n## cursor — resume here\nделаю макет\n\n## context\n- п\n"
        .format(SID), encoding="utf-8")
    cfg = tmp_path / "claude"
    tdir = cfg / "projects" / "-tmp-proj"
    tdir.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    state = tmp_path / "state"
    ws._save(state, {}, T0 - timedelta(days=1))  # state уже есть: не первый запуск (п.7)
    for k in list(ws.ENV_KEYS):
        monkeypatch.delenv(k, raising=False)
    return {"proj": proj, "entry": entry, "transcript": tdir / "{0}.jsonl".format(SID),
            "state": state}


def write_rows(path: Path, rows, cwd: Path):
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            r = dict(r)
            r.setdefault("cwd", str(cwd))
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def session(sid="proj-7", activity="active", sessid=SID):
    return {"id": sid, "projectId": "proj", "activity": activity, "status": "working",
            "isTerminated": False, "agentSessionId": sessid, "role": "worker"}


def run(world, fakes, now, sessions):
    return ws.run_once(sessions=sessions, now=now, say=fakes.say, mail=fakes.mail,
                       state_dir=world["state"])


def test_one_nudge_then_one_mail(world):
    f = Fakes()
    write_rows(world["transcript"], [user_text(T0), tool_use(T0)], world["proj"])
    s = [session()]
    run(world, f, T0 + timedelta(minutes=5), s)
    assert f.said == [] and f.mailed == []
    run(world, f, T0 + timedelta(minutes=21), s)
    assert len(f.said) == 1 and f.said[0][0] == "proj-7"
    assert "сторож: ты молчишь 21 мин в ходе" in f.said[0][1]
    assert "tide offload" in f.said[0][1]
    run(world, f, T0 + timedelta(minutes=25), s)
    assert len(f.said) == 1 and f.mailed == []
    recs = run(world, f, T0 + timedelta(minutes=32), s)
    assert len(f.mailed) == 1
    letter = f.mailed[0]
    for frag in ("proj-7", "designer", "sleep 3000", str(world["transcript"])):
        assert frag in letter
    assert recs[0]["state"] == "stuck"
    for m in (40, 90, 300):
        run(world, f, T0 + timedelta(minutes=m), s)
    assert len(f.said) == 1 and len(f.mailed) == 1
    snap = json.loads((world["state"] / ws.STATE_FILE).read_text(encoding="utf-8"))
    rec = snap["sessions"]["proj-7"]
    assert rec["state"] == "stuck" and rec["reason"] == "tool"
    assert rec["thread"] == "designer" and rec["project"] == "proj"
    assert rec["transcript"] == str(world["transcript"])
    events = [json.loads(x) for x in
              (world["state"] / ws.EVENTS_FILE).read_text(encoding="utf-8").splitlines()]
    assert [e["ev"] for e in events] == ["nudge", "stuck"]


def test_new_activity_starts_new_episode(world):
    f = Fakes()
    rows = [user_text(T0), tool_use(T0)]
    write_rows(world["transcript"], rows, world["proj"])
    s = [session()]
    run(world, f, T0 + timedelta(minutes=21), s)
    run(world, f, T0 + timedelta(minutes=32), s)
    assert (len(f.said), len(f.mailed)) == (1, 1)
    t1 = T0 + timedelta(minutes=40)
    write_rows(world["transcript"], rows + [tool_result(t1), tool_use(t1, tid="toolu_2")],
               world["proj"])
    recs = run(world, f, t1 + timedelta(minutes=5), s)
    assert recs[0]["state"] == "ok"
    run(world, f, t1 + timedelta(minutes=21), s)
    assert len(f.said) == 2


def test_manager_is_never_nudged_but_mailed(world, monkeypatch):
    from tide import manager_guard
    monkeypatch.setattr(manager_guard, "_manager_ao", lambda: "")
    f = Fakes()
    write_rows(world["transcript"], [user_text(T0), tool_use(T0)], world["proj"])
    s = [session(sid="management-39")]
    run(world, f, T0 + timedelta(minutes=21), s)
    assert f.said == []
    run(world, f, T0 + timedelta(minutes=32), s)
    assert f.said == [] and len(f.mailed) == 1


def test_idle_label_recorded_without_nudge(world):
    f = Fakes()
    write_rows(world["transcript"], ended_rows(T0), world["proj"])
    recs = run(world, f, T0 + timedelta(minutes=45), [session()])
    assert recs[0]["state"] == "idle"
    assert f.said == [] and f.mailed == []


def test_waiting_mail_once_per_4h(world):
    passport = world["entry"] / "arc.md"
    passport.write_text(passport.read_text(encoding="utf-8").replace(
        "делаю макет", "жду ответ пользователя по SOC-201"), encoding="utf-8")
    f = Fakes()
    write_rows(world["transcript"], ended_rows(T0), world["proj"])
    s = [session()]
    recs = run(world, f, T0 + timedelta(hours=4, minutes=1), s)
    assert recs[0]["state"] == "waiting"
    assert len(f.mailed) == 1 and "ждёт 4 ч" in f.mailed[0]
    assert "жду ответ пользователя по SOC-201" in f.mailed[0]
    run(world, f, T0 + timedelta(hours=6), s)
    assert len(f.mailed) == 1
    run(world, f, T0 + timedelta(hours=8, minutes=2), s)
    assert len(f.mailed) == 2
    assert f.said == []


def test_dry_run_sends_nothing_and_writes_nothing(world):
    f = Fakes()
    write_rows(world["transcript"], [user_text(T0), tool_use(T0)], world["proj"])
    before = (world["state"] / ws.STATE_FILE).read_text(encoding="utf-8")
    recs = ws.run_once(sessions=[session()], now=T0 + timedelta(minutes=40), say=f.say,
                       mail=f.mail, state_dir=world["state"], dry_run=True)
    assert recs[0]["actions"] == ["nudge"]
    assert f.said == [] and f.mailed == []
    assert (world["state"] / ws.STATE_FILE).read_text(encoding="utf-8") == before


def test_terminated_sessions_dropped(world):
    f = Fakes()
    write_rows(world["transcript"], ended_rows(T0), world["proj"])
    run(world, f, T0 + timedelta(minutes=45), [session()])
    dead = dict(session(), isTerminated=True)
    run(world, f, T0 + timedelta(minutes=46), [dead])
    snap = json.loads((world["state"] / ws.STATE_FILE).read_text(encoding="utf-8"))
    assert snap["sessions"] == {}


def test_off_switch(world, monkeypatch):
    monkeypatch.setenv("TIDE_WATCH_STUCK", "off")
    f = Fakes()
    write_rows(world["transcript"], [user_text(T0), tool_use(T0)], world["proj"])
    assert run(world, f, T0 + timedelta(minutes=40), [session()]) == []
    assert f.said == []


def test_plist_is_minutely_and_not_loaded(tmp_path):
    xml = ws.plist_text(tmp_path, argv=["/opt/homebrew/bin/tide"])
    assert "<string>com.tide.watch-stuck</string>" in xml
    assert "<integer>60</integer>" in xml
    assert "<string>watch-stuck</string>" in xml


# --- SOC-247 правки по ревью -------------------------------------------------------

def _perm_row(world, at):
    d = ws.paths.state_dir(world["proj"])
    d.mkdir(parents=True, exist_ok=True)
    (d / ws.PERMS_FILE).write_text(json.dumps(
        {"ts": _ts(at), "ao_session": "proj-7", "claude_session": SID, "tool": "Bash",
         "input": "rm -rf build"}) + "\n", encoding="utf-8")


def test_p1_no_nudge_into_permission_dialog_mail_at_once(world):
    f = Fakes()
    at = T0 + timedelta(seconds=5)
    write_rows(world["transcript"], [user_text(T0), tool_use(at, inp={"command": "rm -rf build"})],
               world["proj"])
    _perm_row(world, at + timedelta(seconds=1))
    recs = run(world, f, T0 + timedelta(minutes=21), [session()])
    assert recs[0]["reason"] == "permission"
    assert f.said == []
    assert len(f.mailed) == 1 and "не слали" in f.mailed[0]
    run(world, f, T0 + timedelta(minutes=40), [session()])
    assert f.said == [] and len(f.mailed) == 1


def test_p1_no_nudge_on_ask_user_question(world):
    f = Fakes()
    write_rows(world["transcript"], [user_text(T0), tool_use(
        T0, name="AskUserQuestion", inp={"questions": []})], world["proj"])
    run(world, f, T0 + timedelta(minutes=21), [session()])
    assert f.said == [] and len(f.mailed) == 1


@pytest.mark.parametrize("act", ["waiting_input", "blocked"])
def test_p1_no_nudge_when_ao_waiting_or_blocked(world, act):
    f = Fakes()
    write_rows(world["transcript"], [user_text(T0), tool_use(T0)], world["proj"])
    run(world, f, T0 + timedelta(minutes=21), [session(activity=act)])
    assert f.said == [] and len(f.mailed) == 1


def test_p2_api_error_nudged_once_and_mailed_despite_error_after_nudge(world):
    f = Fakes()
    rows = [user_text(T0), api_error(T0 + timedelta(seconds=3)),
            turn_duration(T0 + timedelta(seconds=3))]
    write_rows(world["transcript"], rows, world["proj"])
    s = [session(activity="idle")]
    said_before = 0
    for m in range(1, 181):
        now = T0 + timedelta(minutes=m)
        run(world, f, now, s)
        if len(f.said) > said_before:  # толчок будит сессию — она снова получает ошибку API
            said_before = len(f.said)
            rows = rows + [user_text(now), api_error(now + timedelta(seconds=2)),
                           turn_duration(now + timedelta(seconds=2))]
            write_rows(world["transcript"], rows, world["proj"])
    assert len(f.said) == 1
    assert len(f.mailed) == 1 and "ошибка API" in f.mailed[0]
    snap = json.loads((world["state"] / ws.STATE_FILE).read_text(encoding="utf-8"))
    assert snap["sessions"]["proj-7"]["state"] == "stuck"


def test_p3_interrupted_by_user_ends_turn():
    intr = {"type": "user", "timestamp": _ts(T0 + timedelta(seconds=5)), "message": {
        "role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}}
    rows = [user_text(T0), tool_use(T0 + timedelta(seconds=2)), intr]
    v = ws.classify(rows, now=T0 + timedelta(minutes=45), activity="idle", limits=LIMITS)
    assert v["kind"] == "ended"
    assert v["state"] == "ok"


def test_p3_interrupted_for_tool_use_in_tool_result_and_plain_string():
    intr_tool = tool_result(T0 + timedelta(seconds=5), text="[Request interrupted by user for tool use]")
    v = ws.classify([user_text(T0), tool_use(T0), intr_tool],
                    now=T0 + timedelta(minutes=45), limits=LIMITS)
    assert v["kind"] == "ended"
    plain = user_text(T0 + timedelta(seconds=5), "[Request interrupted by user]")
    v = ws.classify([user_text(T0), end_turn(T0), plain],
                    now=T0 + timedelta(minutes=45), limits=LIMITS)
    assert v["kind"] == "ended"


def test_p3_interrupted_session_is_not_nudged(world):
    f = Fakes()
    intr = user_text(T0 + timedelta(seconds=5), "[Request interrupted by user]")
    write_rows(world["transcript"], [user_text(T0), tool_use(T0), intr], world["proj"])
    for m in (21, 32, 60):
        run(world, f, T0 + timedelta(minutes=m), [session(activity="idle")])
    assert f.said == [] and f.mailed == []


@pytest.fixture
def set_tz(monkeypatch):
    import time

    def _set(name):
        monkeypatch.setenv("TZ", name)
        time.tzset()

    yield _set
    monkeypatch.undo()
    time.tzset()


def _bg_worker_transcript(path, at):
    rows = [user_text(at),
            tool_use(at, tid="toolu_a", name="Agent",
                     inp={"description": "проверка", "run_in_background": True}),
            tool_result(at, tid="toolu_a", text="Async agent launched successfully. agentId: a1b2"),
            end_turn(at), stop_summary(at)]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


@pytest.mark.parametrize("tz", ["America/Sao_Paulo", "Europe/Moscow", "UTC"])
def test_p4_fresh_worker_not_expired_in_any_timezone(tmp_path, set_tz, tz):
    set_tz(tz)
    p = tmp_path / "t.jsonl"
    _bg_worker_transcript(p, T0)
    live = ws.live_workers(p)
    assert [w[0] for w in live] == ["a1b2"]
    now = T0 + timedelta(minutes=5)
    assert ws._worker_expired(live[0], now, LIMITS) is False
    v = ws.classify(ended_rows(T0), now=now, activity="active", workers=live, limits=LIMITS)
    assert v["state"] == "workers"


@pytest.mark.parametrize("tz", ["America/Sao_Paulo", "Europe/Moscow", "UTC"])
def test_p4_old_worker_expired_in_any_timezone(tmp_path, set_tz, tz):
    set_tz(tz)
    p = tmp_path / "t.jsonl"
    _bg_worker_transcript(p, T0)
    live = ws.live_workers(p)
    assert ws._worker_expired(live[0], T0 + timedelta(hours=3, minutes=5), LIMITS) is True
    assert ws._worker_expired(live[0], T0 + timedelta(hours=2, minutes=55), LIMITS) is False


def _boom_locator(bad):
    def loc(s):
        if s["id"] == bad:
            raise FileNotFoundError("гонка: jsonl удалён между glob и stat")
        return ws.locate(s)
    return loc


def test_p5_exception_on_second_session_does_not_repeat_first(world):
    f = Fakes()
    write_rows(world["transcript"], [user_text(T0), tool_use(T0)], world["proj"])
    s = [session(), session(sid="proj-8", sessid="other")]
    loc = _boom_locator("proj-8")
    for m in (21, 22, 23, 33, 34):
        ws.run_once(sessions=s, now=T0 + timedelta(minutes=m), say=f.say, mail=f.mail,
                    state_dir=world["state"], locator=loc)
    assert len(f.said) == 1
    assert len(f.mailed) == 1


def test_p5_event_write_failure_still_saves_state(world, monkeypatch):
    f = Fakes()
    write_rows(world["transcript"], [user_text(T0), tool_use(T0)], world["proj"])

    def bad_event(sdir, ev):
        raise OSError("disk full")

    monkeypatch.setattr(ws, "_event", bad_event)
    s = [session()]
    for m in (21, 22, 23):
        run(world, f, T0 + timedelta(minutes=m), s)
    assert len(f.said) == 1


def test_p6_first_run_without_state_seeds_silently(world):
    import shutil
    shutil.rmtree(world["state"])  # первого state нет: свежая установка / потерян файл
    f = Fakes()
    write_rows(world["transcript"], [user_text(T0), tool_use(T0)], world["proj"])
    s = [session()]
    recs = run(world, f, T0 + timedelta(minutes=40), s)
    assert f.said == [] and f.mailed == []
    assert recs[0]["actions"] == [] and recs[0]["state"] == "stuck"
    run(world, f, T0 + timedelta(minutes=70), s)
    assert f.said == [] and f.mailed == []  # засеянный эпизод молчит
    t1 = T0 + timedelta(minutes=80)
    write_rows(world["transcript"], [user_text(T0), tool_use(T0), tool_result(t1),
                                     tool_use(t1, tid="toolu_2")], world["proj"])
    run(world, f, t1 + timedelta(minutes=21), s)
    assert len(f.said) == 1  # новый эпизод после засева — работает как обычно


def test_p6_first_run_seed_covers_waiting_mail(world):
    import shutil
    shutil.rmtree(world["state"])
    passport = world["entry"] / "arc.md"
    passport.write_text(passport.read_text(encoding="utf-8").replace(
        "делаю макет", "жду ответ пользователя"), encoding="utf-8")
    f = Fakes()
    write_rows(world["transcript"], ended_rows(T0), world["proj"])
    run(world, f, T0 + timedelta(hours=5), [session()])
    assert f.mailed == []


def test_p6_corrupt_state_file_reseeds(world):
    (world["state"] / ws.STATE_FILE).write_text("{oops", encoding="utf-8")
    f = Fakes()
    write_rows(world["transcript"], [user_text(T0), tool_use(T0)], world["proj"])
    run(world, f, T0 + timedelta(minutes=40), [session()])
    assert f.said == [] and f.mailed == []


def test_p8_off_flag_file_pauses_without_restart(world):
    f = Fakes()
    write_rows(world["transcript"], [user_text(T0), tool_use(T0)], world["proj"])
    flag = world["state"] / ws.OFF_FILE
    flag.write_text("", encoding="utf-8")
    assert run(world, f, T0 + timedelta(minutes=40), [session()]) == []
    assert f.said == [] and f.mailed == []
    flag.unlink()
    run(world, f, T0 + timedelta(minutes=41), [session()])
    assert len(f.said) == 1


@pytest.mark.parametrize("val", ["off", "0", "false", "OFF", "no"])
def test_p8_off_env_values(world, monkeypatch, val):
    monkeypatch.setenv("TIDE_WATCH_STUCK", val)
    f = Fakes()
    write_rows(world["transcript"], [user_text(T0), tool_use(T0)], world["proj"])
    assert run(world, f, T0 + timedelta(minutes=40), [session()]) == []


def test_p8_plist_template_mentions_flag_file_not_env():
    text = (Path(__file__).resolve().parent.parent / "packaging" / "launchd"
            / "com.tide.watch-stuck.plist").read_text(encoding="utf-8")
    assert "watch-stuck.off" in text
    assert "TIDE_WATCH_STUCK=off в EnvironmentVariables" not in text

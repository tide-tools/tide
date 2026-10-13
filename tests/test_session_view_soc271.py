"""SOC-271: `tide idle`, `tide why`, `tide gc sessions` — на подменённой выдаче AO."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tide import ball, cli, fields, offload, registry, roster, session_view as sv
from tide.arc import stream
from tide.hooks import autoclose, role_gate

NOW = datetime(2026, 10, 13, 1, 20, tzinfo=timezone.utc)


def _iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ao(handle, project="management", activity="idle", ago=30):
    return {"id": handle, "projectId": project, "activity": activity, "status": activity,
            "isTerminated": False, "lastActivityAt": _iso(ago), "createdAt": _iso(300)}


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
    stream.new_thread(mgmt, "signing", goal="подпись")
    made = {}
    for thread, slug_, sid, ao, nn in [("main", "main-1", "sid-old", "management-9", "01"),
                                       ("main", "main-2", "sid-new", "management-14", "02"),
                                       ("signing", "signing", "sid-sig", "management-20", "01")]:
        s = stream.new_session(mgmt, thread, slug_, claude_session=sid)
        fields.set_field(s / "arc.md", "title",
                         "management — {0} · {1} · {2}".format(thread, nn, ao))
        registry.record(home, sid, ao, thread)
        made[ao] = s
    offload.offload(mgmt, "main-1", note="передаю", cursor="передаю нить main свежей сессии")
    fields.set_field(made["management-9"] / "arc.md", "handed-off",
                     "2026-10-12T21:42:05 → 439-04-main")
    offload.offload(mgmt, "main-2", note="принял", cursor="мяч у меня: SOC-189, жду карту к 22:30",
                    next_steps="22:30 сверка")
    offload.offload(mgmt, "signing", note="раз", cursor="мяч у пользователя: вопрос TG 4586")
    for i in range(3):
        offload.offload(mgmt, "signing", note="шаг {0}".format(i))
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.delenv("AO_SESSION", raising=False)
    monkeypatch.chdir(tmp_path)
    return {"home": home, "mgmt": mgmt, "s": made}


def _no_loc(s):
    return {}  # паспорт — по хвосту title


def _rows():
    return [_ao("management-9", ago=34), _ao("management-14", ago=2),
            _ao("management-20", activity="active", ago=5)]


def _no_ctx(ao, tr):
    return {"tokens": 150_000, "window": 1_000_000, "hard": 200_000, "left": 50_000}


# --- ball.side ---------------------------------------------------------------------

@pytest.mark.parametrize("cursor,kind,label", [
    ("мяч у меня: SOC-1, жду X к 22:00", ball.MINE, "у меня"),
    ("мяч у пользователя: вопрос TG 4586", ball.USER, "у пользователя (TG 4586)"),
    ("отдых по слову пользователя", ball.REST, "отдых"),
    ("мяч у менеджера: ок на чистку", ball.OTHER, "у менеджера"),
    ("", ball.NONE, "—"),
])
def test_ball_side(cursor, kind, label):
    assert ball.side(cursor) == (kind, label)


def test_ball_side_flags_violation():
    kind, label = ball.side("жду ответа")
    assert kind == ball.NONE and "⚠" in label


# --- idle --------------------------------------------------------------------------

def test_idle_table_sorted_longest_idle_first(world):
    rows = sv.idle(sessions=_rows(), locator=_no_loc, now=NOW, context=_no_ctx)
    assert [r["ao_session"] for r in rows] == ["management-9", "management-20", "management-14"]
    old = rows[0]
    assert old["label"] == "management — main · 01"
    assert old["passport"].endswith("main-1/arc.md")
    assert old["cursor"] == "передаю нить main свежей сессии"
    assert int(old["idle_sec"] // 60) == 34
    assert rows[1]["ball_label"] == "у пользователя (TG 4586)"
    text = sv.render_idle(rows)
    assert "management-9" in text.splitlines()[1] and "34м" in text
    assert "150k (ост 50k)" in text


def test_idle_project_filter(world):
    rows = sv.idle(project="music", sessions=_rows() + [_ao("music-3", project="music")],
                   locator=_no_loc, now=NOW, with_context=False)
    assert [r["ao_session"] for r in rows] == ["music-3"]
    assert rows[0]["passport"] == ""


def test_idle_cli_json(world, monkeypatch, capsys):
    monkeypatch.setattr(sv, "ao_rows", _rows)
    from tide import watch_stuck as ws
    monkeypatch.setattr(ws, "locate", _no_loc)
    monkeypatch.setattr(sv, "context_of", lambda ao, tr: None)
    assert cli.main(["idle", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {r["ao_session"] for r in data} == {"management-9", "management-14", "management-20"}


def test_context_of_prefers_ao_journal(tmp_path, monkeypatch):
    d = tmp_path / "ao" / "chat-hosts" / "management-1"
    d.mkdir(parents=True)
    (d / "acp-prompt.journal").write_text(json.dumps({"params": {
        "sessionId": "", "update": {"sessionUpdate": "usage_update", "used": 120000,
                                    "size": 1000000}}}) + "\n", encoding="utf-8")
    monkeypatch.setenv("AO_DATA_DIR", str(tmp_path / "ao"))
    c = sv.context_of("management-1")
    assert c["tokens"] == 120000 and c["left"] == 80000
    assert sv.ctx_label(c) == "120k (ост 80k)"
    assert sv.context_of("management-2") is None


# --- why ---------------------------------------------------------------------------

def test_why_by_ao_id(world):
    r = sv.why("management-20", sessions=_rows(), locator=_no_loc, now=NOW)
    assert r["label"] == "management — signing · 01"
    assert r["question"] == {"tg": "4586", "answered": False}
    assert [x.split(" — ", 1)[1] for x in r["context_tail"]] == ["шаг 0", "шаг 1", "шаг 2"]
    assert r["offloaded_at"]
    text = sv.render_why(r)
    assert "вопрос TG: TG 4586 — открыт" in text
    assert r["passport"] in text


def test_why_by_project_thread(world):
    r = sv.why("management/main", sessions=_rows(), locator=_no_loc, now=NOW)
    assert r["ao_session"] == "management-14"
    assert r["next"] == ["22:30 сверка"]
    assert r["ball_label"] == "у меня"


def test_why_unknown_ao_id_errors(world, capsys):
    with pytest.raises(Exception):
        sv.why("management-99", sessions=_rows(), locator=_no_loc, now=NOW)


def test_why_dead_ao_id_falls_back_to_title(world):
    r = sv.why("management-9", sessions=[], locator=_no_loc, now=NOW)
    assert r["passport"].endswith("main-1/arc.md")
    assert r["handed_off"].startswith("2026-10-12T21:42:05")


# --- gc sessions -------------------------------------------------------------------

def _gc(rows, **kw):
    kw.setdefault("me", "")
    return {v["ao_session"]: v for v in sv.gc_sessions(
        sessions=rows, locator=_no_loc, now=NOW, busy=lambda t: [], **kw)}


def test_gc_lists_replaced_session_only(world):
    v = _gc(_rows())
    assert v["management-9"]["close"] is True
    assert "handed-off" in v["management-9"]["why"]
    assert "management-14" in v["management-9"]["why"]
    assert v["management-14"]["close"] is False and "нет явного признака" in v["management-14"]["why"]
    assert v["management-20"]["close"] is False


def test_gc_never_current_session(world):
    v = _gc(_rows(), me="management-9")
    assert v["management-9"]["close"] is False and v["management-9"]["why"] == "текущая сессия"


def test_gc_never_recent_activity(world):
    rows = [_ao("management-9", ago=4), _ao("management-14", ago=2)]
    v = _gc(rows)
    assert v["management-9"]["close"] is False and "< 10 мин" in v["management-9"]["why"]


def test_gc_never_without_live_successor(world):
    v = _gc([_ao("management-9", ago=60)])
    assert v["management-9"]["close"] is False and "не жив" in v["management-9"]["why"]


def test_gc_never_the_thread_head(world):
    # передал, но новая сессия не началась — ведущая всё ещё эта
    fields.set_field(world["s"]["management-14"] / "arc.md", "dismissed", "рукой")
    v = _gc(_rows())
    assert v["management-9"]["close"] is False and "ведущая" in v["management-9"]["why"]


def test_gc_never_with_live_workers(world):
    v = {x["ao_session"]: x for x in sv.gc_sessions(
        sessions=_rows(), locator=_no_loc, now=NOW, me="",
        busy=lambda t: ["- воркер «тесты» — жив"])}
    assert v["management-9"]["close"] is False and "помощники" in v["management-9"]["why"]


def test_gc_project_filter(world):
    assert _gc(_rows(), project="music") == {}


def test_gc_cli_lists_without_yes_and_kills_with_yes(world, monkeypatch, capsys):
    monkeypatch.setattr(sv, "ao_rows", _rows)
    from tide import watch_stuck as ws
    monkeypatch.setattr(ws, "locate", _no_loc)
    monkeypatch.setattr(sv, "_busy", lambda t: [])
    real_describe = sv.describe
    monkeypatch.setattr(sv, "describe", lambda s, **kw: real_describe(s, **dict(kw, now=NOW)))
    killed = []
    monkeypatch.setattr(autoclose, "_kill", lambda h: (killed.append(h), (True, ""))[1])

    assert cli.main(["gc", "sessions"]) == 0
    out = capsys.readouterr().out
    assert "закрыть (1):" in out and "management-9" in out and killed == []
    assert "tide gc sessions --yes" in out

    assert cli.main(["gc", "sessions", "--yes"]) == 0
    out = capsys.readouterr().out
    assert killed == ["management-9"]
    assert "закрыто (1):" in out and "[kill ok]" in out
    log = (world["home"] / ".tide" / "state" / autoclose.LOG).read_text(encoding="utf-8")
    assert "gc-sessions · kill management-9" in log


def test_gc_kill_failure_is_reported(world, monkeypatch):
    verdicts = [{"ao_session": "management-9", "label": "x", "close": True, "why": "w"},
                {"ao_session": "management-14", "label": "y", "close": False, "why": "k"}]
    out = sv.gc_kill(verdicts, home=world["home"], kill=lambda h: (False, "ao: нет"))
    assert len(out) == 1 and out[0]["killed"] is False and out[0]["kill_error"] == "ao: нет"
    assert "kill НЕ ВЫШЕЛ: ao: нет" in sv.render_gc(verdicts, out)


# --- role-gate: голове разрешено ---------------------------------------------------

@pytest.mark.parametrize("cmd", ["tide idle", "tide why management-53",
                                 "tide gc sessions --yes", "tide idle --project management --json"])
def test_role_gate_allows_head(cmd):
    allow, reason = role_gate.decide("Bash", {"command": cmd}, "orchestrator")
    assert allow is True, reason

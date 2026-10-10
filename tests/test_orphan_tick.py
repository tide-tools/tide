"""SOC-171: детектор нити-сироты по таймеру launchd раз в 2 мин, без сессии.

Хук handoff-confirm бежит только на сообщении живой сессии: в тишине тревога пришла
через 18 мин 23 с после kill (проба SOC-167). Таймер ``tide hook orphan-tick`` делает
тот же проход, троттлинг общий с хуком, сессионный env ему не нужен.

AO, отправка менеджеру, подъём — подделки; ничего наружу.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from tide import handoff_queue as hq
from tide.adapters import claude_proc
from tide.arc import holder
from tide.hooks import orphan

from tests.conftest import build_tide_skeleton

SID_HEAD = "sid-board-6"
DEAD = {"isTerminated": True, "status": "terminated", "activity": {"state": "exited"}}
SESSION_ENV = ("TIDE_ROLE", "AO_PROJECT_ID", "AO_SESSION_ID", "TIDE_PROJECT_ROOT")


@pytest.fixture
def world(tmp_control_home, tmp_path, monkeypatch):
    """control-home с ростером → proj → нить 29-@board, ведущая board · 06 принята;
    сессионного env нет (как у launchd)."""
    home = tmp_control_home
    proj = tmp_path / "proj"
    build_tide_skeleton(proj, name="proj")
    (home / "roster.md").write_text("# tide roster\nproj | {0}\n".format(proj),
                                    encoding="utf-8")
    thread = proj / ".tide" / "arcs" / "29-@board"
    thread.mkdir(parents=True)
    (thread / "board-goal.md").write_text(
        "# board-thread\n\ngoal: доска\nstatus: active\nkind: thread\n", encoding="utf-8")
    head = thread / "arcs" / "06-board-6"
    head.mkdir(parents=True)
    (head / "arc.md").write_text(
        "# 06-board-6\n\ntitle: proj — board · 06\ngoal: доска\nstatus: active\n"
        "claude-session: {0}\n\n## context\n- пульс\n".format(SID_HEAD), encoding="utf-8")
    for k in SESSION_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.delenv(orphan.WATCH_ENV, raising=False)
    monkeypatch.delenv(orphan.REPEAT_ENV, raising=False)
    monkeypatch.setattr(holder, "ao_handle_for",
                        lambda h, sid: "tide-stack-33" if sid == SID_HEAD else "")
    monkeypatch.setattr(hq, "list_offers", lambda h, status=None: [])
    monkeypatch.setattr(claude_proc, "snapshot", lambda **kw: [])
    return {"home": home, "proj": proj, "head": head}


def _stamp(home: Path) -> Path:
    return home / ".tide" / "state" / orphan.STAMP


def _age_stamp(home: Path, seconds: float) -> None:
    p = _stamp(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    t = time.time() - seconds
    os.utime(p, (t, t))


def _count_sweeps(monkeypatch):
    seen = []
    monkeypatch.setattr(orphan, "sweep",
                        lambda home, env=None, **kw: seen.append(Path(home)) or [])
    return seen


# --- таймер зовёт проход ------------------------------------------------------------------

def test_tick_runs_sweep(world, monkeypatch):
    seen = _count_sweeps(monkeypatch)
    res = orphan.on_tick(env={})
    assert res["ran"] is True
    assert seen == [Path(world["home"]).resolve()]
    assert _stamp(world["home"]).is_file()


def test_tick_finds_orphan_logs_and_alerts_manager(world, monkeypatch):
    """Полный проход таймером: строка «нить-сирота» в alerts.log и менеджеру."""
    sent = []
    res = orphan.on_tick(env={}, session_of=lambda h: DEAD if h == "tide-stack-33" else None,
                         alert=lambda text, src, thread: sent.append((src, text)),
                         spawn=lambda v: None)
    assert res["ran"] is True
    assert [v["thread"] for v in res["verdicts"] if v.get("orphan")] == ["board"]
    log = (world["home"] / ".tide" / "state" / "alerts.log").read_text(encoding="utf-8")
    assert orphan.MARK in log and "proj/board" in log
    assert sent and sent[0][0] == "proj/board/orphan"
    line = orphan.tick_line(res)
    assert "сирот 1" in line and "СИРОТА proj/board" in line


def test_tick_alert_goes_route_manager(world, monkeypatch):
    from tide.launcher import autohandoff
    calls = []
    monkeypatch.setattr(autohandoff, "send_alert",
                        lambda text, route, source="", extra_env=None: calls.append(route) or 0)
    orphan.on_tick(env={}, session_of=lambda h: DEAD, spawn=lambda v: None)
    assert calls == [orphan.ROUTE_MANAGER]


def test_tick_without_session_env_from_cwd_home(world, monkeypatch):
    """launchd: ни TIDE_ROLE/AO_SESSION_ID, ни $TIDE_HOME — home по cwd (WorkingDirectory)."""
    monkeypatch.delenv("TIDE_HOME", raising=False)
    monkeypatch.chdir(world["home"])
    seen = _count_sweeps(monkeypatch)
    res = orphan.on_tick(env={})
    assert res["ran"] is True and len(seen) == 1
    assert seen[0].resolve() == Path(world["home"]).resolve()


def test_tick_no_home_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.delenv("TIDE_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    res = orphan.on_tick(env={})
    assert res["ran"] is False and res["why"].startswith("ошибка")


def test_tick_off_switch(world, monkeypatch):
    seen = _count_sweeps(monkeypatch)
    res = orphan.on_tick(env={orphan.WATCH_ENV: "off"})
    assert res["ran"] is False and seen == []
    assert "выключен" in orphan.tick_line(res)


def test_tick_not_blocked_by_throttle_pause(world, monkeypatch):
    """Пауза пульта лимита (SOC-166) таймер не держит: проход идёт (проекты на паузе
    пропускает сам scan, как на хуке)."""
    (world["home"] / ".tide" / "state").mkdir(parents=True, exist_ok=True)
    (world["home"] / ".tide" / "state" / "throttle.json").write_text(json.dumps(
        {"v": 1, "paused": {"all": True, "projects": {}}, "until": None}), encoding="utf-8")
    res = orphan.on_tick(env={}, session_of=lambda h: DEAD, alert=lambda *a: None,
                         spawn=lambda v: None)
    assert res["ran"] is True
    assert [v["why"] for v in res["verdicts"]] == [
        "проект на паузе пульта лимита (throttle.json)"]


# --- троттлинг общий с хуком ---------------------------------------------------------------

def test_hook_then_tick_shares_throttle(world, monkeypatch):
    seen = _count_sweeps(monkeypatch)
    orphan.on_prompt(env={})
    res = orphan.on_tick(env={})
    assert len(seen) == 1
    assert res["ran"] is False and "пропуск" in res["why"]


def test_tick_then_hook_shares_throttle(world, monkeypatch):
    seen = _count_sweeps(monkeypatch)
    assert orphan.on_tick(env={})["ran"] is True
    orphan.on_prompt(env={})
    assert len(seen) == 1


def test_tick_skips_when_hook_passed_recently(world, monkeypatch):
    seen = _count_sweeps(monkeypatch)
    _age_stamp(world["home"], 60)
    assert orphan.on_tick(env={})["ran"] is False and seen == []


def test_tick_does_not_skip_own_previous_pass_by_jitter(world, monkeypatch):
    """Шаг launchd 120 с ± дрожь: прошлый проход 115 с назад — таймер идёт (иначе
    проход раз в 4 мин), а хук по-прежнему ждёт полные THROTTLE_SEC."""
    seen = _count_sweeps(monkeypatch)
    _age_stamp(world["home"], orphan.THROTTLE_SEC - 5)
    orphan.on_prompt(env={})
    assert seen == []
    assert orphan.on_tick(env={})["ran"] is True and len(seen) == 1


def test_tick_runs_after_throttle_window(world, monkeypatch):
    seen = _count_sweeps(monkeypatch)
    _age_stamp(world["home"], orphan.THROTTLE_SEC + 1)
    assert orphan.on_tick(env={})["ran"] is True and len(seen) == 1


# --- CLI и plist -----------------------------------------------------------------------------

def test_cli_hook_orphan_tick(world, monkeypatch, capsys):
    from tide import cli
    seen = _count_sweeps(monkeypatch)
    rc = cli.main(["hook", "orphan-tick"])
    out = capsys.readouterr().out
    assert rc == 0 and len(seen) == 1
    assert "orphan-tick: проход" in out


def test_cli_print_plist(world, monkeypatch, capsys):
    from tide import cli
    monkeypatch.setenv("TIDE_ALERT_CMD", "/x/tg-alert")
    seen = _count_sweeps(monkeypatch)
    assert cli.main(["hook", "orphan-tick", "--print-plist"]) == 0
    assert seen == []  # печать plist прохода не делает
    out = capsys.readouterr().out
    home = str(Path(world["home"]).resolve())
    assert out.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    for frag in ("<string>com.tide.orphan-watch</string>",
                 "<key>StartInterval</key>\n  <integer>120</integer>",
                 "<string>hook</string>\n    <string>orphan-tick</string>\n  </array>",
                 "<key>TIDE_HOME</key>\n    <string>{0}</string>".format(home),
                 "<key>TIDE_ALERT_CMD</key>\n    <string>/x/tg-alert</string>",
                 "<string>{0}/.tide/state/orphan-watch.log</string>".format(home)):
        assert frag in out
    assert not any("<key>{0}</key>".format(k) in out for k in SESSION_ENV)


def test_plist_xml_round_trip(world):
    """Свой XML читается plistlib'ом как тот же словарь (где pyexpat грузится)."""
    plistlib = pytest.importorskip("plistlib")
    d = orphan.tick_plist(world["home"], env={"HOME": "/Users/u", "TIDE_ALERT_CMD": "a&<b>"},
                          argv=["/bin/tide"])
    assert plistlib.loads(orphan.plist_xml(d).encode("utf-8")) == d


def test_plist_without_alert_cmd(world):
    pl = orphan.tick_plist(world["home"], env={"HOME": "/Users/u"}, argv=["/bin/tide"])
    assert "TIDE_ALERT_CMD" not in pl["EnvironmentVariables"]
    assert pl["ProgramArguments"] == ["/bin/tide", "hook", "orphan-tick"]
    assert "/Users/u/.local/bin" in pl["EnvironmentVariables"]["PATH"]

"""SOC-121: журнал подъёма — КАК поднялась сессия по офферу.

Каждый путь подъёма (автохендофф сразу, автоподъём через N мин, ▶ доски /
tide menu, ``tide pickup`` руками) пишет в оффер ``raised-by:``/``raised-at:``,
строку в ``.tide/state/raises.log`` control-home и говорит новой сессии в
стартовом промпте правду о способе.

Все адаптеры фейковые; сон, TG и ao send подменены — ни одного настоящего
подъёма или сообщения.
"""

from __future__ import annotations

import pytest

from tide import cli, handoff_queue as hq, paths
from tide.launcher import autohandoff as ah, menu, pickup, raise_log

from tests.test_handoff_autospawn import _Fake, _FakeAo, _use, world  # noqa: F401


def _log_lines(home):
    path = paths.state_dir(home) / raise_log.RAISES_LOG
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


def _prompt(adapter) -> str:
    return " ".join(adapter.spawned["command"])


def _hang(world):
    """Оффер висит без подъёма (как после стопа автохендоффа)."""
    assert cli.main(["handoff", "seam", "--no-spawn", "--from-session", "old-sid"]) == 0
    (rec,) = hq.list_offers(world["home"])
    return rec


def _fresh(home, key):
    return next(r for r in hq.list_offers(home) if r["name"] == key)


def test_auto_handoff_marks_auto(world, monkeypatch):
    adapter = _use(monkeypatch, _FakeAo(ref="proj-7"))
    assert cli.main(["handoff", "seam", "--from-session", "old-sid"]) == 0
    (rec,) = hq.list_offers(world["home"])
    assert rec["raised_by"] == "auto" and rec["raised_at"] != "-"
    text = rec["path"].read_text(encoding="utf-8")
    assert "raised-by: auto" in text and "raised-at: " in text
    prompt = _prompt(adapter)
    assert "поднята автохендоффом сразу" in prompt and "▶ с доски" not in prompt
    (line,) = _log_lines(world["home"])
    parts = line.split(" · ")
    assert parts[1:3] == [rec["name"], "auto"]
    assert "из сессии old-sid" in parts[3] and "нить" in parts[3]
    assert parts[4] == "ao proj-7"


def test_hand_pickup_marks_pickup(world, monkeypatch):
    rec = _hang(world)
    adapter = _use(monkeypatch, _Fake())
    assert cli.main(["pickup", rec["name"]]) == 0
    assert _fresh(world["home"], rec["name"])["raised_by"] == "pickup"
    assert "по тёплому хендоффу (tide pickup)" in _prompt(adapter)
    (line,) = _log_lines(world["home"])
    assert " · {0} · pickup · ".format(rec["name"]) in line and line.endswith("fake term_x")


def test_board_press_marks_board(world, monkeypatch):
    rec = _hang(world)
    adapter = _use(monkeypatch, _Fake())
    assert cli.main(["pickup", rec["name"], "--json", "--by", "board"]) == 0
    assert _fresh(world["home"], rec["name"])["raised_by"] == "board"
    assert "(▶ с доски)" in _prompt(adapter)
    (line,) = _log_lines(world["home"])
    assert " · {0} · board · ".format(rec["name"]) in line


def test_menu_pick_marks_board_via_menu(world, monkeypatch):
    rec = _hang(world)
    adapter = _Fake()
    res = menu.launch_handoff(rec, menu.list_entries(world["home"]),
                              control_home=world["home"], adapter=adapter,
                              raised_by=raise_log.BOARD, via="tide menu")
    assert res.ok
    assert _fresh(world["home"], rec["name"])["raised_by"] == "board"
    assert "(▶ в tide menu)" in _prompt(adapter)
    (line,) = _log_lines(world["home"])
    assert " · board (tide menu) · " in line


def test_failed_spawn_logs_but_does_not_stamp(world, monkeypatch):
    rec = _hang(world)
    _use(monkeypatch, _Fake(ok=False))
    assert cli.main(["pickup", rec["name"]]) == 1
    assert _fresh(world["home"], rec["name"])["raised_by"] == "-"
    (line,) = _log_lines(world["home"])
    assert "не поднялась: fake failure" in line


def test_dry_run_writes_nothing(world, monkeypatch):
    rec = _hang(world)
    _use(monkeypatch, _Fake())
    assert pickup.run_pickup(world["home"], rec["name"], dry_run=True)["ok"]
    assert _fresh(world["home"], rec["name"])["raised_by"] == "-"
    assert _log_lines(world["home"]) == []


@pytest.fixture
def quiet(monkeypatch):
    """TG и менеджер перехвачены: ни одного сообщения."""
    sent = []
    monkeypatch.setattr(ah, "send_alert", lambda text, **kw: sent.append(text) or 0)
    monkeypatch.setattr(ah, "tell_manager", lambda text: sent.append(text) or {})
    return sent


def test_autoraise_marks_autoraise_and_logs_decision(world, monkeypatch, quiet):
    rec = _hang(world)
    adapter = _use(monkeypatch, _Fake())
    slept = []
    out = ah.run_autoraise(world["home"], rec["name"], after=1800, thread="seam",
                           sleep=slept.append)
    assert out["action"] == "raised" and sum(slept) == 1800
    assert _fresh(world["home"], rec["name"])["raised_by"] == "autoraise"
    assert "поднята сама через 30 мин — никто не ответил на стоп" in _prompt(adapter)
    lines = _log_lines(world["home"])
    assert "autoraise: жду 1800 сек · нить seam" in lines[0]
    assert "autoraise: 1800 сек без ответа — поднимаю сам" in lines[1]
    assert " · {0} · autoraise · ".format(rec["name"]) in lines[2]
    assert lines[2].endswith("fake term_x") and len(lines) == 3


def test_autoraise_logs_taken_exit(world, monkeypatch, quiet):
    rec = _hang(world)
    adapter = _use(monkeypatch, _Fake())

    def sleep(sec):
        hq.take(world["home"], rec["name"], session="sid-x")
    out = ah.run_autoraise(world["home"], rec["name"], after=1800, sleep=sleep)
    assert out["state"] == "taken" and adapter.spawned is None and not quiet
    lines = _log_lines(world["home"])
    assert "autoraise: жду 1800 сек" in lines[0]
    assert "autoraise: оффер взят через 30 сек — выхожу" in lines[1]


def test_autoraise_logs_dropped_exit(world, monkeypatch, quiet):
    rec = _hang(world)
    hq.drop(world["home"], rec["name"])
    out = ah.run_autoraise(world["home"], rec["name"], after=60, sleep=lambda s: None)
    assert out["action"] == "skip"
    assert "autoraise: оффер снят через 0 сек — выхожу" in _log_lines(world["home"])[1]


def test_how_words():
    assert raise_log.how("auto") == "поднята автохендоффом сразу"
    assert raise_log.how("autoraise", after=1800) == (
        "поднята сама через 30 мин — никто не ответил на стоп")
    assert raise_log.how("board") == "▶ с доски"
    assert raise_log.how("pickup") == "tide pickup"

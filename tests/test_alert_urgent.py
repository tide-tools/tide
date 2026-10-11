"""SOC-179: явный признак срочности для пульта — ``TIDE_ALERT_URGENT=1`` у ``$TIDE_ALERT_CMD``.

Пульт копит ``route=manager`` в пачку раз в 25 мин; срочное (эскалация, шов-синк) он
узнаёт по признаку, а не по тексту. Несрочное (сирота, «закрыть?») признака не несёт.
Команда здесь подменена: ни одного живого tg-alert."""

from __future__ import annotations

import subprocess

import pytest

from tide.launcher import autohandoff


@pytest.fixture
def ran(monkeypatch):
    calls = []

    def fake_run(argv, env=None, **kw):
        calls.append({"argv": argv, "env": env})
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setenv(autohandoff.ALERT_CMD_ENV, "/bin/tg-alert-fake")
    monkeypatch.setattr(autohandoff.subprocess, "run", fake_run)
    return calls


def test_urgent_sets_the_flag(ran):
    assert autohandoff.send_alert("шов-синк x", route="manager", source="s", urgent=True) == 0
    assert ran[0]["env"][autohandoff.ALERT_URGENT_ENV] == "1"
    assert ran[0]["env"]["TIDE_ALERT_ROUTE"] == "manager"
    assert ran[0]["argv"][-1] == "шов-синк x"


def test_calm_has_no_flag_even_if_inherited(ran, monkeypatch):
    monkeypatch.setenv(autohandoff.ALERT_URGENT_ENV, "1")   # окружение не протекает
    autohandoff.send_alert("сирота", route="manager", source="s")
    assert autohandoff.ALERT_URGENT_ENV not in ran[0]["env"]


def test_no_cmd_is_none(monkeypatch):
    monkeypatch.delenv(autohandoff.ALERT_CMD_ENV, raising=False)
    assert autohandoff.send_alert("x", route="manager", urgent=True) is None

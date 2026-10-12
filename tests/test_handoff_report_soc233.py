"""SOC-233: отчёт лида в Telegram на хендоффе — отправка, гейт, не-лид."""

from __future__ import annotations

import json

import pytest

from tide import fields, whois
from tide.arc import stream
from tide.hooks import context_watch
from tide.launcher import autohandoff, handoff, handoff_report as hr

from tests.test_handoff_autospawn import world  # noqa: F401  fixture


@pytest.fixture
def lead(world, monkeypatch):
    monkeypatch.setitem(hr.LEADS, "proj", ("seam",))
    topics = world["home"] / "role-topics.json"
    topics.write_text(json.dumps({"topics": [
        {"group": "pulse", "id": 55, "title": "Проект", "route": "proj/main"},
        {"group": "pulse", "id": 56, "title": "Другой", "route": "other/main"}]}))
    monkeypatch.setenv(hr.ROLE_TOPICS_ENV, str(topics))
    monkeypatch.setenv(hr.ALERT_CMD_ENV, "fake-alert")
    monkeypatch.delenv(hr.NO_REPORT_ENV, raising=False)
    calls = []
    monkeypatch.setattr(hr, "_run_alert", lambda argv, env: (calls.append((argv, env)), (0, ""))[1])
    entry = stream.new_session(world["proj"], "seam", "s1", goal="g")
    return {"entry": entry, "calls": calls, **world}


def test_report_goes_to_project_topic_and_stamps_passport(lead):
    where = hr.send_report(lead["entry"], "Сделано: X. SOC-233")
    argv, env = lead["calls"][0]
    assert argv[:5] == ["fake-alert", "--group", "pulse", "--topic", "55"]
    assert "--soc" in argv and argv[-2:] == ["--", "Сделано: X. SOC-233"]
    assert env["TIDE_ALERT_ROUTE"] == "human"
    assert "55" in where
    assert fields.read_field(lead["entry"] / "arc.md", hr.REPORT_FIELD).startswith("sent ")


def test_manager_report_goes_to_dm(lead, monkeypatch):
    monkeypatch.setattr(whois, "is_manager_thread", lambda *a, **k: True)
    hr.send_report(lead["entry"], "итог")
    argv, _ = lead["calls"][0]
    assert "--topic" not in argv and argv[-2:] == ["--", "итог"]


def test_not_a_lead_is_silent(world, monkeypatch):
    monkeypatch.setenv(hr.ALERT_CMD_ENV, "fake-alert")
    called = []
    monkeypatch.setattr(hr, "_run_alert", lambda a, e: called.append(a) or (0, ""))
    entry = stream.new_session(world["proj"], "seam", "s1", goal="g")
    assert hr.send_report(entry, "текст") is None
    assert not called and not hr.has_report(entry)
    hr.require_report(entry)  # не лид — не блокирует


def test_gate_blocks_lead_without_report_and_passes_with(lead):
    with pytest.raises(hr.ReportError) as e:
        hr.require_report(lead["entry"])
    assert "handoffs report" in str(e.value)
    hr.send_report(lead["entry"], "итог")
    hr.require_report(lead["entry"])


def test_gate_stops_auto_handoff_not_dry_run(lead):
    with pytest.raises(hr.ReportError):
        autohandoff.run_auto(lead["proj"], session_ref="seam/s1", spawn=False)
    autohandoff.run_auto(lead["proj"], session_ref="seam/s1", dry_run=True)  # не гейтится


def test_bypass_env_logs(lead, monkeypatch, capsys):
    monkeypatch.setenv(hr.NO_REPORT_ENV, "1")
    hr.require_report(lead["entry"])
    log = (lead["home"] / ".tide" / "state" / hr.LOG_NAME).read_text()
    assert "bypass" in log


def test_templates_mention_report_only_for_lead(lead):
    assert "handoffs report" in context_watch.handoff_steps(lead["entry"])
    stream.new_thread(lead["proj"], "plain", goal="x")
    s = stream.new_session(lead["proj"], "plain", "s1", goal="g")
    assert "handoffs report" not in context_watch.handoff_steps(s)

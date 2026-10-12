"""SOC-233: отчёт агента — запись в reports.jsonl, паспорт, гейт передачи, шаблоны."""

from __future__ import annotations

import json

import pytest

from tide import cli, fields
from tide.arc import stream
from tide.hooks import context_watch
from tide.launcher import autohandoff, handoff_report as hr

from tests.test_handoff_autospawn import world  # noqa: F401  fixture


@pytest.fixture
def sess(world, monkeypatch, tmp_path):
    reports = tmp_path / "reports.jsonl"
    monkeypatch.setenv(hr.REPORTS_FILE_ENV, str(reports))
    monkeypatch.delenv(hr.NO_REPORT_ENV, raising=False)
    monkeypatch.setenv("AO_SESSION_ID", "proj-7")
    entry = stream.new_session(world["proj"], "seam", "s1", goal="g")
    return {"entry": entry, "reports": reports, **world}


def _lines(p):
    return [json.loads(x) for x in p.read_text().splitlines()]


def test_report_appends_record_and_stamps_passport(sess):
    rec = hr.write_report(sess["entry"], done="Починил X (SOC-233)", proof=["/a/b.png", "https://x"],
                          next_="мерж", waits="ок на мерж", soc=["SOC-185"], kind="handoff")
    (got,) = _lines(sess["reports"])
    assert got == rec
    assert got["id"].startswith("r-") and got["project"] == "proj" and got["role"] == "seam"
    assert got["thread"] == "01-@seam" and got["session"] == "01-s1" and got["ao"] == "proj-7"
    assert got["soc"] == ["SOC-185", "SOC-233"] and got["kind"] == "handoff"
    assert 3 <= len(got["text"].splitlines()) <= 8 and "SOC-233" in got["text"]
    assert "Пруф: /a/b.png; https://x" in got["text"]
    assert fields.read_field(sess["entry"] / "arc.md", hr.REPORT_FIELD).startswith(got["id"])


def test_two_reports_two_lines(sess):
    hr.write_report(sess["entry"], done="a")
    hr.write_report(sess["entry"], done="b")
    assert len(_lines(sess["reports"])) == 2


def test_empty_report_refused(sess):
    with pytest.raises(hr.ReportError):
        hr.write_report(sess["entry"])
    assert not sess["reports"].exists()


def test_cli_tide_report_with_work_flags_and_alias(sess, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-1")
    fields.set_field(sess["entry"] / "arc.md", "claude-session", "sid-1")
    assert cli.main(["report", "--done", "сделал", "--soc", "SOC-5"]) == 0
    assert cli.main(["handoffs", "report", "--done", "ещё"]) == 0
    recs = _lines(sess["reports"])
    assert [r["done"] for r in recs] == ["сделал", "ещё"] and recs[0]["soc"] == ["SOC-5"]


def test_gate_blocks_without_report_and_passes_with(sess):
    with pytest.raises(hr.ReportError) as e:
        hr.require_report(sess["entry"])
    assert "tide report" in str(e.value)
    hr.write_report(sess["entry"], done="итог")
    hr.require_report(sess["entry"])


def test_gate_stops_auto_handoff_not_dry_run(sess):
    with pytest.raises(hr.ReportError):
        autohandoff.run_auto(sess["proj"], session_ref="seam/s1", spawn=False)
    autohandoff.run_auto(sess["proj"], session_ref="seam/s1", dry_run=True)


def test_bypass_env_logs(sess, monkeypatch, tmp_path):
    monkeypatch.setenv(hr.NO_REPORT_ENV, "1")
    hr.require_report(sess["entry"])
    assert "bypass" in (sess["home"] / ".tide" / "state" / hr.LOG_NAME).read_text()


def test_templates_have_step_zero_for_every_role(sess):
    assert "tide report --kind handoff" in context_watch.handoff_steps(sess["entry"])
    assert "tide report" in autohandoff.report_step(sess["entry"])

"""U10 — SessionStart hook: board + role reminder + drift / unmerged warnings."""

from __future__ import annotations

import pytest

from tide import cli
from tide.arc import stream
from tide.cannon import store
from tide.hooks import session_start

from tests.conftest import strip_placeholders


def test_render_includes_board_and_role_reminder(tmp_project):
    stream.new_arc(tmp_project, "do-thing")
    text = session_start.render(tmp_project, "orchestrator")
    assert "STREAM" in text
    assert "ORCHESTRATOR" in text


def test_render_worker_role_reminder(tmp_project):
    text = session_start.render(tmp_project, "worker")
    assert "WORKER" in text
    assert "Never merge cannon" in text


def test_render_unknown_role_falls_back_to_worker(tmp_project):
    text = session_start.render(tmp_project, "bogus")
    assert "WORKER" in text


def test_render_flags_drift_on_open_arc(tmp_project):
    # Open an arc (stamps current cannon-rev), then move CANON.md so it drifts.
    stream.new_arc(tmp_project, "do-thing")
    canon = tmp_project / ".tide" / "cannon" / "CANON.md"
    canon.write_text(canon.read_text(encoding="utf-8") + "\nmoved\n", encoding="utf-8")
    text = session_start.render(tmp_project, "orchestrator")
    assert "WARNINGS" in text
    assert "drift" in text
    assert "do-thing" in text


def test_render_flags_unmerged_delta(tmp_project):
    arc = stream.new_arc(tmp_project, "alpha")
    (arc / "output" / "r.md").write_text("ok\n", encoding="utf-8")
    strip_placeholders(arc / "arc.md")
    closed = stream.close(tmp_project, "alpha")
    (closed / "delta.md").write_text(
        "# delta — alpha\n\nadded a thing.\n", encoding="utf-8"
    )
    text = session_start.render(tmp_project, "orchestrator")
    assert "WARNINGS" in text
    assert "unmerged delta" in text
    assert "tide cannon merge alpha" in text


def test_render_clean_project_has_no_warnings(tmp_project):
    stream.new_arc(tmp_project, "do-thing")
    text = session_start.render(tmp_project, "orchestrator")
    assert "WARNINGS" not in text


# --- CLI handler -----------------------------------------------------------

def test_cli_session_start_prints_board(tmp_project, monkeypatch, capsys):
    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("TIDE_ROLE", "worker")
    stream.new_arc(tmp_project, "do-thing")
    rc = cli.main(["hook", "session-start"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "STREAM" in out
    assert "WORKER" in out


def test_cli_session_start_outside_project_is_silent_noop(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # no .tide/
    rc = cli.main(["hook", "session-start"])
    assert rc == 0
    assert capsys.readouterr().out == ""

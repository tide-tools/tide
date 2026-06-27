"""U10 — SessionStart hook: board + role reminder + drift / unmerged warnings."""

from __future__ import annotations

import pytest

from tide import cli, readme
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
    # A truly clean project: open arc (suppresses arc-first) + current README
    # (suppresses readme-drift).  Only these two warnings could fire on a plain
    # new project; both are cleared here so the session opens warning-free.
    stream.new_arc(tmp_project, "do-thing")
    readme.generate(tmp_project)
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


# --- G1 arc-first WARN (advisory; orchestrator-only) ------------------------

def test_arc_first_warning_when_orchestrator_no_arc_no_contract(tmp_project):
    text = session_start.render(tmp_project, "orchestrator")
    assert "WARNINGS" in text
    assert "arc-first" in text


def test_no_arc_first_warning_for_worker(tmp_project):
    text = session_start.render(tmp_project, "worker")
    assert "arc-first" not in text


def test_no_arc_first_warning_when_open_arc(tmp_project):
    stream.new_arc(tmp_project, "do-thing")
    text = session_start.render(tmp_project, "orchestrator")
    assert "arc-first" not in text


def _write_sealed_contract(root, *, state: str) -> None:
    """A CLOSED arc (no open arc) carrying a contract in *state* — anchor probe."""
    arc = root / ".tide" / "arcs" / "__01-sealed__"
    arc.mkdir(parents=True, exist_ok=True)
    (arc / "arc.md").write_text("# 01-sealed\n\nstatus: done\n", encoding="utf-8")
    (arc / "contract.md").write_text(
        "# contract — x\n\nslug: x\nstate: {0}\n".format(state), encoding="utf-8"
    )


def test_no_arc_first_warning_when_signed_contract(tmp_project):
    # A running contract anchors work even with no OPEN arc → no warning.
    _write_sealed_contract(tmp_project, state="running")
    text = session_start.render(tmp_project, "orchestrator")
    assert "arc-first" not in text


def test_draft_contract_does_not_anchor(tmp_project):
    # A draft (unsigned) contract is NOT anchored → the warning still fires.
    _write_sealed_contract(tmp_project, state="draft")
    text = session_start.render(tmp_project, "orchestrator")
    assert "arc-first" in text


# --- readme drift warnings (criterion F) -----------------------------------

def test_render_warns_readme_drift_when_stale(tmp_project):
    """SessionStart includes a readme drift warning when the README is stale/missing."""
    # Open an arc to suppress the arc-first advisory.
    stream.new_arc(tmp_project, "do-thing")
    # README never generated → code 1 → warning expected.
    text = session_start.render(tmp_project, "orchestrator")
    assert "readme: drift" in text
    assert "WARNINGS" in text


def test_render_no_readme_warning_when_current(tmp_project):
    """SessionStart has no readme drift warning when README is up-to-date."""
    readme.generate(tmp_project)
    stream.new_arc(tmp_project, "do-thing")  # suppress arc-first
    text = session_start.render(tmp_project, "orchestrator")
    assert "readme: drift" not in text


def test_readme_drift_warning_silent_on_oracle_error(tmp_path):
    """_readme_drift_warnings returns [] when CANON.md is missing (oracle-error).

    The hook must never raise on infrastructure errors — code 2 stays silent.
    """
    # A path with .tide/ but no CANON.md → check() returns code 2 (oracle-error).
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / ".tide").mkdir()
    warnings = session_start._readme_drift_warnings(bad)
    assert warnings == []


def test_readme_drift_warning_silent_for_nonexistent_path(tmp_path):
    """_readme_drift_warnings returns [] for a totally non-existent path."""
    nonexistent = tmp_path / "no-such-project"
    warnings = session_start._readme_drift_warnings(nonexistent)
    assert warnings == []

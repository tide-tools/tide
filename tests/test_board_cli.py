"""U8 integration — `tide status` / `tide arc status` / `tide canon status` via the CLI."""

from __future__ import annotations

import pytest

from tide import cli, paths
from tide.arc import stream
from tide.contract import lifecycle


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    """Run CLI commands as if cwd is the project root (paths resolves from cwd)."""
    monkeypatch.chdir(tmp_project)
    return tmp_project


def test_cli_status_prints_stream(in_project, capsys):
    cli.main(["arc", "new", "alpha"])
    capsys.readouterr()  # drop the 'created arc' line
    rc = cli.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("STREAM")
    assert "01-alpha" in out


def test_cli_arc_status_prints_stream(in_project, capsys):
    cli.main(["arc", "new", "alpha"])
    capsys.readouterr()
    rc = cli.main(["arc", "status"])
    assert rc == 0
    assert capsys.readouterr().out.startswith("STREAM")


def test_cli_canon_status_groups_by_state(in_project, capsys):
    cli.main(["arc", "new", "a1"])
    lifecycle.new(in_project, "a1")
    capsys.readouterr()  # drop the 'created arc' line
    rc = cli.main(["cannon", "status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("CANON")
    assert "draft (1)" in out


def test_cli_status_all_roster_wide(tmp_control_home, monkeypatch, tmp_path, capsys):
    from tide import roster

    # a second project on disk (new_arc unfolds its .tide/), registered in the roster
    proj = tmp_path / "proj-b"
    proj.mkdir()
    stream.new_arc(proj, "beta")
    roster.add(tmp_control_home, "proj-b", str(proj))

    monkeypatch.chdir(tmp_control_home)
    rc = cli.main(["status", "--all"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "=== proj-b" in out
    assert "01-beta" in out

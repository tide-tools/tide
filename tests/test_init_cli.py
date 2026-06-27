"""U9 integration — `tide init` / `tide version` / `tide help` through the CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from tide import __version__, cli, paths


@pytest.fixture
def in_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --- init ------------------------------------------------------------------

def test_cli_init_unfolds_control_home(in_empty, capsys):
    rc = cli.main(["init", "--name", "home"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "control-home" in out
    assert paths.tide_dir(in_empty).is_dir()
    assert paths.is_control_home(in_empty)
    assert (in_empty / "README.md").is_file()


def test_cli_init_project_only_scaffold(in_empty, capsys):
    rc = cli.main(["init", "--project", "--name", "demo"])
    assert rc == 0
    assert paths.tide_dir(in_empty).is_dir()
    assert not paths.roster_file(in_empty).exists()
    assert not (in_empty / "README.md").exists()


def test_cli_init_rerun_reports_nothing_to_create(in_empty, capsys):
    cli.main(["init", "--name", "home"])
    capsys.readouterr()
    rc = cli.main(["init", "--name", "home"])
    assert rc == 0
    assert "nothing to create" in capsys.readouterr().out


def test_cli_init_then_roster_and_status_work(in_empty, capsys):
    cli.main(["init", "--name", "home"])
    capsys.readouterr()
    assert cli.main(["roster", "add", "focus", "/p/focus"]) == 0
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    assert "STREAM" in capsys.readouterr().out


# --- version / help --------------------------------------------------------

def test_cli_version_command(capsys):
    rc = cli.main(["version"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "tide" in out
    assert __version__ in out


def test_cli_help_command_lists_groups(capsys):
    rc = cli.main(["help"])
    assert rc == 0
    out = capsys.readouterr().out.lower()
    for group in ("init", "roster", "status", "strictness", "arc", "canon", "contract", "version"):
        assert group in out

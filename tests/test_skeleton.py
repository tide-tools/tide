"""Scaffold smoke tests — prove the package imports, the CLI builds, the role
gate refuses workers, and the tmp_project fixture lays down a valid .tide/ tree.
Later units extend the suite; this file must stay green cumulatively.
"""

from __future__ import annotations

import pytest

import tide
from tide import cli


def test_package_exposes_version():
    assert isinstance(tide.__version__, str)
    assert tide.__version__  # non-empty


def test_parser_builds_and_has_groups():
    parser = cli.build_parser()
    help_text = parser.format_help()
    for group in ("init", "status", "arc", "canon", "contract", "candidate", "roster"):
        assert group in help_text


def test_version_flag_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "tide" in capsys.readouterr().out


def test_no_command_prints_help_returns_zero(capsys):
    rc = cli.main([])
    assert rc == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_install_hooks_is_live(tmp_path, monkeypatch, capsys):
    # 'install-hooks' went live in U10 (was the last stub) — outside a project it
    # now errors with the real 'no .tide/' message, not the old stub text. Run from
    # an isolated tmp cwd: since the U13 dogfood the repo IS a tide project, so cwd
    # must be a dir with no ancestor .tide/ to genuinely test the "outside" path.
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["install-hooks"])
    assert rc == 1
    assert "no .tide/" in capsys.readouterr().err


def test_default_role_is_worker(monkeypatch):
    monkeypatch.delenv("TIDE_ROLE", raising=False)
    assert cli.current_role() == cli.ROLE_WORKER


def test_require_orchestrator_refuses_worker(worker_role):
    with pytest.raises(SystemExit) as exc:
        cli.require_orchestrator("canon merge")
    assert exc.value.code != 0


def test_require_orchestrator_allows_orchestrator(orchestrator_role):
    # Should not raise.
    cli.require_orchestrator("canon merge")


def test_tmp_project_skeleton(tmp_project):
    tide_dir = tmp_project / ".tide"
    assert (tide_dir / "canon" / "CANON.md").is_file()
    assert (tide_dir / "canon" / "config").read_text(encoding="utf-8").strip() == "lang=en"
    assert (tide_dir / "arcs" / "candidates").is_dir()
    assert (tide_dir / "state" / "strictness").read_text(encoding="utf-8").strip() == "strict"


def test_control_home_has_roster(tmp_control_home):
    assert (tmp_control_home / "roster.md").is_file()
    assert (tmp_control_home / ".tide").is_dir()

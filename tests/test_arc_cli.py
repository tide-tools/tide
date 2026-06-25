"""U3 integration — `tide arc …` wired through the real CLI parser."""

from __future__ import annotations

import pytest

from tide import cli, fields, paths

from tests.conftest import strip_placeholders


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    """Run CLI commands as if cwd is the project root (paths resolves from cwd)."""
    monkeypatch.chdir(tmp_project)
    return tmp_project


def test_cli_arc_new_creates_entry(in_project):
    rc = cli.main(["arc", "new", "fix leak"])
    assert rc == 0
    entry = paths.arcs_dir(in_project) / "01-fix-leak"
    assert (entry / "arc.md").is_file()
    assert (entry / "workspace").is_dir()
    assert fields.read_field(entry / "arc.md", "cannon-rev")  # stamped on create


def test_cli_arc_new_goal_then_nested_arc(in_project):
    assert cli.main(["arc", "new-goal", "ship"]) == 0
    assert cli.main(["arc", "new", "wire-api", "-g", "ship"]) == 0
    sub = paths.arcs_dir(in_project) / "01-@ship" / "arcs" / "01-wire-api"
    assert (sub / "arc.md").is_file()


def test_cli_arc_close_guards_empty_output(in_project, capsys):
    cli.main(["arc", "new", "alpha"])
    rc = cli.main(["arc", "close", "alpha"])
    assert rc == 1
    assert "empty output" in capsys.readouterr().err


def test_cli_arc_close_and_reopen_roundtrip(in_project):
    cli.main(["arc", "new", "alpha"])
    (paths.arcs_dir(in_project) / "01-alpha" / "output" / "r.md").write_text("x", encoding="utf-8")
    strip_placeholders(paths.arcs_dir(in_project) / "01-alpha" / "arc.md")
    assert cli.main(["arc", "close", "alpha"]) == 0
    assert (paths.arcs_dir(in_project) / "__01-alpha__").is_dir()
    assert cli.main(["arc", "reopen", "alpha"]) == 0
    assert (paths.arcs_dir(in_project) / "01-alpha").is_dir()


def test_cli_arc_close_force(in_project):
    cli.main(["arc", "new", "alpha"])
    assert cli.main(["arc", "close", "-f", "alpha"]) == 0
    assert (paths.arcs_dir(in_project) / "__01-alpha__").is_dir()


def test_cli_arc_close_refuses_leftover_placeholders(in_project, capsys):
    # F5: filled output but scaffolded arc.md → close refuses with a clear message.
    cli.main(["arc", "new", "alpha"])
    (paths.arcs_dir(in_project) / "01-alpha" / "output" / "r.md").write_text("x", encoding="utf-8")
    capsys.readouterr()
    rc = cli.main(["arc", "close", "alpha"])
    assert rc == 1
    assert "placeholder" in capsys.readouterr().err
    assert (paths.arcs_dir(in_project) / "01-alpha").is_dir()  # not sealed


def test_cli_arc_supersede_links_and_seeds(in_project):
    cli.main(["arc", "new", "old-plan"])
    assert cli.main(["arc", "supersede", "old-plan", "new-plan"]) == 0
    new = paths.arcs_dir(in_project) / "02-new-plan"
    assert (paths.arcs_dir(in_project) / "__01-old-plan__").is_dir()
    assert fields.read_field(new / "arc.md", "supersedes") == "old-plan"
    assert (new / "input" / "from-old-plan.md").is_file()


def test_cli_arc_rm_removes_stray_arc(in_project):
    # F8: `tide arc rm` deletes a stray probe arc (empty output, no -f needed).
    cli.main(["arc", "new", "probe"])
    assert cli.main(["arc", "rm", "probe"]) == 0
    assert not (paths.arcs_dir(in_project) / "01-probe").exists()


def test_cli_arc_rm_guards_non_empty_output(in_project, capsys):
    # F8: a non-empty output/ refuses without -f.
    cli.main(["arc", "new", "beta"])
    (paths.arcs_dir(in_project) / "01-beta" / "output" / "r.md").write_text("x", encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["arc", "rm", "beta"]) == 1
    assert "without -f" in capsys.readouterr().err
    assert (paths.arcs_dir(in_project) / "01-beta").is_dir()
    assert cli.main(["arc", "rm", "-f", "beta"]) == 0
    assert not (paths.arcs_dir(in_project) / "01-beta").exists()


def test_cli_arc_abort_alias_removes(in_project):
    # F8: `abort` is an alias of `rm`.
    cli.main(["arc", "new", "probe"])
    assert cli.main(["arc", "abort", "probe"]) == 0
    assert not (paths.arcs_dir(in_project) / "01-probe").exists()


def test_cli_arc_status_renders_stream(in_project, capsys):
    cli.main(["arc", "new", "alpha"])
    capsys.readouterr()  # drop the 'created arc' line
    rc = cli.main(["arc", "status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("STREAM")
    assert "01-alpha" in out

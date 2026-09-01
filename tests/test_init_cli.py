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


# --- work 49: the harness comes up with the install -------------------------

def test_cli_init_wires_the_hooks_and_delivers_the_skills(in_empty, capsys, monkeypatch):
    """A person who ran `tide init` and opened claude by hand used to get a
    session with no start-gate, no handoff flip and no skills. One gesture now."""
    import json

    from tide import harness

    skills = in_empty / "skills-target"
    monkeypatch.setenv("TIDE_SKILLS_DIR", str(skills))
    rc = cli.main(["init", "--name", "home"])
    assert rc == 0

    settings = json.loads(
        (in_empty / ".claude" / "settings.json").read_text(encoding="utf-8"))
    hooks = settings["hooks"]
    commands = [
        h["command"]
        for groups in hooks.values() for g in groups for h in g["hooks"]
    ]
    for cmd in (harness.SESSION_START_CMD, harness.SESSION_END_CMD,
                harness.EDIT_GATE_CMD, harness.ROLE_GATE_CMD,
                harness.HANDOFF_CONFIRM_CMD, harness.OFFLOAD_NUDGE_CMD):
        assert cmd in commands

    # the cycle's skills are on disk, as symlinks into the checkout
    assert (skills / "handoff" / "SKILL.md").is_file()
    assert (skills / "offload").is_symlink()
    out = capsys.readouterr().out
    assert "хуки Claude" in out and "скиллы" in out


def test_cli_init_never_touches_a_foreign_skill(in_empty, monkeypatch):
    """The invariant: only OUR names, never the person's own skills."""
    skills = in_empty / "skills-target"
    (skills / "money-audit").mkdir(parents=True)
    (skills / "money-audit" / "SKILL.md").write_text("личный", encoding="utf-8")
    (skills / "handoff").mkdir()
    (skills / "handoff" / "SKILL.md").write_text("свой руками", encoding="utf-8")
    monkeypatch.setenv("TIDE_SKILLS_DIR", str(skills))

    assert cli.main(["init", "--name", "home"]) == 0
    # personal skill untouched, and a hand-made dir at OUR name is not clobbered
    assert (skills / "money-audit" / "SKILL.md").read_text(encoding="utf-8") == "личный"
    assert (skills / "handoff" / "SKILL.md").read_text(encoding="utf-8") == "свой руками"


def test_cli_init_survives_a_harness_that_cannot_be_wired(in_empty, monkeypatch, capsys):
    """The .tide/ must land even when hooks/skills fail — init never dies on them."""
    from tide import init_home

    def boom(*a, **kw):
        raise OSError("no ~/.claude here")

    monkeypatch.setattr("tide.harness.install_hooks", boom)
    monkeypatch.setattr("tide.skills_install.install_skills", boom)
    assert cli.main(["init", "--name", "home"]) == 0
    assert paths.tide_dir(in_empty).is_dir()

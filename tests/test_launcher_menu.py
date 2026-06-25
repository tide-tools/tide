"""U11 integration — launcher.menu: list, pick N, launch seeded sessions (dry-run)."""

from __future__ import annotations

import pytest

from tide import cli, roster
from tide.init_home import scaffold_project
from tide.launcher import menu


# --- selection parsing (pure) ----------------------------------------------

def test_parse_selection_single():
    assert menu.parse_selection("2", 3) == [2]


def test_parse_selection_comma_and_space():
    assert menu.parse_selection("1,3", 3) == [1, 3]
    assert menu.parse_selection("3 1", 3) == [1, 3]  # sorted + unique


def test_parse_selection_all():
    assert menu.parse_selection("all", 3) == [1, 2, 3]


def test_parse_selection_dedupes():
    assert menu.parse_selection("2,2,1", 3) == [1, 2]


@pytest.mark.parametrize("raw", ["", "  ", "x", "0", "4"])
def test_parse_selection_rejects_bad(raw):
    with pytest.raises(menu.MenuError):
        menu.parse_selection(raw, 3)


# --- render + select -------------------------------------------------------

def test_render_menu_numbers_projects():
    out = menu.render_menu([{"name": "focus", "path": "/p/focus"}])
    assert "1) focus → /p/focus" in out


def test_render_menu_empty():
    assert "roster is empty" in menu.render_menu([])


def test_select_entries_resolves_picks():
    entries = [
        {"name": "a", "path": "/a"},
        {"name": "b", "path": "/b"},
        {"name": "c", "path": "/c"},
    ]
    assert menu.select_entries(entries, "1,3") == [entries[0], entries[2]]


def test_select_entries_empty_roster_raises():
    with pytest.raises(menu.MenuError):
        menu.select_entries([], "1")


# --- launch (dry-run, no terminal opened) ----------------------------------

@pytest.fixture
def home_with_project(tmp_control_home):
    """A control-home whose roster points at a real scaffolded tide project."""
    proj = tmp_control_home / "proj"
    proj.mkdir()
    scaffold_project(proj, name="proj")
    roster.add(tmp_control_home, "proj", str(proj))
    return tmp_control_home, proj


def test_launch_entries_dry_run_builds_tmux_command(home_with_project):
    home, proj = home_with_project
    entries = menu.list_entries(home)
    results = menu.launch_entries(
        entries, control_home=home, adapter_name="tmux", dry_run=True
    )
    assert len(results) == 1
    res = results[0]
    assert res.ok is True
    # the new-window command is cwd'd into the picked project
    new_window = res.commands[0]
    assert new_window[:2] == ["tmux", "new-window"]
    assert str(proj) in new_window
    # the window runs the SCOPED claude session: strict MCP, no global servers,
    # seed delivered by reference (no separate send-keys command anymore).
    assert len(res.commands) == 1
    assert "claude" in new_window
    assert "--strict-mcp-config" in new_window
    assert "--mcp-config" not in new_window  # lean default → no global MCP
    assert "--append-system-prompt" in new_window


def test_build_launch_is_scoped_lean_by_default(home_with_project):
    home, proj = home_with_project
    command = menu.build_launch(proj, control_home=home, dry_run=True)
    assert command[0] == "claude"
    assert "--strict-mcp-config" in command
    assert "--mcp-config" not in command
    assert command[-2:] == ["--append-system-prompt", "@<seed-file>"]


def test_launch_entries_default_adapter_is_orca(home_with_project):
    home, _ = home_with_project
    entries = menu.list_entries(home)
    results = menu.launch_entries(entries, control_home=home, dry_run=True)
    assert results[0].commands[0][0] == "osascript"


def test_resolve_adapter_name_override_wins(home_with_project):
    home, _ = home_with_project
    assert menu.resolve_adapter_name(home, "tmux") == "tmux"
    assert menu.resolve_adapter_name(home, None) is None  # no settings pin


# --- through the CLI -------------------------------------------------------

def test_cli_menu_dry_run_launches_picked_project(home_with_project, monkeypatch, capsys):
    home, _ = home_with_project
    monkeypatch.chdir(home)
    rc = cli.main(["menu", "--pick", "1", "--adapter", "tmux", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "proj" in out and "ok" in out
    # criterion 4: the human SEES the full scoped command on dry-run
    assert "scoped command:" in out
    assert "claude --strict-mcp-config" in out
    assert "--append-system-prompt @<seed-file>" in out


def test_cli_menu_empty_roster_is_a_note(tmp_control_home, monkeypatch, capsys):
    monkeypatch.chdir(tmp_control_home)
    rc = cli.main(["menu", "--pick", "1"])
    assert rc == 0
    assert "roster is empty" in capsys.readouterr().out


def test_cli_bare_still_prints_help(capsys):
    # U11 adds `tide menu` but bare `tide` must keep printing help (U-skeleton rule).
    rc = cli.main([])
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "usage" in out
    assert "menu" in out  # the new command shows up in help

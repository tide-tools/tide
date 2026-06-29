"""U11 integration — launcher.menu: list, pick N, launch seeded sessions (dry-run)."""

from __future__ import annotations

from pathlib import Path

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


def test_active_entries_filters_archived():
    entries = [
        {"name": "a", "path": "/a"},
        {"name": "b", "path": "/b", "status": "archived"},
        {"name": "c", "path": "/c"},
    ]
    assert menu.active_entries(entries) == [entries[0], entries[2]]


def test_render_menu_marks_archived_when_shown():
    out = menu.render_menu([{"name": "old", "path": "/p/old", "status": "archived"}])
    assert "[archived]" in out


def test_cli_menu_hides_archived_by_default(home_with_project, monkeypatch, capsys):
    home, proj = home_with_project
    roster.add(home, "old", str(proj), status="archived")
    monkeypatch.chdir(home)
    rc = cli.main(["menu", "--pick", "1", "--adapter", "tmux", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    # pick #1 is the active project; archived 'old' is not offered
    assert "proj" in out
    assert "old" not in out


def test_cli_menu_all_includes_archived(home_with_project, monkeypatch, capsys):
    home, proj = home_with_project
    roster.add(home, "old", str(proj), status="archived")
    monkeypatch.chdir(home)
    rc = cli.main(["menu", "--all", "--pick", "2", "--adapter", "tmux", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "old" in out


def test_cli_menu_all_archived_notes_when_no_active(tmp_control_home, monkeypatch, capsys):
    roster.add(tmp_control_home, "old", "/p/old", status="archived")
    monkeypatch.chdir(tmp_control_home)
    rc = cli.main(["menu", "--pick", "1", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no active projects" in out


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


def test_launch_preview_returns_name_and_scoped_command(home_with_project):
    home, _ = home_with_project
    entries = menu.list_entries(home)
    preview = menu.launch_preview(entries, control_home=home)
    assert len(preview) == 1
    name, command = preview[0]
    assert name == "proj"
    assert command.startswith("claude ")
    assert "--strict-mcp-config" in command
    assert command.endswith("--append-system-prompt @<seed-file>")


def test_cli_menu_debug_prints_scoped_command(home_with_project, monkeypatch, capsys):
    home, _ = home_with_project
    monkeypatch.chdir(home)
    # --debug paired with --dry-run keeps the test from opening a real terminal,
    # while still proving --debug surfaces the full command before launch.
    rc = cli.main(["menu", "--pick", "1", "--adapter", "tmux", "--debug", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "proj scoped command:" in out
    assert "claude --dangerously-skip-permissions --strict-mcp-config" in out


# --- thread (нить) selection -----------------------------------------------

def test_parse_thread_choice_new_on_empty_or_keyword():
    assert menu.parse_thread_choice("", 2) == menu.THREAD_NEW
    assert menu.parse_thread_choice("new", 2) == menu.THREAD_NEW
    assert menu.parse_thread_choice("3", 2) == menu.THREAD_NEW  # the count+1 row


def test_parse_thread_choice_index_and_bad():
    assert menu.parse_thread_choice("2", 3) == 2
    with pytest.raises(menu.MenuError):
        menu.parse_thread_choice("9", 3)
    with pytest.raises(menu.MenuError):
        menu.parse_thread_choice("x", 3)


def test_list_threads_only_threads(home_with_project):
    _, proj = home_with_project
    from tide.arc import stream
    stream.new_arc(proj, "just-work")
    stream.new_thread(proj, "morning")
    threads = menu.list_threads(proj)
    assert [t["slug"] for t in threads] == ["morning"]


def test_render_thread_menu_has_new_row(home_with_project):
    _, proj = home_with_project
    from tide.arc import stream
    stream.new_thread(proj, "morning")
    out = menu.render_thread_menu("proj", menu.list_threads(proj))
    assert "1) morning" in out
    assert "+ new thread" in out


def test_create_thread_returns_slug_and_persists(home_with_project):
    _, proj = home_with_project
    from tide.arc import stream
    ref = menu.create_thread(proj, "deep work")
    assert ref == "deep-work"
    assert [t["slug"] for t in menu.list_threads(proj)] == ["deep-work"]
    assert menu.create_thread(proj, "  ") is None  # blank → skip


def test_build_launch_binds_thread_into_seed(home_with_project):
    home, proj = home_with_project
    from tide.arc import stream
    stream.new_thread(proj, "my session")
    # a real (non-dry) build persists a seed carrying the bound thread passport
    command = menu.build_launch(proj, control_home=home, arc_ref="my-session")
    seed_arg = command[-1]
    assert seed_arg.startswith("@")
    seed_text = Path(seed_arg[1:]).read_text(encoding="utf-8")
    assert "Active arc" in seed_text and "my-session" in seed_text


def test_cli_menu_new_thread_creates_and_binds(home_with_project, monkeypatch, capsys):
    home, proj = home_with_project
    monkeypatch.chdir(home)
    rc = cli.main(
        ["menu", "--pick", "1", "--new-thread", "kickoff",
         "--adapter", "tmux", "--debug", "--dry-run"]
    )
    assert rc == 0
    # the thread now exists in the project, created by the picker
    assert [t["slug"] for t in menu.list_threads(proj)] == ["kickoff"]


def test_build_launch_skips_permissions_by_default(home_with_project):
    home, proj = home_with_project
    command = menu.build_launch(proj, control_home=home, dry_run=True)
    assert menu.SKIP_PERMISSIONS in command
    assert command[1] == menu.SKIP_PERMISSIONS  # right after the program


def test_build_launch_no_skip_permissions_opt_out(home_with_project):
    home, proj = home_with_project
    command = menu.build_launch(
        proj, control_home=home, skip_permissions=False, dry_run=True
    )
    assert menu.SKIP_PERMISSIONS not in command


def test_cli_menu_no_skip_permissions_flag(home_with_project, monkeypatch, capsys):
    home, _ = home_with_project
    monkeypatch.chdir(home)
    rc = cli.main(
        ["menu", "--pick", "1", "--adapter", "tmux", "--dry-run", "--no-skip-permissions"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "--dangerously-skip-permissions" not in out
    assert "claude --strict-mcp-config" in out  # back to the lean shape


def test_build_launch_is_scoped_lean_by_default(home_with_project):
    home, proj = home_with_project
    command = menu.build_launch(
        proj, control_home=home, skip_permissions=False, dry_run=True
    )
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
    assert "claude --dangerously-skip-permissions --strict-mcp-config" in out
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

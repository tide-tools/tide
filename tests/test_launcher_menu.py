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


# --- prism (призма) + session selection ------------------------------------

def test_parse_pick_zero_and_keywords_are_new():
    assert menu.parse_pick("0", 2) == menu.PICK_NEW
    assert menu.parse_pick("", 2) == menu.PICK_NEW
    assert menu.parse_pick("new", 2) == menu.PICK_NEW


def test_parse_pick_index_and_bad():
    assert menu.parse_pick("2", 3) == 2
    with pytest.raises(menu.MenuError):
        menu.parse_pick("9", 3)
    with pytest.raises(menu.MenuError):
        menu.parse_pick("x", 3)


def test_list_prisms_only_prisms(home_with_project):
    _, proj = home_with_project
    from tide.arc import stream
    stream.new_arc(proj, "just-work")
    stream.new_prism(proj, "morning")
    prisms = menu.list_prisms(proj)
    assert [p["slug"] for p in prisms] == ["morning"]


def test_render_prism_menu_zero_is_new(home_with_project):
    _, proj = home_with_project
    from tide.arc import stream
    stream.new_prism(proj, "morning")
    out = menu.render_prism_menu("proj", menu.list_prisms(proj))
    assert "0) + new prism" in out
    assert "1) morning" in out


def test_render_session_menu_shows_lineage(home_with_project):
    _, proj = home_with_project
    from tide.arc import stream
    stream.new_prism(proj, "prz")
    stream.new_session(proj, "prz", "first")
    stream.new_session(proj, "prz", "second")
    out = menu.render_session_menu("prz", menu.list_sessions(proj, "prz"))
    assert "0) + new session" in out
    assert "1) second" in out  # newest-first: the latest session leads
    assert "(from first)" in out  # session 2 lineage


def test_resolve_session_new_prism_and_session(home_with_project):
    _, proj = home_with_project
    bound = menu.resolve_session(
        proj, "proj", new_prism="deep work", new_session="kickoff"
    )
    assert bound["prism"] == "deep-work"
    assert bound["arc_ref"] == "kickoff"
    assert "## cursor" in (bound["arc_text"] or "")


def test_new_session_pins_claude_session_id_for_later_resume(home_with_project):
    home, proj = home_with_project
    bound = menu.resolve_session(proj, "proj", new_prism="prz", new_session="kickoff")
    assert bound["resume"] is False
    assert bound["session_id"]  # a fresh uuid was minted + persisted
    cmd = menu.build_launch(
        proj, control_home=home, dry_run=True,
        session_id=bound["session_id"], resume=False,
    )
    assert "--session-id" in cmd and bound["session_id"] in cmd


def test_continue_session_with_id_resumes_same_conversation(home_with_project):
    home, proj = home_with_project
    from tide.arc import stream
    from tide import fields
    stream.new_prism(proj, "prz")
    sess = stream.new_session(proj, "prz", "work")
    fields.set_field(sess / "arc.md", "claude-session", "abc-123")
    bound = menu.resolve_session(proj, "proj", prism_ref="prz", session_ref="work")
    assert bound["resume"] is True
    assert bound["session_id"] == "abc-123"
    cmd = menu.build_launch(proj, control_home=home, dry_run=True, session_id="abc-123", resume=True)
    # resume is wrapped so it falls back to a fresh launch if the convo is gone
    assert cmd[0] == "sh" and cmd[1] == "-c"
    shell = cmd[2]
    assert "claude --dangerously-skip-permissions --resume abc-123" in shell
    assert " || " in shell  # fallback to a fresh seeded launch
    assert "--session-id abc-123" in shell  # the fallback re-pins the same id


def test_resume_reapplies_scoped_mcp_config(home_with_project):
    # Regression: a project with a scoped --mcp-config (e.g. mitehq's linear-mite)
    # must keep that MCP on RESUME, not just on a fresh launch. A bare
    # --strict-mcp-config on resume used to silently drop the scoped servers.
    from tide import mcp

    home, proj = home_with_project
    mcp.add_server(proj, "linear-mite", "https://mcp.linear.app/mcp", http=True)
    cmd = menu.build_launch(
        proj, control_home=home, dry_run=True, session_id="abc-123", resume=True
    )
    shell = cmd[2]
    resume_part = shell.split(" || ", 1)[0]  # the `claude --resume …` half
    assert "--resume abc-123" in resume_part
    assert "--strict-mcp-config" in resume_part
    assert "--mcp-config" in resume_part  # the scoped server survives resume
    assert "mcp.json" in resume_part


def test_navigate_back_from_prism_returns_to_type(home_with_project, monkeypatch):
    home, proj = home_with_project
    from tide.arc import stream
    from tide.launcher import select as sel
    stream.new_prism(proj, "prz")
    stream.new_session(proj, "prz", "one")
    # project=0, type=0(Task), prism=BACK (→ back to type), type=0(Task), prism=0, session=0
    seq = iter([0, 0, sel.BACK, 0, 0, 0])
    monkeypatch.setattr(menu.select, "select", lambda *a, **k: next(seq))
    entry, bound = menu.navigate_interactive([{"name": "proj", "path": str(proj)}])
    assert entry["name"] == "proj"
    assert bound["prism"] == "prz"
    assert bound["kind"] == "prism"
    assert bound["arc_ref"] == "one"


# --- routine (рутина) flow + the Type step ---------------------------------

def test_list_routines_only_routines(home_with_project):
    _, proj = home_with_project
    from tide.arc import stream
    stream.new_arc(proj, "just-work")
    stream.new_prism(proj, "morning")
    stream.new_routine(proj, "invite-codes")
    routines = menu.list_routines(proj)
    assert [r["slug"] for r in routines] == ["invite-codes"]


def test_routine_label_shows_gear_marker(home_with_project):
    _, proj = home_with_project
    from tide.arc import stream
    stream.new_routine(proj, "invite-codes")
    label = menu._routine_label(menu.list_routines(proj)[0])
    assert label.startswith(menu.ROUTINE_MARKER)  # ⚙ distinguishes routines from tasks
    assert "invite-codes" in label


def test_navigate_type_routes_task(home_with_project, monkeypatch):
    _, proj = home_with_project
    from tide.arc import stream
    stream.new_prism(proj, "prz")
    stream.new_session(proj, "prz", "one")
    # project=0, type=0(Task), prism=0, session=0
    seq = iter([0, 0, 0, 0])
    monkeypatch.setattr(menu.select, "select", lambda *a, **k: next(seq))
    entry, bound = menu.navigate_interactive([{"name": "proj", "path": str(proj)}])
    assert bound["kind"] == "prism"
    assert bound["prism"] == "prz"
    assert bound["arc_ref"] == "one"


def test_navigate_type_routes_routine_run(home_with_project, monkeypatch):
    _, proj = home_with_project
    from tide.arc import stream
    stream.new_routine(proj, "invite-codes")
    stream.new_session(proj, "invite-codes", "run-one")
    # project=0, type=1(Routine), routine=0, run=0
    seq = iter([0, 1, 0, 0])
    monkeypatch.setattr(menu.select, "select", lambda *a, **k: next(seq))
    entry, bound = menu.navigate_interactive([{"name": "proj", "path": str(proj)}])
    assert bound["kind"] == "routine"
    assert bound["prism"] == "invite-codes"  # container slug rides the prism slot
    assert bound["arc_ref"] == "run-one"


def test_navigate_back_from_routine_returns_to_type(home_with_project, monkeypatch):
    _, proj = home_with_project
    from tide.arc import stream
    from tide.launcher import select as sel
    stream.new_routine(proj, "invite-codes")
    stream.new_session(proj, "invite-codes", "run-one")
    stream.new_prism(proj, "prz")
    stream.new_session(proj, "prz", "one")
    # project=0, type=1(Routine), routine=BACK (→ back to type), type=0(Task), prism=0, session=0
    seq = iter([0, 1, sel.BACK, 0, 0, 0])
    monkeypatch.setattr(menu.select, "select", lambda *a, **k: next(seq))
    entry, bound = menu.navigate_interactive([{"name": "proj", "path": str(proj)}])
    assert bound["kind"] == "prism"
    assert bound["arc_ref"] == "one"


def test_navigate_back_from_type_returns_to_project(home_with_project, monkeypatch):
    _, proj = home_with_project
    from tide.launcher import select as sel
    # project=0, type=BACK (→ back to project), project=BACK (→ cancel)
    seq = iter([0, sel.BACK, sel.BACK])
    monkeypatch.setattr(menu.select, "select", lambda *a, **k: next(seq))
    assert menu.navigate_interactive([{"name": "proj", "path": str(proj)}]) is None


def test_resolve_session_new_routine_binds_run(home_with_project):
    _, proj = home_with_project
    bound = menu.resolve_session(
        proj, "proj", new_routine="invite codes", new_session="run"
    )
    assert bound["kind"] == "routine"
    assert bound["prism"] == "invite-codes"
    assert bound["arc_ref"] == "run"
    from tide.arc import stream
    assert [r["slug"] for r in menu.list_routines(proj)] == ["invite-codes"]


def test_routine_run_seed_frames_procedure(home_with_project):
    home, proj = home_with_project
    from tide.arc import stream
    stream.new_routine(proj, "invite-codes")
    run = stream.new_session(proj, "invite-codes", "run one")
    arc_text = (run / "arc.md").read_text(encoding="utf-8")
    command = menu.build_launch(
        proj, control_home=home, arc_ref="run-one", arc_text=arc_text,
        prism_name="invite-codes", container_kind="routine",
    )
    seed_text = Path(command[-1][1:]).read_text(encoding="utf-8")
    assert "Active routine run" in seed_text
    assert "routine: invite-codes" in seed_text


def test_tab_title_marks_routine_run():
    entry = {"name": "proj", "session": {
        "prism": "invite-codes", "kind": "routine",
        "arc_ref": "run-one", "session_index": "01", "session_title": "",
    }}
    title = menu._tab_title(entry)
    assert title.startswith(menu.ROUTINE_MARKER)
    assert "invite-codes" in title


def test_cli_menu_new_routine_run_creates_and_binds(home_with_project, monkeypatch, capsys):
    home, proj = home_with_project
    monkeypatch.chdir(home)
    rc = cli.main(
        ["menu", "--pick", "1", "--new-routine", "invite-codes", "--new-session", "run",
         "--adapter", "tmux", "--debug", "--dry-run"]
    )
    assert rc == 0
    assert [r["slug"] for r in menu.list_routines(proj)] == ["invite-codes"]
    assert [s["slug"] for s in menu.list_sessions(proj, "invite-codes")] == ["run"]


def test_navigate_back_from_project_cancels(home_with_project, monkeypatch):
    _, proj = home_with_project
    from tide.launcher import select as sel
    monkeypatch.setattr(menu.select, "select", lambda *a, **k: sel.BACK)
    assert menu.navigate_interactive([{"name": "proj", "path": str(proj)}]) is None


def test_build_launch_binds_session_into_seed(home_with_project):
    home, proj = home_with_project
    from tide.arc import stream
    stream.new_prism(proj, "prz")
    sess = stream.new_session(proj, "prz", "work one")
    arc_text = (sess / "arc.md").read_text(encoding="utf-8")
    command = menu.build_launch(
        proj, control_home=home, arc_ref="work-one", arc_text=arc_text, prism_name="prz"
    )
    seed_arg = command[-1]
    assert seed_arg.startswith("@")
    seed_text = Path(seed_arg[1:]).read_text(encoding="utf-8")
    assert "Active session" in seed_text and "prism: prz" in seed_text and "cursor" in seed_text


def test_cli_menu_new_prism_session_creates_and_binds(home_with_project, monkeypatch, capsys):
    home, proj = home_with_project
    monkeypatch.chdir(home)
    rc = cli.main(
        ["menu", "--pick", "1", "--new-prism", "kickoff", "--new-session", "start",
         "--adapter", "tmux", "--debug", "--dry-run"]
    )
    assert rc == 0
    assert [p["slug"] for p in menu.list_prisms(proj)] == ["kickoff"]
    assert [s["slug"] for s in menu.list_sessions(proj, "kickoff")] == ["start"]


def test_list_sessions_newest_first(home_with_project):
    """The session picker surfaces sessions newest-first (handoff/fresh on top).

    The on-disk substream is numbered NN ascending (oldest first — stream-level
    chaining relies on that); the picker reverses it so the freshest session — the
    one a handoff just seeded — sits at the top, with older ones aging downward.
    """
    from tide.arc import stream
    _, proj = home_with_project
    stream.new_prism(proj, "kickoff")
    stream.new_session(proj, "kickoff", "one")
    stream.new_session(proj, "kickoff", "two")
    stream.new_session(proj, "kickoff", "three")
    slugs = [s["slug"] for s in menu.list_sessions(proj, "kickoff")]
    assert slugs == ["three", "two", "one"]  # newest first, oldest last


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
    assert results[0].commands[0][:3] == ["orca", "terminal", "create"]


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

"""U11 integration — launcher.menu: list, pick N, launch seeded sessions (dry-run)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import fill_entry

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


# --- thread (тред) + session selection ------------------------------------

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


def test_list_threads_only_threads(home_with_project):
    _, proj = home_with_project
    from tide.arc import stream
    stream.new_arc(proj, "just-work")
    fill_entry(stream.new_thread(proj, "morning"))
    threads = menu.list_threads(proj)
    assert [p["slug"] for p in threads] == ["morning"]


def test_render_thread_menu_zero_is_new(home_with_project):
    _, proj = home_with_project
    from tide.arc import stream
    fill_entry(stream.new_thread(proj, "morning"))
    out = menu.render_thread_menu("proj", menu.list_threads(proj))
    assert "0) + new thread" in out
    assert "1) morning" in out


def test_render_session_menu_shows_lineage(home_with_project):
    _, proj = home_with_project
    from tide.arc import stream
    fill_entry(stream.new_thread(proj, "prz"))
    stream.new_session(proj, "prz", "first")
    stream.new_session(proj, "prz", "second")
    out = menu.render_session_menu("prz", menu.list_sessions(proj, "prz"))
    assert "0) + new session" in out
    assert "1) second" in out  # newest-first: the latest session leads
    assert "(from first)" in out  # session 2 lineage


def test_resolve_session_new_thread_and_session(home_with_project):
    _, proj = home_with_project
    bound = menu.resolve_session(
        proj, "proj", new_thread="deep work", new_session="kickoff"
    )
    assert bound["thread"] == "deep-work"
    assert bound["arc_ref"] == "kickoff"
    assert "## cursor" in (bound["arc_text"] or "")


def test_new_session_pins_claude_session_id_for_later_resume(home_with_project):
    home, proj = home_with_project
    bound = menu.resolve_session(proj, "proj", new_thread="prz", new_session="kickoff")
    assert bound["resume"] is False
    assert bound["session_id"]  # a fresh uuid was minted + persisted
    cmd = menu.build_launch(
        proj, control_home=home, dry_run=True,
        session_id=bound["session_id"], resume=False,
    )
    assert "--session-id" in cmd and bound["session_id"] in cmd


def _persist_convo(proj, session_id):
    """Fake a claude conversation on disk so the resume-gate chooses --resume."""
    import os
    base = Path(os.environ["CLAUDE_CONFIG_DIR"])
    encoded = str(proj).replace("/", "-").replace(".", "-")
    f = base / "projects" / encoded / "{0}.jsonl".format(session_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("{}\n", encoding="utf-8")


def test_continue_session_with_id_resumes_same_conversation(home_with_project):
    home, proj = home_with_project
    from tide.arc import stream
    from tide import fields
    fill_entry(stream.new_thread(proj, "prz"))
    sess = stream.new_session(proj, "prz", "work")
    fields.set_field(sess / "arc.md", "claude-session", "abc-123")
    _persist_convo(proj, "abc-123")  # the conversation exists → resume, not fresh
    bound = menu.resolve_session(proj, "proj", thread_ref="prz", session_ref="work")
    assert bound["resume"] is True
    assert bound["session_id"] == "abc-123"


def test_never_engaged_session_launches_fresh(home_with_project):
    """A pinned-but-never-persisted id → FRESH launch, not resume (no 'No conversation found').

    The resume-gate checks whether claude actually has the conversation on disk; a
    session that was pinned but never really opened has none, so we launch fresh
    (keeping the id pinned for the next, engaged, entry).
    """
    home, proj = home_with_project
    from tide.arc import stream
    from tide import fields
    fill_entry(stream.new_thread(proj, "prz"))
    sess = stream.new_session(proj, "prz", "work")
    fields.set_field(sess / "arc.md", "claude-session", "ghost-999")
    # no _persist_convo → CLAUDE_CONFIG_DIR has no conversation for ghost-999
    bound = menu.resolve_session(proj, "proj", thread_ref="prz", session_ref="work")
    assert bound["resume"] is False       # never engaged → fresh, no scary resume error
    assert bound["session_id"] == "ghost-999"  # id kept, pinned for next time
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


def test_navigate_back_from_thread_returns_to_project(home_with_project, monkeypatch):
    home, proj = home_with_project
    from tide.arc import stream
    from tide.launcher import select as sel
    fill_entry(stream.new_thread(proj, "prz"))
    stream.new_session(proj, "prz", "one")
    # no offers → no type step: project=0, thread=BACK (→ back to project),
    # project=0, thread=0, session=0
    seq = iter([0, sel.BACK, 0, 0, 0])
    monkeypatch.setattr(menu.select, "select", lambda *a, **k: next(seq))
    entry, bound = menu.navigate_interactive([{"name": "proj", "path": str(proj)}])
    assert entry["name"] == "proj"
    assert bound["thread"] == "prz"
    assert bound["arc_ref"] == "one"


# --- navigation (project → thread → session) -------------------------------

def test_navigate_routes_into_thread(home_with_project, monkeypatch):
    _, proj = home_with_project
    from tide.arc import stream
    fill_entry(stream.new_thread(proj, "prz"))
    stream.new_session(proj, "prz", "one")
    # no offers → no type step: project=0, thread=0, session=0
    seq = iter([0, 0, 0])
    monkeypatch.setattr(menu.select, "select", lambda *a, **k: next(seq))
    entry, bound = menu.navigate_interactive([{"name": "proj", "path": str(proj)}])
    assert bound["thread"] == "prz"
    assert bound["arc_ref"] == "one"


def test_navigate_back_out_of_thread_then_cancel(home_with_project, monkeypatch):
    _, proj = home_with_project
    from tide.launcher import select as sel
    # project=0, thread=BACK (→ back to project), project=BACK (→ cancel)
    seq = iter([0, sel.BACK, sel.BACK])
    monkeypatch.setattr(menu.select, "select", lambda *a, **k: next(seq))
    assert menu.navigate_interactive([{"name": "proj", "path": str(proj)}]) is None


def test_navigate_back_from_project_cancels(home_with_project, monkeypatch):
    _, proj = home_with_project
    from tide.launcher import select as sel
    monkeypatch.setattr(menu.select, "select", lambda *a, **k: sel.BACK)
    assert menu.navigate_interactive([{"name": "proj", "path": str(proj)}]) is None


def test_build_launch_binds_session_into_seed(home_with_project):
    home, proj = home_with_project
    from tide.arc import stream
    fill_entry(stream.new_thread(proj, "prz"))
    sess = stream.new_session(proj, "prz", "work one")
    arc_text = (sess / "arc.md").read_text(encoding="utf-8")
    command = menu.build_launch(
        proj, control_home=home, arc_ref="work-one", arc_text=arc_text, thread_name="prz"
    )
    seed_arg = command[-1]
    assert seed_arg.startswith("@")
    seed_text = Path(seed_arg[1:]).read_text(encoding="utf-8")
    assert "Active session" in seed_text and "thread: prz" in seed_text and "cursor" in seed_text


def test_cli_menu_new_thread_session_creates_and_binds(home_with_project, monkeypatch, capsys):
    home, proj = home_with_project
    monkeypatch.chdir(home)
    rc = cli.main(
        ["menu", "--pick", "1", "--new-thread", "kickoff", "--new-session", "start",
         "--adapter", "tmux", "--debug", "--dry-run"]
    )
    assert rc == 0
    assert [p["slug"] for p in menu.list_threads(proj)] == ["kickoff"]
    assert [s["slug"] for s in menu.list_sessions(proj, "kickoff")] == ["start"]


def test_list_sessions_newest_first(home_with_project):
    """The session picker surfaces sessions newest-first (handoff/fresh on top).

    The on-disk substream is numbered NN ascending (oldest first — stream-level
    chaining relies on that); the picker reverses it so the freshest session — the
    one a handoff just seeded — sits at the top, with older ones aging downward.
    """
    from tide.arc import stream
    _, proj = home_with_project
    fill_entry(stream.new_thread(proj, "kickoff"))
    stream.new_session(proj, "kickoff", "one")
    stream.new_session(proj, "kickoff", "two")
    stream.new_session(proj, "kickoff", "three")
    slugs = [s["slug"] for s in menu.list_sessions(proj, "kickoff")]
    assert slugs == ["three", "two", "one"]  # newest first, oldest last


def test_pick_session_empty_thread_auto_creates_first(home_with_project):
    """Thread law: an EMPTY thread's first session is born automatically.

    The first session begins the narrative — no '+ new session' prompt needed.
    """
    _, proj = home_with_project
    from tide.arc import stream
    fill_entry(stream.new_thread(proj, "kickoff"))
    slug_, path_, is_new = menu._pick_session_interactive(proj, "kickoff")
    assert is_new is True
    assert slug_  # a first session was created
    assert [s["slug"] for s in menu.list_sessions(proj, "kickoff")] == [slug_]


def test_pick_session_nonempty_thread_is_resume_only(home_with_project, monkeypatch):
    """Thread law: a thread WITH sessions offers resume-only — no blank '+ new'.

    Non-first sessions are born from handoffs (real context transfer), not from a
    blank picker entry, so the session step must not advertise '+ new session'.
    """
    _, proj = home_with_project
    from tide.arc import stream
    fill_entry(stream.new_thread(proj, "kickoff"))
    stream.new_session(proj, "kickoff", "one")
    captured = {}

    def fake_select(title, options, **kwargs):
        captured.update(kwargs)
        return 0  # resume the first (only) session

    monkeypatch.setattr(menu.select, "select", fake_select)
    slug_, path_, is_new = menu._pick_session_interactive(proj, "kickoff")
    assert is_new is False  # resumed, not created
    assert captured.get("allow_new") is False  # '+ new session' NOT offered


def test_new_thread_requires_confirmation(home_with_project, monkeypatch):
    """Guard: a mis-tapped '+ new thread' must NOT materialise a thread without a Yes."""
    _, proj = home_with_project
    monkeypatch.setattr(menu, "_ask", lambda p: "oops a voice note")
    seq = iter([menu.select.NEW, 1])  # pick '+ new thread', then 'No' on the confirm
    monkeypatch.setattr(menu.select, "select", lambda *a, **k: next(seq))
    res = menu._pick_thread_interactive(proj, "proj")
    assert res is None                    # declined → nothing returned to the caller
    assert menu.list_threads(proj) == []  # and nothing was created on disk


def test_new_thread_created_on_confirm(home_with_project, monkeypatch):
    """Guard: confirming '+ new thread' DOES create it."""
    _, proj = home_with_project
    monkeypatch.setattr(menu, "_ask", lambda p: "real idea")
    seq = iter([menu.select.NEW, 0])  # '+ new thread', then 'Yes'
    monkeypatch.setattr(menu.select, "select", lambda *a, **k: next(seq))
    res = menu._pick_thread_interactive(proj, "proj")
    assert res == "real-idea"
    assert [t["slug"] for t in menu.list_threads(proj)] == ["real-idea"]


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


# --- draft thread with a LIVE offer stays pickable (cand 28) ----------------

def test_list_threads_hides_plain_draft(home_with_project):
    _, proj = home_with_project
    from tide.arc import stream
    stream.new_thread(proj, "empty-shell")  # placeholder goal, no offer
    assert menu.list_threads(proj) == []


def test_list_threads_shows_draft_with_live_offer(home_with_project, monkeypatch):
    # cand 28: handoff fork 'new' seeds a thread + offered session, but the
    # draft gate hid the thread from the picker — 'а где тред-то?'
    home, proj = home_with_project
    monkeypatch.setenv("TIDE_HOME", str(home))
    from tide import handoff_queue as hq
    from tide.arc import stream

    entry = stream.new_thread(proj, "redesign")          # born a draft
    sess = stream.new_session(proj, "redesign", "kickoff")
    hq.offer(home, "kickoff", project="proj", seed="-",
             arc="{0}/{1}".format(entry.name, sess.name))
    threads = menu.list_threads(proj)
    assert [t["slug"] for t in threads] == ["redesign"]
    assert threads[0]["offered"]
    assert "⌛" in menu._thread_label(threads[0])
    assert "⌛" in menu.render_thread_menu("proj", threads)


def test_list_threads_hides_draft_again_after_drop(home_with_project, monkeypatch):
    home, proj = home_with_project
    monkeypatch.setenv("TIDE_HOME", str(home))
    from tide import handoff_queue as hq
    from tide.arc import stream

    entry = stream.new_thread(proj, "redesign")
    sess = stream.new_session(proj, "redesign", "kickoff")
    hq.offer(home, "kickoff", project="proj", seed="-",
             arc="{0}/{1}".format(entry.name, sess.name))
    hq.drop(home, "kickoff")
    assert menu.list_threads(proj) == []  # no live offer → draft gate applies again


# --- pickup closes the reception seam mechanically (cand 76) ----------------

class _OkAdapter:
    """A stub terminal adapter whose spawn always 'succeeds' (no window opened)."""

    def spawn(self, *, command, cwd, title, dry_run=False):
        from tide.adapters import SpawnResult
        return SpawnResult(ok=True, ref="stub", commands=[command])


def _seed_offer(home, proj):
    """Hang a real offer with a seed file on disk; return (record, session dir)."""
    from tide import handoff_queue as hq
    from tide.arc import stream

    entry = stream.new_thread(proj, "redesign")
    sess = stream.new_session(proj, "redesign", "kickoff")
    seed = sess / "input" / "handoff-seed.md"
    seed.write_text("# seed\n\nделай следующий шаг\n", encoding="utf-8")
    hq.offer(home, "kickoff", project="proj", seed=str(seed),
             arc="{0}/{1}".format(entry.name, sess.name))
    return hq.list_offers(home)[0], sess


def test_pickup_reserves_then_first_prompt_takes(home_with_project):
    # signed A (14.07): spawn reserves; the first message flips — reception is real
    # only when the terminal actually said hello.
    home, proj = home_with_project
    from tide import fields, handoff_queue as hq

    record, sess = _seed_offer(home, proj)
    menu.launch_handoff(record, menu.list_entries(home),
                        control_home=home, adapter=_OkAdapter())
    reserved = hq.list_offers(home)[0]
    assert reserved["status"] == "offered"
    sid = (fields.read_field(sess / "arc.md", "claude-session") or "").strip()
    assert reserved["pickup_session"] == sid
    assert hq.confirm_for_session(home, sid)
    assert hq.list_offers(home)[0]["status"] == "taken"


def test_pickup_records_sid_in_launch_registry(home_with_project):
    # cand 94: a successful launch records sid → terminal handle so ▶ resolves THIS tab
    home, proj = home_with_project
    from tide import fields, registry

    record, sess = _seed_offer(home, proj)
    menu.launch_handoff(record, menu.list_entries(home),
                        control_home=home, adapter=_OkAdapter())
    sid = (fields.read_field(sess / "arc.md", "claude-session") or "").strip()
    reg = registry.read(home)
    assert sid in reg
    assert reg[sid]["handle"] == "stub"
    assert reg[sid]["arc"] == str(sess)


def test_pickup_stamps_passport_active_and_pins_session(home_with_project):
    home, proj = home_with_project
    from tide import fields

    record, sess = _seed_offer(home, proj)
    menu.launch_handoff(record, menu.list_entries(home),
                        control_home=home, adapter=_OkAdapter())
    passport = sess / "arc.md"
    assert fields.read_field(passport, "status") == "active"
    # the pinned claude session id makes the picked-up session resumable
    assert (fields.read_field(passport, "claude-session") or "").strip()


def test_first_prompt_fires_reception_pulse_so_board_sees_it_live(home_with_project):
    # signed A (14.07): the reception stamps (first pulse, offloaded-at) land on the
    # session's FIRST message — a spawned-but-silent terminal must not paint as live.
    home, proj = home_with_project
    from tide import fields, handoff_queue as hq

    record, sess = _seed_offer(home, proj)
    menu.launch_handoff(record, menu.list_entries(home),
                        control_home=home, adapter=_OkAdapter())
    assert "нить принята" not in (sess / "arc.md").read_text(encoding="utf-8")
    sid = (fields.read_field(sess / "arc.md", "claude-session") or "").strip()
    hq.confirm_for_session(home, sid)
    passport_text = (sess / "arc.md").read_text(encoding="utf-8")
    # the mechanical pulse lands in ## context and stamps offloaded-at (board = live)
    assert "нить принята" in passport_text
    assert fields.read_field(sess / "arc.md", "offloaded-at") not in (None, "0", "")


def test_failed_pickup_leaves_offer_hanging(home_with_project):
    home, proj = home_with_project
    from tide import handoff_queue as hq
    from tide.adapters import SpawnResult

    class _FailAdapter:
        def spawn(self, *, command, cwd, title, dry_run=False):
            return SpawnResult(ok=False, detail="no terminal", commands=[command])

    record, _sess = _seed_offer(home, proj)
    menu.launch_handoff(record, menu.list_entries(home),
                        control_home=home, adapter=_FailAdapter())
    # the two-stage guarantee: a failed launch never consumes the offer
    assert hq.list_offers(home)[0]["status"] == "offered"


# --- tide spark: the board's ▶, tide-owned (cand 94) ------------------------

def _spark_entry(home, proj):
    return next(e for e in menu.list_entries(home) if e["path"] == str(proj))


def test_spark_new_thread_creates_pins_and_registers(home_with_project):
    home, proj = home_with_project
    from tide import fields, registry, slug
    from tide.arc import stream

    res = menu.spark(home, _spark_entry(home, proj), new_thread="board ux",
                     goal="unify the board launch", adapter=_OkAdapter())
    assert res.ok
    threads = stream.thread_entries(proj)
    assert threads and slug.entry_slug(threads[0].name) == "board-ux"
    sess = stream.last_session(proj, "board-ux")
    sid = (fields.read_field(sess / "arc.md", "claude-session") or "").strip()
    assert sid                                   # id pinned at birth
    reg = registry.read(home)
    assert sid in reg and reg[sid]["handle"] == "stub"


def test_spark_existing_thread_adds_a_session(home_with_project):
    home, proj = home_with_project
    from tide.arc import stream

    stream.new_thread(proj, "existing", goal="do the thing")
    res = menu.spark(home, _spark_entry(home, proj), thread="existing", adapter=_OkAdapter())
    assert res.ok
    assert stream.last_session(proj, "existing") is not None


def test_spark_requires_a_target(home_with_project):
    home, proj = home_with_project
    with pytest.raises(menu.MenuError):
        menu.spark(home, _spark_entry(home, proj), adapter=_OkAdapter())


def test_spark_unknown_thread_raises(home_with_project):
    home, proj = home_with_project
    with pytest.raises(menu.MenuError):
        menu.spark(home, _spark_entry(home, proj), thread="ghost", adapter=_OkAdapter())


def test_spark_dry_run_writes_nothing(home_with_project):
    # cand 98: dry-run создавал реальный session-shell и пинил sid ДО гейта —
    # «сухой» прогон обязан оставить диск и реестр нетронутыми
    home, proj = home_with_project
    from tide import registry
    from tide.arc import stream

    stream.new_thread(proj, "existing", goal="do the thing")
    before = sorted(p.relative_to(proj) for p in proj.rglob("*"))
    res = menu.spark(home, _spark_entry(home, proj), thread="existing",
                     adapter=_OkAdapter(), dry_run=True)
    assert res.ok
    assert sorted(p.relative_to(proj) for p in proj.rglob("*")) == before
    assert registry.read(home) == {}
    # и новая нить на dry-run тоже не рождается
    res = menu.spark(home, _spark_entry(home, proj), new_thread="ghost ux",
                     adapter=_OkAdapter(), dry_run=True)
    assert res.ok
    assert all("ghost" not in t.name for t in stream.thread_entries(proj))


def test_spark_refuses_thread_with_pending_offer(home_with_project):
    # cand 116 п.3: ▶ при висящей передаче на ту же нить не рожает дубль
    home, proj = home_with_project
    from tide import handoff_queue as hq
    from tide.arc import stream

    stream.new_thread(proj, "payouts", goal="довести выплаты")
    hq.offer(home, "build", arc="payouts/build", project="proj", seed="-")
    with pytest.raises(menu.MenuError, match="висит передача"):
        menu.spark(home, _spark_entry(home, proj), thread="payouts", adapter=_OkAdapter())
    # протухший резерв гард не держит: ▶ снова легален
    key = hq.list_offers(home)[0]["name"]
    hq.reserve(home, key, session="ghost-sid")
    rec = hq.list_offers(home)[0]
    assert rec["pickup_stale"] is False
    from tide import fields as _f
    _f.set_field(rec["path"], "reserved-at", "2026-07-16T00:00:00")
    stale = hq.list_offers(home)[0]
    assert stale["pickup_stale"] is True
    res = menu.spark(home, _spark_entry(home, proj), thread="payouts", adapter=_OkAdapter())
    assert res.ok

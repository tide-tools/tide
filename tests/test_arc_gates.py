"""Gates against мусор (cand 04): draft classification, spawn backpressure, gc.

The mite incident: a loop created seven empty template arcs in two minutes, then
"fixed" it with an eighth empty arc. Three gates close that hole:

1. draft — an unfilled template shell classifies as ``draft`` (computed, never
   stored): the board shows it honestly, the picker skips it.
2. runaway — births are rate-limited per project; overflow refuses + escalates.
3. gc — ``tide arc gc`` sweeps contentless drafts into reversible trash.
"""

from __future__ import annotations

import pytest

from tide import fields
from tide.arc import gc, stream


def _fill_goal(entry, text="ship the thing"):
    fields.set_field(stream.passport_path(entry), "goal", text)


# --- 1. draft classification -------------------------------------------------

def test_fresh_arc_classifies_as_draft(tmp_project):
    e = stream.new_arc(tmp_project, "ship-it")
    assert stream.effective_status(e) == stream.STATUS_DRAFT
    assert stream.passport_filled(e) is False


def test_filled_arc_is_active_no_write_needed(tmp_project):
    e = stream.new_arc(tmp_project, "ship-it")
    _fill_goal(e)
    assert stream.effective_status(e) == "active"
    assert stream.passport_filled(e) is True


def test_placeholder_goal_stays_draft(tmp_project):
    e = stream.new_arc(tmp_project, "ship-it")
    _fill_goal(e, "<one line — what this arc closes>")
    assert stream.effective_status(e) == stream.STATUS_DRAFT


def test_routine_needs_steps_too(tmp_project):
    r = stream.new_routine(tmp_project, "deploy")
    _fill_goal(r, "deploy the stand each release")
    # goal filled but ## steps is still the placeholder — a runbook-less routine
    # cannot be run, so it stays draft
    assert stream.effective_status(r) == stream.STATUS_DRAFT
    pp = stream.passport_path(r)
    text = pp.read_text(encoding="utf-8").replace(
        "<the runbook — the reproducible procedure to follow each run>",
        "1. build 2. push 3. verify",
    )
    pp.write_text(text, encoding="utf-8")
    assert stream.effective_status(r) == "active"


def test_sessions_are_exempt_from_draft(tmp_project):
    stream.new_thread(tmp_project, "work")
    s = stream.new_session(tmp_project, "work", "pickup")
    # a fresh session works before its goal line is polished — never draft
    assert stream.effective_status(s) == "active"


def test_non_active_status_passes_through(tmp_project):
    e = stream.new_arc(tmp_project, "ship-it")
    fields.set_field(stream.passport_path(e), "status", "paused")
    assert stream.effective_status(e) == "paused"


def test_draft_entries_lists_only_unfilled(tmp_project):
    a = stream.new_arc(tmp_project, "empty-one")
    b = stream.new_arc(tmp_project, "real-one")
    _fill_goal(b)
    drafts = stream.draft_entries(tmp_project)
    assert [d.name for d in drafts] == [a.name]


def test_board_shows_draft_badge(tmp_project):
    from tide.arc import board

    stream.new_arc(tmp_project, "empty-one")
    text = board.render_board(tmp_project) if hasattr(board, "render_board") else ""
    if not text:  # render via the status dict (stable public surface)
        data = board.project_status_dict(tmp_project)
        (entry,) = [e for e in data["entries"] if "empty-one" in e["name"]]
        assert entry["status"] == "draft"
    else:
        assert "[draft]" in text


def test_picker_skips_draft_threads_and_routines(tmp_project):
    from tide.launcher import menu

    t = stream.new_thread(tmp_project, "real-thread")
    _fill_goal(t, "a real work-line")
    stream.new_thread(tmp_project, "shell-thread")
    r = stream.new_routine(tmp_project, "real-routine")
    _fill_goal(r, "run the run")
    pp = stream.passport_path(r)
    pp.write_text(
        pp.read_text(encoding="utf-8").replace(
            "<the runbook — the reproducible procedure to follow each run>", "1. go"
        ),
        encoding="utf-8",
    )
    stream.new_routine(tmp_project, "shell-routine")

    threads = [p["slug"] for p in menu.list_threads(tmp_project)]
    routines = [p["slug"] for p in menu.list_routines(tmp_project)]
    assert "real-thread" in threads and "shell-thread" not in threads
    assert "real-routine" in routines and "shell-routine" not in routines


# --- 2. anti-runaway backpressure ---------------------------------------------

def test_runaway_refuses_after_limit(tmp_project, monkeypatch):
    monkeypatch.setenv(stream.SPAWN_LIMIT_ENV, "3")
    stream.new_arc(tmp_project, "one")
    stream.new_arc(tmp_project, "two")
    stream.new_arc(tmp_project, "three")
    with pytest.raises(stream.StreamError, match="RUNAWAY"):
        stream.new_arc(tmp_project, "four")


def test_runaway_message_escalates(tmp_project, monkeypatch):
    monkeypatch.setenv(stream.SPAWN_LIMIT_ENV, "1")
    stream.new_arc(tmp_project, "one")
    with pytest.raises(stream.StreamError, match="escalate"):
        stream.new_thread(tmp_project, "two")


def test_runaway_counts_all_birth_kinds(tmp_project, monkeypatch):
    monkeypatch.setenv(stream.SPAWN_LIMIT_ENV, "3")
    stream.new_thread(tmp_project, "work")
    stream.new_session(tmp_project, "work", "pickup")
    stream.new_goal(tmp_project, "big")
    with pytest.raises(stream.StreamError, match="RUNAWAY"):
        stream.new_arc(tmp_project, "overflow")


def test_runaway_window_expires(tmp_project, monkeypatch):
    monkeypatch.setenv(stream.SPAWN_LIMIT_ENV, "2")
    monkeypatch.setenv(stream.SPAWN_WINDOW_ENV, "600")
    stream.new_arc(tmp_project, "one")
    stream.new_arc(tmp_project, "two")
    # age the recorded births past the window — the gate must forget them
    from tide import paths

    f = paths.state_dir(tmp_project) / stream.BIRTHS_FILE
    import time

    old = time.time() - 601
    f.write_text("\n".join("{0:.3f}".format(old) for _ in range(2)), encoding="utf-8")
    assert stream.new_arc(tmp_project, "three").is_dir()


def test_runaway_zero_limit_disables(tmp_project, monkeypatch):
    monkeypatch.setenv(stream.SPAWN_LIMIT_ENV, "0")
    for i in range(12):
        stream.new_arc(tmp_project, "a{0}".format(i))


def test_refused_birth_not_counted(tmp_project, monkeypatch):
    monkeypatch.setenv(stream.SPAWN_LIMIT_ENV, "2")
    stream.new_arc(tmp_project, "one")
    stream.new_arc(tmp_project, "two")
    with pytest.raises(stream.StreamError):
        stream.new_arc(tmp_project, "three")
    # cleanup + a later retry passes: the refused attempt left no stamp
    from tide import paths

    f = paths.state_dir(tmp_project) / stream.BIRTHS_FILE
    stamps = f.read_text(encoding="utf-8").split()
    assert len(stamps) == 2


# --- 3. tide arc gc ------------------------------------------------------------

def test_gc_finds_only_contentless_drafts(tmp_project):
    shell = stream.new_arc(tmp_project, "shell")
    lived = stream.new_arc(tmp_project, "lived-in")
    (lived / "workspace" / "notes.md").write_text("real work\n", encoding="utf-8")
    filled = stream.new_arc(tmp_project, "filled")
    _fill_goal(filled)

    names = [e.name for e in gc.sweepable(tmp_project)]
    assert [shell.name] == names  # draft+content → kept; filled → kept


def test_gc_dry_run_touches_nothing(tmp_project):
    shell = stream.new_arc(tmp_project, "shell")
    found, moved = gc.sweep(tmp_project, apply=False)
    assert [e.name for e in found] == [shell.name]
    assert moved == []
    assert shell.is_dir()


def test_gc_apply_moves_to_trash_reversibly(tmp_project):
    shell = stream.new_arc(tmp_project, "shell")
    found, moved = gc.sweep(tmp_project, apply=True)
    assert not shell.exists()
    (t,) = moved
    assert t.parent == gc.trash_dir(tmp_project)
    assert (t / "arc.md").is_file()  # the whole dir moved, nothing deleted


def test_gc_sweeps_a_ghost_thread_whose_only_session_is_an_empty_shell(tmp_project):
    # cand 88: a thread with a placeholder goal whose ONLY session is a bare template
    # (no pulse, no claude-session, no content) is a GHOST — gc used to read the shell's
    # arc.md as "life" and let it hang forever (mite 22-@kickoff needed rm -f).
    t = stream.new_thread(tmp_project, "shell-thread")   # placeholder goal
    stream.new_session(tmp_project, "shell-thread", "run")
    assert t in gc.sweepable(tmp_project)


def test_gc_keeps_a_thread_whose_session_pulsed(tmp_project):
    # a session that ever pulsed (offloaded) came alive — the thread is not a ghost.
    stream.new_thread(tmp_project, "live-thread")
    s = stream.new_session(tmp_project, "live-thread", "run")
    fields.set_field(s / "arc.md", "offloaded-at", "2026-07-13T10:00:00")
    assert [e.name for e in gc.sweepable(tmp_project)] == []


def test_gc_keeps_a_thread_whose_session_has_a_claude_id(tmp_project):
    # a pinned claude-session means an agent was launched into it — hands off.
    stream.new_thread(tmp_project, "held-thread")
    s = stream.new_session(tmp_project, "held-thread", "run")
    fields.set_field(s / "arc.md", "claude-session", "df023ba4")
    assert [e.name for e in gc.sweepable(tmp_project)] == []


def test_gc_keeps_a_thread_whose_session_has_real_content(tmp_project):
    stream.new_thread(tmp_project, "worked-thread")
    s = stream.new_session(tmp_project, "worked-thread", "run")
    (s / "workspace" / "notes.md").write_text("real work\n", encoding="utf-8")
    assert [e.name for e in gc.sweepable(tmp_project)] == []


def test_gc_keeps_a_thread_with_a_real_goal_even_if_sessions_are_empty(tmp_project):
    # a stated thread goal is intent — never swept, even with only empty sessions.
    t = stream.new_thread(tmp_project, "purposed", goal="ship the greet CLI end to end")
    stream.new_session(tmp_project, "purposed", "run")
    assert t not in gc.sweepable(tmp_project)


def test_cli_gc_smoke(tmp_project, monkeypatch, capsys):
    from tide import cli

    stream.new_arc(tmp_project, "shell")
    monkeypatch.chdir(tmp_project)
    rc = cli.main(["arc", "gc"])
    out = capsys.readouterr().out
    assert rc == 0 and "dry-run" in out and "shell" in out
    rc = cli.main(["arc", "gc", "--apply"])
    out = capsys.readouterr().out
    assert rc == 0 and "swept" in out
    rc = cli.main(["arc", "gc"])
    out = capsys.readouterr().out
    assert rc == 0 and "no abandoned" in out

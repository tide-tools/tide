"""U-thread unit — тред (thread) container + sessions (sub-arcs inside it).

A thread is a goal-shaped container (``NN-@slug/`` + nested ``arcs/``) tagged
``kind: thread`` — the arc through which you manage other arcs. Its **sessions**
are sub-arcs in that nested stream, numbered in order and chained by ``from:`` so
the picker shows the lineage and lets you continue a session or start a new one
inside the thread.
"""

from __future__ import annotations

import pytest

from tide import fields, slug
from tide.arc import stream


# --- thread (container) -----------------------------------------------------

def test_new_thread_is_a_kind_thread_container(tmp_project):
    entry = stream.new_thread(tmp_project, "deep work")
    assert entry.name == "01-@deep-work"          # goal-shaped (@ sigil)
    assert (entry / "arcs").is_dir()              # nested session substream
    pp = stream.passport_path(entry)
    assert pp.name == "deep-work-goal.md"
    assert fields.read_field(pp, "kind") == "thread"


def test_new_thread_refuses_duplicate_open_slug(tmp_project):
    # Anti-mess gate (candidate 05): re-creating the same open thread is refused.
    stream.new_thread(tmp_project, "kickoff")
    with pytest.raises(stream.StreamError, match="already exists"):
        stream.new_thread(tmp_project, "kickoff")
    # --force allows the rare legitimate second one
    dup = stream.new_thread(tmp_project, "kickoff", force=True)
    assert dup.name == "02-@kickoff"


def test_entry_kind_thread_wins_over_goal(tmp_project):
    arc = stream.new_arc(tmp_project, "a")
    goal = stream.new_goal(tmp_project, "g")
    thread = stream.new_thread(tmp_project, "t")
    assert stream.entry_kind(arc) == stream.KIND_ARC
    assert stream.entry_kind(goal) == stream.KIND_GOAL
    assert stream.entry_kind(thread) == stream.KIND_THREAD
    assert stream.is_thread(thread) and not stream.is_thread(goal)


def test_thread_entries_filters_threads_only(tmp_project):
    stream.new_goal(tmp_project, "real-goal")
    p1 = stream.new_thread(tmp_project, "thread-one")
    stream.new_arc(tmp_project, "work")
    p2 = stream.new_thread(tmp_project, "thread-two")
    names = [p.name for p in stream.thread_entries(tmp_project)]
    assert names == [p1.name, p2.name]


def test_new_thread_empty_slug_raises(tmp_project):
    with pytest.raises(stream.StreamError):
        stream.new_thread(tmp_project, "   ")


# --- sessions (sub-arcs inside a thread) ------------------------------------

def test_new_session_lives_inside_thread_substream(tmp_project):
    stream.new_thread(tmp_project, "prz")
    sess = stream.new_session(tmp_project, "prz", "kickoff")
    assert sess.name == "01-kickoff"
    assert sess.parent.name == "arcs"
    assert sess.parent.parent.name == "01-@prz"
    assert (sess / "arc.md").is_file()
    # a session carries a cursor resume slot
    assert "## cursor" in (sess / "arc.md").read_text(encoding="utf-8")


def test_sessions_number_in_order_and_chain_from(tmp_project):
    stream.new_thread(tmp_project, "prz")
    s1 = stream.new_session(tmp_project, "prz", "first")
    s2 = stream.new_session(tmp_project, "prz", "second")
    assert s1.name == "01-first"
    assert s2.name == "02-second"
    # the lineage: session 2 came from session 1 (by slug ref)
    assert fields.read_field(s2 / "arc.md", "from") == "first"
    assert fields.read_field(s1 / "arc.md", "from") is None


def test_session_entries_lists_open_sessions_in_order(tmp_project):
    stream.new_thread(tmp_project, "prz")
    stream.new_session(tmp_project, "prz", "one")
    stream.new_session(tmp_project, "prz", "two")
    names = [p.name for p in stream.session_entries(tmp_project, "prz")]
    assert names == ["01-one", "02-two"]


def test_last_session_is_newest_or_none(tmp_project):
    stream.new_thread(tmp_project, "prz")
    assert stream.last_session(tmp_project, "prz") is None
    stream.new_session(tmp_project, "prz", "one")
    s2 = stream.new_session(tmp_project, "prz", "two")
    assert stream.last_session(tmp_project, "prz").name == s2.name


def test_new_session_from_ref_sets_explicit_lineage(tmp_project):
    stream.new_thread(tmp_project, "prz")
    stream.new_session(tmp_project, "prz", "one")
    stream.new_session(tmp_project, "prz", "two")
    # branch a third session explicitly forked from the FIRST, not the previous
    s3 = stream.new_session(tmp_project, "prz", "branch", from_ref="one")
    assert fields.read_field(s3 / "arc.md", "from") == "one"


def test_new_session_requires_open_thread(tmp_project):
    with pytest.raises(stream.StreamError):
        stream.new_session(tmp_project, "ghost", "x")


def test_session_opens_via_arc_open_under_thread(tmp_project):
    stream.new_thread(tmp_project, "prz")
    stream.new_session(tmp_project, "prz", "resumable")
    entry = stream.open_arc(tmp_project, "resumable", goal_slug="prz")
    assert entry.name == "01-resumable"


# --- goal at birth (cand 28: no draft placeholder for offer-bound threads) --

def test_new_thread_goal_fills_passport_at_birth(tmp_project):
    from tide.arc import stream

    entry = stream.new_thread(tmp_project, "redesign", goal="redesign the app")
    assert stream.goal_filled(entry)
    assert stream.effective_status(entry) == "active"  # not a draft


def test_new_session_goal_param_sets_field(tmp_project):
    from tide import fields
    from tide.arc import stream

    stream.new_thread(tmp_project, "redesign", goal="redesign the app")
    sess = stream.new_session(tmp_project, "redesign", "kickoff",
                              goal="kick off the redesign")
    assert fields.read_field(sess / "arc.md", "goal") == "kick off the redesign"


def test_cli_new_thread_goal_flag(tmp_project, monkeypatch, capsys):
    from tide import cli
    from tide.arc import stream

    monkeypatch.chdir(tmp_project)
    rc = cli.main(["arc", "new-thread", "redesign", "--goal", "redesign the app"])
    assert rc == 0
    entry = stream.thread_entries(tmp_project)[0]
    assert stream.goal_filled(entry)


def test_cli_new_routine_goal_flag_still_draft_without_steps(tmp_project, monkeypatch):
    # --goal fills the goal line, but a routine without real ## steps stays a
    # draft (it cannot be run) — the goal flag must not weaken that gate.
    from tide import cli
    from tide.arc import stream

    monkeypatch.chdir(tmp_project)
    rc = cli.main(["arc", "new-routine", "deploy", "--goal", "ship to prod"])
    assert rc == 0
    entry = stream.routine_entries(tmp_project)[0]
    assert stream.goal_filled(entry)
    assert stream.effective_status(entry) == "draft"


# --- close_thread: close a whole nit, cascading to sessions (cand 74) --------

def _thread_with_two_sessions(tmp_project):
    entry = stream.new_thread(tmp_project, "ship", goal="ship the greet CLI")
    s1 = stream.new_session(tmp_project, "ship", "start")
    s2 = stream.new_session(tmp_project, "ship", "finish")
    (entry / "output" / "result.md").write_text("done — shipped\n", encoding="utf-8")
    return entry, s1, s2


def test_close_thread_cascades_to_sessions(tmp_project):
    entry, s1, s2 = _thread_with_two_sessions(tmp_project)
    summary = stream.close_thread(tmp_project, "ship", force=True)

    # the thread is sealed and every session came with it — no ghost open sessions
    assert summary["thread"] == "__01-@ship__"
    assert set(summary["sessions"]) == {"__01-start__", "__02-finish__"}
    closed_thread = tmp_project / ".tide" / "arcs" / "__01-@ship__"
    assert closed_thread.is_dir()
    assert fields.read_field(stream.passport_path(closed_thread), "status") == "done"
    open_sessions = [d for d in (closed_thread / "arcs").iterdir()
                     if d.is_dir() and not slug.is_closed_entry(d.name)]
    assert open_sessions == []  # nothing left active under a done thread


def _pulse(session_dir, when):
    """Stamp a session's offloaded-at pulse to *when* (a datetime)."""
    fields.set_field(session_dir / "arc.md", "offloaded-at", when.isoformat(timespec="seconds"))


def test_close_thread_skips_a_live_session(tmp_project):
    # cand 79: a session with a FRESH pulse survives the thread — not buried under a
    # done passport (the Mickey-17 inverse). The dead sibling still seals.
    from datetime import datetime, timedelta

    entry, s1, s2 = _thread_with_two_sessions(tmp_project)
    _pulse(s2, datetime.now())                              # s2 is alive right now
    _pulse(s1, datetime.now() - timedelta(days=3))          # s1 went quiet days ago
    summary = stream.close_thread(tmp_project, "ship", force=True)

    assert summary["sessions"] == ["__01-start__"]          # dead one sealed
    assert summary["skipped_live"] == ["02-finish"]         # live one left OPEN
    closed_thread = tmp_project / ".tide" / "arcs" / "__01-@ship__"
    live = closed_thread / "arcs" / "02-finish"
    assert live.is_dir() and not slug.is_closed_entry(live.name)
    assert fields.read_field(live / "arc.md", "status") == "active"


def test_close_thread_live_skip_holds_even_under_force(tmp_project):
    # a live head is never sealed — -f overrides the OUTPUT guard, not the live guard.
    from datetime import datetime

    entry, s1, s2 = _thread_with_two_sessions(tmp_project)
    _pulse(s1, datetime.now())
    _pulse(s2, datetime.now())
    summary = stream.close_thread(tmp_project, "ship", force=True)
    assert summary["sessions"] == []
    assert set(summary["skipped_live"]) == {"01-start", "02-finish"}


def test_cli_arc_close_warns_about_a_skipped_live_session(tmp_project, monkeypatch, capsys):
    from datetime import datetime
    from tide import cli

    _, s1, s2 = _thread_with_two_sessions(tmp_project)
    _pulse(s2, datetime.now())
    monkeypatch.chdir(tmp_project)
    rc = cli.main(["arc", "close", "ship", "-f"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "живую сессию" in err and "02-finish" in err


def test_close_thread_guards_empty_output_before_touching_sessions(tmp_project):
    entry = stream.new_thread(tmp_project, "ship", goal="ship it")
    s1 = stream.new_session(tmp_project, "ship", "start")
    with pytest.raises(stream.StreamError, match="empty output"):
        stream.close_thread(tmp_project, "ship")            # no output, no force
    # the guard fired first — the session was NOT half-sealed
    assert not slug.is_closed_entry(s1.name)
    assert s1.is_dir()


def test_close_thread_refuses_a_plain_arc(tmp_project):
    stream.new_arc(tmp_project, "loose")
    with pytest.raises(stream.StreamError, match="not a thread"):
        stream.close_thread(tmp_project, "loose")


def test_cli_arc_close_on_thread_cascades(tmp_project, monkeypatch, capsys):
    from tide import cli

    _thread_with_two_sessions(tmp_project)
    monkeypatch.chdir(tmp_project)
    rc = cli.main(["arc", "close", "ship", "-f"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "closed thread __01-@ship__" in out and "2 sessions sealed" in out


# --- cand 93-board-spark: bind head id at birth when claude self-creates the session

def test_new_session_binds_claude_session_when_given(tmp_project):
    stream.new_thread(tmp_project, "work", goal="do the work")
    s = stream.new_session(tmp_project, "work", "spark", claude_session="sid-abc")
    assert fields.read_field(s / "arc.md", "claude-session") == "sid-abc"


def test_new_session_no_head_by_default(tmp_project):
    stream.new_thread(tmp_project, "work", goal="do the work")
    s = stream.new_session(tmp_project, "work", "plain")
    assert not (fields.read_field(s / "arc.md", "claude-session") or "").strip()


def test_cli_new_session_stamps_env_session_id(tmp_project, monkeypatch):
    # the board-spark flow: claude runs `tide arc new-session`; its own id is in env
    from tide import cli

    monkeypatch.chdir(tmp_project)
    stream.new_thread(tmp_project, "work", goal="do the work")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "spark-sid-777")
    rc = cli.main(["arc", "new-session", "spark", "-p", "work"])
    assert rc == 0
    sess = stream.last_session(tmp_project, "work")
    assert fields.read_field(sess / "arc.md", "claude-session") == "spark-sid-777"


def test_cli_new_session_no_stamp_without_env(tmp_project, monkeypatch):
    from tide import cli

    monkeypatch.chdir(tmp_project)
    stream.new_thread(tmp_project, "work", goal="do the work")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    rc = cli.main(["arc", "new-session", "plain", "-p", "work"])
    assert rc == 0
    sess = stream.last_session(tmp_project, "work")
    assert not (fields.read_field(sess / "arc.md", "claude-session") or "").strip()


# --- passport floor at birth (cands 102/105) ---------------------------------

def test_session_born_with_default_title(tmp_project):
    stream.new_thread(tmp_project, "prz")
    sess = stream.new_session(tmp_project, "prz", "kickoff")
    assert fields.read_field(sess / "arc.md", "title") == "prz · kickoff"


def test_session_inherits_live_thread_goal(tmp_project):
    stream.new_thread(tmp_project, "prz", goal="ship the launcher end to end")
    sess = stream.new_session(tmp_project, "prz", "kickoff")
    assert fields.read_field(sess / "arc.md", "goal") == "ship the launcher end to end"


def test_session_keeps_placeholder_on_blind_thread_goal(tmp_project):
    # a draft thread (goal = its own slug) is fine — the session just isn't lied to
    stream.new_thread(tmp_project, "prz", goal="prz")
    sess = stream.new_session(tmp_project, "prz", "kickoff")
    goal = fields.read_field(sess / "arc.md", "goal") or ""
    assert goal != "prz"


def test_explicit_goal_beats_inheritance(tmp_project):
    stream.new_thread(tmp_project, "prz", goal="ship the launcher end to end")
    sess = stream.new_session(tmp_project, "prz", "kickoff", goal="verify step one only")
    assert fields.read_field(sess / "arc.md", "goal") == "verify step one only"


def test_default_title_normalizes_entry_name_ref(tmp_project):
    # the board shows entry names (01-@prz) and people paste them — the title must
    # still read as clean words, same normalization as the from: field
    stream.new_thread(tmp_project, "prz")
    sess = stream.new_session(tmp_project, "01-@prz", "kickoff")
    assert fields.read_field(sess / "arc.md", "title") == "prz · kickoff"


def test_cli_new_session_guard_is_cross_thread(tmp_project, monkeypatch):
    # e2e 14.07: an orchestrator (its session in thread A) creates a session in
    # thread B — its own sid must NOT be stamped onto B (the one-thread guard
    # missed this; the spawned claude then died on "Session ID already in use").
    from tide import cli

    monkeypatch.chdir(tmp_project)
    stream.new_thread(tmp_project, "mine", goal="my own thread")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "orch-sid-1")
    assert cli.main(["arc", "new-session", "me", "-p", "mine"]) == 0  # self-register ok
    stream.new_thread(tmp_project, "other", goal="someone else's work")
    assert cli.main(["arc", "new-session", "probe", "-p", "other"]) == 0
    sess = stream.last_session(tmp_project, "other")
    assert not (fields.read_field(sess / "arc.md", "claude-session") or "").strip()

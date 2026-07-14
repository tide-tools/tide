"""curate — the human's board gestures as domain ops (hold/dismiss/drop/validate)."""

from __future__ import annotations

import pytest

from tide import fields
from tide.arc import curate, stream


# --- hold (☾ / ↑) -------------------------------------------------------------------

def test_hold_stamps_and_removes(tmp_project):
    t = stream.new_thread(tmp_project, "prz", goal="ship it")
    pp = curate.hold(t)
    assert fields.read_field(pp, "held")
    first = fields.read_field(pp, "held")
    curate.hold(t)  # idempotent — the stamp survives, not re-stamped
    assert fields.read_field(pp, "held") == first
    curate.hold(t, on=False)
    assert fields.read_field(pp, "held") is None
    curate.hold(t, on=False)  # off twice — still fine


def test_hold_refuses_outside_arcs(tmp_path):
    with pytest.raises(stream.StreamError):
        curate.hold(tmp_path)


# --- dismiss (✕ head) ---------------------------------------------------------------

def test_dismiss_stamps_single_live_session(tmp_project):
    stream.new_thread(tmp_project, "prz", goal="ship it")
    s1 = stream.new_session(tmp_project, "prz", "one")
    s2 = stream.new_session(tmp_project, "prz", "two")
    stamped = curate.dismiss(s1)
    assert [p.parent.name for p in stamped] == [s1.name]
    assert fields.read_field(s1 / "arc.md", "dismissed")
    assert fields.read_field(s2 / "arc.md", "dismissed") is None


def test_dismiss_on_closed_thread_frees_whole_chain(tmp_project):
    t = stream.new_thread(tmp_project, "prz", goal="ship it")
    s1 = stream.new_session(tmp_project, "prz", "one")
    stream.new_session(tmp_project, "prz", "two")
    closed = t.parent / "__{0}__".format(t.name)
    t.rename(closed)  # a closed (__) thread
    stamped = curate.dismiss(closed / "arcs" / s1.name)
    assert len(stamped) == 2  # every live session freed at once


def test_dismiss_skips_already_dismissed(tmp_project):
    stream.new_thread(tmp_project, "prz", goal="ship it")
    s1 = stream.new_session(tmp_project, "prz", "one")
    curate.dismiss(s1)
    first = fields.read_field(s1 / "arc.md", "dismissed")
    assert curate.dismiss(s1) == []  # second gesture: nothing new stamped
    assert fields.read_field(s1 / "arc.md", "dismissed") == first


# --- retire_sessions (the close-by-hand half) ----------------------------------------

def test_retire_sessions_stamps_all_live(tmp_project):
    t = stream.new_thread(tmp_project, "prz", goal="ship it")
    stream.new_session(tmp_project, "prz", "one")
    stream.new_session(tmp_project, "prz", "two")
    assert len(curate.retire_sessions(t)) == 2
    assert curate.retire_sessions(t) == []  # idempotent


# --- drop_candidate (✕ idea) ---------------------------------------------------------

def test_drop_candidate_moves_to_dropped(tmp_project):
    cdir = tmp_project / ".tide" / "arcs" / "candidates"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "42-idea.md").write_text("# idea\n", encoding="utf-8")
    dest = curate.drop_candidate(tmp_project, "42-idea")
    assert dest.parent.name == "__dropped__" and dest.is_file()
    assert not (cdir / "42-idea.md").exists()


def test_drop_candidate_collision_keeps_both(tmp_project):
    cdir = tmp_project / ".tide" / "arcs" / "candidates"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "__dropped__").mkdir()
    (cdir / "__dropped__" / "42-idea.md").write_text("old\n", encoding="utf-8")
    (cdir / "42-idea.md").write_text("new\n", encoding="utf-8")
    dest = curate.drop_candidate(tmp_project, "42-idea")
    assert dest.name == "42-idea-2.md"  # NN reuse must not clobber the past


def test_drop_candidate_refuses_traversal_and_missing(tmp_project):
    with pytest.raises(stream.StreamError):
        curate.drop_candidate(tmp_project, "../evil")
    with pytest.raises(stream.StreamError):
        curate.drop_candidate(tmp_project, "no-such")


# --- drop_thread (✕ empty thread) ----------------------------------------------------

def test_drop_thread_moves_empty_planless(tmp_project):
    t = stream.new_thread(tmp_project, "blank")
    dest = curate.drop_thread(t)
    assert dest.parent.name == "__dropped__"
    assert not t.exists()


def test_drop_thread_refuses_live_work(tmp_project):
    t = stream.new_thread(tmp_project, "busy", goal="work")
    stream.new_session(tmp_project, "busy", "one")
    with pytest.raises(stream.StreamError):
        curate.drop_thread(t)
    t2 = stream.new_thread(tmp_project, "planned")
    (t2 / "plan.md").write_text("# plan\n", encoding="utf-8")
    with pytest.raises(stream.StreamError):
        curate.drop_thread(t2)


def test_drop_thread_refuses_closed_trophy(tmp_project):
    t = stream.new_thread(tmp_project, "prz")
    closed = t.parent / "__{0}__".format(t.name)
    t.rename(closed)
    with pytest.raises(stream.StreamError):
        curate.drop_thread(closed)


# --- validate_step (✓ gate by hand) --------------------------------------------------

_PLAN = """# план нити

- [>] 1. первый шаг
  гейт: доска показывает
- [ ] 2. второй шаг
  гейт: юзер видит
"""


def test_validate_step_marks_done_and_promotes_next(tmp_project):
    t = stream.new_thread(tmp_project, "prz", goal="ship")
    (t / "plan.md").write_text(_PLAN, encoding="utf-8")
    curate.validate_step(t, "1", who="tester")
    text = (t / "plan.md").read_text(encoding="utf-8")
    assert "- [x] 1." in text
    assert "гейт-пройден:" in text and "tester" in text
    assert "- [>] 2." in text  # next todo promoted to current


def test_validate_step_updates_existing_stamp(tmp_project):
    t = stream.new_thread(tmp_project, "prz", goal="ship")
    (t / "plan.md").write_text(_PLAN, encoding="utf-8")
    curate.validate_step(t, "1", who="first")
    curate.validate_step(t, "1", who="second")
    text = (t / "plan.md").read_text(encoding="utf-8")
    assert text.count("гейт-пройден:") == 1 and "second" in text


def test_validate_step_refuses_unknown(tmp_project):
    t = stream.new_thread(tmp_project, "prz", goal="ship")
    (t / "plan.md").write_text(_PLAN, encoding="utf-8")
    with pytest.raises(stream.StreamError):
        curate.validate_step(t, "9")
    with pytest.raises(stream.StreamError):
        curate.validate_step(t, "1x")


# --- fields.remove_field (the reversible half of hold) --------------------------------

def test_remove_field_is_clean_and_idempotent(tmp_path):
    f = tmp_path / "arc.md"
    f.write_text("# x\n\ngoal: g\nheld: 2026-07-14\nstatus: active\n", encoding="utf-8")
    fields.remove_field(f, "held")
    text = f.read_text(encoding="utf-8")
    assert "held:" not in text and "goal: g" in text and "status: active" in text
    fields.remove_field(f, "held")  # absent — no-op
    fields.remove_field(tmp_path / "ghost.md", "held")  # missing file — no-op

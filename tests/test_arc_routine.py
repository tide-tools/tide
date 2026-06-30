"""U-routine unit — рутина (routine): a reusable-procedure container + its runs.

A routine is a goal-shaped container (``NN-@slug/`` + nested ``arcs/``) tagged
``kind: routine`` — work you did once and now re-run, with its own accumulated
``## experience``. Its **runs** are sub-arcs in that nested stream: a run IS a
session inside the routine, so runs reuse :func:`new_session` /
:func:`session_entries` / :func:`last_session` unchanged, numbered in order and
chained by ``from:``.
"""

from __future__ import annotations

import pytest

from tide import fields
from tide.arc import stream


# --- routine (container) ---------------------------------------------------

def test_new_routine_is_a_kind_routine_container(tmp_project):
    entry = stream.new_routine(tmp_project, "invite codes")
    assert entry.name == "01-@invite-codes"          # goal-shaped (@ sigil)
    assert (entry / "arcs").is_dir()                  # nested run substream
    pp = stream.passport_path(entry)
    assert pp.name == "invite-codes-goal.md"
    assert fields.read_field(pp, "kind") == "routine"
    body = pp.read_text(encoding="utf-8")
    assert "## steps" in body          # the runbook
    assert "## experience" in body     # accrues lessons across runs


def test_new_routine_refuses_duplicate_open_slug(tmp_project):
    # Anti-mess gate (candidate 05): re-creating the same open routine is refused.
    stream.new_routine(tmp_project, "invite codes")
    with pytest.raises(stream.StreamError, match="already exists"):
        stream.new_routine(tmp_project, "invite-codes")


def test_new_routine_force_allows_duplicate(tmp_project):
    stream.new_routine(tmp_project, "invite-codes")
    dup = stream.new_routine(tmp_project, "invite-codes", force=True)
    assert dup.name == "02-@invite-codes"  # second one created under --force


def test_entry_kind_routine_wins_over_goal(tmp_project):
    arc = stream.new_arc(tmp_project, "a")
    goal = stream.new_goal(tmp_project, "g")
    thread = stream.new_thread(tmp_project, "t")
    routine = stream.new_routine(tmp_project, "r")
    assert stream.entry_kind(arc) == stream.KIND_ARC
    assert stream.entry_kind(goal) == stream.KIND_GOAL
    assert stream.entry_kind(thread) == stream.KIND_THREAD
    assert stream.entry_kind(routine) == stream.KIND_ROUTINE
    assert stream.is_routine(routine) and not stream.is_routine(goal)
    assert not stream.is_routine(thread)  # a routine is not a thread


def test_routine_entries_filters_routines_only(tmp_project):
    stream.new_goal(tmp_project, "real-goal")
    stream.new_arc(tmp_project, "work")
    stream.new_thread(tmp_project, "a-thread")
    r1 = stream.new_routine(tmp_project, "routine-one")
    r2 = stream.new_routine(tmp_project, "routine-two")
    names = [p.name for p in stream.routine_entries(tmp_project)]
    assert names == [r1.name, r2.name]


def test_new_routine_empty_slug_raises(tmp_project):
    with pytest.raises(stream.StreamError):
        stream.new_routine(tmp_project, "   ")


# --- runs (sessions inside a routine) --------------------------------------

def test_run_lives_inside_routine_substream(tmp_project):
    stream.new_routine(tmp_project, "invite-codes")
    run = stream.new_session(tmp_project, "invite-codes", "batch")
    assert run.name == "01-batch"
    assert run.parent.name == "arcs"
    assert run.parent.parent.name == "01-@invite-codes"
    assert (run / "arc.md").is_file()


def test_runs_number_in_order_and_chain_from(tmp_project):
    stream.new_routine(tmp_project, "invite-codes")
    r1 = stream.new_session(tmp_project, "invite-codes", "first")
    r2 = stream.new_session(tmp_project, "invite-codes", "second")
    assert r1.name == "01-first"
    assert r2.name == "02-second"
    # the lineage: run 2 came from run 1 (by slug ref)
    assert fields.read_field(r2 / "arc.md", "from") == "first"
    assert fields.read_field(r1 / "arc.md", "from") is None


def test_session_entries_lists_routine_runs_in_order(tmp_project):
    stream.new_routine(tmp_project, "invite-codes")
    stream.new_session(tmp_project, "invite-codes", "one")
    stream.new_session(tmp_project, "invite-codes", "two")
    names = [p.name for p in stream.session_entries(tmp_project, "invite-codes")]
    assert names == ["01-one", "02-two"]


# --- routine run seed carries the procedure (regression: was missing) ------

def test_routine_run_seed_carries_the_procedure(tmp_project):
    """A routine run's seed must include the routine's ## steps / ## experience.

    Regression: the seed used to carry only the (empty) run passport, so the
    launched session had no idea what the routine does.
    """
    from tide.launcher import seed as seedmod
    from tide import fields as F

    routine = stream.new_routine(tmp_project, "invite-codes")
    # write a recognisable procedure onto the routine container's goal doc
    goal_doc = stream.passport_path(routine)
    text = goal_doc.read_text(encoding="utf-8").replace(
        "<the runbook — the reproducible procedure to follow each run>",
        "STEP ONE: do the issuing. STEP TWO: verify in the DB.",
    )
    goal_doc.write_text(text, encoding="utf-8")
    run = stream.new_session(tmp_project, "invite-codes", "first-run")

    s = seedmod.seed_for_project(
        tmp_project,
        arc_ref="first-run",
        arc_text=(run / "arc.md").read_text(encoding="utf-8"),
        thread_name="invite-codes",
        container_kind="routine",
    )
    assert "### Routine procedure" in s
    assert "STEP ONE: do the issuing" in s            # the actual runbook is injected
    assert "## experience" in s
    assert "Active routine run" in s

"""U-prism unit — призма (prism) container + sessions (sub-arcs inside it).

A prism is a goal-shaped container (``NN-@slug/`` + nested ``arcs/``) tagged
``kind: prism`` — the arc through which you manage other arcs. Its **sessions**
are sub-arcs in that nested stream, numbered in order and chained by ``from:`` so
the picker shows the lineage and lets you continue a session or start a new one
inside the prism.
"""

from __future__ import annotations

import pytest

from tide import fields, slug
from tide.arc import stream


# --- prism (container) -----------------------------------------------------

def test_new_prism_is_a_kind_prism_container(tmp_project):
    entry = stream.new_prism(tmp_project, "deep work")
    assert entry.name == "01-@deep-work"          # goal-shaped (@ sigil)
    assert (entry / "arcs").is_dir()              # nested session substream
    pp = stream.passport_path(entry)
    assert pp.name == "deep-work-goal.md"
    assert fields.read_field(pp, "kind") == "prism"


def test_entry_kind_prism_wins_over_goal(tmp_project):
    arc = stream.new_arc(tmp_project, "a")
    goal = stream.new_goal(tmp_project, "g")
    prism = stream.new_prism(tmp_project, "t")
    assert stream.entry_kind(arc) == stream.KIND_ARC
    assert stream.entry_kind(goal) == stream.KIND_GOAL
    assert stream.entry_kind(prism) == stream.KIND_PRISM
    assert stream.is_prism(prism) and not stream.is_prism(goal)


def test_prism_entries_filters_prisms_only(tmp_project):
    stream.new_goal(tmp_project, "real-goal")
    p1 = stream.new_prism(tmp_project, "prism-one")
    stream.new_arc(tmp_project, "work")
    p2 = stream.new_prism(tmp_project, "prism-two")
    names = [p.name for p in stream.prism_entries(tmp_project)]
    assert names == [p1.name, p2.name]


def test_new_prism_empty_slug_raises(tmp_project):
    with pytest.raises(stream.StreamError):
        stream.new_prism(tmp_project, "   ")


# --- sessions (sub-arcs inside a prism) ------------------------------------

def test_new_session_lives_inside_prism_substream(tmp_project):
    stream.new_prism(tmp_project, "prz")
    sess = stream.new_session(tmp_project, "prz", "kickoff")
    assert sess.name == "01-kickoff"
    assert sess.parent.name == "arcs"
    assert sess.parent.parent.name == "01-@prz"
    assert (sess / "arc.md").is_file()
    # a session carries a cursor resume slot
    assert "## cursor" in (sess / "arc.md").read_text(encoding="utf-8")


def test_sessions_number_in_order_and_chain_from(tmp_project):
    stream.new_prism(tmp_project, "prz")
    s1 = stream.new_session(tmp_project, "prz", "first")
    s2 = stream.new_session(tmp_project, "prz", "second")
    assert s1.name == "01-first"
    assert s2.name == "02-second"
    # the lineage: session 2 came from session 1 (by slug ref)
    assert fields.read_field(s2 / "arc.md", "from") == "first"
    assert fields.read_field(s1 / "arc.md", "from") is None


def test_session_entries_lists_open_sessions_in_order(tmp_project):
    stream.new_prism(tmp_project, "prz")
    stream.new_session(tmp_project, "prz", "one")
    stream.new_session(tmp_project, "prz", "two")
    names = [p.name for p in stream.session_entries(tmp_project, "prz")]
    assert names == ["01-one", "02-two"]


def test_last_session_is_newest_or_none(tmp_project):
    stream.new_prism(tmp_project, "prz")
    assert stream.last_session(tmp_project, "prz") is None
    stream.new_session(tmp_project, "prz", "one")
    s2 = stream.new_session(tmp_project, "prz", "two")
    assert stream.last_session(tmp_project, "prz").name == s2.name


def test_new_session_from_ref_sets_explicit_lineage(tmp_project):
    stream.new_prism(tmp_project, "prz")
    stream.new_session(tmp_project, "prz", "one")
    stream.new_session(tmp_project, "prz", "two")
    # branch a third session explicitly forked from the FIRST, not the previous
    s3 = stream.new_session(tmp_project, "prz", "branch", from_ref="one")
    assert fields.read_field(s3 / "arc.md", "from") == "one"


def test_new_session_requires_open_prism(tmp_project):
    with pytest.raises(stream.StreamError):
        stream.new_session(tmp_project, "ghost", "x")


def test_session_opens_via_arc_open_under_prism(tmp_project):
    stream.new_prism(tmp_project, "prz")
    stream.new_session(tmp_project, "prz", "resumable")
    entry = stream.open_arc(tmp_project, "resumable", goal_slug="prz")
    assert entry.name == "01-resumable"

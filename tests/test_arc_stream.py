"""U3 unit — arc-stream lifecycle: create / open / close / reopen / supersede."""

from __future__ import annotations

import pytest

from tide import fields, paths
from tide.arc import stream
from tide.canon import rev

from tests.conftest import strip_placeholders


# --- create ----------------------------------------------------------------

def test_new_arc_builds_triad_and_passport(tmp_project):
    entry = stream.new_arc(tmp_project, "fix the leak")
    assert entry.name == "01-fix-the-leak"
    for sub in ("input", "workspace", "output"):
        assert (entry / sub).is_dir()
    doc = entry / "arc.md"
    assert doc.is_file()
    assert fields.read_field(doc, "status") == "active"


def test_new_arc_stamps_canon_rev(tmp_project):
    entry = stream.new_arc(tmp_project, "alpha")
    stamped = fields.read_field(entry / "arc.md", "canon-rev")
    assert stamped == rev.compute(tmp_project)
    assert stamped  # non-empty


def test_new_arc_numbering_is_continuous(tmp_project):
    a = stream.new_arc(tmp_project, "one")
    b = stream.new_arc(tmp_project, "two")
    assert a.name == "01-one"
    assert b.name == "02-two"


def test_new_arc_empty_slug_raises(tmp_project):
    with pytest.raises(stream.StreamError):
        stream.new_arc(tmp_project, "!!!")


def test_new_goal_builds_substream_and_doc(tmp_project):
    goal = stream.new_goal(tmp_project, "ship v1")
    assert goal.name == "01-@ship-v1"
    assert (goal / "arcs").is_dir()
    doc = goal / "ship-v1-goal.md"
    assert doc.is_file()
    assert fields.read_field(doc, "status") == "active"


def test_arc_and_goal_share_one_counter(tmp_project):
    stream.new_arc(tmp_project, "a")          # 01
    goal = stream.new_goal(tmp_project, "g")  # 02 (not 01)
    assert goal.name == "02-@g"


# --- goal substream --------------------------------------------------------

def test_new_arc_nests_under_open_goal(tmp_project):
    stream.new_goal(tmp_project, "ship")
    sub = stream.new_arc(tmp_project, "wire-api", goal_slug="ship")
    assert sub.parent.name == "arcs"
    assert sub.parent.parent.name == "01-@ship"
    assert sub.name == "01-wire-api"  # local substream numbering


def test_new_arc_under_missing_goal_raises(tmp_project):
    with pytest.raises(stream.StreamError):
        stream.new_arc(tmp_project, "x", goal_slug="nope")


def test_new_arc_under_closed_goal_raises(tmp_project):
    goal = stream.new_goal(tmp_project, "ship")
    (goal / "output" / "done.md").write_text("x", encoding="utf-8")
    strip_placeholders(stream.passport_path(goal))
    stream.close(tmp_project, "ship")
    with pytest.raises(stream.StreamError):
        stream.new_arc(tmp_project, "late", goal_slug="ship")


# --- open / resume ---------------------------------------------------------

def test_open_restamps_canon_rev_after_canon_moves(tmp_project):
    entry = stream.new_arc(tmp_project, "alpha")
    old_rev = fields.read_field(entry / "arc.md", "canon-rev")
    # move the canon → rev changes
    canon = paths.canon_file(tmp_project)
    canon.write_text(canon.read_text(encoding="utf-8") + "\nmoved\n", encoding="utf-8")
    new_rev = stream.open_arc(tmp_project, "alpha")
    assert rev.compute(tmp_project) != old_rev
    stamped = fields.read_field((tmp_project / ".tide/arcs/01-alpha") / "arc.md", "canon-rev")
    assert stamped == rev.compute(tmp_project)


def test_open_missing_arc_raises(tmp_project):
    with pytest.raises(stream.StreamError):
        stream.open_arc(tmp_project, "ghost")


# --- close (guard + dual-mark) ---------------------------------------------

def test_close_refuses_empty_output(tmp_project):
    stream.new_arc(tmp_project, "alpha")
    with pytest.raises(stream.StreamError):
        stream.close(tmp_project, "alpha")


def test_close_dual_marks_done(tmp_project):
    entry = stream.new_arc(tmp_project, "alpha")
    (entry / "output" / "result.md").write_text("done", encoding="utf-8")
    strip_placeholders(entry / "arc.md")
    closed = stream.close(tmp_project, "alpha")
    assert closed.name == "__01-alpha__"
    assert closed.is_dir()
    assert not entry.exists()
    assert fields.read_field(closed / "arc.md", "status") == "done"


def test_close_force_overrides_empty_output(tmp_project):
    stream.new_arc(tmp_project, "alpha")
    closed = stream.close(tmp_project, "alpha", force=True)
    assert closed.name == "__01-alpha__"
    assert fields.read_field(closed / "arc.md", "status") == "done"


def test_close_refuses_leftover_placeholders(tmp_project):
    # F5: a filled output but a still-scaffolded arc.md (angle-bracket spans +
    # the `# supersedes:` hint) is refused — a closed passport must not read
    # like a fill-in form.
    entry = stream.new_arc(tmp_project, "alpha")
    (entry / "output" / "r.md").write_text("done", encoding="utf-8")
    with pytest.raises(stream.StreamError) as ei:
        stream.close(tmp_project, "alpha")
    assert "placeholder" in str(ei.value)
    assert entry.is_dir()  # not sealed


def test_close_force_overrides_placeholder_guard(tmp_project):
    # F5: -f seals even a scaffolded passport (escape hatch).
    stream.new_arc(tmp_project, "alpha")
    closed = stream.close(tmp_project, "alpha", force=True)
    assert closed.name == "__01-alpha__"


def test_close_allows_filled_passport(tmp_project):
    # F5: once the placeholders are filled/removed, close proceeds normally.
    entry = stream.new_arc(tmp_project, "alpha")
    (entry / "output" / "r.md").write_text("done", encoding="utf-8")
    strip_placeholders(entry / "arc.md")
    closed = stream.close(tmp_project, "alpha")
    assert closed.name == "__01-alpha__"
    assert fields.read_field(closed / "arc.md", "status") == "done"


def test_close_prefers_goal_over_arc_same_slug(tmp_project):
    # an arc AND a goal both named 'ship' → close must hit the goal
    stream.new_arc(tmp_project, "ship")    # 01-ship
    goal = stream.new_goal(tmp_project, "ship")  # 02-@ship
    (goal / "output" / "x.md").write_text("x", encoding="utf-8")
    strip_placeholders(stream.passport_path(goal))
    closed = stream.close(tmp_project, "ship")
    assert closed.name == "__02-@ship__"
    assert (tmp_project / ".tide/arcs/01-ship").is_dir()  # the plain arc untouched


def test_close_then_new_never_reuses_number(tmp_project):
    entry = stream.new_arc(tmp_project, "alpha")
    (entry / "output" / "r.md").write_text("x", encoding="utf-8")
    strip_placeholders(entry / "arc.md")
    stream.close(tmp_project, "alpha")
    nxt = stream.new_arc(tmp_project, "beta")
    assert nxt.name == "02-beta"  # 01 consumed by the closed arc


# --- reopen ----------------------------------------------------------------

def test_reopen_reverses_close(tmp_project):
    entry = stream.new_arc(tmp_project, "alpha")
    (entry / "output" / "r.md").write_text("x", encoding="utf-8")
    strip_placeholders(entry / "arc.md")
    stream.close(tmp_project, "alpha")
    opened = stream.reopen(tmp_project, "alpha")
    assert opened.name == "01-alpha"
    assert not (tmp_project / ".tide/arcs/__01-alpha__").exists()
    assert fields.read_field(opened / "arc.md", "status") == "active"


def test_reopen_prefers_goal_over_arc(tmp_project):
    stream.new_arc(tmp_project, "ship")
    goal = stream.new_goal(tmp_project, "ship")
    (goal / "output" / "x.md").write_text("x", encoding="utf-8")
    strip_placeholders(stream.passport_path(goal))
    stream.close(tmp_project, "ship")  # closes the goal
    opened = stream.reopen(tmp_project, "ship")
    assert opened.name == "02-@ship"


# --- supersede -------------------------------------------------------------

def test_supersede_arc_links_old_new_and_seeds_from(tmp_project):
    stream.new_arc(tmp_project, "old-plan")
    entry = stream.supersede(tmp_project, "old-plan", "new-plan")
    # old closed (no output guard needed), new created same kind
    assert (tmp_project / ".tide/arcs/__01-old-plan__").is_dir()
    assert entry.name == "02-new-plan"
    doc = entry / "arc.md"
    assert fields.read_field(doc, "supersedes") == "old-plan"
    # supersedes sits right after status:
    lines = doc.read_text(encoding="utf-8").splitlines()
    si = next(i for i, ln in enumerate(lines) if ln.startswith("status:"))
    assert lines[si + 1] == "supersedes: old-plan"
    # from-seed written into input/
    seed = entry / "input" / "from-old-plan.md"
    assert seed.is_file()
    assert "supersedes old-plan" in seed.read_text(encoding="utf-8")


def test_supersede_reads_via_prev_alias(tmp_project):
    stream.new_arc(tmp_project, "old")
    entry = stream.supersede(tmp_project, "old", "new")
    # written as supersedes:, readable through the prev: alias
    assert fields.read_field(entry / "arc.md", "prev") == "old"


def test_supersede_preserves_goal_kind(tmp_project):
    stream.new_goal(tmp_project, "old-goal")
    entry = stream.supersede(tmp_project, "old-goal", "new-goal")
    assert entry.name == "02-@new-goal"
    doc = entry / "new-goal-goal.md"
    assert doc.is_file()
    assert fields.read_field(doc, "supersedes") == "old-goal"
    assert "This goal supersedes old-goal" in (entry / "input" / "from-old-goal.md").read_text(encoding="utf-8")


def test_supersede_tolerates_wrapped_old_ref(tmp_project):
    stream.new_arc(tmp_project, "old")
    entry = stream.supersede(tmp_project, "__old__", "new")
    assert (tmp_project / ".tide/arcs/__01-old__").is_dir()
    assert fields.read_field(entry / "arc.md", "supersedes") == "old"


# --- rm / abort (F8) -------------------------------------------------------

def test_rm_removes_stray_arc_with_empty_output(tmp_project):
    # F8: a probe arc with nothing in output/ is removable with no -f.
    entry = stream.new_arc(tmp_project, "probe")
    removed = stream.rm(tmp_project, "probe")
    assert removed.name == "01-probe"
    assert not removed.exists()
    assert not (tmp_project / ".tide/arcs/01-probe").exists()


def test_rm_missing_arc_raises(tmp_project):
    with pytest.raises(stream.StreamError):
        stream.rm(tmp_project, "ghost")


def test_rm_refuses_non_empty_output_without_force(tmp_project):
    # F8: a non-empty output/ is auditable content — refuse without -f.
    entry = stream.new_arc(tmp_project, "beta")
    (entry / "output" / "r.md").write_text("done", encoding="utf-8")
    with pytest.raises(stream.StreamError):
        stream.rm(tmp_project, "beta")
    assert entry.is_dir()  # untouched


def test_rm_force_removes_non_empty_output(tmp_project):
    # F8: -f overrides the non-empty-output guard.
    entry = stream.new_arc(tmp_project, "beta")
    (entry / "output" / "r.md").write_text("done", encoding="utf-8")
    removed = stream.rm(tmp_project, "beta", force=True)
    assert not removed.exists()


def test_rm_removes_closed_arc(tmp_project):
    # F8: a closed arc (no merged delta, unreferenced) is removable too.
    entry = stream.new_arc(tmp_project, "alpha")
    (entry / "output" / "r.md").write_text("x", encoding="utf-8")
    strip_placeholders(entry / "arc.md")
    closed = stream.close(tmp_project, "alpha")
    removed = stream.rm(tmp_project, "alpha", force=True)
    assert removed.name == "__01-alpha__"
    assert not (tmp_project / ".tide/arcs/__01-alpha__").exists()


def test_rm_refuses_merged_delta_even_with_force(tmp_project):
    # F8: a delta merged into canon is history — never droppable, -f included.
    from tide.canon import merge

    entry = stream.new_arc(tmp_project, "gamma")
    (entry / "delta.md").write_text("# delta\n## What it is\nhi\n", encoding="utf-8")
    merge.mark_merged(entry / "delta.md")
    with pytest.raises(stream.StreamError) as ei:
        stream.rm(tmp_project, "gamma", force=True)
    assert "canon" in str(ei.value)
    assert entry.is_dir()


def test_rm_allows_unmerged_delta(tmp_project):
    # F8: an UNMERGED delta is just scratch — not an integrity anchor, removable.
    entry = stream.new_arc(tmp_project, "delta-arc")
    (entry / "delta.md").write_text("# delta\n## What it is\nwip\n", encoding="utf-8")
    removed = stream.rm(tmp_project, "delta-arc")
    assert not removed.exists()


def test_rm_refuses_referenced_arc_even_with_force(tmp_project):
    # F8: a superseded arc is referenced by its successor's supersedes: chain.
    stream.new_arc(tmp_project, "old1")
    stream.supersede(tmp_project, "old1", "new1")  # 02-new1 supersedes: old1
    with pytest.raises(stream.StreamError) as ei:
        stream.rm(tmp_project, "old1", force=True)
    assert "referenced" in str(ei.value)
    assert (tmp_project / ".tide/arcs/__01-old1__").is_dir()


def test_rm_referrer_then_old_unblocks_removal(tmp_project):
    # F8: removing the successor first frees the old arc to be removed.
    stream.new_arc(tmp_project, "old1")
    new = stream.supersede(tmp_project, "old1", "new1")
    stream.rm(tmp_project, "new1", force=True)  # successor gone (output empty)
    removed = stream.rm(tmp_project, "old1", force=True)
    assert not removed.exists()


def test_rm_goal_with_subarc_needs_force(tmp_project):
    # F8: a goal carrying nested sub-arcs is real work — refuse without -f.
    stream.new_goal(tmp_project, "ship")
    stream.new_arc(tmp_project, "wire", goal_slug="ship")
    with pytest.raises(stream.StreamError):
        stream.rm(tmp_project, "ship")
    removed = stream.rm(tmp_project, "ship", force=True)
    assert removed.name == "01-@ship"
    assert not removed.exists()


def test_rm_goal_refuses_when_subarc_delta_merged(tmp_project):
    # F8: a goal whose sub-arc merged a delta is canon-anchored — refuse, -f too.
    from tide.canon import merge

    stream.new_goal(tmp_project, "ship")
    sub = stream.new_arc(tmp_project, "wire", goal_slug="ship")
    (sub / "delta.md").write_text("# delta\n## What it is\nx\n", encoding="utf-8")
    merge.mark_merged(sub / "delta.md")
    with pytest.raises(stream.StreamError):
        stream.rm(tmp_project, "ship", force=True)
    assert (tmp_project / ".tide/arcs/01-@ship").is_dir()


def test_rm_prefers_goal_over_arc_same_slug(tmp_project):
    # F8: resolution matches close/reopen — goal wins when both share a slug.
    stream.new_arc(tmp_project, "ship")    # 01-ship
    stream.new_goal(tmp_project, "ship")   # 02-@ship
    removed = stream.rm(tmp_project, "ship", force=True)
    assert removed.name == "02-@ship"
    assert (tmp_project / ".tide/arcs/01-ship").is_dir()  # the plain arc untouched

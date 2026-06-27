"""U7 unit — canon-sync engine: stamp / bump / drift_check / block-unmerged.

The load-bearing tide-new discipline (build-blueprint sync_hook): the canon-rev
becomes a drift anchor + a between-arcs barrier. Covers the pure helpers and the
two DONE-WHEN scenarios — drift surfaces after a merge, and the block fires when
a closed arc carries a non-empty unmerged delta.
"""

from __future__ import annotations

import pytest

from tide import fields, paths, sync
from tide.arc import stream
from tide.canon import merge, rev

from tests.conftest import strip_placeholders


# --- helpers ---------------------------------------------------------------

def _write_delta(arc_dir, body, *, merged="no"):
    (arc_dir / "delta.md").write_text(
        "# delta — {0}\nmerged: {1}\n\n{2}\n".format(arc_dir.name, merged, body),
        encoding="utf-8",
    )


def _closed_arc_with_delta(root, slug_name, body="patched the valve"):
    """Create an arc, give it output + a non-empty unmerged delta, then close it."""
    entry = stream.new_arc(root, slug_name)
    (entry / "output" / "r.md").write_text("done", encoding="utf-8")
    _write_delta(entry, body)
    strip_placeholders(entry / "arc.md")
    return stream.close(root, slug_name)


# --- stamp / bump ----------------------------------------------------------

def test_stamp_writes_current_canon_rev(tmp_project):
    entry = stream.new_arc(tmp_project, "alpha")
    # move the canon so the stamp must be recomputed, then re-stamp via sync.
    canon = paths.canon_file(tmp_project)
    canon.write_text(canon.read_text(encoding="utf-8") + "\nmoved\n", encoding="utf-8")
    r = sync.stamp(entry, tmp_project)
    assert r == rev.compute(tmp_project)
    assert fields.read_field(entry / "arc.md", "canon-rev") == r


def test_stamp_writes_into_goal_passport(tmp_project):
    goal = stream.new_goal(tmp_project, "ship")
    r = sync.stamp(goal, tmp_project)
    assert fields.read_field(goal / "ship-goal.md", "canon-rev") == r


def test_bump_returns_current_rev(tmp_project):
    assert sync.bump(tmp_project) == rev.compute(tmp_project)


def test_bump_tracks_a_merge(tmp_project):
    before = sync.bump(tmp_project)
    entry = stream.new_arc(tmp_project, "alpha")
    _write_delta(entry, "real change")
    merge.merge_delta(tmp_project, entry, slug="alpha")
    assert sync.bump(tmp_project) != before
    assert sync.bump(tmp_project) == rev.compute(tmp_project)


# --- drift check -----------------------------------------------------------

def test_drift_check_no_drift_when_canon_unchanged(tmp_project):
    entry = stream.new_arc(tmp_project, "alpha")
    res = sync.drift_check(entry, tmp_project)
    assert res.drifted is False
    assert res.stamped == res.current
    assert sync.has_drifted(entry, tmp_project) is False


def test_drift_check_reports_drift_after_merging_another_arc(tmp_project):
    # DONE-WHEN: stamp arc A, then merge a *different* arc → A has drifted.
    a = stream.new_arc(tmp_project, "alpha")          # stamped at rev r0
    stamped0 = fields.read_field(a / "arc.md", "canon-rev")

    b = stream.new_arc(tmp_project, "beta")           # b is open, not closed
    _write_delta(b, "beta moves the canon")
    merge.merge_delta(tmp_project, b, slug="beta")    # canon journal grows → r1

    res = sync.drift_check(a, tmp_project)
    assert res.drifted is True
    assert res.stamped == stamped0
    assert res.current == rev.compute(tmp_project)
    assert res.stamped != res.current


def test_drift_check_none_when_arc_unstamped(tmp_project):
    entry = paths.arcs_dir(tmp_project) / "01-bare"
    entry.mkdir(parents=True)
    (entry / "arc.md").write_text(
        "# 01-bare\ngoal: x\nstatus: active\n", encoding="utf-8"
    )
    res = sync.drift_check(entry, tmp_project)
    assert res.stamped is None
    assert res.drifted is False


# --- is_unmerged_delta (pure) ----------------------------------------------

def test_is_unmerged_delta_truth_table(tmp_path):
    d = tmp_path / "delta.md"
    assert sync.is_unmerged_delta(d) is False  # missing file
    d.write_text("# delta — x\nmerged: no\n\n", encoding="utf-8")
    assert sync.is_unmerged_delta(d) is False  # frontmatter only → empty body
    d.write_text("# delta — x\nmerged: no\n\nreal body\n", encoding="utf-8")
    assert sync.is_unmerged_delta(d) is True
    d.write_text("# delta — x\nmerged: yes\n\nreal body\n", encoding="utf-8")
    assert sync.is_unmerged_delta(d) is False  # already merged


# --- unmerged_deltas scan --------------------------------------------------

def test_unmerged_deltas_lists_closed_offenders_by_default(tmp_project):
    closed = _closed_arc_with_delta(tmp_project, "alpha")
    offenders = sync.unmerged_deltas(tmp_project)
    assert offenders == [closed]


def test_unmerged_deltas_default_ignores_open_arc_delta(tmp_project):
    # Default scan is closed-only — the board / edit-gate / session-start view.
    entry = stream.new_arc(tmp_project, "alpha")
    _write_delta(entry, "work in progress")  # arc still OPEN
    assert sync.unmerged_deltas(tmp_project) == []


def test_unmerged_deltas_include_active_sees_open_arc_delta(tmp_project):
    # F1: with include_active the open arc's written delta is an offender too.
    entry = stream.new_arc(tmp_project, "alpha")
    _write_delta(entry, "work in progress")  # arc still OPEN
    assert sync.unmerged_deltas(tmp_project, include_active=True) == [entry]


def test_unmerged_deltas_include_active_ignores_empty_open_delta(tmp_project):
    # An open arc whose delta is frontmatter-only is NOT an offender.
    entry = stream.new_arc(tmp_project, "alpha")
    _write_delta(entry, "")  # empty body
    assert sync.unmerged_deltas(tmp_project, include_active=True) == []


def test_unmerged_deltas_empty_on_clean_stream(tmp_project):
    stream.new_arc(tmp_project, "alpha")
    assert sync.unmerged_deltas(tmp_project) == []


def test_unmerged_deltas_sees_goal_substream(tmp_project):
    stream.new_goal(tmp_project, "ship")
    sub = stream.new_arc(tmp_project, "wire", goal_slug="ship")
    (sub / "output" / "r.md").write_text("x", encoding="utf-8")
    _write_delta(sub, "sub-arc delta")
    strip_placeholders(sub / "arc.md")
    closed = stream.close(tmp_project, "wire", goal_slug="ship")
    assert sync.unmerged_deltas(tmp_project) == [closed]


# --- block_new_arc_if_unmerged_delta ---------------------------------------

def test_block_fires_when_closed_arc_has_unmerged_delta(tmp_project):
    # DONE-WHEN: closed arc with a non-empty unmerged delta blocks a new arc.
    _closed_arc_with_delta(tmp_project, "alpha")
    with pytest.raises(sync.SyncError):
        sync.block_new_arc_if_unmerged_delta(tmp_project)


def test_block_fires_when_active_arc_has_unmerged_delta(tmp_project):
    # F1 DONE-WHEN: an ACTIVE arc with a written-but-unmerged delta blocks too —
    # this is the happy-path hole the old closed-only scan left open.
    entry = stream.new_arc(tmp_project, "alpha")
    _write_delta(entry, "work in progress")  # arc still OPEN
    with pytest.raises(sync.SyncError):
        sync.block_new_arc_if_unmerged_delta(tmp_project)


def test_block_no_raise_on_clean_stream(tmp_project):
    stream.new_arc(tmp_project, "alpha")
    sync.block_new_arc_if_unmerged_delta(tmp_project)  # must not raise


def test_block_no_raise_when_active_delta_is_empty(tmp_project):
    # An open arc carrying only an empty (frontmatter-only) delta is fine.
    entry = stream.new_arc(tmp_project, "alpha")
    _write_delta(entry, "")
    sync.block_new_arc_if_unmerged_delta(tmp_project)  # must not raise


def test_block_clears_after_merge(tmp_project):
    closed = _closed_arc_with_delta(tmp_project, "alpha")
    merge.merge_delta(tmp_project, closed, slug="alpha")  # fold + mark merged
    sync.block_new_arc_if_unmerged_delta(tmp_project)     # barrier lifts
    nxt = stream.new_arc(tmp_project, "beta")
    assert nxt.name == "02-beta"  # 01 consumed by the closed arc


def test_block_still_fires_after_reopen(tmp_project):
    # F1: reopening un-wraps __…__ → an ACTIVE arc that still owes a merge. The
    # barrier now scans active arcs too, so the unmerged delta keeps blocking
    # until it is actually merged (the old closed-only scan wrongly lifted here).
    _closed_arc_with_delta(tmp_project, "alpha")
    stream.reopen(tmp_project, "alpha")
    with pytest.raises(sync.SyncError):
        sync.block_new_arc_if_unmerged_delta(tmp_project)


def test_sync_error_is_a_stream_error():
    # so cli.main catches it on the StreamError arm (prints tide: …, exits 1).
    assert issubclass(sync.SyncError, stream.StreamError)


# --- integration: barrier wired into stream.new / stream.open --------------

def test_new_arc_blocked_by_unmerged_closed_delta(tmp_project):
    _closed_arc_with_delta(tmp_project, "alpha")
    with pytest.raises(sync.SyncError):
        stream.new_arc(tmp_project, "beta")


def test_open_arc_blocked_by_unmerged_closed_delta(tmp_project):
    other = stream.new_arc(tmp_project, "beta")  # an open arc to try to re-enter
    (other / "output" / "x.md").write_text("x", encoding="utf-8")
    _closed_arc_with_delta(tmp_project, "alpha")
    with pytest.raises(sync.SyncError):
        stream.open_arc(tmp_project, "beta")


def test_new_arc_succeeds_once_delta_merged(tmp_project):
    closed = _closed_arc_with_delta(tmp_project, "alpha")
    merge.merge_delta(tmp_project, closed, slug="alpha")
    nxt = stream.new_arc(tmp_project, "beta")  # barrier lifted → succeeds
    assert nxt.is_dir()


def test_new_arc_blocked_by_unmerged_ACTIVE_delta(tmp_project):
    # F1: the still-open arc holds a written delta → no 2nd concurrent arc.
    entry = stream.new_arc(tmp_project, "alpha")
    _write_delta(entry, "patched the valve")  # arc stays OPEN
    with pytest.raises(sync.SyncError):
        stream.new_arc(tmp_project, "beta")


def test_open_arc_blocked_by_unmerged_ACTIVE_delta(tmp_project):
    other = stream.new_arc(tmp_project, "beta")  # an open arc to re-enter
    (other / "output" / "x.md").write_text("x", encoding="utf-8")
    a = stream.new_arc(tmp_project, "alpha")
    _write_delta(a, "patched the valve")  # active arc owes a merge
    with pytest.raises(sync.SyncError):
        stream.open_arc(tmp_project, "beta")


def test_new_arc_blocked_by_active_delta_in_goal_substream(tmp_project):
    stream.new_goal(tmp_project, "ship")
    sub = stream.new_arc(tmp_project, "wire", goal_slug="ship")
    _write_delta(sub, "sub-arc delta")  # active sub-arc owes a merge
    with pytest.raises(sync.SyncError):
        stream.new_arc(tmp_project, "beta")

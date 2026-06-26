"""U8 unit — the STREAM board renderer (computed N/M + CANDIDATES + drift/unmerged)."""

from __future__ import annotations

from tide import fields, paths
from tide.arc import board, candidate, stream
from tide.cannon import rev

from tests.conftest import strip_placeholders


def _set_goal(passport_path, text):
    fields.set_field(passport_path, "goal", text)


# --- goal badge (computed N/M, never hand-ticked) --------------------------

def test_goal_badge_counts_closed_over_total(tmp_project):
    stream.new_goal(tmp_project, "ship")
    stream.new_arc(tmp_project, "wire", goal_slug="ship")   # 01-wire (open)
    stream.new_arc(tmp_project, "test", goal_slug="ship")   # 02-test (open)
    stream.close(tmp_project, "wire", goal_slug="ship", force=True)
    goal_dir = paths.arcs_dir(tmp_project) / "01-@ship"
    assert board.goal_badge(goal_dir) == (1, 2)


def test_goal_badge_none_for_zero_subarcs(tmp_project):
    # empty badge for a zero-sub-arc goal — never 0/0
    goal_dir = stream.new_goal(tmp_project, "fresh")
    assert board.goal_badge(goal_dir) is None


def test_zero_subarc_goal_has_no_badge_suffix(tmp_project):
    stream.new_goal(tmp_project, "fresh")
    out = board.render_board(tmp_project)
    line = next(ln for ln in out.splitlines() if "01-@fresh" in ln)
    assert "/" not in line  # no N/M badge rendered


# --- full STREAM snapshot --------------------------------------------------

def test_render_board_full_snapshot(tmp_project):
    a = stream.new_arc(tmp_project, "alpha")
    _set_goal(a / "arc.md", "fix the leak")

    g = stream.new_goal(tmp_project, "ship")
    _set_goal(g / "ship-goal.md", "ship it")

    sub1 = stream.new_arc(tmp_project, "wire", goal_slug="ship")
    _set_goal(sub1 / "arc.md", "wiring")
    sub2 = stream.new_arc(tmp_project, "test", goal_slug="ship")
    _set_goal(sub2 / "arc.md", "testing")
    stream.close(tmp_project, "wire", goal_slug="ship", force=True)

    candidate.new_candidate(tmp_project, "idea", from_arc="alpha", body="an idea")

    expected = (
        "STREAM\n"
        "  01-alpha  [active]  fix the leak\n"
        "  02-@ship  [active]  ship it  (1/2 ✓)\n"
        "    ✓ __01-wire__  wiring\n"
        "    ○ 02-test  [active]  testing\n"
        "\n"
        "CANDIDATES\n"
        "  01-idea  from alpha\n"
        "\n"
        "HEALTH\n"
        "  cannon-rev: {rev}\n"
        "  unmerged: none\n"
        "  drift: none\n"
        "  deferred: none"
    ).format(rev=rev.compute(tmp_project))
    assert board.render_board(tmp_project) == expected


def test_render_board_empty_stream(tmp_project):
    expected = (
        "STREAM\n"
        "  (empty stream)\n"
        "\n"
        "HEALTH\n"
        "  cannon-rev: {rev}\n"
        "  unmerged: none\n"
        "  drift: none\n"
        "  deferred: none"
    ).format(rev=rev.compute(tmp_project))
    assert board.render_board(tmp_project) == expected


# --- drift flag (tide net-new) ---------------------------------------------

def test_open_arc_flags_drift_when_cannon_moves(tmp_project):
    a = stream.new_arc(tmp_project, "alpha")
    _set_goal(a / "arc.md", "do it")
    # move the cannon under the arc WITHOUT restamping (no open_arc) → drift
    canon = paths.canon_file(tmp_project)
    canon.write_text(canon.read_text(encoding="utf-8") + "\nmoved\n", encoding="utf-8")
    out = board.render_board(tmp_project)
    line = next(ln for ln in out.splitlines() if "01-alpha" in ln)
    assert board.DRIFT_FLAG in line


def test_closed_arc_does_not_flag_drift(tmp_project):
    a = stream.new_arc(tmp_project, "alpha")
    (a / "output" / "r.md").write_text("x", encoding="utf-8")
    strip_placeholders(a / "arc.md")
    stream.close(tmp_project, "alpha")
    canon = paths.canon_file(tmp_project)
    canon.write_text(canon.read_text(encoding="utf-8") + "\nmoved\n", encoding="utf-8")
    out = board.render_board(tmp_project)
    line = next(ln for ln in out.splitlines() if "__01-alpha__" in ln)
    assert board.DRIFT_FLAG not in line


# --- unmerged-delta barrier flag (tide net-new) ----------------------------

def test_unmerged_delta_is_flagged(tmp_project):
    a = stream.new_arc(tmp_project, "leak")
    (a / "output" / "r.md").write_text("x", encoding="utf-8")
    (a / "delta.md").write_text("# delta — leak\nmerged: no\n\npatched the leak\n", encoding="utf-8")
    strip_placeholders(a / "arc.md")
    stream.close(tmp_project, "leak")  # closed dir still carries an unmerged delta
    out = board.render_board(tmp_project)
    assert "UNMERGED DELTAS" in out
    assert "tide cannon merge leak" in out


# --- merge-health footer (tide net-new, fix F4) ----------------------------

def test_health_footer_always_rendered_when_clean(tmp_project):
    # explicit even at zero — silence is ambiguous (clean vs un-checked)
    stream.new_arc(tmp_project, "alpha")
    out = board.render_board(tmp_project)
    assert "HEALTH" in out
    assert "cannon-rev: {0}".format(rev.compute(tmp_project)) in out
    assert "unmerged: none" in out
    assert "drift: none" in out


def test_health_footer_present_on_empty_stream(tmp_project):
    out = board.render_board(tmp_project)
    assert "HEALTH" in out
    assert "unmerged: none" in out
    assert "drift: none" in out


def test_health_footer_reports_unmerged_count_and_arcs(tmp_project):
    a = stream.new_arc(tmp_project, "leak")
    (a / "output" / "r.md").write_text("x", encoding="utf-8")
    (a / "delta.md").write_text(
        "# delta — leak\nmerged: no\n\npatched the leak\n", encoding="utf-8"
    )
    strip_placeholders(a / "arc.md")
    stream.close(tmp_project, "leak")  # closed dir still carries an unmerged delta
    out = board.render_board(tmp_project)
    health = out[out.index("HEALTH"):]
    assert "unmerged: 1 delta(s) (__01-leak__)" in health


def test_health_footer_lists_drifted_open_arcs(tmp_project):
    a = stream.new_arc(tmp_project, "alpha")
    _set_goal(a / "arc.md", "do it")
    # move the cannon under the open arc WITHOUT restamping → drift
    canon = paths.canon_file(tmp_project)
    canon.write_text(canon.read_text(encoding="utf-8") + "\nmoved\n", encoding="utf-8")
    out = board.render_board(tmp_project)
    health = out[out.index("HEALTH"):]
    assert "drift: 01-alpha" in health
    # footer rev must be the post-move (current) rev, matching the drift readout
    assert "cannon-rev: {0}".format(rev.compute(tmp_project)) in health


def test_health_footer_drift_includes_subarcs(tmp_project):
    stream.new_goal(tmp_project, "ship")
    stream.new_arc(tmp_project, "wire", goal_slug="ship")  # open sub-arc, stamped
    canon = paths.canon_file(tmp_project)
    canon.write_text(canon.read_text(encoding="utf-8") + "\nmoved\n", encoding="utf-8")
    out = board.render_board(tmp_project)
    health = out[out.index("HEALTH"):]
    assert "01-wire" in health.split("drift:")[1]


# --- supersede link --------------------------------------------------------

def test_supersedes_link_shown(tmp_project):
    stream.new_arc(tmp_project, "old")
    stream.supersede(tmp_project, "old", "new")
    out = board.render_board(tmp_project)
    line = next(ln for ln in out.splitlines() if " 02-new" in ln)
    assert "(supersedes old)" in line

"""U8 unit — the STREAM board renderer (computed N/M + CANDIDATES + drift/unmerged)."""

from __future__ import annotations

from tide import fields, paths, readme
from tide.arc import board, candidate, stream
from tide.canon import rev

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
        "  canon-rev: {rev}\n"
        "  unmerged: none\n"
        "  drift: none\n"
        "  deferred: none\n"
        "  readme: drift (run 'tide readme')"
    ).format(rev=rev.compute(tmp_project))
    assert board.render_board(tmp_project) == expected


def test_render_board_empty_stream(tmp_project):
    expected = (
        "STREAM\n"
        "  (empty stream)\n"
        "\n"
        "HEALTH\n"
        "  canon-rev: {rev}\n"
        "  unmerged: none\n"
        "  drift: none\n"
        "  deferred: none\n"
        "  readme: drift (run 'tide readme')"
    ).format(rev=rev.compute(tmp_project))
    assert board.render_board(tmp_project) == expected


# --- drift flag (tide net-new) ---------------------------------------------

def test_open_arc_flags_drift_when_canon_moves(tmp_project):
    a = stream.new_arc(tmp_project, "alpha")
    _set_goal(a / "arc.md", "do it")
    # move the canon under the arc WITHOUT restamping (no open_arc) → drift
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
    assert "tide canon merge leak" in out


# --- merge-health footer (tide net-new, fix F4) ----------------------------

def test_health_footer_always_rendered_when_clean(tmp_project):
    # explicit even at zero — silence is ambiguous (clean vs un-checked)
    stream.new_arc(tmp_project, "alpha")
    out = board.render_board(tmp_project)
    assert "HEALTH" in out
    assert "canon-rev: {0}".format(rev.compute(tmp_project)) in out
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
    # move the canon under the open arc WITHOUT restamping → drift
    canon = paths.canon_file(tmp_project)
    canon.write_text(canon.read_text(encoding="utf-8") + "\nmoved\n", encoding="utf-8")
    out = board.render_board(tmp_project)
    health = out[out.index("HEALTH"):]
    assert "drift: 01-alpha" in health
    # footer rev must be the post-move (current) rev, matching the drift readout
    assert "canon-rev: {0}".format(rev.compute(tmp_project)) in health


def test_health_footer_drift_includes_subarcs(tmp_project):
    stream.new_goal(tmp_project, "ship")
    stream.new_arc(tmp_project, "wire", goal_slug="ship")  # open sub-arc, stamped
    canon = paths.canon_file(tmp_project)
    canon.write_text(canon.read_text(encoding="utf-8") + "\nmoved\n", encoding="utf-8")
    out = board.render_board(tmp_project)
    health = out[out.index("HEALTH"):]
    # qualified thread/session — a bare "01-wire" cannot be told from another
    # thread's "01-wire" (live board had four indistinguishable "02-priem")
    assert "01-@ship/01-wire" in health.split("drift:")[1]


# --- drift under a SEALED thread is history, not live work -----------------

def _move_canon(tmp_project):
    """Advance the canon rev without restamping anything → everything open drifts."""
    canon = paths.canon_file(tmp_project)
    canon.write_text(canon.read_text(encoding="utf-8") + "\nmoved\n", encoding="utf-8")


def test_drift_skips_sessions_under_a_closed_thread(tmp_project):
    """A sub-arc under a sealed thread is finished history — never drift.

    Live regression: 19 of the board's 23 drift rows sat under ``__…__`` threads
    that cold entry does not even render, so the line pointed at nothing.
    """
    stream.new_goal(tmp_project, "ship")
    stream.new_arc(tmp_project, "wire", goal_slug="ship")  # open dir, sealed parent
    stream.close(tmp_project, "ship", force=True)
    _move_canon(tmp_project)

    drifted = board._drifted_entries(tmp_project, rev.compute(tmp_project))
    assert drifted == []
    assert "drift: none" in board.render_board(tmp_project)


def test_drift_keeps_sessions_under_an_open_thread(tmp_project):
    """The mirror case: the same sub-arc under a LIVE thread still drifts."""
    stream.new_goal(tmp_project, "ship")
    stream.new_arc(tmp_project, "wire", goal_slug="ship")
    _move_canon(tmp_project)

    labels = [
        board.drift_label(tmp_project, d)
        for d in board._drifted_entries(tmp_project, rev.compute(tmp_project))
    ]
    assert labels == ["01-@ship", "01-@ship/01-wire"]


def test_closed_thread_subarc_has_no_inline_drift_flag(tmp_project):
    """STREAM agrees with the footer: no ⚠ drift on a sealed thread's session."""
    stream.new_goal(tmp_project, "ship")
    stream.new_arc(tmp_project, "wire", goal_slug="ship")
    stream.close(tmp_project, "ship", force=True)
    _move_canon(tmp_project)

    out = board.render_board(tmp_project)
    line = next(ln for ln in out.splitlines() if "01-wire" in ln)
    assert board.DRIFT_FLAG not in line


def test_drift_label_qualifies_subarc_but_not_top_entry(tmp_project):
    stream.new_goal(tmp_project, "ship")
    sub = stream.new_arc(tmp_project, "wire", goal_slug="ship")
    top = stream.new_arc(tmp_project, "alpha")
    assert board.drift_label(tmp_project, top) == "02-alpha"
    assert board.drift_label(tmp_project, sub) == "01-@ship/01-wire"


def test_drift_line_caps_long_list_with_honest_remainder(tmp_project):
    """Past the cap the line counts the rest out loud — never a silent trim."""
    over = board.DRIFT_LIST_MAX + 3
    for i in range(over):
        stream.new_arc(tmp_project, "arc{0}".format(i))
    _move_canon(tmp_project)

    out = board.render_board(tmp_project)
    drift_line = next(ln for ln in out.splitlines() if ln.strip().startswith("drift:"))
    assert "+3 more" in drift_line
    assert board.DRIFT_FLAG in drift_line          # points at the full list in STREAM
    # and every one of them is still individually flagged up in STREAM
    assert out.count(board.DRIFT_FLAG) == over + 1  # per-arc flags + the footer hint


def test_drift_line_lists_all_names_at_the_cap(tmp_project):
    for i in range(board.DRIFT_LIST_MAX):
        stream.new_arc(tmp_project, "arc{0}".format(i))
    _move_canon(tmp_project)

    drift_line = next(
        ln for ln in board.render_board(tmp_project).splitlines()
        if ln.strip().startswith("drift:")
    )
    assert "more" not in drift_line


def test_status_dict_drift_matches_the_footer(tmp_project):
    """The JSON twin reports the same qualified, sealed-filtered list."""
    stream.new_goal(tmp_project, "ship")
    stream.new_arc(tmp_project, "wire", goal_slug="ship")
    stream.new_goal(tmp_project, "old")
    stream.new_arc(tmp_project, "ghost", goal_slug="old")
    stream.close(tmp_project, "old", force=True)
    _move_canon(tmp_project)

    health = board.project_status_dict(tmp_project)["health"]
    assert health["drifted_entries"] == ["01-@ship", "01-@ship/01-wire"]


# --- supersede link --------------------------------------------------------

def test_supersedes_link_shown(tmp_project):
    stream.new_arc(tmp_project, "old")
    stream.supersede(tmp_project, "old", "new")
    out = board.render_board(tmp_project)
    line = next(ln for ln in out.splitlines() if " 02-new" in ln)
    assert "(supersedes old)" in line


# --- readme drift in HEALTH footer (criterion F) ---------------------------

def test_health_footer_readme_ok_when_current(tmp_project):
    """HEALTH shows 'readme: ok' after the README has been generated and is current."""
    readme.generate(tmp_project)
    out = board.render_board(tmp_project)
    health = out[out.index("HEALTH"):]
    assert "readme: ok" in health


def test_health_footer_readme_drift_when_missing(tmp_project):
    """HEALTH shows 'readme: drift' when no README has been generated yet (code 1)."""
    out = board.render_board(tmp_project)
    health = out[out.index("HEALTH"):]
    assert "readme: drift" in health


def test_health_footer_readme_drift_when_stale_after_canon_move(tmp_project):
    """HEALTH shows 'readme: drift' when canon moved ahead of the generated README."""
    readme.generate(tmp_project)
    canon = paths.canon_file(tmp_project)
    canon.write_text(
        canon.read_text(encoding="utf-8") + "\n### extra-entry\nmoved\n",
        encoding="utf-8",
    )
    out = board.render_board(tmp_project)
    health = out[out.index("HEALTH"):]
    assert "readme: drift" in health


def test_health_footer_readme_transitions_ok_to_drift(tmp_project):
    """HEALTH transitions from ok → drift when the README becomes stale."""
    readme.generate(tmp_project)
    # confirm ok first
    assert "readme: ok" in board.render_board(tmp_project)
    # mangle the README body (body drift: hand-edited)
    target = readme.readme_file(tmp_project)
    target.write_text("# hacked\n", encoding="utf-8")
    # now expect drift
    assert "readme: drift" in board.render_board(tmp_project)

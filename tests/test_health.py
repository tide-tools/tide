"""tide.health — the tier-0 Светофор: the four counts, the tristate, the budget.

Each headline number gets a probe-it-in-isolation test (unread / canon-debt /
offers / roster-not-ready), the tristate is exercised across green/yellow/red
including the stale-offer boundary, and a wall-clock test asserts the whole line
computes well under the 50ms entry budget on a fixture. The counters are all
defensive-by-contract, so the "broken corner counts as 0" path is covered too.
"""

from __future__ import annotations

import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tide import health

from tests.conftest import build_tide_skeleton


# --- git helpers (mirror tests/test_arc_worktree.py) -----------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    )


def _make_git_project(root: Path, *, commit: bool = True) -> Path:
    """A git repo (optionally with an initial commit) — the worktree-ready shape."""
    _git(root, "init")
    _git(root, "config", "user.email", "t@tide.local")
    _git(root, "config", "user.name", "Tide Test")
    if commit:
        (root / "seed.txt").write_text("x\n", encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-m", "init")
    return root


def _closed_arc_with_unmerged_delta(project: Path, num: int, slug: str) -> None:
    """Drop a CLOSED arc (``__NN-slug__``) carrying an unmerged, non-empty delta.

    Built directly on disk (not via ``stream.new_arc`` → ``close``) so a fixture
    can stack SEVERAL of them: the between-arcs barrier deliberately refuses to
    open a 2nd arc while the 1st owes a merge, which is exactly the debt we count.
    """
    arc = project / ".tide" / "arcs" / "__{0:02d}-{1}__".format(num, slug)
    arc.mkdir(parents=True, exist_ok=True)
    (arc / "arc.md").write_text("# {0}-{1}\n\nstatus: done\n".format(num, slug), encoding="utf-8")
    (arc / "delta.md").write_text(
        "# delta — {0}\n\nadded a thing.\n".format(slug), encoding="utf-8"
    )


# --- unread (отлив / lookback watermark) -----------------------------------


def test_unread_is_zero_on_a_nongit_project(tmp_project):
    # No git → lookback raises inside → swallowed → 0 (never a false red).
    assert health._count_unread(tmp_project) == 0


def test_unread_is_zero_when_never_marked(tmp_path):
    _make_git_project(tmp_path)
    build_tide_skeleton(tmp_path, name="demo")
    # A git repo with no watermark ref yet: gap → None → counted as 0.
    assert health._count_unread(tmp_path) == 0


def test_unread_counts_trace_commits_behind_the_watermark(tmp_path):
    from tide import lookback

    _make_git_project(tmp_path)
    build_tide_skeleton(tmp_path, name="demo")
    ref = lookback.ref_name(lookback.DEFAULT_READER, tmp_path.name)
    # Mark the watermark at HEAD, then add two trace commits under .tide/.
    lookback.mark(tmp_path, ref)
    for i in range(2):
        (tmp_path / ".tide" / "note{0}.md".format(i)).write_text("n\n", encoding="utf-8")
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-m", "trace {0}".format(i))
    assert health._count_unread(tmp_path) == 2


# --- canon_debt (closed arcs w/ unmerged delta) ----------------------------


def test_canon_debt_zero_on_clean_project(tmp_project):
    assert health._count_canon_debt(tmp_project) == 0


def test_canon_debt_counts_unmerged_closed_deltas(tmp_project):
    _closed_arc_with_unmerged_delta(tmp_project, 1, "alpha")
    _closed_arc_with_unmerged_delta(tmp_project, 2, "beta")
    assert health._count_canon_debt(tmp_project) == 2


# --- offers_waiting + stale ------------------------------------------------


def test_offers_zero_when_no_home(tmp_path):
    assert health._count_offers(None, datetime.now()) == (0, 0)


def test_offers_counts_offered_and_flags_stale(tmp_control_home):
    from tide import handoff_queue as hq

    hq.offer(tmp_control_home, "fresh", arc="-", project="-", seed="-")
    hq.offer(tmp_control_home, "old", arc="-", project="-", seed="-")
    # Backdate the second offer's created stamp past the stale threshold.
    old = hq.list_offers(tmp_control_home)[0]  # newest-first → "old" is num 02
    stale_ts = (datetime.now() - timedelta(days=health.STALE_DAYS + 1)).isoformat(
        timespec="seconds"
    )
    hq._set_field(old["path"], "created", stale_ts)

    waiting, stale = health._count_offers(tmp_control_home, datetime.now())
    assert waiting == 2
    assert stale == 1


def test_taken_offers_do_not_count_as_waiting(tmp_control_home):
    from tide import handoff_queue as hq

    hq.offer(tmp_control_home, "pass-it", arc="-", project="-", seed="-")
    hq.take(tmp_control_home, "pass-it", session="successor")
    waiting, stale = health._count_offers(tmp_control_home, datetime.now())
    assert waiting == 0
    assert stale == 0


# --- roster_not_ready (worktree readiness) ---------------------------------


def test_roster_ready_project_is_not_flagged(tmp_control_home, tmp_path):
    from tide import roster

    proj = tmp_path / "ready"
    proj.mkdir()
    _make_git_project(proj, commit=True)
    roster.add(tmp_control_home, "ready", str(proj))
    not_ready, total = health._count_roster_not_ready(tmp_control_home)
    assert (not_ready, total) == (0, 1)


def test_roster_flags_missing_and_uncommitted_and_nongit(tmp_control_home, tmp_path):
    from tide import roster

    missing = tmp_path / "gone"  # never created
    no_commit = tmp_path / "no-commit"
    no_commit.mkdir()
    _make_git_project(no_commit, commit=False)  # git init, no commit → not ready
    plain = tmp_path / "plain"
    plain.mkdir()  # a dir, not a git repo → not ready

    roster.add(tmp_control_home, "gone", str(missing))
    roster.add(tmp_control_home, "no-commit", str(no_commit))
    roster.add(tmp_control_home, "plain", str(plain))

    not_ready, total = health._count_roster_not_ready(tmp_control_home)
    assert (not_ready, total) == (3, 3)


def test_roster_skips_remote_and_archived(tmp_control_home, tmp_path):
    from tide import roster

    plain = tmp_path / "plain"
    plain.mkdir()  # would be not-ready if counted
    roster.add(tmp_control_home, "remote", str(plain), env="box")
    roster.add(tmp_control_home, "archived", str(plain), status="archived")
    not_ready, total = health._count_roster_not_ready(tmp_control_home)
    assert (not_ready, total) == (0, 0)  # both skipped


# --- worktree-ready primitive ----------------------------------------------


def test_worktree_ready_true_for_committed_repo(tmp_path):
    _make_git_project(tmp_path, commit=True)
    assert health._worktree_ready(tmp_path) is True


def test_worktree_ready_false_before_first_commit(tmp_path):
    _make_git_project(tmp_path, commit=False)
    assert health._worktree_ready(tmp_path) is False


def test_worktree_ready_false_for_missing_and_nongit(tmp_path):
    assert health._worktree_ready(tmp_path / "nope") is False
    plain = tmp_path / "plain"
    plain.mkdir()
    assert health._worktree_ready(plain) is False


# --- tristate --------------------------------------------------------------


def test_green_when_all_zero():
    hl = health.HealthLine(unread=0, canon_debt=0, offers_waiting=0, roster_not_ready=0)
    assert hl.severity == health.SEVERITY_GREEN
    assert hl.exit_code == 0


def test_yellow_when_unread_or_fresh_offers():
    assert (
        health.HealthLine(
            unread=3, canon_debt=0, offers_waiting=0, roster_not_ready=0
        ).severity
        == health.SEVERITY_YELLOW
    )
    assert (
        health.HealthLine(
            unread=0, canon_debt=0, offers_waiting=1, roster_not_ready=0, stale_offers=0
        ).severity
        == health.SEVERITY_YELLOW
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"canon_debt": 1},
        {"roster_not_ready": 1},
        {"offers_waiting": 1, "stale_offers": 1},
    ],
)
def test_red_on_any_rot(kwargs):
    base = dict(unread=0, canon_debt=0, offers_waiting=0, roster_not_ready=0)
    base.update(kwargs)
    hl = health.HealthLine(**base)
    assert hl.severity == health.SEVERITY_RED
    assert hl.exit_code == 2


def test_counts_structure_has_the_four_headline_keys():
    hl = health.HealthLine(unread=1, canon_debt=2, offers_waiting=3, roster_not_ready=4)
    assert hl.counts == {
        "unread": 1,
        "canon_debt": 2,
        "offers_waiting": 3,
        "roster_not_ready": 4,
    }


# --- render ----------------------------------------------------------------


def test_render_line_shows_glyph_and_four_numbers():
    hl = health.HealthLine(unread=1, canon_debt=2, offers_waiting=3, roster_not_ready=4)
    line = health.render_line(hl)
    assert line.startswith("🔴")  # canon_debt + roster rot
    for token in ("непрочитано 1", "правила 2", "передачи 3", "проекты 4"):
        assert token in line
    assert "гниёт" in line


def test_render_line_green_has_no_rot_tail():
    hl = health.HealthLine(unread=0, canon_debt=0, offers_waiting=0, roster_not_ready=0)
    line = health.render_line(hl)
    assert line.startswith("🟢")
    assert "гниёт" not in line


# --- aggregate + injection -------------------------------------------------


def test_compute_health_uses_injected_home_and_clock(tmp_project, tmp_control_home):
    from tide import handoff_queue as hq

    _closed_arc_with_unmerged_delta(tmp_project, 1, "alpha")
    hq.offer(tmp_control_home, "hang", arc="-", project="-", seed="-")

    hl = health.compute_health(tmp_project, home=tmp_control_home, now=datetime.now())
    assert hl.canon_debt == 1
    assert hl.offers_waiting == 1
    assert hl.severity == health.SEVERITY_RED  # canon debt is rot


def test_compute_health_home_none_zeroes_queue_and_roster(tmp_project):
    hl = health.compute_health(tmp_project, home=None)
    assert hl.offers_waiting == 0
    assert hl.roster_not_ready == 0


# --- performance budget ----------------------------------------------------


def test_health_line_is_well_under_the_entry_budget(tmp_project, tmp_control_home):
    # A representative fixture: some canon debt + a couple of offers + a roster.
    from tide import handoff_queue as hq, roster

    _closed_arc_with_unmerged_delta(tmp_project, 1, "alpha")
    hq.offer(tmp_control_home, "a", arc="-", project="-", seed="-")
    hq.offer(tmp_control_home, "b", arc="-", project="-", seed="-")
    roster.add(tmp_control_home, "p", str(tmp_project))

    start = time.perf_counter()
    for _ in range(5):
        health.render_line(health.compute_health(tmp_project, home=tmp_control_home))
    elapsed_ms = (time.perf_counter() - start) * 1000 / 5
    assert elapsed_ms < 50, "health line took {0:.1f}ms (budget 50ms)".format(elapsed_ms)

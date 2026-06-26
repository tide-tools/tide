"""Tests for tide.arc.land — atomic, strictness-gated land + reconcile + batch.

Covers the arc-land-strictness-dial acceptance criteria:
  (1) atomic land: merge worktree → seal/reconcile → re-stamp → gate
  (2) loose defers + writes the ledger / strict enforces full reconciliation
  (3) session-start (+ board + go) surface the deferred debt; reconcile pays it down
  (4) batch-land several arcs in one invocation
  (5) the dial is flag/config driven (not manual close -f)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import build_tide_skeleton, strip_placeholders
from tide import fields, ledger, paths, slug, strictness, sync
from tide.arc import board, land, stream, worktree
from tide.cannon import rev
from tide.contract import lifecycle, model
from tide.hooks import session_start
from tide.launcher import go


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    )


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    """A tmp git repo + tide skeleton with one initial commit."""
    build_tide_skeleton(tmp_path, name="land-proj")
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@tide.local")
    _git(tmp_path, "config", "user.name", "Tide Test")
    (tmp_path / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


def _contracted(root: Path, s: str = "fix-leak") -> Path:
    """An arc carrying a signed contract (state running), nothing reconciled yet."""
    arc = stream.new_arc(root, s)
    lifecycle.new(root, s)
    lifecycle.sign(root, s)
    return arc


def _make_reconcilable(root: Path, s: str = "fix-leak") -> None:
    """Fill report + proof + accept + a non-empty delta + clear placeholders."""
    lifecycle.report(root, s, body="did the thing")
    lifecycle.proof(root, s, body="criteria met")
    lifecycle.accept(root, s)
    model.delta_path(model.resolve_arc_dir(root, s)).write_text(
        "# delta — {0}\nmerged: no\n\n## What it is\n\nthe new truth\n".format(s),
        encoding="utf-8",
    )
    strip_placeholders(model.contract_path(model.resolve_arc_dir(root, s)))


def _no_gate(_root):
    """An injected gate that reports 'current' so tests never depend on lint state."""
    return 0, []


# ---------------------------------------------------------------------------
# (5) strictness resolution — flag wins, else config, else loose default
# ---------------------------------------------------------------------------

class TestStrictnessResolution:
    def test_unset_dial_defaults_to_loose(self, tmp_project):
        paths.strictness_file(tmp_project).unlink()  # never-decided project
        assert land.land_is_strict(tmp_project) is False

    def test_explicit_loose_dial_lands_loose(self, tmp_project):
        strictness.set_strictness(tmp_project, "loose")
        assert land.land_is_strict(tmp_project) is False

    def test_explicit_strict_dial_lands_strict(self, tmp_project):
        strictness.set_strictness(tmp_project, "strict")
        assert land.land_is_strict(tmp_project) is True

    def test_strict_flag_overrides_loose_dial(self, tmp_project):
        strictness.set_strictness(tmp_project, "loose")
        assert land.land_is_strict(tmp_project, strict_flag=True) is True

    def test_loose_flag_overrides_strict_dial(self, tmp_project):
        strictness.set_strictness(tmp_project, "strict")
        assert land.land_is_strict(tmp_project, loose_flag=True) is False

    def test_both_flags_is_an_error(self, tmp_project):
        with pytest.raises(land.LandError, match="not both"):
            land.land_is_strict(tmp_project, strict_flag=True, loose_flag=True)


# ---------------------------------------------------------------------------
# (2) loose defers + writes the ledger
# ---------------------------------------------------------------------------

class TestLooseDefers:
    def test_loose_seals_without_reconciling_and_logs_debt(self, tmp_project):
        arc = _contracted(tmp_project)
        before = rev.compute(tmp_project)

        outcome = land.land_one(tmp_project, "fix-leak", strict=False, gate_fn=_no_gate)

        # Arc sealed (dir renamed __…__), but CANON untouched (reconciliation deferred).
        assert slug.is_closed_entry(outcome.arc)
        assert rev.compute(tmp_project) == before
        assert outcome.reconciled is False
        # All three guards deferred (nothing was written/accepted).
        assert outcome.deferred == ["delta", "report", "proof"]

    def test_loose_writes_a_ledger_entry_with_guards_and_rev(self, tmp_project):
        _contracted(tmp_project)
        land.land_one(tmp_project, "fix-leak", strict=False, gate_fn=_no_gate)

        items = ledger.entries(tmp_project)
        assert len(items) == 1
        assert items[0].ref == "fix-leak"
        assert items[0].deferred == ["delta", "report", "proof"]
        assert items[0].cannon_rev == rev.compute(tmp_project)

    def test_loose_stamps_the_deferred_field_on_the_contract(self, tmp_project):
        arc = _contracted(tmp_project)
        land.land_one(tmp_project, "fix-leak", strict=False, gate_fn=_no_gate)
        sealed = model.resolve_arc_dir(tmp_project, "fix-leak")
        assert "report" in (model.read_field(sealed, "deferred") or "")

    def test_loose_only_defers_the_unsatisfied_guards(self, tmp_project):
        arc = _contracted(tmp_project)
        # Satisfy report + proof, leave the delta empty.
        lifecycle.report(tmp_project, "fix-leak", body="r")
        lifecycle.proof(tmp_project, "fix-leak", body="p")
        lifecycle.accept(tmp_project, "fix-leak")

        outcome = land.land_one(tmp_project, "fix-leak", strict=False, gate_fn=_no_gate)
        assert outcome.deferred == ["delta"]  # only the empty-delta guard


# ---------------------------------------------------------------------------
# (2) strict enforces full reconciliation
# ---------------------------------------------------------------------------

class TestStrictEnforces:
    def test_strict_blocks_with_self_documenting_next_step(self, tmp_project):
        _contracted(tmp_project)
        with pytest.raises(land.LandError) as exc:
            land.land_one(tmp_project, "fix-leak", strict=True, gate_fn=_no_gate)
        msg = str(exc.value)
        # Names the failing guards AND the exact catch-up commands.
        assert "tide contract report" in msg
        assert "tide arc land --strict fix-leak" in msg

    def test_strict_merges_delta_seals_and_bumps_rev(self, tmp_project):
        _contracted(tmp_project)
        _make_reconcilable(tmp_project)
        before = rev.compute(tmp_project)

        outcome = land.land_one(tmp_project, "fix-leak", strict=True, gate_fn=_no_gate)

        assert outcome.reconciled is True
        assert slug.is_closed_entry(outcome.arc)
        # Delta folded into CANON → rev bumped, and the new truth is present.
        assert outcome.new_rev != before
        assert outcome.new_rev == rev.compute(tmp_project)
        assert "the new truth" in paths.canon_file(tmp_project).read_text(encoding="utf-8")
        sealed = model.resolve_arc_dir(tmp_project, "fix-leak")
        assert model.read_state(sealed) == model.CLOSE

    def test_strict_does_not_write_the_ledger(self, tmp_project):
        _contracted(tmp_project)
        _make_reconcilable(tmp_project)
        land.land_one(tmp_project, "fix-leak", strict=True, gate_fn=_no_gate)
        assert ledger.count(tmp_project) == 0


# ---------------------------------------------------------------------------
# (1) atomic land — the worktree merge + gate are part of the one act
# ---------------------------------------------------------------------------

class TestAtomicLand:
    def test_merges_worktree_branch_to_base_then_seals(self, git_project):
        arc = stream.new_arc(git_project, "feat")
        wt = worktree.create(git_project, arc)
        (wt / "feat.txt").write_text("new feature\n", encoding="utf-8")
        _git(wt, "add", "-A")
        _git(wt, "commit", "-m", "add feat.txt")

        outcome = land.land_one(git_project, "feat", strict=False, gate_fn=_no_gate)

        assert outcome.merged is True
        assert (git_project / "feat.txt").is_file()  # landed on base
        assert slug.is_closed_entry(outcome.arc)  # sealed in the same act
        # Worktree cleaned up.
        assert not worktree.worktree_path(git_project, arc).exists()

    def test_worktree_conflict_is_self_documenting_and_seals_nothing(self, git_project):
        # Two arcs touch the SAME line → the second land conflicts.
        seed = git_project / "seed.txt"
        a = stream.new_arc(git_project, "a")
        wta = worktree.create(git_project, a)
        (wta / "seed.txt").write_text("A change\n", encoding="utf-8")
        _git(wta, "commit", "-am", "a")
        b = stream.new_arc(git_project, "b")
        wtb = worktree.create(git_project, b)
        (wtb / "seed.txt").write_text("B change\n", encoding="utf-8")
        _git(wtb, "commit", "-am", "b")

        land.land_one(git_project, "a", strict=False, gate_fn=_no_gate)  # clean
        with pytest.raises(land.LandError) as exc:
            land.land_one(git_project, "b", strict=False, gate_fn=_no_gate)
        assert "tide arc land b" in str(exc.value)
        # b was NOT sealed (the conflict aborted before the seal).
        assert worktree.has_worktree(git_project, b)

    def test_gate_runs_as_part_of_the_act(self, tmp_project):
        _contracted(tmp_project, "x")
        calls = []

        def spy_gate(root):
            calls.append(root)
            return 0, []

        outcome = land.land_one(tmp_project, "x", strict=False, gate_fn=spy_gate)
        assert calls, "the gate must run as part of land"
        assert outcome.gate_code == 0

    def test_gate_can_be_skipped(self, tmp_project):
        _contracted(tmp_project, "x")
        outcome = land.land_one(tmp_project, "x", strict=False, run_gate=False)
        assert outcome.gate_code is None


# ---------------------------------------------------------------------------
# (4) batch-land
# ---------------------------------------------------------------------------

class TestBatchLand:
    def test_lands_several_arcs_in_one_call(self, tmp_project):
        _contracted(tmp_project, "a")
        _contracted(tmp_project, "b")

        outcomes = land.batch_land(tmp_project, ["a", "b"], strict=False, gate_fn=_no_gate)
        assert len(outcomes) == 2
        assert all(slug.is_closed_entry(o.arc) for o in outcomes)
        assert {e.ref for e in ledger.entries(tmp_project)} == {"a", "b"}

    def test_gate_runs_once_for_the_whole_batch(self, tmp_project):
        _contracted(tmp_project, "a")
        _contracted(tmp_project, "b")
        calls = []

        land.batch_land(
            tmp_project, ["a", "b"], strict=False, gate_fn=lambda r: calls.append(r) or (0, [])
        )
        assert len(calls) == 1  # one project-wide gate, not one-per-arc


# ---------------------------------------------------------------------------
# (3) reconcile pays down the ledger
# ---------------------------------------------------------------------------

class TestReconcile:
    def test_reconcile_clears_debt_and_merges_delta(self, tmp_project):
        _contracted(tmp_project)
        land.land_one(tmp_project, "fix-leak", strict=False, gate_fn=_no_gate)
        assert ledger.count(tmp_project) == 1
        before = rev.compute(tmp_project)

        # Operator does the reconciliation paperwork on the sealed arc.
        _make_reconcilable(tmp_project)
        outcomes = land.reconcile(tmp_project, gate_fn=_no_gate)

        assert len(outcomes) == 1
        assert outcomes[0].reconciled is True
        assert ledger.count(tmp_project) == 0  # debt paid
        assert rev.compute(tmp_project) != before  # delta merged into CANON

    def test_reconcile_no_debt_is_a_clean_no_op(self, tmp_project):
        assert land.reconcile(tmp_project, gate_fn=_no_gate) == []

    def test_reconcile_specific_arc_only(self, tmp_project):
        _contracted(tmp_project, "a")
        _contracted(tmp_project, "b")
        land.batch_land(tmp_project, ["a", "b"], strict=False, gate_fn=_no_gate)
        _make_reconcilable(tmp_project, "a")

        land.reconcile(tmp_project, arcs=["a"], gate_fn=_no_gate)
        refs = {e.ref for e in ledger.entries(tmp_project)}
        assert refs == {"b"}  # only a was paid down


# ---------------------------------------------------------------------------
# (3) session-start / board / go surface the deferred debt
# ---------------------------------------------------------------------------

class TestSurfacing:
    def test_board_health_shows_deferred_debt(self, tmp_project):
        _contracted(tmp_project)
        land.land_one(tmp_project, "fix-leak", strict=False, gate_fn=_no_gate)
        out = board.render_board(tmp_project)
        assert "deferred: 1 arc(s)" in out
        assert "tide reconcile" in out

    def test_board_shows_deferred_none_when_clean(self, tmp_project):
        assert "deferred: none" in board.render_board(tmp_project)

    def test_session_start_surfaces_canon_lag(self, tmp_project):
        _contracted(tmp_project)
        land.land_one(tmp_project, "fix-leak", strict=False, gate_fn=_no_gate)
        text = session_start.render(tmp_project, "orchestrator")
        assert "канон отстал" in text
        assert "tide reconcile" in text

    def test_session_start_quiet_when_no_debt(self, tmp_project):
        assert "канон отстал" not in session_start.render(tmp_project, "orchestrator")

    def test_go_surfaces_deferred_debt(self, tmp_project):
        _contracted(tmp_project)
        land.land_one(tmp_project, "fix-leak", strict=False, gate_fn=_no_gate)
        line = go.render_deferred(tmp_project)
        assert "канон отстал" in line
        assert "tide reconcile" in line


# ---------------------------------------------------------------------------
# barrier exemption — a deferred arc does not block dispatching the next arc
# ---------------------------------------------------------------------------

class TestBarrierExemption:
    def test_ledgered_arc_does_not_block_a_new_arc(self, tmp_project):
        arc = _contracted(tmp_project, "deferred-one")
        # A real (non-empty) delta, but report/proof unaccepted → loose defers them.
        model.delta_path(arc).write_text(
            "# delta — deferred-one\nmerged: no\n\nreal body\n", encoding="utf-8"
        )
        land.land_one(tmp_project, "deferred-one", strict=False, gate_fn=_no_gate)

        # The arc carries an unmerged non-empty delta, but it is ledgered debt —
        # opening the next arc must NOT be blocked (discipline without slowness).
        sync.block_new_arc_if_unmerged_delta(tmp_project)  # no raise
        assert stream.new_arc(tmp_project, "next-one").is_dir()

    def test_non_ledgered_unmerged_delta_still_blocks(self, tmp_project):
        a = stream.new_arc(tmp_project, "leak")
        (a / "output" / "r.md").write_text("x", encoding="utf-8")
        (a / "delta.md").write_text(
            "# delta — leak\nmerged: no\n\npatched\n", encoding="utf-8"
        )
        strip_placeholders(a / "arc.md")
        stream.close(tmp_project, "leak")  # plain close — NOT ledgered
        with pytest.raises(sync.SyncError):
            sync.block_new_arc_if_unmerged_delta(tmp_project)


# ---------------------------------------------------------------------------
# no-contract arcs — strictness still gates the empty-output guard
# ---------------------------------------------------------------------------

class TestNoContractArc:
    def test_loose_seals_a_no_contract_arc_without_ledger(self, tmp_project):
        stream.new_arc(tmp_project, "bare")
        outcome = land.land_one(tmp_project, "bare", strict=False, gate_fn=_no_gate)
        assert slug.is_closed_entry(outcome.arc)
        assert ledger.count(tmp_project) == 0  # no canon debt without a contract

    def test_strict_enforces_empty_output_guard(self, tmp_project):
        stream.new_arc(tmp_project, "bare")  # empty output/
        with pytest.raises(land.LandError, match="--strict"):
            land.land_one(tmp_project, "bare", strict=True, gate_fn=_no_gate)

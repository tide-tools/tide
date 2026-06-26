"""U6 unit — contract.lifecycle: new/sign/report/proof/accept/close/reopen/state."""

from __future__ import annotations

import pytest

from tide import fields, strictness
from tide.arc import stream
from tide.cannon import rev, store
from tide.contract import lifecycle, model

from tests.conftest import strip_placeholders


def _arc(root, slug="fix-leak"):
    return stream.new_arc(root, slug)


def _write_delta(arc_dir, body="the new truth"):
    model.delta_path(arc_dir).write_text(
        "# delta — fix-leak\nmerged: no\n\n{0}\n".format(body), encoding="utf-8"
    )


# --- new -------------------------------------------------------------------

def test_new_creates_passport_delta_asks_state_draft(tmp_project):
    arc = _arc(tmp_project)
    cpath = lifecycle.new(tmp_project, "fix-leak", goal="stop leak", criteria="no drip")
    assert cpath.is_file()
    assert model.read_state(arc) == "draft"
    assert model.delta_path(arc).is_file()
    assert model.asks_dir(arc).is_dir()
    assert fields.read_field(cpath, "cannon-rev") == rev.compute(tmp_project)


def test_new_one_per_arc_guard(tmp_project):
    _arc(tmp_project)
    lifecycle.new(tmp_project, "fix-leak")
    with pytest.raises(model.ContractError):
        lifecycle.new(tmp_project, "fix-leak")


def test_new_stores_portable_project_name_not_abs_path(tmp_project):
    # tool ⊥ instance: the passport must carry the portable project NAME, never the
    # absolute path — a baked `/Users/<me>/…` leaks this instance into every contract.
    _arc(tmp_project)
    cpath = lifecycle.new(tmp_project, "fix-leak")
    project = fields.read_field(cpath, "project")
    assert project == tmp_project.resolve().name
    body = cpath.read_text(encoding="utf-8")
    assert "/Users/" not in body and "/home/" not in body
    assert str(tmp_project.resolve()) not in body


# --- sign ------------------------------------------------------------------

def test_sign_strict_defaults_to_human_and_runs(tmp_project):
    arc = _arc(tmp_project)
    lifecycle.new(tmp_project, "fix-leak")
    strictness.set_strictness(tmp_project, "strict")
    stamp = lifecycle.sign(tmp_project, "fix-leak", date="2026-06-25")
    assert stamp.startswith("human @ ")
    assert model.read_state(arc) == "running"
    assert model.read_field(arc, "sign") == "human @ 2026-06-25"


def test_sign_loose_defaults_to_orchestrator(tmp_project):
    arc = _arc(tmp_project)
    lifecycle.new(tmp_project, "fix-leak")
    strictness.set_strictness(tmp_project, "loose")
    stamp = lifecycle.sign(tmp_project, "fix-leak", date="2026-06-25")
    assert stamp.startswith("orchestrator @ ")
    assert model.read_state(arc) == "running"


def test_sign_explicit_signer_overrides(tmp_project):
    _arc(tmp_project)
    lifecycle.new(tmp_project, "fix-leak")
    stamp = lifecycle.sign(tmp_project, "fix-leak", signer="grisha", date="2026-06-25")
    assert stamp == "grisha @ 2026-06-25"


def test_sign_refuses_non_draft(tmp_project):
    _arc(tmp_project)
    lifecycle.new(tmp_project, "fix-leak")
    lifecycle.sign(tmp_project, "fix-leak")
    with pytest.raises(model.ContractError):
        lifecycle.sign(tmp_project, "fix-leak")


# --- report / proof / output advance ---------------------------------------

def test_report_and_proof_write_accepted_no_and_advance(tmp_project):
    arc = _arc(tmp_project)
    lifecycle.new(tmp_project, "fix-leak")
    lifecycle.sign(tmp_project, "fix-leak")
    rpath = lifecycle.report(tmp_project, "fix-leak", body="did the thing")
    assert fields.read_field(rpath, "accepted") == "no"
    # only report yet → still running
    assert model.read_state(arc) == "running"
    ppath = lifecycle.proof(tmp_project, "fix-leak", body="here is evidence")
    assert fields.read_field(ppath, "accepted") == "no"
    # both now exist → advanced to output
    assert model.read_state(arc) == "output"


# --- accept ----------------------------------------------------------------

def test_accept_flips_both_to_yes(tmp_project):
    arc = _arc(tmp_project)
    lifecycle.new(tmp_project, "fix-leak")
    lifecycle.sign(tmp_project, "fix-leak")
    lifecycle.report(tmp_project, "fix-leak", body="x")
    lifecycle.proof(tmp_project, "fix-leak", body="y")
    lifecycle.accept(tmp_project, "fix-leak")
    assert fields.read_field(arc / "report.md", "accepted") == "yes"
    assert fields.read_field(arc / "proof.md", "accepted") == "yes"


def test_accept_requires_both_deliverables(tmp_project):
    _arc(tmp_project)
    lifecycle.new(tmp_project, "fix-leak")
    lifecycle.sign(tmp_project, "fix-leak")
    lifecycle.report(tmp_project, "fix-leak", body="x")
    with pytest.raises(model.ContractError):
        lifecycle.accept(tmp_project, "fix-leak")


# --- close -----------------------------------------------------------------

def _ready_to_close(root):
    arc = _arc(root)
    lifecycle.new(root, "fix-leak")
    lifecycle.sign(root, "fix-leak")
    lifecycle.report(root, "fix-leak", body="x")
    lifecycle.proof(root, "fix-leak", body="y")
    lifecycle.accept(root, "fix-leak")
    _write_delta(arc)
    # F5: a worker fills the passport before close (arc.md too, so a manual
    # `arc close` in the e2e order also passes the placeholder guard).
    strip_placeholders(arc / "arc.md", model.contract_path(arc))
    return arc


def test_close_guard_blocks_when_not_accepted(tmp_project):
    arc = _arc(tmp_project)
    lifecycle.new(tmp_project, "fix-leak")
    lifecycle.sign(tmp_project, "fix-leak")
    lifecycle.report(tmp_project, "fix-leak", body="x")
    lifecycle.proof(tmp_project, "fix-leak", body="y")
    _write_delta(arc)
    strip_placeholders(model.contract_path(arc))  # isolate the not-accepted guard
    with pytest.raises(model.ContractError):
        lifecycle.close(tmp_project, "fix-leak")


def test_close_guard_blocks_on_empty_delta(tmp_project):
    arc = _arc(tmp_project)
    lifecycle.new(tmp_project, "fix-leak")
    lifecycle.sign(tmp_project, "fix-leak")
    lifecycle.report(tmp_project, "fix-leak", body="x")
    lifecycle.proof(tmp_project, "fix-leak", body="y")
    lifecycle.accept(tmp_project, "fix-leak")
    strip_placeholders(model.contract_path(arc))  # isolate the empty-delta guard
    # delta.md from `new` is frontmatter-only → empty body
    with pytest.raises(model.ContractError):
        lifecycle.close(tmp_project, "fix-leak")


def test_close_refuses_leftover_placeholders_in_contract(tmp_project):
    # F5: a fully-accepted contract with a real delta but a still-scaffolded
    # contract.md body (IS → TO-BE / where-we-are / the `# supersedes:` hint) is
    # refused — the merged passport must not read like a fill-in form.
    arc = _arc(tmp_project)
    lifecycle.new(tmp_project, "fix-leak")
    lifecycle.sign(tmp_project, "fix-leak")
    lifecycle.report(tmp_project, "fix-leak", body="x")
    lifecycle.proof(tmp_project, "fix-leak", body="y")
    lifecycle.accept(tmp_project, "fix-leak")
    _write_delta(arc)
    with pytest.raises(model.ContractError) as ei:
        lifecycle.close(tmp_project, "fix-leak")
    assert "placeholder" in str(ei.value)
    assert arc.is_dir()  # not sealed
    assert model.read_state(arc) == "output"  # state untouched


def test_close_force_overrides_placeholder_guard(tmp_project):
    # F5: -f seals even a scaffolded contract.md (escape hatch).
    arc = _arc(tmp_project)
    lifecycle.new(tmp_project, "fix-leak")
    lifecycle.sign(tmp_project, "fix-leak")
    _write_delta(arc)
    lifecycle.close(tmp_project, "fix-leak", force=True, date="2026-06-25")
    sealed = model.resolve_arc_dir(tmp_project, "fix-leak")
    assert sealed.name == "__01-fix-leak__"
    assert model.read_state(sealed) == "close"


def test_close_merges_seals_arc_and_restamps_no_self_drift(tmp_project):
    from tide import sync

    arc = _ready_to_close(tmp_project)
    before = rev.compute(tmp_project)
    new_rev = lifecycle.close(tmp_project, "fix-leak", date="2026-06-25")
    # merged into CANON.md journal
    canon = store.read(tmp_project)
    assert "the new truth" in canon
    assert "### 2026-06-25 · fix-leak" in canon
    # F3 — close now SEALS the arc: the open dir is gone, replaced by __…__.
    assert not arc.is_dir()
    sealed = model.resolve_arc_dir(tmp_project, "fix-leak")
    assert sealed.name == "__01-fix-leak__"
    # dual-marked done: folder + passport status agree
    assert fields.read_field(stream.passport_path(sealed), "status") == "done"
    # rev bumped, contract state close
    assert new_rev != before
    assert new_rev == rev.compute(tmp_project)
    assert model.read_field(sealed, "cannon-rev") == new_rev
    assert model.read_state(sealed) == "close"
    # F3 — the arc passport is re-stamped to the POST-merge rev → no self-drift
    # against the canon it just authored.
    assert fields.read_field(stream.passport_path(sealed), "cannon-rev") == new_rev
    assert sync.has_drifted(sealed, tmp_project) is False
    # delta marked merged
    assert fields.read_field(model.delta_path(sealed), "merged") == "yes"


def test_close_force_overrides_guard_and_seals(tmp_project):
    arc = _arc(tmp_project)
    lifecycle.new(tmp_project, "fix-leak")
    lifecycle.sign(tmp_project, "fix-leak")
    _write_delta(arc)  # no report/proof/accept, but delta present
    new_rev = lifecycle.close(tmp_project, "fix-leak", force=True, date="2026-06-25")
    assert not arc.is_dir()  # sealed
    sealed = model.resolve_arc_dir(tmp_project, "fix-leak")
    assert sealed.name == "__01-fix-leak__"
    assert model.read_state(sealed) == "close"
    assert new_rev == rev.compute(tmp_project)


def test_close_then_reopen_unseals_and_runs(tmp_project):
    arc = _ready_to_close(tmp_project)
    lifecycle.close(tmp_project, "fix-leak", date="2026-06-25")
    # sealed
    assert not arc.is_dir()
    lifecycle.reopen(tmp_project, "fix-leak")
    # un-sealed: open dir back, status active, contract running
    reopened = model.resolve_arc_dir(tmp_project, "fix-leak")
    assert reopened.name == "01-fix-leak"
    assert fields.read_field(stream.passport_path(reopened), "status") == "active"
    assert model.read_state(reopened) == "running"


def test_close_after_manual_arc_close_is_idempotent_seal(tmp_project):
    """`arc close` then `contract close` (the e2e order) seals once + restamps."""
    from tide import sync

    arc = _ready_to_close(tmp_project)
    (arc / "output" / "result.md").write_text("done\n", encoding="utf-8")
    # manual stream seal first (old two-phase order)
    stream.close(tmp_project, "fix-leak")
    new_rev = lifecycle.close(tmp_project, "fix-leak", date="2026-06-25")
    sealed = model.resolve_arc_dir(tmp_project, "fix-leak")
    assert sealed.name == "__01-fix-leak__"
    assert model.read_state(sealed) == "close"
    # re-stamped to post-merge rev → the authoring arc does not self-drift.
    assert fields.read_field(stream.passport_path(sealed), "cannon-rev") == new_rev
    assert sync.has_drifted(sealed, tmp_project) is False


# --- reopen / state --------------------------------------------------------

def test_reopen_reverses_close(tmp_project):
    _ready_to_close(tmp_project)
    lifecycle.close(tmp_project, "fix-leak", date="2026-06-25")
    lifecycle.reopen(tmp_project, "fix-leak")
    reopened = model.resolve_arc_dir(tmp_project, "fix-leak")
    assert model.read_state(reopened) == "running"


def test_reopen_refuses_non_closed(tmp_project):
    _arc(tmp_project)
    lifecycle.new(tmp_project, "fix-leak")
    with pytest.raises(model.ContractError):
        lifecycle.reopen(tmp_project, "fix-leak")


def test_transition_sets_state_by_key(tmp_project):
    arc = _arc(tmp_project)
    lifecycle.new(tmp_project, "fix-leak")
    lifecycle.transition(tmp_project, "fix-leak", "output")
    assert model.read_state(arc) == "output"


# --- list ------------------------------------------------------------------

def test_list_contracts_reports_state_and_arc(tmp_project):
    _arc(tmp_project, "fix-leak")
    lifecycle.new(tmp_project, "fix-leak")
    lifecycle.sign(tmp_project, "fix-leak")
    items = lifecycle.list_contracts(tmp_project)
    assert len(items) == 1
    assert items[0]["slug"] == "fix-leak"
    assert items[0]["state"] == "running"

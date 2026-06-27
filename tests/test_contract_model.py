"""U6 unit — contract.model: passport template, fields, state machine, resolve."""

from __future__ import annotations

import pytest

from tide.arc import stream
from tide.contract import model


def _arc(root, slug="fix-leak"):
    return stream.new_arc(root, slug)


def test_contract_md_template_carries_fields():
    text = model.contract_md("fix-leak", goal="stop the leak", criteria="no drip", project="/p", canon_rev="abc123")
    assert "slug: fix-leak" in text
    assert "goal: stop the leak" in text
    assert "criteria: no drip" in text
    assert "project: /p" in text
    assert "state: draft" in text
    assert "sign:" in text
    assert "canon-rev: abc123" in text
    assert "## IS → TO-BE" in text
    assert "## where we are" in text
    # the supersedes placeholder is a comment, not a real field
    assert "# supersedes:" in text


def test_has_contract_false_until_written(tmp_project):
    arc = _arc(tmp_project)
    assert model.has_contract(arc) is False
    model.contract_path(arc).write_text(model.contract_md("fix-leak"), encoding="utf-8")
    assert model.has_contract(arc) is True


def test_read_and_set_state_roundtrip(tmp_project):
    arc = _arc(tmp_project)
    model.contract_path(arc).write_text(model.contract_md("fix-leak"), encoding="utf-8")
    assert model.read_state(arc) == "draft"
    model.set_state(arc, "running")
    assert model.read_state(arc) == "running"


def test_set_state_rejects_unknown(tmp_project):
    arc = _arc(tmp_project)
    model.contract_path(arc).write_text(model.contract_md("fix-leak"), encoding="utf-8")
    with pytest.raises(model.ContractError):
        model.set_state(arc, "bogus")


def test_contract_slug_falls_back_to_arc_slug(tmp_project):
    arc = _arc(tmp_project, "fix-leak")
    # no slug field written → fall back to the arc's entry slug
    model.contract_path(arc).write_text("# contract\nstate: draft\n", encoding="utf-8")
    assert model.contract_slug(arc) == "fix-leak"


def test_resolve_arc_dir_by_slug_and_dirname(tmp_project):
    arc = _arc(tmp_project, "fix-leak")
    assert model.resolve_arc_dir(tmp_project, "fix-leak") == arc
    assert model.resolve_arc_dir(tmp_project, arc.name) == arc


def test_resolve_arc_dir_missing_raises(tmp_project):
    with pytest.raises(model.ContractError):
        model.resolve_arc_dir(tmp_project, "nope")

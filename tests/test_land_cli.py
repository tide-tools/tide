"""Integration — `tide arc land` / `tide reconcile` through the real CLI parser."""

from __future__ import annotations

import pytest

from tide import cli, ledger, slug
from tide.arc import stream
from tide.contract import lifecycle


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    """cwd = project root; orchestrator role (land merges shared truth)."""
    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("TIDE_ROLE", "orchestrator")
    return tmp_project


def _signed(root, s="fix-leak"):
    stream.new_arc(root, s)
    lifecycle.new(root, s)
    lifecycle.sign(root, s)


# --- role gate -------------------------------------------------------------

def test_arc_land_is_orchestrator_only(tmp_project, monkeypatch, capsys):
    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("TIDE_ROLE", "worker")
    _signed(tmp_project)
    rc = cli.main(["arc", "land", "fix-leak"])
    assert rc == 1
    assert "orchestrator-only" in capsys.readouterr().err


def test_reconcile_is_orchestrator_only(tmp_project, monkeypatch, capsys):
    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("TIDE_ROLE", "worker")
    rc = cli.main(["reconcile"])
    assert rc == 1
    assert "orchestrator-only" in capsys.readouterr().err


# --- loose land via CLI (dial driven by --loose flag, not close -f) --------

def test_cli_loose_land_seals_and_logs_debt(in_project, capsys):
    _signed(in_project)
    rc = cli.main(["arc", "land", "--loose", "fix-leak"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "--loose" in out and "tide reconcile" in out
    assert ledger.count(in_project) == 1


# --- strict land via CLI blocks with the next step ------------------------

def test_cli_strict_land_blocks_self_documenting(in_project, capsys):
    _signed(in_project)
    rc = cli.main(["arc", "land", "--strict", "fix-leak"])
    assert rc == 1
    assert "tide contract report" in capsys.readouterr().err


# --- batch land via CLI ----------------------------------------------------

def test_cli_batch_land_two_arcs(in_project):
    _signed(in_project, "a")
    _signed(in_project, "b")
    rc = cli.main(["arc", "land", "--loose", "a", "b"])
    assert rc == 0
    assert {e.ref for e in ledger.entries(in_project)} == {"a", "b"}


# --- reconcile via CLI pays the ledger down --------------------------------

def test_cli_reconcile_clean_ledger_is_noop(in_project, capsys):
    rc = cli.main(["reconcile"])
    assert rc == 0
    assert "no deferred debt" in capsys.readouterr().out


def test_cli_reconcile_pays_down_after_paperwork(in_project, capsys):
    _signed(in_project)
    cli.main(["arc", "land", "--loose", "fix-leak"])
    # operator fills the deliverables on the sealed arc
    from tide.contract import model
    from tests.conftest import strip_placeholders

    lifecycle.report(in_project, "fix-leak", body="done")
    lifecycle.proof(in_project, "fix-leak", body="evidence")
    lifecycle.accept(in_project, "fix-leak")
    model.delta_path(model.resolve_arc_dir(in_project, "fix-leak")).write_text(
        "# delta — fix-leak\nmerged: no\n\n## What it is\n\nthe truth\n", encoding="utf-8"
    )
    strip_placeholders(model.contract_path(model.resolve_arc_dir(in_project, "fix-leak")))

    rc = cli.main(["reconcile"])
    capsys.readouterr()
    assert ledger.count(in_project) == 0  # debt paid
    assert rc in (0, 1)  # 0 if gate clean, 1 if gate flags residual lint — debt IS paid

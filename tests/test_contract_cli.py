"""U6 integration — `tide contract …` wired through the real CLI parser.

Drives the full lifecycle (new → sign → report → proof → accept → close) plus
ask/answer, and verifies close is the orchestrator-only merge gate.
"""

from __future__ import annotations

import pytest

from tide import cli, paths, strictness
from tide.arc import stream
from tide.canon import rev, store
from tide.contract import model

from tests.conftest import strip_placeholders


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    """Run CLI commands as if cwd is the project root (paths resolves from cwd)."""
    monkeypatch.chdir(tmp_project)
    return tmp_project


def _arc(root, slug="fix-leak"):
    return stream.new_arc(root, slug)


def _write_delta(arc_dir, body="the new truth"):
    model.delta_path(arc_dir).write_text(
        "# delta — fix-leak\nmerged: no\n\n{0}\n".format(body), encoding="utf-8"
    )


def test_cli_full_lifecycle(in_project, orchestrator_role, capsys):
    arc = _arc(in_project)
    before = rev.compute(in_project)

    assert cli.main(["contract", "new", "fix-leak", "--goal", "stop leak"]) == 0
    assert model.read_state(arc) == "draft"

    assert cli.main(["contract", "sign", "fix-leak"]) == 0
    assert model.read_state(arc) == "running"

    assert cli.main(["contract", "report", "fix-leak", "did", "it"]) == 0
    assert cli.main(["contract", "proof", "fix-leak", "evidence"]) == 0
    assert model.read_state(arc) == "output"

    assert cli.main(["contract", "accept", "fix-leak"]) == 0
    _write_delta(arc)
    strip_placeholders(model.contract_path(arc))  # F5: fill the passport before close

    assert cli.main(["contract", "close", "fix-leak"]) == 0
    # F3 — close seals the arc: open dir gone, replaced by __…__.
    assert not arc.is_dir()
    sealed = model.resolve_arc_dir(in_project, "fix-leak")
    assert sealed.name == "__01-fix-leak__"
    assert model.read_state(sealed) == "close"
    assert rev.compute(in_project) != before
    assert "the new truth" in store.read(in_project)

    # reopen reverses: un-seals + back to running
    assert cli.main(["contract", "reopen", "fix-leak"]) == 0
    reopened = model.resolve_arc_dir(in_project, "fix-leak")
    assert reopened.name == "01-fix-leak"
    assert model.read_state(reopened) == "running"


def test_cli_sign_loose_stamps_orchestrator(in_project, capsys):
    _arc(in_project)
    strictness.set_strictness(in_project, "loose")
    cli.main(["contract", "new", "fix-leak"])
    cli.main(["contract", "sign", "fix-leak"])
    out = capsys.readouterr().out
    assert "orchestrator @" in out


def test_cli_close_refused_for_worker(in_project, worker_role, capsys):
    arc = _arc(in_project)
    cli.main(["contract", "new", "fix-leak"])
    cli.main(["contract", "sign", "fix-leak"])
    cli.main(["contract", "report", "fix-leak", "x"])
    cli.main(["contract", "proof", "fix-leak", "y"])
    cli.main(["contract", "accept", "fix-leak"])
    _write_delta(arc)
    rc = cli.main(["contract", "close", "fix-leak"])
    assert rc == 1
    assert "orchestrator-only" in capsys.readouterr().err
    # state untouched
    assert model.read_state(arc) == "output"


def test_cli_ask_answer_roundtrip(in_project, capsys):
    _arc(in_project)
    cli.main(["contract", "new", "fix-leak"])
    assert cli.main(["contract", "ask", "fix-leak", "valve", "which", "valve"]) == 0
    assert cli.main(["contract", "answer", "fix-leak", "valve", "the", "brass", "one"]) == 0
    asks = model.asks_dir(paths.arcs_dir(in_project) / "01-fix-leak")
    text = (asks / "01-valve.md").read_text(encoding="utf-8")
    assert "state: answered" in text
    assert "the brass one" in text


def test_cli_list_shows_contract(in_project, orchestrator_role, capsys):
    _arc(in_project)
    cli.main(["contract", "new", "fix-leak"])
    cli.main(["contract", "list"])
    out = capsys.readouterr().out
    assert "fix-leak" in out
    assert "draft" in out

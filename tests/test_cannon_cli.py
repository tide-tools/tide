"""U2 integration — `tide cannon …` wired through the real CLI parser."""

from __future__ import annotations

import pytest

from tide import cli, paths
from tide.cannon import rev, store


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    """Run CLI commands as if cwd is the project root (paths resolves from cwd)."""
    monkeypatch.chdir(tmp_project)
    return tmp_project


def test_cli_cannon_init_creates_files(in_project):
    # start from an empty cannon dir so init does real work
    paths.canon_file(in_project).unlink()
    paths.cannon_config(in_project).unlink()
    rc = cli.main(["cannon", "init", "--name", "demo"])
    assert rc == 0
    assert paths.canon_file(in_project).is_file()
    assert paths.cannon_config(in_project).read_text(encoding="utf-8") == "lang=en\n"


def test_cli_cannon_rev_prints_hash(in_project, capsys):
    rc = cli.main(["cannon", "rev"])
    assert rc == 0
    printed = capsys.readouterr().out.strip()
    assert printed == rev.compute(in_project)
    assert len(printed) == rev.REV_LEN


def test_cli_cannon_merge_refused_for_worker(in_project, worker_role, capsys):
    arc_dir = paths.arcs_dir(in_project) / "03-fix-leak"
    arc_dir.mkdir(parents=True)
    (arc_dir / "delta.md").write_text("body\n", encoding="utf-8")
    rc = cli.main(["cannon", "merge", "fix-leak"])
    assert rc == 1  # RoleError → nonzero
    assert "orchestrator-only" in capsys.readouterr().err


def test_cli_cannon_merge_runs_for_orchestrator(in_project, orchestrator_role, capsys):
    arc_dir = paths.arcs_dir(in_project) / "03-fix-leak"
    arc_dir.mkdir(parents=True)
    (arc_dir / "delta.md").write_text("# delta — fix-leak\n\npatched\n", encoding="utf-8")
    rc = cli.main(["cannon", "merge", "fix-leak"])
    assert rc == 0
    assert "patched" in store.read(in_project)
    assert "### " in store.read(in_project)


def test_cli_cannon_merge_preview_shows_diff_without_committing(in_project, capsys):
    arc_dir = paths.arcs_dir(in_project) / "03-fix-leak"
    arc_dir.mkdir(parents=True)
    (arc_dir / "delta.md").write_text(
        "# delta — fix-leak\nmerged: no\n\n## What it is\n\nfuture truth\n", encoding="utf-8"
    )
    before = store.read(in_project)
    rc = cli.main(["cannon", "merge", "--preview", "fix-leak"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "NOT committed" in out
    assert "future truth" in out  # the prospective diff
    assert store.read(in_project) == before  # nothing written


def test_cli_cannon_merge_preview_allowed_for_worker(in_project, worker_role, capsys):
    """--preview is read-only → not gated to the orchestrator (review-then-commit)."""
    arc_dir = paths.arcs_dir(in_project) / "03-fix-leak"
    arc_dir.mkdir(parents=True)
    (arc_dir / "delta.md").write_text(
        "# delta — fix-leak\nmerged: no\n\n## What it is\n\npeek\n", encoding="utf-8"
    )
    rc = cli.main(["cannon", "merge", "--preview", "fix-leak"])
    assert rc == 0
    assert "orchestrator-only" not in capsys.readouterr().err

"""Tests for tide.layer — the operator layer lives on the machine, not in the repo.

Работа 60 / decision 15. Real git throughout: the whole point of the unit is what
git actually does with the index and with ``.git/info/exclude``, and a stubbed
subprocess would prove nothing about either.

The load-bearing promises, each with a test:
  * ``.gitignore`` is NEVER written — the exclusion is local to this machine
  * ``tide adopt`` on a repo with history leaves no commit and no dirt
  * shared mode is an explicit opt-in that travels inside ``.tide/``
  * ``tide layer untrack`` unstages, keeps the files, and says what it cannot do
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import build_tide_skeleton
from tide import adopt, cli, layer, paths, roster


def _git(root: Path, *argv: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *argv],
                          check=check, capture_output=True, text=True)


def _repo(path: Path) -> Path:
    """A git repo with an identity and one commit of the person's own file."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@tide.local")
    _git(path, "config", "user.name", "Tide Test")
    (path / "code.py").write_text("print('mine')\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "their own first commit")
    return path


def _status(root: Path) -> str:
    return _git(root, "status", "--porcelain").stdout


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    ch = tmp_path / "home"
    ch.mkdir()
    build_tide_skeleton(ch, name="home", control_home=True)
    monkeypatch.setenv(paths.TIDE_HOME_ENV, str(ch))
    return ch


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Someone's working repository: real history, real files, not ours."""
    return _repo(tmp_path / "theirs")


@pytest.fixture
def no_orca(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)


# --- the mode ---------------------------------------------------------------

def test_a_project_keeps_its_layer_local_by_default(tmp_project: Path):
    assert layer.mode(tmp_project) == layer.LOCAL


def test_a_control_home_shares_its_layer(tmp_control_home: Path):
    """In the control-home .tide/ IS the content — arcs, candidates, decisions."""
    assert layer.mode(tmp_control_home) == layer.SHARED


def test_mode_is_written_inside_the_layer_so_it_travels(tmp_project: Path):
    layer.set_mode(tmp_project, layer.SHARED)
    assert layer.mode_file(tmp_project) == paths.tide_dir(tmp_project) / "layer"
    assert layer.mode(tmp_project) == layer.SHARED
    layer.set_mode(tmp_project, layer.LOCAL)
    assert layer.mode(tmp_project) == layer.LOCAL


def test_a_garbled_mode_file_reads_as_the_default(tmp_project: Path):
    layer.mode_file(tmp_project).write_text("¯\\_(ツ)_/¯\n", encoding="utf-8")
    assert layer.mode(tmp_project) == layer.LOCAL


def test_an_unknown_mode_is_refused(tmp_project: Path):
    with pytest.raises(ValueError):
        layer.set_mode(tmp_project, "whatever")


# --- the exclusion goes in the local file, never .gitignore -----------------

def test_exclude_writes_the_repo_local_file(project: Path):
    assert layer.exclude(project) == "added"
    text = (project / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert "/.tide/" in text
    assert layer.is_excluded(project)


def test_exclude_never_touches_gitignore(project: Path):
    (project / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    layer.exclude(project)
    assert (project / ".gitignore").read_text(encoding="utf-8") == "node_modules/\n"


def test_exclude_keeps_the_persons_own_patterns(project: Path):
    excl = project / ".git" / "info" / "exclude"
    excl.write_text("# mine\nscratch/\n", encoding="utf-8")
    layer.exclude(project)
    text = excl.read_text(encoding="utf-8")
    assert "scratch/" in text and "/.tide/" in text


def test_exclude_is_idempotent(project: Path):
    assert layer.exclude(project) == "added"
    assert layer.exclude(project) == "already"
    text = (project / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert text.count("/.tide/") == 1


def test_unexclude_takes_the_pattern_back_out(project: Path):
    layer.exclude(project)
    assert layer.unexclude(project) == "removed"
    assert not layer.is_excluded(project)
    assert layer.unexclude(project) == "absent"


def test_no_repo_means_nothing_to_exclude_from(tmp_project: Path):
    assert layer.exclude_file(tmp_project) is None
    assert layer.exclude(tmp_project) == "no-git"
    assert layer.ensure_local(tmp_project) == "no-git"


def test_the_exclude_lands_in_the_common_git_dir_from_a_worktree(project: Path,
                                                                tmp_path: Path):
    """Inside a linked worktree ``.git`` is a file — the pattern must still apply."""
    wt = tmp_path / "wt"
    _git(project, "worktree", "add", "-q", "-b", "side", str(wt))
    assert (wt / ".git").is_file()

    assert layer.exclude(wt) == "added"
    assert "/.tide/" in (project / ".git" / "info" / "exclude").read_text(encoding="utf-8")


# --- adopt: the default is a layer that stays home --------------------------

def test_adopt_keeps_the_layer_out_of_a_repo_with_history(home, project, no_orca):
    before = _git(project, "rev-list", "--count", "HEAD").stdout.strip()

    report = adopt.adopt(project, do_orca=False)

    assert report.step("layer").status == adopt.DONE
    # not one commit, and the working tree is exactly as it was
    assert _git(project, "rev-list", "--count", "HEAD").stdout.strip() == before
    assert _status(project).strip() == ""
    assert not (project / ".gitignore").exists()
    assert paths.tide_dir(project).is_dir()          # the layer is there, just invisible to git
    assert roster.read_roster(home)                  # and the board can see the project


def test_adopt_leaves_an_existing_gitignore_byte_for_byte(home, project, no_orca):
    ignore = project / ".gitignore"
    ignore.write_text("dist/\n*.log\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-qm", "add gitignore")

    adopt.adopt(project, do_orca=False)

    assert ignore.read_text(encoding="utf-8") == "dist/\n*.log\n"
    assert _status(project).strip() == ""


def test_adopt_on_an_empty_dir_makes_a_repo_but_commits_no_files(home, tmp_path, no_orca):
    fresh = tmp_path / "fresh"
    fresh.mkdir()

    report = adopt.adopt(fresh, do_orca=False)

    assert report.step("git").status == adopt.DONE
    assert report.step("commit").status == adopt.DONE
    assert _git(fresh, "rev-parse", "--verify", "HEAD").returncode == 0   # threads can spawn
    assert _git(fresh, "ls-files").stdout.strip() == ""                   # carrying nothing
    assert _status(fresh).strip() == ""                                   # .tide/ excluded


def test_adopt_shared_commits_the_layer_with_the_project(home, project, no_orca):
    report = adopt.adopt(project, do_orca=False, shared=True)

    assert report.step("layer").status == adopt.SKIPPED
    assert layer.mode(project) == layer.SHARED
    assert not layer.is_excluded(project)
    # now .tide/ is the team's to commit — git sees it
    assert ".tide/" in _status(project)


def test_adopt_respects_a_layer_someone_else_shared(home, project, no_orca):
    """A clone whose .tide/ came with the repo must not be excluded behind their back."""
    paths.tide_dir(project).mkdir(parents=True, exist_ok=True)
    layer.set_mode(project, layer.SHARED)

    report = adopt.adopt(project, do_orca=False)

    assert report.step("layer").status == adopt.SKIPPED
    assert not layer.is_excluded(project)


def test_adopt_says_so_when_the_layer_is_already_committed(home, project, no_orca):
    build_tide_skeleton(project, name="theirs")
    _git(project, "add", "-A")
    _git(project, "commit", "-qm", "oops, the layer went in")

    report = adopt.adopt(project, do_orca=False)

    step = report.step("layer")
    assert step.status == adopt.WARN
    assert "tide layer untrack" in step.detail
    assert not layer.is_excluded(project)   # an exclude over tracked files would be a lie


# --- untrack: the way out for whoever already committed it ------------------

@pytest.fixture
def committed_layer(project: Path) -> Path:
    build_tide_skeleton(project, name="theirs")
    _git(project, "add", "-A")
    _git(project, "commit", "-qm", "the layer went into history")
    return project


def test_untrack_drops_the_layer_from_the_index_and_keeps_the_files(committed_layer):
    canon = paths.canon_file(committed_layer)
    assert canon.is_file()

    report = layer.untrack(committed_layer)

    assert report.tracked_before > 0
    assert report.staged_removal
    assert layer.tracked(committed_layer) == []
    assert canon.is_file()                       # still on disk, every one of them
    assert layer.is_excluded(committed_layer)


def test_untrack_does_not_commit_and_does_not_rewrite_history(committed_layer):
    head_before = _git(committed_layer, "rev-parse", "HEAD").stdout.strip()

    report = layer.untrack(committed_layer)

    assert _git(committed_layer, "rev-parse", "HEAD").stdout.strip() == head_before
    # the old commit still carries the layer — and the report says exactly that
    in_history = _git(committed_layer, "ls-tree", "-r", "--name-only", "HEAD").stdout
    assert ".tide/canon/CANON.md" in in_history
    assert any("history is untouched" in line for line in report.lines)
    assert any("STAGED, not committed" in line for line in report.lines)
    # the removal is waiting in the index for the person to commit
    assert "D  .tide/canon/CANON.md" in _status(committed_layer)


def test_untrack_leaves_gitignore_alone(committed_layer):
    (committed_layer / ".gitignore").write_text("dist/\n", encoding="utf-8")
    report = layer.untrack(committed_layer)
    assert (committed_layer / ".gitignore").read_text(encoding="utf-8") == "dist/\n"
    assert any("gitignore was not touched" in line for line in report.lines)


def test_untrack_flips_a_shared_project_back_to_local(committed_layer):
    layer.set_mode(committed_layer, layer.SHARED)
    report = layer.untrack(committed_layer)
    assert layer.mode(committed_layer) == layer.LOCAL
    assert any("shared → local" in line for line in report.lines)


def test_untrack_on_a_clean_project_only_writes_the_exclude(project):
    report = layer.untrack(project)
    assert report.tracked_before == 0
    assert not report.staged_removal
    assert layer.is_excluded(project)
    assert any("was not tracked" in line for line in report.lines)


def test_untrack_without_git_changes_nothing(tmp_project):
    report = layer.untrack(tmp_project)
    assert report.tracked_before == 0
    assert any("not a git repo" in line for line in report.lines)


# --- CLI --------------------------------------------------------------------

def test_cli_untrack_says_what_it_will_do_before_doing_it(committed_layer, capsys):
    rc = cli.main(["layer", "untrack", "--path", str(committed_layer)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "git rm -r --cached .tide/" in out
    assert "files stay on disk" in out
    assert "does NOT rewrite history" in out
    assert layer.tracked(committed_layer) == []


def test_cli_untrack_dry_run_changes_nothing(committed_layer, capsys):
    rc = cli.main(["layer", "untrack", "--path", str(committed_layer), "--dry-run"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "nothing was changed" in out
    assert layer.tracked(committed_layer)          # still tracked
    assert not layer.is_excluded(committed_layer)


def test_cli_status_reports_where_the_layer_lives(committed_layer, capsys):
    rc = cli.main(["layer", "--path", str(committed_layer)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "layer: local" in out
    assert "are tracked in this repo" in out
    assert "tide layer untrack" in out


def test_cli_shared_then_local_round_trips(project, capsys):
    assert cli.main(["layer", "shared", "--path", str(project)]) == 0
    assert layer.mode(project) == layer.SHARED

    assert cli.main(["layer", "local", "--path", str(project)]) == 0
    out = capsys.readouterr().out
    assert layer.mode(project) == layer.LOCAL
    assert layer.is_excluded(project)
    assert "your .gitignore was not touched" in out

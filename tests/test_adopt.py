"""Tests for tide.adopt — one-command project onboarding.

git + orca steps are stubbed (monkeypatch subprocess.run / shutil.which); the
control-home is a tmp dir pointed at via $TIDE_HOME so the roster step lands
without touching the real machine.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import build_tide_skeleton
from tide import adopt, cli, paths, readme, roster
from tide.canon import store
from tide.hooks import session_start


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """A tmp control-home, exported via $TIDE_HOME so roster.add resolves it."""
    ch = tmp_path / "home"
    ch.mkdir()
    build_tide_skeleton(ch, name="home", control_home=True)
    monkeypatch.setenv(paths.TIDE_HOME_ENV, str(ch))
    return ch


@pytest.fixture
def target(tmp_path: Path) -> Path:
    d = tmp_path / "myproj"
    d.mkdir()
    return d


# --- core happy path -------------------------------------------------------

def test_adopt_scaffolds_tide_and_rosters(home, target, monkeypatch):
    git_calls = []
    orca_calls = []

    def fake_run(argv, **kwargs):
        if argv[:1] == ["git"]:
            git_calls.append(argv)
        elif argv[:1] == ["orca"]:
            orca_calls.append(argv)
        return _cp()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/orca")
    monkeypatch.setattr(sys, "platform", "darwin")

    report = adopt.adopt(target, name="demo")

    # .tide/ scaffolded
    assert paths.tide_dir(target).is_dir()
    assert report.step("tide").status == adopt.DONE

    # git init invoked with the resolved abs path
    assert report.step("git").status == adopt.DONE
    assert git_calls and git_calls[0][:2] == ["git", "init"]
    assert str(target.resolve()) in git_calls[0]

    # orca repo add --path <abs> --json invoked
    assert report.step("orca").status == adopt.DONE
    assert orca_calls == [["orca", "repo", "add", "--path", str(target.resolve()), "--json"]]

    # rostered into the control-home
    assert report.step("roster").status == adopt.DONE
    entries = roster.read_roster(home)
    assert {"name": "demo", "path": str(target.resolve())} in entries


def test_name_defaults_to_basename(home, target, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _cp())
    monkeypatch.setattr(shutil, "which", lambda name: None)
    report = adopt.adopt(target)
    assert report.name == "myproj"


# --- opt-outs --------------------------------------------------------------

def test_no_git_skips_git(home, target, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: calls.append(argv) or _cp())
    monkeypatch.setattr(shutil, "which", lambda name: None)

    report = adopt.adopt(target, do_git=False)
    assert report.step("git").status == adopt.SKIPPED
    assert not any(c[:1] == ["git"] for c in calls)


def test_no_orca_skips_orca(home, target, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: calls.append(argv) or _cp())
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/orca")

    report = adopt.adopt(target, do_orca=False)
    assert report.step("orca").status == adopt.SKIPPED
    assert not any(c[:1] == ["orca"] for c in calls)


def test_orca_absent_skips_with_note(home, target, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _cp())
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(sys, "platform", "darwin")
    report = adopt.adopt(target)
    step = report.step("orca")
    assert step.status == adopt.SKIPPED
    assert "PATH" in step.detail
    # the skip explains itself: Orca is optional, nothing is broken without it
    assert "optional" in step.detail


def test_orca_not_called_off_darwin_even_when_on_path(home, target, monkeypatch):
    """Finding 5 (release panel): on Linux `orca` is the GNOME screen-reader.

    An orca binary on PATH off-Darwin must NOT be invoked — `orca repo add`
    would launch a screen-reader with junk arguments. The step skips with a
    note instead, exactly like the adapters registry gate.
    """
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: calls.append(argv) or _cp())
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/orca")
    monkeypatch.setattr(sys, "platform", "linux")

    report = adopt.adopt(target)
    step = report.step("orca")
    assert step.status == adopt.SKIPPED
    assert "macOS" in step.detail
    assert not any(c[:1] == ["orca"] for c in calls)


def test_git_missing_warns_and_continues(home, target, monkeypatch):
    def fake_run(argv, **kwargs):
        if argv[:1] == ["git"]:
            raise FileNotFoundError("git")
        return _cp()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(shutil, "which", lambda name: None)

    report = adopt.adopt(target)
    assert report.step("git").status == adopt.WARN
    # the rest still ran
    assert paths.tide_dir(target).is_dir()
    assert report.step("roster").status == adopt.DONE


def test_orca_already_registered_is_success(home, target, monkeypatch):
    def fake_run(argv, **kwargs):
        if argv[:1] == ["orca"]:
            raise subprocess.CalledProcessError(returncode=1, cmd=argv, stderr="already added")
        return _cp()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/orca")
    monkeypatch.setattr(sys, "platform", "darwin")

    report = adopt.adopt(target)
    assert report.step("orca").status == adopt.DONE


# --- no control-home -------------------------------------------------------

def test_no_control_home_skips_roster_gracefully(tmp_path, monkeypatch):
    # No $TIDE_HOME, and cwd has no .tide ancestor → control_home() raises, so the
    # roster step is skipped (not a hard failure). chdir to the clean tmp parent so
    # the cwd-climb fallback finds nothing (the adopted child's .tide is below cwd).
    target = tmp_path / "myproj"
    target.mkdir()
    monkeypatch.delenv(paths.TIDE_HOME_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _cp())
    monkeypatch.setattr(shutil, "which", lambda name: None)

    report = adopt.adopt(target)
    step = report.step("roster")
    assert step.status == adopt.SKIPPED
    assert "TIDE_HOME" in step.detail
    # scaffolding still happened
    assert paths.tide_dir(target).is_dir()


# --- idempotency -----------------------------------------------------------

def test_rerun_is_noop_ish_success(home, target, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _cp())
    monkeypatch.setattr(shutil, "which", lambda name: None)

    adopt.adopt(target, name="demo")
    # second run: .tide/ already there, roster replaces in place (no dup)
    report = adopt.adopt(target, name="demo")
    assert report.step("tide").status == adopt.SKIPPED
    entries = [e for e in roster.read_roster(home) if e["name"] == "demo"]
    assert len(entries) == 1


# --- rendering -------------------------------------------------------------

def test_render_report_has_ready_line(home, target, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _cp())
    monkeypatch.setattr(shutil, "which", lambda name: None)
    report = adopt.adopt(target, name="demo")
    out = adopt.render_report(report)
    assert "ready: tide menu → demo" in out


# --- first commit: adoption makes the repo worktree-ready (cand 32) ---------

def _real_git(root, *argv):
    subprocess.run(["git", "-C", str(root), *argv], check=True, capture_output=True)


def test_adopt_makes_first_commit_worktree_ready(home, target, monkeypatch):
    # Real git: init happens, scaffold lands, and an EMPTY first commit gives the
    # repo a HEAD — `git worktree add` (the tide menu spawn path) needs one.
    monkeypatch.setattr(shutil, "which", lambda name: None)  # skip orca only

    report = adopt.adopt(target, do_orca=False)

    assert report.step("git").status == adopt.DONE
    assert report.step("commit").status == adopt.DONE
    head = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--verify", "HEAD"],
        capture_output=True, text=True,
    )
    assert head.returncode == 0  # worktree-ready


def test_birth_commit_carries_no_files_at_all(home, target, monkeypatch):
    """Работа 60: HEAD exists so threads spawn, and not one file rode into it.

    The layer is excluded and the person's own files (README included) are left
    untracked — theirs to commit, or not.
    """
    monkeypatch.setattr(shutil, "which", lambda name: None)
    (target / "mine.txt").write_text("mine\n", encoding="utf-8")

    adopt.adopt(target, do_orca=False, intent=GOAL)

    tracked = subprocess.run(
        ["git", "-C", str(target), "ls-files"], capture_output=True, text=True
    ).stdout
    assert tracked.strip() == ""
    assert (target / "README.md").is_file()      # written, just not committed


def test_adopt_makes_no_commit_in_a_repo_with_history(home, target, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    _real_git(target, "init", "-q")
    _real_git(target, "config", "user.email", "t@example.com")
    _real_git(target, "config", "user.name", "t")
    (target / "a.txt").write_text("x\n", encoding="utf-8")
    _real_git(target, "add", ".")
    _real_git(target, "commit", "-qm", "existing")
    before = subprocess.run(
        ["git", "-C", str(target), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True).stdout.strip()

    report = adopt.adopt(target, do_orca=False)

    assert report.step("commit").status == adopt.SKIPPED
    assert "commits nothing" in report.step("commit").detail
    after = subprocess.run(
        ["git", "-C", str(target), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    assert after == before
    # and the working tree is as it was: nothing staged, nothing untracked
    assert subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain"],
        capture_output=True, text=True).stdout.strip() == ""


def test_adopt_leaves_a_headless_repo_of_theirs_alone(home, target, monkeypatch):
    """A repo the person made themselves and has not committed in yet is not ours."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    _real_git(target, "init", "-q")

    report = adopt.adopt(target, do_orca=False)

    assert report.step("commit").status == adopt.SKIPPED
    assert "--allow-empty" in report.step("commit").detail
    head = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--verify", "--quiet", "HEAD"],
        capture_output=True, text=True)
    assert head.returncode != 0  # still theirs to open


def test_adopt_no_git_skips_commit_step(home, target, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _cp())
    monkeypatch.setattr(shutil, "which", lambda name: None)
    report = adopt.adopt(target, do_git=False)
    assert report.step("commit").status == adopt.SKIPPED


# --- birth with intent: --goal seeds the canon and the user door ------------

def _stub_env(monkeypatch):
    """Neutralise git + orca so a test exercises only the file-writing steps."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _cp())
    monkeypatch.setattr(shutil, "which", lambda name: None)


GOAL = "A board that shows the factory its own state."


def test_adopt_with_goal_seeds_the_canon(home, target, monkeypatch):
    _stub_env(monkeypatch)

    report = adopt.adopt(target, name="demo", intent=GOAL)

    sections = store.scan(target)
    assert sections[store.INTENT_SECTION] == GOAL
    assert not store.is_empty_skeleton(store.read(target))
    assert "canon seeded" in report.step("tide").detail


def test_adopt_with_goal_generates_a_readme_that_says_something(home, target, monkeypatch):
    _stub_env(monkeypatch)

    report = adopt.adopt(target, name="demo", intent=GOAL)

    assert report.step("readme").status == adopt.DONE
    text = readme.readme_file(target).read_text(encoding="utf-8")
    assert text.startswith("# demo")
    assert GOAL in text                      # not a stub — the intent is on the page
    assert readme.STAMP_PREFIX in text       # derived + stamped, not hand-written


def test_adopt_with_goal_leaves_no_drift_behind(home, target, monkeypatch):
    """The pair born together must already pass the gate the hook reproaches on."""
    _stub_env(monkeypatch)
    adopt.adopt(target, name="demo", intent=GOAL)

    code, reasons = readme.check(target)
    assert (code, reasons) == (0, [])
    assert session_start._readme_drift_warnings(target) == []


def test_adopt_goal_normalises_a_multiline_goal(home, target, monkeypatch):
    _stub_env(monkeypatch)
    adopt.adopt(target, name="demo", intent="  a goal\n  spread over lines  ")
    assert store.scan(target)[store.INTENT_SECTION] == "a goal spread over lines"


# --- birth without intent: everything exactly as before --------------------

def test_adopt_without_goal_writes_no_readme(home, target, monkeypatch):
    _stub_env(monkeypatch)

    report = adopt.adopt(target, name="demo")

    assert report.step("readme").status == adopt.SKIPPED
    assert "no --goal" in report.step("readme").detail
    assert not readme.readme_file(target).exists()


def test_adopt_without_goal_stays_a_silent_newborn(home, target, monkeypatch):
    """The whole point of the old behaviour: nothing is demanded of the human."""
    _stub_env(monkeypatch)
    adopt.adopt(target, name="demo")

    assert store.is_empty_skeleton(store.read(target))
    assert session_start._is_newborn(target)
    assert session_start._readme_drift_warnings(target) == []


def test_adopt_goal_does_not_touch_an_already_adopted_project(home, target, monkeypatch):
    """Re-running with a goal must not rewrite someone else's canon or README."""
    _stub_env(monkeypatch)
    adopt.adopt(target, name="demo")                       # born blank
    paths.canon_file(target).write_text("# CANON.md — demo\n\n## What it is\n\nmine\n",
                                        encoding="utf-8")

    report = adopt.adopt(target, name="demo", intent=GOAL)  # late goal

    assert report.step("readme").status == adopt.SKIPPED
    assert "canon already written" in report.step("readme").detail
    assert "mine" in paths.canon_file(target).read_text(encoding="utf-8")
    assert not readme.readme_file(target).exists()


# --- CLI + commit wiring ---------------------------------------------------

def test_cli_adopt_goal_reaches_the_canon(home, target, monkeypatch, capsys):
    _stub_env(monkeypatch)

    rc = cli.main(["adopt", str(target), "--name", "demo", "--goal", GOAL])

    assert rc == 0
    assert store.scan(target)[store.INTENT_SECTION] == GOAL
    assert "README.md generated from canon" in capsys.readouterr().out


def test_readme_is_left_for_the_human_to_commit(home, target, monkeypatch):
    """The README is written, and left untracked — committing it is their call."""
    monkeypatch.setattr(shutil, "which", lambda name: None)  # real git, no orca

    adopt.adopt(target, name="demo", do_orca=False, intent=GOAL)

    tracked = subprocess.run(
        ["git", "-C", str(target), "ls-files"], capture_output=True, text=True
    ).stdout
    assert "README.md" not in tracked
    status = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain"], capture_output=True, text=True
    ).stdout
    assert "README.md" in status          # visible to them, waiting
    assert ".tide/" not in status         # the layer is not, it is excluded

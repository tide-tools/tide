"""50-release — `tide release`: the path out of this checkout into someone's install.

The guarantees under test, in order of how much they would cost to get wrong:

* a DRY RUN publishes nothing — it is the same plan, printed;
* the sha256 in the formula is the digest of the artifact actually built, never
  a value carried over from a previous release;
* a red preflight or a red gate REFUSES — no tag, no release, no formula push;
* the artifact does not carry the releasing machine's own identity.

Every test drives a scripted runner, so no git command, no `gh` call and no
network ever happen here.
"""

from __future__ import annotations

import tarfile
from pathlib import Path
from typing import List, Tuple

import pytest

from tide.release import core


FORMULA = '''class Tide < Formula
  desc "orchestration machine"
  homepage "https://github.com/tide-tools/tide"

  url "https://github.com/tide-tools/tide/releases/download/v0.9.0/tide-0.9.0.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"  # old
  license "MIT"

  test do
    assert_match "tide 0.9.0", shell_output("#{bin}/tide version")
  end
end
'''


class FakeRunner:
    """Answers git/gh calls from a script; records everything it was asked to run."""

    def __init__(self, **answers):
        self.answers = {
            "rev-parse --git-dir": (0, ".git"),
            "rev-parse --abbrev-ref": (0, "main"),
            "status --porcelain": (0, ""),
            "tag --list": (0, ""),
            "ls-remote": (0, ""),
            "gh auth": (0, "logged in"),
        }
        self.answers.update(answers)
        self.calls: List[List[str]] = []

    def __call__(self, cmd, cwd=None, env=None) -> Tuple[int, str]:
        self.calls.append(list(cmd))
        joined = " ".join(cmd)
        for key, answer in self.answers.items():
            if all(part in joined for part in key.split()):
                return answer
        return (0, "")

    def ran(self, needle: str) -> bool:
        return any(needle in " ".join(c) for c in self.calls)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools"]\n\n[project]\nname = "tide"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (tmp_path / "packaging").mkdir()
    (tmp_path / "packaging" / "tide.rb").write_text(FORMULA, encoding="utf-8")
    return tmp_path


# --- version ------------------------------------------------------------------


def test_current_version_reads_pyproject(repo: Path):
    assert core.current_version(repo) == "1.2.3"


def test_set_version_rewrites_project_not_build_system(repo: Path):
    previous = core.set_pyproject_version(repo, "1.3.0")
    assert previous == "1.2.3"
    text = (repo / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "1.3.0"' in text
    assert 'requires = ["setuptools"]' in text  # the build-system block is untouched


# --- preflight ----------------------------------------------------------------


def test_preflight_all_green(repo: Path):
    checks = core.preflight(repo, "1.2.3", runner=FakeRunner())
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def test_preflight_refuses_a_dirty_tree(repo: Path):
    runner = FakeRunner(**{"status --porcelain": (0, " M src/tide/cli.py\n")})
    checks = {c.name: c for c in core.preflight(repo, "1.2.3", runner=runner)}
    assert checks["clean-tree"].ok is False
    # The reason matters: uncommitted work would silently NOT ship, because the
    # artifact is cut from the commit.
    assert "would silently NOT ship" in checks["clean-tree"].detail


def test_preflight_allows_a_dirty_tree_when_asked(repo: Path):
    runner = FakeRunner(**{"status --porcelain": (0, " M x\n")})
    checks = {c.name: c for c in core.preflight(repo, "1.2.3", allow_dirty=True, runner=runner)}
    assert checks["clean-tree"].ok is True


def test_preflight_refuses_the_wrong_branch(repo: Path):
    runner = FakeRunner(**{"rev-parse --abbrev-ref": (0, "wip/experiment")})
    checks = {c.name: c for c in core.preflight(repo, "1.2.3", runner=runner)}
    assert checks["branch"].ok is False


def test_preflight_refuses_an_existing_tag(repo: Path):
    # Re-cutting a tag that already exists is how two different builds end up
    # answering to one version.
    runner = FakeRunner(**{"tag --list": (0, "v1.2.3\n")})
    checks = {c.name: c for c in core.preflight(repo, "1.2.3", runner=runner)}
    assert checks["tag-free"].ok is False
    assert "already exists" in checks["tag-free"].detail


def test_preflight_reports_every_blocker_at_once(repo: Path):
    runner = FakeRunner(**{
        "rev-parse --abbrev-ref": (0, "side"),
        "status --porcelain": (0, " M x\n"),
        "tag --list": (0, "v1.2.3\n"),
    })
    bad = [c.name for c in core.preflight(repo, "1.2.3", runner=runner) if not c.ok]
    assert set(bad) == {"branch", "clean-tree", "tag-free"}


# --- the artifact --------------------------------------------------------------


def _fake_tarball(path: Path, members: dict) -> Path:
    import io

    with tarfile.open(path, "w:gz") as tf:
        for name, body in members.items():
            data = body.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def test_sha256_is_the_digest_of_the_built_file(tmp_path: Path):
    p = tmp_path / "a.tar.gz"
    p.write_bytes(b"hello")
    import hashlib

    assert core.sha256_of(p) == hashlib.sha256(b"hello").hexdigest()


def test_artifact_scan_flags_the_releasing_machines_identity(tmp_path: Path):
    tb = _fake_tarball(tmp_path / "t.tar.gz", {"tide/x.py": 'HOME = "/home/zaphod/tide"\n'})
    leaks = core.scan_artifact_for_instance_tokens(tb, tokens=["zaphod"])
    assert len(leaks) == 1
    assert leaks[0].detail == "zaphod"


def test_artifact_scan_ignores_invented_home_paths(tmp_path: Path):
    # A test fixture's made-up home path is not a leak of THIS machine. Failing on
    # those would train people to wave the check through.
    tb = _fake_tarball(tmp_path / "t.tar.gz", {"tests/t.py": 'p = "/Users/alice/secret"\n'})
    assert core.scan_artifact_for_instance_tokens(tb, tokens=["zaphod"]) == []


def test_artifact_scan_survives_a_binary_member(tmp_path: Path):
    import io

    path = tmp_path / "t.tar.gz"
    with tarfile.open(path, "w:gz") as tf:
        data = b"\x00\x01\xff\xfe binary"
        info = tarfile.TarInfo("tide/blob.bin")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    assert core.scan_artifact_for_instance_tokens(path, tokens=["zaphod"]) == []


# --- the formula ---------------------------------------------------------------


def test_render_formula_rewrites_url_sha_and_smoke():
    out = core.render_formula(
        FORMULA, github_repo="tide-tools/tide", version="1.2.3", sha256="abc123"
    )
    assert 'url "https://github.com/tide-tools/tide/releases/download/v1.2.3/tide-1.2.3.tar.gz"' in out
    assert 'sha256 "abc123"' in out
    assert 'assert_match "tide 1.2.3"' in out
    # the old values are gone — a half-rewrite is the failure mode that breaks
    # every user's install
    assert "0.9.0" not in out
    assert "0000000000" not in out


def test_render_formula_keeps_the_packagers_own_lines():
    out = core.render_formula(FORMULA, github_repo="o/r", version="2.0.0", sha256="d")
    assert 'desc "orchestration machine"' in out
    assert 'license "MIT"' in out


def test_render_formula_refuses_a_formula_it_cannot_rewrite():
    with pytest.raises(RuntimeError):
        core.render_formula("class Tide < Formula\nend\n", github_repo="o/r", version="1", sha256="d")


def test_asset_url_is_the_immutable_release_asset():
    url = core.asset_url("tide-tools/tide", "1.2.3")
    assert url.endswith("/releases/download/v1.2.3/tide-1.2.3.tar.gz")
    assert "/archive/" not in url  # the mutable one, never


# --- the plan ------------------------------------------------------------------


def _plan(repo: Path, tmp_path: Path, runner=None, **kw) -> core.ReleasePlan:
    """A plan with the real git archive stubbed out into a file we control."""
    runner = runner or FakeRunner()
    tap = tmp_path / "tap"
    (tap / "Formula").mkdir(parents=True)
    (tap / "Formula" / "tide.rb").write_text(FORMULA, encoding="utf-8")
    (tap / ".git").mkdir()

    def fake_build(r, version, dest, *, ref="HEAD", runner=None):
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        return _fake_tarball(dest / core.artifact_name(version), {"tide/ok.py": "x = 1\n"})

    kw.setdefault("run_gate", False)
    original = core.build_artifact
    core.build_artifact = fake_build
    try:
        kw.setdefault("tap_dir", tap)
        return core.plan_release(repo, runner=runner, **kw)
    finally:
        core.build_artifact = original


def test_plan_is_green_and_lists_every_step(repo: Path, tmp_path: Path):
    plan = _plan(repo, tmp_path)
    assert plan.ok is True
    names = [s.name for s in plan.steps]
    assert names == ["tag", "push-tag", "rebuild-artifact", "gh-release",
                     "formula", "commit-formula", "push-formula"]


def test_plan_pins_the_sha_of_the_artifact_it_built(repo: Path, tmp_path: Path):
    plan = _plan(repo, tmp_path)
    assert plan.sha256 == core.sha256_of(plan.artifact)
    preview = core.formula_preview(plan)
    assert plan.sha256 in preview
    assert plan.asset_url in preview


def test_plan_adds_a_version_bump_when_cutting_a_new_number(repo: Path, tmp_path: Path):
    plan = _plan(repo, tmp_path, version="2.0.0")
    names = [s.name for s in plan.steps]
    assert names[:2] == ["bump-version", "commit-version"]
    assert any("2.0.0" in n for n in plan.notes)


def test_plan_never_runs_a_mutating_command_while_resolving(repo: Path, tmp_path: Path):
    runner = FakeRunner()
    _plan(repo, tmp_path, runner=runner)
    for forbidden in ("git tag", "git push", "gh release create", "git commit"):
        assert not runner.ran(forbidden), "{0} ran during planning".format(forbidden)


def test_plan_offers_to_tap_when_brew_has_not(repo: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(core, "brew_tap_dir", lambda tap: (tmp_path / "taps" / "t", False))
    plan = _plan(repo, tmp_path, tap_dir=None)
    assert "tap-clone" in [s.name for s in plan.steps]


# --- applying ------------------------------------------------------------------


def test_apply_refuses_a_plan_that_is_not_green(repo: Path, tmp_path: Path):
    plan = _plan(repo, tmp_path)
    plan.checks.append(core.Check("invented", False, "nope"))
    runner = FakeRunner()
    res = core.apply_release(plan, runner=runner)
    assert res.ok is False
    assert res.done == []
    assert runner.calls == []  # not one command ran
    assert "REFUSED" in res.messages[0]


def test_apply_refuses_when_the_gate_is_red(repo: Path, tmp_path: Path):
    plan = _plan(repo, tmp_path)
    plan.gate = core.GateResult(portable_ok=True, suite_ok=False, suite_ran=True)
    runner = FakeRunner()
    res = core.apply_release(plan, runner=runner)
    assert res.ok is False
    assert runner.calls == []


def test_apply_runs_the_steps_and_writes_the_formula(repo: Path, tmp_path: Path):
    plan = _plan(repo, tmp_path)
    runner = FakeRunner()
    res = core.apply_release(plan, runner=runner)
    assert res.ok is True
    assert runner.ran("git tag -a v1.2.3")
    assert runner.ran("gh release create v1.2.3")
    formula = (plan.tap_dir / core.TAP_FORMULA_RELPATH).read_text(encoding="utf-8")
    assert plan.sha256 in formula
    assert 'assert_match "tide 1.2.3"' in formula


def test_apply_stops_at_the_first_failure(repo: Path, tmp_path: Path):
    # A half-applied release is recoverable by re-running; blindly carrying on
    # past a failed tag push is not.
    plan = _plan(repo, tmp_path)
    runner = FakeRunner(**{"push origin": (1, "remote rejected")})
    res = core.apply_release(plan, runner=runner)
    assert res.ok is False
    assert res.done == ["tag"]
    assert not runner.ran("gh release create")
    assert "remote rejected" in res.messages[-1]


def test_apply_bumps_the_version_on_disk(repo: Path, tmp_path: Path):
    plan = _plan(repo, tmp_path, version="2.0.0")
    core.apply_release(plan, runner=FakeRunner())
    assert core.current_version(repo) == "2.0.0"


def test_no_tap_leaves_the_release_green_and_the_formula_alone(repo: Path, tmp_path: Path):
    # The owner's word: the front door is `git clone` + install.sh. A pushed tag is
    # the release; Homebrew is a secondary channel that must never block one.
    plan = _plan(repo, tmp_path, update_tap=False)
    assert plan.ok is True
    names = [s.name for s in plan.steps]
    assert names == ["tag", "push-tag", "rebuild-artifact", "gh-release"]
    assert plan.tap_dir is None
    assert any("secondary channel" in n for n in plan.notes)


def test_a_missing_tap_is_a_note_not_a_refusal(repo: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(core, "brew_tap_dir", lambda tap: (None, False))
    plan = _plan(repo, tmp_path, tap_dir=None)
    assert plan.ok is True  # the release still stands
    tap_step = [s for s in plan.steps if s.name == "tap"][0]
    assert "not a blocker" in tap_step.detail

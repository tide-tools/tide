"""U1 unit/integration — paths: .tide root resolves from cwd-or-ancestor."""

from __future__ import annotations

import pytest

from tide import paths


def test_find_tide_root_at_root(tmp_project):
    assert paths.find_tide_root(tmp_project) == tmp_project.resolve()


def test_find_tide_root_from_descendant(tmp_project):
    deep = tmp_project / ".tide" / "arcs" / "03-x" / "workspace"
    deep.mkdir(parents=True)
    # resolving from deep inside the project climbs back to the project root
    assert paths.find_tide_root(deep) == tmp_project.resolve()


def test_find_tide_root_none_when_absent(tmp_path):
    assert paths.find_tide_root(tmp_path) is None


def test_require_tide_root_raises_when_absent(tmp_path):
    with pytest.raises(FileNotFoundError):
        paths.require_tide_root(tmp_path)


def test_require_tide_root_returns_root(tmp_project):
    assert paths.require_tide_root(tmp_project) == tmp_project.resolve()


def test_subdir_helpers_match_blueprint_layout(tmp_project):
    assert paths.canon_file(tmp_project) == tmp_project / ".tide" / "canon" / "CANON.md"
    assert paths.canon_config(tmp_project) == tmp_project / ".tide" / "canon" / "config"
    assert paths.arcs_dir(tmp_project) == tmp_project / ".tide" / "arcs"
    assert paths.candidates_dir(tmp_project) == tmp_project / ".tide" / "arcs" / "candidates"
    assert paths.state_dir(tmp_project) == tmp_project / ".tide" / "state"
    assert paths.strictness_file(tmp_project) == tmp_project / ".tide" / "state" / "strictness"


def test_control_home_detected(tmp_control_home):
    assert paths.is_control_home(tmp_control_home) is True
    assert paths.roster_file(tmp_control_home).is_file()


def test_plain_project_is_not_control_home(tmp_project):
    # no roster.md → not a control-home
    assert paths.is_control_home(tmp_project) is False


def test_global_install_dirs_point_at_shipped_dirs():
    root = paths.install_root()
    # the shipped scaffold has prompts/ and rules/ at the repo root
    assert paths.global_prompts_dir() == root / "prompts"
    assert paths.global_rules_dir() == root / "rules"
    assert (root / "src" / "tide").is_dir()  # sanity: install_root climbed correctly

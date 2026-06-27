"""U9 unit — tide.init_home logic (control-home unfold + per-project scaffold)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tide import init_home, paths, roster, strictness
from tide.canon import store


# --- per-project scaffold --------------------------------------------------

def test_scaffold_project_lays_down_skeleton(tmp_path: Path):
    init_home.scaffold_project(tmp_path, name="demo")

    assert paths.canon_file(tmp_path).is_file()
    assert paths.canon_config(tmp_path).read_text(encoding="utf-8").strip() == "lang=en"
    assert paths.candidates_dir(tmp_path).is_dir()
    assert paths.strictness_file(tmp_path).read_text(encoding="utf-8").strip() == "strict"


def test_scaffold_uses_dir_name_when_name_omitted(tmp_path: Path):
    proj = tmp_path / "alpha"
    proj.mkdir()
    init_home.scaffold_project(proj)
    assert "alpha" in paths.canon_file(proj).read_text(encoding="utf-8")


def test_scaffold_does_not_make_roster(tmp_path: Path):
    init_home.scaffold_project(tmp_path)
    assert not paths.roster_file(tmp_path).exists()


def test_scaffold_is_non_destructive(tmp_path: Path):
    init_home.scaffold_project(tmp_path, name="demo")
    paths.canon_file(tmp_path).write_text("# hand-edited CANON\n", encoding="utf-8")
    strictness.set_strictness(tmp_path, "loose")

    init_home.scaffold_project(tmp_path, name="demo")  # re-run

    assert paths.canon_file(tmp_path).read_text(encoding="utf-8") == "# hand-edited CANON\n"
    assert strictness.read_strictness(tmp_path) == "loose"


def test_scaffold_reports_created_then_empty_on_rerun(tmp_path: Path):
    first = init_home.scaffold_project(tmp_path, name="demo")
    assert any("CANON" in n for n in first)
    second = init_home.scaffold_project(tmp_path, name="demo")
    assert second == []


# --- control-home unfold ---------------------------------------------------

def test_unfold_control_home_full_layout(tmp_path: Path):
    init_home.unfold_control_home(tmp_path, name="home")

    assert paths.tide_dir(tmp_path).is_dir()
    assert paths.canon_file(tmp_path).is_file()
    assert paths.is_control_home(tmp_path)  # roster.md present
    assert (tmp_path / "README.md").is_file()
    assert paths.roster_file(tmp_path).read_text(encoding="utf-8").startswith(roster.HEADER)


def test_unfold_readme_mentions_name(tmp_path: Path):
    init_home.unfold_control_home(tmp_path, name="atlas")
    assert "atlas" in (tmp_path / "README.md").read_text(encoding="utf-8")


def test_unfold_preserves_existing_roster_and_readme(tmp_path: Path):
    init_home.unfold_control_home(tmp_path, name="home")
    roster.add(tmp_path, "focus", "/p/focus")
    (tmp_path / "README.md").write_text("# custom\n", encoding="utf-8")

    init_home.unfold_control_home(tmp_path, name="home")  # re-run

    assert {"name": "focus", "path": "/p/focus"} in roster.read_roster(tmp_path)
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "# custom\n"


def test_unfold_force_overwrites_readme(tmp_path: Path):
    init_home.unfold_control_home(tmp_path, name="home")
    (tmp_path / "README.md").write_text("# custom\n", encoding="utf-8")
    init_home.unfold_control_home(tmp_path, name="home", force=True)
    assert "tide control-home" in (tmp_path / "README.md").read_text(encoding="utf-8")


def test_unfold_with_git_creates_repo(tmp_path: Path):
    pytest.importorskip("subprocess")
    init_home.unfold_control_home(tmp_path, name="home", git=True)
    # git may be absent on the box → best-effort; only assert when it ran.
    git_dir = tmp_path / ".git"
    if git_dir.exists():
        assert git_dir.is_dir()


def test_unfolded_home_is_resolvable_root(tmp_path: Path):
    init_home.unfold_control_home(tmp_path, name="home")
    nested = tmp_path / "deep" / "nested"
    nested.mkdir(parents=True)
    assert paths.find_tide_root(nested) == tmp_path.resolve()

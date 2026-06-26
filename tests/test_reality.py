"""M2 unit — cannon.reality: manifest parsing + reality-rev (content hash).

Coverage targets:
* _parse_canon_text: indented globs, dash-list globs, stops at ## , None when absent
* parse_manifest: canon preamble vs state-file fallback, None when neither
* reality_rev: None for no manifest; stable; changes with covered files;
  stable for uncovered files; empty-match → defined (not None)
* git mode: ignores untracked files; detects new tracked files
* stamp_reality_rev: stamps the passport doc; no-op when no manifest
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List

import pytest

from tide.cannon import reality

from tests.conftest import build_tide_skeleton


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _init_git(path: Path) -> None:
    """Initialize a bare git repo with an initial commit."""
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    (path / ".gitkeep").write_text("", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(path), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init"],
        check=True, capture_output=True,
    )


def _git_add_commit(path: Path, *rel_paths: str) -> None:
    for rel in rel_paths:
        subprocess.run(
            ["git", "-C", str(path), "add", rel], check=True, capture_output=True
        )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "add files"],
        check=True, capture_output=True,
    )


@pytest.fixture
def tmp_git_project(tmp_path: Path) -> Path:
    """A tmp project with a git repo initialized and an initial commit."""
    build_tide_skeleton(tmp_path, name="demo")
    _init_git(tmp_path)
    return tmp_path


def _write_canon_covers_preamble(root: Path, globs: List[str]) -> None:
    """Insert a ``canon-covers:`` block into CANON.md's preamble (indented format)."""
    from tide import paths
    canon = paths.canon_file(root)
    text = canon.read_text(encoding="utf-8")
    block = "canon-covers:\n" + "".join("  {0}\n".format(g) for g in globs)
    # Insert after the H1 line (first line starting with "# ")
    lines = text.splitlines()
    h1_idx = next(i for i, ln in enumerate(lines) if ln.startswith("# "))
    lines.insert(h1_idx + 1, block.rstrip())
    canon.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_state_covers(root: Path, lines: List[str]) -> None:
    """Write *lines* to ``.tide/state/canon-covers``."""
    from tide import paths
    (paths.state_dir(root) / "canon-covers").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# _parse_canon_text (pure)
# ---------------------------------------------------------------------------

def test_parse_canon_text_none_when_no_marker():
    text = "# CANON.md — demo\n\n## What it is\n"
    assert reality._parse_canon_text(text) is None


def test_parse_canon_text_indented_globs():
    text = (
        "# CANON.md — demo\n"
        "canon-covers:\n"
        "  src/**/*.py\n"
        "  tests/*.py\n"
        "\n"
        "## What it is\n"
    )
    globs = reality._parse_canon_text(text)
    assert globs == ["src/**/*.py", "tests/*.py"]


def test_parse_canon_text_dash_list_format():
    text = (
        "# CANON.md — demo\n"
        "canon-covers:\n"
        "- src/**/*.py\n"
        "- tests/*.py\n"
        "\n"
        "## What it is\n"
    )
    globs = reality._parse_canon_text(text)
    assert globs == ["src/**/*.py", "tests/*.py"]


def test_parse_canon_text_stops_at_first_h2():
    text = (
        "# CANON.md — demo\n"
        "canon-covers:\n"
        "  src/*.py\n"
        "## What it is\n"
        "  not-a-glob-section-body\n"
    )
    globs = reality._parse_canon_text(text)
    assert globs == ["src/*.py"]


def test_parse_canon_text_ends_at_non_indented_line():
    text = (
        "# CANON.md — demo\n"
        "canon-covers:\n"
        "  src/*.py\n"
        "some-other-field: value\n"
        "  should-not-be-a-glob\n"
    )
    globs = reality._parse_canon_text(text)
    assert globs == ["src/*.py"]


def test_parse_canon_text_blank_lines_inside_block_ok():
    text = (
        "# CANON.md — demo\n"
        "canon-covers:\n"
        "  src/*.py\n"
        "\n"
        "  tests/*.py\n"
        "\n"
        "## What it is\n"
    )
    globs = reality._parse_canon_text(text)
    assert globs == ["src/*.py", "tests/*.py"]


def test_parse_canon_text_returns_none_on_empty_block():
    text = (
        "# CANON.md — demo\n"
        "canon-covers:\n"
        "\n"
        "## What it is\n"
    )
    assert reality._parse_canon_text(text) is None


# ---------------------------------------------------------------------------
# parse_manifest
# ---------------------------------------------------------------------------

def test_parse_manifest_none_when_no_manifest(tmp_project):
    assert reality.parse_manifest(tmp_project) is None


def test_parse_manifest_from_canon_preamble(tmp_project):
    _write_canon_covers_preamble(tmp_project, ["src/**/*.py", "tests/*.py"])
    globs = reality.parse_manifest(tmp_project)
    assert globs == ["src/**/*.py", "tests/*.py"]


def test_parse_manifest_from_state_file(tmp_project):
    _write_state_covers(tmp_project, ["src/*.py", "# comment line", "tests/*.py"])
    globs = reality.parse_manifest(tmp_project)
    # comment lines must be stripped
    assert globs == ["src/*.py", "tests/*.py"]


def test_parse_manifest_canon_takes_priority_over_state(tmp_project):
    """When both CANON.md preamble and state file have manifests, CANON.md wins."""
    _write_canon_covers_preamble(tmp_project, ["src/*.py"])
    _write_state_covers(tmp_project, ["tests/*.py"])
    globs = reality.parse_manifest(tmp_project)
    assert globs == ["src/*.py"]


def test_parse_manifest_falls_back_to_state_when_canon_has_none(tmp_project):
    """No canon-covers in CANON.md → fall through to state file."""
    _write_state_covers(tmp_project, ["*.md"])
    globs = reality.parse_manifest(tmp_project)
    assert globs == ["*.md"]


def test_parse_manifest_state_only_comments_returns_none(tmp_project):
    _write_state_covers(tmp_project, ["# just a comment"])
    assert reality.parse_manifest(tmp_project) is None


# ---------------------------------------------------------------------------
# reality_rev — no manifest (graceful degradation)
# ---------------------------------------------------------------------------

def test_reality_rev_none_when_no_manifest(tmp_project):
    """No canon-covers manifest → None (graceful, not an error)."""
    assert reality.reality_rev(tmp_project) is None


def test_reality_rev_none_with_empty_state_manifest(tmp_project):
    _write_state_covers(tmp_project, ["# only comments"])
    assert reality.reality_rev(tmp_project) is None


# ---------------------------------------------------------------------------
# reality_rev — filesystem fallback (no git required)
# ---------------------------------------------------------------------------

def test_reality_rev_stable_without_git(tmp_project):
    """Same content → same rev (deterministic)."""
    _write_state_covers(tmp_project, ["*.md"])
    (tmp_project / "readme.md").write_text("hello", encoding="utf-8")
    r1 = reality.reality_rev(tmp_project)
    r2 = reality.reality_rev(tmp_project)
    assert r1 is not None
    assert r1 == r2


def test_reality_rev_changes_when_covered_file_changes(tmp_project):
    """Modifying a covered file bumps the rev."""
    _write_state_covers(tmp_project, ["*.md"])
    f = tmp_project / "readme.md"
    f.write_text("v1", encoding="utf-8")
    r1 = reality.reality_rev(tmp_project)

    f.write_text("v2", encoding="utf-8")
    r2 = reality.reality_rev(tmp_project)
    assert r1 != r2


def test_reality_rev_stable_when_uncovered_file_changes(tmp_project):
    """Changing a file NOT in the manifest does not bump the rev."""
    _write_state_covers(tmp_project, ["*.md"])
    (tmp_project / "readme.md").write_text("hello", encoding="utf-8")
    r1 = reality.reality_rev(tmp_project)

    # change a .py file (not *.md → not covered)
    (tmp_project / "script.py").write_text("print('hi')", encoding="utf-8")
    r2 = reality.reality_rev(tmp_project)
    assert r1 == r2


def test_reality_rev_changes_when_covered_file_added(tmp_project):
    """Adding a new file matching the glob bumps the rev."""
    _write_state_covers(tmp_project, ["*.md"])
    r1 = reality.reality_rev(tmp_project)  # no .md files yet

    (tmp_project / "notes.md").write_text("new", encoding="utf-8")
    r2 = reality.reality_rev(tmp_project)
    assert r1 != r2


def test_reality_rev_empty_glob_match_returns_defined_rev(tmp_project):
    """A manifest that matches no files returns a defined rev (not None)."""
    _write_state_covers(tmp_project, ["nonexistent/**/*.xyz"])
    r = reality.reality_rev(tmp_project)
    assert r is not None  # empty-tree hash, not None


def test_reality_rev_is_short(tmp_project):
    _write_state_covers(tmp_project, ["*.md"])
    (tmp_project / "f.md").write_text("x", encoding="utf-8")
    r = reality.reality_rev(tmp_project)
    assert r is not None
    assert len(r) == reality.REV_LEN


# ---------------------------------------------------------------------------
# API-surface fingerprinting for CODE files (kill gate fatigue)
# ---------------------------------------------------------------------------

_PY_V1 = (
    "import os\n"
    "\n"
    "# original comment\n"
    "def greet(name):\n"
    "    return 'hello ' + name\n"
    "\n"
    "class Widget:\n"
    "    def render(self):\n"
    "        return 1\n"
)


def test_api_surface_extracts_signatures():
    """_api_surface keeps only signature lines, stripped + sorted."""
    surface = reality._api_surface(_PY_V1)
    lines = surface.splitlines()
    assert "def greet(name):" in lines
    assert "class Widget:" in lines
    assert "def render(self):" in lines
    # body / import / comment lines are excluded
    assert "import os" not in surface
    assert "return 1" not in surface
    assert "# original comment" not in surface


def test_reality_rev_no_trip_on_comment_only_edit(tmp_project):
    """(a) A comment-only edit to a covered .py does NOT change reality-rev."""
    _write_state_covers(tmp_project, ["*.py"])
    f = tmp_project / "mod.py"
    f.write_text(_PY_V1, encoding="utf-8")
    r1 = reality.reality_rev(tmp_project)

    f.write_text(_PY_V1.replace("# original comment", "# a totally rewritten comment"),
                 encoding="utf-8")
    r2 = reality.reality_rev(tmp_project)
    assert r1 == r2  # comments are not API surface


def test_reality_rev_no_trip_on_whitespace_only_edit(tmp_project):
    """(a) A whitespace-only edit to a covered .py does NOT change reality-rev."""
    _write_state_covers(tmp_project, ["*.py"])
    f = tmp_project / "mod.py"
    f.write_text(_PY_V1, encoding="utf-8")
    r1 = reality.reality_rev(tmp_project)

    # add trailing whitespace + extra blank lines (no signature change)
    noisy = _PY_V1.replace("def greet(name):", "def greet(name):   ") + "\n\n\n"
    f.write_text(noisy, encoding="utf-8")
    r2 = reality.reality_rev(tmp_project)
    assert r1 == r2  # whitespace churn is normalized away


def test_reality_rev_no_trip_on_body_only_edit(tmp_project):
    """(a) A function-BODY-only edit to a covered .py does NOT change reality-rev.

    This is the deliberate Drift tradeoff: a behavioural change that keeps the
    same signature is invisible to the reality axis (M3/substance covers that).
    """
    _write_state_covers(tmp_project, ["*.py"])
    f = tmp_project / "mod.py"
    f.write_text(_PY_V1, encoding="utf-8")
    r1 = reality.reality_rev(tmp_project)

    body_changed = _PY_V1.replace(
        "return 'hello ' + name", "return 'HELLO ' + name.upper()"
    )
    f.write_text(body_changed, encoding="utf-8")
    r2 = reality.reality_rev(tmp_project)
    assert r1 == r2  # body change is not API surface


def test_reality_rev_no_trip_on_added_test(tmp_project):
    """(a) Adding a non-covered test file does NOT change reality-rev."""
    _write_state_covers(tmp_project, ["src/*.py"])
    src = tmp_project / "src"
    src.mkdir()
    (src / "mod.py").write_text(_PY_V1, encoding="utf-8")
    r1 = reality.reality_rev(tmp_project)

    # a test file outside the covered glob
    tests = tmp_project / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text("def test_greet():\n    assert True\n",
                                       encoding="utf-8")
    r2 = reality.reality_rev(tmp_project)
    assert r1 == r2  # uncovered test addition is invisible


def test_reality_rev_trips_on_new_def_signature(tmp_project):
    """(b) Adding a new def signature to a covered .py DOES change reality-rev."""
    _write_state_covers(tmp_project, ["*.py"])
    f = tmp_project / "mod.py"
    f.write_text(_PY_V1, encoding="utf-8")
    r1 = reality.reality_rev(tmp_project)

    f.write_text(_PY_V1 + "\ndef farewell(name):\n    return 'bye ' + name\n",
                 encoding="utf-8")
    r2 = reality.reality_rev(tmp_project)
    assert r1 != r2  # a new signature is a real API change


def test_reality_rev_trips_on_changed_def_signature(tmp_project):
    """(b) Changing an existing def signature DOES change reality-rev."""
    _write_state_covers(tmp_project, ["*.py"])
    f = tmp_project / "mod.py"
    f.write_text(_PY_V1, encoding="utf-8")
    r1 = reality.reality_rev(tmp_project)

    # add a parameter → signature changes
    f.write_text(_PY_V1.replace("def greet(name):", "def greet(name, loud=False):"),
                 encoding="utf-8")
    r2 = reality.reality_rev(tmp_project)
    assert r1 != r2


def test_reality_rev_trips_on_new_class(tmp_project):
    """(b) Adding a new class DOES change reality-rev."""
    _write_state_covers(tmp_project, ["*.py"])
    f = tmp_project / "mod.py"
    f.write_text(_PY_V1, encoding="utf-8")
    r1 = reality.reality_rev(tmp_project)

    f.write_text(_PY_V1 + "\nclass Gadget:\n    pass\n", encoding="utf-8")
    r2 = reality.reality_rev(tmp_project)
    assert r1 != r2


def test_reality_rev_md_full_content_still_trips(tmp_project):
    """(d) A covered .md content change still trips (full-content fallback).

    Non-code files have no recognizable signatures, so they fall back to a full
    content hash — docs/config remain tracked verbatim.
    """
    _write_state_covers(tmp_project, ["*.md"])
    f = tmp_project / "doc.md"
    f.write_text("# Title\n\nFirst draft.\n", encoding="utf-8")
    r1 = reality.reality_rev(tmp_project)

    f.write_text("# Title\n\nSecond draft (reworded).\n", encoding="utf-8")
    r2 = reality.reality_rev(tmp_project)
    assert r1 != r2  # .md is non-code → full content hash


def test_reality_rev_ts_export_signature_trips(tmp_project):
    """API-surface fingerprinting also covers TS (export/interface/type).

    Note: the line-based stdlib regex can only separate signature from body when
    they are on different lines (the Pythonic case). A comment-only edit never
    trips; a new ``export``/``interface`` signature always does.
    """
    _write_state_covers(tmp_project, ["*.ts"])
    f = tmp_project / "api.ts"
    body_v1 = (
        "// header comment\n"
        "export function foo(): number {\n"
        "  return 1;\n"
        "}\n"
        "interface Shape { x: number; }\n"
    )
    f.write_text(body_v1, encoding="utf-8")
    r1 = reality.reality_rev(tmp_project)

    # comment-only edit → no trip (comments are never API surface)
    f.write_text(body_v1.replace("// header comment", "// changed comment"),
                 encoding="utf-8")
    assert reality.reality_rev(tmp_project) == r1

    # body-only edit on its own line → no trip
    f.write_text(body_v1.replace("  return 1;", "  return 2;"), encoding="utf-8")
    assert reality.reality_rev(tmp_project) == r1

    # new export signature → trip
    f.write_text(body_v1 + "export function bar(): void {}\n", encoding="utf-8")
    assert reality.reality_rev(tmp_project) != r1


# ---------------------------------------------------------------------------
# canon-covers-exclude (candidate 32)
# ---------------------------------------------------------------------------

def _write_state_exclude(root, globs):
    from tide import paths as _p
    (_p.state_dir(root) / "canon-covers-exclude").write_text(
        "\n".join(globs) + "\n", encoding="utf-8"
    )


def test_parse_exclude_none_returns_empty_list(tmp_project):
    assert reality.parse_exclude(tmp_project) == []


def test_parse_exclude_from_state_file(tmp_project):
    _write_state_exclude(tmp_project, ["*.lock", "# comment", "vendor/*"])
    assert reality.parse_exclude(tmp_project) == ["*.lock", "vendor/*"]


def test_parse_exclude_from_canon_preamble(tmp_project):
    from tide import paths as _p
    canon = _p.canon_file(tmp_project)
    text = canon.read_text(encoding="utf-8")
    lines = text.splitlines()
    h1 = next(i for i, ln in enumerate(lines) if ln.startswith("# "))
    lines.insert(h1 + 1, "canon-covers-exclude:\n  *.lock\n  generated/*".rstrip())
    canon.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert reality.parse_exclude(tmp_project) == ["*.lock", "generated/*"]


def test_reality_rev_excluded_path_no_trip(tmp_project):
    """(c) Changing an excluded path does NOT trip reality-rev."""
    _write_state_covers(tmp_project, ["*.py", "*.lock"])
    _write_state_exclude(tmp_project, ["*.lock"])
    (tmp_project / "mod.py").write_text(_PY_V1, encoding="utf-8")
    lock = tmp_project / "deps.lock"
    lock.write_text("dep-a==1.0\n", encoding="utf-8")
    r1 = reality.reality_rev(tmp_project)

    # bump the lockfile → excluded, must not trip
    lock.write_text("dep-a==1.1\ndep-b==2.0\n", encoding="utf-8")
    r2 = reality.reality_rev(tmp_project)
    assert r1 == r2  # excluded path is invisible to reality-rev


def test_reality_rev_excluded_path_dropped_entirely(tmp_project):
    """An excluded path contributes nothing — same rev with or without it present."""
    _write_state_covers(tmp_project, ["*.py", "*.lock"])
    _write_state_exclude(tmp_project, ["*.lock"])
    (tmp_project / "mod.py").write_text(_PY_V1, encoding="utf-8")
    r_no_lock = reality.reality_rev(tmp_project)

    (tmp_project / "deps.lock").write_text("dep-a==1.0\n", encoding="utf-8")
    r_with_lock = reality.reality_rev(tmp_project)
    assert r_no_lock == r_with_lock  # adding an excluded file changes nothing


def test_reality_rev_non_excluded_code_still_trips(tmp_project):
    """Sanity: with excludes configured, a real signature change still trips."""
    _write_state_covers(tmp_project, ["*.py", "*.lock"])
    _write_state_exclude(tmp_project, ["*.lock"])
    f = tmp_project / "mod.py"
    f.write_text(_PY_V1, encoding="utf-8")
    r1 = reality.reality_rev(tmp_project)

    f.write_text(_PY_V1 + "\ndef extra():\n    pass\n", encoding="utf-8")
    r2 = reality.reality_rev(tmp_project)
    assert r1 != r2


def test_exclude_works_in_git_mode(tmp_git_project):
    """Excludes apply in git mode too (lockfile bump invisible)."""
    _write_state_covers(tmp_git_project, ["*.py", "*.lock"])
    _write_state_exclude(tmp_git_project, ["*.lock"])
    (tmp_git_project / "mod.py").write_text(_PY_V1, encoding="utf-8")
    (tmp_git_project / "deps.lock").write_text("a==1.0\n", encoding="utf-8")
    _git_add_commit(tmp_git_project, "mod.py", "deps.lock")
    r1 = reality.reality_rev(tmp_git_project)

    (tmp_git_project / "deps.lock").write_text("a==1.1\n", encoding="utf-8")
    _git_add_commit(tmp_git_project, "deps.lock")
    r2 = reality.reality_rev(tmp_git_project)
    assert r1 == r2  # excluded lockfile change invisible


def test_git_mode_code_body_change_no_trip(tmp_git_project):
    """In git mode, a committed body-only change to covered code does NOT trip."""
    _write_state_covers(tmp_git_project, ["*.py"])
    f = tmp_git_project / "mod.py"
    f.write_text(_PY_V1, encoding="utf-8")
    _git_add_commit(tmp_git_project, "mod.py")
    r1 = reality.reality_rev(tmp_git_project)

    f.write_text(_PY_V1.replace("return 1", "return 99"), encoding="utf-8")
    _git_add_commit(tmp_git_project, "mod.py")
    r2 = reality.reality_rev(tmp_git_project)
    assert r1 == r2  # body change, same signatures


def test_git_mode_code_signature_change_trips(tmp_git_project):
    """In git mode, a committed signature change to covered code DOES trip."""
    _write_state_covers(tmp_git_project, ["*.py"])
    f = tmp_git_project / "mod.py"
    f.write_text(_PY_V1, encoding="utf-8")
    _git_add_commit(tmp_git_project, "mod.py")
    r1 = reality.reality_rev(tmp_git_project)

    f.write_text(_PY_V1 + "\ndef added():\n    return 0\n", encoding="utf-8")
    _git_add_commit(tmp_git_project, "mod.py")
    r2 = reality.reality_rev(tmp_git_project)
    assert r1 != r2


# ---------------------------------------------------------------------------
# reality_rev — git mode
# ---------------------------------------------------------------------------

def test_reality_rev_with_git_returns_rev(tmp_git_project):
    """In a git repo with a manifest and tracked file → returns a rev."""
    _write_state_covers(tmp_git_project, ["*.md"])
    (tmp_git_project / "readme.md").write_text("v1", encoding="utf-8")
    _git_add_commit(tmp_git_project, "readme.md")
    r = reality.reality_rev(tmp_git_project)
    assert r is not None
    assert len(r) == reality.REV_LEN


def test_reality_rev_git_ignores_untracked_files(tmp_git_project):
    """Untracked files are invisible to reality-rev in git mode."""
    _write_state_covers(tmp_git_project, ["*.md"])
    (tmp_git_project / "readme.md").write_text("v1", encoding="utf-8")
    _git_add_commit(tmp_git_project, "readme.md")
    r1 = reality.reality_rev(tmp_git_project)

    # Write an untracked file (not committed)
    (tmp_git_project / "untracked.md").write_text("invisible", encoding="utf-8")
    r2 = reality.reality_rev(tmp_git_project)
    assert r1 == r2  # untracked file → no change


def test_reality_rev_git_changes_after_new_tracked_file(tmp_git_project):
    """Committing a new covered file bumps the rev."""
    _write_state_covers(tmp_git_project, ["*.md"])
    r1 = reality.reality_rev(tmp_git_project)  # no .md files tracked yet

    (tmp_git_project / "readme.md").write_text("v1", encoding="utf-8")
    _git_add_commit(tmp_git_project, "readme.md")
    r2 = reality.reality_rev(tmp_git_project)
    assert r1 != r2


def test_reality_rev_git_changes_after_content_change(tmp_git_project):
    """Committing a changed covered file bumps the rev."""
    _write_state_covers(tmp_git_project, ["*.md"])
    (tmp_git_project / "readme.md").write_text("v1", encoding="utf-8")
    _git_add_commit(tmp_git_project, "readme.md")
    r1 = reality.reality_rev(tmp_git_project)

    (tmp_git_project / "readme.md").write_text("v2", encoding="utf-8")
    _git_add_commit(tmp_git_project, "readme.md")
    r2 = reality.reality_rev(tmp_git_project)
    assert r1 != r2


def test_reality_rev_git_stable_on_uncommitted_change(tmp_git_project):
    """Modifying a tracked file WITHOUT committing → git ls-files sees old content."""
    _write_state_covers(tmp_git_project, ["*.md"])
    (tmp_git_project / "readme.md").write_text("v1", encoding="utf-8")
    _git_add_commit(tmp_git_project, "readme.md")
    r1 = reality.reality_rev(tmp_git_project)

    # Modify in-place but don't commit (git ls-files reads the working-tree file,
    # so the rev WILL change — the hash is over the current file content, not the
    # committed blob). This test documents that behaviour explicitly.
    (tmp_git_project / "readme.md").write_text("v2-unstaged", encoding="utf-8")
    r2 = reality.reality_rev(tmp_git_project)
    # git ls-files returns the path; we hash the current file content.
    # An unstaged change IS visible because we read the file, not the blob.
    assert r1 != r2


# ---------------------------------------------------------------------------
# stamp_reality_rev
# ---------------------------------------------------------------------------

def test_stamp_reality_rev_writes_field(tmp_project):
    """stamp_reality_rev writes reality-rev into the passport doc."""
    from tide import fields
    _write_state_covers(tmp_project, ["*.md"])
    (tmp_project / "readme.md").write_text("hello", encoding="utf-8")

    passport = tmp_project / "arc.md"
    passport.write_text("# 01-work\nstatus: active\n", encoding="utf-8")

    rr = reality.stamp_reality_rev(passport, tmp_project)
    assert rr is not None
    assert fields.read_field(passport, "reality-rev") == rr


def test_stamp_reality_rev_noop_when_no_manifest(tmp_project):
    """Without a manifest, stamp_reality_rev is a no-op (returns None)."""
    from tide import fields
    passport = tmp_project / "arc.md"
    passport.write_text("# 01-work\nstatus: active\n", encoding="utf-8")

    rr = reality.stamp_reality_rev(passport, tmp_project)
    assert rr is None
    assert fields.read_field(passport, "reality-rev") is None


# ---------------------------------------------------------------------------
# stamp_rev integration (M2 wired into arc lifecycle)
# ---------------------------------------------------------------------------

def test_new_arc_stamps_reality_rev_when_manifest_present(tmp_project):
    """arc new stamps reality-rev when a canon-covers manifest exists."""
    from tide import fields
    from tide.arc import stream
    _write_state_covers(tmp_project, ["*.md"])

    entry = stream.new_arc(tmp_project, "work")
    rr = fields.read_field(entry / "arc.md", "reality-rev")
    assert rr is not None
    assert rr == reality.reality_rev(tmp_project)


def test_new_arc_no_reality_rev_without_manifest(tmp_project):
    """arc new does NOT stamp reality-rev when there is no manifest."""
    from tide import fields
    from tide.arc import stream

    entry = stream.new_arc(tmp_project, "work")
    assert fields.read_field(entry / "arc.md", "reality-rev") is None


def test_open_arc_restamps_reality_rev(tmp_project):
    """arc open re-stamps reality-rev to the current value."""
    from tide import fields
    from tide.arc import stream
    _write_state_covers(tmp_project, ["*.md"])

    entry = stream.new_arc(tmp_project, "work")
    f = tmp_project / "readme.md"
    f.write_text("v1", encoding="utf-8")
    # Force-stamp old value so we can verify a re-stamp happens
    reality.stamp_reality_rev(entry / "arc.md", tmp_project)
    old_rr = fields.read_field(entry / "arc.md", "reality-rev")

    f.write_text("v2", encoding="utf-8")
    stream.open_arc(tmp_project, "work")
    new_rr = fields.read_field(entry / "arc.md", "reality-rev")
    assert new_rr == reality.reality_rev(tmp_project)
    assert new_rr != old_rr  # re-stamp updated it

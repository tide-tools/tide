"""18-self-update — the pluggable VERSION SOURCE (source-of-truth abstraction).

Covers Revision identity + staleness, the git/pyproject/marker probes, and the
LocalSourceCheckout's installed/available/install_command — including the
editable-vs-marker distinction that defines "what is installed".
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tide.update import source as src


# --- Revision identity / staleness -----------------------------------------


def test_revision_identity_uses_commit_when_known():
    r = src.Revision(version="0.1.0", commit="abc1234")
    assert r.identity == "0.1.0+abc1234"


def test_revision_identity_falls_back_to_version():
    assert src.Revision(version="0.2.0").identity == "0.2.0"


def test_dirty_does_not_change_identity():
    clean = src.Revision("0.1.0", "abc", dirty=False)
    dirty = src.Revision("0.1.0", "abc", dirty=True)
    assert clean.identity == dirty.identity


# --- pyproject + git probes -------------------------------------------------


def test_read_pyproject_version(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "tide"\nversion = "9.9.9"\n', encoding="utf-8"
    )
    assert src.read_pyproject_version(tmp_path) == "9.9.9"


def test_read_pyproject_version_missing_is_none(tmp_path: Path):
    assert src.read_pyproject_version(tmp_path) is None


def _git_init(repo: Path) -> None:
    for args in (
        ["init", "--quiet"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_git_head_reports_sha_and_clean(tmp_path: Path):
    _git_init(tmp_path)
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init", "--quiet"],
        check=True, capture_output=True,
    )
    sha, dirty = src.git_head(tmp_path)
    assert sha and len(sha) >= 4
    assert dirty is False


def test_git_head_reports_dirty(tmp_path: Path):
    _git_init(tmp_path)
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init", "--quiet"],
        check=True, capture_output=True,
    )
    (tmp_path / "f.txt").write_text("changed", encoding="utf-8")
    _, dirty = src.git_head(tmp_path)
    assert dirty is True


def test_git_head_non_repo_is_none(tmp_path: Path):
    assert src.git_head(tmp_path) == (None, False)


# --- install marker ---------------------------------------------------------


def test_marker_roundtrip(tmp_path: Path):
    marker = tmp_path / "install-marker.json"
    rev = src.Revision("1.2.3", "deadbee", dirty=False)
    src.write_marker(marker, rev, tmp_path)
    data = src.read_marker(marker)
    assert data["version"] == "1.2.3"
    assert data["commit"] == "deadbee"
    assert data["source"] == str(tmp_path)


def test_read_marker_absent_is_none(tmp_path: Path):
    assert src.read_marker(tmp_path / "nope.json") is None


def test_read_marker_bad_json_is_none(tmp_path: Path):
    bad = tmp_path / "m.json"
    bad.write_text("{not json", encoding="utf-8")
    assert src.read_marker(bad) is None


def test_default_marker_path_honours_tide_home():
    p = src.default_marker_path({"TIDE_HOME": "/somewhere/tide"})
    assert p == Path("/somewhere/tide") / "install-marker.json"


# --- LocalSourceCheckout ----------------------------------------------------


def _checkout(tmp_path: Path, *, editable: bool, marker: Path) -> src.LocalSourceCheckout:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "5.0.0"\n', encoding="utf-8"
    )
    return src.LocalSourceCheckout(
        source_dir=tmp_path, python_exe="/py", editable=editable, marker_path=marker
    )


def test_available_reads_pyproject_version(tmp_path: Path):
    co = _checkout(tmp_path, editable=True, marker=tmp_path / "m.json")
    assert co.available().version == "5.0.0"


def test_installed_prefers_marker(tmp_path: Path):
    marker = tmp_path / "m.json"
    src.write_marker(marker, src.Revision("0.0.1", "old1234"), tmp_path)
    co = _checkout(tmp_path, editable=True, marker=marker)
    inst = co.installed()
    assert inst.version == "0.0.1"
    assert inst.commit == "old1234"


def test_stale_when_marker_lags_source(tmp_path: Path):
    # marker says we installed 0.0.1; pyproject (source) says 5.0.0 → stale.
    marker = tmp_path / "m.json"
    src.write_marker(marker, src.Revision("0.0.1", "old1234"), tmp_path)
    co = _checkout(tmp_path, editable=True, marker=marker)
    assert src.is_stale(co) is True


def test_not_stale_when_marker_matches_source(tmp_path: Path):
    co = _checkout(tmp_path, editable=True, marker=tmp_path / "m.json")
    src.write_marker(tmp_path / "m.json", co.available(), tmp_path)
    assert src.is_stale(co) is False


def test_install_command_preserves_editable(tmp_path: Path):
    co = _checkout(tmp_path, editable=True, marker=tmp_path / "m.json")
    cmd = co.install_command()
    assert cmd[:5] == ["/py", "-m", "pip", "install", "--upgrade"]
    assert "-e" in cmd
    assert cmd[-1] == str(tmp_path)


def test_install_command_non_editable_has_no_dash_e(tmp_path: Path):
    co = _checkout(tmp_path, editable=False, marker=tmp_path / "m.json")
    assert "-e" not in co.install_command()


def test_record_install_stamps_marker(tmp_path: Path):
    marker = tmp_path / "m.json"
    co = _checkout(tmp_path, editable=False, marker=marker)
    recorded = co.record_install()
    assert recorded.version == "5.0.0"
    assert src.read_marker(marker)["version"] == "5.0.0"


# --- resolution -------------------------------------------------------------


def test_resolve_source_honours_tide_source_override(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n', encoding="utf-8")
    source = src.resolve_source(
        env={"TIDE_SOURCE": str(tmp_path)},
        python_exe="/py",
        marker_path=tmp_path / "m.json",
    )
    assert source is not None
    assert source.name() == "local-source"
    assert source.available().version == "1.0.0"


def test_resolve_source_none_when_override_missing(tmp_path: Path, monkeypatch):
    # Override points nowhere and editable_origin disabled → no local source.
    monkeypatch.setattr(src, "editable_origin", lambda: None)
    monkeypatch.setattr(src, "_walk_up_to_checkout", lambda start: None)
    source = src.resolve_source(
        env={"TIDE_SOURCE": str(tmp_path / "does-not-exist")},
        python_exe="/py",
        marker_path=tmp_path / "m.json",
    )
    assert source is None

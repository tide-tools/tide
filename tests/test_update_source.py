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


# --- version ordering (newer-only staleness for the published channel) ------


def test_version_is_newer_true_for_higher_patch():
    assert src.version_is_newer("1.0.2", "1.0.1") is True


def test_version_is_newer_false_for_lower_version():
    # the regression this fix is about: an installed build AHEAD of the channel
    # must NOT read as "newer available" (no downgrade nudge).
    assert src.version_is_newer("1.0.1", "1.0.2") is False


def test_version_is_newer_false_for_equal_version():
    assert src.version_is_newer("1.0.2", "1.0.2") is False


def test_version_is_newer_zero_pads_unequal_lengths():
    # 1.0 == 1.0.0 — neither is newer than the other.
    assert src.version_is_newer("1.0", "1.0.0") is False
    assert src.version_is_newer("1.0.0", "1.0") is False
    assert src.version_is_newer("1.0.1", "1.0") is True


def test_version_is_newer_defensive_on_non_numeric():
    # odd/non-numeric components fall back to "not newer" rather than crash.
    assert src.version_is_newer("1.0.0-rc1", "1.0.0") is False
    assert src.version_is_newer("2.0.0", "abc") is False
    assert src.version_is_newer("", "1.0.0") is False


def test_version_tuple_rejects_unicode_digits_without_crash():
    # str.isdigit() accepts non-ASCII digits (e.g. "²") that int() then rejects —
    # the ascii gate must treat them as non-numeric (None), never raise ValueError.
    assert src._version_tuple("1.0.²") is None  # "1.0.²"


def test_version_is_newer_defensive_on_unicode_digits():
    assert src.version_is_newer("1.0.²", "1.0.0") is False
    assert src.version_is_newer("1.0.0", "1.0.²") is False


def test_revision_is_stale_newer_only_never_flags_downgrade():
    installed = src.Revision("1.0.2")
    available = src.Revision("1.0.1")
    assert src.revision_is_stale(installed, available, newer_only=True) is False
    assert src.revision_is_stale(installed, available, newer_only=False) is True  # identity differs


def test_revision_is_stale_newer_only_flags_genuine_upgrade():
    assert src.revision_is_stale(
        src.Revision("1.0.1"), src.Revision("1.0.2"), newer_only=True
    ) is True


def test_revision_is_stale_identity_axis_flags_new_commit_same_version():
    installed = src.Revision("1.0.0", "aaaa")
    available = src.Revision("1.0.0", "bbbb")
    assert src.revision_is_stale(installed, available, newer_only=False) is True


def test_prefers_newer_only_per_source_type(tmp_path: Path):
    local = _checkout(tmp_path, editable=True, marker=tmp_path / "m.json")
    assert src.prefers_newer_only(local) is False
    published = src.PublishedChannelSource(
        python_exe="/py",
        marker_path=tmp_path / "marker.json",
        cache_path=tmp_path / "cache.json",
        rollback_path=tmp_path / "rb.json",
    )
    assert src.prefers_newer_only(published) is True


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


def test_resolve_source_falls_back_to_published_when_no_local(tmp_path: Path, monkeypatch):
    # Override points nowhere and editable_origin disabled → no local source, so
    # resolution now falls back to the published channel (crit E seam filled).
    monkeypatch.setattr(src, "editable_origin", lambda: None)
    monkeypatch.setattr(src, "_walk_up_to_checkout", lambda start: None)
    source = src.resolve_source(
        env={"TIDE_SOURCE": str(tmp_path / "does-not-exist"), "TIDE_HOME": str(tmp_path)},
        python_exe="/py",
        marker_path=tmp_path / "m.json",
    )
    assert isinstance(source, src.PublishedChannelSource)
    assert source.name() == "published-channel"
    assert source.cache_path == tmp_path / "published-channel-cache.json"


# --- uv-tool installs (cand 08) ----------------------------------------------


def test_is_uv_tool_python_by_path():
    assert src.is_uv_tool_python(
        "/Users/x/.local/share/uv/tools/tide/bin/python", env={}
    ) is True
    assert src.is_uv_tool_python("/usr/bin/python3", env={}) is False


def test_is_uv_tool_python_by_env_override(tmp_path: Path):
    exe = tmp_path / "sandbox" / "tide" / "bin" / "python"
    assert src.is_uv_tool_python(
        str(exe), env={"UV_TOOL_DIR": str(tmp_path / "sandbox")}
    ) is True


def test_install_command_uv_tool_uses_uv_not_pip(tmp_path: Path):
    co = _checkout(tmp_path, editable=False, marker=tmp_path / "m.json")
    co.uv_tool = True
    cmd = co.install_command()
    assert cmd[:4] == ["uv", "tool", "install", "--force"]
    assert "--reinstall" in cmd
    assert cmd[-1] == str(tmp_path)
    assert "pip" not in cmd  # the sandbox has no pip — never route through it


def test_resolve_source_detects_uv_tool(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n', encoding="utf-8")
    source = src.resolve_source(
        env={"TIDE_SOURCE": str(tmp_path)},
        python_exe="/Users/x/.local/share/uv/tools/tide/bin/python",
        marker_path=tmp_path / "m.json",
    )
    assert source.uv_tool is True
    assert source.install_command()[0] == "uv"

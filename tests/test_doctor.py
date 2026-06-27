"""candidate 23 — `tide doctor` health/diagnostic checks (unit level).

Each check is a pure-ish function returning a :class:`tide.doctor.CheckResult`
(ok | warn | fail + a human line). We test the PASS case and the FAIL case for
every check, plus the aggregate report's exit-code contract (0 = no fail, nonzero
= at least one fail). The self-update channel probe is exercised offline-tolerant:
a network failure reports *unreachable* (warn), never crashes — and is NEVER run
implicitly (only the explicit doctor invocation calls it).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import pytest

from tide import doctor
from tide.hooks import install
from tide.update.source import LocalSourceCheckout, PublishedChannelSource


# --- fakes (mirror tests/test_update_published.py — never touch the network) --


class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _opener(payload: bytes):
    def _open(req, timeout=None):
        return _FakeResp(payload)

    return _open


def _boom_opener(req, timeout=None):
    raise OSError("network down")


def _feed(tag: str) -> bytes:
    return json.dumps({"tag_name": tag}).encode("utf-8")


def _published(tmp_path: Path, *, opener) -> PublishedChannelSource:
    return PublishedChannelSource(
        python_exe="/py",
        marker_path=tmp_path / "install-marker.json",
        cache_path=tmp_path / "cache.json",
        rollback_path=tmp_path / "rollback.json",
        repo="tide-tools/tide",
        opener=opener,
    )


# --- python check ----------------------------------------------------------


def test_check_python_passes_on_supported_interpreter():
    res = doctor.check_python(version_info=(3, 12, 1))
    assert res.status == doctor.STATUS_OK
    assert "3.12" in res.detail


def test_check_python_fails_on_too_old_interpreter():
    res = doctor.check_python(version_info=(3, 10, 9))
    assert res.status == doctor.STATUS_FAIL
    assert "3.10" in res.detail


# --- .tide structure check -------------------------------------------------


def test_check_structure_passes_on_intact_skeleton(tmp_project):
    res = doctor.check_structure(tmp_project)
    assert res.status == doctor.STATUS_OK


def test_check_structure_fails_when_no_tide_root():
    res = doctor.check_structure(None)
    assert res.status == doctor.STATUS_FAIL


def test_check_structure_fails_on_missing_subdir(tmp_project):
    # Remove an expected dir → structure is broken.
    import shutil

    shutil.rmtree(tmp_project / ".tide" / "arcs")
    res = doctor.check_structure(tmp_project)
    assert res.status == doctor.STATUS_FAIL
    assert "arcs" in res.detail


# --- canon readable check --------------------------------------------------


def test_check_canon_passes_on_readable_canon(tmp_project):
    res = doctor.check_canon(tmp_project)
    assert res.status == doctor.STATUS_OK


def test_check_canon_fails_when_canon_missing(tmp_project):
    (tmp_project / ".tide" / "canon" / "CANON.md").unlink()
    res = doctor.check_canon(tmp_project)
    assert res.status == doctor.STATUS_FAIL


def test_check_canon_warns_on_partial_sections(tmp_project):
    # A readable but skeletal canon missing the canonical sections → warn, not fail.
    (tmp_project / ".tide" / "canon" / "CANON.md").write_text(
        "# CANON.md — demo\n\n## What it is\n", encoding="utf-8"
    )
    res = doctor.check_canon(tmp_project)
    assert res.status == doctor.STATUS_WARN


# --- hooks wired check -----------------------------------------------------


def test_check_hooks_passes_when_wired(tmp_project):
    install.install_hooks(tmp_project)  # writes .claude/settings.json
    res = doctor.check_hooks(tmp_project)
    assert res.status == doctor.STATUS_OK


def test_check_hooks_warns_when_not_installed(tmp_project):
    res = doctor.check_hooks(tmp_project)
    assert res.status == doctor.STATUS_WARN


def test_check_hooks_fails_on_invalid_settings_json(tmp_project):
    settings = install.settings_path(tmp_project)
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text("{not json", encoding="utf-8")
    res = doctor.check_hooks(tmp_project)
    assert res.status == doctor.STATUS_FAIL


# --- install-marker valid check --------------------------------------------


def test_check_install_marker_passes_on_valid_marker(tmp_path):
    marker = tmp_path / "install-marker.json"
    marker.write_text(json.dumps({"version": "1.0.3", "commit": "abc"}), encoding="utf-8")
    res = doctor.check_install_marker(marker_path=marker)
    assert res.status == doctor.STATUS_OK
    assert "1.0.3" in res.detail


def test_check_install_marker_warns_when_absent(tmp_path):
    res = doctor.check_install_marker(marker_path=tmp_path / "nope.json")
    assert res.status == doctor.STATUS_WARN


def test_check_install_marker_fails_on_corrupt_marker(tmp_path):
    marker = tmp_path / "install-marker.json"
    marker.write_text("{garbage", encoding="utf-8")
    res = doctor.check_install_marker(marker_path=marker)
    assert res.status == doctor.STATUS_FAIL


def test_check_install_marker_fails_when_version_missing(tmp_path):
    marker = tmp_path / "install-marker.json"
    marker.write_text(json.dumps({"commit": "abc"}), encoding="utf-8")
    res = doctor.check_install_marker(marker_path=marker)
    assert res.status == doctor.STATUS_FAIL


# --- self-update channel reachable check -----------------------------------


def test_check_channel_ok_for_local_source(tmp_path):
    src = LocalSourceCheckout(
        source_dir=tmp_path,  # is_dir() → reachable, no network
        python_exe="/py",
        editable=True,
        marker_path=tmp_path / "marker.json",
    )
    res = doctor.check_channel(source=src)
    assert res.status == doctor.STATUS_OK
    assert "local-source" in res.detail


def test_check_channel_ok_for_reachable_published(tmp_path):
    src = _published(tmp_path, opener=_opener(_feed("v1.0.3")))
    res = doctor.check_channel(source=src)
    assert res.status == doctor.STATUS_OK
    assert "reachable" in res.detail.lower()


def test_check_channel_warns_when_published_unreachable(tmp_path):
    # Offline / network down must REPORT unreachable (warn), never crash.
    src = _published(tmp_path, opener=_boom_opener)
    res = doctor.check_channel(source=src)
    assert res.status == doctor.STATUS_WARN
    assert "unreachable" in res.detail.lower()


def test_check_channel_skips_network_when_disabled(tmp_path):
    # --no-network: report the configured source WITHOUT touching the channel.
    src = _published(tmp_path, opener=_boom_opener)  # would raise if probed
    res = doctor.check_channel(source=src, network=False)
    assert res.status == doctor.STATUS_OK
    assert "skipped" in res.detail.lower()


def test_check_channel_warns_when_no_source():
    res = doctor.check_channel(source=None)
    assert res.status == doctor.STATUS_WARN


# --- aggregate report + exit-code contract ---------------------------------


def test_run_doctor_all_ok_exits_zero(tmp_project, tmp_path):
    marker = tmp_path / "install-marker.json"
    marker.write_text(json.dumps({"version": "1.0.3"}), encoding="utf-8")
    install.install_hooks(tmp_project)
    report = doctor.run_doctor(
        tmp_project,
        marker_path=marker,
        source=None,         # no source → warn (not fail)
        network=False,
    )
    assert report.exit_code == 0
    assert report.ok is True
    # every check produced a line
    names = {r.name for r in report.results}
    assert {"python", "structure", "canon", "hooks", "install-marker", "channel"} <= names


def test_run_doctor_nonzero_when_a_check_fails(tmp_path):
    # No .tide root → structure + canon fail → nonzero exit (scriptable).
    report = doctor.run_doctor(
        None,
        marker_path=tmp_path / "absent.json",
        source=None,
        network=False,
    )
    assert report.exit_code != 0
    assert report.ok is False


def test_run_doctor_warn_does_not_trip_exit_code(tmp_project, tmp_path):
    # hooks not wired (warn) + no marker (warn) must NOT make doctor exit nonzero.
    report = doctor.run_doctor(
        tmp_project,
        marker_path=tmp_path / "absent.json",
        source=None,
        network=False,
    )
    assert report.exit_code == 0
    assert any(r.status == doctor.STATUS_WARN for r in report.results)

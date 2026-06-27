"""18-self-update — the REGRESSION GATE + the self_update flow + the session probe.

The load-bearing guarantee: a self-update is accepted ONLY when the gate
(verify --portable + the suite) is fully green; a red gate REFUSES and installs
nothing. These tests drive the flow with a fake source + scripted runner so no
real install / suite ever runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import pytest

from tide.update import core
from tide.update.source import Revision


# --- fakes ------------------------------------------------------------------


@dataclass
class FakeSource:
    """A VersionSource stand-in with controllable installed/available + a record sink."""

    installed_rev: Revision
    available_rev: Revision
    source_dir: Path = Path("/src")
    python_exe: str = "/py"
    recorded: List[Revision] = field(default_factory=list)

    def name(self) -> str:
        return "fake"

    def installed(self) -> Revision:
        return self.installed_rev

    def available(self) -> Revision:
        return self.available_rev

    def install_command(self) -> List[str]:
        return [self.python_exe, "-m", "pip", "install", "--upgrade", str(self.source_dir)]

    def record_install(self) -> Revision:
        # Faithful to a real marker: stamping makes installed() read the new
        # revision, so a subsequent staleness check reads "current".
        self.recorded.append(self.available_rev)
        self.installed_rev = self.available_rev
        return self.available_rev


class FakeRunner:
    """Scripts (rc, out) per command kind: verify / pytest / pip / version smoke."""

    def __init__(self, *, portable=(0, ""), suite=(0, ""), install=(0, ""), smoke=(0, "tide 0.2.0")):
        self.portable = portable
        self.suite = suite
        self.install = install
        self.smoke = smoke
        self.calls: List[List[str]] = []

    def __call__(self, cmd: List[str], cwd=None, env=None) -> Tuple[int, str]:
        self.calls.append(cmd)
        if "verify" in cmd:
            return self.portable
        if "pytest" in cmd:
            return self.suite
        if "pip" in cmd:
            return self.install
        if "version" in cmd:
            return self.smoke
        raise AssertionError("unexpected command: {0}".format(cmd))

    def ran(self, needle: str) -> bool:
        return any(needle in c for c in self.calls)


def _stale_source() -> FakeSource:
    return FakeSource(
        installed_rev=Revision("0.1.0", "oldaaaa"),
        available_rev=Revision("0.2.0", "newbbbb"),
    )


def _current_source() -> FakeSource:
    rev = Revision("0.2.0", "newbbbb")
    return FakeSource(installed_rev=rev, available_rev=rev)


# --- the gate ---------------------------------------------------------------


def test_gate_green_when_portable_and_suite_pass():
    runner = FakeRunner(portable=(0, ""), suite=(0, ""))
    gate = core.run_regression_gate(_stale_source(), runner=runner)
    assert gate.ok is True
    assert gate.portable_ok and gate.suite_ok and gate.suite_ran


def test_gate_red_when_portable_fails():
    runner = FakeRunner(portable=(1, "LEAK found"), suite=(0, ""))
    gate = core.run_regression_gate(_stale_source(), runner=runner)
    assert gate.ok is False
    assert gate.portable_ok is False


def test_gate_red_when_suite_fails():
    runner = FakeRunner(portable=(0, ""), suite=(1, "1 failed"))
    gate = core.run_regression_gate(_stale_source(), runner=runner)
    assert gate.ok is False
    assert gate.suite_ok is False


def test_gate_red_when_pytest_unavailable_is_failure_not_skip():
    # FAIL-LOUD: an unverifiable suite must NOT pass the gate.
    runner = FakeRunner(portable=(0, ""), suite=(1, "No module named pytest"))
    gate = core.run_regression_gate(_stale_source(), runner=runner)
    assert gate.ok is False
    assert any("CANNOT RUN" in m for m in gate.messages)


def test_gate_no_suite_waives_suite_but_keeps_portable():
    runner = FakeRunner(portable=(0, ""))
    gate = core.run_regression_gate(_stale_source(), run_suite=False, runner=runner)
    assert gate.ok is True
    assert gate.suite_ran is False
    assert not runner.ran("pytest")


def test_gate_on_source_without_checkout_is_red_not_crash():
    """Gating a source with no ``source_dir`` (a raw PublishedChannelSource) must
    NOT raise AttributeError — it returns RED with a clear message.

    A published source has no in-place checkout to run the suite against; the
    supported path materializes the artifact first (see self_update_published). A
    misuse here must refuse loudly (RED), never crash and never silently pass.
    """
    from tide.update.source import PublishedChannelSource

    raw = PublishedChannelSource(
        python_exe="/py",
        marker_path=Path("/m"),
        cache_path=Path("/c"),
        rollback_path=Path("/r"),
    )
    gate = core.run_regression_gate(raw, runner=FakeRunner())
    assert gate.ok is False
    assert gate.suite_ran is False
    assert any("no local source" in m for m in gate.messages)


# --- self_update flow -------------------------------------------------------


def test_self_update_current_is_noop():
    source = _current_source()
    runner = FakeRunner()
    res = core.self_update(source, runner=runner)
    assert res.accepted is True
    assert res.applied is False
    assert res.stale is False
    assert runner.calls == []  # no gate, no install
    assert source.recorded == []


def test_self_update_stale_green_gate_installs_and_stamps():
    source = _stale_source()
    runner = FakeRunner(portable=(0, ""), suite=(0, ""), install=(0, ""), smoke=(0, "tide 0.2.0"))
    res = core.self_update(source, runner=runner)
    assert res.accepted is True
    assert res.applied is True
    assert runner.ran("pip")
    assert source.recorded == [source.available_rev]  # marker stamped


def test_self_update_red_gate_refuses_and_installs_nothing():
    source = _stale_source()
    runner = FakeRunner(portable=(0, ""), suite=(1, "1 failed"))
    res = core.self_update(source, runner=runner)
    assert res.accepted is False
    assert res.applied is False
    assert not runner.ran("pip")  # the nightmare guard: nothing shipped
    assert source.recorded == []


def test_self_update_force_reinstalls_current():
    source = _current_source()
    runner = FakeRunner(portable=(0, ""), suite=(0, ""), install=(0, ""))
    res = core.self_update(source, force=True, runner=runner)
    assert res.applied is True
    assert runner.ran("pip")


def test_self_update_install_failure_is_not_accepted():
    source = _stale_source()
    runner = FakeRunner(portable=(0, ""), suite=(0, ""), install=(1, "pip blew up"))
    res = core.self_update(source, runner=runner)
    assert res.accepted is False
    assert res.applied is True  # we attempted the install
    assert source.recorded == []  # but never stamped


def test_self_update_smoke_failure_warns_and_stamps_to_stop_renudge():
    # The install PHYSICALLY happened (pip succeeded) — the freshly installed code
    # IS the new version, it just fails to smoke. Stamping the marker makes
    # installed()==available() so session-start stops nudging the user to reinstall
    # a version they already have (the old behaviour left a stale marker → a
    # perpetual re-nudge loop). accepted stays False + the loud WARNING preserves
    # the failure signal.
    source = _stale_source()
    runner = FakeRunner(portable=(0, ""), suite=(0, ""), install=(0, ""), smoke=(1, "ImportError"))
    res = core.self_update(source, runner=runner)
    assert res.accepted is False
    assert any("smoke FAILED" in m for m in res.messages)
    # marker stamped → the source no longer reads stale → no perpetual re-nudge
    assert source.recorded == [Revision("0.2.0", "newbbbb")]
    assert core.check_for_update(source).stale is False


def test_self_update_no_suite_path_skips_pytest():
    source = _stale_source()
    runner = FakeRunner(portable=(0, ""), install=(0, ""), smoke=(0, "tide 0.2.0"))
    res = core.self_update(source, run_suite=False, runner=runner)
    assert res.accepted is True
    assert not runner.ran("pytest")


# --- check_for_update + session_note ---------------------------------------


def test_check_for_update_flags_stale():
    status = core.check_for_update(_stale_source())
    assert status.stale is True
    assert status.source_name == "fake"


def test_session_note_present_when_stale():
    note = core.session_note(resolver=_stale_source)
    assert note is not None
    assert "update available" in note


def test_session_note_none_when_current():
    assert core.session_note(resolver=_current_source) is None


def test_session_note_none_when_no_source():
    assert core.session_note(resolver=lambda: None) is None


def test_session_note_swallows_errors():
    def boom():
        raise RuntimeError("metadata explosion")

    assert core.session_note(resolver=boom) is None

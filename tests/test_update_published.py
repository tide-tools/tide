"""crit E — the PublishedChannelSource (brew / pip-from-git gated self-update).

Covers the published source's installed/available with a 24h network-defensive
feed cache, channel detection, the release-artifact gate (download → extract →
gate → install), and the rollback affordance. The GitHub feed + tarball downloads
are mocked (a fake urlopen) so NO real network is ever touched.
"""

from __future__ import annotations

import json
import tarfile
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

import pytest

from tide.update import core
from tide.update import source as src
from tide.update.source import PublishedChannelSource, Revision


# --- fakes ------------------------------------------------------------------


class _FakeResp:
    """A minimal urlopen-style context manager yielding fixed bytes."""

    def __init__(self, payload: bytes):
        self._buf = payload

    def read(self, amt=None) -> bytes:
        # DRAIN the buffer (mirrors HTTPResponse.read(amt) → b"" at EOF) so the
        # bounded, looping reader terminates instead of re-reading forever.
        amt = len(self._buf) if amt is None else amt
        out, self._buf = self._buf[:amt], self._buf[amt:]
        return out

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _opener(payload: bytes, *, calls: Optional[List[str]] = None):
    """A urlopen stand-in returning *payload*; records requested URLs into *calls*."""

    def _open(req, timeout=None):
        if calls is not None:
            calls.append(getattr(req, "full_url", str(req)))
        return _FakeResp(payload)

    return _open


def _boom_opener(req, timeout=None):
    raise OSError("network down")


def _feed(tag: str) -> bytes:
    return json.dumps({"tag_name": tag}).encode("utf-8")


def _published(
    tmp_path: Path,
    *,
    homebrew: bool = False,
    opener=None,
) -> PublishedChannelSource:
    return PublishedChannelSource(
        python_exe="/py",
        marker_path=tmp_path / "install-marker.json",
        cache_path=tmp_path / "cache.json",
        rollback_path=tmp_path / "rollback.json",
        repo="tide-tools/tide",
        homebrew=homebrew,
        opener=opener if opener is not None else _opener(_feed("v1.0.1")),
    )


def _fresh_cache(path: Path, tag: str) -> None:
    path.write_text(json.dumps({"tag": tag, "fetched_at": time.time()}), encoding="utf-8")


def _stale_cache(path: Path, tag: str) -> None:
    old = time.time() - (src.CACHE_TTL_S + 60)
    path.write_text(json.dumps({"tag": tag, "fetched_at": old}), encoding="utf-8")


class FakeRunner:
    """Scripts (rc, out) per command kind: verify / pytest / install / version smoke."""

    def __init__(self, *, portable=(0, ""), suite=(0, ""), install=(0, ""), smoke=(0, "tide 1.0.1")):
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
        if "pip" in cmd or "brew" in cmd:
            return self.install
        if "version" in cmd:
            return self.smoke
        raise AssertionError("unexpected command: {0}".format(cmd))

    def ran(self, needle: str) -> bool:
        return any(needle in c for c in self.calls)


# --- installed() / available() / staleness ----------------------------------


def test_installed_prefers_marker_version_only(tmp_path: Path):
    s = _published(tmp_path)
    src.write_marker(s.marker_path, Revision("0.9.0", "shouldignore"), tmp_path)
    inst = s.installed()
    assert inst.version == "0.9.0"
    # published installs are keyed on version only — a commit never enters identity
    assert inst.commit is None
    assert inst.identity == "0.9.0"


def test_available_uses_fresh_cache_without_network(tmp_path: Path):
    _fresh_cache(tmp_path / "cache.json", "v1.0.1")
    s = _published(tmp_path, opener=_boom_opener)  # would raise if network touched
    assert s.available().version == "1.0.1"


def test_available_fetches_and_caches_on_cold_cache(tmp_path: Path):
    calls: List[str] = []
    s = _published(tmp_path, opener=_opener(_feed("v1.0.1"), calls=calls))
    assert s.available().version == "1.0.1"
    assert calls  # the feed was queried
    cached = json.loads((tmp_path / "cache.json").read_text())
    assert cached["tag"] == "v1.0.1"
    assert isinstance(cached["fetched_at"], (int, float))


def test_available_refetches_when_cache_expired(tmp_path: Path):
    _stale_cache(tmp_path / "cache.json", "v0.0.1")  # > 24h old
    calls: List[str] = []
    s = _published(tmp_path, opener=_opener(_feed("v1.0.1"), calls=calls))
    assert s.available().version == "1.0.1"  # refetched, not the stale tag
    assert calls


def test_available_swallows_network_error_as_not_stale(tmp_path: Path):
    # Cold cache + offline → available == installed (no crash, no false nudge).
    src.write_marker((tmp_path / "install-marker.json"), Revision("3.3.3"), tmp_path)
    s = _published(tmp_path, opener=_boom_opener)
    assert s.available().identity == s.installed().identity
    assert src.is_stale(s) is False


def test_available_falls_back_to_stale_cache_when_fetch_fails(tmp_path: Path):
    _stale_cache(tmp_path / "cache.json", "v1.0.1")
    s = _published(tmp_path, opener=_boom_opener)  # refetch fails → reuse stale cache
    assert s.available().version == "1.0.1"


def test_stale_when_marker_lags_published(tmp_path: Path):
    src.write_marker((tmp_path / "install-marker.json"), Revision("0.1.0"), tmp_path)
    _fresh_cache(tmp_path / "cache.json", "v1.0.1")
    s = _published(tmp_path)
    assert src.is_stale(s) is True


def test_not_stale_when_installed_ahead_of_published(tmp_path: Path):
    # installed 1.0.2, channel-latest 1.0.1 → AHEAD, not stale (no downgrade nudge).
    src.write_marker((tmp_path / "install-marker.json"), Revision("1.0.2"), tmp_path)
    _fresh_cache(tmp_path / "cache.json", "v1.0.1")
    s = _published(tmp_path)
    assert src.is_stale(s) is False
    assert core.check_for_update(s).stale is False


def test_not_stale_when_installed_equals_published(tmp_path: Path):
    src.write_marker((tmp_path / "install-marker.json"), Revision("1.0.1"), tmp_path)
    _fresh_cache(tmp_path / "cache.json", "v1.0.1")
    s = _published(tmp_path)
    assert src.is_stale(s) is False


def test_session_note_silent_when_installed_ahead_of_published(tmp_path: Path):
    # the SessionStart surface must NOT nudge a downgrade.
    src.write_marker((tmp_path / "install-marker.json"), Revision("1.0.2"), tmp_path)
    _fresh_cache(tmp_path / "cache.json", "v1.0.1")
    s = _published(tmp_path, opener=_boom_opener)
    assert core.session_note(resolver=lambda: s) is None


def test_self_update_published_ahead_of_channel_is_noop_not_downgrade(tmp_path: Path):
    # installed ahead of the channel → no fetch, no gate, no install (no downgrade).
    src.write_marker((tmp_path / "install-marker.json"), Revision("1.0.2"), tmp_path)
    _fresh_cache(tmp_path / "cache.json", "v1.0.1")
    s = _published(tmp_path, opener=_boom_opener)
    runner = FakeRunner()
    res = core.self_update_published(s, runner=runner, workdir_factory=_wd_factory(tmp_path))
    assert res.accepted is True
    assert res.applied is False
    assert res.stale is False
    assert runner.calls == []  # never fetched/gated/installed an older release
    assert src.read_marker(s.marker_path)["version"] == "1.0.2"  # unchanged


# --- repo discovery (no hardcoded instance-token in shipped source) ---------


class _FakeMeta:
    def __init__(self, urls, home=None):
        self._urls = urls
        self._home = home

    def get_all(self, key):
        return self._urls if key == "Project-URL" else None

    def get(self, key):
        return self._home if key == "Home-page" else None


def test_discover_repo_from_project_url(monkeypatch):
    import importlib.metadata as md

    monkeypatch.setattr(
        md, "metadata",
        lambda name: _FakeMeta(["Source, https://github.com/tide-cli/tide"]),
    )
    assert src.discover_repo() == "tide-cli/tide"


def test_discover_repo_strips_git_suffix(monkeypatch):
    import importlib.metadata as md

    monkeypatch.setattr(
        md, "metadata",
        lambda name: _FakeMeta([], home="https://github.com/acme/widget.git"),
    )
    assert src.discover_repo() == "acme/widget"


def test_discover_repo_falls_back_when_no_url(monkeypatch):
    import importlib.metadata as md

    monkeypatch.setattr(md, "metadata", lambda name: _FakeMeta([], home=None))
    assert src.discover_repo() == src._FALLBACK_REPO


# --- channel detection / install_command ------------------------------------


def test_install_command_pip_from_git_when_not_brew(tmp_path: Path):
    _fresh_cache(tmp_path / "cache.json", "v1.0.1")
    s = _published(tmp_path, homebrew=False)
    cmd = s.install_command()
    assert cmd[:5] == ["/py", "-m", "pip", "install", "--upgrade"]
    assert cmd[-1] == "git+https://github.com/tide-tools/tide@v1.0.1"


def test_install_command_brew_when_homebrew(tmp_path: Path):
    s = _published(tmp_path, homebrew=True)
    assert s.install_command() == ["brew", "upgrade", "tide-tools/tide/tide"]


def test_detect_homebrew_from_cellar_path():
    assert src._detect_homebrew("/opt/homebrew/Cellar/tide/1.0.1/libexec/bin/python") is True


def test_detect_homebrew_false_for_plain_python():
    assert src._detect_homebrew("/usr/bin/python3.12") is False


def test_rollback_command_pins_installed_version(tmp_path: Path):
    src.write_marker((tmp_path / "install-marker.json"), Revision("0.1.0"), tmp_path)
    s = _published(tmp_path)
    cmd = s.rollback_command()
    assert cmd[-1] == "git+https://github.com/tide-tools/tide@v0.1.0"


def test_record_install_stamps_version_only(tmp_path: Path):
    _fresh_cache(tmp_path / "cache.json", "v1.0.1")
    s = _published(tmp_path)
    rev = s.record_install()
    assert rev.version == "1.0.1"
    marker = src.read_marker(s.marker_path)
    assert marker["version"] == "1.0.1"
    assert marker["commit"] is None  # version-keyed, no commit


# --- release artifact (download + extract) ----------------------------------


def _make_release_tarball(build_root: Path, version: str = "1.0.1") -> bytes:
    top = build_root / "tide-{0}".format(version)
    (top / "src").mkdir(parents=True)
    (top / "pyproject.toml").write_text(
        '[project]\nversion = "{0}"\n'.format(version), encoding="utf-8"
    )
    (top / "tests").mkdir()
    tarball = build_root / "rel.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(top, arcname="tide-{0}".format(version))
    return tarball.read_bytes()


def test_tarball_url():
    s = PublishedChannelSource(
        python_exe="/py", marker_path=Path("/m"), cache_path=Path("/c"),
        rollback_path=Path("/r"), repo="tide-tools/tide",
    )
    assert s.tarball_url("v1.0.1") == (
        "https://github.com/tide-tools/tide/archive/refs/tags/v1.0.1.tar.gz"
    )


def test_safe_extract_returns_source_root(tmp_path: Path):
    payload = _make_release_tarball(tmp_path / "build")
    tb = tmp_path / "dl.tar.gz"
    tb.write_bytes(payload)
    dest = tmp_path / "out"
    dest.mkdir()
    root = src.safe_extract(tb, dest)
    assert (root / "pyproject.toml").is_file()


class _DrainResp:
    """A read()-able that DRAINS a buffer, honouring amt and partial-read chunking.

    Mirrors HTTPResponse.read(n): returns up to n bytes per call (and never more
    than *chunk* when set — to simulate partial TCP reads), b"" at EOF."""

    def __init__(self, payload: bytes, *, chunk: Optional[int] = None):
        self._buf = payload
        self._chunk = chunk

    def read(self, amt=None) -> bytes:
        amt = len(self._buf) if amt is None else amt
        if self._chunk is not None:
            amt = min(amt, self._chunk)
        out, self._buf = self._buf[:amt], self._buf[amt:]
        return out

    def __enter__(self) -> "_DrainResp":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def test_read_bounded_rejects_oversized_body():
    """The network read is BOUNDED: a body larger than the cap is refused, not
    truncated — a defence against a decompression-bomb / runaway download."""
    with pytest.raises(RuntimeError, match="exceeds"):
        src._read_bounded(_DrainResp(b"x" * 16), cap=8)


def test_read_bounded_returns_body_within_cap():
    assert src._read_bounded(_DrainResp(b"abc"), cap=1024) == b"abc"


def test_read_bounded_rejects_oversized_body_delivered_in_chunks():
    # HTTPResponse.read(n) may return FEWER than n bytes (partial TCP) — an
    # oversized body delivered in small chunks must STILL be rejected, so the
    # bound loops until cap+1 or EOF rather than trusting a single read.
    with pytest.raises(RuntimeError, match="exceeds"):
        src._read_bounded(_DrainResp(b"y" * 40, chunk=4), cap=8)


def test_fetch_latest_tag_bounds_oversized_feed(tmp_path: Path, monkeypatch):
    """The releases-feed read is BOUNDED too: a MITM/compromised endpoint that
    streams a giant body must NOT be read whole (OOM) — it is bounded and the
    fetch degrades to None, never crashes the process. (Cap monkeypatched tiny.)"""
    monkeypatch.setattr(src, "MAX_FEED_BYTES", 16)
    served = {"n": 0}
    body = b'{"tag_name": "v9.9.9"}' + b" " * 4000

    class _Resp(_DrainResp):
        def read(self, amt=None) -> bytes:
            out = super().read(amt)
            served["n"] += len(out)
            return out

    s = _published(tmp_path, opener=lambda req, timeout=None: _Resp(body, chunk=8))
    assert s._fetch_latest_tag() is None  # oversized feed bounded → swallowed, no crash
    assert served["n"] <= 16 + 1  # never read the whole multi-KB body


def test_safe_extract_rejects_total_size_bomb(tmp_path: Path, monkeypatch):
    """A tarball whose DECLARED uncompressed size exceeds the total cap is refused.

    We sum each member's declared size and reject BEFORE extracting, so a
    decompression bomb cannot explode on disk. (Cap monkeypatched tiny so a normal
    tarball trips it — no need to craft a real multi-hundred-MB archive.)"""
    monkeypatch.setattr(src, "MAX_EXTRACT_TOTAL_BYTES", 1)
    payload = _make_release_tarball(tmp_path / "build")
    tb = tmp_path / "dl.tar.gz"
    tb.write_bytes(payload)
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(RuntimeError, match="total"):
        src.safe_extract(tb, dest)


def test_safe_extract_rejects_oversized_member(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(src, "MAX_EXTRACT_MEMBER_BYTES", 1)
    payload = _make_release_tarball(tmp_path / "build")
    tb = tmp_path / "dl.tar.gz"
    tb.write_bytes(payload)
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(RuntimeError, match="member"):
        src.safe_extract(tb, dest)


def test_safe_extract_rejects_path_traversal(tmp_path: Path):
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tf:
        payload = tmp_path / "p"
        payload.write_text("x", encoding="utf-8")
        tf.add(payload, arcname="../escape.txt")
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(RuntimeError):
        src.safe_extract(evil, dest)


def test_materialize_source_downloads_and_extracts(tmp_path: Path):
    _fresh_cache(tmp_path / "cache.json", "v1.0.1")
    payload = _make_release_tarball(tmp_path / "build")
    calls: List[str] = []
    s = _published(tmp_path, opener=_opener(payload, calls=calls))
    workdir = tmp_path / "wd"
    workdir.mkdir()
    root = s.materialize_source(workdir)
    assert (root / "pyproject.toml").is_file()
    assert any("archive/refs/tags/v1.0.1.tar.gz" in u for u in calls)


# --- self_update_published flow ---------------------------------------------


def _stale_published(tmp_path: Path, *, homebrew: bool = False) -> PublishedChannelSource:
    src.write_marker((tmp_path / "install-marker.json"), Revision("0.1.0"), tmp_path)
    _fresh_cache(tmp_path / "cache.json", "v1.0.1")
    payload = _make_release_tarball(tmp_path / "build")
    return _published(tmp_path, homebrew=homebrew, opener=_opener(payload))


def _wd_factory(tmp_path: Path):
    return lambda: tempfile.mkdtemp(dir=str(tmp_path))


def test_published_update_green_gate_installs_stamps_and_records_rollback(tmp_path: Path):
    s = _stale_published(tmp_path)
    runner = FakeRunner(portable=(0, ""), suite=(0, ""), install=(0, ""), smoke=(0, "tide 1.0.1"))
    res = core.self_update_published(s, runner=runner, workdir_factory=_wd_factory(tmp_path))
    assert res.accepted is True
    assert res.applied is True
    assert runner.ran("pip")  # the channel install ran
    # marker stamped to the new version, rollback marker written (pinning the old)
    assert src.read_marker(s.marker_path)["version"] == "1.0.1"
    rollback = src.read_rollback(s.rollback_path)
    assert rollback["version"] == "0.1.0"
    assert rollback["command"][-1] == "git+https://github.com/tide-tools/tide@v0.1.0"


def test_published_update_brew_channel_uses_brew_install(tmp_path: Path):
    s = _stale_published(tmp_path, homebrew=True)
    runner = FakeRunner()
    res = core.self_update_published(s, runner=runner, workdir_factory=_wd_factory(tmp_path))
    assert res.accepted is True
    assert runner.ran("brew")
    assert not runner.ran("pip")


def test_published_update_red_gate_refuses_and_installs_nothing(tmp_path: Path):
    s = _stale_published(tmp_path)
    runner = FakeRunner(portable=(0, ""), suite=(1, "1 failed"))
    res = core.self_update_published(s, runner=runner, workdir_factory=_wd_factory(tmp_path))
    assert res.accepted is False
    assert res.applied is False
    assert not runner.ran("pip")
    assert not runner.ran("brew")
    # no rollback marker either — we never reached the install step
    assert src.read_rollback(s.rollback_path) is None


def test_published_update_fetch_failure_is_refused(tmp_path: Path):
    src.write_marker((tmp_path / "install-marker.json"), Revision("0.1.0"), tmp_path)
    _fresh_cache(tmp_path / "cache.json", "v1.0.1")
    s = _published(tmp_path, opener=_boom_opener)  # download blows up
    runner = FakeRunner()
    res = core.self_update_published(s, runner=runner, workdir_factory=_wd_factory(tmp_path))
    assert res.accepted is False
    assert res.applied is False
    assert any("could not fetch" in m for m in res.messages)


def test_published_update_noop_when_current(tmp_path: Path):
    rev = "1.0.1"
    src.write_marker((tmp_path / "install-marker.json"), Revision(rev), tmp_path)
    _fresh_cache(tmp_path / "cache.json", "v" + rev)
    s = _published(tmp_path)
    runner = FakeRunner()
    res = core.self_update_published(s, runner=runner, workdir_factory=_wd_factory(tmp_path))
    assert res.accepted is True
    assert res.applied is False
    assert runner.calls == []  # no fetch, no gate, no install


def test_published_update_cleans_up_workdir(tmp_path: Path):
    s = _stale_published(tmp_path)
    runner = FakeRunner()
    created: List[str] = []

    def factory() -> str:
        d = tempfile.mkdtemp(dir=str(tmp_path))
        created.append(d)
        return d

    core.self_update_published(s, runner=runner, workdir_factory=factory)
    assert created and not Path(created[0]).exists()  # temp checkout removed


def test_published_update_smoke_failure_stamps_to_stop_renudge(tmp_path: Path):
    # pip succeeded (the new version IS installed); only the smoke failed. The
    # marker advances to the new version so installed()==available() and the user
    # is no longer perpetually re-nudged to reinstall a version they already have —
    # while accepted stays False and the loud WARNING keeps the failure visible.
    s = _stale_published(tmp_path)
    runner = FakeRunner(portable=(0, ""), suite=(0, ""), install=(0, ""), smoke=(1, "ImportError"))
    res = core.self_update_published(s, runner=runner, workdir_factory=_wd_factory(tmp_path))
    assert res.accepted is False
    assert src.read_marker(s.marker_path)["version"] == "1.0.1"  # advanced → no re-nudge
    assert core.check_for_update(s).stale is False
    assert any("smoke FAILED" in m for m in res.messages)


def test_smoke_failure_writes_persistent_broken_marker(tmp_path: Path):
    # A smoke-failed install must stay LOUD: stamping the version marker silences
    # the stale-version nudge, so a SEPARATE broken-install marker keeps the
    # failure visible. session_note surfaces a DISTINCT, persistent warning in a
    # FRESH subsequent session — not just the one-time WARNING line.
    s = _stale_published(tmp_path)
    runner = FakeRunner(portable=(0, ""), suite=(0, ""), install=(0, ""), smoke=(1, "ImportError"))
    core.self_update_published(s, runner=runner, workdir_factory=_wd_factory(tmp_path))

    broken = tmp_path / "broken-install-marker.json"
    assert src.read_broken(broken) is not None
    assert src.read_broken(broken)["version"] == "1.0.1"

    # a brand-new session (fresh resolve) — the stale nudge is gone (marker
    # advanced) but the broken-install warning persists
    note = core.session_note(resolver=lambda: _published(
        tmp_path, opener=_opener(_make_release_tarball(tmp_path / "b2"))
    ))
    assert note is not None
    assert "BROKEN" in note


def test_broken_marker_cleared_on_successful_update(tmp_path: Path):
    s = _stale_published(tmp_path)
    core.self_update_published(
        s,
        runner=FakeRunner(portable=(0, ""), suite=(0, ""), install=(0, ""), smoke=(1, "x")),
        workdir_factory=_wd_factory(tmp_path),
    )
    broken = tmp_path / "broken-install-marker.json"
    assert src.read_broken(broken) is not None

    # a subsequent forced reinstall whose smoke PASSES clears the broken marker
    res = core.self_update_published(
        s,
        force=True,
        runner=FakeRunner(portable=(0, ""), suite=(0, ""), install=(0, ""), smoke=(0, "tide 1.0.1")),
        workdir_factory=_wd_factory(tmp_path),
    )
    assert res.accepted is True
    assert src.read_broken(broken) is None


# --- rollback ---------------------------------------------------------------


def test_rollback_replays_pinned_command_and_smokes(tmp_path: Path):
    marker = tmp_path / "rollback.json"
    src.write_rollback(
        marker, "0.1.0",
        ["/py", "-m", "pip", "install", "--upgrade", "git+https://github.com/tide-tools/tide@v0.1.0"],
    )
    runner = FakeRunner(install=(0, ""), smoke=(0, "tide 0.1.0"))
    res = core.rollback(marker, runner=runner)
    assert res.ok is True
    assert res.target == "0.1.0"
    assert runner.ran("pip")


def test_rollback_clears_broken_install_marker(tmp_path: Path):
    # A successful rollback recovers a working install → the broken-install marker
    # must be cleared so session_note stops warning.
    marker = tmp_path / "rollback.json"
    src.write_rollback(
        marker, "0.1.0",
        ["/py", "-m", "pip", "install", "--upgrade", "git+https://github.com/tide-tools/tide@v0.1.0"],
    )
    broken = tmp_path / "broken-install-marker.json"
    src.write_broken(broken, "1.0.1", "post-install smoke check failed")
    runner = FakeRunner(install=(0, ""), smoke=(0, "tide 0.1.0"))
    res = core.rollback(marker, runner=runner)
    assert res.ok is True
    assert src.read_broken(broken) is None


def test_rollback_no_marker_refuses(tmp_path: Path):
    res = core.rollback(tmp_path / "nope.json", runner=FakeRunner())
    assert res.ok is False
    assert res.target is None


def test_rollback_install_failure_is_not_ok(tmp_path: Path):
    marker = tmp_path / "rollback.json"
    src.write_rollback(marker, "0.1.0", ["/py", "-m", "pip", "install", "git+x@v0.1.0"])
    runner = FakeRunner(install=(1, "pip blew up"))
    res = core.rollback(marker, runner=runner)
    assert res.ok is False
    assert any("FAILED" in m for m in res.messages)


# --- session_note through the published source ------------------------------


def test_session_note_surfaces_through_published_source(tmp_path: Path):
    src.write_marker((tmp_path / "install-marker.json"), Revision("0.1.0"), tmp_path)
    _fresh_cache(tmp_path / "cache.json", "v1.0.1")
    s = _published(tmp_path, opener=_boom_opener)  # cache fresh → no network at session start
    note = core.session_note(resolver=lambda: s)
    assert note is not None
    assert "update available" in note


def test_session_note_silent_when_published_current(tmp_path: Path):
    src.write_marker((tmp_path / "install-marker.json"), Revision("1.0.1"), tmp_path)
    _fresh_cache(tmp_path / "cache.json", "v1.0.1")
    s = _published(tmp_path, opener=_boom_opener)
    assert core.session_note(resolver=lambda: s) is None

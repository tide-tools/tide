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
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

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
        repo="socaseinpoint/tide",
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
    assert cmd[-1] == "git+https://github.com/socaseinpoint/tide@v1.0.1"


def test_install_command_brew_when_homebrew(tmp_path: Path):
    s = _published(tmp_path, homebrew=True)
    assert s.install_command() == ["brew", "upgrade", "socaseinpoint/tide/tide"]


def test_detect_homebrew_from_cellar_path():
    assert src._detect_homebrew("/opt/homebrew/Cellar/tide/1.0.1/libexec/bin/python") is True


def test_detect_homebrew_false_for_plain_python():
    assert src._detect_homebrew("/usr/bin/python3.12") is False


def test_rollback_command_pins_installed_version(tmp_path: Path):
    src.write_marker((tmp_path / "install-marker.json"), Revision("0.1.0"), tmp_path)
    s = _published(tmp_path)
    cmd = s.rollback_command()
    assert cmd[-1] == "git+https://github.com/socaseinpoint/tide@v0.1.0"


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
        rollback_path=Path("/r"), repo="socaseinpoint/tide",
    )
    assert s.tarball_url("v1.0.1") == (
        "https://github.com/socaseinpoint/tide/archive/refs/tags/v1.0.1.tar.gz"
    )


def test_safe_extract_returns_source_root(tmp_path: Path):
    payload = _make_release_tarball(tmp_path / "build")
    tb = tmp_path / "dl.tar.gz"
    tb.write_bytes(payload)
    dest = tmp_path / "out"
    dest.mkdir()
    root = src.safe_extract(tb, dest)
    assert (root / "pyproject.toml").is_file()


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
    assert rollback["command"][-1] == "git+https://github.com/socaseinpoint/tide@v0.1.0"


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


def test_published_update_smoke_failure_does_not_stamp(tmp_path: Path):
    s = _stale_published(tmp_path)
    runner = FakeRunner(portable=(0, ""), suite=(0, ""), install=(0, ""), smoke=(1, "ImportError"))
    res = core.self_update_published(s, runner=runner, workdir_factory=_wd_factory(tmp_path))
    assert res.accepted is False
    assert src.read_marker(s.marker_path)["version"] == "0.1.0"  # NOT advanced
    assert any("smoke FAILED" in m for m in res.messages)


# --- rollback ---------------------------------------------------------------


def test_rollback_replays_pinned_command_and_smokes(tmp_path: Path):
    marker = tmp_path / "rollback.json"
    src.write_rollback(
        marker, "0.1.0",
        ["/py", "-m", "pip", "install", "--upgrade", "git+https://github.com/socaseinpoint/tide@v0.1.0"],
    )
    runner = FakeRunner(install=(0, ""), smoke=(0, "tide 0.1.0"))
    res = core.rollback(marker, runner=runner)
    assert res.ok is True
    assert res.target == "0.1.0"
    assert runner.ran("pip")


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

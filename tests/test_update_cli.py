"""18-self-update — the ``tide self-update`` CLI surface + SessionStart wiring.

The handler is argparse + printing; we stub ``resolve_source`` so no real
install ever runs, and assert the exit-code contract (--check tri-state) + that
SessionStart SURFACES an available update (never applies it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import pytest

from tide import cli
from tide.update import commands, core
from tide.update.source import Revision


@dataclass
class FakeSource:
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
        self.recorded.append(self.available_rev)
        return self.available_rev


def _stale():
    return FakeSource(Revision("0.1.0", "old"), Revision("0.2.0", "new"))


def _current():
    rev = Revision("0.2.0", "new")
    return FakeSource(rev, rev)


# --- --check tri-state exit code -------------------------------------------


def test_check_exit_0_when_current(monkeypatch, capsys):
    monkeypatch.setattr(commands, "resolve_source", lambda: _current())
    rc = cli.main(["self-update", "--check"])
    assert rc == 0
    assert "current" in capsys.readouterr().out


def test_check_exit_1_when_stale(monkeypatch, capsys):
    monkeypatch.setattr(commands, "resolve_source", lambda: _stale())
    rc = cli.main(["self-update", "--check"])
    assert rc == 1
    assert "UPDATE AVAILABLE" in capsys.readouterr().out


def test_exit_2_when_no_source(monkeypatch, capsys):
    monkeypatch.setattr(commands, "resolve_source", lambda: None)
    rc = cli.main(["self-update", "--check"])
    assert rc == 2
    assert "no local source" in capsys.readouterr().out


# --- --dry-run --------------------------------------------------------------


def test_dry_run_shows_command_and_acts_not(monkeypatch, capsys):
    source = _stale()
    monkeypatch.setattr(commands, "resolve_source", lambda: source)
    rc = cli.main(["self-update", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "would run" in out
    assert "pip" in out
    assert source.recorded == []  # nothing applied


# --- default flow (gate stubbed via runner-less self_update) ----------------


def test_default_flow_accepts_on_green_gate(monkeypatch, capsys):
    source = _stale()
    monkeypatch.setattr(commands, "resolve_source", lambda: source)

    def fake_self_update(src, **kw):
        return core.SelfUpdateResult(
            source_name=src.name(),
            installed=src.installed(),
            available=src.available(),
            stale=True,
            accepted=True,
            applied=True,
            messages=["accepted — installed + stamped"],
        )

    monkeypatch.setattr(core, "self_update", fake_self_update)
    rc = cli.main(["self-update"])
    assert rc == 0
    assert "accepted" in capsys.readouterr().out


def test_default_flow_returns_1_on_refused(monkeypatch, capsys):
    source = _stale()
    monkeypatch.setattr(commands, "resolve_source", lambda: source)

    def refused(src, **kw):
        return core.SelfUpdateResult(
            source_name=src.name(),
            installed=src.installed(),
            available=src.available(),
            stale=True,
            accepted=False,
            applied=False,
            messages=["REFUSED — gate is red"],
        )

    monkeypatch.setattr(core, "self_update", refused)
    rc = cli.main(["self-update"])
    assert rc == 1
    assert "REFUSED" in capsys.readouterr().out


# --- SessionStart surfaces an available update (never applies) --------------


def test_session_start_surfaces_update(tmp_project, monkeypatch, capsys):
    from tide.hooks import session_start

    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("TIDE_ROLE", "worker")
    monkeypatch.setattr(
        session_start, "_update_note", lambda: "  ↑ tide update available: 0.1.0 → 0.2.0"
    )
    rc = cli.main(["hook", "session-start"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "UPDATE" in out
    assert "update available" in out


def test_session_start_silent_when_no_update(tmp_project, monkeypatch, capsys):
    from tide.hooks import session_start

    monkeypatch.chdir(tmp_project)
    monkeypatch.setenv("TIDE_ROLE", "worker")
    monkeypatch.setattr(session_start, "_update_note", lambda: None)
    rc = cli.main(["hook", "session-start"])
    assert rc == 0
    assert "UPDATE" not in capsys.readouterr().out

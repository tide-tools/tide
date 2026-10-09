"""Shared pytest fixtures for the tide suite.

``tmp_project`` builds a minimal per-project ``.tide/`` skeleton in a tmp dir,
matching the blueprint ``tide_dir_format``. Later units (arc/canon/contract)
build their integration + e2e tests on top of it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

# A leftover ``<…>`` scaffold span (mirrors tide.placeholders._ANGLE) — used by
# ``strip_placeholders`` below to fill in a passport before close, the test-side
# equivalent of a worker finishing the doc so the F5 close guard passes.
_PLACEHOLDER_RE = re.compile(r"<[^<>\n]+>")


def strip_placeholders(*paths: Path) -> None:
    """Fill in F5 scaffold placeholders in docs so the close placeholder-guard passes.

    Drops the ``# supersedes:`` hint comment and replaces every ``<…>`` angle-bracket
    placeholder with filler text — exactly what a worker does before close. Missing
    files are skipped. Operates on arc.md / goal docs / contract.md alike.
    """
    for path in paths:
        p = Path(path)
        if not p.is_file():
            continue
        lines = [
            ln
            for ln in p.read_text(encoding="utf-8").splitlines()
            if not ln.lstrip().startswith("# supersedes:")
        ]
        text = _PLACEHOLDER_RE.sub("filled in", "\n".join(lines))
        p.write_text(text + "\n", encoding="utf-8")


CANON_MD_TEMPLATE = """# CANON.md — {name}

## What it is

## State & components

## Interfaces / how used

## Canon journal
"""


def build_tide_skeleton(root: Path, *, name: str, control_home: bool = False) -> Path:
    """Create a ``.tide/`` skeleton under *root* and return the .tide path.

    Layout (per blueprint tide_dir_format):
      .tide/canon/CANON.md    — living-IS doc
      .tide/canon/config      — lang=en
      .tide/arcs/             — work stream (NN-<slug>/ entries land here)
      .tide/arcs/candidates/  — separately-numbered candidate backlog
      .tide/state/strictness  — per-project dial (default 'strict')

    A control-home additionally gets a top-level roster.md ('name | path' lines).
    """
    tide = root / ".tide"
    canon = tide / "canon"
    arcs = tide / "arcs"
    state = tide / "state"
    for d in (canon, arcs, arcs / "candidates", state):
        d.mkdir(parents=True, exist_ok=True)

    (canon / "CANON.md").write_text(CANON_MD_TEMPLATE.format(name=name), encoding="utf-8")
    (canon / "config").write_text("lang=en\n", encoding="utf-8")
    (state / "strictness").write_text("strict\n", encoding="utf-8")

    if control_home:
        (root / "roster.md").write_text("# tide roster\n", encoding="utf-8")

    return tide


@pytest.fixture(autouse=True)
def _isolate_real_tide_home(monkeypatch, tmp_path):
    """Never let a test touch the developer's REAL ``$TIDE_HOME`` / ``~/.claude``.

    ``paths.control_home()`` PREFERS ``$TIDE_HOME`` over the cwd climb, so a test
    that only ``chdir``'s into a tmp control-home would otherwise read/write the
    real roster — leaking junk entries (``demo``/``alpha``/``bb`` …) into it on
    every run. Unset it so resolution falls back to the cwd climb (the test's own
    tmp home). Tests that need a specific home ``setenv`` it themselves (their
    monkeypatch runs after this autouse one, so it wins).

    Also point ``CLAUDE_CONFIG_DIR`` at a per-test tmp dir so the resume-gate
    (``menu._claude_conversation_exists``) reads an ISOLATED store — by default no
    conversation exists (→ fresh launch); a test that wants resume creates the
    ``projects/<encoded>/<id>.jsonl`` file under this dir itself.
    """
    monkeypatch.delenv("TIDE_HOME", raising=False)
    # A test must never inherit the developer's LIVE claude session id — else
    # `tide arc new-session` would stamp it into fresh test sessions (cand 93-board-spark),
    # polluting the "a fresh session has no head" assumption across the suite.
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    # Same reason, the other half of the pair (cand 144): every tide command now binds
    # `(sid, handle)` from its own env (`sessions.self_register`), so a suite that
    # inherited the developer's pane handle would write the runner's own terminal into
    # whatever registry the test points at. Drop both — a test has no terminal.
    monkeypatch.delenv("ORCA_TERMINAL_HANDLE", raising=False)
    # Agent Orchestrator: the runner's own AO session id / data dir must not leak in,
    # and the developer's GLOBAL terminal-adapter pin (~/.claude/settings.json) or a
    # $TIDE_ADAPTER must not reroute a test's launches into a real app.
    monkeypatch.delenv("AO_SESSION_ID", raising=False)
    monkeypatch.delenv("AO_DATA_DIR", raising=False)
    monkeypatch.delenv("TIDE_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("TIDE_ADAPTER", raising=False)
    # No test may reach the REAL desktop AO (install-hooks now checks the AO
    # project config, SOC-101): a missing binary = "AO not installed". The
    # fake_ao fixture sets its own TIDE_AO_BIN after this one.
    monkeypatch.setenv("TIDE_AO_BIN", str(tmp_path / "no-real-ao"))
    # SOC-101: no test may message the real manager (ao send to the address from
    # ~/.tide/manager.json) or Grisha (the Telegram alert command).
    monkeypatch.setenv("TIDE_MANAGER_FILE", str(tmp_path / "no-manager.json"))
    monkeypatch.delenv("TIDE_ALERT_CMD", raising=False)
    monkeypatch.setenv("TIDE_AO_DB", str(tmp_path / "no-ao.db"))
    # The live pult's shared dialog journal (AO project env) flips dialog.tail
    # into "shared journal" filtering — a test reads only its own journals.
    monkeypatch.delenv("TIDE_DIALOG_LOG", raising=False)
    # `tide handoff` raises the fresh session itself (decision 07): a test reaching
    # the CLI must never open a real terminal / AO session. Tests of the auto-spawn
    # delenv this and inject a fake adapter.
    monkeypatch.setenv("TIDE_HANDOFF_NO_SPAWN", "1")
    monkeypatch.setenv("TIDE_GLOBAL_SETTINGS", str(tmp_path / "global-claude-settings.json"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    # `tide init` delivers the skills as part of the install (work 49) — point the
    # target at a per-test tmp dir so no test can ever link into the developer's
    # real ~/.claude/skills (which holds ~30 personal skills of theirs).
    monkeypatch.setenv("TIDE_SKILLS_DIR", str(tmp_path / "claude-skills"))
    # Tests legitimately create many arcs per second (a rate only a runaway loop
    # produces in real life) — lift the spawn backpressure for the suite; the
    # gate's own tests in test_arc_gates.py set a low limit explicitly.
    monkeypatch.setenv("TIDE_SPAWN_LIMIT", "0")


def fill_entry(entry: Path, goal: str = "a real goal for the picker") -> Path:
    """Give a test entry a REAL formulation so it passes the draft gate (cand 04).

    Fresh templates classify as drafts and are hidden from the picker; tests that
    need an entry to be pickable fill its goal the way real flows do.
    """
    from tide import fields
    from tide.arc import stream as _stream

    pp = _stream.passport_path(entry)
    fields.set_field(pp, "goal", goal)
    text = pp.read_text(encoding="utf-8")
    marker = "<the runbook — the reproducible procedure to follow each run>"
    if marker in text:
        pp.write_text(text.replace(marker, "1. go"), encoding="utf-8")
    return entry


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """A tmp dir with a fresh ``.tide/`` skeleton; returns the project root."""
    build_tide_skeleton(tmp_path, name="demo")
    return tmp_path


@pytest.fixture
def tmp_control_home(tmp_path: Path) -> Path:
    """A tmp control-home: ``.tide/`` skeleton + roster.md (dogfood install dir)."""
    build_tide_skeleton(tmp_path, name="control-home", control_home=True)
    return tmp_path


@pytest.fixture
def worker_role(monkeypatch) -> None:
    """Force TIDE_ROLE=worker for role-gating tests."""
    monkeypatch.setenv("TIDE_ROLE", "worker")


@pytest.fixture
def orchestrator_role(monkeypatch) -> None:
    """Force TIDE_ROLE=orchestrator for role-gating tests."""
    monkeypatch.setenv("TIDE_ROLE", "orchestrator")

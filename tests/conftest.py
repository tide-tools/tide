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

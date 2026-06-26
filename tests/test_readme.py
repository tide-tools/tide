"""114-derived-materials — `tide readme`: generate + stamp + gate the user door.

The README is a DERIVED material: generated from CANON.md, stamped with the
cannon-rev it was projected from, and gated so drift is detectable. These tests
mirror the code↔canon gate's contract one level up (canon↔README): the gate
passes on a fresh projection, trips STALE when canon moves ahead OR the README is
hand-edited, and FAIL-LOUDs (code 2) when CANON.md is missing.
"""

from __future__ import annotations

import pytest

from tide import cli, paths, readme
from tide.cannon import rev, store


# A populated canon (the conftest skeleton leaves sections empty) so the
# projection has user-facing content to carry.
CANON_POPULATED = """# CANON.md — widget

## What it is
Widget turns gizmos into gadgets.

## State & components
INTERNAL: gizmo-engine v3, gadget-cache, agent-only wiring detail.

## Interfaces / how used
Run `widget build` then open the result.

## Cannon journal

### 2026-06-26 · seeded
first entry
"""


@pytest.fixture
def populated(tmp_project):
    """tmp_project whose CANON.md carries real user-facing sections."""
    paths.canon_file(tmp_project).write_text(CANON_POPULATED, encoding="utf-8")
    return tmp_project


@pytest.fixture
def in_project(populated, monkeypatch):
    """Run CLI commands as if cwd is the populated project root."""
    monkeypatch.chdir(populated)
    return populated


# --- pure render / parse ---------------------------------------------------

def test_project_name_parsed_from_canon_h1():
    assert readme.project_name(CANON_POPULATED) == "widget"


def test_project_name_falls_back_when_header_absent():
    assert readme.project_name("## What it is\nno header\n", fallback="x") == "x"


def test_render_embeds_stamp_with_cannon_rev():
    text = readme.render(CANON_POPULATED, "abc123def456")
    assert readme.STAMP_PREFIX in text
    assert "cannon-rev abc123def456" in text
    assert readme.parse_stamp(text) == "abc123def456"


def test_render_projects_user_sections_only():
    text = readme.render(CANON_POPULATED, "deadbeef0000")
    # title + intent + how-to-use are projected
    assert text.startswith("# widget")
    assert "Widget turns gizmos into gadgets." in text
    assert "## How to use" in text
    assert "Run `widget build`" in text
    # the agent-facing State & components detail is NOT dumped (referenced, not duplicated)
    assert "gizmo-engine v3" not in text
    assert "INTERNAL" not in text
    # README points to CANON for living state
    assert ".tide/cannon/CANON.md" in text


def test_render_is_deterministic():
    a = readme.render(CANON_POPULATED, "feedface0001")
    b = readme.render(CANON_POPULATED, "feedface0001")
    assert a == b


def test_parse_stamp_none_when_unstamped():
    assert readme.parse_stamp("# Hand written\n\nno stamp here\n") is None


# --- generate --------------------------------------------------------------

def test_generate_creates_readme_with_stamp(populated):
    text, status = readme.generate(populated)
    assert status == "generated"
    target = readme.readme_file(populated)
    assert target.is_file()
    assert readme.parse_stamp(target.read_text(encoding="utf-8")) == rev.compute(populated)
    assert text == target.read_text(encoding="utf-8")


def test_generate_idempotent_is_current(populated):
    readme.generate(populated)
    _text, status = readme.generate(populated)
    assert status == "current"


def test_generate_regenerates_after_drift(populated):
    readme.generate(populated)
    readme.readme_file(populated).write_text("# hand-mangled\n", encoding="utf-8")
    _text, status = readme.generate(populated)
    assert status == "regenerated"


def test_generate_dry_run_writes_nothing(populated):
    text, status = readme.generate(populated, dry_run=True)
    assert status == "dry-run"
    assert readme.STAMP_PREFIX in text
    assert not readme.readme_file(populated).exists()


def test_generate_raises_when_canon_missing(populated):
    paths.canon_file(populated).unlink()
    with pytest.raises(FileNotFoundError):
        readme.generate(populated)


# --- check (the drift gate) ------------------------------------------------

def test_check_current_on_fresh_projection(populated):
    readme.generate(populated)
    code, reasons = readme.check(populated)
    assert code == 0
    assert reasons == []


def test_check_stale_when_readme_missing(populated):
    code, reasons = readme.check(populated)
    assert code == 1
    assert any("missing" in r for r in reasons)


def test_check_stale_when_unstamped(populated):
    readme.readme_file(populated).write_text("# hand written\n\nno stamp\n", encoding="utf-8")
    code, reasons = readme.check(populated)
    assert code == 1
    assert any("no tide-readme stamp" in r for r in reasons)


def test_check_stale_when_hand_edited_after_generate(populated):
    readme.generate(populated)
    target = readme.readme_file(populated)
    # keep the (valid, current-rev) stamp but mutate the body → drift from projection
    edited = target.read_text(encoding="utf-8").replace("# widget", "# WIDGET HACKED")
    target.write_text(edited, encoding="utf-8")
    code, reasons = readme.check(populated)
    assert code == 1
    assert any("drifted" in r for r in reasons)


def test_check_stale_when_canon_moves_ahead(populated):
    readme.generate(populated)
    old_rev = rev.compute(populated)
    # move canon forward → cannon-rev changes, README stamp now lags
    canon = paths.canon_file(populated)
    canon.write_text(
        canon.read_text(encoding="utf-8") + "\n### 2026-06-27 · moved\nnew\n",
        encoding="utf-8",
    )
    assert rev.compute(populated) != old_rev
    code, reasons = readme.check(populated)
    assert code == 1
    assert any("canon moved ahead" in r for r in reasons)


def test_check_oracle_error_when_canon_missing(populated):
    readme.generate(populated)
    paths.canon_file(populated).unlink()
    code, reasons = readme.check(populated)
    assert code == 2
    assert any("oracle-error" in r for r in reasons)


# --- CLI integration -------------------------------------------------------

def test_cli_readme_generates(in_project, capsys):
    rc = cli.main(["readme"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "generated" in out
    assert readme.readme_file(in_project).is_file()


def test_cli_readme_idempotent(in_project, capsys):
    cli.main(["readme"])
    capsys.readouterr()
    rc = cli.main(["readme"])
    assert rc == 0
    assert "already current" in capsys.readouterr().out


def test_cli_readme_dry_run_prints_without_writing(in_project, capsys):
    rc = cli.main(["readme", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert readme.STAMP_PREFIX in out
    assert not readme.readme_file(in_project).exists()


def test_cli_readme_check_current(in_project, capsys):
    cli.main(["readme"])
    capsys.readouterr()
    rc = cli.main(["readme", "--check"])
    assert rc == 0
    assert "current" in capsys.readouterr().out


def test_cli_readme_check_stale_exit_1(in_project, capsys):
    rc = cli.main(["readme", "--check"])  # never generated → missing → stale
    assert rc == 1
    assert "stale" in capsys.readouterr().out


def test_cli_readme_check_oracle_error_exit_2(in_project, capsys):
    cli.main(["readme"])
    paths.canon_file(in_project).unlink()
    capsys.readouterr()
    rc = cli.main(["readme", "--check"])
    assert rc == 2
    assert "oracle-error" in capsys.readouterr().err


def test_cli_readme_generate_oracle_error_exit_2(in_project, capsys):
    """Generate mode (no --check) also FAIL-LOUDs code 2 on a missing CANON.md —
    it must NOT conflate infrastructure-broken with main()'s generic code-1."""
    paths.canon_file(in_project).unlink()
    rc = cli.main(["readme"])
    assert rc == 2
    assert "oracle-error" in capsys.readouterr().err


# --- empty-section guards (raw skeleton: the initial state of every project) ---

def test_generate_on_raw_skeleton_omits_empty_sections(tmp_project, monkeypatch):
    """A fresh project's CANON.md has empty `## What it is` / `## Interfaces / how
    used`; the projection must skip those guards — title + divider + stamp only,
    no dangling `## How to use` heading."""
    monkeypatch.chdir(tmp_project)
    text, status = readme.generate(tmp_project)
    assert status == "generated"
    assert text.startswith("# demo")          # title from the skeleton name
    assert "## How to use" not in text         # `if how:` guard skipped
    assert "---" in text                        # divider still present
    assert readme.STAMP_PREFIX in text          # stamp still present
    # the gate accepts its own fresh projection even when sections are empty
    code, _ = readme.check(tmp_project)
    assert code == 0

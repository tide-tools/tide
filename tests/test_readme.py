"""114-derived-materials — `tide readme`: generate + stamp + gate the user door.

The README is a DERIVED material: generated from CANON.md, stamped with the
cannon-rev it was projected from, and gated so drift is detectable. These tests
mirror the code↔canon gate's contract one level up (canon↔README): the gate
passes on a fresh projection, trips STALE when canon moves ahead OR the README is
hand-edited, and FAIL-LOUDs (code 2) when CANON.md is missing.
"""

from __future__ import annotations

import pytest

from tide import cli, paths, readme, roster
from tide.cannon import rev, store
from tests.conftest import build_tide_skeleton


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


# --- sweep (roster-wide generate / check) ------------------------------------

def _make_project(tmp_path, name: str):
    """Helper: build a minimal tide project dir and return its root."""
    proj = tmp_path / name
    build_tide_skeleton(proj, name=name)
    return proj


def test_sweep_empty_roster_returns_empty(tmp_control_home):
    """Empty roster → empty result list, no crash."""
    results = readme.sweep(tmp_control_home)
    assert results == []


def test_sweep_generate_creates_readme(tmp_control_home, tmp_path):
    """Generate mode: sweep generates a README for each roster entry."""
    proj = _make_project(tmp_path, "alpha")
    roster.add(tmp_control_home, "alpha", str(proj))

    results = readme.sweep(tmp_control_home)

    assert len(results) == 1
    name, status = results[0]
    assert name == "alpha"
    assert status == "generated"
    assert readme.readme_file(proj).is_file()


def test_sweep_generate_current_on_second_run(tmp_control_home, tmp_path):
    """Generate mode: idempotent — second sweep reports 'current'."""
    proj = _make_project(tmp_path, "beta")
    roster.add(tmp_control_home, "beta", str(proj))

    readme.sweep(tmp_control_home)
    results = readme.sweep(tmp_control_home)

    _, status = results[0]
    assert status == "current"


def test_sweep_check_current_after_generate(tmp_control_home, tmp_path):
    """Check mode: 'current' after README has been generated."""
    proj = _make_project(tmp_path, "gamma")
    roster.add(tmp_control_home, "gamma", str(proj))
    readme.generate(proj)

    results = readme.sweep(tmp_control_home, check_mode=True)

    _, status = results[0]
    assert status == "current"


def test_sweep_check_stale_when_no_readme(tmp_control_home, tmp_path):
    """Check mode: 'stale' when README has never been generated."""
    proj = _make_project(tmp_path, "delta")
    roster.add(tmp_control_home, "delta", str(proj))

    results = readme.sweep(tmp_control_home, check_mode=True)

    _, status = results[0]
    assert status == "stale"


def test_sweep_oracle_error_for_missing_path(tmp_control_home):
    """A roster entry with a non-existent path reports oracle-error, never crashes."""
    roster.add(tmp_control_home, "ghost", "/nonexistent/path/tide-sweep-ghost")

    results = readme.sweep(tmp_control_home)

    assert len(results) == 1
    name, status = results[0]
    assert name == "ghost"
    assert "oracle-error" in status


def test_sweep_oracle_error_check_mode_missing_canon(tmp_control_home, tmp_path):
    """Check mode: oracle-error when project path exists but has no CANON.md."""
    proj = tmp_path / "broken"
    proj.mkdir()
    (proj / ".tide").mkdir()  # .tide/ present but no cannon/CANON.md
    roster.add(tmp_control_home, "broken", str(proj))

    results = readme.sweep(tmp_control_home, check_mode=True)

    _, status = results[0]
    assert status == "oracle-error"


def test_sweep_continues_past_errors(tmp_control_home, tmp_path):
    """An oracle-error on one entry does not abort the sweep — all entries visited."""
    good = _make_project(tmp_path, "good")
    roster.add(tmp_control_home, "ghost", "/nonexistent/path/tide-sweep-ghost2")
    roster.add(tmp_control_home, "good", str(good))

    results = readme.sweep(tmp_control_home)

    assert len(results) == 2
    names = [n for n, _ in results]
    assert names == ["ghost", "good"]
    assert "oracle-error" in results[0][1]
    assert results[1][1] == "generated"


def test_sweep_dry_run_writes_nothing(tmp_control_home, tmp_path):
    """Dry-run mode: no README file written, status is 'dry-run'."""
    proj = _make_project(tmp_path, "dryproj")
    roster.add(tmp_control_home, "dryproj", str(proj))

    results = readme.sweep(tmp_control_home, dry_run=True)

    _, status = results[0]
    assert status == "dry-run"
    assert not readme.readme_file(proj).exists()


# --- CLI --all flag ----------------------------------------------------------

def test_cli_readme_all_generates_roster(tmp_control_home, tmp_path, monkeypatch, capsys):
    """'tide readme --all' sweeps the roster and reports per-project status."""
    monkeypatch.chdir(tmp_control_home)
    proj = _make_project(tmp_path, "proj")
    roster.add(tmp_control_home, "proj", str(proj))

    rc = cli.main(["readme", "--all"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "proj:" in out
    assert "generated" in out


def test_cli_readme_all_check_exit_1_when_stale(tmp_control_home, tmp_path, monkeypatch, capsys):
    """'tide readme --check --all' exits 1 when any project README is stale."""
    monkeypatch.chdir(tmp_control_home)
    proj = _make_project(tmp_path, "proj")
    roster.add(tmp_control_home, "proj", str(proj))
    # README never generated → stale

    rc = cli.main(["readme", "--check", "--all"])

    assert rc == 1
    out = capsys.readouterr().out
    assert "stale" in out


def test_cli_readme_all_check_exit_0_when_all_current(tmp_control_home, tmp_path, monkeypatch, capsys):
    """'tide readme --check --all' exits 0 when every registered README is current."""
    monkeypatch.chdir(tmp_control_home)
    proj = _make_project(tmp_path, "proj")
    roster.add(tmp_control_home, "proj", str(proj))
    readme.generate(proj)

    rc = cli.main(["readme", "--check", "--all"])

    assert rc == 0
    assert "current" in capsys.readouterr().out


def test_cli_readme_all_empty_roster_exits_0(tmp_control_home, monkeypatch, capsys):
    """'tide readme --all' with an empty roster exits 0 with an informational note."""
    monkeypatch.chdir(tmp_control_home)

    rc = cli.main(["readme", "--all"])

    assert rc == 0
    assert "empty" in capsys.readouterr().out


def test_cli_readme_all_oracle_error_exits_nonzero_in_check_mode(
    tmp_control_home, monkeypatch, capsys
):
    """'tide readme --check --all' exits nonzero when a project has an oracle-error."""
    monkeypatch.chdir(tmp_control_home)
    roster.add(tmp_control_home, "ghost", "/nonexistent/path/tide-cli-ghost")

    rc = cli.main(["readme", "--check", "--all"])

    assert rc != 0
    out = capsys.readouterr().out
    assert "oracle-error" in out


# --- regression: tilde-bad-username path must not crash sweep ---------------
# Covers two bugs fixed together:
#   HIGH  — expanduser() was outside the try block; a bad ~username raises
#            RuntimeError which escaped and crashed the whole sweep.
#   MEDIUM — has_issue matched exact "oracle-error" but exception-path produces
#            "oracle-error: <detail>"; startswith() now catches both forms so
#            the CI gate exits nonzero even for the exception path.

_BAD_TILDE_PATH = "~nonexistentuser123tide/anything"


def test_sweep_bad_tilde_does_not_crash_generate_mode(tmp_control_home, tmp_path):
    """expanduser() on a bad ~username must be captured as oracle-error, never raise.

    A good entry after the bad one must still be processed (collect-and-continue).
    """
    good = _make_project(tmp_path, "good")
    roster.add(tmp_control_home, "bad-tilde", _BAD_TILDE_PATH)
    roster.add(tmp_control_home, "good", str(good))

    # Must not raise RuntimeError or any other exception.
    results = readme.sweep(tmp_control_home)

    assert len(results) == 2
    names = [n for n, _ in results]
    assert names == ["bad-tilde", "good"]
    bad_status = results[0][1]
    assert bad_status.startswith("oracle-error"), bad_status
    # good entry was reached and processed despite the prior error
    assert results[1][1] == "generated"


def test_sweep_bad_tilde_check_mode_sets_has_issue(tmp_control_home):
    """Exception-path oracle-error (detail string) must count as a CI-gate failure.

    Covers the MEDIUM bug: 'oracle-error: <detail>' must match the has_issue check
    so --check --all exits nonzero even when the error came from the exception path.
    """
    roster.add(tmp_control_home, "bad-tilde", _BAD_TILDE_PATH)

    results = readme.sweep(tmp_control_home, check_mode=True)

    assert len(results) == 1
    _, status = results[0]
    # Exception path produces "oracle-error: <detail>" (with colon + detail).
    assert status.startswith("oracle-error"), status

    # Simulate what _cmd_readme_all does: the startswith check must fire.
    has_issue = any(
        s == "stale" or s.startswith("oracle-error") for _, s in results
    )
    assert has_issue, "CI gate would have silently passed a broken entry"

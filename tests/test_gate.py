"""M1 unit — gate.decide: tri-state cannon-gate oracle (0=current 1=stale 2=oracle-error).

Coverage targets:
* decide: all three codes returned correctly
* cannon_lint: c1 placeholders, c2 dup headings, c3 dup stamps, c4 empty sections
* _open_arc_dirs: top-level + goal sub-arcs
* reality-rev stale-detection: covered file changed → gate returns 1
* oracle-error FAIL-LOUD: returns 2, NEVER 0, when oracle can't evaluate
* CLI smoke: tide cannon gate exits 0/1/2
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tide import gate, paths
from tide.arc import stream
from tide.cannon import merge, rev as cannon_rev, store

from tests.conftest import strip_placeholders


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_delta(arc_dir: Path, body: str, *, merged: str = "no") -> None:
    (arc_dir / "delta.md").write_text(
        "# delta\nmerged: {0}\n\n{1}\n".format(merged, body),
        encoding="utf-8",
    )


def _close_arc_with_delta(root: Path, slug_name: str, body: str = "some change") -> Path:
    entry = stream.new_arc(root, slug_name)
    (entry / "output" / "r.md").write_text("done", encoding="utf-8")
    _write_delta(entry, body)
    strip_placeholders(entry / "arc.md")
    return stream.close(root, slug_name)


def _filled_canon(root: Path) -> None:
    """Write a maintained CANON.md (non-empty sections + journal entry) to *root*."""
    paths.canon_file(root).write_text(
        "# CANON.md — demo\n\n"
        "## What it is\n\nA demo project.\n\n"
        "## State & components\n\nComponents here.\n\n"
        "## Interfaces / how used\n\nPublic API.\n\n"
        "## Cannon journal\n\n### 2026-01-01 · init\n\nInitial entry.\n",
        encoding="utf-8",
    )


def _write_state_covers(root: Path, globs: list) -> None:
    from tide.cannon import reality as _r  # avoid top-level import of reality in gate tests
    from tide import paths as _p
    (_p.state_dir(root) / "canon-covers").write_text(
        "\n".join(globs) + "\n", encoding="utf-8"
    )


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    """Run CLI commands as if cwd is the project root."""
    monkeypatch.chdir(tmp_project)
    return tmp_project


# ---------------------------------------------------------------------------
# tri-state: code 0 (current)
# ---------------------------------------------------------------------------

def test_gate_current_clean_fresh_project(tmp_project):
    """A freshly initialised project with no arcs and no issues → current (0)."""
    code, reasons = gate.decide(tmp_project)
    assert code == 0
    assert reasons == []


def test_gate_current_with_open_arc_and_no_drift(tmp_project):
    """An open arc stamped at the current cannon-rev → current (0)."""
    stream.new_arc(tmp_project, "work")
    code, reasons = gate.decide(tmp_project)
    assert code == 0
    assert reasons == []


def test_gate_current_after_merge_and_new_arc(tmp_project):
    """After a complete merge cycle (close→merge), opening a fresh arc → current (0)."""
    # Pre-fill CANON.md so lint stays clean after merge (maintained project, all sections)
    paths.canon_file(tmp_project).write_text(
        "# CANON.md — demo\n\n"
        "## What it is\n\nA demo project.\n\n"
        "## State & components\n\nV1\n\n"
        "## Interfaces / how used\n\nPublic API.\n\n"
        "## Cannon journal\n",
        encoding="utf-8",
    )
    # Close arc init with a small delta so the barrier lifts after the merge
    entry = stream.new_arc(tmp_project, "init")
    _write_delta(entry, "Some work was done.")
    (entry / "output" / "r.md").write_text("done", encoding="utf-8")
    strip_placeholders(entry / "arc.md")
    closed = stream.close(tmp_project, "init")
    # Merge the closed arc's delta (marks merged, bumps cannon-rev)
    merge.merge_delta(tmp_project, closed, slug="init")
    # Open a fresh arc (barrier lifted; stamps the new current cannon-rev)
    stream.new_arc(tmp_project, "next")
    code, reasons = gate.decide(tmp_project)
    assert code == 0, reasons


def test_gate_current_fresh_empty_sections_not_stale(tmp_project):
    """A fresh project's empty canonical sections are NOT a lint error (not maintained)."""
    # Default CANON.md has empty sections + no journal entries → not maintained
    code, reasons = gate.decide(tmp_project)
    assert code == 0


# ---------------------------------------------------------------------------
# tri-state: code 1 (stale)
# ---------------------------------------------------------------------------

def test_gate_stale_unmerged_closed_delta(tmp_project):
    """Closed arc with unmerged delta → stale (1), reason mentions 'unmerged'."""
    _close_arc_with_delta(tmp_project, "alpha")
    code, reasons = gate.decide(tmp_project)
    assert code == 1
    assert any("unmerged" in r for r in reasons)


def test_gate_stale_unmerged_active_delta(tmp_project):
    """Active arc with a written-but-unmerged delta → stale (1)."""
    entry = stream.new_arc(tmp_project, "work")
    _write_delta(entry, "work in progress")
    code, reasons = gate.decide(tmp_project)
    assert code == 1
    assert any("unmerged" in r for r in reasons)


def test_gate_stale_cannon_rev_drift(tmp_project):
    """Open arc drifted on cannon-rev → stale (1), reason mentions 'cannon-rev'."""
    # Pre-fill CANON.md so lint passes (maintained project)
    _filled_canon(tmp_project)

    # Create arc → stamps cannon-rev = r0
    entry = stream.new_arc(tmp_project, "work")
    stamped_cr = cannon_rev.compute(tmp_project)

    # Modify CANON.md → cannon-rev = r1 ≠ r0
    canon = paths.canon_file(tmp_project)
    canon.write_text(
        canon.read_text(encoding="utf-8") + "\n### 2026-02-01 · bump\n\nmore.\n",
        encoding="utf-8",
    )
    assert cannon_rev.compute(tmp_project) != stamped_cr  # precondition

    code, reasons = gate.decide(tmp_project)
    assert code == 1
    assert any("cannon-rev" in r for r in reasons)


def test_gate_stale_lint_duplicate_heading(tmp_project):
    """Duplicate ## heading in CANON.md → stale (1)."""
    paths.canon_file(tmp_project).write_text(
        "# CANON.md — demo\n\n"
        "## What it is\n\nfoo\n\n"
        "## What it is\n\nbar (duplicate!)\n\n"
        "## Cannon journal\n",
        encoding="utf-8",
    )
    code, reasons = gate.decide(tmp_project)
    assert code == 1
    assert any("duplicate heading" in r for r in reasons)


def test_gate_stale_lint_placeholder(tmp_project):
    """Template placeholder <…> in CANON.md → stale (1)."""
    paths.canon_file(tmp_project).write_text(
        "# CANON.md — demo\n\n"
        "## What it is\n\n<fill in here>\n\n"
        "## Cannon journal\n",
        encoding="utf-8",
    )
    code, reasons = gate.decide(tmp_project)
    assert code == 1
    assert any("placeholder" in r for r in reasons)


def test_gate_stale_lint_empty_canonical_section_on_maintained_project(tmp_project):
    """A maintained project (≥1 journal entry) with an empty canonical section → stale (1).

    Distinguishes 'seeded-empty new project' from 'hollow maintained canon'.
    """
    paths.canon_file(tmp_project).write_text(
        "# CANON.md — demo\n\n"
        "## What it is\n\n"  # intentionally empty
        "## State & components\n\nsome state\n\n"
        "## Interfaces / how used\n\nsome interfaces\n\n"
        "## Cannon journal\n\n### 2026-01-01 · init\n\nInitial.\n",
        encoding="utf-8",
    )
    code, reasons = gate.decide(tmp_project)
    assert code == 1
    assert any("empty canonical section" in r for r in reasons)


def test_gate_stale_lint_duplicate_journal_stamp(tmp_project):
    """Duplicate journal stamp in CANON.md → stale (1)."""
    paths.canon_file(tmp_project).write_text(
        "# CANON.md — demo\n\n"
        "## Cannon journal\n\n"
        "### 2026-01-01 · work\n\nbody\n\n"
        "### 2026-01-01 · work\n\nduplicate!\n",
        encoding="utf-8",
    )
    code, reasons = gate.decide(tmp_project)
    assert code == 1
    assert any("duplicate journal stamp" in r for r in reasons)


def test_gate_stale_multiple_reasons(tmp_project):
    """Multiple issues → stale (1), all reasons reported."""
    # Duplicate heading AND a closed unmerged delta
    paths.canon_file(tmp_project).write_text(
        "# CANON.md — demo\n\n"
        "## What it is\n\nfoo\n\n"
        "## What it is\n\nbar\n\n"
        "## Cannon journal\n",
        encoding="utf-8",
    )
    # We can't use _close_arc_with_delta because block_new_arc fires for the second
    # arc – instead write a fake closed entry manually.
    closed = paths.arcs_dir(tmp_project) / "__01-alpha__"
    closed.mkdir(parents=True)
    (closed / "delta.md").write_text(
        "# delta\nmerged: no\n\nreal body\n", encoding="utf-8"
    )
    code, reasons = gate.decide(tmp_project)
    assert code == 1
    assert len(reasons) >= 2


# ---------------------------------------------------------------------------
# tri-state: code 2 (oracle-error) — FAIL-LOUD
# ---------------------------------------------------------------------------

def test_gate_oracle_error_missing_canon(tmp_project):
    """CANON.md missing → oracle-error (2)."""
    paths.canon_file(tmp_project).unlink()
    code, reasons = gate.decide(tmp_project)
    assert code == 2
    assert any("oracle-error" in r for r in reasons)


def test_gate_oracle_error_never_returns_zero(tmp_project):
    """oracle-error MUST return 2, never 0 (the FAIL-LOUD contract)."""
    paths.canon_file(tmp_project).unlink()
    code, _reasons = gate.decide(tmp_project)
    assert code != 0


def test_gate_oracle_error_unreadable_canon(tmp_project):
    """Unreadable CANON.md → oracle-error (2), not stale (1) or current (0)."""
    import stat
    canon = paths.canon_file(tmp_project)
    original_mode = canon.stat().st_mode
    canon.chmod(0o000)
    try:
        code, reasons = gate.decide(tmp_project)
        assert code == 2
        assert any("oracle-error" in r for r in reasons)
    finally:
        canon.chmod(original_mode)


def test_gate_oracle_error_is_always_2_not_0(tmp_project):
    """Verify the FAIL-LOUD invariant from another angle: stale state never 0."""
    entry = stream.new_arc(tmp_project, "alpha")
    _write_delta(entry, "real work")
    # unmerged active delta → stale, not current
    code, _ = gate.decide(tmp_project)
    assert code == 1  # stale
    assert code != 0  # not silently current


# ---------------------------------------------------------------------------
# reality-rev stale-detection (M2 integration)
# ---------------------------------------------------------------------------

def test_gate_stale_when_reality_rev_drifts(tmp_project):
    """Core M2 scenario: open arc with stale reality-rev stamp → stale (1).

    A covered file changed after the arc was opened (code shipped), but the arc
    has not been re-stamped (canon didn't update).  The gate must trip STALE.
    """
    _write_state_covers(tmp_project, ["*.md"])
    f = tmp_project / "tracked.md"
    f.write_text("v1", encoding="utf-8")

    # Open arc: stamps reality-rev = rr0 (based on v1)
    entry = stream.new_arc(tmp_project, "work")
    from tide import fields
    rr0 = fields.read_field(entry / "arc.md", "reality-rev")
    assert rr0 is not None, "reality-rev must be stamped when manifest present"

    # Modify the covered file → current reality-rev is now rr1 ≠ rr0
    f.write_text("v2", encoding="utf-8")
    from tide.cannon.reality import reality_rev as _rrv
    rr1 = _rrv(tmp_project)
    assert rr0 != rr1, "precondition: reality-rev must have moved"

    # Gate sees arc stamped at rr0 but current is rr1 → stale
    code, reasons = gate.decide(tmp_project)
    assert code == 1
    assert any("reality-rev" in r for r in reasons)


def test_gate_current_after_reality_rev_restamped(tmp_project):
    """Re-stamping an arc after a covered-file change clears the reality drift."""
    _write_state_covers(tmp_project, ["*.md"])
    f = tmp_project / "tracked.md"
    f.write_text("v1", encoding="utf-8")

    entry = stream.new_arc(tmp_project, "work")
    f.write_text("v2", encoding="utf-8")  # reality drifts

    # Re-stamp (simulates arc open/resume after the file changed)
    from tide.arc.stream import stamp_rev
    stamp_rev(entry, tmp_project)

    code, reasons = gate.decide(tmp_project)
    assert code == 0, reasons


def test_gate_no_reality_drift_when_uncovered_file_changes(tmp_project):
    """Changing a file NOT in the manifest does not trigger reality-rev drift."""
    _write_state_covers(tmp_project, ["*.md"])
    f = tmp_project / "tracked.md"
    f.write_text("v1", encoding="utf-8")

    entry = stream.new_arc(tmp_project, "work")

    # Change a .py file (not covered by *.md glob)
    (tmp_project / "script.py").write_text("print('hi')", encoding="utf-8")

    code, reasons = gate.decide(tmp_project)
    assert code == 0, reasons


def test_gate_no_reality_drift_when_no_manifest(tmp_project):
    """Without a manifest, reality-rev is None → no reality-rev drift check."""
    entry = stream.new_arc(tmp_project, "work")
    (tmp_project / "any.md").write_text("new", encoding="utf-8")

    code, reasons = gate.decide(tmp_project)
    assert code == 0  # no manifest → no reality axis


# ---------------------------------------------------------------------------
# cannon_lint (unit tests)
# ---------------------------------------------------------------------------

def test_cannon_lint_clean_project(tmp_project):
    """Fresh CANON.md with no journal → no lint issues."""
    issues = gate.cannon_lint(tmp_project)
    assert issues == []


def test_cannon_lint_raises_file_not_found_when_canon_missing(tmp_project):
    """Missing CANON.md → FileNotFoundError (oracle-error path in decide)."""
    paths.canon_file(tmp_project).unlink()
    with pytest.raises(FileNotFoundError):
        gate.cannon_lint(tmp_project)


def test_cannon_lint_detects_duplicate_heading(tmp_project):
    paths.canon_file(tmp_project).write_text(
        "# CANON.md — demo\n\n## What it is\n\nA\n\n## What it is\n\nB\n\n## Cannon journal\n",
        encoding="utf-8",
    )
    issues = gate.cannon_lint(tmp_project)
    assert any("duplicate heading" in i for i in issues)


def test_cannon_lint_detects_placeholder(tmp_project):
    paths.canon_file(tmp_project).write_text(
        "# CANON.md — demo\n\n## What it is\n\n<fill me in>\n\n## Cannon journal\n",
        encoding="utf-8",
    )
    issues = gate.cannon_lint(tmp_project)
    assert any("placeholder" in i for i in issues)


def test_cannon_lint_ignores_angle_span_inside_code(tmp_project):
    """`<…>` inside inline backticks / fenced blocks are examples, not placeholders.

    Candidate 109: the backticked goal-H1 example tripped the gate twice; the proper
    fix lets the original backticked form pass without a guillemet workaround.
    """
    paths.canon_file(tmp_project).write_text(
        "# CANON.md — demo\n\n"
        "## What it is\n\n"
        "Run `tide arc new <slug>` to start. See `<one line — what this arc closes>`.\n\n"
        "```\n"
        "tide contract sign <slug> --signer <role>\n"
        "```\n\n"
        "## Cannon journal\n",
        encoding="utf-8",
    )
    issues = gate.cannon_lint(tmp_project)
    assert not any("placeholder" in i for i in issues), issues


def test_cannon_lint_flags_bare_span_but_not_backticked_on_mixed_line(tmp_project):
    """A bare `<…>` in prose is still flagged even when a backticked one shares the line."""
    paths.canon_file(tmp_project).write_text(
        "# CANON.md — demo\n\n"
        "## What it is\n\n"
        "Fill <fill me in> like `<code>` here.\n\n"
        "## Cannon journal\n",
        encoding="utf-8",
    )
    issues = gate.cannon_lint(tmp_project)
    placeholder_issues = [i for i in issues if "placeholder" in i]
    assert len(placeholder_issues) == 1
    assert "<fill me in>" in placeholder_issues[0]
    assert "<code>" not in placeholder_issues[0]


def test_cannon_lint_detects_duplicate_journal_stamp(tmp_project):
    paths.canon_file(tmp_project).write_text(
        "# CANON.md — demo\n\n"
        "## Cannon journal\n\n"
        "### 2026-01-01 · x\n\nfoo\n\n"
        "### 2026-01-01 · x\n\nbar\n",
        encoding="utf-8",
    )
    issues = gate.cannon_lint(tmp_project)
    assert any("duplicate journal stamp" in i for i in issues)


def test_cannon_lint_maintained_project_empty_sections_is_issue(tmp_project):
    paths.canon_file(tmp_project).write_text(
        "# CANON.md — demo\n\n"
        "## What it is\n\n"  # empty
        "## State & components\n\ndata\n\n"
        "## Interfaces / how used\n\napi\n\n"
        "## Cannon journal\n\n### 2026-01-01 · init\n\nentry\n",
        encoding="utf-8",
    )
    issues = gate.cannon_lint(tmp_project)
    assert any("empty canonical section" in i for i in issues)
    assert any("What it is" in i for i in issues)


def test_cannon_lint_fresh_project_empty_sections_ok(tmp_project):
    """No journal entries → sections can be empty (seed state, not neglect)."""
    issues = gate.cannon_lint(tmp_project)
    assert issues == []


# ---------------------------------------------------------------------------
# _open_arc_dirs
# ---------------------------------------------------------------------------

def test_open_arc_dirs_empty_when_no_arcs(tmp_project):
    assert gate._open_arc_dirs(tmp_project) == []


def test_open_arc_dirs_finds_open_arc(tmp_project):
    entry = stream.new_arc(tmp_project, "work")
    dirs = gate._open_arc_dirs(tmp_project)
    assert entry in dirs


def test_open_arc_dirs_excludes_closed_arc(tmp_project):
    entry = stream.new_arc(tmp_project, "work")
    (entry / "output" / "r.md").write_text("done", encoding="utf-8")
    strip_placeholders(entry / "arc.md")
    closed = stream.close(tmp_project, "work")
    dirs = gate._open_arc_dirs(tmp_project)
    assert closed not in dirs
    assert entry not in dirs


def test_open_arc_dirs_finds_sub_arc_in_open_goal(tmp_project):
    goal = stream.new_goal(tmp_project, "ship")
    sub = stream.new_arc(tmp_project, "wire", goal_slug="ship")
    dirs = gate._open_arc_dirs(tmp_project)
    assert goal in dirs
    assert sub in dirs


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------

def test_cannon_gate_cli_exits_zero_on_current(in_project):
    """``tide cannon gate`` exits 0 on a clean, current project."""
    from tide.cli import main
    code = main(["cannon", "gate"])
    assert code == 0


def test_cannon_gate_cli_exits_one_on_stale(in_project):
    """``tide cannon gate`` exits 1 when there is a stale issue."""
    _close_arc_with_delta(in_project, "alpha")
    from tide.cli import main
    code = main(["cannon", "gate"])
    assert code == 1


def test_cannon_gate_cli_exits_two_on_oracle_error(in_project):
    """``tide cannon gate`` exits 2 on oracle-error (CANON.md deleted)."""
    paths.canon_file(in_project).unlink()
    from tide.cli import main
    code = main(["cannon", "gate"])
    assert code == 2


def test_cannon_gate_cli_is_registered(in_project, capsys):
    """``tide cannon gate --help`` shows without error."""
    from tide.cli import main
    with pytest.raises(SystemExit) as exc:
        main(["cannon", "gate", "--help"])
    assert exc.value.code == 0


# ---------------------------------------------------------------------------
# Standing reality↔canon baseline (G2): prose-staleness independent of arcs
# ---------------------------------------------------------------------------

def test_gate_standing_reality_drift(tmp_project):
    """Baseline stamped, then a covered signature moves → standing prose-stale (1)."""
    from tide.cannon import reality

    _write_state_covers(tmp_project, ["*.py"])
    f = tmp_project / "mod.py"
    f.write_text("def foo():\n    return 1\n", encoding="utf-8")
    reality.stamp_canon_baseline(tmp_project)  # baseline == current reality

    code, _ = gate.decide(tmp_project)
    assert code == 0  # freshly baselined → no standing drift

    # An API-surface change (new signature) moves reality past the baseline.
    f.write_text("def foo():\n    return 1\n\ndef bar():\n    return 2\n", encoding="utf-8")
    code, reasons = gate.decide(tmp_project)
    assert code == 1
    assert any("canon prose may be stale" in r for r in reasons)


def test_gate_no_standing_drift_on_body_only_edit(tmp_project):
    """A body-only edit keeps the API surface → no standing drift (deliberate)."""
    from tide.cannon import reality

    _write_state_covers(tmp_project, ["*.py"])
    f = tmp_project / "mod.py"
    f.write_text("def foo():\n    return 1\n", encoding="utf-8")
    reality.stamp_canon_baseline(tmp_project)

    f.write_text("def foo():\n    return 999  # body churn only\n", encoding="utf-8")
    code, reasons = gate.decide(tmp_project)
    assert code == 0, reasons


def test_gate_no_standing_drift_without_baseline(tmp_project):
    """Manifest but no baseline (legacy canon) → standing clause stays silent."""
    _write_state_covers(tmp_project, ["*.py"])
    (tmp_project / "mod.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    # default CANON (no journal, no baseline) → only the standing clause is at issue
    code, reasons = gate.decide(tmp_project)
    assert not any("canon prose may be stale" in r for r in reasons)


def test_gate_lint_missing_baseline(tmp_project):
    """Maintained canon + manifest but no baseline → c5 lint surfaces it."""
    _filled_canon(tmp_project)
    _write_state_covers(tmp_project, ["*.py"])
    (tmp_project / "mod.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    issues = gate.cannon_lint(tmp_project)
    assert any("missing reality-rev baseline" in i for i in issues)


def test_gate_lint_baseline_present_clears_c5(tmp_project):
    from tide.cannon import reality

    _filled_canon(tmp_project)
    _write_state_covers(tmp_project, ["*.py"])
    (tmp_project / "mod.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    reality.stamp_canon_baseline(tmp_project)
    issues = gate.cannon_lint(tmp_project)
    assert not any("missing reality-rev baseline" in i for i in issues)


def test_gate_clean_after_close_chain_with_manifest(tmp_project):
    """Risk #1: open→delta→merge→close WITH a covers manifest leaves the gate clean.

    contract.close merges the delta (which now stamps the reality baseline + bumps
    cannon-rev) and re-stamps the post-merge rev onto the sealed arc. The standing
    baseline equals current reality and the authoring arc is closed → no churn loop.
    """
    from tide.contract import lifecycle
    from tide.contract import model
    from tide.cannon import reality

    _filled_canon(tmp_project)
    _write_state_covers(tmp_project, ["*.py"])
    (tmp_project / "mod.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    reality.stamp_canon_baseline(tmp_project)  # pre-existing maintained canon is baselined

    entry = stream.new_arc(tmp_project, "work")
    lifecycle.new(tmp_project, "work", goal="do the work", criteria="it works")
    lifecycle.sign(tmp_project, "work")
    lifecycle.report(tmp_project, "work", body="did the work")
    lifecycle.proof(tmp_project, "work", body="here is the evidence")
    lifecycle.accept(tmp_project, "work")
    _write_delta(entry, "the durable truth of this arc")
    strip_placeholders(entry / "arc.md", model.contract_path(entry))

    lifecycle.close(tmp_project, "work")

    code, reasons = gate.decide(tmp_project)
    assert code == 0, reasons
    # the baseline now equals the post-close reality (no standing drift)
    assert reality.parse_baseline(tmp_project) == reality.reality_rev(tmp_project)


def test_gate_no_self_drift_when_canon_covers_matches_canon_itself(tmp_project):
    """FOOTGUN: a ``canon-covers`` glob that matches CANON.md itself (e.g. ``**/*.md``)
    must NOT create a false standing-drift loop — CANON.md is excluded from its own
    reality fingerprint, so stamping the baseline at merge never re-trips the gate.
    """
    from tide.cannon import reality

    _filled_canon(tmp_project)
    _write_state_covers(tmp_project, ["**/*.md"])  # matches CANON.md + every other .md
    (tmp_project / "notes.md").write_text("hello\n", encoding="utf-8")

    arc_dir = paths.arcs_dir(tmp_project) / "01-x"
    arc_dir.mkdir(parents=True, exist_ok=True)
    _write_delta(arc_dir, "did the work")
    merge.merge_delta(tmp_project, arc_dir, slug="x", date="2026-06-25")

    # The baseline was stamped INTO CANON.md by the merge; the gate must stay clean
    # (no prose-stale loop) because CANON.md is not part of its own fingerprint.
    code, reasons = gate.decide(tmp_project)
    assert code == 0, reasons
    assert not any("canon prose may be stale" in r for r in reasons)

    # Idempotent re-stamp at the same reality → no churn.
    rr = reality.parse_baseline(tmp_project)
    reality.stamp_canon_baseline(tmp_project)
    assert reality.parse_baseline(tmp_project) == rr

"""U2 unit — cannon.merge: append delta under journal, create header, append-only."""

from __future__ import annotations

from tide import fields, paths
from tide.cannon import merge, rev, store

CANON_WITH_JOURNAL = (
    "# CANON.md — demo\n\n"
    "## What it is\nthe truth\n\n"
    "## Cannon journal\n"
)

CANON_NO_JOURNAL = (
    "# CANON.md — demo\n\n"
    "## What it is\nthe truth\n"
)


def test_has_journal_detects_header():
    assert merge.has_journal(CANON_WITH_JOURNAL) is True
    assert merge.has_journal(CANON_NO_JOURNAL) is False


def test_merge_appends_stamped_entry_under_journal():
    out = merge.merge_delta_text(
        CANON_WITH_JOURNAL, "added X", date="2026-06-25", slug="fix-leak"
    )
    assert "### 2026-06-25 · fix-leak" in out
    assert "added X" in out
    # entry sits after the journal header
    assert out.index("## Cannon journal") < out.index("### 2026-06-25 · fix-leak")


def test_merge_creates_header_when_missing():
    out = merge.merge_delta_text(
        CANON_NO_JOURNAL, "added X", date="2026-06-25", slug="fix-leak"
    )
    assert "## Cannon journal" in out
    assert out.index("## What it is") < out.index("## Cannon journal")
    assert out.index("## Cannon journal") < out.index("### 2026-06-25 · fix-leak")


def test_merge_is_append_only():
    once = merge.merge_delta_text(
        CANON_WITH_JOURNAL, "first delta", date="2026-06-25", slug="a1"
    )
    twice = merge.merge_delta_text(once, "second delta", date="2026-06-26", slug="a2")
    # the first entry is untouched and still precedes the second
    assert "### 2026-06-25 · a1" in twice
    assert "first delta" in twice
    assert twice.index("### 2026-06-25 · a1") < twice.index("### 2026-06-26 · a2")
    assert twice.index("first delta") < twice.index("second delta")


def test_merge_empty_delta_still_stamps():
    out = merge.merge_delta_text(CANON_WITH_JOURNAL, "", date="2026-06-25", slug="empty")
    assert "### 2026-06-25 · empty" in out


def test_merge_keeps_single_trailing_newline():
    out = merge.merge_delta_text(
        CANON_WITH_JOURNAL, "body", date="2026-06-25", slug="s"
    )
    assert out.endswith("\n")
    assert not out.endswith("\n\n")


def test_merge_keeps_journal_last_and_chronological():
    # F2: the journal is the canonical FINAL section; a non-canonical sibling that
    # happened to sit after it is preserved but lands BEFORE the journal on re-emit.
    canon = (
        "# CANON.md — demo\n\n"
        "## Cannon journal\n\n"
        "### 2026-06-01 · old\nold body\n\n"
        "## Changelog\nstuff\n"
    )
    out = merge.merge_delta_text(canon, "new body", date="2026-06-25", slug="new")
    # the journal is forced to be the last section
    assert out.index("## Changelog") < out.index("## Cannon journal")
    # prior entry preserved + new entry appended chronologically after it
    assert out.index("### 2026-06-01 · old") < out.index("### 2026-06-25 · new")
    assert "old body" in out and "stuff" in out


def test_canonical_delta_section_fills_top_not_journal():
    # F2 core: a delta carrying canonical ## sections routes them into the SINGLE
    # top heading (filling the empty seed), NOT a dump under the journal.
    canon = merge.store.canon_template("demo")  # seed: empty canonical sections
    delta = (
        "## What it is\n"
        "a brass idle clicker\n\n"
        "## State & components\n"
        "- 12 py files\n\n"
        "## Interfaces / how used\n"
        "tide play\n"
    )
    out = merge.merge_delta_text(delta_body=delta, canon_text=canon,
                                 date="2026-06-25", slug="seed")
    sections = store.scan_text(out)
    # canonical sections are now FILLED at the top
    assert sections["What it is"].strip() == "a brass idle clicker"
    assert "12 py files" in sections["State & components"]
    assert sections["Interfaces / how used"].strip() == "tide play"
    # exactly ONE of each canonical top header — no duplicates
    for title in ("## What it is", "## State & components", "## Interfaces / how used"):
        assert out.count(title) == 1
    # the canonical content did NOT leak into the journal as a top-level header
    assert out.count("## Cannon journal") == 1
    assert out.index("### 2026-06-25 · seed") > out.index("## Cannon journal")


def test_canonical_delta_section_appends_within_existing():
    # an already-populated canonical section is appended-within (never replaced
    # blind, never duplicated as a second top header).
    canon = (
        "# CANON.md — demo\n\n"
        "## What it is\noriginal identity\n\n"
        "## State & components\n\n"
        "## Interfaces / how used\n\n"
        "## Cannon journal\n"
    )
    delta = "## What it is\nnow with idle clicking\n"
    out = merge.merge_delta_text(canon, delta, date="2026-06-25", slug="grow")
    sections = store.scan_text(out)
    assert "original identity" in sections["What it is"]
    assert "now with idle clicking" in sections["What it is"]
    assert out.count("## What it is") == 1  # single header, appended within


def test_merge_dedupes_duplicate_top_headers():
    # pre-existing rot: two identical top headers get folded into one on merge.
    canon = (
        "# CANON.md — demo\n\n"
        "## What it is\nfirst\n\n"
        "## What it is\nsecond\n\n"
        "## Cannon journal\n"
    )
    out = merge.merge_delta_text(canon, "note", date="2026-06-25", slug="dedup")
    assert out.count("## What it is") == 1
    sections = store.scan_text(out)
    assert "first" in sections["What it is"]
    assert "second" in sections["What it is"]


def test_non_canonical_delta_section_demoted_into_journal():
    # a non-canonical delta section is kept (chronicle) but demoted so it can't
    # masquerade as a top-level CANON section.
    canon = merge.store.canon_template("demo")
    delta = "## Findings\n- rough edge spotted\n"
    out = merge.merge_delta_text(canon, delta, date="2026-06-25", slug="probe")
    sections = store.scan_text(out)
    # not promoted to a top-level section
    assert "Findings" not in sections
    # demoted heading + content live under the journal stamp
    assert "#### Findings" in out
    assert "rough edge spotted" in out
    assert out.index("### 2026-06-25 · probe") < out.index("#### Findings")


# --- file-level wrapper -----------------------------------------------------

def _make_delta(arc_dir, body):
    arc_dir.mkdir(parents=True, exist_ok=True)
    (arc_dir / "delta.md").write_text(
        "# delta — fix-leak\nmerged: no\n\n{0}\n".format(body), encoding="utf-8"
    )


def test_merge_delta_file_appends_and_bumps_rev(tmp_path):
    store.init(tmp_path, name="demo")
    before = rev.compute(tmp_path)
    arc_dir = paths.arcs_dir(tmp_path) / "03-fix-leak"
    _make_delta(arc_dir, "patched the valve")

    new_rev = merge.merge_delta(tmp_path, arc_dir, slug="fix-leak", date="2026-06-25")

    canon_text = store.read(tmp_path)
    assert "### 2026-06-25 · fix-leak" in canon_text
    assert "patched the valve" in canon_text
    # frontmatter/heading of the delta file is stripped from the journal body
    assert "merged: no" not in canon_text
    # rev bumped + matches recompute
    assert new_rev != before
    assert new_rev == rev.compute(tmp_path)


def test_merge_delta_file_marks_delta_merged(tmp_path):
    store.init(tmp_path, name="demo")
    arc_dir = paths.arcs_dir(tmp_path) / "03-fix-leak"
    _make_delta(arc_dir, "body")
    merge.merge_delta(tmp_path, arc_dir, slug="fix-leak", date="2026-06-25")
    assert fields.read_field(arc_dir / "delta.md", "merged") == "yes"


def test_merge_delta_file_missing_raises(tmp_path):
    store.init(tmp_path, name="demo")
    arc_dir = paths.arcs_dir(tmp_path) / "03-fix-leak"
    arc_dir.mkdir(parents=True)
    import pytest

    with pytest.raises(FileNotFoundError):
        merge.merge_delta(tmp_path, arc_dir, slug="fix-leak")

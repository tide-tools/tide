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


# --- journal dedup + idempotency (regression tests for the dup bug) ----------

def test_journal_stamp_not_duplicated_on_second_merge():
    """Re-merging same delta with same date+slug must not add a second journal entry."""
    canon = store.canon_template("demo")
    delta = "some update"
    once = merge.merge_delta_text(canon, delta, date="2026-06-25", slug="tide-terminal")
    twice = merge.merge_delta_text(once, delta, date="2026-06-25", slug="tide-terminal")
    # stamp appears exactly ONCE even after two calls
    assert twice.count("### 2026-06-25 · tide-terminal") == 1


def test_merge_idempotent_plain_delta():
    """Merging same plain-prose delta twice yields identical CANON text."""
    canon = store.canon_template("demo")
    delta = "launched scoped context"
    once = merge.merge_delta_text(canon, delta, date="2026-06-25", slug="scoped-launch-context")
    twice = merge.merge_delta_text(once, delta, date="2026-06-25", slug="scoped-launch-context")
    assert once == twice


def test_merge_idempotent_with_canonical_sections():
    """Merging same structured delta (canonical ## sections) twice is idempotent."""
    canon = store.canon_template("demo")
    delta = (
        "## What it is\n"
        "tide orchestration machine\n\n"
        "## State & components\n"
        "- cannon/merge.py\n"
    )
    once = merge.merge_delta_text(canon, delta, date="2026-06-25", slug="seed")
    twice = merge.merge_delta_text(once, delta, date="2026-06-25", slug="seed")
    assert once == twice


def test_multiple_different_slugs_all_preserved():
    """Multiple distinct slug entries do NOT get deduped — only exact dup stamps."""
    canon = store.canon_template("demo")
    after_a = merge.merge_delta_text(canon, "body-a", date="2026-06-25", slug="a")
    after_b = merge.merge_delta_text(after_a, "body-b", date="2026-06-26", slug="b")
    # both stamps still present after a third merge with same slug 'a'
    after_a2 = merge.merge_delta_text(after_b, "body-a", date="2026-06-25", slug="a")
    assert after_a2.count("### 2026-06-25 · a") == 1
    assert after_a2.count("### 2026-06-26 · b") == 1


# --- normalize_canon_text (heal function) ------------------------------------

def test_normalize_deduplicates_journal_stamps():
    """normalize_canon_text removes duplicate ### date · slug entries, keeping first."""
    bad_canon = (
        "# CANON.md — demo\n\n"
        "## What it is\n\ncontent\n\n"
        "## Cannon journal\n\n"
        "### 2026-06-01 · tide-terminal\n\nbody\n\n"
        "### 2026-06-01 · tide-terminal\n\nbody\n"   # duplicate
    )
    healed = merge.normalize_canon_text(bad_canon)
    assert healed.count("### 2026-06-01 · tide-terminal") == 1
    assert "body" in healed


def test_normalize_heals_blind_append_empty_sections():
    """normalize_canon_text extracts ## headers buried in journal from old blind-appends.

    Old code blind-appended the full delta (including canonical ## headings) under
    ## Cannon journal, leaving top sections empty.  normalize re-routes them.
    """
    bad_canon = (
        "# CANON.md — demo\n\n"
        "## What it is\n\n"            # ← empty (template never filled)
        "## State & components\n\n"    # ← empty
        "## Interfaces / how used\n\n" # ← empty
        "## Cannon journal\n\n"
        "### 2026-06-01 · tide-terminal\n\n"
        "## What it is\n\n"            # ← content buried in journal by old blind-append
        "tide orchestration machine\n\n"
        "## State & components\n\n"
        "- 12 py files\n"
    )
    healed = merge.normalize_canon_text(bad_canon)
    sections = store.scan_text(healed)
    assert "tide orchestration machine" in sections["What it is"]
    assert "12 py files" in sections["State & components"]
    # exactly one of each top header
    assert healed.count("## What it is") == 1
    assert healed.count("## State & components") == 1


def test_normalize_is_idempotent():
    """Calling normalize_canon_text twice yields same result as calling it once."""
    bad_canon = (
        "# CANON.md — demo\n\n"
        "## What it is\n\ncontent\n\n"
        "## Cannon journal\n\n"
        "### 2026-06-01 · tide-terminal\n\nbody\n\n"
        "### 2026-06-01 · tide-terminal\n\nbody\n"
    )
    once = merge.normalize_canon_text(bad_canon)
    twice = merge.normalize_canon_text(once)
    assert once == twice


def test_normalize_preserves_unique_journal_entries():
    """normalize_canon_text keeps distinct entries intact."""
    canon = (
        "# CANON.md — demo\n\n"
        "## What it is\n\ncontent\n\n"
        "## Cannon journal\n\n"
        "### 2026-06-01 · a\n\nbody-a\n\n"
        "### 2026-06-02 · b\n\nbody-b\n"
    )
    healed = merge.normalize_canon_text(canon)
    assert healed.count("### 2026-06-01 · a") == 1
    assert healed.count("### 2026-06-02 · b") == 1
    assert "body-a" in healed
    assert "body-b" in healed

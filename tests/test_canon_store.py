"""U2 unit — canon.store: init seeds CANON.md + config; read/scan."""

from __future__ import annotations

import pytest

from tide import paths
from tide.canon import store


def test_init_creates_canon_and_config(tmp_path):
    canon = store.init(tmp_path, name="demo")
    assert canon == paths.canon_dir(tmp_path)
    assert paths.canon_file(tmp_path).is_file()
    assert paths.canon_config(tmp_path).is_file()


def test_init_canon_has_all_canonical_sections(tmp_path):
    store.init(tmp_path, name="demo")
    text = store.read(tmp_path)
    assert text.startswith("# CANON.md — demo")
    for title in store.SECTIONS:
        assert "## {0}".format(title) in text
    # the merge anchor must be present and last
    assert "## Canon journal" in text


def test_init_config_is_lang_line(tmp_path):
    store.init(tmp_path, name="demo", lang="en")
    assert paths.canon_config(tmp_path).read_text(encoding="utf-8") == "lang=en\n"


def test_init_defaults_name_to_dir(tmp_path):
    store.init(tmp_path)
    assert store.read(tmp_path).startswith("# CANON.md — {0}".format(tmp_path.resolve().name))


def test_init_is_non_clobbering_by_default(tmp_path):
    store.init(tmp_path, name="demo")
    canon = paths.canon_file(tmp_path)
    canon.write_text("# CANON.md — demo\n\nhand-edited\n", encoding="utf-8")
    store.init(tmp_path, name="demo")  # second init must NOT overwrite
    assert "hand-edited" in canon.read_text(encoding="utf-8")


def test_init_force_overwrites(tmp_path):
    store.init(tmp_path, name="demo")
    canon = paths.canon_file(tmp_path)
    canon.write_text("garbage\n", encoding="utf-8")
    store.init(tmp_path, name="demo", force=True)
    assert canon.read_text(encoding="utf-8").startswith("# CANON.md — demo")


def test_read_raises_when_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        store.read(tmp_path)


def test_scan_splits_sections(tmp_path):
    store.init(tmp_path, name="demo")
    canon = paths.canon_file(tmp_path)
    canon.write_text(
        "# CANON.md — demo\n\n"
        "## What it is\nthe truth\n\n"
        "## State & components\na, b\n\n"
        "## Canon journal\n",
        encoding="utf-8",
    )
    sections = store.scan(tmp_path)
    assert sections["What it is"] == "the truth"
    assert sections["State & components"] == "a, b"
    assert sections["Canon journal"] == ""


def test_template_matches_skeleton_shape(tmp_path):
    # store.init must agree byte-for-byte with the hand-built conftest skeleton
    # (header + four H2 sections, blank-line separated, single trailing newline).
    expected = (
        "# CANON.md — demo\n\n"
        "## What it is\n\n"
        "## State & components\n\n"
        "## Interfaces / how used\n\n"
        "## Canon journal\n"
    )
    store.init(tmp_path, name="demo")
    assert store.read(tmp_path) == expected


# --- is_empty_skeleton (shared by doctor + the SessionStart newborn guard) ---

def test_is_empty_skeleton_true_for_a_fresh_init(tmp_path):
    # a just-adopted project's canon: four headings, nothing said yet
    store.init(tmp_path, name="demo")
    assert store.is_empty_skeleton(store.read(tmp_path))


def test_is_empty_skeleton_false_once_any_section_speaks(tmp_path):
    store.init(tmp_path, name="demo")
    canon = store.read(tmp_path).replace(
        "## What it is\n", "## What it is\n\nA thing that does a thing.\n"
    )
    assert not store.is_empty_skeleton(canon)


def test_is_empty_skeleton_ignores_the_journal_anchor(tmp_path):
    # "Canon journal" is the merge anchor and is INTENTIONALLY empty — a canon
    # whose only content is a journal entry is still an empty skeleton…
    store.init(tmp_path, name="demo")
    canon = store.read(tmp_path) + "\n### 2026-07-19 · merged something\n"
    assert store.is_empty_skeleton(canon)


def test_is_empty_skeleton_false_for_text_without_sections(tmp_path):
    # no H2 sections at all → not a skeleton (nothing to be empty)
    assert not store.is_empty_skeleton("# CANON.md — demo\n\njust prose\n")


# --- intent seed at birth (tide adopt --goal) -------------------------------

def test_canon_template_without_intent_is_the_bare_skeleton(tmp_path):
    # The conftest fixture is byte-synced to this shape — an empty intent must
    # not shift a single character of it.
    from tests.conftest import CANON_MD_TEMPLATE

    assert store.canon_template("demo") == CANON_MD_TEMPLATE.format(name="demo")
    assert store.canon_template("demo", intent="") == store.canon_template("demo")


def test_init_with_intent_seeds_the_what_it_is_section(tmp_path):
    store.init(tmp_path, name="demo", intent="A tide board for the factory.")
    sections = store.scan(tmp_path)
    assert sections[store.INTENT_SECTION] == "A tide board for the factory."
    # only that section speaks; the rest are still open
    assert sections["State & components"] == ""


def test_init_with_intent_is_not_an_empty_skeleton(tmp_path):
    store.init(tmp_path, name="demo", intent="A tide board for the factory.")
    assert not store.is_empty_skeleton(store.read(tmp_path))


def test_init_without_intent_stays_an_empty_skeleton(tmp_path):
    # unchanged birth: the newborn guard must keep holding its tongue
    store.init(tmp_path, name="demo")
    assert store.is_empty_skeleton(store.read(tmp_path))


def test_init_intent_does_not_clobber_an_existing_canon(tmp_path):
    store.init(tmp_path, name="demo")
    paths.canon_file(tmp_path).write_text("# hand-written\n", encoding="utf-8")
    store.init(tmp_path, name="demo", intent="late goal")
    assert paths.canon_file(tmp_path).read_text(encoding="utf-8") == "# hand-written\n"


def test_seed_line_collapses_whitespace_and_strips_heading_marks():
    assert store.seed_line("  a goal\n  over lines  ") == "a goal over lines"
    # a goal phrased as a heading must not open a bogus H2 section
    assert store.seed_line("## What it is") == "What it is"


def test_intent_phrased_as_a_heading_does_not_forge_a_section(tmp_path):
    store.init(tmp_path, name="demo", intent="## State & components")
    sections = store.scan(tmp_path)
    assert list(sections) == store.SECTIONS  # no extra / duplicated section
    assert sections[store.INTENT_SECTION] == "State & components"

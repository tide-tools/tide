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

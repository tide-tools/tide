"""U2 unit — canon.rev: deterministic, stable, CANON.md-only content hash."""

from __future__ import annotations

from tide import paths
from tide.canon import rev, store


def test_compute_text_is_deterministic():
    assert rev.compute_text("hello") == rev.compute_text("hello")


def test_compute_text_changes_on_any_byte():
    a = rev.compute_text("the truth")
    b = rev.compute_text("the truth ")  # one trailing space
    assert a != b


def test_compute_text_is_short():
    assert len(rev.compute_text("x")) == rev.REV_LEN


def test_compute_over_file_is_stable(tmp_path):
    store.init(tmp_path, name="demo")
    first = rev.compute(tmp_path)
    second = rev.compute(tmp_path)
    assert first == second
    # matches a direct hash of the file's content (no path/mtime influence)
    assert first == rev.compute_text(store.read(tmp_path))


def test_compute_bumps_when_canon_changes(tmp_path):
    store.init(tmp_path, name="demo")
    before = rev.compute(tmp_path)
    canon = paths.canon_file(tmp_path)
    canon.write_text(canon.read_text(encoding="utf-8") + "\nmoved\n", encoding="utf-8")
    assert rev.compute(tmp_path) != before


def test_rev_ignores_config_and_other_canon_files(tmp_path):
    # Decision: hash CANON.md ONLY — config/notes tweaks must NOT bump the rev.
    store.init(tmp_path, name="demo")
    before = rev.compute(tmp_path)
    paths.canon_config(tmp_path).write_text("lang=ru\n", encoding="utf-8")
    (paths.canon_dir(tmp_path) / "notes.md").write_text("scratch\n", encoding="utf-8")
    assert rev.compute(tmp_path) == before


def test_compute_missing_canon_is_empty_hash(tmp_path):
    # No CANON.md yet → stable empty-content hash, never raises.
    assert rev.compute(tmp_path) == rev.compute_text("")

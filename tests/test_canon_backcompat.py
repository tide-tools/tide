"""Back-compat suite: existing on-disk instances (legacy spelling) keep working.

Tests the four back-compat invariants mandated by the cannon → canon rename:

B. Directory: .tide/cannon/ (old) is readable; first write migrates it atomically.
C. Stamp field: cannon-rev: in passports is read as canon-rev.
D. Journal heading: ## Cannon journal in CANON.md is parsed on read; merge
   writes ## Canon journal and does not lose existing journal entries.
E. CLI alias: `tide cannon <sub>` routes to the same handler as `tide canon <sub>`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tide import cli, fields, paths
from tide.arc import stream
from tide.canon import merge, rev, store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_legacy_project(tmp_path: Path) -> Path:
    """Set up a .tide/ tree with the OLD .tide/cannon/ spelling on disk."""
    td = tmp_path / ".tide"
    cannon = td / "cannon"
    arcs = td / "arcs"
    state = td / "state"
    for d in (cannon, arcs, arcs / "candidates", state):
        d.mkdir(parents=True, exist_ok=True)
    # Write CANON.md with old journal heading
    (cannon / "CANON.md").write_text(
        "# CANON.md — legacy\n\n"
        "## What it is\n\nThe thing.\n\n"
        "## State & components\n\n"
        "## Interfaces / how used\n\n"
        "## Cannon journal\n",
        encoding="utf-8",
    )
    (cannon / "config").write_text("lang=en\n", encoding="utf-8")
    (state / "strictness").write_text("strict\n", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# B. Directory back-compat
# ---------------------------------------------------------------------------

class TestDirectoryBackCompat:
    def test_canon_dir_resolves_legacy_cannon_dir(self, tmp_path: Path):
        root = _make_legacy_project(tmp_path)
        # canon_dir() must fall back to the legacy .tide/cannon/ when .tide/canon/ absent
        d = paths.canon_dir(root)
        assert d == root / ".tide" / "cannon"
        assert d.is_dir()

    def test_canon_file_resolves_through_legacy_dir(self, tmp_path: Path):
        root = _make_legacy_project(tmp_path)
        assert paths.canon_file(root) == root / ".tide" / "cannon" / "CANON.md"
        assert paths.canon_file(root).is_file()

    def test_canon_config_resolves_through_legacy_dir(self, tmp_path: Path):
        root = _make_legacy_project(tmp_path)
        assert paths.canon_config(root) == root / ".tide" / "cannon" / "config"
        assert paths.canon_config(root).is_file()

    def test_migrate_canon_dir_renames_legacy_to_new(self, tmp_path: Path):
        root = _make_legacy_project(tmp_path)
        old = root / ".tide" / "cannon"
        new = root / ".tide" / "canon"
        assert old.is_dir()
        assert not new.exists()
        migrated = paths.migrate_canon_dir(root)
        assert migrated is True
        assert not old.exists()
        assert new.is_dir()
        # CANON.md preserved under new name
        assert (new / "CANON.md").is_file()

    def test_migrate_canon_dir_noop_when_new_already_exists(self, tmp_path: Path):
        """migrate_canon_dir returns False and leaves dirs untouched when new dir exists."""
        td = tmp_path / ".tide"
        new = td / "canon"
        new.mkdir(parents=True)
        (new / "CANON.md").write_text("new\n", encoding="utf-8")
        result = paths.migrate_canon_dir(tmp_path)
        assert result is False
        assert new.is_dir()

    def test_canon_dir_prefers_new_over_legacy_when_both_exist(self, tmp_path: Path):
        """If somehow both dirs exist, canon_dir() prefers .tide/canon/."""
        td = tmp_path / ".tide"
        for name in ("canon", "cannon"):
            (td / name).mkdir(parents=True)
        assert paths.canon_dir(tmp_path) == td / "canon"


# ---------------------------------------------------------------------------
# C. Stamp field back-compat
# ---------------------------------------------------------------------------

class TestStampFieldBackCompat:
    def test_read_cannon_rev_from_old_doc(self):
        """cannon-rev: in a passport is readable via canon-rev and cannon-rev keys."""
        doc = "# arc\nstatus: active\ncannon-rev: abc123\n"
        assert fields.read_field_text(doc, "cannon-rev") == "abc123"
        assert fields.read_field_text(doc, "canon-rev") == "abc123"

    def test_read_canon_rev_from_new_doc(self):
        """canon-rev: in a passport is readable via both key spellings."""
        doc = "# arc\nstatus: active\ncanon-rev: xyz789\n"
        assert fields.read_field_text(doc, "canon-rev") == "xyz789"
        assert fields.read_field_text(doc, "cannon-rev") == "xyz789"

    def test_set_canon_rev_writes_canonical_key(self):
        """Writing canon-rev always emits the new canonical key, not cannon-rev."""
        doc = "# arc\nstatus: active\n"
        out = fields.set_field_text(doc, "canon-rev", "abc000")
        assert "canon-rev: abc000" in out
        assert "cannon-rev:" not in out

    def test_cannon_rev_alias_writes_canonical_key(self):
        """Writing cannon-rev (old key) also emits canon-rev (canonical form)."""
        doc = "# arc\nstatus: active\ncannon-rev: old\n"
        out = fields.set_field_text(doc, "cannon-rev", "new123")
        # canonical key is written, old key is gone
        assert "canon-rev: new123" in out
        assert "cannon-rev:" not in out

    def test_open_arc_on_legacy_project_stamps_canon_rev(self, tmp_path: Path):
        """Opening an arc on a legacy project stamps canon-rev (new key)."""
        root = _make_legacy_project(tmp_path)
        arc = stream.new_arc(root, "do-thing")
        passport = arc / "arc.md"
        # should stamp with new key
        assert fields.read_field_text(
            passport.read_text(encoding="utf-8"), "canon-rev"
        ) == rev.compute(root)


# ---------------------------------------------------------------------------
# D. Journal heading back-compat
# ---------------------------------------------------------------------------

class TestJournalHeadingBackCompat:
    def test_has_journal_detects_old_heading(self):
        text = "# CANON.md\n\n## Cannon journal\n"
        assert merge.has_journal(text) is True

    def test_has_journal_detects_new_heading(self):
        text = "# CANON.md\n\n## Canon journal\n"
        assert merge.has_journal(text) is True

    def test_has_journal_false_for_neither(self):
        text = "# CANON.md\n\n## What it is\n"
        assert merge.has_journal(text) is False

    def test_merge_reads_old_cannon_journal_entries(self, tmp_path: Path):
        """merge_delta reads existing Cannon journal entries and keeps them."""
        root = _make_legacy_project(tmp_path)
        # Add a legacy journal entry under the old heading
        canon = paths.canon_file(root)
        existing_text = canon.read_text(encoding="utf-8")
        # Already has ## Cannon journal; add an entry
        existing_text += "- old-entry · abc123\n"
        canon.write_text(existing_text, encoding="utf-8")

        # Create an arc + delta to merge
        arc_dir = stream.new_arc(root, "patch")
        delta = arc_dir / "delta.md"
        delta.write_text("# delta — patch\n\nFix the thing.\n", encoding="utf-8")

        merge.merge_delta(root, arc_dir, slug="patch")

        new_text = paths.canon_file(root).read_text(encoding="utf-8")
        # New journal heading is written on emit
        assert "## Canon journal" in new_text
        # Old entry survived
        assert "old-entry" in new_text
        # New merge entry present
        assert "patch" in new_text

    def test_merge_writes_new_canon_journal_heading(self, tmp_path: Path):
        """After merge, heading is always ## Canon journal even if legacy was present."""
        root = _make_legacy_project(tmp_path)
        arc_dir = stream.new_arc(root, "fix")
        delta = arc_dir / "delta.md"
        delta.write_text("# delta — fix\n\nPatched.\n", encoding="utf-8")

        merge.merge_delta(root, arc_dir, slug="fix")
        new_text = paths.canon_file(root).read_text(encoding="utf-8")
        assert "## Canon journal" in new_text
        assert "## Cannon journal" not in new_text


# ---------------------------------------------------------------------------
# E. CLI alias back-compat
# ---------------------------------------------------------------------------

class TestCliAliasBackCompat:
    def test_tide_cannon_init_routes_to_canon(self, tmp_project: Path, monkeypatch):
        """tide cannon init runs as tide canon init (alias routes to same handler)."""
        # Remove CANON.md so init does real work
        paths.canon_file(tmp_project).unlink()
        monkeypatch.chdir(tmp_project)
        monkeypatch.setenv("TIDE_ROLE", "orchestrator")
        rc = cli.main(["cannon", "init"])
        # exits 0 (success) — key is no ImportError/crash and alias is recognized
        assert rc == 0
        assert paths.canon_file(tmp_project).is_file()

    def test_tide_cannon_status_routes_to_canon(self, tmp_project: Path, monkeypatch):
        """tide cannon status runs as tide canon status (alias works)."""
        monkeypatch.chdir(tmp_project)
        monkeypatch.setenv("TIDE_ROLE", "orchestrator")
        rc = cli.main(["cannon", "status"])
        assert rc == 0


# ---------------------------------------------------------------------------
# Fresh init creates new spelling
# ---------------------------------------------------------------------------

class TestFreshInitCreatesNewSpelling:
    def test_fresh_init_creates_dot_tide_canon(self, tmp_project: Path, monkeypatch):
        """store.init on a fresh project creates .tide/canon/ (not .tide/cannon/)."""
        # tmp_project already has .tide/canon/ from the fixture
        assert (tmp_project / ".tide" / "canon").is_dir()
        assert not (tmp_project / ".tide" / "cannon").exists()

    def test_fresh_canon_md_has_new_journal_heading(self, tmp_project: Path):
        """A freshly-seeded CANON.md uses ## Canon journal (not ## Cannon journal)."""
        text = paths.canon_file(tmp_project).read_text(encoding="utf-8")
        assert "## Canon journal" in text
        assert "## Cannon journal" not in text

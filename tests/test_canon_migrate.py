"""`tide canon migrate` — atomic cannon-folder → canon-folder rename + stamp rewrite.

This migration is SEPARATE from the ``.arcs/`` → ``.tide/`` migrator (``migrate-arcs``):
it only renames a project's per-knowledge dir ``.tide/cannon/`` (old spelling) to
``.tide/canon/`` and rewrites the legacy stamps that live INSIDE that dir
(``## Cannon journal`` heading + ``cannon-rev`` field). Tested invariants:

* happy rename — legacy dir renamed, contents preserved, stamps rewritten;
* stamp rewrites — exactly the legacy spellings that occur on real disk;
* idempotent no-op — already on ``.tide/canon/`` → clean "nothing to migrate";
* run-twice safety — a second apply is a no-op;
* LOUD on coexistence — both dirs present → refuse, touch nothing;
* dry-run — reports the plan, changes nothing (and still refuses on coexistence);
* CLI wiring — ``tide canon migrate`` routes through the real parser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tide import cli, paths
from tide.canon import migrate as cm
from tide.canon import store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LEGACY_CANON = (
    "# CANON.md — legacy\n\n"
    "## What it is\n\nThe thing.\n\n"
    "## State & components\n\n"
    "## Interfaces / how used\n\n"
    "## Cannon journal\n\n"
    "### 2026-06-01 · seed\n"
    "- opened at cannon-rev abc123def456\n"
)


def _make_legacy_only(tmp_path: Path) -> Path:
    """A ``.tide/`` whose canon home is the OLD ``.tide/cannon/`` spelling only."""
    cannon = tmp_path / ".tide" / "cannon"
    cannon.mkdir(parents=True)
    (cannon / "CANON.md").write_text(_LEGACY_CANON, encoding="utf-8")
    (cannon / "config").write_text("lang=ru\n", encoding="utf-8")
    return tmp_path


def _make_coexist(tmp_path: Path) -> Path:
    """A ``.tide/`` holding BOTH ``.tide/cannon/`` and ``.tide/canon/`` (ambiguous)."""
    root = _make_legacy_only(tmp_path)
    canon = tmp_path / ".tide" / "canon"
    canon.mkdir(parents=True)
    (canon / "CANON.md").write_text("# CANON.md — new\n\n## Canon journal\n", encoding="utf-8")
    (canon / "config").write_text("lang=en\n", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Unit: stamp rewrite
# ---------------------------------------------------------------------------

class TestRewriteStamps:
    def test_rewrites_journal_heading_and_rev_field(self):
        text = "## Cannon journal\n- at cannon-rev deadbeef\n"
        out, n = cm.rewrite_stamps(text)
        assert "## Canon journal" in out
        assert "canon-rev deadbeef" in out
        assert "Cannon journal" not in out
        assert "cannon-rev" not in out
        assert n == 2

    def test_rewrites_nested_h3_journal_heading(self):
        text = "### Cannon journal\nbody\n"
        out, n = cm.rewrite_stamps(text)
        assert out == "### Canon journal\nbody\n"
        assert n == 1

    def test_clean_text_is_unchanged_noop(self):
        text = "## Canon journal\n- at canon-rev abc\n"
        out, n = cm.rewrite_stamps(text)
        assert out == text
        assert n == 0

    def test_leaves_plain_cannon_word_in_prose_alone(self):
        # The standalone path/word ".tide/cannon" is NOT a stamp — must survive
        # untouched (only the journal heading + cannon-rev field are rewritten).
        text = "see .tide/cannon for the old layout\n"
        out, n = cm.rewrite_stamps(text)
        assert out == text
        assert n == 0


# ---------------------------------------------------------------------------
# Happy path: rename + rewrite
# ---------------------------------------------------------------------------

class TestApplyHappy:
    def test_renames_legacy_dir_to_canon(self, tmp_path: Path):
        root = _make_legacy_only(tmp_path)
        result = cm.apply(cm.plan(root))
        assert result.migrated is True
        assert (root / ".tide" / "canon").is_dir()
        assert not (root / ".tide" / "cannon").exists()

    def test_preserves_dir_contents(self, tmp_path: Path):
        root = _make_legacy_only(tmp_path)
        cm.apply(cm.plan(root))
        assert paths.canon_config(root).read_text(encoding="utf-8") == "lang=ru\n"
        assert paths.canon_file(root).is_file()

    def test_rewrites_journal_heading_in_migrated_canon(self, tmp_path: Path):
        root = _make_legacy_only(tmp_path)
        cm.apply(cm.plan(root))
        text = store.read(root)
        assert "## Canon journal" in text
        assert "Cannon journal" not in text
        assert "cannon-rev" not in text
        assert "canon-rev abc123def456" in text

    def test_result_lists_rewritten_file(self, tmp_path: Path):
        root = _make_legacy_only(tmp_path)
        result = cm.apply(cm.plan(root))
        names = [name for name, _count in result.files_rewritten]
        assert "CANON.md" in names

    def test_no_tmp_litter_left_behind(self, tmp_path: Path):
        root = _make_legacy_only(tmp_path)
        cm.apply(cm.plan(root))
        leftovers = list((root / ".tide" / "canon").glob("*.tmp"))
        assert leftovers == []


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotent:
    def test_no_legacy_dir_is_clean_noop(self, tmp_project: Path):
        # tmp_project already has the modern .tide/canon/ — nothing to migrate.
        result = cm.apply(cm.plan(tmp_project))
        assert result.migrated is False
        assert (tmp_project / ".tide" / "canon").is_dir()

    def test_running_twice_is_safe(self, tmp_path: Path):
        root = _make_legacy_only(tmp_path)
        first = cm.apply(cm.plan(root))
        second = cm.apply(cm.plan(root))
        assert first.migrated is True
        assert second.migrated is False
        assert "## Canon journal" in store.read(root)


# ---------------------------------------------------------------------------
# LOUD on coexistence
# ---------------------------------------------------------------------------

class TestCoexistenceRefusal:
    def test_plan_flags_coexistence(self, tmp_path: Path):
        root = _make_coexist(tmp_path)
        assert cm.plan(root).coexist is True

    def test_apply_refuses_loudly_on_coexistence(self, tmp_path: Path):
        root = _make_coexist(tmp_path)
        with pytest.raises(cm.CanonMigrateError) as exc:
            cm.apply(cm.plan(root))
        msg = str(exc.value)
        assert "cannon" in msg and "canon" in msg
        assert "resolve" in msg.lower() or "by hand" in msg.lower()

    def test_coexistence_touches_nothing(self, tmp_path: Path):
        root = _make_coexist(tmp_path)
        with pytest.raises(cm.CanonMigrateError):
            cm.apply(cm.plan(root))
        # Both dirs and their original contents survive untouched.
        assert (root / ".tide" / "cannon" / "CANON.md").is_file()
        assert (root / ".tide" / "canon" / "CANON.md").read_text(encoding="utf-8") == (
            "# CANON.md — new\n\n## Canon journal\n"
        )


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_plan_reports_without_acting(self, tmp_path: Path):
        root = _make_legacy_only(tmp_path)
        rendered = cm.render_plan(cm.plan(root))
        assert "CANON.md" in rendered
        # Nothing moved.
        assert (root / ".tide" / "cannon").is_dir()
        assert not (root / ".tide" / "canon").exists()


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

class TestCli:
    @pytest.fixture
    def _chdir(self, monkeypatch):
        def _go(root: Path) -> Path:
            monkeypatch.chdir(root)
            return root
        return _go

    def test_cli_migrate_renames(self, tmp_path: Path, _chdir, capsys):
        root = _chdir(_make_legacy_only(tmp_path))
        rc = cli.main(["canon", "migrate"])
        assert rc == 0
        assert (root / ".tide" / "canon").is_dir()
        assert not (root / ".tide" / "cannon").exists()

    def test_cli_migrate_alias_cannon_routes_same(self, tmp_path: Path, _chdir):
        root = _chdir(_make_legacy_only(tmp_path))
        rc = cli.main(["cannon", "migrate"])
        assert rc == 0
        assert (root / ".tide" / "canon").is_dir()

    def test_cli_migrate_dry_run_changes_nothing(self, tmp_path: Path, _chdir, capsys):
        root = _chdir(_make_legacy_only(tmp_path))
        rc = cli.main(["canon", "migrate", "--dry-run"])
        assert rc == 0
        assert (root / ".tide" / "cannon").is_dir()
        assert not (root / ".tide" / "canon").exists()

    def test_cli_migrate_noop_message(self, tmp_project: Path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_project)
        rc = cli.main(["canon", "migrate"])
        assert rc == 0
        assert "nothing to migrate" in capsys.readouterr().out.lower()

    def test_cli_migrate_refuses_coexistence_nonzero(self, tmp_path: Path, _chdir, capsys):
        root = _chdir(_make_coexist(tmp_path))
        rc = cli.main(["canon", "migrate"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "cannon" in err and "canon" in err

"""Tests for ``tide migrate-arcs`` — finishing a legacy ``.arcs/`` → ``.tide/`` migration.

Builds a realistic legacy ``.arcs/`` fixture (open + closed + goal arcs, candidates,
a ``lang=ru`` config, optional ``canon/CANON.md``) and drives the migration end-to-end:
copy, config/lang carry, CANON carry/stub, placeholder-goal flagging, idempotency,
``--dry-run`` (no mutation), conflict refuse/--force, and backup-not-delete.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tide import migrate, paths
from tide.cli import main


# --- fixture builder -------------------------------------------------------

ARC_MD_PLACEHOLDER = """# {name}

goal: <одна строка — что закрывает арка>
status: {status}

## output → пойнтеры
- done
"""

ARC_MD_FILLED = """# {name}

goal: {goal}
status: {status}

## output → пойнтеры
- {goal}
"""

GOAL_MD = """# @{slug}

goal: {goal}
status: active
"""


def _write_arc(arc_dir: Path, *, name: str, status: str = "active", goal: str | None = None) -> None:
    arc_dir.mkdir(parents=True, exist_ok=True)
    (arc_dir / "output").mkdir(exist_ok=True)
    (arc_dir / "output" / "result.md").write_text("done\n", encoding="utf-8")
    if goal is None:
        text = ARC_MD_PLACEHOLDER.format(name=name, status=status)
    else:
        text = ARC_MD_FILLED.format(name=name, status=status, goal=goal)
    (arc_dir / "arc.md").write_text(text, encoding="utf-8")


def build_legacy(
    root: Path,
    *,
    lang: str = "ru",
    rules: str = "subagents",
    with_canon: bool = False,
) -> Path:
    """Build a legacy ``.arcs/`` tree under *root*; return the ``.arcs`` path.

    Layout: one filled open arc, one closed (``__…__``) arc with a placeholder goal,
    one goal (``NN-@slug``) with a nested sub-arc, two candidates, a multi-line config
    (``lang=… / rules=…``), and optionally a ``canon/CANON.md``.
    """
    arcs = root / ".arcs"
    (arcs / "arcs").mkdir(parents=True, exist_ok=True)
    (arcs / "candidates").mkdir(parents=True, exist_ok=True)

    # open arc with a real goal
    _write_arc(arcs / "arcs" / "01-real-work", name="01-real-work", goal="ship the thing")
    # closed arc with a placeholder goal (old .arcs left it unfilled)
    _write_arc(arcs / "arcs" / "__02-stale__", name="02-stale", status="done")
    # a goal with a nested sub-arc (the sub-arc goal is also a placeholder)
    goal_dir = arcs / "arcs" / "03-@bigger-goal"
    (goal_dir / "output").mkdir(parents=True, exist_ok=True)
    (goal_dir / "output" / "g.md").write_text("g\n", encoding="utf-8")
    (goal_dir / "bigger-goal-goal.md").write_text(
        GOAL_MD.format(slug="bigger-goal", goal="the standing purpose"), encoding="utf-8"
    )
    _write_arc(goal_dir / "arcs" / "01-substep", name="01-substep", status="done")

    # candidates
    (arcs / "candidates" / "01-an-idea.md").write_text(
        "# 01-an-idea\n\nfrom: 01-real-work\n\na surfaced idea\n", encoding="utf-8"
    )
    (arcs / "candidates" / "02-another.md").write_text(
        "# 02-another\n\nfrom: -\n\nanother idea\n", encoding="utf-8"
    )

    # config (multi-line, lang first)
    (arcs / "config").write_text("lang={0}\nrules={1}\n".format(lang, rules), encoding="utf-8")

    if with_canon:
        (arcs / "canon").mkdir(parents=True, exist_ok=True)
        (arcs / "canon" / "CANON.md").write_text(
            "# CANON.md — legacy\n\n## Где мы сейчас\n- нить: real legacy truth\n", encoding="utf-8"
        )

    return arcs


# --- criterion 1 + 6: copy arcs (open + closed + goals) + candidates --------

def test_migrate_copies_open_closed_and_goal_arcs(tmp_path: Path):
    build_legacy(tmp_path)
    plan = migrate.plan_migration(tmp_path)
    migrate.apply_migration(plan)

    dst = paths.arcs_dir(tmp_path)
    assert (dst / "01-real-work" / "arc.md").is_file()        # open arc
    assert (dst / "__02-stale__" / "arc.md").is_file()        # closed arc
    assert (dst / "03-@bigger-goal" / "bigger-goal-goal.md").is_file()  # goal
    assert (dst / "03-@bigger-goal" / "arcs" / "01-substep" / "arc.md").is_file()  # nested sub-arc


def test_migrate_copies_candidates(tmp_path: Path):
    build_legacy(tmp_path)
    migrate.apply_migration(migrate.plan_migration(tmp_path))

    cands = paths.candidates_dir(tmp_path)
    assert (cands / "01-an-idea.md").is_file()
    assert (cands / "02-another.md").is_file()


# --- criterion 4: config / lang carry (the override-default case) -----------

def test_migrate_carries_lang_overriding_default(tmp_path: Path):
    build_legacy(tmp_path, lang="ru", rules="subagents")
    migrate.apply_migration(migrate.plan_migration(tmp_path))

    cfg = paths.cannon_config(tmp_path).read_text(encoding="utf-8")
    # the real project lang (ru) survived — NOT reset to tide's default (en)
    assert "lang=ru" in cfg
    assert "lang=en" not in cfg
    # the rest of the legacy config (rules=…) was carried too
    assert "rules=subagents" in cfg


# --- criterion 5 / CANON handling ------------------------------------------

def test_migrate_carries_legacy_canon(tmp_path: Path):
    build_legacy(tmp_path, with_canon=True)
    migrate.apply_migration(migrate.plan_migration(tmp_path))

    canon = paths.canon_file(tmp_path).read_text(encoding="utf-8")
    assert "real legacy truth" in canon


def test_migrate_scaffolds_canon_stub_when_no_legacy_canon(tmp_path: Path):
    build_legacy(tmp_path, with_canon=False)
    migrate.apply_migration(migrate.plan_migration(tmp_path))

    canon = paths.canon_file(tmp_path).read_text(encoding="utf-8")
    assert "## Где мы сейчас" in canon
    # the merge anchor must survive so the migrated project can still merge deltas
    assert "## Cannon journal" in canon


def test_migrate_does_not_clobber_a_real_pretide_canon(tmp_path: Path):
    # .tide already exists with a real CANON, AND legacy carries one too → keep the real .tide CANON.
    from tide.cannon import store

    paths.cannon_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    store.init(tmp_path, name="demo")
    paths.canon_file(tmp_path).write_text("# CANON.md — demo\n\nREAL PRE-TIDE CANON\n", encoding="utf-8")
    build_legacy(tmp_path, with_canon=True)

    migrate.apply_migration(migrate.plan_migration(tmp_path))
    canon = paths.canon_file(tmp_path).read_text(encoding="utf-8")
    assert "REAL PRE-TIDE CANON" in canon
    assert "real legacy truth" not in canon


# --- criterion 3: placeholder-goal flagging ---------------------------------

def test_migrate_flags_placeholder_goals(tmp_path: Path):
    build_legacy(tmp_path)
    result = migrate.apply_migration(migrate.plan_migration(tmp_path))

    flagged = set(result.placeholder_goals)
    assert "__02-stale__" in flagged                      # closed arc w/ placeholder goal
    assert "03-@bigger-goal/01-substep" in flagged        # nested sub-arc placeholder
    assert "01-real-work" not in flagged                  # filled goal is NOT flagged


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, True),
        ("", True),
        ("   ", True),
        ("<one line — what this arc closes>", True),
        ("<одна строка — что закрывает арка>", True),
        ("no goal yet", True),
        ("ship the feature", False),
        ("fix the <thing> bug", False),  # partial angle span mid-string is a real goal
    ],
)
def test_goal_is_placeholder(value, expected):
    assert migrate.goal_is_placeholder(value) is expected


# --- criterion 6: backup is a rename, never a delete ------------------------

def test_migrate_backs_up_arcs_never_deletes(tmp_path: Path):
    build_legacy(tmp_path)
    migrate.apply_migration(migrate.plan_migration(tmp_path))

    assert not (tmp_path / ".arcs").exists()              # original moved away
    assert (tmp_path / ".arcs.pre-tide-bak").is_dir()     # preserved under the backup name
    # the backup still holds the original content (proof nothing was deleted)
    assert (tmp_path / ".arcs.pre-tide-bak" / "arcs" / "01-real-work" / "arc.md").is_file()


def test_migrate_refuses_to_clobber_existing_backup(tmp_path: Path):
    build_legacy(tmp_path)
    (tmp_path / ".arcs.pre-tide-bak").mkdir()
    with pytest.raises(migrate.MigrateError, match="backup .* already exists"):
        migrate.apply_migration(migrate.plan_migration(tmp_path))
    # nothing moved — legacy still there
    assert (tmp_path / ".arcs").is_dir()


# --- criterion 2: idempotency -----------------------------------------------

def test_migrate_is_idempotent_second_run_is_a_noop(tmp_path: Path, capsys):
    build_legacy(tmp_path)
    migrate.apply_migration(migrate.plan_migration(tmp_path))

    # second run via the CLI: legacy is gone, backup present → reported as already migrated, no error.
    rc = main(["migrate-arcs", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "already migrated" in out


def test_replanning_after_partial_copy_detects_conflicts(tmp_path: Path):
    build_legacy(tmp_path)
    # simulate a prior partial migration: one arc already in .tide/arcs/
    dst = paths.arcs_dir(tmp_path)
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "01-real-work").mkdir()

    plan = migrate.plan_migration(tmp_path)
    assert "01-real-work" in plan.arc_conflicts
    assert plan.has_conflicts
    # refuse without --force, no mutation
    with pytest.raises(migrate.MigrateError, match="would collide"):
        migrate.apply_migration(plan, force=False)
    assert (tmp_path / ".arcs").is_dir()  # untouched


def test_force_skips_conflicts_and_proceeds(tmp_path: Path):
    build_legacy(tmp_path)
    dst = paths.arcs_dir(tmp_path)
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "01-real-work").mkdir()
    (dst / "01-real-work" / "marker.txt").write_text("pre-existing\n", encoding="utf-8")

    plan = migrate.plan_migration(tmp_path)
    result = migrate.apply_migration(plan, force=True)

    # conflicting arc kept as-is (not clobbered), the rest copied
    assert (dst / "01-real-work" / "marker.txt").is_file()
    assert "01-real-work" in result.arcs_skipped
    assert "__02-stale__" in result.arcs_copied
    assert (tmp_path / ".arcs.pre-tide-bak").is_dir()


# --- criterion 2: --dry-run mutates nothing ---------------------------------

def test_dry_run_changes_nothing(tmp_path: Path, capsys):
    build_legacy(tmp_path)
    rc = main(["migrate-arcs", str(tmp_path), "--dry-run"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "migrate-arcs plan" in out
    # NOT mutated: no .tide/, legacy still present, no backup
    assert not paths.tide_dir(tmp_path).exists()
    assert (tmp_path / ".arcs").is_dir()
    assert not (tmp_path / ".arcs.pre-tide-bak").exists()
    # plan surfaces the placeholder-goal count
    assert "placeholder goals:" in out


# --- criterion 5: verification (cold-entry check) ---------------------------

def test_verify_after_migration_resolves(tmp_path: Path):
    build_legacy(tmp_path)
    migrate.apply_migration(migrate.plan_migration(tmp_path))

    ok, lines = migrate.verify_migration(tmp_path)
    assert ok is True
    joined = "\n".join(lines)
    assert "context show: resolves" in joined
    assert "arc status: renders" in joined
    # 3 top-level arcs (01, __02__, 03-@goal) + 2 candidates surfaced
    assert "3 arc(s), 2 candidate(s)" in joined


def test_cli_migrate_runs_end_to_end_and_verifies(tmp_path: Path, capsys):
    build_legacy(tmp_path)
    rc = main(["migrate-arcs", str(tmp_path)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "migrate-arcs: done" in out
    assert "verify (ok)" in out
    # placeholder goals surfaced for backfill
    assert "placeholder goal" in out
    assert paths.tide_dir(tmp_path).is_dir()


def test_cli_no_legacy_is_graceful(tmp_path: Path, capsys):
    # a dir with neither .arcs nor a backup → nothing to migrate, exit 0
    rc = main(["migrate-arcs", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "nothing to migrate" in out


# --- command registration ---------------------------------------------------

def test_migrate_arcs_is_registered():
    from tide.cli import build_parser

    parser = build_parser()
    # argparse exposes subcommands via the subparsers action choices
    sub = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
    assert "migrate-arcs" in sub.choices

"""tide.migrate — finish a legacy ``.arcs/`` → ``.tide/`` migration in one command.

Across the roster many projects still carry the LEGACY arcs layout

    .arcs/
      arcs/        NN-<slug>/ work stream (open AND closed __…__ entries, goals)
      candidates/  NN-<slug>.md backlog
      config       lang=… [rules=… plugins=…]
      canon/CANON.md   (sometimes — older projects never had one)

but tide's entry (``tide context`` / ``tide go`` / the board) reads ``.tide/``, so
those projects under-report or fail cold-entry. ``tide migrate-arcs`` replays — as
deterministic code — the hand-validated recipe a human ran on one real project:

1. scaffold ``.tide/`` (``tide init --project``) when it is not there yet;
2. copy ``.arcs/arcs/.`` → ``.tide/arcs/`` (open + closed + goals);
3. copy ``.arcs/candidates/.`` → ``.tide/arcs/candidates/``;
4. carry ``.arcs/config`` → ``.tide/cannon/config`` — OVERRIDING the default ``lang``
   that ``tide init`` wrote (the project's real ``lang=ru`` must survive);
5. CANON.md: carry ``.arcs/canon/CANON.md`` when present (never clobbering a real
   ``.tide`` CANON that already existed), else scaffold a ``## Где мы сейчас`` stub;
6. back up — ``mv .arcs .arcs.pre-tide-bak`` — NEVER delete.

Everything is plan-then-apply: :func:`plan_migration` is a pure read that returns a
:class:`MigratePlan`; :func:`apply_migration` is the only mutator. ``--dry-run``
prints the plan and changes nothing; conflicting ``.tide/arcs/`` entries refuse the
run (``--force`` skips them, never clobbers). Post-migration we scan for unfilled
placeholder goals (old ``.arcs`` left many empty) and verify cold-entry resolves.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from . import fields, paths
from .arc.stream import StreamError
from .init_home import scaffold_project

# --- legacy on-disk names (single source of truth) -------------------------
LEGACY_DIRNAME = ".arcs"
BACKUP_DIRNAME = ".arcs.pre-tide-bak"
LEGACY_ARCS = "arcs"
LEGACY_CANDIDATES = "candidates"
LEGACY_CONFIG = "config"
LEGACY_CANON = ("canon", "CANON.md")


class MigrateError(StreamError):
    """A migrate-arcs error (no legacy dir, backup collision, conflicts sans --force).

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on the
    same ``except`` arm (prints ``tide: …`` and exits nonzero).
    """


# --- placeholder-goal detection --------------------------------------------

def goal_is_placeholder(goal_value: Optional[str]) -> bool:
    """True when an arc's ``goal:`` is empty or still the scaffold placeholder.

    Old ``.arcs`` arcs were often opened without ever filling the goal: a missing
    ``goal:`` line (None), an empty value, the literal angle-bracket span
    (``<one line …>`` / ``<одна строка …>``), or a bare "no goal yet" all count —
    so the migration can REPORT them for the human to backfill.
    """
    if goal_value is None:
        return True
    v = goal_value.strip()
    if not v:
        return True
    if v.startswith("<") and v.endswith(">"):
        return True
    if "no goal yet" in v.lower():
        return True
    return False


def _passport_in(entry_dir: Path) -> Optional[Path]:
    """The status-bearing doc in a legacy entry dir (goal doc if present, else arc.md)."""
    goals = sorted(Path(entry_dir).glob("*-goal.md"))
    if goals:
        return goals[-1]
    arc_md = Path(entry_dir) / "arc.md"
    return arc_md if arc_md.is_file() else None


def scan_placeholder_goals(arcs_dir: Path) -> List[str]:
    """Names of entries under *arcs_dir* whose goal is empty/placeholder, in order.

    Descends one level into a goal's ``arcs/`` substream (reported as
    ``goal/sub-arc``), mirroring the stream model. Pure read — used both to PREDICT
    on ``--dry-run`` (scanning the legacy source) and to REPORT after the copy.
    """
    arcs_dir = Path(arcs_dir)
    out: List[str] = []
    if not arcs_dir.is_dir():
        return out
    for entry in sorted(arcs_dir.iterdir()):
        if not entry.is_dir() or entry.name == LEGACY_CANDIDATES:
            continue
        pp = _passport_in(entry)
        if pp is not None and goal_is_placeholder(fields.read_field(pp, "goal")):
            out.append(entry.name)
        sub = entry / LEGACY_ARCS
        if sub.is_dir():
            for child in sorted(sub.iterdir()):
                if not child.is_dir():
                    continue
                cpp = _passport_in(child)
                if cpp is not None and goal_is_placeholder(fields.read_field(cpp, "goal")):
                    out.append("{0}/{1}".format(entry.name, child.name))
    return out


# --- CANON stub (when no legacy CANON.md exists to carry) -------------------

def canon_stub_text(name: str) -> str:
    """A real ``## Где мы сейчас`` CANON stub (thread + remotes + release placeholders).

    Keeps the canonical English H2 sections — crucially the append-only
    ``## Cannon journal`` anchor :mod:`tide.cannon.merge` writes into — so a migrated
    project can still merge deltas, and adds the user's ``## Где мы сейчас`` thread
    section the roster's CANON convention expects on entry.
    """
    return "\n".join(
        [
            "# CANON.md — {0}".format(name),
            "",
            "## Где мы сейчас",
            "- нить: <одна строка — что сейчас активно>",
            "- remotes: <ссылки / PR / доски — опционально>",
            "- release: <последняя веха / релиз — опционально>",
            "",
            "## What it is",
            "",
            "## State & components",
            "",
            "## Interfaces / how used",
            "",
            "## Cannon journal",
            "",
        ]
    )


# --- the plan --------------------------------------------------------------

@dataclass
class MigratePlan:
    """A pure, immutable description of what a migration WOULD do (no mutation yet)."""

    root: Path
    legacy: Path
    backup: Path
    tide_existed: bool
    arc_copies: List[Tuple[Path, Path]] = field(default_factory=list)
    arc_conflicts: List[str] = field(default_factory=list)
    candidate_copies: List[Tuple[Path, Path]] = field(default_factory=list)
    candidate_conflicts: List[str] = field(default_factory=list)
    config_carry: Optional[Tuple[Path, Path]] = None
    canon_carry: Optional[Tuple[Path, Path]] = None
    canon_stub: bool = False
    canon_action: str = ""
    placeholder_goals: List[str] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        return bool(self.arc_conflicts or self.candidate_conflicts)


def plan_migration(root: Path) -> MigratePlan:
    """Compute the deterministic migration plan for *root* (pure — no disk writes).

    Requires a legacy ``.arcs/`` at *root* (raises :class:`MigrateError` otherwise).
    Captures the pre-migration state (does ``.tide`` / its CANON already exist?) so
    :func:`apply_migration` can carry a legacy CANON over a freshly-scaffolded one
    yet never clobber a CANON that was already real.
    """
    root = Path(root)
    legacy = root / LEGACY_DIRNAME
    if not legacy.is_dir():
        raise MigrateError(
            "no legacy {0}/ at {1} — nothing to migrate".format(LEGACY_DIRNAME, root)
        )

    tide_existed = paths.tide_dir(root).is_dir()
    canon_preexisted = paths.canon_file(root).is_file()

    plan = MigratePlan(
        root=root,
        legacy=legacy,
        backup=root / BACKUP_DIRNAME,
        tide_existed=tide_existed,
    )

    # 2. arcs (open + closed + goals) — conflict when the target name already exists.
    src_arcs = legacy / LEGACY_ARCS
    if src_arcs.is_dir():
        dst_arcs = paths.arcs_dir(root)
        for child in sorted(src_arcs.iterdir()):
            if not child.is_dir() or child.name == LEGACY_CANDIDATES:
                continue
            dst = dst_arcs / child.name
            if dst.exists():
                plan.arc_conflicts.append(child.name)
            else:
                plan.arc_copies.append((child, dst))

    # 3. candidates.
    src_cands = legacy / LEGACY_CANDIDATES
    if src_cands.is_dir():
        dst_cands = paths.candidates_dir(root)
        for child in sorted(src_cands.iterdir()):
            if not child.is_file():
                continue
            dst = dst_cands / child.name
            if dst.exists():
                plan.candidate_conflicts.append(child.name)
            else:
                plan.candidate_copies.append((child, dst))

    # 4. config carry (overrides the default lang tide init wrote).
    src_cfg = legacy / LEGACY_CONFIG
    if src_cfg.is_file():
        plan.config_carry = (src_cfg, paths.cannon_config(root))

    # 5. CANON.md.
    legacy_canon = legacy.joinpath(*LEGACY_CANON)
    target_canon = paths.canon_file(root)
    if legacy_canon.is_file():
        if canon_preexisted:
            plan.canon_action = (
                "skip — real .tide/cannon/CANON.md already present (not overwritten)"
            )
        else:
            plan.canon_carry = (legacy_canon, target_canon)
            plan.canon_action = "carry .arcs/canon/CANON.md → .tide/cannon/CANON.md"
    else:
        if canon_preexisted:
            plan.canon_action = "keep existing .tide/cannon/CANON.md"
        else:
            plan.canon_stub = True
            plan.canon_action = "scaffold a '## Где мы сейчас' CANON stub"

    # placeholder-goal scan (predicted from the legacy source — same content copied).
    plan.placeholder_goals = scan_placeholder_goals(src_arcs)
    return plan


# --- apply -----------------------------------------------------------------

@dataclass
class MigrateResult:
    """What :func:`apply_migration` actually did (for the CLI summary)."""

    initialized: bool
    arcs_copied: List[str] = field(default_factory=list)
    arcs_skipped: List[str] = field(default_factory=list)
    candidates_copied: List[str] = field(default_factory=list)
    candidates_skipped: List[str] = field(default_factory=list)
    config_carried: bool = False
    canon_action: str = ""
    backup: Optional[Path] = None
    placeholder_goals: List[str] = field(default_factory=list)


def apply_migration(plan: MigratePlan, force: bool = False) -> MigrateResult:
    """Execute *plan* — the ONLY mutator. Idempotent inputs in, deterministic out.

    Refuses (no mutation) when the backup target already exists, or when the plan
    carries conflicting ``.tide/arcs/`` entries and *force* is not set — never
    clobbers existing work. With *force*, conflicting entries are SKIPPED (the
    existing target is kept), the rest proceeds.
    """
    # Backup collision — never overwrite a prior backup (and never delete .arcs).
    if plan.backup.exists():
        raise MigrateError(
            "backup {0} already exists — refusing to overwrite it "
            "(resolve the previous migration first)".format(plan.backup)
        )

    if plan.has_conflicts and not force:
        raise MigrateError(_conflict_message(plan))

    root = plan.root

    # 1. scaffold .tide/ (non-destructive: existing CANON/config/state survive).
    scaffold_project(root)

    # 2. arcs.
    result = MigrateResult(initialized=not plan.tide_existed)
    for src, dst in plan.arc_copies:
        shutil.copytree(src, dst)
        result.arcs_copied.append(dst.name)
    result.arcs_skipped = list(plan.arc_conflicts)  # force ⇒ kept-as-is

    # 3. candidates.
    dst_cands = paths.candidates_dir(root)
    dst_cands.mkdir(parents=True, exist_ok=True)
    for src, dst in plan.candidate_copies:
        shutil.copy2(src, dst)
        result.candidates_copied.append(dst.name)
    result.candidates_skipped = list(plan.candidate_conflicts)

    # 4. config carry — OVERRIDE the default lang tide init wrote.
    if plan.config_carry is not None:
        src_cfg, dst_cfg = plan.config_carry
        dst_cfg.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_cfg, dst_cfg)
        result.config_carried = True

    # 5. CANON.md.
    if plan.canon_carry is not None:
        src_canon, dst_canon = plan.canon_carry
        dst_canon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_canon, dst_canon)
    elif plan.canon_stub:
        target = paths.canon_file(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(canon_stub_text(root.resolve().name), encoding="utf-8")
    result.canon_action = plan.canon_action

    # 6. backup — mv .arcs .arcs.pre-tide-bak (NEVER rm).
    shutil.move(str(plan.legacy), str(plan.backup))
    result.backup = plan.backup

    result.placeholder_goals = list(plan.placeholder_goals)
    return result


def _conflict_message(plan: MigratePlan) -> str:
    """The refuse-on-conflict message listing every colliding arc/candidate."""
    lines = [
        "cannot migrate {0}: .tide/ already holds entries that would collide "
        "(override to skip them: migrate-arcs --force):".format(plan.root)
    ]
    for name in plan.arc_conflicts:
        lines.append("  arc      .tide/arcs/{0} (already present)".format(name))
    for name in plan.candidate_conflicts:
        lines.append("  candidate .tide/arcs/candidates/{0} (already present)".format(name))
    return "\n".join(lines)


# --- verification (cold-entry check) ---------------------------------------

def verify_migration(root: Path) -> Tuple[bool, List[str]]:
    """Confirm a migrated ``.tide/`` resolves cleanly on cold entry.

    Replays the checks a fresh session relies on: ``tide context show``
    (:func:`tide.launcher.context.render_enter`) and the board
    (:func:`tide.arc.board.render_board`) both render without raising, and reports
    how many arcs + candidates surface. Returns ``(ok, lines)`` — ``ok`` is False if
    any check raised. Pure read (no mutation).
    """
    from .arc import board, candidate
    from .launcher import context

    root = Path(root)
    lines: List[str] = []
    ok = True

    try:
        context.render_enter(root)
        lines.append("context show: resolves")
    except Exception as exc:  # noqa: BLE001 — verification reports, never crashes
        ok = False
        lines.append("context show: FAILED — {0}".format(exc))

    try:
        board.render_board(root)
        lines.append("arc status: renders")
    except Exception as exc:  # noqa: BLE001
        ok = False
        lines.append("arc status: FAILED — {0}".format(exc))

    arcs_dir = paths.arcs_dir(root)
    n_arcs = 0
    if arcs_dir.is_dir():
        n_arcs = sum(
            1
            for p in arcs_dir.iterdir()
            if p.is_dir() and p.name != paths.CANDIDATES_DIRNAME
        )
    n_cands = len(candidate.list_candidates(root))
    lines.append("surfaced: {0} arc(s), {1} candidate(s)".format(n_arcs, n_cands))
    return ok, lines


# --- rendering -------------------------------------------------------------

def render_plan(plan: MigratePlan) -> str:
    """Human-readable ``--dry-run`` view: every move, the config/CANON action, flags."""
    lines = ["migrate-arcs plan — {0}".format(plan.root)]
    lines.append("  init .tide/:   {0}".format("yes (scaffold)" if not plan.tide_existed else "already present"))

    lines.append("  arcs → .tide/arcs/:")
    if plan.arc_copies:
        for src, dst in plan.arc_copies:
            lines.append("    + {0}".format(dst.name))
    if plan.arc_conflicts:
        for name in plan.arc_conflicts:
            lines.append("    ! {0} (conflict — already present)".format(name))
    if not plan.arc_copies and not plan.arc_conflicts:
        lines.append("    (none)")

    lines.append("  candidates → .tide/arcs/candidates/:")
    if plan.candidate_copies:
        for src, dst in plan.candidate_copies:
            lines.append("    + {0}".format(dst.name))
    if plan.candidate_conflicts:
        for name in plan.candidate_conflicts:
            lines.append("    ! {0} (conflict — already present)".format(name))
    if not plan.candidate_copies and not plan.candidate_conflicts:
        lines.append("    (none)")

    if plan.config_carry is not None:
        lines.append("  config:        carry .arcs/config → .tide/cannon/config (override default lang)")
    else:
        lines.append("  config:        (no legacy .arcs/config)")

    lines.append("  CANON.md:      {0}".format(plan.canon_action))
    lines.append("  backup:        mv .arcs → {0}".format(plan.backup.name))

    lines.append("  placeholder goals: {0}".format(len(plan.placeholder_goals)))
    for name in plan.placeholder_goals:
        lines.append("    ? {0}".format(name))

    if plan.has_conflicts:
        lines.append("  NOTE: conflicts present — run needs --force (skips them) or manual resolve")
    return "\n".join(lines)


def render_result(result: MigrateResult) -> str:
    """Human-readable post-migration summary (what actually happened + flags to backfill)."""
    lines = ["migrate-arcs: done"]
    lines.append(
        "  arcs:        {0} copied{1}".format(
            len(result.arcs_copied),
            ", {0} skipped".format(len(result.arcs_skipped)) if result.arcs_skipped else "",
        )
    )
    lines.append(
        "  candidates:  {0} copied{1}".format(
            len(result.candidates_copied),
            ", {0} skipped".format(len(result.candidates_skipped)) if result.candidates_skipped else "",
        )
    )
    lines.append("  config:      {0}".format("carried (lang preserved)" if result.config_carried else "default kept"))
    lines.append("  CANON.md:    {0}".format(result.canon_action))
    if result.backup is not None:
        lines.append("  backup:      .arcs → {0}".format(result.backup.name))

    if result.placeholder_goals:
        lines.append(
            "  ⚠ {0} arc(s) carry an empty/placeholder goal — backfill them:".format(
                len(result.placeholder_goals)
            )
        )
        for name in result.placeholder_goals:
            lines.append("    ? {0}".format(name))
    else:
        lines.append("  placeholder goals: none")
    return "\n".join(lines)


# --- CLI wiring ------------------------------------------------------------

def _cmd_migrate_arcs(args) -> int:
    root = Path(args.path).resolve() if getattr(args, "path", None) else Path.cwd()
    legacy = root / LEGACY_DIRNAME
    backup = root / BACKUP_DIRNAME

    # Nothing to migrate — distinguish "already migrated" from "never had .arcs".
    if not legacy.is_dir():
        if backup.is_dir():
            print("tide: {0}/ already migrated (backup {1} present)".format(LEGACY_DIRNAME, BACKUP_DIRNAME))
            if getattr(args, "verify", False) and paths.tide_dir(root).is_dir():
                ok, vlines = verify_migration(root)
                _print_verify(ok, vlines)
            return 0
        print("tide: no legacy {0}/ at {1} — nothing to migrate".format(LEGACY_DIRNAME, root))
        return 0

    plan = plan_migration(root)

    if getattr(args, "dry_run", False):
        print(render_plan(plan))
        return 0

    result = apply_migration(plan, force=getattr(args, "force", False))
    print(render_result(result))

    # Post-migration verification — the cold-entry check (always runs).
    ok, vlines = verify_migration(root)
    _print_verify(ok, vlines)
    return 0 if ok else 1


def _print_verify(ok: bool, lines: List[str]) -> None:
    print("verify ({0}):".format("ok" if ok else "FAILED"))
    for line in lines:
        print("  {0}".format(line))


def register(subparsers) -> None:
    """Add the top-level ``migrate-arcs`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "migrate-arcs",
        help="finish a legacy .arcs/ → .tide/ migration (copy arcs+candidates, carry config/CANON, backup)",
    )
    p.add_argument("path", nargs="?", help="project dir to migrate (default: cwd)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the full plan (what moves where) and change nothing",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="proceed despite conflicting .tide/arcs/ entries (skips them, never clobbers)",
    )
    p.add_argument(
        "--verify",
        action="store_true",
        help="run the cold-entry check (also runs automatically after a migration)",
    )
    p.set_defaults(func=_cmd_migrate_arcs, _cmd="migrate-arcs")

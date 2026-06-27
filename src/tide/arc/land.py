"""tide.arc.land — the ATOMIC ``tide arc land`` + ``tide reconcile`` (strictness-gated).

``tide arc land <arc>`` used to do exactly one thing: merge the arc's worktree
branch back to base. This module makes it **atomic** — one act that, in order:

1. **merges** the worktree branch → base (the FILE axis; conflict aborts cleanly);
2. **seals** the arc (rename ``__…__`` + ``status: done``) and, on the strict
   path, **reconciles** its delta into CANON via :func:`tide.contract.lifecycle.close`;
3. **re-stamps** the canon-rev so the just-landed arc shows no self-drift;
4. runs the **gate** (:func:`tide.gate.decide`) and reports the verdict.

Every failure is self-documenting: it prints the EXACT next command the operator
should run, so the head never has to discover the procedure live.

The strictness dial (``strict|loose`` — :mod:`tide.strictness`) now drives ``land``
as well as ``sign``:

* **loose** (the land-axis default — *discipline without slowness*): SKIP the
  report/proof/delta-nonempty reconciliation guards. The arc is sealed immediately,
  the skipped guards are recorded as ``deferred`` on the contract AND appended to
  the debt ledger (:mod:`tide.ledger`); the delta is left unmerged for a later
  ``tide reconcile``. The head dispatches the next arc without waiting.
* **strict**: enforce full reconciliation (a non-empty delta merged + an accepted
  report.md & proof.md) before the seal — the same guard ``contract close`` owns.

``tide reconcile`` (and the ``--strict`` batch form of land) pays down the ledger:
it re-runs the strict land on each owed arc, merging its delta and clearing its
debt. ``land`` and ``reconcile`` accept several arcs in one invocation (batch).

Merging shared truth is orchestrator-only, so the CLI handlers are role-gated
(mirroring ``canon merge`` / ``contract close``); the logic functions stay
gate-free so they are unit-testable.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .. import ledger, paths, slug, strictness
from ..contract import lifecycle, model
from . import stream

STRICT = strictness.STRICT
LOOSE = strictness.LOOSE

# A gate evaluator returns the tri-state ``(code, reasons)`` of tide.gate.decide.
GateFn = Callable[[Path], Tuple[int, List[str]]]


class LandError(stream.StreamError):
    """A land/reconcile failure carrying the operator's exact next step.

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on
    the same arm (prints ``tide: …``, exits nonzero).
    """


# --- outcome ---------------------------------------------------------------

@dataclass
class LandOutcome:
    """The result of one atomic land (pure data the CLI renders).

    * ``arc`` / ``ref`` — the sealed dir name and its bare slug.
    * ``strict`` — which dial the land ran under.
    * ``merged`` — the worktree branch was merged to base (False for non-git / no branch).
    * ``reconciled`` — the delta was merged into CANON (strict path).
    * ``deferred`` — guards skipped + recorded to the ledger (loose path).
    * ``new_rev`` — the canon-rev stamped on the arc after the act.
    * ``gate_code`` / ``gate_reasons`` — the post-land gate verdict (None when skipped).
    """

    arc: str
    ref: str
    strict: bool
    merged: bool
    reconciled: bool
    new_rev: str
    deferred: List[str] = field(default_factory=list)
    gate_code: Optional[int] = None
    gate_reasons: List[str] = field(default_factory=list)


# --- strictness resolution (flag wins, else config; land-axis default = loose) ---

def land_is_strict(
    root: Path, *, strict_flag: bool = False, loose_flag: bool = False
) -> bool:
    """Resolve the land strictness: a flag wins, else the dial, else LOOSE.

    ``--strict``/``--loose`` flags are the per-invocation control. Absent a flag
    the project dial drives it (``tide strictness …``): an *explicit* ``strict``
    lands strict, an *explicit* ``loose`` lands loose, and a project that never set
    the dial lands **loose** — the land-axis default (*discipline without slowness*).
    Note the sign-axis safe default (``strict``) is intentionally NOT inherited
    here: a never-decided project should dispatch fast, not block on full
    reconciliation.
    """
    if strict_flag and loose_flag:
        raise LandError("land: choose --strict OR --loose, not both")
    if strict_flag:
        return True
    if loose_flag:
        return False
    return strictness.read_strictness_explicit(root) == STRICT


# --- worktree merge (FILE axis) --------------------------------------------

def _merge_worktree(root: Path, arc_dir: Path, ref: str) -> bool:
    """Merge the arc's worktree branch → base; True when it merged, False if no-op.

    A conflict raises :class:`LandError` with the resolve-then-retry next step (the
    branch is left un-merged and the repo clean). No-op (False) for non-git projects
    or an arc that never had a worktree.
    """
    from . import worktree

    if not (worktree.is_git_repo(root) and worktree.has_worktree(root, arc_dir)):
        return False
    result = worktree.land(root, arc_dir)
    if result.conflict:
        raise LandError(
            "cannot land {0!r}: {1} — the worktree branch was NOT merged.\n"
            "  next: resolve the conflict in the worktree, commit, then re-run "
            "tide arc land {0}".format(ref, result.detail)
        )
    if result.landed:
        # Fix 2 regression: worktree.remove can now raise WorktreeError (e.g.
        # locked worktree on NFS, registry race).  WorktreeError is NOT a
        # subclass of LandError/StreamError, so it would escape cmd_land's
        # except-LandError and cli.main's except-StreamError as a raw traceback.
        # Wrap it here — the merge already succeeded, so the message tells the
        # operator the branch is safe and gives the manual cleanup next step.
        try:
            worktree.remove(root, arc_dir)
        except worktree.WorktreeError as exc:
            raise LandError(
                "arc {0!r}: branch landed but worktree cleanup failed: {1}\n"
                "  the merge succeeded — no work was lost.\n"
                "  next: run 'git worktree remove --force <worktree-path>' "
                "manually, then retry 'tide arc land {0}'".format(ref, exc)
            ) from exc
        return True
    return False


# --- deferred-guard inspection ---------------------------------------------

def deferred_guards(arc_dir: Path) -> List[str]:
    """Which strict-reconciliation guards are unsatisfied on *arc_dir* right now.

    A subset of ``delta``/``report``/``proof`` (:data:`tide.ledger.GUARDS`): the
    guards a loose land would SKIP and record as debt. An empty list means the arc
    is already fully reconcilable (a loose land owes nothing).
    """
    out: List[str] = []
    if not lifecycle._delta_nonempty(arc_dir):
        out.append(ledger.GUARD_DELTA)
    if lifecycle._accepted(arc_dir, lifecycle.REPORT_FILE) != "yes":
        out.append(ledger.GUARD_REPORT)
    if lifecycle._accepted(arc_dir, lifecycle.PROOF_FILE) != "yes":
        out.append(ledger.GUARD_PROOF)
    return out


# --- strict / loose seal paths ---------------------------------------------

def _land_strict(
    root: Path, ref: str, has_contract: bool, date: Optional[str], goal_slug: Optional[str]
) -> Tuple[Path, str]:
    """Strict seal: enforce full reconciliation, merge the delta, clear any debt.

    Returns ``(sealed_dir, new_rev)``. Raises :class:`LandError` (with the exact
    deliverable-fixing next steps) when the reconciliation guard is unmet.
    """
    if has_contract:
        try:
            new_rev = lifecycle.close(root, ref, force=False, goal_slug=goal_slug, date=date)
        except model.ContractError as exc:
            raise LandError(
                "cannot land {0!r} --strict: {1}\n"
                "  next: write + accept the deliverables, then retry —\n"
                "    tide contract report {0} <what was done>\n"
                "    tide contract proof {0} <criteria evidence>\n"
                "    tide contract accept {0}\n"
                "    tide arc land --strict {0}\n"
                "  (or defer: tide arc land --loose {0})".format(ref, exc)
            )
        ledger.remove(root, ref)  # debt paid down (no-op when it wasn't owed)
        sealed = model.resolve_arc_dir(root, ref, goal_slug=goal_slug)
        return sealed, new_rev

    # No contract: strict = honour the arc's empty-output / placeholder guards.
    try:
        sealed = stream.close(root, ref, goal_slug=goal_slug, force=False)
    except stream.StreamError as exc:
        raise LandError(
            "cannot land {0!r} --strict: {1}\n"
            "  next: fill the arc's output/, then retry tide arc land --strict {0} "
            "(or defer: tide arc land --loose {0})".format(ref, exc)
        )
    new_rev = stream.stamp_rev(sealed, root)
    return sealed, new_rev


def _land_loose(
    root: Path, arc_dir: Path, ref: str, has_contract: bool, goal_slug: Optional[str]
) -> Tuple[Path, str, List[str]]:
    """Loose seal: skip the reconciliation guards, record the debt, defer the merge.

    Returns ``(sealed_dir, new_rev, deferred)``. The delta is intentionally left
    unmerged (canon reconciliation is owed); the arc is re-stamped with the CURRENT
    rev so it is not also flagged as canon-rev drift.
    """
    deferred = deferred_guards(arc_dir) if has_contract else []

    if slug.is_closed_entry(arc_dir.name):
        sealed = arc_dir  # already sealed (e.g. a manual arc close first)
    else:
        sealed = stream.close(root, ref, goal_slug=goal_slug, force=True)

    new_rev = stream.stamp_rev(sealed, root)

    if has_contract and deferred:
        # F3/P1 ordering fix: ledger.append is the authoritative sink — write it
        # FIRST so a crash between the two writes leaves a ledger entry that
        # reconcile can find.  A cosmetic "deferred:" note with no ledger entry
        # means the delta is unmerged forever and reconcile never sees it.
        # Wrap OSError so the operator gets a clear message (the arc IS sealed;
        # the debt just needs to be re-registered).
        try:
            ledger.append(root, sealed.name, deferred, new_rev)
        except OSError as exc:
            raise LandError(
                "arc {0!r} is sealed but debt could not be recorded in the ledger: {1}\n"
                "  the arc is done; retry 'tide reconcile' to re-register the debt.".format(
                    sealed.name, exc
                )
            ) from exc
        model.set_field(sealed, "deferred", "{0} @ {1}".format(", ".join(deferred), new_rev))
    return sealed, new_rev, deferred


# --- the atomic act --------------------------------------------------------

def land_one(
    root: Path,
    ref: str,
    *,
    strict: bool,
    run_gate: bool = True,
    gate_fn: Optional[GateFn] = None,
    date: Optional[str] = None,
    goal_slug: Optional[str] = None,
) -> LandOutcome:
    """Atomically land arc *ref*: merge worktree → seal/reconcile → re-stamp → gate.

    *strict* selects the seal path (see the module docstring). Raises
    :class:`LandError` (self-documenting) on a worktree conflict or an unmet strict
    guard; on success returns the :class:`LandOutcome`. A non-clean gate is reported
    in the outcome, not raised — the seal already happened.
    """
    root = Path(root)
    arc_dir = model.resolve_arc_dir(root, ref, goal_slug=goal_slug)
    ref_slug = slug.entry_slug(arc_dir.name)
    has_contract = model.has_contract(arc_dir)

    merged = _merge_worktree(root, arc_dir, ref_slug)

    if strict:
        sealed, new_rev = _land_strict(root, ref_slug, has_contract, date, goal_slug)
        deferred: List[str] = []
        reconciled = has_contract  # the delta was merged into CANON
    else:
        sealed, new_rev, deferred = _land_loose(
            root, arc_dir, ref_slug, has_contract, goal_slug
        )
        reconciled = False

    outcome = LandOutcome(
        arc=sealed.name,
        ref=ref_slug,
        strict=strict,
        merged=merged,
        reconciled=reconciled,
        new_rev=new_rev,
        deferred=deferred,
    )
    if run_gate:
        outcome.gate_code, outcome.gate_reasons = _gate(root, gate_fn)
    return outcome


def _gate(root: Path, gate_fn: Optional[GateFn]) -> Tuple[int, List[str]]:
    """Evaluate the post-land gate (injectable; defaults to the canon-gate oracle)."""
    if gate_fn is not None:
        return gate_fn(root)
    from .. import gate as _gate_mod

    return _gate_mod.decide(root)


# --- batch land / reconcile ------------------------------------------------

def batch_land(
    root: Path,
    refs: List[str],
    *,
    strict: bool,
    run_gate: bool = True,
    gate_fn: Optional[GateFn] = None,
    date: Optional[str] = None,
    goal_slug: Optional[str] = None,
) -> List[LandOutcome]:
    """Land several arcs in one invocation; the gate runs ONCE after the batch.

    Each arc is sealed in turn (the same atomic act as :func:`land_one`, with the
    per-arc gate suppressed); the single project-wide gate verdict is then attached
    to every returned outcome so the caller can report it once.
    """
    outcomes: List[LandOutcome] = []
    for ref in refs:
        outcomes.append(
            land_one(
                root, ref, strict=strict, run_gate=False, date=date, goal_slug=goal_slug
            )
        )
    if run_gate and outcomes:
        code, reasons = _gate(root, gate_fn)
        for o in outcomes:
            o.gate_code, o.gate_reasons = code, list(reasons)
    return outcomes


@dataclass
class ReconcilePreview:
    """A single arc's previewed merge — what reconciling it WOULD change in CANON.md."""

    ref: str
    diff: str  # unified CANON.md diff ("" → no change: already merged / empty delta)


@dataclass
class ReconcileReport:
    """The outcome of a debt-paydown sweep (sequential, fault-isolated).

    * ``paid`` — arcs reconciled this run (delta merged, debt line cleared).
    * ``failed`` — ``(ref, next-step)`` for arcs that could not auto-reconcile
      (e.g. report/proof not yet written) — left on the ledger for a re-run.
    * ``gate_code`` / ``gate_reasons`` — the single post-sweep gate verdict.
    """

    paid: List[LandOutcome] = field(default_factory=list)
    failed: List[Tuple[str, str]] = field(default_factory=list)
    gate_code: Optional[int] = None
    gate_reasons: List[str] = field(default_factory=list)


def _owed_refs(root: Path, arcs: Optional[List[str]]) -> List[str]:
    """The refs to reconcile: *arcs* if given, else the whole ledger (file order)."""
    return list(arcs) if arcs else [e.ref for e in ledger.entries(root)]


def reconcile_one(root: Path, ref: str, *, date: Optional[str] = None) -> LandOutcome:
    """Reconcile exactly ONE owed arc: a strict land (delta→CANON), debt cleared.

    Idempotent: the strict close re-merges a delta that is already folded into
    CANON as a no-op diff (the structural merge + journal dedup), so re-running
    after an interruption neither double-applies nor corrupts the truth. No gate
    here — the sweep runs the project-wide gate once at the end.
    """
    return land_one(root, ref, strict=True, run_gate=False, date=date)


def preview_reconcile(root: Path, *, arcs: Optional[List[str]] = None) -> List[ReconcilePreview]:
    """Dry-run the paydown: the prospective CANON.md diff for each owed arc, no writes.

    The review step an autonomous agent runs BEFORE committing — each deferred arc's
    delta merge shown as a reviewable diff. Pure (no disk mutation).
    """
    from ..canon import merge

    out: List[ReconcilePreview] = []
    for ref in _owed_refs(root, arcs):
        try:
            arc_dir = model.resolve_arc_dir(root, ref)
        except model.ContractError as exc:
            out.append(ReconcilePreview(ref=ref, diff="(cannot preview: {0})".format(exc)))
            continue
        cslug = (
            model.contract_slug(arc_dir)
            if model.has_contract(arc_dir)
            else slug.entry_slug(arc_dir.name)
        )
        try:
            current, prospective = merge.preview_delta(root, arc_dir, slug=cslug)
            out.append(ReconcilePreview(ref=ref, diff=merge.unified_diff(current, prospective)))
        except FileNotFoundError:
            out.append(ReconcilePreview(ref=ref, diff=""))
    return out


def reconcile(
    root: Path,
    *,
    arcs: Optional[List[str]] = None,
    run_gate: bool = True,
    gate_fn: Optional[GateFn] = None,
    date: Optional[str] = None,
) -> ReconcileReport:
    """Pay down deferred debt SEQUENTIALLY, one arc at a time, fault-isolated.

    With *arcs* given, reconciles exactly those; otherwise the whole ledger.
    Reconciliation is always strict (it IS the full reconciliation a loose land
    deferred). Each arc is processed independently: a single un-reconcilable arc
    (missing report/proof) is recorded in ``failed`` and skipped — it does NOT
    abort the sweep — so the run is safe for an autonomous agent and re-runnable
    after an interruption (paid arcs leave the ledger; the rest are retried). The
    gate runs once after the sweep.
    """
    report = ReconcileReport()
    for ref in _owed_refs(root, arcs):
        try:
            report.paid.append(reconcile_one(root, ref, date=date))
        except LandError as exc:
            report.failed.append((ref, str(exc)))
    if run_gate and (report.paid or report.failed):
        report.gate_code, report.gate_reasons = _gate(root, gate_fn)
    return report


# --- rendering -------------------------------------------------------------

def render_outcome(o: LandOutcome) -> str:
    """One-line (plus owed-debt hint) human summary of a single land outcome."""
    merged_note = "merged→base, " if o.merged else ""
    if o.strict:
        recon = "reconciled" if o.reconciled else "sealed"
        return "tide: landed {arc} --strict → canon-rev {rev} ({merged}{recon})".format(
            arc=o.arc, rev=o.new_rev, merged=merged_note, recon=recon
        )
    if o.deferred:
        return (
            "tide: landed {arc} --loose → {merged}sealed; "
            "deferred {guards} → debt logged.\n"
            "  reconcile when ready: tide arc land --strict {ref}  (or tide reconcile)".format(
                arc=o.arc, merged=merged_note, guards="+".join(o.deferred), ref=o.ref
            )
        )
    return "tide: landed {arc} --loose → {merged}sealed (nothing deferred)".format(
        arc=o.arc, merged=merged_note
    )


def render_gate(code: int, reasons: List[str]) -> str:
    """Render the post-land gate verdict with the catch-up next step on failure."""
    if code == 0:
        return "tide: gate ✓ — canon current"
    head = "tide: gate ⚠ — canon stale:" if code == 1 else "tide: gate ✗ — oracle-error:"
    lines = [head]
    for r in reasons:
        lines.append("  - {0}".format(r))
    if code == 1:
        lines.append("  next: tide reconcile  (or tide canon merge <arc>)")
    return "\n".join(lines)


# --- CLI handlers ----------------------------------------------------------

def _root() -> Path:
    return paths.require_tide_root()


def _orca_issue(arc_dir: Path) -> Optional[str]:
    """The arc's linked Orca/GitHub issue number, or None (a headless local arc)."""
    from .. import fields
    from ..adapters import orca_worktree as _ow
    from . import worktree

    return fields.read_field(worktree._passport(arc_dir), _ow.ISSUE_FIELD)


def _orca_land(root: Path, arc_dir: Path, issue_num: str) -> int:
    """The Orca gh-first land path (push → PR → in-review) — preserved verbatim."""
    from ..adapters import orca_worktree as _ow

    try:
        pr_url = _ow.orca_land(root, arc_dir)
    except _ow.OrcaLandError as exc:
        print("tide: {0}".format(exc), file=sys.stderr)
        return 1
    print("tide: PR created: {url} (issue #{n} marked in-review)".format(url=pr_url, n=issue_num))
    return 0


def cmd_land(args) -> int:
    """``tide arc land <slug>...`` — atomic, strictness-gated, batch-capable land.

    Orchestrator-only (it merges shared truth). An arc with a linked Orca issue
    keeps the gh-first PR flow; headless arcs run the atomic local land. The gate
    runs once after all local arcs land.
    """
    from ..cli import require_orchestrator

    require_orchestrator("arc land")
    root = _root()
    strict = land_is_strict(
        root, strict_flag=getattr(args, "strict", False), loose_flag=getattr(args, "loose", False)
    )
    run_gate = not getattr(args, "no_gate", False)
    goal_slug = getattr(args, "goal", None)

    rc = 0
    local_refs: List[str] = []
    for ref in args.slug:
        try:
            arc_dir = model.resolve_arc_dir(root, ref, goal_slug=goal_slug)
        except model.ContractError as exc:
            print("tide: {0}".format(exc), file=sys.stderr)
            rc = 1
            continue
        issue = _orca_issue(arc_dir)
        if issue:
            rc = _orca_land(root, arc_dir, issue) or rc
        else:
            local_refs.append(ref)

    if not local_refs:
        return rc

    try:
        outcomes = batch_land(
            root, local_refs, strict=strict, run_gate=False, goal_slug=goal_slug
        )
    except LandError as exc:
        print("tide: {0}".format(exc), file=sys.stderr)
        return 1
    for o in outcomes:
        print(render_outcome(o))
    if run_gate:
        code, reasons = _gate(root, None)
        print(render_gate(code, reasons))
        if code != 0:
            rc = 1
    return rc


def render_preview(p: "ReconcilePreview") -> str:
    """Render one arc's reconcile preview (its prospective CANON.md diff)."""
    if not p.diff.strip():
        return "tide: preview {0} — no change to CANON.md (already merged / empty delta)".format(
            p.ref
        )
    return "tide: preview {0} → CANON.md (NOT committed):\n{1}".format(p.ref, p.diff)


def cmd_reconcile(args) -> int:
    """``tide reconcile [<slug>...]`` — pay down deferred-reconciliation debt.

    ``--preview`` is a read-only dry-run (any role): print the prospective CANON.md
    diff for each owed arc and commit nothing — review-then-commit. The real sweep
    is orchestrator-only, SEQUENTIAL and fault-isolated: each owed arc is reconciled
    on its own (one previewable merge at a time); an arc that can't auto-reconcile
    (missing report/proof) is reported and left on the ledger for a re-run, so the
    command is safe to run by an autonomous agent and re-runnable after interruption.
    """
    root = _root()
    arcs = list(args.slug) if getattr(args, "slug", None) else None

    # --preview: read-only, no role gate, no writes.
    if getattr(args, "preview", False):
        if arcs is None and ledger.count(root) == 0:
            print("tide: reconcile --preview — no deferred debt (ledger clean)")
            return 0
        for p in preview_reconcile(root, arcs=arcs):
            print(render_preview(p))
        return 0

    from ..cli import require_orchestrator

    require_orchestrator("reconcile")

    if arcs is None and ledger.count(root) == 0:
        print("tide: reconcile — no deferred debt (ledger clean)")
        return 0

    report = reconcile(root, arcs=arcs, run_gate=False)
    for o in report.paid:
        print(render_outcome(o))
    for ref, reason in report.failed:
        print("tide: deferred {0} still owes reconciliation — {1}".format(ref, reason))

    code, reasons = _gate(root, None)
    print(render_gate(code, reasons))
    remaining = ledger.count(root)
    if remaining:
        print(
            "tide: {0} arc(s) still owe reconciliation → re-run tide reconcile "
            "after writing their report/proof".format(remaining)
        )
    # Nonzero when work remains (failed arcs) or the gate is not clean.
    return 0 if (code == 0 and not report.failed) else 1


# --- registration ----------------------------------------------------------

def _add_goal_opt(p) -> None:
    p.add_argument("-g", "--goal", help="operate inside this goal's substream")


def register_land(arc_subparsers) -> None:
    """Add the atomic ``tide arc land`` verb to the ``tide arc`` group (cli.py)."""
    lp = arc_subparsers.add_parser(
        "land",
        help="atomic land: merge worktree → seal/reconcile → re-stamp → gate (strictness-dialled)",
    )
    lp.add_argument("slug", nargs="+", help="arc(s) to land (batch: several slugs)")
    mode = lp.add_mutually_exclusive_group()
    mode.add_argument(
        "--strict", action="store_true", help="enforce full reconciliation (delta+report+proof)"
    )
    mode.add_argument(
        "--loose", action="store_true", help="skip reconciliation guards, log the debt (default)"
    )
    lp.add_argument("--no-gate", action="store_true", help="skip the post-land gate")
    _add_goal_opt(lp)
    lp.set_defaults(func=cmd_land, _cmd="arc land")


def register_reconcile(subparsers) -> None:
    """Add the top-level ``tide reconcile`` command (cli.py)."""
    p = subparsers.add_parser(
        "reconcile",
        help="pay down deferred-reconciliation debt (strict-land each owed arc)",
    )
    p.add_argument("slug", nargs="*", help="arc(s) to reconcile (default: the whole ledger)")
    p.add_argument(
        "--preview",
        action="store_true",
        help="dry-run: print the prospective CANON.md diff per owed arc, commit nothing (any role)",
    )
    p.set_defaults(func=cmd_reconcile, _cmd="reconcile")

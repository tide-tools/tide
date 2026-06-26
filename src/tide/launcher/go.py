"""tide.launcher.go — ``tide go``: the light ENTRY dispatcher (mirror of handoff).

``tide handoff`` is the clean EXIT — it distils a chat and forks out. ``tide go``
is the missing symmetric ENTRY: a light gate the human walks through to get back
INTO tide, asking only *"resume prior work, or start new?"*. It is a ROUTER, not a
brain — it resolves a seed, then hands the launch to ``tide terminal`` (the clean
logged-in in-place session). It never opens its own kind of session and never
duplicates the scoped+skip-perms launch shape.

Two doors:

* **resume** — open arcs that carry a *resumable thread*. Each open arc is
  classified by its LATEST ``workspace/handoff-*.md`` (the distil ``tide handoff``
  wrote): ``continue`` → a live thread, seeded from that distil; ``close`` → put
  down on purpose, **hidden** (the human said "его нет"); none (chat ended without
  a handoff) → ``raw``, resumed from the arc's passport ("поднять сыро").
* **new** — every open arc as a fresh start (seeded from its passport), plus a
  ``just chat`` option (no arc — the plain head seed, ``MIGRATE.md``).

Before EITHER door launches, a light **in-flight gate** runs at the single launch
choke point: a file-signal read (over ``tide status``, NOT process-locking) for
work still being processed — unmerged deltas, running/output contracts, drift. If
anything is in flight the human is shown it and asked to wait/enter-anyway/cancel,
so a controlled entry never drops them into a half-merged, half-closed state. (A
real concurrent-session lock is a separate candidate, not this.)

Layering matches the package: the listing/classification/rendering helpers are
pure (argparse- and exec-free, snapshot-testable); :func:`cmd_go` is the thin
interactive handler, and the actual launch is delegated to
:func:`tide.launcher.terminal.cmd_terminal` so the scoped argv lives in ONE place.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .. import fields, paths, slug
from ..adapters.base import persist_seed
from ..arc import board
from ..arc.stream import StreamError
from . import context, seed, terminal

WORKSPACE_DIRNAME = "workspace"
HANDOFF_GLOB = "handoff-*.md"

# Role-by-place: which role `tide go` enters with, decided by the launch dir.
# A control-home (carries roster.md) → ORCHESTRATOR + head; a plain project
# (.tide/ but no roster) → PROJECT-MANAGER scoped to that project; --orchestrator
# forces the head from anywhere. The env value handed to the spawned session
# (TIDE_ROLE) is the tide CLI's own role gate — orchestrator | worker.
ROLE_ORCHESTRATOR = "orchestrator"
ROLE_PROJECT_MANAGER = "project-manager"
ENV_ROLE_ORCHESTRATOR = "orchestrator"
ENV_ROLE_WORKER = "worker"

# Thread kinds (how an open arc is resumable).
KIND_CONTINUE = "continue"  # latest handoff mode==continue → seed from the distil
KIND_RAW = "raw"            # no handoff (chat ended without one) → seed from passport

# The "no arc, plain head" choice in the NEW menu — index 0, reserved.
JUST_CHAT = "just chat"

# Contract states that mean "still being processed" (not yet sealed) — the
# in-flight gate flags these so the human isn't dropped onto half-done work.
LIVE_CONTRACT_STATES = ("running", "output")

# In-flight WAIT bounds: poll the file signals this often, up to this long, before
# giving up and re-asking. Kept short — this is a courtesy wait for another session
# to finish its merge, not a process lock.
WAIT_INTERVAL_S = 2.0
WAIT_MAX_S = 60.0


class GoError(StreamError):
    """A dispatcher error (bad pick, empty menu, not-a-tide-dir). Caught by ``cli.main``."""


# --- role-by-place ---------------------------------------------------------

@dataclass
class RoleDecision:
    """Which role `tide go` enters with, the root it operates on, and why (pure).

    * ``role`` — display role (``orchestrator`` | ``project-manager``).
    * ``env_role`` — the ``TIDE_ROLE`` env value handed to the session
      (``orchestrator`` | ``worker``).
    * ``root`` — the dir resume/new/in-flight all operate on (control-home for the
      head, the project itself for a project-manager).
    * ``reason`` — the one-line "why" surfaced in ``--dry-run``.
    """

    role: str
    env_role: str
    root: Path
    reason: str

    @property
    def is_orchestrator(self) -> bool:
        return self.role == ROLE_ORCHESTRATOR


def resolve_role(start: Path, *, force_orchestrator: bool = False) -> RoleDecision:
    """Decide the role from the launch dir (the symmetric in-bound role pick).

    ``--orchestrator`` (``force_orchestrator``) → always the head: climb to the
    nearest control-home (``terminal.find_control_home``), role orchestrator. Else
    look at the nearest ``.tide`` root: a control-home (``roster.md``) → orchestrator
    + head; a plain project (``.tide`` but no roster) → project-manager scoped to
    that project. No ``.tide`` anywhere → a clear :class:`GoError` hint.
    """
    tide_root = paths.find_tide_root(start)
    if tide_root is None:
        raise GoError(
            "tide go: not inside a tide project — cd into a control-home or a "
            "project (a dir with a .tide/) first, then run tide go"
        )
    if force_orchestrator:
        root = terminal.find_control_home(start)
        return RoleDecision(ROLE_ORCHESTRATOR, ENV_ROLE_ORCHESTRATOR, root, "--orchestrator forced")
    if paths.is_control_home(tide_root):
        return RoleDecision(
            ROLE_ORCHESTRATOR, ENV_ROLE_ORCHESTRATOR, tide_root, "control-home"
        )
    return RoleDecision(
        ROLE_PROJECT_MANAGER,
        ENV_ROLE_WORKER,
        tide_root,
        tide_root.name,
    )


def render_role(d: RoleDecision) -> str:
    """The one-line role line (``role: X (why)``) — composed into the header banner."""
    return "role: {0} ({1})".format(d.role, d.reason)


# Front-door banner: a calm titled header the human sees on EVERY entry. House
# mono-mood — a quiet title, a hairline, then the role line. Indented to a steady
# left margin so the menus below sit under the same edge.
FRONT_DOOR_TITLE = "tide · go"
_HAIRLINE = "─" * 44


def render_header(d: RoleDecision) -> str:
    """The front-door header: titled banner + hairline + the role line (pure)."""
    return "\n".join(
        [
            "  {0}".format(FRONT_DOOR_TITLE),
            "  {0}".format(_HAIRLINE),
            "  {0}".format(render_role(d)),
        ]
    )


# --- handoff inspection (pure-ish reads) -----------------------------------

def latest_handoff(arc_dir: Path) -> Optional[Path]:
    """The most recent ``workspace/handoff-<date>.md`` for *arc_dir*, or None.

    Filenames are ``handoff-<ISO-date>.md`` so a lexical sort is chronological;
    the last is the latest distil — the one that decides the arc's resume state.
    """
    ws = Path(arc_dir) / WORKSPACE_DIRNAME
    if not ws.is_dir():
        return None
    files = sorted(ws.glob(HANDOFF_GLOB))
    return files[-1] if files else None


def handoff_mode(path: Path) -> str:
    """The ``mode:`` field of a handoff distil (continue|new|close), lowercased."""
    return (fields.read_field(path, "mode") or "").strip().lower()


def handoff_oneliner(path: Path) -> str:
    """First real line of the distil's ``## Where we are`` section (one-liner).

    Falls back to "" when the section is absent or still the un-distilled
    placeholder, so the caller can drop back to the arc's goal line.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("## where we are"):
            in_section = True
            continue
        if in_section:
            if not stripped:
                continue
            if stripped.startswith("## "):  # next section, nothing useful found
                return ""
            if stripped.startswith("(") and "not distilled" in stripped.lower():
                return ""  # the placeholder build_summary writes when empty
            return stripped
    return ""


# --- resume listing (pure) -------------------------------------------------

@dataclass
class Thread:
    """A resumable thread: an open arc + how to re-enter it.

    * ``arc_dir`` / ``name`` / ``ref`` — the open arc dir, its dir name, its bare slug.
    * ``kind`` — ``continue`` (seed from the distil) or ``raw`` (seed from passport).
    * ``handoff`` — the distil path for a ``continue`` thread (None for ``raw``).
    * ``summary`` — the one-line gist shown in the menu (distil line or goal line).
    """

    arc_dir: Path
    name: str
    ref: str
    kind: str
    handoff: Optional[Path]
    summary: str


def _goal_line(arc_dir: Path) -> str:
    """The arc's one-line ``goal:`` (empty when still the scaffold placeholder)."""
    from ..arc.stream import passport_path

    goal = (fields.read_field(passport_path(arc_dir), "goal") or "").strip()
    if goal.startswith("<") and goal.endswith(">"):
        return ""  # un-filled scaffold hint — not a real summary
    return goal


def resumable_threads(root: Path) -> List[Thread]:
    """Open arcs that are resumable, classified by their latest handoff (pure read).

    Walks the control-home's open arcs. An arc whose latest handoff mode is
    ``close`` is intentionally put down → **excluded**. ``continue`` → a live
    thread seeded from the distil. Anything else (no handoff, or a non-continue
    non-close mode) → ``raw``, seeded from the passport. The menu summary prefers
    the distil's "Where we are" line, falling back to the arc's goal line.
    """
    out: List[Thread] = []
    for arc in board.open_entries(Path(root)):
        ref = slug.entry_slug(arc.name)
        goal = _goal_line(arc)
        handoff = latest_handoff(arc)
        if handoff is not None:
            mode = handoff_mode(handoff)
            if mode == "close":
                continue  # put down on purpose — "его нет"
            if mode == KIND_CONTINUE:
                summary = handoff_oneliner(handoff) or goal or "(thread not distilled)"
                out.append(Thread(arc, arc.name, ref, KIND_CONTINUE, handoff, summary))
                continue
        # no handoff, or a non-continue/non-close mode → raise it raw from the cursor
        out.append(Thread(arc, arc.name, ref, KIND_RAW, None, goal or "(no goal yet)"))
    return out


def render_resume_menu(threads: List[Thread]) -> str:
    """The numbered resume pick-list (column-aligned), or an empty-state note → ``new``."""
    if not threads:
        return "  Resume — (no resumable threads; start fresh: 'tide go --mode new')"
    name_w = max(len(t.name) for t in threads)
    lines = ["  Resume — pick up a thread"]
    for i, t in enumerate(threads, start=1):
        tag = "[{0}]".format(t.kind)
        lines.append(
            "    {0}) {1}  {2}  {3}".format(i, t.name.ljust(name_w), tag.ljust(10), t.summary)
        )
    return "\n".join(lines)


# --- new listing (pure) ----------------------------------------------------

def new_options(root: Path) -> List[Path]:
    """Open arcs offered as fresh starts in the NEW menu (numeric order)."""
    return board.open_entries(Path(root))


def render_new_menu(arcs: List[Path], root: Path, *, is_orchestrator: bool = True) -> str:
    """The numbered new-start pick-list: open arcs + the ``0) just chat`` option.

    The ``just chat`` label reflects the role: a head session for the orchestrator,
    a project-scoped session for a project-manager.
    """
    chat_kind = "plain head session" if is_orchestrator else "plain project session"
    name_w = max([len(JUST_CHAT)] + [len(a.name) for a in arcs])
    lines = ["  New — start fresh"]
    lines.append("    0) {0}  {1}".format(JUST_CHAT.ljust(name_w), chat_kind))
    for i, arc in enumerate(arcs, start=1):
        goal = _goal_line(arc) or "(no goal yet)"
        lines.append("    {0}) {1}  {2}".format(i, arc.name.ljust(name_w), goal))
    return "\n".join(lines)


# --- selection parsing (pure) ----------------------------------------------

def parse_pick(raw: str, count: int, *, allow_zero: bool = False) -> int:
    """Parse a single 1-based pick into an int, validated to ``[lo..count]``.

    *allow_zero* widens the floor to 0 (the NEW menu's ``just chat`` slot). Raises
    :class:`GoError` on an empty, non-numeric, or out-of-range pick.
    """
    s = (raw or "").strip()
    lo = 0 if allow_zero else 1
    if not s:
        raise GoError("go: empty pick (choose a number {0}..{1})".format(lo, count))
    if not s.isdigit():
        raise GoError("go: invalid pick {0!r} (want a number {1}..{2})".format(s, lo, count))
    n = int(s)
    if not (lo <= n <= count):
        raise GoError("go: pick {0} out of range ({1}..{2})".format(n, lo, count))
    return n


# --- seed resolution (one seed file per choice) ----------------------------

RESUME_HEADER = """# tide go — resume thread: {arc}

You are re-entering tide as **{role}** to RESUME a prior thread. Your standing
orientation is in {orient} — read it first, then pick up the distilled thread below.

---
"""

# Where each role reads its standing orientation from (named in the resume seed).
ORIENT_ORCHESTRATOR = "the control-home MIGRATE.md (stay light — coordinate, don't do project work here)"
ORIENT_PROJECT_MANAGER = "this project's CLAUDE.md / `tide context show` (work in THIS project, isolated)"

# Project-manager just-chat seed: orient the worker from the deterministic on-entry
# triad (read-order + open arcs/candidates/questions) instead of the head MIGRATE.
PM_SEED_HEADER = """# tide go — project session: {name}

You are opening a WORKER session scoped to THIS project, isolated (TIDE_ROLE=worker).
Orient from the on-entry view below — what to read first, and what work is open —
then pick up. Do project work here; orchestrator-only ops are refused by role.

---
"""


def build_resume_seed(arc_ref: str, distil_text: str, *, is_orchestrator: bool = True) -> str:
    """Compose a continue-thread seed: a role-appropriate orientation pointer + distil."""
    role = "the HEAD (coordinator)" if is_orchestrator else "this project's manager"
    orient = ORIENT_ORCHESTRATOR if is_orchestrator else ORIENT_PROJECT_MANAGER
    header = RESUME_HEADER.format(arc=arc_ref, role=role, orient=orient)
    return header + distil_text.strip() + "\n"


def project_orientation_seed(root: Path) -> str:
    """A project-manager just-chat seed: the deterministic on-entry triad (pure-ish read)."""
    return PM_SEED_HEADER.format(name=Path(root).name) + context.render_enter(root) + "\n"


def seed_for_thread(
    root: Path, thread: Thread, *, is_orchestrator: bool = True, dry_run: bool = False
) -> Optional[str]:
    """Persist and return the seed-file path for *thread* (None ⇒ default seed).

    A ``continue`` thread is seeded from its distil (wrapped with the role-appropriate
    orientation pointer); a ``raw`` thread from the arc passport via
    :func:`seed.seed_for_project` — with the roster attached only for the orchestrator
    (a project-manager session stays scoped to its own project). On *dry_run* nothing
    is written; a placeholder token keeps the ``@<seed-file>`` shape visible.
    """
    if thread.kind == KIND_CONTINUE and thread.handoff is not None:
        distil = Path(thread.handoff).read_text(encoding="utf-8")
        text = build_resume_seed(thread.ref, distil, is_orchestrator=is_orchestrator)
    else:
        text = seed.seed_for_project(
            root,
            arc_ref=thread.ref,
            role=seed.ROLE_ORCHESTRATOR if is_orchestrator else seed.ROLE_WORKER,
            control_home=root if is_orchestrator else None,
        )
    if dry_run:
        return "<seed-file>"
    return str(persist_seed(text, "tide-go-{0}".format(slug.slugify(thread.ref) or "resume")))


def seed_for_new_arc(
    root: Path, arc_dir: Path, *, is_orchestrator: bool = True, dry_run: bool = False
) -> str:
    """Persist and return the seed-file path for a fresh start on *arc_dir* (role-aware)."""
    ref = slug.entry_slug(arc_dir.name)
    text = seed.seed_for_project(
        root,
        arc_ref=ref,
        role=seed.ROLE_ORCHESTRATOR if is_orchestrator else seed.ROLE_WORKER,
        control_home=root if is_orchestrator else None,
    )
    if dry_run:
        return "<seed-file>"
    return str(persist_seed(text, "tide-go-new-{0}".format(slug.slugify(ref) or "arc")))


# --- in-flight gate (file signals over `tide status`, NOT a process lock) ---

@dataclass
class InFlight:
    """A snapshot of "work still being processed" — three file signals (pure read).

    * ``unmerged`` — closed arcs whose ``delta.md`` is written but un-merged (the
      between-arcs barrier offenders).
    * ``contracts`` — ``(arc, state)`` of contracts still ``running``/``output``
      (signed but not sealed).
    * ``drift`` — open arcs whose stamped ``cannon-rev`` lags the current one.

    ``clean`` is the gate's verdict: nothing in flight ⇒ enter silently.
    """

    unmerged: List[str]
    contracts: List[Tuple[str, str]]
    drift: List[str]

    @property
    def clean(self) -> bool:
        return not (self.unmerged or self.contracts or self.drift)


def inflight_signals(root: Path) -> InFlight:
    """Read the three in-flight signals for *root* over the same on-disk truth as
    ``tide status`` (pure, no locking). Lazy imports keep the launcher light.
    """
    from .. import sync
    from ..arc.stream import passport_path
    from ..cannon import rev
    from ..contract import lifecycle

    unmerged = [p.name for p in sync.unmerged_deltas(Path(root))]
    contracts = [
        (str(c["arc"]), str(c["state"]))
        for c in lifecycle.list_contracts(Path(root))
        if c.get("state") in LIVE_CONTRACT_STATES
    ]
    current = rev.compute(Path(root))
    drift: List[str] = []
    for entry in board.open_entries(Path(root)):
        stamped = fields.read_field(passport_path(entry), "cannon-rev")
        if stamped and stamped != current:
            drift.append(entry.name)
    return InFlight(unmerged, contracts, drift)


def render_deferred(root: Path) -> str:
    """One calm line on entry: deferred-reconciliation debt + the catch-up command.

    ``tide go`` is an entry door, so the head must see "канон отстал" here too (not
    only in the SessionStart board). ``deferred: none`` when the ledger is clean —
    explicit, mirroring the in-flight block.
    """
    from .. import ledger  # lazy: keep the launcher light

    debt = ledger.entries(Path(root))
    if not debt:
        return "  deferred: none"
    return (
        "  deferred: ⚠ канон отстал — {0} арок landed loose, ждут "
        "strict-реконсиляции ({1}) → tide reconcile".format(
            len(debt), ", ".join(e.arc for e in debt)
        )
    )


def render_inflight(s: InFlight) -> str:
    """One short, calm block: ``in-flight: none`` when clear, else the live signals."""
    if s.clean:
        return "  in-flight: none"
    lines = ["  in-flight: ⚠ work still being processed:"]
    if s.unmerged:
        lines.append("    unmerged deltas: {0}".format(", ".join(s.unmerged)))
    if s.contracts:
        lines.append(
            "    running/output contracts: {0}".format(
                ", ".join("{0} [{1}]".format(a, st) for a, st in s.contracts)
            )
        )
    if s.drift:
        lines.append("    drift: {0}".format(", ".join(s.drift)))
    return "\n".join(lines)


def wait_until_settled(
    root: Path,
    *,
    interval: float = WAIT_INTERVAL_S,
    max_wait: float = WAIT_MAX_S,
    sleep_fn: Callable[[float], None] = time.sleep,
    signal_fn: Callable[[Path], InFlight] = inflight_signals,
) -> bool:
    """Poll the in-flight signals until clean or *max_wait* elapses; True iff clean.

    A bounded courtesy wait for another session to finish its merge — never an
    unbounded block. ``sleep_fn``/``signal_fn`` are injected so tests drive it
    without real time or disk.
    """
    waited = 0.0
    while True:
        if signal_fn(Path(root)).clean:
            return True
        if waited >= max_wait:
            return False
        sleep_fn(interval)
        waited += interval


def _prompt_choice(prompt: str, allowed: Tuple[str, ...], default: str) -> str:
    """Read one lowercased letter from *allowed*; *default* on EOF or anything else."""
    try:
        ans = input(prompt).strip().lower()
    except EOFError:
        return default
    first = ans[:1]
    return first if first in allowed else default


def _inflight_gate(root: Path, *, dry_run: bool) -> bool:
    """Run the in-flight gate; return True to proceed with the launch, False to abort.

    Always PRINTS the check (clean or not) so it's visible — including under
    ``--dry-run``, where it never prompts (just shows the status, then proceeds).
    When live and interactive: ``c`` aborts, ``g`` enters anyway, ``w`` waits for
    the signals to settle (bounded) then enters — falling back to a final g/c ask
    if the wait times out.
    """
    signals = inflight_signals(root)
    print(render_inflight(signals))
    if signals.clean or dry_run:
        return True
    choice = _prompt_choice(
        "Есть незавершённая обработка — подождать завершения [w] / войти осознанно [g] / отмена [c]? ",
        ("w", "g", "c"),
        default="c",
    )
    if choice == "c":
        print("go: cancelled — nothing launched (work still in flight)")
        return False
    if choice == "g":
        return True
    # 'w' — wait for the other session's merge to land, bounded.
    print("waiting for in-flight work to settle…")
    if wait_until_settled(root):
        print("in-flight settled — entering")
        return True
    print(render_inflight(inflight_signals(root)))
    again = _prompt_choice(
        "Still in flight after waiting — войти осознанно [g] / отмена [c]? ",
        ("g", "c"),
        default="c",
    )
    if again != "g":
        print("go: cancelled — nothing launched (work still in flight)")
        return False
    return True


# --- launch delegation -----------------------------------------------------

def _maybe_activate_orca(arc_dir: Path) -> bool:
    """Best-effort focus of an arc's Orca workspace (never raises / blocks entry)."""
    try:
        from ..adapters import orca_worktree
        return orca_worktree.activate_workspace(Path(arc_dir))
    except Exception:  # noqa: BLE001  a failed focus must never block the launch
        return False


def _launch(
    seed_file: Optional[str],
    decision: RoleDecision,
    *,
    dry_run: bool,
    cwd: Optional[Path] = None,
) -> int:
    """Hand the resolved seed to ``tide terminal`` (the single scoped-launch path).

    First runs the in-flight gate at this single choke point (both doors funnel
    here); if it aborts, nothing is launched. Otherwise builds the Namespace
    ``terminal.cmd_terminal`` expects and calls it directly — so the scoped+skip-
    perms argv, the cwd, and the exec live in ONE place (``launcher.terminal``),
    never duplicated here. The in-flight gate stays on *decision.root* (project-level
    signals), but the launch *cwd* is *cwd* when given (the arc's worktree) so the
    session lands in the right place — falling back to *decision.root* (the head
    ``just chat`` path keeps the control-home root + its MIGRATE.md seed). The
    ``tide_role`` carries the role into the spawned session's env. ``seed_file=None``
    lets terminal resolve its own default (MIGRATE.md/RESUME.md).
    """
    if not _inflight_gate(decision.root, dry_run=dry_run):
        return 0
    ns = argparse.Namespace(
        seed=seed_file,
        dry_run=dry_run,
        no_disable_slash=False,
        no_skip_permissions=False,
        root=str(cwd or decision.root),
        tide_role=decision.env_role,
    )
    return terminal.cmd_terminal(ns)


# --- CLI handler -----------------------------------------------------------

def _resolve_mode(args, dry_run: bool) -> Optional[str]:
    """Resolve the resume/new mode: explicit ``--mode``, else the light r/n prompt.

    On a dry-run with no ``--mode`` we return None so :func:`cmd_go` prints the
    OVERVIEW (both menus) instead of blocking on stdin — the inspectable view.
    """
    mode = getattr(args, "mode", None)
    if mode:
        return mode
    if dry_run:
        return None
    try:
        ans = input("  resume prior work or start new? [r/n] ").strip().lower()
    except EOFError:
        ans = ""
    return "resume" if ans.startswith("r") else "new"


def _render_overview(decision: RoleDecision) -> str:
    """Both menus + the in-flight check — the dry-run inspectable view (role banner
    is printed by :func:`cmd_go` just above this)."""
    root = decision.root
    threads = resumable_threads(root)
    arcs = new_options(root)
    return "\n\n".join(
        [
            render_resume_menu(threads),
            render_new_menu(arcs, root, is_orchestrator=decision.is_orchestrator),
            render_deferred(root),
            render_inflight(inflight_signals(root)),
        ]
    )


def _do_resume(decision: RoleDecision, args, dry_run: bool) -> int:
    """Resume flow: list threads, pick one, seed from it (role-aware), delegate."""
    root = decision.root
    threads = resumable_threads(root)
    print(render_resume_menu(threads))
    if not threads:
        return 0
    raw = getattr(args, "pick", None)
    if not raw and not dry_run:
        try:
            raw = input("resume> ")
        except EOFError:
            raw = ""
    if not raw:  # dry-run overview within a mode: show the menu, don't pick
        return 0
    n = parse_pick(raw, len(threads))
    thread = threads[n - 1]
    seed_file = seed_for_thread(
        root, thread, is_orchestrator=decision.is_orchestrator, dry_run=dry_run
    )
    from ..arc import worktree
    cwd = worktree.resolve_cwd(root, thread.arc_dir)
    if not dry_run:
        _maybe_activate_orca(thread.arc_dir)
    if dry_run:
        print("\nwould resume [{0}] {1} →".format(thread.kind, thread.name))
    return _launch(seed_file, decision, dry_run=dry_run, cwd=cwd)


def _do_new(decision: RoleDecision, args, dry_run: bool) -> int:
    """New flow: list open arcs + just-chat, pick one, seed it (role-aware), delegate."""
    root = decision.root
    arcs = new_options(root)
    print(render_new_menu(arcs, root, is_orchestrator=decision.is_orchestrator))
    raw = getattr(args, "pick", None)
    if not raw and not dry_run:
        try:
            raw = input("new> ")
        except EOFError:
            raw = ""
    if not raw:  # dry-run overview within a mode
        return 0
    n = parse_pick(raw, len(arcs), allow_zero=True)
    if n == 0:
        # just-chat: orchestrator ⇒ seed None (terminal resolves the MIGRATE.md head);
        # project-manager ⇒ the project on-entry triad seed (no head MIGRATE).
        if decision.is_orchestrator:
            seed_file: Optional[str] = None
            chat_desc = "plain head session (MIGRATE.md seed)"
        else:
            seed_file = "<seed-file>" if dry_run else str(
                persist_seed(project_orientation_seed(root), "tide-go-pm-{0}".format(root.name))
            )
            chat_desc = "plain project session (tide context show triad)"
        if dry_run:
            print("\nwould start [just chat] → {0}".format(chat_desc))
        # just-chat keeps the project/control-home root (its MIGRATE.md seed) — no arc cwd.
        return _launch(seed_file, decision, dry_run=dry_run)
    arc = arcs[n - 1]
    seed_file = seed_for_new_arc(
        root, arc, is_orchestrator=decision.is_orchestrator, dry_run=dry_run
    )
    from ..arc import worktree
    cwd = worktree.resolve_cwd(root, arc)
    if not dry_run:
        _maybe_activate_orca(arc)
    if dry_run:
        print("\nwould start [new arc] {0} →".format(arc.name))
    return _launch(seed_file, decision, dry_run=dry_run, cwd=cwd)


def cmd_go(args) -> int:
    """``tide go`` — light entry dispatcher: role-by-place, then resume or start new.

    Resolves the role from the launch dir first (``--orchestrator`` forces the head),
    prints the role banner, then runs the chosen door. Every door funnels through the
    in-flight gate + ``tide terminal`` (one launch path).
    """
    decision = resolve_role(Path.cwd(), force_orchestrator=bool(getattr(args, "orchestrator", False)))
    print(render_header(decision))
    print()  # a calm blank line between the banner and what follows
    debt_line = render_deferred(decision.root)
    if "none" not in debt_line:  # surface the canon-lag on entry (silent when clean)
        print(debt_line)
        print()
    dry_run = bool(getattr(args, "dry_run", False))
    mode = _resolve_mode(args, dry_run)

    if mode is None:  # dry-run, no mode → print the overview, exec nothing
        print(_render_overview(decision))
        return 0
    if mode == "resume":
        return _do_resume(decision, args, dry_run)
    if mode == "new":
        return _do_new(decision, args, dry_run)
    raise GoError("go: unknown mode {0!r} (want resume|new)".format(mode))


def register(subparsers) -> None:
    """Add the top-level ``go`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "go",
        help="light entry dispatcher: resume a prior thread or start new (mirror of handoff)",
    )
    p.add_argument(
        "-O",
        "--orchestrator",
        action="store_true",
        help="force the orchestrator (head) role from ANY directory (ignore role-by-place)",
    )
    p.add_argument(
        "--mode",
        choices=("resume", "new"),
        help="skip the r/n prompt: resume a prior thread | start new",
    )
    p.add_argument("--pick", help="non-interactive selection within the mode (e.g. '1', or '0' for just-chat)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="print the menus + what would launch, without exec'ing a session",
    )
    p.set_defaults(func=cmd_go, _cmd="go")

"""tide.health — the Светофор: a tier-0 health line seen on entry.

ONE line of numbers, computed by ordinary code (filesystem-first, no network,
milliseconds), that answers the four questions a session must not walk past
blind:

* **unread**            — trace commits behind the reading watermark (отлив /
                          закрома): how much of the log has not been read back
                          (:mod:`tide.lookback`).
* **canon_debt**        — closed arcs still carrying an unmerged ``delta.md``: the
                          truth owed to CANON (:func:`tide.sync.unmerged_deltas`).
* **offers_waiting**    — handoff offers hanging in the queue (``status: offered``)
                          waiting for a pickup (:mod:`tide.handoff_queue`).
* **roster_not_ready**  — local, active roster projects that are NOT worktree-ready
                          (missing / not a git repo / no commit) — a worker sent
                          there would die trying to isolate (:mod:`tide.roster`).

TRISTATE (mirrors the ``tide canon gate`` 0/1/2 semantics):

* **green (0)** — every count is zero. Nothing to see.
* **yellow (1)** — something UNCLOSED but not yet rotten: unread traces or fresh
                   offers waiting. Advisory.
* **red (2)** — something ROTTEN: a canon debt, a not-ready roster project, or an
                offer that has hung longer than :data:`STALE_DAYS` days. Красное
                видно до того, как укусило.

DESIGN RAZOR — this is a pure READ. It never marks the watermark, never merges,
never mutates the queue, and never touches the network. Every counter is wrapped
defensively: a broken corner reports ``0`` for that number rather than crashing
the entry line (a health line that breaks the session is worse than useless).

Two layers as everywhere else: pure functions (argparse-free, unit-testable) +
the thin ``tide doctor --line`` skin (wired in :mod:`tide.doctor`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import paths

# Sentinel: tell "caller omitted home → resolve the control-home" apart from
# "caller passed home=None → there is genuinely no home" (mirrors doctor._UNSET).
_UNSET = object()

# An offer older than this many days has gone ROTTEN — it is no longer "waiting",
# it is stuck, and that trips the light red.
STALE_DAYS = 3

SEVERITY_GREEN = "green"
SEVERITY_YELLOW = "yellow"
SEVERITY_RED = "red"

# Tristate exit codes — same 0/1/2 as `tide canon gate`.
_EXIT = {SEVERITY_GREEN: 0, SEVERITY_YELLOW: 1, SEVERITY_RED: 2}

# The three traffic-light glyphs (the светофор reads instantly at a glance).
_GLYPH = {SEVERITY_GREEN: "🟢", SEVERITY_YELLOW: "🟡", SEVERITY_RED: "🔴"}


# --- result model ----------------------------------------------------------


@dataclass(frozen=True)
class HealthLine:
    """The four tier-0 counts + the tristate verdict over them.

    ``stale_offers`` is the subset of ``offers_waiting`` older than
    :data:`STALE_DAYS` (the rotten ones); ``roster_total`` is the number of
    local+active roster projects checked (context for the ``roster_not_ready``
    numerator). Both feed the render/severity — they are not among the four
    headline counts.
    """

    unread: int
    canon_debt: int
    offers_waiting: int
    roster_not_ready: int
    stale_offers: int = 0
    roster_total: int = 0

    @property
    def counts(self) -> Dict[str, int]:
        """The four headline counts as the ``{unread, canon_debt, …}`` structure."""
        return {
            "unread": self.unread,
            "canon_debt": self.canon_debt,
            "offers_waiting": self.offers_waiting,
            "roster_not_ready": self.roster_not_ready,
        }

    @property
    def rot(self) -> List[str]:
        """The ROTTEN reasons (what makes the light red), in reading order."""
        reasons: List[str] = []
        if self.canon_debt > 0:
            reasons.append("канон-долг")
        if self.roster_not_ready > 0:
            reasons.append("проект не готов")
        if self.stale_offers > 0:
            reasons.append("оффер >{0}д".format(STALE_DAYS))
        return reasons

    @property
    def severity(self) -> str:
        """green (all clean) · yellow (unclosed) · red (rotten)."""
        if self.rot:
            return SEVERITY_RED
        if self.unread > 0 or self.offers_waiting > 0:
            return SEVERITY_YELLOW
        return SEVERITY_GREEN

    @property
    def exit_code(self) -> int:
        """Tristate exit code — 0 green, 1 yellow, 2 red (like `tide canon gate`)."""
        return _EXIT[self.severity]


# --- per-count probes (each defensive: a broken corner counts as 0) --------


def _ref_exists_on_disk(root: Path, ref: str) -> bool:
    """True when watermark *ref* exists as a loose or packed ref — no subprocess.

    The fast-path guard for :func:`_count_unread`: the overwhelmingly common case
    is "no watermark set yet", and answering it from the filesystem keeps the whole
    health line in the low milliseconds instead of paying two ``git`` spawns to
    learn the ref is missing. A ``.git`` FILE (linked worktree / submodule) can't
    be resolved this cheaply, so we let git decide there (rare).
    """
    git = root / ".git"
    if git.is_file():
        return True  # linked worktree — defer to the real git resolution
    if (git / ref).is_file():  # loose ref
        return True
    packed = git / "packed-refs"
    if packed.is_file():
        try:
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return True
        except OSError:
            return False
    return False


def _count_unread(root: Optional[Path]) -> int:
    """Trace commits behind the reading watermark for *root* (0 when none/git-less).

    Reuses :func:`tide.lookback.gap` (the one watermark implementation) with the
    default reader + the project-dir scope, exactly as ``tide lookback status``
    resolves it. A never-marked ref counts as 0 — no watermark yet is not "unread",
    and a false red on a fresh project is the opposite of the point — and that case
    is answered from the filesystem (:func:`_ref_exists_on_disk`) so the git
    ``rev-list`` runs ONLY once a real watermark exists. A non-git project raises
    inside lookback → swallowed → 0.
    """
    if root is None:
        return 0
    try:
        from . import lookback

        root = Path(root)
        ref = lookback.ref_name(lookback.DEFAULT_READER, root.name)
        if not _ref_exists_on_disk(root, ref):
            return 0  # no watermark yet — the common case, no subprocess
        return lookback.gap(root, ref) or 0
    except Exception:
        return 0


def _count_canon_debt(root: Optional[Path]) -> int:
    """Closed arcs owing an unmerged canon delta (filesystem-only; 0 on any error)."""
    if root is None:
        return 0
    try:
        from . import sync

        return len(sync.unmerged_deltas(Path(root)))
    except Exception:
        return 0


def _parse_ts(raw: object) -> Optional[datetime]:
    """Parse a handoff ``created:`` ISO stamp, or None (blank / ``-`` / malformed)."""
    if not raw or raw == "-":
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return None


def _count_offers(home: Optional[Path], now: datetime) -> Tuple[int, int]:
    """``(waiting, stale)`` — offered handoffs, and how many are older than STALE_DAYS."""
    if home is None:
        return 0, 0
    try:
        from . import handoff_queue as hq

        offers = hq.list_offers(home, status=hq.STATUS_OFFERED)
    except Exception:
        return 0, 0
    cutoff = now - timedelta(days=STALE_DAYS)
    stale = 0
    for o in offers:
        created = _parse_ts(o.get("created"))
        if created is not None and created < cutoff:
            stale += 1
    return len(offers), stale


def _worktree_ready(path: Path) -> bool:
    """True when *path* is a git repo with at least one commit — filesystem-only.

    "worktree-ready" means ``tide arc work`` could cut an isolated worktree there
    (:func:`tide.arc.worktree.create`): a git repo with a commit to branch from.
    Checked WITHOUT a subprocess so the health line stays in the low milliseconds:

    * the dir must exist,
    * it must carry a ``.git`` (dir for a normal repo, file for a linked
      worktree / submodule — the latter is git-backed, so ready),
    * a normal repo must have at least one commit, proxied by at least one loose
      ref under ``.git/refs/heads/`` OR a ``.git/packed-refs`` — both absent only
      before the first commit (exactly the "no commit to branch from" case).
    """
    try:
        if not path.is_dir():
            return False
        git = path / ".git"
        if not git.exists():
            return False
        if git.is_file():
            return True  # linked worktree / submodule pointer — git-backed
        heads = git / "refs" / "heads"
        if heads.is_dir() and any(heads.iterdir()):
            return True
        return (git / "packed-refs").is_file()
    except Exception:
        return False


def _count_roster_not_ready(home: Optional[Path]) -> Tuple[int, int]:
    """``(not_ready, total)`` over LOCAL, ACTIVE roster projects.

    Remote projects (an ``environment`` field) can't be checked from this machine
    and archived projects are out of the picker — both are skipped so the number
    only ever flags a project a worker could actually be sent to today.
    """
    if home is None:
        return 0, 0
    try:
        from . import roster

        entries = roster.read_roster(home)
    except Exception:
        return 0, 0
    total = 0
    not_ready = 0
    for e in entries:
        if e.get("environment"):
            continue  # remote — not this machine's to judge
        if e.get("status") == getattr(roster, "STATUS_ARCHIVED", "archived"):
            continue  # archived — hidden from the picker
        path = e.get("path")
        if not path:
            continue
        total += 1
        if not _worktree_ready(Path(str(path)).expanduser()):
            not_ready += 1
    return not_ready, total


# --- aggregate -------------------------------------------------------------


def _resolve_home(home) -> Optional[Path]:
    """Resolve the control-home for the queue/roster counts (defensive).

    ``home`` omitted → resolve the real control-home (None when there is none);
    ``home=None`` → genuinely no home (queue/roster counts are 0); an explicit
    path is used as-is (tests inject a tmp home).
    """
    if home is _UNSET:
        try:
            return paths.control_home()
        except Exception:
            return None
    return Path(home) if home is not None else None


def compute_health(
    root: Optional[Path], *, home=_UNSET, now: Optional[datetime] = None
) -> HealthLine:
    """Compute the tier-0 :class:`HealthLine` for project *root*.

    *root* is the per-project root (unread + canon-debt live there); *home* is the
    control-home (the handoff queue + roster live there) — omitted to resolve the
    real one, injectable for tests. *now* defaults to the wall clock (injectable so
    the stale-offer boundary is deterministic in tests). Fast: filesystem for three
    of the four numbers, one bounded git call for the watermark; never the network.
    """
    now = now or datetime.now()
    home_path = _resolve_home(home)
    offers_waiting, stale_offers = _count_offers(home_path, now)
    roster_not_ready, roster_total = _count_roster_not_ready(home_path)
    return HealthLine(
        unread=_count_unread(root),
        canon_debt=_count_canon_debt(root),
        offers_waiting=offers_waiting,
        roster_not_ready=roster_not_ready,
        stale_offers=stale_offers,
        roster_total=roster_total,
    )


# --- render ----------------------------------------------------------------


def render_line(health: HealthLine) -> str:
    """One line: the traffic-light glyph + the four numbers (+ a rot hint when red).

    Stable shape so the board can dergать it by subprocess and parse it back; the
    rot tail is appended only when red, and only after the numbers, so a reader
    (or a naive splitter) still finds the four counts in the same place.
    """
    line = "{glyph} закрома {u} · канон {c} · передачи {o} · ростер {r}".format(
        glyph=_GLYPH[health.severity],
        u=health.unread,
        c=health.canon_debt,
        o=health.offers_waiting,
        r=health.roster_not_ready,
    )
    if health.severity == SEVERITY_RED and health.rot:
        line += "  ⚠ гниёт: " + ", ".join(health.rot)
    return line

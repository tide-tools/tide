"""tide.canon.board — render the canon status board (``tide canon status``).

Ported from canon ``status`` (architect ``tide canon status (board)``): scan all
per-arc canon homes, **group by the 5-state contract lifecycle**, list open asks,
and fold the dropped desk/dock projection into a single **NEEDS YOU** slice.

A "canon home" is any arc dir carrying a ``contract.md`` (the one-per-arc binding,
``1 arc = 1 contract``). :func:`tide.contract.lifecycle.list_contracts` already does
the stream walk (top level + one goal substream deep) and reads each contract's
``state:`` — we reuse it (DRY) and enrich each row with:

* **needs_you** — does this contract await the human? The two human gates are
  ``sign`` (awaiting signature) and ``output`` (deliverables written, awaiting
  accept/close); under the ``strict`` dial a ``draft`` also waits on the human to
  sign. Under ``loose`` the orchestrator signs synchronously, so a draft is active
  work, not a gate. (The canon auto-advance/owner machinery is intentionally dropped.)
* **open_asks** — the arc's durable ``asks/NN-slug.md`` entries still ``state: open``.

The board is a pure projection (never stored); :func:`render_board` is
snapshot-testable and :func:`cmd_status` is the thin handler ``canon.commands`` wires.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from .. import fields, paths, strictness
from ..contract import ask as ask_mod
from ..contract import lifecycle, model

# 5-state display order (lifecycle order); each non-empty state renders a group.
GROUP_ORDER: List[str] = list(model.STATES)  # draft, sign, running, output, close
# The two human gates; draft joins them only under the strict dial.
GATE_STATES = (model.SIGN, model.OUTPUT)


# --- scan ------------------------------------------------------------------

def _needs_you(state: object, strict: bool) -> bool:
    """True when a contract in *state* awaits the human (gate-aware + strict-aware)."""
    if state in GATE_STATES:
        return True
    return bool(state == model.DRAFT and strict)


def _open_asks(arc_dir: Path) -> List[str]:
    """Stems of the arc's ``asks/`` entries still ``state: open`` (NN order)."""
    adir = Path(arc_dir) / model.ASKS_DIRNAME
    out: List[str] = []
    if not adir.is_dir():
        return out
    for p in sorted(adir.glob("*.md")):
        if fields.read_field(p, "state") == ask_mod.OPEN:
            out.append(p.stem)
    return out


def scan(root: Path) -> List[Dict[str, object]]:
    """Scan per-arc canon homes; one enriched row per contract (stream order).

    Each row carries ``{arc, slug, state, sign, path, arc_dir, needs_you, open_asks}``.
    Reuses :func:`tide.contract.lifecycle.list_contracts` for the walk + state read.
    """
    strict = strictness.is_strict(root)
    rows: List[Dict[str, object]] = []
    for it in lifecycle.list_contracts(root):
        arc_dir = Path(it["path"]).parent
        rows.append(
            {
                **it,
                "arc_dir": arc_dir,
                "needs_you": _needs_you(it["state"], strict),
                "open_asks": _open_asks(arc_dir),
            }
        )
    return rows


# --- render ----------------------------------------------------------------

def _row_line(row: Dict[str, object], *, with_state: bool) -> str:
    """One contract line; ``with_state`` shows ``[state]`` (for the NEEDS YOU slice)."""
    if with_state:
        return "  {arc}  [{state}]  {slug}".format(
            arc=row["arc"], state=row["state"], slug=row["slug"]
        )
    return "  {arc}  {slug}".format(arc=row["arc"], slug=row["slug"])


def render_board(root: Path) -> str:
    """Render the full canon status board for *root* (pure, snapshot-testable)."""
    rows = scan(Path(root))
    lines: List[str] = ["CANON"]

    if not rows:
        lines.append("  (no contracts)")
        return "\n".join(lines)

    # NEEDS YOU slice (the folded desk/dock projection).
    needy = [r for r in rows if r["needs_you"]]
    if needy:
        lines.append("")
        lines.append("NEEDS YOU")
        for r in needy:
            lines.append(_row_line(r, with_state=True))

    # Group by the 5-state lifecycle (only non-empty states shown).
    for state in GROUP_ORDER:
        members = [r for r in rows if r["state"] == state]
        if not members:
            continue
        lines.append("")
        lines.append("{0} ({1})".format(state, len(members)))
        for r in members:
            lines.append(_row_line(r, with_state=False))

    # Open asks across every scanned arc.
    ask_lines: List[str] = []
    for r in rows:
        for stem in r["open_asks"]:
            ask_lines.append("  {arc} · {stem}".format(arc=r["arc"], stem=stem))
    if ask_lines:
        lines.append("")
        lines.append("OPEN ASKS")
        lines.extend(ask_lines)

    return "\n".join(lines)


# --- CLI handler -----------------------------------------------------------

def cmd_status(args) -> int:
    """Print the canon status board (``tide canon status``)."""
    root = paths.require_tide_root()
    print(render_board(root))
    return 0

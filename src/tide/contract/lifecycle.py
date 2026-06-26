"""tide.contract.lifecycle — the contract verbs + the ``tide contract`` CLI group.

Implements the 5-state lifecycle (see :mod:`tide.contract.model`):

    new → sign → report+proof → accept → close   (reopen reverses a close)

The load-bearing rules this module owns (build-blueprint + architect contract_module):

* **new** — one-per-arc guard (refuse a second ``contract.md``), seed
  ``contract.md`` + empty ``delta.md`` + ``asks/``, stamp the current cannon-rev,
  state ``draft``.
* **sign** — the dispatch gate, **respecting strictness**: ``strict`` = the human
  signs in the live session (default signer ``human``); ``loose`` = the
  orchestrator stamps synchronously (default signer ``orchestrator``). Either way
  the sign moves ``draft → running`` and stamps ``sign: <who> @ <date>``.
* **report / proof** — write the per-arc ``report.md`` / ``proof.md`` deliverables
  (``accepted: no``); once both exist the contract advances ``running → output``.
* **accept** — the two-step gate: flip ``accepted: no → yes`` on BOTH deliverables.
* **close** — guard (``report`` + ``proof`` accepted **and** a non-empty
  ``delta.md``; ``-f`` overrides) → ``cannon.merge`` the delta into CANON.md →
  bump the cannon-rev → state ``close``. Merging is the orchestrator-only
  serialization point, so the CLI handler is role-gated (the logic stays gate-free
  for testability, mirroring ``cannon merge``).
* **reopen** — reverse a close: ``close → running``.
* **state** — manual transition by key (``draft|sign|running|output|close``).

Logic is plain functions; :func:`register` wires the thin handlers ``cli.py`` calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from .. import fields, paths, placeholders, slug, strictness
from ..cannon import merge
from . import ask as ask_mod
from . import model

REPORT_FILE = "report.md"
PROOF_FILE = "proof.md"


# --- new -------------------------------------------------------------------

def new(
    root: Path,
    arc_ref: str,
    *,
    contract_slug: Optional[str] = None,
    goal: Optional[str] = None,
    criteria: Optional[str] = None,
    goal_slug: Optional[str] = None,
) -> Path:
    """Draft a contract bound to an arc (one-per-arc guard), state ``draft``.

    Resolves the arc, refuses a second ``contract.md``, seeds the passport + an
    empty ``delta.md`` + an ``asks/`` dir, and stamps the current cannon-rev.
    Returns the new ``contract.md`` path.
    """
    arc_dir = model.resolve_arc_dir(root, arc_ref, goal_slug=goal_slug)
    if model.has_contract(arc_dir):
        raise model.ContractError(
            "arc {0!r} already has a contract (one contract per arc) — "
            "edit it or supersede".format(arc_dir.name)
        )
    cslug = slug.slugify(contract_slug) if contract_slug else slug.entry_slug(arc_dir.name)
    if not cslug:
        raise model.ContractError("contract new: empty slug after slugify")

    from ..cannon import rev

    cannon_rev = rev.compute(root)
    # Store the PORTABLE project name, not the absolute path — a baked
    # `/Users/<me>/…` would leak this instance into every passport (tool ⊥
    # instance). Mirrors the codebase's `.name` portability pattern (init/scaffold).
    project = Path(root).resolve().name
    text = model.contract_md(
        cslug, goal=goal, criteria=criteria, project=project, cannon_rev=cannon_rev
    )
    cpath = model.contract_path(arc_dir)
    cpath.write_text(text, encoding="utf-8")

    # Stage an empty delta + the durable asks/ home.
    dpath = model.delta_path(arc_dir)
    if not dpath.is_file():
        dpath.write_text("# delta — {0}\nmerged: no\n\n".format(cslug), encoding="utf-8")
    model.asks_dir(arc_dir).mkdir(parents=True, exist_ok=True)
    return cpath


# --- sign ------------------------------------------------------------------

def sign(
    root: Path,
    arc_ref: str,
    *,
    signer: Optional[str] = None,
    goal_slug: Optional[str] = None,
    date: Optional[str] = None,
) -> str:
    """Sign a draft contract — respecting strictness — and move it to ``running``.

    ``strict`` ⇒ the human signs (default signer ``human``); ``loose`` ⇒ the
    orchestrator stamps synchronously (default signer ``orchestrator``). An explicit
    *signer* overrides the default in either mode. Stamps ``sign: <who> @ <date>``
    and sets state ``running``. Refuses anything but a ``draft`` contract.
    Returns the stamp string.
    """
    arc_dir = model.resolve_arc_dir(root, arc_ref, goal_slug=goal_slug)
    if not model.has_contract(arc_dir):
        raise model.ContractError("no contract on arc {0!r} to sign".format(arc_dir.name))
    state = model.read_state(arc_dir)
    if state != model.DRAFT:
        raise model.ContractError(
            "can only sign a draft contract (arc {0!r} is in state {1!r})".format(
                arc_dir.name, state
            )
        )
    who = (signer or "").strip() or ("human" if strictness.is_strict(root) else "orchestrator")
    stamp = "{0} @ {1}".format(who, date or model.today())
    model.set_field(arc_dir, "sign", stamp)
    model.set_state(arc_dir, model.RUNNING)
    return stamp


# --- report / proof --------------------------------------------------------

def _deliverable_md(kind: str, cslug: str, body: Optional[str]) -> str:
    """Render a ``report.md`` / ``proof.md`` deliverable with ``accepted: no``."""
    text = (body or "").strip()
    if not text:
        text = (
            "<what was done>" if kind == "report" else "<criteria evidence — how each is met>"
        )
    return (
        "# {kind} — {slug}\n"
        "contract: {slug}\n"
        "accepted: no\n"
        "\n"
        "{body}\n"
    ).format(kind=kind, slug=cslug, body=text)


def _maybe_advance_to_output(arc_dir: Path) -> None:
    """Advance ``running → output`` once BOTH report.md and proof.md exist."""
    have_both = (arc_dir / REPORT_FILE).is_file() and (arc_dir / PROOF_FILE).is_file()
    if have_both and model.read_state(arc_dir) == model.RUNNING:
        model.set_state(arc_dir, model.OUTPUT)


def report(
    root: Path,
    arc_ref: str,
    *,
    body: Optional[str] = None,
    goal_slug: Optional[str] = None,
) -> Path:
    """Write the arc's ``report.md`` (what was done), ``accepted: no``.

    Advances the contract to ``output`` once both report and proof exist. Returns
    the report path. Requires a contract on the arc.
    """
    arc_dir = model.resolve_arc_dir(root, arc_ref, goal_slug=goal_slug)
    if not model.has_contract(arc_dir):
        raise model.ContractError("no contract on arc {0!r}".format(arc_dir.name))
    cslug = model.contract_slug(arc_dir)
    path = arc_dir / REPORT_FILE
    path.write_text(_deliverable_md("report", cslug, body), encoding="utf-8")
    _maybe_advance_to_output(arc_dir)
    return path


def proof(
    root: Path,
    arc_ref: str,
    *,
    body: Optional[str] = None,
    goal_slug: Optional[str] = None,
) -> Path:
    """Write the arc's ``proof.md`` (criteria evidence), ``accepted: no``.

    Advances the contract to ``output`` once both report and proof exist. Returns
    the proof path. Requires a contract on the arc.
    """
    arc_dir = model.resolve_arc_dir(root, arc_ref, goal_slug=goal_slug)
    if not model.has_contract(arc_dir):
        raise model.ContractError("no contract on arc {0!r}".format(arc_dir.name))
    cslug = model.contract_slug(arc_dir)
    path = arc_dir / PROOF_FILE
    path.write_text(_deliverable_md("proof", cslug, body), encoding="utf-8")
    _maybe_advance_to_output(arc_dir)
    return path


# --- accept ----------------------------------------------------------------

def _accepted(arc_dir: Path, fname: str) -> Optional[str]:
    from .. import fields

    p = arc_dir / fname
    return fields.read_field(p, "accepted") if p.is_file() else None


def accept(root: Path, arc_ref: str, *, goal_slug: Optional[str] = None) -> Path:
    """Flip ``accepted: no → yes`` on BOTH ``report.md`` and ``proof.md``.

    Requires both deliverables to exist (the two-step gate before a close).
    Returns the arc dir. Idempotent — accepting an already-accepted pair is a no-op.
    """
    from .. import fields

    arc_dir = model.resolve_arc_dir(root, arc_ref, goal_slug=goal_slug)
    missing = [f for f in (REPORT_FILE, PROOF_FILE) if not (arc_dir / f).is_file()]
    if missing:
        raise model.ContractError(
            "cannot accept: arc {0!r} is missing {1} — write report+proof first".format(
                arc_dir.name, " + ".join(missing)
            )
        )
    for fname in (REPORT_FILE, PROOF_FILE):
        fields.set_field(arc_dir / fname, "accepted", "yes")
    return arc_dir


# --- close / reopen --------------------------------------------------------

def _delta_nonempty(arc_dir: Path) -> bool:
    """True when ``delta.md`` carries a real (non-frontmatter) body to merge."""
    dpath = model.delta_path(arc_dir)
    if not dpath.is_file():
        return False
    return bool(merge._delta_body(dpath.read_text(encoding="utf-8")).strip())


def close(
    root: Path,
    arc_ref: str,
    *,
    force: bool = False,
    goal_slug: Optional[str] = None,
    date: Optional[str] = None,
) -> str:
    """Close a contract AND seal its arc — one unified step (dogfood fix F3).

    Guard (skipped with *force*): the committed ``contract.md`` carries no leftover
    scaffold placeholder (``<…>`` spans / the ``# supersedes:`` hint — fix F5) AND
    ``report.md`` AND ``proof.md`` both ``accepted: yes`` AND a non-empty
    ``delta.md``. Then, in order:

    1. :func:`cannon.merge.merge_delta` routes the delta into CANON.md and returns
       the bumped (post-merge) cannon-rev.
    2. The arc is **sealed** like ``tide arc close`` — renamed to ``__…__`` and its
       passport stamped ``status: done`` (via :func:`tide.arc.stream.close`, the
       empty-output guard forced since the contract's report/proof/delta ARE the
       arc's auditable output). Sealing is idempotent: an arc already closed (e.g.
       a manual ``arc close`` first) is left as-is.
    3. The **post-merge** cannon-rev is re-stamped onto the arc's passport (and the
       contract), so the just-merged arc does NOT show drift against the canon it
       authored — killing the two-phase footgun + post-merge self-drift.
    4. The contract state flips to ``close``.

    Returns the new cannon-rev. ``tide arc close`` stays for arcs without a
    contract; for a contracted arc this is the single sealing path.

    NOTE: merging is orchestrator-only — the CLI handler calls
    ``require_orchestrator`` before this runs; the logic stays gate-free so it is
    unit-testable (mirrors ``cannon merge`` / ``candidate promote``).
    """
    from ..arc import stream

    arc_dir = model.resolve_arc_dir(root, arc_ref, goal_slug=goal_slug)
    if not model.has_contract(arc_dir):
        raise model.ContractError("no contract on arc {0!r} to close".format(arc_dir.name))

    if not force:
        # Scaffold-placeholder guard (dogfood fix F5): refuse to seal a contract
        # whose committed contract.md still carries `<…>` template spans or the
        # `# supersedes:` hint, so the merged passport never reads like a form.
        leftovers = placeholders.find_in_file(model.contract_path(arc_dir))
        if leftovers:
            raise model.ContractError(
                placeholders.refuse_message(model.CONTRACT_FILE, arc_dir.name, leftovers)
            )

    if not force:
        reasons: List[str] = []
        if _accepted(arc_dir, REPORT_FILE) != "yes":
            reasons.append("report.md not accepted")
        if _accepted(arc_dir, PROOF_FILE) != "yes":
            reasons.append("proof.md not accepted")
        if not _delta_nonempty(arc_dir):
            reasons.append("delta.md is empty")
        if reasons:
            raise model.ContractError(
                "cannot close {0!r}: {1} (accept report+proof and write a delta "
                "first, or override with close -f)".format(arc_dir.name, "; ".join(reasons))
            )

    cslug = model.contract_slug(arc_dir)
    new_rev = merge.merge_delta(root, arc_dir, slug=cslug, date=date)

    # Seal the arc unless it was already closed (idempotent — supports a prior
    # manual `arc close`). The contract guard above already vouched for the
    # deliverables, so the stream's empty-output guard is forced.
    if not slug.is_closed_entry(arc_dir.name):
        arc_dir = stream.close(
            root, slug.entry_slug(arc_dir.name), goal_slug=goal_slug, force=True
        )

    # Re-stamp the POST-merge rev onto the arc passport (the drift anchor) AND the
    # contract, so the arc that authored this canon shows no self-drift.
    fields.set_field(stream.passport_path(arc_dir), "cannon-rev", new_rev)
    model.set_field(arc_dir, "cannon-rev", new_rev)
    model.set_state(arc_dir, model.CLOSE)
    return new_rev


def reopen(root: Path, arc_ref: str, *, goal_slug: Optional[str] = None) -> str:
    """Reverse a unified close: un-seal the arc AND ``close → running``.

    Mirrors :func:`close` (fix F3): since close now seals the arc (``__…__`` +
    ``status: done``), reopen un-seals it (strip the marker + ``status: active``,
    via :func:`tide.arc.stream.reopen`) before flipping the contract back to
    ``running`` — so the arc seal and the contract state never disagree. Returns
    the new state (``running``).
    """
    from ..arc import stream

    arc_dir = model.resolve_arc_dir(root, arc_ref, goal_slug=goal_slug)
    if not model.has_contract(arc_dir):
        raise model.ContractError("no contract on arc {0!r} to reopen".format(arc_dir.name))
    if model.read_state(arc_dir) != model.CLOSE:
        raise model.ContractError(
            "can only reopen a closed contract (arc {0!r} is in state {1!r})".format(
                arc_dir.name, model.read_state(arc_dir)
            )
        )
    if slug.is_closed_entry(arc_dir.name):
        arc_dir = stream.reopen(root, slug.entry_slug(arc_dir.name), goal_slug=goal_slug)
    return model.set_state(arc_dir, model.RUNNING)


# --- state (manual transition) ---------------------------------------------

def transition(root: Path, arc_ref: str, key: str, *, goal_slug: Optional[str] = None) -> str:
    """Manually set the contract state to *key* (``draft|sign|running|output|close``)."""
    arc_dir = model.resolve_arc_dir(root, arc_ref, goal_slug=goal_slug)
    if not model.has_contract(arc_dir):
        raise model.ContractError("no contract on arc {0!r}".format(arc_dir.name))
    return model.set_state(arc_dir, key)


# --- list ------------------------------------------------------------------

def list_contracts(root: Path) -> List[Dict[str, object]]:
    """Scan the work stream for arcs carrying a ``contract.md``; one dict each.

    Returns ``{arc, slug, state, sign, path}`` ordered by entry dir name. Includes
    closed (``__…__``) arcs. Goal substreams are scanned one level deep.
    """
    out: List[Dict[str, object]] = []
    arcs = paths.arcs_dir(root)
    if not arcs.is_dir():
        return out

    def _scan(stream_dir: Path) -> None:
        for entry in sorted(stream_dir.iterdir()):
            if not entry.is_dir() or entry.name == paths.CANDIDATES_DIRNAME:
                continue
            if model.has_contract(entry):
                out.append(
                    {
                        "arc": entry.name,
                        "slug": model.contract_slug(entry),
                        "state": model.read_state(entry),
                        "sign": model.read_field(entry, "sign") or "",
                        "path": model.contract_path(entry),
                    }
                )
            # one level of goal substream
            sub = entry / paths.ARCS_DIRNAME
            if slug.is_goal_entry(entry.name) and sub.is_dir():
                for s in sorted(sub.iterdir()):
                    if s.is_dir() and model.has_contract(s):
                        out.append(
                            {
                                "arc": "{0}/{1}".format(entry.name, s.name),
                                "slug": model.contract_slug(s),
                                "state": model.read_state(s),
                                "sign": model.read_field(s, "sign") or "",
                                "path": model.contract_path(s),
                            }
                        )

    _scan(arcs)
    return out


def render_list(root: Path) -> str:
    """One-line-per-contract rendering (``<arc>  <state>  <slug>``)."""
    items = list_contracts(root)
    if not items:
        return "(no contracts)"
    lines: List[str] = []
    for it in items:
        lines.append(
            "{arc}  [{state}]  {slug}".format(
                arc=it["arc"], state=it["state"], slug=it["slug"]
            )
        )
    return "\n".join(lines)


# --- CLI wiring ------------------------------------------------------------

def _root() -> Path:
    return paths.require_tide_root()


def _cmd_new(args) -> int:
    cpath = new(
        _root(),
        args.arc,
        contract_slug=args.slug,
        goal=args.goal,
        criteria=args.criteria,
        goal_slug=args.in_goal,
    )
    print("tide: drafted contract {0} (state: draft)".format(cpath))
    return 0


def _cmd_sign(args) -> int:
    stamp = sign(_root(), args.arc, signer=args.signer, goal_slug=args.in_goal)
    print("tide: signed → running (sign: {0})".format(stamp))
    return 0


def _cmd_report(args) -> int:
    body = " ".join(args.text) if args.text else None
    path = report(_root(), args.arc, body=body, goal_slug=args.in_goal)
    print("tide: wrote {0} (accepted: no)".format(path.name))
    return 0


def _cmd_proof(args) -> int:
    body = " ".join(args.text) if args.text else None
    path = proof(_root(), args.arc, body=body, goal_slug=args.in_goal)
    print("tide: wrote {0} (accepted: no)".format(path.name))
    return 0


def _cmd_accept(args) -> int:
    accept(_root(), args.arc, goal_slug=args.in_goal)
    print("tide: accepted report+proof (accepted: yes)")
    return 0


def _cmd_close(args) -> int:
    # cli.main wraps RoleError → exit 1; import lazily to avoid an import cycle.
    from ..cli import require_orchestrator

    require_orchestrator("contract close")
    new_rev = close(_root(), args.arc, force=args.force, goal_slug=args.in_goal)
    print("tide: closed contract → cannon-rev {0} (state: close)".format(new_rev))
    return 0


def _cmd_reopen(args) -> int:
    state = reopen(_root(), args.arc, goal_slug=args.in_goal)
    print("tide: reopened contract (state: {0})".format(state))
    return 0


def _cmd_state(args) -> int:
    state = transition(_root(), args.arc, args.key, goal_slug=args.in_goal)
    print("tide: contract state → {0}".format(state))
    return 0


def _cmd_list(args) -> int:
    print(render_list(_root()))
    return 0


def _cmd_ask(args) -> int:
    body = " ".join(args.text) if args.text else None
    path = ask_mod.ask(
        _root(), args.arc, args.slug, question=body, from_ref=args.from_ref, goal_slug=args.in_goal
    )
    print("tide: dropped ask {0} (state: open)".format(path.name))
    return 0


def _cmd_answer(args) -> int:
    body = " ".join(args.text) if args.text else None
    path = ask_mod.answer(_root(), args.arc, args.key, answer=body, goal_slug=args.in_goal)
    print("tide: answered {0} (state: answered)".format(path.name))
    return 0


def _add_in_goal(p) -> None:
    p.add_argument("-g", "--in-goal", help="resolve the arc inside this goal's substream")


def register(subparsers) -> None:
    """Add the ``contract`` command group to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser("contract", help="worker→arc binding: lifecycle + ask/answer")
    csub = p.add_subparsers(dest="contract_cmd")

    np = csub.add_parser("new", help="draft a contract bound to an arc (one per arc)")
    np.add_argument("arc", help="arc ref (dir name or slug) the contract binds to")
    np.add_argument("--slug", help="contract slug (default: the arc's slug)")
    np.add_argument("--goal", help="goal — one line")
    np.add_argument("--criteria", help="acceptance criteria (done-when)")
    _add_in_goal(np)
    np.set_defaults(func=_cmd_new, _cmd="contract new")

    sp = csub.add_parser("sign", help="sign (strict=human / loose=synchronous) → running")
    sp.add_argument("arc")
    sp.add_argument("--signer", help="who signs (default: human under strict, orchestrator under loose)")
    _add_in_goal(sp)
    sp.set_defaults(func=_cmd_sign, _cmd="contract sign")

    rp = csub.add_parser("report", help="write report.md (what was done)")
    rp.add_argument("arc")
    rp.add_argument("text", nargs="*", help="report body")
    _add_in_goal(rp)
    rp.set_defaults(func=_cmd_report, _cmd="contract report")

    pp = csub.add_parser("proof", help="write proof.md (criteria evidence)")
    pp.add_argument("arc")
    pp.add_argument("text", nargs="*", help="proof body")
    _add_in_goal(pp)
    pp.set_defaults(func=_cmd_proof, _cmd="contract proof")

    ap = csub.add_parser("accept", help="flip report+proof accepted:no→yes")
    ap.add_argument("arc")
    _add_in_goal(ap)
    ap.set_defaults(func=_cmd_accept, _cmd="contract accept")

    clp = csub.add_parser("close", help="ORCHESTRATOR-ONLY: guard + merge delta → state=close")
    clp.add_argument("arc")
    clp.add_argument("-f", "--force", action="store_true", help="skip the close guard")
    _add_in_goal(clp)
    clp.set_defaults(func=_cmd_close, _cmd="contract close")

    rop = csub.add_parser("reopen", help="undo a close (close → running)")
    rop.add_argument("arc")
    _add_in_goal(rop)
    rop.set_defaults(func=_cmd_reopen, _cmd="contract reopen")

    stp = csub.add_parser("state", help="manual transition by key (draft|sign|running|output|close)")
    stp.add_argument("arc")
    stp.add_argument("key", choices=model.STATES)
    _add_in_goal(stp)
    stp.set_defaults(func=_cmd_state, _cmd="contract state")

    lp = csub.add_parser("list", help="list contracts with state + bound arc")
    lp.set_defaults(func=_cmd_list, _cmd="contract list")

    qp = csub.add_parser("ask", help="drop a durable open-question on the arc")
    qp.add_argument("arc")
    qp.add_argument("slug", help="ask slug")
    qp.add_argument("--from", dest="from_ref", help="origin ref (recorded as from:)")
    qp.add_argument("text", nargs="*", help="the question")
    _add_in_goal(qp)
    qp.set_defaults(func=_cmd_ask, _cmd="contract ask")

    anp = csub.add_parser("answer", help="answer an open ask (state → answered)")
    anp.add_argument("arc")
    anp.add_argument("key", help="ask NN, NN-slug, or slug")
    anp.add_argument("text", nargs="*", help="the answer")
    _add_in_goal(anp)
    anp.set_defaults(func=_cmd_answer, _cmd="contract answer")

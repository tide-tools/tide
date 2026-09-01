"""tide.arc.decision — the ``decision`` entity: a thread's conclusion-atoms.

Work is the unit of *doing*; a **decision** is the unit of *concluding*. The
relation that names it: ``decision : canon :: commit : repo`` — a decision is a
diff to canon with a rationale; canon is the accumulated state. Until this module
decisions had no home — they smeared across ``offload``/``context``, ``plan.md``
wave descriptions and candidate bodies, so (a) a thread's conclusions never rode
into another session's context and (b) an agent re-litigated what was already
settled (CLAUDE.md forbids it, but a smeared decision is invisible). Candidate
128-B (from the ``cross-session-experience`` nit) named the entity; this is it.

Home = ONE file per thread, ``<thread>/decisions.md`` (a log, unlike candidates'
one-file-each): decisions are taken INSIDE a nit, optionally linked to a work.
The record shape mirrors the live reference the press nit wrote by hand
(``press/.tide/arcs/01-@cross-session-experience/decisions.md``):

    ## NN — <slug>
    thread / work / status(open|settled|superseded, +supersedes) / what / why /
    closes / description

Lifecycle → canon: ``open`` lives in the nit → ``settled`` rises into the
project's ``CANON.md`` journal → ``superseded`` stays history. ``closes`` is the
anti-re-litigation body (what this decision closes); ``description`` is the recall
descriptor a future selective-assembly pass (candidate 131, wave 2) will match on.

All logic is plain functions (argparse-free, unit-testable); :func:`register`
wires the thin CLI handlers.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .. import fields, io as _io, paths, resolve, slug
from . import stream

DECISIONS_FILE = "decisions.md"

# A record header: ``## NN — slug`` (em-dash or hyphen tolerated, 2+ digit NN).
_DEC_RE = re.compile(r"^##\s+(\d{2,})\s+[—-]\s+(.+?)\s*$")

# The status vocabulary + the ordered fields of a record body.
_STATUSES = ("open", "settled", "superseded")
_BODY_FIELDS = ("thread", "work", "status", "what", "why", "closes", "description")


class DecisionError(stream.StreamError):
    """A user-facing decisions error (unknown thread, empty text, bad key …).

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it
    with the same ``except`` arm (prints ``tide: …``, exits nonzero).
    """


# --- thread + file resolution ----------------------------------------------

def _thread_dir(root: Path, thread_ref: Optional[str]) -> Path:
    """Resolve the thread dir a decision belongs to (its ``decisions.md`` home).

    Explicit *thread_ref* resolves via the shared matcher (open top-stream entry,
    goal preferred). Omitted, we fall back to the CALLER's own nit — the session
    arc pinned to ``$CLAUDE_CODE_SESSION_ID`` → its ``NN-@thread`` container — so
    ``tide decision add`` from inside a session just records against that nit
    (the dogfood ergonomic). Raises when neither resolves.
    """
    if thread_ref:
        entry = resolve.open_top_entry(root, thread_ref)
        if entry is None:
            raise DecisionError(
                "decision: no open thread matching {0!r} in {1}".format(
                    thread_ref, paths.arcs_dir(root))
            )
        return entry
    caller = _caller_thread_dir(root)
    if caller is None:
        raise DecisionError(
            "decision: no thread given and no caller session to infer one "
            "(pass --thread <nit>)"
        )
    return caller


def _caller_thread_dir(root: Path) -> Optional[Path]:
    """The ``NN-@thread`` dir of the session that invoked us, or None.

    Reads ``$CLAUDE_CODE_SESSION_ID`` → the session arc pinned to it →
    its thread container (``…/NN-@thread/arcs/NN-session`` → ``parents[1]``).
    """
    import os

    sid = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    if not sid:
        return None
    from .. import offload  # lazy: avoid an import cycle at module load

    entry = offload.find_session_by_claude_id(root, sid)
    if entry is None:
        return None
    try:
        return entry.parents[1]  # …/NN-@thread/arcs/NN-session
    except IndexError:
        return None


def decisions_file(thread_dir: Path) -> Path:
    """Path to a thread's decisions log (``<thread>/decisions.md``)."""
    return Path(thread_dir) / DECISIONS_FILE


# --- template + record rendering -------------------------------------------

def _header(thread_slug: str) -> str:
    """The one-time preamble of a fresh ``decisions.md`` (format + lifecycle)."""
    return (
        "# decisions — {slug}\n"
        "\n"
        "Дом решений этой нити. Решение принимается ВНУТРИ нити, опц. связано с\n"
        "работой. Осевшее (`status: settled`) поднимается в `## journal` канона\n"
        "проекта (решение → canon, как коммит → репо). `superseded` остаётся\n"
        "историей.\n"
        "\n"
        "Формат:\n"
        "  ## NN — <slug>\n"
        "  thread / work / status (+supersedes) / what / why / closes / description\n"
        "\n"
        "---\n"
    ).format(slug=thread_slug)


def _record(num: str, dslug: str, rec: Dict[str, str]) -> str:
    """Render one ``## NN — slug`` decision block from its fields."""
    def _val(key: str) -> str:
        return (rec.get(key) or "").strip() or "—"

    return (
        "\n"
        "## {num} — {slug}\n"
        "thread: {thread}\n"
        "work: {work}\n"
        "status: {status}\n"
        "what:  {what}\n"
        "why:   {why}\n"
        "closes: {closes}\n"
        "description: {description}\n"
    ).format(
        num=num, slug=dslug, thread=_val("thread"), work=_val("work"),
        status=(rec.get("status") or "open").strip(), what=_val("what"),
        why=_val("why"), closes=_val("closes"), description=_val("description"),
    )


# --- numbering + parse ------------------------------------------------------

def _next_num(text: str) -> str:
    """Next 2-digit record number for a decisions log (max existing + 1)."""
    nums = [int(m.group(1)) for m in (_DEC_RE.match(ln) for ln in text.splitlines()) if m]
    return "{0:02d}".format((max(nums) + 1) if nums else 1)


def list_decisions(root: Path, thread_ref: Optional[str] = None) -> List[Dict[str, object]]:
    """Parse a thread's decisions log into records (in file order).

    Each record carries ``num``/``slug`` plus the body fields (``thread``/``work``/
    ``status``/``what``/``why``/``closes``/``description``). Returns ``[]`` when the
    thread has no log yet. Raises when the thread ref doesn't resolve.
    """
    tdir = _thread_dir(root, thread_ref)
    f = decisions_file(tdir)
    if not f.is_file():
        return []
    out: List[Dict[str, object]] = []
    cur: Optional[Dict[str, object]] = None
    for ln in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        h = _DEC_RE.match(ln)
        if h:
            cur = {"num": h.group(1), "slug": h.group(2)}
            for k in _BODY_FIELDS:
                cur[k] = ""
            out.append(cur)
            continue
        if cur is None:
            continue
        fm = re.match(r"^([a-z]+):\s*(.*)$", ln)
        if fm and fm.group(1) in _BODY_FIELDS:
            cur[fm.group(1)] = fm.group(2).strip()
    return out


def render_list(root: Path, thread_ref: Optional[str] = None) -> str:
    """One-line-per-decision rendering (``NN slug · status · what``)."""
    items = list_decisions(root, thread_ref)
    if not items:
        return "(no decisions)"
    lines: List[str] = []
    for it in items:
        lines.append("{num} {slug}  · {status} · {what}".format(
            num=it["num"], slug=it["slug"],
            status=it.get("status") or "open", what=it.get("what") or ""))
    return "\n".join(lines)


# The context-injection cap (128-A, bounded per 131): never pour the whole log —
# a nit's OPEN decisions are the anti-re-litigation set, newest-first, capped.
CONTEXT_CAP = 7

CONTEXT_HEADER = "### Решения этой нити (открытые — уже решено, не перерешивать)"


def render_open_for_context(
    root: Path, thread_ref: Optional[str] = None, cap: int = CONTEXT_CAP
) -> str:
    """Compact block of a nit's OPEN decisions for seed/hook context (cand 128-A).

    The anti-re-litigation set — what THIS nit already concluded and hasn't retired
    — newest-first, capped at *cap* (131: bounded, never the whole log). Settled
    decisions live in canon (the floor, always read) and are NOT re-injected here;
    cross-nit / relevance-matched injection stays wave-2. Returns ``""`` when there
    are no open decisions, so a caller drops the block rather than inject noise.
    """
    try:
        items = list_decisions(root, thread_ref)
    except DecisionError:
        return ""
    openish = [d for d in items if (d.get("status") or "open") == "open"]
    if not openish:
        return ""
    shown = list(reversed(openish))[:cap]  # newest-first, capped
    lines = [CONTEXT_HEADER]
    for d in shown:
        lines.append("- {0} {1} — {2}".format(d["num"], d["slug"], d.get("what") or ""))
    if len(openish) > len(shown):
        lines.append("- (+{0} ещё в decisions.md)".format(len(openish) - len(shown)))
    return "\n".join(lines)


# --- add --------------------------------------------------------------------

def add_decision(
    root: Path,
    what: str,
    *,
    thread_ref: Optional[str] = None,
    why: Optional[str] = None,
    closes: Optional[str] = None,
    description: Optional[str] = None,
    work: Optional[str] = None,
    dslug: Optional[str] = None,
) -> "tuple[Path, str, str]":
    """Append a decision to its thread's ``decisions.md``; return (file, num, slug).

    *what* is the decision itself (required). *thread_ref* selects the nit
    (default: the caller's own, see :func:`_thread_dir`). The record slug is
    *dslug* when given, else a short handle off *what*. A fresh log gets the
    format/lifecycle header first. New decisions are born ``open``.
    """
    text = (what or "").strip()
    if not text:
        raise DecisionError("decision: empty text — say what was decided")
    tdir = _thread_dir(root, thread_ref)
    thread_slug = slug.entry_slug(tdir.name)
    dslug = slug.slugify(dslug) if dslug else slug.short_slug(text)
    if not dslug:
        raise DecisionError("decision: empty slug after slugify — pass --slug")

    f = decisions_file(tdir)
    body = f.read_text(encoding="utf-8", errors="ignore") if f.is_file() else _header(thread_slug)
    num = _next_num(body)
    rec = {
        "thread": thread_slug, "work": work, "status": "open",
        "what": text, "why": why, "closes": closes, "description": description,
    }
    _io.atomic_write(f, body + _record(num, dslug, rec))
    return f, num, dslug


# --- lifecycle: settle → canon journal · supersede → history ---------------

def _resolve_key(root: Path, thread_ref: Optional[str], key: str) -> Dict[str, object]:
    """Return the decision record matching *key* (NN / NN-slug / slug), or raise."""
    raw = (key or "").strip().lstrip("-")
    want = slug.slugify(raw)
    for d in list_decisions(root, thread_ref):
        num, dslug = str(d["num"]), str(d["slug"])
        if raw.isdigit() and int(raw) == int(num):
            return d
        if raw == "{0}-{1}".format(num, dslug):
            return d
        if want and want == dslug:
            return d
    raise DecisionError("decision: no decision matching {0!r} in the thread".format(key))


def _edit_block(text: str, num: str, transform) -> str:
    """Apply *transform* to the ``## NN — …`` record block for *num* in *text*."""
    pat = re.compile(r"(^## " + re.escape(num) + r" — .*?)(?=^## |\Z)", re.M | re.S)
    m = pat.search(text)
    if not m:
        raise DecisionError("decision: record {0} not found on disk".format(num))
    return text[:m.start()] + transform(m.group(1)) + text[m.end():]


def _set_status_line(block: str, new_status: str, extra: Optional[str] = None) -> str:
    """Rewrite a block's ``status:`` line (and optionally add one line after it)."""
    block = re.sub(r"^status:.*$", "status: {0}".format(new_status), block, count=1, flags=re.M)
    if extra:
        block = re.sub(r"^(status:.*)$", r"\1\n" + extra, block, count=1, flags=re.M)
    return block


def _append_canon_journal(root: Path, line: str) -> None:
    """Append *line* under ``## Canon journal`` (the terminal section) of CANON.md."""
    canon = paths.canon_file(root)
    if not canon.is_file():
        raise DecisionError("decision settle: no canon (run 'tide canon init')")
    text = canon.read_text(encoding="utf-8")
    if "## Canon journal" not in text and "## Cannon journal" not in text:
        raise DecisionError("decision settle: CANON.md has no journal section")
    _io.atomic_write(canon, "{0}\n{1}\n".format(text.rstrip("\n"), line))


def settle(
    root: Path,
    key: str,
    *,
    thread_ref: Optional[str] = None,
    now: Optional[datetime] = None,
) -> "tuple[str, str]":
    """Settle a decision: status → ``settled`` + a line in the canon journal.

    This is the one-way promotion strand→canon (decision:canon :: commit:repo): the
    conclusion leaves the nit's open log and lands in the project's durable truth.
    Returns ``(num, slug)``. The CLI handler gates this orchestrator-only.
    """
    rec = _resolve_key(root, thread_ref, key)
    num, dslug, what = str(rec["num"]), str(rec["slug"]), str(rec["what"])
    thread = str(rec["thread"]) or slug.entry_slug(_thread_dir(root, thread_ref).name)
    f = decisions_file(_thread_dir(root, thread_ref))
    text = f.read_text(encoding="utf-8")
    _io.atomic_write(f, _edit_block(text, num, lambda b: _set_status_line(b, "settled")))
    stamp = (now or datetime.now()).strftime("%Y-%m-%d")
    _append_canon_journal(
        root, "- {0} · decision {1}/{2} {3} — {4}".format(stamp, thread, num, dslug, what))
    return num, dslug


def supersede(
    root: Path,
    key: str,
    by: str,
    *,
    thread_ref: Optional[str] = None,
) -> "tuple[str, str]":
    """Supersede a decision: status → ``superseded`` + a ``superseded-by:`` line.

    *by* names what replaces it (a decision ref or a short note) — the record stays
    as history (never deleted), pointing forward. Returns ``(num, slug)``.
    """
    b = (by or "").strip()
    if not b:
        raise DecisionError("decision supersede: say what supersedes it (--by)")
    rec = _resolve_key(root, thread_ref, key)
    num, dslug = str(rec["num"]), str(rec["slug"])
    f = decisions_file(_thread_dir(root, thread_ref))
    text = f.read_text(encoding="utf-8")
    _io.atomic_write(f, _edit_block(
        text, num,
        lambda blk: _set_status_line(blk, "superseded", extra="superseded-by: {0}".format(b))))
    return num, dslug


# --- CLI wiring ------------------------------------------------------------

def _root() -> Path:
    return paths.require_tide_root()


def _cmd_add(args) -> int:
    what = " ".join(args.what) if args.what else ""
    f, num, dslug = add_decision(
        _root(), what,
        thread_ref=getattr(args, "thread", None),
        why=getattr(args, "why", None),
        closes=getattr(args, "closes", None),
        description=getattr(args, "description", None),
        work=getattr(args, "work", None),
        dslug=getattr(args, "slug", None),
    )
    print("tide: decision {0} — {1} → {2}".format(num, dslug, f))
    return 0


def _cmd_list(args) -> int:
    print(render_list(_root(), thread_ref=getattr(args, "thread", None)))
    return 0


def _cmd_settle(args) -> int:
    # Promotion strand→canon is a deliberate one-way valve — orchestrator-only,
    # exactly like `canon merge` / `candidate promote`. cli.main wraps RoleError.
    from ..cli import require_orchestrator

    require_orchestrator("decision settle")
    num, dslug = settle(_root(), args.key, thread_ref=getattr(args, "thread", None))
    print("tide: decision {0} — {1} settled → canon journal".format(num, dslug))
    return 0


def _cmd_supersede(args) -> int:
    num, dslug = supersede(
        _root(), args.key, args.by, thread_ref=getattr(args, "thread", None))
    print("tide: decision {0} — {1} superseded (by {2})".format(num, dslug, args.by))
    return 0


def register(subparsers) -> None:
    """Add the ``decision`` command group to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "decision", help="capture/list a thread's decisions (conclusion-atoms)")
    dsub = p.add_subparsers(dest="decision_cmd")

    ap = dsub.add_parser("add", help="record a decision in a thread's decisions.md")
    ap.add_argument("what", nargs="+", help="what was decided (the decision itself)")
    ap.add_argument("--thread", help="target nit (default: the caller session's own)")
    ap.add_argument("--work", help="optional work this decision is linked to")
    ap.add_argument("--why", help="rationale")
    ap.add_argument("--closes", help="what this decision closes (anti-re-litigation)")
    ap.add_argument("--description", help="recall descriptor for later injection")
    ap.add_argument("--slug", help="explicit record slug (default: short handle off <what>)")
    ap.set_defaults(func=_cmd_add, _cmd="decision add")

    lp = dsub.add_parser("list", help="list a thread's decisions")
    lp.add_argument("--thread", help="target nit (default: the caller session's own)")
    lp.set_defaults(func=_cmd_list, _cmd="decision list")

    sp = dsub.add_parser("settle", help="ORCHESTRATOR-ONLY: settle a decision → canon journal")
    sp.add_argument("key", help="decision NN, NN-slug, or slug")
    sp.add_argument("--thread", help="target nit (default: the caller session's own)")
    sp.set_defaults(func=_cmd_settle, _cmd="decision settle")

    up = dsub.add_parser("supersede", help="mark a decision superseded (stays as history)")
    up.add_argument("key", help="decision NN, NN-slug, or slug")
    up.add_argument("--by", required=True, help="what replaces it (a decision ref or note)")
    up.add_argument("--thread", help="target nit (default: the caller session's own)")
    up.set_defaults(func=_cmd_supersede, _cmd="decision supersede")

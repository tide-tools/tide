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
    thread / work / status / kind / done / proof / what / why / closes / description

**A decision carries two independent axes** (decision 28 of the release thread: a
rule without a machine check is not a rule):

    status:  is it still in force?   accepted · superseded · dropped
    done:    was it carried out?     a date, or empty

They are not the same question, and collapsing them is exactly what blinded this
thread: with one field, ``open`` looked like an answer while answering nothing, and
nine signed decisions sat unexecuted — three of them for years — with no way to
ask. Classic ADR practice collapses them safely because choosing a technology is
executed by the act of choosing; our decisions are promises with a WORK standing
between the signature and the world.

Who carries it is ``work:`` — a field this format has had from day one and had
never once been filled. ``proof:`` covers the case with no work (a commit, a
command that answers). ``kind: rule`` marks a standing criterion, which takes
effect on signature and will never have a work.

There is no automatic verification that a decision was executed, here or anywhere:
nobody has built one in fifteen years of ADR practice, and the strongest advice in
the field's own standards is "raise a ticket and follow it up". So the check does
not pretend to. It asks the cheap honest question underneath — *does this live
promise have an owner?* — and ``tide thread --check`` prints the ones that don't.

``settle`` stays the canon door: it marks the decision done *and* raises the line
into the project's ``CANON.md`` journal (decision : canon :: commit : repo).
``closes`` is the anti-re-litigation body (what this decision closes);
``description`` is the recall descriptor a future selective-assembly pass
(candidate 131, wave 2) will match on.

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

# --- TWO AXES, and they are genuinely independent -------------------------
#
# `status:` answers only Nygard's question — is this decision still in force?
# `done:` answers the other one — has anyone carried it out? Classic ADRs collapse
# the two because choosing a technology is executed by the act of choosing; OUR
# decisions are promises about the future, with a work standing between the
# signature and the world. Collapsing them is what let nine signed decisions sit
# unexecuted for years: `open` looked like an answer and was not one. Decentraland
# hit the same wall and bolted an extra status on (ADR-277); the two-field shape
# is the same conclusion without the bolt.

ACCEPTED = "accepted"
SUPERSEDED = "superseded"
DROPPED = "dropped"
STATUSES = (ACCEPTED, SUPERSEDED, DROPPED)
IN_FORCE = ACCEPTED

# Pre-state spellings, kept readable forever. `settled` and the `done` status of
# the first build of this module both meant "carried out", which is now the
# `done:` line — `normalize` moves them across so no log needs rewriting.
_LEGACY_STATUS = {"open": ACCEPTED, "settled": ACCEPTED, "done": ACCEPTED}
_LEGACY_MEANS_DONE = ("settled", "done")

# `kind:` — a commitment is a promise someone must carry out; a RULE is a standing
# criterion that takes effect the moment it is signed ("приёмка — живой проход",
# "у всякой записи назван потребитель"). A rule has no work and never will, so
# without this distinction the ownership check would print every rule, forever,
# and a check that cries wrongly gets switched off — which is worse than none.
COMMITMENT = "commitment"
RULE = "rule"
KINDS = (COMMITMENT, RULE)

_BODY_FIELDS = ("thread", "work", "status", "kind", "done", "proof", "what",
                "why", "closes", "description")

_EMPTY = ("", "—", "-")


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
        "работой. У решения ДВЕ независимые оси:\n"
        "\n"
        "  status:  в силе ли оно — accepted · superseded · dropped\n"
        "  done:    выполнено ли — дата, когда сделано (пусто = ещё нет)\n"
        "\n"
        "Это разные вопросы: решение бывает в силе и не выполнено годами. Кто\n"
        "выполняет — в `work:` (номер работы); чем доказано, когда работы нет —\n"
        "в `proof:`. `kind: rule` помечает стоячее правило: оно действует с\n"
        "подписи, работы у него нет и не будет.\n"
        "\n"
        "`tide decision settle` = done + строка в `## journal` канона проекта\n"
        "(решение → canon, как коммит → репо). Обещание без исполнителя находит\n"
        "`tide thread --check`.\n"
        "\n"
        "Формат:\n"
        "  ## NN — <slug>\n"
        "  thread / work / status / kind / done / proof / what / why / closes /\n"
        "  description\n"
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
        "kind: {kind}\n"
        "done: {done}\n"
        "proof: {proof}\n"
        "what:  {what}\n"
        "why:   {why}\n"
        "closes: {closes}\n"
        "description: {description}\n"
    ).format(
        num=num, slug=dslug, thread=_val("thread"), work=_val("work"),
        status=(rec.get("status") or ACCEPTED).strip(),
        kind=(rec.get("kind") or COMMITMENT).strip(), done=_val("done"),
        proof=_val("proof"), what=_val("what"), why=_val("why"),
        closes=_val("closes"), description=_val("description"),
    )


# --- numbering + parse ------------------------------------------------------

def _next_num(text: str) -> str:
    """Next 2-digit record number for a decisions log (max existing + 1)."""
    nums = [int(m.group(1)) for m in (_DEC_RE.match(ln) for ln in text.splitlines()) if m]
    return "{0:02d}".format((max(nums) + 1) if nums else 1)


def normalize_status(raw: str) -> str:
    """The canonical in-force word for a record's ``status:`` as written on disk.

    Maps every pre-state spelling onto the vocabulary and reads anything
    unrecognised — an empty field included — as :data:`ACCEPTED`, the safe answer:
    a recorded decision is at least in force. This axis says nothing about whether
    the decision was carried out; :func:`is_done` is that question.
    """
    s = (raw or "").strip().lower()
    if s in STATUSES:
        return s
    return _LEGACY_STATUS.get(s, ACCEPTED)


def in_force(rec: Dict[str, object]) -> bool:
    """True when the decision still binds (not superseded, not withdrawn)."""
    return rec.get("status") == ACCEPTED


def is_done(rec: Dict[str, object]) -> bool:
    """True when someone has said this decision was carried out.

    Reads the ``done:`` line, and treats the pre-two-axis words (``settled``, and
    the short-lived ``done`` status) as the same claim, so an untouched old log
    still answers the question it was always trying to answer.
    """
    if str(rec.get("done") or "").strip() not in _EMPTY:
        return True
    return str(rec.get("status_raw") or "").strip().lower() in _LEGACY_MEANS_DONE


def is_rule(rec: Dict[str, object]) -> bool:
    """True when the record is a standing criterion, not a promise to carry out.

    A rule takes effect on signature and has no work behind it, ever — so it is
    exempt from the ownership check. Everything else is a commitment by default:
    the safe direction, since a mislabelled commitment gets noticed by the check
    while a mislabelled rule would quietly escape it.
    """
    return str(rec.get("kind") or "").strip().lower() == RULE


def owner(rec: Dict[str, object]) -> str:
    """The work carrying this decision (``work:``), or ``""`` when none is named."""
    w = str(rec.get("work") or "").strip()
    return "" if w in _EMPTY else w


def unowned(rec: Dict[str, object]) -> bool:
    """True when a live commitment has nobody carrying it — the check's one rule.

    In force · not carried out · no work linked · not a standing rule. This is
    deliberately NOT a verification that the decision was executed — no such
    machine exists, and fifteen years of ADR practice never built one. It is the
    cheap, honest question underneath: does this promise have an owner? The
    strongest advice in the field's own standards is "raise a ticket and follow it
    up by hand", and ``work:`` has been in this format from the first day, unused.
    """
    return in_force(rec) and not is_done(rec) and not is_rule(rec) and not owner(rec)


def unproved(rec: Dict[str, object]) -> bool:
    """True when a carried-out (or withdrawn) record says nothing that shows it.

    ``done`` with neither a work nor a proof is a claim no reader can follow, which
    is the blindness this whole build exists to end.
    """
    if rec.get("status") == DROPPED:
        return str(rec.get("proof") or "").strip() in _EMPTY
    if not is_done(rec):
        return False
    return not owner(rec) and str(rec.get("proof") or "").strip() in _EMPTY


def list_decisions(root: Path, thread_ref: Optional[str] = None) -> List[Dict[str, object]]:
    """Parse a thread's decisions log into records (in file order).

    Each record carries ``num``/``slug`` plus the body fields (``thread``/``work``/
    ``status``/``proof``/``what``/``why``/``closes``/``description``). ``status`` is
    the CANONICAL state (:func:`normalize_status`); ``status_raw`` keeps the word as
    written, because "accepted" and "never stated" render the same after
    normalisation and the check has to tell them apart. Returns ``[]`` when the
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
    for rec in out:
        rec["status_raw"] = rec["status"]
        rec["status"] = normalize_status(str(rec["status"]))
    return out


def render_list(root: Path, thread_ref: Optional[str] = None,
                state: Optional[str] = None, closes: bool = False) -> str:
    """One-line-per-decision rendering (``NN slug · status · what``).

    *state* narrows to one word of the vocabulary — the ergonomic behind the
    thread screen's "+N more" pointer, so a reader who wants the full accepted
    pile can ask for exactly it instead of re-reading all of them.

    *closes* prints the anti-re-litigation body instead of the decision: what
    argument each record settled. That is the answer to "what has this thread
    already rejected" — the single question a cold session cannot answer without
    opening every record, and the one that makes it re-open settled arguments.
    """
    items = list_decisions(root, thread_ref)
    if state:
        want = (state or "").strip().lower()
        if want in STATUSES:
            items = [d for d in items if d.get("status") == want]
        elif want == "done":
            items = [d for d in items if is_done(d)]
        elif want in ("not-done", "undone"):
            items = [d for d in items if in_force(d) and not is_done(d)]
        elif want == "rule":
            items = [d for d in items if is_rule(d)]
        elif want == "unowned":
            items = [d for d in items if unowned(d)]
        else:
            raise DecisionError(
                "decision list: unknown filter {0!r} — one of: {1}, done, "
                "not-done, rule, unowned".format(state, ", ".join(STATUSES)))
    if closes:
        items = [d for d in items
                 if str(d.get("closes") or "").strip() not in ("", "—", "-")]
    if not items:
        return "(no decisions)"
    lines: List[str] = []
    for it in items:
        if closes:
            lines.append("{num} · {status} · closes: {closes}".format(
                num=it["num"], status=it.get("status"), closes=it.get("closes")))
            continue
        lines.append("{num} {slug}  · {status}{done}{who} · {what}".format(
            num=it["num"], slug=it["slug"], status=it.get("status"),
            done=" · done {0}".format(it["done"]) if is_done(it) else
                 (" · rule" if is_rule(it) else " · not done"),
            who=" · work {0}".format(owner(it)) if owner(it) else "",
            what=it.get("what") or ""))
    return "\n".join(lines)


# The context-injection cap (128-A, bounded per 131): never pour the whole log —
# a nit's OPEN decisions are the anti-re-litigation set, newest-first, capped.
CONTEXT_CAP = 7

CONTEXT_HEADER = "### Решения этой нити (открытые — уже решено, не перерешивать)"


def render_open_for_context(
    root: Path, thread_ref: Optional[str] = None, cap: int = CONTEXT_CAP
) -> str:
    """Compact block of a nit's OPEN decisions for seed/hook context (cand 128-A).

    The anti-re-litigation set — what THIS nit concluded and hasn't retired — is
    the in-force pile that is NOT yet carried out, newest-first, capped at *cap*
    (131: bounded, never the whole log). Carried-out decisions are already visible
    as the world they made and live in canon (the floor, always read);
    ``superseded``/``dropped`` are history. Cross-nit / relevance-matched injection
    stays wave-2. Returns ``""`` when there is nothing live, so a caller drops the
    block rather than inject noise.
    """
    try:
        items = list_decisions(root, thread_ref)
    except DecisionError:
        return ""
    openish = [d for d in items if in_force(d) and not is_done(d)]
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
    rule: bool = False,
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
        "thread": thread_slug, "work": work, "status": ACCEPTED,
        "kind": RULE if rule else COMMITMENT, "done": None, "proof": None,
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


def _set_line(block: str, key: str, value: str) -> str:
    """Set the ``key: value`` line of a record block — replaced in place, or added.

    Line surgery rather than ``re.sub`` with the value in the replacement string:
    the values here are human text (a proof reference, a supersede note) and
    ``re.sub`` would read a stray backslash or ``\\1`` in them as a group escape.
    A missing key is grown right after ``status:`` — records written before a
    field existed have no line to replace, and that is exactly the case the state
    pass over an old log runs into.
    """
    line = "{0}: {1}".format(key, " ".join((value or "").split()))
    lines = block.splitlines(keepends=True)
    tail = "" if block.endswith("\n") else "\n"
    for i, ln in enumerate(lines):
        if ln.startswith(key + ":"):
            lines[i] = line + ("\n" if ln.endswith("\n") else tail)
            return "".join(lines)
    for i, ln in enumerate(lines):
        if ln.startswith("status:"):
            lines.insert(i + 1, line + "\n")
            return "".join(lines)
    return block.rstrip("\n") + "\n" + line + "\n"


def _set_status_line(block: str, new_status: str, extra: Optional[str] = None) -> str:
    """Rewrite a block's ``status:`` line (and optionally one keyed line after it)."""
    block = re.sub(r"^status:.*$", "status: {0}".format(new_status), block, count=1, flags=re.M)
    if extra:
        key, _, val = extra.partition(":")
        block = _set_line(block, key.strip(), val)
    return block


def _require_proof(proof: Optional[str], verb: str, hint: str) -> str:
    """Return a non-empty one-line *proof*, or raise telling the caller what to say."""
    p = " ".join((proof or "").split())
    if not p:
        raise DecisionError("decision {0}: say {1}".format(verb, hint))
    return p


def _set_state(
    root: Path,
    key: str,
    new_status: str,
    *,
    thread_ref: Optional[str] = None,
    proof: Optional[str] = None,
    done: Optional[str] = None,
    extra: Optional[str] = None,
    fields_: Optional[Dict[str, str]] = None,
) -> "tuple[str, str]":
    """Write one record's axes (+ optional ``proof:``/``done:``/extra line).

    The single write path behind every verb — ``accept``, ``done``, ``drop``,
    ``supersede`` and ``settle`` all land here, so there is one place that knows
    how a decision reaches disk and no chance of two of them drifting apart.
    *done* of ``""`` clears the line (un-claiming); ``None`` leaves it alone.
    """
    rec = _resolve_key(root, thread_ref, key)
    num, dslug = str(rec["num"]), str(rec["slug"])
    f = decisions_file(_thread_dir(root, thread_ref))
    text = f.read_text(encoding="utf-8")

    def _apply(block: str) -> str:
        block = _set_status_line(block, new_status, extra=extra)
        # Every record this verb touches leaves with the full shape. An absent
        # `kind:` already MEANS commitment, so filling it in changes nothing —
        # but a file where some records carry the field and others don't reads
        # like the field means something when present, and the format is the one
        # thing that cannot be corrected later without migrating other people's
        # trees. Never overwrite an existing kind: plain `accept` must not
        # silently demote a standing rule.
        if not re.search(r"^kind:", block, flags=re.M):
            block = _set_line(block, "kind", COMMITMENT)
        if done is not None:
            block = _set_line(block, "done", done or "—")
        if proof is not None:
            block = _set_line(block, "proof", proof)
        for k, v in (fields_ or {}).items():
            block = _set_line(block, k, v)
        return block

    _io.atomic_write(f, _edit_block(text, num, _apply))
    return num, dslug


def _append_canon_journal(root: Path, line: str) -> None:
    """Append *line* under ``## Canon journal`` (the terminal section) of CANON.md."""
    canon = paths.canon_file(root)
    if not canon.is_file():
        raise DecisionError("decision settle: no canon (run 'tide canon init')")
    text = canon.read_text(encoding="utf-8")
    if "## Canon journal" not in text and "## Cannon journal" not in text:
        raise DecisionError("decision settle: CANON.md has no journal section")
    _io.atomic_write(canon, "{0}\n{1}\n".format(text.rstrip("\n"), line))


def accept(
    root: Path,
    key: str,
    *,
    thread_ref: Optional[str] = None,
    work: Optional[str] = None,
    rule: bool = False,
) -> "tuple[str, str]":
    """Put a decision in force, and optionally name who carries it.

    New decisions are born in force, so this verb serves the cases that are not
    birth: naming the work that will carry an existing promise (``--work``),
    declaring a record a standing rule (``--rule``), restating a record written
    before the axes existed, and un-claiming one marked done too early — which is
    why it clears ``done:``. It asks for no proof, because being in force is not
    a claim about the world.
    """
    extra = {"kind": RULE} if rule else {}
    if work:
        extra["work"] = work
    return _set_state(root, key, ACCEPTED, thread_ref=thread_ref, done="",
                      fields_=extra)


def mark_done(
    root: Path,
    key: str,
    proof: Optional[str] = None,
    *,
    thread_ref: Optional[str] = None,
    work: Optional[str] = None,
    now: Optional[datetime] = None,
) -> "tuple[str, str]":
    """Record that a decision was carried out: stamp ``done:`` with today's date.

    The in-force axis is untouched — a carried-out decision still binds. What is
    needed is something a reader can FOLLOW: either the work that did it
    (``--work``, the link this format always had and never used) or a short
    ``--proof`` (a commit, a command that answers). One of the two is required;
    "done, trust me" is precisely what this replaces.
    """
    if not (work or (proof or "").strip()):
        raise DecisionError(
            "decision done: say who carried it — --work <NN>, or --proof "
            "\"<commit / command that answers>\" when there is no work")
    stamp = (now or datetime.now()).strftime("%Y-%m-%d")
    extra = {"work": work} if work else {}
    return _set_state(root, key, ACCEPTED, thread_ref=thread_ref, done=stamp,
                      proof=" ".join((proof or "").split()) or None, fields_=extra)


def drop(
    root: Path,
    key: str,
    why: str,
    *,
    thread_ref: Optional[str] = None,
) -> "tuple[str, str]":
    """Withdraw a decision: status → ``dropped``, ``proof:`` says why.

    For a decision the world moved past without replacing — the question it closed
    stopped being asked. Unlike :func:`supersede` nothing points forward, so the
    reason is the only thing a later reader gets; it is required for that reason.
    """
    p = _require_proof(why, "drop", "why it is withdrawn (--why)")
    return _set_state(root, key, DROPPED, thread_ref=thread_ref, proof=p)


def settle(
    root: Path,
    key: str,
    *,
    thread_ref: Optional[str] = None,
    proof: Optional[str] = None,
    now: Optional[datetime] = None,
) -> "tuple[str, str]":
    """Settle a decision: mark it ``done`` AND raise it into the canon journal.

    The one-way promotion strand→canon (decision:canon :: commit:repo): the
    conclusion lands in the project's durable truth. It is the canon *door* onto
    the same state axis, not a second one — a settled decision is a done decision
    whose proof is the journal line, so nothing can be settled-but-not-done or
    read two ways. Returns ``(num, slug)``. The CLI handler gates this
    orchestrator-only.
    """
    rec = _resolve_key(root, thread_ref, key)
    num, dslug, what = str(rec["num"]), str(rec["slug"]), str(rec["what"])
    thread = str(rec["thread"]) or slug.entry_slug(_thread_dir(root, thread_ref).name)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d")
    line = "- {0} · decision {1}/{2} {3} — {4}".format(stamp, thread, num, dslug, what)
    # Canon first: it can refuse (no canon, no journal section), and a refusal
    # must leave the record untouched rather than half-promoted.
    _append_canon_journal(root, line)
    _set_state(root, key, ACCEPTED, thread_ref=thread_ref, done=stamp,
               proof=proof or "canon journal {0}".format(stamp))
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
    b = " ".join((by or "").split())
    if not b:
        raise DecisionError("decision supersede: say what supersedes it (--by)")
    return _set_state(root, key, SUPERSEDED, thread_ref=thread_ref,
                      extra="superseded-by: {0}".format(b))


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
        rule=getattr(args, "rule", False),
    )
    print("tide: decision {0} — {1} → {2}".format(num, dslug, f))
    return 0


def _cmd_list(args) -> int:
    print(render_list(_root(), thread_ref=getattr(args, "thread", None),
                      state=getattr(args, "state", None),
                      closes=getattr(args, "closes", False)))
    return 0


def _cmd_accept(args) -> int:
    num, dslug = accept(_root(), args.key, thread_ref=getattr(args, "thread", None),
                        work=getattr(args, "work", None),
                        rule=getattr(args, "rule", False))
    what = "standing rule" if getattr(args, "rule", False) else "in force, not carried out"
    who = ", carried by work {0}".format(args.work) if getattr(args, "work", None) else ""
    print("tide: decision {0} — {1} accepted ({2}{3})".format(num, dslug, what, who))
    return 0


def _cmd_done(args) -> int:
    num, dslug = mark_done(_root(), args.key, getattr(args, "proof", None),
                           thread_ref=getattr(args, "thread", None),
                           work=getattr(args, "work", None))
    shown = "work {0}".format(args.work) if getattr(args, "work", None) else args.proof
    print("tide: decision {0} — {1} carried out ({2})".format(num, dslug, shown))
    return 0


def _cmd_drop(args) -> int:
    num, dslug = drop(_root(), args.key, args.why,
                      thread_ref=getattr(args, "thread", None))
    print("tide: decision {0} — {1} dropped ({2})".format(num, dslug, args.why))
    return 0


def _cmd_settle(args) -> int:
    # Promotion strand→canon is a deliberate one-way valve — orchestrator-only,
    # exactly like `canon merge` / `candidate promote`. cli.main wraps RoleError.
    from ..cli import require_orchestrator

    require_orchestrator("decision settle")
    num, dslug = settle(_root(), args.key, thread_ref=getattr(args, "thread", None),
                        proof=getattr(args, "proof", None))
    print("tide: decision {0} — {1} done → canon journal".format(num, dslug))
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
    ap.add_argument("--rule", action="store_true",
                    help="a standing criterion rather than a promise to carry out")
    ap.set_defaults(func=_cmd_add, _cmd="decision add")

    lp = dsub.add_parser("list", help="list a thread's decisions")
    lp.add_argument("--thread", help="target nit (default: the caller session's own)")
    lp.add_argument("--state",
                    choices=list(STATUSES) + ["done", "not-done", "rule", "unowned"],
                    help="narrow: an in-force state, or done / not-done / rule / "
                         "unowned (in force, not done, nobody carrying it)")
    lp.add_argument("--closes", action="store_true",
                    help="print what each decision SETTLED — the arguments this "
                         "thread has already rejected and will not re-open")
    lp.set_defaults(func=_cmd_list, _cmd="decision list")

    cp = dsub.add_parser(
        "accept", help="put a decision in force; name the work that carries it")
    cp.add_argument("key", help="decision NN, NN-slug, or slug")
    cp.add_argument("--work", help="the work carrying this decision (its number)")
    cp.add_argument("--rule", action="store_true",
                    help="a standing criterion: in effect from signature, never "
                         "has a work — exempt from the ownership check")
    cp.add_argument("--thread", help="target nit (default: the caller session's own)")
    cp.set_defaults(func=_cmd_accept, _cmd="decision accept")

    np = dsub.add_parser("done", help="record that a decision was carried out")
    np.add_argument("key", help="decision NN, NN-slug, or slug")
    np.add_argument("--work", help="the work that carried it (its number)")
    np.add_argument("--proof",
                    help="what shows it, when no work did — a commit, a command")
    np.add_argument("--thread", help="target nit (default: the caller session's own)")
    np.set_defaults(func=_cmd_done, _cmd="decision done")

    dp = dsub.add_parser("drop", help="withdraw a decision (stays as history)")
    dp.add_argument("key", help="decision NN, NN-slug, or slug")
    dp.add_argument("--why", required=True, help="why it is withdrawn")
    dp.add_argument("--thread", help="target nit (default: the caller session's own)")
    dp.set_defaults(func=_cmd_drop, _cmd="decision drop")

    sp = dsub.add_parser(
        "settle", help="ORCHESTRATOR-ONLY: mark done AND raise it into the canon journal")
    sp.add_argument("key", help="decision NN, NN-slug, or slug")
    sp.add_argument("--proof", help="what proves it (default: the canon journal line)")
    sp.add_argument("--thread", help="target nit (default: the caller session's own)")
    sp.set_defaults(func=_cmd_settle, _cmd="decision settle")

    up = dsub.add_parser("supersede", help="mark a decision superseded (stays as history)")
    up.add_argument("key", help="decision NN, NN-slug, or slug")
    up.add_argument("--by", required=True, help="what replaces it (a decision ref or note)")
    up.add_argument("--thread", help="target nit (default: the caller session's own)")
    up.set_defaults(func=_cmd_supersede, _cmd="decision supersede")

"""tide.arc.work — «работы»: human work-cards as arcs under ``.tide/arcs/works/``.

A work is an arc-kind entity (``kind: work``): a dir ``NN-<slug>/`` holding a
``work.md`` passport — meta fields + free text (a task or a problem, no card
types) + ``## чеклист`` + ``## журнал``. The live board renders these files as
cards and its ``/work-*`` handlers are the HUMAN's hand; these CLI verbs are
the AGENT's deterministic gestures over the same files, so a status move or a
journal line can never be forgotten (the first live run proved they are:
the agent checked an item and moved no status — candidate 125-work-cli-verbs).

The signed model lives with the instance (work-cycle.md, Гриша 16.07); the
machine here: **open → taken → review → done**.

* ``take``    — open → taken (+ ``taken-by``/``taken-at``); starts on the
  human's word, recorded when given.
* ``check``   — mark item N with a REQUIRED ``--proof``; when ALL items are
  checked, a taken work auto-moves to review — gesture 4 can't be forgotten.
* ``uncheck`` — unmark item N; a review work falls back to taken.
* ``close``   — any live status → done; REQUIRES ``--word`` (the human's word:
  closing is the human's gate, the word is recorded in the journal).
* ``reopen``  — done → open.
* ``add`` / ``list`` / ``show`` — housekeeping (``add`` mirrors the board form).

Every verb appends a ``## журнал`` line — nothing sinks silently. All logic is
plain functions (argparse-free); :func:`register` wires the thin handlers.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import io as _io, numbering, paths, slug
from . import stream

WORKS_DIRNAME = "works"
_STAMP_FMT = "%Y-%m-%d %H:%M"
_ITEM_RE = re.compile(r"^- \[( |x)\] (.*)$")
_STATUS_RE = re.compile(r"^status: .*$", re.M)
_DEADLINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LIVE = ("open", "taken", "review")

# tide.slug drops cyrillic entirely, but work titles are usually Russian
# (the board solved this the same way — serve_live._CYR2LAT).
_CYR2LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


class WorkError(stream.StreamError):
    """A user-facing works error (bad transition, unknown key, missing proof …)."""


# --- paths / parsing ---------------------------------------------------------

def works_dir(root: Path) -> Path:
    """``<project>/.tide/arcs/works`` — the works live beside the stream."""
    return paths.arcs_dir(root) / WORKS_DIRNAME


def _work_slug(text: str) -> str:
    """Short latin handle for a (usually Russian) work title."""
    lat = "".join(_CYR2LAT.get(ch, ch) for ch in text.lower())
    return slug.short_slug(lat) or "work"


def _find(root: Path, key: str) -> Path:
    """Resolve a work dir by NN, NN-slug or slug; fail loud on 0 or 2+ hits."""
    wdir = works_dir(root)
    key = (key or "").strip().rstrip("/")
    if not key:
        raise WorkError("work: пустой ключ")
    hits = []
    for p in sorted(wdir.iterdir()) if wdir.is_dir() else []:
        if not p.is_dir() or not (p / "work.md").is_file():
            continue
        num, _, rest = p.name.partition("-")
        if key in (p.name, num, rest):
            hits.append(p)
    if not hits:
        raise WorkError("work: не нашёл работу {0!r} в {1}".format(key, wdir))
    if len(hits) > 1:
        raise WorkError(
            "work: ключ {0!r} неоднозначен: {1}".format(
                key, ", ".join(p.name for p in hits)))
    return hits[0]


def _read(wdir: Path) -> Tuple[Path, str]:
    f = wdir / "work.md"
    return f, f.read_text(encoding="utf-8")


def _status_of(text: str) -> str:
    m = re.search(r"^status:\s*(\S+)", text, re.M)
    if not m:
        raise WorkError("work: паспорт без поля status")
    return m.group(1)


def _set_status(text: str, new: str) -> str:
    return _STATUS_RE.sub("status: " + new, text, count=1)


def _journal(text: str, line: str) -> str:
    """Append a journal line, creating the (always-last) section when absent."""
    if re.search(r"^## журнал", text, re.M):
        return text.rstrip("\n") + "\n" + line + "\n"
    return text.rstrip("\n") + "\n\n## журнал\n" + line + "\n"


def _stamp(now: Optional[datetime]) -> str:
    return (now or datetime.now()).strftime(_STAMP_FMT)


def items(text: str) -> List[Tuple[bool, str]]:
    """The checklist as ``(done, text)`` pairs, in file order."""
    out = []
    for ln in text.splitlines():
        m = _ITEM_RE.match(ln)
        if m:
            out.append((m.group(1) == "x", m.group(2)))
    return out


def _mark_item(text: str, index: int, done: bool) -> Tuple[str, str]:
    """Set item *index* (1-based) to *done*; returns (new_text, item_text)."""
    lines = text.splitlines()
    n = 0
    for j, ln in enumerate(lines):
        m = _ITEM_RE.match(ln)
        if not m:
            continue
        n += 1
        if n != index:
            continue
        if (m.group(1) == "x") == done:
            state = "уже чекнут" if done else "и так не чекнут"
            raise WorkError("work: пункт {0} {1}".format(index, state))
        lines[j] = "- [{0}] {1}".format("x" if done else " ", m.group(2))
        return "\n".join(lines) + "\n", m.group(2)
    raise WorkError("work: нет пункта {0} (в чеклисте {1})".format(index, n))


# --- verbs -------------------------------------------------------------------

def new_work(
    root: Path,
    text: str,
    deadline: Optional[str] = None,
    for_project: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Path:
    """Create ``works/NN-<slug>/work.md`` — mirrors the board's «завести» form."""
    title = " ".join((text or "").split())
    if not title:
        raise WorkError("work: пустая работа")
    if deadline and not _DEADLINE_RE.match(deadline):
        raise WorkError("work: кривой дедлайн {0!r} (нужен YYYY-MM-DD)".format(deadline))
    wdir = works_dir(root)
    wdir.mkdir(parents=True, exist_ok=True)
    name = "{0}-{1}".format(numbering.next_num(wdir), _work_slug(title))
    d = wdir / name
    d.mkdir()
    stamp = (now or datetime.now()).strftime("%Y-%m-%d")
    body = (
        "# {t}\n\nkind: work\nproject: {p}\nstatus: open\ncreated: {c}\n{dl}"
        "\n## чеклист\n- [ ] {t}\n"
    ).format(t=title, p=for_project or "", c=stamp,
             dl="deadline: {0}\n".format(deadline) if deadline else "")
    _io.atomic_write(d / "work.md", body)
    return d


def set_checklist(
    root: Path,
    key: str,
    texts: List[str],
    force: bool = False,
    now: Optional[datetime] = None,
) -> str:
    """Replace the checklist with the AGREED items (gesture 1: разложить).

    Refuses when checked items exist (progress would be erased) unless *force*
    — the human's explicit word. Journals the agreement.
    """
    texts = [" ".join(t.split()) for t in texts if t and t.strip()]
    if not texts:
        raise WorkError("work: пустой чеклист — дай пункты")
    wdir = _find(root, key)
    f, text = _read(wdir)
    st = _status_of(text)
    if st == "done":
        raise WorkError("work: {0} закрыта — сначала tide work reopen".format(wdir.name))
    if any(done for done, _ in items(text)) and not force:
        raise WorkError(
            "work: в чеклисте есть чекнутые пункты — замена сотрёт прогресс "
            "(--force только по слову человека)")
    lines = text.splitlines()
    try:
        head = next(i for i, ln in enumerate(lines)
                    if ln.startswith("## чеклист"))
    except StopIteration:
        raise WorkError("work: паспорт без секции ## чеклист")
    end = head + 1
    while end < len(lines) and not lines[end].startswith("## "):
        end += 1
    block = ["## чеклист"] + ["- [ ] {0}".format(t) for t in texts] + [""]
    text = "\n".join(lines[:head] + block + lines[end:])
    if not text.endswith("\n"):
        text += "\n"
    text = _journal(text, "- {0} — чеклист согласован: {1} пункт(ов)".format(
        _stamp(now), len(texts)))
    _io.atomic_write(f, text)
    return wdir.name


def _set_meta(text: str, key: str, value: str) -> str:
    """Set ``key: value`` in the passport meta; empty *value* removes the line.

    An existing key is rewritten in place; a new key lands right after
    ``created:`` (the meta block). Order inside the block is irrelevant — the
    board parses meta line-by-line by regex.
    """
    pat = re.compile(r"^{0}:\s*.*$".format(re.escape(key)), re.M)
    if not value:
        return re.sub(r"^{0}:\s*.*\n?".format(re.escape(key)), "", text,
                      count=1, flags=re.M)
    if pat.search(text):
        return pat.sub("{0}: {1}".format(key, value), text, count=1)
    return re.sub(r"^(created: .*)$", "\\g<0>\n{0}: {1}".format(key, value),
                  text, count=1, flags=re.M)


def _resolve_caller_thread(work_root: Path) -> Optional[str]:
    """The нить of the session that invoked us, as an address — or None.

    Reads ``$CLAUDE_CODE_SESSION_ID`` → the session arc pinned to it IN THE
    CALLER's own project (which may differ from where the work lives). Returns
    the bare thread slug when the caller sits in the same project as the work,
    else the cross-project address ``<project>/<thread>``. None when there is no
    sid, the caller isn't a tide session, or nothing matches — then ``take``
    records no owner and the human attaches one on the board (fork «и рукой»).
    """
    import os
    sid = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    if not sid:
        return None
    from .. import offload  # lazy: avoid an import cycle at module load
    origin = paths.find_tide_root()
    if origin is None:
        return None
    entry = offload.find_session_by_claude_id(origin, sid)
    if entry is None:
        return None
    try:
        thread = entry.parents[1].name  # …/NN-@thread/arcs/NN-session
    except IndexError:
        return None
    if origin.resolve() == Path(work_root).resolve():
        return thread
    return "{0}/{1}".format(origin.name, thread)


def set_thread(
    root: Path,
    key: str,
    thread: Optional[str],
    source: str = "рука человека",
    now: Optional[datetime] = None,
) -> str:
    """Set/clear the responsible нить (``thread:``). Empty *thread* clears it."""
    wdir = _find(root, key)
    f, text = _read(wdir)
    val = (thread or "").strip()
    text = _set_meta(text, "thread", val)
    line = ("- {0} — ответственная нить → {1} ({2})".format(_stamp(now), val, source)
            if val else
            "- {0} — нить снята ({1})".format(_stamp(now), source))
    text = _journal(text, line)
    _io.atomic_write(f, text)
    return wdir.name


def take(
    root: Path,
    key: str,
    by: Optional[str] = None,
    word: Optional[str] = None,
    thread: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[str, Optional[str]]:
    """open → taken: stamp ``taken-by``/``taken-at`` + owner нить + journal.

    The responsible нить is *thread* when given, else auto-resolved from the
    caller's session (fork «и авто»); None when unresolvable. Returns
    ``(slug, owner)`` so the caller can say which нить now owns the work.
    """
    wdir = _find(root, key)
    f, text = _read(wdir)
    st = _status_of(text)
    if st == "done":
        raise WorkError("work: {0} закрыта — сначала tide work reopen".format(wdir.name))
    if st != "open":
        raise WorkError("work: {0} уже взята (status: {1})".format(wdir.name, st))
    text = _set_status(text, "taken")
    # taken-by/taken-at live right after status (the board parses them as meta)
    who = (by or "").strip() or "агент"
    at = (now or datetime.now()).strftime("%Y-%m-%dT%H:%M")
    text = re.sub(r"^(status: .*)$",
                  "\\1\ntaken-by: {0}\ntaken-at: {1}".format(who, at),
                  text, count=1, flags=re.M)
    owner = (thread or "").strip() or _resolve_caller_thread(root)
    if owner:
        text = _set_meta(text, "thread", owner)
    note = " по слову: «{0}»".format(word.strip()) if word and word.strip() else ""
    tail = " · нить {0}".format(owner) if owner else ""
    text = _journal(text, "- {0} — взята в работу ({1}){2}{3}".format(
        _stamp(now), who, note, tail))
    _io.atomic_write(f, text)
    return wdir.name, owner


def check(
    root: Path,
    key: str,
    index: int,
    proof: str,
    now: Optional[datetime] = None,
) -> Tuple[str, bool]:
    """Mark item *index* with *proof*; auto taken → review when all are done.

    Returns ``(slug, reviewed)`` — *reviewed* is True when this check moved the
    work to review (all items done), so the caller can say it out loud.
    """
    if not (proof or "").strip():
        raise WorkError("work: чек без пруфа не жест — дай --proof «что сделано»")
    wdir = _find(root, key)
    f, text = _read(wdir)
    st = _status_of(text)
    if st not in ("taken", "review"):
        raise WorkError(
            "work: {0} не взята (status: {1}) — сначала tide work take".format(
                wdir.name, st))
    text, item_text = _mark_item(text, index, True)
    text = _journal(text, "- {0} — пункт {1} ✓ «{2}»: {3}".format(
        _stamp(now), index, item_text, proof.strip()))
    reviewed = False
    if st == "taken" and all(done for done, _ in items(text)):
        text = _set_status(text, "review")
        text = _journal(text, "- {0} — все пункты чекнуты → review, ждёт "
                              "закрытия человеком".format(_stamp(now)))
        reviewed = True
    _io.atomic_write(f, text)
    return wdir.name, reviewed


def uncheck(
    root: Path,
    key: str,
    index: int,
    reason: Optional[str] = None,
    now: Optional[datetime] = None,
) -> str:
    """Unmark item *index*; a review work honestly falls back to taken."""
    wdir = _find(root, key)
    f, text = _read(wdir)
    st = _status_of(text)
    if st not in ("taken", "review"):
        raise WorkError(
            "work: {0} не взята (status: {1}) — сначала tide work take".format(
                wdir.name, st))
    text, item_text = _mark_item(text, index, False)
    why = ": {0}".format(reason.strip()) if reason and reason.strip() else ""
    text = _journal(text, "- {0} — пункт {1} расчекнут «{2}»{3}".format(
        _stamp(now), index, item_text, why))
    if st == "review":
        text = _set_status(text, "taken")
        text = _journal(text, "- {0} — чеклист снова неполон → taken".format(
            _stamp(now)))
    _io.atomic_write(f, text)
    return wdir.name


def close(root: Path, key: str, word: str, now: Optional[datetime] = None) -> str:
    """Any live status → done. The human's word is REQUIRED — it IS the gate."""
    if not (word or "").strip():
        raise WorkError(
            "work: done ставит человек — закрывай только с --word «его слово»")
    wdir = _find(root, key)
    f, text = _read(wdir)
    if _status_of(text) == "done":
        raise WorkError("work: {0} уже закрыта".format(wdir.name))
    text = _set_status(text, "done")
    text = _journal(text, "- {0} — закрыта по слову человека: «{1}»".format(
        _stamp(now), word.strip()))
    _io.atomic_write(f, text)
    return wdir.name


def reopen(root: Path, key: str, word: Optional[str] = None,
           now: Optional[datetime] = None) -> str:
    """done → open."""
    wdir = _find(root, key)
    f, text = _read(wdir)
    if _status_of(text) != "done":
        raise WorkError("work: {0} и так открыта".format(wdir.name))
    text = _set_status(text, "open")
    note = " по слову: «{0}»".format(word.strip()) if word and word.strip() else ""
    text = _journal(text, "- {0} — открыта заново{1}".format(_stamp(now), note))
    _io.atomic_write(f, text)
    return wdir.name


# --- list / show -------------------------------------------------------------

def render_list(root: Path) -> str:
    """The works board as text: live first (deadline order), closed below."""
    wdir = works_dir(root)
    rows = []
    for p in sorted(wdir.iterdir()) if wdir.is_dir() else []:
        f = p / "work.md"
        if not p.is_dir() or not f.is_file():
            continue
        text = f.read_text(encoding="utf-8")
        title = next((ln[2:].strip() for ln in text.splitlines()
                      if ln.startswith("# ")), p.name)
        st = _status_of(text)
        its = items(text)
        m = re.search(r"^deadline:\s*(\S+)", text, re.M)
        dl = m.group(1) if m else ""
        rows.append((st == "done", dl or "9999", p.name, st, its, dl, title))
    if not rows:
        return "tide: работ нет ({0})".format(wdir)
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    out = []
    for _, _, name, st, its, dl, title in rows:
        done_n = sum(1 for d, _ in its if d)
        out.append("{0:<34} {1:<7} {2}/{3}{4}  {5}".format(
            name, st, done_n, len(its),
            "  до " + dl if dl else "", title))
    return "\n".join(out)


def show(root: Path, key: str) -> str:
    """The raw work.md — the file IS the truth."""
    _, text = _read(_find(root, key))
    return text.rstrip("\n")


# --- CLI wiring --------------------------------------------------------------

def _root(args) -> Path:
    project = getattr(args, "project", None)
    if project:
        # same cross-project resolution as `candidate add --project`
        from .candidate import _resolve_target_root
        return _resolve_target_root(project)
    return paths.require_tide_root()


def _cmd_add(args) -> int:
    d = new_work(_root(args), " ".join(args.text),
                 deadline=args.deadline, for_project=args.for_project)
    print("tide: работа заведена — {0}".format(d.name))
    return 0


def _cmd_checklist(args) -> int:
    name = set_checklist(_root(args), args.key, args.items, force=args.force)
    print("tide: {0} — чеклист согласован ({1} пункт(ов))".format(
        name, len(args.items)))
    return 0


def _cmd_take(args) -> int:
    name, owner = take(_root(args), args.key, by=args.by, word=args.word,
                       thread=getattr(args, "thread", None))
    print("tide: {0} — взята (open → taken)".format(name))
    if owner:
        print("tide: ответственная нить — {0}".format(owner))
    return 0


def _cmd_thread(args) -> int:
    val = "" if args.clear else (args.set or "")
    name = set_thread(_root(args), args.key, val, source="рука человека (CLI)")
    print("tide: {0} — {1}".format(
        name, "нить снята" if not val else "ответственная нить → {0}".format(val)))
    return 0


def _cmd_check(args) -> int:
    name, reviewed = check(_root(args), args.key, args.index, args.proof)
    print("tide: {0} — пункт {1} чекнут".format(name, args.index))
    if reviewed:
        print("tide: {0} — все пункты чекнуты → review; done ставит "
              "человек (кнопка на доске или его слово)".format(name))
    return 0


def _cmd_uncheck(args) -> int:
    name = uncheck(_root(args), args.key, args.index, reason=args.reason)
    print("tide: {0} — пункт {1} расчекнут".format(name, args.index))
    return 0


def _cmd_close(args) -> int:
    name = close(_root(args), args.key, args.word)
    print("tide: {0} — закрыта (слово человека в журнале)".format(name))
    return 0


def _cmd_reopen(args) -> int:
    name = reopen(_root(args), args.key, word=args.word)
    print("tide: {0} — открыта заново".format(name))
    return 0


def _cmd_list(args) -> int:
    print(render_list(_root(args)))
    return 0


def _cmd_show(args) -> int:
    print(show(_root(args), args.key))
    return 0


def register(subparsers) -> None:
    """Add the ``work`` command group to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "work", help="работы: add/take/check/uncheck/close/reopen/list/show")
    wsub = p.add_subparsers(dest="work_cmd")

    def _common(sp):
        sp.add_argument(
            "--project",
            help="target ANOTHER rostered project's works (by roster name)")

    ap = wsub.add_parser("add", help="завести работу (зеркало формы доски)")
    ap.add_argument("text", nargs="+", help="что сделать — одной строкой")
    ap.add_argument("--deadline", help="YYYY-MM-DD (один дедлайн на работу)")
    ap.add_argument("--for", dest="for_project",
                    help="поле project: в паспорте — где меняется мир")
    _common(ap)
    ap.set_defaults(func=_cmd_add, _cmd="work add")

    kp = wsub.add_parser(
        "checklist",
        help="жест 1: заменить чеклист СОГЛАСОВАННЫМИ пунктами (+журнал)")
    kp.add_argument("key")
    kp.add_argument("items", nargs="+", help="пункты, по одному аргументу")
    kp.add_argument("--force", action="store_true",
                    help="заменить несмотря на чекнутые (слово человека)")
    _common(kp)
    kp.set_defaults(func=_cmd_checklist, _cmd="work checklist")

    tp = wsub.add_parser("take", help="взять работу: open → taken (+нить, +журнал)")
    tp.add_argument("key", help="NN, NN-slug или slug работы")
    tp.add_argument("--by", help="кто берёт (в taken-by и журнал)")
    tp.add_argument("--word", help="слово человека, по которому берёшь")
    tp.add_argument("--thread", help="ответственная нить явно (иначе — авто "
                    "из сессии, что зовёт)")
    _common(tp)
    tp.set_defaults(func=_cmd_take, _cmd="work take")

    hp = wsub.add_parser(
        "thread", help="ответственная нить работы: прикрепить/сменить/снять")
    hp.add_argument("key")
    hp.add_argument("--set", help="слаг нити (NN-@slug) или адрес proj/NN-@slug")
    hp.add_argument("--clear", action="store_true", help="снять нить")
    _common(hp)
    hp.set_defaults(func=_cmd_thread, _cmd="work thread")

    cp = wsub.add_parser(
        "check", help="чекнуть пункт N с пруфом (все чекнуты → review сам)")
    cp.add_argument("key")
    cp.add_argument("index", type=int, help="номер пункта (с 1)")
    cp.add_argument("--proof", required=True,
                    help="что именно сделано: коммит, ссылка, файл")
    _common(cp)
    cp.set_defaults(func=_cmd_check, _cmd="work check")

    up = wsub.add_parser("uncheck", help="расчекнуть пункт N (review → taken)")
    up.add_argument("key")
    up.add_argument("index", type=int)
    up.add_argument("--reason", help="почему расчекнут (в журнал)")
    _common(up)
    up.set_defaults(func=_cmd_uncheck, _cmd="work uncheck")

    dp = wsub.add_parser(
        "close", help="закрыть: done ставится ТОЛЬКО со словом человека")
    dp.add_argument("key")
    dp.add_argument("--word", required=True,
                    help="слово человека, которым закрыто (в журнал)")
    _common(dp)
    dp.set_defaults(func=_cmd_close, _cmd="work close")

    rp = wsub.add_parser("reopen", help="открыть закрытую заново: done → open")
    rp.add_argument("key")
    rp.add_argument("--word", help="слово человека (в журнал)")
    _common(rp)
    rp.set_defaults(func=_cmd_reopen, _cmd="work reopen")

    lp = wsub.add_parser("list", help="доска работ текстом (живые + закрытые)")
    _common(lp)
    lp.set_defaults(func=_cmd_list, _cmd="work list")

    sp = wsub.add_parser("show", help="паспорт работы как есть (файл = правда)")
    sp.add_argument("key")
    _common(sp)
    sp.set_defaults(func=_cmd_show, _cmd="work show")

"""tide.arc.work — «работы»: human work-cards as arcs under ``.tide/arcs/works/``.

A work is an arc-kind entity (``kind: work``): a dir ``NN-<slug>/`` holding a
``work.md`` passport — meta fields + free text (a task or a problem, no card
types) + ``## план`` + ``## чеклист`` + ``## фиксы`` + ``## журнал``. The live
board renders these files as cards and its ``/work-*`` handlers are the HUMAN's
hand; these CLI verbs are the AGENT's deterministic gestures over the same
files, so a status move or a journal line can never be forgotten (the first live
run proved they are: the agent checked an item and moved no status —
cand 125-work-cli-verbs).

The signed model lives with the instance (work-cycle.md, 16.07); the
machine here: **open → taken → review → done**.

* ``plan``    — put the agreed-to-be plan as free text into ``## план``; the
  agent's word, so it moves NO status (work 16, tide-stack).
* ``propose`` — offer checklist items as ``- [?]``: visible, numbered, but not
  yet the plan — only the human's «да» turns them into ``- [ ]``.
  ``--replace`` withdraws the standing offers first (agreed items untouched);
  a work sitting in review comes back to taken — there is a question on the
  table again.
* ``agree``   — that «да» when it is SAID, not clicked: flips the named
  ``- [?]`` items (or every standing offer) into ``- [ ]`` and writes the
  human's word into the journal; ``--drop`` withdraws a rejected offer
  instead. REQUIRES ``--word`` and moves no status.
* ``fix``     — the human's afterthoughts at the gate: appends items to
  ``## фиксы`` ALREADY agreed (``- [ ]``), because ``--word`` — his remark
  verbatim — IS the agreement, and asking him to sign what he just asked for
  would be a second gate. A work sitting in review comes back to taken: there
  is work on the table again.
* ``drop``    — снять СОГЛАСОВАННЫЙ пункт N; REQUIRES ``--word`` (правка
  подписанного договора — только рукой человека). Висящее ``- [?]`` сюда не
  ходит: у предложения свой ответ — ``agree --drop``.
* ``take``    — open → taken (+ ``taken-by``/``taken-at``); starts on the
  human's word, recorded when given.
* ``step``    — адрес работы в плане нити: ``step: N``. Ставится руками, но
  обычно приезжает сам вместе с нитью (см. ниже).
* ``dispatch``— «строитель отправлен: имя» строкой в журнал. СТАТУС НЕ ДВИГАЕТ:
  это событие, а не состояние. Только на taken, повторный — ещё одна строка
  (кандидаты 179/180: очередь диспатчей жила в голове оркестратора).
* ``check``   — mark item N with a REQUIRED ``--proof``; when ALL AGREED items
  are checked AND no offer is left standing, a taken work auto-moves to review
  — gesture 4 can't be forgotten. A ``- [?]`` item is refused: it is still an
  offer.
* ``uncheck`` — unmark item N; a review work falls back to taken.
* ``close``   — any live status → done; REQUIRES ``--word`` (the human's word:
  closing is the human's gate, the word is recorded in the journal). That word
  also ACCEPTS every checked item at once — see below.
* ``reopen``  — done → назад в живой статус ПО ПАСПОРТУ: есть ``taken-by`` —
  к исполнителю (taken, а при закрытом чеклисте сразу review), нет — open.
* ``title``   — rewrite the H1; the name is what the human signed, so it moves
  only with ``--word`` and never moves the status.
* ``add`` / ``list`` / ``show`` — housekeeping (``add`` mirrors the board form).
  ``add --cand NN`` РОЖДАЕТ работу из кандидата одним жестом: текст кандидата
  ложится черновиком в ``## план``, сам кандидат уходит с полки в
  ``candidates/__dropped__/`` (восстановим), связь пишется в журнал.

Имя работы — это H1, а НЕ первый пункт чеклиста: ``add`` рождает работу с пустым
``## чеклист``. Пункт-двойник имени случался трижды (работы 23, 41, 44) — он
никем не делался и врал счётчику (0/6 вместо 0/5), а снять его было нечем
(кандидат 183). Чеклист набирается разговором: ``propose`` → «да» человека.

## Куда работа идёт: ``thread:`` и ``step:``

``thread:`` — ответственная нить, ``step:`` — номер шага её плана
(``<нить>/plan.md``, раздел ``## шаги``, текущий помечен ``[>]``). Вдвоём они
отвечают на «куда эта работа ведёт»: без шага работы висят плоским списком —
видно, что делается, не видно, зачем.

Оба приезжают САМИ из сессии, которая работу завела (``add``/``plan``/
``propose``) или взяла (``take``) — агент и так знает, из чьей сессии пишет.
Раньше нить писал только ``take``, то есть уже ПОСЛЕ «да» человека: его звали
смотреть план во вкладку нити, а работы там ещё не было (кандидат 182).
Уже проставленную нить автоматика не перетирает — явное сильнее.

Шаг без нити бессмысленен, поэтому он ходит за ней: сменилась нить —
перечитывается из её плана, снялась нить — снимается и шаг. Нет ``plan.md``,
нет раздела ``## шаги``, никто не помечен ``[>]`` — поле просто не ставится.
Гадать нельзя: соврать адресом хуже, чем его не иметь.

An item is a SHORT title on its own line plus an optional description on the
lines below it, indented by two spaces::

    - [?] Переучить ритуал передачи
      скилл handoff: сначала draft — потом курация

The human signs steps on the board, so the title has to be readable at a glance
and the details wait below for whoever wants them. Only ``- [`` lines count as
items: a description never shifts a number, so «пункт 3» means the same line for
the CLI, the board and the human. ``propose``/``checklist``/``fix`` take the two
apart at a ``\n`` inside the item argument — first line the title, the rest the
description (same convention as ``plan``).

An item carries TWO facts and only the first one is a checkbox: «сделано» is the
agent's ``- [x]`` with its proof, «принято» is the human's hand afterwards.
Acceptance therefore lives in JOURNAL lines — the checklist format never changes,
so nothing that already reads a passport has to relearn it::

    - 2026-07-30 14:02 — пункт 2 принят рукой              (board, one item)
    - 2026-07-30 14:05 — все сделанные пункты приняты словом  (close, the lot)

The agent has NO per-item acceptance verb: that gesture is the human's hand on
the board. ``close`` writes the mass line (when anything is checked) right before
the closing word — one word accepts what it closes. ``show`` reads both lines
back and marks an item that has both facts ``✓✓``; the mass line speaks for the
work as it was closed, so it counts only while the work IS closed — a ``reopen``
touches no acceptance (history is never rubbed out), but the checklist moves on
and only per-item acceptance survives it.

Items live in TWO sections — ``## чеклист`` then ``## фиксы`` — and the numbering
runs straight through them in file order: with four items in the checklist the
first fix is item 5. Every hand (``check``, ``uncheck``, ``agree``, the board)
counts the same way, so a number said out loud means one line everywhere.

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
# three item states: agreed-open « », done «x», proposed by the agent «?»
_ITEM_RE = re.compile(r"^- \[( |x|\?)\] (.*)$")
PROPOSED = "?"
_STATUS_RE = re.compile(r"^status: .*$", re.M)
_DEADLINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LIVE = ("open", "taken", "review")
PLAN_TITLE = "план"
CHECKLIST_TITLE = "чеклист"
# the human's afterthoughts at the gate — items, same numbering, own section
FIXES_TITLE = "фиксы"
JOURNAL_TITLE = "журнал"
# приёмка человека живёт СТРОКАМИ ЖУРНАЛА, формат чеклиста не трогает: «- [x]» —
# сделано, ✓✓ — сделано И принято (второй факт читается только из журнала)
ACCEPTED_MARK = "✓✓"
ACCEPT_ALL_LINE = "все сделанные пункты приняты словом"
# лениво к штампу (строку про пункт пишет РУКА с доски, не мы), строго к фразе
_ACCEPT_ONE_RE = re.compile(r"^- .+ — пункт (\d+) принят рукой\s*$")
_ACCEPT_ALL_RE = re.compile(r"^- .+ — {0}\s*$".format(re.escape(ACCEPT_ALL_LINE)))
# шаг плана нити: «- [>] 3. имя шага | что делается | результат: «…»»
# состояние — [x] пройден, [>] текущий, [ ] будущий (пишет curate.validate_step)
_PLAN_STEP_RE = re.compile(r"^- \[([x> ])\]\s*(\d{1,3})\.\s*(.*)$")
PLAN_STEPS_TITLE = "шаги"
CURRENT_STEP = ">"
# a work card is read at a glance on the board — longer is a warning, not a bar
TITLE_MAX = 80
_TITLE_IN_JOURNAL = 60

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
    """Resolve a work dir by NN, NN-slug or slug; fail loud on 0 or 2+ hits.

    A bare number matches too: ``1`` is the work ``01-…`` — humans type what
    ``add`` printed, minus the leading zero. Numbers are unique per project,
    so the shortcut cannot make a key ambiguous.
    """
    wdir = works_dir(root)
    key = (key or "").strip().rstrip("/")
    if not key:
        raise WorkError("work: пустой ключ")
    want_num = int(key) if key.isdigit() else None
    hits = []
    for p in sorted(wdir.iterdir()) if wdir.is_dir() else []:
        if not p.is_dir() or not (p / "work.md").is_file():
            continue
        num, _, rest = p.name.partition("-")
        if key in (p.name, num, rest) or (
                want_num is not None and num.isdigit()
                and int(num) == want_num):
            hits.append(p)
    if not hits:
        raise WorkError(
            "work: не нашёл работу {0!r} в {1} "
            "(работы зовутся 01, 02, … — смотри tide work list)".format(
                key, wdir))
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


def _is_continuation(line: str) -> bool:
    """An indented, non-empty line — the description of the item above it.

    Read leniently (any indent), written strictly (two spaces): a hand-edited
    passport shouldn't lose its details over a stray space.
    """
    return bool(line[:1].isspace() and line.strip())


def _item_end(lines: List[str], start: int) -> int:
    """End (exclusive) of the item block opened at *start* — its description."""
    end = start + 1
    while end < len(lines) and _is_continuation(lines[end]):
        end += 1
    return end


def item_blocks(text: str) -> List[Tuple[str, str, str]]:
    """Every checklist item as ``(state, title, description)``, in file order.

    State is ``" "``/``"x"``/``"?"``; the description is the indented block
    under the title (dedented, ``""`` when the item has none).
    """
    lines = text.splitlines()
    out = []
    for j, ln in enumerate(lines):
        m = _ITEM_RE.match(ln)
        if not m:
            continue
        tail = lines[j + 1:_item_end(lines, j)]
        out.append((m.group(1), m.group(2), "\n".join(t.strip() for t in tail)))
    return out


def all_items(text: str) -> List[Tuple[str, str]]:
    """Every checklist item as ``(state, title)`` — description left aside.

    File order IS the numbering every hand addresses items by (CLI, board,
    human) — proposals and fixes included, both sections read as one run, so
    «пункт 3» means the same line everywhere.
    """
    return [(st, title) for st, title, _ in item_blocks(text)]


def _count_items(lines: List[str]) -> int:
    """How many items sit in *lines* — what a new one lands after."""
    return sum(1 for ln in lines if _ITEM_RE.match(ln))


def _split_item(text: str) -> Tuple[str, List[str]]:
    """``"Тайтл\\nподробности"`` → title + description lines (blanks dropped)."""
    parts = (text or "").split("\n")
    title = " ".join(parts[0].split())
    if not title:
        raise WorkError(
            "work: пункт без заголовка — первая строка это тайтл, "
            "подробности ниже")
    return title, [" ".join(p.split()) for p in parts[1:] if p.strip()]


def _item_lines(state: str, text: str) -> List[str]:
    """One item as file lines: ``- [s] Тайтл`` + its two-space description."""
    title, desc = _split_item(text)
    return ["- [{0}] {1}".format(state, title)] + ["  " + d for d in desc]


def items(text: str) -> List[Tuple[bool, str]]:
    """The AGREED checklist as ``(done, text)`` pairs, in file order.

    Proposed items (``- [?]``) are left out on purpose: an offer is not the
    plan, so it neither counts as progress nor holds review back.
    """
    return [(st == "x", t) for st, t in all_items(text) if st != PROPOSED]


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
        if m.group(1) == PROPOSED:
            raise WorkError(
                "work: пункт {0} — ещё предложение, ждёт «да» человека "
                "(пока не согласован — не чекается)".format(index))
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
    """Create ``works/NN-<slug>/work.md`` — mirrors the board's «завести» form.

    Чеклист рождается ПУСТЫМ: имя работы — это H1, а не первый её шаг
    (кандидат 183). Ответственная нить и её шаг приезжают из сессии-автора, если
    она сидит в нити, — работа видна во вкладке нити сразу, а не после «да»
    человека (кандидат 182).
    """
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
        "\n## чеклист\n"
    ).format(t=title, p=for_project or "", c=stamp,
             dl="deadline: {0}\n".format(deadline) if deadline else "")
    body, _ = _autostamp_thread(root, body, now)
    _io.atomic_write(d / "work.md", body)
    return d


# Шапка кандидата: H1 «# NN-slug» и два поля. Тело — всё, что под ними.
_CAND_META_RE = re.compile(r"^(from|dropped):\s")


def _candidate_gist(text: str) -> str:
    """Идея кандидата без служебной шапки (H1 + ``from:``/``dropped:``).

    Режем по ИЗВЕСТНОЙ форме файла кандидата, а не общим парсером фронтматтера:
    тело кандидата — вольная проза, и первая же её строка часто выглядит
    ключом («механика движка: верб …»). Узкое правило её не съест.
    """
    lines = text.splitlines()
    i = 1 if lines[:1] and lines[0].startswith("# ") else 0
    while i < len(lines) and (not lines[i].strip() or _CAND_META_RE.match(lines[i])):
        i += 1
    return "\n".join(lines[i:]).strip()


def new_work_from_candidate(
    root: Path,
    text: str,
    cand: str,
    deadline: Optional[str] = None,
    for_project: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[Path, str]:
    """Родить работу ИЗ КАНДИДАТА одним жестом: завести + перенести + выбросить.

    Правило человека 01.08 — «чтоб не дублировалось»: кандидат, ушедший в
    работу, полке больше не принадлежит. Раньше это были два независимых жеста
    и держалось всё на памяти агента: за один день десятерых выбрасывали задним
    числом, а до тех пор полка дублировала стол. Здесь связь механическая —
    забыть её нельзя, потому что она один верб.

    Имя работы берётся ИЗ АРГУМЕНТА, а не из кандидата: слаг кандидата — это
    длинный handle («174-rozhdenie-raboty-iz-kandidata-mehanikoy»), и тайтлом
    работы он не годится (норма — имя в 2–4 слова). Текст кандидата ложится
    ЧЕРНОВИКОМ в ``## план``: черновик, а не согласие. Работа рождается обычной
    ``open`` и проходит цикл plan → propose → «да» человека целиком, как любая
    другая, — рождение из кандидата ничего не обходит.

    Кандидат уходит в ``candidates/__dropped__/`` тем же путём, что крестик на
    доске (:func:`tide.arc.curate.drop_candidate`) — с полки, но не с диска.
    Returns ``(work_dir, candidate_stem)``.
    """
    # соседние домены — локальным импортом, как это делают _root и candidate._cmd_drop
    from .candidate import _resolve as _resolve_cand
    from .curate import drop_candidate

    cdir = paths.candidates_dir(root)
    cfile = _resolve_cand(cdir, cand)
    if cfile is None:
        raise WorkError(
            "work: не нашёл кандидата {0!r} в {1}".format(cand, cdir))
    stem = cfile.stem
    gist = _candidate_gist(cfile.read_text(encoding="utf-8"))
    # заводим ПЕРВЫМ делом: кривой тайтл или дедлайн должны упасть до того, как
    # кандидат уедет с полки, — иначе жест разваливается пополам
    d = new_work(root, text, deadline=deadline, for_project=for_project, now=now)
    f, doc = _read(d)
    doc = _journal(doc, "- {0} — рождена из кандидата {1}".format(_stamp(now), stem))
    _io.atomic_write(f, doc)
    if gist:
        set_plan(root, d.name, gist, now=now)
    drop_candidate(root, stem)
    return d, stem


def _section(lines: List[str], title: str) -> Optional[Tuple[int, int]]:
    """``(head, end)`` line indices of section *title*; *end* is exclusive."""
    try:
        head = next(i for i, ln in enumerate(lines)
                    if ln.startswith("## " + title))
    except StopIteration:
        return None
    end = head + 1
    while end < len(lines) and not lines[end].startswith("## "):
        end += 1
    return head, end


def _require_live(wdir: Path, text: str) -> None:
    """Guard the verbs that write into a work: a closed one is reopened first."""
    if _status_of(text) == "done":
        raise WorkError(
            "work: {0} закрыта — сначала tide work reopen".format(wdir.name))


def _review_ready(text: str) -> bool:
    """The review gate: every AGREED item checked AND no offer left standing.

    «Every» spans both sections — a fix is an item like any other, so an
    unchecked one in ``## фиксы`` holds the gate exactly as an unchecked step in
    ``## чеклист`` does.

    A standing ``- [?]`` means the plan itself is still open — the work waits
    for the human's word, not for a verdict (работа 15 уехала в review с двумя
    висящими предложениями: чеклист был закрыт, разговор — нет).
    """
    its = items(text)
    return (bool(its) and all(done for done, _ in its)
            and not any(st == PROPOSED for st, _ in all_items(text)))


def _resync_status(
    text: str,
    now: Optional[datetime] = None,
) -> Tuple[str, Optional[str]]:
    """Re-hang the review gate after the checklist moved under it.

    One place for both directions: a checked last item (or a withdrawn last
    offer) closes the gate, an unchecked item (or a fresh offer agreed into the
    plan) re-opens it. ``done`` is never touched — that one is the human's.
    Returns ``(text, moved_to)``; *moved_to* is None when nothing moved.
    """
    st = _status_of(text)
    if st == "taken" and _review_ready(text):
        text = _set_status(text, "review")
        return _journal(text, "- {0} — все пункты чекнуты → review, ждёт "
                              "закрытия человеком".format(_stamp(now))), "review"
    if st == "review" and not _review_ready(text):
        text = _set_status(text, "taken")
        return _journal(text, "- {0} — чеклист снова неполон → taken".format(
            _stamp(now))), "taken"
    return text, None


def set_plan(
    root: Path,
    key: str,
    text: str,
    now: Optional[datetime] = None,
) -> Tuple[str, bool, str]:
    """Put the plan (free text) into ``## план`` — the agent's word, not a move.

    The section is created before ``## чеклист`` when absent. The status is
    deliberately untouched: proposing a plan is not taking the work, and the
    «да» stays with the human — но нить (и её шаг) проставляются уже здесь, до
    его «да»: иначе человека зовут смотреть план во вкладку нити, где работы
    ещё нет (кандидат 182). Returns ``(slug, replaced, owner)``.
    """
    body = (text or "").strip("\n").rstrip()
    if not body.strip():
        raise WorkError("work: пустой план — дай текст")
    wdir = _find(root, key)
    f, doc = _read(wdir)
    _require_live(wdir, doc)
    lines = doc.splitlines()
    block = ["## план", ""] + body.split("\n") + [""]
    found = _section(lines, PLAN_TITLE)
    if found:
        head, end = found
        lines = lines[:head] + block + lines[end:]
    else:
        at = _section(lines, CHECKLIST_TITLE)
        cut = at[0] if at else next(
            (i for i, ln in enumerate(lines) if ln.startswith("## ")), len(lines))
        lines = lines[:cut] + block + lines[cut:]
    doc = "\n".join(lines)
    if not doc.endswith("\n"):
        doc += "\n"
    doc = _journal(doc, "- {0} — план {1} агентом".format(
        _stamp(now), "обновлён" if found else "предложен"))
    doc, owner = _autostamp_thread(root, doc, now)
    _io.atomic_write(f, doc)
    return wdir.name, bool(found), owner


def _proposal_words(first: int, last: int, replaced: bool = False) -> str:
    """One wording for the print and the journal line — singular or a span."""
    one = first == last
    what = "шаг {0}".format(first) if one else "шаги {0}–{1}".format(first, last)
    waits = "ждёт" if one else "ждут"
    head = ("предложения заменены: " if replaced else
            "предложен " if one else "предложены ")
    return "{0}{1} ({2} «да» человека)".format(head, what, waits)


def _drop_proposals(lines: List[str]) -> List[str]:
    """Every ``- [?]`` item WITH its description gone; the rest untouched."""
    out: List[str] = []
    j = 0
    while j < len(lines):
        m = _ITEM_RE.match(lines[j])
        if m and m.group(1) == PROPOSED:
            j = _item_end(lines, j)
            continue
        out.append(lines[j])
        j += 1
    return out


def propose(
    root: Path,
    key: str,
    texts: List[str],
    replace: bool = False,
    now: Optional[datetime] = None,
) -> Tuple[str, int, int, bool, str]:
    """Append ``- [?]`` items to the checklist — an offer, not the plan.

    A ``\\n`` inside an item splits title from description (see the module
    docstring). With *replace* the standing offers are withdrawn first — the
    agent rethought the steps, and stale ``- [?]`` lines would only crowd the
    board; agreed ``- [ ]``/``- [x]`` items are never touched.

    Numbering is file-order (proposals included), so the journal names the very
    numbers the human sees on the board. ``check`` refuses these items until the
    human's «да» turns them into ``- [ ]``. A work sitting in review comes back
    to taken: there is a fresh question on the table, so it is not waiting to be
    closed. Нить (и её шаг) проставляются здесь же, если их ещё нет: пункты
    уходят человеку на «да», и вкладка нити должна их уже показывать
    (кандидат 182). Returns ``(slug, first, last, reopened, owner)``.
    """
    texts = [t for t in texts if t and t.strip()]
    if not texts:
        raise WorkError("work: пустое предложение — дай пункты")
    blocks = [_item_lines(PROPOSED, t) for t in texts]  # validates the titles
    wdir = _find(root, key)
    f, doc = _read(wdir)
    _require_live(wdir, doc)
    lines = doc.splitlines()
    if replace:
        lines = _drop_proposals(lines)
    found = _section(lines, CHECKLIST_TITLE)
    if not found:
        raise WorkError("work: паспорт без секции ## чеклист")
    head, end = found
    at = end
    while at > head + 1 and not lines[at - 1].strip():
        at -= 1  # land right after the last item, before the section's blank tail
    # numbers are counted at the LANDING spot, not over the whole file: with a
    # ## фиксы section below, an offer appended to the checklist takes number 5
    # and pushes the fixes down — that is the number the human will see
    first = _count_items(lines[:at]) + 1
    last = first + len(texts) - 1
    doc = "\n".join(lines[:at] + [ln for b in blocks for ln in b] + lines[at:])
    if not doc.endswith("\n"):
        doc += "\n"
    doc = _journal(doc, "- {0} — {1}".format(
        _stamp(now), _proposal_words(first, last, replace)))
    reopened = _status_of(doc) == "review"
    if reopened:
        doc = _set_status(doc, "taken")
        doc = _journal(doc, "- {0} — предложены шаги — работа вернулась в "
                            "работу (review → taken)".format(_stamp(now)))
    doc, owner = _autostamp_thread(root, doc, now)
    _io.atomic_write(f, doc)
    return wdir.name, first, last, reopened, owner


def _item_number(raw: str, key: str) -> int:
    """Parse an item number typed on the CLI, or refuse in tide's own words.

    The hints print a literal ``N`` for the number, and a person pastes the line
    before substituting. argparse's ``type=int`` would answer that with "invalid
    int value: 'N'" — the parser's voice, at the exact moment the message was
    trying to teach (работа 57 п.7). So the number arrives as text and lands here.
    """
    s = (raw or "").strip()
    if s.isdigit() and int(s) > 0:
        return int(s)
    return _bad_number(s, key)


def _bad_number(s: str, key: str) -> int:
    raise WorkError(
        "work: {0!r} — это не номер пункта, подставь вместо N число "
        "(первый пункт — 1). Номера видно в tide work show {1}".format(s, key))


def _require_word(word: Optional[str], whose: str = "согласовывает человек",
                  *, kept: str = "", howto: str = "") -> str:
    """The human's word IS the agreement — no word, no gesture (as in ``close``).

    THE ONE place the requirement lives, so every caller — CLI, board, API — is
    refused with the same sentence. The CLI parsers deliberately do NOT mark
    ``--word`` argparse-required (работа 57 п.5): these are the human's SIGNATURE
    gestures, met in a person's first hour, and argparse would answer them with
    "the following arguments are required: --word" — the parser's words, no hint
    of what to do next. Dropping ``required`` lets the miss reach this guard,
    which says what happened, *kept* — what stayed as it was — and *howto*, the
    command with the key already filled in. The refusal itself is unchanged: a
    blank or whitespace word is still no word.
    """
    w = (word or "").strip()
    if not w:
        raise WorkError('work: {0}{1} — зови с --word "его слово дословно"{2}'.format(
            whose,
            ", {0}".format(kept) if kept else "",
            ". {0}".format(howto) if howto else ""))
    return w


def _proposed_block(lines: List[str], index: int) -> Tuple[int, int, str]:
    """``(start, end, title)`` of the PROPOSED item *index*; *end* is exclusive.

    Refuses an item that is no longer an offer — agreeing twice, or over a
    checked item, would silently rewrite what the human already signed.
    """
    n = 0
    for j, ln in enumerate(lines):
        m = _ITEM_RE.match(ln)
        if not m:
            continue
        n += 1
        if n != index:
            continue
        if m.group(1) != PROPOSED:
            raise WorkError(
                "work: пункт {0} {1} — это уже не предложение".format(
                    index, "чекнут" if m.group(1) == "x" else "согласован"))
        return j, _item_end(lines, j), m.group(2)
    raise WorkError("work: нет пункта {0} (в чеклисте {1})".format(index, n))


def _steps_words(nums: List[int]) -> str:
    """«шаг 2» / «шаги 2, 4» — the numbers the human sees on the board."""
    if len(nums) == 1:
        return "шаг {0}".format(nums[0])
    return "шаги {0}".format(", ".join(str(n) for n in nums))


def agree(
    root: Path,
    key: str,
    indexes: Optional[List[int]] = None,
    word: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[str, List[int], bool, Optional[str]]:
    """``- [?]`` → ``- [ ]`` by the human's WORD — the «да» as it is said.

    The «да» used to be a board button; in a chat it is a word, and a word only
    counts when it is written down — so *word* is required and lands in the
    journal verbatim. Empty *indexes* means every standing offer (the «да» came
    for the lot). Descriptions ride along untouched. Agreement is not progress:
    a fresh ``- [ ]`` is something still to do, so a work parked in review comes
    back to taken. Returns ``(slug, numbers, was_all, moved_to)``.
    """
    w = _require_word(
        word, kept="пункты остались предложениями",
        howto='tide work agree {0} {1} --word "его слово"; '
             'что предложено — в tide work show {0}'.format(
                 key, " ".join(str(n) for n in (indexes or [])) or "--all"))
    wdir = _find(root, key)
    f, doc = _read(wdir)
    _require_live(wdir, doc)
    lines = doc.splitlines()
    nums = sorted(set(indexes or []))
    every = not nums
    if every:
        nums = [i for i, (st, _) in enumerate(all_items(doc), 1) if st == PROPOSED]
        if not nums:
            raise WorkError(
                "work: нет предложенных пунктов — согласовывать нечего")
    for n in nums:
        j, _, title = _proposed_block(lines, n)
        lines[j] = "- [ ] {0}".format(title)
    doc = "\n".join(lines)
    if not doc.endswith("\n"):
        doc += "\n"
    if every:
        doc = _journal(doc, "- {0} — предложения подтверждены словом: «{1}»".format(
            _stamp(now), w))
    else:
        for n in nums:
            doc = _journal(doc, "- {0} — пункт {1} подтверждён словом: «{2}»".format(
                _stamp(now), n, w))
    doc, moved = _resync_status(doc, now)
    _io.atomic_write(f, doc)
    return wdir.name, nums, every, moved


def drop_proposed(
    root: Path,
    key: str,
    indexes: List[int],
    word: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[str, List[int], Optional[str]]:
    """The other half of the answer: «нет» to offer *N* — the item is gone.

    Removes the ``- [?]`` line WITH its description (a withdrawn offer leaves no
    orphan details) and journals the word that dropped it. Cut from the back so
    the numbers still ahead don't slide out from under us; the journal names the
    numbers the human saw. Dropping the LAST offer can finish a work whose items
    are all checked — the gate is re-hung here. Returns
    ``(slug, numbers, moved_to)``.
    """
    w = _require_word(
        word, "снять предложение — тоже слово человека", kept="ничего не снято",
        howto='tide work agree {0} --drop {1} --word "его слово"'.format(
            key, " ".join(str(n) for n in (indexes or [])) or "N"))
    nums = sorted(set(indexes or []))
    if not nums:
        raise WorkError("work: снимать — по номерам: --drop N (можно несколько)")
    wdir = _find(root, key)
    f, doc = _read(wdir)
    _require_live(wdir, doc)
    lines = doc.splitlines()
    for n in reversed(nums):
        j, end, _ = _proposed_block(lines, n)
        lines = lines[:j] + lines[end:]
    doc = "\n".join(lines)
    if not doc.endswith("\n"):
        doc += "\n"
    for n in nums:
        doc = _journal(doc, "- {0} — пункт {1} снят словом: «{2}»".format(
            _stamp(now), n, w))
    # тем же порядком, что резали, — с хвоста: иначе второй сдвиг уедет
    for n in reversed(nums):
        doc = _shift_refs(doc, n, now)
    doc, moved = _resync_status(doc, now)
    _io.atomic_write(f, doc)
    return wdir.name, nums, moved


def _shift_refs(text: str, removed: int, now: Optional[datetime] = None) -> str:
    """Свести ЖИВЫЕ ссылки на номера пунктов после того, как пункт *removed* снят.

    Номер пункта — адрес строки в файле, и снятие двигает всё, что ниже. Живых
    ссылок на этот адрес ровно две, и обе читаются ПРЯМО СЕЙЧАС: курсор ``at:``
    (доска подчёркивает им пункт) и строки приёмки в журнале (из них выводится
    ✓✓ — см. :func:`accepted_items`). Обе и правим, иначе они начнут показывать
    на чужой пункт: соврать адресом хуже, чем его потерять.

    Остальные строки журнала — рассказ, а не адрес: «пункт 3 ✓ «текст»» несёт
    сам текст пункта и читается как история («тогда он был третьим»). Историю не
    трут. По той же причине снятая приёмка не стирается, а глушится припиской —
    слово человека остаётся в файле, адресом быть перестаёт.
    """
    cur = re.search(r"^at:\s*(\d+)\s*$", text, re.M)
    if cur:
        n = int(cur.group(1))
        if n == removed:
            text = _set_meta(text, "at", "")
            text = _journal(text, "- {0} — курсор снят: его пункт снят".format(
                _stamp(now)))
        elif n > removed:
            text = _set_meta(text, "at", str(n - 1))
            text = _journal(text, "- {0} — курсор переехал на пункт {1} "
                                  "(номера сдвинулись)".format(_stamp(now), n - 1))
    lines = text.splitlines()
    found = _section(lines, JOURNAL_TITLE)
    if not found:
        return text
    for j in range(found[0], found[1]):
        m = _ACCEPT_ONE_RE.match(lines[j])
        if not m:
            continue
        n = int(m.group(1))
        if n == removed:
            lines[j] = lines[j].rstrip() + " (пункт снят)"
        elif n > removed:
            lines[j] = re.sub(r"— пункт \d+ принят рукой\s*$",
                              "— пункт {0} принят рукой".format(n - 1), lines[j])
    return "\n".join(lines) + "\n"


def _agreed_block(lines: List[str], index: int) -> Tuple[int, int, str]:
    """``(start, end, title)`` СОГЛАСОВАННОГО и НЕ чекнутого пункта *index*.

    Предложение и чекнутый пункт сюда не ходят: у каждого свой ответ, и оба
    снимаются не тем жестом, что правка договора (см. :func:`drop_item`).
    """
    n = 0
    for j, ln in enumerate(lines):
        m = _ITEM_RE.match(ln)
        if not m:
            continue
        n += 1
        if n != index:
            continue
        if m.group(1) == PROPOSED:
            raise WorkError(
                "work: пункт {0} — ещё предложение, а не договор; на него "
                "отвечают «нет»: tide work agree <key> --drop {0} --word "
                "«слово»".format(index))
        if m.group(1) == "x":
            raise WorkError(
                "work: пункт {0} чекнут — снять значит стереть сделанное с его "
                "пруфом; сначала tide work uncheck {0}".format(index))
        return j, _item_end(lines, j), m.group(2)
    raise WorkError("work: нет пункта {0} (в чеклисте {1})".format(index, n))


def drop_item(
    root: Path,
    key: str,
    index: int,
    word: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[str, str, Optional[str]]:
    """Снять согласованный пункт *index* — REQUIRES ``--word``.

    Договор подписан человеком, и правит его только он: ``--word`` — то, чем он
    попросил снять. До этого верба снять обычный пункт было НЕЧЕМ (кандидат
    183): ``agree --drop`` отвечает лишь на висящее ``- [?]``, а ``checklist``
    подменяет весь чеклист «согласованными» пунктами, то есть обходит гейт «да».

    Пункт уходит со своим описанием (осиротевшие подробности никому не нужны),
    живые ссылки на номера сводятся (:func:`_shift_refs`), а последний
    несделанный пункт, уйдя, может закрыть гейт — статус перевешивается здесь
    же. Returns ``(slug, title, moved_to)``.
    """
    w = _require_word(
        word, "снять согласованный пункт может человек",
        kept="пункт {0} остался на месте".format(index),
        howto='tide work drop {0} {1} --word "его слово"'.format(key, index))
    if index < 1:
        raise WorkError("work: номер пункта считается с 1")
    wdir = _find(root, key)
    f, doc = _read(wdir)
    _require_live(wdir, doc)
    lines = doc.splitlines()
    j, end, title = _agreed_block(lines, index)
    doc = "\n".join(lines[:j] + lines[end:])
    if not doc.endswith("\n"):
        doc += "\n"
    doc = _journal(doc, "- {0} — пункт {1} «{2}» снят словом: «{3}»".format(
        _stamp(now), index, title, w))
    doc = _shift_refs(doc, index, now)
    doc, moved = _resync_status(doc, now)
    _io.atomic_write(f, doc)
    return wdir.name, title, moved


def set_checklist(
    root: Path,
    key: str,
    texts: List[str],
    force: bool = False,
    now: Optional[datetime] = None,
) -> str:
    """Replace the checklist with the AGREED items (gesture 1: разложить).

    A ``\\n`` inside an item splits title from description, as in ``propose``.
    Refuses when checked items exist (progress would be erased) unless *force*
    — the human's explicit word. Journals the agreement.
    """
    texts = [t for t in texts if t and t.strip()]
    if not texts:
        raise WorkError("work: пустой чеклист — дай пункты")
    blocks = [_item_lines(" ", t) for t in texts]  # validates the titles
    wdir = _find(root, key)
    f, text = _read(wdir)
    _require_live(wdir, text)
    if any(done for done, _ in items(text)) and not force:
        raise WorkError(
            "work: в чеклисте есть чекнутые пункты — замена сотрёт прогресс "
            "(--force только по слову человека)")
    lines = text.splitlines()
    found = _section(lines, CHECKLIST_TITLE)
    if not found:
        raise WorkError("work: паспорт без секции ## чеклист")
    head, end = found
    block = ["## чеклист"] + [ln for b in blocks for ln in b] + [""]
    text = "\n".join(lines[:head] + block + lines[end:])
    if not text.endswith("\n"):
        text += "\n"
    text = _journal(text, "- {0} — чеклист согласован: {1} пункт(ов)".format(
        _stamp(now), len(texts)))
    _io.atomic_write(f, text)
    return wdir.name


def _fixes_anchor(lines: List[str]) -> int:
    """Where a fresh ``## фиксы`` is born: after ``## чеклист``, before журнал.

    The section has one place in the file and the whole numbering leans on it —
    fixes come after the checklist, so their numbers continue it, and before the
    journal, which stays last (``_journal`` appends to the tail of the file).
    """
    checklist = _section(lines, CHECKLIST_TITLE)
    if checklist:
        return checklist[1]
    journal = _section(lines, JOURNAL_TITLE)
    if journal:
        return journal[0]
    return len(lines)


def _fix_words(first: int, last: int) -> str:
    """One wording for the print and the journal — singular or a span."""
    if first == last:
        return "фикс {0} добавлен".format(first)
    return "фиксы {0}–{1} добавлены".format(first, last)


def add_fixes(
    root: Path,
    key: str,
    texts: List[str],
    word: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[str, int, int, bool]:
    """The human's afterthoughts at the gate → ``## фиксы``, already agreed.

    He looked at the finished work and said «а ещё вот это» — that remark IS the
    agreement, so the items land as ``- [ ]`` straight away and *word* is
    required: without it there is nothing making them the plan, and a ``- [?]``
    would ask him to sign what he has just asked for.

    Numbers continue the checklist (see the module docstring): with four items
    above, the first fix is 5 — the journal names the numbers the board shows.
    A work parked in review comes back to taken; there is work on the table
    again, not a verdict. Returns ``(slug, first, last, reopened)``.
    """
    texts = [t for t in texts if t and t.strip()]
    if not texts:
        raise WorkError("work: пустой фикс — дай пункты")
    w = _require_word(
        word, "фикс несёт накидку человека", kept="ничего не добавлено",
        howto='tide work fix {0} "что доделать" --word "его слово"'.format(key))
    blocks = [_item_lines(" ", t) for t in texts]  # validates the titles
    wdir = _find(root, key)
    f, doc = _read(wdir)
    _require_live(wdir, doc)
    lines = doc.splitlines()
    body = [ln for b in blocks for ln in b]
    found = _section(lines, FIXES_TITLE)
    if found:
        head, end = found
        at = end
        while at > head + 1 and not lines[at - 1].strip():
            at -= 1  # after the last fix, before the section's blank tail
        tail = body
    else:
        at = _fixes_anchor(lines)
        gap = [""] if at and lines[at - 1].strip() else []
        tail = gap + ["## " + FIXES_TITLE] + body + [""]
    first = _count_items(lines[:at]) + 1
    last = first + len(texts) - 1
    doc = "\n".join(lines[:at] + tail + lines[at:])
    if not doc.endswith("\n"):
        doc += "\n"
    doc = _journal(doc, "- {0} — {1} словом: «{2}»".format(
        _stamp(now), _fix_words(first, last), w))
    reopened = _status_of(doc) == "review"
    if reopened:
        doc = _set_status(doc, "taken")
        doc = _journal(doc, "- {0} — фикс вернул в работу (review → taken)".format(
            _stamp(now)))
    _io.atomic_write(f, doc)
    return wdir.name, first, last, reopened


def _head_title(text: str) -> Tuple[int, str]:
    """``(line index, title)`` of the passport's H1 — the name the board shows."""
    for j, ln in enumerate(text.splitlines()):
        if ln.startswith("# "):
            return j, ln[2:].strip()
    raise WorkError("work: паспорт без заголовка — первая строка это «# …»")


def _clip(text: str, limit: int) -> str:
    """Cut a title down to one journal line's worth."""
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def set_title(
    root: Path,
    key: str,
    title: str,
    word: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[str, str, str, bool]:
    """Rewrite the H1 — REQUIRES the human's word, moves no status.

    The name is what the human signed up to, so the agent may only carry it over
    with the word that asked for the rename, and that word lands in the journal
    beside the old name — a renamed work still reads back. The dir slug is left
    alone: it is the address the board, the journal and every other hand already
    hold. A done work renames too (its history has to stay readable), and the
    caller says so out loud. Returns ``(slug, old, new, was_done)``.
    """
    new = " ".join((title or "").split())
    if not new:
        raise WorkError("work: пустой заголовок — дай название работы")
    w = _require_word(
        word, "имя работы меняет человек", kept="заголовок остался прежним",
        howto='tide work title {0} "новый заголовок" --word "его слово"'.format(key))
    wdir = _find(root, key)
    f, text = _read(wdir)
    j, old = _head_title(text)
    if new == old:
        raise WorkError("work: {0} уже так и называется".format(wdir.name))
    lines = text.splitlines()
    lines[j] = "# " + new
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    text = _journal(text, "- {0} — переименована словом: «{1}» (было: {2})".format(
        _stamp(now), w, _clip(old, _TITLE_IN_JOURNAL)))
    _io.atomic_write(f, text)
    return wdir.name, old, new, _status_of(text) == "done"


def _set_meta(text: str, key: str, value: str, after: str = "created") -> str:
    """Set ``key: value`` in the passport meta; empty *value* removes the line.

    An existing key is rewritten in place; a new key lands right after *after*
    (``created:`` by default, i.e. the head of the meta block). Order inside the
    block is irrelevant to the parsers — every hand reads meta line-by-line by
    regex — but a human reads the file too, and ``step:`` right under ``thread:``
    says «нить и её шаг» at a glance instead of making him hunt.
    """
    pat = re.compile(r"^{0}:\s*.*$".format(re.escape(key)), re.M)
    if not value:
        return re.sub(r"^{0}:\s*.*\n?".format(re.escape(key)), "", text,
                      count=1, flags=re.M)
    if pat.search(text):
        return pat.sub("{0}: {1}".format(key, value), text, count=1)
    anchor = after if re.search(r"^{0}: .*$".format(re.escape(after)), text, re.M) \
        else "created"
    return re.sub(r"^({0}: .*)$".format(re.escape(anchor)),
                  "\\g<0>\n{0}: {1}".format(key, value),
                  text, count=1, flags=re.M)


def resolve_caller_thread(work_root: Path) -> Optional[str]:
    """The нить of the session that invoked us, as an address — or None.

    Reads ``$CLAUDE_CODE_SESSION_ID`` → the session arc pinned to it IN THE
    CALLER's own project (which may differ from where the work lives). Returns
    the bare thread slug when the caller sits in the same project as the work,
    else the cross-project address ``<project>/<thread>``. None when there is no
    sid, the caller isn't a tide session, or nothing matches — then ``take``
    records no owner and the human attaches one on the board (fork «и рукой»).

    Public because ``arc.artifact`` stamps ``from-arc:`` off the same answer —
    one address for «откуда это пришло», wherever it is asked.
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


def thread_dir(root: Path, thread: str) -> Optional[Path]:
    """Каталог нити по адресу с карточки — в своём проекте или в соседнем.

    ``thread:`` бывает голым слагом (``26-@release``) и кросс-проектным адресом
    (``tide-stack/26-@release``, см. :func:`resolve_caller_thread`). Соседний
    проект ищется по ростеру; нет ростера или нет такой нити — None, и читающий
    просто останется без плана (гадать нельзя).
    """
    ref = (thread or "").strip().rstrip("/")
    if not ref:
        return None
    project, _, tail = ref.rpartition("/")
    if project:
        from .candidate import _resolve_target_root
        try:
            root = _resolve_target_root(project)
        except Exception:  # нет ростера/нет проекта — адрес просто не читается
            return None
    d = paths.arcs_dir(root)
    if not d.is_dir():
        return None
    want = _thread_key(tail)
    return next((p for p in sorted(d.iterdir())
                 if p.is_dir() and _thread_key(p.name) == want), None)


def plan_steps(root: Path, thread: str) -> List[Tuple[int, str, str]]:
    """Шаги плана нити как ``(номер, состояние, имя)`` — пусто, когда плана нет.

    Читается ``<нить>/plan.md``, раздел ``## шаги``. Имя шага — то, что до
    первой ``|``: строка шага несёт три поля (имя · что делается · результат), а
    в глаза человеку смотрит первое.
    """
    d = thread_dir(root, thread)
    if d is None:
        return []
    plan = d / "plan.md"
    if not plan.is_file():
        return []
    try:
        lines = plan.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    found = _section(lines, PLAN_STEPS_TITLE)
    if not found:
        return []
    out = []
    for ln in lines[found[0]:found[1]]:
        m = _PLAN_STEP_RE.match(ln)
        if m:
            out.append((int(m.group(2)), m.group(1),
                        m.group(3).split("|")[0].strip()))
    return out


def current_plan_step(root: Path, thread: str) -> Optional[int]:
    """Номер шага, помеченного ``[>]`` в плане нити — или None.

    None и когда плана нет, и когда текущий шаг никем не помечен: работа тогда
    остаётся без адреса, и это честнее, чем угадать «наверное, первый».
    """
    return next((n for n, st, _ in plan_steps(root, thread) if st == CURRENT_STEP),
                None)


def plan_step_title(root: Path, thread: str, step: int) -> str:
    """Имя шага *step* в плане нити — «», когда не прочиталось."""
    return next((t for n, _, t in plan_steps(root, thread) if n == step), "")


def _follow_step(
    root: Path,
    text: str,
    thread: str,
    now: Optional[datetime] = None,
) -> Tuple[str, Optional[int]]:
    """Перечитать ``step:`` из плана нити *thread* — шаг всегда ходит за нитью.

    Нить сменилась или пришла — старый адрес шага уже про другой план, поэтому
    поле не дополняется, а ПЕРЕСТАВЛЯЕТСЯ: прочиталось — новый номер, не
    прочиталось — снимается. Каждое движение строкой в журнал.
    """
    was = re.search(r"^step:\s*(\d+)\s*$", text, re.M)
    step = current_plan_step(root, thread) if thread else None
    if step is None:
        if not was:
            return text, None
        text = _set_meta(text, "step", "")
        return _journal(text, "- {0} — шаг снят (в плане нити его нет)".format(
            _stamp(now))), None
    if was and int(was.group(1)) == step:
        return text, step
    text = _set_meta(text, "step", str(step), after="thread")
    title = plan_step_title(root, thread, step)
    return _journal(text, "- {0} — шаг плана → {1}{2} (из плана нити)".format(
        _stamp(now), step, " «{0}»".format(title) if title else "")), step


def _autostamp_thread(
    root: Path,
    text: str,
    now: Optional[datetime] = None,
) -> Tuple[str, str]:
    """Проставить нить (и её шаг) из сессии-автора, если на карточке их нет.

    Зовут ``add``/``plan``/``propose`` — то есть работа обретает адрес ДО «да»
    человека, а не после (кандидат 182). Уже проставленную нить не трогаем:
    явное — рука человека или ``take --thread`` — сильнее автоматики.
    Returns ``(text, owner)``; *owner* пуст, когда ничего не проставилось.
    """
    if re.search(r"^thread:\s*\S", text, re.M):
        return text, ""
    owner = resolve_caller_thread(root)
    if not owner:
        return text, ""
    text = _set_meta(text, "thread", owner)
    text = _journal(text, "- {0} — ответственная нить → {1} (сессия-автор)".format(
        _stamp(now), owner))
    text, _ = _follow_step(root, text, owner, now)
    return text, owner


def set_thread(
    root: Path,
    key: str,
    thread: Optional[str],
    source: str = "рука человека",
    now: Optional[datetime] = None,
) -> str:
    """Set/clear the responsible нить (``thread:``). Empty *thread* clears it.

    ``step:`` едет следом: у новой нити свой план, а без нити адрес шага
    бессмыслен (см. :func:`_follow_step`).
    """
    wdir = _find(root, key)
    f, text = _read(wdir)
    val = (thread or "").strip()
    text = _set_meta(text, "thread", val)
    line = ("- {0} — ответственная нить → {1} ({2})".format(_stamp(now), val, source)
            if val else
            "- {0} — нить снята ({1})".format(_stamp(now), source))
    text = _journal(text, line)
    text, _ = _follow_step(root, text, val, now)
    _io.atomic_write(f, text)
    return wdir.name


def set_step(
    root: Path,
    key: str,
    step: Optional[int],
    word: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[str, Optional[int], str]:
    """Поставить/снять ``step:`` руками — когда автоподхват промолчал.

    Шаг живёт только при нити: без ``thread:`` номер не к чему привязать, и
    карточка соврала бы адресом в чужом плане. Номер НЕ сверяется с планом
    намеренно — план правится и на ходу, а работа может целить в шаг, который
    ещё дописывают; сверка была бы гейтом там, где нужна пометка.
    Returns ``(slug, step, title)``.
    """
    wdir = _find(root, key)
    f, text = _read(wdir)
    m = re.search(r"^thread:\s*(\S+)", text, re.M)
    if step is None:
        if not re.search(r"^step:\s*\S", text, re.M):
            raise WorkError("work: {0} — шаг и так не стоит".format(wdir.name))
        text = _set_meta(text, "step", "")
        text = _journal(text, "- {0} — шаг снят{1}".format(
            _stamp(now), " словом: «{0}»".format(word.strip())
            if word and word.strip() else ""))
        _io.atomic_write(f, text)
        return wdir.name, None, ""
    if step < 1:
        raise WorkError("work: шаг плана считается с 1")
    if not m:
        raise WorkError(
            "work: {0} без нити — шаг это адрес В ПЛАНЕ НИТИ; сначала "
            "tide work thread {0} --set <нить>".format(wdir.name))
    text = _set_meta(text, "step", str(step), after="thread")
    title = plan_step_title(root, m.group(1), step)
    text = _journal(text, "- {0} — шаг плана → {1}{2} (рукой)".format(
        _stamp(now), step, " «{0}»".format(title) if title else ""))
    _io.atomic_write(f, text)
    return wdir.name, step, title


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
    caller's session (fork «и авто»); None when unresolvable — а вместе с нитью
    перечитывается ``step:``, её текущий шаг. Обычно к моменту ``take`` нить уже
    стоит с ``add``/``plan``; здесь она подтверждается (и адрес шага
    освежается: пока работу согласовывали, план мог шагнуть). Returns
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
    owner = (thread or "").strip() or resolve_caller_thread(root)
    if owner:
        text = _set_meta(text, "thread", owner)
    note = " по слову: «{0}»".format(word.strip()) if word and word.strip() else ""
    tail = " · нить {0}".format(owner) if owner else ""
    text = _journal(text, "- {0} — взята в работу ({1}){2}{3}".format(
        _stamp(now), who, note, tail))
    if owner:
        text, _ = _follow_step(root, text, owner, now)
    _io.atomic_write(f, text)
    return wdir.name, owner


def dispatch(
    root: Path,
    key: str,
    to: str,
    now: Optional[datetime] = None,
) -> Tuple[str, str]:
    """След «строитель отправлен» — ФАКТ в журнале, статус не двигает.

    Урок 01.08 (кандидаты 179/180): оркестратор взял работу 35, доложил
    человеку и писал в пульсы «у воркера» — а сообщения воркеру не отправил.
    Работа три часа стояла молча, доска показывала стройку, которой не было.
    Очередь диспатчей жила В ГОЛОВЕ оркестратора, и сверить её было не с чем:
    единственным признаком жизни был курсор, а его ставит тот самый воркер,
    которого не запустили.

    Поэтому жест: отправил строителя — скажи это файлу. Статус не двигается
    намеренно — «строитель отправлен» не состояние работы, а событие, которое с
    ней случилось; стейт-машина open→taken→review→done остаётся ровно той же.
    По той же причине повторный диспатч — не ошибка, а ещё одна строка: воркера
    переотправляют (упал, сменили, добавили второго), и каждая отправка
    самостоятельный факт со своим временем.

    Шлём ТОЛЬКО на взятую: диспатч на open — дырка цикла (плана ещё нет, «да»
    человека не сказано, отвечать за работу некому), на review — стройка
    кончилась и ждут руку, на done — работа закрыта. Returns ``(slug, who)``.
    """
    who = " ".join((to or "").split())
    if not who:
        raise WorkError(
            "work: кого отправили? — никто не отправлен, статус не тронут; "
            'dispatch зовут с --to "имя". '
            'tide work dispatch {0} --to "имя строителя"'.format(key))
    wdir = _find(root, key)
    f, text = _read(wdir)
    st = _status_of(text)
    if st == "done":
        raise WorkError(
            "work: {0} закрыта — сначала tide work reopen".format(wdir.name))
    if st != "taken":
        raise WorkError(
            "work: {0} не в работе (status: {1}) — строителя шлют на ВЗЯТУЮ: "
            "план согласован, работа взята, тогда dispatch".format(wdir.name, st))
    text = _journal(text, "- {0} — строитель отправлен: {1}".format(
        _stamp(now), who))
    _io.atomic_write(f, text)
    return wdir.name, who


def _item_text(text: str, index: int) -> str:
    """Текст пункта *index* (с 1) — или WorkError, если такого пункта нет."""
    n = 0
    for ln in text.splitlines():
        m = _ITEM_RE.match(ln)
        if not m:
            continue
        n += 1
        if n == index:
            return m.group(2)
    raise WorkError("work: нет пункта {0} (в чеклисте {1})".format(index, n))


def at(
    root: Path,
    key: str,
    index: Optional[int],
    now: Optional[datetime] = None,
) -> Tuple[str, str]:
    """Курсор работы: `at: N` — на КАКОМ ПУНКТЕ исполнитель сейчас.

    Единственный честный источник для подчёркивания текущего пункта на доске
    (фикс 15 работы 25). Вывести его было неоткуда: журнал знает лишь «пункт N
    ✓» — прошлое, последний завершённый; а эвристика «первый нечекнутый» уже
    соврала человеку — пункты делаются не по порядку, и фиксы идут вперемешку.
    Поэтому курсор не выводится, а СТАВИТСЯ жестом: взялся за пункт — сказал.

    Пустой *index* снимает курсор. Курсор — не статус: он ничего не гейтит и
    ничему не мешает, это метка «я тут». Снимается сам, когда пункт чекнут (см.
    ``check``), и протухает на доске вместе с работой: доска верит `at:`, лишь
    пока работа жива по своему журналу, — упавший агент не будет врать
    подчёркиванием сутками.

    Returns ``(slug, item_text)``; *item_text* пуст, когда курсор снят.
    """
    wdir = _find(root, key)
    f, text = _read(wdir)
    if index is None:
        if not re.search(r"^at:\s*.*$", text, re.M):
            raise WorkError("work: {0} — курсор и так не стоит".format(wdir.name))
        text = _set_meta(text, "at", "")
        text = _journal(text, "- {0} — курсор снят".format(_stamp(now)))
        _io.atomic_write(f, text)
        return wdir.name, ""
    if index < 1:
        raise WorkError("work: номер пункта считается с 1")
    item = _item_text(text, index)
    text = _set_meta(text, "at", str(index))
    text = _journal(text, "- {0} — курсор на пункте {1} «{2}»".format(
        _stamp(now), index, item))
    _io.atomic_write(f, text)
    return wdir.name, item


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
    # курсор снимается сам, когда его пункт закрыт (фикс 15): держать «я тут» на
    # сделанном — врать, а требовать от агента второго жеста значит гарантировать,
    # что он его забудет. Курсор на ДРУГОМ пункте не трогаем: воркеров бывает
    # несколько, и чужую метку этот чек не отменяет
    if re.search(r"^at:\s*{0}\s*$".format(index), text, re.M):
        text = _set_meta(text, "at", "")
    text, moved = _resync_status(text, now)
    _io.atomic_write(f, text)
    return wdir.name, moved == "review"


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
    text, _ = _resync_status(text, now)
    _io.atomic_write(f, text)
    return wdir.name


def close(root: Path, key: str, word: str,
          now: Optional[datetime] = None) -> Tuple[str, int]:
    """Any live status → done. The human's word is REQUIRED — it IS the gate.

    The same word ACCEPTS the work it closes: when anything is checked, the mass
    acceptance line lands right before the closing one, so every «сделано» gets
    its «принято» in one gesture (the human doesn't tick a done work item by
    item to say he took it). Nothing checked — nothing to accept, no line.
    Returns ``(slug, accepted)`` — how many checked items that word took.
    """
    if not (word or "").strip():
        raise WorkError(
            "work: done ставит человек, {0} осталась открытой — закрывай только "
            'с --word "его слово". tide work close {0} --word "его слово"'.format(key))
    wdir = _find(root, key)
    f, text = _read(wdir)
    if _status_of(text) == "done":
        raise WorkError("work: {0} уже закрыта".format(wdir.name))
    accepted = sum(1 for done, _ in items(text) if done)
    text = _set_status(text, "done")
    if accepted:
        text = _journal(text, "- {0} — {1}".format(_stamp(now), ACCEPT_ALL_LINE))
    text = _journal(text, "- {0} — закрыта по слову человека: «{1}»".format(
        _stamp(now), word.strip()))
    _io.atomic_write(f, text)
    return wdir.name, accepted


def reopen(root: Path, key: str, word: Optional[str] = None,
           now: Optional[datetime] = None) -> Tuple[str, str]:
    """done → назад в ЖИВОЙ статус по паспорту. Приёмки не трогает: журнал —
    история, её не трут.

    Куда именно — читается из карточки, а не штампуется «open». Работа с
    ``taken-by`` не становится ничьей оттого, что её открыли обратно: исполнитель
    как числился, так и числится, а чеки никто не снимал — «open» соврал бы про
    оба факта разом (кандидат 168: работа 31 висела open с чужим taken-by и 1/1
    сделано — состояния, которого не бывает). Поэтому:
    есть ``taken-by`` → taken, а если при этом чеклист закрыт — сразу review
    (всё сделано, человек просто передумал закрывать); нет ``taken-by`` → open.

    The mass acceptance line stays where it was written; it just stops speaking
    for the checklist once the work is live again (see the module docstring).
    Returns ``(slug, status)`` — куда работа вернулась.
    """
    wdir = _find(root, key)
    f, text = _read(wdir)
    if _status_of(text) != "done":
        raise WorkError("work: {0} и так открыта".format(wdir.name))
    who = re.search(r"^taken-by:\s*(\S.*)$", text, re.M)
    note = " по слову: «{0}»".format(word.strip()) if word and word.strip() else ""
    if who:
        text = _set_status(text, "taken")
        text = _journal(text, "- {0} — открыта заново{1} → taken ({2})".format(
            _stamp(now), note, who.group(1).strip()))
        text, _ = _resync_status(text, now)  # чеклист закрыт → сразу review
    else:
        text = _set_status(text, "open")
        text = _journal(text, "- {0} — открыта заново{1}".format(_stamp(now), note))
    _io.atomic_write(f, text)
    return wdir.name, _status_of(text)


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
        props = sum(1 for s, _ in all_items(text) if s == PROPOSED)
        m = re.search(r"^deadline:\s*(\S+)", text, re.M)
        dl = m.group(1) if m else ""
        rows.append((st == "done", dl or "9999", p.name, st, its, dl, title, props))
    if not rows:
        return "tide: работ нет ({0})".format(wdir)
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    out = []
    for _, _, name, st, its, dl, title, props in rows:
        done_n = sum(1 for d, _ in its if d)
        out.append("{0:<34} {1:<7} {2}/{3}{4}{5}  {6}".format(
            name, st, done_n, len(its),
            "+{0}?".format(props) if props else "",
            "  до " + dl if dl else "", title))
    return "\n".join(out)


def _thread_key(ref: str) -> str:
    """Сравнимый ключ адреса нити: хвост кросс-проектного адреса, без номера.

    ``thread:`` на карточке бывает и голым слагом нити, и адресом соседнего
    проекта (``<проект>/<нить>``, см. :func:`resolve_caller_thread`) — сверять
    строки как есть значит терять половину совпадений.
    """
    tail = (ref or "").strip().rstrip("/").rsplit("/", 1)[-1]
    return slug.entry_slug(tail) or tail


def thread_works(root: Path, thread: str,
                 live_only: bool = True) -> List[Dict[str, object]]:
    """Работы, за которые отвечает нить *thread* — по записи на карточку.

    Запись: ``num``, ``slug`` (имя папки), ``title`` (H1), ``status``,
    ``taken_by`` (кто числится, «» — никто), ``done``/``total`` по
    СОГЛАСОВАННОМУ чеклисту (предложения не прогресс, см. :func:`items`),
    ``at`` — курсор исполнителя (0, когда не стоит) — и ``step``, номер шага
    плана нити (0, когда адреса нет): по нему читающий раскладывает работы под
    их шагами. Порядок — по имени папки, то есть по номеру: сортировку под себя
    делает читающий.

    ``taken_by`` едет рядом со статусом, потому что читателю (сид хендоффа) их
    нужно СВЕРИТЬ: паспорт бывает и рассогласован, и тогда верить одному статусу
    значит соврать принимающей сессии (кандидат 168).

    Битую карточку молча пропускаем: читатель этого списка (сид хендоффа) не
    должен падать из-за одного паспорта без ``status``.
    """
    want = _thread_key(thread)
    wdir = works_dir(root)
    out: List[Dict[str, object]] = []
    for p in sorted(wdir.iterdir()) if wdir.is_dir() else []:
        f = p / "work.md"
        if not p.is_dir() or not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
            st = _status_of(text)
        except (OSError, WorkError):
            continue
        m = re.search(r"^thread:\s*(\S+)", text, re.M)
        if not m or _thread_key(m.group(1)) != want:
            continue
        if live_only and st not in LIVE:
            continue
        its = items(text)
        cur = re.search(r"^at:\s*(\d+)\s*$", text, re.M)
        by = re.search(r"^taken-by:\s*(\S.*)$", text, re.M)
        stp = re.search(r"^step:\s*(\d+)\s*$", text, re.M)
        out.append({
            "num": p.name.partition("-")[0],
            "slug": p.name,
            "title": next((ln[2:].strip() for ln in text.splitlines()
                           if ln.startswith("# ")), p.name),
            "status": st,
            "taken_by": by.group(1).strip() if by else "",
            "done": sum(1 for d, _ in its if d),
            "total": len(its),
            "at": int(cur.group(1)) if cur else 0,
            "step": int(stp.group(1)) if stp else 0,
        })
    return out


def accepted_items(text: str) -> List[int]:
    """Numbers of the items the human has ACCEPTED, read off the journal.

    Two hands write acceptance, both as journal lines (see the module
    docstring): the board takes one item at a time, ``close`` takes everything
    checked with one word. The mass line is read only while the work is closed —
    it accepted the checklist as it stood at that moment, and after a ``reopen``
    the checklist moves on. Read inside ``## журнал`` alone: a passport's free
    text may well quote the same words without being a record of a gesture.
    """
    lines = text.splitlines()
    found = _section(lines, JOURNAL_TITLE)
    journal = lines[found[0]:found[1]] if found else []
    nums = set()
    for ln in journal:
        m = _ACCEPT_ONE_RE.match(ln)
        if m:
            nums.add(int(m.group(1)))
    if (_status_of(text) == "done"
            and any(_ACCEPT_ALL_RE.match(ln) for ln in journal)):
        nums |= {i for i, (st, _) in enumerate(all_items(text), 1) if st == "x"}
    return sorted(nums)


def _mark_accepted(text: str) -> str:
    """Append ``✓✓`` to every checked-AND-accepted item — display only.

    The file keeps the plain ``- [x]`` line: acceptance is a journal fact, and
    rewriting the checklist for it would fork the format every other hand reads.
    An accepted item that was later unchecked wears no mark — the two facts are
    shown together or not at all.
    """
    nums = accepted_items(text)
    if not nums:
        return text
    lines = text.splitlines()
    n = 0
    for j, ln in enumerate(lines):
        m = _ITEM_RE.match(ln)
        if not m:
            continue
        n += 1
        if n in nums and m.group(1) == "x":
            lines[j] = "{0} {1}".format(ln.rstrip(), ACCEPTED_MARK)
    return "\n".join(lines)


def show(root: Path, key: str) -> str:
    """The work.md as it is — the file IS the truth, plus the acceptance mark.

    Raw is nearly the whole rendering: ``## план`` sits where it lives (after the
    description, before the checklist), a proposed item shows its own ``?`` and
    an item's description is already indented under its title. The one thing
    added is ``✓✓`` on an item that is both done and accepted — that second fact
    lives in the journal, and a reader shouldn't have to walk it line by line.
    """
    _, text = _read(_find(root, key))
    return _mark_accepted(text).rstrip("\n")


# --- CLI wiring --------------------------------------------------------------

def _root(args) -> Path:
    project = getattr(args, "project", None)
    if project:
        # same cross-project resolution as `candidate add --project`
        from .candidate import _resolve_target_root
        return _resolve_target_root(project)
    return paths.require_tide_root()


def _say_address(root: Path, name: str) -> None:
    """Куда работа идёт: нить и шаг её плана — сразу после рождения работы."""
    text = (works_dir(root) / name / "work.md").read_text(encoding="utf-8")
    th = re.search(r"^thread:\s*(\S+)", text, re.M)
    if not th:
        return
    st = re.search(r"^step:\s*(\d+)\s*$", text, re.M)
    if not st:
        print("tide: нить {0} (из сессии-автора)".format(th.group(1)))
        return
    title = plan_step_title(root, th.group(1), int(st.group(1)))
    print("tide: нить {0}, шаг {1}{2} (из сессии-автора и её плана)".format(
        th.group(1), st.group(1), " «{0}»".format(title) if title else ""))


def _cmd_add(args) -> int:
    root = _root(args)
    if getattr(args, "cand", None):
        d, stem = new_work_from_candidate(
            root, " ".join(args.text), args.cand,
            deadline=args.deadline, for_project=args.for_project)
        print("tide: работа заведена — {0}".format(d.name))
        print("tide: рождена из кандидата {0} — он ушёл с полки в __dropped__ "
              "(восстановим), его текст лёг черновиком в ## план".format(stem))
        _say_address(root, d.name)
        return 0
    d = new_work(root, " ".join(args.text),
                 deadline=args.deadline, for_project=args.for_project)
    print("tide: работа заведена — {0}".format(d.name))
    print("tide: чеклист пуст — пункты набираются разговором: propose → «да» "
          "человека (имя работы это заголовок, а не первый шаг)")
    _say_address(root, d.name)
    return 0


def _say_owner(name: str, owner: str) -> None:
    """Сказать вслух, что работа обрела нить: человек идёт смотреть её вкладку."""
    if owner:
        print("tide: {0} — ответственная нить {1} (из сессии-автора); работа "
              "видна во вкладке нити уже сейчас".format(name, owner))


def _cmd_plan(args) -> int:
    # \n in a single shell argument is the practical way to pass a multi-line plan
    text = " ".join(args.text).replace("\\n", "\n")
    name, replaced, owner = set_plan(_root(args), args.key, text)
    print("tide: {0} — план {1} (## план); статус не тронут, «да» за человеком"
          .format(name, "обновлён" if replaced else "предложен"))
    _say_owner(name, owner)
    return 0


def _unfold(texts: List[str]) -> List[str]:
    """``\\n`` typed in a shell argument becomes a real newline (as in `plan`)."""
    return [t.replace("\\n", "\n") for t in texts]


def _print_move(name: str, moved: Optional[str]) -> None:
    """Say a status move out loud — the gate must never move silently."""
    if moved == "review":
        print("tide: {0} — все пункты чекнуты, предложений нет → review; done "
              "ставит человек — его слово фиксируешь tide work close --word "
              "(на живой доске есть и кнопка)".format(name))
    elif moved == "taken":
        print("tide: {0} — есть несделанное → снова taken (review снят)".format(name))


def _cmd_propose(args) -> int:
    replace = getattr(args, "replace", False)
    name, first, last, reopened, owner = propose(_root(args), args.key,
                                                 _unfold(args.items),
                                                 replace=replace)
    print("tide: {0} — {1}".format(name, _proposal_words(first, last, replace)))
    print("tide: пока это «- [?]» — чекнуть их нельзя; ждут «да» человека — "
          "его слово фиксируешь tide work agree --word (на живой доске есть "
          "и кнопка)")
    _say_owner(name, owner)
    if reopened:
        print("tide: {0} — работа вернулась в работу (review → taken): есть "
              "что согласовать".format(name))
    return 0


def _cmd_agree(args) -> int:
    # два разных ответа человека — «да» и «нет»; в одном вызове не мешаем
    if args.drop and (args.indexes or args.all):
        raise WorkError(
            "work: --drop — отдельный жест: сначала снять, потом согласовать")
    if args.all and args.indexes:
        raise WorkError("work: --all или номера — что-то одно")
    if args.drop:
        name, nums, moved = drop_proposed(_root(args), args.key, args.drop,
                                          args.word)
        print("tide: {0} — снято: {1} (слово человека в журнале)".format(
            name, _steps_words(nums)))
        _print_move(name, moved)
        return 0
    name, nums, every, moved = agree(_root(args), args.key, args.indexes,
                                     args.word)
    print("tide: {0} — согласовано: {1}{2}".format(
        name, _steps_words(nums), " (все предложения)" if every else ""))
    print("tide: теперь это «- [ ]» — чекать можно, но только с --proof")
    _print_move(name, moved)
    return 0


def _cmd_checklist(args) -> int:
    name = set_checklist(_root(args), args.key, _unfold(args.items),
                         force=args.force)
    print("tide: {0} — чеклист согласован ({1} пункт(ов))".format(
        name, len(args.items)))
    return 0


def _cmd_fix(args) -> int:
    name, first, last, reopened = add_fixes(_root(args), args.key,
                                            _unfold(args.items), args.word)
    print("tide: {0} — {1} (## фиксы, сразу согласованы словом человека)".format(
        name, _fix_words(first, last)))
    print("tide: номера сквозные — чекать их с --proof, как остальные пункты")
    if reopened:
        print("tide: {0} — работа вернулась в работу (review → taken): есть "
              "что доделать".format(name))
    return 0


def _cmd_take(args) -> int:
    name, owner = take(_root(args), args.key, by=args.by, word=args.word,
                       thread=getattr(args, "thread", None))
    print("tide: {0} — взята (open → taken)".format(name))
    if owner:
        print("tide: ответственная нить — {0}".format(owner))
    return 0


def _cmd_dispatch(args) -> int:
    name, who = dispatch(_root(args), args.key, args.to)
    print("tide: {0} — строитель отправлен: {1} (след в журнале; статус не "
          "тронут)".format(name, who))
    return 0


def _cmd_thread(args) -> int:
    val = "" if args.clear else (args.set or "")
    name = set_thread(_root(args), args.key, val, source="рука человека (CLI)")
    print("tide: {0} — {1}".format(
        name, "нить снята" if not val else "ответственная нить → {0}".format(val)))
    return 0


def _cmd_step(args) -> int:
    if args.set is None and not args.clear:
        raise WorkError("work: скажи номер шага (--set N) или --clear")
    name, step, title = set_step(_root(args), args.key,
                                 None if args.clear else args.set,
                                 word=getattr(args, "word", None))
    if step is None:
        print("tide: {0} — шаг снят".format(name))
    else:
        print("tide: {0} — шаг плана {1}{2}".format(
            name, step, " «{0}»".format(title) if title else ""))
    return 0


def _cmd_drop(args) -> int:
    if args.index is None:
        raise WorkError(
            'work: снимать нечего — скажи номер пункта; N ниже подставь числом, '
            'номера видно в tide work show {0}. Снимать так: '
            'tide work drop {0} N --word "его слово"'.format(args.key))
    index = _item_number(args.index, args.key)
    name, title, moved = drop_item(_root(args), args.key, index, args.word)
    print("tide: {0} — пункт {1} «{2}» снят (слово человека в журнале)".format(
        name, index, title))
    print("tide: номера ниже сдвинулись на один — курсор и приёмки переехали "
          "следом, журнал прежних жестов не тронут")
    _print_move(name, moved)
    return 0


def _cmd_at(args) -> int:
    if args.index is None and not args.clear:
        raise WorkError("work: скажи номер пункта или --clear")
    name, item = at(_root(args), args.key,
                    None if args.clear else args.index)
    print("tide: {0} — {1}".format(
        name, "курсор снят" if not item
        else "курсор на пункте {0} «{1}»".format(args.index, item)))
    return 0


def _cmd_check(args) -> int:
    if args.index is None:
        raise WorkError(
            'work: чекать нечего — скажи номер пункта; N ниже подставь числом, '
            'номера видно в tide work show {0}. Чекать так: '
            'tide work check {0} N --proof "что проверено"'.format(args.key))
    index = _item_number(args.index, args.key)
    if not (args.proof or "").strip():
        raise WorkError(
            "work: чек без пруфа не жест — пункт {0} остался как был. "
            "Пруф это одна строка о том, что реально проверено (не «сделал»): "
            "tide work check {1} {0} --proof \"что проверено\"".format(
                index, args.key))
    name, reviewed = check(_root(args), args.key, index, args.proof)
    print("tide: {0} — пункт {1} чекнут".format(name, index))
    _print_move(name, "review" if reviewed else None)
    return 0


def _cmd_uncheck(args) -> int:
    name = uncheck(_root(args), args.key, args.index, reason=args.reason)
    print("tide: {0} — пункт {1} расчекнут".format(name, args.index))
    return 0


def _cmd_close(args) -> int:
    name, accepted = close(_root(args), args.key, args.word)
    print("tide: {0} — закрыта (слово человека в журнале)".format(name))
    if accepted:
        print("tide: этим же словом принято сделанное — {0} пункт(ов); в show "
              "они с {1}".format(accepted, ACCEPTED_MARK))
    return 0


def _cmd_title(args) -> int:
    name, old, new, was_done = set_title(_root(args), args.key,
                                         " ".join(args.title), args.word)
    print("tide: {0} — переименована: «{1}» (было: «{2}»)".format(
        name, new, _clip(old, _TITLE_IN_JOURNAL)))
    if len(new) > TITLE_MAX:
        print("tide: заголовок длинный ({0} символов) — на доске карточка "
              "читается с одного взгляда, короче лучше".format(len(new)))
    if was_done:
        print("tide: {0} закрыта — переименована задним числом; в истории она "
              "осталась под старым именем".format(name))
    return 0


def _cmd_reopen(args) -> int:
    name, st = reopen(_root(args), args.key, word=args.word)
    print("tide: {0} — открыта заново (status: {1})".format(name, st))
    return 0


def _cmd_list(args) -> int:
    print(render_list(_root(args)))
    return 0


def _cmd_show(args) -> int:
    root = _root(args)
    text = show(root, args.key)
    print(text)
    th = re.search(r"^thread:\s*(\S+)", text, re.M)
    st = re.search(r"^step:\s*(\d+)\s*$", text, re.M)
    if th and st:
        # поле несёт номер, человек живёт именами: имя шага дочитываем из плана
        title = plan_step_title(root, th.group(1), int(st.group(1)))
        print("\ntide: куда идёт — нить {0}, шаг {1}{2}".format(
            th.group(1), st.group(1), " «{0}»".format(title) if title else ""))
    elif th:
        print("\ntide: куда идёт — нить {0}, шаг не проставлен "
              "(tide work step {1} --set N)".format(th.group(1), args.key))
    if ACCEPTED_MARK in text:
        print("\ntide: {0} — пункт сделан И принят человеком; «- [x]» без метки "
              "— сделан, приёмки пока нет".format(ACCEPTED_MARK))
    return 0


def register(subparsers) -> None:
    """Add the ``work`` command group to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "work",
        help="works: add/plan/propose/agree/drop/fix/take/step/check/uncheck/"
             "close/reopen/title/list/show")
    wsub = p.add_subparsers(dest="work_cmd")

    def _common(sp):
        sp.add_argument(
            "--project",
            help="target ANOTHER rostered project's works (by roster name)")

    ap = wsub.add_parser("add", help="add a work (mirrors the board's form)")
    ap.add_argument("text", nargs="+", help="what to do — one line")
    ap.add_argument("--deadline", help="YYYY-MM-DD (one deadline per work)")
    ap.add_argument("--for", dest="for_project",
                    help="the 'project:' field — where the world changes")
    ap.add_argument("--cand", metavar="KEY",
                    help="born from a candidate (NN / NN-slug / slug): its text "
                         "lands as a draft in '## план', the candidate leaves "
                         "the shelf for __dropped__; the work's name still comes "
                         "FROM THE ARGUMENT")
    _common(ap)
    ap.set_defaults(func=_cmd_add, _cmd="work add")

    pp = wsub.add_parser(
        "plan", help="propose the work's plan as text (the '## план' section; "
                     "does not move status)")
    pp.add_argument("key")
    pp.add_argument("text", nargs="+",
                    help="the plan text; \\n in the argument is a line break")
    _common(pp)
    pp.set_defaults(func=_cmd_plan, _cmd="work plan")

    op = wsub.add_parser(
        "propose", help="propose checklist items as '- [?]' — they wait for the "
                        "human's yes")
    op.add_argument("key")
    op.add_argument("items", nargs="+",
                    help="items, one per argument; \\n inside an item ends the "
                         "title, the rest is the description")
    op.add_argument("--replace", action="store_true",
                    help="drop the previous '- [?]' and propose these (agreed "
                         "items are left alone)")
    _common(op)
    op.set_defaults(func=_cmd_propose, _cmd="work propose")

    gp = wsub.add_parser(
        "agree", help="the human's yes, in words: '- [?]' → '- [ ]' "
                      "(--drop is the no)")
    gp.add_argument("key")
    gp.add_argument("indexes", nargs="*", type=int, metavar="N",
                    help="item numbers; without them, every proposed item at once")
    gp.add_argument("--all", action="store_true",
                    help="explicitly: agree to every '- [?]'")
    gp.add_argument("--drop", nargs="+", type=int, metavar="N",
                    help="drop proposed items N (with their descriptions)")
    # NOT argparse-required on purpose: this is the human's SIGNATURE, and a
    # person meets it in their first hour. argparse would answer "the following
    # arguments are required: --word" — the parser's words, no hint of the next
    # gesture. The body says what happened, what stayed as it was, and what to
    # type (cand 223, работа 57 п.5). The refusal itself is unchanged.
    gp.add_argument("--word",
                    help="the human's word, verbatim (goes to the journal)")
    _common(gp)
    gp.set_defaults(func=_cmd_agree, _cmd="work agree")

    xp = wsub.add_parser(
        "drop", help="drop an AGREED item N — only with the human's word "
                     "(a proposed '- [?]' is dropped via agree --drop)")
    xp.add_argument("key")
    xp.add_argument("index", nargs="?", help="item number (from 1)")  # str: see check

    # NOT argparse-required on purpose: this is the human's SIGNATURE, and a
    # person meets it in their first hour. argparse would answer "the following
    # arguments are required: index, --word" — the parser's words, no hint of the next
    # gesture. The body says what happened, what stayed as it was, and what to
    # type (cand 223, работа 57 п.5). The refusal itself is unchanged.
    xp.add_argument("--word",
                    help="the human's word, verbatim (goes to the journal)")
    _common(xp)
    xp.set_defaults(func=_cmd_drop, _cmd="work drop")

    kp = wsub.add_parser(
        "checklist",
        help="gesture 1: replace the checklist with AGREED items (+journal)")
    kp.add_argument("key")
    kp.add_argument("items", nargs="+",
                    help="items, one per argument; \\n inside an item ends the "
                         "title, the rest is the description")
    kp.add_argument("--force", action="store_true",
                    help="replace despite checked items (the human's word)")
    _common(kp)
    kp.set_defaults(func=_cmd_checklist, _cmd="work checklist")

    fp = wsub.add_parser(
        "fix", help="what the human adds at acceptance: items into '## фиксы', "
                    "agreed by their word right away")
    fp.add_argument("key")
    fp.add_argument("items", nargs="+",
                    help="items, one per argument; \\n inside an item ends the "
                         "title, the rest is the description")
    # NOT argparse-required on purpose: this is the human's SIGNATURE, and a
    # person meets it in their first hour. argparse would answer "the following
    # arguments are required: --word" — the parser's words, no hint of the next
    # gesture. The body says what happened, what stayed as it was, and what to
    # type (cand 223, работа 57 п.5). The refusal itself is unchanged.
    fp.add_argument("--word",
                    help="what the human added, verbatim (goes to the journal)")
    _common(fp)
    fp.set_defaults(func=_cmd_fix, _cmd="work fix")

    tp = wsub.add_parser("take", help="take the work: open → taken "
                                      "(+thread, +journal)")
    tp.add_argument("key", help="NN, NN-slug or the work's slug")
    tp.add_argument("--by", help="who takes it (into taken-by and the journal)")
    tp.add_argument("--word", help="the human's word you're taking it on")
    tp.add_argument("--thread", help="the owning thread, explicitly (otherwise "
                    "auto, from the calling session)")
    _common(tp)
    tp.set_defaults(func=_cmd_take, _cmd="work take")

    dp = wsub.add_parser(
        "dispatch", help="mark that a builder was sent out: a line in the "
                         "journal (status unchanged; call it again and you get "
                         "one more line)")
    dp.add_argument("key")
    # NOT argparse-required on purpose: this is the human's SIGNATURE, and a
    # person meets it in their first hour. argparse would answer "the following
    # arguments are required: --to" — the parser's words, no hint of the next
    # gesture. The body says what happened, what stayed as it was, and what to
    # type (cand 223, работа 57 п.5). The refusal itself is unchanged.
    dp.add_argument("--to", metavar="NAME",
                    help="who was sent out to build")
    _common(dp)
    dp.set_defaults(func=_cmd_dispatch, _cmd="work dispatch")

    hp = wsub.add_parser(
        "thread", help="the work's owning thread: attach / change / clear")
    hp.add_argument("key")
    hp.add_argument("--set", help="the thread's slug (NN-@slug) or an address "
                                  "proj/NN-@slug")
    hp.add_argument("--clear", action="store_true", help="clear the thread")
    _common(hp)
    hp.set_defaults(func=_cmd_thread, _cmd="work thread")

    stp = wsub.add_parser(
        "step", help="the thread's plan step this work heads for (usually "
                     "arrives with the thread — by hand only when it stayed "
                     "silent)")
    stp.add_argument("key")
    stp.add_argument("--set", type=int, metavar="N", help="step number (from 1)")
    stp.add_argument("--clear", action="store_true", help="clear the step")
    stp.add_argument("--word", help="the human's word, if they set the step "
                                    "(goes to the journal)")
    _common(stp)
    stp.set_defaults(func=_cmd_step, _cmd="work step")

    atp = wsub.add_parser(
        "at", help="cursor: which item the doer is on right now (the board "
                   "underlines it). Clears itself when the item is checked")
    atp.add_argument("key")
    atp.add_argument("index", type=int, nargs="?",
                     help="item number (from 1); without it, use --clear")
    atp.add_argument("--clear", action="store_true", help="clear the cursor")
    _common(atp)
    atp.set_defaults(func=_cmd_at, _cmd="work at")

    cp = wsub.add_parser(
        "check", help="check item N with proof (all checked → review on its own)")
    cp.add_argument("key")
    # index and --proof are NOT argparse-required, on purpose. A check without
    # proof is the single most common first-hour mistake, and argparse answers it
    # with "the following arguments are required: index, --proof" — the parser's
    # words, not tide's, and no hint of what to do next. Accepting them as missing
    # lets :func:`_cmd_check` say why the gesture exists and what to type (cand 223).
    # Not type=int: a person pastes the hint before substituting, and argparse
    # answers "invalid int value: 'N'" — the parser's voice again, at the exact
    # moment the message was trying to teach. _cmd_check parses it and says what N is.
    cp.add_argument("index", nargs="?", help="item number (from 1)")
    cp.add_argument("--proof",
                    help="what exactly was done: a commit, a link, a file")
    _common(cp)
    cp.set_defaults(func=_cmd_check, _cmd="work check")

    up = wsub.add_parser("uncheck", help="uncheck item N (review → taken)")
    up.add_argument("key")
    up.add_argument("index", type=int)
    up.add_argument("--reason", help="why it was unchecked (goes to the journal)")
    _common(up)
    up.set_defaults(func=_cmd_uncheck, _cmd="work uncheck")

    dp = wsub.add_parser(
        "close", help="close it: done is set ONLY with the human's word (the "
                      "same word accepts everything done)")
    dp.add_argument("key")
    # NOT argparse-required on purpose: this is the human's SIGNATURE, and a
    # person meets it in their first hour. argparse would answer "the following
    # arguments are required: --word" — the parser's words, no hint of the next
    # gesture. The body says what happened, what stayed as it was, and what to
    # type (cand 223, работа 57 п.5). The refusal itself is unchanged.
    dp.add_argument("--word",
                    help="the human's word that closes and accepts it (goes to "
                         "the journal)")
    _common(dp)
    dp.set_defaults(func=_cmd_close, _cmd="work close")

    np = wsub.add_parser(
        "title", help="rename the work: the H1 changes ONLY with the human's word")
    np.add_argument("key")
    np.add_argument("title", nargs="+", help="the new title — short, one line")
    # NOT argparse-required on purpose: this is the human's SIGNATURE, and a
    # person meets it in their first hour. argparse would answer "the following
    # arguments are required: --word" — the parser's words, no hint of the next
    # gesture. The body says what happened, what stayed as it was, and what to
    # type (cand 223, работа 57 п.5). The refusal itself is unchanged.
    np.add_argument("--word",
                    help="the human's word, verbatim (goes to the journal)")
    _common(np)
    np.set_defaults(func=_cmd_title, _cmd="work title")

    rp = wsub.add_parser("reopen", help="open a closed work again: done → open")
    rp.add_argument("key")
    rp.add_argument("--word", help="the human's word (goes to the journal)")
    _common(rp)
    rp.set_defaults(func=_cmd_reopen, _cmd="work reopen")

    lp = wsub.add_parser("list", help="the works board as text (live + closed)")
    _common(lp)
    lp.set_defaults(func=_cmd_list, _cmd="work list")

    sp = wsub.add_parser("show", help="the work's card as it is (the file is "
                                      "the truth)")
    sp.add_argument("key")
    _common(sp)
    sp.set_defaults(func=_cmd_show, _cmd="work show")

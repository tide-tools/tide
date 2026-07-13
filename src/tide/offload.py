"""tide.offload — по-ходовая выгрузка сессии (``tide offload``) + Stop-хук-пинок.

Боль (Гриша, 2026-07-05): «одна перегруженная сессия, потом тяжёлая выгрузка,
много контекста теряется» — а по-ходовая выгрузка «отвлекает агента от работы».
Ответ по закону «доказанный механизм тонет в инфраструктуру»:

* ``tide offload <session> [--cursor …] <note…>`` — ОДНА быстрая команда: строка
  с меткой времени дописывается в ``## context`` паспорта, ``--cursor`` заменяет
  тело ``## cursor``, поле ``offloaded-at`` штампуется. Секунды, ноль LLM.
* **Stop-хук** ``tide hook offload-nudge`` — пинок на естественной паузе: если
  workspace арки двигался, а паспорт не трогали дольше окна, ход НЕ завершается
  (``decision: block``) и агенту выдаётся точная команда с правилом «одна строка:
  где стою / что решил / что дальше». Анти-зацикливание — ``stop_hook_active``.

Сессия находится по slug (вложенный резолв по всем тредам) или по пину
``claude-session`` в паспорте (путь хука). Хук полностью defensive: любая ошибка
= молчаливый exit 0 — хук не смеет ломать сессию.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import fields, paths, slug
from .arc.stream import StreamError

CONTEXT_SECTION = "## context"
CURSOR_SECTION = "## cursor — resume here"
NEXT_SECTION = "## next"
OFFLOADED_FIELD = "offloaded-at"
CLAUDE_SESSION_FIELD = "claude-session"

# The nudge window: workspace moved but the passport untouched for this long →
# the stop is blocked once with the exact offload command. Short enough to keep
# the cursor honest, long enough to never nag mid-flow.
NUDGE_WINDOW_SECONDS = 15 * 60


class OffloadError(StreamError):
    """A user-facing offload error (no such session, nothing to write)."""


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --- session resolution ------------------------------------------------------

def _session_dirs(root: Path) -> List[Path]:
    """Every OPEN nested session/run dir across all containers (thread/routine)."""
    arcs = paths.arcs_dir(Path(root))
    out: List[Path] = []
    if not arcs.is_dir():
        return out
    for container in sorted(arcs.iterdir()):
        sub = container / paths.ARCS_DIRNAME
        if not container.is_dir() or slug.is_closed_entry(container.name) or not sub.is_dir():
            continue
        for entry in sorted(sub.iterdir()):
            if entry.is_dir() and slug.is_entry(entry.name) and not slug.is_closed_entry(entry.name):
                out.append(entry)
    return out


def find_session(root: Path, ref: str) -> Optional[Path]:
    """Resolve the open nested session *ref* names, or None if absent.

    *ref* forms: an exact dir name (``03-pickup`` — unique), a bare slug
    (``pickup``/``01-mvp``, cand 43), or a thread-qualified ``<thread>/<session>``.

    A bare slug shared by open sessions in MULTIPLE threads is AMBIGUOUS and RAISES
    with thread-qualified options — silently resolving to the first match wrote a
    pulse into a stranger's passport in the wild (cand 85, data corruption). Exact
    dir-name and thread-qualified refs are never ambiguous, so they never raise.
    """
    dirs = _session_dirs(root)

    # thread-qualified: '<thread>/<session>' (either part in dir-name or bare-slug form)
    parts = [p for p in str(ref).split("/") if p and p != paths.ARCS_DIRNAME]
    if len(parts) >= 2:
        tw = {slug.slugify(parts[0]), slug.entry_slug(parts[0])}
        sw = {slug.slugify(parts[1]), slug.entry_slug(parts[1])}
        for entry in dirs:
            thread = entry.parent.parent
            if (thread.name == parts[0] or slug.entry_slug(thread.name) in tw) and \
               (entry.name == parts[1] or slug.entry_slug(entry.name) in sw):
                return entry
        return None

    def _ambiguous(cands: List[Path]) -> "OffloadError":
        opts = ", ".join("{0}/{1}".format(e.parent.parent.name, e.name) for e in cands)
        return OffloadError(
            "offload: session {0!r} is ambiguous — {1} open sessions match across "
            "threads. Qualify it as <thread>/<session>: {2}".format(ref, len(cands), opts))

    # An exact dir-name (NN-slug) is preferred, but even that can collide across
    # threads (01-work in two threads) — so ambiguity is checked here too (cand 85).
    exact = [e for e in dirs if e.name == ref]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise _ambiguous(exact)

    wants = {slug.slugify(ref), slug.entry_slug(ref)}
    matches = [e for e in dirs if slug.entry_slug(e.name) in wants]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise _ambiguous(matches)
    return None


def find_session_by_claude_id(root: Path, session_id: str) -> Optional[Path]:
    """The open session whose passport pins ``claude-session: <session_id>``."""
    if not session_id:
        return None
    for entry in _session_dirs(root):
        pin = (fields.read_field(entry / "arc.md", CLAUDE_SESSION_FIELD) or "").strip()
        if pin == session_id:
            return entry
    return None


# --- the offload write -------------------------------------------------------

def offload(root: Path, ref: str, *, note: str = "", cursor: str = "",
            next_steps: str = "") -> Path:
    """Append *note* to ``## context``; optionally reset cursor and ``## next``.

    ФОРМА ЗАПИСИ (закон доски, Гриша 07.07 — «агенты пишут так, чтобы можно
    было анализировать»): cursor = ТЕКУЩЕЕ ДЕЙСТВИЕ одной строкой, настоящее
    время («женю доску с формой записи»); next = 1–3 следующих шага через « · »;
    note = что решил/сделал, по-человечески, без тех-жаргона в первых словах.
    Доска показывает их буквально: cursor → «сейчас», next → «дальше».
    """
    if not any(s.strip() for s in (note or "", cursor or "", next_steps or "")):
        raise OffloadError(
            "offload: nothing to write — pass a note and/or --cursor/--next "
            "(правило: одна строка «где стою / что решил / что дальше»)"
        )
    entry = find_session(Path(root), ref)
    if entry is None:
        names = ", ".join(e.name for e in _session_dirs(Path(root))) or "(none)"
        raise OffloadError(
            "offload: no open session matching {0!r}. Open sessions: {1}".format(ref, names)
        )
    return write_pulse(entry, note=note, cursor=cursor, next_steps=next_steps)


def write_pulse(entry: Path, *, note: str = "", cursor: str = "",
                next_steps: str = "") -> Path:
    """Write a pulse to a KNOWN session dir — the resolution-free core of :func:`offload`.

    Callers that already hold the exact session dir MUST use this, not ``offload(root,
    ref)``: resolving by slug (:func:`find_session`) collides when sessions share a slug
    (every handoff pickup is ``NN-pickup`` → entry_slug ``pickup``), so a slug lookup
    lands the pulse on the FIRST same-slug session. That silently mis-stamped a fresh
    pickup's "нить принята" onto an OLDER sibling — the reception seam looked half-open
    on the real session (caught by a live 6-hop dogfood, cand 78). Taking the exact dir
    removes the ambiguity entirely.
    """
    passport = entry / "arc.md"
    text = passport.read_text(encoding="utf-8")

    if (note or "").strip():
        line = "- {0} — {1}".format(_now_iso(), " ".join(note.split()))
        if CONTEXT_SECTION in text:
            head, _sep, tail = text.partition(CONTEXT_SECTION)
            rest = tail.split("\n## ", 1)  # don't swallow a section that follows
            trailing = ("\n## " + rest[1]) if len(rest) > 1 else "\n"
            # keep existing entries, drop a leftover template <placeholder> line
            body = [
                ln for ln in rest[0].splitlines()[1:]
                if ln.strip() and not ln.strip().startswith("<")
            ]
            body.append(line)
            text = head + CONTEXT_SECTION + "\n" + "\n".join(body) + "\n" + trailing.lstrip("\n")
        else:
            text = text.rstrip() + "\n\n{0}\n{1}\n".format(CONTEXT_SECTION, line)

    passport.write_text(text, encoding="utf-8")
    if (cursor or "").strip():
        _replace_section(passport, CURSOR_SECTION, " ".join(cursor.split()))
    if (next_steps or "").strip():
        _replace_section(passport, NEXT_SECTION, " ".join(next_steps.split()))
    fields.set_field(passport, OFFLOADED_FIELD, _now_iso())
    return passport


def _replace_section(passport: Path, header: str, body: str) -> None:
    """Replace the body of *header*'s section in *passport* (atomic-ish rewrite)."""
    text = passport.read_text(encoding="utf-8")
    if header not in text:
        text = text.rstrip() + "\n\n{0}\n{1}\n".format(header, body)
    else:
        head, _sep, tail = text.partition(header)
        rest = tail.split("\n## ", 1)
        trailing = ("\n## " + rest[1]) if len(rest) > 1 else "\n"
        text = head + header + "\n" + body + "\n" + trailing
    passport.write_text(text, encoding="utf-8")


# --- the Stop-hook nudge -----------------------------------------------------

def _newest_mtime(d: Path) -> float:
    """Newest file mtime under *d* (0.0 when absent/empty)."""
    best = 0.0
    if not d.is_dir():
        return best
    for p in d.rglob("*"):
        try:
            if p.is_file():
                best = max(best, p.stat().st_mtime)
        except OSError:
            continue
    return best


def nudge_reason(root: Path, session_id: str, *, now: Optional[float] = None) -> Optional[str]:
    """The block-reason when *session_id*'s arc needs an offload, else None.

    Triggers when the session's ``workspace/`` moved AND its passport has not
    been touched for :data:`NUDGE_WINDOW_SECONDS`. Deterministic file mtimes —
    no git, no LLM, milliseconds.
    """
    entry = find_session_by_claude_id(Path(root), session_id)
    if entry is None:
        return None
    passport = entry / "arc.md"
    try:
        passport_m = passport.stat().st_mtime
    except OSError:
        return None
    ws_m = _newest_mtime(entry / "workspace")
    if ws_m <= passport_m:
        return None  # passport is as fresh as the work — nothing owed
    now_ts = now if now is not None else datetime.now().timestamp()
    if now_ts - passport_m < NUDGE_WINDOW_SECONDS:
        return None  # touched recently — don't nag mid-flow
    return (
        "tide: выгрузка отстала — workspace арки {0} двигался, а её паспорт не "
        "трогали дольше {1} мин. Сделай сейчас, это 10 секунд:\n"
        "  tide offload {2} --cursor \"<текущее действие, наст. время>\" --next \"<шаги через · >\" \"<что сделал>\"\n"
        "Правило: одна строка на каждое, без отчётов. Потом заканчивай ход."
    ).format(entry.name, NUDGE_WINDOW_SECONDS // 60, slug.entry_slug(entry.name))


# --- CLI + hook wiring -------------------------------------------------------

# Words that CLAIM a nit is finished. If the pulse says so but the thread is still
# open on disk, the board keeps showing it live — words don't convince the board,
# only disk does (cand 80). Deliberately closure-specific to avoid false warns.
_CLOSURE_MARKERS = ("закрыт", "влит", "смерж", "выпущен", "merged", "shipped")


def _closure_word_warning(passport: Path, blob: str) -> Optional[str]:
    """Warn when the pulse SAYS closed but its thread is OPEN on disk (cand 80).

    The pulse lands in ``<thread>/arcs/<session>/arc.md``; the thread is the dir two
    levels up. If it isn't wrapped ``__…__`` (closed) yet the text claims closure, the
    board will still paint the nit live — nudge the human to close it for real.
    """
    low = (blob or "").lower()
    if not any(m in low for m in _CLOSURE_MARKERS):
        return None
    try:
        thread = passport.parent.parent.parent  # arc.md → session → arcs → thread
        if thread.is_dir() and not slug.is_closed_entry(thread.name):
            return ("tide: ⚠ пульс говорит «закрыто/влито», а нить {0} на диске ОТКРЫТА — "
                    "слова доску не убеждают. Закрой руками: tide arc close {1}".format(
                        thread.name, slug.entry_slug(thread.name)))
    except Exception:  # noqa: BLE001  a warning must never break offload
        return None
    return None


def _cmd_offload(args) -> int:
    root = paths.require_tide_root()
    note = " ".join(getattr(args, "note", []) or [])
    cursor = getattr(args, "cursor", "") or ""
    next_steps = getattr(args, "next_steps", "") or ""
    passport = offload(root, args.session, note=note, cursor=cursor, next_steps=next_steps)
    print("tide: offloaded → {0}".format(passport))
    warn = _closure_word_warning(passport, " ".join((note, cursor, next_steps)))
    if warn:
        print(warn, file=sys.stderr)
    return 0


def cmd_offload_nudge(args) -> int:
    """``tide hook offload-nudge`` — Stop: block once when the offload is owed.

    Fully defensive: any error/no-match prints nothing and exits 0. The
    ``stop_hook_active`` flag from the harness is the anti-loop: when this stop
    was already blocked by a hook, we never block again.
    """
    try:
        if paths.find_tide_root() is None:
            return 0
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except (ValueError, OSError):
            return 0
        if payload.get("stop_hook_active"):
            return 0
        session = payload.get("session_id") or payload.get("session") or ""
        reason = nudge_reason(paths.require_tide_root(), str(session))
        if reason:
            print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001  a hook must never raise
        print("tide: [offload-nudge] skipped: {0}".format(exc), file=sys.stderr)
    return 0


def register(subparsers) -> None:
    """Add the top-level ``offload`` command (called by cli.py)."""
    p = subparsers.add_parser(
        "offload",
        help="по-ходовая выгрузка: строка в ## context паспорта сессии (+ --cursor)",
    )
    p.add_argument("session", help="open session slug (nested resolve across threads)")
    p.add_argument("--cursor", help="ТЕКУЩЕЕ ДЕЙСТВИЕ одной строкой, настоящее время («женю доску с формой»)")
    p.add_argument("--next", dest="next_steps",
                   help="1–3 следующих шага через « · » — доска покажет как «дальше»")
    p.add_argument("note", nargs="*", help="context note: что решил/сделал, по-человечески")
    p.set_defaults(func=_cmd_offload, _cmd="offload")
